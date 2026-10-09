#!/usr/bin/env python3
"""
ai-codex for ESS Analyzer (Python + FastAPI web UI + Plotly)
Generates a compact codebase index for AI context injection.
"""

import os
import re
from datetime import datetime

# ---------------------------------------------------------------------------
# Path Configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = SCRIPT_DIR
TODAY = datetime.today().strftime('%Y-%m-%d')
OUTPUT_DIR = os.path.join(ROOT, '.ai-codex')

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def read_file_safe(filepath: str) -> str:
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            return f.read()
    except Exception:
        return ''

def pad(string: str, length: int) -> str:
    return string.ljust(length)

def extract_module_docstring(content: str) -> str:
    """Extract the first triple-quoted string at the top of a module."""
    match = re.match(r'\s*(?:"""(.*?)"""|\'\'\'(.*?)\'\'\')', content, re.DOTALL)
    if match:
        doc = (match.group(1) or match.group(2)).strip()
        # Collapse to first line only
        return doc.split('\n')[0].strip()
    return ''

def extract_classes(content: str) -> list[dict]:
    """Extract class names and their method signatures."""
    classes = []
    # Find class definitions
    class_pattern = re.compile(r'^class\s+(\w+)(?:\(([^)]*)\))?:', re.MULTILINE)
    for m in class_pattern.finditer(content):
        name = m.group(1)
        base = m.group(2) or ''
        # Find methods within this class (until next class or EOF)
        class_start = m.end()
        next_class = class_pattern.search(content, class_start)
        class_body = content[class_start: next_class.start() if next_class else len(content)]
        methods = extract_functions(class_body, method=True)
        classes.append({'name': name, 'base': base, 'methods': methods})
    return classes

def extract_functions(content: str, method: bool = False) -> list[dict]:
    """Extract function/method names and their parameter signatures."""
    results = []
    # Match def lines, handling async def and indentation
    if method:
        # Methods: indented defs
        pattern = re.compile(r'^\s{4,}(?:async\s+)?def\s+(\w+)\s*\((.*?)\)', re.MULTILINE)
    else:
        # Module-level defs: no or minimal indentation
        pattern = re.compile(r'^(?:async\s+)?def\s+(\w+)\s*\((.*?)\)', re.MULTILINE)

    for m in pattern.finditer(content):
        name = m.group(1)
        params = m.group(2).strip()
        if len(params) > 60:
            params = params[:57] + '...'
        results.append({'name': name, 'params': params})
    return results

def extract_constants(content: str) -> list[dict]:
    """Extract top-level constant assignments (UPPER_CASE names)."""
    results = []
    pattern = re.compile(r'^([A-Z][A-Z0-9_]+)\s*=\s*(.+)', re.MULTILINE)
    for m in pattern.finditer(content):
        name = m.group(1)
        value = m.group(2).strip()
        if len(value) > 60:
            value = value[:57] + '...'
        results.append({'name': name, 'value': value})
    return results

# ---------------------------------------------------------------------------
# 1. python-modules.md
# ---------------------------------------------------------------------------

def generate_python_modules() -> str:
    root_py_files = [
        'app.py', 'config.py', 'log_parser.py', 'metrics.py',
        'report.py', 'pipeline.py', 'server.py',
    ]

    output = [
        f"# Python Modules Map (generated {TODAY})",
        f"# Root-level modules: classes, methods, top-level functions",
        ""
    ]

    for filename in root_py_files:
        filepath = os.path.join(ROOT, filename)
        if not os.path.exists(filepath):
            continue

        content = read_file_safe(filepath)
        if not content:
            continue

        line_count = len(content.splitlines())
        docstring = extract_module_docstring(content)
        classes = extract_classes(content)
        top_fns = extract_functions(content)

        output.append(f"## {filename}  ({line_count} lines)")
        if docstring:
            output.append(f"   {docstring}")
        output.append("")

        if classes:
            for cls in classes:
                base_str = f"({cls['base']})" if cls['base'] else ""
                output.append(f"   class {cls['name']}{base_str}")
                for m in cls['methods']:
                    output.append(f"     def {pad(m['name'], 28)} ({m['params']})")
                output.append("")

        if top_fns:
            output.append("   Top-level functions:")
            for fn in top_fns:
                output.append(f"     def {pad(fn['name'], 28)} ({fn['params']})")
            output.append("")

    return '\n'.join(output)

