#!/usr/bin/env python3
"""One-time backtest: run the production composite scoring logic against full
historical data to sanity-check whether the Calm/Watch/Elevated/High-Alert
thresholds actually line up with known market stress periods.

NOT part of the daily pipeline -- run manually, writes results to
backtest_output/ in this directory.

Data availability constraint discovered while building this: FRED truncated
BAMLH0A0HYM2 (and every other ICE BofA series) to a rolling 3-year window
starting April 2026, per a licensing change from ICE Data Indices -- see the
series notes via the FRED API. Pre-2023-07-24 credit-spread history is simply
gone from FRED's public API now, confirmed by direct queries for 2008-10 and
2020-03 both returning zero observations. T10Y2Y (Fed/Treasury, not ICE) and
VIX (CBOE, not FRED at all) are unaffected and go back to 1976 and 1990
respectively. So this script produces two separate backtests:

  1. A 2-factor backtest (T10Y2Y + VIX only) across the full 1990-present
     overlap, to check those two thresholds against every major crisis since
     1990 (the 2008 GFC, 2020 COVID crash, etc.)
  2. The real 3-factor composite (matching production exactly, same
     WATCH_ALERT_MAP/composite_status functions imported from
     build_dashboard.py) but only computable from 2023-07-24 onward, since
     that's as far back as the credit-spread input now exists.
"""

import csv
import io
import json
import os

import pandas as pd
import requests

from build_dashboard import (
    WATCH_ALERT_MAP,
    composite_status,
    status_hy,
    status_t10y2y,
    status_vix,
)

FRED_API_URL = "https://api.stlouisfed.org/fred/series/observations"
CBOE_VIX_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest_output")

SEVERITY_RANK = {"Calm": 0, "Mild Watch": 1, "Elevated": 2, "High Alert": 3}


def fetch_fred_full_history(series_id: str) -> pd.Series:
    params = {
        "series_id": series_id,
        "api_key": os.environ["FRED_API_KEY"],
        "file_type": "json",
        "sort_order": "asc",
        "observation_start": "1900-01-01",
    }
    response = requests.get(FRED_API_URL, params=params, timeout=60)
    response.raise_for_status()
    obs = response.json()["observations"]
    data = {o["date"]: float(o["value"]) for o in obs if o["value"] != "."}
    s = pd.Series(data)
    s.index = pd.to_datetime(s.index)
    return s.sort_index()


def fetch_cboe_vix_full_history() -> pd.Series:
    response = requests.get(CBOE_VIX_URL, timeout=60)
    response.raise_for_status()
    reader = csv.DictReader(io.StringIO(response.text))
    data = {}
    for row in reader:
        month, day, year = row["DATE"].split("/")
        iso_date = f"{year}-{int(month):02d}-{int(day):02d}"
        data[iso_date] = float(row["CLOSE"])
    s = pd.Series(data)
    s.index = pd.to_datetime(s.index)
    return s.sort_index()


def build_daily_frame(series_dict: dict) -> pd.DataFrame:
    """Union all dates across the given series, forward-fill each column to
    its latest known value on every date -- matches production semantics,
    where the dashboard always shows each source's latest available reading
    rather than requiring same-day alignment across sources."""
    all_dates = sorted(set().union(*[s.index for s in series_dict.values()]))
    df = pd.DataFrame(index=pd.DatetimeIndex(all_dates))
    for name, s in series_dict.items():
        df[name] = s.reindex(all_dates)
    df = df.ffill()
    return df


def score_two_factor(row) -> dict:
    t_status = status_t10y2y(row["t10y2y"])
    v_status = status_vix(row["vix"])
    flags = [
        WATCH_ALERT_MAP["t10y2y"][t_status] if t_status else "calm",
        WATCH_ALERT_MAP["vix"][v_status] if v_status else "calm",
    ]
    return {
        "t_status": t_status,
        "v_status": v_status,
        "composite": composite_status(flags),
    }


def score_three_factor(row) -> dict:
    t_status = status_t10y2y(row["t10y2y"])
    h_status = status_hy(row["hy"])
    v_status = status_vix(row["vix"])
    flags = [
        WATCH_ALERT_MAP["t10y2y"][t_status] if t_status else "calm",
        WATCH_ALERT_MAP["hy"][h_status] if h_status else "calm",
        WATCH_ALERT_MAP["vix"][v_status] if v_status else "calm",
    ]
    return {
        "t_status": t_status,
        "h_status": h_status,
        "v_status": v_status,
        "composite": composite_status(flags),
    }


