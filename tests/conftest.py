from __future__ import annotations

import os
import sys

import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tests.synthetic import SynthOptions, write_workbook  # noqa: E402

SMALL = dict(n_stations=4, n_robots=24, start_hour=7, end_hour=10)


@pytest.fixture(scope="session")
def workbook(tmp_path_factory) -> str:
    path = tmp_path_factory.mktemp("data") / "day1.xlsx"
    return write_workbook(str(path), SynthOptions(**SMALL))


@pytest.fixture(scope="session")
def workbook_day2(tmp_path_factory) -> str:
    path = tmp_path_factory.mktemp("data2") / "day2.xlsx"
    return write_workbook(str(path), SynthOptions(seed=21, day=pd.Timestamp("2026-06-13"), **SMALL))


@pytest.fixture(scope="session")
def workbook_blank_create(tmp_path_factory) -> str:
    path = tmp_path_factory.mktemp("data3") / "blank_create.xlsx"
    return write_workbook(str(path), SynthOptions(seed=5, create_time_fill=0.0, **SMALL))


@pytest.fixture(scope="session")
def loaded(workbook):
    from pipeline import load_day
    return load_day([workbook])


@pytest.fixture(scope="session")
def day_data(loaded):
    from data_loader import filter_to_peak_day
    return filter_to_peak_day(loaded.data), loaded.cfg
