"""
data_loader.py — Excel ingestion, sheet-signature detection, and runtime config
               builder.  All data-shape knowledge lives here.
"""
from __future__ import annotations

import io
import json
import os
import re
from dataclasses import dataclass, field

import pandas as pd

from config import (
    AMR_DELIVERY_TYPE_HINT,
    ANALYSIS_MODULES,
    AUTO_TYPE_PALETTE,
    PICK_START_EVENT_DEFAULT,
    PICK_START_EVENTS,
    STAGE_COLORS,
    STAGE_LABEL_MAP,
    SWITCH_S_FALLBACK,
    TOTAL_DURATION_COL,
)

# ── Supported file extensions ────────────────────────────────────────────────
_VALID_EXTENSIONS = {".xlsx", ".xlsm", ".log"}


# ── Validation result container ──────────────────────────────────────────────

@dataclass
class ValidationResult:
    """Collects warnings and errors from file / data validation."""
    errors:   list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)


# ── File-level validation ────────────────────────────────────────────────────

def validate_file_path(path: str) -> ValidationResult:
    """Check that *path* points to a readable Excel file before opening it."""
    vr = ValidationResult()
    if not os.path.isfile(path):
        vr.add_error(f"File not found: {path}")
        return vr

    ext = os.path.splitext(path)[1].lower()
    if ext not in _VALID_EXTENSIONS:
        vr.add_error(
            f"Unsupported file type '{ext}'. "
            f"Expected one of: {', '.join(sorted(_VALID_EXTENSIONS))}"
        )
        return vr

    size = os.path.getsize(path)
    if size == 0:
        vr.add_error("File is empty (0 bytes).")
    elif size < 4096 and ext != ".log":
        vr.add_warning(
            f"File is very small ({size:,} bytes) — it may not contain usable data."
        )
    return vr


# ── Column-signature requirements per sheet ──────────────────────────────────

# Minimum columns that MUST be present for each detected sheet to be usable.
_REQUIRED_COLUMNS: dict[str, list[str]] = {
    "callback": ["动作类型", "位置类型"],
    "station":  ["事件类型"],
    "lifecycle": [],  # detected by duration column — validated separately
}

# Columns that SHOULD be present (analysis degrades without them).
_EXPECTED_COLUMNS: dict[str, list[str]] = {
    "callback":  ["时间戳", "机器人编号", "位置编号", "容器编号"],
    "station":   ["时间戳", "机器人编号", "位置编号", "机器人类型"],
    "lifecycle": ["任务全程耗时(秒)", "目标位置", "起始位置", "容器编号"],
}

_MIN_ROWS = 10  # sheets with fewer rows than this trigger a warning

# Minimum 'ppReady' events for the station-readiness analysis to run.
# Keep in sync with analyses/station_readiness.py (_MIN_PPREADY_EVENTS).
_MIN_PPREADY_EVENTS = 50


def validate_data(data: dict[str, pd.DataFrame | None]) -> ValidationResult:
    """Validate loaded sheet data for schema conformance and quality."""
    vr = ValidationResult()

    # callback and station are mandatory
    for key in ("callback", "station"):
        df = data.get(key)
        if df is None:
            vr.add_error(f"Required sheet '{key}' was not detected in the file.")
            continue
        _validate_sheet(vr, key, df)

    # lifecycle is optional but validated when present
    tlc = data.get("lifecycle")
    if tlc is not None:
        _validate_sheet(vr, "lifecycle", tlc)
        # Must have at least one duration column
        dur_cols = [c for c in tlc.columns if "耗时" in str(c)]
        if not dur_cols:
            vr.add_error(
                "lifecycle sheet has no duration columns (耗时). "
                "Cycle time and retrieval analyses will fail."
            )

    return vr


