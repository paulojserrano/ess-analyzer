"""The door-independent day numbers metrics.py hands to the report."""
from __future__ import annotations

import pytest

from metrics import day_base, zones_for
from tests.synthetic import ACR_IDS, MULTI_EVERY, PICK_S, PRE_S, STATIONS, SWITCH_S


def test_operator_time_per_station(base):
    by_station = {t["station"]: t for t in base["station_table"]}
    for station, pick in PICK_S.items():
        assert by_station[station]["op_med"] == pytest.approx(pick)


def test_every_arrival_pairs_with_a_release(base):
    assert base["n_unpaired"] == 0
    assert base["overall"]["visits"] == base["n_arrivals"]


def test_station_median_gap_is_the_core_switch(base):
    for t in base["station_table"]:
        assert t["core_s"] == pytest.approx(SWITCH_S)


def test_zones_follow_the_station_rows(base):
    z = zones_for([base])
    assert z["LABOR-1"] == z["LABOR-2"] != z["LABOR-3"]
    assert z["LABOR-1"] == "Zone A"


def test_raw_arrays_are_sorted_deciseconds(base):
    for s, station in enumerate(base["stations"]):
        for h in range(24):
            op, gap = base["raw"]["op"][s][h], base["raw"]["gap"][s][h]
            assert op == sorted(op) and gap == sorted(gap)
            assert len(op) == (base["hm_visits"][s][h] or 0)
            if op:
                assert op[0] == int(PICK_S[station] * 10)


def test_zero_door_budget_covers_each_full_hour(base):
    """occ + core + wait tile the hour when the station is busy all of it."""
    B = base["budget0"]
    for s, station in enumerate(base["stations"]):
        for h in base["full_hours"][1:-1]:          # inner hours, no shift edges
            total = B["occ"][s][h] + B["core"][s][h] + B["wait"][s][h]
            assert total == pytest.approx(3600, abs=60), (station, h)


def test_rate_per_full_hour(base):
    by_station = {t["station"]: t for t in base["station_table"]}
    # LABOR-1 picks in 8 s and switches in 4 s → 300 an hour at most
    assert 250 <= by_station["LABOR-1"]["rate_full"] <= 300


def test_k50_cycles_and_multi_station(base):
    k = base["robot_k50"]
    assert k["no_visit_loads"] == 0
    assert k["multi_station_pct"] == pytest.approx(100 / MULTI_EVERY, abs=0.4)
    assert sum(k["stations_per_cycle"].values()) == k["total"]
    assert sum(h["multi"] for h in base["hourly"]) == k["multi_station_n"]


def test_acr_moves(base):
    a = base["robot_acr"]
    assert a["n"] == len(ACR_IDS)
    assert a["put"] == a["store"] > 0 and a["reloc"] == 0


def test_full_hours_cover_the_shift(base):
    assert base["full_hours"] == [6, 7, 8, 9]


def test_grids_are_rectangular(base):
    for key in ("hm_visits", "hm_op_med"):
        assert len(base[key]) == len(base["stations"])
        assert all(len(row) == 24 for row in base[key])
    for key in ("occ", "core", "wait"):
        assert len(base["budget0"][key]) == len(STATIONS)


# ── utilization ───────────────────────────────────────────────────────────────

def test_utilization_counts_from_allocation(base):
    """ACR rounds: put busy 30 s (10 s allocation lead + 20 s carry), store
    busy 20 s (not allocated, so from the load), every ~95 s."""
    u = base["utilization"]["ACR"]
    assert u["fleet"] == len(ACR_IDS)
    assert u["day"]["util_fleet_full"] == pytest.approx(50 / 95 * 100, abs=3)


def test_k50_utilization_is_bounded(base):
    u = base["utilization"]["K50"]
    d = u["day"]
    assert 0 < d["util_fleet_full"] <= d["util_active_full"] <= 100
    assert d["peak"] <= u["fleet"]
    assert len(u["busy5"]) == 288 and len(u["hourly"]) == 24


def test_utilization_is_zero_outside_the_shift(base):
    for role in ("K50", "ACR"):
        assert base["utilization"][role]["hourly"][2]["busy"] == 0


def test_stations_per_cycle_histogram(base):
    k = base["robot_k50"]
    hist = k["stations_hist"]
    assert sum(hist) == k["total"]
    assert hist[0] == k["stations_per_cycle"]["1"]
    assert hist[1] == k["stations_per_cycle"]["2"]
    assert sum(hist[2:]) == k["stations_per_cycle"]["3+"]


def test_multi_station_starvation(base):
    m = base["multi"]
    kinds = m["kinds"]
    # Plain handovers take exactly SWITCH_S, the station median, so never wait.
    assert kinds["plain"]["n"] > 0
    assert kinds["plain"]["wait_s"] == 0 and kinds["plain"]["starved"] == 0
    # Every chained cycle leaves one station early and arrives at another.
    assert kinds["out"]["n"] + kinds["both"]["n"] > 0
    assert abs((kinds["out"]["n"] + kinds["both"]["n"]) - (kinds["in"]["n"] + kinds["both"]["n"])) <= 3
    # The chained robot arrives at least 20 s after leaving its first station.
    assert m["transit_med"] >= 20
    # So all the waiting there is comes with multi-station handovers…
    total = sum(k["wait_s"] for k in kinds.values())
    assert m["wait_s"] == pytest.approx(total, abs=0.5)
    assert m["excess_s"] == pytest.approx(total, abs=0.5)
    assert sum(s["excess_s"] for s in m["stations"]) == pytest.approx(m["excess_s"], abs=0.5)
    # …and the windows hold no more handovers than there are.
    assert sum(b["handovers"] for b in m["bands"]) <= sum(k["n"] for k in kinds.values())
    assert sum(h["handovers"] for h in m["hourly"]) >= sum(k["n"] for k in kinds.values())


