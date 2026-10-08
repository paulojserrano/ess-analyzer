"""
log_parser.py — read Hairobotics "play_extract" application logs into native frames.

This is the only ingestion path.  A log line looks like:

    [thread] 2026-09-30 23:47:39,305 [INFO] from callback.EventCallbackHandler-line:1474 - produce callback: <id> message: {JSON}
    [thread] 2026-09-30 23:47:42,492 [INFO] from c.h.e.a.s.k.EssKubotStationHandleLetRobotGo-line:104 - station: LABOR-7 robot: kubot-347 will leave

Timestamps are the log line's own local time, millisecond precision.

Lines we keep
-------------
    CALLBACK_OF_ROBOT_REACH_STATION      a robot reaches a station  → arrival
    "station: X robot: Y will leave"     the operator lets it go    → release
    CALLBACK_OF_TOTE_LOADED_BY_ROBOT     tote picked up             → move start
    CALLBACK_OF_TOTE_UNLOADED_BY_ROBOT   tote put down              → move end
    CALLBACK_OF_TASK_ALLOCATED           robot assigned a task      → busy from here
    "wmsTask[…]: ND… is created … destinationCodes: [LABOR-N]"
                                         the warehouse system creates a task → supply

``CALLBACK_OF_TASK_FINISHED`` fires in the same millisecond as the arrival, so it
is *not* the operator release — only the "will leave" line is.

Output (`LogData`)
------------------
    arrivals     ts, station, robot, tote, point
    releases     ts, station, robot
    tote_events  ts, kind (load/unload), robot, tote, loc, task
    allocations  ts, robot, task, station   (station = the K50's destination)
    created      ts, task, dest
    moves        robot, tote, t_load, t_unload, from_loc, to_loc, task
    roles        {robot: "K50" | "ACR"}

``moves`` pairs every unload with that robot's most recent earlier load of the
same tote.  A K50 that chains a second station re-loads the tote at the first
station, so counting whole buffer-to-buffer cycles needs the load side
restricted to the buffer — see ``pair_moves(..., load_contains=BUFFER_K50)``.

Robot roles are read from behaviour, not from the robot numbering: a robot that
reaches a station, reports a HAIFLEX type, or works the haiflex buffer is a K50
(the station-serving robot); everything else is an ACR (storage ↔ kubot buffer).
"""
from __future__ import annotations

import gzip
import json
import logging
import os
import re
from dataclasses import dataclass, field

import pandas as pd

_log = logging.getLogger(__name__)

# ── Log line format ───────────────────────────────────────────────────────────
_LINE_RE = re.compile(
    r'^\[[^\]]*\] (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) \[\w+\] from (\S+?)-line:\d+ - (.*)$'
)
_LEAVE_RE = re.compile(r'station:\s*(\S+)\s+robot:\s*(\S+)\s+will leave')
# Read the event code straight off the raw line: parsing every callback as JSON
# just to find out we don't want it costs seconds on a 250 MB log.
_EVENT_RE = re.compile(r'"eventCode"\s*:\s*"([^"]+)"')
_CREATED_RE = re.compile(r'wmsTask\[[^\]]*\]:\s*(\S+)\s+is created.*?destinationCodes:\s*\[([^\]]*)\]')
_DATE_IN_NAME_RE = re.compile(r'(\d{4}-\d{2}-\d{2})')

# Callback event codes we act on.
_EV_REACH = "CALLBACK_OF_ROBOT_REACH_STATION"
_EV_LOAD = "CALLBACK_OF_TOTE_LOADED_BY_ROBOT"
_EV_UNLOAD = "CALLBACK_OF_TOTE_UNLOADED_BY_ROBOT"
_EV_ALLOC = "CALLBACK_OF_TASK_ALLOCATED"
_WANTED = (_EV_REACH, _EV_LOAD, _EV_UNLOAD, _EV_ALLOC)