def _validate_sheet(
    vr: ValidationResult, key: str, df: pd.DataFrame
) -> None:
    """Check a single sheet for required/expected columns and row counts."""
    cols = set(df.columns.astype(str))

    # Required columns
    for req in _REQUIRED_COLUMNS.get(key, []):
        if req not in cols:
            vr.add_error(
                f"'{key}' sheet is missing required column '{req}'."
            )

    # Expected columns (warnings only)
    for exp in _EXPECTED_COLUMNS.get(key, []):
        if exp not in cols:
            vr.add_warning(
                f"'{key}' sheet is missing expected column '{exp}' — "
                f"some analyses may be limited."
            )

    # Row count
    if len(df) == 0:
        vr.add_error(f"'{key}' sheet is empty (0 rows).")
    elif len(df) < _MIN_ROWS:
        vr.add_warning(
            f"'{key}' sheet has only {len(df)} row(s) — "
            f"results may not be meaningful."
        )

    # Timestamp sanity: at least one parseable timestamp
    ts_col = next((c for c in df.columns if "时间戳" in str(c) or "时间" in str(c)), None)
    if ts_col is not None:
        parsed = pd.to_datetime(df[ts_col], errors="coerce")
        valid_count = parsed.notna().sum()
        if valid_count == 0:
            vr.add_error(
                f"'{key}' sheet column '{ts_col}' contains no parseable timestamps."
            )
        elif valid_count < len(df) * 0.5:
            bad_pct = round((1 - valid_count / len(df)) * 100)
            vr.add_warning(
                f"'{key}' sheet: {bad_pct}% of rows in '{ts_col}' "
                f"have unparseable timestamps."
            )


# ── Analysis preflight — which analyses can actually run on this data ─────────
#
# validate_data() checks sheet/column schema.  This goes one level deeper and
# looks at the *event content* each analysis actually needs (e.g. the station
# sheet must carry 'arrived'/'release' events, not just 'triggerGo'), so the GUI
# can tell the user up front which analyses will run, be limited, or produce
# nothing — instead of them silently returning [].

# Status values, ordered from most to least capable (used when aggregating
# across several loaded days: the best status any day achieves wins).
PREFLIGHT_OK          = "ok"
PREFLIGHT_DEGRADED    = "degraded"
PREFLIGHT_UNAVAILABLE = "unavailable"
_PREFLIGHT_RANK = {PREFLIGHT_OK: 2, PREFLIGHT_DEGRADED: 1, PREFLIGHT_UNAVAILABLE: 0}


def best_preflight_status(statuses: list[str]) -> str:
    """Return the most-capable status in a list (see _PREFLIGHT_RANK)."""
    if not statuses:
        return PREFLIGHT_UNAVAILABLE
    return max(statuses, key=lambda s: _PREFLIGHT_RANK.get(s, 0))


def _analysis_status(key: str, f: dict) -> tuple[str, str]:
    """(status, reason) for one analysis given a facts dict from _preflight_facts.

    Preconditions are kept coarse and stable — sheet presence plus which station
    event types are logged — matching the guards inside each analysis module.
    """
    ev      = f["station_events"]
    pp      = f["station_event_counts"].get("ppReady", 0)
    have_st = f["has_station"]

    def _found() -> str:
        return ", ".join(sorted(ev)) if ev else "none"

    if key == "throughput":
        if not have_st or "triggerGo" not in ev:
            return PREFLIGHT_UNAVAILABLE, "station sheet has no 'triggerGo' events to count completions"
        if not {"arrived", "release"} <= ev:
            return (PREFLIGHT_DEGRADED,
                    "no 'arrived'/'release' events — completion counts work, but the "
                    "'% of implied throughput' and active-time views will be blank")
        return PREFLIGHT_OK, ""

    if key == "dwell":
        if not have_st or "triggerGo" not in ev:
            return (PREFLIGHT_UNAVAILABLE,
                    f"needs 'triggerGo' events (station sheet has: {_found()})")
        if "arrived" not in ev:
            # No 'arrived' — the ppReady→triggerGo fallback can stand in, but only
            # if the user confirms it in the GUI (see pick_source_status).
            if pp >= _MIN_PPREADY_EVENTS:
                return (PREFLIGHT_DEGRADED,
                        "no 'arrived' events — pick time can fall back to "
                        "'ppReady'→'triggerGo' if you confirm it before running (RUN card)")
            return (PREFLIGHT_UNAVAILABLE,
                    f"needs 'arrived'→'triggerGo' pairs, and too few 'ppReady' events "
                    f"({pp}) to fall back on (station sheet has: {_found()})")
        return PREFLIGHT_OK, ""

    if key == "switch":
        if not have_st or not {"release", "arrived"} <= ev:
            return (PREFLIGHT_UNAVAILABLE,
                    f"needs 'release'→'arrived' gaps (station sheet has: {_found()})")
        return PREFLIGHT_OK, ""

    if key == "readiness":
        if not have_st or pp < _MIN_PPREADY_EVENTS:
            return (PREFLIGHT_UNAVAILABLE,
                    f"needs at least {_MIN_PPREADY_EVENTS} 'ppReady' events (found {pp})")
        if "arrived" not in ev:
            return (PREFLIGHT_DEGRADED,
                    "'ppReady' events present but no 'arrived' events to compare against")
        return PREFLIGHT_OK, ""

    if key in ("backlog", "retrieval"):
        if not f["has_lifecycle"]:
            return PREFLIGHT_UNAVAILABLE, "requires the lifecycle sheet, which is not present"
        return PREFLIGHT_OK, ""

    if key == "fleet":
        if not f["has_lifecycle"] and not have_st:
            return PREFLIGHT_UNAVAILABLE, "requires the lifecycle and/or station sheet"
        if not f["has_lifecycle"]:
            return (PREFLIGHT_DEGRADED,
                    "no lifecycle sheet — only the fleet-utilisation timeseries runs "
                    "(queue depth and Little's law are skipped)")
        return PREFLIGHT_OK, ""

    if key == "robot":
        if not f["has_lifecycle"] and not have_st:
            return PREFLIGHT_UNAVAILABLE, "requires the lifecycle or station sheet"
        return PREFLIGHT_OK, ""

    if key == "returns":
        if not f["has_callback"]:
            return PREFLIGHT_UNAVAILABLE, "requires the callback sheet, which is not present"
        return PREFLIGHT_OK, ""

    if key == "efficiency":
        if not f["has_efficiency"]:
            return PREFLIGHT_UNAVAILABLE, "requires the HPS3 efficiency sheet, which is not present"
        return PREFLIGHT_OK, ""

    if key == "quality":
        return PREFLIGHT_OK, ""

    # Unknown / disabled keys (e.g. cycle) — treat as ok so we never hide a
    # module the registry adds later without a rule here.
    return PREFLIGHT_OK, ""


