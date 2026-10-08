#!/usr/bin/env python3
"""补全全部已上市跟踪股票的期权到期日。

直接读取 Nasdaq 期权链，按股票逐个刷新；单只股票抓取失败时保留该股票上一次的事件，
避免一个来源异常导致全部期权日消失。仅处理实际上市且 Nasdaq 有期权链的股票。
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from dateutil import parser as date_parser
from icalendar import Calendar, Event

OUTPUT = Path("stocks.ics")
TODAY = date.today()
HORIZON = TODAY + timedelta(days=60)
TIMEOUT = 25
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json,text/html,*/*",
}

# 与当前财报跟踪名单中的已上市股票保持一致。
# SpaceX 目前不作为可交易股票处理，因此不生成其期权到期日。
TICKERS = ("CRCL", "MSTR", "TSLA", "POET", "OKLO", "NVDA", "MU", "SNDK", "ORCL")


def stable_uid(ticker: str, expiry: date) -> str:
    digest = hashlib.sha256(f"NASDAQ-{ticker}|{expiry.isoformat()}".encode()).hexdigest()[:24]
    return f"nasdaq-{ticker.lower()}-{digest}@stock-calendar"


def fetch_expiries(ticker: str) -> list[date]:
    url = (
        f"https://api.nasdaq.com/api/quote/{ticker}/option-chain?assetclass=stocks"
        f"&fromdate={TODAY.isoformat()}&todate={HORIZON.isoformat()}"
    )
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    payload = r.json()
    data = payload.get("data") or {}
    values = data.get("expirationDates") or [
        row.get("expirygroup")
        for row in ((data.get("table") or {}).get("rows") or [])
        if row.get("expirygroup")
    ]
    out: set[date] = set()
    for value in values:
        try:
            day = date_parser.parse(str(value)).date()
        except (ValueError, OverflowError):
            continue
        if TODAY <= day <= HORIZON:
            out.add(day)
    return sorted(out)


def make_event(ticker: str, expiry: date) -> Event:
    ev = Event()
    monthly = expiry.weekday() == 4 and 15 <= expiry.day <= 21
    kind = "月度期权到期日" if monthly else "周度期权到期日"
    page = f"https://www.nasdaq.com/market-activity/stocks/{ticker.lower()}/option-chain"
    ev.add("uid", stable_uid(ticker, expiry))
    ev.add("dtstamp", datetime.utcnow())
    ev.add("dtstart", expiry)
    ev.add("dtend", expiry + timedelta(days=1))
    ev.add("summary", f"{ticker} {kind}")
    ev.add("description", f"{ticker} 实际已挂牌期权到期日\n官方来源：Nasdaq 期权链\n官方网址：{page}")
    ev.add("x-source", f"NASDAQ-{ticker}")
    return ev


def main() -> None:
    if not OUTPUT.exists():
        raise SystemExit("stocks.ics 不存在")

    cal = Calendar.from_ical(OUTPUT.read_bytes())

    for ticker in TICKERS:
        try:
            expiries = fetch_expiries(ticker)
        except Exception:
            # 该股票本次抓取失败时，保留旧数据。
            continue

        source = f"NASDAQ-{ticker}"
        cal.subcomponents = [
            c for c in cal.subcomponents
            if not (
                getattr(c, "name", "") == "VEVENT"
                and str(c.get("x-source", "")) == source
            )
        ]
        for expiry in expiries:
            cal.add_component(make_event(ticker, expiry))

    OUTPUT.write_bytes(cal.to_ical())


if __name__ == "__main__":
    main()