# Buffer location tokens — which fleet stages totes at which buffer.
BUFFER_K50 = "coop_haiflex"
BUFFER_ACR = "coop_kubot"

ROLE_K50 = "K50"
ROLE_ACR = "ACR"

# Only stations whose code starts with this are operator stations; conveyor
# drop points (CS-00N) are logged the same way but are not picked at.
STATION_PREFIX = "LABOR"

LOG_SUFFIXES = (".log", ".log.gz")


class LogError(ValueError):
    """A log file could not be read or contained nothing usable."""


def natural_key(name) -> tuple:
    """Sort key that orders 'LABOR-2' before 'LABOR-10'."""
    return tuple(int(t) if t.isdigit() else t for t in re.split(r"(\d+)", str(name)))


# ── File helpers ──────────────────────────────────────────────────────────────

def is_log_path(path: str) -> bool:
    """True for a plain .log file or a gzip-compressed one (.log.gz)."""
    return path.lower().endswith(LOG_SUFFIXES)


def log_stem(path: str) -> str:
    """Basename without the .log / .log.gz suffix."""
    name = os.path.basename(path)
    for suf in LOG_SUFFIXES:
        if name.lower().endswith(suf):
            return name[: -len(suf)]
    return os.path.splitext(name)[0]


def log_date(path: str) -> str | None:
    """'YYYY-MM-DD' found in the filename, or None."""
    m = _DATE_IN_NAME_RE.search(os.path.basename(path))
    return m.group(1) if m else None


def open_log(path: str):
    """Open a log as text, transparently decompressing .gz."""
    if path.lower().endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return open(path, encoding="utf-8", errors="replace")


def looks_like_log(path: str) -> bool:
    """True when the first non-blank line matches the expected format."""
    try:
        with open_log(path) as fh:
            for _ in range(50):
                line = fh.readline()
                if not line:
                    return False
                if line.strip():
                    return bool(_LINE_RE.match(line.rstrip("\r\n")))
    except (OSError, EOFError):
        return False
    return False


def group_by_day(paths: list[str]) -> list[list[str]]:
    """Group log files into days.  Files sharing a date in their name belong to
    the same day (split logs); anything undated stands alone."""
    days: dict[str, list[str]] = {}
    for p in paths:
        days.setdefault(log_date(p) or os.path.basename(p), []).append(p)
    return [sorted(g, key=os.path.basename) for _, g in sorted(days.items())]


# ── Parsed result ─────────────────────────────────────────────────────────────

@dataclass
class LogData:
    """One operational day's events, in native form."""
    arrivals: pd.DataFrame        # ts, station, robot, tote
    releases: pd.DataFrame        # ts, station, robot
    tote_events: pd.DataFrame     # ts, kind (load/unload), robot, tote, loc, task
    moves: pd.DataFrame           # robot, tote, t_load, t_unload, from_loc, to_loc, task
    roles: dict[str, str]         # robot → ROLE_K50 / ROLE_ACR
    paths: list[str] = field(default_factory=list)
    skipped_stations: dict[str, int] = field(default_factory=dict)
    allocations: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=["ts", "robot", "task", "station"]))
    created: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=["ts", "task", "dest"]))

    @property
    def source(self) -> str:
        if not self.paths:
            return ""
        first = os.path.basename(self.paths[0])
        return first if len(self.paths) == 1 else f"{first} (+{len(self.paths) - 1} more)"

    @property
    def stations(self) -> list[str]:
        return sorted(self.arrivals["station"].unique(), key=natural_key)

    @property
    def date(self) -> pd.Timestamp:
        """The calendar day most arrivals fall on."""
        return self.arrivals["ts"].dt.normalize().value_counts().idxmax()

    def robots(self, role: str) -> set[str]:
        return {r for r, v in self.roles.items() if v == role}


# ── Parsing ───────────────────────────────────────────────────────────────────

