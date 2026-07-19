#!/usr/bin/env python3
"""Fetch upcoming economic release dates into the econ_calendar table.

GDP, CPI, and the Employment Situation (jobs report) come from FRED's own
release calendar, which BEA/BLS keep populated with real forward-looking
scheduled dates. FOMC rate decisions have no equivalent in FRED -- the
closest release (id 101, "FOMC Press Release") turns out to be a daily
republish of the currently-active Fed funds target range, not the ~8x/year
meeting calendar -- so those dates are hardcoded from the Fed's own published
schedule instead. See https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
"""

import os
import sqlite3
from datetime import date, datetime, timedelta, timezone

import requests

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spreads.db")

FRED_API_URL = "https://api.stlouisfed.org/fred/release/dates"

# release_id -> display name for each FRED-backed release we track.
FRED_RELEASES = {
    53: "GDP Release",
    10: "CPI Release",
    50: "Employment Situation (Jobs Report)",
}

# Rate decisions are announced 2pm ET on the second day of each two-day
# FOMC meeting. Source: https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
# NOTE: this list only covers 2026 (the Fed's currently published schedule).
# It needs a manual update once the Fed publishes next year's calendar,
# typically each summer -- there is no API source for this.
FOMC_RATE_DECISIONS_2026 = [
    "2026-01-28",
    "2026-03-18",
    "2026-04-29",
    "2026-06-17",
    "2026-07-29",
    "2026-09-16",
    "2026-10-28",
    "2026-12-09",
]

LOOKAHEAD_DAYS = 120


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS econ_calendar (
            event_date TEXT NOT NULL,
            event_name TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (event_date, event_name)
        )
        """
    )
    conn.commit()


def fetch_fred_release_dates(release_id: str, today: str) -> list:
    params = {
        "release_id": release_id,
        "api_key": os.environ["FRED_API_KEY"],
        "file_type": "json",
        "realtime_start": today,
        "sort_order": "asc",
        "limit": 6,
        # Without this, FRED only returns dates where data has already been
        # published, silently excluding scheduled-but-not-yet-occurred dates
        # -- which is the entire point of a forward-looking calendar.
        "include_release_dates_with_no_data": "true",
    }
    response = requests.get(FRED_API_URL, params=params, timeout=15)
    response.raise_for_status()
    return [d["date"] for d in response.json().get("release_dates", [])]


def main():
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    today = date.today()
    today_str = today.isoformat()
    cutoff = (today + timedelta(days=LOOKAHEAD_DAYS)).isoformat()
    now = datetime.now(timezone.utc).isoformat()

    events = []
    for release_id, name in FRED_RELEASES.items():
        for event_date in fetch_fred_release_dates(release_id, today_str):
            if today_str <= event_date <= cutoff:
                events.append((event_date, name))

    for event_date in FOMC_RATE_DECISIONS_2026:
        if today_str <= event_date <= cutoff:
            events.append((event_date, "FOMC Rate Decision"))

    for event_date, name in events:
        conn.execute(
            """
            INSERT INTO econ_calendar (event_date, event_name, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(event_date, event_name) DO UPDATE SET updated_at = excluded.updated_at
            """,
            (event_date, name, now),
        )
    conn.commit()

    events.sort()
    print(f"Econ calendar: upserted {len(events)} event(s) through {cutoff}")
    for event_date, name in events:
        print(f"  {event_date}: {name}")

    conn.close()


if __name__ == "__main__":
    main()
