#!/usr/bin/env python3
"""Generate the static GitHub Pages dashboard (docs/index.html, docs/data.json) from spreads.db."""

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spreads.db")
DOCS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs")

HISTORY_DAYS = 90


def status_t10y2y(v):
    if v is None:
        return None
    if v > 0.25:
        return "Calm"
    if v >= 0:
        return "Watch"
    return "Alert"


def status_hy(v_pct):
    if v_pct is None:
        return None
    bps = v_pct * 100
    if bps < 400:
        return "Calm"
    if bps <= 500:
        return "Watch"
    return "Alert"


def status_vix(v):
    if v is None:
        return None
    if v < 15:
        return "Calm"
    if v < 20:
        return "Normal"
    if v < 25:
        return "Elevated"
    return "High"


def status_vix_term(spread):
    """spread = VIX3M - VIX9D. Calibrated against CBOE history since 2015:
    ~70% of days sit at spread >= 2 (normal contango), ~17% land 0-2
    (flattening), ~13% go negative (backwardation/inversion) -- which
    historically clusters around acute stress episodes."""
    if spread is None:
        return None
    if spread >= 2:
        return "Calm"
    if spread >= 0:
        return "Watch"
    return "Alert"


def status_momentum(pct_change, calm_bound, watch_bound):
    """Generic rate-of-change status: |pct_change| below calm_bound is Calm,
    up to watch_bound is Watch, beyond that is Alert. Used for DXY and gold,
    which don't have an inherent "crisis level" the way spreads/VIX do --
    large, fast moves are the more meaningful stress signal for these two."""
    if pct_change is None:
        return None
    abs_change = abs(pct_change)
    if abs_change < calm_bound:
        return "Calm"
    if abs_change <= watch_bound:
        return "Watch"
    return "Alert"


def pct_change_over(history, periods=20):
    """Percent change from `periods` readings ago to the latest reading."""
    if len(history) <= periods:
        return None
    old = history[-(periods + 1)]["value"]
    new = history[-1]["value"]
    if not old:
        return None
    return (new - old) / old * 100


# Maps each indicator's own status vocabulary onto the shared calm/watch/alert
# scale the composite formula counts against. VIX's Normal tier counts as calm;
# Elevated/High count as watch/alert respectively.
WATCH_ALERT_MAP = {
    "t10y2y": {"Calm": "calm", "Watch": "watch", "Alert": "alert"},
    "hy": {"Calm": "calm", "Watch": "watch", "Alert": "alert"},
    "vix": {"Calm": "calm", "Normal": "calm", "Elevated": "watch", "High": "alert"},
}


def composite_status(flags):
    alerts = flags.count("alert")
    watches = flags.count("watch")
    if alerts >= 2:
        return "High Alert"
    if alerts == 1 or watches >= 3:
        return "Elevated"
    if watches >= 1:
        return "Mild Watch"
    return "Calm"


def fetch_history(conn, table, date_col, value_col, since):
    rows = conn.execute(
        f"SELECT {date_col}, {value_col} FROM {table} WHERE {date_col} >= ? AND {value_col} IS NOT NULL ORDER BY {date_col}",
        (since,),
    ).fetchall()
    return [{"date": r[0], "value": r[1]} for r in rows]


def fetch_latest(conn, table, date_col, value_col):
    row = conn.execute(
        f"SELECT {date_col}, {value_col} FROM {table} WHERE {value_col} IS NOT NULL ORDER BY {date_col} DESC LIMIT 1"
    ).fetchone()
    if not row:
        return None, None
    return row[0], row[1]


def fetch_latest_multi(conn, table, date_col, value_cols):
    cols = ", ".join(value_cols)
    row = conn.execute(
        f"SELECT {date_col}, {cols} FROM {table} ORDER BY {date_col} DESC LIMIT 1"
    ).fetchone()
    if not row:
        return None, {c: None for c in value_cols}
    return row[0], dict(zip(value_cols, row[1:]))


def fetch_history_multi(conn, table, date_col, value_cols, since):
    cols = ", ".join(value_cols)
    rows = conn.execute(
        f"SELECT {date_col}, {cols} FROM {table} WHERE {date_col} >= ? ORDER BY {date_col}",
        (since,),
    ).fetchall()
    result = []
    for r in rows:
        entry = {"date": r[0]}
        entry.update(dict(zip(value_cols, r[1:])))
        result.append(entry)
    return result


