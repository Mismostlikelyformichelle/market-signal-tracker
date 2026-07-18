#!/usr/bin/env python3
"""Fetch a single intraday VIX reading (^VIX, via yfinance) into the vix_intraday table."""

import os
import sqlite3
from datetime import datetime, timezone

import yfinance as yf

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spreads.db")


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vix_intraday (
            timestamp TEXT PRIMARY KEY,
            vix_value REAL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.commit()


def fetch_latest_quote():
    history = yf.Ticker("^VIX").history(period="1d", interval="1m")
    if history.empty:
        raise RuntimeError("yfinance returned no intraday VIX data")
    return history.iloc[-1]


def main():
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    latest = fetch_latest_quote()
    reading_time = latest.name
    if reading_time.tzinfo is None:
        reading_time = reading_time.tz_localize("UTC")
    timestamp = reading_time.tz_convert("UTC").isoformat()
    value = float(latest["Close"])
    now = datetime.now(timezone.utc).isoformat()

    conn.execute(
        """
        INSERT INTO vix_intraday (timestamp, vix_value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(timestamp) DO UPDATE SET vix_value = excluded.vix_value, updated_at = excluded.updated_at
        """,
        (timestamp, value, now),
    )
    conn.commit()

    print(f"VIX intraday: {timestamp} = {value}")

    conn.close()


if __name__ == "__main__":
    main()
