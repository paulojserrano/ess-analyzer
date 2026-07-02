"""
analyses/station_readiness.py — who waits for whom at the workstations.

The station sheet's 'ppReady' event (put-point ready, logged per robot with a
blank location) signals that the put point was ready for the robot's
container.  Its position relative to the robot's 'arrived' event decomposes
every pick cycle into one of two regimes:

  • ppReady BEFORE arrived  → the station was ready and waiting; the robot
                              (fleet supply / travel) was the constraint.
  • ppReady AFTER  arrived  → the robot stood at the station waiting for the
                              put point; the station side was the constraint.

This is a *direct* measurement of the operator-vs-robot question that the
pick-time and starvation charts can only approach indirectly.

Charts produced
---------------
1. readiness_who_waits — hourly share of cycles in each regime, with median
                         wait durations.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from config import ACCENT, INK

_MIN_PPREADY_EVENTS = 50    # below this the signal is too sparse to chart
_MATCH_WINDOW_S     = 600.0  # ppReady more than 10 min from the cycle is unrelated


# ── public entry point ────────────────────────────────────────────────────────

def run(data: dict, cfg: dict) -> list[dict]:
    lsr = data.get("station")
    if lsr is None or "事件类型" not in lsr.columns:
        return []

    if int((lsr["事件类型"] == "ppReady").sum()) < _MIN_PPREADY_EVENTS:
        return []

    d = lsr.copy()
    d["ts"]      = pd.to_datetime(d["时间戳"], errors="coerce")
    d["station"] = d["位置编号"].map(cfg["point2ws"])
    d = d.dropna(subset=["ts"]).sort_values(["机器人编号", "ts"])

    # Classify each arrived→triggerGo cycle by the nearest ppReady of the same
    # robot within the match window.
    cycles: list[dict] = []
    for _rb, sub in d.groupby("机器人编号"):
        last_pp = None
        arr = arr_loc = None
        pp_in_cycle = None
        for ts, et, loc in sub[["ts", "事件类型", "station"]].values:
            if et == "ppReady":
                if arr is not None and pp_in_cycle is None:
                    pp_in_cycle = ts
                else:
                    last_pp = ts
            elif et == "arrived":
                arr, arr_loc = ts, loc
                pp_in_cycle = None
            elif et == "triggerGo" and arr is not None:
                rec: dict | None = None
                if pp_in_cycle is not None and (pp_in_cycle - arr).total_seconds() <= _MATCH_WINDOW_S:
                    # put point became ready only after the robot arrived
                    rec = {
                        "regime": "station_wait",
                        "wait_s": (pp_in_cycle - arr).total_seconds(),
                    }
                elif last_pp is not None and 0 <= (arr - last_pp).total_seconds() <= _MATCH_WINDOW_S:
                    # put point was ready before the robot arrived
                    rec = {
                        "regime": "robot_wait",
                        "wait_s": (arr - last_pp).total_seconds(),
                    }
                if rec is not None and pd.notna(arr_loc):
                    rec["station"] = arr_loc
                    rec["hour"]    = arr.floor("h")
                    cycles.append(rec)
                arr = None
                last_pp = None
                pp_in_cycle = None

    if len(cycles) < _MIN_PPREADY_EVENTS:
        return []

    cy = pd.DataFrame(cycles)
    n_total   = len(cy)
    n_robot   = int((cy["regime"] == "robot_wait").sum())
    n_station = int((cy["regime"] == "station_wait").sum())

    med_robot_lead   = float(cy.loc[cy["regime"] == "robot_wait",   "wait_s"].median()) if n_robot   else 0.0
    med_station_wait = float(cy.loc[cy["regime"] == "station_wait", "wait_s"].median()) if n_station else 0.0

    hourly = (
        cy.groupby(["hour", "regime"]).size()
        .unstack(fill_value=0)
        .reindex(columns=["robot_wait", "station_wait"], fill_value=0)
    )
    hourly["total"]     = hourly.sum(axis=1)
    hourly["robot_pct"] = hourly["robot_wait"] / hourly["total"] * 100.0

    hl = [h.strftime("%H:00") for h in hourly.index]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=hl, y=hourly["robot_wait"].values,
        marker_color="#2563eb",
        name="Station ready first (robot was the constraint)",
        hovertemplate="<b>%{x}</b><br>Cycles where the robot arrived late: %{y:,}<extra></extra>",
    ))
    fig.add_trace(go.Bar(
        x=hl, y=hourly["station_wait"].values,
        marker_color="#f59e0b",
        name="Robot waited for the put point (station was the constraint)",
        hovertemplate="<b>%{x}</b><br>Cycles where the robot waited: %{y:,}<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=hl, y=hourly["robot_pct"].round(1).values,
        mode="lines+markers", yaxis="y2",
        line=dict(color=INK, width=2, dash="dot"), marker=dict(size=5),
        name="% robot-constrained",
        hovertemplate="<b>%{x}</b><br>Robot-constrained: %{y:.0f}%<extra></extra>",
    ))
    fig.update_layout(
        barmode="stack",
        title=dict(
            text="Who Waits for Whom? — ppReady vs Robot Arrival",
            x=0, pad=dict(l=12), font=dict(size=17, color=INK),
        ),
        xaxis=dict(title="Hour", tickangle=-45, tickfont=dict(size=9)),
        yaxis=dict(title="Classified pick cycles", showgrid=True,
                   gridcolor="#eeeeee", rangemode="tozero"),
        yaxis2=dict(title="% robot-constrained", overlaying="y", side="right",
                    range=[0, 100], showgrid=False),
        legend=dict(orientation="h", y=-0.28, x=0, font=dict(size=10)),
        plot_bgcolor="white", paper_bgcolor="white",
        font=dict(color=INK, family="Inter, sans-serif"),
        margin=dict(t=80, b=130, l=70, r=70),
        hoverlabel=dict(bgcolor="white", bordercolor="#cccccc"),
    )
    fig.add_annotation(
        xref="paper", yref="paper", x=0.99, y=0.97,
        text=(
            f"Robot-constrained: <b>{n_robot / n_total * 100:.0f}%</b> "
            f"(station led by median {med_robot_lead:.0f} s)<br>"
            f"Station-constrained: <b>{n_station / n_total * 100:.0f}%</b> "
            f"(robot waited median {med_station_wait:.0f} s)"
        ),
        font=dict(size=11, color=INK),
        bgcolor="rgba(255,255,255,0.88)",
        bordercolor="#cccccc", borderwidth=1, borderpad=6,
        showarrow=False, align="right", xanchor="right", yanchor="top",
    )

    return [{
        "id":          "readiness_who_waits",
        "title":       "Who Waits for Whom? — ppReady vs Robot Arrival",
        "figure":      fig,
        "source":      "Station record sheet (ppReady / arrived / triggerGo events)",
        "method":      (
            "Each arrived→triggerGo pick cycle is classified by the robot's nearest "
            "ppReady (put-point ready) event within 10 minutes: if ppReady preceded the "
            "robot's arrival, the station was ready first and the robot / fleet supply was "
            "the binding constraint for that cycle (blue); if ppReady fired only after the "
            "robot arrived, the robot stood waiting for the put point (amber). "
            "Cycles with no nearby ppReady are excluded "
            f"({n_total:,} classified). "
            "The dotted line is the hourly robot-constrained share: sustained values above "
            "~50 % mean adding robots or shortening travel would raise throughput; low "
            "values mean the put-point side (operator or downstream take-away) is the "
            "limiter and fleet investment would be wasted. "
            "Caveat: ppReady semantics are inferred from event ordering — validate against "
            "a few known cycles before acting on absolute percentages; the hour-to-hour "
            "trend is robust either way."
        ),
        "export_hint": "",
        "raw_data": {
            "description": "Hourly classified pick cycles by binding constraint",
            "n_classified": n_total,
            "robot_constrained_total": n_robot,
            "station_constrained_total": n_station,
            "median_station_lead_s": round(med_robot_lead, 1),
            "median_robot_wait_s": round(med_station_wait, 1),
            "rows": [
                {
                    "hour": h,
                    "robot_constrained": int(hourly["robot_wait"].iloc[i]),
                    "station_constrained": int(hourly["station_wait"].iloc[i]),
                    "robot_constrained_pct": round(float(hourly["robot_pct"].iloc[i]), 1),
                }
                for i, h in enumerate(hl)
            ],
        },
    }]