def test_starvation_stages_split_the_wait_exactly(base):
    s = base["starve"]
    assert s["handovers"] == sum(k["n"] for k in base["multi"]["kinds"].values())
    assert sum(s["stages"].values()) == pytest.approx(s["wait_s"], abs=0.5)
    for row in s["stations"]:
        assert sum(row["stages"].values()) == pytest.approx(row["wait_s"], abs=0.5)
    for h in s["hourly"]:
        assert sum(h["stages"].values()) == pytest.approx(h["wait_s"], abs=0.5)
    # Plain handovers never wait in the synthetic day: all the waiting is a
    # robot still busy at its first station.
    assert s["stages"]["chained"] == pytest.approx(s["wait_s"], abs=0.5)
    assert sum(s["starved_at"].values()) == s["starved"]


def test_starvation_counts_add_up(base):
    s = base["starve"]
    assert sum(x["n"] for x in s["en_route"]) == s["handovers"]
    assert sum(x["starved"] for x in s["en_route"]) == s["starved"]
    assert sum(x["n"] for x in s["context"].values()) == s["starved"]
    assert sum(x["n"] for x in s["resume"]) == s["handovers"]
    assert sum(x["n"] for x in s["pace"]) <= s["handovers"]
    for row in s["stations"]:
        assert sum(row["en_route_n"]) == row["handovers"]
        assert sum(row["en_route_starved"]) == row["starved"]
    # Every arrival is preceded by its own allocation, so robots are always on the way.
    assert s["en_route"][0]["n"] < s["handovers"]


def test_starvation_travel_is_pickup_to_arrival(base):
    s = base["starve"]
    hist = s["travel_hist"]
    assert sum(hist) > 0
    # Every first-station trip takes PRE_S seconds in the synthetic day.
    assert hist.index(max(hist)) == int(PRE_S // s["travel_w"])
    assert sum(r["created"] for r in s["stations"]) > 0


def test_starved_threshold_is_a_parameter(data, base):
    assert base["starve"]["starved_s"] == 1.0
    strict = day_base(data, starved_s=0.0)
    lax = day_base(data, starved_s=1e4)
    assert strict["starve"]["starved"] >= base["starve"]["starved"] >= lax["starve"]["starved"] == 0
    assert lax["multi"]["starved_s"] == 1e4
    # The threshold only changes what counts as starved, not the waiting itself.
    assert strict["starve"]["wait_s"] == base["starve"]["wait_s"] == lax["starve"]["wait_s"]


def test_operator_time_average(base):
    for row in base["station_table"]:
        # Every pick at a station takes the same time in the synthetic day.
        assert row["op_mean"] == pytest.approx(PICK_S[row["station"]], abs=0.1)
    assert min(PICK_S.values()) <= base["overall"]["op_mean"] <= max(PICK_S.values())


# ── rack and buffer locations ─────────────────────────────────────────────────

def test_spatial_sources_cover_every_put(base):
    from tests.synthetic import STORAGE_AISLES, STORAGE_LEVELS
    sp = base["spatial"]
    assert sum(map(sum, sp["grid"])) == sp["puts"] == base["robot_acr"]["put"]
    assert set(sp["aisles"]) <= set(STORAGE_AISLES) and set(sp["levels"]) <= set(STORAGE_LEVELS)
    assert len(sp["grid"]) == len(sp["levels"]) and all(len(row) == len(sp["aisles"]) for row in sp["grid"])
    # Every put is handled in 20 s in the synthetic day, at every level.
    assert sum(x["n"] for x in sp["lift"]) == sp["puts"]
    for x in sp["lift"]:
        assert x["handle_s"] == pytest.approx(20 * x["n"], abs=1)


def test_spatial_returns_and_repeats(base):
    sp = base["spatial"]
    # Each synthetic tote is put once and stored once: none comes back out.
    r = sp["returns"]
    assert r["stores"] > 0 and r["back"][-1] == r["stores"] and sum(r["back"]) == r["stores"]
    assert sp["repeats"]["totes"][0] == sp["puts"] and sum(sp["repeats"]["totes"][1:]) == 0


def test_spatial_crowding_and_travel(base):
    from tests.synthetic import ACR_ALLOC_LEAD_S, ALLOC_LEAD_S, BUFFER_AISLES
    cr = base["spatial"]["crowd"]
    for fleet, lead in (("K50", ALLOC_LEAD_S), ("ACR", ACR_ALLOC_LEAD_S)):
        c = cr[fleet]
        assert sum(b["n"] for b in c["bins"]) == c["trips"]
        # Every lead is the same, so nothing is left once the fleet load is held fixed.
        assert c["lead_s"] == pytest.approx(lead * c["trips"], abs=1)
        assert all(b["excess_s"] == pytest.approx(0, abs=1) for b in c["bins"])
    tr = base["spatial"]["travel"]
    assert set(tr["aisles"]) <= set(BUFFER_AISLES) and tr["stations"] == STATIONS
    for row_n, row_s in zip(tr["n"], tr["sum_s"]):
        for n, s in zip(row_n, row_s):
            assert s == pytest.approx(PRE_S * n, abs=1)
