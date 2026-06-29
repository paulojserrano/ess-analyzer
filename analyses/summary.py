"""
analyses/summary.py — cross-day trend charts for the Summary tab.

Called after all individual day analyses complete.  Requires at least
two days of data to produce any charts.

Charts produced
---------------
1. Total completed tasks per day  +  Median cycle time per day (two panels).
2. Median pick / dwell time per day.
3. Day-over-day change heatmap across all key metrics.
4. Cycle time tail severity (p90 ÷ median ratio) per day.
5. Within-day throughput consistency (hourly CV and peak-to-mean ratio).
6. Cycle time distribution overlay (violin per day).
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from config import ACCENT, INK, TOTAL_DURATION_COL

# One distinct colour per day (cycles if > 8 days)
_DAY_PALETTE = [
    "#2563eb", "#16a34a", "#d97706", "#7c3aed",
    "#0891b2", "#dc2626", "#f59e0b", "#10b981",
]


def _collect_stats(all_days: list[dict]) -> list[dict]:
    """Extract per-day summary statistics from raw data dicts."""
    rows: list[dict] = []
    for day in all_days:
        label = day["label"]
        data  = day["data"]
        cb    = data.get("callback")
        tlc   = data.get("lifecycle")
        lsr   = data.get("station")

        # ── total completed tasks ─────────────────────────────────────────────
        # Count triggerGo events from the station record — consistent with the
        # throughput analysis.  triggerGo fires when the operator finishes
        # picking and releases the robot (the true task-completion signal).
        total_tasks: int | None = None
        if lsr is not None:
            point2ws = day.get("cfg", {}).get("point2ws", {})
            evt_col  = next((c for c in lsr.columns if "事件类型" in str(c)), None)
            loc_col  = next((c for c in lsr.columns if "位置编号" in str(c)), None)
            if evt_col and loc_col:
                lsr_s = lsr.copy()
                lsr_s["_station"] = lsr_s[loc_col].map(point2ws) if point2ws else None
                tgo = lsr_s[lsr_s[evt_col] == "triggerGo"]
                if point2ws:
                    tgo = tgo[tgo["_station"].notna()]
                total_tasks = int(len(tgo))

        # ── median + p90 cycle time (minutes) ─────────────────────────────────
        med_cycle_min: float | None = None
        p90_cycle_min: float | None = None
        if tlc is not None and TOTAL_DURATION_COL in tlc.columns:
            t = tlc[TOTAL_DURATION_COL].dropna()
            t = t[(t >= 0) & (t < 7200)] / 60.0
            if len(t):
                med_cycle_min = float(t.median())
                p90_cycle_min = float(t.quantile(0.9))

        # ── average pick time (seconds) overall + per station ────────────────
        _SWITCH_S = 6.0
        avg_pick_s: float | None = None
        avg_pick_by_station:     dict[str, float] = {}
        avg_util_pct_by_station: dict[str, float] = {}
        if lsr is not None:
            point2ws  = day.get("cfg", {}).get("point2ws", {})
            lsr_c     = lsr.copy()
            lsr_c["ts"]       = pd.to_datetime(lsr_c["时间戳"], errors="coerce")
            lsr_c["_station"] = lsr_c["位置编号"].map(point2ws) if point2ws else None
            ev = lsr_c.sort_values(["机器人编号", "ts"])
            picks_all: list[float] = []
            picks_by_ws: dict[str, list[float]] = {}
            picks_by_ws_hour: dict[tuple, list[float]] = {}
            for _rb, sub in ev.groupby("机器人编号"):
                arr = arr_ws = None
                for ts, et, ws in sub[["ts", "事件类型", "_station"]].values:
                    if et == "arrived":
                        arr, arr_ws = ts, ws
                    elif et == "triggerGo" and arr is not None:
                        val = float((ts - arr).total_seconds())
                        if 0 <= val < 3600:
                            picks_all.append(val)
                            if pd.notna(arr_ws):
                                picks_by_ws.setdefault(str(arr_ws), []).append(val)
                                picks_by_ws_hour.setdefault(
                                    (str(arr_ws), ts.floor("h")), []
                                ).append(val)
                        arr = None
            if picks_all:
                avg_pick_s = float(np.mean(picks_all))
            for ws, vals in picks_by_ws.items():
                avg_pick_by_station[ws] = float(np.mean(vals))
            # util % per station: mean across hours of (actual / implied * 100)
            util_by_ws: dict[str, list[float]] = {}
            for (ws, _hr), pick_list in picks_by_ws_hour.items():
                avg_p   = float(np.mean(pick_list))
                actual  = len(pick_list)              # triggerGo count = completions
                implied = 3600.0 / (avg_p + _SWITCH_S)
                util_by_ws.setdefault(ws, []).append(actual / implied * 100.0)
            for ws, pcts in util_by_ws.items():
                avg_util_pct_by_station[ws] = float(np.mean(pcts))

        # ── median switch time (seconds) ──────────────────────────────────────
        med_switch_s: float | None = None
        if lsr is not None:
            lsr_c   = lsr.copy()
            lsr_c["ts"] = pd.to_datetime(lsr_c["时间戳"], errors="coerce")
            pos_col = next((c for c in lsr_c.columns if "位置编号" in str(c)), None)
            evt_col = next((c for c in lsr_c.columns if "事件类型" in str(c)), None)
            if pos_col and evt_col:
                ev2 = lsr_c[
                    lsr_c[evt_col].isin(["release", "arrived"]) &
                    lsr_c[pos_col].notna()
                ].sort_values([pos_col, "ts"])
                gaps: list[float] = []
                for _loc, sub in ev2.groupby(pos_col):
                    open_rel = None
                    for ts, et in sub[["ts", evt_col]].values:
                        if et == "release":
                            open_rel = ts
                        elif et == "arrived" and open_rel is not None:
                            gap = (ts - open_rel).total_seconds()
                            if 0 <= gap < 7200:
                                gaps.append(gap)
                            open_rel = None
                if gaps:
                    med_switch_s = float(np.median(gaps))

        rows.append({
            "label":                   label,
            "total_tasks":             total_tasks,
            "med_cycle_min":           med_cycle_min,
            "p90_cycle_min":           p90_cycle_min,
            "avg_pick_s":              avg_pick_s,
            "avg_pick_by_station":     avg_pick_by_station,
            "avg_util_pct_by_station": avg_util_pct_by_station,
            "med_switch_s":            med_switch_s,
        })
    return rows


# ── chart builders ────────────────────────────────────────────────────────────

def _delta_heatmap(stats: list[dict]) -> dict | None:
    """% change between consecutive days for every key metric."""
    if len(stats) < 2:
        return None

    labels     = [r["label"] for r in stats]
    col_labels = [f"{labels[i]} → {labels[i + 1]}" for i in range(len(labels) - 1)]

    # (display label, stats key, higher_is_better)
    metric_defs = [
        ("Throughput (tasks)",    "total_tasks",    True),
        ("Median cycle (min)",    "med_cycle_min",  False),
        ("p90 cycle (min)",       "p90_cycle_min",  False),
        ("Avg pick time (s)",     "avg_pick_s",     False),
        ("Switch time (s)",       "med_switch_s",   False),
    ]

    z_rows:    list[list[float]] = []
    text_rows: list[list[str]]   = []
    y_labels:  list[str]         = []

    for display, key, higher_is_good in metric_defs:
        row_z: list[float] = []
        row_t: list[str]   = []
        has_data = False
        for i in range(len(stats) - 1):
            v0, v1 = stats[i][key], stats[i + 1][key]
            if v0 is None or v1 is None or v0 == 0:
                row_z.append(float("nan"))
                row_t.append("—")
            else:
                raw_pct  = (v1 - v0) / abs(v0) * 100
                # Flip sign so that z > 0 always means improvement
                impr_pct = raw_pct if higher_is_good else -raw_pct
                row_z.append(impr_pct)
                sign = "+" if raw_pct >= 0 else ""
                row_t.append(f"{sign}{raw_pct:.1f}%")
                has_data = True
        if has_data:
            z_rows.append(row_z)
            text_rows.append(row_t)
            y_labels.append(display)

    if not y_labels:
        return None

    all_vals = [v for row in z_rows for v in row if not np.isnan(v)]
    abs_max  = max(max(abs(v) for v in all_vals), 5.0) if all_vals else 20.0

    fig = go.Figure(go.Heatmap(
        z=z_rows,
        x=col_labels,
        y=y_labels,
        text=text_rows,
        texttemplate="%{text}",
        textfont=dict(size=12, color=INK),
        colorscale=[
            [0.0,  "#ef4444"],
            [0.35, "#fca5a5"],
            [0.5,  "#f3f4f6"],
            [0.65, "#86efac"],
            [1.0,  "#16a34a"],
        ],
        zmin=-abs_max,
        zmax= abs_max,
        hovertemplate="<b>%{y}</b><br>%{x}<br>Change: %{text}<extra></extra>",
        colorbar=dict(
            title="← worse | better →",
            thickness=14, len=0.8,
            tickvals=[-abs_max, 0, abs_max],
            ticktext=[f"−{abs_max:.0f}%", "0 %", f"+{abs_max:.0f}%"],
        ),
    ))
    fig.update_layout(
        title=dict(
            text="Day-over-Day Change in Key Metrics",
            x=0, pad=dict(l=12), font=dict(size=17, color=INK),
        ),
        xaxis=dict(tickangle=-30, side="top"),
        yaxis=dict(autorange="reversed"),
        plot_bgcolor="white", paper_bgcolor="white",
        font=dict(color=INK, family="Inter, sans-serif"),
        margin=dict(t=110, b=60, l=185, r=100),
        hoverlabel=dict(bgcolor="white", bordercolor="#cccccc"),
        annotations=[dict(
            xref="paper", yref="paper", x=0, y=-0.1,
            text=(
                "Cell text = raw % change. "
                "Green = improved; red = worse. "
                "For timing metrics a decrease is an improvement."
            ),
            font=dict(size=10, color="#666"), showarrow=False,
        )],
    )
    return {
        "id":          "summary_delta_heatmap",
        "title":       "Day-over-Day Change in Key Metrics",
        "figure":      fig,
        "source":      "All days",
        "method":      (
            "Percentage change between consecutive days for five metrics: "
            "throughput, median cycle time, p90 cycle time, pick/dwell time, and switch time. "
            "Cell text shows the raw % change (e.g. −8.3% means the value fell by 8.3%). "
            "Colour encodes whether the change is an improvement: green = better, red = worse. "
            "For throughput, higher is better; for all timing metrics, lower is better. "
            "A red throughput cell alongside green timing cells in the same column means the "
            "system did fewer tasks but completed each one faster — a lighter day, "
            "not a genuine operational improvement."
        ),
        "export_hint": "summary_metrics.xlsx",
    }


def _pick_time_per_station(stats: list[dict]) -> dict | None:
    """Average operator pick time per day — combined or broken down per station."""
    labels = [r["label"] for r in stats]

    combined = [r.get("avg_pick_s") for r in stats]
    if all(v is None for v in combined):
        return None

    # Collect ordered station list (preserve insertion order across days)
    seen: dict[str, None] = {}
    for r in stats:
        for ws in r.get("avg_pick_by_station", {}).keys():
            seen[ws] = None
    all_stations = sorted(seen.keys())

    fig = go.Figure()

    # ── Trace 0 : combined all-station average ────────────────────────────────
    fig.add_trace(go.Scatter(
        x=labels, y=combined,
        mode="lines+markers",
        line=dict(color="#16a34a", width=2.5),
        marker=dict(size=9, color="#16a34a"),
        name="All stations (avg)",
        hovertemplate="<b>%{x}</b><br>Avg pick: %{y:.1f} s<extra></extra>",
        visible=False,
    ))

    # ── Traces 1…N : one per station ─────────────────────────────────────────
    for i, ws in enumerate(all_stations):
        color = _DAY_PALETTE[i % len(_DAY_PALETTE)]
        vals  = [r.get("avg_pick_by_station", {}).get(ws) for r in stats]
        fig.add_trace(go.Scatter(
            x=labels, y=vals,
            mode="lines+markers",
            line=dict(color=color, width=2),
            marker=dict(size=7, color=color),
            name=ws,
            hovertemplate=f"<b>%{{x}}</b><br>{ws}: %{{y:.1f}} s<extra></extra>",
            visible=True,
        ))

    n_ws = len(all_stations)

    fig.update_layout(
        title=dict(
            text="Average Operator Pick Time per Day",
            x=0, pad=dict(l=12), font=dict(size=17, color=INK),
        ),
        updatemenus=[dict(
            type="buttons", direction="right",
            x=1.0, y=1.08, xanchor="right", yanchor="bottom",
            showactive=True,
            active=0,
            buttons=[
                dict(
                    label="Per Station",
                    method="update",
                    args=[
                        {"visible": [False] + [True]  * n_ws},
                        {"showlegend": True},
                    ],
                ),
                dict(
                    label="All Stations",
                    method="update",
                    args=[
                        {"visible": [True]  + [False] * n_ws},
                        {"showlegend": False},
                    ],
                ),
            ],
            bgcolor="white", bordercolor="#cccccc",
            font=dict(color=INK, size=11),
            pad=dict(r=4, t=4),
        )],
        xaxis=dict(title="Day", tickangle=-30),
        yaxis=dict(
            title="Average pick time (seconds)",
            showgrid=True, gridcolor="#eeeeee",
        ),
        showlegend=True,
        legend=dict(orientation="h", y=-0.22, x=0.5, xanchor="center"),
        plot_bgcolor="white", paper_bgcolor="white",
        font=dict(color=INK, family="Inter, sans-serif"),
        margin=dict(t=100, b=90, l=70, r=40),
        hoverlabel=dict(bgcolor="white", bordercolor="#cccccc"),
    )
    return {
        "id":          "summary_pick_time",
        "title":       "Average Operator Pick Time Trend",
        "figure":      fig,
        "source":      "All days",
        "method":      (
            "Average operator pick time (arrived → triggerGo) per day. "
            "'All Stations' shows the combined average across the whole floor. "
            "'Per Station' shows one line per workstation so you can spot which "
            "stations are driving the overall trend. "
            "Rising values can indicate operator fatigue, a changing task mix, "
            "or ergonomic issues at specific stations."
        ),
        "export_hint": "summary_metrics.xlsx",
    }


def _avg_util_pct_trend(stats: list[dict]) -> dict | None:
    """Average % of implied pick rate per station per day."""
    labels = [r["label"] for r in stats]

    seen: dict[str, None] = {}
    for r in stats:
        for ws in r.get("avg_util_pct_by_station", {}).keys():
            seen[ws] = None
    all_stations = sorted(seen.keys())

    if not all_stations:
        return None

    # combined: mean across all stations for each day
    combined: list[float | None] = []
    for r in stats:
        vals = list(r.get("avg_util_pct_by_station", {}).values())
        combined.append(float(np.mean(vals)) if vals else None)

    if all(v is None for v in combined):
        return None

    fig = go.Figure()

    # Trace 0: combined — hidden by default
    fig.add_trace(go.Scatter(
        x=labels, y=combined,
        mode="lines+markers",
        line=dict(color="#16a34a", width=2.5),
        marker=dict(size=9, color="#16a34a"),
        name="All stations (avg)",
        hovertemplate="<b>%{x}</b><br>Avg util: %{y:.1f}%<extra></extra>",
        visible=False,
    ))

    # Traces 1…N: one per station — visible by default
    for i, ws in enumerate(all_stations):
        color = _DAY_PALETTE[i % len(_DAY_PALETTE)]
        vals  = [r.get("avg_util_pct_by_station", {}).get(ws) for r in stats]
        fig.add_trace(go.Scatter(
            x=labels, y=vals,
            mode="lines+markers",
            line=dict(color=color, width=2),
            marker=dict(size=7, color=color),
            name=ws,
            hovertemplate=f"<b>%{{x}}</b><br>{ws}: %{{y:.1f}}%<extra></extra>",
            visible=True,
        ))

    n_ws = len(all_stations)

    fig.update_layout(
        title=dict(
            text="Average % of Implied Pick Rate per Station per Day",
            x=0, pad=dict(l=12), font=dict(size=17, color=INK),
        ),
        updatemenus=[dict(
            type="buttons", direction="right",
            x=1.0, y=1.08, xanchor="right", yanchor="bottom",
            showactive=True,
            active=0,
            buttons=[
                dict(
                    label="Per Station",
                    method="update",
                    args=[
                        {"visible": [False] + [True]  * n_ws},
                        {"showlegend": True},
                    ],
                ),
                dict(
                    label="All Stations",
                    method="update",
                    args=[
                        {"visible": [True]  + [False] * n_ws},
                        {"showlegend": False},
                    ],
                ),
            ],
            bgcolor="white", bordercolor="#cccccc",
            font=dict(color=INK, size=11),
            pad=dict(r=4, t=4),
        )],
        xaxis=dict(title="Day", tickangle=-30),
        yaxis=dict(
            title="Avg % of implied pick rate",
            showgrid=True, gridcolor="#eeeeee",
            rangemode="tozero",
        ),
        showlegend=True,
        legend=dict(orientation="h", y=-0.22, x=0.5, xanchor="center"),
        plot_bgcolor="white", paper_bgcolor="white",
        font=dict(color=INK, family="Inter, sans-serif"),
        margin=dict(t=100, b=90, l=70, r=40),
        hoverlabel=dict(bgcolor="white", bordercolor="#cccccc"),
    )
    return {
        "id":          "summary_util_pct_trend",
        "title":       "Average % of Implied Pick Rate per Station per Day",
        "figure":      fig,
        "source":      "All days",
        "method":      (
            "For each station-hour, implied throughput = 3 600 ÷ (avg pick time + 6 s switch). "
            "Utilisation % = actual completions ÷ implied × 100. "
            "The value shown per station per day is the mean of that ratio across all active hours. "
            "100 % means the station was producing exactly as fast as operator speed allows. "
            "Values below 100 % indicate robot-supply gaps or non-pick losses. "
            "Values above 100 % can occur when pick times were measured over a shorter window "
            "than the completion count, or when robots queued ahead of the operator."
        ),
        "export_hint": "summary_metrics.xlsx",
    }


def _tail_severity(stats: list[dict]) -> dict | None:
    """p90 ÷ median cycle time ratio per day."""
    ratios: list[float | None] = []
    for r in stats:
        med, p90 = r["med_cycle_min"], r["p90_cycle_min"]
        ratios.append(p90 / med if (med and p90 and med > 0) else None)

    if all(v is None for v in ratios):
        return None

    labels = [r["label"] for r in stats]
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=labels, y=ratios,
        mode="lines+markers",
        line=dict(color="#7c3aed", width=2.5),
        marker=dict(size=9, color="#7c3aed"),
        name="p90 ÷ median",
        hovertemplate="<b>%{x}</b><br>p90 ÷ median: %{y:.2f}×<extra></extra>",
    ))
    for ref_y, label_text, color in [
        (1.5, "1.5× — moderate tail", "#f59e0b"),
        (2.0, "2.0× — severe tail",   ACCENT),
    ]:
        fig.add_hline(
            y=ref_y, line_dash="dash", line_color=color, line_width=1.5,
            annotation_text=label_text, annotation_position="top right",
            annotation_font=dict(color=color, size=10),
        )
    fig.update_layout(
        title=dict(
            text="Cycle Time Tail Severity (p90 ÷ Median) per Day",
            x=0, pad=dict(l=12), font=dict(size=17, color=INK),
        ),
        xaxis=dict(title="Day", tickangle=-30),
        yaxis=dict(
            title="p90 ÷ median ratio",
            showgrid=True, gridcolor="#eeeeee",
            rangemode="tozero",
        ),
        plot_bgcolor="white", paper_bgcolor="white",
        font=dict(color=INK, family="Inter, sans-serif"),
        margin=dict(t=70, b=70, l=70, r=40),
        showlegend=False,
        hoverlabel=dict(bgcolor="white", bordercolor="#cccccc"),
    )
    return {
        "id":          "summary_tail_severity",
        "title":       "Cycle Time Tail Severity (p90 ÷ Median)",
        "figure":      fig,
        "source":      "All days",
        "method":      (
            "Ratio of the 90th-percentile cycle time to the median, per day. "
            "A ratio of 1.0 would mean a perfectly tight distribution with no tail. "
            "A rising ratio means the worst tasks are getting disproportionately slower "
            "even if the median holds steady — tail congestion is quietly worsening "
            "without surfacing in the headline median metric. "
            "Crossing the 1.5× reference line indicates moderate tail severity; "
            "2.0× is severe and typically points to a queuing or prioritisation problem "
            "affecting a specific subset of tasks or routes."
        ),
        "export_hint": "summary_metrics.xlsx",
    }


def _throughput_consistency(all_days: list[dict]) -> dict | None:
    """Within-day hourly throughput CV and peak-to-mean ratio per day."""
    labels:     list[str]         = []
    cvs:        list[float | None] = []
    peak_means: list[float | None] = []

    for day in all_days:
        labels.append(day["label"])
        cb = day["data"].get("callback")
        if cb is None:
            cvs.append(None)
            peak_means.append(None)
            continue

        act_col = next((c for c in cb.columns if "动作类型" in str(c)), None)
        loc_col = next((c for c in cb.columns if "位置类型" in str(c)), None)
        ts_col  = next((c for c in cb.columns if "时间戳"   in str(c)), None)

        if not (act_col and loc_col and ts_col):
            cvs.append(None)
            peak_means.append(None)
            continue

        lab = cb[cb[loc_col].astype(str).str.startswith("LABOR")].copy()
        lab = lab[lab[act_col] == "complete"]
        lab["_hour"] = pd.to_datetime(lab[ts_col], errors="coerce").dt.floor("h")
        hourly = lab.groupby("_hour").size()

        if len(hourly) < 2:
            cvs.append(None)
            peak_means.append(None)
            continue

        mu = float(hourly.mean())
        if mu <= 0:
            cvs.append(None)
            peak_means.append(None)
            continue

        cvs.append(float(hourly.std() / mu))
        peak_means.append(float(hourly.max() / mu))

    if all(v is None for v in cvs):
        return None

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=labels, y=cvs,
        mode="lines+markers",
        line=dict(color="#2563eb", width=2.5),
        marker=dict(size=9, color="#2563eb"),
        name="CV (σ ÷ mean)",
        hovertemplate="<b>%{x}</b><br>CV: %{y:.2f}<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=labels, y=peak_means,
        mode="lines+markers",
        line=dict(color=ACCENT, width=2, dash="dot"),
        marker=dict(size=7, color=ACCENT),
        name="Peak ÷ mean",
        hovertemplate="<b>%{x}</b><br>Peak ÷ mean: %{y:.2f}×<extra></extra>",
    ))
    fig.update_layout(
        title=dict(
            text="Within-Day Throughput Consistency per Day",
            x=0, pad=dict(l=12), font=dict(size=17, color=INK),
        ),
        xaxis=dict(title="Day", tickangle=-30),
        yaxis=dict(
            title="Ratio",
            showgrid=True, gridcolor="#eeeeee",
            rangemode="tozero",
        ),
        legend=dict(orientation="h", y=1.08, x=1, xanchor="right"),
        plot_bgcolor="white", paper_bgcolor="white",
        font=dict(color=INK, family="Inter, sans-serif"),
        margin=dict(t=80, b=70, l=70, r=40),
        hoverlabel=dict(bgcolor="white", bordercolor="#cccccc"),
    )
    return {
        "id":          "summary_throughput_consistency",
        "title":       "Within-Day Throughput Consistency",
        "figure":      fig,
        "source":      "All days",
        "method":      (
            "Two measures of how evenly throughput is distributed across hours within each day. "
            "CV (σ ÷ mean of hourly completions): lower means smoother, more predictable "
            "throughput. A CV of 0 would be perfectly flat across every hour. "
            "Peak ÷ mean: how many times larger the busiest single hour is versus the daily "
            "average — a value of 2.0 means the peak hour was twice as busy as average. "
            "Both metrics rising together means operations are becoming lumpier and harder "
            "for the fleet to absorb. A high CV alongside a low total task count often "
            "reflects a slow start or early finish rather than genuine intra-day variability."
        ),
        "export_hint": "summary_metrics.xlsx",
    }


def _distribution_overlay(all_days: list[dict]) -> dict | None:
    """Overlaid violin per day — reveals how the full cycle time distribution shifts."""
    CAP_MIN = 30.0  # minutes, consistent with the single-day histogram

    traces: list[tuple[str, np.ndarray]] = []
    for day in all_days:
        tlc = day["data"].get("lifecycle")
        if tlc is None or TOTAL_DURATION_COL not in tlc.columns:
            continue
        t = tlc[TOTAL_DURATION_COL].dropna()
        t = t[(t >= 0) & (t < CAP_MIN * 60)] / 60.0
        if len(t) >= 10:
            traces.append((day["label"], t.values))

    if len(traces) < 2:
        return None

    fig = go.Figure()
    for i, (label, vals) in enumerate(traces):
        color = _DAY_PALETTE[i % len(_DAY_PALETTE)]
        fig.add_trace(go.Violin(
            x=[label] * len(vals),
            y=vals,
            name=label,
            line_color=color,
            fillcolor=color,
            opacity=0.35,
            box_visible=True,
            box_fillcolor="white",
            meanline_visible=True,
            meanline_color=color,
            points=False,
            hovertemplate="<b>" + label + "</b><br>Cycle: %{y:.1f} min<extra></extra>",
        ))
    fig.update_layout(
        title=dict(
            text=f"Cycle Time Distribution Shift Across Days (capped at {int(CAP_MIN)} min)",
            x=0, pad=dict(l=12), font=dict(size=17, color=INK),
        ),
        xaxis=dict(title="Day", tickangle=-30),
        yaxis=dict(
            title="Cycle time (minutes)",
            showgrid=True, gridcolor="#eeeeee",
            rangemode="tozero",
        ),
        violinmode="overlay",
        plot_bgcolor="white", paper_bgcolor="white",
        font=dict(color=INK, family="Inter, sans-serif"),
        margin=dict(t=80, b=70, l=70, r=40),
        showlegend=False,
        hoverlabel=dict(bgcolor="white", bordercolor="#cccccc"),
    )
    return {
        "id":          "summary_distribution_overlay",
        "title":       "Cycle Time Distribution Shift Across Days",
        "figure":      fig,
        "source":      "All days",
        "method":      (
            f"One violin per day showing the full distribution of task cycle times "
            f"(capped at {int(CAP_MIN)} min to match the single-day histogram). "
            "The inner box covers the interquartile range; the line through it is the median. "
            "The horizontal line through the violin body is the mean. "
            "A shifting violin body means the typical task is getting faster or slower. "
            "A widening shape means more day-to-day variability. "
            "A growing upper tail — the violin stretching upward — means tail congestion "
            "is worsening even if the body (and median) appears unchanged. "
            "A consistent downward drift of the median line across days is the clearest "
            "signal of genuine operational improvement."
        ),
        "export_hint": "summary_metrics.xlsx",
    }


def _pick_r2_trend(all_days: list[dict]) -> dict | None:
    """
    For each day, regress actual throughput (tasks/hr per station-hour) on avg
    pick time (s).  Plot R² per day as bars + slope as a secondary line.

    A high R² means operator speed is the dominant lever that day.
    A low R² means robot supply or other factors dominate.
    """
    _SWITCH_S = 6.0  # keep consistent with dwell_time.py

    day_labels: list[str]        = []
    r2_vals:    list[float|None] = []
    slope_vals: list[float|None] = []
    n_obs_vals: list[int|None]   = []

    for day in all_days:
        label = day["label"]
        lsr   = day["data"].get("station")
        cb    = day["data"].get("callback")
        cfg   = day.get("cfg", {})

        day_labels.append(label)

        if lsr is None or cb is None or not cfg.get("point2ws") or not cfg.get("ws_order"):
            r2_vals.append(None)
            slope_vals.append(None)
            n_obs_vals.append(None)
            continue

        ws_order = cfg["ws_order"]
        point2ws = cfg["point2ws"]

        # ── pick times per station×hour ──────────────────────────────────────
        lsr_c = lsr.copy()
        lsr_c["ts"]      = pd.to_datetime(lsr_c["时间戳"], errors="coerce")
        lsr_c["station"] = lsr_c["位置编号"].map(point2ws)

        amr_type = cfg.get("amr_type")
        if amr_type and "机器人类型" in lsr_c.columns:
            lsr_c = lsr_c[lsr_c["机器人类型"] == amr_type]

        ev = lsr_c.sort_values(["机器人编号", "ts"])
        pick_rows: list[dict] = []
        for _rb, sub in ev.groupby("机器人编号"):
            arr = arr_loc = None
            for ts, et, loc in sub[["ts", "事件类型", "station"]].values:
                if et == "arrived":
                    arr, arr_loc = ts, loc
                elif et == "triggerGo" and arr is not None:
                    pick_rows.append({
                        "station": arr_loc,
                        "hour_dt": arr.floor("h"),
                        "pick_s":  (ts - arr).total_seconds(),
                    })
                    arr = None

        if not pick_rows:
            r2_vals.append(None)
            slope_vals.append(None)
            n_obs_vals.append(None)
            continue

        pick_df = pd.DataFrame(pick_rows).dropna(subset=["station"])
        pick_df = pick_df[(pick_df["pick_s"] >= 0) & (pick_df["pick_s"] < 3600)]
        pivot_avg_s = (
            pick_df.groupby(["station", "hour_dt"])["pick_s"]
            .mean()
            .unstack()
        )
        if pivot_avg_s.empty:
            r2_vals.append(None)
            slope_vals.append(None)
            n_obs_vals.append(None)
            continue

        day_ref   = pivot_avg_s.columns.min().normalize()
        all_hours = pd.date_range(day_ref, periods=24, freq="h")
        pivot_avg_s = pivot_avg_s.reindex(columns=all_hours)

        # ── actual completions per station×hour ──────────────────────────────
        loc_col = next((c for c in cb.columns if "位置类型" in str(c)), None)
        act_col = next((c for c in cb.columns if "动作类型" in str(c)), None)
        ts_col  = next((c for c in cb.columns if "时间戳"   in str(c)), None)

        if not (loc_col and act_col and ts_col):
            r2_vals.append(None)
            slope_vals.append(None)
            n_obs_vals.append(None)
            continue

        lab = cb[cb[loc_col].astype(str).str.startswith("LABOR")].copy()
        lab["ts"]      = pd.to_datetime(lab[ts_col], errors="coerce")
        lab["hour"]    = lab["ts"].dt.floor("h")
        lab            = lab[lab[act_col] == "complete"]
        lab["station"] = lab[loc_col].map(
            lambda v: v if v in ws_order else point2ws.get(v, v)
        )
        actual_pivot = (
            lab.groupby(["hour", "station"])
               .size()
               .unstack(fill_value=0)
               .reindex(index=all_hours, columns=ws_order, fill_value=0)
        )
        actual_t = actual_pivot.T  # station × hour

        # ── build paired observations ─────────────────────────────────────────
        records: list[tuple[float, float]] = []
        for ws in ws_order:
            if ws not in pivot_avg_s.index or ws not in actual_t.index:
                continue
            for col in pivot_avg_s.columns:
                pick_s = pivot_avg_s.at[ws, col]
                tph    = actual_t.at[ws, col] if col in actual_t.columns else float("nan")
                if pd.notna(pick_s) and pd.notna(tph) and pick_s > 0 and tph > 0:
                    records.append((pick_s, tph))

        if len(records) < 3:
            r2_vals.append(None)
            slope_vals.append(None)
            n_obs_vals.append(len(records))
            continue

        x_arr = np.array([r[0] for r in records])
        y_arr = np.array([r[1] for r in records])
        coeffs = np.polyfit(x_arr, y_arr, 1)
        y_pred = np.polyval(coeffs, x_arr)
        ss_res = float(np.sum((y_arr - y_pred) ** 2))
        ss_tot = float(np.sum((y_arr - y_arr.mean()) ** 2))
        r2     = 1.0 - ss_res / ss_tot if ss_tot > 0 else None

        r2_vals.append(r2)
        slope_vals.append(float(coeffs[0]))
        n_obs_vals.append(len(records))

    if all(v is None for v in r2_vals):
        return None

    # ── figure ────────────────────────────────────────────────────────────────
    fig = go.Figure()

    # Trace 0: R² bars (default)
    bar_colors = [
        ("#16a34a" if (v is not None and v >= 0.5) else
         "#f59e0b" if (v is not None and v >= 0.25) else
         "#ef4444") if v is not None else "#d1d5db"
        for v in r2_vals
    ]
    hover_r2 = [
        f"<b>{l}</b><br>R² = {v:.3f}<br>n = {n} observations<extra></extra>"
        if v is not None else
        f"<b>{l}</b><br>R² = n/a<extra></extra>"
        for l, v, n in zip(day_labels, r2_vals, n_obs_vals)
    ]
    fig.add_trace(go.Bar(
        x=day_labels,
        y=[v if v is not None else 0 for v in r2_vals],
        name="R²",
        marker_color=bar_colors,
        hovertemplate=hover_r2,
        visible=True,
    ))

    # Trace 1: slope line (hidden initially, toggle)
    hover_sl = [
        f"<b>{l}</b><br>Slope = {v:+.2f} tasks/hr per s<extra></extra>"
        if v is not None else
        f"<b>{l}</b><br>Slope = n/a<extra></extra>"
        for l, v in zip(day_labels, slope_vals)
    ]
    fig.add_trace(go.Scatter(
        x=day_labels,
        y=slope_vals,
        mode="lines+markers",
        name="OLS slope (tasks/hr per s pick time)",
        line=dict(color="#7c3aed", width=2.5),
        marker=dict(size=9, color="#7c3aed"),
        hovertemplate=hover_sl,
        visible=False,
    ))

    # Reference line at R²=0.5 on the bar view
    fig.add_hline(
        y=0.5, line_dash="dash", line_color="#f59e0b", line_width=1.5,
        annotation_text="R² = 0.50 — moderate explanatory power",
        annotation_position="top right",
        annotation_font=dict(color="#f59e0b", size=10),
    )

    fig.update_layout(
        title=dict(
            text="How Much Does Pick Time Explain Throughput? (R² per Day)",
            x=0, pad=dict(l=12), font=dict(size=17, color=INK),
        ),
        updatemenus=[dict(
            type="buttons", direction="right",
            x=1.0, y=1.08, xanchor="right", yanchor="bottom",
            showactive=True,
            buttons=[
                dict(
                    label="R² (explanatory power)",
                    method="update",
                    args=[
                        {"visible": [True, False]},
                        {
                            "title.text": "How Much Does Pick Time Explain Throughput? (R² per Day)",
                            "yaxis.title.text": "R² — fraction of throughput variance explained by pick time",
                            "yaxis.range": [0, 1],
                        },
                    ],
                ),
                dict(
                    label="OLS Slope",
                    method="update",
                    args=[
                        {"visible": [False, True]},
                        {
                            "title.text": "Pick Time → Throughput Sensitivity (OLS Slope per Day)",
                            "yaxis.title.text": "Slope (tasks/hr per second of pick time)",
                            "yaxis.range": None,
                        },
                    ],
                ),
            ],
            bgcolor="white", bordercolor="#cccccc",
            font=dict(color=INK, size=11),
            pad=dict(r=4, t=4),
        )],
        xaxis=dict(title="Day", tickangle=-30),
        yaxis=dict(
            title="R² — fraction of throughput variance explained by pick time",
            range=[0, 1],
            showgrid=True, gridcolor="#eeeeee",
        ),
        plot_bgcolor="white", paper_bgcolor="white",
        font=dict(color=INK, family="Inter, sans-serif"),
        margin=dict(t=100, b=110, l=80, r=40),
        showlegend=False,
        hoverlabel=dict(bgcolor="white", bordercolor="#cccccc"),
        annotations=[dict(
            xref="paper", yref="paper", x=0, y=-0.20,
            text=(
                "OLS regression of actual completions/hr on avg pick time per station-hour, "
                "repeated independently for each day.  "
                "<b>R² near 1</b> = operator pick speed is the primary driver of throughput — "
                "faster picks directly lift output.  "
                "<b>R² near 0</b> = robot availability, queue depth, or other factors dominate — "
                "improving pick time alone will not move the needle.  "
                "Slope (tasks/hr per s) quantifies sensitivity: "
                "a slope of −1.5 means each 1 s reduction in pick time yields ~1.5 more tasks/hr."
            ),
            font=dict(size=9, color="#666666"), showarrow=False, align="left",
        )],
    )
    return {
        "id":          "summary_pick_r2_trend",
        "title":       "How Much Does Pick Time Explain Throughput? (R² per Day)",
        "figure":      fig,
        "source":      "All days — station record + callback sheets",
        "method":      (
            "For each day, a linear regression (OLS) is fitted to station-hour observations "
            "where X = average operator pick time (s) and Y = actual completions/hr. "
            "R² measures how much of the day's throughput variance is explained by pick "
            "time alone — values near 1 mean operator speed is the dominant lever; "
            "values near 0 mean robot supply, queue depth, or task-mix variation dominate. "
            "The slope (tasks/hr per second of pick time) shows sensitivity: "
            "a steeper negative slope means reducing pick time yields more throughput gain. "
            "Toggle between R² and slope views with the buttons above the chart."
        ),
        "export_hint": "summary_metrics.xlsx",
    }


# ── xlsx export ──────────────────────────────────────────────────────────────

def export_xlsx(all_days: list[dict], outdir: str) -> None:
    """Write summary_metrics.xlsx to outdir with one sheet per summary chart."""
    stats = _collect_stats(all_days)

    # ── Sheet 1: daily summary ────────────────────────────────────────────────
    daily_rows = []
    for r in stats:
        daily_rows.append({
            "Day":                    r["label"],
            "Total Tasks":            r["total_tasks"],
            "Median Cycle Time (min)": round(r["med_cycle_min"], 2)  if r["med_cycle_min"]  else None,
            "p90 Cycle Time (min)":   round(r["p90_cycle_min"], 2)   if r["p90_cycle_min"]  else None,
            "Avg Pick Time (s)":      round(r["avg_pick_s"], 1)      if r["avg_pick_s"]     else None,
            "Median Switch Time (s)": round(r["med_switch_s"], 1)    if r["med_switch_s"]   else None,
        })
    df_daily = pd.DataFrame(daily_rows).set_index("Day")

    # ── Sheet 2: avg pick time by station ─────────────────────────────────────
    all_stations = sorted({ws for r in stats for ws in r.get("avg_pick_by_station", {})})
    pick_rows = []
    for r in stats:
        row: dict = {"Day": r["label"]}
        for ws in all_stations:
            v = r.get("avg_pick_by_station", {}).get(ws)
            row[ws] = round(v, 1) if v is not None else None
        pick_rows.append(row)
    df_pick = pd.DataFrame(pick_rows).set_index("Day") if pick_rows else pd.DataFrame()

    # ── Sheet 3: day-over-day % change ────────────────────────────────────────
    labels     = [r["label"] for r in stats]
    metric_defs = [
        ("Total Tasks",             "total_tasks",   True),
        ("Median Cycle Time (min)", "med_cycle_min", False),
        ("p90 Cycle Time (min)",    "p90_cycle_min", False),
        ("Avg Pick Time (s)",       "avg_pick_s",    False),
        ("Median Switch Time (s)",  "med_switch_s",  False),
    ]
    delta_data: dict = {"Metric": [m[0] for m in metric_defs]}
    for i in range(len(stats) - 1):
        col = f"{labels[i]} → {labels[i + 1]}"
        col_vals = []
        for _, key, _ in metric_defs:
            v0, v1 = stats[i][key], stats[i + 1][key]
            if v0 and v1 and v0 != 0:
                col_vals.append(round((v1 - v0) / abs(v0) * 100, 1))
            else:
                col_vals.append(None)
        delta_data[col] = col_vals
    df_delta = pd.DataFrame(delta_data).set_index("Metric") if len(stats) >= 2 else pd.DataFrame()

    # ── Sheet 4: tail severity ────────────────────────────────────────────────
    tail_rows = []
    for r in stats:
        med, p90 = r["med_cycle_min"], r["p90_cycle_min"]
        ratio = round(p90 / med, 3) if (med and p90 and med > 0) else None
        tail_rows.append({"Day": r["label"], "p90 / Median Ratio": ratio})
    df_tail = pd.DataFrame(tail_rows).set_index("Day")

    # ── Sheet 5: throughput consistency ──────────────────────────────────────
    consist_rows = []
    for day in all_days:
        cb    = day["data"].get("callback")
        label = day["label"]
        cv    = peak_mean = None
        if cb is not None:
            act_col = next((c for c in cb.columns if "动作类型" in str(c)), None)
            loc_col = next((c for c in cb.columns if "位置类型" in str(c)), None)
            ts_col  = next((c for c in cb.columns if "时间戳"   in str(c)), None)
            if act_col and loc_col and ts_col:
                lab = cb[cb[loc_col].astype(str).str.startswith("LABOR")].copy()
                lab = lab[lab[act_col] == "complete"]
                lab["_h"] = pd.to_datetime(lab[ts_col], errors="coerce").dt.floor("h")
                hourly = lab.groupby("_h").size()
                if len(hourly) >= 2:
                    mu = float(hourly.mean())
                    if mu > 0:
                        cv        = round(float(hourly.std() / mu), 3)
                        peak_mean = round(float(hourly.max() / mu), 3)
        consist_rows.append({"Day": label, "CV (σ / mean)": cv, "Peak / Mean Ratio": peak_mean})
    df_consist = pd.DataFrame(consist_rows).set_index("Day")

    # ── Sheet 6: cycle time percentiles per day ───────────────────────────────
    pct_rows = []
    for day in all_days:
        tlc = day["data"].get("lifecycle")
        if tlc is None or TOTAL_DURATION_COL not in tlc.columns:
            continue
        t = tlc[TOTAL_DURATION_COL].dropna()
        t = t[(t >= 0) & (t < 7200)] / 60.0
        if len(t) == 0:
            continue
        q = t.quantile([0.25, 0.5, 0.75, 0.9, 0.95, 0.99]).round(2)
        pct_rows.append({
            "Day":        day["label"],
            "Mean (min)": round(float(t.mean()), 2),
            "p25 (min)":  q[0.25],
            "p50 (min)":  q[0.50],
            "p75 (min)":  q[0.75],
            "p90 (min)":  q[0.90],
            "p95 (min)":  q[0.95],
            "p99 (min)":  q[0.99],
            "Count":      len(t),
        })
    df_pct = pd.DataFrame(pct_rows).set_index("Day") if pct_rows else pd.DataFrame()

    with pd.ExcelWriter(os.path.join(outdir, "summary_metrics.xlsx")) as w:
        df_daily.to_excel(w, sheet_name="daily_summary")
        if not df_pick.empty:
            df_pick.to_excel(w, sheet_name="avg_pick_time_by_station")
        if not df_delta.empty:
            df_delta.to_excel(w, sheet_name="day_over_day_change_pct")
        df_tail.to_excel(w, sheet_name="tail_severity")
        df_consist.to_excel(w, sheet_name="throughput_consistency")
        if not df_pct.empty:
            df_pct.to_excel(w, sheet_name="cycle_time_percentiles")


# ── public entry point ────────────────────────────────────────────────────────

def run(all_days: list[dict]) -> list[dict]:
    """
    Build summary trend charts from multiple days.

    Parameters
    ----------
    all_days : list[dict]
        Each dict: {"label": str, "data": {"callback": df, "lifecycle": df, ...}}

    Returns
    -------
    list of chart dicts (same schema as other analysis modules).
    """
    if len(all_days) < 2:
        return []

    stats  = _collect_stats(all_days)
    labels = [r["label"] for r in stats]
    charts: list[dict] = []

    # ── Chart 1: tasks + cycle time ───────────────────────────────────────────
    tasks  = [r["total_tasks"]   for r in stats]
    cycles = [r["med_cycle_min"] for r in stats]

    has_tasks  = any(t is not None for t in tasks)
    has_cycles = any(c is not None for c in cycles)

    if has_tasks or has_cycles:
        fig1 = make_subplots(
            rows=1, cols=2,
            subplot_titles=[
                "Total Completed Tasks per Day",
                "Median Cycle Time per Day (min)",
            ],
            horizontal_spacing=0.12,
        )

        if has_tasks:
            fig1.add_trace(go.Bar(
                x=labels,
                y=[t if t is not None else 0 for t in tasks],
                marker_color="#2563eb",
                name="Total tasks",
                hovertemplate="<b>%{x}</b><br>Tasks: %{y:,}<extra></extra>",
            ), row=1, col=1)

        if has_cycles:
            fig1.add_trace(go.Scatter(
                x=labels,
                y=cycles,
                mode="lines+markers",
                line=dict(color=ACCENT, width=2.5),
                marker=dict(size=9, color=ACCENT),
                name="Median cycle (min)",
                hovertemplate="<b>%{x}</b><br>Median cycle: %{y:.1f} min<extra></extra>",
            ), row=1, col=2)

        fig1.update_layout(
            title=dict(
                text="Day-over-Day Performance Trends",
                x=0, pad=dict(l=12), font=dict(size=17, color=INK),
            ),
            plot_bgcolor="white", paper_bgcolor="white",
            font=dict(color=INK, family="Inter, sans-serif"),
            margin=dict(t=80, b=70, l=70, r=40),
            showlegend=False,
            hoverlabel=dict(bgcolor="white", bordercolor="#cccccc"),
        )
        fig1.update_yaxes(showgrid=True, gridcolor="#eeeeee")
        fig1.update_xaxes(tickangle=-30)

        charts.append({
            "id":          "summary_trends",
            "title":       "Day-over-Day Performance Trends",
            "figure":      fig1,
            "source":      "All days",
            "method":      (
                "Left: total completed tasks per day — the headline throughput metric. "
                "Right: median cycle time per day. "
                "Look for consistent trends across the period (e.g. throughput declining "
                "or cycle time creeping up week-over-week) versus isolated anomalies "
                "(a single outlier day). "
                "Days where throughput is high AND cycle time is also high are operating "
                "near or beyond comfortable capacity — the system is delivering volume "
                "but at the cost of service consistency."
            ),
            "export_hint": "summary_metrics.xlsx",
        })

    # ── Chart 2: average pick time per day (all stations + per station) ─────
    result = _pick_time_per_station(stats)
    if result:
        charts.append(result)

    # ── Chart 3: avg % of implied pick rate per station per day ──────────────
    result = _avg_util_pct_trend(stats)
    if result:
        charts.append(result)

    # ── Charts 4–8: disabled ──────────────────────────────────────────────────
    # result = _delta_heatmap(stats)          # Day-over-Day Change in Key Metrics
    # result = _tail_severity(stats)          # Cycle Time Tail Severity (p90 ÷ Median)
    # result = _throughput_consistency(all_days)  # Within-Day Throughput Consistency
    # result = _distribution_overlay(all_days)    # Cycle Time Distribution Shift Across Days
    # result = _pick_r2_trend(all_days)           # How Much Does Pick Time Explain Throughput?

    return charts