# ---------------------------------------------------------------------------
# 3. data-schema.md
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 2. log-schema.md
# ---------------------------------------------------------------------------

EVENTS = [
    ('CALLBACK_OF_ROBOT_REACH_STATION', 'arrival',
     'robot reaches a station (the tote becomes pickable)'),
    ('EssKubotStationHandleLetRobotGo', 'release',
     '"station: X robot: Y will leave" - the operator release'),
    ('CALLBACK_OF_TOTE_LOADED_BY_ROBOT', 'move start', 'tote picked up'),
    ('CALLBACK_OF_TOTE_UNLOADED_BY_ROBOT', 'move end', 'tote put down'),
    ('CALLBACK_OF_TASK_ALLOCATED', 'busy from', 'robot given a task; stationCode = K50 destination'),
    ('wmsTask[...]: ND... is created', 'supply', 'task created by the warehouse system (destinationCodes)'),
    ('CALLBACK_OF_TASK_EXCEPTION', 'closure', "message DISABLED_TARGET = the task's station is disabled"),
    ('CALLBACK_OF_LOCATION_ABNORMAL', 'fault', 'load/unload tries at a slot over the limit (by location)'),
    ('CALLBACK_OF_TOTE_LOAD_FAILED', 'fault', 'a robot could not take a tote from a slot'),
    ('CALLBACK_OF_TASK_SUSPENDED', 'fault', 'a robot stopped mid-task (e.g. could not put the tote)'),
    ('CALLBACK_OF_ROBOT_ABNORMAL', 'fault', 'robot fault: chassis, lift, box dropped, unreachable, ...'),
    ('CALLBACK_OF_TASK_CANCELLED', 'fault', 'a task cancelled'),
]

