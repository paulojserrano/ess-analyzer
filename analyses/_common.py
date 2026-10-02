"""
analyses/_common.py — small helpers shared by several analysis modules.

Keeping these in one place guarantees that every chart (and the Excel
exports) count the same events the same way.
"""
from __future__ import annotations

import re

import pandas as pd


def natural_key(name) -> tuple:
    """Sort key that orders 'LABOR-2' before 'LABOR-10'."""
    return tuple(int(t) if t.isdigit() else t for t in re.split(r"(\d+)", str(name)))


def station_events(lsr: pd.DataFrame, cfg: dict, amr_only: bool = True) -> pd.DataFrame:
    """Station-sheet rows with parsed `ts` and mapped `station` columns.

    Rows with unparseable timestamps are dropped.  With ``amr_only`` the rows
    are restricted to the delivery AMR type (cfg["amr_type"]) — the robots
    that actually serve the operator — exactly as throughput and pick time do.
    """
    d = lsr.copy()
    d["ts"] = pd.to_datetime(d["时间戳"], errors="coerce")
    d["station"] = d["位置编号"].map(cfg.get("point2ws", {}))
    amr_type = cfg.get("amr_type")
    if amr_only and amr_type and "机器人类型" in d.columns:
        d = d[d["机器人类型"] == amr_type]
    return d.dropna(subset=["ts"])


def triggergo_completions(lsr: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Task completions = AMR 'triggerGo' events at a configured LABOR station.

    This is the single completion definition used by the throughput charts,
    the cross-day summary, the data-quality panel and the Excel exports.
    Returns the station-event rows (ts, station, …) for those completions.
    """
    d = station_events(lsr, cfg)
    tgo = d[(d["事件类型"] == "triggerGo") & d["station"].notna()]
    ws_order = cfg.get("ws_order") or []
    if ws_order:
        tgo = tgo[tgo["station"].isin(ws_order)]
    return tgo
