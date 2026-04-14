#!/usr/bin/env python3
"""Monitor SchengenAppointments.com pages and email when new slots appear.

This bot monitors the *public* SchengenAppointments summary pages such as:
- https://schengenappointments.com/in/dublin/tourism
- https://schengenappointments.com/in/london/tourism
- https://schengenappointments.com/in/london/business

It does not attempt to log into VFS or bypass CAPTCHA/Cloudflare/OTP.
"""

from __future__ import annotations

import json
import logging
import os
import re
import smtplib
import ssl
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Iterable

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

BASE_URL = "https://schengenappointments.com"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("schengen-slot-notifier")


@dataclass(frozen=True)
class SlotRecord:
    country: str
    status: str
    earliest: str | None = None
    last_checked: str | None = None
    month_hint: str | None = None


class ScrapeError(RuntimeError):
    pass


def build_url(city_slug: str, visa_type: str) -> str:
    visa_type = visa_type.strip().lower()
    city_slug = city_slug.strip().lower().replace(" ", "-")
    allowed = {"tourism", "business"}
    if visa_type not in allowed:
        raise ValueError(f"Unsupported visa_type={visa_type!r}. Expected one of {sorted(allowed)}")
    return f"{BASE_URL}/in/{city_slug}/{visa_type}"


def fetch_html(url: str, timeout: int = 25) -> str:
    response = requests.get(
        url,
        timeout=timeout,
        headers={
            "User-Agent": os.getenv("USER_AGENT", DEFAULT_USER_AGENT),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-GB,en;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        },
    )
    response.raise_for_status()
    if "text/html" not in response.headers.get("Content-Type", ""):
        raise ScrapeError(f"Unexpected content type: {response.headers.get('Content-Type')}")
    return response.text


def normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()



def extract_available_records_from_text(lines: list[str]) -> list[SlotRecord]:
    records: list[SlotRecord] = []
    months = {"Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"}

    for raw in lines:
        line = normalize_spaces(raw)
        if not line or line.startswith(("tap a country", "Countries below", "Email Feedback", "Built with")):
            continue

        if "No availability" in line:
            continue

        # Typical examples:
        # "Iceland 15 Jun checked 26 seconds ago notify me notify me 1 + slots"
        # "Portugal 20 Apr checked 19 seconds ago 2 + slots notify me"
        m = re.match(
            r"^(?P<country>[A-Z][A-Za-z\-\s]+?)\s+(?P<earliest>(?:\d{1,2}\s+[A-Z][a-z]{2}|Waitlist Open))\s+checked\s+(?P<checked>.+?)\s+(?:(?P<slots>\d+\s*\+?\s*slots?).*)$",
            line,
        )
        if m:
            country = normalize_spaces(m.group("country"))
            earliest = normalize_spaces(m.group("earliest"))
            checked = normalize_spaces(m.group("checked"))
            slots = normalize_spaces(m.group("slots"))
            month_hint = None
            for token in earliest.split():
                if token in months:
                    month_hint = token
                    break
            records.append(
                SlotRecord(
                    country=country,
                    status=slots,
                    earliest=earliest,
                    last_checked=checked,
                    month_hint=month_hint,
                )
            )
            continue

        # Fallback: any line with "slots" and without "No availability"
        if "slot" in line.lower():
            country_match = re.match(r"^(?P<country>[A-Z][A-Za-z\-\s]+)", line)
            if country_match:
                country = normalize_spaces(country_match.group("country"))
                earliest_match = re.search(r"(\d{1,2}\s+[A-Z][a-z]{2}|Waitlist Open)", line)
                checked_match = re.search(r"checked\s+(.+?)(?:\s+\d+\s*\+?\s*slots?|$)", line)
                slots_match = re.search(r"(\d+\s*\+?\s*slots?)", line, flags=re.I)
                records.append(
                    SlotRecord(
                        country=country,
                        status=normalize_spaces(slots_match.group(1)) if slots_match else "slots available",
                        earliest=normalize_spaces(earliest_match.group(1)) if earliest_match else None,
                        last_checked=normalize_spaces(checked_match.group(1)) if checked_match else None,
                    )
                )

    deduped: dict[str, SlotRecord] = {r.country.lower(): r for r in records}
    return list(deduped.values())



