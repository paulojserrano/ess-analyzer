"""End to end: logs in, reports out."""
from __future__ import annotations

import json
import os
import re

import pytest

from config import CONFIG_FILENAME, Settings, load_settings
from pipeline import find_logs, run
from report import REPORT_NAME
from tests.synthetic import write


@pytest.fixture()
def two_days(tmp_path):
    folder = tmp_path / "logs"
    folder.mkdir()
    write(str(folder / "play_extract_2026-10-01.log"), date="2026-10-01")
    write(str(folder / "play_extract_2026-10-02.log.gz"), date="2026-10-02")
    return folder


def test_find_logs_groups_and_ignores_other_files(two_days):
    (two_days / "notes.txt").write_text("x", encoding="utf-8")
    groups = find_logs([str(two_days)])
    assert len(groups) == 2
    assert all(len(g) == 1 for g in groups)


def test_find_logs_accepts_a_folder_or_files(two_days):
    by_folder = find_logs([str(two_days)])
    by_file = find_logs([str(p) for p in sorted(two_days.iterdir()) if "play_extract" in p.name])
    assert by_folder == by_file


def _payload(path: str) -> dict:
    html = open(path, encoding="utf-8").read()
    return json.loads(re.search(r"const REPORT=(\{.*?\});\n</script>", html, re.S).group(1))


def test_single_day_writes_one_report(tmp_path, two_days):
    one = find_logs([str(two_days / "play_extract_2026-10-01.log")])
    result = run(one, Settings(output_root=str(tmp_path / "out")))
    assert len(result.completed) == 1
    assert os.path.isfile(result.report) and result.report.endswith(REPORT_NAME)
    assert [d["date"] for d in _payload(result.report)["days"]] == ["2026-10-01"]


def test_every_day_goes_into_one_report(tmp_path, two_days):
    result = run(find_logs([str(two_days)]), Settings(output_root=str(tmp_path / "out")))
    assert len(result.completed) == 2
    files = [f for f in os.listdir(result.run_dir)]
    assert files == [REPORT_NAME]                      # one file, no per-day folders
    assert [d["date"] for d in _payload(result.report)["days"]] == ["2026-10-01", "2026-10-02"]


def test_a_bad_log_does_not_sink_the_run(tmp_path, two_days):
    (two_days / "play_extract_2026-10-03.log").write_text("not a log\n", encoding="utf-8")
    result = run(find_logs([str(two_days)]), Settings(output_root=str(tmp_path / "out")))
    assert len(result.completed) == 2
    failed = [d for d in result.days if not d.ok]
    assert len(failed) == 1 and failed[0].error
    assert result.warnings


def test_run_fails_when_no_day_works(tmp_path):
    bad = tmp_path / "play_extract_2026-10-01.log"
    bad.write_text("not a log\n", encoding="utf-8")
    with pytest.raises(ValueError, match="No day could be analysed"):
        run(find_logs([str(bad)]), Settings(output_root=str(tmp_path / "out")))


def test_settings_become_the_report_defaults(tmp_path, two_days):
    one = find_logs([str(two_days / "play_extract_2026-10-01.log")])
    result = run(one, Settings(door_s=2.0, targets={"LABOR-3": 60}, output_root=str(tmp_path / "out")))
    d = _payload(result.report)["defaults"]
    assert d["door_s"] == 2.0
    assert d["targets"] == {"LABOR-3": 60.0, "Zone A": 270.0}


def test_invalid_settings_are_rejected(tmp_path, two_days):
    with pytest.raises(ValueError, match="door_s"):
        run(find_logs([str(two_days)]), Settings(door_s=-1, output_root=str(tmp_path)))


def test_runs_do_not_overwrite_each_other(tmp_path, two_days):
    one = find_logs([str(two_days / "play_extract_2026-10-01.log")])
    out = str(tmp_path / "out")
    a = run(one, Settings(output_root=out))
    b = run(one, Settings(output_root=out))
    assert a.run_dir != b.run_dir


# ── settings file ─────────────────────────────────────────────────────────────

def test_config_file_is_read(tmp_path):
    (tmp_path / CONFIG_FILENAME).write_text(
        json.dumps({"door_s": 1.5, "target_rate": 300, "targets": {"Zone B": 60, "LABOR-12": 80}}),
        encoding="utf-8")
    s, problems = load_settings(str(tmp_path))
    assert not problems
    assert s.door_s == 1.5 and s.target_rate == 300
    assert s.targets == {"Zone B": 60.0, "LABOR-12": 80.0}


def test_pick_and_switch_targets_in_config(tmp_path):
    (tmp_path / CONFIG_FILENAME).write_text(
        json.dumps({"pick_s": {"Zone A": 8.5}, "switch_s": {"LABOR-3": 5}}), encoding="utf-8")
    s, problems = load_settings(str(tmp_path))
    assert not problems
    assert s.pick_s == {"Zone A": 8.5} and s.switch_s == {"LABOR-3": 5.0}


