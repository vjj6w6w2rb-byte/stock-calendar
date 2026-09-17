#!/usr/bin/env python3
"""Build stocks.ics from primary US-government and issuer sources.

All timed events use America/New_York.  Existing events from a failed source are
kept, so a transient outage cannot empty a subscriber's calendar.
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
from icalendar import Calendar, Event, vText

OUTPUT = Path("stocks.ics")
NY = ZoneInfo("America/New_York")
TODAY = datetime.now(NY).date()
HORIZON = TODAY + timedelta(days=550)
OPTION_HORIZON = TODAY + timedelta(days=60)  # refreshed daily; keeps API responses bounded
TIMEOUT = 25
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "text/calendar,application/json,text/html,*/*",
}
TICKERS = ("CRCL", "MSTR", "TSLA", "POET", "OKLO")

# Official investor-relations pages.  A page can yield no event; this is safer
# than guessing a date from an earnings estimate or a press rumor.
COMPANY_EVENT_PAGES = {
    "CRCL": "https://investor.circle.com/news-events/events-and-presentations",
    "MSTR": "https://www.strategy.com/investor-relations/events-and-presentations",
    "TSLA": "https://ir.tesla.com/",
    "POET": "https://investors.poet-technologies.com/events-and-presentations",
    "OKLO": "https://investors.oklo.com/news-events/events-and-presentations",
}

MACRO_KEYWORDS = {
    "consumer price index": ("🔴【CPI】美国 CPI", "CPI / Core CPI"),
    "producer price index": ("🔴【PPI】美国 PPI", "PPI / Core PPI"),
    "employment situation": ("🟠【NFP】美国非农就业数据", "Nonfarm Payrolls / Unemployment Rate"),
    "employment cost index": ("🟠【ECI】美国 Employment Cost Index", "Employment Cost Index"),
    "personal income and outlays": ("🔴【PCE】美国 PCE", "PCE / Core PCE"),
    "gross domestic product": ("🔴【GDP】美国 GDP", "GDP"),
    "gdp (": ("🔴【GDP】美国 GDP", "GDP"),
    "advance estimate": ("🔴【GDP】美国 GDP", "GDP"),
    "retail sales": ("🟠【零售】美国 Retail Sales", "Retail Sales"),
}


def stable_uid(source: str, identity: str) -> str:
    digest = hashlib.sha256(f"{source}|{identity}".encode()).hexdigest()[:24]
    return f"{source.lower()}-{digest}@stock-calendar"


def event(source: str, identity: str, summary: str, when: datetime, description: str, end: datetime | None = None) -> Event:
    item = Event()
    item.add("uid", stable_uid(source, identity))
    item.add("dtstamp", datetime.now(NY))
    item.add("dtstart", when)
    item.add("dtend", end or when + timedelta(minutes=30))
    item.add("summary", summary)
    item.add("description", description)
    item.add("x-source", source)
    return item


def get(url: str) -> requests.Response:
    response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    return response


def future(when: datetime) -> bool:
    return TODAY - timedelta(days=7) <= when.date() <= HORIZON


def parse_bls() -> list[Event]:
    source = "BLS"
    incoming = Calendar.from_ical(get("https://www.bls.gov/schedule/news_release/bls.ics").content)
    result = []
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
        if future(dtstart):
            title, label = match
            result.append(event(source, f"{raw_summary}|{dtstart.isoformat()}", title, dtstart,
                                f"{label}. Official BLS release calendar.\nSource: https://www.bls.gov/schedule/news_release/bls.ics"))
    return result


def parse_schedule_rows(url: str, source: str) -> list[Event]:
    soup = BeautifulSoup(get(url).text, "html.parser")
    page_text = soup.get_text(" ", strip=True)
    # BEA's schedule heading supplies the year once; individual rows often omit it.
    year_match = re.search(r"\bYear\s+(20\d{2})\b", page_text, re.I) or re.search(r"\b(20\d{2})\b", page_text)
    schedule_year = int(year_match.group(1)) if year_match else TODAY.year
    result = []
    # Official pages present their release date, time and name in table rows.
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
        if future(when):
            title, label = match
            result.append(event(source, f"{text}|{when.isoformat()}", title, when,
                                f"{label}. Official {source} release schedule.\nSource: {url}"))
    return result


def parse_fomc() -> list[Event]:
    source = "FED"
    soup = BeautifulSoup(get("https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm").text, "html.parser")
    heading = next((h for h in soup.find_all(["h3", "h4"]) if "FOMC Meetings" in h.get_text() and str(TODAY.year) in h.get_text()), None)
    if not heading:
        raise ValueError("Current-year FOMC section not found")
    section = []
    for sibling in heading.find_all_next():
        if sibling.name in {"h3", "h4"} and sibling is not heading:
            break
        section.append(sibling.get_text(" ", strip=True))
    text = " ".join(section)
    # Month + '27-28*' (also supports a meeting spanning two months in a simple way).
    pattern = re.compile(r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})-(\d{1,2})(\*)?", re.I)
    result = []
    for month, start_day, end_day, sep in pattern.findall(text):
        start = date_parser.parse(f"{month} {start_day} {TODAY.year}").date()
        end_day_i = int(end_day)
        end = start.replace(day=end_day_i) if end_day_i >= start.day else (start.replace(day=1) + timedelta(days=32)).replace(day=end_day_i)
        if end < TODAY - timedelta(days=7) or start > HORIZON:
            continue
        start_dt = datetime.combine(start, datetime.min.time(), NY)
        end_dt = datetime.combine(end + timedelta(days=1), datetime.min.time(), NY)
        identity = f"{start.isoformat()}-{end.isoformat()}"
        result.append(event(source, identity + "-meeting", "🔴【FOMC】美联储两日会议", start_dt,
                            "Official Federal Reserve FOMC meeting calendar.", end_dt))
        decision = datetime.combine(end, datetime.strptime("14:00", "%H:%M").time(), NY)
        result.append(event(source, identity + "-decision", "🔴【FOMC】美联储利率决议", decision,
                            "Policy statement / rate decision. Official Federal Reserve calendar."))
        result.append(event(source, identity + "-press", "🔴【FOMC】Powell Press Conference", decision + timedelta(minutes=30),
                            "Scheduled after the policy decision. Official Federal Reserve calendar."))
        if sep:
            result.append(event(source, identity + "-sep", "🔴【FOMC】SEP / Dot Plot", decision,
                                "Summary of Economic Projections (SEP / dot plot). Official Federal Reserve calendar."))
    return result


def parse_options() -> list[Event]:
    result = []
    for ticker in TICKERS:
        source = f"NASDAQ-{ticker}"
        # Ask for a rolling window, not the complete multi-year chain. This both
        # confirms actual listings and avoids a needlessly large API response.
        url = (f"https://api.nasdaq.com/api/quote/{ticker}/option-chain?assetclass=stocks"
               f"&fromdate={TODAY.isoformat()}&todate={OPTION_HORIZON.isoformat()}")
        payload = get(url).json()
        data = payload.get("data") or {}
        # The documented endpoint currently exposes one `expirygroup` header per
        # listed expiry in table rows (rather than an expirationDates array).
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
            # Nasdaq only lists contracts actually offered for this symbol.  The
            # listed monthly expiry is explicitly labelled when the API provides it.
            monthly = "monthly" in value.lower() or (expiry.weekday() == 4 and 15 <= expiry.day <= 21)
            kind = "Monthly OpEx" if monthly else "Weekly Expiration"
            icon = "🔴" if monthly else "🟡"
            when = datetime.combine(expiry, datetime.strptime("16:00", "%H:%M").time(), NY)
            result.append(event(source, expiry.isoformat(), f"{icon}【{ticker} 期权】{kind}", when,
                                f"Actual listed {ticker} option expiration, verified from Nasdaq option-chain availability.\nSource: https://www.nasdaq.com/market-activity/stocks/{ticker.lower()}/option-chain"))
    return result


def parse_company_events() -> list[Event]:
    result = []
    # Date-bearing issuer event cards vary across providers. We accept only cards
    # whose own text contains an explicit full date and an event keyword.
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
                when = datetime.combine(date_parser.parse(match.group()).date(), datetime.min.time(), NY)
            except ValueError:
                continue
            if not future(when):
                continue
            short = re.sub(r"\s+", " ", text)[:350]
            if re.search(r"earnings|quarterly results", short, re.I):
                title = f"🟣【{ticker}】财报"
            else:
                title = f"🟣【{ticker}】已确认公司活动"
            result.append(event(source, f"{short}|{when.date()}", title, when, f"{short}\nOfficial investor relations source: {url}"))
    return result


FETCHERS: dict[str, Callable[[], list[Event]]] = {
    "BLS": parse_bls,
    "BEA": lambda: parse_schedule_rows("https://www.bea.gov/news/schedule", "BEA"),
    "CENSUS": lambda: parse_schedule_rows("https://www.census.gov/economic-indicators/calendar-listview.html", "Census"),
    "FED": parse_fomc,
    "OPTIONS": parse_options,
    "COMPANY": parse_company_events,
}


def old_events() -> list[Event]:
    if not OUTPUT.exists():
        return []
    try:
        return [c for c in Calendar.from_ical(OUTPUT.read_bytes()).walk("VEVENT")]
    except Exception as exc:
        logging.warning("Cannot parse previous calendar: %s", exc)
        return []


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    previous = old_events()
    fresh: list[Event] = []
    successful_prefixes: set[str] = set()
    for name, fetcher in FETCHERS.items():
        try:
            events = fetcher()
            fresh.extend(events)
            successful_prefixes.add(name)
            logging.info("%s succeeded: %d events", name, len(events))
        except Exception as exc:
            logging.warning("%s failed; preserving prior events: %s", name, exc)
    # Retain only events belonging to sources that failed. A successful source is
    # authoritative, including when it currently has no matching future events.
    retained = []
    for item in previous:
        source = str(item.get("x-source", ""))
        group = "OPTIONS" if source.startswith("NASDAQ-") else "COMPANY" if source.startswith("IR-") else source
        if group not in successful_prefixes:
            retained.append(item)
    all_events = {str(e.get("uid")): e for e in retained + fresh}
    calendar = Calendar()
    calendar.add("prodid", "-//stock-calendar//GitHub Actions//EN")
    calendar.add("version", "2.0")
    calendar.add("calscale", "GREGORIAN")
    calendar.add("x-wr-calname", "US Stocks & Macro Calendar")
    calendar.add("x-wr-timezone", "America/New_York")
    # Embed the DST rules, rather than relying only on a TZID lookup on the phone.
    from icalendar import Timezone
    calendar.add_component(Timezone.from_tzid("America/New_York"))
    for item in sorted(all_events.values(), key=lambda e: e.decoded("dtstart")):
        calendar.add_component(item)
    OUTPUT.write_bytes(calendar.to_ical())
    logging.info("Wrote %s with %d events", OUTPUT, len(all_events))


if __name__ == "__main__":
    main()
