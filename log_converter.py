"""
log_converter.py — Convert Hairobotics .log files to the same dict structure
                   as data_loader.load_data(): {callback, station, lifecycle}.

Log line format (one event per line):
    [ISO-timestamp][level][app-instance][env][Category][EVENT_TYPE] - {JSON}

Multiple log files for the same day are named with a sequence suffix, e.g.:
    task_chain-2026-06-25.000000.log
    task_chain-2026-06-25.000001.log

Pass all paths for a day to convert_log_to_data() and they will be merged in
filename-sorted order before parsing.

Event-type → sheet mappings (confirmed against real log files):
  TASK_EXECUTION_CALLBACK_SENT:
    CALLBACK_OF_TOTE_LOADED_BY_ROBOT   → callback row, 动作类型='load'
    CALLBACK_OF_TOTE_UNLOADED_BY_ROBOT → callback row, 动作类型='unload'
    CALLBACK_OF_TASK_FINISHED          → callback row, 动作类型='complete'  (stationCode=LABOR-N)
                                       → station row, 事件类型='triggerGo' + 'release'
    CALLBACK_OF_ROBOT_REACH_STATION    → station row, 事件类型='arrived'

  BUSINESS_TASK_DATA_CHANGE (ND task codes only):
    Accumulate across multiple state-change events:
      fromLocationCode (top-level payload) → 起始位置  (only present in some PROCESSING events)
      data.finalToStationCode              → 目标位置
      data.wmsTaskCreated/assignedTime/loadTime/unloadTime/completedTime  → timestamps
    Only include rows where completedTime != 0  (task fully done)
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime

import pandas as pd

# ── Log line regex ─────────────────────────────────────────────────────────────
_LINE_RE = re.compile(
    r'\[([^\]]+)\]'    # 1: ISO timestamp
    r'\[\d+\]'         # log level (ignored)
    r'\[[^\]]+\]'      # app instance (ignored)
    r'\[[^\]]+\]'      # env (ignored)
    r'\[[^\]]+\]'      # category (ignored)
    r'\[([^\]]+)\]'    # 2: event type
    r'\s*-\s*(.+)',    # 3: JSON payload (rest of line)
    re.DOTALL,
)

# Split content at entry boundaries — each entry starts at [ followed by a date
_ENTRY_SPLIT_RE = re.compile(r'(?=\[\d{4}-\d{2}-\d{2}T)')

_ET_SYSTEM   = "SYSTEM_TASK_DATA_CHANGE"
_ET_BIZZ     = "BUSINESS_TASK_DATA_CHANGE"
_ET_CALLBACK = "TASK_EXECUTION_CALLBACK_SENT"

# Callback names that map to the callback sheet and/or station sheet
_CB_LOAD     = "CALLBACK_OF_TOTE_LOADED_BY_ROBOT"
_CB_UNLOAD   = "CALLBACK_OF_TOTE_UNLOADED_BY_ROBOT"
_CB_FINISHED = "CALLBACK_OF_TASK_FINISHED"
_CB_REACHED  = "CALLBACK_OF_ROBOT_REACH_STATION"

# Regex to extract a date from a log filename: YYYY-MM-DD
_DATE_IN_FILENAME_RE = re.compile(r'(\d{4}-\d{2}-\d{2})')


# ── Filename helpers ───────────────────────────────────────────────────────────

def extract_log_date(path: str) -> str | None:
    """Return 'YYYY-MM-DD' found in the filename, or None."""
    m = _DATE_IN_FILENAME_RE.search(os.path.basename(path))
    return m.group(1) if m else None


# ── Timestamp helpers ──────────────────────────────────────────────────────────

def _parse_iso(ts_str: str) -> datetime:
    """Parse ISO 8601 timestamp → naive datetime (timezone stripped)."""
    s = re.sub(r'[+-]\d{2}:\d{2}$', '', ts_str.strip()).rstrip('Z')
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return datetime.fromisoformat(s[:19])


def _ms_to_dt(ms_val) -> datetime | None:
    """Convert millisecond-epoch int/string → naive local datetime. None for 0."""
    try:
        ms = int(ms_val)
        if ms == 0:
            return None
        return datetime.fromtimestamp(ms / 1000.0)
    except (ValueError, TypeError, OSError):
        return None


# ── Robot / location helpers ───────────────────────────────────────────────────

def _robot_type(robot_code: str, location: str = '') -> str:
    if 'haiflex' in location.lower():
        return 'A42'
    if robot_code.lower().startswith('kubot-'):
        return 'K50'
    return 'K50'


def _clean_dest(dest: str) -> str:
    """Strip the #N suffix from destination codes."""
    return dest.split('#')[0] if dest else dest


