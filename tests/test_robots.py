"""Robot health: per-robot work, speed and faults, and the engine's tests on them."""
from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timedelta

import pytest

from log_parser import parse_logs
from metrics import STUCK_MIN_ATTEMPTS, day_base
from tests import synthetic
from tests.test_engine import ENGINE

BAD = "kubot-301"
LIFT = 12


@pytest.fixture(scope="module")
def day(tmp_path_factory):
    lines = synthetic.build()
    t = datetime(2026, 10, 1, 7, 0, 0)
    for i in range(LIFT):
        lines.append(synthetic._callback(t + timedelta(minutes=5 * i), {
            "eventCode": "CALLBACK_OF_ROBOT_ABNORMAL", "robotCode": BAD, "alleyNo": -1, "errorCode": "ROBOT_COMMAND_FAILURE",
            "message": "[APPROCH_TARGETS] command cancel,actuator(lift) report dislocation!", "callId": "1"}))
    # A stuck storage slot: failed loads there are the slot's, not the robot's.
    for i in range(STUCK_MIN_ATTEMPTS):
        lines.append(synthetic._callback(t + timedelta(minutes=3 * i, seconds=7), {
            "eventCode": "CALLBACK_OF_TOTE_LOAD_FAILED", "taskCode": f"ND77{i}", "robotCode": "kubot-2",
            "containerCode": "S1", "locationCode": "HAI-005-005-05_1", "failedReason": "END_ACTION_FAILED", "callId": "1"}))
    p = tmp_path_factory.mktemp("robots") / "play_extract_2026-10-01.log"
    p.write_text("\n".join(sorted(lines)) + "\n", encoding="utf-8")
    data = parse_logs(str(p))
    return data, day_base(data)


def test_robot_rows_cover_the_fleets(day):
    data, b = day
    R = b["robots"]
    assert {r[0] for r in R["K50"]} == data.robots("K50")
    assert sum(r[2] for r in R["K50"]) == b["robot_k50"]["total"]
    al = data.allocations
    k50_tasks = int(al["robot"].isin(data.robots("K50")).sum())
    assert sum(r[1] for r in R["K50"]) == k50_tasks
    # Every return trip takes POST_S, so each robot's return equals its expected.
    for _, _, cyc, _, ret, exp in R["K50"]:
        if cyc:
            assert ret == pytest.approx(exp)
    assert R["ACR"] and all(r[2] > 0 for r in R["ACR"])
    # Expected is the group mean of the capped trips, so the fleet's index is exactly 1.
    for fleet, a, e in (("K50", 4, 5), ("ACR", 3, 4)):
        assert sum(r[a] for r in R[fleet]) == pytest.approx(sum(r[e] for r in R[fleet]), rel=1e-3)


def test_robot_faults_by_kind(day):
    R = day[1]["robots"]
    assert R["faults"]["lift"] == {BAD: LIFT}
    assert "load_failed" not in R["faults"]                # all at the stuck slot
    assert R["stuck_left_out"] == STUCK_MIN_ATTEMPTS


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_engine_flags_the_robot(day, tmp_path):
    b = day[1]
    src = tmp_path / "in.json"
    src.write_text(json.dumps({"robots": b["robots"], "faults": b.get("faults")}), encoding="utf-8")
    js = (f"const E=require({json.dumps(ENGINE)});const fs=require('fs');"
          f"const x=JSON.parse(fs.readFileSync({json.dumps(str(src))},'utf8'));"
          "const R=E.robotStats([x.robots],[x.faults]);process.stdout.write(JSON.stringify({R,T:E.robotText(R,'today')}));")
    out = json.loads(subprocess.run([shutil.which("node"), "-e", js], capture_output=True, text=True, check=True).stdout)
    R = out["R"]
    lift = next(k for k in R["kinds"] if k["key"] == "lift")
    assert lift["events"] == LIFT and lift["robots_with"] == 1 and lift["robot_bound"]
    assert any(f["robot"] == BAD and f["kind"] == "lift" for f in R["flagged"])
    assert R["k50"]["index_med"] == pytest.approx(1)
    assert not R["k50"]["slow"]
    row = next(x for x in R["rows"] if x["robot"] == BAD)
    assert row["above"] == ["Lift dislocation"] and row["faults"] == LIFT
    assert out["T"]["faults"] and out["T"]["perf"]
