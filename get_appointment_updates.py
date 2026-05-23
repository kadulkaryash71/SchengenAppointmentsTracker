import re
import requests
from bs4 import BeautifulSoup
import json
import os
import smtplib
from email.mime.text import MIMEText
from pathlib import Path
import logging
from dotenv import load_dotenv
import time
load_dotenv()


LOG_FILE = Path(__file__).resolve().parent / "scheduler.log"

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)

DATE_RE = re.compile(r"\b\d{1,2}\s+[A-Z][a-z]{2}\b")
SLOTS_RE = re.compile(r"\b\d+\s*\+\s*slots?\b", re.IGNORECASE)
WAITLIST_RE = re.compile(r"\bWaitlist\s+Open\b", re.IGNORECASE)
CHECKED_RE = re.compile(r"\bchecked\b", re.IGNORECASE)

COUNTRY_CODES = {
    "Austria": "aut",
    "Croatia": "hrv",
    "Denmark": "dnk",
    "Finland": "fin",
    "Hungary": "hun",
    "Iceland": "isl",
    "Netherlands": "nld",
}

VFS_URL_TEMPLATE = "https://visa.vfsglobal.com/irl/en/{code}/book-an-appointment"
DEFAULT_VFS_URL = "https://www.vfsglobal.com/en/individuals/index.html"


def build_vfs_url(country: str) -> str:
    country_code = COUNTRY_CODES.get(country)
    return VFS_URL_TEMPLATE.format(code=country_code) if country_code else DEFAULT_VFS_URL


