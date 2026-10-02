"""
pipeline.py — GUI-agnostic analysis pipeline.

Both front ends (the web UI in server.py and the headless CLI in app.py) go
through this module, so a given input always produces the same report.

    load_day(paths)                        → LoadedDay   (validate + detect + config)
    run_pipeline(days, settings, ...)      → RunResult   (analyses, exports, report)

Analysis modules are resolved from config.ANALYSIS_IMPLEMENTATIONS with
importlib; nothing here (or in the front ends) imports them directly.
Progress and log lines are reported through callbacks; analyses log via the
standard `logging` module and those records are captured per run.
"""
from __future__ import annotations

import importlib
import logging
import os
import sys
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

import pandas as pd

from config import (
    ANALYSIS_IMPLEMENTATIONS,
    ANALYSIS_MODULES,
    CHART_DISPLAY_ORDER,
    LIFECYCLE_ONLY,
    PICK_START_EVENT_DEFAULT,
    PICK_START_EVENTS,
    SWITCH_S_FALLBACK,
)
from data_loader import (
    PREFLIGHT_OK,
    build_config,
    detect_data_date,
    filter_to_peak_day,
    load_data,
    load_log_day,
    load_user_config,
    pick_source_status,
    preflight_analyses,
    validate_data,
    validate_file_path,
)

_log = logging.getLogger(__name__)

STEP_LABEL: dict[str, str] = dict(ANALYSIS_MODULES)
SHEET_KEYS = ("callback", "station", "lifecycle", "efficiency")

ProgressFn = Callable[[float, str], None]
LogFn = Callable[[str, str], None]          # (level, message)


class LoadError(ValueError):
    """A day could not be loaded.  ``messages`` holds (level, text) details."""

    def __init__(self, message: str, messages: list[tuple[str, str]] | None = None):
        super().__init__(message)
        self.messages = messages or []


class RunCancelled(Exception):
    """Raised inside run_pipeline when the cancel event is set."""


# ══════════════════════════════════════════════════════════════════════════════
# Output location
# ══════════════════════════════════════════════════════════════════════════════

def default_output_root() -> str:
    """Where runs are written.

    ESS_OUTPUT_DIR wins when set.  A frozen (PyInstaller) build writes next to
    the executable — never inside the temporary unpack directory, which is
    deleted when the program exits.  From source, the project folder is used.
    """
    env = os.environ.get("ESS_OUTPUT_DIR")
    if env:
        return os.path.abspath(env)
    if getattr(sys, "frozen", False):
        base = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "asrs_analysis_output")


def _safe_name(text: str, fallback: str) -> str:
    out = "".join(c if c.isalnum() or c in " _-" else "_" for c in text).strip()
    return out or fallback


# ══════════════════════════════════════════════════════════════════════════════
# Loading
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class LoadedDay:
    """One operational day, loaded and validated, ready to analyse."""
    paths:       list[str]
    label:       str
    date:        str | None
    data:        dict[str, pd.DataFrame | None]
    cfg:         dict
    user_cfg:    dict
    sheet_rows:  dict[str, int | None]
    preflight:   list[dict]
    pick_source: dict
    messages:    list[tuple[str, str]] = field(default_factory=list)
    id:          str = field(default_factory=lambda: uuid.uuid4().hex[:10])

    @property
    def display_name(self) -> str:
        if len(self.paths) > 1:
            return f"{os.path.basename(self.paths[0])} (+{len(self.paths) - 1} files)"
        return os.path.basename(self.paths[0])


def group_input_paths(paths: list[str]) -> list[list[str]]:
    """Group input files into days: .log files sharing a date in their name
    form one day (split logs); every Excel file is its own day."""
    from log_converter import extract_log_date

    logs: dict[str, list[str]] = {}
    groups: list[list[str]] = []
    for p in paths:
        if os.path.splitext(p)[1].lower() == ".log":
            logs.setdefault(extract_log_date(p) or os.path.basename(p), []).append(p)
        else:
            groups.append([p])
    for group in logs.values():
        groups.append(sorted(group, key=os.path.basename))
    return groups