def _preflight_facts(data: dict[str, pd.DataFrame | None]) -> dict:
    lsr = data.get("station")
    events: dict[str, int] = {}
    if lsr is not None and "事件类型" in lsr.columns:
        events = {
            str(k): int(v)
            for k, v in lsr["事件类型"].dropna().astype(str).value_counts().items()
        }
    return {
        "has_callback":         data.get("callback")   is not None,
        "has_station":          lsr is not None,
        "has_lifecycle":        data.get("lifecycle")  is not None,
        "has_efficiency":       data.get("efficiency") is not None,
        "station_events":       set(events.keys()),
        "station_event_counts": events,
    }


def preflight_analyses(data: dict[str, pd.DataFrame | None]) -> list[dict]:
    """Report which analyses can run against `data`.

    Returns one dict per analysis in ANALYSIS_MODULES order:
        {"key", "label", "status", "reason"}
    where status is one of PREFLIGHT_OK / PREFLIGHT_DEGRADED / PREFLIGHT_UNAVAILABLE.
    'reason' is a human-readable explanation (empty when status is OK).
    """
    facts = _preflight_facts(data)
    findings: list[dict] = []
    for key, label in ANALYSIS_MODULES:
        status, reason = _analysis_status(key, facts)
        findings.append({"key": key, "label": label, "status": status, "reason": reason})
    return findings


def pick_source_status(data: dict[str, pd.DataFrame | None]) -> dict:
    """Which pick-time start event(s) the station data supports.

    Returns {"arrived": bool, "ppready": bool}:
      • arrived  — standard 'arrived'→'triggerGo' picks are countable.
      • ppready  — the 'ppReady'→'triggerGo' fallback is countable (enough
                   ppReady events and triggerGo present) when 'arrived' is absent.
    The GUI uses this to decide whether to offer / confirm the ppReady fallback.
    """
    facts   = _preflight_facts(data)
    ev      = facts["station_events"]
    pp      = facts["station_event_counts"].get("ppReady", 0)
    have_tg = "triggerGo" in ev
    return {
        "arrived": have_tg and "arrived" in ev,
        "ppready": have_tg and pp >= _MIN_PPREADY_EVENTS,
    }


# ── User config validation ───────────────────────────────────────────────────

_USER_CFG_KEYS = {
    "station_types", "design_rates", "type_colors", "amr_type",
    "switch_s_fixed", "switch_mode", "pick_start_event",
}


