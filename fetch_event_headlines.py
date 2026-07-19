#!/usr/bin/env python3
"""Match headlines to recently-passed econ_calendar events via Google News RSS.

For each economic calendar event within the last LOOKBACK_DAYS, searches
Google News for that event's keyword set and checks whether any result was
published within [event_date, event_date + MATCH_WINDOW_DAYS]. The first
match found is stored permanently (not overwritten on later runs), so the
dashboard shows a stable follow-up headline rather than one that changes
day to day during its display window.
"""

import os
import sqlite3
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone

import requests

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spreads.db")

GOOGLE_NEWS_RSS_URL = "https://news.google.com/rss/search"

# Keyword set to search per calendar event type. Add an entry here whenever
# a new event name is introduced in fetch_econ_calendar.py, or it will never
# get a matched headline.
EVENT_KEYWORDS = {
    "FOMC Rate Decision": ["Fed", "FOMC", "rate decision", "Powell"],
    "CPI Release": ["CPI", "inflation report", "consumer price index"],
    "Employment Situation (Jobs Report)": ["jobs report", "nonfarm payrolls", "unemployment rate"],
    "GDP Release": ["GDP", "gross domestic product", "economic growth"],
}

LOOKBACK_DAYS = 7      # keep trying to match events that passed within this window
MATCH_WINDOW_DAYS = 2  # a headline counts as a match if published within [event_date, event_date+2]


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS event_headlines (
            event_date TEXT NOT NULL,
            event_name TEXT NOT NULL,
            headline TEXT NOT NULL,
            source TEXT,
            link TEXT,
            matched_at TEXT NOT NULL,
            PRIMARY KEY (event_date, event_name)
        )
        """
    )
    conn.commit()


def search_google_news(keywords: list) -> list:
    query = " OR ".join(f'"{k}"' if " " in k else k for k in keywords)
    params = {"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"}
    url = f"{GOOGLE_NEWS_RSS_URL}?{urllib.parse.urlencode(params)}"
    response = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
    response.raise_for_status()

    root = ET.fromstring(response.content)
    items = []
    for item in root.findall(".//item"):
        title = item.findtext("title") or ""
        link = item.findtext("link") or ""
        pub_date_str = item.findtext("pubDate") or ""
        source_el = item.find("source")
        source = source_el.text if source_el is not None else ""
        # Google News titles end with " - PublisherName"; strip it since we
        # already capture the publisher separately via the <source> tag.
        suffix = f" - {source}"
        if source and title.endswith(suffix):
            title = title[: -len(suffix)]
        try:
            pub_date = datetime.strptime(pub_date_str, "%a, %d %b %Y %H:%M:%S %Z").date()
        except ValueError:
            continue
        items.append({"title": title, "link": link, "source": source, "pub_date": pub_date})
    return items


def find_matching_headline(event_date_str: str, keywords: list):
    event_date = datetime.strptime(event_date_str, "%Y-%m-%d").date()
    window_end = event_date + timedelta(days=MATCH_WINDOW_DAYS)

    for item in search_google_news(keywords):
        if event_date <= item["pub_date"] <= window_end:
            return item
    return None


def main():
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    today = date.today()
    lookback_start = (today - timedelta(days=LOOKBACK_DAYS)).isoformat()
    today_str = today.isoformat()

    pending = conn.execute(
        """
        SELECT event_date, event_name FROM econ_calendar
        WHERE event_date >= ? AND event_date <= ?
        AND NOT EXISTS (
            SELECT 1 FROM event_headlines h
            WHERE h.event_date = econ_calendar.event_date AND h.event_name = econ_calendar.event_name
        )
        """,
        (lookback_start, today_str),
    ).fetchall()

    now = datetime.now(timezone.utc).isoformat()
    matched_count = 0
    for event_date, event_name in pending:
        keywords = EVENT_KEYWORDS.get(event_name)
        if not keywords:
            continue
        match = find_matching_headline(event_date, keywords)
        if match:
            conn.execute(
                """
                INSERT INTO event_headlines (event_date, event_name, headline, source, link, matched_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_date, event_name) DO NOTHING
                """,
                (event_date, event_name, match["title"], match["source"], match["link"], now),
            )
            matched_count += 1
            print(f"Matched: {event_date} {event_name} -> {match['title']}")

    conn.commit()
    print(f"Matched {matched_count} new headline(s) out of {len(pending)} pending event(s)")
    conn.close()


if __name__ == "__main__":
    main()
