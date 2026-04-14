# Reservation Bot

## What this project does
Automates restaurant reservation booking on Resy and OpenTable.
Runs every Sunday morning at a scheduled time via macOS launchd.
Targets Friday dinner (7-10pm), Saturday brunch (11am-1pm), and Saturday dinner
(7-10pm) slots for the coming weekend and up to 4 weeks out.

## Project structure
- config.yaml — restaurant list, platforms, slugs, time preferences
- bot/main.py — entry point, orchestrates the run
- bot/resy.py — Resy API client
- bot/opentable.py — OpenTable web API client
- bot/scheduler.py — date/slot logic
- bot/notify.py — macOS notifications
- com.joey.reservationbot.plist — launchd schedule file
- logs/ — run logs

## Key rules
- Never hammer APIs. One request at a time, 1-2 second delays between calls.
- Resy auth token is stored in .env as RESY_API_KEY
- OpenTable session cookies stored in .env as OT_AUTH_TOKEN
- Always log successes and failures to logs/run.log with timestamps
- On success: send macOS notification with restaurant name, date, time
- On failure: log reason, move on to next restaurant — never crash the whole run
- Party size defaults to 2 unless overridden in config
- Verify Resy venue slugs against the API before attempting any bookings

## How to run manually
python bot/main.py

## Dependencies
See requirements.txt

