"""
analyses/return_flow.py — outbound vs return task mix from the callback sheet.

Task IDs prefixed with 'return:' are restocking movements (containers going
back into storage).  They consume the same fleet capacity as outbound work
but are counted as ordinary events by every other chart.  This module shows
how much of the hourly workload is returns and whether returns crowd out
outbound work at peak.

Charts produced
---------------
1. return_flow_mix — hourly unique tasks stacked by kind (outbound / return)
                     with the return share as a line on the right axis.
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from config import ACCENT, INK


def _find_col(df: pd.DataFrame, needle: str) -> str | None:
    return next((c for c in df.columns if needle in str(c)), None)


# ── public entry point ────────────────────────────────────────────────────────

def run(data: dict, cfg: dict) -> list[dict]:
    cb = data.get("callback")
    if cb is None:
        return []

    task_col = _find_col(cb, "任务编号")
    ts_col   = _find_col(cb, "时间戳")
    if task_col is None or ts_col is None:
        return []

    df = cb[[task_col, ts_col]].copy()
    df.columns = ["task", "ts"]
    df["ts"] = pd.to_datetime(df["ts"], errors="coerce")
    df = df.dropna(subset=["task", "ts"])
    df["task"] = df["task"].astype(str)

    # One row per task, stamped at the task's first observed event
    first = df.groupby("task")["ts"].min().reset_index()
    first["is_return"] = first["task"].str.startswith("return")

    n_return = int(first["is_return"].sum())
    if n_return == 0:
        return []
    n_total = len(first)

    first["hour"] = first["ts"].dt.floor("h")
    pivot = (
        first.groupby(["hour", "is_return"]).size()
        .unstack(fill_value=0)
        .rename(columns={False: "outbound", True: "return"})
    )
    for c in ("outbound", "return"):
        if c not in pivot.columns:
            pivot[c] = 0
    pivot["share"] = pivot["return"] / (pivot["outbound"] + pivot["return"]) * 100.0

    hl = [h.strftime("%H:00") for h in pivot.index]
    overall_share = n_return / n_total * 100.0

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=hl, y=pivot["outbound"].values,
        marker_color="#2563eb", name="Outbound tasks",
        hovertemplate="<b>%{x}</b><br>Outbound: %{y:,}<extra></extra>",
    ))
    fig.add_trace(go.Bar(
        x=hl, y=pivot["return"].values,
        marker_color="#f59e0b", name="Return tasks",
        hovertemplate="<b>%{x}</b><br>Returns: %{y:,}<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=hl, y=pivot["share"].round(1).values,
        mode="lines+markers", yaxis="y2",
        line=dict(color=ACCENT, width=2, dash="dot"), marker=dict(size=5),
        name="Return share (%)",
        hovertemplate="<b>%{x}</b><br>Return share: %{y:.1f}%<extra></extra>",
    ))
    fig.update_layout(
        barmode="stack",
        title=dict(
            text="Outbound vs Return Task Mix by Hour",
            x=0, pad=dict(l=12), font=dict(size=17, color=INK),
        ),
        xaxis=dict(title="Hour", tickangle=-45, tickfont=dict(size=9)),
        yaxis=dict(title="Unique tasks started", showgrid=True,
                   gridcolor="#eeeeee", rangemode="tozero"),
        yaxis2=dict(title="Return share (%)", overlaying="y", side="right",
                    showgrid=False, rangemode="tozero"),
        legend=dict(orientation="h", y=1.08, x=1, xanchor="right", font=dict(size=10)),
        plot_bgcolor="white", paper_bgcolor="white",
        font=dict(color=INK, family="Inter, sans-serif"),
        margin=dict(t=80, b=90, l=70, r=70),
        hoverlabel=dict(bgcolor="white", bordercolor="#cccccc"),
    )
    fig.add_annotation(
        xref="paper", yref="paper", x=0.99, y=0.97,
        text=(
            f"Day total: <b>{n_total - n_return:,}</b> outbound · "
            f"<b>{n_return:,}</b> returns  (<b>{overall_share:.1f}%</b>)"
        ),
        font=dict(size=11, color=INK),
        bgcolor="rgba(255,255,255,0.88)",
        bordercolor="#cccccc", borderwidth=1, borderpad=6,
        showarrow=False, align="right", xanchor="right", yanchor="top",
    )

    return [{
        "id":          "return_flow_mix",
        "title":       "Outbound vs Return Task Mix by Hour",
        "figure":      fig,
        "source":      "Callback detail sheet (task IDs prefixed 'return:')",
        "method":      (
            "Unique task IDs from the callback sheet, stamped at each task's first "
            "observed event and split by kind: IDs prefixed 'return:' are restocking "
            "movements back into storage; everything else is treated as outbound work. "
            "Stacked bars show volume per hour; the dotted line (right axis) is the "
            "return share. Returns consume the same fleet capacity as outbound tasks "
            "but produce no outbound throughput — a rising return share during peak "
            "outbound hours means restocking is competing with picking for robots, and "
            "shifting returns into demand troughs is free outbound capacity. "
            "A consistently high return share may also indicate totes cycling back and "
            "forth (see the tote re-retrieval chart for the complementary signal)."
        ),
        "export_hint": "",
        "raw_data": {
            "description": "Unique tasks per hour by kind, with return share",
            "total_outbound": int(n_total - n_return),
            "total_returns": n_return,
            "overall_return_share_pct": round(overall_share, 2),
            "rows": [
                {
                    "hour": h,
                    "outbound": int(pivot["outbound"].iloc[i]),
                    "returns":  int(pivot["return"].iloc[i]),
                    "return_share_pct": round(float(pivot["share"].iloc[i]), 1),
                }
                for i, h in enumerate(hl)
            ],
        },
    }]