DEFINITIONS = [
    'operator time   release - arrival, as logged (so it includes door travel)',
    'gap             next arrival at that station - release, as logged',
    'switch time     gap + door_s (the door-open command is logged at the arrival,',
    '                so the door\'s physical travel never appears in the log)',
    'K50 cycle       buffer pickup -> one or more station visits -> buffer return',
    'multi-station   a cycle whose tote was presented at 2+ stations before returning',
    'ACR move        storage->buffer = put, buffer->storage = store, else relocation',
    'hour budget     door + picking + switch + waiting, every interval clipped to the',
    '                hour, so the shares always sum to the hour',
    'time budget     3600 / target seconds per tote vs mean pick + switch + wait',
    '                (means, because they add up to the real cycle = 3600 / rate)',
    'target pickable 1 - target x median switch / 3600',
    'robot states    on a task (allocation -> tote put down) / between tasks (gap < AWAY_MIN_S)',
    '                / away (gap >= AWAY_MIN_S, most likely charging - not in the log)',
    'utilization     on a task / available (on task + between); also / day fleet (lower bound)',
    'task supply     created -> tote ready in buffer (ACR put; same slot as the K50 pickup)',
    '                -> K50 allocated; a K50 is never allocated before the tote is ready',
    'handover        a release and the next arrival at that station; its wait is the gap',
    '                beyond the station median gap; starved = wait > starved_s (default 1 s)',
    'starve stages   where the arriving robot was in each waiting second: no task yet /',
    '                ACR / tote ready, no K50 / K50 to buffer / carrying the tote /',
    '                at another station - split exactly, so they sum to the wait',
    'closed          a handover whose leaving robot was held over CLOSED_HOLD_S (break,',
    '                shift change) or whose gap holds a DISABLED_TARGET exception for a',
    '                task bound there; reported apart, never counted as starvation',
    'pick before     operator time of the visit just released, in bands, vs starvation',
    'refill          starved after a pick < 12 s: previous arrival -> next arrival',
    'K50 cycle time  fetch (alloc -> pickup) / travel / queue / at station (first',
    '                arrival -> last release) / return (-> buffer unload); travel =',
    '                min(pickup -> arrival, free-flow), free-flow = 10th percentile per',
    '                station x buffer aisle; queue = the rest',
    'en route        K50s allocated to the station, not yet arrived, at the release',
    'station slots   tasks assigned to a station (K50 alloc -> release); a limit is a',
    '                ceiling it sits at while its ready totes pile up behind it',
    'zone            stations on the same row (same Y of their LT_LABOR:POINT)',
    'rack slot       HAI-<aisle>-<bay>-<level>_<depth>[_coop_kubot|_coop_haiflex]',
    'aisle crowding  allocation -> pickup vs other trips of that fleet bound for the same',
    '                aisle at the allocation; excess over the median lead at the same',
    '                fleet-wide load; overlap vs chance = same / (fleet x sum share^2)',
    'return trip     a store (buffer -> storage) whose tote is put again later; minutes',
    '                until that next put, in bands',
    'buffer travel   K50 buffer pickup -> first arrival, by buffer aisle x station',
    'flagged pickup  K50 buffer load preceded (<= 10 min) by LOCATION_ABNORMAL',
    '                LOAD_FAILED_COUNT_EXCEEDED_THE_LIMIT for that tote and slot',
    'slot vs tote    next pickup by the same slot (other tote) / tote (other slot)',
    '                after a flagged vs a clean pickup',
    'stuck slot      5+ TOTE_LOAD_FAILED at one storage slot in a day (left out of',
    '                the robot faults: the slot is the problem)',
    'robot fault     kinds read by message; expected = kind total x robot share of',
    '                the fleet tasks; dispersion = chi2/(robots-1), 1 = chance',
    'speed index     K50: return trip / day median for station x buffer aisle;',
    '                ACR: handling / day median for the rack level',
]

SETTINGS = [
    ('door_s', 'DOOR_S_DEFAULT', 'seconds of door travel added to every switch'),
    ('target_rate', 'TARGET_RATE_DEFAULT', 'auto target for high-rate zones'),
    ('starved_s', 'STARVED_S_DEFAULT', 'wait beyond the median handover that counts as starved (run setting)'),
]
SETTING_TABLES = ['targets  {station|zone: totes/h}', 'pick_s   {station|zone: s}  blank = budget - switch',
                  'switch_s {station|zone: s}  blank = measured median switch',
                  'no_door  {station|zone: bool} True = no door (no door seconds there); default: door',
                  'no_door_days [YYYY-MM-DD]   days the doors were not in use: no door seconds at all']

TUNING = ('FULL_HOUR_SHARE', 'HIGH_RATE_SHARE', 'LONG_PICK_S',
          'IDLE_MIN_MINUTES', 'MAX_SWITCH_S', 'DOOR_S_MAX', 'AWAY_MIN_S', 'CLOSED_HOLD_S')


def _const(content: str, name: str) -> str:
    m = re.search(r'^' + name + r'\s*=\s*(.+)$', content, re.MULTILINE)
    return m.group(1).strip() if m else '?'


