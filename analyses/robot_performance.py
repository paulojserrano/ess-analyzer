"""
analyses/robot_performance.py — per-robot workload and speed profile.

Robot IDs (机器人编号) are used throughout the pipeline for event pairing but
never analysed in their own right.  This module profiles individual robots:
how much work each one did and how fast its deliveries were, exposing
under-performing or faulty units that no station-level chart can show.

Primary source: lifecycle sheet (tasks + delivery-leg duration per robot).
Fallback:       station sheet (triggerGo counts + pick dwell per robot) when
                the lifecycle sheet is absent.

Charts produced
---------------
1. robot_performance_profile — histogram of tasks per robot + scatter of
                               workload vs median leg time, slow outliers
                               highlighted.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from config import ACCENT, INK
from analyses.fleet_utilization import _delivery_leg_col


def _find_col(df: pd.DataFrame, needle: str) -> str | None:
    return next((c for c in df.columns if needle in str(c)), None)


def _robot_table(data: dict, cfg: dict) -> tuple[pd.DataFrame | None, str, str]:
    """Return (per-robot DataFrame [robot, n_tasks, med_s], duration label, source label)."""
    tlc = data.get("lifecycle")
    lsr = data.get("station")

    if tlc is not None:
        robot_col = _find_col(tlc, "机器人编号")
        leg_col   = _delivery_leg_col(tlc, cfg)
        if robot_col is not None and leg_col is not None:
            df = tlc[[robot_col, leg_col]].copy()
            df.columns = ["robot", "dur_s"]
            df["dur_s"] = pd.to_numeric(df["dur_s"], errors="coerce")
            df = df.dropna(subset=["robot"])
            df["robot"] = df["robot"].astype(str)
            valid = df[(df["dur_s"] > 0) & (df["dur_s"] < 3600)]
            per = valid.groupby("robot")["dur_s"].agg(
                n_tasks="size", med_s="median",
            ).reset_index()
            if len(per) >= 3:
                return per, "median delivery leg (s)", "task lifecycle sheet"

    if lsr is not None:
        robot_col = _find_col(lsr, "机器人编号")
        evt_col   = _find_col(lsr, "事件类型")
        if robot_col is not None and evt_col is not None:
            tgo = lsr[lsr[evt_col] == "triggerGo"].dropna(subset=[robot_col])
            per = tgo.groupby(tgo[robot_col].astype(str)).size().reset_index()
            per.columns = ["robot", "n_tasks"]
            per["med_s"] = np.nan
            if len(per) >= 3:
                return per, "", "station record sheet (triggerGo counts only)"

    return None, "", ""


# ── public entry point ────────────────────────────────────────────────────────

def run(data: dict, cfg: dict) -> list[dict]:
    per, dur_label, src_label = _robot_table(data, cfg)
    if per is None:
        return []

    has_dur = dur_label != "" and per["med_s"].notna().any()
    n_robots = len(per)
    med_tasks = float(per["n_tasks"].median())

    # Slow outliers: robots with meaningful volume whose median leg exceeds
    # the p90 of per-robot medians.
    slow_ids: set[str] = set()
    if has_dur:
        busy = per[per["n_tasks"] >= 10]
        if len(busy) >= 10:
            p90_med = float(busy["med_s"].quantile(0.9))
            slow_ids = set(busy.loc[busy["med_s"] > p90_med, "robot"])

    fig = make_subplots(
        rows=1, cols=2 if has_dur else 1,
        column_widths=[0.42, 0.58] if has_dur else None,
        subplot_titles=(
            [f"Workload distribution ({n_robots} robots)",
             "Workload vs speed — each dot is one robot"]
            if has_dur else
            [f"Workload distribution ({n_robots} robots)"]
        ),
        horizontal_spacing=0.10,
    )

    # ── Left: histogram of tasks per robot ────────────────────────────────────
    fig.add_trace(go.Histogram(
        x=per["n_tasks"].values,
        marker_color="#2563eb", opacity=0.85,
        hovertemplate="Tasks: %{x}<br>Robots: %{y}<extra></extra>",
        name="Robots",
    ), row=1, col=1)
    fig.add_vline(
        x=med_tasks, line_dash="dash", line_color="#666666", line_width=1.5,
        annotation_text=f"median {med_tasks:.0f}",
        annotation_position="top right",
        annotation_font=dict(size=10, color="#666666"),
        row=1, col=1,
    )

    # ── Right: workload vs median leg time ────────────────────────────────────
    if has_dur:
        normal = per[~per["robot"].isin(slow_ids)]
        slow   = per[per["robot"].isin(slow_ids)]
        fig.add_trace(go.Scatter(
            x=normal["n_tasks"], y=normal["med_s"],
            mode="markers",
            marker=dict(size=6, color="#2563eb", opacity=0.6,
                        line=dict(width=0.5, color="white")),
            customdata=normal["robot"].values,
            name="Robot",
            hovertemplate=(
                "<b>%{customdata}</b><br>Tasks: %{x}<br>"
                "Median leg: %{y:.0f} s<extra></extra>"
            ),
            showlegend=False,
        ), row=1, col=2)
        if not slow.empty:
            fig.add_trace(go.Scatter(
                x=slow["n_tasks"], y=slow["med_s"],
                mode="markers",
                marker=dict(size=9, color=ACCENT, symbol="diamond",
                            line=dict(width=1, color="white")),
                customdata=slow["robot"].values,
                name=f"Slow outlier ({len(slow)})",
                hovertemplate=(
                    "<b>%{customdata}</b>  ⚠ slow<br>Tasks: %{x}<br>"
                    "Median leg: %{y:.0f} s<extra></extra>"
                ),
            ), row=1, col=2)

    fig.update_layout(
        title=dict(
            text="Per-Robot Performance Profile",
            x=0, pad=dict(l=12), font=dict(size=17, color=INK),
        ),
        plot_bgcolor="white", paper_bgcolor="white",
        font=dict(color=INK, family="Inter, sans-serif"),
        margin=dict(t=80, b=90, l=70, r=40),
        legend=dict(orientation="h", y=-0.18, x=0, font=dict(size=10)),
        hoverlabel=dict(bgcolor="white", bordercolor="#cccccc"),
    )
    fig.update_xaxes(row=1, col=1, title_text="Tasks completed")
    fig.update_yaxes(row=1, col=1, title_text="Number of robots",
                     showgrid=True, gridcolor="#eeeeee")
    if has_dur:
        fig.update_xaxes(row=1, col=2, title_text="Tasks completed")
        fig.update_yaxes(row=1, col=2, title_text="Median delivery leg (s)",
                         showgrid=True, gridcolor="#eeeeee", rangemode="tozero")

    _rows = [
        {
            "robot":   str(r["robot"]),
            "n_tasks": int(r["n_tasks"]),
            "median_leg_s": (
                round(float(r["med_s"]), 1) if has_dur and pd.notna(r["med_s"]) else None
            ),
            "slow_outlier": bool(r["robot"] in slow_ids),
        }
        for _, r in per.sort_values("n_tasks", ascending=False).iterrows()
    ]

    return [{
        "id":          "robot_performance_profile",
        "title":       "Per-Robot Performance Profile",
        "figure":      fig,
        "source":      src_label,
        "method":      (
            "Left: distribution of completed tasks per robot — a healthy fleet shows a "
            "tight mound around the median; a long left tail is robots doing far less work "
            "than their peers (charging problems, errors, or parked units). "
            + (
                "Right: each dot is one robot, plotting workload against its median "
                "delivery-leg duration. Diamonds mark robots with ≥ 10 tasks whose median "
                "leg exceeds the 90th percentile of per-robot medians — consistently slow "
                "units worth a maintenance check. A robot that is both low-volume and slow "
                "is the strongest maintenance signal in this dataset. Note that route mix "
                "affects leg time, so investigate before condemning a unit that happens to "
                "serve distant aisles. "
                if has_dur else
                "Delivery-leg durations were unavailable (no lifecycle sheet), so only "
                "workload counts are shown. "
            )
            + f"Fleet median: {med_tasks:.0f} tasks/robot across {n_robots} robots."
        ),
        "export_hint": "",
        "raw_data": {
            "description": "Per-robot task count and median delivery leg duration",
            "n_robots": n_robots,
            "median_tasks_per_robot": round(med_tasks, 1),
            "rows": _rows,
        },
    }]
