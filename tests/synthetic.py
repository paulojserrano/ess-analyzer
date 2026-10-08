"""
tests/synthetic.py — build a small but realistic play_extract log.

    python -m tests.synthetic demo.log [--date 2026-10-01] [--gz]

The generated day has a known shape, so the tests can assert exact numbers:
every station runs a fixed pick time, every K50 cycle is allocated, then runs
buffer → station(s) → buffer, and the ACRs put (allocated) and store (not
allocated, as in the real log) one tote each per round.  LABOR-1 and LABOR-2
sit on one row and LABOR-3 on another, so they fall into two zones.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import random
import sys
from datetime import datetime, timedelta

BUFFER_ACR = "HAI-030-045-01_1_coop_kubot"
# Rack spread for the location analysis: storage aisles × levels, K50 buffer
# aisles.  Drawn from their own generator so the timings never change.
STORAGE_AISLES = (3, 4, 5, 6)
STORAGE_LEVELS = (2, 8, 14)
BUFFER_AISLES = (1, 2)

STATIONS = ["LABOR-1", "LABOR-2", "LABOR-3"]
# Two rows of stations, so zone detection has something to find.
POINTS = {"LABOR-1": "LT_LABOR:POINT:100:80448", "LABOR-2": "LT_LABOR:POINT:101:80448",
          "LABOR-3": "LT_LABOR:POINT:102:161371"}

# Each station's operator time, in seconds — fixed so tests can assert medians.
PICK_S = {"LABOR-1": 8.0, "LABOR-2": 10.0, "LABOR-3": 40.0}
SWITCH_S = 4.0            # release → next arrival, every station
PRE_S = 60.0              # buffer pickup → arrival at the station
POST_S = 30.0             # release → back at the buffer
MULTI_EVERY = 20          # every Nth cycle is presented at a second station too
ALLOC_LEAD_S = 20.0       # K50 allocation → buffer pickup (empty travel)
CREATE_LEAD_S = 70.0      # task created → K50 allocated (tote already in the buffer)
ACR_ALLOC_LEAD_S = 10.0   # ACR put allocation → storage pickup
ACR_IDS = [f"kubot-{i}" for i in range(1, 4)]


def _line(ts: datetime, cls: str, body: str) -> str:
    return (f"[application-ess-pinned-dispatcher-1] {ts:%Y-%m-%d %H:%M:%S},"
            f"{ts.microsecond // 1000:03d} [INFO] from {cls}-line:1 - {body}")


def _callback(ts: datetime, payload: dict) -> str:
    return _line(ts, "callback.EventCallbackHandler",
                 "produce callback: 1 message: " + json.dumps(payload))


def _leave(ts: datetime, station: str, robot: str) -> str:
    return _line(ts, "c.h.e.a.s.k.EssKubotStationHandleLetRobotGo",
                 f"station: {station} robot: {robot} will leave")


def _load(ts, robot, tote, loc, task="ND0000001"):
    return _callback(ts, {"eventCode": "CALLBACK_OF_TOTE_LOADED_BY_ROBOT", "taskCode": task,
                          "actionCode": "load", "robotCode": robot, "containerCode": tote,
                          "stationCode": "LA_SHELF_STORAGE", "locationCode": loc, "callId": "1"})


def _unload(ts, robot, tote, loc, task="return:task-1"):
    return _callback(ts, {"eventCode": "CALLBACK_OF_TOTE_UNLOADED_BY_ROBOT", "taskCode": task,
                          "actionCode": "unload", "robotCode": robot, "containerCode": tote,
                          "stationCode": "LA_SHELF_STORAGE", "locationCode": loc, "callId": "1"})


def _created(ts, task, dest):
    return _line(ts, "c.h.e.a.tms.EssWmsTaskProcessor",
                 f"wmsTask[TMS]: {task} is created, taskType: TMS, priority: 10, "
                 f"destinationCodes: [{dest}], wmsStationCodes: [{dest}]")


def _alloc(ts, robot, task, station=""):
    return _callback(ts, {"eventCode": "CALLBACK_OF_TASK_ALLOCATED", "taskCode": task,
                          "robotCode": robot, "stationCode": station, "callId": "1"})


def _reach(ts, robot, station, tote):
    return _callback(ts, {"eventCode": "CALLBACK_OF_ROBOT_REACH_STATION", "robotCode": robot,
                          "robotTypeCode": "RT_KUBOT_MINI_HAIFLEX", "stationCode": station,
                          "locationCode": POINTS[station], "containerCode": tote,
                          "trays": [{"containerCode": tote}], "callId": "1"})


def _finished(ts, robot, station, tote):
    """Fires in the same millisecond as the arrival — never the release."""
    return _callback(ts, {"eventCode": "CALLBACK_OF_TASK_FINISHED", "taskCode": "ND0000001",
                          "robotCode": robot, "containerCode": tote, "stationCode": station,
                          "locationCode": POINTS[station], "callId": "1"})


def build(date: str = "2026-10-01", hours: tuple[int, int] = (6, 10),
          seed: int = 7) -> list[str]:
    """One day of events.  Returns unsorted log lines."""
    rnd = random.Random(seed)
    where = random.Random(seed + 1)

    def storage_slot() -> str:
        return f"HAI-{where.choice(STORAGE_AISLES):03d}-087-{where.choice(STORAGE_LEVELS):02d}_1"

    def buffer_slot() -> str:
        return f"HAI-{where.choice(BUFFER_AISLES):03d}-001-01_1_coop_haiflex"
    day = datetime.fromisoformat(date)
    lines: list[str] = []
    tote_n = 0

    # ── K50s: buffer → station(s) → buffer ──
    # A small discrete simulation, because the physical constraints are what the
    # metrics rely on: a station holds one robot at a time, and a robot carries
    # one tote at a time.  Overlapping either would push the hour budget past
    # 100% and make the numbers untestable.
    start = day + timedelta(hours=hours[0])
    end = day + timedelta(hours=hours[1])
    free_at: dict[str, datetime] = {s: start for s in STATIONS}      # station → next slot
    robot_free: dict[str, datetime] = {}                             # robot → back at buffer
    turn = 0

    def take_robot(loaded_at: datetime) -> str:
        """Any robot already back at the buffer by `loaded_at`, else a new one.

        The test is against the pickup, not the arrival: a robot that collects
        its next tote before dropping the last one would be carrying two at
        once, and its cycle window would swallow the previous visit.
        """
        for robot, free in robot_free.items():
            if free <= loaded_at:
                return robot
        robot = f"kubot-{301 + len(robot_free)}"
        robot_free[robot] = start
        return robot

    def present(robot: str, tote: str, station: str, at: datetime) -> datetime:
        """Emit one presentation; returns the release time."""
        lines.append(_reach(at, robot, station, tote))
        lines.append(_finished(at, robot, station, tote))
        rel = at + timedelta(seconds=PICK_S[station])
        lines.append(_leave(rel, station, robot))
        free_at[station] = rel + timedelta(seconds=SWITCH_S)
        return rel

    while min(free_at.values()) < end:
        station = min(free_at, key=lambda s: free_at[s])
        t = free_at[station]
        if t >= end:
            break
        loaded = t - timedelta(seconds=PRE_S)
        allocated = loaded - timedelta(seconds=ALLOC_LEAD_S)
        robot = take_robot(allocated)
        tote_n += 1
        tote = f"A{tote_n:09d}"
        task = f"ND{tote_n:010d}"
        second = (STATIONS[(STATIONS.index(station) + 1) % len(STATIONS)]
                  if turn % MULTI_EVERY == MULTI_EVERY - 1 else None)
        turn += 1

        lines.append(_created(allocated - timedelta(seconds=CREATE_LEAD_S), task, station))
        lines.append(_alloc(allocated, robot, task, station))
        slot = buffer_slot()
        lines.append(_load(loaded, robot, tote, slot, task))
        rel = present(robot, tote, station, t)
        back = rel + timedelta(seconds=POST_S)
        if second:
            # Re-loaded at the first station, then presented at the second — the
            # re-load must not be read as the start of a new cycle.
            lines.append(_load(rel + timedelta(seconds=1), robot, tote, ""))
            t2 = max(free_at[second], rel + timedelta(seconds=20))
            rel2 = present(robot, tote, second, t2)
            back = rel2 + timedelta(seconds=POST_S)
        lines.append(_unload(back, robot, tote, slot))
        robot_free[robot] = back

    # ── ACRs: storage → buffer (put) and buffer → storage (store) ──
    for robot in ACR_IDS:
        t = day + timedelta(hours=hours[0])
        end = day + timedelta(hours=hours[1])
        while t < end:
            tote_n += 1
            tote = f"B{tote_n:09d}"
            task = f"ND{tote_n:010d}"
            lines.append(_created(t - timedelta(seconds=ACR_ALLOC_LEAD_S + 5), task, "LABOR-1"))
            lines.append(_alloc(t - timedelta(seconds=ACR_ALLOC_LEAD_S), robot, task))
            slot = storage_slot()
            lines.append(_load(t, robot, tote, slot, task))
            lines.append(_unload(t + timedelta(seconds=20), robot, tote, BUFFER_ACR, task))
            lines.append(_load(t + timedelta(seconds=40), robot, tote, BUFFER_ACR))
            lines.append(_unload(t + timedelta(seconds=60), robot, tote, slot))
            t += timedelta(seconds=90 + rnd.randint(0, 10))

    return lines


def write(path: str, **kw) -> str:
    lines = sorted(build(**kw))
    text = "\n".join(lines) + "\n"
    if path.endswith(".gz"):
        with gzip.open(path, "wt", encoding="utf-8") as fh:
            fh.write(text)
    else:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
    return path


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Write a synthetic play_extract log.")
    ap.add_argument("out", nargs="?", default="demo.log")
    ap.add_argument("--date", default="2026-10-01")
    args = ap.parse_args(argv)
    out = write(args.out, date=args.date)
    print(f"{out} — {os.path.getsize(out):,} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
