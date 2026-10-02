from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from server import create_app


def _wait(client, pred, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = client.get("/api/state").json()
        if pred(st):
            return st
        time.sleep(0.2)
    raise AssertionError("timed out waiting for server state")


@pytest.fixture()
def client(tmp_path):
    return TestClient(create_app(str(tmp_path / "out")))


def test_static_and_meta(client):
    assert "ESS Analyzer" in client.get("/").text
    assert client.get("/vendor/plotly.min.js").status_code == 200
    meta = client.get("/api/meta").json()
    assert {a["key"] for a in meta["analyses"]} >= {"throughput", "dwell", "quality"}


def test_upload_run_and_results(client, workbook):
    with open(workbook, "rb") as fh:
        cfg = json.dumps({"design_rates": {"LABOR-1": 100}}).encode()
        r = client.post("/api/files", files=[
            ("files", ("day1.xlsx", fh.read(), "application/octet-stream")),
            ("files", ("asrs_config.json", cfg, "application/json")),
            ("files", ("notes.txt", b"x", "text/plain")),
        ])
    body = r.json()
    assert len(body["added"]) == 1
    assert any("notes.txt" in m for m in body["rejected"])
    st = _wait(client, lambda s: s["loading"] == 0)
    day = st["days"][0]
    assert day["status"] == "ready" and day["config_file"]
    assert any(s["station"] == "LABOR-1" and s["design_rate"] == 100 for s in st["stations"])

    # Validation errors are reported, not crashes.
    assert client.post("/api/run", json={"enabled": ["throughput"], "switch_s_fixed": -1}).status_code == 400
    assert client.post("/api/run", json={"enabled": []}).status_code == 400

    r = client.post("/api/run", json={"enabled": ["throughput", "dwell", "quality"],
                                      "station_types": {"LABOR-1": "Express"},
                                      "excel_exports": False})
    assert r.status_code == 200
    st = _wait(client, lambda s: s["job"]["status"] in ("done", "error"))
    assert st["job"]["status"] == "done", st["job"]
    assert st["job"]["logs"]

    res = client.get("/api/results").json()
    charts = res["days"][0]["charts"]
    assert any(c["id"] == "throughput_total" for c in charts)
    fig = client.get(f"/api/results/0/{charts[0]['idx']}/figure").json()
    assert "data" in fig and "layout" in fig
    rows = client.get(f"/api/results/0/{charts[0]['idx']}/rows").json()
    assert rows["rows"]
    assert client.get(res["report_url"]).status_code == 200
    assert client.get("/api/results/7/0/figure").status_code == 404
    assert client.get("/api/runs").json()["runs"]


def test_bad_file_reports_error(client, tmp_path):
    r = client.post("/api/files", files=[("files", ("broken.xlsx", b"not an excel file", "application/octet-stream"))])
    assert r.status_code == 200
    st = _wait(client, lambda s: s["loading"] == 0)
    assert st["days"][0]["status"] == "error"
    assert st["days"][0]["error"]


def test_open_rejects_paths_outside_output(client):
    r = client.post("/api/open", json={"target": "file", "url": "/runs/../../etc/passwd"})
    assert r.status_code == 400
