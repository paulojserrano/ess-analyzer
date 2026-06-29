"""
analyses/fleet_utilization.py — station queue depth and global fleet utilisation.

Charts produced
---------------
1. Station queue depth vs delivery leg performance
     — scatter of concurrent deliveries per station vs leg duration,
       median step-curve, and segmented box plots by queue depth.

2. Real-time fleet utilisation profile
     — 5-minute concurrent active-robot counts for the delivery AMR fleet
       and the shuttle fleet, expressed as % of total known fleet per type.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from config import ACCENT, AUTO_TYPE_PALETTE, INK


# ── layout helper (mirrors other modules) ─────────────────────────────────────

def _base_layout(fig: go.Figure, title: str) -> None:
    fig.update_layout(
        title=dict(text=title, x=0, pad=dict(l=12), font=dict(size=17, color=INK)),
        plot_bgcolor="white", paper_bgcolor="white",
        font=dict(color=INK, family="Inter, sans-serif"),
        margin=dict(t=70, b=70, l=70, r=40),
        hoverlabel=dict(bgcolor="white", bordercolor="#cccccc"),
    )


# ── shared helper ─────────────────────────────────────────────────────────────

def _delivery_leg_col(tlc: pd.DataFrame, cfg: dict) -> str | None:
    """Return the delivery-leg duration column, falling back to last
    stage column detected in cfg, or None if neither exists."""
    amr_type = cfg.get("amr_type") or "K50"
    col = next((c for c in tlc.columns if f"{amr_type}完成耗时" in str(c)), None)
    if col:
        return col
    # Secondary fallback: any *完成耗时 column
    col = next((c for c in tlc.columns if "完成耗时" in str(c)), None)
    if col:
        return col
    stages = cfg.get("stages", [])
    return stages[-1] if stages else None


# ── 1. Queue depth vs delivery leg performance ────────────────────────────────

def _queue_depth_leg_map(tlc: pd.DataFrame, cfg: dict) -> dict | None:
    """
    Left: box plots showing how many robots are concurrently assigned to each
    outbound station (distribution across the shift).
    Right: median delivery leg duration bucketed by concurrent robot count —
    shows how a fuller pipeline lengthens each individual delivery.
    """
    leg_col  = _delivery_leg_col(tlc, cfg)
    dest_col = next((c for c in tlc.columns if "目标位置" in str(c)), None)

    if leg_col is None or dest_col is None:
        return None

    df = tlc.copy()
    df["complete_ts"] = pd.to_datetime(df["complete(任务完成时间)"], errors="coerce")
    df = df.dropna(subset=[leg_col, dest_col, "complete_ts"])
    df = df[(df[leg_col] > 0) & (df[leg_col] < 3600)]
    df = df[df[dest_col].astype(str).str.startswith("LABOR")]

    if df.empty:
        return None

    df["station"] = df[dest_col].astype(str)
    df["leg_s"]   = df[leg_col].astype(float)
    df["d_start"] = df["complete_ts"] - pd.to_timedelta(df["leg_s"], unit="s")
    df = df.reset_index(drop=True)

    # ── concurrent robots per station (vectorised O(n²), loop fallback) ───────
    depths = np.zeros(len(df), dtype=np.int32)
    for _stn, grp in df.groupby("station"):
        idx = grp.index.values
        s   = grp["d_start"].astype("int64").values
        e   = grp["complete_ts"].astype("int64").values
        n   = len(idx)
        if n == 0:
            continue
        if n <= 5_000:
            overlap = (s[np.newaxis, :] <= e[:, np.newaxis]) & \
                      (e[np.newaxis, :] >= s[:, np.newaxis])
            np.fill_diagonal(overlap, False)
            counts = overlap.sum(axis=1).astype(np.int32)
        else:
            counts = np.array([
                int(np.sum((s <= e[i]) & (e >= s[i]))) - 1
                for i in range(n)
            ], dtype=np.int32)
        for i, orig_i in enumerate(idx):
            depths[orig_i] = counts[i] + 1  # +1 to include the robot itself

    df["queue_depth"] = depths

    ordered_stations = [s for s in cfg.get("ws_order", []) if s in df["station"].values]
    if not ordered_stations:
        ordered_stations = sorted(df["station"].unique())

    def _short(s: str) -> str:
        return s.replace("LABOR-", "L")

    # ── 5 quantile bins for the right chart ──────────────────────────────────
    qd = df["queue_depth"]
    try:
        _, edges = pd.qcut(qd, q=5, retbins=True, duplicates="drop")
    except ValueError:
        _, edges = pd.qcut(qd, q=3, retbins=True, duplicates="drop")

    edges = np.unique(np.round(edges).astype(int))
    edges[0]   = max(1, edges[0])          # floor at 1 — every delivery has ≥ 1 robot
    if len(edges) < 2:                     # constant depth (e.g. all robots work solo)
        edges = np.array([1, max(2, int(qd.max()) + 1)])
    cut_bins   = [0] + edges[1:].tolist()  # 0 so pd.cut captures depth=1 with include_lowest

    bin_labels: list[str] = []
    for i in range(len(edges) - 1):
        lo, hi = int(edges[i]), int(edges[i + 1])
        if i == 0:
            bin_labels.append(f"<{hi}")
        elif i == len(edges) - 2:
            bin_labels.append(f"{lo}+")
        else:
            bin_labels.append(f"{lo}–{hi}")

    df["depth_bin"] = pd.cut(
        qd, bins=cut_bins, labels=bin_labels, include_lowest=True,
    )

    bin_med = (
        df.groupby("depth_bin", observed=True)["leg_s"]
        .median()
        .reindex(bin_labels)
        .dropna()
    )

    bar_palette = ["#fca97a", "#f47741", "#e05228", "#c42828", "#8b1a1a"]
    bar_colors  = [bar_palette[min(i, len(bar_palette) - 1)]
                   for i in range(len(bin_med))]

    r        = float(df[["queue_depth", "leg_s"]].corr().iloc[0, 1])
    stn_meds = df.groupby("station")["queue_depth"].median()
    med_lo   = int(stn_meds.min())
    med_hi   = int(stn_meds.max())
    med_avg  = (med_lo + med_hi) // 2

    fig = make_subplots(
        rows=1, cols=2,
        column_widths=[0.48, 0.52],
        subplot_titles=[
            f"~{med_lo}–{med_hi} robots assigned per outbound station (median)",
            f"Delivery time grows as the pipeline fills (r={r:.2f})",
        ],
        horizontal_spacing=0.12,
    )

    # Left: box per station
    for stn in ordered_stations:
        sub = df[df["station"] == stn]["queue_depth"]
        fig.add_trace(go.Box(
            y=sub.values,
            name=_short(stn),
            marker_color="#93bbdf",
            line_color="#5a9fd4",
            showlegend=False,
            hovertemplate=f"<b>{_short(stn)}</b><br>Robots: %{{y}}<extra></extra>",
        ), row=1, col=1)

    # Right: bar per bucket
    fig.add_trace(go.Bar(
        x=bin_med.index.tolist(),
        y=bin_med.values,
        marker_color=bar_colors,
        showlegend=False,
        text=[f"{int(v)}s" for v in bin_med.values],
        textposition="outside",
        textfont=dict(size=12, color=INK),
        cliponaxis=False,
        hovertemplate="Robots: %{x}<br>Median delivery: %{y:.0f}s<extra></extra>",
    ), row=1, col=2)

    # Annotation: explanation in accent colour on right chart
    fig.add_annotation(
        xref="x2 domain", yref="y2 domain",
        x=0.38, y=0.78,
        text="More robots converging on a station →<br>each one waits longer to deliver",
        font=dict(size=10, color=ACCENT),
        showarrow=False,
        align="right",
        bgcolor="rgba(255,255,255,0.7)",
    )

    # Footer
    fig.add_annotation(
        xref="paper", yref="paper",
        x=0, y=-0.16,
        text=(
            f"{len(df):,} deliveries across {len(ordered_stations)} station(s).  "
            f"Pearson r (concurrent robot count vs delivery time) = {r:.3f}."
        ),
        font=dict(size=10, color="#666"),
        showarrow=False,
    )

    fig.update_layout(
        title=dict(
            text=(
                "Robots assigned per station vs delivery time<br>"
                f"<sup>Each outbound station has ~{med_avg} robots working toward it at once.  "
                "The deeper that pipeline, the slower each delivery.</sup>"
            ),
            x=0, pad=dict(l=12),
            font=dict(size=17, color=INK),
        ),
        plot_bgcolor="white", paper_bgcolor="white",
        font=dict(color=INK, family="Inter, sans-serif"),
        margin=dict(t=110, b=90, l=70, r=70),
        hoverlabel=dict(bgcolor="white", bordercolor="#cccccc"),
    )
    fig.update_xaxes(row=1, col=1, title_text="Outbound station",
                     tickfont=dict(size=11))
    fig.update_yaxes(row=1, col=1, title_text="Robots concurrently assigned to station",
                     showgrid=True, gridcolor="#eeeeee", rangemode="tozero")
    fig.update_xaxes(row=1, col=2,
                     title_text="# robots assigned to station when this container was deposited",
                     tickfont=dict(size=10))
    fig.update_yaxes(row=1, col=2, title_text="Median delivery time (s)",
                     showgrid=True, gridcolor="#eeeeee", rangemode="tozero")

    return {
        "id":          "queue_depth_leg_map",
        "title":       "Robots assigned per station vs delivery time",
        "figure":      fig,
        "source":      "Task lifecycle sheet",
        "method":      (
            "Left: distribution of how many robots are simultaneously in transit to each "
            "outbound station. A wide box with a high median means that station consistently "
            "has a deep pipeline of robots queuing to deliver. "
            "Right: median delivery time grouped by the number of concurrent robots assigned "
            "to the same station. A rising step-curve confirms the queueing effect: each "
            "additional robot in the pipeline lengthens every individual delivery because "
            "robots must wait for the station to be free. "
            "The Pearson r in the subtitle quantifies the strength of this relationship. "
            "The primary lever here is robot dispatch policy: smoothing the inflow rate "
            "per station reduces peak queue depth and shortens average delivery time."
        ),
        "export_hint": "fleet_delivery_leg.xlsx",
        "raw_data": {
            "description": "Per-delivery: destination station, delivery leg duration, concurrent robot count",
            "pearson_r_queue_vs_leg": round(float(r), 4),
            "rows": [
                {
                    "station": str(row["station"]),
                    "leg_s": round(float(row["leg_s"]), 2),
                    "queue_depth": int(row["queue_depth"]),
                }
                for _, row in df[["station", "leg_s", "queue_depth"]].iterrows()
            ],
        },
    }


# ── 2. Fleet utilisation time-series ─────────────────────────────────────────

def _fleet_utilization_timeseries(lsr: pd.DataFrame, cfg: dict, tlc: pd.DataFrame | None = None) -> dict | None:
    """
    For every 5-minute bin, count how many robots are actively executing a task.
    A robot is 'on task' for the full duration of its assignment — travel to
    storage, retrieval, travel to station, and station dwell all count.
    Only genuine idle time (waiting for the next assignment) counts as off-task.

    Interval source priority:
      1. Lifecycle sheet — AMR: [complete_ts − K50_duration, complete_ts];
         shuttle: A42 stage columns ending where the AMR leg begins.
      2. Station sheet fallback (arrived→release only) when lifecycle is absent.
    """
    robot_id_col   = next((c for c in lsr.columns if "机器人编号" in str(c)), None)
    robot_type_col = next((c for c in lsr.columns if "机器人类型" in str(c)), None)
    event_col      = next((c for c in lsr.columns if "事件类型" in str(c)), None)

    if not robot_id_col or not robot_type_col:
        return None

    lsr = lsr.copy()
    lsr["ts"] = pd.to_datetime(lsr["时间戳"], errors="coerce")
    lsr = lsr.dropna(subset=["ts", robot_id_col, robot_type_col])
    if lsr.empty:
        return None

    amr_type = cfg.get("amr_type")
    if amr_type:
        amr_df     = lsr[lsr[robot_type_col] == amr_type]
        shuttle_df = lsr[lsr[robot_type_col] != amr_type]
    else:
        amr_df     = lsr
        shuttle_df = pd.DataFrame(columns=lsr.columns)

    total_amr     = amr_df[robot_id_col].nunique()
    total_shuttle = shuttle_df[robot_id_col].nunique()

    if total_amr == 0 and total_shuttle == 0:
        return None

    # ── Build task intervals ──────────────────────────────────────────────────
    # Default: arrived→release pairs at station (station dwell only).
    # Overridden below with lifecycle-derived intervals when available.

    def _station_intervals(df: pd.DataFrame) -> pd.DataFrame:
        """arrived→release pairs per robot from the station sheet (fallback)."""
        if df.empty:
            return pd.DataFrame(columns=["start", "end"])
        if event_col is None:
            ivs = df.groupby(robot_id_col)["ts"].agg(["min", "max"])
            return ivs.rename(columns={"min": "start", "max": "end"}).reset_index(drop=True)
        df_ev = df[df[event_col].isin(["arrived", "release"])].copy()
        df_ev = df_ev.sort_values([robot_id_col, "ts"])
        records: list[tuple] = []
        for _, grp in df_ev.groupby(robot_id_col, sort=False):
            start_ts = None
            for evt, ts in grp[[event_col, "ts"]].values:
                if evt == "arrived" and start_ts is None:
                    start_ts = ts
                elif evt == "release" and start_ts is not None:
                    records.append((start_ts, ts))
                    start_ts = None
        if not records:
            ivs = df.groupby(robot_id_col)["ts"].agg(["min", "max"])
            return ivs.rename(columns={"min": "start", "max": "end"}).reset_index(drop=True)
        return pd.DataFrame(records, columns=["start", "end"])

    interval_source = "station"
    amr_ivs     = _station_intervals(amr_df)
    shuttle_ivs = _station_intervals(shuttle_df)

    if tlc is not None:
        _tlc     = tlc.copy()
        cmp_col  = next((c for c in _tlc.columns if "complete(" in str(c)), None)
        leg_col  = _delivery_leg_col(_tlc, cfg)
        a42_cols = [c for c in _tlc.columns if "A42" in str(c) and "耗时" in str(c)]

        if cmp_col and leg_col:
            _tlc["_cmp"] = pd.to_datetime(_tlc[cmp_col], errors="coerce")
            _tlc = _tlc.dropna(subset=["_cmp", leg_col])
            _tlc = _tlc[(_tlc[leg_col] > 0) & (_tlc[leg_col] < 7200)]

            if not _tlc.empty:
                leg_td = pd.to_timedelta(_tlc[leg_col].astype(float), unit="s")

                # AMR: active for the entire delivery leg (travel + dwell)
                amr_ivs = pd.DataFrame({
                    "start": _tlc["_cmp"] - leg_td,
                    "end":   _tlc["_cmp"],
                }).dropna().reset_index(drop=True)

                # Shuttle: active during A42 retrieval stages, ending when AMR starts
                if a42_cols:
                    shuttle_dur = _tlc[a42_cols].fillna(0).clip(lower=0).sum(axis=1)
                    shuttle_end = _tlc["_cmp"] - leg_td
                    shuttle_ivs = pd.DataFrame({
                        "start": shuttle_end - pd.to_timedelta(shuttle_dur, unit="s"),
                        "end":   shuttle_end,
                    }).dropna()
                    shuttle_ivs = shuttle_ivs[
                        (shuttle_ivs["end"] - shuttle_ivs["start"]).dt.total_seconds() > 0
                    ].reset_index(drop=True)
                # else: shuttle_ivs stays as station-based (set above)

                interval_source = "lifecycle"

    # 5-minute bins across the full log span
    ts_min = lsr["ts"].min().floor("5min")
    ts_max = lsr["ts"].max().ceil("5min")
    bins   = pd.date_range(ts_min, ts_max, freq="5min")
    if len(bins) < 3:
        return None

    bin_mids = (bins[:-1] + pd.Timedelta("2min30s")).values.astype("int64")

    def _concurrent(ivs: pd.DataFrame, mids: np.ndarray) -> np.ndarray:
        """Count task intervals whose [start, end] spans each bin midpoint."""
        if ivs.empty:
            return np.zeros(len(mids), dtype=np.int32)
        s = ivs["start"].values.astype("int64")
        e = ivs["end"].values.astype("int64")
        return np.array(
            [int(np.sum((s <= t) & (e >= t))) for t in mids],
            dtype=np.int32,
        )

    amr_counts     = _concurrent(amr_ivs,     bin_mids)
    shuttle_counts = _concurrent(shuttle_ivs, bin_mids)

    # Lifecycle counts represent concurrent tasks (one robot per task).
    # Cap at fleet size to absorb any timestamp imprecision in the source data.
    if interval_source == "lifecycle":
        amr_counts     = np.minimum(amr_counts,     total_amr)
        shuttle_counts = np.minimum(shuttle_counts, total_shuttle)

    amr_pct     = amr_counts     / total_amr     * 100 if total_amr     > 0 else np.zeros_like(amr_counts,     float)
    shuttle_pct = shuttle_counts / total_shuttle * 100 if total_shuttle > 0 else np.zeros_like(shuttle_counts, float)

    # X-axis labels — show only every-hour tick to avoid crowding
    hl       = [t.strftime("%H:%M") for t in (bins[:-1] + pd.Timedelta("2min30s"))]
    tick_idx = list(range(0, len(hl), 12))  # every 12 × 5 min = 1 hour

    fig = go.Figure()

    amr_label = f"Delivery AMR ({amr_type})" if amr_type else "AMR fleet"
    fig.add_trace(go.Scatter(
        x=hl, y=np.round(amr_pct, 1),
        mode="lines", fill="tozeroy",
        fillcolor="rgba(37,99,235,0.12)",
        line=dict(color="#2563eb", width=2.5),
        name=f"{amr_label}  [{total_amr} robots]",
        customdata=np.stack([amr_counts,
                             np.full(len(hl), total_amr)], axis=1),
        hovertemplate=(
            "<b>%{x}</b><br>"
            "On task: %{customdata[0]} / %{customdata[1]} robots<br>"
            "Utilisation: %{y:.1f}%"
            "<extra>" + amr_label + "</extra>"
        ),
    ))

    if total_shuttle > 0:
        shuttle_types = shuttle_df[robot_type_col].dropna().unique()
        shuttle_label = (
            str(shuttle_types[0]) if len(shuttle_types) == 1 else "Shuttle fleet"
        )
        fig.add_trace(go.Scatter(
            x=hl, y=np.round(shuttle_pct, 1),
            mode="lines", fill="tozeroy",
            fillcolor="rgba(124,58,237,0.10)",
            line=dict(color="#7c3aed", width=2.5),
            name=f"{shuttle_label}  [{total_shuttle} robots]",
            customdata=np.stack([shuttle_counts,
                                  np.full(len(hl), total_shuttle)], axis=1),
            hovertemplate=(
                "<b>%{x}</b><br>"
                "On task: %{customdata[0]} / %{customdata[1]} robots<br>"
                "Utilisation: %{y:.1f}%"
                "<extra>" + shuttle_label + "</extra>"
            ),
        ))
    else:
        shuttle_label = None

    amr_active   = amr_pct[amr_pct > 0]
    amr_mean     = float(amr_active.mean())   if amr_active.size   else 0.0
    amr_median   = float(np.median(amr_active)) if amr_active.size else 0.0

    if total_shuttle > 0:
        shuttle_active = shuttle_pct[shuttle_pct > 0]
        shuttle_mean   = float(shuttle_active.mean())     if shuttle_active.size else 0.0
        shuttle_median = float(np.median(shuttle_active)) if shuttle_active.size else 0.0
    else:
        shuttle_mean = shuttle_median = 0.0

    peak_amr     = float(amr_pct.max())
    peak_shuttle = float(shuttle_pct.max()) if total_shuttle > 0 else 0.0

    fig.add_hline(y=amr_mean,   line_dash="dash", line_color="#2563eb",
                  line_width=1.0, opacity=0.45)
    fig.add_hline(y=amr_median, line_dash="dot",  line_color="#2563eb",
                  line_width=1.0, opacity=0.45)
    if total_shuttle > 0:
        fig.add_hline(y=shuttle_mean,   line_dash="dash", line_color="#7c3aed",
                      line_width=1.0, opacity=0.45)
        fig.add_hline(y=shuttle_median, line_dash="dot",  line_color="#7c3aed",
                      line_width=1.0, opacity=0.45)

    _base_layout(fig, "Real-Time Fleet Utilisation Profile (5-min resolution)")
    fig.update_layout(
        xaxis=dict(
            title="Time of day",
            tickangle=-45,
            tickvals=[hl[i] for i in tick_idx],
            ticktext=[hl[i] for i in tick_idx],
        ),
        yaxis=dict(
            title="Fleet utilisation (%)",
            range=[0, 108],
            showgrid=True, gridcolor="#eeeeee",
        ),
        legend=dict(orientation="h", y=1.08, x=1, xanchor="right"),
        margin=dict(t=80, b=110, l=70, r=40),
        annotations=[dict(
            xref="paper", yref="paper", x=0, y=-0.22,
            text=(
                f"Fleet: {total_amr} delivery AMR ({amr_label})"
                + (f", {total_shuttle} shuttle ({shuttle_label})" if shuttle_label else "")
                + f".  Peak utilisation — AMR: {peak_amr:.1f}%"
                + (f", shuttle: {peak_shuttle:.1f}%" if total_shuttle > 0 else "")
                + (
                    "  On-task intervals from lifecycle sheet: full engagement "
                    "(retrieval travel + station dwell)."
                    if interval_source == "lifecycle" else
                    "  On-task intervals: station dwell only (lifecycle sheet unavailable; "
                    "travel time excluded — actual utilisation is higher)."
                )
                + f"<br>AMR — Mean: <b>{amr_mean:.1f}%</b>  ·  Median: <b>{amr_median:.1f}%</b>"
                + (
                    f"   |   Shuttle — Mean: <b>{shuttle_mean:.1f}%</b>  ·  Median: <b>{shuttle_median:.1f}%</b>"
                    if total_shuttle > 0 else ""
                )
                + "   (─── Mean  ·····  Median, reference lines above)"
            ),
            font=dict(size=10, color="#666"), showarrow=False,
        )],
    )
    fig.add_annotation(
        xref="paper", yref="paper",
        x=0.99, y=0.97,
        text=(
            f"AMR  avg <b>{amr_mean:.1f}%</b> · med <b>{amr_median:.1f}%</b>"
            + (
                f"<br>Shuttle  avg <b>{shuttle_mean:.1f}%</b> · med <b>{shuttle_median:.1f}%</b>"
                if total_shuttle > 0 else ""
            )
        ),
        font=dict(size=11, color=INK),
        bgcolor="rgba(255,255,255,0.88)",
        bordercolor="#cccccc", borderwidth=1, borderpad=6,
        showarrow=False, align="right",
        xanchor="right", yanchor="top",
    )

    # Build time-series raw data
    _ts_rows: list[dict] = []
    for _i, _t in enumerate(hl):
        _row_f: dict = {
            "time": _t,
            "amr_on_task": int(amr_counts[_i]),
            "amr_total": total_amr,
            "amr_utilisation_pct": round(float(amr_pct[_i]), 1),
        }
        if total_shuttle > 0:
            _row_f["shuttle_on_task"]       = int(shuttle_counts[_i])
            _row_f["shuttle_total"]         = total_shuttle
            _row_f["shuttle_utilisation_pct"] = round(float(shuttle_pct[_i]), 1)
        _ts_rows.append(_row_f)

    return {
        "id":          "fleet_utilization",
        "title":       "Real-Time Fleet Utilisation Profile",
        "figure":      fig,
        "source":      (
            "Task lifecycle sheet (primary) + station record sheet (fleet counts)"
            if interval_source == "lifecycle" else
            "Station record sheet (lifecycle sheet unavailable)"
        ),
        "method":      (
            "Percentage of each fleet actively on task in every 5-minute window. "
            + (
                "A robot is counted as 'on task' for the full duration of its assignment: "
                "AMR (delivery robot) intervals span [complete_ts − delivery_leg_duration, complete_ts], "
                "capturing travel to the station and station dwell. "
                "Shuttle intervals span the A42 retrieval stages, ending when the AMR leg begins. "
                "Fleet sizes (the denominator) come from unique robot IDs in the station record sheet. "
                if interval_source == "lifecycle" else
                "Lifecycle sheet was unavailable; intervals are station dwell only "
                "(arrived→release at LABOR stations) — actual utilisation is higher than shown. "
            )
            + "A robot is only considered off-task when it has no active assignment. "
            "Sustained utilisation above ~80% leaves little buffer for demand spikes — "
            "any surge will immediately create a queue. "
            "Troughs near 0% indicate idle periods; check whether these coincide with low "
            "demand or with operational pauses (breaks, shift changes). "
            "Comparing the two fleet lines reveals which robot type is the binding constraint: "
            "if the delivery AMR is at 90% while shuttles are at 40%, adding delivery robots "
            "is more impactful than adding shuttles."
        ),
        "export_hint": "fleet_utilization_profile.xlsx",
        "raw_data": {
            "description": "Fleet utilisation per 5-minute bin — on-task robot count and % of fleet",
            "interval_source": interval_source,
            "amr_type": amr_type or "AMR",
            "amr_fleet_size": total_amr,
            "shuttle_fleet_size": total_shuttle if total_shuttle > 0 else None,
            "rows": _ts_rows,
        },
    }


# ── public entry point ────────────────────────────────────────────────────────

def run(data: dict, cfg: dict) -> list[dict]:
    tlc = data.get("lifecycle")
    lsr = data.get("station")
    charts: list[dict] = []

    if tlc is not None:
        result = _queue_depth_leg_map(tlc.copy(), cfg)
        if result:
            charts.append(result)

    if lsr is not None:
        result = _fleet_utilization_timeseries(lsr.copy(), cfg, tlc=tlc.copy() if tlc is not None else None)
        if result:
            charts.append(result)

    return charts
