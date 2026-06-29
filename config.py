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

# ── AMR auto-detection hint: robot type name containing this string is treated
#    as the delivery AMR (K50 equivalent).  Override via asrs_config.json. ───
AMR_DELIVERY_TYPE_HINT = "50"

# ── Analysis registry — order controls sidebar display and tab order ─────────
ANALYSIS_MODULES: list[tuple[str, str]] = [
    ("throughput", "Throughput per station / hour"),
    ("dwell",      "Dwell / pick time"),
    ("switch",     "Switch time"),
    # ("cycle",      "Cycle time  (lifecycle sheet)"),  # temporarily disabled
    ("retrieval",  "Retrieval demand  (lifecycle sheet)"),
    ("fleet",      "Fleet utilisation & queue depth  (lifecycle + station)"),
]

# Analyses checked by default in the GUI
DEFAULT_CHECKED: set[str] = {"throughput", "dwell", "switch"}
