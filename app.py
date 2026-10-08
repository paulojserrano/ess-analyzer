"""
app.py — ESS station & robot cycle analyser.

    python app.py                       open the UI
    python app.py LOG [LOG ...]         analyse those logs and write the reports
    python app.py all_logs/             analyse every log in a folder

Inputs are Hairobotics "play_extract" application logs, plain (.log) or gzipped
(.log.gz).  Files sharing a date in their name are treated as one day.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

from config import DOOR_S_DEFAULT, STARVED_S_DEFAULT, TARGET_RATE_DEFAULT, Settings, load_settings
from pipeline import find_logs, run


def _parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="ess-analyzer",
        description="Station and robot cycle analysis from Hairobotics logs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="With no files, the desktop UI opens instead.",
    )
    ap.add_argument("logs", nargs="*", metavar="LOG",
                    help=".log or .log.gz files, or folders containing them")
    ap.add_argument("--door", type=float, default=None, metavar="S",
                    help=f"seconds of shutter-door travel added to every switch time "
                         f"(default {DOOR_S_DEFAULT:g}); the same seconds come off the front "
                         f"of each pick, so the hour budget still adds up")
    ap.add_argument("--no-door", default=None, metavar="LIST",
                    help='comma-separated stations or zones without a shutter door, e.g. '
                         '"LABOR-8,Zone B" (every station has one by default)')
    ap.add_argument("--no-door-days", default=None, metavar="DATES",
                    help='comma-separated dates when the doors were not in use, e.g. '
                         '"2026-09-25,2026-09-26" (no door seconds on those days)')
    ap.add_argument("--starved", type=float, default=None, metavar="S",
                    help=f"seconds a station waits beyond its median handover before it counts "
                         f"as starved for a robot (default {STARVED_S_DEFAULT:g})")
    ap.add_argument("--target", type=float, default=None, metavar="RATE",
                    help=f"target totes per hour for the high-rate zone(s) "
                         f"(default {TARGET_RATE_DEFAULT:g}); per-station or per-zone targets go "
                         f"in ess_config.json, and every target can be changed in the report")
    ap.add_argument("--out", default=None, metavar="DIR",
                    help="output folder (default: asrs_analysis_output next to the app)")
    ap.add_argument("--open", action="store_true", help="open the report when it is written")
    ap.add_argument("--quiet", action="store_true", help="only print the final paths")
    # UI options
    ap.add_argument("--browser", action="store_true",
                    help="open the UI in the default browser instead of a window")
    ap.add_argument("--port", type=int, default=0, help="port for the UI (default: automatic)")
    return ap.parse_args(argv)


def _settings(args: argparse.Namespace, groups: list[list[str]]) -> tuple[Settings, list[str]]:
    """CLI flags over ess_config.json (read next to the first log) over defaults."""
    folder = os.path.dirname(os.path.abspath(groups[0][0])) if groups else os.getcwd()
    s, problems = load_settings(folder)
    if args.door is not None:
        s.door_s = args.door
    if args.target is not None:
        s.target_rate = args.target
    if args.starved is not None:
        s.starved_s = args.starved
    if args.no_door:
        s.no_door.update({x.strip(): True for x in args.no_door.split(",") if x.strip()})
    if args.no_door_days:
        s.no_door_days = sorted(set(s.no_door_days) | {x.strip() for x in args.no_door_days.split(",") if x.strip()})
    s.output_root = args.out
    return s, [p for p in problems if "unknown setting" not in p] + s.validate()


def run_headless(args: argparse.Namespace) -> int:
    groups = find_logs(args.logs)
    if not groups:
        print("No .log or .log.gz files found in: " + ", ".join(args.logs), file=sys.stderr)
        return 2

    settings, problems = _settings(args, groups)
    if problems:
        for p in problems:
            print(f"error: {p}", file=sys.stderr)
        return 2

    level_icon = {"head": "\n==", "done": "  [ok]", "warning": "  [warn]",
                  "error": "  [ERROR]", "muted": "  ", "info": "  "}

    def log(level: str, msg: str) -> None:
        if args.quiet and level not in ("warning", "error"):
            return
        print(f"{level_icon.get(level, '  ')} {msg}")

    n_days = len(groups)
    print(f"{n_days} day{'s' if n_days != 1 else ''} to analyse · "
          f"door {settings.door_s:g} s · target {settings.target_rate:g}/h · "
          f"starved over {settings.starved_s:g} s")

    try:
        result = run(groups, settings, on_log=log)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    done, failed = result.completed, [d for d in result.days if not d.ok]
    print(f"\nComplete in {result.seconds:.1f}s — {len(done)} of {n_days} day(s)"
          + (f", {len(failed)} failed" if failed else ""))
    print(f"  Report:  {result.report}")
    for d in done:
        print(f"  {d.date or d.label}: {d.metrics['overall']['visits']:,} totes presented")
    for d in failed:
        print(f"  {d.label}: FAILED — {d.error}", file=sys.stderr)
    print(f"  Folder:  {result.run_dir}")

    if args.open and result.report:
        import webbrowser
        webbrowser.open(f"file://{os.path.abspath(result.report)}")
    return 0 if not failed else 1


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    if not args.logs:
        from server import launch
        launch(port=args.port, output_root=args.out,
               mode="browser" if args.browser else "auto")
        return 0
    return run_headless(args)


if __name__ == "__main__":
    sys.exit(main())
