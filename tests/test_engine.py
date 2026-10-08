"""The report's live calculations (templates/engine.js), run under Node.

These are the numbers a reader sees after moving the door slider or editing
a target, so they are tested against the synthetic day's known shape.
Skipped when Node is not installed.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

from report import build_payload
from config import Settings
from tests.synthetic import PRE_S, SWITCH_S

NODE = shutil.which("node")
ENGINE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates", "engine.js")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is not installed")

SCRIPT = r"""
const E = require(process.argv[2]);
const fs = require("fs");
const { report, settings } = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const derived = report.days.map((d) => E.computeDay(d, settings, report.zones, report.constants));
const summary = E.computeSummary(report.days, derived, report.stations);
process.stdout.write(JSON.stringify({ derived, summary }));
"""


def run_engine(tmp_path, report: dict, door: float = 0.0, targets: dict | None = None) -> dict:
    payload = tmp_path / "in.json"
    payload.write_text(json.dumps({"report": report, "settings": {
        "door_s": door, "targets": report["defaults"]["targets"] if targets is None else targets}}),
        encoding="utf-8")
    script = tmp_path / "run.js"
    script.write_text(SCRIPT, encoding="utf-8")
    out = subprocess.run([NODE, str(script), ENGINE, str(payload)], capture_output=True,
                         text=True, check=True, timeout=120)
    return json.loads(out.stdout)


@pytest.fixture(scope="module")
def report(bases):
    return build_payload(bases, Settings())


def _station(D, name):
    return next(t for t in D["stations"] if t["station"] == name)


def test_default_targets_go_to_the_high_rate_zone(report):
    assert report["defaults"]["targets"] == {"Zone A": 270.0}


def test_switch_is_the_measured_gap_plus_door(tmp_path, report):
    for door in (0.0, 1.5, 3.0):
        D = run_engine(tmp_path, report, door)["derived"][0]
        assert D["overall"]["med"] == pytest.approx(SWITCH_S + door)
        assert _station(D, "LABOR-1")["sw_med"] == pytest.approx(SWITCH_S + door)


def test_door_moves_seconds_from_picking_to_switch(tmp_path, report):
    a = run_engine(tmp_path, report, 0.0)["derived"][0]["budget"]["zones"][0]["rows"]
    b = run_engine(tmp_path, report, 2.0)["derived"][0]["budget"]["zones"][0]["rows"]
    for x, y in zip(a, b):
        assert y["pick_s"] == pytest.approx(x["pick_s"] - 2.0, abs=0.02)
        assert y["switch_s"] == pytest.approx(x["switch_s"] + 2.0, abs=0.02)
        assert y["wait_s"] == pytest.approx(x["wait_s"])
        assert y["cycle_s"] == pytest.approx(x["cycle_s"], abs=0.02)   # nothing counted twice


def test_budget_parts_add_up_to_the_real_cycle(tmp_path, report):
    rows = run_engine(tmp_path, report)["derived"][0]["budget"]["zones"][0]["rows"]
    for r in rows:
        assert r["pick_s"] + r["switch_s"] + r["wait_s"] == pytest.approx(r["cycle_s"], abs=0.03)
        assert r["cycle_s"] == pytest.approx(3600 / r["rate"], rel=0.02)
        assert r["budget_s"] == pytest.approx(3600 / 270, abs=0.01)
        assert r["over_s"] == pytest.approx(r["cycle_s"] - r["budget_s"], abs=0.02)


def test_over_and_under_budget(tmp_path, report):
    rows = {r["station"]: r for r in run_engine(tmp_path, report)["derived"][0]["budget"]["zones"][0]["rows"]}
    assert rows["LABOR-1"]["over_s"] < 0        # 8 s pick + 4 s switch fits 13.33 s
    assert rows["LABOR-2"]["over_s"] > 0        # 10 s + 4 s does not


def test_target_pickable_share(tmp_path, report):
    for door in (0.0, 2.0):
        D = run_engine(tmp_path, report, door)["derived"][0]
        expected = (1 - 270 * (SWITCH_S + door) / 3600) * 100
        assert _station(D, "LABOR-1")["target_util"] == pytest.approx(expected, abs=0.1)
        assert _station(D, "LABOR-3")["target_util"] is None      # Zone B has no target


def test_station_target_overrides_its_zone(tmp_path, report):
    D = run_engine(tmp_path, report, targets={"Zone A": 270, "LABOR-2": 200, "LABOR-3": 60})["derived"][0]
    assert _station(D, "LABOR-1")["target"] == 270
    assert _station(D, "LABOR-2")["target"] == 200
    assert _station(D, "LABOR-3")["target"] == 60
    zones = {z["zone"] for z in D["budget"]["zones"]}
    assert zones == {"Zone A", "Zone B"}


def test_zero_target_excludes_a_station(tmp_path, report):
    D = run_engine(tmp_path, report, targets={"Zone A": 270, "LABOR-2": 0})["derived"][0]
    stations = [r["station"] for z in D["budget"]["zones"] for r in z["rows"]]
    assert stations == ["LABOR-1"]


def test_no_targets_means_no_budget(tmp_path, report):
    D = run_engine(tmp_path, report, targets={})["derived"][0]
    assert D["budget"]["zones"] == []


def test_pickable_time_falls_with_the_door(tmp_path, report):
    a = _station(run_engine(tmp_path, report, 0.0)["derived"][0], "LABOR-1")["util"]
    b = _station(run_engine(tmp_path, report, 2.0)["derived"][0], "LABOR-1")["util"]
    assert b < a


def test_narrative_and_method_follow_the_settings(tmp_path, report):
    D = run_engine(tmp_path, report, 1.5)["derived"][0]
    assert "1.5 s of door travel" in " ".join(D["method"])
    assert D["text"]["switch"] and D["text"]["operator"]
    assert "Zone A" in D["text"]["budget"]
    assert set(D["text"]["utilization"]) == {"K50", "ACR"}


def test_summary_across_days(tmp_path, report):
    out = run_engine(tmp_path, report)
    S = out["summary"]
    assert S["days"] == ["2026-10-01", "2026-10-02", "2026-10-03"]
    for key, grid in S["by_station"].items():
        assert len(grid) == len(S["stations"]) and all(len(r) == 3 for r in grid), key
    for key, grid in S["by_hour"].items():
        assert len(grid) == 24 and all(len(r) == 3 for r in grid), key
    # the new per-hour rate is the day's rate_full, station by station
    i = S["stations"].index("LABOR-1")
    assert S["by_station"]["rate_full"][i] == [
        next(t["rate_full"] for t in d["station_table"] if t["station"] == "LABOR-1") for d in report["days"]]
    assert all(r["k50_util"] is not None for r in S["headline"])
    assert S["text"]["headline"].startswith("3 days")


def test_summary_needs_two_days(tmp_path, bases):
    one = build_payload(bases[:1], Settings())
    assert run_engine(tmp_path, one)["summary"] is None


# ── targets, the against-target split, presented vs possible, hours ───────────

def test_blank_pick_and_switch_targets_take_their_defaults(tmp_path, report):
    D = run_engine(tmp_path, report)["derived"][0]
    t = _station(D, "LABOR-1")
    assert t["target_switch"] == pytest.approx(t["sw_med"])               # measured switch
    assert t["target_pick"] == pytest.approx(3600 / 270 - t["sw_med"], abs=0.01)
    assert t["target_allowance"] == pytest.approx(0, abs=0.01)            # no waiting allowed
    assert t["pick_from"] == "budget" and t["switch_from"] == "measured"


def test_set_targets_leave_a_wait_allowance(tmp_path, report):
    settings = {"door_s": 0, "targets": {"Zone A": 270}, "pick_s": {"Zone A": 8}, "switch_s": {"LABOR-1": 3.5}}
    payload = tmp_path / "in.json"
    payload.write_text(json.dumps({"report": report, "settings": settings}), encoding="utf-8")
    script = tmp_path / "run.js"
    script.write_text(SCRIPT, encoding="utf-8")
    out = json.loads(subprocess.run([NODE, str(script), ENGINE, str(payload)], capture_output=True,
                                    text=True, check=True).stdout)
    t = _station(out["derived"][0], "LABOR-1")
    assert t["target_pick"] == 8 and t["target_switch"] == 3.5
    assert t["target_allowance"] == pytest.approx(3600 / 270 - 11.5, abs=0.01)
    row = next(r for r in out["derived"][0]["budget"]["zones"][0]["rows"] if r["station"] == "LABOR-1")
    assert row["wait_allowance_s"] == pytest.approx(1.83, abs=0.01)


def test_excesses_net_to_the_overrun(tmp_path, report):
    """Over-target pick, switch and wait always add up to cycle − budget."""
    rows = run_engine(tmp_path, report, 1.5)["derived"][0]["budget"]["zones"][0]["rows"]
    for r in rows:
        total = r["excess_pick_s"] + r["excess_switch_s"] + r["excess_wait_s"] + r["target_overflow_s"]
        assert total == pytest.approx(r["over_s"], abs=0.03)


def test_targets_that_overrun_the_budget_are_not_charged_to_waiting(tmp_path, report):
    settings = {"door_s": 0, "targets": {"Zone A": 270}, "pick_s": {"Zone A": 11}, "switch_s": {"Zone A": 4}}
    payload = tmp_path / "in.json"
    payload.write_text(json.dumps({"report": report, "settings": settings}), encoding="utf-8")
    script = tmp_path / "run.js"
    script.write_text(SCRIPT, encoding="utf-8")
    rows = json.loads(subprocess.run([NODE, str(script), ENGINE, str(payload)], capture_output=True,
                                     text=True, check=True).stdout)["derived"][0]["budget"]["zones"][0]["rows"]
    r = next(x for x in rows if x["station"] == "LABOR-1")
    assert r["target_overflow_s"] == pytest.approx(11 + 4 - 3600 / 270, abs=0.01)
    assert r["excess_wait_s"] == pytest.approx(r["wait_s"])          # no allowance, nothing hidden
    assert r["excess_pick_s"] + r["excess_switch_s"] + r["excess_wait_s"] + r["target_overflow_s"] == pytest.approx(r["over_s"], abs=0.03)


def test_possible_matches_presented_when_the_target_is_the_actual_pick(tmp_path, report):
    """LABOR-1 picks in exactly 8 s: with an 8 s target, possible = presented."""
    settings = {"door_s": 0, "targets": {"Zone A": 270}, "pick_s": {"LABOR-1": 8}}
    payload = tmp_path / "in.json"
    payload.write_text(json.dumps({"report": report, "settings": settings}), encoding="utf-8")
    script = tmp_path / "run.js"
    script.write_text(SCRIPT, encoding="utf-8")
    D = json.loads(subprocess.run([NODE, str(script), ENGINE, str(payload)], capture_output=True,
                                  text=True, check=True).stdout)["derived"][0]
    day = report["days"][0]
    s = day["stations"].index("LABOR-1")
    for h in day["full_hours"][1:-1]:
        assert D["hm_possible"][s][h] == pytest.approx(day["hm_visits"][s][h], abs=2)


def test_slots_needed_is_rate_times_lead(tmp_path, report):
    D = run_engine(tmp_path, report)["derived"][0]
    t = _station(D, "LABOR-1")
    assert t["slots_needed"] == pytest.approx(270 * t["lead_med"] / 3600, abs=0.05)


def test_hour_points_and_relations(tmp_path, report):
    out = run_engine(tmp_path, report)
    D = out["derived"][0]
    assert len(D["hours"]) == len(report["days"][0]["full_hours"])
    p = D["hours"][1]
    for key in ("wait_s", "totes", "k50_util", "k50_avail_util", "k50_away", "created", "ready"):
        assert key in p
    assert out["summary"]["relations"]["avg"]["k50_avail_util"] > 0
    assert isinstance(out["summary"]["text"]["relations"], str)


# ── stations without a door ───────────────────────────────────────────────────

def _run_with(tmp_path, report, settings):
    payload = tmp_path / "in.json"
    payload.write_text(json.dumps({"report": report, "settings": settings}), encoding="utf-8")
    script = tmp_path / "run.js"
    script.write_text(SCRIPT, encoding="utf-8")
    return json.loads(subprocess.run([NODE, str(script), ENGINE, str(payload)], capture_output=True,
                                     text=True, check=True).stdout)["derived"][0]


def test_a_station_without_a_door_gets_no_door_seconds(tmp_path, report):
    base = {"door_s": 2.0, "targets": {"Zone A": 270}}
    D = _run_with(tmp_path, report, {**base, "no_door": {"LABOR-2": True}})
    assert _station(D, "LABOR-1")["sw_med"] == pytest.approx(SWITCH_S + 2.0)
    assert _station(D, "LABOR-2")["sw_med"] == pytest.approx(SWITCH_S)
    assert _station(D, "LABOR-2")["door"] is False and _station(D, "LABOR-1")["door"] is True
    # its picks keep their logged length: pickable time as with no door at all
    plain = _run_with(tmp_path, report, {**base, "door_s": 0})
    assert _station(D, "LABOR-2")["util"] == pytest.approx(_station(plain, "LABOR-2")["util"])
    assert _station(D, "LABOR-1")["util"] < _station(plain, "LABOR-1")["util"]
    assert D["no_door"] == ["LABOR-2"]
    assert "LABOR-2 has no door" in " ".join(D["method"])


def test_zone_without_door_and_a_station_override(tmp_path, report):
    D = _run_with(tmp_path, report, {"door_s": 2.0, "targets": {"Zone A": 270},
                                     "no_door": {"Zone A": True, "LABOR-1": False}})
    assert _station(D, "LABOR-1")["door"] is True          # its own entry wins
    assert _station(D, "LABOR-2")["door"] is False         # inherits the zone's
    assert _station(D, "LABOR-3")["door"] is True          # other zone untouched


def test_budget_still_adds_up_with_mixed_doors(tmp_path, report):
    D = _run_with(tmp_path, report, {"door_s": 2.0, "targets": {"Zone A": 270}, "no_door": {"LABOR-2": True}})
    rows = {r["station"]: r for r in D["budget"]["zones"][0]["rows"]}
    plain = {r["station"]: r for r in _run_with(tmp_path, report, {"door_s": 0, "targets": {"Zone A": 270}})["budget"]["zones"][0]["rows"]}
    assert rows["LABOR-2"]["pick_s"] == pytest.approx(plain["LABOR-2"]["pick_s"])     # untouched
    assert rows["LABOR-1"]["pick_s"] == pytest.approx(plain["LABOR-1"]["pick_s"] - 2.0, abs=0.02)
    for r in rows.values():
        assert r["cycle_s"] == pytest.approx(plain[r["station"]]["cycle_s"], abs=0.02)


def test_overall_switch_mixes_the_doors(tmp_path, report):
    """Half the handovers shifted by the door, half not: the median sits between."""
    D = _run_with(tmp_path, report, {"door_s": 2.0, "targets": {}, "no_door": {"LABOR-2": True, "LABOR-3": True}})
    assert SWITCH_S <= D["overall"]["med"] <= SWITCH_S + 2.0
    assert D["overall"]["n"] == _run_with(tmp_path, report, {"door_s": 0, "targets": {}})["overall"]["n"]


# ── doors switched off for whole days ─────────────────────────────────────────

def test_a_day_with_doors_off_gets_no_door_seconds(tmp_path, bases):
    report = build_payload(bases, Settings())
    off_day, on_day = report["days"][0]["date"], report["days"][1]["date"]
    settings = {"door_s": 2.0, "targets": {"Zone A": 270}, "no_door_days": {off_day: True}}
    payload = tmp_path / "in.json"
    payload.write_text(json.dumps({"report": report, "settings": settings}), encoding="utf-8")
    script = tmp_path / "run.js"
    script.write_text(SCRIPT, encoding="utf-8")
    out = json.loads(subprocess.run([NODE, str(script), ENGINE, str(payload)], capture_output=True,
                                    text=True, check=True).stdout)["derived"]
    off, on = out[0], out[1]
    assert off["door"] == 0 and off["door_day_off"] is True
    assert _station(off, "LABOR-1")["sw_med"] == pytest.approx(SWITCH_S)
    assert _station(on, "LABOR-1")["sw_med"] == pytest.approx(SWITCH_S + 2.0)
    assert "switched off for this day" in " ".join(off["method"])
    assert on["door_day_off"] is False


def test_multi_station_pools_days(tmp_path, report):
    out = run_engine(tmp_path, report)
    days = [D["multi"] for D in out["derived"]]
    S = out["summary"]["multi"]
    assert S["handovers"] == sum(d["handovers"] for d in days)
    assert S["starved"] == sum(d["starved"] for d in days)
    assert S["hist"] == [sum(x) for x in zip(*(d["hist"] for d in days))]
    assert S["cycles"] == sum(b["robot_k50"]["total"] for b in report["days"])
    # Plain handovers never wait in the synthetic day, so every starved
    # handover involves a multi-station tote.
    if S["starved"]:
        assert S["starved_multi_pct"] == 100
    assert out["derived"][0]["text"]["multi"]
    assert out["summary"]["text"]["multi"]
    assert any(p["multi_pct"] for p in out["derived"][0]["hours"])


def test_starvation_pools_days(tmp_path, report):
    out = run_engine(tmp_path, report)
    days = [D["starve"] for D in out["derived"]]
    S = out["summary"]["starve"]
    assert S["handovers"] == sum(d["handovers"] for d in days)
    assert S["starved"] == sum(d["starved"] for d in days)
    assert sum(x["pct"] or 0 for x in S["stages"]) == pytest.approx(100, abs=0.5)
    assert sum(x["n"] for x in S["en_route"]) == S["handovers"]
    # Pickup → arrival is PRE_S for every trip; the binned median lands in its bin.
    assert PRE_S <= S["travel"]["med"] < PRE_S + S["travel_w"]
    text = out["summary"]["text"]["starve"]
    assert text["stages"] and text["context"]
    assert any(p["travel_s"] for p in out["derived"][0]["hours"])


def test_average_switch_moves_with_the_door(tmp_path, report):
    for door in (0.0, 2.0):
        out = run_engine(tmp_path, report, door=door)
        D = out["derived"][0]
        for t in D["stations"]:
            # Most switches take SWITCH_S; a few chained arrivals take longer.
            assert t["sw_mean"] >= SWITCH_S + door - 0.05
            assert t["sw_mean"] >= t["sw_med"] - 0.05
        assert D["overall"]["mean"] >= D["overall"]["med"] - 0.05
    assert out["summary"]["headline"][0]["sw_mean"] == out["derived"][0]["overall"]["mean"]
    assert out["summary"]["headline"][0]["op_mean"] == report["days"][0]["overall"]["op_mean"]


def test_spatial_pools_days(tmp_path, report):
    out = run_engine(tmp_path, report)
    days = [D["spatial"] for D in out["derived"]]
    S = out["summary"]["spatial"]
    assert S["sources"]["puts"] == sum(d["sources"]["puts"] for d in days)
    assert sum(map(sum, S["sources"]["grid"])) == S["sources"]["puts"]
    assert sum(x["n"] for x in S["sources"]["by_aisle"]) == S["sources"]["puts"]
    for fleet in ("K50", "ACR"):
        c = S["crowd"][fleet]
        assert c["trips"] == sum(d["crowd"][fleet]["trips"] for d in days)
        # Fixed leads: sharing an aisle adds nothing.
        assert all(b["added_s"] in (None, 0) for b in c["bins"])
    assert S["returns"]["stores"] == sum(d["returns"]["stores"] for d in days)
    assert S["returns"]["within_60"] == 0
    # Every trip from the buffer takes PRE_S, whichever aisle it starts in.
    for st in S["travel"]["by_station"]:
        assert st["mean_s"] == PRE_S
    text = out["summary"]["text"]["spatial"]
    assert text["sources"] and text["crowd"] and text["returns"]
    assert out["derived"][0]["text"]["spatial"]["sources"]