def _ts(values) -> pd.Series:
    """'2026-09-30 23:47:39,305' → datetime64, vectorised."""
    s = pd.Series(values, dtype="object").str.replace(",", ".", regex=False)
    return pd.to_datetime(s, format="%Y-%m-%d %H:%M:%S.%f", errors="coerce")


def _scan(paths: list[str]) -> tuple[list[dict], list[tuple], dict[str, int], list[tuple]]:
    """Stream the files once: (callbacks, leaves, skipped_stations, created)."""
    callbacks: list[dict] = []
    leaves: list[tuple] = []
    created: list[tuple] = []
    skipped: dict[str, int] = {}

    for path in paths:
        with open_log(path) as fh:
            for raw in fh:
                # Substring guards first — the regex is far too slow to run on
                # every one of ~600k lines.
                if "produce callback:" in raw:
                    i = raw.find("message: ")
                    if i < 0:
                        continue
                    found = _EVENT_RE.search(raw, i)
                    if not found or found.group(1) not in _WANTED:
                        continue
                    code = found.group(1)
                    m = _LINE_RE.match(raw.rstrip("\r\n"))
                    if not m:
                        continue
                    try:
                        msg = json.loads(raw[i + 9:].rstrip("\r\n"))
                    except json.JSONDecodeError:
                        continue
                    station = str(msg.get("stationCode", "") or "")
                    if code == _EV_REACH and not station.startswith(STATION_PREFIX):
                        skipped[station] = skipped.get(station, 0) + 1
                        continue
                    msg["_ts"] = m.group(1)
                    msg["_code"] = code
                    callbacks.append(msg)
                elif "will leave" in raw:
                    m = _LINE_RE.match(raw.rstrip("\r\n"))
                    lm = _LEAVE_RE.search(m.group(3)) if m else None
                    if lm and lm.group(1).startswith(STATION_PREFIX):
                        leaves.append((m.group(1), lm.group(1), lm.group(2)))
                elif "is created" in raw and "wmsTask[" in raw:
                    m = _LINE_RE.match(raw.rstrip("\r\n"))
                    cm = _CREATED_RE.search(m.group(3)) if m else None
                    if cm:
                        dest = next((x.strip() for x in cm.group(2).split(",") if x.strip()), "")
                        created.append((m.group(1), cm.group(1), dest))
    return callbacks, leaves, skipped, created


def _roles(callbacks: list[dict]) -> dict[str, str]:
    """Classify every robot seen, from what it actually did."""
    k50: set[str] = set()
    seen: set[str] = set()
    for c in callbacks:
        robot = c.get("robotCode")
        if not robot or c["_code"] == _EV_ALLOC:
            continue
        seen.add(robot)
        if (c["_code"] == _EV_REACH
                or BUFFER_K50 in str(c.get("locationCode", ""))
                or "HAIFLEX" in str(c.get("robotTypeCode", ""))):
            k50.add(robot)
    return {r: (ROLE_K50 if r in k50 else ROLE_ACR) for r in seen}


_MOVE_COLS = ["robot", "tote", "t_load", "t_unload", "from_loc", "to_loc", "task"]


def pair_moves(events: pd.DataFrame, load_contains: str | None = None,
               robots: set[str] | None = None) -> pd.DataFrame:
    """Pair every unload with the same robot's most recent earlier load of that
    tote.  Returns robot, tote, t_load, t_unload, from_loc, to_loc.

    ``load_contains`` keeps only loads whose location contains that token, which
    is how a whole buffer-to-buffer cycle is measured: a K50 chaining a second
    station re-loads the tote at the first one, and that re-load must not be
    mistaken for the start of a new cycle.
    """
    df = events
    if robots is not None:
        df = df[df["robot"].isin(robots)]
    loads = df[df["kind"] == "load"]
    if load_contains:
        loads = loads[loads["loc"].str.contains(load_contains, na=False)]
    if "task" not in loads.columns:
        loads = loads.assign(task="")
    loads = loads.rename(columns={"ts": "t_load", "loc": "from_loc"})[
        ["t_load", "robot", "tote", "from_loc", "task"]]
    unloads = df[df["kind"] == "unload"].rename(columns={"ts": "t_unload", "loc": "to_loc"})[
        ["t_unload", "robot", "tote", "to_loc"]]
    if loads.empty or unloads.empty:
        return pd.DataFrame(columns=_MOVE_COLS)

    paired = pd.merge_asof(
        unloads.sort_values("t_unload"), loads.sort_values("t_load"),
        left_on="t_unload", right_on="t_load", by=["robot", "tote"], direction="backward",
    )
    return paired.dropna(subset=["t_load"]).reset_index(drop=True)


