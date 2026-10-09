"""
config.py — run settings and the few constants the analysis depends on.

The report recalculates live in the browser, so ``door_s`` and the targets set
here are only its starting values; they can be changed in the report itself.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field

# ── Door travel ───────────────────────────────────────────────────────────────
# The shutter-door open command is issued in the same millisecond as the robot's
# arrival, so the door's physical travel never appears in the log.  DOOR_S is
# added to every measured release→arrival gap to give the true switch time, and
# the same seconds are taken off the front of each visit's pick window (the door
# is still opening, so the operator is not picking yet).  0 means "as logged".
DOOR_S_DEFAULT = 0.0
DOOR_S_MAX = 60.0

# ── Starvation ────────────────────────────────────────────────────────────────
# A station is "starved" on a handover when it waits for the next robot more than
# this many seconds beyond its median handover (release → next arrival).
STARVED_S_DEFAULT = 1.0
STARVED_S_MAX = 600.0
# A robot held at a station this long before its release means the station was
# closed (a break, a shift change — the log shows every station released in the
# same second afterwards).  The gap that follows is the station reopening, not
# starvation, and is reported apart from it.
CLOSED_HOLD_S = 600.0

# ── Targets ───────────────────────────────────────────────────────────────────
# Totes a station is expected to present per hour.  Targets can be set per
# station or per zone (a row of stations — see log_parser.assign_zones).  Zones
# without an explicit target get TARGET_RATE_DEFAULT when they are a high-rate
# zone (see HIGH_RATE_SHARE) and no target otherwise.
TARGET_RATE_DEFAULT = 270.0
TARGET_MAX = 10000.0

# A station-hour counts as "full production" when its K50 cycles reach this
# share of the busiest hour of the day.
FULL_HOUR_SHARE = 0.75
# Zones averaging at least this share of the busiest zone's presentations per
# station-hour are "high-rate" and get the default target automatically.
HIGH_RATE_SHARE = 0.5
# Picks longer than this are the "tail" quoted in the budget commentary.
LONG_PICK_S = 20.0
# No robot reaching any station for this long is reported as an idle window.
IDLE_MIN_MINUTES = 30
# A robot with no task for this long is "away" (most likely charging — the log
# has no charging events) rather than "between tasks" and available.
AWAY_MIN_S = 300.0
# A release→arrival gap longer than this is a stand-down, not a handover, and is
# left out of the switch percentiles.
MAX_SWITCH_S = 3600.0

# ── Histogram shape (bins × width, last bin collects the tail) ────────────────
SWITCH_HIST_BINS, SWITCH_HIST_W = 60, 0.5
OPERATOR_HIST_BINS, OPERATOR_HIST_W = 30, 2.0

CONFIG_FILENAME = "ess_config.json"
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class Settings:
    """Everything a run can be tuned with."""
    door_s: float = DOOR_S_DEFAULT
    target_rate: float = TARGET_RATE_DEFAULT
    # Per station ("LABOR-12") or per zone ("Zone B"); a station's own entry
    # wins over its zone's.  0 means "none".
    targets: dict[str, float] = field(default_factory=dict)    # totes per hour
    pick_s: dict[str, float] = field(default_factory=dict)     # target pick seconds per tote
    switch_s: dict[str, float] = field(default_factory=dict)   # target switch seconds per tote
    # Stations (or zones) without a shutter door: True = no door.  A station's
    # own entry wins over its zone's, so {"Zone B": True, "LABOR-9": False}
    # means every Zone B station but LABOR-9.  Unlisted stations have a door.
    no_door: dict[str, bool] = field(default_factory=dict)
    # Dates ("YYYY-MM-DD") on which the doors were not in use at all.
    no_door_days: list[str] = field(default_factory=list)
    # Seconds beyond a station's median handover that count as starved.
    starved_s: float = STARVED_S_DEFAULT
    output_root: str | None = None

    def validate(self) -> list[str]:
        """Return a list of problems; empty when the settings are usable."""
        bad: list[str] = []
        if not 0 <= self.door_s <= DOOR_S_MAX:
            bad.append(f"door_s must be between 0 and {DOOR_S_MAX:.0f} seconds (got {self.door_s}).")
        if not 0 <= self.starved_s <= STARVED_S_MAX:
            bad.append(f"starved_s must be between 0 and {STARVED_S_MAX:.0f} seconds (got {self.starved_s}).")
        if not 1 <= self.target_rate <= TARGET_MAX:
            bad.append(f"target_rate must be between 1 and {TARGET_MAX:.0f} (got {self.target_rate}).")
        for name, rate in self.targets.items():
            if not isinstance(rate, (int, float)) or not 0 <= rate <= TARGET_MAX:
                bad.append(f"target for '{name}' must be a number between 0 and {TARGET_MAX:.0f}.")
        bad += [f"no_door_days: '{d}' is not a date like 2026-09-25." for d in self.no_door_days
                if not (isinstance(d, str) and _DATE_RE.match(d))]
        for kind, table in (("pick_s", self.pick_s), ("switch_s", self.switch_s)):
            for name, sec in table.items():
                if not isinstance(sec, (int, float)) or not 0 <= sec <= 3600:
                    bad.append(f"{kind} for '{name}' must be a number of seconds between 0 and 3600.")
        return bad

    def to_dict(self) -> dict:
        return asdict(self)


def load_settings(folder: str, base: Settings | None = None) -> tuple[Settings, list[str]]:
    """Merge ``ess_config.json`` from *folder* over *base*.

    Returns (settings, problems).  A missing file is not a problem.
    """
    s = Settings(**(base.to_dict() if base else {}))
    path = os.path.join(folder, CONFIG_FILENAME)
    if not os.path.isfile(path):
        return s, []
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        return s, [f"{CONFIG_FILENAME} could not be read: {exc}"]
    if not isinstance(raw, dict):
        return s, [f"{CONFIG_FILENAME} must contain a JSON object."]

    known = {"door_s", "target_rate", "starved_s", "targets", "pick_s", "switch_s", "no_door", "no_door_days"}
    problems = [f"{CONFIG_FILENAME}: unknown setting '{k}'." for k in raw if k not in known]
    for key in ("door_s", "target_rate", "starved_s"):
        if key in raw:
            try:
                setattr(s, key, float(raw[key]))
            except (TypeError, ValueError):
                problems.append(f"{CONFIG_FILENAME}: '{key}' must be a number.")
    if "no_door" in raw:
        nd = raw["no_door"]
        if isinstance(nd, list) and all(isinstance(x, str) for x in nd):
            s.no_door.update({x: True for x in nd})
        elif isinstance(nd, dict) and all(isinstance(v, bool) for v in nd.values()):
            s.no_door.update({str(k): v for k, v in nd.items()})
        else:
            problems.append(f"{CONFIG_FILENAME}: 'no_door' must be a list of stations or zones, "
                            'like ["LABOR-8", "Zone B"].')
    if "no_door_days" in raw:
        nd = raw["no_door_days"]
        if isinstance(nd, list) and all(isinstance(x, str) and _DATE_RE.match(x) for x in nd):
            s.no_door_days = sorted(set(s.no_door_days) | set(nd))
        else:
            problems.append(f"{CONFIG_FILENAME}: 'no_door_days' must be a list of dates like "
                            '["2026-09-25", "2026-09-26"].')
    for key, table in (("targets", s.targets), ("pick_s", s.pick_s), ("switch_s", s.switch_s)):
        if key not in raw:
            continue
        if not isinstance(raw[key], dict):
            problems.append(f"{CONFIG_FILENAME}: '{key}' must be an object like "
                            '{"Zone A": 270, "LABOR-12": 80}.')
            continue
        for name, val in raw[key].items():
            try:
                table[str(name)] = float(val)
            except (TypeError, ValueError):
                problems.append(f"{CONFIG_FILENAME}: {key} for '{name}' must be a number.")
    return s, problems + s.validate()
