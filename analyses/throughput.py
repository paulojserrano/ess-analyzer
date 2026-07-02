"""
analyses/throughput.py — completions per LABOR station per hour.

Charts produced
---------------
1. throughput_total       — Total triggerGo completions across all stations, hourly bar chart.
2. throughput_heatmap     — Per-station Effective Tasks heatmap (actual completions with
                            proportional hour-boundary attribution), with toggle to
                            % of Implied Throughput. Falls back to raw triggerGo counts
                            if pick-time data is unavailable.
3. throughput_picker_rate — Per-station instantaneous hourly rate (rolling ROLL_MIN-min window),
                            minute-resolution line chart with horizontal range slider.
4. throughput_utilisation — Per-station % of configured design rate, hourly heatmap.
                            Only rendered when design_rate is configured.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from config import ACCENT, INK

# ── helpers ───────────────────────────────────────────────────────────────────

def _zone_colorscale(type_map: dict, type_colors: dict, ws_order: list[str]) -> list[str]:
    """Return a per-station list of zone hex colors (for axis annotation use)."""
    return [type_colors.get(type_map.get(ws, ""), "#555555") for ws in ws_order]


def _heatmap_layout(fig: go.Figure, hour_labels: list[str], title: str) -> None:
    fig.update_layout(
        title=dict(text=title, x=0, pad=dict(l=12), font=dict(size=17, color=INK)),
        yaxis=dict(autorange="reversed", tickfont=dict(size=10)),
        xaxis=dict(tickangle=-45, tickfont=dict(size=9), title="Hour"),
        plot_bgcolor="white",
        paper_bgcolor="white",
        font=dict(color=INK, family="Inter, sans-serif"),
        margin=dict(t=70, b=90, l=110, r=80),
        hoverlabel=dict(bgcolor="white", bordercolor="#cccccc"),
    )


# ── public entry point ────────────────────────────────────────────────────────

def run(data: dict, cfg: dict) -> list[dict]:
    lsr = data.get("station")
    if lsr is None:
        return []

    ws_order     = cfg["ws_order"]
    type_map     = cfg["type_map"]
    type_colors  = cfg["type_colors"]
    design_rate  = cfg["design_rate"]
    design_total = cfg["design_total_rate"]

    # Prepare station-record rows once — used for both throughput count and pick time
    lsr_t = lsr.copy()
    lsr_t["ts"]      = pd.to_datetime(lsr_t["时间戳"])
    lsr_t["station"] = lsr_t["位置编号"].map(cfg["point2ws"])
    amr_type = cfg.get("amr_type")
    if amr_type and "机器人类型" in lsr_t.columns:
        lsr_t = lsr_t[lsr_t["机器人类型"] == amr_type]

    # Throughput = triggerGo events: operator done, robot leaves (true task completion)
    tgo = lsr_t[lsr_t["事件类型"] == "triggerGo"].dropna(subset=["station"])
    tgo = tgo[tgo["station"].isin(ws_order)]
    tgo["hour"] = tgo["ts"].dt.floor("h")

    pivot = tgo.groupby(["hour", "station"]).size().unstack(fill_value=0)
    order = [w for w in ws_order if w in pivot.columns]
    if not order:
        return []

    pivot = pivot[order]

    # Reindex rows to full 24-hour range so x-axis always shows 00–23
    all_hours = None
    if not pivot.empty:
        day = pivot.index.min().normalize()
        all_hours = pd.date_range(day, periods=24, freq="h")
        pivot = pivot.reindex(all_hours, fill_value=0)

    hours       = pivot.index
    hour_labels = [h.strftime("%H:00") for h in hours]
    totals      = pivot.sum(axis=1)
    raw         = pivot.T.values.astype(float)   # (n_stations, n_hours)

    # ── Implied throughput % (pick + switch occupancy, station × hour) ──────────
    # (pick_seconds + switch_seconds) ÷ 3 600, with events that span hour
    # boundaries split proportionally.  Guaranteed ≤ 100 % because each
    # station serves one robot at a time.
    util_pct_2d: np.ndarray | None = None
    effective_tasks_2d: np.ndarray | None = None
    capacity_2d: np.ndarray | None = None
    if all_hours is not None:
        try:
            from analyses.dwell_time import _clipped_occupancy

            pick_rows: list[dict] = []
            for _rb, sub in lsr_t.sort_values(["机器人编号", "ts"]).groupby("机器人编号"):
                arr = arr_loc = None
                for ts_val, et, loc in sub[["ts", "事件类型", "station"]].values:
                    if et == "arrived":
                        arr, arr_loc = ts_val, loc
                    elif et == "triggerGo" and arr is not None:
                        pick_s = (ts_val - arr).total_seconds()
                        if 0 < pick_s < 3600 and arr_loc in order:
                            pick_rows.append({
                                "station":  arr_loc,
                                "arr_ts":   arr,
                                "tg_ts":    ts_val,
                            })
                        arr = None

            if pick_rows:
                pick_d = pd.DataFrame(pick_rows)
                pick_occ = _clipped_occupancy(
                    pick_d, "arr_ts", "tg_ts", order, all_hours,
                )

                # Switch time: release → next arrived, per station
                ev_sw = lsr_t[
                    lsr_t["事件类型"].isin(["release", "arrived"]) & lsr_t["station"].notna()
                ].sort_values(["station", "ts"])
                sw_rows: list[dict] = []
                for ws, sub in ev_sw.groupby("station"):
                    open_rel = None
                    for ts_val, et in sub[["ts", "事件类型"]].values:
                        if et == "release":
                            open_rel = ts_val
                        elif et == "arrived" and open_rel is not None:
                            sw_s = (ts_val - open_rel).total_seconds()
                            if 0 <= sw_s < 7200:
                                sw_rows.append({
                                    "station":    ws,
                                    "rel_ts":     open_rel,
                                    "next_arr_ts": ts_val,
                                })
                            open_rel = None

                if sw_rows:
                    sw_d = pd.DataFrame(sw_rows)
                    switch_occ = _clipped_occupancy(
                        sw_d, "rel_ts", "next_arr_ts", order, all_hours,
                    )
                    full_occ = (pick_occ + switch_occ).clip(upper=3600.0)
                else:
                    full_occ = pick_occ

                util_pct_2d = (full_occ / 3600.0 * 100.0).values

                # ── Effective Tasks: actual completions with proportional hour attribution ──
                # When a pick spans an hour boundary (arrived in hour A, triggerGo in hour B),
                # credit is split proportionally by time spent in each hour.
                pick_d["pick_s"] = (pick_d["tg_ts"] - pick_d["arr_ts"]).dt.total_seconds()
                effective_tasks_2d = np.zeros_like(raw, dtype=float)
                ws_idx = {ws: i for i, ws in enumerate(order)}
                hr_idx = {hr: j for j, hr in enumerate(all_hours)}
                for _, row in pick_d.iterrows():
                    si = ws_idx.get(row["station"])
                    if si is None:
                        continue
                    arr_hr = row["arr_ts"].floor("h")
                    tg_hr  = row["tg_ts"].floor("h")
                    duration = row["pick_s"]
                    if duration <= 0:
                        continue
                    if arr_hr == tg_hr:
                        # Entire pick within one hour
                        ji = hr_idx.get(arr_hr)
                        if ji is not None:
                            effective_tasks_2d[si, ji] += 1.0
                    else:
                        # Pick spans hour boundary — split proportionally
                        boundary = tg_hr  # start of the completion hour
                        secs_before = (boundary - row["arr_ts"]).total_seconds()
                        frac_before = secs_before / duration
                        frac_after  = 1.0 - frac_before
                        ji_before = hr_idx.get(arr_hr)
                        ji_after  = hr_idx.get(tg_hr)
                        if ji_before is not None:
                            effective_tasks_2d[si, ji_before] += frac_before
                        if ji_after is not None:
                            effective_tasks_2d[si, ji_after] += frac_after
                # Mark inactive cells as NaN
                effective_tasks_2d = np.where(raw == 0, np.nan, effective_tasks_2d)

                # ── Implied capacity: weighted avg pick time using the same proportional fractions ──
                # Each task contributes its full duration, weighted by the fraction of the
                # task attributed to that hour. This keeps capacity consistent with
                # effective_tasks so that a user can verify: eff_tasks / capacity = %.
                weighted_sum = np.zeros_like(raw, dtype=float)  # sum(frac * duration)
                weight_total = np.zeros_like(raw, dtype=float)  # sum(frac)
                for _, row in pick_d.iterrows():
                    si = ws_idx.get(row["station"])
                    if si is None:
                        continue
                    arr_hr = row["arr_ts"].floor("h")
                    tg_hr  = row["tg_ts"].floor("h")
                    duration = row["pick_s"]
                    if duration <= 0:
                        continue
                    if arr_hr == tg_hr:
                        ji = hr_idx.get(arr_hr)
                        if ji is not None:
                            weighted_sum[si, ji] += duration
                            weight_total[si, ji] += 1.0
                    else:
                        boundary = tg_hr
                        secs_before = (boundary - row["arr_ts"]).total_seconds()
                        frac_before = secs_before / duration
                        frac_after  = 1.0 - frac_before
                        ji_before = hr_idx.get(arr_hr)
                        ji_after  = hr_idx.get(tg_hr)
                        if ji_before is not None:
                            weighted_sum[si, ji_before] += frac_before * duration
                            weight_total[si, ji_before] += frac_before
                        if ji_after is not None:
                            weighted_sum[si, ji_after] += frac_after * duration
                            weight_total[si, ji_after] += frac_after
                avg_pick_2d = np.where(
                    weight_total > 0, weighted_sum / weight_total, np.nan,
                )
                capacity_2d = np.where(
                    ~np.isnan(avg_pick_2d),
                    3600.0 / (avg_pick_2d + 6.0),
                    np.nan,
                )
                capacity_2d = np.where(raw == 0, np.nan, capacity_2d)
        except Exception:
            pass

    nonzero_totals = totals[totals > 0]
    avg_tp  = float(nonzero_totals.mean()) if len(nonzero_totals) else 0.0
    peak_tp = float(totals.max()) if len(totals) else 0.0

    charts: list[dict] = []

    # ── Chart 1: total bar ───────────────────────────────────────────────────
    peak_idx    = int(totals.values.argmax()) if len(totals) else 0
    bar_colors  = [ACCENT if i == peak_idx else "#16213e" for i in range(len(totals))]

    fig1 = go.Figure()
    fig1.add_trace(go.Bar(
        x=hour_labels,
        y=totals.values,
        marker_color=bar_colors,
        hovertemplate="<b>%{x}</b><br>Tasks: %{y:,}<extra></extra>",
        name="Throughput",
    ))
    if design_total:
        fig1.add_hline(
            y=design_total, line_dash="dash", line_color="#f59e0b", line_width=2,
            annotation_text=f"Design capacity  {design_total:,}/hr",
            annotation_position="top right",
            annotation_font=dict(color="#f59e0b", size=11),
        )
    fig1.add_hline(
        y=avg_tp, line_dash="dot", line_color="#888888", line_width=1.5,
        annotation_text=f"Avg  {avg_tp:,.0f} /hr",
        annotation_position="bottom right",
        annotation_font=dict(color="#666666", size=11),
    )
    fig1.update_layout(
        title=dict(text="Total ASRS Throughput by Hour", x=0, pad=dict(l=12), font=dict(size=17, color=INK)),
        xaxis=dict(tickangle=-45, title="Hour"),
        yaxis=dict(title="Tasks / hr (all stations)", showgrid=True, gridcolor="#eeeeee"),
        showlegend=False,
        plot_bgcolor="white", paper_bgcolor="white",
        font=dict(color=INK, family="Inter, sans-serif"),
        margin=dict(t=70, b=90, l=70, r=40),
        hoverlabel=dict(bgcolor="white", bordercolor="#cccccc"),
    )
    fig1.add_annotation(
        xref="paper", yref="paper",
        x=0.99, y=0.97,
        text=f"Peak  <b>{peak_tp:,.0f}</b> /hr  ·  Avg  <b>{avg_tp:,.0f}</b> /hr",
        font=dict(size=11, color=INK),
        bgcolor="rgba(255,255,255,0.88)",
        bordercolor="#cccccc", borderwidth=1, borderpad=6,
        showarrow=False, align="right",
        xanchor="right", yanchor="top",
    )

    charts.append({
        "id":          "throughput_total",
        "title":       "Total Throughput by Hour",
        "figure":      fig1,
        "source":      "Labor station record (triggerGo events)",
        "method":      (
            "Hourly count of triggerGo events across all LABOR stations combined. "
            "triggerGo fires when the operator finishes picking and releases the robot — "
            "it is the true task-completion signal. "
            "The highlighted bar is the peak hour. "
            "If the design-capacity line is visible, hours that exceed it indicate the fleet "
            "was pushed beyond its rated throughput — sustained overrun here signals that "
            "the design rate may need revision or that the peak window needs load-smoothing."
        ),
        "export_hint": "throughput_by_workstation_hour.xlsx",
        "raw_data": {
            "description": "Total completions across all LABOR stations, per hour",
            "design_total_rate": design_total,
            "rows": [
                {"hour": h, "completions": int(totals.iloc[i])}
                for i, h in enumerate(hour_labels)
            ],
        },
    })

    # ── Chart 2: per-station heatmap + % of implied throughput toggle ─────────
    # When effective-tasks data is available, the default trace shows
    # Effective Tasks (capacity × active-time fraction).
    # Otherwise, fall back to raw triggerGo counts.
    use_effective = effective_tasks_2d is not None

    if use_effective:
        default_z = np.where(np.isnan(effective_tasks_2d), np.nan, effective_tasks_2d)
        text_default = [
            [
                f"{effective_tasks_2d[i, j]:.1f}"
                if not np.isnan(effective_tasks_2d[i, j])
                else ""
                for j in range(effective_tasks_2d.shape[1])
            ]
            for i in range(effective_tasks_2d.shape[0])
        ]
        nonzero_default = default_z[~np.isnan(default_z)]
        default_hover = "<b>%{y}</b><br>%{x}<br>Effective Tasks: %{z:.1f}<extra></extra>"
        default_cbar_title = "Eff. Tasks"
        default_chart_title = "Effective Tasks per Station per Hour"
    else:
        default_z = np.where(raw == 0, np.nan, raw)
        text_default = [
            [str(int(raw[i, j])) if raw[i, j] > 0 else "" for j in range(raw.shape[1])]
            for i in range(raw.shape[0])
        ]
        nonzero_default = raw[raw > 0]
        default_hover = "<b>%{y}</b><br>%{x}<br>Completions: %{z:.1f}<extra></extra>"
        default_cbar_title = "Tasks"
        default_chart_title = "Actual Completions per Station per Hour"

    overall_avg = float(np.mean(nonzero_default))   if len(nonzero_default) else 0.0
    overall_med = float(np.median(nonzero_default)) if len(nonzero_default) else 0.0

    # Text-label toggle (Actual / Actual+Target / % of Target) for configured design rates
    dr_toggle: list[dict] = []
    if design_rate:
        implied_row = np.array([design_rate.get(ws, 0) for ws in order], dtype=float)
        implied_mat = np.tile(implied_row[:, None], (1, raw.shape[1]))
        # Use effective tasks values when available for the text toggle
        display_vals = effective_tasks_2d if use_effective else raw
        text_vs = [
            [
                (
                    f"{display_vals[i, j]:.1f}/{int(implied_mat[i, j])}"
                    if use_effective
                    else f"{int(raw[i, j])}/{int(implied_mat[i, j])}"
                )
                if (not np.isnan(display_vals[i, j]) if use_effective else raw[i, j] > 0)
                and implied_mat[i, j] > 0
                else (
                    f"{display_vals[i, j]:.1f}" if use_effective and not np.isnan(display_vals[i, j])
                    else (str(int(raw[i, j])) if raw[i, j] > 0 else "")
                )
                for j in range(raw.shape[1])
            ]
            for i in range(raw.shape[0])
        ]
        pct_dr = np.where(
            implied_mat > 0,
            np.where(
                ~np.isnan(display_vals) if use_effective else raw > 0,
                display_vals / implied_mat * 100.0,
                np.nan,
            ),
            np.nan,
        )
        text_pct_dr = [
            [
                f"{pct_dr[i, j]:.0f}%" if not np.isnan(pct_dr[i, j]) else ""
                for j in range(pct_dr.shape[1])
            ]
            for i in range(pct_dr.shape[0])
        ]
        val_label = "Effective" if use_effective else "Actual"
        dr_toggle = [dict(
            type="buttons",
            direction="right",
            x=0.0, y=1.13,
            xanchor="left", yanchor="bottom",
            pad={"t": 4, "r": 4},
            bgcolor="#f8fafc",
            bordercolor="#e2e8f0",
            borderwidth=1,
            font=dict(size=11, color=INK),
            buttons=[
                dict(label=val_label,                method="restyle", args=[{"text": [text_default]}, [0]]),
                dict(label=f"{val_label} / Target",  method="restyle", args=[{"text": [text_vs]},      [0]]),
                dict(label="% of Target",            method="restyle", args=[{"text": [text_pct_dr]},  [0]]),
            ],
        )]

    fig2 = go.Figure()

    # Trace 0: default heatmap (effective tasks when available, raw counts otherwise)
    fig2.add_trace(go.Heatmap(
        z=default_z, x=hour_labels, y=order,
        colorscale=[
            [0.0, "#f7f7fb"], [0.25, "#5161a8"],
            [0.6, "#16213e"], [1.0, "#e94560"],
        ],
        text=text_default, texttemplate="%{text}", textfont=dict(size=7),
        hovertemplate=default_hover,
        colorbar=dict(title=default_cbar_title, thickness=14, len=0.8),
        visible=True,
    ))

    updatemenus: list[dict] = []

    # Trace 1: % of implied throughput (effective_tasks / capacity, capped at 100%)
    if util_pct_2d is not None:
        if use_effective and capacity_2d is not None:
            implied_pct_2d = np.where(
                ~np.isnan(effective_tasks_2d) & ~np.isnan(capacity_2d) & (capacity_2d > 0),
                np.minimum(effective_tasks_2d / capacity_2d * 100.0, 100.0),
                np.nan,
            )
        else:
            implied_pct_2d = np.where(raw == 0, np.nan, util_pct_2d)

        text_util = [
            [
                f"{implied_pct_2d[i, j]:.0f}%"
                if not np.isnan(implied_pct_2d[i, j])
                else ""
                for j in range(implied_pct_2d.shape[1])
            ]
            for i in range(implied_pct_2d.shape[0])
        ]
        _util_vals = implied_pct_2d[~np.isnan(implied_pct_2d)]
        util_avg = float(np.mean(_util_vals))   if len(_util_vals) else 0.0
        util_med = float(np.median(_util_vals)) if len(_util_vals) else 0.0
        fig2.add_trace(go.Heatmap(
            z=implied_pct_2d,
            x=hour_labels, y=order,
            colorscale=[
                [0.0,  "#ef4444"],
                [0.42, "#fbbf24"],
                [0.85, "#16a34a"],
                [1.0,  "#15803d"],
            ],
            zmin=0, zmax=100.0,
            text=text_util, texttemplate="%{text}", textfont=dict(size=8),
            hovertemplate=(
                "<b>%{y}</b><br>%{x}<br>"
                "% of implied throughput: %{z:.0f}%<extra></extra>"
            ),
            colorbar=dict(
                title="% Implied",
                thickness=14, len=0.8,
                tickvals=[0, 25, 50, 75, 100],
                ticktext=["0%", "25%", "50%", "75%", "100%"],
            ),
            visible=False,
        ))
        default_btn_label = "Effective Tasks" if use_effective else "Actual Count"
        updatemenus.append(dict(
            type="buttons", direction="right",
            x=1.0, y=1.10, xanchor="right", yanchor="bottom",
            showactive=True,
            buttons=[
                dict(
                    label=default_btn_label,
                    method="update",
                    args=[
                        {"visible": [True, False]},
                        {
                            "title.text": default_chart_title,
                            "annotations[0].text": (
                                f"Avg  <b>{overall_avg:,.1f}</b> /hr"
                                f"  ·  Median  <b>{overall_med:,.1f}</b> /hr"
                            ),
                        },
                    ],
                ),
                dict(
                    label="% of Implied Throughput",
                    method="update",
                    args=[
                        {"visible": [False, True]},
                        {
                            "title.text": "% of Implied Throughput per Station per Hour",
                            "annotations[0].text": (
                                f"Avg  <b>{util_avg:.0f}</b> %"
                                f"  ·  Median  <b>{util_med:.0f}</b> %"
                            ),
                        },
                    ],
                ),
            ],
            bgcolor="white", bordercolor="#cccccc",
            font=dict(color=INK, size=11),
            pad=dict(r=4, t=4),
        ))

    updatemenus.extend(dr_toggle)

    _heatmap_layout(fig2, hour_labels, default_chart_title)
    fig2.add_annotation(
        xref="paper", yref="paper",
        x=0.99, y=0.97,
        text=f"Avg  <b>{overall_avg:,.1f}</b> /hr  ·  Median  <b>{overall_med:,.1f}</b> /hr",
        font=dict(size=11, color=INK),
        bgcolor="rgba(255,255,255,0.88)",
        bordercolor="#cccccc", borderwidth=1, borderpad=6,
        showarrow=False, align="right",
        xanchor="right", yanchor="top",
    )
    if updatemenus:
        fig2.update_layout(
            updatemenus=updatemenus,
            margin=dict(t=150 if dr_toggle else 120, b=90, l=110, r=80),
        )

    _heatmap_rows = []
    for _i, _ws in enumerate(order):
        for _j, _h in enumerate(hour_labels):
            _row: dict = {"station": _ws, "hour": _h, "completions": int(raw[_i, _j])}
            if util_pct_2d is not None:
                _v = util_pct_2d[_i, _j]
                _row["active_time_pct"] = (
                    None if np.isnan(_v) else round(float(_v), 1)
                )
            if effective_tasks_2d is not None:
                _ev = effective_tasks_2d[_i, _j]
                _row["effective_tasks"] = (
                    None if np.isnan(_ev) else round(float(_ev), 1)
                )
            _heatmap_rows.append(_row)

    charts.append({
        "id":          "throughput_heatmap",
        "title":       "Effective Tasks per Station per Hour" if use_effective else "Throughput per Station per Hour (Heatmap)",
        "figure":      fig2,
        "source":      "Labor station record (arrived→triggerGo pairs for pick time; pick+switch occupancy for active time)",
        "method":      (
            (
                "Effective Tasks = actual completed tasks (arrived→triggerGo pairs) with "
                "proportional hour-boundary attribution. When a pick spans two hour buckets "
                "(e.g. arrived at 10:55, triggerGo at 11:03), credit is split proportionally "
                "by time spent in each hour (5/8 to 10:00, 3/8 to 11:00). "
                "The % of Implied Throughput toggle (top-right) shows Effective Tasks as a "
                "percentage of Implied Capacity, where Implied Capacity = 3 600 ÷ (mean pick "
                "time + 6 s) per station-hour, capped at 100 %. "
                "Dark cells are high-throughput station-hours; pale cells are low-activity periods."
            )
            if use_effective else
            (
                "triggerGo events per station per hour (operator done, robot released). "
                "Dark cells are busy station-hours; pale cells are low-activity periods. "
                "Look for columns where most stations go cold simultaneously — that is a system-wide "
                "supply gap, not a station problem. "
                "Rows that are consistently pale while others are dark indicate an uneven load "
                "distribution that may warrant rebalancing."
                + (
                    "  Toggle '% of Implied Throughput' (top-right) to view (pick + switch "
                    "time) ÷ 3 600 per station-hour — the fraction of the hour the station "
                    "was actively in use."
                    if util_pct_2d is not None else ""
                )
            )
        ),
        "export_hint": "throughput_by_workstation_hour.xlsx",
        "raw_data": {
            "description": "Effective tasks per station per hour (with active time % and raw completions)" if use_effective else "Completions per station per hour (and active time % where available)",
            "rows": _heatmap_rows,
        },
    })

    # ── Chart 3: Instantaneous picker rate (rolling window, tasks/hr) ──────────
    # For each station at each minute, count triggerGo events in the trailing
    # ROLL_MIN window and scale to an hourly rate — analogous to an annualised
    # return: "if this picker maintained their current pace for a full hour,
    # how many tasks would they complete?"
    ROLL_MIN = 10

    if all_hours is not None and len(tgo) > 0:
        day_start  = all_hours[0]
        day_end    = all_hours[-1] + pd.Timedelta(hours=1)
        minute_idx = pd.date_range(day_start, day_end, freq="min", inclusive="left")
        scale      = 60.0 / ROLL_MIN   # multiply rolling sum → tasks/hr

        zone_colors = _zone_colorscale(type_map, type_colors, order)

        fig_pr = go.Figure()
        for si, ws in enumerate(order):
            ws_tgo = tgo[tgo["station"] == ws]
            # Bin each triggerGo event into its floor-minute bucket
            per_min = (
                ws_tgo.assign(minute=ws_tgo["ts"].dt.floor("min"))
                .groupby("minute").size()
                .reindex(minute_idx, fill_value=0)
            )
            # Trailing window — min_periods=ROLL_MIN so the first ROLL_MIN minutes
            # of each shift are excluded rather than shown with an artificially
            # inflated rate from a partial window.
            rolling_rate = per_min.rolling(window=ROLL_MIN, min_periods=ROLL_MIN).sum() * scale

            # Convert NaN → None so Plotly renders gaps instead of zeroes
            y_vals = [None if np.isnan(v) else v for v in rolling_rate.tolist()]

            fig_pr.add_trace(go.Scatter(
                x=minute_idx.tolist(),
                y=y_vals,
                mode="lines",
                name=ws,
                line=dict(width=1.5, color=zone_colors[si]),
                connectgaps=False,
                hovertemplate=(
                    f"<b>{ws}</b><br>%{{x|%H:%M}}<br>"
                    f"Rate ({ROLL_MIN}-min rolling): %{{y:.0f}} tasks/hr<extra></extra>"
                ),
            ))

        # Initial view: first 4 hours; range slider exposes the rest
        init_end = day_start + pd.Timedelta(hours=4)
        fig_pr.update_layout(
            title=dict(
                text=f"Picker Instantaneous Rate — {ROLL_MIN}-min Rolling (tasks / hr)",
                x=0, pad=dict(l=12), font=dict(size=17, color=INK),
            ),
            xaxis=dict(
                range=[day_start.isoformat(), init_end.isoformat()],
                rangeslider=dict(visible=True, thickness=0.05),
                tickformat="%H:%M",
                tickangle=-45,
                tickfont=dict(size=9),
                title="Time of Day",
            ),
            yaxis=dict(
                title="Tasks / hr",
                showgrid=True,
                gridcolor="#eeeeee",
                rangemode="tozero",
            ),
            plot_bgcolor="white",
            paper_bgcolor="white",
            font=dict(color=INK, family="Inter, sans-serif"),
            margin=dict(t=70, b=140, l=70, r=40),
            hoverlabel=dict(bgcolor="white", bordercolor="#cccccc"),
            legend=dict(
                orientation="h",
                y=-0.30, x=0,
                font=dict(size=10),
                traceorder="normal",
            ),
        )

        charts.append({
            "id":          "throughput_picker_rate",
            "title":       f"Picker Instantaneous Rate — {ROLL_MIN}-min Rolling (tasks / hr)",
            "figure":      fig_pr,
            "source":      "Labor station record (triggerGo events)",
            "method":      (
                f"For each station (proxy for one operator), the chart plots the "
                f"{ROLL_MIN}-minute trailing count of triggerGo events scaled to an hourly "
                f"rate (× {60 // ROLL_MIN}): the operator's implied productivity if they "
                f"sustained their current pace for a full hour. "
                f"The x-axis is minute-resolution across the full day. "
                f"The rolling window requires {ROLL_MIN} full minutes of data before producing "
                f"a value, so the line only appears from 00:{ROLL_MIN:02d} onward — this avoids "
                f"inflated rates from partial windows at the start of the day. "
                f"Idle periods (no picks in the trailing {ROLL_MIN} minutes) appear as the line "
                f"dropping to 0, not as gaps. "
                f"Use the range slider below the chart to scroll across the full shift. "
                f"Lines are coloured by zone type."
            ),
            "export_hint": "",
        })

    # ── Chart 4: % of design rate (only if design_rate is set) ──────────────
    if design_rate:
        pct = np.full_like(raw, np.nan)
        for i, ws in enumerate(order):
            dr = design_rate.get(ws)
            if dr:
                pct[i] = np.where(raw[i] > 0, raw[i] / dr * 100.0, np.nan)

        text3 = [
            [
                f"{pct[i, j]:.0f}%" if not np.isnan(pct[i, j]) else ""
                for j in range(pct.shape[1])
            ]
            for i in range(pct.shape[0])
        ]

        fig3 = go.Figure(go.Heatmap(
            z=pct, x=hour_labels, y=order,
            colorscale=[
                [0.0,  "#f1f5ff"], [0.33, "#93c5fd"],
                [0.50, "#1d4ed8"], [0.67, "#15803d"],
                [0.85, "#fbbf24"], [1.0,  "#ef4444"],
            ],
            zmin=0, zmax=150,
            text=text3, texttemplate="%{text}", textfont=dict(size=8),
            hovertemplate="<b>%{y}</b><br>%{x}<br>Utilisation: %{z:.0f}%<extra></extra>",
            colorbar=dict(
                title="% design",
                thickness=14, len=0.8,
                tickvals=[0, 50, 100, 150],
                ticktext=["0 %", "50 %", "100 %", "≥150 %"],
            ),
        ))
        _heatmap_layout(fig3, hour_labels, "Station Utilisation vs Design Rate (%)")

        charts.append({
            "id":          "throughput_utilisation",
            "title":       "Station Utilisation vs Design Rate (%)",
            "figure":      fig3,
            "source":      "Labor station record (triggerGo events)",
            "method":      (
                "Actual completions as a percentage of each station's configured design rate. "
                "Green cells (~100 %) are operating as intended. "
                "Red cells (>100 %) indicate the station exceeded its design target — check "
                "whether this is sustainable or a sign that the target is underspecified. "
                "Pale blue cells (<50 %) point to underutilisation: the station had capacity "
                "but was not being fed enough work, suggesting an upstream supply constraint."
            ),
            "export_hint": "throughput_by_workstation_hour.xlsx",
            "raw_data": {
                "description": "Actual completions vs design rate (%) per station per hour",
                "rows": [
                    {
                        "station": order[_i],
                        "hour": hour_labels[_j],
                        "completions": int(raw[_i, _j]),
                        "design_rate": design_rate.get(order[_i]),
                        "utilisation_pct": (
                            None if np.isnan(pct[_i, _j]) else round(float(pct[_i, _j]), 1)
                        ),
                    }
                    for _i in range(len(order))
                    for _j in range(raw.shape[1])
                ],
            },
        })

    return charts