def parse_logs(paths: str | list[str]) -> LogData:
    """Parse one day's log file(s) into native frames.  Raises LogError."""
    if isinstance(paths, str):
        paths = [paths]
    paths = sorted(paths, key=os.path.basename)
    for p in paths:
        if not os.path.isfile(p):
            raise LogError(f"File not found: {p}")
        if not is_log_path(p):
            raise LogError(f"Not a log file: {os.path.basename(p)} (expected .log or .log.gz)")

    try:
        callbacks, leaves, skipped, created_rows = _scan(paths)
    except (OSError, EOFError) as exc:
        raise LogError(f"Cannot read log: {exc}") from exc

    if not callbacks and not leaves:
        raise LogError(
            "No usable events found. Expected a Hairobotics play_extract application log "
            "with 'produce callback:' and 'will leave' lines.")

    roles = _roles(callbacks)

    arr_rows, move_rows, alloc_rows = [], [], []
    for c in callbacks:
        code = c["_code"]
        task = c.get("taskCode", "")
        if isinstance(task, list):
            task = task[0] if task else ""
        if code == _EV_ALLOC:
            if c.get("robotCode"):
                alloc_rows.append({"_ts": c["_ts"], "robot": c["robotCode"], "task": str(task or ""),
                                   "station": str(c.get("stationCode", "") or "")})
            continue
        if code == _EV_REACH:
            trays = c.get("trays") or []
            arr_rows.append({
                "_ts": c["_ts"],
                "station": c.get("stationCode", ""),
                "robot": c.get("robotCode", ""),
                "tote": (trays[0].get("containerCode", "") if trays else "")
                        or (c.get("containerCode") or ""),
                "point": c.get("locationCode", "") or "",
            })
        else:
            move_rows.append({
                "_ts": c["_ts"],
                "kind": "load" if code == _EV_LOAD else "unload",
                "robot": c.get("robotCode", ""),
                "tote": c.get("containerCode", "") or "",
                "loc": c.get("locationCode", "") or "",
                "task": str(task or ""),
            })

    arrivals = pd.DataFrame(arr_rows, columns=["_ts", "station", "robot", "tote", "point"])
    arrivals.insert(0, "ts", _ts(arrivals.pop("_ts")))
    arrivals = arrivals.dropna(subset=["ts"]).sort_values("ts", kind="stable").reset_index(drop=True)

    releases = pd.DataFrame(leaves, columns=["_ts", "station", "robot"])
    releases.insert(0, "ts", _ts(releases.pop("_ts")))
    releases = releases.dropna(subset=["ts"]).sort_values("ts", kind="stable").reset_index(drop=True)

    if arrivals.empty:
        raise LogError("The log has no robot-reaches-station events, so there is nothing to analyse.")

    tote_events = pd.DataFrame(move_rows, columns=["_ts", "kind", "robot", "tote", "loc", "task"])
    tote_events.insert(0, "ts", _ts(tote_events.pop("_ts")))
    tote_events = (tote_events.dropna(subset=["ts"])
                   .sort_values("ts", kind="stable").reset_index(drop=True))

    allocations = pd.DataFrame(alloc_rows, columns=["_ts", "robot", "task", "station"])
    allocations.insert(0, "ts", _ts(allocations.pop("_ts")))
    allocations = (allocations.dropna(subset=["ts"])
                   .sort_values("ts", kind="stable").reset_index(drop=True))

    created = pd.DataFrame(created_rows, columns=["_ts", "task", "dest"])
    created.insert(0, "ts", _ts(created.pop("_ts")))
    created = created.dropna(subset=["ts"]).sort_values("ts", kind="stable").reset_index(drop=True)

    data = LogData(arrivals=arrivals, releases=releases, tote_events=tote_events,
                   moves=pair_moves(tote_events), roles=roles,
                   paths=list(paths), skipped_stations=skipped, allocations=allocations,
                   created=created)
    _log.info("%s: %s arrivals, %s releases, %s tote moves, %s robots",
              data.source, f"{len(arrivals):,}", f"{len(releases):,}",
              f"{len(data.moves):,}", f"{len(roles):,}")
    if skipped:
        _log.info("Ignored %s visits to non-operator stations (%s).",
                  f"{sum(skipped.values()):,}", ", ".join(sorted(skipped)))
    return data