def validate_user_config(cfg: dict) -> ValidationResult:
    """Check that asrs_config.json has a valid structure."""
    vr = ValidationResult()
    if not isinstance(cfg, dict):
        vr.add_error("asrs_config.json must be a JSON object (dict), "
                     f"got {type(cfg).__name__}.")
        return vr

    unknown = set(cfg.keys()) - _USER_CFG_KEYS
    if unknown:
        vr.add_warning(
            f"asrs_config.json has unknown keys: {', '.join(sorted(unknown))}. "
            f"Valid keys: {', '.join(sorted(_USER_CFG_KEYS))}"
        )

    for key in ("station_types", "design_rates", "type_colors"):
        val = cfg.get(key)
        if val is not None and not isinstance(val, dict):
            vr.add_error(
                f"asrs_config.json '{key}' must be an object/dict, "
                f"got {type(val).__name__}."
            )

    amr = cfg.get("amr_type")
    if amr is not None and not isinstance(amr, str):
        vr.add_error(
            f"asrs_config.json 'amr_type' must be a string, "
            f"got {type(amr).__name__}."
        )

    sw = cfg.get("switch_s_fixed")
    if sw is not None and (not isinstance(sw, (int, float)) or isinstance(sw, bool) or sw < 0):
        vr.add_error(
            f"asrs_config.json 'switch_s_fixed' must be a non-negative number, "
            f"got {sw!r}."
        )

    mode = cfg.get("switch_mode")
    if mode is not None and mode not in ("fixed", "measured"):
        vr.add_error(
            f"asrs_config.json 'switch_mode' must be 'fixed' or 'measured', "
            f"got {mode!r}."
        )

    pse = cfg.get("pick_start_event")
    if pse is not None and pse not in PICK_START_EVENTS:
        vr.add_error(
            f"asrs_config.json 'pick_start_event' must be one of "
            f"{', '.join(PICK_START_EVENTS)}, got {pse!r}."
        )

    # design_rates values must be numeric
    rates = cfg.get("design_rates")
    if isinstance(rates, dict):
        for k, v in rates.items():
            if not isinstance(v, (int, float)):
                vr.add_error(
                    f"asrs_config.json design_rates['{k}'] must be a number, "
                    f"got {type(v).__name__}."
                )

    return vr


# ── DataFrame ↔ JSON helpers for dcc.Store ────────────────────────────────────

def df_to_store(df: pd.DataFrame) -> str:
    """Serialise a DataFrame to a JSON string suitable for dcc.Store."""
    return df.to_json(orient="split", date_format="iso", default_handler=str)


def df_from_store(json_str: str) -> pd.DataFrame:
    """Deserialise a DataFrame that was stored with df_to_store."""
    return pd.read_json(io.StringIO(json_str), orient="split")


# ── Sheet-signature detection ─────────────────────────────────────────────────

def _read_sheets(xl: pd.ExcelFile) -> dict[str, pd.DataFrame | None]:
    data: dict[str, pd.DataFrame | None] = {
        "callback":   None,
        "station":    None,
        "lifecycle":  None,
        "efficiency": None,
    }

    for name in xl.sheet_names:
        try:
            df   = pd.read_excel(xl, sheet_name=name, nrows=3)
            cols = set(df.columns.astype(str))

            if {"动作类型", "位置类型"}.issubset(cols):
                data["callback"] = pd.read_excel(xl, sheet_name=name)

            elif (
                "事件类型" in cols
                and TOTAL_DURATION_COL not in cols
                and "K50完成耗时(秒)" not in cols
            ):
                data["station"] = pd.read_excel(xl, sheet_name=name)

            elif TOTAL_DURATION_COL in cols or "K50完成耗时(秒)" in cols:
                data["lifecycle"] = pd.read_excel(xl, sheet_name=name)

            elif "效率瓶颈" in cols or "自然小时" in cols:
                data["efficiency"] = pd.read_excel(xl, sheet_name=name)

        except Exception:
            continue

    # Fallback by known sheet names
    fallbacks = {
        "callback":   ["回调明细"],
        "station":    ["labor_station_record"],
        "lifecycle":  ["任务生命周期"],
        "efficiency": ["HPS3效率对比"],
    }
    for key, candidates in fallbacks.items():
        if data[key] is None:
            for c in candidates:
                if c in xl.sheet_names:
                    try:
                        data[key] = pd.read_excel(xl, sheet_name=c)
                    except Exception:
                        continue
                    break

    missing = [k for k in ("callback", "station") if data[k] is None]
    if missing:
        available = ", ".join(xl.sheet_names) if xl.sheet_names else "(none)"
        raise ValueError(
            f"Could not identify required sheet(s): {', '.join(missing)}.\n"
            f"The file must contain sheets with the correct column signatures.\n"
            f"Sheets found in file: {available}\n\n"
            f"Expected signatures:\n"
            f"  • callback: columns '动作类型' and '位置类型'\n"
            f"  • station:  column '事件类型'\n"
            f"  • lifecycle (optional): column '{TOTAL_DURATION_COL}'"
        )
    return data