# ── Entry parser ───────────────────────────────────────────────────────────────

def _parse_entries(content: str) -> list[tuple]:
    """Return list of (ts_str, event_type, payload) for every parseable log entry."""
    entries = []
    for chunk in re.split(_ENTRY_SPLIT_RE, content):
        chunk = chunk.strip()
        if not chunk:
            continue
        m = _LINE_RE.match(chunk)
        if not m:
            continue
        ts_str, event_type, json_str = m.groups()
        try:
            payload = json.loads(json_str.strip())
        except json.JSONDecodeError:
            continue
        entries.append((ts_str, event_type, payload))
    return entries


# ── Callback sheet builder ─────────────────────────────────────────────────────

def _build_callback_df(entries: list) -> pd.DataFrame | None:
    """
    Build the callback sheet (回调明细) from TASK_EXECUTION_CALLBACK_SENT events.

    Three callback names produce rows:
      CALLBACK_OF_TOTE_LOADED_BY_ROBOT   → 动作类型 = 'load'
      CALLBACK_OF_TOTE_UNLOADED_BY_ROBOT → 动作类型 = 'unload'
      CALLBACK_OF_TASK_FINISHED          → 动作类型 = 'complete'  (LABOR stations only)
    """
    rows = []
    for ts_str, event_type, payload in entries:
        if event_type != _ET_CALLBACK:
            continue

        data = payload.get('data', {})
        cb_name = data.get('name', '')
        if cb_name not in (_CB_LOAD, _CB_UNLOAD, _CB_FINISHED):
            continue

        message_str = data.get('message', '')
        try:
            msg = json.loads(message_str) if message_str else {}
        except json.JSONDecodeError:
            msg = {}

        station_code = msg.get('stationCode', '')
        location     = msg.get('locationCode', '')

        # For TASK_FINISHED, only emit for LABOR stations
        if cb_name == _CB_FINISHED and not station_code.startswith('LABOR'):
            continue

        ts         = _parse_iso(ts_str)
        task_codes = payload.get('taskCode', [])
        task_code  = task_codes[0] if task_codes else ''
        task_group = payload.get('taskGroupCode', '') or msg.get('taskGroupCode', '')
        containers = payload.get('containerCode', [])
        container  = containers[0] if containers else ''
        robot_code = msg.get('robotCode', '')
        call_id    = msg.get('callId', '') or str(data.get('id', ''))

        # Determine 动作类型
        if cb_name == _CB_FINISHED:
            action = 'complete'
            # For finished callbacks, stationCode IS the station (e.g. LABOR-7)
            # and locationCode is LT_LABOR:POINT:... — swap for callback sheet convention
            loc       = station_code  # position type = station
            loc_code  = location      # position code = LT_LABOR:POINT
        else:
            action    = msg.get('actionCode', cb_name.split('_')[-1].lower())
            loc       = station_code
            loc_code  = location

        rows.append({
            '时间戳':    ts,
            '动作类型':  action,
            '任务组编号': task_group,
            '任务编号':   task_code,
            '机器人编号': robot_code,
            '容器编号':   container,
            '位置编号':   loc_code,
            '位置类型':   loc,
            '回调ID':    call_id,
            '机器人类型': _robot_type(robot_code, location),
        })

    if not rows:
        return None
    df = pd.DataFrame(rows)
    df.sort_values('时间戳', inplace=True)
    return df.reset_index(drop=True)


