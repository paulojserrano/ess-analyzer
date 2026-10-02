# Python Modules Map (generated 2026-10-02)
# Root-level modules: classes, methods, top-level functions

## app.py  (129 lines)
   app.py — ESS / ASRS Log Analyzer entry point.

   Top-level functions:
     def _parse_args                  (argv: list[str])
     def run_headless                 (args: argparse.Namespace)
     def main                         (argv: list[str] | None = None)

## config.py  (112 lines)
   config.py — Single source of truth for all constants and palette definitions.

## data_loader.py  (914 lines)
   data_loader.py — Excel ingestion, sheet-signature detection, and runtime config

   class ValidationResult
     def ok                           (self)
     def add_error                    (self, msg: str)
     def add_warning                  (self, msg: str)
     def _found                       ()

   Top-level functions:
     def validate_file_path           (path: str)
     def _ts_candidates               (df: pd.DataFrame, key: str)
     def row_timestamps               (df: pd.DataFrame, key: str)
     def validate_data                (data: dict[str, pd.DataFrame | None])
     def best_preflight_status        (statuses: list[str])
     def _analysis_status             (key: str, f: dict)
     def _preflight_facts             (data: dict[str, pd.DataFrame | None])
     def preflight_analyses           (data: dict[str, pd.DataFrame | None])
     def pick_source_status           (data: dict[str, pd.DataFrame | None])
     def validate_user_config         (cfg: dict)
     def _read_sheets                 (xl: pd.ExcelFile)
     def load_log_day                 (paths: list[str])
     def _open_excel                  (path: str)
     def load_data                    (path: str)
     def load_user_config             (xlsx_path: str)
     def detect_data_date             (data: dict[str, pd.DataFrame | None])
     def _parse_labor_points          (lsr: pd.DataFrame)
     def _natural_key                 (name: str)
     def _callback_station_names      (cb: pd.DataFrame | None, points: set[str])
     def _format_stage_label          (col: str)
     def _detect_amr_type             (lsr: pd.DataFrame)

## pipeline.py  (509 lines)
   pipeline.py — GUI-agnostic analysis pipeline.

   class LoadError(ValueError)
     def __init__                     (self, message: str, messages: list[tuple[str, str]] | Non...)

   class RunCancelled(Exception)

   class LoadedDay
     def display_name                 (self)

   class RunSettings

   class DayResult

   class RunResult

   class _RunLogHandler(logging.Handler)
     def __init__                     (self, on_log: LogFn, thread_id: int)
     def emit                         (self, record: logging.LogRecord)
     def check_cancel                 ()
     def warn                         (msg: str)

   Top-level functions:
     def default_output_root          ()
     def _safe_name                   (text: str, fallback: str)
     def group_input_paths            (paths: list[str])
     def resolve_module               (key: str)
     def sort_registry                (registry: list[dict])
     def _merged_user_cfg             (day: LoadedDay, s: RunSettings)
     def day_kpis                     (data: dict, cfg: dict)
     def _validate_charts             (key: str, charts: list[dict])
     def _dedupe_ids                  (registry: list[dict], warn: Callable[[str], None])

## exports.py  (592 lines)
   exports.py — Excel exports written alongside the HTML report.

   Top-level functions:
     def _day_hours                   (ts: pd.Series)
     def _operational_switches        (lsr: pd.DataFrame, cfg: dict)
     def _delivery_legs               (tlc: pd.DataFrame)
     def _cycle_rows                  (tlc: pd.DataFrame)
     def _fleet_hourly                (lsr: pd.DataFrame)
     def _fmt_hours                   (p: pd.DataFrame)
     def save_day_exports             (data: dict, cfg: dict, outdir: str)
     def _sheet_name                  (chart_id: str, used: set[str])
     def save_combined_workbook       (completed_days: list[dict], base_outdir: str)
     def _write_combined              (completed_days: list[dict], base_outdir: str)

## report_builder.py  (1149 lines)
   report_builder.py — Generate a self-contained HTML report with interactive

   Top-level functions:
     def _json_safe                   (obj)
     def _script_safe                 (json_text: str)
     def _chart_json_payload          (entry: dict)
     def _raw_data_payload            (entry: dict)
     def _export_bar                  (entry: dict, outdir: str, json_id: str, report_dir: str |...)
     def _kpi_strip                   (kpis: list[dict])
     def _failure_notice              (failures: list[dict])
     def _methodology_html            ()

## server.py  (652 lines)
   server.py — local web server behind the desktop UI (webui/).

   class DayEntry

   class Job

   class Session
     def __init__                     (self, output_root: str | None = None)
     def add_paths                    (self, paths: list[str])
     def _load                        (self, day_id: str)
     def start_run                    (self, settings: RunSettings)
     def _run                         (self, job: Job, days: list[LoadedDay], settings: RunSettings)
     def on_progress                  (pct: float, step: str)
     def on_log                       (level: str, msg: str)
     def stations                     (self)
     def state                        (self, log_from: int = 0)
     def index                        ()
     def plotly_js                    ()
     def meta                         ()
     def state                        (log_from: int = 0)
     def upload                       (files: list[UploadFile] = File(...)
     def add_paths                    (payload: dict = Body(...)
     def native_dialog                ()
     def rename_day                   (day_id: str, payload: dict = Body(...)
     def remove_day                   (day_id: str)
     def reorder                      (payload: dict = Body(...)
     def run                          (payload: dict = Body(...)
     def cancel                       ()
     def results                      ()
     def figure                       (day: str, idx: int)
     def rows                         (day: str, idx: int)
     def runs                         ()
     def open_path                    (payload: dict = Body(...)
     def watch                        ()

   Top-level functions:
     def _resource_dir                ()
     def _plotly_js_path              ()
     def _round                       (v, nd)
     def _day_json                    (e: DayEntry)
     def _chart_meta                  (entry: dict, idx: int)
     def _registry                    (result: RunResult, day: str)
     def _rel_url                     (result: RunResult, path: str | None, root: str)
     def _open_in_os                  (path: str)
     def create_app                   (output_root: str | None = None, native: bool = False)
     def _free_port                   ()
     def _start_idle_watchdog         (app: FastAPI, server, idle_s: float = 90.0)
     def launch                       (port: int = 0, output_root: str | None = None, mode: str ...)

## log_converter.py  (450 lines)
   log_converter.py — Convert Hairobotics .log files to the same dict structure

   Top-level functions:
     def extract_log_date             (path: str)
     def _parse_iso                   (ts_str: str)
     def _ms_to_dt                    (ms_val)
     def _robot_type                  (robot_code: str, location: str = '')
     def _clean_dest                  (dest: str)
     def _parse_entries               (content: str)
     def _build_callback_df           (entries: list)
     def _build_station_df            (entries: list)
     def _build_lifecycle_df          (entries: list)
