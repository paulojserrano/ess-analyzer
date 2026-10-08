"""The single-file HTML report."""
from __future__ import annotations

import json
import re

from config import Settings
from report import build_payload, default_targets, write_report


def _payload(html: str) -> dict:
    return json.loads(re.search(r"const REPORT=(\{.*?\});\n</script>", html, re.S).group(1))


def test_one_file_holds_every_day(tmp_path, bases):
    path = write_report(bases, Settings(), str(tmp_path / "r.html"))
    html = open(path, encoding="utf-8").read()
    data = _payload(html)
    assert [d["date"] for d in data["days"]] == ["2026-10-01", "2026-10-02", "2026-10-03"]
    assert "const ENGINE" in html or "ENGINE = " in html
    assert "plotly" not in html.lower()


def test_every_slot_is_filled(tmp_path, bases):
    html = open(write_report(bases, Settings(), str(tmp_path / "r.html")), encoding="utf-8").read()
    outside_data = re.sub(r"const REPORT=\{.*?\};\n</script>", "", html, flags=re.S)
    assert not re.search(r"__(TITLE|STYLE|ENGINE|APP|DATA)__", outside_data)


def test_script_tag_cannot_be_closed_early(tmp_path, bases):
    """A stray </script> inside the data would truncate the page."""
    b = dict(bases[0])
    b["source"] = "</script><h1>oops</h1>.log"
    html = open(write_report([b], Settings(), str(tmp_path / "x.html")), encoding="utf-8").read()
    assert "<h1>oops</h1>" not in html


def test_title_names_the_date_range(tmp_path, bases):
    html = open(write_report(bases, Settings(), str(tmp_path / "r.html")), encoding="utf-8").read()
    assert "<title>Station &amp; robot cycle analysis, 1 Oct – 3 Oct 2026</title>" in html


def test_payload_shape(bases):
    p = build_payload(bases, Settings(door_s=1.5))
    assert p["defaults"]["door_s"] == 1.5
    assert p["stations"] == ["LABOR-1", "LABOR-2", "LABOR-3"]
    assert {z["zone"] for z in p["zone_list"]} == {"Zone A", "Zone B"}
    assert p["constants"]["switch_hist_bins"] > 0


def test_report_id_changes_with_the_defaults(bases):
    """Edits saved in the browser are keyed by id; new defaults must not inherit them."""
    a = build_payload(bases, Settings())["id"]
    b = build_payload(bases, Settings(door_s=2.0))["id"]
    assert a != b
    assert a == build_payload(bases, Settings())["id"]


def test_explicit_targets_win(bases, zones):
    t = default_targets(bases, zones, Settings(targets={"Zone B": 70, "LABOR-2": 250}))
    assert t["Zone B"] == 70 and t["LABOR-2"] == 250
    assert t["Zone A"] == 270          # still auto, as a high-rate zone


def test_slow_zones_get_no_automatic_target(bases, zones):
    t = default_targets(bases, zones, Settings())
    assert "Zone B" not in t