def parse_page(html: str) -> list[SlotRecord]:
    soup = BeautifulSoup(html, "html.parser")

    # First pass: line-based extraction from visible text. This is resilient to simple layout changes.
    text_lines = [line.strip() for line in soup.get_text("\n").splitlines() if line.strip()]
    records = extract_available_records_from_text(text_lines)
    if records:
        return sorted(records, key=lambda r: r.country.lower())

    raise ScrapeError(
        "Could not parse any availability rows. The site layout may have changed, or the content is protected."
    )



def load_state(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning("State file was invalid JSON. Starting fresh.")
        return {}



def save_state(path: Path, records: Iterable[SlotRecord]) -> None:
    payload = {
        record.country.lower(): {
            **asdict(record),
            "seen_at": datetime.now(timezone.utc).isoformat(),
        }
        for record in records
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")



def diff_new_or_changed(current: list[SlotRecord], previous_state: dict[str, dict]) -> list[SlotRecord]:
    changed: list[SlotRecord] = []
    for record in current:
        old = previous_state.get(record.country.lower())
        if not old:
            changed.append(record)
            continue
        old_signature = (old.get("status"), old.get("earliest"), old.get("month_hint"))
        new_signature = (record.status, record.earliest, record.month_hint)
        if old_signature != new_signature:
            changed.append(record)
    return changed



def filter_records(records: list[SlotRecord], countries: list[str]) -> list[SlotRecord]:
    if not countries:
        return records
    wanted = {c.strip().lower() for c in countries if c.strip()}
    return [r for r in records if r.country.lower() in wanted]



def build_email_body(url: str, city: str, visa_type: str, records: list[SlotRecord]) -> str:
    lines = [
        f"Schengen appointment availability detected for {city.title()} ({visa_type}).",
        "",
        f"Source: {url}",
        "",
        "Available destinations:",
    ]
    for record in records:
        parts = [f"- {record.country}: {record.status}"]
        if record.earliest:
            parts.append(f"earliest {record.earliest}")
        if record.last_checked:
            parts.append(f"checked {record.last_checked}")
        lines.append(" | ".join(parts))
    lines.extend([
        "",
        "Book quickly on the official provider if the slot fits your needs.",
        "",
        f"Sent at {datetime.now(timezone.utc).isoformat()} UTC",
    ])
    return "\n".join(lines)



def send_email(subject: str, body: str) -> None:
    smtp_host = require_env("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT", "465"))
    smtp_user = require_env("SMTP_USER")
    smtp_password = require_env("SMTP_PASSWORD")
    email_from = os.getenv("EMAIL_FROM", smtp_user)
    email_to = require_env("EMAIL_TO")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = email_from
    message["To"] = email_to
    message.set_content(body)

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(smtp_host, smtp_port, context=context) as server:
        server.login(smtp_user, smtp_password)
        server.send_message(message)



def require_env(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {key}")
    return value



def main() -> int:
    load_dotenv()

    city_slug = require_env("CITY_SLUG")
    visa_type = os.getenv("VISA_TYPE", "tourism")
    countries = [c for c in os.getenv("COUNTRIES", "").split(",") if c.strip()]
    url = os.getenv("TARGET_URL") or build_url(city_slug, visa_type)
    state_file = Path(os.getenv("STATE_FILE", ".state/schengen_state.json"))
    always_notify = os.getenv("ALWAYS_NOTIFY", "false").lower() == "true"

    logger.info("Fetching %s", url)
    html = fetch_html(url)
    current_records = parse_page(html)
    current_records = filter_records(current_records, countries)

    if not current_records:
        logger.info("No matching available slots found right now.")
        if os.getenv("SAVE_EMPTY_STATE", "false").lower() == "true":
            save_state(state_file, [])
        return 0

    previous_state = load_state(state_file)
    changed_records = current_records if always_notify else diff_new_or_changed(current_records, previous_state)

    if not changed_records:
        logger.info("Availability exists, but nothing is new or changed since the last run.")
        return 0

    subject = f"Schengen slot alert: {city_slug.title()} {visa_type}"
    body = build_email_body(url, city_slug, visa_type, changed_records)
    send_email(subject, body)
    logger.info("Notification sent for %d record(s).", len(changed_records))

    save_state(state_file, current_records)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        logger.exception("Bot failed: %s", exc)
        raise SystemExit(1)
