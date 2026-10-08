"""
report.py — write the single-file report.

    build_payload(bases, settings)       -> dict   (the REPORT object the page reads)
    write_report(bases, settings, path)  -> path

One HTML file holds every day.  A left-hand panel switches between the
cross-day summary and each day; the door seconds and station targets can be
changed at the top of the page and everything recalculates in the browser
(templates/engine.js).  Styles, scripts and data are all inlined, so the file
works offline and can be mailed or archived on its own.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
from datetime import datetime

import numpy as np

from config import (
    AWAY_MIN_S,
    FULL_HOUR_SHARE,
    HIGH_RATE_SHARE,
    LONG_PICK_S,
    MAX_SWITCH_S,
    SWITCH_HIST_BINS,
    SWITCH_HIST_W,
    Settings,
)
from log_parser import natural_key
from metrics import zones_for

TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
REPORT_NAME = "station_robot_cycle_report.html"


def _template(name: str) -> str:
    with open(os.path.join(TEMPLATE_DIR, name), encoding="utf-8") as fh:
        return fh.read()


def _payload_json(obj) -> str:
    """JSON for inlining in a <script> block."""
    text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str)
    return text.replace("</", "<\\/")       # never let content close the script tag


def _zone_list(zones: dict[str, str]) -> list[dict]:
    by_zone: dict[str, list[str]] = {}
    for st, z in zones.items():
        by_zone.setdefault(z, []).append(st)
    return [{"zone": z, "stations": sorted(sts, key=natural_key)}
            for z, sts in sorted(by_zone.items(), key=lambda kv: natural_key(kv[0]))]


def default_targets(bases: list[dict], zones: dict[str, str], s: Settings) -> dict[str, float]:
    """The targets the report opens with.

    Explicit entries (by station or by zone) come from the settings.  Every
    zone without one gets ``s.target_rate`` if it is a high-rate zone — one
    whose stations average at least HIGH_RATE_SHARE of the busiest zone's
    totes per full-production hour — and no target otherwise.  Slow zones
    (different kinds of work) are left for the user to set, rather than being
    judged against a rate they were never meant to reach.
    """
    targets = {k: float(v) for k, v in s.targets.items()}
    rates: dict[str, list[float]] = {}
    for b in bases:
        for row in b["station_table"]:
            if row.get("rate_full") is not None:
                rates.setdefault(zones.get(row["station"], row["station"]), []).append(row["rate_full"])
    zone_rate = {z: float(np.mean(v)) for z, v in rates.items() if v}
    if not zone_rate:
        return targets
    busiest = max(zone_rate.values())
    for z, rate in zone_rate.items():
        if z not in targets and rate >= HIGH_RATE_SHARE * busiest:
            targets[z] = float(s.target_rate)
    return targets


def build_payload(bases: list[dict], s: Settings) -> dict:
    bases = sorted(bases, key=lambda b: b["date"])
    zones = zones_for(bases)
    stations = sorted({st for b in bases for st in b["stations"]}, key=natural_key)
    defaults = {"door_s": float(s.door_s), "targets": default_targets(bases, zones, s),
                "pick_s": {k: float(v) for k, v in s.pick_s.items()},
                "switch_s": {k: float(v) for k, v in s.switch_s.items()},
                "no_door": {k: bool(v) for k, v in s.no_door.items()},
                "no_door_days": {d: True for d in s.no_door_days}}
    ident = hashlib.sha1(json.dumps(
        [[b["date"], b["source"]] for b in bases] + [defaults], sort_keys=True).encode()).hexdigest()[:12]
    return {
        "id": ident,
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "target_rate": float(s.target_rate),
        "constants": {
            "full_hour_share": FULL_HOUR_SHARE, "long_pick_s": LONG_PICK_S,
            "max_switch_s": MAX_SWITCH_S, "switch_hist_bins": SWITCH_HIST_BINS,
            "switch_hist_w": SWITCH_HIST_W, "away_min_s": AWAY_MIN_S,
        },
        "defaults": defaults,
        "zones": zones,
        "zone_list": _zone_list(zones),
        "stations": stations,
        "hours": list(range(24)),
        "days": bases,
    }


def _title(payload: dict) -> str:
    days = payload["days"]
    fmt = lambda iso: datetime.strptime(iso, "%Y-%m-%d")
    if not days:
        return "Station & robot cycle analysis"
    a, b = fmt(days[0]["date"]), fmt(days[-1]["date"])
    if len(days) == 1:
        return f"Station & robot cycle analysis, {a.day} {a:%b %Y}"
    return f"Station & robot cycle analysis, {a.day} {a:%b} – {b.day} {b:%b %Y}"


def write_report(bases: list[dict], s: Settings, path: str) -> str:
    """Write the report to *path* and return it."""
    payload = build_payload(bases, s)
    page = _template("report.html")
    slots = {
        "__TITLE__": html.escape(_title(payload)),
        "__STYLE__": _template("report.css"),
        "__ENGINE__": _template("engine.js"),
        "__APP__": _template("report.js"),
        "__DATA__": _payload_json(payload),
    }
    # Fill the data last: it is user content and could contain a slot name.
    for key in ("__TITLE__", "__STYLE__", "__ENGINE__", "__APP__"):
        page = page.replace(key, slots[key])
    page = page.replace("__DATA__", slots["__DATA__"], 1)

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(page)
    return path
