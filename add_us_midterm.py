#!/usr/bin/env python3
"""把美国 2026 年中期选举加入订阅日历。"""
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from icalendar import Calendar, Event

OUTPUT = Path("stocks.ics")
BJ = ZoneInfo("Asia/Shanghai")
SOURCE = "US-MIDTERM-2026"
ELECTION_DAY = date(2026, 11, 3)


def main() -> None:
    if not OUTPUT.exists():
        raise SystemExit("stocks.ics 不存在")

    cal = Calendar.from_ical(OUTPUT.read_bytes())

    # 删除旧版本，确保重复运行不会产生重复事件。
    cal.subcomponents = [
        c for c in cal.subcomponents
        if not (
            getattr(c, "name", "") == "VEVENT"
            and str(c.get("x-source", "")) == SOURCE
        )
    ]

    ev = Event()
    ev.add("uid", "us-midterm-2026-11-03@stock-calendar")
    ev.add("dtstamp", datetime.now(BJ))
    ev.add("dtstart", ELECTION_DAY)
    ev.add("dtend", ELECTION_DAY + timedelta(days=1))
    ev.add("summary", "美国中期选举")
    ev.add(
        "description",
        "2026 年美国联邦中期选举日。美国众议院 435 个席位全部改选，参议院约三分之一席位改选。\n"
        "官方来源：美国联邦选举委员会 FEC、USAGov\n"
        "FEC：https://www.fec.gov/introduction-campaign-finance/election-results-and-voting-information/\n"
        "USAGov：https://www.usa.gov/midterm-elections",
    )
    ev.add("x-source", SOURCE)
    cal.add_component(ev)

    OUTPUT.write_bytes(cal.to_ical())


if __name__ == "__main__":
    main()