def test_bad_targets_in_config_are_reported(tmp_path):
    (tmp_path / CONFIG_FILENAME).write_text(json.dumps({"targets": [270]}), encoding="utf-8")
    _, problems = load_settings(str(tmp_path))
    assert any("targets" in p for p in problems)


def test_missing_config_file_is_fine(tmp_path):
    s, problems = load_settings(str(tmp_path))
    assert not problems and s.door_s == Settings().door_s


def test_bad_config_file_is_reported(tmp_path):
    (tmp_path / CONFIG_FILENAME).write_text("{not json", encoding="utf-8")
    _, problems = load_settings(str(tmp_path))
    assert problems and "could not be read" in problems[0]


def test_out_of_range_config_is_reported(tmp_path):
    (tmp_path / CONFIG_FILENAME).write_text(json.dumps({"door_s": 999}), encoding="utf-8")
    _, problems = load_settings(str(tmp_path))
    assert any("door_s" in p for p in problems)


def test_a_folder_named_like_a_log_is_ignored(tmp_path, two_days):
    """Unpacking an archive leaves a folder called "play_extract_….log"."""
    (two_days / "play_extract_2026-10-01.log.d").mkdir()
    os.rename(two_days / "play_extract_2026-10-01.log.d",
              two_days / "play_extract_2026-10-03.log")
    groups = find_logs([str(two_days)])
    assert all(os.path.isfile(p) for g in groups for p in g)
    assert len(groups) == 2


def test_no_door_in_config_as_list_or_map(tmp_path):
    (tmp_path / CONFIG_FILENAME).write_text(json.dumps({"no_door": ["LABOR-8", "Zone B"]}), encoding="utf-8")
    s, problems = load_settings(str(tmp_path))
    assert not problems and s.no_door == {"LABOR-8": True, "Zone B": True}
    (tmp_path / CONFIG_FILENAME).write_text(json.dumps({"no_door": {"Zone B": True, "LABOR-9": False}}), encoding="utf-8")
    s, problems = load_settings(str(tmp_path))
    assert not problems and s.no_door == {"Zone B": True, "LABOR-9": False}
    (tmp_path / CONFIG_FILENAME).write_text(json.dumps({"no_door": "LABOR-8"}), encoding="utf-8")
    _, problems = load_settings(str(tmp_path))
    assert any("no_door" in p for p in problems)


def test_no_door_reaches_the_report_defaults(tmp_path, two_days):
    one = find_logs([str(two_days / "play_extract_2026-10-01.log")])
    result = run(one, Settings(no_door={"LABOR-3": True}, output_root=str(tmp_path / "out")))
    assert _payload(result.report)["defaults"]["no_door"] == {"LABOR-3": True}


def test_cli_no_door_option(tmp_path, two_days):
    import app
    args = app._parse_args([str(two_days), "--no-door", "LABOR-3, Zone B"])
    s, problems = app._settings(args, find_logs([str(two_days)]))
    assert not problems and s.no_door == {"LABOR-3": True, "Zone B": True}


def test_no_door_days_in_config_and_cli(tmp_path, two_days):
    (tmp_path / CONFIG_FILENAME).write_text(json.dumps({"no_door_days": ["2026-10-02"]}), encoding="utf-8")
    s, problems = load_settings(str(tmp_path))
    assert not problems and s.no_door_days == ["2026-10-02"]
    (tmp_path / CONFIG_FILENAME).write_text(json.dumps({"no_door_days": ["Oct 2"]}), encoding="utf-8")
    _, problems = load_settings(str(tmp_path))
    assert any("no_door_days" in p for p in problems)
    import app
    args = app._parse_args([str(two_days), "--no-door-days", "2026-10-01, 2026-10-02"])
    s, problems = app._settings(args, find_logs([str(two_days)]))
    assert not problems and s.no_door_days == ["2026-10-01", "2026-10-02"]


def test_no_door_days_reach_the_report(tmp_path, two_days):
    result = run(find_logs([str(two_days)]), Settings(no_door_days=["2026-10-02"], output_root=str(tmp_path / "out")))
    assert _payload(result.report)["defaults"]["no_door_days"] == {"2026-10-02": True}


def test_starved_threshold_in_config(tmp_path):
    (tmp_path / CONFIG_FILENAME).write_text(json.dumps({"starved_s": 2.5}), encoding="utf-8")
    s, problems = load_settings(str(tmp_path))
    assert problems == [] and s.starved_s == 2.5
    assert Settings().starved_s == 1.0
    assert Settings(starved_s=-1).validate()


def test_starved_threshold_reaches_the_report(tmp_path, two_days):
    res = run(find_logs([str(two_days)]), Settings(starved_s=3.0, output_root=str(tmp_path)))
    assert all(d.metrics["starve"]["starved_s"] == 3.0 and d.metrics["multi"]["starved_s"] == 3.0
               for d in res.days if d.ok)
