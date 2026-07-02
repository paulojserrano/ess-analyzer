"""
analyses/dwell_time.py — operator pick time at workstations.

Pick time = time a robot sits at a LABOR station while the operator works,
measured from 'arrived' to 'triggerGo' events in the station-record sheet.

Charts produced
---------------
1. Pick time per station per hour (heatmap) — always shows Average pick time;
   toggles Pick Time (s) / Implied Throughput (tasks/hr, assuming 6 s switch
   per cycle).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from config import AUTO_TYPE_PALETTE, INK

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

# Fixed overhead added to each robot cycle outside of the observed pick
_SWITCH_S = 6.0   # robot switch / handoff time (seconds)


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
    _TPH_TITLE  = f"Implied Station Throughput — Pick Time + {int(_SWITCH_S)}s switch"

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


_OCC_COLORSCALE = [
    [0.0,  "#ef4444"],   # 0 %  — red   (idle)
    [0.42, "#fbbf24"],   # ~40 % — yellow
    [0.85, "#16a34a"],   # ~85 % — green
    [1.0,  "#15803d"],   # 100 % — dark green (fully occupied)
]


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


def _capacity_gap_heatmap(
    util_pick:  pd.DataFrame,          # station × hour — pick occupancy %
    util_full:  pd.DataFrame | None,   # station × hour — pick + switch occupancy %
    cfg: dict,
) -> go.Figure:
    """
    Two-trace toggle:
      Trace 0 — Pick occupancy: fraction of hour spent picking  [default]
      Trace 1 — Station occupancy: pick + robot switch time
    Both guaranteed ≤ 100 % for single-robot stations.
    """
    ws_order    = cfg["ws_order"]
    hour_labels = [h.strftime("%H:00") for h in util_pick.columns]

    def _arr(df: pd.DataFrame) -> np.ndarray:
        return df.reindex(ws_order).values.astype(float)

    def _text(arr: np.ndarray) -> list:
        return [
            [f"{arr[i, j]:.0f}%" if not np.isnan(arr[i, j]) else ""
             for j in range(arr.shape[1])]
            for i in range(arr.shape[0])
        ]

    _colorbar = dict(
        title="Occupancy",
        thickness=14, len=0.8,
        tickvals=[0, 25, 50, 75, 100],
        ticktext=["0%", "25%", "50%", "75%", "100%"],
    )

    pick_arr  = _arr(util_pick)
    pick_text = _text(pick_arr)

    def _arr_stats(arr: np.ndarray) -> tuple[float, float]:
        valid = arr[~np.isnan(arr)]
        if valid.size == 0:
            return 0.0, 0.0
        return float(np.mean(valid)), float(np.median(valid))

    mean_p, med_p = _arr_stats(pick_arr)

    _PICK_TITLE = "Station Occupancy — Pick Time Only"
    _FULL_TITLE = "Station Occupancy — Pick + Switch Time"

    fig = go.Figure()

    # ── Trace 0 : pick occupancy (default) ───────────────────────────────────
    fig.add_trace(go.Heatmap(
        z=pick_arr.tolist(),
        text=pick_text,
        x=hour_labels, y=ws_order,
        colorscale=_OCC_COLORSCALE,
        zmin=0, zmax=100.0,
        texttemplate="%{text}", textfont=dict(size=8),
        hovertemplate=(
            "<b>%{y}</b><br>%{x}<br>"
            "Pick occupancy: %{z:.0f}%<extra></extra>"
        ),
        colorbar=_colorbar,
        visible=True,
    ))

    buttons = [
        dict(
            label="Pick Only",
            method="update",
            args=[{"visible": [True, False]}, {"title.text": _PICK_TITLE}],
        ),
    ]

    # ── Trace 1 : full station occupancy (hidden, added only if data exists) ─
    if util_full is not None:
        full_arr  = _arr(util_full)
        full_text = _text(full_arr)
        mean_f, med_f = _arr_stats(full_arr)
        fig.add_trace(go.Heatmap(
            z=full_arr.tolist(),
            text=full_text,
            x=hour_labels, y=ws_order,
            colorscale=_OCC_COLORSCALE,
            zmin=0, zmax=100.0,
            texttemplate="%{text}", textfont=dict(size=8),
            hovertemplate=(
                "<b>%{y}</b><br>%{x}<br>"
                "Station occupancy: %{z:.0f}%<extra></extra>"
            ),
            colorbar=_colorbar,
            visible=False,
        ))
        buttons.append(dict(
            label="Pick + Switch",
            method="update",
            args=[{"visible": [False, True]}, {"title.text": _FULL_TITLE}],
        ))

    _stats_text = (
        f"Pick only — Mean: <b>{mean_p:.0f}%</b>  ·  Median: <b>{med_p:.0f}%</b>"
    )
    if util_full is not None:
        _stats_text += (
            f"   |   Pick + switch — Mean: <b>{mean_f:.0f}%</b>  ·  Median: <b>{med_f:.0f}%</b>"
        )

    fig.update_layout(
        title=dict(text=_PICK_TITLE, x=0, pad=dict(l=12), font=dict(size=17, color=INK)),
        updatemenus=[dict(
            type="buttons", direction="right",
            x=1.0, y=1.10, xanchor="right", yanchor="bottom",
            showactive=True,
            buttons=buttons,
            bgcolor="white", bordercolor="#cccccc",
            font=dict(color=INK, size=11),
            pad=dict(r=4, t=4),
        )],
        yaxis=dict(autorange="reversed", tickfont=dict(size=10)),
        xaxis=dict(tickangle=-45, tickfont=dict(size=9), title="Hour"),
        plot_bgcolor="white", paper_bgcolor="white",
        font=dict(color=INK, family="Inter, sans-serif"),
        margin=dict(t=110, b=130, l=110, r=80),
        hoverlabel=dict(bgcolor="white", bordercolor="#cccccc"),
        annotations=[dict(
            xref="paper", yref="paper", x=0, y=-0.20,
            text=(
                "Occupancy = seconds the station was in use ÷ 3 600.  "
                "Events spanning hour boundaries are split proportionally.  "
                "Guaranteed ≤ 100 % — one robot at a time per station.<br>"
                + _stats_text
            ),
            font=dict(size=9, color="#666666"), showarrow=False, align="left",
        )],
    )
    return fig


def _pick_vs_throughput_scatter(
    pivot_avg_s: pd.DataFrame,   # station × hour — avg pick time (s)
    actual_t: pd.DataFrame,      # station × hour — actual completions/hr
    cfg: dict,
) -> go.Figure:
    """
    Scatter of actual throughput (Y) vs avg pick time (X) for every
    station × hour observation that has both values.  OLS regression line
    and R² are computed on the pooled sample to quantify how much of
    throughput variance is explained by pick time alone.
    """
    ws_order    = cfg["ws_order"]
    type_map    = cfg.get("type_map", {})
    type_colors = cfg.get("type_colors", {})

    _zone_color: dict[str, str] = {}
    _pal_idx = 0

    def _color(ws: str) -> str:
        nonlocal _pal_idx
        zone = type_map.get(ws, "")
        if zone in type_colors:
            return type_colors[zone]
        if zone not in _zone_color:
            _zone_color[zone] = AUTO_TYPE_PALETTE[_pal_idx % len(AUTO_TYPE_PALETTE)]
            _pal_idx += 1
        return _zone_color[zone]

    # Build long-form table: one row per (station, hour) with both values
    records = []
    for ws in ws_order:
        if ws not in pivot_avg_s.index or ws not in actual_t.index:
            continue
        for col in pivot_avg_s.columns:
            pick_s = pivot_avg_s.at[ws, col]
            tph    = actual_t.at[ws, col] if col in actual_t.columns else float("nan")
            if pd.notna(pick_s) and pd.notna(tph) and pick_s > 0 and tph > 0:
                records.append({"station": ws, "pick_s": pick_s, "tph": tph})

    if not records:
        fig = go.Figure()
        fig.update_layout(
            title=dict(text="Pick Time vs Actual Throughput (no data)", x=0),
        )
        return fig

    df = pd.DataFrame(records)

    # ── Automatic outlier removal (Tukey IQR fencing, 1.5×) ──────────────────
    # Applied independently on both axes: removes station-hours with abnormally
    # long/short pick times (instrument noise, robot stalls) and abnormally
    # high/low throughput (start-of-shift ramp, end-of-shift drain).
    def _iqr_bounds(s: pd.Series, k: float = 1.5) -> tuple[float, float]:
        q1, q3 = float(s.quantile(0.25)), float(s.quantile(0.75))
        iqr = q3 - q1
        return q1 - k * iqr, q3 + k * iqr

    x_lo, x_hi = _iqr_bounds(df["pick_s"])
    y_lo, y_hi = _iqr_bounds(df["tph"])
    inlier_mask = df["pick_s"].between(x_lo, x_hi) & df["tph"].between(y_lo, y_hi)
    df_fit = df[inlier_mask].reset_index(drop=True)
    df_out = df[~inlier_mask].reset_index(drop=True)
    if df_fit.shape[0] < 4:          # safety: if too aggressive, keep everything
        df_fit, df_out = df.copy(), pd.DataFrame(columns=df.columns)
    n_removed = len(df_out)

    # OLS on inliers only
    x_all  = df_fit["pick_s"].values
    y_all  = df_fit["tph"].values
    coeffs = np.polyfit(x_all, y_all, 1)
    slope, intercept = coeffs
    y_pred = np.polyval(coeffs, x_all)
    ss_res = float(np.sum((y_all - y_pred) ** 2))
    ss_tot = float(np.sum((y_all - y_all.mean()) ** 2))
    r2     = 1 - ss_res / ss_tot if ss_tot > 0 else float("nan")

    x_line = np.linspace(x_all.min(), x_all.max(), 200)
    y_line = np.polyval(coeffs, x_line)

    fig = go.Figure()

    # ── Outlier points — grey, rendered first so inliers sit on top ──────────
    if not df_out.empty:
        fig.add_trace(go.Scatter(
            x=df_out["pick_s"], y=df_out["tph"],
            mode="markers",
            name=f"Excluded — IQR outlier ({n_removed})",
            marker=dict(size=6, color="#cccccc", opacity=0.5,
                        line=dict(width=0.5, color="#999999")),
            customdata=df_out["station"].values,
            hovertemplate=(
                "<b>%{customdata}</b><br>"
                "Pick time: %{x:.0f} s<br>"
                "Throughput: %{y:.0f} tasks/hr<br>"
                "<i>excluded from OLS fit (IQR outlier)</i><extra></extra>"
            ),
        ))

    # ── Per-station scatter traces (inliers only) ─────────────────────────────
    for ws in ws_order:
        sub = df_fit[df_fit["station"] == ws]
        if sub.empty:
            continue
        fig.add_trace(go.Scatter(
            x=sub["pick_s"], y=sub["tph"],
            mode="markers",
            name=ws,
            marker=dict(size=7, color=_color(ws), opacity=0.75,
                        line=dict(width=0.5, color="white")),
            hovertemplate=(
                f"<b>{ws}</b><br>"
                "Pick time: %{x:.0f} s<br>"
                "Throughput: %{y:.0f} tasks/hr<extra></extra>"
            ),
        ))

    # ── OLS regression line ───────────────────────────────────────────────────
    r2_label    = f"R² = {r2:.3f}" if not np.isnan(r2) else "R² = n/a"
    slope_label = f"slope = {slope:+.2f} tasks/hr per s"
    fig.add_trace(go.Scatter(
        x=x_line, y=y_line,
        mode="lines",
        name=f"OLS fit  ({r2_label})",
        line=dict(color="#111827", width=2, dash="dash"),
        hovertemplate=(
            "OLS fit<br>"
            "Pick time: %{x:.0f} s<br>"
            "Fitted throughput: %{y:.1f} tasks/hr<extra></extra>"
        ),
    ))

    # ── Theoretical ceiling: 3600 / (pick_s + _SWITCH_S) ────────────────────
    x_ceil = np.linspace(max(1, x_all.min()), x_all.max(), 200)
    y_ceil = 3600.0 / (x_ceil + _SWITCH_S)
    fig.add_trace(go.Scatter(
        x=x_ceil, y=y_ceil,
        mode="lines",
        name=f"Theoretical ceiling  (pick + {int(_SWITCH_S)}s switch)",
        line=dict(color="#ef4444", width=1.5, dash="dot"),
        hovertemplate=(
            "Ceiling (operator-limited)<br>"
            "Pick time: %{x:.0f} s<br>"
            "Max throughput: %{y:.1f} tasks/hr<extra></extra>"
        ),
    ))

    n_fit = len(df_fit)
    outlier_note = (
        f"  {n_removed} of {len(df)} points excluded as IQR outliers "
        f"(Tukey 1.5× fence on pick time and throughput independently) — shown in grey."
    ) if n_removed else ""
    note = (
        f"<b>{r2_label}</b> — pick time explains {r2*100:.0f}% of throughput variance "
        f"across {n_fit} station-hour observations (fit on inliers only).{outlier_note}<br>"
        f"{slope_label}.  "
        f"Red dotted line = operator-speed ceiling (3 600 ÷ (pick + {int(_SWITCH_S)}s)).  "
        "Points below the ceiling indicate robot-supply or non-pick losses."
    ) if not np.isnan(r2) else (
        f"Regression could not be computed ({n_fit} observations)."
    )

    fig.update_layout(
        title=dict(
            text="Pick Time vs Actual Throughput — OLS Fit",
            x=0, pad=dict(l=12), font=dict(size=17, color=INK),
        ),
        xaxis=dict(
            title="Avg pick time per station-hour (s)",
            tickfont=dict(size=10), showgrid=True, gridcolor="#f0f0f0",
        ),
        yaxis=dict(
            title="Actual completions / hr",
            tickfont=dict(size=10), showgrid=True, gridcolor="#f0f0f0",
        ),
        legend=dict(orientation="v", x=1.02, y=1, font=dict(size=10)),
        plot_bgcolor="white", paper_bgcolor="white",
        font=dict(color=INK, family="Inter, sans-serif"),
        margin=dict(t=80, b=120, l=80, r=200),
        hoverlabel=dict(bgcolor="white", bordercolor="#cccccc"),
        annotations=[dict(
            xref="paper", yref="paper", x=0, y=-0.18,
            text=note,
            font=dict(size=9, color="#666666"), showarrow=False, align="left",
        )],
    )
    return fig


def _pick_time_distribution(d: pd.DataFrame, cfg: dict) -> go.Figure:
    """
    Smoothed density line per workstation.
    Uses 2 s histogram bins convolved with a Gaussian kernel — no scipy required.
    """
    ws_order    = cfg["ws_order"]
    type_map    = cfg.get("type_map", {})
    type_colors = cfg.get("type_colors", {})

    # Assign a colour per station, falling back to AUTO_TYPE_PALETTE
    _zone_color: dict[str, str] = {}
    _pal_idx = 0

    def _color(ws: str) -> str:
        nonlocal _pal_idx
        zone = type_map.get(ws, "")
        if zone in type_colors:
            return type_colors[zone]
        if zone not in _zone_color:
            _zone_color[zone] = AUTO_TYPE_PALETTE[_pal_idx % len(AUTO_TYPE_PALETTE)]
            _pal_idx += 1
        return _zone_color[zone]

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
        counts, _ = np.histogram(vals, bins=bins, density=True)
        smoothed  = _smooth(counts)
        y_max_density = max(y_max_density, float(smoothed.max()))
        mean_s = float(vals.mean())
        med_s  = float(vals.median())
        station_stats.append((ws, mean_s, med_s, _color(ws)))
        fig.add_trace(go.Scatter(
            x=centers,
            y=smoothed,
            mode="lines",
            name=ws,
            line=dict(width=2, color=_color(ws)),
            hovertemplate=(
                f"<b>{ws}</b><br>"
                "Pick time: %{x:.0f} s<br>"
                "Density: %{y:.5f}<extra></extra>"
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


# ── public entry point ────────────────────────────────────────────────────────

def run(data: dict, cfg: dict) -> list[dict]:
    lsr = data.get("station")
    if lsr is None:
        return []

    lsr = lsr.copy()
    lsr["ts"]      = pd.to_datetime(lsr["时间戳"])
    lsr["station"] = lsr["位置编号"].map(cfg["point2ws"])

    # Only K50 delivery robots do operator picks — filter out shuttles so their
    # shorter dwell events don't lower the average and inflate the implied ceiling.
    amr_type = cfg.get("amr_type")
    if amr_type and "机器人类型" in lsr.columns:
        lsr = lsr[lsr["机器人类型"] == amr_type]

    ev   = lsr.sort_values(["机器人编号", "ts"])
    rows = []
    for _rb, sub in ev.groupby("机器人编号"):
        arr = arr_loc = None
        for ts, et, loc in sub[["ts", "事件类型", "station"]].values:
            if et == "arrived":
                arr, arr_loc = ts, loc
            elif et == "triggerGo" and arr is not None:
                rows.append({
                    "station":  arr_loc,
                    "hour_dt":  arr.floor("h"),
                    "pick_s":   (ts - arr).total_seconds(),
                    "arr_ts":   arr,
                    "tg_ts":    ts,
                })
                arr = None

    if not rows:
        return []

    d = pd.DataFrame(rows).dropna(subset=["station"])
    d = d[(d["pick_s"] >= 0) & (d["pick_s"] < 3600)]

    # ── Time-slice expansion for heatmap pivots ──────────────────────────────
    # Split each pick's seconds proportionally across clock-hour boundaries
    # so that the heatmap aligns with the throughput chart's rigid hour buckets.
    # A 20 s pick spanning 04:59:50 → 05:00:10 credits 10 s to 04:00, 10 s to 05:00.
    _hour_td = pd.Timedelta(hours=1)
    d["_arr_hour"]  = d["arr_ts"].dt.floor("h")
    d["_secs_left"] = (d["_arr_hour"] + _hour_td - d["arr_ts"]).dt.total_seconds()

    # Fast path — picks that fit entirely within their start hour (vast majority)
    within = d[d["pick_s"] <= d["_secs_left"]]
    sliced_rows: list[dict] = [
        {"station": ws, "hour_dt": h, "pick_s": ps}
        for ws, h, ps in zip(within["station"], within["_arr_hour"], within["pick_s"])
    ]

    # Slow path — picks that span an hour boundary (typically < 1 %)
    for _, r in d[d["pick_s"] > d["_secs_left"]].iterrows():
        s, e = r["arr_ts"], r["tg_ts"]
        h = r["_arr_hour"]
        while h < e:
            seg_s = (min(e, h + _hour_td) - max(s, h)).total_seconds()
            if seg_s > 0:
                sliced_rows.append({
                    "station": r["station"],
                    "hour_dt": h,
                    "pick_s":  seg_s,
                })
            h += _hour_td

    d.drop(columns=["_arr_hour", "_secs_left"], inplace=True)
    ds = pd.DataFrame(sliced_rows)
    grp         = ds.groupby(["station", "hour_dt"])["pick_s"]
    pivot_med_s = grp.median().unstack()
    pivot_avg_s = grp.mean().unstack()

    # Reindex columns to full 24-hour range so x-axis always shows 00–23
    all_hours: pd.DatetimeIndex | None = None
    if not pivot_med_s.empty:
        day       = pivot_med_s.columns.min().normalize()
        all_hours = pd.date_range(day, periods=24, freq="h")
        pivot_med_s = pivot_med_s.reindex(columns=all_hours)
        pivot_avg_s = pivot_avg_s.reindex(columns=all_hours)

    # ── Weighted avg pick time for implied throughput ──────────────────────────
    # Each task contributes its full duration, weighted by the fraction of the
    # task attributed to that hour (matching the proportional task-count logic
    # in throughput.py).  This ensures implied_capacity × fraction ≈ eff_tasks.
    _hour_td2 = pd.Timedelta(hours=1)
    _w_rows: list[dict] = []
    for _, r in d.iterrows():
        arr_hr = r["arr_ts"].floor("h")
        tg_hr  = r["tg_ts"].floor("h")
        dur    = r["pick_s"]
        if dur <= 0:
            continue
        if arr_hr == tg_hr:
            _w_rows.append({"station": r["station"], "hour_dt": arr_hr,
                            "full_dur": dur, "frac": 1.0})
        else:
            h = arr_hr
            while h <= tg_hr:
                seg = (min(r["tg_ts"], h + _hour_td2) - max(r["arr_ts"], h)).total_seconds()
                if seg > 0:
                    _w_rows.append({"station": r["station"], "hour_dt": h,
                                    "full_dur": dur, "frac": seg / dur})
                h += _hour_td2
    if _w_rows:
        _wdf = pd.DataFrame(_w_rows)
        _wdf["w_dur"] = _wdf["frac"] * _wdf["full_dur"]
        _wg = _wdf.groupby(["station", "hour_dt"])
        pivot_wavg_s = (_wg["w_dur"].sum() / _wg["frac"].sum()).unstack()
        if all_hours is not None:
            pivot_wavg_s = pivot_wavg_s.reindex(columns=all_hours)
    else:
        pivot_wavg_s = pivot_avg_s

    # Implied throughput = 3600 / (weighted avg pick + switch)
    pivot_avg_tph = 3600.0 / (pivot_wavg_s + _SWITCH_S)

    fig = _station_hour_heatmap_toggle(pivot_med_s, pivot_wavg_s, pivot_avg_tph, cfg)

    # ── Raw data shared by the first two charts ──────────────────────────────
    # Per-station-hour summary (aggregated from time-sliced data)
    _dwell_summary_rows = []
    for (_ws, _hr), _sub in ds.groupby(["station", "hour_dt"])["pick_s"]:
        _dwell_summary_rows.append({
            "station":   _ws,
            "hour":      _hr.strftime("%H:%M") if hasattr(_hr, "strftime") else str(_hr),
            "median_s":  round(float(_sub.median()), 2),
            "mean_s":    round(float(_sub.mean()), 2),
            "count":     int(len(_sub)),
            "implied_tph_avg": round(3600.0 / (float(_sub.mean()) + _SWITCH_S), 2) if float(_sub.mean()) > 0 else None,
        })
    # Individual pick events (full resolution — original durations for distribution)
    _dwell_all_rows = [
        {
            "station": str(r["station"]),
            "hour":    r["hour_dt"].strftime("%H:%M") if hasattr(r["hour_dt"], "strftime") else str(r["hour_dt"]),
            "pick_s":  round(float(r["pick_s"]), 2),
        }
        for _, r in d[["station", "hour_dt", "pick_s"]].iterrows()
    ]

    charts = [{
        "id":          "dwell_heatmap",
        "title":       "Operator Pick Time at Workstations",
        "figure":      fig,
        "source":      "Station record sheet (labor_station_record)",
        "method":      (
            "Time a robot waits at the station while the operator works, measured from "
            "each robot's 'arrived' event to its next 'triggerGo'. "
            "Pick time seconds are time-sliced at clock-hour boundaries — a pick spanning "
            "two hours is split proportionally so each hour only receives the seconds that "
            "physically occurred within it (aligned with throughput counting). "
            "Toggle between Pick Time (Median), Pick Time (Average), and Implied Throughput "
            f"(Average) — computed as tasks/hr = 3 600 ÷ (pick_s + {int(_SWITCH_S)}s switch). "
            "High pick time means the operator is the bottleneck — the robot is ready but "
            "waiting for the pick to complete. "
            "Stations with consistently high values may have harder tasks, heavier items, or "
            "an ergonomic issue. "
            "Increasing values across the shift can indicate operator fatigue."
        ),
        "export_hint": "robot_dwell_intervals.xlsx",
        "raw_data": {
            "description": "Pick time (arrived→triggerGo) per station per hour — median, mean, count, implied throughput",
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

    return charts
