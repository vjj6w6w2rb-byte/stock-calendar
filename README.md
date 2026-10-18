# Stock calendar

## Eastern Time Apple Calendar subscription

The GitHub Pages deployment publishes a DST-safe, read-only Apple Calendar feed for US stock-market times:

- 00:00
- 04:00 — 盘前开盘
- 09:30 — 开盘
- 20:00 — 夜盘开盘 (Sunday through Thursday; labelled as the next trading day)

Its public URL is:

`https://vjj6w6w2rb-byte.github.io/stock-calendar/eastern-time.ics`

All events are stored in `America/New_York`; Apple Calendar converts them automatically for `Asia/Shanghai`, including US daylight saving time.

## Subscribe

On iPhone, open Calendar → Calendars → Add Calendar → Add Subscription Calendar, then paste the public URL above.
