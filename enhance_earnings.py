#!/usr/bin/env python3
"""自动补充所有跟踪股票的财报发布与电话会议。

规则：
- 只使用公司官方 Investor Relations / News / Events 页面及其官方详情页。
- 有精确时间则写入美东时间 America/New_York；只有盘前/盘后则不虚构分钟。
- 每家公司独立刷新；某家公司本轮抓取失败或未抓到新数据时，保留该公司上一次有效事件。
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from dateutil import parser as date_parser
from icalendar import Calendar, Event

OUTPUT = Path("stocks.ics")
ET = ZoneInfo("America/New_York")
CT = ZoneInfo("America/Chicago")
MT = ZoneInfo("America/Denver")
PT = ZoneInfo("America/Los_Angeles")
TODAY = datetime.now(ET).date()
HORIZON = TODAY + timedelta(days=550)
TIMEOUT = 25
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "text/html,*/*"}

# 只包含当前公开上市、需要自动跟踪财报的股票。
IR_PAGES: dict[str, list[str]] = {
    "CRCL": [
        "https://investor.circle.com/",
        "https://investor.circle.com/news-events/events-and-presentations",
        "https://investor.circle.com/news/default.aspx",
    ],
    "MSTR": [
        "https://www.strategy.com/investor-relations",
        "https://www.strategy.com/press",
    ],
    "TSLA": [
        "https://ir.tesla.com/",
    ],
    "POET": [
        "https://investors.poet-technologies.com/",
        "https://investors.poet-technologies.com/events-and-presentations",
        "https://investors.poet-technologies.com/news-events",
    ],
    "OKLO": [
        "https://investors.oklo.com/",
        "https://investors.oklo.com/news-events/events-and-presentations",
        "https://investors.oklo.com/news-events",
    ],
    "NVDA": [
        "https://investor.nvidia.com/",
        "https://investor.nvidia.com/events-and-presentations/default.aspx",
        "https://investor.nvidia.com/news-and-events/default.aspx",
    ],
    "MU": [
        "https://investors.micron.com/",
        "https://investors.micron.com/events-and-presentations/default.aspx",
        "https://investors.micron.com/news/press-release/default.aspx",
    ],
    "SNDK": [
        "https://investor.sandisk.com/",
        "https://investor.sandisk.com/news-events/events",
        "https://investor.sandisk.com/news-events/news-releases",
    ],
    "ORCL": [
        "https://investor.oracle.com/",
        "https://investor.oracle.com/investor-news/default.aspx",
    ],
}

# 同时支持 October 21, 2026 和 Oct 21, 2026。Tesla IR 首页使用缩写月份。
DATE_PATTERNS = [
    re.compile(
        r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
        r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
        r"\s+\d{1,2},?\s+20\d{2}",
        re.I,
    ),
    re.compile(r"\b\d{1,2}/\d{1,2}/20\d{2}\b"),
    re.compile(r"\b20\d{2}-\d{2}-\d{2}\b"),
]
TIME_RE = re.compile(
    r"\b(\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?|AM|PM))\s*"
    r"(ET|EST|EDT|Eastern Time|CT|CST|CDT|Central Time|MT|MST|MDT|Mountain Time|PT|PST|PDT|Pacific Time)\b",
    re.I,
)
CALL_WORDS = re.compile(
    r"conference call|earnings call|financial call|analyst call|post earnings|webcast|webinar|Q&A|question and answer|"
    r"discuss (?:the )?(?:financial )?results|live video|livestream",
    re.I,
)
RELEASE_WORDS = re.compile(
    r"earnings date|earnings announcement|earnings release|financial results|quarterly results|"
    r"report(?:s|ed|ing)?[^.]{0,100}(?:results|earnings)|release(?:s|d|ing)?[^.]{0,100}(?:results|earnings)|"
    r"announce(?:s|d|ing)?[^.]{0,100}(?:results|earnings)|post(?:s|ed|ing)?[^.]{0,100}(?:results|earnings)",
    re.I,
)
LINK_WORDS = re.compile(
    r"earnings|financial|quarter|results|webcast|conference|call|investor|press|release|production|deliver|event|presentation",
    re.I,
)
AFTER_CLOSE = re.compile(r"after (?:the )?(?:u\.?s\.? )?(?:financial )?markets? close|after market close|after the close", re.I)
BEFORE_OPEN = re.compile(r"before (?:the )?(?:u\.?s\.? )?(?:financial )?markets? open|before market open|before the open", re.I)


def stable_uid(ticker: str, kind: str, period: str) -> str:
    raw = f"IR-TIMES|{ticker}|{kind}|{period}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24] + "@stock-calendar"


def get(url: str) -> requests.Response:
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r


def zone_for(label: str) -> ZoneInfo:
    label = label.lower()
    if label.startswith(("ct", "cst", "cdt", "central")):
        return CT
    if label.startswith(("mt", "mst", "mdt", "mountain")):
        return MT
    if label.startswith(("pt", "pst", "pdt", "pacific")):
        return PT
    return ET


def parse_times(text: str, day: date) -> list[tuple[datetime, str]]:
    out: list[tuple[datetime, str]] = []
    for m in TIME_RE.finditer(text):
        try:
            t = date_parser.parse(m.group(1)).time().replace(tzinfo=None)
            out.append((datetime.combine(day, t, zone_for(m.group(2))), m.group(0)))
        except (ValueError, OverflowError):
            continue
    return out


def period_key(text: str, day: date) -> str:
    q = re.search(r"\bQ([1-4])\s*(20\d{2})\b|\b(20\d{2})\s*Q([1-4])\b", text, re.I)
    if q:
        if q.group(1):
            return f"{q.group(2)}-Q{q.group(1)}"
        return f"{q.group(3)}-Q{q.group(4)}"
    named = re.search(r"\b(first|second|third|fourth)\s+quarter(?:\s+(?:fiscal\s+)?)?(20\d{2})", text, re.I)
    if named:
        num = {"first": 1, "second": 2, "third": 3, "fourth": 4}[named.group(1).lower()]
        return f"{named.group(2)}-Q{num}"
    return day.isoformat()


def add_timed(events: list[Event], ticker: str, kind: str, day: date, when: datetime,
              source_url: str, source_text: str, period: str) -> None:
    local = when.astimezone(ET)
    ev = Event()
    ev.add("uid", stable_uid(ticker, kind, period))
    ev.add("dtstamp", datetime.now(ET))
    ev.add("dtstart", local)
    ev.add("dtend", local + timedelta(minutes=60 if kind == "电话会议" else 15))
    ev.add("summary", f"{ticker} {kind}")
    ev.add("description", f"美东时间：{local:%Y-%m-%d %H:%M}\n官方来源：{ticker} 投资者关系官网\n官方网址：{source_url}\n原文摘要：{source_text[:500]}")
    ev.add("x-source", f"IR-TIMES-{ticker}")
    events.append(ev)


def add_date_only(events: list[Event], ticker: str, day: date, source_url: str, note: str,
                  source_text: str, period: str) -> None:
    ev = Event()
    ev.add("uid", stable_uid(ticker, "财报发布", period))
    ev.add("dtstamp", datetime.now(ET))
    ev.add("dtstart", day)
    ev.add("dtend", day + timedelta(days=1))
    ev.add("summary", f"{ticker} 财报发布（{note}）")
    ev.add("description", f"官方已确认财报日期/时段，但未公布精确发布时间。\n官方来源：{ticker} 投资者关系官网\n官方网址：{source_url}\n原文摘要：{source_text[:500]}")
    ev.add("x-source", f"IR-TIMES-{ticker}")
    events.append(ev)


def candidate_pages(base_url: str) -> list[str]:
    try:
        soup = BeautifulSoup(get(base_url).text, "html.parser")
    except Exception:
        return [base_url]
    urls = [base_url]
    host = urlparse(base_url).netloc
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"])
        label = a.get_text(" ", strip=True)
        href_host = urlparse(href).netloc
        if not (href_host == host or href_host.endswith("." + host) or host.endswith("." + href_host)):
            continue
        if LINK_WORDS.search(label + " " + href):
            urls.append(href)
        if len(urls) >= 60:
            break
    return list(dict.fromkeys(urls))


def page_text(url: str) -> str:
    soup = BeautifulSoup(get(url).text, "html.parser")
    chunks = [soup.get_text(" ", strip=True)]
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        if script.string:
            chunks.append(script.string)
    return re.sub(r"\s+", " ", " ".join(chunks))


def parse_page(ticker: str, url: str) -> list[Event]:
    text = page_text(url)
    if not (RELEASE_WORDS.search(text) or CALL_WORDS.search(text)):
        return []

    matches = []
    for p in DATE_PATTERNS:
        matches.extend(list(p.finditer(text)))

    result: list[Event] = []
    seen: set[tuple[str, str]] = set()
    for dm in matches:
        try:
            day = date_parser.parse(dm.group()).date()
        except (ValueError, OverflowError):
            continue
        if not (TODAY - timedelta(days=21) <= day <= HORIZON):
            continue

        window = text[max(0, dm.start() - 600): min(len(text), dm.end() + 1500)]
        if not (RELEASE_WORDS.search(window) or CALL_WORDS.search(window)):
            continue
        period = period_key(window, day)
        times = parse_times(window, day)

        if CALL_WORDS.search(window) and times:
            call_when = times[0][0]
            for when, raw in times:
                pos = window.lower().find(raw.lower())
                around = window[max(0, pos - 260): pos + 320] if pos >= 0 else window
                if CALL_WORDS.search(around):
                    call_when = when
                    break
            key = (period, "电话会议")
            if key not in seen:
                seen.add(key)
                add_timed(result, ticker, "电话会议", day, call_when, url, window, period)

        if RELEASE_WORDS.search(window):
            key = (period, "财报发布")
            if key in seen:
                continue
            if AFTER_CLOSE.search(window):
                seen.add(key)
                add_date_only(result, ticker, day, url, "盘后", window, period)
                continue
            if BEFORE_OPEN.search(window):
                seen.add(key)
                add_date_only(result, ticker, day, url, "盘前", window, period)
                continue

            release_when: datetime | None = None
            for when, raw in times:
                pos = window.lower().find(raw.lower())
                around = window[max(0, pos - 280): pos + 300] if pos >= 0 else window[:600]
                if RELEASE_WORDS.search(around) and not CALL_WORDS.search(around):
                    release_when = when
                    break
            seen.add(key)
            if release_when:
                add_timed(result, ticker, "财报发布", day, release_when, url, window, period)
            else:
                add_date_only(result, ticker, day, url, "具体时间未公布", window, period)
    return result


def process_ticker(ticker: str, bases: list[str]) -> list[Event]:
    all_events: dict[str, Event] = {}
    visited: set[str] = set()
    for base in bases:
        for url in candidate_pages(base):
            if url in visited:
                continue
            visited.add(url)
            try:
                for ev in parse_page(ticker, url):
                    all_events[str(ev.get("uid"))] = ev
            except Exception as exc:
                logging.debug("%s 页面失败 %s: %s", ticker, url, exc)
    return list(all_events.values())


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if not OUTPUT.exists():
        raise SystemExit("stocks.ics 不存在")
    cal = Calendar.from_ical(OUTPUT.read_bytes())

    old_by_ticker: dict[str, list[Event]] = {ticker: [] for ticker in IR_PAGES}
    base_components = []
    for c in list(cal.subcomponents):
        if getattr(c, "name", "") != "VEVENT":
            base_components.append(c)
            continue
        source = str(c.get("x-source", ""))
        if source.startswith("IR-TIMES-"):
            ticker = source.removeprefix("IR-TIMES-")
            if ticker in old_by_ticker:
                old_by_ticker[ticker].append(c)
                continue
        base_components.append(c)

    cal.subcomponents = base_components

    total = 0
    for ticker, bases in IR_PAGES.items():
        try:
            fresh = process_ticker(ticker, bases)
        except Exception as exc:
            logging.warning("%s 财报抓取失败：%s", ticker, exc)
            fresh = []
        chosen = fresh if fresh else old_by_ticker.get(ticker, [])
        for ev in chosen:
            cal.add_component(ev)
        total += len(chosen)
        logging.info("%s 财报事件：%d", ticker, len(chosen))

    OUTPUT.write_bytes(cal.to_ical())
    logging.info("财报增强完成，共 %d 个事件", total)


if __name__ == "__main__":
    main()
