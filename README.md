# Schengen slot notifier

This bot monitors the public summary pages on `schengenappointments.com` and emails you when it sees new or changed appointment availability.

It is designed for pages such as:
- `https://schengenappointments.com/in/dublin/tourism`
- `https://schengenappointments.com/in/london/tourism`
- `https://schengenappointments.com/in/london/business`

It does **not** attempt to log into VFS, solve CAPTCHA, bypass Cloudflare, or automate booking.

## Why this approach

The site already publishes city-specific availability pages and offers its own paid email alerts. Its own copy also notes the underlying visa systems can involve Cloudflare, CAPTCHA, OTP, and IP bans. Monitoring the public availability page is therefore the cleaner and lower-risk route.

## Setup

1. Create and activate a virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Copy the example config:

```bash
cp .env.example .env
```

4. Fill in `.env`.

### Gmail setup

If you use Gmail SMTP:
- enable 2-Step Verification on your Google account
- create an App Password
- use that App Password as `SMTP_PASSWORD`

## Run once

```bash
python get_appointment_updates.py
```

## How filtering works

- `CITY_SLUG=dublin` with `VISA_TYPE=tourism` checks `https://schengenappointments.com/in/dublin/tourism`
- `COUNTRIES=Iceland,Netherlands` means you only get emailed for those destinations
- leave `COUNTRIES` blank to notify on any country listed as available on that page

## Avoid repeated alerts

The script stores the last seen availability in `STATE_FILE` and only emails when availability is new or has changed.

## Cron example

Run every 5 minutes:

```cron
*/5 * * * * cd /path/to/schengen_slot_bot && /path/to/venv/bin/python schengen_slot_notifier.py >> bot.log 2>&1
```

## GitHub Actions option

You can also run this in GitHub Actions on a schedule by storing the `.env` values as repository secrets.
A starter workflow is included at `.github/workflows/check-slots.yml`.

## Notes

- The page structure may change. If parsing breaks, the script will exit with an error instead of silently pretending everything is fine.
- Appointment data on the public page may disappear quickly because someone else books first.
- You should review the website’s terms and usage expectations before running frequent checks.
