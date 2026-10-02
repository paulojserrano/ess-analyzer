"""
analyses/retrieval.py — where containers are fetched from in the storage grid.

Charts produced
---------------
1. Retrievals per storage aisle (bar, hot aisles highlighted).
2. Retrieval density heatmap — every storage bay (aisle × bay grid).
3. Tote-level retrieval concentration — volume bar + Pareto curve.
4. Retrievals + delivery leg time by storage level (vertical distribution).
5. Median delivery leg per source aisle (distance proxy).
6. Tote re-retrieval intervals (slotting / buffering opportunity).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from config import ACCENT, INK, SEQ_COLORSCALE
from analyses.fleet_utilization import _delivery_leg_col

_HEAT_COLORSCALE = SEQ_COLORSCALE  # darker = more retrievals


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
    # Column range spans the observed bay IDs (0- or 1-based alike) so the
    # factorised fallback path no longer drops bay 0.
    min_bay = int(s2["bay_i"].min())
    max_bay = int(s2["bay_i"].max())
    bays    = list(range(min_bay, max_bay + 1))
    grid = (
        s2.groupby(["aisle_i", "bay_i"])
        .size()
        .unstack(fill_value=0)
        .reindex(index=aisles, columns=bays, fill_value=0)
    )
    gv   = grid.values.astype(float)
    vmax = float(np.percentile(gv[gv > 0], 97)) if (gv > 0).any() else 1.0

    fig = go.Figure(go.Heatmap(
        z=gv,
        x=bays,
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
        for _bi, _b in enumerate(bays)
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


def _level_profile(src: pd.DataFrame, leg_col: str | None) -> dict | None:
    """Retrievals per storage level, with median delivery leg when available —
    the vertical dimension of the grid, which the aisle/bay charts ignore."""
    s = src.copy()
    try:
        s["_lvl"] = s["Level"].astype(int)
    except (ValueError, TypeError):
        s["_lvl"] = s["Level"].astype(str)

    counts = s.groupby("_lvl").size().sort_index()
    if len(counts) < 2:
        return None

    lvl_labels = [str(l) for l in counts.index]

    med_leg = None
    if leg_col is not None and leg_col in s.columns:
        legs = pd.to_numeric(s[leg_col], errors="coerce")
        s["_leg"] = legs.where((legs > 0) & (legs < 3600))
        by_lvl = s.groupby("_lvl")["_leg"]
        med_leg = by_lvl.median().reindex(counts.index)
        n_leg   = by_lvl.count().reindex(counts.index)
        med_leg = med_leg.where(n_leg >= 10)  # suppress noisy levels

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=lvl_labels, y=counts.values,
        marker_color="#2563eb", name="Retrievals",
        hovertemplate="Level %{x}<br>Retrievals: %{y:,}<extra></extra>",
    ))
    if med_leg is not None and med_leg.notna().any():
        fig.add_trace(go.Scatter(
            x=lvl_labels, y=med_leg.round(1).values,
            mode="lines+markers", yaxis="y2",
            line=dict(color=ACCENT, width=2), marker=dict(size=6),
            name="Median delivery leg (s)",
            hovertemplate="Level %{x}<br>Median leg: %{y:.0f} s<extra></extra>",
        ))
    fig.update_layout(
        title=dict(text="Retrieval Demand and Delivery Time by Storage Level",
                   x=0, pad=dict(l=12), font=dict(size=17, color=INK)),
        xaxis=dict(title="Storage level", type="category"),
        yaxis=dict(title="Retrievals", showgrid=True, gridcolor="#eeeeee",
                   rangemode="tozero"),
        yaxis2=dict(title="Median delivery leg (s)", overlaying="y", side="right",
                    showgrid=False, rangemode="tozero"),
        legend=dict(orientation="h", y=1.08, x=1, xanchor="right", font=dict(size=10)),
        plot_bgcolor="white", paper_bgcolor="white",
        font=dict(color=INK, family="Inter, sans-serif"),
        margin=dict(t=80, b=70, l=70, r=70),
        hoverlabel=dict(bgcolor="white", bordercolor="#cccccc"),
    )
    return {
        "id":          "retrieval_level_profile",
        "title":       "Retrieval Demand and Delivery Time by Storage Level",
        "figure":      fig,
        "source":      "Task lifecycle sheet",
        "method":      (
            "Retrievals per storage level (bars) with the median delivery-leg duration per "
            "level (line, right axis; suppressed below 10 samples). Level is the vertical "
            "dimension the aisle/bay charts ignore: if high levels show materially longer "
            "delivery legs, moving high-velocity totes to lower levels is a cheap win. "
            "If demand concentrates on slow levels, the slotting strategy is actively "
            "working against the fleet."
        ),
        "export_hint": "",
        "raw_data": {
            "description": "Retrievals and median delivery leg per storage level",
            "rows": [
                {
                    "level": lvl_labels[i],
                    "retrievals": int(counts.iloc[i]),
                    "median_leg_s": (
                        round(float(med_leg.iloc[i]), 1)
                        if med_leg is not None and pd.notna(med_leg.iloc[i]) else None
                    ),
                }
                for i in range(len(counts))
            ],
        },
    }


def _leg_by_aisle(src: pd.DataFrame, leg_col: str | None) -> dict | None:
    """Median delivery-leg duration per source aisle — tests whether storage
    distance actually drives delivery time (the actionable follow-up to the
    bay heatmap's repositioning advice)."""
    if leg_col is None or leg_col not in src.columns:
        return None

    s = src.copy()
    legs = pd.to_numeric(s[leg_col], errors="coerce")
    s["_leg"] = legs.where((legs > 0) & (legs < 3600))
    s = s.dropna(subset=["_leg"])
    if s.empty:
        return None

    g   = s.groupby("Aisle")["_leg"]
    med = g.median()
    q1  = g.quantile(0.25)
    q3  = g.quantile(0.75)
    n   = g.count()
    keep = n >= 20
    if keep.sum() < 3:
        return None
    med, q1, q3, n = med[keep], q1[keep], q3[keep], n[keep]
    med = med.sort_index()
    q1, q3, n = q1.reindex(med.index), q3.reindex(med.index), n.reindex(med.index)

    overall = float(s["_leg"].median())
    ax = med.index.astype(str).tolist()

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=ax + ax[::-1],
        y=q3.values.tolist() + q1.values[::-1].tolist(),
        fill="toself", fillcolor="rgba(37,99,235,0.10)",
        line=dict(width=0), hoverinfo="skip", showlegend=True,
        name="Interquartile range",
    ))
    fig.add_trace(go.Scatter(
        x=ax, y=med.values,
        mode="lines+markers",
        line=dict(color="#2563eb", width=2), marker=dict(size=6),
        customdata=n.values,
        name="Median delivery leg",
        hovertemplate=(
            "Aisle %{x}<br>Median leg: %{y:.0f} s<br>n = %{customdata}<extra></extra>"
        ),
    ))
    fig.add_hline(
        y=overall, line_dash="dash", line_color="#666666", line_width=1.5,
        annotation_text=f"grid median {overall:.0f} s",
        annotation_position="top right",
        annotation_font=dict(size=10, color="#666666"),
    )
    fig.update_layout(
        title=dict(text="Delivery Leg Duration by Source Aisle (distance proxy)",
                   x=0, pad=dict(l=12), font=dict(size=17, color=INK)),
        xaxis=dict(title="Source aisle", tickfont=dict(size=8)),
        yaxis=dict(title="Delivery leg (s)", showgrid=True, gridcolor="#eeeeee",
                   rangemode="tozero"),
        legend=dict(orientation="h", y=1.08, x=1, xanchor="right", font=dict(size=10)),
        plot_bgcolor="white", paper_bgcolor="white",
        font=dict(color=INK, family="Inter, sans-serif"),
        margin=dict(t=80, b=70, l=70, r=40),
        hoverlabel=dict(bgcolor="white", bordercolor="#cccccc"),
    )
    return {
        "id":          "retrieval_leg_by_aisle",
        "title":       "Delivery Leg Duration by Source Aisle",
        "figure":      fig,
        "source":      "Task lifecycle sheet",
        "method":      (
            "Median delivery-leg duration per source aisle (line) with the interquartile "
            "range (band); aisles with fewer than 20 deliveries are suppressed. "
            "This tests the assumption behind the repositioning advice on the bay heatmap: "
            "if the median rises with aisle number (or shows clear slow zones), storage "
            "distance genuinely drives delivery time and moving hot totes closer will pay "
            "off proportionally. If the line is flat, travel is not the dominant leg cost "
            "and slotting changes will disappoint — look at station queueing instead. "
            "A wide band on specific aisles indicates congestion variability rather than "
            "distance (robots sometimes crawl through them)."
        ),
        "export_hint": "",
        "raw_data": {
            "description": "Median / IQR delivery leg per source aisle (n ≥ 20)",
            "grid_median_leg_s": round(overall, 1),
            "rows": [
                {
                    "aisle": ax[i],
                    "median_leg_s": round(float(med.iloc[i]), 1),
                    "q1_s": round(float(q1.iloc[i]), 1),
                    "q3_s": round(float(q3.iloc[i]), 1),
                    "n": int(n.iloc[i]),
                }
                for i in range(len(med))
            ],
        },
    }


