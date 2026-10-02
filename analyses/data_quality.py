"""
analyses/data_quality.py — data-quality panel and pick-time cross-validation.

Several analyses silently absorb data problems (unpaired events, missing
timestamps, dropped outliers, disagreeing completion counts).  This module
surfaces them so report readers know how much to trust each chart, and
cross-validates the two independent pick-time measurements the dataset
carries: arrived→triggerGo from the station sheet vs 拣选耗时(秒) from the
lifecycle sheet.

Charts produced
---------------
1. dq_panel          — table of per-sheet row counts, missing/unparseable
                       fields, pairing anomalies, and completion-count
                       consistency.
2. dq_pick_crossval  — scatter of lifecycle picking duration vs station-sheet
                       pick time for the same task (matched per robot by
                       completion timestamp).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from config import ACCENT, INK, TOTAL_DURATION_COL
from analyses._common import triggergo_completions
from analyses.dwell_time import extract_picks


def _find_col(df: pd.DataFrame, needle: str) -> str | None:
    return next((c for c in df.columns if needle in str(c)), None)


def _pct(part: int, whole: int) -> str:
    return f"{part / whole * 100:.1f}%" if whole else "—"


def _pairing_stats(lsr: pd.DataFrame, cfg: dict) -> dict:
    """Count arrived/triggerGo pairing anomalies on the AMR-filtered event
    stream (the same stream every pick-based chart uses)."""
    d = lsr.copy()
    d["ts"]      = pd.to_datetime(d["时间戳"], errors="coerce")
    d["station"] = d["位置编号"].map(cfg["point2ws"])
    amr_type = cfg.get("amr_type")
    if amr_type and "机器人类型" in d.columns:
        d = d[d["机器人类型"] == amr_type]
    d = d.dropna(subset=["ts"]).sort_values(["机器人编号", "ts"])

    stats = {
        "arrived": 0, "triggergo": 0,
        "orphan_arrived": 0,       # arrived overwritten by another arrived / never closed
        "orphan_triggergo": 0,     # triggerGo with no open arrived
        "station_mismatch": 0,     # pair whose triggerGo is at a different station
    }
    for _rb, sub in d.groupby("机器人编号"):
        arr = arr_loc = None
        for ts, et, loc in sub[["ts", "事件类型", "station"]].values:
            if et == "arrived":
                if arr is not None:
                    stats["orphan_arrived"] += 1
                arr, arr_loc = ts, loc
                stats["arrived"] += 1
            elif et == "triggerGo":
                stats["triggergo"] += 1
                if arr is None:
                    stats["orphan_triggergo"] += 1
                else:
                    if pd.notna(loc) and pd.notna(arr_loc) and loc != arr_loc:
                        stats["station_mismatch"] += 1
                    arr = None
        if arr is not None:
            stats["orphan_arrived"] += 1
    return stats


def _dq_table(data: dict, cfg: dict) -> dict:
    cb  = data.get("callback")
    lsr = data.get("station")
    tlc = data.get("lifecycle")

    rows: list[tuple[str, str, str]] = []   # (metric, value, note)

    # ── per-sheet row counts + timestamp parseability ─────────────────────────
    for key, df, ts_needle in (
        ("callback",  cb,  "时间戳"),
        ("station",   lsr, "时间戳"),
        ("lifecycle", tlc, "complete("),
    ):
        if df is None:
            rows.append((f"{key} sheet", "absent", "analyses needing it are skipped"))
            continue
        ts_col = _find_col(df, ts_needle)
        if ts_col is not None:
            bad = int(pd.to_datetime(df[ts_col], errors="coerce").isna().sum())
            rows.append((
                f"{key} sheet rows", f"{len(df):,}",
                f"{_pct(bad, len(df))} unparseable timestamps in '{ts_col}'",
            ))
        else:
            rows.append((f"{key} sheet rows", f"{len(df):,}", "no timestamp column found"))

    # ── lifecycle field coverage ──────────────────────────────────────────────
    if tlc is not None:
        create_col = _find_col(tlc, "创建时间")
        if create_col is not None:
            miss = int(pd.to_datetime(tlc[create_col], errors="coerce").isna().sum())
            rows.append((
                "lifecycle: missing 创建时间", f"{miss:,} ({_pct(miss, len(tlc))})",
                "backlog-depth chart is a lower bound when high",
            ))
        if TOTAL_DURATION_COL in tlc.columns:
            dur = pd.to_numeric(tlc[TOTAL_DURATION_COL], errors="coerce")
            miss_dur = int(dur.isna().sum())
            over_2h  = int((dur >= 7200).sum())
            rows.append((
                "lifecycle: missing total duration",
                f"{miss_dur:,} ({_pct(miss_dur, len(tlc))})",
                "these tasks are invisible to all cycle-time charts",
            ))
            rows.append((
                "lifecycle: tasks over 2 h", f"{over_2h:,}",
                "excluded from cycle-time statistics as outliers",
            ))

    # ── station-sheet pairing anomalies ───────────────────────────────────────
    tgo_at_stations: int | None = None
    if lsr is not None and cfg.get("point2ws"):
        ps = _pairing_stats(lsr, cfg)
        rows.append((
            "station: arrived events (AMR)", f"{ps['arrived']:,}",
            f"{ps['orphan_arrived']:,} unpaired ({_pct(ps['orphan_arrived'], ps['arrived'])}) — no matching triggerGo",
        ))
        rows.append((
            "station: triggerGo events (AMR)", f"{ps['triggergo']:,}",
            f"{ps['orphan_triggergo']:,} without a preceding arrived",
        ))
        rows.append((
            "station: cross-station pairs dropped", f"{ps['station_mismatch']:,}",
            "arrival and triggerGo at different stations (lost events)",
        ))

        # Same completion definition as the throughput charts (AMR triggerGo
        # at a configured station).
        tgo_at_stations = int(len(triggergo_completions(lsr, cfg)))

    # ── completion-count consistency across sheets ────────────────────────────
    if cb is not None and tgo_at_stations is not None:
        act_col = _find_col(cb, "动作类型")
        loc_col = _find_col(cb, "位置类型")
        if act_col and loc_col:
            is_done = (cb[act_col] == "complete") & cb[loc_col].astype(str).str.startswith("LABOR")
            # Return (restock) tasks are not outbound picks — triggerGo never
            # counts them, so exclude them here for a like-for-like comparison.
            task_col = _find_col(cb, "任务编号")
            if task_col is not None:
                is_done &= ~cb[task_col].astype(str).str.startswith("return")
            cb_completes = int(is_done.sum())
            if max(tgo_at_stations, cb_completes) > 0:
                diff_pct = (
                    abs(tgo_at_stations - cb_completes)
                    / max(tgo_at_stations, cb_completes) * 100
                )
                rows.append((
                    "completion consistency",
                    f"triggerGo {tgo_at_stations:,} vs callback complete {cb_completes:,}",
                    f"{diff_pct:.1f}% disagreement between the two completion definitions",
                ))

    fig = go.Figure(go.Table(
        header=dict(
            values=["<b>Metric</b>", "<b>Value</b>", "<b>Implication</b>"],
            fill_color="#16213e",
            font=dict(color="white", size=12),
            align="left",
            height=30,
        ),
        cells=dict(
            values=[
                [r[0] for r in rows],
                [r[1] for r in rows],
                [r[2] for r in rows],
            ],
            fill_color=[["#f8fafc", "#ffffff"] * ((len(rows) + 1) // 2)],
            font=dict(color=INK, size=11),
            align="left",
            height=26,
        ),
        columnwidth=[0.30, 0.28, 0.42],
    ))
    fig.update_layout(
        title=dict(
            text="Data Quality Panel",
            x=0, pad=dict(l=12), font=dict(size=17, color=INK),
        ),
        paper_bgcolor="white",
        font=dict(color=INK, family="Inter, sans-serif"),
        margin=dict(t=70, b=30, l=20, r=20),
    )
    return {
        "id":          "dq_panel",
        "title":       "Data Quality Panel",
        "figure":      fig,
        "source":      "All loaded sheets",
        "method":      (
            "Per-sheet integrity metrics that other charts absorb silently: row counts and "
            "unparseable timestamps; lifecycle field coverage (missing creation timestamps, "
            "missing durations, over-2 h outliers dropped from cycle statistics); station "
            "event-pairing anomalies on the AMR stream (unpaired arrivals, orphan "
            "triggerGos, cross-station pairs); and the disagreement between the two "
            "completion definitions (station-sheet triggerGo vs callback complete-at-LABOR). "
            "Small anomaly rates (< 2 %) are normal logging noise. Large values mean the "
            "affected charts understate activity — check the Implication column for which "
            "chart each metric feeds."
        ),
        "export_hint": "",
        "raw_data": {
            "description": "Data-quality metrics",
            "rows": [
                {"metric": r[0], "value": r[1], "implication": r[2]} for r in rows
            ],
        },
    }


def _pick_crossval(data: dict, cfg: dict) -> dict | None:
    """Match lifecycle picking durations to station-sheet pick times per robot
    (nearest completion timestamps within 90 s) and compare the two."""
    tlc = data.get("lifecycle")
    lsr = data.get("station")
    if tlc is None or lsr is None:
        return None

    robot_col  = _find_col(tlc, "机器人编号")
    pickdur_col = _find_col(tlc, "拣选耗时")
    finish_col  = _find_col(tlc, "拣选完成时间")
    if not (robot_col and pickdur_col and finish_col):
        return None

    life = tlc[[robot_col, pickdur_col, finish_col]].copy()
    life.columns = ["robot", "life_pick_s", "finish_ts"]
    life["robot"]       = life["robot"].astype(str)
    life["life_pick_s"] = pd.to_numeric(life["life_pick_s"], errors="coerce")
    life["finish_ts"]   = pd.to_datetime(life["finish_ts"], errors="coerce")
    life = life.dropna()
    life = life[(life["life_pick_s"] > 0) & (life["life_pick_s"] < 3600)]
    if life.empty:
        return None

    picks = extract_picks(lsr, cfg)
    picks = picks[picks["pick_s"] > 0]
    if picks.empty:
        return None

    merged = pd.merge_asof(
        life.sort_values("finish_ts"),
        picks[["robot", "tg_ts", "pick_s", "station"]].sort_values("tg_ts"),
        left_on="finish_ts", right_on="tg_ts",
        by="robot",
        direction="nearest",
        tolerance=pd.Timedelta(seconds=90),
    ).dropna(subset=["pick_s"])

    if len(merged) < 30:
        return None

    x = merged["life_pick_s"].values.astype(float)
    y = merged["pick_s"].values.astype(float)
    diff = y - x
    med_abs = float(np.median(np.abs(diff)))
    med_bias = float(np.median(diff))
    r = float(np.corrcoef(x, y)[0, 1]) if len(x) > 2 else float("nan")

    lim = float(np.percentile(np.concatenate([x, y]), 99))

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=[0, lim], y=[0, lim], mode="lines",
        line=dict(color="#999999", dash="dot", width=1.5),
        name="Perfect agreement (y = x)",
    ))
    fig.add_trace(go.Scatter(
        x=x, y=y, mode="markers",
        marker=dict(size=5, color="#2563eb", opacity=0.45,
                    line=dict(width=0.3, color="white")),
        customdata=merged["station"].astype(str).values,
        name="Matched task",
        hovertemplate=(
            "<b>%{customdata}</b><br>"
            "Lifecycle 拣选耗时: %{x:.0f} s<br>"
            "Station arrived→triggerGo: %{y:.0f} s<extra></extra>"
        ),
    ))
    fig.update_layout(
        title=dict(
            text="Pick Time Cross-Validation — Two Independent Measurements",
            x=0, pad=dict(l=12), font=dict(size=17, color=INK),
        ),
        xaxis=dict(title="Lifecycle picking duration 拣选耗时 (s)",
                   range=[0, lim], showgrid=True, gridcolor="#f0f0f0"),
        yaxis=dict(title="Station-sheet pick time arrived→triggerGo (s)",
                   range=[0, lim], showgrid=True, gridcolor="#f0f0f0"),
        legend=dict(orientation="h", y=1.08, x=1, xanchor="right", font=dict(size=10)),
        plot_bgcolor="white", paper_bgcolor="white",
        font=dict(color=INK, family="Inter, sans-serif"),
        margin=dict(t=80, b=100, l=80, r=40),
        hoverlabel=dict(bgcolor="white", bordercolor="#cccccc"),
        annotations=[dict(
            xref="paper", yref="paper", x=0, y=-0.18,
            text=(
                f"{len(merged):,} tasks matched per robot within ±90 s of completion.  "
                f"Median |difference| = <b>{med_abs:.0f} s</b>;  "
                f"median bias (station − lifecycle) = <b>{med_bias:+.0f} s</b>;  r = {r:.2f}.  "
                "Station time includes robot positioning before picking starts, so a small "
                "positive bias is expected."
            ),
            font=dict(size=9, color="#666"), showarrow=False, align="left",
        )],
    )
    return {
        "id":          "dq_pick_crossval",
        "title":       "Pick Time Cross-Validation",
        "figure":      fig,
        "source":      "Task lifecycle sheet + station record sheet",
        "method":      (
            "The dataset measures picking twice and independently: the lifecycle sheet logs "
            "拣选耗时 (picking duration) per task, and the station sheet implies pick time "
            "from arrived→triggerGo. Tasks are matched per robot by nearest completion "
            "timestamps (±90 s tolerance) and plotted against each other. "
            "Points hugging the y = x line mean the pick-time family of charts "
            "(dwell heatmap, implied throughput, utilisation %) rests on trustworthy data. "
            "A consistent vertical offset means the station measurement includes fixed "
            "overhead (robot positioning) — subtract it mentally when comparing to operator "
            "standards. A cloud with low correlation means at least one of the two "
            "measurements is unreliable and pick-time conclusions should be treated "
            "with caution."
        ),
        "export_hint": "",
        "raw_data": {
            "description": "Matched pick-time pairs (lifecycle vs station sheet)",
            "n_matched": int(len(merged)),
            "median_abs_diff_s": round(med_abs, 1),
            "median_bias_s": round(med_bias, 1),
            "pearson_r": round(r, 3) if not np.isnan(r) else None,
            "rows": [
                {
                    "station": str(s_),
                    "lifecycle_pick_s": round(float(a), 1),
                    "station_pick_s": round(float(b), 1),
                }
                for s_, a, b in zip(
                    merged["station"].values, x, y,
                )
            ],
        },
    }


# ── public entry point ────────────────────────────────────────────────────────

def run(data: dict, cfg: dict) -> list[dict]:
    charts: list[dict] = [_dq_table(data, cfg)]

    xval = _pick_crossval(data, cfg)
    if xval is not None:
        charts.append(xval)

    return charts
