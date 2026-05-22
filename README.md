# ICBC Road Test Catcher

Automates monitoring and booking ICBC road test appointments for a target date range.

## Current Project Status

- [x] Core booking flow is implemented in `main.py`
- [x] Supports polling multiple locations via `LOCATION_IDS`
- [x] Automatically detects eligible exam type at login (can override with `EXAM_TYPE`)
- [x] Handles OTP flow through Gmail IMAP
- [x] Can replace an existing booking **only if** an earlier slot is found
- [x] GitHub Actions workflow runs on a 6-hour schedule and manual trigger
- [x] Unit/integration test

## How It Works

1. Validate required environment variables
2. Log in and fetch auth token / driver info
3. Poll appointments and filter by date range
4. If an earlier valid slot is found:
   - lock slot
   - send and read OTP email
   - verify OTP
   - complete booking
5. If user already has a booking, it is cancelled only when a strictly earlier slot is found

## Requirements

- Python 3.9+ (GitHub Actions uses 3.12)
- Gmail account with:
  - 2FA enabled
  - App Password generated
  - IMAP enabled
- Valid ICBC license credentials

## Configuration

Set these environment variables:

### Required

- `USER_LAST_NAME`
- `USER_LICENSE_NUMBER`
- `USER_KEYWORD`
- `USER_GMAIL`
- `USER_GMAIL_APP_PASSWORD`

### Optional

- `DESIRED_DATE_START` (default: `2025-06-24`)
- `DESIRED_DATE_END` (default: `2025-06-30`)
- `LOCATION_IDS` (default: `274`, comma-separated for multiple)
- `EXAM_TYPE` (default fallback: `7-R-1`; script will auto-detect eligible exam on login)

## Local Usage

1. Install dependencies:

```bash
pip install -r requirements.txt
```

2. Export required environment variables.

3. Run:

```bash
python main.py
```

## Docker Usage

1. Fill values in `docker-compose.yml` environment section.
2. Start:

```bash
docker compose up -d
```

3. Logs:

```bash
docker compose logs -f icbc-catcher
```

4. Stop:

```bash
docker compose down
```

## GitHub Actions Automation

Workflow: `.github/workflows/check-appointments.yml`

- Triggered every 6 hours (`cron`)
- Can also be run manually (`workflow_dispatch`)
- Uses repository **Secrets/Variables** for runtime config

Expected settings in repo:

### Secrets
- `USER_LICENSE_NUMBER`
- `USER_KEYWORD`
- `USER_GMAIL_APP_PASSWORD`

### Variables
- `USER_LAST_NAME`
- `USER_GMAIL`
- `DESIRED_DATE_START`
- `DESIRED_DATE_END`
- `LOCATION_IDS`

## Notes

- The script checks every 90 seconds by default.
- Token refresh interval is 1500 seconds by default.
- The process will continuously searching for earlier slot if possible

## Disclaimer

Use at your own risk. You are responsible for complying with ICBC terms and using this tool responsibly.
