"""Where a K50 cycle's time goes, and station closures kept apart from starvation."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from config import CLOSED_HOLD_S
from log_parser import parse_logs
from metrics import K50_SEGMENTS, day_base, handovers, k50_cycles, visits
from tests import synthetic
from tests.synthetic import ALLOC_LEAD_S, POST_S, PRE_S


def test_cycle_segments_follow_the_synthetic_timings(base):
    k = base["k50_time"]
    assert k["n"] > 0 and k["no_alloc"] == 0
    seg = k["segments"]
    assert set(seg) == set(K50_SEGMENTS)
    # Every pickup → arrival takes PRE_S, so the free-flow trip is all of it.
    assert seg["queue"] == pytest.approx(0, abs=0.5)
    assert seg["fetch"] / k["n"] == pytest.approx(ALLOC_LEAD_S, abs=0.01)
    assert seg["travel"] / k["n"] == pytest.approx(PRE_S, abs=0.01)
    assert seg["return"] / k["n"] == pytest.approx(POST_S, abs=0.01)
    assert sum(k["queue_hist"]) == k["n"]
    assert sum(x["n"] for x in k["stations"]) == k["n"]
    for x in k["stations"]:
        assert sum(x["segments"].values()) > 0


def test_cycle_segments_sum_to_the_cycle(data, base):
    """fetch + travel + queue + at station + return = allocation → buffer return."""
    k = base["k50_time"]
    c = k50_cycles(data)
    c = c[c["n_st"] >= 1].merge(data.allocations.drop_duplicates("task")[["task", "ts"]], on="task")
    c = c[c["t_unload"].dt.hour.isin(base["full_hours"])]
    whole = (c["t_unload"] - c["ts"]).dt.total_seconds()
    # The cycle set is chosen by first arrival, so compare per cycle, not in total.
    assert sum(k["segments"].values()) / k["n"] == pytest.approx(whole.mean(), rel=0.02)


# ── closures ──────────────────────────────────────────────────────────────────
# One extra station, LABOR-9, after the synthetic shift: robots 10 s apart,
# then one held over CLOSED_HOLD_S, one gap with the station disabled, and one
# genuine wait.
EXTRA = "LABOR-9"


def _reach(ts, robot, tote):
    return synthetic._callback(ts, {
        "eventCode": "CALLBACK_OF_ROBOT_REACH_STATION", "robotCode": robot,
        "robotTypeCode": "RT_KUBOT_MINI_HAIFLEX", "stationCode": EXTRA,
        "locationCode": "LT_LABOR:POINT:900:999", "containerCode": tote,
        "trays": [{"containerCode": tote}], "callId": "1"})


def _visit(lines, at, robot, op_s):
    lines.append(_reach(at, robot, "T" + robot))
    lines.append(synthetic._leave(at + timedelta(seconds=op_s), EXTRA, robot))
    return at + timedelta(seconds=op_s)


@pytest.fixture(scope="module")
def closed_day(tmp_path_factory):
    lines = synthetic.build()
    t = datetime(2026, 10, 1, 11, 0, 0)
    for i in range(8):                                          # normal: 10 s gaps
        t = _visit(lines, t, f"kubot-9{i:02d}", 5) + timedelta(seconds=10)
    rel = _visit(lines, t, "kubot-950", CLOSED_HOLD_S + 60)     # held: a break
    t = rel + timedelta(seconds=300)
    rel = _visit(lines, t, "kubot-951", 5)                      # then disabled
    lines.append(synthetic._created(rel + timedelta(seconds=5), "ND9999999", EXTRA))
    lines.append(synthetic._callback(rel + timedelta(seconds=30), {
        "eventCode": "CALLBACK_OF_TASK_EXCEPTION", "eventType": "non_executable_outbound_task",
        "containerCode": "X1", "message": "DISABLED_TARGET", "taskCode": ["ND9999999"], "callId": "1"}))
    t = rel + timedelta(seconds=200)
    rel = _visit(lines, t, "kubot-952", 5)                      # a genuine wait
    t = rel + timedelta(seconds=60)
    for i in range(4):
        t = _visit(lines, t, f"kubot-96{i}", 5) + timedelta(seconds=10)
    p = tmp_path_factory.mktemp("closed") / "play_extract_2026-10-01.log"
    p.write_text("\n".join(sorted(lines)) + "\n", encoding="utf-8")
    return parse_logs(str(p))


def test_task_exceptions_are_parsed(closed_day):
    ex = closed_day.exceptions
    assert list(ex["task"]) == ["ND9999999"] and list(ex["message"]) == ["DISABLED_TARGET"]


def test_closed_handovers_are_not_starvation(closed_day):
    v, _, _ = visits(closed_day, closed_day.date)
    _, ho = handovers(closed_day, v, k50_cycles(closed_day))
    h = ho[ho["station"] == EXTRA]
    assert int(h["held"].sum()) == 1
    assert int((h["disabled"] & ~h["held"]).sum()) == 1
    assert not (h["closed"] & h["starved"]).any()
    # The one genuine wait is still starvation.
    assert int(h["starved"].sum()) == 1
    assert h.loc[h["starved"], "wait"].iloc[0] == pytest.approx(50, abs=0.5)


def test_closures_are_reported_apart(closed_day):
    b = day_base(closed_day)
    c = b["starve"]["closed"]
    assert (c["n"], c["held"], c["disabled"]) == (2, 1, 1)
    assert c["wait_s"] > 400 and c["hold_s"] == CLOSED_HOLD_S
    row = next(x for x in b["starve"]["stations"] if x["station"] == EXTRA)
    assert row["closed_n"] == 2
    assert sum(h["closed_s"] for h in b["starve"]["hourly"]) == pytest.approx(c["wait_s"], abs=0.5)


def test_pick_bands_cover_every_handover(base):
    s = base["starve"]
    assert sum(x["n"] for x in s["pick"]) == s["handovers"]
    assert sum(x["starved"] for x in s["pick"]) == s["starved"]
    assert len(s["pick_edges"]) == len(s["pick"])
    assert s["closed"]["n"] == 0
