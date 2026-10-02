"""
app.py — ESS / ASRS Log Analyzer entry point.

    python app.py                         open the desktop UI
    python app.py day1.xlsx day2.xlsx     analyse files headless (CLI)
    python app.py --help                  all options

The UI is a local web app (server.py + webui/) shown in a native window when
pywebview is installed, otherwise in the default browser.  The CLI and the UI
share one pipeline (pipeline.py), so they produce identical reports.

Architecture
------------
  config.py          — constants, colour palettes, analysis registry
  data_loader.py     — Excel / log ingestion, sheet detection, build_config()
  analyses/          — one module per domain; each exposes run(data, cfg)
  pipeline.py        — load → analyse → export → report orchestration
  exports.py         — Excel workbooks written alongside the report
  report_builder.py  — self-contained HTML report
  server.py, webui/  — desktop UI
  app.py             — command-line entry point (this file)
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

from config import ANALYSIS_MODULES, PICK_START_EVENTS, SWITCH_S_FALLBACK


def _parse_args(argv: list[str]) -> argparse.Namespace:
    keys = [k for k, _ in ANALYSIS_MODULES]
    ap = argparse.ArgumentParser(
        prog="ESS_Analyzer",
        description="Analyse Hairobotics ESS/ASRS exports. With no files, opens the UI.",
    )
    ap.add_argument("files", nargs="*", help=".xlsx/.xlsm/.log files (one Excel file per day; "
                                             "split .log files of one day are merged)")
    ap.add_argument("--out", help="output folder (default: asrs_analysis_output next to the app)")
    ap.add_argument("--analyses", help=f"comma-separated subset of: {', '.join(keys)}")
    ap.add_argument("--switch-mode", choices=["fixed", "measured"], default="fixed",
                    help="robot switch-time model for implied throughput (default: fixed)")
    ap.add_argument("--switch-s", type=float, default=SWITCH_S_FALLBACK,
                    help=f"fixed switch time in seconds (default {SWITCH_S_FALLBACK:g})")
    ap.add_argument("--pick-start", choices=list(PICK_START_EVENTS),
                    help="pick-time start event (default: automatic)")
    ap.add_argument("--no-excel", action="store_true", help="skip the Excel exports")
    ui = ap.add_argument_group("UI")
    ui.add_argument("--port", type=int, default=0, help="UI port (default: any free port)")
    ui.add_argument("--browser", action="store_true",
                    help="open the UI in the web browser instead of a native window")
    ui.add_argument("--no-open", action="store_true", help="start the UI server only")
    ap.add_argument("-v", "--verbose", action="store_true")
    return ap.parse_args(argv)


def run_headless(args: argparse.Namespace) -> int:
    from pipeline import LoadError, RunSettings, group_input_paths, load_day, run_pipeline

    style = {"warning": "[warn] ", "error": "[ERROR] ", "done": "[done] ", "head": "\n== "}

    def log(level: str, msg: str) -> None:
        print(f"  {style.get(level, '')}{msg}", flush=True)

    days = []
    for group in group_input_paths(args.files):
        print(f"Reading {', '.join(os.path.basename(p) for p in group)}")
        try:
            day = load_day(group)
        except LoadError as exc:
            for lvl, m in exc.messages:
                log(lvl, m)
            print(f"  [ERROR] {exc}")
            return 1
        for lvl, m in day.messages:
            log(lvl, m)
        print(f"  {day.label}: {len(day.cfg['ws_order'])} stations, "
              + ", ".join(f"{k} {v:,}" for k, v in day.sheet_rows.items() if v is not None))
        days.append(day)

    enabled = {k for k, _ in ANALYSIS_MODULES}
    if args.analyses:
        enabled = {a.strip() for a in args.analyses.split(",") if a.strip()}
        unknown = enabled - {k for k, _ in ANALYSIS_MODULES}
        if unknown:
            print(f"Unknown analyses: {', '.join(sorted(unknown))}")
            return 2

    settings = RunSettings(
        enabled=enabled,
        switch_s_fixed=args.switch_s,
        switch_mode=args.switch_mode,
        pick_start_event=args.pick_start,
        output_root=args.out,
        excel_exports=not args.no_excel,
    )
    result = run_pipeline(days, settings, on_log=log)
    n = sum(len(d.registry) for d in result.days) + len(result.summary_registry)
    failed = sum(len(d.failures) for d in result.days)
    print(f"\nComplete in {result.seconds:.1f}s — {n} charts"
          + (f", {failed} analysis failure(s)" if failed else ""))
    print(f"  Report: {result.html_path}")
    print(f"  Folder: {result.run_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    if args.files:
        missing = [f for f in args.files if not os.path.isfile(f)]
        if missing:
            print(f"File not found: {', '.join(missing)}")
            return 2
        return run_headless(args)

    from server import launch
    launch(port=args.port, output_root=args.out,
           mode="none" if args.no_open else ("browser" if args.browser else "auto"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
