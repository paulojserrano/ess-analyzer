"""
config.py — Single source of truth for all constants and palette definitions.
"""

# ── Brand colours ────────────────────────────────────────────────────────────
INK    = "#1a1a2e"
ACCENT = "#e94560"

# ── Zone / station type palette (cycled when > 6 types are detected) ─────────
AUTO_TYPE_PALETTE: list[str] = [
    "#2563eb", "#16a34a", "#d97706", "#7c3aed", "#0891b2", "#dc2626",
]

# ── Lifecycle stage colours (one per stage, cycled if needed) ────────────────
STAGE_COLORS: list[str] = [
    "#f59e0b", "#16a34a", "#10b981", "#2563eb",
    "#7c3aed", "#ec4899", "#0891b2", "#f97316",
]

# ── Chinese column-prefix → English stage label ──────────────────────────────
STAGE_LABEL_MAP: dict[str, str] = {
    "分配":    "Allocation wait",
    "A42取箱": "A42 retrieve",
    "A42放箱": "A42 deposit",
    "K50完成": "K50 deliver + dwell",
    "拣选":    "Picking",
}

# ── Column names ─────────────────────────────────────────────────────────────
TOTAL_DURATION_COL = "任务全程耗时(秒)"

# ── Switch-time model (shared by dwell, throughput, switch, summary) ─────────
# Default robot handoff time used in implied-throughput formulas.  This is the
# starting value for the user-configurable "switch/wait time" (set in the GUI
# or via asrs_config.json "switch_s_fixed"); it is also the per-station fallback
# in "measured" mode when a station has no measurable release→arrived swaps.
# Resolved at runtime by analyses.switch_time.resolve_switch_s(cfg, station).
SWITCH_S_FALLBACK = 6.0
# A release→arrived gap longer than this is treated as starvation / idle time
# (breaks, no demand, dispatch gaps) rather than an operational robot swap.
MAX_OPERATIONAL_SWITCH_S = 300.0

# ── AMR auto-detection hint: robot type name containing this string is treated
#    as the delivery AMR (K50 equivalent).  Override via asrs_config.json. ───
AMR_DELIVERY_TYPE_HINT = "50"

# ── Analysis registry — order controls sidebar display and tab order ─────────
ANALYSIS_MODULES: list[tuple[str, str]] = [
    ("throughput", "Throughput per station / hour"),
    ("dwell",      "Dwell / pick time"),
    ("switch",     "Switch time & starvation"),
    ("readiness",  "Station readiness (ppReady)"),
    # ("cycle",      "Cycle time  (lifecycle sheet)"),  # temporarily disabled
    ("backlog",    "Allocation wait & backlog  (lifecycle sheet)"),
    ("retrieval",  "Retrieval demand  (lifecycle sheet)"),
    ("fleet",      "Fleet utilisation & queue depth  (lifecycle + station)"),
    ("robot",      "Per-robot performance"),
    ("returns",    "Outbound vs return flow"),
    ("efficiency", "HPS3 bottleneck attribution  (efficiency sheet)"),
    ("quality",    "Data quality & cross-validation"),
]

# Analyses checked by default in the GUI
DEFAULT_CHECKED: set[str] = {"throughput", "dwell", "switch"}
