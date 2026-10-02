from __future__ import annotations

import pandas as pd

from data_loader import (
    build_config,
    detect_data_date,
    filter_to_peak_day,
    load_data,
    row_timestamps,
    validate_data,
)


def test_all_sheets_detected(workbook):
    data = load_data(workbook)
    assert all(data[k] is not None for k in ("callback", "station", "lifecycle", "efficiency"))
    assert validate_data(data).ok


def test_blank_create_time_column_does_not_fail_validation(workbook_blank_create):
    # Regression: the first '…时间' column (创建时间) was used as the lifecycle
    # timestamp; when blank, the whole file was rejected.
    data = load_data(workbook_blank_create)
    assert data["lifecycle"]["创建时间"].isna().all()
    vr = validate_data(data)
    assert vr.ok, vr.errors


def test_peak_day_filter_keeps_lifecycle_rows(workbook_blank_create):
    # Regression: lifecycle rows were dated by 创建时间, so a blank column
    # wiped the whole sheet whenever the export spanned midnight.
    data = load_data(workbook_blank_create)
    cb_dates = pd.to_datetime(data["callback"]["时间戳"]).dt.date
    assert cb_dates.nunique() == 2, "fixture should span midnight"
    peak = filter_to_peak_day(data)
    tlc = peak["lifecycle"]
    assert len(tlc) > 0.9 * len(data["lifecycle"])
    done = pd.to_datetime(tlc["complete(任务完成时间)"]).dt.date
    assert (done == pd.Timestamp("2026-06-12").date()).all()
    assert detect_data_date(peak) == "2026-06-12"


def test_row_timestamps_coalesce_columns():
    df = pd.DataFrame({"创建时间": [None, "2026-01-01 10:00:00"],
                       "complete(任务完成时间)": ["2026-01-02 11:00:00", None]})
    ts = row_timestamps(df, "lifecycle")
    assert ts.iloc[0] == pd.Timestamp("2026-01-02 11:00:00")
    assert ts.iloc[1] == pd.Timestamp("2026-01-01 10:00:00")


def test_station_names_come_from_callback(workbook):
    data = load_data(workbook)
    cfg = build_config(data, {})
    cb = data["callback"]
    truth = (cb[cb["位置类型"].str.startswith("LABOR")]
             .drop_duplicates("位置编号").set_index("位置编号")["位置类型"].to_dict())
    for point, name in cfg["point2ws"].items():
        assert truth[point] == name
    assert cfg["ws_order"] == sorted(cfg["ws_order"], key=lambda s: int(s.split("-")[1]))


def test_station_names_fall_back_to_coordinates_without_callback(workbook):
    data = load_data(workbook)
    data = {**data, "callback": None}
    cfg = build_config(data, {})
    assert cfg["ws_order"] == [f"LABOR-{i}" for i in range(1, len(cfg["ws_order"]) + 1)]


def test_user_overrides_apply(workbook):
    data = load_data(workbook)
    cfg = build_config(data, {"station_types": {"LABOR-1": "Fast"},
                              "design_rates": {"LABOR-1": 120},
                              "switch_mode": "measured"})
    assert cfg["type_map"]["LABOR-1"] == "Fast"
    assert cfg["design_rate"] == {"LABOR-1": 120}
    assert cfg["switch_mode"] == "measured" and cfg["switch_measured"]