# ── Public load functions ─────────────────────────────────────────────────────

def load_log_day(paths: list[str]) -> dict[str, pd.DataFrame | None]:
    """Load one or more .log files that together cover a single operational day.

    Files are sorted by filename before parsing so split-log days are merged
    in chronological order.  Raises ValueError on read errors or empty output.
    """
    from log_converter import convert_log_to_data
    return convert_log_to_data(paths)


def load_data(path: str) -> dict[str, pd.DataFrame | None]:
    """Load an xlsx or .log file from a filesystem path.

    Raises ValueError for unreadable files or missing required sheets/events.
    For richer validation (warnings, data quality) call validate_file_path()
    and validate_data() separately.
    """
    if os.path.splitext(path)[1].lower() == ".log":
        from log_converter import convert_log_to_data
        return convert_log_to_data([path])

    try:
        xl = pd.ExcelFile(path, engine="calamine")
    except Exception as exc:
        raise ValueError(
            f"Cannot open '{os.path.basename(path)}' as an Excel file: {exc}"
        ) from exc

    # Close the file handle when done — otherwise the xlsx stays locked on
    # Windows until garbage collection.
    with xl:
        if not xl.sheet_names:
            raise ValueError(
                f"'{os.path.basename(path)}' contains no sheets."
            )
        return _read_sheets(xl)


def load_data_from_bytes(content_bytes: bytes) -> dict[str, pd.DataFrame | None]:
    """Load an xlsx file from raw bytes (Dash upload callback)."""
    xl = pd.ExcelFile(io.BytesIO(content_bytes), engine="calamine")
    return _read_sheets(xl)