# ── Stations and zones ────────────────────────────────────────────────────────

_POINT_RE = re.compile(r":POINT:(-?\d+):(-?\d+)")


def point_xy(point: str) -> tuple[int, int] | None:
    """'LT_LABOR:POINT:101294:80448' → (101294, 80448)."""
    m = _POINT_RE.search(str(point))
    return (int(m.group(1)), int(m.group(2))) if m else None


def station_points(arrivals: pd.DataFrame) -> dict[str, tuple[int, int]]:
    """Each station's pick point, as the (x, y) it is most often reached at."""
    out: dict[str, tuple[int, int]] = {}
    if "point" not in arrivals.columns:
        return out
    for station, pts in arrivals.groupby("station")["point"]:
        for point in pts.value_counts().index:
            xy = point_xy(point)
            if xy:
                out[station] = xy
                break
    return out


def assign_zones(points: dict[str, tuple[int, int]],
                 stations: list[str] | None = None) -> dict[str, str]:
    """Group stations into zones by the row they sit on (their Y coordinate).

    Stations on the same physical row do the same kind of work, so they share a
    zone and, by default, a target.  Rows are lettered from the lowest Y up
    (Zone A, Zone B, ...).  A station with no known point gets its own zone
    named after it, so it can still be given a target.
    """
    ys = sorted({xy[1] for xy in points.values()})
    letter = {y: f"Zone {chr(ord('A') + i)}" for i, y in enumerate(ys)}
    zones = {s: letter[xy[1]] for s, xy in points.items()}
    for s in stations or []:
        zones.setdefault(s, s)
    return zones


def scan_stations(path: str, settle: int = 2000, limit: int = 200_000) -> dict[str, tuple[int, int]]:
    """Find the operator stations in a log without parsing all of it.

    Reads robot-reaches-station events until no new station has turned up for
    `settle` of them (or `limit` are read).  Every station is normally seen in
    the first hour of a shift, so this stops a few percent into the file.
    """
    found: dict[str, tuple[int, int]] = {}
    since_new = seen = 0
    try:
        with open_log(path) as fh:
            for raw in fh:
                if _EV_REACH not in raw:
                    continue
                i = raw.find("message: ")
                try:
                    msg = json.loads(raw[i + 9:].rstrip("\r\n"))
                except (json.JSONDecodeError, ValueError):
                    continue
                station = str(msg.get("stationCode", "") or "")
                seen += 1
                since_new += 1
                if station.startswith(STATION_PREFIX) and station not in found:
                    xy = point_xy(msg.get("locationCode", ""))
                    if xy:
                        found[station] = xy
                        since_new = 0
                if (found and since_new >= settle) or seen >= limit:
                    break
    except (OSError, EOFError) as exc:
        raise LogError(f"Cannot read log: {exc}") from exc
    return found
