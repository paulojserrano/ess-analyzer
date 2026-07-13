"""
analyses/dwell_time.py — operator pick time at workstations.

Pick time = time a robot sits at a LABOR station while the operator works,
measured from 'arrived' to 'triggerGo' events in the station-record sheet.
A pick is only counted when the triggerGo fires at the same station the robot
arrived at (mismatches indicate lost events and are dropped).

When an export is missing 'arrived' events, the pick-start event can be set to
'ppReady' (cfg["pick_start_event"], surfaced as a GUI confirmation) so pick time
is measured from 'ppReady' → 'triggerGo' instead — see extract_picks().

Duration statistics (median / average) attribute each pick's *full* duration
to its arrival hour — durations are never split into hour segments, because a
median of partial segments is not a median of pick times.  Occupancy-style
metrics (used by throughput.py via _clipped_occupancy) do split seconds at
hour boundaries, which is correct for time-in-use sums.

Charts produced
---------------
1. dwell_heatmap            — pick time per station per hour; toggles
                              Median / Average / Implied Throughput
                              (3 600 ÷ (avg pick + measured per-station switch)).
2. dwell_pick_distribution  — smoothed pick-time density per workstation
                              (all stations overlaid).
3. dwell_pick_distribution_by_station
                            — same density with a workstation selector
                              (dropdown: 'All' or a single station).

Zero-second picks (start event and triggerGo sharing a timestamp) are treated as
non-events and excluded from every statistic — see extract_picks().
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from config import (
    AUTO_TYPE_PALETTE,
    INK,
    MAX_OPERATIONAL_SWITCH_S,
    PICK_START_EVENT_DEFAULT,
    PICK_START_EVENTS,
)
from analyses.switch_time import resolve_switch_s

# Low value = good (fast picks)
_HEAT_COLORSCALE = [
    [0.0, "#f0f4ff"], [0.2, "#93c5fd"],
    [0.5, "#1d4ed8"], [0.75, "#15803d"],
    [0.9, "#fbbf24"], [1.0, "#ef4444"],
]

# High value = good (high throughput) — reversed colour direction
_THROUGHPUT_COLORSCALE = [
    [0.0, "#ef4444"], [0.1, "#fbbf24"],
    [0.25, "#15803d"], [0.5, "#1d4ed8"],
    [0.8, "#93c5fd"], [1.0, "#f0f4ff"],
]

# Distinct per-station palette for the pick-time distribution charts — cycled by
# each station's position in ws_order, independent of zone type, so stations
# sharing a zone still get visually distinct lines by default.
_STATION_DISTRIBUTION_PALETTE = [
    "#2563eb", "#dc2626", "#16a34a", "#d97706", "#7c3aed", "#0891b2",
    "#db2777", "#65a30d", "#ea580c", "#0d9488", "#4f46e5", "#ca8a04",
    "#be123c", "#059669", "#9333ea", "#0284c7",
]


def _station_color_map(ws_order: list[str]) -> dict[str, str]:
    """One distinct colour per station, cycled by position in ws_order."""
    n = len(_STATION_DISTRIBUTION_PALETTE)
    return {ws: _STATION_DISTRIBUTION_PALETTE[i % n] for i, ws in enumerate(ws_order)}


def resolve_pick_start_event(cfg: dict) -> str:
    """Return the configured pick-start event ('arrived' or 'ppReady').

    Falls back to the default ('arrived') for any unset or unrecognised value,
    so callers never have to guard the cfg key themselves.
    """
    ev = cfg.get("pick_start_event", PICK_START_EVENT_DEFAULT)
    return ev if ev in PICK_START_EVENTS else PICK_START_EVENT_DEFAULT


def extract_picks(lsr: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Pair a start event → triggerGo per robot into pick records.

    The start event is cfg["pick_start_event"]:
      • "arrived" (default) — the robot's 'arrived' event; the station comes
        from the arrival location and a station-match guard drops pairs whose
        triggerGo fired elsewhere (lost events in between).
      • "ppReady"           — the put-point-ready event, used as a fallback when
        the export has no 'arrived' events.  ppReady carries a blank location,
        so the station is taken from the triggerGo instead.  The most recent
        ppReady before each triggerGo is paired.

    Returns a DataFrame with columns: robot, station, hour_dt (start hour),
    pick_s, arr_ts (start timestamp), tg_ts.  The arr_ts/tg_ts column names are
    kept stable across both modes so downstream occupancy code is unaffected.
    Shared by throughput.py, summary.py and data_quality.py so all modules
    count identically.
    """
    start_event = resolve_pick_start_event(cfg)

    d = lsr.copy()
    d["ts"]      = pd.to_datetime(d["时间戳"], errors="coerce")
    d["station"] = d["位置编号"].map(cfg["point2ws"])

    # Only the delivery AMR does operator picks — filter out shuttles so their
    # shorter dwell events don't lower the average and inflate the implied ceiling.
    amr_type = cfg.get("amr_type")
    if amr_type and "机器人类型" in d.columns:
        d = d[d["机器人类型"] == amr_type]

    ev   = d.dropna(subset=["ts"]).sort_values(["机器人编号", "ts"])
    rows: list[dict] = []
    for _rb, sub in ev.groupby("机器人编号"):
        start_ts = start_loc = None
        for ts, et, loc in sub[["ts", "事件类型", "station"]].values:
            if et == start_event:
                # Latest start event before the next triggerGo wins (a robot may
                # log several ppReady in a row while it settles at the station).
                start_ts, start_loc = ts, loc
            elif et == "triggerGo" and start_ts is not None:
                # The station: from the start event when it carries one
                # ('arrived'), otherwise from the triggerGo itself ('ppReady'
                # locations are blank).
                station = start_loc if pd.notna(start_loc) else loc
                # Station-match guard (arrived mode only): if the triggerGo
                # carries a station that differs from the arrival station, events
                # were lost in between — the duration would be misattributed.
                if (start_event == "arrived"
                        and pd.notna(loc) and pd.notna(start_loc) and loc != start_loc):
                    start_ts = None
                    continue
                rows.append({
                    "robot":    str(_rb),
                    "station":  station,
                    "hour_dt":  start_ts.floor("h"),
                    "pick_s":   (ts - start_ts).total_seconds(),
                    "arr_ts":   start_ts,
                    "tg_ts":    ts,
                })
                start_ts = None

    if not rows:
        return pd.DataFrame(columns=["robot", "station", "hour_dt", "pick_s", "arr_ts", "tg_ts"])

    out = pd.DataFrame(rows).dropna(subset=["station"])
    # Ignore zero-second picks: a start→triggerGo gap of 0 s means both events
    # carry the same timestamp (nothing was actually waited on) and would drag
    # the median/average down.  Only strictly positive durations are real waits.
    return out[(out["pick_s"] > 0) & (out["pick_s"] < 3600)]


