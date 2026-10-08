"""Robot states, task supply and station slots."""
from __future__ import annotations

from datetime import timedelta

import pandas as pd
import pytest

from log_parser import LogData
from metrics import _station_slots, day_base
from tests.synthetic import CREATE_LEAD_S


# ── robot states ──────────────────────────────────────────────────────────────

def test_states_never_exceed_the_fleet(base):
    for role, u in base["utilization"].items():
        for h in u["hourly"]:
            assert h["busy"] + h["idle"] + h["away"] <= u["fleet"] + 0.5, (role, h)


def test_available_utilization_is_at_least_fleet_utilization(base):
    for u in base["utilization"].values():
        d = u["day"]
        assert d["util_available_full"] >= d["util_fleet_full"]


def test_gap_bands_cover_all_idle_time(base):
    for u in base["utilization"].values():
        assert sum(b["share"] for b in u["gap_bands"]) == pytest.approx(100, abs=0.5)
        assert [b["away"] for b in u["gap_bands"]] == sorted(b["away"] for b in u["gap_bands"])


# ── task supply ───────────────────────────────────────────────────────────────

def test_task_creation_is_read(data):
    assert len(data.created) > 0
    assert data.created["dest"].str.startswith("LABOR").all()


def test_ready_to_allocation_wait(base):
    """K50 totes start in the buffer, so they are ready when created."""
    f = base["flow"]["day"]
    assert f["ready_to_alloc_med"] == pytest.approx(CREATE_LEAD_S)


def test_acr_leg_is_timed_only_where_there_is_one(base):
    # ACR put: created 15 s before the load, carried 20 s → ready after 35 s
    assert base["flow"]["day"]["created_to_ready_med"] == pytest.approx(35.0)


def test_supply_hourly_counts_add_up(base):
    f = base["flow"]
    assert sum(h["created"] for h in f["hourly"]) == f["day"]["created"]


# ── station slots ─────────────────────────────────────────────────────────────

def test_a_steady_station_is_not_reported_as_capped(base):
    """Regular arrivals hold one level, but nothing queues behind it."""
    for row in base["slots"]["stations"]:
        assert row["limit"] is None
        assert row["level"] >= 1


def test_lead_time_is_allocation_to_release(base):
    lead = {r["station"]: r["lead_med"] for r in base["slots"]["stations"]}
    assert lead["LABOR-1"] == pytest.approx(80 + 8)      # travel 80 s, pick 8 s


def _capped_day(cap: int = 3, tasks: int = 100) -> LogData:
    """One station that only takes a new task when one of `cap` slots frees,
    with a backlog of ready work waiting behind it."""
    day = pd.Timestamp("2026-10-01")
    t0 = day + timedelta(hours=8)
    created, alloc, arr, rel = [], [], [], []
    free_at = [t0] * cap                  # when each slot next frees
    station_free = t0
    for i in range(tasks):
        task, robot = f"ND{i:06d}", f"kubot-{301 + i}"
        created.append((t0, task, "LABOR-1"))
        slot = min(range(cap), key=lambda k: free_at[k])
        a = max(free_at[slot], t0)
        reach = max(a + timedelta(seconds=60), station_free)
        r = reach + timedelta(seconds=30)
        alloc.append((a, robot, task, "LABOR-1"))
        arr.append((reach, "LABOR-1", robot, "", "LT_LABOR:POINT:1:1"))
        rel.append((r, "LABOR-1", robot))
        free_at[slot] = r
        station_free = r
    roles = {f"kubot-{301 + i}": "K50" for i in range(tasks)}
    return LogData(
        arrivals=pd.DataFrame(arr, columns=["ts", "station", "robot", "tote", "point"]),
        releases=pd.DataFrame(rel, columns=["ts", "station", "robot"]),
        tote_events=pd.DataFrame(columns=["ts", "kind", "robot", "tote", "loc", "task"]),
        moves=pd.DataFrame(columns=["robot", "tote", "t_load", "t_unload", "from_loc", "to_loc", "task"]),
        roles=roles,
        allocations=pd.DataFrame(alloc, columns=["ts", "robot", "task", "station"]),
        created=pd.DataFrame(created, columns=["ts", "task", "dest"]),
    )


def test_a_capped_station_is_detected():
    data = _capped_day(cap=3)
    out = _station_slots(data, pd.Timestamp("2026-10-01"), [8], ["LABOR-1"])
    row = out["stations"][0]
    assert row["limit"] == 3
    assert row["at_limit_pct"] > 50
    assert row["ready_at_limit"] > row["ready_below_limit"]
    assert row["max_assigned"] == 3