def generate_log_schema() -> str:
    """Document the log events consumed and the settings that tune a run."""
    parser = read_file_safe(os.path.join(ROOT, 'log_parser.py'))
    cfg = read_file_safe(os.path.join(ROOT, 'config.py'))
    if not parser or not cfg:
        return ''

    out = [
        f"# Log Schema Reference (generated {TODAY})",
        "# Input: Hairobotics play_extract application logs (.log / .log.gz)",
        "",
        "## Log line format",
        "  [thread] YYYY-MM-DD HH:MM:SS,mmm [LEVEL] from <class>-line:<n> - <body>",
        "  Timestamps are the log line's own local time, millisecond precision.",
        "",
        "## Lines consumed",
    ]
    for name, role, note in EVENTS:
        out.append(f"  {pad(name, 36)} {pad(role, 11)} {note}")
    out += [
        "",
        "  NOTE  CALLBACK_OF_TASK_FINISHED fires in the same millisecond as the arrival,",
        "        so it is never the operator release. Only the 'will leave' line is.",
        "",
        "## Native frames (log_parser.LogData)",
        "  arrivals     ts, station, robot, tote, point",
        "  releases     ts, station, robot",
        "  tote_events  ts, kind (load/unload), robot, tote, loc, task",
        "  allocations  ts, robot, task, station",
        "  created      ts, task, dest",
        "  exceptions   ts, task, message",
        "  faults       ts, kind, loc, tote, robot, task, message",
        "  moves        robot, tote, t_load, t_unload, from_loc, to_loc, task",
        "  roles        {robot: 'K50' | 'ACR'}",
        "",
        "## Robot roles (read from behaviour, not from the robot numbering)",
        "  K50   reaches a station, reports a HAIFLEX type, or works the haiflex buffer",
        "  ACR   everything else - shelf storage to and from the kubot buffer",
        "",
        "## Location tokens",
    ]
    for const in ('BUFFER_K50', 'BUFFER_ACR', 'STATION_PREFIX'):
        out.append(f"  {pad(const, 16)} {_const(parser, const)}")

    out += ["", "## Metric definitions"]
    out += ["  " + line for line in DEFINITIONS]

    out += ["", "## Settings (config.Settings; ess_config.json next to the logs)"]
    for key, default, note in SETTINGS:
        out.append(f"  {pad(key, 14)} default {pad(_const(cfg, default), 8)} {note}")

    for line in SETTING_TABLES:
        out.append("  " + line)
    out.append("  station entries beat zone entries; 0 = none")

    out += ["", "## Tuning constants (config.py)"]
    for const in TUNING:
        out.append(f"  {pad(const, 20)} {_const(cfg, const)}")

    out += [
        "",
        "## Output per run",
        "  <run>/station_robot_cycle_report.html   one self-contained file: summary + every day",
        "",
        "## Where each number is computed",
        "  metrics.py (Python)          door- and target-independent: operator time, cycles,",
        "                               utilization, zero-door hour budget, sorted raw arrays",
        "  templates/engine.js (browser) everything that moves with door_s or a target:",
        "                               switch stats, time pickable, time budget, summary",
        "",
    ]
    return '\n'.join(out)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print('\nai-codex -- ESS Analyzer Indexer\n')

    try:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
    except Exception as e:
        print(f"Error: could not create output directory \"{OUTPUT_DIR}\": {e}")
        return

    generators = [
        ('python-modules.md',   generate_python_modules),
        ('log-schema.md',       generate_log_schema),
    ]

    total_files = 0
    total_lines = 0

    for filename, generator in generators:
        try:
            content = generator()
        except Exception as e:
            print(f"  {pad(filename, 25)} ERROR: {e}")
            continue

        if not content:
            print(f"  {pad(filename, 25)} skipped (no content)")
            continue

        line_count = len(content.split('\n'))
        total_lines += line_count
        total_files += 1

        out_path = os.path.join(OUTPUT_DIR, filename)
        try:
            with open(out_path, 'w', encoding='utf-8') as f:
                f.write(content)
        except Exception as e:
            print(f"  {pad(filename, 25)} ERROR writing: {e}")
            continue

        print(f"  {pad(filename, 25)} {line_count} lines  ->  {out_path}")

    print(f"\n  Total: {total_lines} lines across {total_files} files")
    print(f"  Output: {os.path.relpath(OUTPUT_DIR, ROOT)}/\n")


if __name__ == '__main__':
    main()
