"""
analyses/cycle_time.py — full container journey from task-create to complete.

Charts produced
---------------
1. Cycle time histogram (distribution, capped at 30 min).
2. Median cycle time by hour (bar + p90 line).
3. Throughput demand vs median cycle time (dual-axis + scatter).
4. Lifecycle stage composition donut  (only when stage columns detected).
5. Stage composition stacked bar by destination station  (only with stages + dest col).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from config import ACCENT, AUTO_TYPE_PALETTE, INK, TOTAL_DURATION_COL


# ── helpers ───────────────────────────────────────────────────────────────────

def _base_layout(fig: go.Figure, title: str) -> None:
    fig.update_layout(
        title=dict(text=title, x=0, pad=dict(l=12), font=dict(size=17, color=INK)),
        plot_bgcolor="white", paper_bgcolor="white",
        font=dict(color=INK, family="Inter, sans-serif"),
        margin=dict(t=70, b=70, l=70, r=40),
        hoverlabel=dict(bgcolor="white", bordercolor="#cccccc"),
    )


# ── sub-charts ────────────────────────────────────────────────────────────────

def _histogram(tj: pd.DataFrame) -> dict:
    t   = tj["cycle_min"]
    CAP = 30
    med, p90, p99 = t.median(), t.quantile(0.9), t.quantile(0.99)
    _hist_hour_col = tj["hour"].dt.strftime("%H:%M") if hasattr(tj["hour"].iloc[0], "strftime") else tj["hour"].astype(str)
    beyond = (t > CAP).mean() * 100

    bins   = list(np.arange(0, CAP + 0.5, 0.5))
    clipped = t.clip(upper=CAP)

    fig = go.Figure()
    fig.add_trace(go.Histogram(
        x=clipped, xbins=dict(start=0, end=CAP, size=0.5),
        marker_color="#2563eb", opacity=0.85,
        hovertemplate="Bin: %{x:.1f} min<br>Count: %{y:,}<extra></extra>",
        name="Tasks",
    ))
    for (val, color, label), yshift in zip(
        [
            (med,             INK,      f"Median {med:.1f} min"),
            (p90,             ACCENT,   f"p90 {p90:.1f} min"),
            (min(p99, CAP),   "#7c3aed", f"p99 {p99:.1f} min"),
        ],
        [0, -24, -48],
    ):
        fig.add_vline(x=val, line_color=color, line_width=2,
                      annotation_text=label, annotation_position="top right",
                      annotation_yshift=yshift,
                      annotation_font=dict(color=color, size=11))
    _base_layout(fig, "Distribution of Container Cycle Times")
    fig.update_layout(
        xaxis=dict(title="Cycle time (minutes)", range=[0, CAP]),
        yaxis=dict(title="Number of tasks", showgrid=True, gridcolor="#eeeeee"),
        showlegend=False,
        annotations=[dict(
            xref="paper", yref="paper", x=0, y=-0.18,
            text=(f"{len(t):,} tasks · capped at {CAP} min "
                  f"({beyond:.1f}% run longer)"),
            font=dict(size=10, color="#666"), showarrow=False,
        )],
    )
    return {
        "id":          "cycle_histogram",
        "title":       "Distribution of Container Cycle Times",
        "figure":      fig,
        "source":      "Task lifecycle sheet",
        "method":      (
            f"Distribution of total task durations, capped at {CAP} min for readability "
            f"(tasks beyond the cap are counted in the footer annotation). "
            "A narrow, symmetric mound around the median is healthy. "
            "A long right tail — where p90 or p99 is much larger than the median — means a "
            "significant fraction of tasks experience severe delays worth investigating. "
            "A bimodal shape (two humps) often indicates two distinct route types or "
            "operational modes mixing in the same dataset."
        ),
        "export_hint": "cycle_time_distribution.xlsx",
        "raw_data": {
            "description": "All individual task cycle times",
            "summary": {
                "count": int(len(t)),
                "median_min": round(float(med), 3),
                "p90_min": round(float(p90), 3),
                "p99_min": round(float(p99), 3),
                "pct_beyond_cap": round(float((t > CAP).mean() * 100), 2),
            },
            "rows": [
                {"hour": str(h), "cycle_min": round(float(c), 3)}
                for h, c in zip(_hist_hour_col, t.values)
            ],
        },
    }


def _by_hour(tj: pd.DataFrame) -> dict:
    med  = tj.groupby("hour")["cycle_min"].median()
    p90  = tj.groupby("hour")["cycle_min"].quantile(0.9)
    hl   = [h.strftime("%H:00") for h in med.index]
    x    = list(range(len(hl)))

    fig = go.Figure()
    peak = int(med.values.argmax()) if len(med) else 0
    bar_colors = ["#1e3a8a" if i == peak else "#2563eb" for i in range(len(med))]
    fig.add_trace(go.Bar(
        x=hl, y=med.values, marker_color=bar_colors,
        name="Median cycle time", opacity=0.9,
        hovertemplate="<b>%{x}</b><br>Median: %{y:.1f} min<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=hl, y=p90.values, mode="lines+markers",
        line=dict(color=ACCENT, width=2), marker=dict(size=5),
        name="p90",
        hovertemplate="<b>%{x}</b><br>p90: %{y:.1f} min<extra></extra>",
    ))
    _base_layout(fig, "Median Container Cycle Time by Hour")
    fig.update_layout(
        xaxis=dict(title="Hour", tickangle=-45),
        yaxis=dict(title="Cycle time (minutes)", showgrid=True, gridcolor="#eeeeee"),
        legend=dict(orientation="h", y=1.08, x=1, xanchor="right"),
    )
    return {
        "id":          "cycle_by_hour",
        "title":       "Median Cycle Time by Hour of Day",
        "figure":      fig,
        "source":      "Task lifecycle sheet",
        "method":      (
            "Median (bars) and p90 (line) cycle time per hour of day. "
            "Hours where the p90 line spikes well above the median bars signal episodic "
            "congestion — the typical task is fine but outlier tasks are badly delayed. "
            "Hours where both bars and line are elevated together indicate systemic slowdowns, "
            "often correlating with peak throughput periods in the throughput chart. "
            "Comparing the two charts: if throughput is high AND cycle time is high, the "
            "system is capacity-constrained in that window."
        ),
        "export_hint": "cycle_time_distribution.xlsx",
        "raw_data": {
            "description": "Median and p90 cycle time per hour of day",
            "rows": [
                {
                    "hour": h,
                    "median_min": round(float(med.iloc[i]), 3),
                    "p90_min":    round(float(p90.iloc[i]),  3),
                }
                for i, h in enumerate(hl)
            ],
        },
    }


def _demand_vs_cycle(tj: pd.DataFrame, cb: pd.DataFrame) -> dict | None:
    cb = cb.copy()
    cb["ts"]   = pd.to_datetime(cb["时间戳"])
    cb["hour"] = cb["ts"].dt.floor("h")
    comp   = cb[
        (cb["动作类型"] == "complete") &
        cb["位置类型"].astype(str).str.startswith("LABOR")
    ]
    demand = comp.groupby("hour").size()
    med    = tj.groupby("hour")["cycle_min"].median()
    joined = pd.DataFrame({"demand": demand, "cycle": med}).dropna()
    if joined.empty:
        return None

    hl = [h.strftime("%H:00") for h in joined.index]

    fig = make_subplots(
        rows=1, cols=2,
        column_widths=[0.62, 0.38],
        specs=[[{"secondary_y": True}, {}]],
        subplot_titles=[
            "Hourly throughput (bars) vs median cycle time (line, right axis)",
            "Demand vs cycle time — each point = 1 hour",
        ],
    )

    # Left: dual-axis time chart — throughput on primary, cycle time on secondary
    fig.add_trace(go.Bar(
        x=hl, y=joined["demand"].values, marker_color="#cbd5e1",
        name="Throughput (tasks/hr)", opacity=0.85,
        hovertemplate="<b>%{x}</b><br>Throughput: %{y:,}<extra></extra>",
    ), row=1, col=1, secondary_y=False)
    fig.add_trace(go.Scatter(
        x=hl, y=joined["cycle"].values, mode="lines+markers",
        line=dict(color="#2563eb", width=2.5), marker=dict(size=5),
        name="Median cycle time (min)",
        hovertemplate="<b>%{x}</b><br>Cycle: %{y:.1f} min<extra></extra>",
    ), row=1, col=1, secondary_y=True)

    # Right: scatter + trend
    xd, yd = joined["demand"].values, joined["cycle"].values
    coef       = np.polyfit(xd, yd, 1)
    xs         = np.linspace(xd.min(), xd.max(), 80)
    r          = float(np.corrcoef(xd, yd)[0, 1])
    # Spearman ρ = Pearson r of the rank-transformed data (no scipy needed)
    r_spearman = float(np.corrcoef(
        pd.Series(xd).rank().values, pd.Series(yd).rank().values
    )[0, 1])

    fig.add_trace(go.Scatter(
        x=xd, y=yd, mode="markers",
        marker=dict(size=7, color="#2563eb", opacity=0.75,
                    line=dict(width=0.5, color="white")),
        name="Hour sample",
        hovertemplate="Throughput: %{x:,}<br>Cycle: %{y:.1f} min<extra></extra>",
    ), row=1, col=2)
    fig.add_trace(go.Scatter(
        x=xs, y=np.poly1d(coef)(xs), mode="lines",
        line=dict(color=ACCENT, width=2, dash="dash"),
        name=f"Linear trend  r={r:.2f}  ρ={r_spearman:.2f}", showlegend=True,
    ), row=1, col=2)

    fig.update_layout(
        title=dict(text="Throughput Demand vs Cycle Time", x=0, pad=dict(l=12), font=dict(size=17, color=INK)),
        plot_bgcolor="white", paper_bgcolor="white",
        font=dict(color=INK, family="Inter, sans-serif"),
        margin=dict(t=80, b=70, l=70, r=90),
        legend=dict(orientation="h", y=-0.18, x=0),
        hoverlabel=dict(bgcolor="white", bordercolor="#cccccc"),
    )
    fig.update_xaxes(tickangle=-45)
    fig.update_yaxes(showgrid=True, gridcolor="#eeeeee")
    fig.update_yaxes(title_text="Throughput (tasks/hr)", row=1, col=1, secondary_y=False)
    fig.update_yaxes(
        title_text="Median cycle time (min)", color="#2563eb",
        row=1, col=1, secondary_y=True,
        showgrid=False,
    )

    return {
        "id":          "demand_vs_cycle",
        "title":       "Throughput Demand vs Cycle Time",
        "figure":      fig,
        "source":      "Task lifecycle sheet + callback detail sheet",
        "method":      (
            "Left: hourly throughput (bars) overlaid with median cycle time (line, right axis) — "
            "shows whether the two move together. "
            "Right: each dot is one hour; the dashed line is a linear trend. "
            "A positive slope (r > 0, ρ > 0) means the system is demand-sensitive: "
            "higher throughput causes longer cycles, the hallmark of a capacity-constrained queue. "
            "A flat or negative slope suggests cycle time is driven by other factors "
            "(storage distances, operator pace, robot availability) rather than demand volume. "
            "Pearson r measures linear correlation; Spearman ρ is more reliable when the "
            "relationship is non-linear (common near capacity limits)."
        ),
        "export_hint": "cycle_time_distribution.xlsx",
        "raw_data": {
            "description": "Hourly throughput demand and median cycle time",
            "pearson_r": round(float(r), 4),
            "spearman_rho": round(float(r_spearman), 4),
            "rows": [
                {
                    "hour": h,
                    "demand_tasks_per_hr": int(joined["demand"].iloc[i]),
                    "median_cycle_min": round(float(joined["cycle"].iloc[i]), 3),
                }
                for i, h in enumerate(hl)
            ],
        },
    }


def _stage_donut(tlc: pd.DataFrame, cfg: dict) -> dict | None:
    stages, stage_lbl, stage_col = cfg["stages"], cfg["stage_lbl"], cfg["stage_col"]
    if not stages:
        return None

    meds = []
    for s in stages:
        dd = tlc[s].dropna()
        dd = dd[(dd >= 0) & (dd < 3600)]
        meds.append(float(dd.median()) if len(dd) else 0.0)

    total_med = float(
        tlc[TOTAL_DURATION_COL]
        .pipe(lambda s: s[(s >= 0) & (s < 7200)])
        .median()
    )

    fig = go.Figure(go.Pie(
        labels=stage_lbl,
        values=meds,
        hole=0.45,
        marker=dict(colors=stage_col, line=dict(color="white", width=2)),
        textinfo="label+percent",
        hovertemplate="<b>%{label}</b><br>Median: %{value:.0f}s<br>Share: %{percent}<extra></extra>",
    ))
    fig.update_layout(
        title=dict(text="Median Cycle Time Composition (All Tasks)", x=0, pad=dict(l=12), font=dict(size=17, color=INK)),
        annotations=[dict(
            text=f"<b>{int(total_med)}s</b><br>total median",
            x=0.5, y=0.5, xref="paper", yref="paper",
            showarrow=False, font=dict(size=15, color=INK),
        )],
        plot_bgcolor="white", paper_bgcolor="white",
        font=dict(color=INK, family="Inter, sans-serif"),
        margin=dict(t=70, b=40, l=40, r=40),
        showlegend=True,
        legend=dict(orientation="v", x=1.02, y=0.5),
        hoverlabel=dict(bgcolor="white", bordercolor="#cccccc"),
    )
    return {
        "id":          "cycle_stage_donut",
        "title":       "Median Cycle Time Stage Composition",
        "figure":      fig,
        "source":      "Task lifecycle sheet",
        "method":      (
            f"Median duration of each detected stage column "
            f"({', '.join(stage_lbl)}) across all completed tasks. "
            "The centre shows the actual total-duration median. "
            "The largest slice is where most task time is consumed and therefore the "
            "highest-leverage stage to target for improvement. "
            "Note: stage medians are computed independently and are not additive, "
            "so their sum may differ from the total shown in the centre."
        ),
        "export_hint": "cycle_time_distribution.xlsx",
        "raw_data": {
            "description": "Median duration per lifecycle stage (all tasks combined)",
            "total_median_s": round(float(total_med), 2),
            "stages": [
                {"stage": lbl, "median_s": round(float(v), 2)}
                for lbl, v in zip(stage_lbl, meds)
            ],
        },
    }


def _stage_by_station(tlc: pd.DataFrame, cfg: dict) -> dict | None:
    stages, stage_lbl, stage_col = cfg["stages"], cfg["stage_lbl"], cfg["stage_col"]
    ws_order = cfg["ws_order"]
    if not stages or "station" not in tlc.columns:
        return None

    tj2 = tlc.dropna(subset=[TOTAL_DURATION_COL, "station"])
    tj2 = tj2[(tj2[TOTAL_DURATION_COL] >= 0) & (tj2[TOTAL_DURATION_COL] < 7200)]
    if tj2.empty:
        return None

    fig = go.Figure()
    bottoms = np.zeros(len(ws_order))
    for s, lbl, c in zip(stages, stage_lbl, stage_col):
        vals = np.array([
            float(np.nan_to_num(
                tj2[tj2["station"] == ws][s]
                .pipe(lambda x: x[(x >= 0) & (x < 3600)])
                .median(),
                nan=0.0,
            ))
            for ws in ws_order
        ])
        fig.add_trace(go.Bar(
            name=lbl, x=ws_order, y=vals, base=bottoms,
            marker_color=c,
            hovertemplate="<b>%{x}</b><br>" + lbl + ": %{y:.0f}s<extra></extra>",
        ))
        bottoms += vals

    fig.update_layout(
        barmode="overlay",
        title=dict(text="Cycle Time Composition by Workstation", x=0, pad=dict(l=12), font=dict(size=17, color=INK)),
        xaxis_title="Station", yaxis_title="Median time in stage (s)",
        plot_bgcolor="white", paper_bgcolor="white",
        font=dict(color=INK, family="Inter, sans-serif"),
        margin=dict(t=70, b=70, l=70, r=160),
        legend=dict(orientation="v", x=1.01, y=1, xanchor="left", title="Stage"),
        yaxis=dict(showgrid=True, gridcolor="#eeeeee"),
        hoverlabel=dict(bgcolor="white", bordercolor="#cccccc"),
    )
    # Build raw data: per-station median for each stage
    _stage_rows = []
    for _ws in ws_order:
        _row_s: dict = {"station": _ws}
        for _s, _lbl in zip(stages, stage_lbl):
            _sub_s = tj2[tj2["station"] == _ws][_s].pipe(lambda x: x[(x >= 0) & (x < 3600)])
            _row_s[_lbl] = round(float(_sub_s.median()), 2) if len(_sub_s) else None
        _stage_rows.append(_row_s)

    return {
        "id":          "cycle_stage_by_station",
        "title":       "Cycle Time Stage Composition by Workstation",
        "figure":      fig,
        "source":      "Task lifecycle sheet",
        "method":      (
            "Median time in each lifecycle stage, stacked per destination workstation. "
            "Taller bars overall indicate stations with longer cycle times. "
            "Comparing the colour composition across stations reveals *why* they differ: "
            "a station with a disproportionately large delivery-leg segment may be "
            "further from storage or in a congested aisle, while a large allocation-wait "
            "segment points to a robot-supply problem rather than a distance problem."
        ),
        "export_hint": "cycle_time_distribution.xlsx",
        "raw_data": {
            "description": "Median time per lifecycle stage per destination workstation",
            "stages": stage_lbl,
            "rows": _stage_rows,
        },
    }



# ── public entry point ────────────────────────────────────────────────────────

def run(data: dict, cfg: dict) -> list[dict]:
    tlc = data.get("lifecycle")
    cb  = data.get("callback")
    if tlc is None:
        return []

    tlc = tlc.copy()
    tlc["complete_ts"] = pd.to_datetime(tlc["complete(任务完成时间)"])
    tlc["hour"]        = tlc["complete_ts"].dt.floor("h")

    dest_col = next((c for c in tlc.columns if "目标位置" in str(c)), None)
    if dest_col:
        tlc["station"] = tlc[dest_col].where(
            tlc[dest_col].astype(str).str.startswith("LABOR")
        )

    tj = tlc.dropna(subset=[TOTAL_DURATION_COL]).copy()
    tj = tj[(tj[TOTAL_DURATION_COL] >= 0) & (tj[TOTAL_DURATION_COL] < 7200)]
    tj["cycle_min"] = tj[TOTAL_DURATION_COL] / 60.0

    charts: list[dict] = []
    charts.append(_histogram(tj))
    charts.append(_by_hour(tj))

    if cb is not None:
        result = _demand_vs_cycle(tj, cb)
        if result:
            charts.append(result)

    donut = _stage_donut(tlc, cfg)
    if donut:
        charts.append(donut)

    stacked = _stage_by_station(tlc, cfg)
    if stacked:
        charts.append(stacked)

    return charts
