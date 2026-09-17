# Stock calendar

An iPhone-friendly ICS calendar for `CRCL`, `MSTR`, `TSLA`, `POET`, and `OKLO`, plus selected US macro releases, FOMC events, and actual listed options expirations.

## Subscribe

After this repository is pushed to GitHub, subscribe in iPhone Calendar with:

`https://raw.githubusercontent.com/<USERNAME>/stock-calendar/main/stocks.ics`

Use the **raw** URL (not the GitHub file page). Events are stored in `America/New_York`; Apple Calendar converts them automatically for `Asia/Shanghai`, including US daylight saving time.

## Data policy

- BLS macro releases are read from its official ICS calendar.
- BEA and Census releases are read from their official schedules.
- FOMC events come from the Federal Reserve calendar. SEP meetings are marked from the official `*` notation.
- Option dates are pulled per ticker from Nasdaq's option-chain availability; monthly OpEx is highlighted separately.
- Company events are collected only from issuer investor-relations pages where an explicit date is published. Estimates and rumours are not added.

The workflow runs twice daily and may also be run manually from Actions. If one upstream source is unavailable, its existing events are retained rather than removed.

## Run locally

```bash
python3.12 -m pip install -r requirements.txt
python3.12 generate_calendar.py
```
