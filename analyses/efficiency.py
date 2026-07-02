"""
analyses/efficiency.py — HPS3 bottleneck attribution from the efficiency sheet.

The HPS3效率对比 sheet contains the system's own hourly self-diagnosis:
upstream task creation, A42 / K50 movement counts, the target outbound rate
(TPH), and a declared bottleneck label per hour.  No other module consumes
this sheet — it directly answers "which subsystem limited us each hour",
which the regression-based charts can only infer.

Charts produced
---------------
1. efficiency_target_vs_actual — hourly flows vs target TPH, with the
                                 declared bottleneck shading each hour.
2. efficiency_bottleneck_share — how many hours each bottleneck was binding,
                                 and the average shortfall vs target under it.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from config import ACCENT, INK

# Stable colours per bottleneck category (unknown labels cycle through extras)
_BOTTLENECK_COLORS = {
    "Upstream":   "#f59e0b",
    "A42":        "#7c3aed",
    "K50":        "#2563eb",
    "Labor":      "#16a34a",
    "Downstream": "#0891b2",
}
_EXTRA_COLORS = ["#dc2626", "#10b981", "#ec4899", "#6b7280"]


def _find_col(df: pd.DataFrame, needle: str) -> str | None:
    return next((c for c in df.columns if needle in str(c)), None)


def _bottleneck_color_map(labels: list[str]) -> dict[str, str]:
    cmap: dict[str, str] = {}
    extra = 0
    for lbl in labels:
        if lbl in _BOTTLENECK_COLORS:
            cmap[lbl] = _BOTTLENECK_COLORS[lbl]
        else:
            cmap[lbl] = _EXTRA_COLORS[extra % len(_EXTRA_COLORS)]
            extra += 1
    return cmap


# ── public entry point ────────────────────────────────────────────────────────

def run(data: dict, cfg: dict) -> list[dict]:
    eff = data.get("efficiency")
    if eff is None or eff.empty:
        return []

    hour_col   = _find_col(eff, "自然小时")
    target_col = _find_col(eff, "目标出库")
    bn_col     = _find_col(eff, "瓶颈")
    up_col     = _find_col(eff, "上游创建")
    a42_col    = _find_col(eff, "A42下移")
    k50_col    = _find_col(eff, "K50平移")

    if hour_col is None:
        return []

    df = eff.copy()
    df["_hour"] = pd.to_datetime(df[hour_col], errors="coerce")
    df = df.dropna(subset=["_hour"]).sort_values("_hour").reset_index(drop=True)
    if df.empty:
        return []

    for c in (target_col, up_col, a42_col, k50_col):
        if c is not None:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    hl = [h.strftime("%H:00") for h in df["_hour"]]

    charts: list[dict] = []

    # ── Chart 1: hourly flows vs target, bottleneck shading ───────────────────
    fig = go.Figure()

    bn_labels: list[str] = []
    cmap: dict[str, str] = {}
    if bn_col is not None:
        bn_series = df[bn_col].fillna("(none)").astype(str)
        bn_labels = [l for l in bn_series.unique() if l != "(none)"]
        cmap = _bottleneck_color_map(bn_labels)
        # One translucent vertical band per hour, coloured by declared bottleneck
        for i, lbl in enumerate(bn_series):
            if lbl == "(none)":
                continue
            fig.add_vrect(
                x0=i - 0.5, x1=i + 0.5,
                fillcolor=cmap[lbl], opacity=0.10, line_width=0,
            )
        # Legend proxies for the shading
        for lbl in bn_labels:
            fig.add_trace(go.Scatter(
                x=[None], y=[None], mode="markers",
                marker=dict(size=10, color=cmap[lbl], symbol="square", opacity=0.45),
                name=f"Bottleneck: {lbl}",
            ))

    if up_col is not None:
        fig.add_trace(go.Bar(
            x=hl, y=df[up_col].values, marker_color="#cbd5e1",
            name="Upstream tasks created",
            hovertemplate="<b>%{x}</b><br>Upstream created: %{y:,}<extra></extra>",
        ))
    if a42_col is not None:
        fig.add_trace(go.Scatter(
            x=hl, y=df[a42_col].values, mode="lines+markers",
            line=dict(color="#7c3aed", width=2), marker=dict(size=5),
            name="A42 retrieval moves",
            hovertemplate="<b>%{x}</b><br>A42 moves: %{y:,}<extra></extra>",
        ))
    if k50_col is not None:
        fig.add_trace(go.Scatter(
            x=hl, y=df[k50_col].values, mode="lines+markers",
            line=dict(color="#2563eb", width=2), marker=dict(size=5),
            name="K50 delivery moves",
            hovertemplate="<b>%{x}</b><br>K50 moves: %{y:,}<extra></extra>",
        ))
    if target_col is not None:
        fig.add_trace(go.Scatter(
            x=hl, y=df[target_col].values, mode="lines",
            line=dict(color=ACCENT, width=2, dash="dash"),
            name="System target (TPH)",
            hovertemplate="<b>%{x}</b><br>Target: %{y:,} TPH<extra></extra>",
        ))

    fig.update_layout(
        title=dict(
            text="System Flows vs Target — Declared Bottleneck per Hour",
            x=0, pad=dict(l=12), font=dict(size=17, color=INK),
        ),
        xaxis=dict(title="Hour", tickangle=-45, tickfont=dict(size=9)),
        yaxis=dict(title="Tasks / moves per hour", showgrid=True,
                   gridcolor="#eeeeee", rangemode="tozero"),
        legend=dict(orientation="h", y=-0.25, x=0, font=dict(size=10)),
        plot_bgcolor="white", paper_bgcolor="white",
        font=dict(color=INK, family="Inter, sans-serif"),
        margin=dict(t=70, b=130, l=70, r=40),
        hoverlabel=dict(bgcolor="white", bordercolor="#cccccc"),
        barmode="overlay",
    )

    _rows1 = []
    for i in range(len(df)):
        _r: dict = {"hour": hl[i]}
        if up_col:     _r["upstream_created"] = None if pd.isna(df[up_col].iloc[i])     else int(df[up_col].iloc[i])
        if a42_col:    _r["a42_moves"]        = None if pd.isna(df[a42_col].iloc[i])    else int(df[a42_col].iloc[i])
        if k50_col:    _r["k50_moves"]        = None if pd.isna(df[k50_col].iloc[i])    else int(df[k50_col].iloc[i])
        if target_col: _r["target_tph"]       = None if pd.isna(df[target_col].iloc[i]) else int(df[target_col].iloc[i])
        if bn_col:     _r["bottleneck"]       = str(df[bn_col].iloc[i]) if pd.notna(df[bn_col].iloc[i]) else None
        _rows1.append(_r)

    charts.append({
        "id":          "efficiency_target_vs_actual",
        "title":       "System Flows vs Target — Declared Bottleneck per Hour",
        "figure":      fig,
        "source":      "HPS3 efficiency comparison sheet (HPS3效率对比)",
        "method":      (
            "Hourly system flows from the HPS3 self-diagnosis sheet: upstream task "
            "creation (bars), A42 retrieval moves and K50 delivery moves (lines), and the "
            "system's own target outbound rate (dashed line). The translucent background "
            "band shows which subsystem the system itself declared as the binding "
            "bottleneck for that hour. Unlike the regression-based charts, this is a direct "
            "statement of the constraint — use it to confirm or challenge what the pick-time "
            "and starvation charts suggest. Hours where flows sit well below target with an "
            "'Upstream' label mean the warehouse was under-fed, not under-powered; hours "
            "labelled with a robot subsystem while operators show idle time (see starvation "
            "chart) identify genuine fleet constraints."
        ),
        "export_hint": "",
        "raw_data": {
            "description": "Hourly flows, target TPH, and declared bottleneck",
            "rows": _rows1,
        },
    })

    # ── Chart 2: bottleneck share + shortfall ─────────────────────────────────
    if bn_col is not None and bn_labels:
        bn_series = df[bn_col].fillna("(none)").astype(str)
        counts = bn_series[bn_series != "(none)"].value_counts()

        # Average shortfall vs target under each bottleneck: use K50 moves as
        # the realised outbound proxy when both columns exist.
        shortfall: dict[str, float | None] = {}
        if target_col is not None and k50_col is not None:
            valid = df[[bn_col, target_col, k50_col]].dropna()
            for lbl in counts.index:
                sub = valid[valid[bn_col].astype(str) == lbl]
                if len(sub):
                    shortfall[lbl] = float(
                        (sub[target_col] - sub[k50_col]).mean()
                    )
                else:
                    shortfall[lbl] = None

        cmap2 = _bottleneck_color_map(list(counts.index))
        hover = [
            (
                f"<b>{lbl}</b><br>Binding for {int(counts[lbl])} hour(s)"
                + (
                    f"<br>Avg shortfall vs target: {shortfall[lbl]:,.0f} tasks/hr"
                    if shortfall.get(lbl) is not None else ""
                )
                + "<extra></extra>"
            )
            for lbl in counts.index
        ]
        fig2 = go.Figure(go.Bar(
            x=counts.index.tolist(),
            y=counts.values,
            marker_color=[cmap2[l] for l in counts.index],
            text=[str(int(v)) for v in counts.values],
            textposition="outside",
            cliponaxis=False,
            hovertemplate=hover,
        ))
        fig2.update_layout(
            title=dict(
                text="Which Subsystem Was the Bottleneck, and for How Long?",
                x=0, pad=dict(l=12), font=dict(size=17, color=INK),
            ),
            xaxis=dict(title="Declared bottleneck"),
            yaxis=dict(title="Hours binding", showgrid=True, gridcolor="#eeeeee"),
            plot_bgcolor="white", paper_bgcolor="white",
            font=dict(color=INK, family="Inter, sans-serif"),
            margin=dict(t=70, b=70, l=70, r=40),
            showlegend=False,
            hoverlabel=dict(bgcolor="white", bordercolor="#cccccc"),
        )
        charts.append({
            "id":          "efficiency_bottleneck_share",
            "title":       "Bottleneck Share of the Day",
            "figure":      fig2,
            "source":      "HPS3 efficiency comparison sheet (HPS3效率对比)",
            "method":      (
                "Number of hours each subsystem was declared the binding bottleneck by the "
                "HPS3 self-diagnosis. Hover shows the average shortfall of realised K50 "
                "delivery moves versus the target rate while that bottleneck was active — "
                "a bottleneck that binds for many hours with a large shortfall is the "
                "highest-leverage investment target. 'Upstream' hours cannot be fixed with "
                "more robots or faster operators: they indicate the order stream itself "
                "was the constraint."
            ),
            "export_hint": "",
            "raw_data": {
                "description": "Hours binding and mean shortfall per declared bottleneck",
                "rows": [
                    {
                        "bottleneck": str(lbl),
                        "hours_binding": int(counts[lbl]),
                        "avg_shortfall_vs_target": (
                            round(shortfall[lbl], 1)
                            if shortfall.get(lbl) is not None else None
                        ),
                    }
                    for lbl in counts.index
                ],
            },
        })

    return charts
