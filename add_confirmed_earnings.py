#!/usr/bin/env python3
"""把已经由公司官方确认、但自动网页解析暂时抓不到的财报事件补进 stocks.ics。

这些事件只作为当前确认信息的兜底；主抓取器 enhance_earnings.py 仍负责后续自动发现新日期。
"""
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from icalendar import Calendar, Event

OUTPUT = Path("stocks.ics")
ET = ZoneInfo("America/New_York")
SOURCE_PREFIX = "IR-CONFIRMED-"

CONFIRMED = [
    {
        "ticker": "TSLA",
        "period": "2026-Q3",
        "day": date(2026, 10, 21),
        "release_note": "盘后",
        "call": time(17, 30),
        "url": "https://ir.tesla.com/press-release/tesla-third-quarter-2026-production-deliveries-and-deployments",
    },
    {
        "ticker": "MSTR",
        "period": "2026-Q3",
        "day": date(2026, 10, 29),
        "release_note": "盘后",
        "call": time(17, 0),
        "url": "https://www.strategy.com/press/strategy-announces-earnings-release-date-and-live-video-webinar-for-third-quarter-2026-financial-results_10-08-2026",
    },
    {
        "ticker": "SNDK",
        "period": "FY2027-Q1",
        "day": date(2026, 10, 29),
        "release_note": "具体发布时间未公布",
        "call": time(16, 30),
        "url": "https://investor.sandisk.com/news-events/events",
    },
    {
        "ticker": "CRCL",
        "period": "2026-Q3",
        "day": date(2026, 11, 4),
        "release_note": "具体发布时间未公布",
        "call": time(8, 0),
        "url": "https://investor.circle.com/news/news-details/2026/Circle-to-Announce-Q3-2026-Financial-Results-on-November-4-2026/default.aspx",
    },
    {
        "ticker": "ORCL",
        "period": "FY2027-Q2",
        "day": date(2026, 12, 14),
        "release_note": "具体发布时间未公布",
        "call": None,
        "url": "https://investor.oracle.com/faq/default.aspx",
    },
]


def uid(ticker: str, kind: str, period: str) -> str:
    return f"confirmed-{ticker.lower()}-{period.lower()}-{kind}@stock-calendar"


def add_release(cal: Calendar, item: dict) -> None:
    ev = Event()
    ev.add("uid", uid(item["ticker"], "release", item["period"]))
    ev.add("dtstamp", datetime.now(ET))
    ev.add("dtstart", item["day"])
    ev.add("dtend", item["day"] + timedelta(days=1))
    ev.add("summary", f'{item["ticker"]} 财报发布（{item["release_note"]}）')
    ev.add("description", f'公司官方已确认财报日期。\n官方来源：{item["ticker"]} 投资者关系官网\n官方网址：{item["url"]}')
    ev.add("x-source", SOURCE_PREFIX + item["ticker"])
    cal.add_component(ev)


def add_call(cal: Calendar, item: dict) -> None:
    if item["call"] is None:
        return
    start = datetime.combine(item["day"], item["call"], ET)
    ev = Event()
    ev.add("uid", uid(item["ticker"], "call", item["period"]))
    ev.add("dtstamp", datetime.now(ET))
    ev.add("dtstart", start)
    ev.add("dtend", start + timedelta(hours=1))
    ev.add("summary", f'{item["ticker"]} 电话会议')
    ev.add("description", f'美东时间：{start:%Y-%m-%d %H:%M}\n官方来源：{item["ticker"]} 投资者关系官网\n官方网址：{item["url"]}')
    ev.add("x-source", SOURCE_PREFIX + item["ticker"])
    cal.add_component(ev)


def main() -> None:
    if not OUTPUT.exists():
        raise SystemExit("stocks.ics 不存在")
    cal = Calendar.from_ical(OUTPUT.read_bytes())
    cal.subcomponents = [
        c for c in cal.subcomponents
        if not (
            getattr(c, "name", "") == "VEVENT"
            and str(c.get("x-source", "")).startswith(SOURCE_PREFIX)
        )
    ]
    for item in CONFIRMED:
        add_release(cal, item)
        add_call(cal, item)
    OUTPUT.write_bytes(cal.to_ical())


if __name__ == "__main__":
    main()
