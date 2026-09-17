#!/usr/bin/env python3
"""补充财报发布时间与电话会议/业绩说明会时间。

只使用公司官方投资者关系页面及其官方详情页。若官方只写“盘前/盘后”而未给出精确时刻，
不会伪造一个时间；会保留日期事件，并在说明中标注“具体时间未公布”。
所有精确时间最终转换为北京时间 Asia/Shanghai。
"""
from __future__ import annotations

import hashlib
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
BJ = ZoneInfo("Asia/Shanghai")
NY = ZoneInfo("America/New_York")
CT = ZoneInfo("America/Chicago")
PT = ZoneInfo("America/Los_Angeles")
TODAY = datetime.now(BJ).date()
HORIZON = TODAY + timedelta(days=550)
TIMEOUT = 25
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "text/html,*/*"}

IR_PAGES = {
    "CRCL": "https://investor.circle.com/",
    "MSTR": "https://www.strategy.com/investor-relations",
    "TSLA": "https://ir.tesla.com/",
    "POET": "https://investors.poet-technologies.com/",
    "OKLO": "https://investors.oklo.com/",
    "NVDA": "https://investor.nvidia.com/",
    "MU": "https://investors.micron.com/",
    "SNDK": "https://investor.sandisk.com/",
    "ORCL": "https://investor.oracle.com/",
    "SPCX": "https://ir.spacex.com/",
}

DATE_PATTERNS = [
    re.compile(r"(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+20\d{2}", re.I),
    re.compile(r"\b\d{1,2}/\d{1,2}/20\d{2}\b"),
]
TIME_RE = re.compile(
    r"\b(\d{1,2}:\d{2}\s*(?:a\.?m\.?|p\.?m\.?|AM|PM))\s*"
    r"(ET|EST|EDT|Eastern Time|CT|CST|CDT|Central Time|PT|PST|PDT|Pacific Time)\b",
    re.I,
)
CALL_WORDS = re.compile(r"conference call|earnings call|financial call|post earnings analyst call|webcast|webinar|Q&A|question and answer|discuss (?:the )?results|live video|livestream", re.I)
RELEASE_WORDS = re.compile(r"report(?:s|ed|ing)? .*financial results|release(?:s|d|ing)? .*financial results|announce(?:s|d|ing)? .*financial results|post(?:s|ed|ing)? .*financial results|earnings release|financial results", re.I)
EARNINGS_LINK_WORDS = re.compile(r"earnings|financial results|quarterly results|webcast|conference call|financial call|results|investor", re.I)


def stable_uid(ticker: str, kind: str, day: date, when: datetime | None = None) -> str:
    raw = f"IR-TIMES|{ticker}|{kind}|{day.isoformat()}|{when.isoformat() if when else ''}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24] + "@stock-calendar"


def get(url: str) -> requests.Response:
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r


def zone_for(label: str) -> ZoneInfo:
    label = label.lower()
    if label.startswith(("ct", "cst", "cdt", "central")):
        return CT
    if label.startswith(("pt", "pst", "pdt", "pacific")):
        return PT
    return NY


def parse_times(text: str, day: date) -> list[tuple[datetime, str]]:
    out = []
    for m in TIME_RE.finditer(text):
        try:
            t = date_parser.parse(m.group(1)).time().replace(tzinfo=None)
            z = zone_for(m.group(2))
            out.append((datetime.combine(day, t, z), m.group(0)))
        except (ValueError, OverflowError):
            continue
    return out


def add_timed(cal: Calendar, ticker: str, kind: str, day: date, when: datetime, source_url: str, source_text: str) -> None:
    ev = Event()
    ev.add("uid", stable_uid(ticker, kind, day, when))
    ev.add("dtstamp", datetime.now(BJ))
    ev.add("dtstart", when.astimezone(BJ))
    ev.add("dtend", when.astimezone(BJ) + timedelta(minutes=45 if kind == "电话会议" else 15))
    ev.add("summary", f"{ticker} 财报{kind}")
    ev.add("description", f"北京时间：{when.astimezone(BJ):%Y-%m-%d %H:%M}\n官方来源：{ticker} 投资者关系官网\n官方网址：{source_url}\n原文摘要：{source_text[:500]}")
    ev.add("x-source", f"IR-TIMES-{ticker}")
    cal.add_component(ev)


