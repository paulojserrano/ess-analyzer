"""The local web API behind the UI."""
from __future__ import annotations

import os
import time

import pytest
from fastapi.testclient import TestClient

from server import create_app
from tests.synthetic import write


@pytest.fixture()
def client(tmp_path):
    app = create_app(output_root=str(tmp_path / "out"))
    with TestClient(app) as c:
        c.app_state = app.state.session
        yield c


@pytest.fixture()
def logs(tmp_path):
    folder = tmp_path / "logs"
    folder.mkdir()
    write(str(folder / "play_extract_2026-10-01.log"), date="2026-10-01")
    write(str(folder / "play_extract_2026-10-02.log.gz"), date="2026-10-02")
    return folder


def test_index_and_meta(client):
    assert client.get("/").status_code == 200
    meta = client.get("/api/meta").json()
    assert meta["app"] and "door_s" in meta["defaults"]


def test_state_starts_empty(client):
    st = client.get("/api/state").json()
    assert st["days"] == [] and st["job"]["status"] == "idle"


def test_add_paths_groups_days(client, logs):
    r = client.post("/api/paths", json={"paths": [str(logs)]}).json()
    assert r["added"] == 2
    days = client.get("/api/state").json()["days"]
    assert [d["date"] for d in days] == ["2026-10-01", "2026-10-02"]
    assert all(d["bytes"] > 0 for d in days)


def test_adding_the_same_file_twice_is_a_no_op(client, logs):
    client.post("/api/paths", json={"paths": [str(logs)]})
    again = client.post("/api/paths", json={"paths": [str(logs)]}).json()
    assert again["added"] == 0
    assert len(client.get("/api/state").json()["days"]) == 2


def test_non_log_files_are_rejected(client, tmp_path):
    bad = tmp_path / "export.xlsx"
    bad.write_bytes(b"x")
    r = client.post("/api/paths", json={"paths": [str(bad)]}).json()
    assert r["added"] == 0 and r["rejected"]


def test_missing_path_is_reported(client, tmp_path):
    r = client.post("/api/paths", json={"paths": [str(tmp_path / "nope.log")]}).json()
    assert r["added"] == 0 and "not found" in r["rejected"][0]


def test_upload_accepts_a_log(client, logs):
    path = logs / "play_extract_2026-10-01.log"
    with open(path, "rb") as fh:
        r = client.post("/api/upload", files={"files": (path.name, fh, "text/plain")}).json()
    assert r["added"] == 1


def test_upload_rejects_other_files(client):
    r = client.post("/api/upload", files={"files": ("data.xlsx", b"x", "application/octet-stream")}).json()
    assert r["added"] == 0 and r["rejected"]


def test_remove_and_clear(client, logs):
    client.post("/api/paths", json={"paths": [str(logs)]})
    day = client.get("/api/state").json()["days"][0]
    client.post("/api/days/remove", json={"files": day["files"]})
    assert len(client.get("/api/state").json()["days"]) == 1
    client.post("/api/days/clear", json={})
    assert client.get("/api/state").json()["days"] == []


def test_run_requires_files(client):
    assert client.post("/api/run", json={}).status_code == 400


def test_run_rejects_bad_settings(client, logs):
    client.post("/api/paths", json={"paths": [str(logs)]})
    assert client.post("/api/run", json={"door_s": -5}).status_code == 400
    assert client.post("/api/run", json={"door_s": "abc"}).status_code == 400


def _wait(client, timeout=180):
    for _ in range(int(timeout * 10)):
        st = client.get("/api/state").json()
        if st["job"]["status"] not in ("running",):
            return st
        time.sleep(0.1)
    raise AssertionError("the run did not finish")


def test_full_run_exposes_results(client, logs):
    client.post("/api/paths", json={"paths": [str(logs)]})
    assert client.post("/api/run", json={"door_s": 1.5}).status_code == 200
    st = _wait(client)
    assert st["job"]["status"] == "done", st["job"]["error"]

    r = client.get("/api/results").json()
    assert len(r["days"]) == 2 and all(d["ok"] for d in r["days"])
    for day in r["days"]:
        assert day["headline"]["visits"] > 0
    page = client.get(r["report_url"])
    assert page.status_code == 200 and "const REPORT=" in page.text


