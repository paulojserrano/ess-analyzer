"""
data_loader.py — Excel ingestion, sheet-signature detection, and runtime config
               builder.  All data-shape knowledge lives here.
"""
from __future__ import annotations

import json
import logging
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

_log = logging.getLogger(__name__)

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


# ── Per-sheet timestamp columns ──────────────────────────────────────────────
#
# Each sheet carries its event time in a different column.  The lifecycle
# sheet in particular has several '…时间' columns, and the first of them
# ('创建时间') is often completely blank — so "first column containing 时间"
# is NOT a safe choice.  Columns are tried in preference order and coalesced
# row by row (see row_timestamps).
_TS_PREFERENCE: dict[str, list[str]] = {
    "callback":   ["时间戳"],
    "station":    ["时间戳"],
    "lifecycle":  ["complete(任务完成时间)", "放箱时间", "取箱时间", "分配时间", "创建时间"],
    "efficiency": ["自然小时"],
}


def _ts_candidates(df: pd.DataFrame, key: str) -> list[str]:
    cols = [str(c) for c in df.columns]
    ordered: list[str] = []
    for needle in _TS_PREFERENCE.get(key, []):
        ordered += [c for c in cols if needle in c and c not in ordered]
    # Any other time-like column comes last, as a fallback.
    ordered += [c for c in cols if ("时间" in c or "小时" in c) and c not in ordered]
    return ordered


def row_timestamps(df: pd.DataFrame, key: str) -> pd.Series | None:
    """One timestamp per row: the first parseable value across the sheet's
    preferred time columns.  Returns None when the sheet has no time column."""
    cands = _ts_candidates(df, key)
    if not cands:
        return None
    out = pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns]")
    for c in cands:
        missing = out.isna()
        if not missing.any():
            break
        out[missing] = pd.to_datetime(df.loc[missing, c], errors="coerce")
    return out


