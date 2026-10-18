#!/usr/bin/env python3
"""Generate a DST-safe Apple Calendar subscription in America/New_York."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
OUTPUT = Path("public/eastern-time.ics")
PAST_DAYS = 30
FUTURE_DAYS = 400
DAY_EVENTS = (
    ("00", 0, 0, "00:00"),
    ("04", 4, 0, "盘前开盘"),
    ("09", 9, 30, "开盘"),
)
OVERNIGHT_EVENT_ID = "18"  # Preserves the UID of the earlier overnight event.


def fold(line: str) -> str:
    """RFC 5545 content-line folding, measured in UTF-8 octets."""
    chunks: list[bytes] = []
    current = b""
    for char in line:
        encoded = char.encode("utf-8")
        if len(current) + len(encoded) > 75:
            chunks.append(current)
            current = b" " + encoded
        else:
            current += encoded
    chunks.append(current)
    return "\r\n".join(chunk.decode("utf-8") for chunk in chunks)


def lines_for_event(
    day: date,
    event_id: str,
    hour: int,
    minute: int,
    label: str,
    stamp: datetime,
    summary_day: date | None = None,
) -> list[str]:
    local = datetime.combine(day, time(hour, minute), NY)
    end = local + timedelta(minutes=1)
    title_day = summary_day or day
    summary = f"美东 {title_day.month}月{title_day.day}日 {label}"
    identity = f"eastern-{day.isoformat()}-{event_id}@eastern-time-calendar"
    return [
        "BEGIN:VEVENT",
        f"UID:{identity}",
        f"DTSTAMP:{stamp.strftime('%Y%m%dT%H%M%SZ')}",
        f"DTSTART;TZID=America/New_York:{local.strftime('%Y%m%dT%H%M%S')}",
        f"DTEND;TZID=America/New_York:{end.strftime('%Y%m%dT%H%M%S')}",
        f"SUMMARY:{summary}",
        "TRANSP:TRANSPARENT",
        "END:VEVENT",
    ]


def calendar_text(today: date | None = None, stamp: datetime | None = None) -> str:
    if today is None:
        today = datetime.now(NY).date()
    if stamp is None:
        stamp = datetime.now(timezone.utc)
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Eastern Time Calendar//EN",
        "CALSCALE:GREGORIAN", "METHOD:PUBLISH", "X-WR-CALNAME:美东工作日时间",
        "X-WR-TIMEZONE:America/New_York",
        "BEGIN:VTIMEZONE", "TZID:America/New_York", "X-LIC-LOCATION:America/New_York",
        "BEGIN:DAYLIGHT", "TZOFFSETFROM:-0500", "TZOFFSETTO:-0400", "TZNAME:EDT",
        "DTSTART:19700308T020000", "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU", "END:DAYLIGHT",
        "BEGIN:STANDARD", "TZOFFSETFROM:-0400", "TZOFFSETTO:-0500", "TZNAME:EST",
        "DTSTART:19701101T020000", "RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU", "END:STANDARD",
        "END:VTIMEZONE",
    ]
    for offset in range(-PAST_DAYS, FUTURE_DAYS + 1):
        day = today + timedelta(days=offset)
        if day.weekday() < 5:
            for event_id, hour, minute, label in DAY_EVENTS:
                lines.extend(lines_for_event(day, event_id, hour, minute, label, stamp))
        if day.weekday() < 4 or day.weekday() == 6:  # Sunday through Thursday
            trade_day = day + timedelta(days=1)
            lines.extend(
                lines_for_event(
                    day,
                    OVERNIGHT_EVENT_ID,
                    20,
                    0,
                    "夜盘开盘",
                    stamp,
                    summary_day=trade_day,
                )
            )
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(line) for line in lines) + "\r\n"


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_bytes(calendar_text().encode("utf-8"))
    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    main()
