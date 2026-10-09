"""Problem locations: flagged buffer pickups, failed put-aways and stuck storage slots."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from log_parser import parse_logs
from metrics import STUCK_MIN_ATTEMPTS, day_base
from tests import synthetic

BAD_SLOT = "HAI-001-001-01_1_coop_haiflex"


def _ts(line: str) -> datetime:
    return datetime.strptime(line.split("] ", 1)[1][:23], "%Y-%m-%d %H:%M:%S,%f")


@pytest.fixture(scope="module")
def faulty(tmp_path_factory):
    lines = synthetic.build()
    flagged = 0
    loads = [ln for ln in lines if "TOTE_LOADED_BY_ROBOT" in ln and BAD_SLOT in ln]
    for i, ln in enumerate(sorted(loads)):
        if i % 2:
            continue
        msg = json.loads(ln[ln.find("message: ") + 9:])
        # The flag comes a few seconds before the load it belongs to.
        lines.append(synthetic._callback(_ts(ln) - timedelta(seconds=6), {
            "eventCode": "CALLBACK_OF_LOCATION_ABNORMAL", "stationCode": "LA_MINI_HAIFLEX_SHELF_STORAGE",
            "locationCode": BAD_SLOT, "containerCode": msg["containerCode"],
            "message": "LOAD_FAILED_COUNT_EXCEEDED_THE_LIMIT", "callId": "1"}))
        flagged += 1
    # A stuck storage slot: an ACR allocated, then failing to load, again and again.
    t = datetime(2026, 10, 1, 7, 0, 0)
    for i in range(STUCK_MIN_ATTEMPTS + 1):
        task = f"ND88{i:06d}"
        lines.append(synthetic._alloc(t, "kubot-1", task))
        lines.append(synthetic._callback(t + timedelta(seconds=40), {
            "eventCode": "CALLBACK_OF_TOTE_LOAD_FAILED", "taskCode": task, "actionCode": "load",
            "robotCode": "kubot-1", "containerCode": "STUCK1", "stationCode": "LA_SHELF_STORAGE",
            "locationCode": "HAI-009-009-09_1", "failedReason": "END_ACTION_FAILED", "callId": "1"}))
        t += timedelta(minutes=2)
    # Two failed put-aways, both at rear slots.
    for k in range(2):
        lines.append(synthetic._callback(datetime(2026, 10, 1, 8, k, 0), {
            "eventCode": "CALLBACK_OF_TASK_SUSPENDED", "robotCode": "kubot-2", "containerCode": f"P{k}",
            "stationCode": "LA_SHELF_STORAGE", "locationCode": f"HAI-00{k + 3}-087-02_2",
            "message": "hooked failed,fail to put the box!", "callId": "1"}))
    p = tmp_path_factory.mktemp("faults") / "play_extract_2026-10-01.log"
    p.write_text("\n".join(sorted(lines)) + "\n", encoding="utf-8")
    data = parse_logs(str(p))
    return data, day_base(data), flagged


def test_faults_are_parsed_and_do_not_change_roles(faulty, data):
    d, _, _ = faulty
    assert set(d.faults["kind"]) == {"abnormal", "load_failed", "suspended"}
    assert d.robots("K50") == data.robots("K50")


def test_flagged_pickups_are_counted_per_slot(faulty):
    _, b, flagged = faulty
    B = b["faults"]["buffer"]
    assert B["flagged"] == flagged == B["flags"]
    assert sum(map(sum, B["n"])) == B["pickups"] and sum(map(sum, B["k"])) == B["flagged"]
    a, y = B["aisles"].index(1), B["bays"].index(1)
    assert B["k"][a][y] == flagged
    assert B["k"][B["aisles"].index(2)][y] == 0
    assert sum(B["lag_hist"]) == flagged and B["lag_hist"][6] == flagged
    assert sum(r[2] for r in B["robots"]) == flagged
    assert sum(h[1] for h in B["hourly"]) == flagged


def test_repeat_separates_slot_from_tote(faulty):
    B = faulty[1]["faults"]["buffer"]
    s_ = B["repeat"]["slot"]
    # Every other pickup at the bad slot is flagged, so after a flag the slot's
    # next pickup almost never is (pickups in the same millisecond can swap
    # order), while after a clean one — at either slot — a third are.
    assert s_[0] > 100 and s_[1] / s_[0] < 0.05
    assert s_[3] / s_[2] > 10 * s_[1] / s_[0]


def test_storage_faults(faulty):
    St = faulty[1]["faults"]["storage"]
    assert St["put_failed"] == {"1": 0, "2": 2}
    assert St["failed_loads"] == STUCK_MIN_ATTEMPTS + 1
    (stuck,) = St["stuck"]
    assert stuck["loc"] == "HAI-009-009-09_1" and stuck["attempts"] == STUCK_MIN_ATTEMPTS + 1
    assert stuck["acr_s"] == pytest.approx(40 * (STUCK_MIN_ATTEMPTS + 1))
    assert St["putaways"]["1"] + St["putaways"]["2"] > 0


def test_clean_day_has_no_buffer_flags(base):
    f = base.get("faults")
    assert f is None or not f.get("buffer") or f["buffer"]["flagged"] == 0
