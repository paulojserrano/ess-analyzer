"""
analyses/switch_time.py — robot handoff / switch time at workstations.

Switch time = gap between one robot's 'release' and the next robot's 'arrived'
at the same station.  It represents how long a station sits empty between tasks.

Charts produced
---------------
1. Switch time per station per hour (heatmap) — toggles Median / Average.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from config import INK

_HEAT_COLORSCALE = [
    [0.0, "#f0f4ff"], [0.2, "#93c5fd"],
    [0.5, "#1d4ed8"], [0.75, "#15803d"],
    [0.9, "#fbbf24"], [1.0, "#ef4444"],
]


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
    vmax = float(np.percentile(all_valid, 95)) if all_valid.size else 1.0

    common = dict(
        x=hour_labels, y=ws_order,
        colorscale=_HEAT_COLORSCALE,
        zmin=0, zmax=vmax,
        texttemplate="%{text}", textfont=dict(size=8),
        colorbar=dict(title="Switch time (s)", thickness=14, len=0.8),
    )

    fig = go.Figure()
    fig.add_trace(go.Heatmap(
        z=np.clip(med_arr, 0, vmax).tolist(),
        text=med_text,
        hovertemplate="<b>%{y}</b><br>%{x}<br>Median: %{z:.0f}s<extra></extra>",
        visible=True,
        **common,
    ))
    fig.add_trace(go.Heatmap(
        z=np.clip(avg_arr, 0, vmax).tolist(),
        text=avg_text,
        hovertemplate="<b>%{y}</b><br>%{x}<br>Average: %{z:.0f}s<extra></extra>",
        visible=False,
        **common,
    ))

    fig.update_layout(
        title=dict(
            text="Robot Switch Time at Workstations",
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
                        "z":    [np.clip(med_arr, 0, vmax).tolist(), np.clip(avg_arr, 0, vmax).tolist()],
                        "text": [med_text, avg_text],
                        "visible": [True, False],
                    }, [0, 1]],
                ),
                dict(
                    label="Average",
                    method="restyle",
                    args=[{
                        "z":    [np.clip(med_arr, 0, vmax).tolist(), np.clip(avg_arr, 0, vmax).tolist()],
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
            f"Fleet switch time (all stations, all hours) — "
            f"Median: <b>{overall_median_s:.0f} s</b>   ·   Mean: <b>{overall_mean_s:.0f} s</b>"
        ),
        font=dict(size=9, color="#666666"), showarrow=False, align="left",
    )
    return fig


# ── public entry point ────────────────────────────────────────────────────────

def run(data: dict, cfg: dict) -> list[dict]:
    lsr = data.get("station")
    if lsr is None:
        return []

    lsr = lsr.copy()
    lsr["ts"]      = pd.to_datetime(lsr["时间戳"])
    lsr["station"] = lsr["位置编号"].map(cfg["point2ws"])

    ev = lsr[lsr["事件类型"].isin(["release", "arrived"]) & lsr["station"].notna()]
    ev = ev.sort_values(["station", "ts"])

    rows = []
    for ws, sub in ev.groupby("station"):
        open_rel: tuple | None = None
        for ts, et, rb in sub[["ts", "事件类型", "机器人编号"]].values:
            if et == "release":
                open_rel = (ts, rb)
            elif et == "arrived" and open_rel is not None:
                rows.append({
                    "station":   ws,
                    "hour_dt":   open_rel[0].floor("h"),
                    "switch_s":  (ts - open_rel[0]).total_seconds(),
                })
                open_rel = None

    if not rows:
        return []

    d = pd.DataFrame(rows)
    d = d[(d["switch_s"] >= 0) & (d["switch_s"] < 7200)]

    overall_median_s = float(d["switch_s"].median())
    overall_mean_s   = float(d["switch_s"].mean())

    grp          = d.groupby(["station", "hour_dt"])["switch_s"]
    pivot_median = grp.median().unstack()
    pivot_mean   = grp.mean().unstack()

    # Reindex columns to full 24-hour range so x-axis always shows 00–23
    if not pivot_median.empty:
        day = pivot_median.columns.min().normalize()
        all_hours = pd.date_range(day, periods=24, freq="h")
        pivot_median = pivot_median.reindex(columns=all_hours)
        pivot_mean   = pivot_mean.reindex(columns=all_hours)

    fig = _station_hour_heatmap_toggle(pivot_median, pivot_mean, cfg, overall_median_s, overall_mean_s)

    # Build raw data: one row per (station, hour) with median/mean switch_s and count
    _raw_rows = []
    for (_ws, _hr), _sub in d.groupby(["station", "hour_dt"])["switch_s"]:
        _raw_rows.append({
            "station":   _ws,
            "hour":      _hr.strftime("%H:%M") if hasattr(_hr, "strftime") else str(_hr),
            "median_s":  round(float(_sub.median()), 2),
            "mean_s":    round(float(_sub.mean()), 2),
            "count":     int(len(_sub)),
        })

    return [{
        "id":          "switch_heatmap",
        "title":       "Robot Switch Time at Workstations",
        "figure":      fig,
        "source":      "Station record sheet (labor_station_record)",
        "method":      (
            "Time a station sits idle between one robot leaving ('release') and the next "
            "robot arriving ('arrived'). Toggle between Median and Average per station per hour. "
            "High switch time means the station is starved of robots — the operator is ready "
            "but has nothing to work on, which directly caps throughput. "
            "Compare stations side-by-side: a station with high switch time and low throughput "
            "is a scheduling gap, not an operator problem. "
            "Switch time typically rises during low-demand hours and at shift changeovers."
        ),
        "export_hint": "robot_switch_time_intervals.xlsx",
        "raw_data": {
            "description": "Robot switch time (release → next arrived) per station per hour",
            "fleet_overall_median_s": round(overall_median_s, 2),
            "fleet_overall_mean_s":   round(overall_mean_s, 2),
            "rows": _raw_rows,
        },
    }]