def add_date_only(cal: Calendar, ticker: str, kind: str, day: date, source_url: str, note: str) -> None:
    ev = Event()
    ev.add("uid", stable_uid(ticker, kind, day))
    ev.add("dtstamp", datetime.now(BJ))
    ev.add("dtstart", day)
    ev.add("dtend", day + timedelta(days=1))
    ev.add("summary", f"{ticker} 财报发布（{note}）")
    ev.add("description", f"官方仅公布了日期/时段，未公布精确发布时间。\n官方来源：{ticker} 投资者关系官网\n官方网址：{source_url}")
    ev.add("x-source", f"IR-TIMES-{ticker}")
    cal.add_component(ev)


def candidate_pages(base_url: str) -> list[str]:
    soup = BeautifulSoup(get(base_url).text, "html.parser")
    urls = [base_url]
    host = urlparse(base_url).netloc
    for a in soup.find_all("a", href=True):
        label = a.get_text(" ", strip=True)
        href = urljoin(base_url, a["href"])
        if urlparse(href).netloc != host:
            continue
        if EARNINGS_LINK_WORDS.search(label) or EARNINGS_LINK_WORDS.search(href):
            urls.append(href)
        if len(urls) >= 30:
            break
    return list(dict.fromkeys(urls))


def process_ticker(cal: Calendar, ticker: str, base_url: str) -> None:
    seen: set[tuple[str, date, str]] = set()
    for url in candidate_pages(base_url):
        try:
            soup = BeautifulSoup(get(url).text, "html.parser")
        except Exception:
            continue
        text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
        if not (RELEASE_WORDS.search(text) or CALL_WORDS.search(text)):
            continue

        matches = []
        for p in DATE_PATTERNS:
            matches.extend(list(p.finditer(text)))
        for dm in matches:
            try:
                day = date_parser.parse(dm.group()).date()
            except (ValueError, OverflowError):
                continue
            if not (TODAY - timedelta(days=14) <= day <= HORIZON):
                continue
            start = max(0, dm.start() - 320)
            end = min(len(text), dm.end() + 900)
            window = text[start:end]
            if not (RELEASE_WORDS.search(window) or CALL_WORDS.search(window)):
                continue
            times = parse_times(window, day)

            if CALL_WORDS.search(window):
                for when, _raw in times:
                    key = ("电话会议", day, when.isoformat())
                    if key not in seen:
                        seen.add(key)
                        add_timed(cal, ticker, "电话会议", day, when, url, window)
                        break

            if RELEASE_WORDS.search(window):
                release_added = False
                for when, raw in times:
                    pos = window.lower().find(raw.lower())
                    around = window[max(0, pos - 180):pos + 240] if pos >= 0 else window[:420]
                    if RELEASE_WORDS.search(around) and not CALL_WORDS.search(around[:220]):
                        key = ("发布时间", day, when.isoformat())
                        if key not in seen:
                            seen.add(key)
                            add_timed(cal, ticker, "发布时间", day, when, url, window)
                        release_added = True
                        break
                if not release_added:
                    if re.search(r"after (?:the )?(?:u\.?s\.? )?(?:financial )?markets? close|after market close", window, re.I):
                        key = ("盘后", day, "")
                        if key not in seen:
                            seen.add(key)
                            add_date_only(cal, ticker, "发布时间", day, url, "盘后，具体时间未公布")
                    elif re.search(r"before (?:the )?(?:u\.?s\.? )?(?:financial )?markets? open|before market open", window, re.I):
                        key = ("盘前", day, "")
                        if key not in seen:
                            seen.add(key)
                            add_date_only(cal, ticker, "发布时间", day, url, "盘前，具体时间未公布")


def main() -> None:
    if not OUTPUT.exists():
        raise SystemExit("stocks.ics 不存在")
    cal = Calendar.from_ical(OUTPUT.read_bytes())

    kept = []
    for c in list(cal.subcomponents):
        if getattr(c, "name", "") == "VEVENT" and str(c.get("x-source", "")).startswith("IR-TIMES-"):
            continue
        kept.append(c)
    cal.subcomponents = kept

    for ticker, url in IR_PAGES.items():
        try:
            process_ticker(cal, ticker, url)
        except Exception:
            continue

    OUTPUT.write_bytes(cal.to_ical())


if __name__ == "__main__":
    main()