def load_day(paths: list[str], label: str | None = None,
             user_cfg_override: dict | None = None) -> LoadedDay:
    """Read, validate and auto-configure one day.  Raises LoadError."""
    msgs: list[tuple[str, str]] = []
    if not paths:
        raise LoadError("No files given.")

    for p in paths:
        fvr = validate_file_path(p)
        msgs += [("warning", w) for w in fvr.warnings]
        if not fvr.ok:
            raise LoadError("; ".join(fvr.errors), msgs + [("error", e) for e in fvr.errors])

    try:
        if os.path.splitext(paths[0])[1].lower() == ".log":
            data = load_log_day(paths)
        else:
            data = load_data(paths[0])
    except LoadError:
        raise
    except Exception as exc:
        raise LoadError(str(exc), msgs + [("error", str(exc))]) from exc

    dvr = validate_data(data)
    msgs += [("warning", w) for w in dvr.warnings]
    if not dvr.ok:
        raise LoadError("Data validation failed: " + "; ".join(dvr.errors),
                        msgs + [("error", e) for e in dvr.errors])

    if user_cfg_override is not None:
        user_cfg = dict(user_cfg_override)
    else:
        user_cfg, cvr = load_user_config(paths[0])
        msgs += [("warning", w) for w in cvr.warnings]
        if not cvr.ok:
            raise LoadError("asrs_config.json is invalid: " + "; ".join(cvr.errors),
                            msgs + [("error", e) for e in cvr.errors])
        if user_cfg:
            msgs.append(("info", "Applied asrs_config.json from the data folder."))

    peak = filter_to_peak_day(data)
    cfg = build_config(peak, user_cfg)
    date = detect_data_date(peak)
    preflight = preflight_analyses(data)
    for f in preflight:
        if f["status"] != PREFLIGHT_OK:
            msgs.append(("warning", f"{f['label']}: {f['status']} — {f['reason']}"))
    if not cfg["ws_order"]:
        msgs.append(("warning", "No LABOR station points were found in the station sheet; "
                                "station-level charts will be empty."))

    return LoadedDay(
        paths=list(paths),
        label=label or date or os.path.splitext(os.path.basename(paths[0]))[0],
        date=date,
        data=data,
        cfg=cfg,
        user_cfg=user_cfg,
        sheet_rows={k: (None if data.get(k) is None else int(len(data[k]))) for k in SHEET_KEYS},
        preflight=preflight,
        pick_source=pick_source_status(data),
        messages=msgs,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Running
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class RunSettings:
    """User choices that apply to every day in a run."""
    enabled:          set[str] = field(default_factory=lambda: {k for k, _ in ANALYSIS_MODULES})
    switch_s_fixed:   float = SWITCH_S_FALLBACK
    switch_mode:      str = "fixed"            # "fixed" | "measured"
    # None = automatic: 'arrived', or 'ppReady' for days without 'arrived' events.
    pick_start_event: str | None = None
    station_types:    dict[str, str] = field(default_factory=dict)
    design_rates:     dict[str, float] = field(default_factory=dict)
    type_colors:      dict[str, str] = field(default_factory=dict)
    output_root:      str | None = None
    excel_exports:    bool = True


@dataclass
class DayResult:
    label:    str
    date:     str | None
    outdir:   str
    registry: list[dict]
    data:     dict
    cfg:      dict
    failures: list[dict] = field(default_factory=list)
    kpis:     list[dict] = field(default_factory=list)
    exports:  list[str] = field(default_factory=list)


@dataclass
class RunResult:
    run_dir:          str
    html_path:        str
    combined_path:    str | None
    days:             list[DayResult]
    summary_registry: list[dict]
    seconds:          float
    warnings:         list[str]


def resolve_module(key: str):
    """Import the analysis module registered for `key`."""
    return importlib.import_module(ANALYSIS_IMPLEMENTATIONS[key])


def sort_registry(registry: list[dict]) -> list[dict]:
    order = {cid: i for i, cid in enumerate(CHART_DISPLAY_ORDER)}
    n = len(order)
    return [e for _, e in sorted(
        enumerate(registry), key=lambda ie: (order.get(ie[1]["id"], n), ie[0]))]


def _merged_user_cfg(day: LoadedDay, s: RunSettings) -> dict:
    """asrs_config.json values, overridden by the choices made for this run."""
    u = dict(day.user_cfg)
    u["station_types"] = {**u.get("station_types", {}), **s.station_types}
    u["design_rates"] = {**u.get("design_rates", {}),
                         **{k: v for k, v in s.design_rates.items() if v}}
    u["type_colors"] = {**u.get("type_colors", {}), **s.type_colors}
    u["switch_s_fixed"] = s.switch_s_fixed
    u["switch_mode"] = s.switch_mode
    pse = s.pick_start_event or u.get("pick_start_event")
    if pse not in PICK_START_EVENTS:
        pse = None
    if pse is None:
        ps = day.pick_source
        pse = "ppReady" if (not ps.get("arrived") and ps.get("ppready")) else PICK_START_EVENT_DEFAULT
    u["pick_start_event"] = pse
    return u


def day_kpis(data: dict, cfg: dict) -> list[dict]:
    """Headline numbers for a day (shown in the UI and the report)."""
    from analyses._common import triggergo_completions
    from analyses.dwell_time import extract_picks
    from analyses.switch_time import _release_arrived_gaps
    from config import MAX_OPERATIONAL_SWITCH_S

    k: list[dict] = []
    lsr = data.get("station")
    if lsr is not None and cfg.get("point2ws"):
        tgo = triggergo_completions(lsr, cfg)
        if len(tgo):
            hourly = tgo.groupby(tgo["ts"].dt.floor("h")).size()
            k.append({"label": "Completions", "value": f"{len(tgo):,}",
                      "hint": "Delivery-AMR triggerGo events at LABOR stations"})
            k.append({"label": "Peak hour", "value": f"{int(hourly.max()):,}/h",
                      "hint": f"at {hourly.idxmax():%H:00}"})
            k.append({"label": "Avg / active hour", "value": f"{hourly.mean():,.0f}/h",
                      "hint": f"over {len(hourly)} active hours"})
        picks = extract_picks(lsr, cfg)
        if len(picks):
            k.append({"label": "Median pick", "value": f"{picks['pick_s'].median():.0f} s",
                      "hint": f"mean {picks['pick_s'].mean():.1f} s · {len(picks):,} picks"})
        gaps = _release_arrived_gaps(lsr, cfg["point2ws"])
        if len(gaps):
            op = gaps[gaps["gap_s"] <= MAX_OPERATIONAL_SWITCH_S]
            starved = gaps[(gaps["gap_s"] > MAX_OPERATIONAL_SWITCH_S) & (gaps["gap_s"] < 7200)]
            if len(op):
                k.append({"label": "Median switch", "value": f"{op['gap_s'].median():.0f} s",
                          "hint": "release → next arrival, operational swaps"})
            k.append({"label": "Starved time", "value": f"{starved['gap_s'].sum() / 3600:.1f} h",
                      "hint": f"{len(starved)} episodes > {MAX_OPERATIONAL_SWITCH_S / 60:.0f} min"})
    k.append({"label": "Stations", "value": str(len(cfg.get("ws_order", []))),
              "hint": f"{len(set(cfg.get('type_map', {}).values()))} zone(s)"})
    return k


class _RunLogHandler(logging.Handler):
    """Forwards log records emitted on the run's thread to the on_log callback."""

    def __init__(self, on_log: LogFn, thread_id: int):
        super().__init__(level=logging.INFO)
        self._on_log = on_log
        self._tid = thread_id

    def emit(self, record: logging.LogRecord) -> None:
        if record.thread != self._tid:
            return
        try:
            self._on_log(record.levelname.lower(), record.getMessage())
        except Exception:
            pass


def run_pipeline(
    days: list[LoadedDay],
    settings: RunSettings | None = None,
    on_progress: ProgressFn | None = None,
    on_log: LogFn | None = None,
    cancel: threading.Event | None = None,
) -> RunResult:
    """Analyse every day, write Excel exports and the HTML report."""
    from analyses import summary as summary_mod
    from exports import save_chart_data, save_combined_workbook, save_day_exports
    from report_builder import generate_html_report

    s = settings or RunSettings()
    progress = on_progress or (lambda pct, msg: None)
    log = on_log or (lambda lvl, msg: None)
    t_start = time.time()
    warnings: list[str] = []

    if not days:
        raise ValueError("No days to analyse.")

    def check_cancel() -> None:
        if cancel is not None and cancel.is_set():
            raise RunCancelled()

    def warn(msg: str) -> None:
        warnings.append(msg)
        log("warning", msg)

    root = s.output_root or default_output_root()
    run_dir = os.path.join(root, datetime.now().strftime("%Y-%m-%d_%H%M%S"))
    suffix = 1
    while os.path.exists(run_dir):
        suffix += 1
        run_dir = os.path.join(root, datetime.now().strftime("%Y-%m-%d_%H%M%S") + f"_{suffix}")
    os.makedirs(run_dir, exist_ok=True)

    handler = _RunLogHandler(log, threading.get_ident())
    logging.getLogger().addHandler(handler)
    prev_level = logging.getLogger().level
    if prev_level > logging.INFO or prev_level == logging.NOTSET:
        logging.getLogger().setLevel(logging.INFO)
    try:
        results: list[DayResult] = []
        n_days = len(days)
        used_dirs: set[str] = set()
        for di, day in enumerate(days):
            check_cancel()
            data = filter_to_peak_day(day.data)
            cfg = build_config(data, _merged_user_cfg(day, s))
            if cfg["pick_start_event"] != PICK_START_EVENT_DEFAULT:
                warn(f"{day.label}: pick time uses the ppReady → triggerGo fallback "
                     "('arrived' events are missing).")

            safe = _safe_name(day.label, f"day_{di + 1}")
            while safe in used_dirs:
                safe += "_"
            used_dirs.add(safe)
            day_dir = os.path.join(run_dir, safe)
            os.makedirs(day_dir, exist_ok=True)

            steps = [k for k, _ in ANALYSIS_MODULES if k in s.enabled]
            skipped = [k for k in steps if k in LIFECYCLE_ONLY and data.get("lifecycle") is None]
            steps = [k for k in steps if k not in skipped]
            log("head", f"{day.label} — {len(steps)} analyses")
            if skipped:
                warn(f"{day.label}: skipped (no lifecycle sheet): "
                     + ", ".join(STEP_LABEL.get(k, k) for k in skipped))

            registry: list[dict] = []
            failures: list[dict] = []
            for si, key in enumerate(steps):
                check_cancel()
                label = STEP_LABEL.get(key, key)
                progress(90 * (di + si / max(len(steps), 1)) / n_days, f"{day.label} · {label}")
                t0 = time.time()
                try:
                    charts = resolve_module(key).run(data, cfg) or []
                    _validate_charts(key, charts)
                    registry.extend(charts)
                    log("done", f"{label}: {len(charts)} chart{'s' if len(charts) != 1 else ''}"
                                f" ({time.time() - t0:.1f}s)")
                    if not charts:
                        log("muted", f"{label}: no charts — required inputs not present")
                except RunCancelled:
                    raise
                except Exception as exc:
                    failures.append({"key": key, "label": label,
                                     "error": f"{type(exc).__name__}: {exc}"})
                    log("error", f"{label} failed — {type(exc).__name__}: {exc}")
                    _log.debug("Traceback for %s:\n%s", key, traceback.format_exc())

            _dedupe_ids(registry, warn)
            check_cancel()
            exports: list[str] = []
            if s.excel_exports:
                progress(90 * (di + 1) / n_days, f"{day.label} · Excel exports")
                exports = save_day_exports(data, cfg, day_dir)
                cd = save_chart_data(registry, day_dir)
                if cd:
                    exports.append(cd)
            try:
                kpis = day_kpis(data, cfg)
            except Exception as exc:
                kpis = []
                warn(f"{day.label}: headline KPIs unavailable ({exc})")
            results.append(DayResult(
                label=day.label, date=day.date, outdir=day_dir,
                registry=sort_registry(registry), data=data, cfg=cfg,
                failures=failures, kpis=kpis, exports=exports,
            ))

        check_cancel()
        summary_registry: list[dict] = []
        if len(results) > 1:
            progress(92, "Cross-day summary")
            all_days = [{"label": r.label, "data": r.data, "cfg": r.cfg} for r in results]
            try:
                summary_registry = summary_mod.run(all_days)
                log("done", f"Summary: {len(summary_registry)} charts")
                if s.excel_exports:
                    summary_mod.export_xlsx(all_days, run_dir)
            except Exception as exc:
                warn(f"Summary charts skipped: {type(exc).__name__}: {exc}")

        combined = None
        if s.excel_exports:
            check_cancel()
            progress(94, "Combined workbook")
            combined = save_combined_workbook(
                [{"label": r.label, "data": r.data, "cfg": r.cfg} for r in results], run_dir)

        check_cancel()
        progress(97, "Building HTML report")
        html_path = generate_html_report(
            run_dir,
            [{"label": r.label, "registry": r.registry, "outdir": r.outdir,
              "kpis": r.kpis, "failures": r.failures} for r in results],
            summary_registry=summary_registry,
        )
        n_charts = sum(len(r.registry) for r in results) + len(summary_registry)
        progress(100, f"Done — {n_charts} charts")
        log("done", f"Report written: {html_path}")
        return RunResult(run_dir=run_dir, html_path=html_path, combined_path=combined,
                         days=results, summary_registry=summary_registry,
                         seconds=time.time() - t_start, warnings=warnings)
    finally:
        logging.getLogger().removeHandler(handler)
        logging.getLogger().setLevel(prev_level)


_REQUIRED_KEYS = ("id", "title", "figure", "source", "method", "export_hint")


def _validate_charts(key: str, charts: list[dict]) -> None:
    for c in charts:
        missing = [k for k in _REQUIRED_KEYS if k not in c]
        if missing:
            raise TypeError(f"{key}: chart {c.get('id', '?')} is missing {missing}")
        if not hasattr(c["figure"], "to_json"):
            raise TypeError(f"{key}: chart {c['id']} figure is not a Plotly figure")


def _dedupe_ids(registry: list[dict], warn: Callable[[str], None]) -> None:
    seen: dict[str, int] = {}
    for c in registry:
        cid = c["id"]
        if cid in seen:
            seen[cid] += 1
            c["id"] = f"{cid}_{seen[cid]}"
            warn(f"Duplicate chart id '{cid}' renamed to '{c['id']}'.")
        else:
            seen[cid] = 1
