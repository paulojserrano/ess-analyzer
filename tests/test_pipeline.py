from __future__ import annotations

import os
import re
import threading

import pandas as pd
import pytest

from config import MAX_OPERATIONAL_SWITCH_S
from pipeline import RunCancelled, RunSettings, load_day, run_pipeline


def test_end_to_end_two_days(workbook, workbook_day2, tmp_path):
    days = [load_day([workbook]), load_day([workbook_day2])]
    progress, logs = [], []
    res = run_pipeline(days, RunSettings(output_root=str(tmp_path)),
                       on_progress=lambda p, m: progress.append(p),
                       on_log=lambda lvl, m: logs.append((lvl, m)))
    assert os.path.isfile(res.html_path)
    assert res.combined_path and os.path.isfile(res.combined_path)
    assert [d.label for d in res.days] == ["2026-06-12", "2026-06-13"]
    assert all(not d.failures for d in res.days)
    assert res.summary_registry
    assert progress[-1] == 100
    assert progress == sorted(progress)
    assert any(k["label"] == "Completions" for k in res.days[0].kpis)

    html = open(res.html_path, encoding="utf-8").read()
    # Every export link in the report must point at a file that exists.
    for href in re.findall(r'<a href="([^"]+)" class="export-link"', html):
        assert os.path.isfile(os.path.join(res.run_dir, href.replace("%20", " "))), href


def test_excel_exports_use_shared_definitions(loaded, tmp_path):
    res = run_pipeline([loaded], RunSettings(output_root=str(tmp_path),
                                             enabled={"throughput", "dwell", "switch"}))
    day_dir = res.days[0].outdir
    xl = os.path.join(day_dir, "dwell_capacity_utilisation.xlsx")
    sw = pd.read_excel(xl, sheet_name="raw_switch_events")
    assert sw["Switch Time (s)"].max() <= MAX_OPERATIONAL_SWITCH_S
    picks = pd.read_excel(xl, sheet_name="raw_pick_events")
    from analyses.dwell_time import extract_picks
    from data_loader import filter_to_peak_day
    data = filter_to_peak_day(loaded.data)
    assert len(picks) == len(extract_picks(data["station"], res.days[0].cfg))
    tp = pd.read_excel(os.path.join(day_dir, "throughput_by_workstation_hour.xlsx"),
                       sheet_name="throughput_by_hour", index_col=0)
    chart_total = sum(r["completions"] for r in next(
        c for c in res.days[0].registry if c["id"] == "throughput_total")["raw_data"]["rows"])
    assert tp.loc["TOTAL", "TOTAL"] == chart_total
    assert os.path.isfile(os.path.join(day_dir, "chart_data.xlsx"))


def test_failing_analysis_is_isolated(loaded, tmp_path, monkeypatch):
    import analyses.switch_time as sw

    def boom(data, cfg):
        raise RuntimeError("synthetic failure")
    monkeypatch.setattr(sw, "run", boom)
    res = run_pipeline([loaded], RunSettings(output_root=str(tmp_path), excel_exports=False,
                                             enabled={"throughput", "switch"}))
    day = res.days[0]
    assert [f["key"] for f in day.failures] == ["switch"]
    assert any(c["id"] == "throughput_total" for c in day.registry)
    assert "synthetic failure" in open(res.html_path, encoding="utf-8").read()


def test_cancel(loaded, tmp_path):
    ev = threading.Event()
    ev.set()
    with pytest.raises(RunCancelled):
        run_pipeline([loaded], RunSettings(output_root=str(tmp_path)), cancel=ev)


def test_ppready_fallback_is_automatic_when_arrived_missing(loaded, tmp_path):
    lsr = loaded.data["station"]
    data = {**loaded.data, "station": lsr[lsr["事件类型"] != "arrived"].reset_index(drop=True)}
    from data_loader import pick_source_status
    loaded2 = type(loaded)(**{**loaded.__dict__, "data": data,
                              "pick_source": pick_source_status(data)})
    logs = []
    res = run_pipeline([loaded2], RunSettings(output_root=str(tmp_path), excel_exports=False,
                                              enabled={"dwell"}),
                       on_log=lambda lvl, m: logs.append(m))
    assert res.days[0].cfg["pick_start_event"] == "ppReady"
    assert any("ppReady" in m for m in logs)
    assert res.days[0].registry


def test_cli(workbook, tmp_path):
    from app import main
    rc = main([workbook, "--out", str(tmp_path), "--analyses", "throughput,quality", "--no-excel"])
    assert rc == 0
    runs = os.listdir(tmp_path)
    assert len(runs) == 1
    assert os.path.isfile(os.path.join(tmp_path, runs[0], "asrs_analysis_report.html"))
