"""
pipeline.py — parse → measure → report, shared by the CLI and the web UI.

    find_logs(paths)              → [[one day's files], ...]
    run(groups, settings, ...)    → RunResult

One run writes a single self-contained report into a timestamped folder:

    asrs_analysis_output/2026-10-05_191500/station_robot_cycle_report.html

The report holds every day plus, from two days up, a cross-day summary; a
left-hand panel switches between them.

A day that fails is recorded and the run carries on, so one bad log never costs
you the rest of the batch.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from config import Settings
from log_parser import LogError, group_by_day, is_log_path, log_stem, parse_logs
from metrics import day_base
from report import REPORT_NAME, write_report

_log = logging.getLogger(__name__)

ProgressFn = Callable[[float, str], None]
LogFn = Callable[[str, str], None]          # (level, message)


class RunCancelled(Exception):
    """Raised inside run() when the cancel event is set."""


@dataclass
class DayResult:
    label: str
    paths: list[str]
    date: str | None = None
    metrics: dict | None = None
    error: str | None = None
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class RunResult:
    run_dir: str
    days: list[DayResult]
    report: str | None = None
    seconds: float = 0.0
    warnings: list[str] = field(default_factory=list)

    @property
    def completed(self) -> list[DayResult]:
        return [d for d in self.days if d.ok]


# ── Inputs ────────────────────────────────────────────────────────────────────

def find_logs(paths: list[str]) -> list[list[str]]:
    """Expand folders, keep log files only, and group them into days.

    Files sharing a date in their name belong to the same day (split logs).
    """
    found: list[str] = []
    for p in paths:
        if os.path.isdir(p):
            # Files only: an unpacked log often leaves a *folder* named
            # "play_extract_….log" sitting next to the archive it came from.
            found += [f for f in (os.path.join(p, n) for n in sorted(os.listdir(p)))
                      if is_log_path(f) and os.path.isfile(f)]
        elif is_log_path(p) and os.path.isfile(p):
            found.append(p)
    return group_by_day(sorted(set(found)))


def default_output_root() -> str:
    """Where runs are written.  ESS_OUTPUT_DIR wins; a frozen build writes next
    to the executable, never into the temporary unpack folder."""
    env = os.environ.get("ESS_OUTPUT_DIR")
    if env:
        return os.path.abspath(env)
    base = (os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, "frozen", False)
            else os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "asrs_analysis_output")


def _safe_name(text: str, fallback: str) -> str:
    out = "".join(c if c.isalnum() or c in " _-" else "_" for c in text).strip()
    return out or fallback


class _RunLogHandler(logging.Handler):
    """Forward log records emitted on the run's thread to the on_log callback."""

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


# ── Run ───────────────────────────────────────────────────────────────────────

def run(groups: list[list[str]],
        settings: Settings | None = None,
        on_progress: ProgressFn | None = None,
        on_log: LogFn | None = None,
        cancel: threading.Event | None = None) -> RunResult:
    """Analyse every day and write the reports."""
    s = settings or Settings()
    problems = s.validate()
    if problems:
        raise ValueError("; ".join(problems))
    if not groups:
        raise ValueError("No log files to analyse.")

    progress = on_progress or (lambda pct, msg: None)
    log = on_log or (lambda level, msg: None)
    warnings: list[str] = []
    started = time.time()

    def check_cancel() -> None:
        if cancel is not None and cancel.is_set():
            raise RunCancelled()

    def warn(msg: str) -> None:
        warnings.append(msg)
        log("warning", msg)

    root = s.output_root or default_output_root()
    run_dir = os.path.join(root, datetime.now().strftime("%Y-%m-%d_%H%M%S"))
    n = 2
    while os.path.exists(run_dir):
        run_dir = os.path.join(root, datetime.now().strftime("%Y-%m-%d_%H%M%S") + f"_{n}")
        n += 1
    os.makedirs(run_dir, exist_ok=True)

    handler = _RunLogHandler(log, threading.get_ident())
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    prev_level = root_logger.level
    if prev_level > logging.INFO or prev_level == logging.NOTSET:
        root_logger.setLevel(logging.INFO)

    try:
        results: list[DayResult] = []
        total = len(groups)

        for i, paths in enumerate(groups):
            check_cancel()
            label = log_stem(paths[0])
            t0 = time.time()
            log("head", f"{label} — {len(paths)} file{'s' if len(paths) != 1 else ''}")
            progress(90 * i / total, f"{label} · reading")

            day = DayResult(label=label, paths=list(paths))
            try:
                data = parse_logs(paths)
                check_cancel()
                progress(90 * (i + 0.5) / total, f"{label} · measuring")
                base = day_base(data, s.starved_s)
                day.date = base["date"]
                day.metrics = base
                day.seconds = time.time() - t0
                log("done", f"{label}: {base['overall']['visits']:,} visits, "
                            f"{len(base['stations'])} stations ({day.seconds:.1f}s)")
                if base["n_unpaired"]:
                    log("muted", f"{label}: {base['n_unpaired']:,} arrivals had no matching release")
            except RunCancelled:
                raise
            except (LogError, ValueError) as exc:
                day.error = str(exc)
                warn(f"{label} skipped — {exc}")
            except Exception as exc:
                day.error = f"{type(exc).__name__}: {exc}"
                warn(f"{label} failed — {day.error}")
                _log.debug("Traceback for %s:\n%s", label, traceback.format_exc())
            results.append(day)

        check_cancel()
        done = [d for d in results if d.ok]
        if not done:
            first = next((d.error for d in results if d.error), "")
            raise ValueError(f"No day could be analysed. {first}")

        # Two logs for the same calendar day would collide in the report.
        seen: dict[str, DayResult] = {}
        for d in done:
            if d.date in seen:
                warn(f"{d.label} covers {d.date}, already loaded from {seen[d.date].label}; "
                     "only the first is in the report.")
                d.error = f"duplicate of {seen[d.date].label}"
            else:
                seen[d.date] = d
        done = [d for d in results if d.ok]

        progress(95, "Writing the report")
        report = write_report([d.metrics for d in done], s, os.path.join(run_dir, REPORT_NAME))
        log("done", f"Report: {len(done)} day{'s' if len(done) != 1 else ''}"
                    + (" with a cross-day summary" if len(done) > 1 else ""))

        progress(100, f"Done — {len(done)} day{'s' if len(done) != 1 else ''}")
        return RunResult(run_dir=run_dir, days=results, report=report,
                         seconds=time.time() - started, warnings=warnings)
    finally:
        root_logger.removeHandler(handler)
        root_logger.setLevel(prev_level)
