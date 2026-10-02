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

# ── Heatmap colour scales ────────────────────────────────────────────────────
# Ordered single-hue sequential scale: darker always means "more" (longer time,
# more retrievals, higher throughput).  Replaces the earlier rainbow scales,
# whose mid-range hues (green vs yellow) had no intuitive order.
SEQ_COLORSCALE: list[list] = [
    [0.0, "#f4f8fd"], [0.2, "#cfe0f3"], [0.45, "#7fb0de"],
    [0.7, "#3a7bc0"], [0.88, "#1d4f91"], [1.0, "#0c2a5b"],
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

# ── Pick-time start event ────────────────────────────────────────────────────
# Pick time is normally measured from each robot's 'arrived' event to its next
# 'triggerGo'.  Some exports are missing 'arrived' events entirely; when that
# happens the GUI (or asrs_config.json) can fall back to 'ppReady'→'triggerGo'.
# Resolved from cfg["pick_start_event"]; must be one of these two values.
PICK_START_EVENT_DEFAULT = "arrived"
PICK_START_EVENTS = ("arrived", "ppReady")

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

# Implementing module for each registry key — the pipeline imports these with
# importlib (see pipeline.py); nothing else imports analysis modules directly.
ANALYSIS_IMPLEMENTATIONS: dict[str, str] = {
    "throughput": "analyses.throughput",
    "dwell":      "analyses.dwell_time",
    "switch":     "analyses.switch_time",
    "readiness":  "analyses.station_readiness",
    "cycle":      "analyses.cycle_time",
    "backlog":    "analyses.backlog",
    "retrieval":  "analyses.retrieval",
    "fleet":      "analyses.fleet_utilization",
    "robot":      "analyses.robot_performance",
    "returns":    "analyses.return_flow",
    "efficiency": "analyses.efficiency",
    "quality":    "analyses.data_quality",
}

# Analyses that need the lifecycle sheet and are skipped without it.
LIFECYCLE_ONLY: set[str] = {"cycle", "retrieval", "backlog"}

# Preferred chart order inside a day's report.  Listed IDs come first in this
# order; all others follow in pipeline order.
CHART_DISPLAY_ORDER: list[str] = [
    "throughput_total",
    "throughput_heatmap",
    "dwell_heatmap",
    "switch_heatmap",
    "dwell_pick_distribution",
    "throughput_picker_rate",
]

# Analyses checked by default in the GUI
DEFAULT_CHECKED: set[str] = {"throughput", "dwell", "switch"}
