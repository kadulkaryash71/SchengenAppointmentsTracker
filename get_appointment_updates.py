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
NO_AVAIL_RE = re.compile(r"\bNo availability\b", re.IGNORECASE)
WAITLIST_RE = re.compile(r"\bWaitlist\s+Open\b", re.IGNORECASE)
CHECKED_RE = re.compile(r"\bchecked\b", re.IGNORECASE)

_STATUS_RANK = {"available": 2, "waitlist": 1, "unavailable": 0}

def fetch_page_lines(url: str):
    resp = requests.get(
        url,
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")

    lines = []
    for line in soup.get_text("\n").splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)

    return lines

def strip_flags_and_icons(text: str) -> str:
    # Keep letters, spaces, and a few safe punctuation chars.
    # This removes flag emoji and bell icons from country lines.
    cleaned = re.sub(r"[^A-Za-z\s&()\-]", "", text)
    return " ".join(cleaned.split()).strip()

def looks_like_country(line: str) -> bool:
    cleaned = strip_flags_and_icons(line)

    banned = {
        "Dublin",
        "Destination",
        "Country",
        "Earliest",
        "Available",
        "Apr",
        "May",
        "Jun",
        "Tourist Visa",
        "Business Visa",
        "No availability",
        "Waitlist Open",
        "notify me",
        "request it",
        "Email Alerts",
    }

    if not cleaned or cleaned in banned:
        return False

    lowered = cleaned.lower()
    if "checked" in lowered or "slot" in lowered or "availability" in lowered:
        return False

    if len(cleaned) > 30:
        return False

    return bool(re.fullmatch(r"[A-Za-z][A-Za-z\s&()\-]+", cleaned))

def parse_availability(lines):
    rows = []
    i = 0

    while i < len(lines):
        raw_country_line = lines[i]

        if looks_like_country(raw_country_line):
            country = strip_flags_and_icons(raw_country_line)

            # Look at the next few lines because the page splits content across lines.
            nearby_lines = lines[i + 1:i + 7]

            # Normalize split slot text: "1 +" + "slots" => "1 + slots"
            normalized_lines = []
            j = 0
            while j < len(nearby_lines):
                current_line = nearby_lines[j]

                if (
                    j + 1 < len(nearby_lines)
                    and re.fullmatch(r"\d+\s*\+", current_line)
                    and nearby_lines[j + 1].strip().lower() == "slots"
                ):
                    normalized_lines.append(f"{current_line} slots")
                    j += 2
                    continue

                normalized_lines.append(current_line)
                j += 1

            window = " | ".join(normalized_lines)

            # Available row
            date_match = DATE_RE.search(window)
            slots_match = SLOTS_RE.search(window)

            if CHECKED_RE.search(window) and date_match and slots_match:
                rows.append({
                    "country": country,
                    "earliest": date_match.group(0),
                    "status": "available",
                    "slots": slots_match.group(0),
                    "raw": window,
                })
                i += 1
                continue

            # Waitlist row
            if WAITLIST_RE.search(window) and CHECKED_RE.search(window):
                rows.append({
                    "country": country,
                    "earliest": None,
                    "status": "waitlist",
                    "slots": None,
                    "raw": window,
                })
                i += 1
                continue

            # Unavailable row
            if NO_AVAIL_RE.search(window):
                rows.append({
                    "country": country,
                    "earliest": None,
                    "status": "unavailable",
                    "slots": None,
                    "raw": window,
                })
                i += 1
                continue

        i += 1

    # Deduplicate by country, preferring higher-ranked status (available > waitlist > unavailable)
    deduped = {}
    for row in rows:
        key = row["country"].lower()
        if key not in deduped:
            deduped[key] = row
        elif _STATUS_RANK.get(row["status"], 0) > _STATUS_RANK.get(deduped[key]["status"], 0):
            deduped[key] = row

    parsed = list(deduped.values())

    if not parsed:
        raise RuntimeError(
            "Could not parse any availability rows. "
            "Fetched page successfully, but line structure differed from expected."
        )

    return parsed

def parse_available_rows(lines):
    rows = []
    i = 0

    while i < len(lines):
        raw_country_line = lines[i]

        if looks_like_country(raw_country_line):
            country = strip_flags_and_icons(raw_country_line)

            nearby_lines = lines[i + 1:i + 7]

            normalized_lines = []
            j = 0
            while j < len(nearby_lines):
                current_line = nearby_lines[j]

                if (
                    j + 1 < len(nearby_lines)
                    and re.fullmatch(r"\d+\s*\+", current_line)
                    and nearby_lines[j + 1].strip().lower() == "slots"
                ):
                    normalized_lines.append(f"{current_line} slots")
                    j += 2
                    continue

                normalized_lines.append(current_line)
                j += 1

            window = " | ".join(normalized_lines)

            date_match = DATE_RE.search(window)
            slots_match = SLOTS_RE.search(window)

            if CHECKED_RE.search(window) and date_match and slots_match:
                rows.append({
                    "country": country,
                    "earliest": date_match.group(0),
                    "status": "available",
                    "slots": slots_match.group(0),
                    "raw": window,
                })

        i += 1

    return rows

STATE_FILE = Path("last_seen.json")

def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"seen": []}

def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")

def make_signature(row):
    return f"{row['country']}|{row['status']}|{row['earliest']}|{row['slots']}"

def get_new_rows(rows, state):
    seen = set(state.get("seen", []))
    current = {make_signature(r) for r in rows}
    new = [r for r in rows if make_signature(r) not in seen]
    return new, {"seen": list(current)}

def send_email(subject, body, smtp_host, smtp_port, smtp_user, smtp_password, sender, recipient):
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient

    logging.info("Connecting to SMTP server...")
    with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
        server.starttls()
        server.login(smtp_user, smtp_password)
        server.send_message(msg)
        logging.info("Email sent successfully")
    
def build_email_body(rows, page_url):
    lines = ["Slots found:\n"]
    for row in rows:
        if row["status"] == "waitlist":
            lines.append(f"- {row['country']}: Waitlist Open")
        else:
            lines.append(f"- {row['country']}: {row['earliest']} ({row['slots']})")
    lines.append(f"\nPage: {page_url}")
    return "\n".join(lines)

if __name__ == "__main__":
    start = time.time()
    logging.info("Task started")

    try:
        PAGE_URL = "https://schengenappointments.com/in/dublin/tourism"
        lines = fetch_page_lines(PAGE_URL)
        print(lines)
        logging.info("Fetched page")

        rows = parse_availability(lines)
        filtered_rows = [r for r in rows if r["status"] in ("available", "waitlist")]
        logging.info("Filtered rows: %s", filtered_rows)

        state = load_state()
        new_rows, new_state = get_new_rows(filtered_rows, state)
        logging.info("New rows: %s", new_rows)

        if new_rows:
            body = build_email_body(new_rows, PAGE_URL)
            logging.info("About to send email")
            send_email(
                subject="Schengen appointment slots available",
                body=body,
                smtp_host=os.environ["SMTP_HOST"],
                smtp_port=int(os.environ["SMTP_PORT"]),
                smtp_user=os.environ["SMTP_USER"],
                smtp_password=os.environ["SMTP_PASSWORD"],
                sender=os.environ["EMAIL_FROM"],
                recipient=os.environ["EMAIL_TO"],
            )
            logging.info("Email sent")
        else:
            logging.info("No new slots found")

        save_state(new_state)
        logging.info("State saved")

    except Exception:
        logging.exception("Task failed")
        raise
    finally:
        elapsed = time.time() - start
        logging.info("Task finished in %.2f seconds", elapsed)