def fetch_todays_intraday(conn):
    latest_ts = conn.execute(
        "SELECT MAX(timestamp) FROM vix_intraday"
    ).fetchone()[0]
    if not latest_ts:
        return None, []
    latest_date = latest_ts[:10]
    rows = conn.execute(
        "SELECT timestamp, vix_value FROM vix_intraday WHERE timestamp LIKE ? ORDER BY timestamp",
        (f"{latest_date}%",),
    ).fetchall()
    series = [{"timestamp": r[0], "value": r[1]} for r in rows]
    return series[-1] if series else None, series


def build_data(conn):
    since = (datetime.now(timezone.utc) - timedelta(days=HISTORY_DAYS)).strftime("%Y-%m-%d")

    t10y2y_date, t10y2y_val = fetch_latest(conn, "spreads", "date", "t10y2y")
    hy_date, hy_val = fetch_latest(conn, "spreads", "date", "bamlh0a0hym2")
    vix_date, vix_val = fetch_latest(conn, "vix_eod", "date", "vix_close")

    t10y2y_status = status_t10y2y(t10y2y_val)
    hy_status = status_hy(hy_val)
    vix_status = status_vix(vix_val)

    flags = [
        WATCH_ALERT_MAP["t10y2y"][t10y2y_status] if t10y2y_status else "calm",
        WATCH_ALERT_MAP["hy"][hy_status] if hy_status else "calm",
        WATCH_ALERT_MAP["vix"][vix_status] if vix_status else "calm",
    ]
    composite = composite_status(flags)

    intraday_latest, intraday_series = fetch_todays_intraday(conn)

    vixterm_date, vixterm_vals = fetch_latest_multi(conn, "vix_term", "date", ["vix9d", "vix", "vix3m"])
    vixterm_history = fetch_history_multi(conn, "vix_term", "date", ["vix9d", "vix", "vix3m"], since)
    vixterm_spread = None
    if vixterm_vals.get("vix3m") is not None and vixterm_vals.get("vix9d") is not None:
        vixterm_spread = vixterm_vals["vix3m"] - vixterm_vals["vix9d"]
    vixterm_status = status_vix_term(vixterm_spread)

    dxy_date, dxy_val = fetch_latest(conn, "dxy", "date", "dxy_close")
    dxy_history = fetch_history(conn, "dxy", "date", "dxy_close", since)
    dxy_pct20 = pct_change_over(dxy_history, 20)
    dxy_status = status_momentum(dxy_pct20, 2, 4)

    gold_date, gold_val = fetch_latest(conn, "gold", "date", "gold_close")
    gold_history = fetch_history(conn, "gold", "date", "gold_close", since)
    gold_pct20 = pct_change_over(gold_history, 20)
    gold_status = status_momentum(gold_pct20, 6, 12)

    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "composite": {
            "status": composite,
            "watches": flags.count("watch"),
            "alerts": flags.count("alert"),
        },
        "indicators": {
            "t10y2y": {
                "label": "10Y-2Y Treasury Spread",
                "latest_date": t10y2y_date,
                "latest_value": t10y2y_val,
                "unit": "pp",
                "status": t10y2y_status,
                "thresholds": "Calm >0.25 · Watch 0–0.25 · Alert <0",
                "history": fetch_history(conn, "spreads", "date", "t10y2y", since),
            },
            "hy": {
                "label": "ICE BofA High Yield Spread",
                "latest_date": hy_date,
                "latest_value": hy_val,
                "latest_value_bps": (hy_val * 100) if hy_val is not None else None,
                "unit": "pp",
                "status": hy_status,
                "thresholds": "Calm <400bps · Watch 400–500bps · Alert >500bps",
                "history": fetch_history(conn, "spreads", "date", "bamlh0a0hym2", since),
            },
            "vix": {
                "label": "VIX (Close)",
                "latest_date": vix_date,
                "latest_value": vix_val,
                "unit": "",
                "status": vix_status,
                "thresholds": "Calm <15 · Normal 15–20 · Elevated 20–25 · High >25",
                "history": fetch_history(conn, "vix_eod", "date", "vix_close", since),
            },
            "vix_term": {
                "label": "VIX Term Structure (VIX3M − VIX9D)",
                "latest_date": vixterm_date,
                "vix9d": vixterm_vals.get("vix9d"),
                "vix": vixterm_vals.get("vix"),
                "vix3m": vixterm_vals.get("vix3m"),
                "spread": vixterm_spread,
                "unit": "",
                "status": vixterm_status,
                "thresholds": "Calm: spread ≥2 (contango) · Watch 0–2 (flattening) · Alert <0 (inverted)",
                "history": vixterm_history,
            },
            "dxy": {
                "label": "US Dollar Index (DXY)",
                "latest_date": dxy_date,
                "latest_value": dxy_val,
                "pct_change_20d": dxy_pct20,
                "unit": "",
                "status": dxy_status,
                "thresholds": "Based on 20-day move: Calm <2% · Watch 2–4% · Alert >4%",
                "history": dxy_history,
            },
            "gold": {
                "label": "Gold (GC=F)",
                "latest_date": gold_date,
                "latest_value": gold_val,
                "pct_change_20d": gold_pct20,
                "unit": "$",
                "status": gold_status,
                "thresholds": "Based on 20-day move: Calm <6% · Watch 6–12% · Alert >12%",
                "history": gold_history,
            },
        },
        "vix_intraday": {
            "latest": intraday_latest,
            "series": intraday_series,
        },
    }
    return data


HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Market Signal Tracker</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  :root {
    --bg: #0b0f14;
    --card: #151b23;
    --border: #2a313c;
    --text: #e6edf3;
    --muted: #8b949e;
    --calm: #2ea043;
    --watch: #d29922;
    --alert: #f85149;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    padding: 24px;
  }
  h1 { font-size: 1.4rem; margin: 0 0 4px; }
  .updated { color: var(--muted); font-size: 0.85rem; margin-bottom: 20px; }
  .composite {
    display: inline-block;
    padding: 10px 18px;
    border-radius: 8px;
    font-weight: 600;
    font-size: 1.1rem;
    margin-bottom: 24px;
    border: 1px solid var(--border);
  }
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
    gap: 16px;
    margin-bottom: 28px;
  }
  .card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 16px;
  }
  .card h3 { margin: 0 0 6px; font-size: 0.95rem; color: var(--muted); font-weight: 500; }
  .card .value { font-size: 1.8rem; font-weight: 700; margin-bottom: 6px; }
  .pill {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 999px;
    font-size: 0.8rem;
    font-weight: 600;
  }
  .card .thresholds { color: var(--muted); font-size: 0.75rem; margin-top: 8px; }
  .status-Calm, .status-Normal { background: rgba(46,160,67,0.15); color: var(--calm); }
  .status-Watch, .status-Elevated { background: rgba(210,153,34,0.15); color: var(--watch); }
  .status-Alert, .status-High { background: rgba(248,81,73,0.15); color: var(--alert); }
  .composite-Calm { background: rgba(46,160,67,0.15); color: var(--calm); border-color: var(--calm); }
  .composite-Mild-Watch { background: rgba(210,153,34,0.15); color: var(--watch); border-color: var(--watch); }
  .composite-Elevated { background: rgba(210,153,34,0.25); color: var(--watch); border-color: var(--watch); }
  .composite-High-Alert { background: rgba(248,81,73,0.2); color: var(--alert); border-color: var(--alert); }
  .charts {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(360px, 1fr));
    gap: 16px;
  }
  .chart-card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 16px;
  }
  .chart-card h3 { margin: 0 0 10px; font-size: 0.95rem; color: var(--muted); font-weight: 500; }
  .no-data { color: var(--muted); font-size: 0.85rem; }