def _prep_heatmap_arrays(
    pivot: pd.DataFrame,
    ws_order: list,
    fmt: str,
) -> tuple:
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
    pivot_med_s:   pd.DataFrame,
    pivot_avg_s:   pd.DataFrame,
    pivot_avg_tph: pd.DataFrame,
    cfg: dict,
) -> go.Figure:
    """
    Three traces + one row of three buttons:
      Trace 0 — Pick Time (Median)       [default]
      Trace 1 — Pick Time (Average)
      Trace 2 — Implied Throughput (Average)
    """
    ws_order    = cfg["ws_order"]
    hour_labels = [h.strftime("%H:00") for h in pivot_med_s.columns]

    med_s_arr,   med_s_text   = _prep_heatmap_arrays(pivot_med_s,   ws_order, "{:.0f}s")
    avg_s_arr,   avg_s_text   = _prep_heatmap_arrays(pivot_avg_s,   ws_order, "{:.1f}s")
    avg_tph_arr, avg_tph_text = _prep_heatmap_arrays(pivot_avg_tph, ws_order, "{:.0f}/hr")

    # Colour ranges — pick time anchored to median, throughput to average
    valid_s = med_s_arr[~np.isnan(med_s_arr)]
    vmax_s  = float(np.percentile(valid_s, 95)) if valid_s.size else 1.0

    valid_tph = avg_tph_arr[~np.isnan(avg_tph_arr)]
    vmin_tph  = float(np.percentile(valid_tph,  5)) if valid_tph.size else 0.0
    vmax_tph  = float(np.percentile(valid_tph, 95)) if valid_tph.size else 1.0

    fig = go.Figure()

    # ── Trace 0 : Pick Time — Median (default) ───────────────────────────────
    fig.add_trace(go.Heatmap(
        z=np.clip(med_s_arr, 0, vmax_s).tolist(),
        text=med_s_text,
        x=hour_labels, y=ws_order,
        colorscale=_HEAT_COLORSCALE,
        zmin=0, zmax=vmax_s,
        texttemplate="%{text}", textfont=dict(size=8),
        hovertemplate="<b>%{y}</b><br>%{x}<br>Pick time (median): %{z:.0f} s<extra></extra>",
        colorbar=dict(title="Pick time (s)", thickness=14, len=0.8),
        visible=True,
    ))

    # ── Trace 1 : Pick Time — Average ────────────────────────────────────────
    fig.add_trace(go.Heatmap(
        z=np.clip(avg_s_arr, 0, vmax_s).tolist(),
        text=avg_s_text,
        x=hour_labels, y=ws_order,
        colorscale=_HEAT_COLORSCALE,
        zmin=0, zmax=vmax_s,
        texttemplate="%{text}", textfont=dict(size=8),
        hovertemplate="<b>%{y}</b><br>%{x}<br>Pick time (avg): %{z:.1f} s<extra></extra>",
        colorbar=dict(title="Pick time (s)", thickness=14, len=0.8),
        visible=False,
    ))

    # ── Trace 2 : Implied Throughput — Average ────────────────────────────────
    fig.add_trace(go.Heatmap(
        z=np.clip(avg_tph_arr, vmin_tph, vmax_tph).tolist(),
        text=avg_tph_text,
        x=hour_labels, y=ws_order,
        colorscale=_THROUGHPUT_COLORSCALE,
        zmin=vmin_tph, zmax=vmax_tph,
        texttemplate="%{text}", textfont=dict(size=8),
        hovertemplate="<b>%{y}</b><br>%{x}<br>Implied throughput (avg): %{z:.0f} tasks/hr<extra></extra>",
        colorbar=dict(title="Throughput<br>(tasks/hr)", thickness=14, len=0.8),
        visible=False,
    ))

    _PICK_TITLE = "Operator Pick Time at Workstations"
    _TPH_TITLE  = "Implied Station Throughput — Pick Time + Measured Switch"

    fig.update_layout(
        title=dict(text=_PICK_TITLE, x=0, pad=dict(l=12), font=dict(size=17, color=INK)),
        updatemenus=[dict(
            type="buttons", direction="right",
            x=1.0, y=1.10, xanchor="right", yanchor="bottom",
            showactive=True,
            buttons=[
                dict(
                    label="Pick Time (Median)",
                    method="update",
                    args=[{"visible": [True, False, False]}, {"title.text": _PICK_TITLE}],
                ),
                dict(
                    label="Pick Time (Average)",
                    method="update",
                    args=[{"visible": [False, True, False]}, {"title.text": _PICK_TITLE}],
                ),
                dict(
                    label="Implied Throughput (Average)",
                    method="update",
                    args=[{"visible": [False, False, True]}, {"title.text": _TPH_TITLE}],
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
        margin=dict(t=110, b=90, l=110, r=80),
        hoverlabel=dict(bgcolor="white", bordercolor="#cccccc"),
    )
    return fig


def _clipped_occupancy(events, start_col, end_col, ws_order, all_hours):
    """
    Seconds occupied per station × hour, clipping intervals at hour
    boundaries so that a 45-min pick starting at 11:55 contributes 300 s
    to the 11:00 bucket and 2 400 s to the 12:00 bucket.

    Guaranteed ≤ 3 600 per cell when the station serves one robot at a time.
    """
    hour_td = pd.Timedelta(hours=1)
    occ = pd.DataFrame(0.0, index=ws_order, columns=all_hours)

    if events.empty:
        return occ

    ev = events[[start_col, end_col, "station"]].dropna().copy()
    ev["_dur"]  = (ev[end_col] - ev[start_col]).dt.total_seconds()
    ev["_hour"] = ev[start_col].dt.floor("h")
    ev["_rem"]  = (ev["_hour"] + hour_td - ev[start_col]).dt.total_seconds()

    # Fast path — events that fit entirely inside their start hour (vast majority)
    within = ev[ev["_dur"] <= ev["_rem"]]
    if not within.empty:
        occ = occ.add(
            within.groupby(["station", "_hour"])["_dur"]
            .sum().unstack(fill_value=0)
            .reindex(index=ws_order, columns=all_hours, fill_value=0),
            fill_value=0,
        )

    # Slow path — events that span an hour boundary (typically < 1 %)
    for _, r in ev[ev["_dur"] > ev["_rem"]].iterrows():
        ws, s, e = r["station"], r[start_col], r[end_col]
        if ws not in occ.index:
            continue
        h = s.floor("h")
        while h < e:
            if h in occ.columns:
                occ.at[ws, h] += (min(e, h + hour_td) - max(s, h)).total_seconds()
            h += hour_td

    return occ.clip(upper=3600.0)


def _pick_time_distribution(d: pd.DataFrame, cfg: dict) -> go.Figure:
    """
    Smoothed density line per workstation.
    Uses 2 s histogram bins convolved with a Gaussian kernel — no scipy required.
    """
    ws_order   = cfg["ws_order"]
    station_color = _station_color_map(ws_order)

    # X-axis: 0 → p99 of all pick times, capped at 180 s for readability
    all_vals = d["pick_s"].values
    x_max    = min(float(np.percentile(all_vals, 99)), 180.0)
    BIN_S    = 2.0
    bins     = np.arange(0, x_max + BIN_S, BIN_S)
    centers  = (bins[:-1] + bins[1:]) / 2

    def _smooth(arr: np.ndarray, sigma: float = 2.5) -> np.ndarray:
        size   = max(int(sigma * 4) * 2 + 1, 3)
        x      = np.arange(size) - size // 2
        kernel = np.exp(-0.5 * (x / sigma) ** 2)
        kernel /= kernel.sum()
        return np.clip(np.convolve(arr, kernel, mode="same"), 0, None)

    station_stats: list[tuple[str, float, float, str]] = []  # (ws, mean_s, med_s, color)
    y_max_density = 0.0

    fig = go.Figure()

    for ws in ws_order:
        vals = d.loc[d["station"] == ws, "pick_s"]
        vals = vals[(vals >= 0) & (vals <= x_max)]
        if len(vals) < 10:
            continue
        raw_counts, _ = np.histogram(vals, bins=bins, density=False)
        density, _    = np.histogram(vals, bins=bins, density=True)
        smoothed      = _smooth(density)
        y_max_density = max(y_max_density, float(smoothed.max()))
        mean_s = float(vals.mean())
        med_s  = float(vals.median())
        color  = station_color[ws]
        station_stats.append((ws, mean_s, med_s, color))
        fig.add_trace(go.Scatter(
            x=centers,
            y=smoothed,
            mode="lines",
            name=ws,
            line=dict(width=2, color=color),
            customdata=raw_counts,
            hovertemplate=(
                f"<b>{ws}</b><br>"
                "Pick time: %{x:.0f} s<br>"
                "Density: %{y:.5f}<br>"
                "Picks in bucket: %{customdata}<extra></extra>"
            ),
        ))

    if y_max_density > 0 and station_stats:
        y_rug = -y_max_density * 0.08
        for ws, mean_s, med_s, color in station_stats:
            fig.add_trace(go.Scatter(
                x=[mean_s], y=[y_rug],
                mode="markers",
                marker=dict(symbol="triangle-up", size=9, color=color,
                            line=dict(width=1, color="white")),
                showlegend=False,
                hovertemplate=f"<b>{ws}</b>  Mean: {mean_s:.0f} s<extra></extra>",
            ))
            fig.add_trace(go.Scatter(
                x=[med_s], y=[y_rug],
                mode="markers",
                marker=dict(symbol="diamond-tall", size=8, color=color,
                            line=dict(width=1, color="white")),
                showlegend=False,
                hovertemplate=f"<b>{ws}</b>  Median: {med_s:.0f} s<extra></extra>",
            ))
        y_range = [y_rug * 1.5, y_max_density * 1.08]
    else:
        y_range = None

    fig.update_layout(
        title=dict(
            text="Pick Time Distribution by Workstation",
            x=0, pad=dict(l=12), font=dict(size=17, color=INK),
        ),
        xaxis=dict(
            title="Pick time (seconds)", range=[0, x_max],
            tickfont=dict(size=10), showgrid=True, gridcolor="#f0f0f0",
        ),
        yaxis=dict(
            title="Density",
            range=y_range,
            tickfont=dict(size=10), showgrid=True, gridcolor="#f0f0f0",
        ),
        legend=dict(orientation="v", x=1.02, y=1, font=dict(size=10)),
        plot_bgcolor="white", paper_bgcolor="white",
        font=dict(color=INK, family="Inter, sans-serif"),
        margin=dict(t=80, b=90, l=80, r=160),
        hoverlabel=dict(bgcolor="white", bordercolor="#cccccc"),
        annotations=[dict(
            xref="paper", yref="paper", x=0, y=-0.14,
            text=(
                "Smoothed empirical density — 2 s bins with Gaussian kernel.  "
                f"X-axis bounded at the 99th percentile (≤ 180 s).  "
                "Narrow tall peaks = consistent pick time; wide flat curves = high variance.  "
                "▲ = Mean   ◆ = Median (per workstation, at baseline)."
            ),
            font=dict(size=9, color="#666666"), showarrow=False, align="left",
        )],
    )
    return fig


def _pick_time_distribution_by_station(d: pd.DataFrame, cfg: dict) -> go.Figure | None:
    """
    Same smoothed pick-time density as _pick_time_distribution, but with a
    workstation selector: an 'All' entry (every station overlaid) plus one entry
    per workstation that isolates a single station's curve.  Returns None when
    no station has enough picks to plot.
    """
    ws_order      = cfg["ws_order"]
    station_color = _station_color_map(ws_order)

    all_vals = d["pick_s"].values
    if len(all_vals) == 0:
        return None
    x_max = min(float(np.percentile(all_vals, 99)), 180.0)
    if not np.isfinite(x_max) or x_max <= 0:
        x_max = 180.0
    BIN_S   = 2.0
    bins    = np.arange(0, x_max + BIN_S, BIN_S)
    centers = (bins[:-1] + bins[1:]) / 2

    def _smooth(arr: np.ndarray, sigma: float = 2.5) -> np.ndarray:
        size   = max(int(sigma * 4) * 2 + 1, 3)
        x      = np.arange(size) - size // 2
        kernel = np.exp(-0.5 * (x / sigma) ** 2)
        kernel /= kernel.sum()
        return np.clip(np.convolve(arr, kernel, mode="same"), 0, None)

    # Per-station smoothed curves + stats
    per_station: list[tuple[str, np.ndarray, np.ndarray, float, float, str]] = []
    y_max_density = 0.0
    for ws in ws_order:
        vals = d.loc[d["station"] == ws, "pick_s"]
        vals = vals[(vals > 0) & (vals <= x_max)]
        if len(vals) < 10:
            continue
        raw_counts, _ = np.histogram(vals, bins=bins, density=False)
        density, _    = np.histogram(vals, bins=bins, density=True)
        smoothed      = _smooth(density)
        y_max_density = max(y_max_density, float(smoothed.max()))
        per_station.append((
            ws, smoothed, raw_counts, float(vals.mean()), float(vals.median()),
            station_color[ws],
        ))

    if not per_station:
        return None

    y_rug = -y_max_density * 0.08

    fig = go.Figure()
    trace_owner: list[str] = []   # the station each trace belongs to

    for ws, smoothed, raw_counts, mean_s, med_s, color in per_station:
        fig.add_trace(go.Scatter(
            x=centers, y=smoothed, mode="lines", name=ws,
            line=dict(width=2, color=color),
            customdata=raw_counts,
            hovertemplate=(
                f"<b>{ws}</b><br>Pick time: %{{x:.0f}} s<br>"
                "Density: %{y:.5f}<br>"
                "Picks in bucket: %{customdata}<extra></extra>"
            ),
        ))
        trace_owner.append(ws)
        fig.add_trace(go.Scatter(
            x=[mean_s], y=[y_rug], mode="markers",
            marker=dict(symbol="triangle-up", size=9, color=color,
                        line=dict(width=1, color="white")),
            showlegend=False,
            hovertemplate=f"<b>{ws}</b>  Mean: {mean_s:.0f} s<extra></extra>",
        ))
        trace_owner.append(ws)
        fig.add_trace(go.Scatter(
            x=[med_s], y=[y_rug], mode="markers",
            marker=dict(symbol="diamond-tall", size=8, color=color,
                        line=dict(width=1, color="white")),
            showlegend=False,
            hovertemplate=f"<b>{ws}</b>  Median: {med_s:.0f} s<extra></extra>",
        ))
        trace_owner.append(ws)

    n_traces = len(trace_owner)
    stations = [ws for ws, *_ in per_station]

    _TITLE = "Pick Time Distribution — Select Workstation"
    buttons = [dict(
        label="All",
        method="update",
        args=[{"visible": [True] * n_traces},
              {"title.text": f"{_TITLE}: All Workstations"}],
    )]
    for ws in stations:
        vis = [owner == ws for owner in trace_owner]
        buttons.append(dict(
            label=ws,
            method="update",
            args=[{"visible": vis},
                  {"title.text": f"{_TITLE}: {ws}"}],
        ))

    fig.update_layout(
        title=dict(text=f"{_TITLE}: All Workstations", x=0, pad=dict(l=12),
                   font=dict(size=17, color=INK)),
        updatemenus=[dict(
            type="dropdown", direction="down",
            x=1.0, y=1.14, xanchor="right", yanchor="bottom",
            showactive=True, active=0,
            buttons=buttons,
            bgcolor="white", bordercolor="#cccccc",
            font=dict(color=INK, size=11), pad=dict(r=4, t=4),
        )],
        xaxis=dict(title="Pick time (seconds)", range=[0, x_max],
                   tickfont=dict(size=10), showgrid=True, gridcolor="#f0f0f0"),
        yaxis=dict(title="Density", range=[y_rug * 1.5, y_max_density * 1.08],
                   tickfont=dict(size=10), showgrid=True, gridcolor="#f0f0f0"),
        legend=dict(orientation="v", x=1.02, y=1, font=dict(size=10)),
        plot_bgcolor="white", paper_bgcolor="white",
        font=dict(color=INK, family="Inter, sans-serif"),
        margin=dict(t=110, b=90, l=80, r=160),
        hoverlabel=dict(bgcolor="white", bordercolor="#cccccc"),
        annotations=[dict(
            xref="paper", yref="paper", x=0, y=-0.14,
            text=(
                "Smoothed empirical density — 2 s bins with Gaussian kernel.  "
                "Use the dropdown (top-right) to isolate one workstation or show All.  "
                "▲ = Mean   ◆ = Median.  Zero-second picks are excluded."
            ),
            font=dict(size=9, color="#666666"), showarrow=False, align="left",
        )],
    )
    return fig


# ── public entry point ────────────────────────────────────────────────────────

def run(data: dict, cfg: dict) -> list[dict]:
    lsr = data.get("station")
    if lsr is None:
        return []

    d = extract_picks(lsr, cfg)
    if d.empty:
        return []

    # ── Duration statistics: full pick durations, attributed to arrival hour ──
    # (Never sliced into hour segments — a median of partial segments would
    # understate and double-count boundary-spanning picks.)
    grp         = d.groupby(["station", "hour_dt"])["pick_s"]
    pivot_med_s = grp.median().unstack()

    # Reindex columns to full 24-hour range so x-axis always shows 00–23
    all_hours: pd.DatetimeIndex | None = None
    if not pivot_med_s.empty:
        day       = pivot_med_s.columns.min().normalize()
        all_hours = pd.date_range(day, periods=24, freq="h")
        pivot_med_s = pivot_med_s.reindex(columns=all_hours)

    # ── Weighted avg pick time for implied throughput ──────────────────────────
    # Each task contributes its full duration, weighted by the fraction of the
    # task attributed to that hour (matching the proportional task-count logic
    # in throughput.py).  This ensures implied_capacity × fraction ≈ eff_tasks.
    # Vast majority of picks land entirely within one hour — handle those with a
    # vectorised assignment, and only loop row-by-row for the rare boundary-spanning
    # picks (which may cross more than one hour boundary for very long picks).
    _hour_td2  = pd.Timedelta(hours=1)
    _arr_hr    = d["arr_ts"].dt.floor("h")
    _tg_hr     = d["tg_ts"].dt.floor("h")
    _pos_mask  = d["pick_s"] > 0
    _same_mask = _pos_mask & (_arr_hr == _tg_hr)
    _cross_mask = _pos_mask & ~_same_mask

    _w_frames: list[pd.DataFrame] = []
    if _same_mask.any():
        _w_frames.append(pd.DataFrame({
            "station":  d.loc[_same_mask, "station"].values,
            "hour_dt":  _arr_hr[_same_mask].values,
            "full_dur": d.loc[_same_mask, "pick_s"].values,
            "frac":     1.0,
        }))

    if _cross_mask.any():
        _cross_rows: list[dict] = []
        for r in d.loc[_cross_mask, ["station", "arr_ts", "tg_ts", "pick_s"]].itertuples(index=False):
            arr_hr = r.arr_ts.floor("h")
            tg_hr  = r.tg_ts.floor("h")
            h = arr_hr
            while h <= tg_hr:
                seg = (min(r.tg_ts, h + _hour_td2) - max(r.arr_ts, h)).total_seconds()
                if seg > 0:
                    _cross_rows.append({"station": r.station, "hour_dt": h,
                                        "full_dur": r.pick_s, "frac": seg / r.pick_s})
                h += _hour_td2
        if _cross_rows:
            _w_frames.append(pd.DataFrame(_cross_rows))

    if _w_frames:
        _wdf = pd.concat(_w_frames, ignore_index=True)
        _wdf["w_dur"] = _wdf["frac"] * _wdf["full_dur"]
        _wg = _wdf.groupby(["station", "hour_dt"])
        pivot_wavg_s = (_wg["w_dur"].sum() / _wg["frac"].sum()).unstack()
        if all_hours is not None:
            pivot_wavg_s = pivot_wavg_s.reindex(columns=all_hours)
    else:
        pivot_wavg_s = grp.mean().unstack()
        if all_hours is not None:
            pivot_wavg_s = pivot_wavg_s.reindex(columns=all_hours)

    # ── Implied throughput = 3600 / (weighted avg pick + switch time) ──────────
    # The switch term is user-configurable (see resolve_switch_s): either the
    # flat, user-set value or the per-station measured operational switch time.
    _sw_series = pd.Series(
        {ws: resolve_switch_s(cfg, ws) for ws in pivot_wavg_s.index},
        dtype=float,
    )
    pivot_avg_tph = 3600.0 / pivot_wavg_s.add(_sw_series, axis=0)

    fig = _station_hour_heatmap_toggle(pivot_med_s, pivot_wavg_s, pivot_avg_tph, cfg)

    # ── Raw data shared by the two charts ─────────────────────────────────────
    # Per-station-hour summary (full pick durations, arrival-hour attribution)
    _dwell_summary_rows = []
    for (_ws, _hr), _sub in d.groupby(["station", "hour_dt"])["pick_s"]:
        _mean = float(_sub.mean())
        _sw   = resolve_switch_s(cfg, _ws)
        _dwell_summary_rows.append({
            "station":   _ws,
            "hour":      _hr.strftime("%H:%M") if hasattr(_hr, "strftime") else str(_hr),
            "median_s":  round(float(_sub.median()), 2),
            "mean_s":    round(_mean, 2),
            "count":     int(len(_sub)),
            "switch_s_used": round(_sw, 2),
            "implied_tph_avg": round(3600.0 / (_mean + _sw), 2) if _mean > 0 else None,
        })
    # Individual pick events (full resolution — original durations for distribution)
    _dwell_all_rows = pd.DataFrame({
        "station": d["station"].astype(str),
        "hour":    d["hour_dt"].dt.strftime("%H:%M"),
        "pick_s":  d["pick_s"].astype(float).round(2),
    }).to_dict("records")

    # Describe the pick-time measurement so the report reflects the actual
    # source when the ppReady fallback is active.
    _start_event = resolve_pick_start_event(cfg)
    if _start_event == "ppReady":
        _pick_measure = (
            "measured from each robot's 'ppReady' (put-point ready) event to its next "
            "'triggerGo' — a fallback used because this export has no 'arrived' events. "
            "The station is taken from the triggerGo (ppReady locations are blank). "
            "Note: this measures put-point-ready→release, a slightly wider window than the "
            "standard arrived→triggerGo pick, so absolute values run a little high — treat it "
            "as an approximation and rely on the hour-to-hour and station-to-station comparison"
        )
        _pick_pair_label = "ppReady→triggerGo"
    else:
        _pick_measure = (
            "measured from each robot's 'arrived' event to its next 'triggerGo' at the same "
            "station (pairs whose triggerGo fired at a different station indicate lost events "
            "and are dropped)"
        )
        _pick_pair_label = "arrived→triggerGo"

    _sw_fixed = resolve_switch_s(cfg)
    if cfg.get("switch_mode") == "measured":
        _sw_note = (
            "the per-station measured operational switch time (release→next arrived, "
            f"gaps ≤ {MAX_OPERATIONAL_SWITCH_S/60:.0f} min), falling back to the user-set "
            f"{cfg.get('switch_s_fixed', _sw_fixed):.0f} s where a station has no measured swaps"
        )
    else:
        _sw_note = (
            f"a flat, user-set {_sw_fixed:.0f} s assumption (station-level switch time isn't "
            "reliably measurable with current logging quality — switch to 'measured' mode or see "
            "the Robot Switch Time chart for measured median/average switch time)"
        )

    charts = [{
        "id":          "dwell_heatmap",
        "title":       "Operator Pick Time at Workstations",
        "figure":      fig,
        "source":      "Station record sheet (labor_station_record)",
        "method":      (
            "Time a robot waits at the station while the operator works, "
            f"{_pick_measure}. Each pick's full duration is attributed to its start hour — "
            "durations are not split across hour boundaries, so the median and average are "
            "true pick-time statistics. "
            "Toggle between Pick Time (Median), Pick Time (Average), and Implied Throughput "
            f"(Average) — computed as tasks/hr = 3 600 ÷ (avg pick + switch), where switch "
            f"is the {_sw_note}. "
            "High pick time means the operator is the bottleneck — the robot is ready but "
            "waiting for the pick to complete. "
            "Stations with consistently high values may have harder tasks, heavier items, or "
            "an ergonomic issue. "
            "Increasing values across the shift can indicate operator fatigue."
        ),
        "export_hint": "robot_dwell_intervals.xlsx",
        "raw_data": {
            "description": f"Pick time ({_pick_pair_label}) per station per hour — median, mean, count, implied throughput",
            "switch_time_source": _sw_note,
            "rows": _dwell_summary_rows,
        },
    }, {
        "id":          "dwell_pick_distribution",
        "title":       "Pick Time Distribution by Workstation",
        "figure":      _pick_time_distribution(d, cfg),
        "source":      "Station record sheet (labor_station_record)",
        "method":      (
            "Smoothed empirical density of robot dwell / pick times per workstation. "
            "Each line represents one station, derived from 2-second histogram bins "
            "convolved with a Gaussian kernel. "
            "Narrow tall peaks indicate consistent pick times; wide flat curves indicate "
            "high variance. "
            "X-axis is bounded at the 99th percentile (≤ 180 s) to focus on the bulk of picks. "
            "Stations with similar distributions are likely doing the same task type; "
            "outliers suggest ergonomic or workflow differences."
        ),
        "export_hint": "robot_dwell_intervals.xlsx",
        "raw_data": {
            "description": "All individual robot pick events (station, hour, pick duration in seconds)",
            "rows": _dwell_all_rows,
        },
    }]

    # ── Per-workstation pick-time distribution with a station selector ─────────
    fig_by_station = _pick_time_distribution_by_station(d, cfg)
    if fig_by_station is not None:
        charts.append({
            "id":          "dwell_pick_distribution_by_station",
            "title":       "Pick Time Distribution — Select Workstation",
            "figure":      fig_by_station,
            "source":      "Station record sheet (labor_station_record)",
            "method":      (
                "The same smoothed pick-time density as the previous chart, but with a "
                "workstation selector (dropdown, top-right): choose 'All' to overlay every "
                "station's curve, or pick a single workstation to isolate its distribution. "
                "Each curve is derived from 2-second histogram bins convolved with a Gaussian "
                "kernel; ▲ marks the station mean and ◆ the median. "
                "Narrow tall peaks indicate consistent pick times; wide flat curves indicate "
                "high variance. "
                "X-axis is bounded at the 99th percentile (≤ 180 s); zero-second picks are "
                "excluded. "
                "Isolating one station makes it easy to see a bimodal (two-mode) pick pattern "
                "that is otherwise hidden when all stations overlap."
            ),
            "export_hint": "robot_dwell_intervals.xlsx",
            "raw_data": {
                "description": "All individual robot pick events (station, hour, pick duration in seconds)",
                "rows": _dwell_all_rows,
            },
        })

    return charts