def find_episodes(df: pd.DataFrame, extra_cols: list) -> list:
    is_stress = df["composite"] != "Calm"
    group_ids = (is_stress != is_stress.shift()).cumsum()
    episodes = []
    for _, group in df[is_stress].groupby(group_ids[is_stress]):
        peak_idx = group["composite"].map(SEVERITY_RANK).idxmax()
        peak_row = group.loc[peak_idx]
        episode = {
            "start": group.index.min().strftime("%Y-%m-%d"),
            "end": group.index.max().strftime("%Y-%m-%d"),
            "trading_days": len(group),
            "peak_date": peak_idx.strftime("%Y-%m-%d"),
            "peak_composite": peak_row["composite"],
        }
        for col in extra_cols:
            episode[f"peak_{col}"] = round(float(peak_row[col]), 3)
        episodes.append(episode)
    return episodes


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    print("Fetching full T10Y2Y history from FRED...")
    t10y2y = fetch_fred_full_history("T10Y2Y")
    print(f"  {len(t10y2y)} observations, {t10y2y.index.min().date()} to {t10y2y.index.max().date()}")

    print("Fetching full VIX history from CBOE...")
    vix = fetch_cboe_vix_full_history()
    print(f"  {len(vix)} observations, {vix.index.min().date()} to {vix.index.max().date()}")

    print("Fetching HY credit spread history from FRED (truncated by ICE licensing change)...")
    hy = fetch_fred_full_history("BAMLH0A0HYM2")
    print(f"  {len(hy)} observations, {hy.index.min().date()} to {hy.index.max().date()}")

    # --- 2-factor backtest: T10Y2Y + VIX, full overlap since VIX inception ---
    two_factor_df = build_daily_frame({"t10y2y": t10y2y, "vix": vix})
    two_factor_df = two_factor_df.dropna()
    scores = two_factor_df.apply(score_two_factor, axis=1, result_type="expand")
    two_factor_df = pd.concat([two_factor_df, scores], axis=1)
    two_factor_episodes = find_episodes(two_factor_df, ["t10y2y", "vix"])

    # --- 3-factor backtest: matches production exactly, limited to HY's window ---
    three_factor_df = build_daily_frame({"t10y2y": t10y2y, "hy": hy, "vix": vix})
    three_factor_df = three_factor_df.loc[three_factor_df.index >= hy.index.min()].dropna()
    scores3 = three_factor_df.apply(score_three_factor, axis=1, result_type="expand")
    three_factor_df = pd.concat([three_factor_df, scores3], axis=1)
    three_factor_episodes = find_episodes(three_factor_df, ["t10y2y", "hy", "vix"])

    result = {
        "data_coverage": {
            "t10y2y": {"start": str(t10y2y.index.min().date()), "end": str(t10y2y.index.max().date())},
            "vix": {"start": str(vix.index.min().date()), "end": str(vix.index.max().date())},
            "hy_spread": {"start": str(hy.index.min().date()), "end": str(hy.index.max().date())},
            "note": "BAMLH0A0HYM2 truncated to a rolling 3-year window by FRED/ICE licensing change "
                    "as of April 2026; pre-2023-07-24 credit-spread data is unavailable via FRED.",
        },
        "two_factor_backtest": {
            "inputs": "T10Y2Y + VIX only (no credit spread)",
            "coverage": f"{two_factor_df.index.min().date()} to {two_factor_df.index.max().date()}",
            "total_days": len(two_factor_df),
            "days_by_composite": two_factor_df["composite"].value_counts().to_dict(),
            "episodes": two_factor_episodes,
        },
        "three_factor_backtest": {
            "inputs": "T10Y2Y + HY spread + VIX (matches production exactly)",
            "coverage": f"{three_factor_df.index.min().date()} to {three_factor_df.index.max().date()}",
            "total_days": len(three_factor_df),
            "days_by_composite": three_factor_df["composite"].value_counts().to_dict(),
            "episodes": three_factor_episodes,
        },
    }

    out_path = os.path.join(OUT_DIR, "backtest_results.json")
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2, default=str)

    two_factor_df.to_csv(os.path.join(OUT_DIR, "two_factor_daily.csv"))
    three_factor_df.to_csv(os.path.join(OUT_DIR, "three_factor_daily.csv"))

    print(f"\nWrote {out_path}")
    print(f"2-factor episodes found: {len(two_factor_episodes)}")
    print(f"3-factor episodes found: {len(three_factor_episodes)}")


if __name__ == "__main__":
    main()
