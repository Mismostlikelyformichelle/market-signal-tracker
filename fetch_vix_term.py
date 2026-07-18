#!/usr/bin/env python3
"""Fetch VIX term-structure indices (VIX9D, VIX, VIX3M) from CBOE's official
history CSVs into the vix_term table."""

import csv
import io
import os
import sqlite3
from datetime import datetime, timezone

import requests

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spreads.db")

CBOE_URLS = {
    "vix9d": "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX9D_History.csv",
    "vix": "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv",
    "vix3m": "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX3M_History.csv",
}

# Small trailing window each run; the upsert makes re-fetching harmless and
# this keeps the request/parse cheap even though CBOE serves full history.
LOOKBACK_ROWS = 10


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vix_term (
            date TEXT PRIMARY KEY,
            vix9d REAL,
            vix REAL,
            vix3m REAL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.commit()


def fetch_recent_closes(column: str) -> list:
    response = requests.get(CBOE_URLS[column], timeout=15)
    response.raise_for_status()
    reader = csv.DictReader(io.StringIO(response.text))
    rows = list(reader)[-LOOKBACK_ROWS:]

    result = []
    for row in rows:
        month, day, year = row["DATE"].split("/")
        iso_date = f"{year}-{int(month):02d}-{int(day):02d}"
        result.append((iso_date, float(row["CLOSE"])))
    return result


def main():
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)
    now = datetime.now(timezone.utc).isoformat()

    by_date = {}
    for column in ("vix9d", "vix", "vix3m"):
        for iso_date, close in fetch_recent_closes(column):
            by_date.setdefault(iso_date, {})[column] = close

    count = 0
    for iso_date, values in sorted(by_date.items()):
        for column, value in values.items():
            conn.execute(
                f"""
                INSERT INTO vix_term (date, {column}, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(date) DO UPDATE SET {column} = excluded.{column}, updated_at = excluded.updated_at
                """,
                (iso_date, value, now),
            )
        count += 1
    conn.commit()

    latest_date = max(by_date.keys())
    latest = by_date[latest_date]
    print(
        f"VIX term: upserted {count} date(s); latest {latest_date} = "
        f"VIX9D={latest.get('vix9d')}, VIX={latest.get('vix')}, VIX3M={latest.get('vix3m')}"
    )

    conn.close()


if __name__ == "__main__":
    main()
