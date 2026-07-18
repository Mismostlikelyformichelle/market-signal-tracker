#!/usr/bin/env python3
"""Fetch daily DXY (US Dollar Index, via yfinance ticker DX-Y.NYB) into the dxy table."""

import os
import sqlite3
from datetime import datetime, timezone

import yfinance as yf

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spreads.db")


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dxy (
            date TEXT PRIMARY KEY,
            dxy_close REAL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.commit()


def fetch_recent_closes():
    history = yf.Ticker("DX-Y.NYB").history(period="5d", interval="1d")
    if history.empty:
        raise RuntimeError("yfinance returned no DXY daily data")
    return history


def main():
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    history = fetch_recent_closes()
    now = datetime.now(timezone.utc).isoformat()

    count = 0
    for ts, row in history.iterrows():
        conn.execute(
            """
            INSERT INTO dxy (date, dxy_close, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET dxy_close = excluded.dxy_close, updated_at = excluded.updated_at
            """,
            (ts.strftime("%Y-%m-%d"), float(row["Close"]), now),
        )
        count += 1
    conn.commit()

    latest_date = history.index[-1].strftime("%Y-%m-%d")
    latest_close = float(history.iloc[-1]["Close"])
    print(f"DXY: upserted {count} row(s); latest {latest_date} = {latest_close}")

    conn.close()


if __name__ == "__main__":
    main()
