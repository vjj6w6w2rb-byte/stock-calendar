#!/usr/bin/env python3
"""生成美股、美国宏观、美联储、期权到期与美股休市订阅日历。

定时事件最终统一转换为北京时间 Asia/Shanghai；期权到期日与休市日使用全天事件。
如果某个数据源暂时失败，则保留该来源上一次的有效事件，避免订阅日历突然清空。
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from dateutil import parser as date_parser
from dateutil.easter import easter
from icalendar import Calendar, Event, Timezone

OUTPUT = Path("stocks.ics")
NY = ZoneInfo("America/New_York")
BJ = ZoneInfo("Asia/Shanghai")
TODAY = datetime.now(BJ).date()
HORIZON = TODAY + timedelta(days=550)
OPTION_HORIZON = TODAY + timedelta(days=60)
TIMEOUT = 25
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "text/calendar,application/json,text/html,*/*",
}
TICKERS = ("CRCL", "MSTR", "TSLA", "POET", "OKLO")

COMPANY_EVENT_PAGES = {
    "CRCL": "https://investor.circle.com/news-events/events-and-presentations",
    "MSTR": "https://www.strategy.com/investor-relations/events-and-presentations",
    "TSLA": "https://ir.tesla.com/",
    "POET": "https://investors.poet-technologies.com/events-and-presentations",
    "OKLO": "https://investors.oklo.com/news-events/events-and-presentations",
}

MACRO_KEYWORDS = {
    "consumer price index": ("美国消费者价格指数", "消费者价格指数及核心消费者价格指数"),
    "producer price index": ("美国生产者价格指数", "生产者价格指数及核心生产者价格指数"),
    "employment situation": ("美国非农就业数据", "非农就业、失业率等就业数据"),
    "employment cost index": ("美国就业成本指数", "就业成本指数"),
    "personal income and outlays": ("美国个人消费支出物价指数", "个人消费支出物价指数及核心个人消费支出物价指数"),
    "gross domestic product": ("美国国内生产总值", "国内生产总值"),
    "gdp (": ("美国国内生产总值", "国内生产总值"),
    "advance estimate": ("美国国内生产总值", "国内生产总值"),
    "retail sales": ("美国零售销售", "零售销售"),
}


def stable_uid(source: str, identity: str) -> str:
    digest = hashlib.sha256(f"{source}|{identity}".encode()).hexdigest()[:24]
    return f"{source.lower()}-{digest}@stock-calendar"


def timed_event(source: str, identity: str, summary: str, when: datetime, description: str,
                end: datetime | None = None) -> Event:
    item = Event()
    item.add("uid", stable_uid(source, identity))
    item.add("dtstamp", datetime.now(BJ))
    local = when.astimezone(BJ)
    item.add("dtstart", local)
    item.add("dtend", (end.astimezone(BJ) if end else local + timedelta(minutes=30)))
    item.add("summary", summary)
    item.add("description", description)
    item.add("x-source", source)
    return item


def allday_event(source: str, identity: str, summary: str, day: date, description: str) -> Event:
    item = Event()
    item.add("uid", stable_uid(source, identity))
    item.add("dtstamp", datetime.now(BJ))
    item.add("dtstart", day)
    item.add("dtend", day + timedelta(days=1))
    item.add("summary", summary)
    item.add("description", description)
    item.add("x-source", source)
    return item


def get(url: str) -> requests.Response:
    response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    return response


def future_dt(when: datetime) -> bool:
    return TODAY - timedelta(days=7) <= when.astimezone(BJ).date() <= HORIZON


def parse_bls() -> list[Event]:
    source = "BLS"
    url = "https://www.bls.gov/schedule/news_release/bls.ics"
    incoming = Calendar.from_ical(get(url).content)
    result: list[Event] = []
    for component in incoming.walk("VEVENT"):
        raw_summary = str(component.get("summary", ""))
        lowered = raw_summary.lower()
        match = next(((title, label) for key, (title, label) in MACRO_KEYWORDS.items() if key in lowered), None)
        if not match:
            continue
        dtstart = component.decoded("dtstart")
        if isinstance(dtstart, date) and not isinstance(dtstart, datetime):
            dtstart = datetime.combine(dtstart, datetime.min.time(), NY)
        elif dtstart.tzinfo is None:
            dtstart = dtstart.replace(tzinfo=NY)
        else:
            dtstart = dtstart.astimezone(NY)
        if future_dt(dtstart):
            title, label = match
            result.append(timed_event(source, f"{raw_summary}|{dtstart.isoformat()}", title, dtstart,
                                      f"{label}\n官方来源：美国劳工统计局 BLS\n官方网址：{url}"))
    return result


def parse_schedule_rows(url: str, source: str) -> list[Event]:
    soup = BeautifulSoup(get(url).text, "html.parser")
    page_text = soup.get_text(" ", strip=True)
    year_match = re.search(r"\bYear\s+(20\d{2})\b", page_text, re.I) or re.search(r"\b(20\d{2})\b", page_text)
    schedule_year = int(year_match.group(1)) if year_match else TODAY.year
    result: list[Event] = []
    for row in soup.select("tr"):
        cells = [c.get_text(" ", strip=True) for c in row.select("th, td")]
        text = " | ".join(cells)
        lowered = text.lower()
        match = next(((title, label) for key, (title, label) in MACRO_KEYWORDS.items() if key in lowered), None)
        if not match:
            continue
        found = re.search(r"([A-Z][a-z]+\s+\d{1,2}(?:,?\s+20\d{2})?).*?(\d{1,2}:\d{2}\s*(?:AM|PM))", text, re.I)
        if not found:
            continue
        try:
            date_text, time_text = found.groups()
            if not re.search(r"20\d{2}", date_text):
                date_text = f"{date_text}, {schedule_year}"
            when = date_parser.parse(f"{date_text} {time_text}").replace(tzinfo=NY)
        except (ValueError, OverflowError):
            continue
        if future_dt(when):
            title, label = match
            org = "美国经济分析局 BEA" if source == "BEA" else "美国人口普查局 Census"
            result.append(timed_event(source, f"{text}|{when.isoformat()}", title, when,
                                      f"{label}\n官方来源：{org}\n官方网址：{url}"))
    return result


def parse_fomc() -> list[Event]:
    source = "FED"
    url = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
    soup = BeautifulSoup(get(url).text, "html.parser")
    heading = next((h for h in soup.find_all(["h3", "h4"]) if "FOMC Meetings" in h.get_text() and str(TODAY.year) in h.get_text()), None)
    if not heading:
        raise ValueError("未找到当年 FOMC 日程")
    section = []
    for sibling in heading.parent.find_next_siblings():
        if "panel-heading" in (sibling.get("class") or []):
            break
        section.append(sibling.get_text(" ", strip=True))
    text = " ".join(section)
    pattern = re.compile(r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})-(\d{1,2})(\*)?", re.I)
    result: list[Event] = []
    for month, start_day, end_day, sep in pattern.findall(text):
        start = date_parser.parse(f"{month} {start_day} {TODAY.year}").date()
        end_day_i = int(end_day)
        end = start.replace(day=end_day_i) if end_day_i >= start.day else (start.replace(day=1) + timedelta(days=32)).replace(day=end_day_i)
        if end < TODAY - timedelta(days=7) or start > HORIZON:
            continue
        identity = f"{start.isoformat()}-{end.isoformat()}"
        result.append(allday_event(source, identity + "-meeting", "美联储两日会议", start,
                                   f"美联储联邦公开市场委员会会议\n官方来源：Federal Reserve\n官方网址：{url}"))
        decision = datetime.combine(end, datetime.strptime("14:00", "%H:%M").time(), NY)
        result.append(timed_event(source, identity + "-decision", "美联储利率决议", decision,
                                  f"FOMC 政策声明及利率决议\n官方来源：Federal Reserve\n官方网址：{url}"))
        result.append(timed_event(source, identity + "-press", "鲍威尔新闻发布会", decision + timedelta(minutes=30),
                                  f"美联储主席新闻发布会\n官方来源：Federal Reserve\n官方网址：{url}"))
        if sep:
            result.append(timed_event(source, identity + "-sep", "美联储经济预测与点阵图", decision,
                                      f"经济预测摘要 SEP 与点阵图\n官方来源：Federal Reserve\n官方网址：{url}"))
    return result


def parse_options() -> list[Event]:
    result: list[Event] = []
    for ticker in TICKERS:
        source = f"NASDAQ-{ticker}"
        url = (f"https://api.nasdaq.com/api/quote/{ticker}/option-chain?assetclass=stocks"
               f"&fromdate={TODAY.isoformat()}&todate={OPTION_HORIZON.isoformat()}")
        payload = get(url).json()
        data = payload.get("data") or {}
        expirations = data.get("expirationDates") or [
            row.get("expirygroup") for row in ((data.get("table") or {}).get("rows") or []) if row.get("expirygroup")
        ]
        for value in sorted(set(expirations)):
            value = str(value)
            try:
                expiry = date_parser.parse(value).date()
            except (ValueError, OverflowError):
                continue
            if not (TODAY <= expiry <= OPTION_HORIZON):
                continue
            monthly = "monthly" in value.lower() or (expiry.weekday() == 4 and 15 <= expiry.day <= 21)
            kind = "月度期权到期日" if monthly else "周度期权到期日"
            page = f"https://www.nasdaq.com/market-activity/stocks/{ticker.lower()}/option-chain"
            result.append(allday_event(source, expiry.isoformat(), f"{ticker} {kind}", expiry,
                                       f"{ticker} 实际已挂牌期权到期日\n官方来源：Nasdaq 期权链\n官方网址：{page}"))
    return result


def parse_company_events() -> list[Event]:
    result: list[Event] = []
    keywords = re.compile(r"earnings|quarterly results|annual meeting|investor day|conference|presentation|webcast|product", re.I)
    date_re = re.compile(r"(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+20\d{2}", re.I)
    for ticker, url in COMPANY_EVENT_PAGES.items():
        source = f"IR-{ticker}"
        soup = BeautifulSoup(get(url).text, "html.parser")
        for node in soup.select("article, li, .event, .item, tr"):
            text = node.get_text(" ", strip=True)
            match = date_re.search(text)
            if not match or not keywords.search(text):
                continue
            try:
                day = date_parser.parse(match.group()).date()
            except ValueError:
                continue
            if not (TODAY - timedelta(days=7) <= day <= HORIZON):
                continue
            short = re.sub(r"\s+", " ", text)[:350]
            title = f"{ticker} 财报" if re.search(r"earnings|quarterly results", short, re.I) else f"{ticker} 已确认公司活动"
            result.append(allday_event(source, f"{short}|{day}", title, day,
                                       f"{short}\n官方来源：{ticker} 投资者关系官网\n官方网址：{url}"))
    return result


def nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    return d + timedelta(days=(weekday - d.weekday()) % 7 + 7 * (n - 1))


def last_weekday(year: int, month: int, weekday: int) -> date:
    d = date(year + (month == 12), 1 if month == 12 else month + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def observed(day: date) -> date:
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def parse_market_holidays() -> list[Event]:
    source = "MARKET-HOLIDAY"
    url = "https://www.nyse.com/markets/hours-calendars"
    result: list[Event] = []
    for year in range(TODAY.year, HORIZON.year + 1):
        holidays = [
            (observed(date(year, 1, 1)), "元旦"),
            (nth_weekday(year, 1, 0, 3), "马丁·路德·金纪念日"),
            (nth_weekday(year, 2, 0, 3), "美国总统日"),
            (easter(year) - timedelta(days=2), "耶稣受难日"),
            (last_weekday(year, 5, 0), "阵亡将士纪念日"),
            (observed(date(year, 6, 19)), "六月节"),
            (observed(date(year, 7, 4)), "美国独立日"),
            (nth_weekday(year, 9, 0, 1), "美国劳动节"),
            (nth_weekday(year, 11, 3, 4), "感恩节"),
            (observed(date(year, 12, 25)), "圣诞节"),
        ]
        for day, name in holidays:
            if TODAY - timedelta(days=7) <= day <= HORIZON:
                result.append(allday_event(source, f"closed-{day.isoformat()}", f"美股休市：{name}", day,
                                           f"纽约证券交易所休市日\n官方来源：NYSE\n官方网址：{url}"))
        thanksgiving = nth_weekday(year, 11, 3, 4)
        early_days = [
            (thanksgiving + timedelta(days=1), "感恩节次日"),
            (date(year, 12, 24), "平安夜"),
        ]
        for day, name in early_days:
            if day.weekday() < 5 and TODAY - timedelta(days=7) <= day <= HORIZON:
                close_ny = datetime.combine(day, datetime.strptime("13:00", "%H:%M").time(), NY)
                close_bj = close_ny.astimezone(BJ)
                result.append(allday_event(source, f"early-{day.isoformat()}", f"美股提前收市：{name}", day,
                                           f"美东时间 13:00 提前收市；北京时间 {close_bj:%H:%M}\n官方来源：NYSE\n官方网址：{url}"))
    return result


FETCHERS: dict[str, Callable[[], list[Event]]] = {
    "BLS": parse_bls,
    "BEA": lambda: parse_schedule_rows("https://www.bea.gov/news/schedule", "BEA"),
    "CENSUS": lambda: parse_schedule_rows("https://www.census.gov/economic-indicators/calendar-listview.html", "Census"),
    "FED": parse_fomc,
    "OPTIONS": parse_options,
    "COMPANY": parse_company_events,
    "MARKET-HOLIDAY": parse_market_holidays,
}


def old_events() -> list[Event]:
    if not OUTPUT.exists():
        return []
    try:
        return [c for c in Calendar.from_ical(OUTPUT.read_bytes()).walk("VEVENT")]
    except Exception as exc:
        logging.warning("无法解析上一次日历：%s", exc)
        return []


def source_group(item: Event) -> str:
    source = str(item.get("x-source", ""))
    if source.startswith("NASDAQ-"):
        return "OPTIONS"
    if source.startswith("IR-"):
        return "COMPANY"
    return source


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    previous = old_events()
    fresh: list[Event] = []
    successful: set[str] = set()
    for name, fetcher in FETCHERS.items():
        try:
            events = fetcher()
            fresh.extend(events)
            successful.add(name)
            logging.info("%s 成功：%d 个事件", name, len(events))
        except Exception as exc:
            logging.warning("%s 失败，保留上一次事件：%s", name, exc)
    retained = [item for item in previous if source_group(item) not in successful]
    all_events = {str(e.get("uid")): e for e in retained + fresh}

    calendar = Calendar()
    calendar.add("prodid", "-//stock-calendar//GitHub Actions//ZH-CN")
    calendar.add("version", "2.0")
    calendar.add("calscale", "GREGORIAN")
    calendar.add("x-wr-calname", "美股重要事件日历")
    calendar.add("x-wr-timezone", "Asia/Shanghai")
    calendar.add_component(Timezone.from_tzid("Asia/Shanghai"))

    def sort_key(e: Event):
        value = e.decoded("dtstart")
        if isinstance(value, datetime):
            return value.astimezone(BJ)
        return datetime.combine(value, datetime.min.time(), BJ)

    for item in sorted(all_events.values(), key=sort_key):
        calendar.add_component(item)
    OUTPUT.write_bytes(calendar.to_ical())
    logging.info("已生成 %s，共 %d 个事件", OUTPUT, len(all_events))


if __name__ == "__main__":
    main()
