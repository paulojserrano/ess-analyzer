"""Reading the play_extract log into native frames."""
from __future__ import annotations

import gzip
import os

import pytest

import log_parser as lp
from tests.synthetic import ACR_IDS, PICK_S, STATIONS, write


def test_detects_log_paths(tmp_path):
    assert lp.is_log_path("a/play_extract_2026-10-01.log")
    assert lp.is_log_path("a/play_extract_2026-10-01.LOG.GZ")
    assert not lp.is_log_path("a/export.xlsx")
    assert not lp.is_log_path("a/archive.tar.gz")


def test_stem_and_date():
    assert lp.log_stem("x/play_extract_2026-10-01.log.gz") == "play_extract_2026-10-01"
    assert lp.log_stem("x/play_extract_2026-10-01.log") == "play_extract_2026-10-01"
    assert lp.log_date("x/play_extract_2026-10-01.log.gz") == "2026-10-01"
    assert lp.log_date("x/nodate.log") is None


def test_groups_split_logs_by_date():
    groups = lp.group_by_day([
        "a/play_extract_2026-10-01.000001.log",
        "a/play_extract_2026-10-01.000000.log",
        "a/play_extract_2026-10-02.log.gz",
    ])
    assert len(groups) == 2
    assert [os.path.basename(p) for p in groups[0]] == [
        "play_extract_2026-10-01.000000.log", "play_extract_2026-10-01.000001.log"]


def test_parses_events(data):
    assert set(data.stations) == set(STATIONS)
    assert len(data.arrivals) == len(data.releases)
    assert set(data.roles.values()) == {"K50", "ACR"}
    assert data.robots("ACR") == set(ACR_IDS)
    assert str(data.date.date()) == "2026-10-01"


def test_release_is_the_will_leave_line(data):
    """TASK_FINISHED fires on arrival, so it must never be read as a release."""
    arr = data.arrivals.sort_values("ts").iloc[0]
    rel = data.releases[(data.releases["robot"] == arr["robot"])
                        & (data.releases["ts"] > arr["ts"])].iloc[0]
    assert (rel["ts"] - arr["ts"]).total_seconds() == PICK_S[arr["station"]]


def test_ignores_non_operator_stations(tmp_path):
    from tests.synthetic import _callback, build
    import datetime as dt
    lines = build()
    lines.append(_callback(dt.datetime(2026, 10, 1, 7, 0), {
        "eventCode": "CALLBACK_OF_ROBOT_REACH_STATION", "robotCode": "kubot-301",
        "stationCode": "CS-005", "locationCode": "LT_CONVEYOR_1", "callId": "1"}))
    p = tmp_path / "play_extract_2026-10-01.log"
    p.write_text("\n".join(sorted(lines)), encoding="utf-8")
    data = lp.parse_logs(str(p))
    assert data.skipped_stations == {"CS-005": 1}
    assert "CS-005" not in data.stations


def test_gzip_matches_plain(log_file, log_gz):
    plain, packed = lp.parse_logs(log_file), lp.parse_logs(log_gz)
    assert len(plain.arrivals) == len(packed.arrivals)
    assert len(plain.moves) == len(packed.moves)


def test_event_code_spacing_is_tolerated(tmp_path):
    """Some writers emit '"eventCode": "X"' with a space; both must parse."""
    p = tmp_path / "play_extract_2026-10-01.log"
    write(str(p))
    text = p.read_text(encoding="utf-8")
    p.write_text(text.replace('"eventCode": "', '"eventCode":"'), encoding="utf-8")
    assert len(lp.parse_logs(str(p)).arrivals) > 0


def test_rejects_unreadable_input(tmp_path):
    missing = tmp_path / "play_extract_2026-10-01.log"
    with pytest.raises(lp.LogError, match="not found"):
        lp.parse_logs(str(missing))

    wrong = tmp_path / "notes.txt"
    wrong.write_text("hello", encoding="utf-8")
    with pytest.raises(lp.LogError, match="Not a log file"):
        lp.parse_logs(str(wrong))

    empty = tmp_path / "play_extract_2026-10-02.log"
    empty.write_text("nothing useful here\n", encoding="utf-8")
    with pytest.raises(lp.LogError, match="No usable events"):
        lp.parse_logs(str(empty))


def test_truncated_gzip_is_reported(tmp_path):
    good = tmp_path / "play_extract_2026-10-01.log.gz"
    write(str(good))
    broken = tmp_path / "play_extract_2026-10-02.log.gz"
    broken.write_bytes(good.read_bytes()[: 2000])
    with pytest.raises(lp.LogError):
        lp.parse_logs(str(broken))


def test_pair_moves_can_restrict_the_load_side(data):
    """A K50 re-loading at a station must not start a new buffer cycle.

    Both pairings return one row per unload; what changes is where the cycle is
    taken to begin.  Restricting the load side pushes a chained cycle's start
    back to the buffer pickup, so the window covers both station visits.
    """
    k50 = data.robots("K50")
    everything = lp.pair_moves(data.tote_events, robots=k50)
    buffer_only = lp.pair_moves(data.tote_events, load_contains=lp.BUFFER_K50, robots=k50)
    assert buffer_only["from_loc"].str.contains(lp.BUFFER_K50).all()
    assert not everything["from_loc"].str.contains(lp.BUFFER_K50).all()

    key = ["robot", "tote", "t_unload"]
    merged = everything.merge(buffer_only, on=key, suffixes=("_all", "_buf"))
    assert (merged["t_load_buf"] <= merged["t_load_all"]).all()
    assert (merged["t_load_buf"] < merged["t_load_all"]).any()   # the chained cycles


def test_allocations_are_kept(data):
    assert len(data.allocations) > 0
    assert set(data.allocations.columns) >= {"ts", "robot", "task"}
    # an ACR allocated an ND task is still an ACR
    assert data.robots("ACR") == set(ACR_IDS)


def test_moves_carry_their_task(data):
    assert data.moves["task"].str.startswith("ND").any()


def test_quick_scan_finds_every_station(log_file, log_gz):
    for path in (log_file, log_gz):
        pts = lp.scan_stations(path)
        assert set(pts) == set(STATIONS)
        assert pts["LABOR-3"][1] != pts["LABOR-1"][1]


def test_zones_from_points():
    z = lp.assign_zones({"LABOR-1": (1, 500), "LABOR-2": (2, 500), "LABOR-9": (3, 900)}, ["LABOR-20"])
    assert z == {"LABOR-1": "Zone A", "LABOR-2": "Zone A", "LABOR-9": "Zone B", "LABOR-20": "LABOR-20"}