</style>
</head>
<body>
  <h1>Market Signal Tracker</h1>
  <div class="updated" id="updated"></div>
  <div class="composite" id="composite"></div>

  <div class="grid" id="cards"></div>

  <div class="charts">
    <div class="chart-card"><h3>10Y-2Y Treasury Spread</h3><canvas id="chart-t10y2y" height="180"></canvas></div>
    <div class="chart-card"><h3>ICE BofA High Yield Spread</h3><canvas id="chart-hy" height="180"></canvas></div>
    <div class="chart-card"><h3>VIX (Daily Close)</h3><canvas id="chart-vix" height="180"></canvas></div>
    <div class="chart-card"><h3>VIX Intraday (most recent session)</h3><canvas id="chart-intraday" height="180"></canvas></div>
    <div class="chart-card"><h3>VIX Term Structure (VIX9D / VIX / VIX3M)</h3><canvas id="chart-vixterm" height="180"></canvas></div>
    <div class="chart-card"><h3>US Dollar Index (DXY)</h3><canvas id="chart-dxy" height="180"></canvas></div>
    <div class="chart-card"><h3>Gold (GC=F)</h3><canvas id="chart-gold" height="180"></canvas></div>
  </div>

<script>
const DATA = __DATA_JSON__;

document.getElementById("updated").textContent =
  "Last updated: " + new Date(DATA.generated_at).toLocaleString();

const compositeEl = document.getElementById("composite");
compositeEl.textContent = DATA.composite.status;
compositeEl.className = "composite composite-" + DATA.composite.status.replace(/ /g, "-");

const cardsEl = document.getElementById("cards");
function renderCard(ind, valueText) {
  if (!ind.status) {
    return `<div class="card"><h3>${ind.label}</h3><div class="no-data">No data available</div></div>`;
  }
  return `<div class="card">
    <h3>${ind.label}</h3>
    <div class="value">${valueText}</div>
    <span class="pill status-${ind.status.replace(/ /g, "-")}">${ind.status}</span>
    <div class="thresholds">${ind.thresholds}</div>
  </div>`;
}
cardsEl.innerHTML =
  renderCard(DATA.indicators.t10y2y, DATA.indicators.t10y2y.latest_value !== null ? DATA.indicators.t10y2y.latest_value.toFixed(2) : "") +
  renderCard(DATA.indicators.hy, DATA.indicators.hy.latest_value !== null ? DATA.indicators.hy.latest_value.toFixed(2) + "% (" + Math.round(DATA.indicators.hy.latest_value_bps) + " bps)" : "") +
  renderCard(DATA.indicators.vix, DATA.indicators.vix.latest_value !== null ? DATA.indicators.vix.latest_value.toFixed(2) : "") +
  (function() {
    const iv = DATA.vix_intraday.latest;
    if (!iv) return `<div class="card"><h3>VIX Intraday (latest)</h3><div class="no-data">No reading available (outside market hours)</div></div>`;
    const t = new Date(iv.timestamp).toLocaleString();
    return `<div class="card"><h3>VIX Intraday (latest)</h3><div class="value">${iv.value.toFixed(2)}</div><div class="thresholds">${t}</div></div>`;
  })() +
  (function() {
    const vt = DATA.indicators.vix_term;
    if (!vt.status) return `<div class="card"><h3>${vt.label}</h3><div class="no-data">No data available</div></div>`;
    return `<div class="card">
      <h3>${vt.label}</h3>
      <div class="value">${vt.spread.toFixed(2)}</div>
      <span class="pill status-${vt.status}">${vt.status}</span>
      <div class="thresholds">VIX9D ${vt.vix9d.toFixed(2)} · VIX ${vt.vix.toFixed(2)} · VIX3M ${vt.vix3m.toFixed(2)}<br>${vt.thresholds}</div>
    </div>`;
  })() +
  renderMomentumCard(DATA.indicators.dxy, v => v.toFixed(2)) +
  renderMomentumCard(DATA.indicators.gold, v => "$" + v.toFixed(2));

