#!/usr/bin/env python3
"""Fetch Treasury and credit spread history from FRED into a local SQLite database."""

import os
import sqlite3
import sys
from datetime import date, datetime, timezone

import requests

FRED_API_URL = "https://api.stlouisfed.org/fred/series/observations"

SERIES = {
    "T10Y2Y": {"label": "10Y-2Y Treasury Yield Spread", "column": "t10y2y"},
    "BAMLH0A0HYM2": {"label": "ICE BofA High Yield Credit Spread", "column": "bamlh0a0hym2"},
}

# Earliest date to backfill on first run; every run re-fetches from here so
# later FRED revisions get picked up too.
BACKFILL_START_DATE = "2026-07-01"

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spreads.db")


def get_api_key() -> str:
    api_key = os.environ.get("FRED_API_KEY")
    if not api_key:
        sys.exit(
            "Error: FRED_API_KEY environment variable is not set.\n"
            "Set it with: export FRED_API_KEY=your_key_here"
        )
    return api_key


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS spreads (
            date TEXT PRIMARY KEY,
            t10y2y REAL,
            bamlh0a0hym2 REAL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.commit()


def fetch_observations(series_id: str, api_key: str, start_date: str) -> list:
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "observation_start": start_date,
        "observation_end": date.today().isoformat(),
    }
    response = requests.get(FRED_API_URL, params=params, timeout=10)
    response.raise_for_status()
    return response.json().get("observations", [])


def upsert_observations(conn: sqlite3.Connection, column: str, observations: list) -> int:
    now = datetime.now(timezone.utc).isoformat()
    count = 0
    for obs in observations:
        if obs["value"] == ".":
            continue  # FRED uses "." for dates with no published reading
        conn.execute(
            f"""
            INSERT INTO spreads (date, {column}, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET {column} = excluded.{column}, updated_at = excluded.updated_at
            """,
            (obs["date"], float(obs["value"]), now),
        )
        count += 1
    conn.commit()
    return count


def main():
    api_key = get_api_key()
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    for series_id, meta in SERIES.items():
        try:
            observations = fetch_observations(series_id, api_key, BACKFILL_START_DATE)
        except requests.RequestException as exc:
            print(f"Failed to fetch {series_id} ({meta['label']}): {exc}", file=sys.stderr)
            continue

        n = upsert_observations(conn, meta["column"], observations)
        print(f"{meta['label']} [{series_id}]: upserted {n} observation(s)")

    latest = conn.execute(
        "SELECT date, t10y2y, bamlh0a0hym2 FROM spreads ORDER BY date DESC LIMIT 1"
    ).fetchone()
    if latest:
        print(f"\nLatest row — {latest[0]}: T10Y2Y={latest[1]}, BAMLH0A0HYM2={latest[2]}")

    row_count = conn.execute("SELECT COUNT(*) FROM spreads").fetchone()[0]
    print(f"Database now has {row_count} row(s) — {DB_PATH}")

    conn.close()


if __name__ == "__main__":
    main()
