#!/usr/bin/env python3
"""Fetch top financial headlines from a curated set of RSS feeds into the
headlines table. Stores headline text, source, link, and timestamp only --
never article body or summary text.

Feed choices (verified working as of 2026-07):
  - Reuters has no working public RSS anymore (feeds.reuters.com is dead;
    reuters.com/business/rss requires JS/blocks bots) -- Investing.com's
    general news feed substitutes for it.
  - MarketWatch has several RSS slugs on feeds.content.dowjones.io that
    return HTTP 200 but are actually abandoned/frozen (mw_marketpulse,
    mw_realtimeheadlines both stopped updating mid-2025) -- only
    mw_bulletins is actually live. A 200 response does not mean the feed
    is current; verify pubDates before trusting a new feed here.
  - CNBC blocks requests without a browser User-Agent (403 otherwise).
"""

import os
import sqlite3
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spreads.db")

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}

FEEDS = [
    ("CNBC", "https://www.cnbc.com/id/15839069/device/rss/rss.html"),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_bulletins"),
    ("Investing.com", "https://www.investing.com/rss/news.rss"),
]

TOP_N = 10

# Some feeds (Investing.com especially) publish far more frequently than
# others. Sorting all feeds together by raw recency lets the fastest-posting
# feed monopolize every slot, defeating the point of curating multiple
# sources. Instead, take a capped number from each feed first, then sort
# that smaller pool by recency -- guarantees a mix across sources.
PER_FEED_LIMIT = 4

PUBDATE_FORMATS = [
    "%a, %d %b %Y %H:%M:%S %Z",   # CNBC, MarketWatch: "Sat, 18 Jul 2026 23:04:22 GMT"
    "%Y-%m-%d %H:%M:%S",           # Investing.com: "2026-07-19 00:39:35"
]


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS headlines (
            link TEXT PRIMARY KEY,
            headline TEXT NOT NULL,
            source TEXT NOT NULL,
            published_at TEXT NOT NULL,
            fetched_at TEXT NOT NULL
        )
        """
    )
    conn.commit()


def parse_pubdate(pub_date_str: str):
    for fmt in PUBDATE_FORMATS:
        try:
            dt = datetime.strptime(pub_date_str, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


def fetch_feed(source: str, url: str) -> list:
    response = requests.get(url, timeout=15, headers=HEADERS)
    response.raise_for_status()
    root = ET.fromstring(response.content)

    items = []
    for item in root.findall(".//item"):
        title = item.findtext("title")
        link = item.findtext("link")
        pub_date = parse_pubdate(item.findtext("pubDate") or "")
        if not title or not link or not pub_date:
            continue
        items.append({"headline": title, "link": link, "source": source, "published_at": pub_date})
    return items


def main():
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    pooled_items = []
    for source, url in FEEDS:
        try:
            items = fetch_feed(source, url)
        except (requests.RequestException, ET.ParseError) as exc:
            print(f"Failed to fetch {source}: {exc}")
            continue
        items.sort(key=lambda x: x["published_at"], reverse=True)
        pooled_items.extend(items[:PER_FEED_LIMIT])

    pooled_items.sort(key=lambda x: x["published_at"], reverse=True)
    top_items = pooled_items[:TOP_N]

    now = datetime.now(timezone.utc).isoformat()
    before_count = conn.execute("SELECT COUNT(*) FROM headlines").fetchone()[0]
    for item in top_items:
        conn.execute(
            """
            INSERT INTO headlines (link, headline, source, published_at, fetched_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(link) DO NOTHING
            """,
            (item["link"], item["headline"], item["source"], item["published_at"].isoformat(), now),
        )
    conn.commit()
    after_count = conn.execute("SELECT COUNT(*) FROM headlines").fetchone()[0]

    print(f"Headlines: pooled {len(pooled_items)} across feeds, kept top {len(top_items)}, {after_count - before_count} newly inserted")
    for item in top_items:
        print(f"  [{item['source']}] {item['headline']}")

    conn.close()


if __name__ == "__main__":
    main()