def best_ts_column(df: pd.DataFrame, key: str) -> str | None:
    """The preferred time column that actually contains parseable values."""
    for c in _ts_candidates(df, key):
        if pd.to_datetime(df[c], errors="coerce").notna().any():
            return c
    return None


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

    # Timestamp sanity: every row should be datable from the sheet's preferred
    # time columns (a blank optional column such as 创建时间 is fine).
    if len(df) and _ts_candidates(df, key):
        parsed = row_timestamps(df, key)
        valid_count = int(parsed.notna().sum())
        if valid_count == 0:
            vr.add_error(
                f"'{key}' sheet has no parseable timestamps "
                f"(checked: {', '.join(_ts_candidates(df, key)[:4])})."
            )
        elif valid_count < len(df) * 0.5:
            bad_pct = round((1 - valid_count / len(df)) * 100)
            vr.add_warning(
                f"'{key}' sheet: {bad_pct}% of rows have no parseable timestamp."
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

        except Exception as exc:
            _log.warning("Sheet '%s' could not be read: %s", name, exc)
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


def _open_excel(path: str) -> pd.ExcelFile:
    """Open a workbook with the fast calamine reader when it is installed,
    falling back to openpyxl (always available) otherwise."""
    errors: list[str] = []
    for engine in ("calamine", "openpyxl"):
        try:
            return pd.ExcelFile(path, engine=engine)
        except ImportError as exc:
            errors.append(f"{engine}: not installed ({exc})")
        except Exception as exc:
            errors.append(f"{engine}: {exc}")
            if engine == "calamine" and "not supported" not in str(exc).lower():
                # The file itself is unreadable; openpyxl will fail the same way.
                continue
    raise ValueError(
        f"Cannot open '{os.path.basename(path)}' as an Excel file — "
        + "; ".join(errors)
    )


def load_data(path: str) -> dict[str, pd.DataFrame | None]:
    """Load an xlsx or .log file from a filesystem path.

    Raises ValueError for unreadable files or missing required sheets/events.
    For richer validation (warnings, data quality) call validate_file_path()
    and validate_data() separately.
    """
    if os.path.splitext(path)[1].lower() == ".log":
        from log_converter import convert_log_to_data
        return convert_log_to_data([path])

    xl = _open_excel(path)

    # Close the file handle when done — otherwise the xlsx stays locked on
    # Windows until garbage collection.
    with xl:
        if not xl.sheet_names:
            raise ValueError(
                f"'{os.path.basename(path)}' contains no sheets."
            )
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
    most rows in the callback sheet (station sheet when callback is absent).
    If that sheet covers only one date (the common case) the original dict is
    returned unchanged.

    Each sheet is dated row by row from its own preferred time columns
    (row_timestamps) — e.g. lifecycle rows use their completion time, never the
    often-blank 创建时间.  Rows that cannot be dated at all are kept rather than
    silently dropped.
    """
    ref_key = "callback" if data.get("callback") is not None else "station"
    ref = data.get(ref_key)
    if ref is None:
        return data
    ref_ts = row_timestamps(ref, ref_key)
    if ref_ts is None:
        return data
    ref_dates = ref_ts.dt.date.dropna()
    if ref_dates.nunique() <= 1:
        return data

    peak_date = ref_dates.value_counts().idxmax()

    result: dict[str, pd.DataFrame | None] = {}
    for key, df in data.items():
        if df is None:
            result[key] = None
            continue
        ts = row_timestamps(df, key)
        if ts is None:
            result[key] = df
            continue
        mask = (ts.dt.date == peak_date) | ts.isna()
        result[key] = df[mask].reset_index(drop=True)
    return result


def detect_data_date(data: dict[str, pd.DataFrame | None]) -> str | None:
    """Return 'YYYY-MM-DD' of the date most rows fall on (callback → station →
    lifecycle), so a few pre-midnight events don't mislabel the day."""
    for key in ("callback", "station", "lifecycle"):
        df = data.get(key)
        if df is None or df.empty:
            continue
        ts = row_timestamps(df, key)
        if ts is None:
            continue
        dates = ts.dropna().dt.date
        if len(dates):
            return dates.value_counts().idxmax().strftime("%Y-%m-%d")
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


def _natural_key(name: str) -> tuple:
    """Sort 'LABOR-2' before 'LABOR-10'."""
    return tuple(int(t) if t.isdigit() else t for t in re.split(r"(\d+)", str(name)))


def _callback_station_names(cb: pd.DataFrame | None, points: set[str]) -> dict[str, str]:
    """Real station names for station-sheet point codes, read from the callback
    sheet, where each LABOR row carries both the point code (位置编号) and the
    system's own station label (位置类型, e.g. 'LABOR-9').

    Only a strict one-to-one mapping is returned: if any point maps to several
    labels or any label to several points, {} is returned and the caller falls
    back to coordinate-based naming (which keeps one robot per station — an
    assumption the occupancy maths relies on).
    """
    if cb is None or not {"位置编号", "位置类型"} <= set(cb.columns):
        return {}
    sub = cb[["位置编号", "位置类型"]].dropna().astype(str)
    sub = sub[sub["位置编号"].isin(points) & sub["位置类型"].str.startswith("LABOR")]
    if sub.empty:
        return {}
    # Majority label per point tolerates the odd mis-logged row.
    mapping = (
        sub.groupby("位置编号")["位置类型"]
        .agg(lambda v: v.value_counts().idxmax())
        .to_dict()
    )
    if len(set(mapping.values())) != len(mapping):
        _log.warning(
            "Callback sheet maps several station points to the same LABOR label; "
            "using coordinate-based station names instead."
        )
        return {}
    return mapping


def _auto_station_config(
    lsr: pd.DataFrame,
    cb: pd.DataFrame | None = None,
) -> tuple[dict, list, dict, dict]:
    """Detect LABOR stations from the station sheet's point codes.

    Station names come from the callback sheet when it provides an unambiguous
    point → 'LABOR-N' mapping, so every sheet (station, callback, lifecycle
    目标位置) refers to the same physical station by the same name.  Otherwise
    stations are numbered LABOR-1… in (y, x) coordinate order.  Zones are
    always derived from the y coordinate (one zone per row of stations).
    """
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

    real = _callback_station_names(cb, set(df["point"]))
    use_real = bool(real) and len(real) == len(df)
    if real and not use_real:
        # Partial coverage: only use real names if they cannot collide with the
        # generated LABOR-<n> names for the remaining points.
        _log.info("Callback sheet names %d of %d station points; "
                  "using coordinate-based names.", len(real), len(df))

    point2ws: dict[str, str] = {}
    type_map: dict[str, str] = {}
    for idx, row in enumerate(df.itertuples(), start=1):
        name = real[row.point] if use_real else f"LABOR-{idx}"
        point2ws[row.point] = name
        type_map[name] = f"Zone {zone_letter[row.y]}"

    ws_order = sorted(point2ws.values(), key=_natural_key) if use_real else list(point2ws.values())

    unique_types = list(dict.fromkeys(type_map[w] for w in ws_order))
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
        point2ws, ws_order, type_map, type_colors = _auto_station_config(
            lsr, data.get("callback"))
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
