"""
analyses/retrieval.py — where containers are fetched from in the storage grid.

Charts produced
---------------
1. Retrievals per storage aisle (bar, hot aisles highlighted).
2. Retrieval density heatmap — every storage bay (aisle × bay grid).
3. Tote-level retrieval concentration — volume bar + Pareto curve.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from config import ACCENT, INK

_HEAT_COLORSCALE = [
    [0.0, "#f0f4ff"], [0.2, "#93c5fd"],
    [0.5, "#1d4ed8"], [0.75, "#15803d"],
    [0.9, "#fbbf24"], [1.0, "#ef4444"],
]


def _parse_source(tlc: pd.DataFrame) -> tuple[pd.DataFrame | None, str | None, str | None, str | None]:
    src_col  = next((c for c in tlc.columns if "起始位置" in str(c)), None)
    bin_col  = next((c for c in tlc.columns if "容器编号" in str(c)), None)
    dest_col = next((c for c in tlc.columns if "目标位置" in str(c)), None)
    if src_col is None:
        return None, src_col, bin_col, dest_col

    src = tlc.dropna(subset=[src_col]).copy()
    src = src[src[src_col].astype(str).str.startswith("HAI")]
    base  = src[src_col].astype(str).str.split("_").str[0]
    parts = base.str.split("-", expand=True)
    if parts.shape[1] < 4:
        return None, src_col, bin_col, dest_col

    src["Aisle"] = parts[1]
    src["Bay"]   = parts[2]
    src["Level"] = parts[3]
    return src, src_col, bin_col, dest_col


def _aisle_bar(src: pd.DataFrame) -> dict:
    vc     = src["Aisle"].value_counts().sort_index()
    mean_v = vc.mean()
    colors = [
        ACCENT    if v > mean_v * 1.5
        else "#2563eb" if v > mean_v
        else "#93c5fd"
        for v in vc.values
    ]

    fig = go.Figure(go.Bar(
        x=vc.index.tolist(), y=vc.values,
        marker_color=colors,
        hovertemplate="Aisle %{x}<br>Retrievals: %{y:,}<extra></extra>",
    ))
    fig.add_hline(
        y=mean_v, line_dash="dash", line_color="#666666",
        annotation_text=f"Mean {mean_v:.0f}/aisle",
        annotation_position="top right",
        annotation_font=dict(color="#666666", size=10),
    )
    fig.update_layout(
        title=dict(text="Retrieval Demand by Storage Aisle", x=0, pad=dict(l=12), font=dict(size=17, color=INK)),
        xaxis=dict(title="Storage aisle", tickfont=dict(size=8)),
        yaxis=dict(title="Retrievals", showgrid=True, gridcolor="#eeeeee"),
        plot_bgcolor="white", paper_bgcolor="white",
        font=dict(color=INK, family="Inter, sans-serif"),
        margin=dict(t=70, b=70, l=70, r=40),
        showlegend=False,
        hoverlabel=dict(bgcolor="white", bordercolor="#cccccc"),
    )
    return {
        "id":          "retrieval_aisle_bar",
        "title":       "Retrieval Demand by Storage Aisle",
        "figure":      fig,
        "source":      "Task lifecycle sheet",
        "method":      (
            "Total retrievals per storage aisle for the day. "
            "Red bars exceed 1.5× the mean — these are hot aisles where robots "
            "concentrate, increasing congestion and travel-time variability. "
            "Heavily imbalanced demand across aisles is worth addressing through "
            "SKU repositioning: moving high-velocity items out of hot aisles reduces "
            "robot contention and travel distance across the fleet."
        ),
        "export_hint": "retrieval_demand_by_aisle.xlsx",
        "raw_data": {
            "description": "Total retrievals per storage aisle",
            "mean_retrievals_per_aisle": round(float(mean_v), 2),
            "rows": [
                {"aisle": str(aisle), "retrievals": int(count)}
                for aisle, count in zip(vc.index, vc.values)
            ],
        },
    }


def _bay_heatmap(src: pd.DataFrame) -> dict:
    s2 = src.copy()
    try:
        s2["aisle_i"] = s2["Aisle"].astype(int)
        s2["bay_i"]   = s2["Bay"].astype(int)
    except ValueError:
        s2["aisle_i"] = pd.factorize(s2["Aisle"])[0]
        s2["bay_i"]   = pd.factorize(s2["Bay"])[0]

    aisles  = sorted(s2["aisle_i"].unique())
    max_bay = int(s2["bay_i"].max())
    grid = (
        s2.groupby(["aisle_i", "bay_i"])
        .size()
        .unstack(fill_value=0)
        .reindex(index=aisles, columns=range(1, max_bay + 1), fill_value=0)
    )
    gv   = grid.values.astype(float)
    vmax = float(np.percentile(gv[gv > 0], 97)) if (gv > 0).any() else 1.0

    fig = go.Figure(go.Heatmap(
        z=gv,
        x=list(range(1, max_bay + 1)),
        y=[str(a) for a in aisles],
        colorscale=_HEAT_COLORSCALE,
        zmin=0, zmax=vmax,
        hovertemplate="Aisle %{y}  Bay %{x}<br>Retrievals: %{z}<extra></extra>",
        colorbar=dict(title="Retrievals<br>(p97 cap)", thickness=14, len=0.8),
    ))
    fig.update_layout(
        title=dict(text="Retrieval Demand Heatmap — Every Storage Bay", x=0, pad=dict(l=12), font=dict(size=17, color=INK)),
        xaxis=dict(title="Bay"),
        yaxis=dict(title="Aisle", autorange="reversed"),
        plot_bgcolor="white", paper_bgcolor="white",
        font=dict(color=INK, family="Inter, sans-serif"),
        margin=dict(t=70, b=70, l=70, r=80),
        hoverlabel=dict(bgcolor="white", bordercolor="#cccccc"),
    )
    # Build flat rows from the grid for raw data
    _bay_rows = [
        {"aisle": str(aisles[_ai]), "bay": int(_b), "retrievals": int(gv[_ai, _bi])}
        for _ai in range(len(aisles))
        for _bi, _b in enumerate(range(1, max_bay + 1))
        if gv[_ai, _bi] > 0
    ]

    return {
        "id":          "retrieval_bay_heatmap",
        "title":       "Retrieval Demand Heatmap — Every Storage Bay",
        "figure":      fig,
        "source":      "Task lifecycle sheet",
        "method":      (
            "Retrieval density across the full storage grid (aisles × bays). "
            "Dark clusters are high-velocity locations — robots visit them repeatedly "
            "throughout the shift. Light areas are rarely accessed. "
            "Dense clusters near one end of the grid suggest that storage assignment "
            "is not optimised for distance to outbound stations. "
            "Repositioning the items in dark clusters to locations closer to outbound "
            "stations (or spreading them across aisles) directly reduces robot travel time."
        ),
        "export_hint": "retrieval_demand_by_bay.xlsx",
        "raw_data": {
            "description": "Retrieval count per storage aisle × bay (zero-count cells omitted)",
            "rows": _bay_rows,
        },
    }


def _tote_pareto(tlc: pd.DataFrame, bin_col: str) -> dict:
    vc_t = tlc[bin_col].dropna().value_counts()
    maxf = 20
    freq = vc_t.value_counts().sort_index()
    xs   = list(range(1, maxf + 1))
    vols = [int(freq.get(n, 0)) * n for n in xs]
    over = int(sum(freq.get(k, 0) * k for k in freq.index if k > maxf))

    sv   = np.sort(vc_t.values)[::-1]
    cum  = np.cumsum(sv) / sv.sum() * 100
    xpct = np.arange(1, len(sv) + 1) / len(sv) * 100

    fig = make_subplots(
        rows=1, cols=2,
        subplot_titles=[
            "Where retrieval volume comes from",
            "Retrieval concentration (Pareto)",
        ],
        column_widths=[0.5, 0.5],
    )

    # Volume bar
    bar_x  = xs + [maxf + 2]
    bar_y  = vols + [over]
    b_cols = ["#2563eb"] * len(xs) + [ACCENT]
    fig.add_trace(go.Bar(
        x=bar_x, y=bar_y, marker_color=b_cols,
        hovertemplate="Retrieved %{x}×<br>Contributed: %{y:,}<extra></extra>",
        name="Volume",
    ), row=1, col=1)

    # Pareto curve
    fig.add_trace(go.Scatter(
        x=xpct, y=cum, mode="lines",
        line=dict(color="#7c3aed", width=2.5),
        fill="tozeroy", fillcolor="rgba(124,58,237,0.08)",
        name="Cumulative %",
        hovertemplate="Top %{x:.1f}% of totes<br>= %{y:.1f}% of retrievals<extra></extra>",
    ), row=1, col=2)
    fig.add_trace(go.Scatter(
        x=[0, 100], y=[0, 100], mode="lines",
        line=dict(color="#bbbbbb", dash="dot", width=1),
        showlegend=False,
    ), row=1, col=2)

    for pct_mark, col in [(5, ACCENT), (20, "#f59e0b")]:
        yv = float(cum[max(0, int(len(sv) * pct_mark / 100) - 1)])
        fig.add_trace(go.Scatter(
            x=[pct_mark], y=[yv], mode="markers+text",
            marker=dict(size=9, color=col),
            text=[f"Top {pct_mark}% → {yv:.0f}%"],
            textposition="top right",
            textfont=dict(size=10, color=col),
            showlegend=False,
        ), row=1, col=2)

    fig.update_layout(
        title=dict(text="Tote-Level Retrieval Demand", x=0, pad=dict(l=12), font=dict(size=17, color=INK)),
        plot_bgcolor="white", paper_bgcolor="white",
        font=dict(color=INK, family="Inter, sans-serif"),
        margin=dict(t=80, b=70, l=70, r=40),
        showlegend=False,
        hoverlabel=dict(bgcolor="white", bordercolor="#cccccc"),
    )
    fig.update_xaxes(row=1, col=1, title_text="Times a tote was retrieved")
    fig.update_yaxes(row=1, col=1, title_text="Retrievals contributed", showgrid=True, gridcolor="#eeeeee")
    fig.update_xaxes(row=1, col=2, title_text="% of totes (most requested first)", range=[0, 100])
    fig.update_yaxes(row=1, col=2, title_text="% of retrievals", range=[0, 100], showgrid=True, gridcolor="#eeeeee")

    return {
        "id":          "retrieval_tote_pareto",
        "title":       "Tote-Level Retrieval Concentration (Pareto)",
        "figure":      fig,
        "source":      "Task lifecycle sheet",
        "method":      (
            "Left: how much of the day's retrieval volume each frequency cohort contributes "
            "(e.g. totes retrieved exactly 3 times vs exactly 10 times). "
            "Right: Pareto curve — the x-axis is the share of totes (ranked most-to-least "
            "requested), the y-axis is the cumulative share of total retrievals. "
            "The closer the curve hugs the top-left corner, the more concentrated demand is. "
            "If the top 5% of totes account for >50% of retrievals, relocating those totes "
            "to positions nearest to outbound stations would have an outsized impact on "
            "average travel distance and robot utilisation."
        ),
        "export_hint": "retrieval_demand_by_aisle.xlsx",
        "raw_data": {
            "description": "Per-tote retrieval count (all totes, ranked most-to-least requested)",
            "total_totes": int(len(vc_t)),
            "total_retrievals": int(vc_t.sum()),
            "rows": [
                {"tote_id": str(tote), "retrievals": int(count)}
                for tote, count in vc_t.items()
            ],
        },
    }


# ── public entry point ────────────────────────────────────────────────────────

def run(data: dict, cfg: dict) -> list[dict]:
    tlc = data.get("lifecycle")
    if tlc is None:
        return []

    tlc = tlc.copy()
    src, src_col, bin_col, dest_col = _parse_source(tlc)
    if src is None:
        return []

    charts: list[dict] = [
        _aisle_bar(src),
        _bay_heatmap(src),
    ]
    if bin_col and bin_col in tlc.columns:
        charts.append(_tote_pareto(tlc, bin_col))

    return charts
