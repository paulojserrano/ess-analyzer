"""
analyses/switch_time.py — robot handoff / switch time and station starvation.

Switch time = gap between one robot's 'release' and the next robot's 'arrived'
at the same station.  Gaps are split into two regimes:

  • operational switch  (0 – MAX_OPERATIONAL_SWITCH_S seconds) — a genuine
    robot swap while work is flowing;
  • starvation episodes (MAX_OPERATIONAL_SWITCH_S – 2 h) — the station sat
    empty because no robot was dispatched (demand gap, dispatch gap, break).

Gaps longer than 2 h are treated as shift boundaries and excluded entirely.

Charts produced
---------------
1. switch_heatmap     — operational switch time per station per hour
                        (Median / Average toggle).
2. switch_starvation  — starved minutes per station per hour, with episode
                        counts (hour-boundary clipped).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from config import INK, MAX_OPERATIONAL_SWITCH_S, SEQ_COLORSCALE, SWITCH_S_FALLBACK

_HEAT_COLORSCALE = SEQ_COLORSCALE  # darker = slower swaps

# 0 min starved = white, more = deeper red
_STARVE_COLORSCALE = [
    [0.0, "#ffffff"], [0.25, "#fecaca"],
    [0.6, "#ef4444"], [1.0, "#7f1d1d"],
]

_SHIFT_GAP_S = 7200.0  # gaps beyond this are shift boundaries, not starvation


def _release_arrived_gaps(lsr: pd.DataFrame, point2ws: dict) -> pd.DataFrame:
    """All release→arrived gaps per station.

    Returns a DataFrame with columns: station, rel_ts, arr_ts, gap_s.
    Shared parsing step for both charts and for other modules that need
    measured switch times (see operational_switch_by_station).
    """
    d = lsr.copy()
    d["ts"]      = pd.to_datetime(d["时间戳"], errors="coerce")
    d["station"] = d["位置编号"].map(point2ws)

    ev = d[d["事件类型"].isin(["release", "arrived"]) & d["station"].notna()]
    ev = ev.dropna(subset=["ts"]).sort_values(["station", "ts"])

    rows: list[dict] = []
    for ws, sub in ev.groupby("station"):
        open_rel = None
        for ts, et in sub[["ts", "事件类型"]].values:
            if et == "release":
                open_rel = ts
            elif et == "arrived" and open_rel is not None:
                gap = (ts - open_rel).total_seconds()
                if gap >= 0:
                    rows.append({
                        "station": ws,
                        "rel_ts":  open_rel,
                        "arr_ts":  ts,
                        "gap_s":   gap,
                    })
                open_rel = None
    return pd.DataFrame(rows, columns=["station", "rel_ts", "arr_ts", "gap_s"])


def operational_switch_by_station(lsr: pd.DataFrame, point2ws: dict) -> dict[str, float]:
    """Median *operational* switch time (s) per station.

    Only gaps ≤ MAX_OPERATIONAL_SWITCH_S count — longer gaps are starvation
    or breaks, not robot swaps.  Used by dwell_time / throughput / summary to
    replace the fixed 6 s assumption in implied-throughput formulas.
    Returns {} when the station sheet yields no usable gaps.
    """
    gaps = _release_arrived_gaps(lsr, point2ws)
    if gaps.empty:
        return {}
    op = gaps[gaps["gap_s"] <= MAX_OPERATIONAL_SWITCH_S]
    if op.empty:
        return {}
    return op.groupby("station")["gap_s"].median().to_dict()


def resolve_switch_s(cfg: dict, station: str | None = None) -> float:
    """Effective robot switch/wait time (s) for implied-throughput formulas.

    The value is driven by two user-configurable cfg keys (see build_config):

      • switch_mode == "fixed"    → the flat, user-set ``switch_s_fixed`` value
                                     (default SWITCH_S_FALLBACK) for every station.
      • switch_mode == "measured" → the per-station *measured* operational-switch
                                     median (cfg["switch_measured"][station]),
                                     falling back to ``switch_s_fixed`` where a
                                     station has no measured swaps.

    When ``station`` is None in measured mode, the fleet-level median of the
    measured values is returned (again falling back to the fixed value).
    """
    fixed = float(cfg.get("switch_s_fixed", SWITCH_S_FALLBACK))
    if cfg.get("switch_mode") != "measured":
        return fixed
    measured = cfg.get("switch_measured") or {}
    if station is not None:
        return float(measured.get(station, fixed))
    if measured:
        return float(np.median(list(measured.values())))
    return fixed


def _prep_arrays(pivot: pd.DataFrame, ws_order: list, fmt: str) -> tuple:
    """Return (data_array, text_grid) for a single pivot."""
    data = pivot.reindex(ws_order).values.astype(float)
    text = [
        [
            fmt.format(data[i, j]) if not np.isnan(data[i, j]) else ""
            for j in range(data.shape[1])
        ]
        for i in range(data.shape[0])
    ]
    return data, text


def _station_hour_heatmap_toggle(
    pivot_median: pd.DataFrame,
    pivot_mean: pd.DataFrame,
    cfg: dict,
    overall_median_s: float = 0.0,
    overall_mean_s: float = 0.0,
    n_starved: int = 0,
) -> go.Figure:
    ws_order    = cfg["ws_order"]
    hour_labels = [h.strftime("%H:00") for h in pivot_median.columns]
    fmt         = "{:.0f}s"

    med_arr, med_text = _prep_arrays(pivot_median, ws_order, fmt)
    avg_arr, avg_text = _prep_arrays(pivot_mean,   ws_order, fmt)

    # Colour range anchored to 95th percentile across both datasets
    all_valid = np.concatenate([
        med_arr[~np.isnan(med_arr)],
        avg_arr[~np.isnan(avg_arr)],
    ])
    vmin = float(np.percentile(all_valid, 5)) if all_valid.size else 0.0
    vmax = float(np.percentile(all_valid, 95)) if all_valid.size else 1.0
    if vmax <= vmin:
        vmin, vmax = 0.0, max(vmax, 1.0)

    common = dict(
        x=hour_labels, y=ws_order,
        colorscale=_HEAT_COLORSCALE,
        zmin=vmin, zmax=vmax,
        texttemplate="%{text}", textfont=dict(size=8),
        colorbar=dict(title="Switch time (s)", thickness=14, len=0.8),
    )

    fig = go.Figure()
    fig.add_trace(go.Heatmap(
        z=med_arr.tolist(),
        text=med_text,
        hovertemplate="<b>%{y}</b><br>%{x}<br>Median: %{z:.0f}s<extra></extra>",
        visible=True,
        **common,
    ))
    fig.add_trace(go.Heatmap(
        z=avg_arr.tolist(),
        text=avg_text,
        hovertemplate="<b>%{y}</b><br>%{x}<br>Average: %{z:.0f}s<extra></extra>",
        visible=False,
        **common,
    ))

    fig.update_layout(
        title=dict(
            text="Robot Switch Time at Workstations (operational swaps only)",
            x=0, pad=dict(l=12), font=dict(size=17, color=INK),
        ),
        updatemenus=[dict(
            type="buttons", direction="right",
            x=1.0, y=1.08, xanchor="right", yanchor="bottom",
            showactive=True,
            buttons=[
                dict(
                    label="Median",
                    method="restyle",
                    args=[{
                        "z":    [med_arr.tolist(), avg_arr.tolist()],
                        "text": [med_text, avg_text],
                        "visible": [True, False],
                    }, [0, 1]],
                ),
                dict(
                    label="Average",
                    method="restyle",
                    args=[{
                        "z":    [med_arr.tolist(), avg_arr.tolist()],
                        "text": [med_text, avg_text],
                        "visible": [False, True],
                    }, [0, 1]],
                ),
            ],
            bgcolor="white", bordercolor="#cccccc",
            font=dict(color=INK, size=11),
            pad=dict(r=4, t=4),
        )],
        yaxis=dict(autorange="reversed", tickfont=dict(size=10)),
        xaxis=dict(tickangle=-45, tickfont=dict(size=9), title="Hour"),
        plot_bgcolor="white", paper_bgcolor="white",
        font=dict(color=INK, family="Inter, sans-serif"),
        margin=dict(t=90, b=110, l=110, r=80),
        hoverlabel=dict(bgcolor="white", bordercolor="#cccccc"),
    )
    fig.add_annotation(
        xref="paper", yref="paper",
        x=0, y=-0.15,
        text=(
            f"Operational switches only (gaps ≤ {MAX_OPERATIONAL_SWITCH_S:.0f} s) — "
            f"Median: <b>{overall_median_s:.0f} s</b>   ·   Mean: <b>{overall_mean_s:.0f} s</b>.  "
            f"{n_starved:,} longer gap(s) are shown separately in the starvation chart."
        ),
        font=dict(size=9, color="#666666"), showarrow=False, align="left",
    )
    return fig


def _starvation_heatmap(starved: pd.DataFrame, cfg: dict, all_hours: pd.DatetimeIndex) -> dict | None:
    """Starved minutes per station per hour, hour-boundary clipped."""
    from analyses.dwell_time import _clipped_occupancy

    ws_order = cfg["ws_order"]
    if starved.empty:
        return None

    occ_s = _clipped_occupancy(starved, "rel_ts", "arr_ts", ws_order, all_hours)
    occ_m = occ_s / 60.0  # minutes

    # Episode counts by the hour the episode started (for hover/context)
    cnt = (
        starved.assign(_hour=starved["rel_ts"].dt.floor("h"))
        .groupby(["station", "_hour"]).size()
        .unstack(fill_value=0)
        .reindex(index=ws_order, columns=all_hours, fill_value=0)
    )

    hour_labels = [h.strftime("%H:00") for h in all_hours]
    z    = occ_m.values.astype(float)
    text = [
        [f"{z[i, j]:.0f}m" if z[i, j] >= 1 else "" for j in range(z.shape[1])]
        for i in range(z.shape[0])
    ]

    total_starved_min = float(np.nansum(z))
    worst_ws = occ_m.sum(axis=1).idxmax() if occ_m.sum(axis=1).max() > 0 else "—"

    fig = go.Figure(go.Heatmap(
        z=z.tolist(),
        x=hour_labels, y=ws_order,
        colorscale=_STARVE_COLORSCALE,
        zmin=0, zmax=60,
        text=text, texttemplate="%{text}", textfont=dict(size=8),
        customdata=cnt.values,
        hovertemplate=(
            "<b>%{y}</b><br>%{x}<br>"
            "Starved: %{z:.1f} min<br>"
            "Episodes started: %{customdata}<extra></extra>"
        ),
        colorbar=dict(
            title="Starved<br>(min/hr)", thickness=14, len=0.8,
            tickvals=[0, 15, 30, 45, 60],
        ),
    ))
    fig.update_layout(
        title=dict(
            text="Station Starvation — Minutes Without a Robot",
            x=0, pad=dict(l=12), font=dict(size=17, color=INK),
        ),
        yaxis=dict(autorange="reversed", tickfont=dict(size=10)),
        xaxis=dict(tickangle=-45, tickfont=dict(size=9), title="Hour"),
        plot_bgcolor="white", paper_bgcolor="white",
        font=dict(color=INK, family="Inter, sans-serif"),
        margin=dict(t=70, b=110, l=110, r=80),
        hoverlabel=dict(bgcolor="white", bordercolor="#cccccc"),
        annotations=[dict(
            xref="paper", yref="paper", x=0, y=-0.15,
            text=(
                f"A starvation episode = release→arrived gap between "
                f"{MAX_OPERATIONAL_SWITCH_S/60:.0f} min and {_SHIFT_GAP_S/3600:.0f} h "
                f"(longer gaps are treated as shift boundaries and excluded).  "
                f"Total starved: <b>{total_starved_min:,.0f} min</b> across all stations "
                f"({len(starved):,} episodes).  Most starved station: <b>{worst_ws}</b>."
            ),
            font=dict(size=9, color="#666666"), showarrow=False, align="left",
        )],
    )

    _rows = []
    for _ws in ws_order:
        for _j, _h in enumerate(all_hours):
            _v = float(occ_m.loc[_ws, _h]) if _ws in occ_m.index else 0.0
            if _v > 0:
                _rows.append({
                    "station":       _ws,
                    "hour":          _h.strftime("%H:%M"),
                    "starved_min":   round(_v, 2),
                    "episodes":      int(cnt.loc[_ws, _h]) if _ws in cnt.index else 0,
                })

    return {
        "id":          "switch_starvation",
        "title":       "Station Starvation — Minutes Without a Robot",
        "figure":      fig,
        "source":      "Station record sheet (labor_station_record)",
        "method":      (
            f"Each cell is the number of minutes in that hour during which the station "
            f"had no robot present, summed over starvation episodes (release→arrived gaps "
            f"longer than {MAX_OPERATIONAL_SWITCH_S/60:.0f} min but shorter than "
            f"{_SHIFT_GAP_S/3600:.0f} h; episodes spanning hour boundaries are split "
            f"proportionally). Unlike median switch time, this shows the total capacity "
            f"actually lost to robot supply gaps: a station starved 30 min in an hour lost "
            f"half of that hour's potential output regardless of how fast its typical swap is. "
            f"Dark red columns across many stations at once indicate a fleet-wide dispatch or "
            f"demand gap; dark rows for a single station point at its dispatch priority."
        ),
        "export_hint": "robot_switch_time_intervals.xlsx",
        "raw_data": {
            "description": "Starved minutes and episode counts per station per hour",
            "total_starved_min": round(total_starved_min, 1),
            "episode_definition_s": [MAX_OPERATIONAL_SWITCH_S, _SHIFT_GAP_S],
            "rows": _rows,
        },
    }


# ── public entry point ────────────────────────────────────────────────────────

def run(data: dict, cfg: dict) -> list[dict]:
    lsr = data.get("station")
    if lsr is None:
        return []

    gaps = _release_arrived_gaps(lsr, cfg["point2ws"])
    if gaps.empty:
        return []

    op      = gaps[gaps["gap_s"] <= MAX_OPERATIONAL_SWITCH_S]
    starved = gaps[(gaps["gap_s"] > MAX_OPERATIONAL_SWITCH_S) & (gaps["gap_s"] < _SHIFT_GAP_S)]

    charts: list[dict] = []

    # Reindex to full 24-hour range so x-axis always shows 00–23
    ref_ts    = gaps["rel_ts"].min().normalize()
    all_hours = pd.date_range(ref_ts, periods=24, freq="h")

    if not op.empty:
        d = op.assign(hour_dt=op["rel_ts"].dt.floor("h"))

        overall_median_s = float(d["gap_s"].median())
        overall_mean_s   = float(d["gap_s"].mean())

        grp          = d.groupby(["station", "hour_dt"])["gap_s"]
        pivot_median = grp.median().unstack().reindex(columns=all_hours)
        pivot_mean   = grp.mean().unstack().reindex(columns=all_hours)

        fig = _station_hour_heatmap_toggle(
            pivot_median, pivot_mean, cfg,
            overall_median_s, overall_mean_s, n_starved=len(starved),
        )

        _raw_rows = []
        for (_ws, _hr), _sub in d.groupby(["station", "hour_dt"])["gap_s"]:
            _raw_rows.append({
                "station":   _ws,
                "hour":      _hr.strftime("%H:%M") if hasattr(_hr, "strftime") else str(_hr),
                "median_s":  round(float(_sub.median()), 2),
                "mean_s":    round(float(_sub.mean()), 2),
                "count":     int(len(_sub)),
            })

        charts.append({
            "id":          "switch_heatmap",
            "title":       "Robot Switch Time at Workstations",
            "figure":      fig,
            "source":      "Station record sheet (labor_station_record)",
            "method":      (
                "Time a station sits idle between one robot leaving ('release') and the next "
                "robot arriving ('arrived'), for operational swaps only — gaps longer than "
                f"{MAX_OPERATIONAL_SWITCH_S/60:.0f} min are starvation or breaks and are shown "
                "in the separate starvation chart, so they no longer inflate these averages. "
                "Toggle between Median and Average per station per hour. "
                "High values mean robots are slow to rotate through the station even when "
                "work is flowing — a dispatch-tempo issue rather than a supply outage. "
                "Compare with the starvation chart: a station can have fast swaps yet still "
                "lose most of an hour to starvation episodes."
            ),
            "export_hint": "robot_switch_time_intervals.xlsx",
            "raw_data": {
                "description": (
                    "Operational robot switch time (release → next arrived, "
                    f"≤ {MAX_OPERATIONAL_SWITCH_S:.0f} s) per station per hour"
                ),
                "fleet_operational_median_s": round(overall_median_s, 2),
                "fleet_operational_mean_s":   round(overall_mean_s, 2),
                "starvation_episodes_excluded": int(len(starved)),
                "rows": _raw_rows,
            },
        })

    starve_chart = _starvation_heatmap(starved, cfg, all_hours)
    if starve_chart:
        charts.append(starve_chart)

    return charts
