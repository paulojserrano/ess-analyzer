"""Shared fixtures — synthetic log days, parsed and measured."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from log_parser import parse_logs      # noqa: E402
from metrics import day_base, zones_for  # noqa: E402
from tests.synthetic import write      # noqa: E402


@pytest.fixture(scope="session")
def log_file(tmp_path_factory):
    return write(str(tmp_path_factory.mktemp("logs") / "play_extract_2026-10-01.log"))


@pytest.fixture(scope="session")
def log_gz(tmp_path_factory):
    return write(str(tmp_path_factory.mktemp("logs_gz") / "play_extract_2026-10-01.log.gz"))


@pytest.fixture(scope="session")
def data(log_file):
    return parse_logs(log_file)


@pytest.fixture(scope="session")
def base(data):
    return day_base(data)


@pytest.fixture(scope="session")
def bases(tmp_path_factory):
    """Three days with slightly different shifts, so trends have something to show."""
    folder = tmp_path_factory.mktemp("days")
    out = []
    for i, date in enumerate(("2026-10-01", "2026-10-02", "2026-10-03")):
        p = folder / f"play_extract_{date}.log"
        write(str(p), date=date, hours=(6, 9 + i))
        out.append(day_base(parse_logs(str(p))))
    return out


@pytest.fixture(scope="session")
def zones(bases):
    return zones_for(bases)