function renderMomentumCard(ind, formatValue) {
  if (ind.latest_value === null || ind.latest_value === undefined) {
    return `<div class="card"><h3>${ind.label}</h3><div class="no-data">No data available</div></div>`;
  }
  if (!ind.status) {
    return `<div class="card">
      <h3>${ind.label}</h3>
      <div class="value">${formatValue(ind.latest_value)}</div>
      <div class="thresholds">Status pending — needs 20 trading days of history to compute a 20-day move<br>${ind.thresholds}</div>
    </div>`;
  }
  const chg = ind.pct_change_20d !== null ? (ind.pct_change_20d >= 0 ? "+" : "") + ind.pct_change_20d.toFixed(1) + "% (20d)" : "";
  return `<div class="card">
    <h3>${ind.label}</h3>
    <div class="value">${formatValue(ind.latest_value)}</div>
    <span class="pill status-${ind.status}">${ind.status}</span>
    <div class="thresholds">${chg}<br>${ind.thresholds}</div>
  </div>`;
}

const chartDefaults = {
  type: "line",
  options: {
    responsive: true,
    plugins: { legend: { display: false } },
    scales: {
      x: { ticks: { color: "#8b949e" }, grid: { color: "#2a313c" } },
      y: { ticks: { color: "#8b949e" }, grid: { color: "#2a313c" } },
    },
  },
};

function renderLineChart(canvasId, history, labelKey, valueKey, color) {
  const canvas = document.getElementById(canvasId);
  if (!history || history.length === 0) {
    canvas.parentElement.innerHTML += '<div class="no-data">No data available</div>';
    return;
  }
  new Chart(canvas, {
    ...chartDefaults,
    data: {
      labels: history.map(r => r[labelKey]),
      datasets: [{
        data: history.map(r => r[valueKey]),
        borderColor: color,
        backgroundColor: color,
        pointRadius: 0,
        borderWidth: 2,
        tension: 0.15,
      }],
    },
  });
}

function renderMultiLineChart(canvasId, history, labelKey, series) {
  const canvas = document.getElementById(canvasId);
  if (!history || history.length === 0) {
    canvas.parentElement.innerHTML += '<div class="no-data">No data available</div>';
    return;
  }
  new Chart(canvas, {
    type: "line",
    data: {
      labels: history.map(r => r[labelKey]),
      datasets: series.map(s => ({
        label: s.name,
        data: history.map(r => r[s.key]),
        borderColor: s.color,
        backgroundColor: s.color,
        pointRadius: 0,
        borderWidth: 2,
        tension: 0.15,
      })),
    },
    options: {
      responsive: true,
      plugins: { legend: { display: true, labels: { color: "#e6edf3" } } },
      scales: {
        x: { ticks: { color: "#8b949e" }, grid: { color: "#2a313c" } },
        y: { ticks: { color: "#8b949e" }, grid: { color: "#2a313c" } },
      },
    },
  });
}

renderLineChart("chart-t10y2y", DATA.indicators.t10y2y.history, "date", "value", "#58a6ff");
renderLineChart("chart-hy", DATA.indicators.hy.history, "date", "value", "#d2a8ff");
renderLineChart("chart-vix", DATA.indicators.vix.history, "date", "value", "#ffa657");
renderLineChart("chart-intraday", DATA.vix_intraday.series, "timestamp", "value", "#7ee787");
renderMultiLineChart("chart-vixterm", DATA.indicators.vix_term.history, "date", [
  { name: "VIX9D", key: "vix9d", color: "#f85149" },
  { name: "VIX", key: "vix", color: "#ffa657" },
  { name: "VIX3M", key: "vix3m", color: "#7ee787" },
]);
renderLineChart("chart-dxy", DATA.indicators.dxy.history, "date", "value", "#58a6ff");
renderLineChart("chart-gold", DATA.indicators.gold.history, "date", "value", "#e3b341");
</script>
</body>
</html>
"""


def main():
    conn = sqlite3.connect(DB_PATH)
    data = build_data(conn)
    conn.close()

    os.makedirs(DOCS_DIR, exist_ok=True)

    data_json_path = os.path.join(DOCS_DIR, "data.json")
    with open(data_json_path, "w") as f:
        json.dump(data, f, indent=2)

    html = HTML_TEMPLATE.replace("__DATA_JSON__", json.dumps(data))
    index_path = os.path.join(DOCS_DIR, "index.html")
    with open(index_path, "w") as f:
        f.write(html)

    print(f"Wrote {data_json_path}")
    print(f"Wrote {index_path}")
    print(f"Composite status: {data['composite']['status']}")


if __name__ == "__main__":
    main()