# ── Station sheet builder ──────────────────────────────────────────────────────

def _build_station_df(entries: list) -> pd.DataFrame | None:
    """
    Build the station sheet (labor_station_record) from TASK_EXECUTION_CALLBACK_SENT events.

    CALLBACK_OF_ROBOT_REACH_STATION → 事件类型 = 'arrived'
    CALLBACK_OF_TASK_FINISHED       → 事件类型 = 'triggerGo' + 'release'  (LABOR only)

    Deduplication: (event, location, robot, timestamp) must be unique.
    """
    rows: list[dict] = []
    seen: set[tuple] = set()

    def _add(ts, event, loc, robot):
        key = (event, loc, robot, ts)
        if key in seen:
            return
        seen.add(key)
        rows.append({
            '时间戳':    ts,
            '事件类型':  event,
            '位置编号':  loc,
            '机器人编号': robot,
            '机器人类型': _robot_type(robot),
        })

    for ts_str, event_type, payload in entries:
        if event_type != _ET_CALLBACK:
            continue

        data    = payload.get('data', {})
        cb_name = data.get('name', '')
        if cb_name not in (_CB_REACHED, _CB_FINISHED):
            continue

        message_str = data.get('message', '')
        try:
            msg = json.loads(message_str) if message_str else {}
        except json.JSONDecodeError:
            msg = {}

        station_code = msg.get('stationCode', '')
        location     = msg.get('locationCode', '')  # LT_LABOR:POINT:X:Y
        robot_code   = msg.get('robotCode', '')
        ts           = _parse_iso(ts_str)

        if cb_name == _CB_REACHED:
            # Robot arrived at LT_LABOR:POINT queue position
            if not station_code.startswith('LABOR'):
                continue
            loc = location or station_code
            _add(ts, 'arrived', loc, robot_code)

        elif cb_name == _CB_FINISHED:
            # Operator done — robot triggered to leave, then released
            if not station_code.startswith('LABOR'):
                continue
            loc = location or station_code
            _add(ts, 'triggerGo', loc, robot_code)
            _add(ts, 'release',   loc, robot_code)

    if not rows:
        return None

    df = pd.DataFrame(rows)
    df.sort_values('时间戳', inplace=True)
    return df.reset_index(drop=True)


# ── Lifecycle sheet builder ────────────────────────────────────────────────────

