"""
analyses/backlog.py — allocation wait and task backlog from the lifecycle sheet.

Allocation wait = time between a task being created (创建时间) and a robot
being assigned to it (分配时间).  It is the cleanest *demand-pressure* signal
in the dataset: it rises when tasks queue for robots, before cycle time does,
and — unlike completion counts — it measures offered demand rather than
realised throughput, so it is not capped by capacity.

Charts produced
---------------
1. backlog_alloc_wait   — median + p90 allocation wait per hour with task
                          creation volume (demand) bars.
2. backlog_queue_depth  — created-but-unassigned task count over time
                          (minute-resolution sweep-line).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from config import ACCENT, INK

_MAX_WAIT_S = 4 * 3600.0  # waits beyond 4 h are treated as data errors


def _find_col(df: pd.DataFrame, needle: str) -> str | None:
    return next((c for c in df.columns if needle in str(c)), None)


# ── public entry point ────────────────────────────────────────────────────────

def run(data: dict, cfg: dict) -> list[dict]:
    tlc = data.get("lifecycle")
    if tlc is None:
        return []

    create_col = _find_col(tlc, "创建时间")
    assign_col = _find_col(tlc, "分配时间")
    wait_col   = _find_col(tlc, "分配耗时")

    if assign_col is None:
        return []

    df = tlc.copy()
    df["_create"] = (
        pd.to_datetime(df[create_col], errors="coerce") if create_col else pd.NaT
    )
    df["_assign"] = pd.to_datetime(df[assign_col], errors="coerce")

    # Allocation wait: prefer the logged duration column, fall back to the
    # timestamp difference where the column is blank.
    wait_from_col = (
        pd.to_numeric(df[wait_col], errors="coerce") if wait_col else pd.Series(np.nan, index=df.index)
    )
    wait_from_ts = (df["_assign"] - df["_create"]).dt.total_seconds()
    df["_wait_s"]  = wait_from_col.fillna(wait_from_ts)
    df["_derived"] = wait_from_col.isna() & wait_from_ts.notna()
    df = df[(df["_wait_s"] >= 0) & (df["_wait_s"] < _MAX_WAIT_S)]

    if len(df) < 30:
        return []

    # Demand hour = creation hour where known, assignment hour otherwise
    df["_hour"] = df["_create"].fillna(df["_assign"]).dt.floor("h")
    df = df.dropna(subset=["_hour"])
    n_ts_derived = int(df["_derived"].sum())

    charts: list[dict] = []

    # ── Chart 1: allocation wait per hour + demand volume ─────────────────────
    grp    = df.groupby("_hour")["_wait_s"]
    med    = grp.median()
    p90    = grp.quantile(0.9)
    volume = grp.size()
    hl     = [h.strftime("%H:00") for h in med.index]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=hl, y=volume.values, marker_color="#cbd5e1",
        name="Tasks created (demand)", yaxis="y2", opacity=0.7,
        hovertemplate="<b>%{x}</b><br>Tasks created: %{y:,}<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=hl, y=med.values, mode="lines+markers",
        line=dict(color="#2563eb", width=2.5), marker=dict(size=6),
        name="Median wait",
        hovertemplate="<b>%{x}</b><br>Median wait: %{y:.0f} s<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=hl, y=p90.values, mode="lines+markers",
        line=dict(color=ACCENT, width=2, dash="dot"), marker=dict(size=5),
        name="p90 wait",
        hovertemplate="<b>%{x}</b><br>p90 wait: %{y:.0f} s<extra></extra>",
    ))
    fig.update_layout(
        title=dict(
            text="Allocation Wait — How Long Tasks Queue for a Robot",
            x=0, pad=dict(l=12), font=dict(size=17, color=INK),
        ),
        xaxis=dict(title="Hour", tickangle=-45, tickfont=dict(size=9)),
        yaxis=dict(title="Allocation wait (s)", showgrid=True,
                   gridcolor="#eeeeee", rangemode="tozero"),
        yaxis2=dict(title="Tasks created / hr", overlaying="y", side="right",
                    showgrid=False, rangemode="tozero"),
        legend=dict(orientation="h", y=1.08, x=1, xanchor="right", font=dict(size=10)),
        plot_bgcolor="white", paper_bgcolor="white",
        font=dict(color=INK, family="Inter, sans-serif"),
        margin=dict(t=80, b=90, l=70, r=70),
        hoverlabel=dict(bgcolor="white", bordercolor="#cccccc"),
    )
    charts.append({
        "id":          "backlog_alloc_wait",
        "title":       "Allocation Wait — How Long Tasks Queue for a Robot",
        "figure":      fig,
        "source":      "Task lifecycle sheet (创建时间 / 分配时间 / 分配耗时)",
        "method":      (
            "Time between task creation and robot assignment, per hour (median line, p90 "
            "dotted line), with the number of tasks created per hour as grey bars on the "
            "right axis. This is the purest demand-pressure signal available: unlike "
            "completion counts it is not capped by system capacity, so rising wait during "
            "high-creation hours is direct evidence that the fleet could not keep up with "
            "offered demand. A p90 spiking far above the median means a subset of tasks "
            "(e.g. one zone or task type) starves while the typical task is fine. "
            "Waits ≥ 4 h are excluded as data errors. Where the 分配耗时 column is blank "
            "the wait is derived from the timestamps instead."
        ),
        "export_hint": "",
        "raw_data": {
            "description": "Allocation wait per hour (median/p90) and creation volume",
            "n_tasks": int(len(df)),
            "n_waits_derived_from_timestamps": n_ts_derived,
            "rows": [
                {
                    "hour": h,
                    "median_wait_s": round(float(med.iloc[i]), 1),
                    "p90_wait_s":    round(float(p90.iloc[i]), 1),
                    "tasks_created": int(volume.iloc[i]),
                }
                for i, h in enumerate(hl)
            ],
        },
    })

    # ── Chart 2: unassigned backlog depth over time ───────────────────────────
    both = df.dropna(subset=["_create", "_assign"])
    both = both[both["_create"] <= both["_assign"]]
    if len(both) >= 100:
        t0 = both["_create"].min().floor("h")
        t1 = both["_assign"].max().ceil("h")
        minutes = pd.date_range(t0, t1, freq="min", inclusive="left")
        if len(minutes) >= 60:
            c_sorted = np.sort(both["_create"].values.astype("int64"))
            a_sorted = np.sort(both["_assign"].values.astype("int64"))
            mins_i64 = minutes.values.astype("int64")
            depth = (
                np.searchsorted(c_sorted, mins_i64, side="right")
                - np.searchsorted(a_sorted, mins_i64, side="right")
            ).astype(float)

            peak_i = int(depth.argmax())

            fig2 = go.Figure()
            fig2.add_trace(go.Scatter(
                x=minutes.tolist(), y=depth,
                mode="lines", fill="tozeroy",
                fillcolor="rgba(37,99,235,0.10)",
                line=dict(color="#2563eb", width=1.8),
                name="Unassigned backlog",
                hovertemplate="<b>%{x|%H:%M}</b><br>Unassigned tasks: %{y:.0f}<extra></extra>",
            ))
            fig2.add_annotation(
                x=minutes[peak_i], y=float(depth[peak_i]),
                text=f"Peak {int(depth[peak_i])} @ {minutes[peak_i].strftime('%H:%M')}",
                showarrow=True, arrowhead=2, ax=30, ay=-30,
                font=dict(size=11, color=ACCENT),
            )
            fig2.update_layout(
                title=dict(
                    text="Task Backlog — Created but Not Yet Assigned",
                    x=0, pad=dict(l=12), font=dict(size=17, color=INK),
                ),
                xaxis=dict(title="Time of day", tickformat="%H:%M", tickangle=-45),
                yaxis=dict(title="Unassigned tasks", showgrid=True,
                           gridcolor="#eeeeee", rangemode="tozero"),
                plot_bgcolor="white", paper_bgcolor="white",
                font=dict(color=INK, family="Inter, sans-serif"),
                margin=dict(t=70, b=90, l=70, r=40),
                showlegend=False,
                hoverlabel=dict(bgcolor="white", bordercolor="#cccccc"),
            )
            charts.append({
                "id":          "backlog_queue_depth",
                "title":       "Task Backlog — Created but Not Yet Assigned",
                "figure":      fig2,
                "source":      "Task lifecycle sheet (创建时间 / 分配时间)",
                "method":      (
                    "Minute-resolution count of tasks that exist (created) but have no robot "
                    "assigned yet, computed with a sweep-line over creation→assignment "
                    "intervals. Only tasks with both timestamps contribute "
                    f"({len(both):,} of {len(df):,} tasks), so the level is a lower bound "
                    "when creation timestamps are sparsely logged. "
                    "Sustained plateaus mean the dispatcher is saturated — every robot is "
                    "committed and new demand queues. Sharp spikes that drain quickly are "
                    "wave releases, which are usually benign. Compare the peak window with "
                    "the fleet-utilisation chart: a backlog peak while fleet utilisation is "
                    "below ~90 % points to a dispatcher/allocation constraint rather than "
                    "fleet size."
                ),
                "export_hint": "",
                "raw_data": {
                    "description": "Unassigned task backlog sampled per minute (nonzero only)",
                    "n_tasks_with_both_timestamps": int(len(both)),
                    "peak_backlog": int(depth[peak_i]),
                    "peak_time": minutes[peak_i].strftime("%H:%M"),
                    "rows": [
                        {"time": m.strftime("%H:%M"), "unassigned": int(v)}
                        for m, v in zip(minutes, depth)
                        if v > 0
                    ],
                },
            })

    return charts