def load_user_config(xlsx_path: str) -> tuple[dict, ValidationResult]:
    """Load optional asrs_config.json from the same directory as the xlsx.

    Returns (config_dict, validation_result).  If no config file exists the
    dict is empty and the result has no issues.
    """
    vr = ValidationResult()
    cfg_path = os.path.join(os.path.dirname(xlsx_path), "asrs_config.json")
    if not os.path.isfile(cfg_path):
        return {}, vr

    try:
        with open(cfg_path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except json.JSONDecodeError as exc:
        vr.add_error(f"asrs_config.json is not valid JSON: {exc}")
        return {}, vr
    except Exception as exc:
        vr.add_error(f"Cannot read asrs_config.json: {exc}")
        return {}, vr

    cfg_vr = validate_user_config(raw)
    vr.errors.extend(cfg_vr.errors)
    vr.warnings.extend(cfg_vr.warnings)

    if not cfg_vr.ok:
        return {}, vr
    return raw, vr


def filter_to_peak_day(
    data: dict[str, pd.DataFrame | None],
) -> dict[str, pd.DataFrame | None]:
    """Return a copy of *data* filtered to the single calendar date with the
    most rows in the callback sheet.  If the callback sheet already covers
    only one date (the common case) the original dict is returned unchanged.

    Each sheet is filtered using whichever column whose name contains '时间戳'
    (or falls back to any column containing '时间') is found first.
    """
    cb = data.get("callback")
    if cb is None:
        return data

    ts_col = next(
        (c for c in cb.columns if "时间戳" in str(c)),
        next((c for c in cb.columns if "时间" in str(c)), None),
    )
    if ts_col is None:
        return data

    cb_dates = pd.to_datetime(cb[ts_col], errors="coerce").dt.date.dropna()
    if cb_dates.nunique() <= 1:
        return data

    peak_date = cb_dates.value_counts().idxmax()

    result: dict[str, pd.DataFrame | None] = {}
    for key, df in data.items():
        if df is None:
            result[key] = None
            continue
        # Find the best timestamp column for this sheet
        col_k = next(
            (c for c in df.columns if "时间戳" in str(c)),
            next((c for c in df.columns if "时间" in str(c)), None),
        )
        if col_k is not None:
            mask = pd.to_datetime(df[col_k], errors="coerce").dt.date == peak_date
            result[key] = df[mask].reset_index(drop=True)
        else:
            result[key] = df
    return result


def detect_data_date(data: dict[str, pd.DataFrame | None]) -> str | None:
    """Return 'YYYY-MM-DD' of the earliest timestamp found in any loaded sheet."""
    for key in ("callback", "station", "lifecycle"):
        df = data.get(key)
        if df is None:
            continue
        for col in df.columns:
            if "时间" in str(col):
                try:
                    ts = pd.to_datetime(df[col], errors="coerce").dropna()
                    if len(ts):
                        return ts.min().strftime("%Y-%m-%d")
                except Exception:
                    pass
    return None


# ── Station auto-detection ────────────────────────────────────────────────────

def _parse_labor_points(lsr: pd.DataFrame) -> list[dict]:
    rows: list[dict] = []
    for p in lsr["位置编号"].dropna().astype(str).unique():
        if "LABOR" not in p or "POINT" not in p:
            continue
        parts = p.split(":")
        if len(parts) < 4:
            continue
        try:
            rows.append({"point": p, "x": int(parts[2]), "y": int(parts[3])})
        except ValueError:
            pass
    return rows


def _auto_station_config(
    lsr: pd.DataFrame,
) -> tuple[dict, list, dict, dict]:
    rows = _parse_labor_points(lsr)
    if not rows:
        return {}, [], {}, {}

    df = (
        pd.DataFrame(rows)
        .sort_values(["y", "x"])
        .reset_index(drop=True)
    )
    y_vals      = sorted(df["y"].unique())
    zone_letter = {y: chr(ord("A") + i) for i, y in enumerate(y_vals)}

    point2ws: dict[str, str] = {}
    ws_order:  list[str]     = []
    type_map:  dict[str, str] = {}

    for idx, row in enumerate(df.itertuples(), start=1):
        name = f"LABOR-{idx}"
        point2ws[row.point] = name
        ws_order.append(name)
        type_map[name] = f"Zone {zone_letter[row.y]}"

    unique_types = list(dict.fromkeys(type_map.values()))
    type_colors  = {
        t: AUTO_TYPE_PALETTE[i % len(AUTO_TYPE_PALETTE)]
        for i, t in enumerate(unique_types)
    }
    return point2ws, ws_order, type_map, type_colors


# ── Lifecycle stage auto-detection ───────────────────────────────────────────

def _format_stage_label(col: str) -> str:
    """Return an English label for a stage duration column.

    Strips the '耗时(秒)' suffix to obtain the prefix (e.g. 'A42取箱'),
    then looks it up in STAGE_LABEL_MAP.  Falls back to splitting at the
    ASCII/Chinese boundary (e.g. 'A42 - 取箱') if no mapping is found.
    """
    prefix = str(col).replace("耗时(秒)", "").strip()
    if prefix in STAGE_LABEL_MAP:
        return STAGE_LABEL_MAP[prefix]
    m = re.match(r'^([A-Za-z0-9]+)(.+)$', prefix)
    if m:
        return f"{m.group(1)} - {m.group(2)}"
    return prefix


def _auto_stage_config(
    tlc: pd.DataFrame,
) -> tuple[list[str], list[str], list[str]]:
    candidates = [
        c for c in tlc.columns
        if "耗时" in str(c)
        and str(c) != TOTAL_DURATION_COL
        and tlc[c].notna().any()
    ]
    stages: list[str] = []
    labels: list[str] = []
    colors: list[str] = []
    for i, col in enumerate(candidates):
        stages.append(col)
        labels.append(_format_stage_label(col))
        colors.append(STAGE_COLORS[i % len(STAGE_COLORS)])
    return stages, labels, colors


# ── AMR delivery type auto-detection ─────────────────────────────────────────

def _detect_amr_type(lsr: pd.DataFrame) -> str | None:
    col = next((c for c in lsr.columns if "机器人类型" in str(c)), None)
    if col is None:
        return None
    types = lsr[col].dropna().unique()
    hint  = next((t for t in types if AMR_DELIVERY_TYPE_HINT in str(t)), None)
    return str(hint) if hint is not None else (str(types[0]) if len(types) else None)


# ── Master config builder ─────────────────────────────────────────────────────

def build_config(
    data: dict[str, pd.DataFrame | None],
    user_cfg: dict,
) -> dict:
    """
    Build the runtime config dict from auto-detected data + optional user overrides.

    user_cfg keys (all optional):
      station_types  : {station_name: type_label}
      design_rates   : {station_name_or_type_label: int}
      type_colors    : {type_label: hex_color}
      amr_type       : str  (override AMR delivery robot type name)
    """
    lsr = data.get("station")
    tlc = data.get("lifecycle")

    # Stations
    if lsr is not None:
        point2ws, ws_order, type_map, type_colors = _auto_station_config(lsr)
    else:
        point2ws, ws_order, type_map, type_colors = {}, [], {}, {}

    # Apply user station-type overrides
    for ws, t in user_cfg.get("station_types", {}).items():
        if ws in type_map:
            type_map[ws] = t

    # Rebuild type_colors after type overrides
    unique_types = list(dict.fromkeys(type_map.values()))
    for i, t in enumerate(unique_types):
        if t not in type_colors:
            type_colors[t] = AUTO_TYPE_PALETTE[i % len(AUTO_TYPE_PALETTE)]
    for t, c in user_cfg.get("type_colors", {}).items():
        type_colors[t] = c

    # Design rates (station-level first, then type-level fallback)
    user_rates  = user_cfg.get("design_rates", {})
    design_rate: dict[str, int] = {}
    if user_rates:
        for ws in ws_order:
            if ws in user_rates:
                design_rate[ws] = int(user_rates[ws])
            elif type_map.get(ws) in user_rates:
                design_rate[ws] = int(user_rates[type_map[ws]])
    design_total = sum(design_rate.values()) if design_rate else None

    # Lifecycle stages
    if tlc is not None:
        stages, stage_lbl, stage_col = _auto_stage_config(tlc)
    else:
        stages, stage_lbl, stage_col = [], [], []

    # AMR delivery robot type
    amr_type = user_cfg.get("amr_type") or (
        _detect_amr_type(lsr) if lsr is not None else None
    )

    # Robot switch/wait time model (drives implied-throughput formulas).
    #   switch_s_fixed  : user-set flat handoff time (default SWITCH_S_FALLBACK).
    #   switch_mode     : "fixed" uses that flat value everywhere; "measured"
    #                     uses the per-station measured operational-switch median
    #                     (switch_measured), falling back to the fixed value.
    try:
        switch_s_fixed = float(user_cfg.get("switch_s_fixed", SWITCH_S_FALLBACK))
        if switch_s_fixed < 0:
            switch_s_fixed = SWITCH_S_FALLBACK
    except (TypeError, ValueError):
        switch_s_fixed = SWITCH_S_FALLBACK
    switch_mode = user_cfg.get("switch_mode", "fixed")
    if switch_mode not in ("fixed", "measured"):
        switch_mode = "fixed"

    # Pick-time start event: 'arrived' (standard) or 'ppReady' (fallback when the
    # export lacks 'arrived' events).  Chosen in the GUI or asrs_config.json.
    pick_start_event = user_cfg.get("pick_start_event", PICK_START_EVENT_DEFAULT)
    if pick_start_event not in PICK_START_EVENTS:
        pick_start_event = PICK_START_EVENT_DEFAULT
    switch_measured: dict[str, float] = {}
    if lsr is not None and point2ws:
        # Imported lazily to avoid coupling the loader to the analyses package
        # at import time.
        from analyses.switch_time import operational_switch_by_station
        switch_measured = operational_switch_by_station(lsr, point2ws)

    return {
        "point2ws":          point2ws,
        "ws_order":          ws_order,
        "type_map":          type_map,
        "type_colors":       type_colors,
        "design_rate":       design_rate,
        "design_total_rate": design_total,
        "stages":            stages,
        "stage_lbl":         stage_lbl,
        "stage_col":         stage_col,
        "amr_type":          amr_type,
        "switch_s_fixed":    switch_s_fixed,
        "switch_mode":       switch_mode,
        "switch_measured":   switch_measured,
        "pick_start_event":  pick_start_event,
    }