def get_soup(url: str) -> BeautifulSoup:
    resp = requests.get(
        url,
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def strip_flags_and_icons(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z\s&()\-]", "", text)
    return " ".join(cleaned.split()).strip()


def get_available_slots(rows) -> list[dict]:
    countries = []

    for row in rows[1:]:
        th = row.find("th")
        if th is None:
            continue

        country = strip_flags_and_icons(th.get_text(strip=True))

        cells = row.find_all("td")
        if len(cells) <= 1:
            continue

        availability_cell = cells[0]
        availability_text = availability_cell.get_text(" ", strip=True)

        if "No availability" in availability_text:
            continue

        earliest_span = availability_cell.find("span")
        earliest = earliest_span.get_text(strip=True) if earliest_span else None

        has_checked_date = (
            CHECKED_RE.search(availability_text)
            and DATE_RE.search(availability_text)
        )

        if has_checked_date:
            status = "available"
        elif WAITLIST_RE.search(availability_text):
            status = "waitlist"
        else:
            status = None

        slots = sum(
            1
            for cell in cells[1:]
            if SLOTS_RE.search(cell.get_text())
        )

        countries.append({
            "country": country,
            "status": status,
            "earliest": earliest,
            "slots": slots,
        })

    return countries


def fetch_slots_from_table(url: str) -> list[dict]:
    soup = get_soup(url)

    table = soup.find("table")
    if table is None:
        raise RuntimeError(
            "No table found on the page. "
            "The site layout may have changed or the request was blocked."
        )

    rows = table.find_all("tr")
    return get_available_slots(rows)


STATE_FILE = Path(os.getenv("STATE_FILE", "last_seen.json"))
BCC_STATE_FILE = Path(os.getenv("BCC_STATE_FILE", "last_bcc.json"))


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logging.warning("State file was corrupted. Starting fresh.")
    return {"seen": []}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def load_bcc_state() -> str:
    """Return the last known EMAIL_BCC string, or '' if never stored."""
    if BCC_STATE_FILE.exists():
        try:
            return json.loads(BCC_STATE_FILE.read_text(encoding="utf-8")).get("bcc", "")
        except json.JSONDecodeError:
            logging.warning("BCC state file was corrupted. Starting fresh.")
    return ""


def save_bcc_state(bcc: str) -> None:
    BCC_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    BCC_STATE_FILE.write_text(json.dumps({"bcc": bcc}, indent=2), encoding="utf-8")


def check_bcc_change() -> None:
    current_bcc = os.getenv("EMAIL_BCC", "").strip()
    previous_bcc = load_bcc_state()

    if not previous_bcc:
        logging.info("No previous BCC state found. Storing current value.")
        save_bcc_state(current_bcc)
        return

    if current_bcc == previous_bcc:
        logging.info("EMAIL_BCC unchanged.")
        return

    logging.info("EMAIL_BCC changed. Sending notification.")

    body = (
        "The BCC mailing list for Schengen slot notifications has been updated.\n\n"
        f"Previous: {previous_bcc}\n"
        f"Current:  {current_bcc}\n\n"
        "You are receiving this because you are subscribed to Schengen slot alerts."
    )

    to_raw = os.getenv("EMAIL_TO", "")
    to_list = [addr.strip() for addr in to_raw.split(",") if addr.strip()]
    new_bcc_list = [addr.strip() for addr in current_bcc.split(",") if addr.strip()]

    if not to_list and not new_bcc_list:
        raise RuntimeError("No recipients configured. Set EMAIL_TO or EMAIL_BCC.")
    if not to_list:
        to_list.append(new_bcc_list.pop(0))

    send_email(
        subject="Schengen bot: BCC mailing list updated",
        body=body,
        smtp_host=os.environ["SMTP_HOST"],
        smtp_port=int(os.environ["SMTP_PORT"]),
        smtp_user=os.environ["SMTP_USER"],
        smtp_password=os.environ["SMTP_PASSWORD"],
        sender=os.environ["EMAIL_FROM"],
        to_recipients=to_list,
        bcc_recipients=new_bcc_list,
    )

    save_bcc_state(current_bcc)
    logging.info("BCC state updated.")


def make_signature(row):
    return f"{row['country']}|{row['status']}|{row['earliest']}|{row['slots']}"


def get_new_rows(rows, state):
    seen = set(state.get("seen", []))
    current = {make_signature(r) for r in rows}
    new = [r for r in rows if make_signature(r) not in seen]
    return new, {"seen": list(current)}


def send_email(subject, body, smtp_host, smtp_port, smtp_user, smtp_password, sender, to_recipients=None, bcc_recipients=None):
    to_recipients = to_recipients or []
    bcc_recipients = bcc_recipients or []

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(to_recipients)

    all_recipients = to_recipients + bcc_recipients

    logging.info("Connecting to SMTP server...")
    with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
        server.starttls()
        server.login(smtp_user, smtp_password)
        server.send_message(msg, to_addrs=all_recipients)
        logging.info("Email sent successfully")


def build_email_body(rows, page_url):
    lines = ["Slots found:\n"]
    for row in rows:
        if row["status"] == "waitlist":
            lines.append(f"- {row['country']}: Waitlist Open")
        else:
            lines.append(f"- {row['country']}: {row['earliest']} ({row['slots']} slot(s) available)")
        lines.append(f"\t- Book appointment at: {build_vfs_url(row['country'])}")

    lines.append(
        "\n☕ Found this useful? "
        "Support the project: "
        "https://buymeacoffee.com/kadulkaryash71"
    )
    return "\n".join(lines)


def _run_slot_check():
    PAGE_URL = "https://schengenappointments.com/in/dublin/tourism"

    rows = fetch_slots_from_table(PAGE_URL)
    logging.info("Fetched rows: %s", rows)

    state = load_state()
    new_rows, new_state = get_new_rows(rows, state)
    logging.info("New rows: %s", new_rows)

    if new_rows:
        body = build_email_body(new_rows, PAGE_URL)
        logging.info("About to send email")

        to_raw = os.getenv("EMAIL_TO", "")
        to_list = [addr.strip() for addr in to_raw.split(",") if addr.strip()]

        bcc_raw = os.getenv("EMAIL_BCC", "")
        bcc_list = [addr.strip() for addr in bcc_raw.split(",") if addr.strip()]

        if not to_list:
            if not bcc_list:
                raise RuntimeError(
                    "No recipients configured. Set EMAIL_TO or EMAIL_BCC."
                )
            to_list.append(bcc_list.pop(0))

        send_email(
            subject="Schengen appointment slots available",
            body=body,
            smtp_host=os.environ["SMTP_HOST"],
            smtp_port=int(os.environ["SMTP_PORT"]),
            smtp_user=os.environ["SMTP_USER"],
            smtp_password=os.environ["SMTP_PASSWORD"],
            sender=os.environ["EMAIL_FROM"],
            to_recipients=to_list,
            bcc_recipients=bcc_list,
        )
        logging.info("Email sent")
    else:
        logging.info("No new slots found")

    save_state(new_state)
    logging.info("State saved")


if __name__ == "__main__":
    start = time.time()
    logging.info("Task started")

    try:
        mode = os.getenv("RUN_MODE", "slots")
        if mode == "bcc-check":
            check_bcc_change()
        else:
            _run_slot_check()

    except Exception:
        logging.exception("Task failed")
        raise
    finally:
        elapsed = time.time() - start
        logging.info("Task finished in %.2f seconds", elapsed)