def test_targets_are_passed_to_the_report(client, logs):
    client.post("/api/paths", json={"paths": [str(logs)]})
    assert client.post("/api/run", json={"door_s": 1, "targets": {"LABOR-3": 60, "Zone A": ""}}).status_code == 200
    st = _wait(client)
    assert st["job"]["status"] == "done", st["job"]["error"]
    html = client.get(client.get("/api/results").json()["report_url"]).text
    assert '"targets":{"LABOR-3":60.0' in html


def test_bad_targets_are_rejected(client, logs):
    client.post("/api/paths", json={"paths": [str(logs)]})
    assert client.post("/api/run", json={"targets": {"LABOR-1": "fast"}}).status_code == 400
    assert client.post("/api/run", json={"targets": [270]}).status_code == 400


def test_stations_are_found_when_logs_are_added(client, logs):
    client.post("/api/paths", json={"paths": [str(logs)]})
    for _ in range(100):
        st = client.get("/api/state").json()["stations"]
        if not st["scanning"] and st["zones"]:
            break
        time.sleep(0.05)
    zones = {z["zone"]: z["stations"] for z in st["zones"]}
    assert zones == {"Zone A": ["LABOR-1", "LABOR-2"], "Zone B": ["LABOR-3"]}


def test_results_404_before_a_run(client):
    assert client.get("/api/results").status_code == 404


def test_history_lists_the_run(client, logs):
    client.post("/api/paths", json={"paths": [str(logs)]})
    client.post("/api/run", json={})
    _wait(client)
    runs = client.get("/api/runs").json()["runs"]
    assert len(runs) == 1 and runs[0]["url"]


def test_open_rejects_paths_outside_the_output_folder(client):
    r = client.post("/api/open", json={"target": "file", "url": "/runs/../../etc/passwd"})
    assert r.status_code in (400, 404)


def test_pick_and_switch_targets_reach_the_report(client, logs):
    client.post("/api/paths", json={"paths": [str(logs)]})
    r = client.post("/api/run", json={"pick_s": {"Zone A": 8.5}, "switch_s": {"LABOR-3": 5}})
    assert r.status_code == 200
    st = _wait(client)
    assert st["job"]["status"] == "done", st["job"]["error"]
    html = client.get(client.get("/api/results").json()["report_url"]).text
    assert '"pick_s":{"Zone A":8.5}' in html and '"switch_s":{"LABOR-3":5.0}' in html


def test_no_door_reaches_the_report(client, logs):
    client.post("/api/paths", json={"paths": [str(logs)]})
    assert client.post("/api/run", json={"no_door": {"LABOR-3": True}}).status_code == 200
    st = _wait(client)
    assert st["job"]["status"] == "done", st["job"]["error"]
    html = client.get(client.get("/api/results").json()["report_url"]).text
    assert '"no_door":{"LABOR-3":true}' in html
    assert client.post("/api/run", json={"no_door": "LABOR-3"}).status_code == 400


def test_no_door_days_reach_the_report(client, logs):
    client.post("/api/paths", json={"paths": [str(logs)]})
    assert client.post("/api/run", json={"no_door_days": ["2026-10-02"]}).status_code == 200
    st = _wait(client)
    assert st["job"]["status"] == "done", st["job"]["error"]
    html = client.get(client.get("/api/results").json()["report_url"]).text
    assert '"no_door_days":{"2026-10-02":true}' in html
    assert client.post("/api/run", json={"no_door_days": ["Oct 2"]}).status_code == 400


def test_starved_threshold_is_a_run_setting(client, logs):
    assert client.get("/api/meta").json()["defaults"]["starved_s"] == 1.0
    client.post("/api/paths", json={"paths": [str(logs)]})
    assert client.post("/api/run", json={"starved_s": -1}).status_code == 400
    assert client.post("/api/run", json={"starved_s": 2}).status_code == 200
    st = _wait(client)
    assert st["job"]["status"] == "done", st["job"]["error"]
    html = client.get(client.get("/api/results").json()["report_url"]).text
    assert '"starved_s":2.0' in html
