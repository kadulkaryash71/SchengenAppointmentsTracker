# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A bot that monitors the public summary pages on `schengenappointments.com` (e.g.
`https://schengenappointments.com/in/dublin/tourism`) and emails subscribers when new or changed Schengen visa
appointment availability appears. It deliberately does **not** log into VFS, solve CAPTCHA, bypass Cloudflare, or
automate booking — scraping the public availability page is the intentionally lower-risk approach (see README.md
"Why this approach").

## Commands

```bash
pip install -r requirements.txt   # install dependencies
cp .env.example .env              # then fill in .env
python get_appointment_updates.py # run once, in "slots" mode (default)
```

There is no test suite, linter, or build step in this repo.

To run in BCC-change-notification mode instead of slot-checking mode:

```bash
RUN_MODE=bcc-check python get_appointment_updates.py
```

## Architecture

Everything currently lives in a single script, `get_appointment_updates.py`, which supports two run modes selected
by the `RUN_MODE` env var (defaults to slot-checking):

1. **Slot checking** (`_run_slot_check`, default mode)
   - Fetches the target page's HTML table via `fetch_slots_from_table` → `get_soup` (requests + BeautifulSoup).
   - Parses each country row (`get_available_slots`) into a dict of `{country, status, earliest, slots}`. Status is
     `"available"` (a checked date matched `CHECKED_RE`/`DATE_RE`), `"waitlist"` (matched `WAITLIST_RE`), or `None`.
   - Builds a per-row signature (`make_signature`) and diffs against `STATE_FILE` (JSON, default `last_seen.json`,
     see `.env.example` for `STATE_FILE` override) via `get_new_rows` — only genuinely new/changed rows trigger an
     email. This is the anti-spam mechanism described in the README as "Avoid repeated alerts."
   - If there are new rows, emails `EMAIL_TO` (with `EMAIL_BCC` as bcc) via SMTP (`send_email`), including a
     VFS booking link per country built from `COUNTRY_CODES`/`build_vfs_url`.
   - If parsing fails (e.g. site layout changed, table missing), the script raises rather than silently succeeding —
     this is intentional per the README's "Notes" section.

2. **BCC change notification** (`check_bcc_change`, `RUN_MODE=bcc-check`)
   - Compares the current `EMAIL_BCC` env value against `BCC_STATE_FILE` (default `last_bcc.json`).
   - When new addresses appear in `EMAIL_BCC` that weren't there before, sends a welcome email to just the newly
     added addresses (promoting the first new address to `To:` if `EMAIL_TO` is empty) and persists the new BCC
     state.

Both modes log to `scheduler.log` (in the repo root, via Python `logging`) rather than stdout.

### Automation

Two independent GitHub Actions workflows (`.github/workflows/`) drive the script on a schedule, each writing its own
`.env` from repo secrets/vars before invoking `get_appointment_updates.py`:

- `check-slots.yml` — runs every 5 minutes, default `RUN_MODE` (slot checking). Persists `STATE_FILE`
  (`.state/last_seen.json`) across runs using `actions/cache`, keyed by `github.ref`/`run_id` with broadening
  restore-key fallbacks so state survives even without an exact cache hit.
- `check-bcc-change.yml` — runs every 30 minutes with `RUN_MODE=bcc-check`, similarly caching `BCC_STATE_FILE`
  (`last_bcc.json`).

### Landing page (`pages/`) and subscription API (`api/`)

The HTML/CSS subscription landing page (`pages/index.html`, `pages/tos.html`, `pages/assets/css/`) is served by
the same FastAPI app that handles subscriptions — not hosted separately:

```bash
uvicorn api.main:app --reload --port 8000
```

- `api/models.py` — `SubscribeRequest`/`SubscribeResponse` Pydantic models. `visa_type` defaults to `"Tourism"` if
  omitted/blank (the frontend's `#visa-type` select generally tracks Dublin-tourism appointments, so this is
  mostly a fallback).
- `api/store.py` — a `Subscription` class (mirrors `db_utils.py`'s `User`/`Appointment` `to_dict()` style) held in
  a plain in-memory list. Storage is intentionally not persistent yet — nothing survives a server restart.
- `api/main.py` —
  - `POST /api/subscribe` (409 on duplicate email) and a debug-only `GET /api/subscribers`.
  - `GET /api/index/` and `GET /api/tos/` return `pages/index.html`/`pages/tos.html` verbatim as
    `HTMLResponse` — no template engine, since neither page has server-injected variables. The two pages'
    cross-links (`href="/api/tos/"`, `href="/api/index/"`) point at these routes.
  - `pages/assets/` (CSS) is served via a `StaticFiles` mount at `/pages/assets`, matching the `<link>` paths
    already in the HTML — so the HTML's asset references didn't need to change.
  - CORS is wide open (`allow_origins=["*"]`); now mostly moot for the pages themselves since they're same-origin
    with the API, but still relevant if `/api/subscribe` is ever called from elsewhere.

Hosting/deployment of this API (and therefore the pages it now serves) is still an open decision — nothing here
assumes a specific target (no Docker/serverless config exists).

### Database layer (`db_utils.py`)

`db_utils.py` (defines `User`/`Appointment` model classes and a `UserDatabase` (pymongo-backed) for persisting
subscriber emails to MongoDB) is still not imported by any other file. It's scaffolding for a future move from the
in-memory `api/store.py` list to real persistence — not yet integrated with `get_appointment_updates.py` or `api/`.

## Configuration

All runtime config comes from environment variables (loaded via `python-dotenv` from `.env`; see `.env.example` for
the full list and defaults). Key ones:

- `CITY_SLUG` / `VISA_TYPE` / `TARGET_URL` — which page to check (`TARGET_URL` overrides the slug/type-built URL).
- `COUNTRIES` — optional comma-separated allowlist filter; blank means notify for any country on the page.
- `SMTP_*`, `EMAIL_FROM`, `EMAIL_TO`, `EMAIL_BCC` — outbound email delivery.
- `STATE_FILE`, `BCC_STATE_FILE` — where dedup state is persisted between runs.
- `RUN_MODE` — `slots` (default) or `bcc-check`.

Note: `get_appointment_updates.py` currently hardcodes `PAGE_URL` to the Dublin tourism page in `_run_slot_check`
rather than reading `CITY_SLUG`/`VISA_TYPE`/`TARGET_URL` from the environment — keep this in mind if config-driven
URL selection appears to have no effect.