def _build_lifecycle_df(entries: list) -> pd.DataFrame | None:
    """
    Build the lifecycle sheet (任务生命周期) from BUSINESS_TASK_DATA_CHANGE events.

    Accumulates data across multiple state-change events per ND task code.
    Only tasks with a non-zero completedTime are included.

    fromLocationCode (起始位置) is captured at the top-level payload because it
    is only populated in certain PROCESSING state events and is empty by FINISHED.
    """
    records: dict[str, dict] = {}

    for ts_str, event_type, payload in entries:
        if event_type != _ET_BIZZ:
            continue

        task_codes = payload.get('taskCode', [])
        if not task_codes:
            continue
        task_code = task_codes[0]
        if not task_code.startswith('ND'):
            continue

        data_inner = payload.get('data', {})

        if task_code not in records:
            records[task_code] = {
                '任务编号':               task_code,
                '任务组编号':             payload.get('taskGroupCode', ''),
                '容器编号':               '',
                '机器人编号':             '',
                '起始位置':              '',
                '目标位置':              '',
                '创建时间':              None,
                '分配时间':              None,
                '取箱时间':              None,
                '放箱时间':              None,
                'complete(任务完成时间)':  None,
            }

        rec = records[task_code]

        # Container
        containers = payload.get('containerCode', [])
        if containers:
            rec['容器编号'] = containers[0]

        # Robot
        robot = data_inner.get('assignedRobotCode', '')
        if robot:
            rec['机器人编号'] = robot

        # Source location: fromLocationCode at TOP-LEVEL payload (only present in
        # some PROCESSING events; empty in FINISHED — capture it whenever we see it)
        from_loc = payload.get('fromLocationCode', '')
        if from_loc and from_loc.startswith('HAI-') and not rec['起始位置']:
            rec['起始位置'] = from_loc

        # Destination workstation: prefer finalToStationCode → nextDestination
        fts = data_inner.get('finalToStationCode', '')
        if fts and fts.startswith('LABOR'):
            rec['目标位置'] = fts
        elif not rec['目标位置']:
            nd = data_inner.get('nextDestination', [])
            if nd and nd[0].startswith('LABOR'):
                rec['目标位置'] = nd[0]

        # Timestamps — only overwrite when a non-zero value arrives
        def _update_ts(field: str, raw) -> None:
            if raw and str(raw) not in ('0', ''):
                dt = _ms_to_dt(raw)
                if dt is not None:
                    rec[field] = dt

        _update_ts('创建时间',              data_inner.get('wmsTaskCreated', '0'))
        _update_ts('分配时间',              data_inner.get('assignedTime',   '0'))
        _update_ts('取箱时间',              data_inner.get('loadTime',       '0'))
        _update_ts('放箱时间',              data_inner.get('unloadTime',     '0'))
        _update_ts('complete(任务完成时间)',  data_inner.get('completedTime',  '0'))

    if not records:
        return None

    df = pd.DataFrame(list(records.values()))

    # Keep only completed tasks
    df = df[df['complete(任务完成时间)'].notna()].copy()
    # Only keep tasks that went to a LABOR station (deliveries, not relocations)
    df = df[df['目标位置'].str.startswith('LABOR', na=False)].copy()

    if df.empty:
        return None

    # Derived duration columns
    def _secs(t1, t2):
        try:
            if pd.notna(t1) and pd.notna(t2):
                return round((t2 - t1).total_seconds())
        except Exception:
            pass
        return None

    df['分配耗时(秒)'] = df.apply(
        lambda r: _secs(r['创建时间'], r['分配时间']), axis=1)

    # 任务全程耗时 = creation → completion.  'wmsTaskCreated' is not always
    # present in this log schema, so fall back to assignedTime as start.
    def _total_secs(r):
        start = r['创建时间'] if pd.notna(r['创建时间']) else r['分配时间']
        return _secs(start, r['complete(任务完成时间)'])

    df['任务全程耗时(秒)'] = df.apply(_total_secs, axis=1)

    # Drop columns that are entirely None — they confuse the validator's
    # timestamp checks and add no analytical value.
    df.dropna(axis=1, how='all', inplace=True)

    return df.reset_index(drop=True)


# ── Public entry points ────────────────────────────────────────────────────────

def convert_log_to_data(
    log_paths: str | list[str],
) -> dict[str, pd.DataFrame | None]:
    """
    Parse one or more Hairobotics .log files and return a data dict compatible
    with data_loader.load_data():

        {
            'callback':  pd.DataFrame | None,   # 回调明细
            'station':   pd.DataFrame | None,   # labor_station_record
            'lifecycle': pd.DataFrame | None,   # 任务生命周期
        }

    When multiple paths are supplied they are sorted by filename (chronological)
    and concatenated before parsing — use this for split-log days.

    Raises ValueError if no files can be read or no usable events are found.
    """
    if isinstance(log_paths, str):
        log_paths = [log_paths]

    # Sort by filename to preserve chronological order
    log_paths = sorted(log_paths, key=os.path.basename)

    content_parts: list[str] = []
    for path in log_paths:
        try:
            with open(path, encoding='utf-8', errors='replace') as fh:
                content_parts.append(fh.read())
        except OSError as exc:
            raise ValueError(f"Cannot read log file '{os.path.basename(path)}': {exc}") from exc

    content = '\n'.join(content_parts)
    if not content.strip():
        raise ValueError("Log file(s) are empty.")

    entries = _parse_entries(content)
    if not entries:
        raise ValueError(
            "No parseable log entries found. "
            "Check that the file(s) match the expected Hairobotics log format."
        )

    return {
        'callback':  _build_callback_df(entries),
        'station':   _build_station_df(entries),
        'lifecycle': _build_lifecycle_df(entries),
    }
