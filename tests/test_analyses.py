from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from config import ANALYSIS_MODULES, MAX_OPERATIONAL_SWITCH_S
from pipeline import resolve_module

REQUIRED = ("id", "title", "figure", "source", "method", "export_hint")
ENABLED = [k for k, _ in ANALYSIS_MODULES]


@pytest.fixture(scope="module")
def all_charts(day_data):
    data, cfg = day_data
    return {key: resolve_module(key).run(data, cfg) for key in ENABLED}


def test_every_module_produces_valid_charts(all_charts):
    ids = []
    for key, charts in all_charts.items():
        assert charts, f"{key} produced no charts on complete synthetic data"
        for c in charts:
            assert all(k in c for k in REQUIRED), (key, c.get("id"))
            c["figure"].to_json()
            ids.append(c["id"])
    assert len(ids) == len(set(ids)), "chart ids must be unique"


def test_data_dict_not_mutated(day_data):
    data, cfg = day_data
    before = {k: (None if v is None else (v.shape, list(v.columns))) for k, v in data.items()}
    for key in ENABLED:
        resolve_module(key).run(data, cfg)
    after = {k: (None if v is None else (v.shape, list(v.columns))) for k, v in data.items()}
    assert before == after


def _chart(all_charts, key, cid):
    return next(c for c in all_charts[key] if c["id"] == cid)


def test_dwell_heatmap_hover_shows_true_values(all_charts, day_data):
    # Regression: z was clipped to the colour range, so hover under-reported
    # the slowest station-hours.
    data, cfg = day_data
    from analyses.dwell_time import extract_picks
    picks = extract_picks(data["station"], cfg)
    true_max = picks.groupby(["station", "hour_dt"])["pick_s"].median().max()
    fig = _chart(all_charts, "dwell", "dwell_heatmap")["figure"]
    z = np.array(fig.data[0].z, dtype=float)
    assert np.nanmax(z) == pytest.approx(true_max)


def test_dwell_average_matches_exported_rows(all_charts):
    # The heatmap 'Average' trace and the raw rows use the same mean.
    ch = _chart(all_charts, "dwell", "dwell_heatmap")
    fig = ch["figure"]
    hours = list(fig.data[1].x)
    stations = list(fig.data[1].y)
    z = np.array(fig.data[1].z, dtype=float)
    for row in ch["raw_data"]["rows"]:
        i, j = stations.index(row["station"]), hours.index(row["hour"][:2] + ":00")
        assert z[i, j] == pytest.approx(row["mean_s"], abs=0.01)


def test_switch_heatmap_only_operational(all_charts):
    rows = _chart(all_charts, "switch", "switch_heatmap")["raw_data"]["rows"]
    assert max(r["median_s"] for r in rows) <= MAX_OPERATIONAL_SWITCH_S


def test_bay_heatmap_rows_align_with_grid():
    # Regression: raw rows assumed bays start at 1.
    from analyses.retrieval import _bay_heatmap
    src = pd.DataFrame({"Aisle": ["001", "001", "002"], "Bay": ["005", "006", "006"],
                        "Level": ["01"] * 3})
    rows = _bay_heatmap(src)["raw_data"]["rows"]
    assert sorted((r["aisle"], r["bay"], r["retrievals"]) for r in rows) == \
        [("1", 5, 1), ("1", 6, 1), ("2", 6, 1)]


def test_completion_definitions_agree(all_charts, day_data):
    data, cfg = day_data
    from analyses._common import triggergo_completions
    n = len(triggergo_completions(data["station"], cfg))
    total = sum(r["completions"] for r in _chart(all_charts, "throughput", "throughput_total")["raw_data"]["rows"])
    assert total == n
    hm = _chart(all_charts, "throughput", "throughput_heatmap")["raw_data"]
    assert sum(r["completions"] for r in hm["rows"]) == n
    assert 90 <= hm["effective_task_coverage_pct"] <= 100.01


def test_throughput_tolerates_bad_timestamps(day_data):
    data, cfg = day_data
    lsr = data["station"].copy()
    lsr.loc[lsr.index[:5], "时间戳"] = "not a time"
    charts = resolve_module("throughput").run({**data, "station": lsr}, cfg)
    assert charts


def test_modules_return_empty_without_inputs(day_data):
    _, cfg = day_data
    empty = {"callback": None, "station": None, "lifecycle": None, "efficiency": None}
    for key in ENABLED:
        if key == "quality":   # the data-quality panel reports absent sheets
            continue
        assert resolve_module(key).run(empty, cfg) == []