def _tote_reretrieval(tlc: pd.DataFrame, bin_col: str) -> dict | None:
    """Time between consecutive retrievals of the same tote — many short
    intervals argue for a near-station buffer instead of full re-storage."""
    cmp_col = next((c for c in tlc.columns if "complete(" in str(c)), None)
    if cmp_col is None:
        return None

    df = tlc[[bin_col, cmp_col]].copy()
    df.columns = ["tote", "ts"]
    df["ts"] = pd.to_datetime(df["ts"], errors="coerce")
    df = df.dropna().sort_values(["tote", "ts"])

    gaps_min = (
        df.groupby("tote")["ts"].diff().dt.total_seconds().dropna() / 60.0
    )
    gaps_min = gaps_min[gaps_min > 0]
    if len(gaps_min) < 30:
        return None

    CAP_MIN   = 480.0   # display cap: 8 h
    THRESH_MIN = 30.0   # "rapid re-retrieval" threshold
    within = float((gaps_min <= THRESH_MIN).mean() * 100)
    med    = float(gaps_min.median())
    clipped = gaps_min.clip(upper=CAP_MIN)

    fig = go.Figure()
    fig.add_trace(go.Histogram(
        x=clipped, xbins=dict(start=0, end=CAP_MIN, size=10),
        marker_color="#2563eb", opacity=0.85,
        hovertemplate="Interval: %{x:.0f} min<br>Re-retrievals: %{y:,}<extra></extra>",
        name="Re-retrievals",
    ))
    fig.add_vline(
        x=THRESH_MIN, line_color=ACCENT, line_width=2, line_dash="dash",
        annotation_text=f"{within:.0f}% within {THRESH_MIN:.0f} min",
        annotation_position="top right",
        annotation_font=dict(color=ACCENT, size=11),
    )
    fig.update_layout(
        title=dict(text="Tote Re-Retrieval Intervals", x=0, pad=dict(l=12),
                   font=dict(size=17, color=INK)),
        xaxis=dict(title=f"Minutes between consecutive retrievals of the same tote (capped at {CAP_MIN:.0f})"),
        yaxis=dict(title="Re-retrieval events", showgrid=True, gridcolor="#eeeeee"),
        plot_bgcolor="white", paper_bgcolor="white",
        font=dict(color=INK, family="Inter, sans-serif"),
        margin=dict(t=70, b=90, l=70, r=40),
        showlegend=False,
        hoverlabel=dict(bgcolor="white", bordercolor="#cccccc"),
        annotations=[dict(
            xref="paper", yref="paper", x=0, y=-0.18,
            text=(
                f"{len(gaps_min):,} re-retrieval events · median interval "
                f"<b>{med:.0f} min</b> · {(gaps_min > CAP_MIN).mean() * 100:.1f}% beyond "
                f"the {CAP_MIN/60:.0f} h display cap"
            ),
            font=dict(size=10, color="#666"), showarrow=False,
        )],
    )
    return {
        "id":          "retrieval_tote_reinterval",
        "title":       "Tote Re-Retrieval Intervals",
        "figure":      fig,
        "source":      "Task lifecycle sheet",
        "method":      (
            "For every tote retrieved more than once, the time between consecutive "
            "retrievals (task completion timestamps). Each rapid re-retrieval means a tote "
            "was carried back into storage only to be fetched again minutes later — two "
            "full robot round-trips that a near-station buffer or delayed put-away would "
            "have avoided. If a large share of re-retrievals falls inside the 30-minute "
            "line, holding recently used totes near the outbound stations would directly "
            "remove that share of storage round-trips. This is the complementary signal to "
            "the Pareto chart: the Pareto says WHICH totes are hot, this says HOW SOON they "
            "come back."
        ),
        "export_hint": "retrieval_demand_by_aisle.xlsx",
        "raw_data": {
            "description": "Intervals between consecutive retrievals of the same tote",
            "n_reretrievals": int(len(gaps_min)),
            "median_interval_min": round(med, 1),
            "pct_within_30min": round(within, 1),
            "rows": [
                {"interval_min": round(float(v), 1)} for v in gaps_min.values
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

    leg_col = _delivery_leg_col(tlc, cfg)
    for maybe in (
        _level_profile(src, leg_col),
        _leg_by_aisle(src, leg_col),
        _tote_reretrieval(tlc, bin_col) if bin_col and bin_col in tlc.columns else None,
    ):
        if maybe:
            charts.append(maybe)

    return charts
