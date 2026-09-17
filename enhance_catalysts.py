#!/usr/bin/env python3
"""补充 TSLA/CRCL/MSTR/POET/OKLO 的重大公司催化事件。

只使用公司官方投资者关系/新闻页面；只加入文本中有明确日期的事件，不把传闻或未定日期加入日历。
所有事件标题使用股票代码 + 中文事件名称；没有精确时间的重大催化按全天事件处理。
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
TODAY = datetime.now(BJ).date()
HORIZON = TODAY + timedelta(days=550)
TIMEOUT = 25
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "text/html,*/*"}

SOURCES = {
    "TSLA": ["https://ir.tesla.com/"],
    "CRCL": ["https://investor.circle.com/", "https://investor.circle.com/news/default.aspx"],
    "MSTR": ["https://www.strategy.com/press", "https://www.strategy.com/investor-relations"],
    "POET": ["https://investors.poet-technologies.com/", "https://investors.poet-technologies.com/news-events"],
    "OKLO": ["https://investors.oklo.com/", "https://investors.oklo.com/news-events"],
}

# 只抓可能显著影响公司中短期基本面/估值预期的官方事件。
RULES = {
    "TSLA": [
        (re.compile(r"robotaxi|cybercab|autonomous|autonomy", re.I), "Robotaxi/自动驾驶重大进展"),
        (re.compile(r"FSD|full self-driving", re.I), "FSD 重大进展"),
        (re.compile(r"product reveal|delivery event|launch|starts? deliveries|begin(?:s|ning)? deliveries", re.I), "产品发布/交付重大事件"),
        (re.compile(r"Optimus|Tesla Bot", re.I), "Optimus 重大进展"),
    ],
    "CRCL": [
        (re.compile(r"\bArc\b.*(?:mainnet|launch|go live|validator|integration)|(?:mainnet|launch).*\bArc\b", re.I), "Arc 主网/生态重大进展"),
        (re.compile(r"USDC|EURC|stablecoin", re.I), "稳定币业务重大进展"),
        (re.compile(r"charter|license|regulat|approval", re.I), "监管/牌照重大进展"),
        (re.compile(r"partnership|integration|acquire|acquisition", re.I), "重大合作/并购"),
    ],
    "MSTR": [
        (re.compile(r"acquires? .*BTC|bitcoin acquisition|purchases? .*bitcoin", re.I), "比特币增持"),
        (re.compile(r"convertible|notes|preferred|offering|capital raise|ATM", re.I), "融资/资本市场事件"),
        (re.compile(r"repurchase|buyback", re.I), "回购事件"),
    ],
    "POET": [
        (re.compile(r"production|mass production|volume production|shipment|ships|customer", re.I), "量产/客户重大进展"),
        (re.compile(r"partnership|collaboration|design win|purchase order|order", re.I), "重大订单/合作"),
        (re.compile(r"optical engine|light source|AI|data center", re.I), "核心产品重大进展"),
    ],
    "OKLO": [
        (re.compile(r"NRC|license|permit|authorization|regulat", re.I), "核监管/许可重大进展"),
        (re.compile(r"power purchase|PPA|power agreement|electricity|offtake", re.I), "电力协议重大进展"),
        (re.compile(r"construction|groundbreaking|site|deployment|Aurora", re.I), "项目建设/部署重大进展"),
        (re.compile(r"partnership|agreement|contract|customer", re.I), "重大合作/合同"),
    ],
}

DATE_PATTERNS = [
    re.compile(r"(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+20\d{2}", re.I),
    re.compile(r"\b20\d{2}-\d{2}-\d{2}\b"),
    re.compile(r"\b\d{1,2}/\d{1,2}/20\d{2}\b"),
]


def uid(ticker: str, day: date, title: str, url: str) -> str:
    raw = f"CATALYST|{ticker}|{day.isoformat()}|{title}|{url}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24] + "@stock-calendar"


def get(url: str) -> requests.Response:
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r


def dates_in(text: str) -> list[tuple[date, int]]:
    out = []
    for p in DATE_PATTERNS:
        for m in p.finditer(text):
            try:
                out.append((date_parser.parse(m.group()).date(), m.start()))
            except (ValueError, OverflowError):
                pass
    return out


def candidate_pages(base: str) -> list[str]:
    try:
        soup = BeautifulSoup(get(base).text, "html.parser")
    except Exception:
        return [base]
    host = urlparse(base).netloc
    urls = [base]
    for a in soup.find_all("a", href=True):
        href = urljoin(base, a["href"])
        label = a.get_text(" ", strip=True)
        if urlparse(href).netloc != host:
            continue
        if re.search(r"news|press|event|presentation|investor|release", label + " " + href, re.I):
            urls.append(href)
        if len(urls) >= 35:
            break
    return list(dict.fromkeys(urls))


def add_event(cal: Calendar, ticker: str, day: date, title: str, url: str, excerpt: str) -> None:
    ev = Event()
    ev.add("uid", uid(ticker, day, title, url))
    ev.add("dtstamp", datetime.now(BJ))
    ev.add("dtstart", day)
    ev.add("dtend", day + timedelta(days=1))
    ev.add("summary", f"{ticker} {title}")
    ev.add("description", f"官方来源：{ticker} 公司官网/投资者关系\n官方网址：{url}\n官方内容摘要：{excerpt[:500]}")
    ev.add("x-source", f"CATALYST-{ticker}")
    cal.add_component(ev)


def process(cal: Calendar, ticker: str, bases: list[str]) -> None:
    seen: set[tuple[date, str]] = set()
    rules = RULES[ticker]
    for base in bases:
        for url in candidate_pages(base):
            try:
                soup = BeautifulSoup(get(url).text, "html.parser")
            except Exception:
                continue
            text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
            for day, pos in dates_in(text):
                if not (TODAY - timedelta(days=30) <= day <= HORIZON):
                    continue
                window = text[max(0, pos - 450): min(len(text), pos + 1200)]
                for pattern, title in rules:
                    if not pattern.search(window):
                        continue
                    key = (day, title)
                    if key in seen:
                        continue
                    seen.add(key)
                    add_event(cal, ticker, day, title, url, window)


def main() -> None:
    if not OUTPUT.exists():
        raise SystemExit("stocks.ics 不存在")
    cal = Calendar.from_ical(OUTPUT.read_bytes())

    # 每次重建本脚本产生的事件，避免官方日期调整后旧事件残留。
    cal.subcomponents = [
        c for c in cal.subcomponents
        if not (getattr(c, "name", "") == "VEVENT" and str(c.get("x-source", "")).startswith("CATALYST-"))
    ]

    for ticker, bases in SOURCES.items():
        try:
            process(cal, ticker, bases)
        except Exception:
            continue

    OUTPUT.write_bytes(cal.to_ical())


if __name__ == "__main__":
    main()
