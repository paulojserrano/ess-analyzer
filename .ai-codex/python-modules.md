# Python Modules Map (generated 2026-10-07)
# Root-level modules: classes, methods, top-level functions

## app.py  (136 lines)
   app.py — ESS station & robot cycle analyser.

   Top-level functions:
     def _parse_args                  (argv: list[str])
     def _settings                    (args: argparse.Namespace, groups: list[list[str]])
     def run_headless                 (args: argparse.Namespace)
     def main                         (argv: list[str] | None = None)

## config.py  (159 lines)
   config.py — run settings and the few constants the analysis depends on.

   class Settings
     def validate                     (self)
     def to_dict                      (self)

   Top-level functions:
     def load_settings                (folder: str, base: Settings | None = None)

## log_parser.py  (457 lines)
   log_parser.py — read Hairobotics "play_extract" application logs into native frames.

   class LogError(ValueError)

   class LogData
     def source                       (self)
     def stations                     (self)
     def date                         (self)
     def robots                       (self, role: str)

   Top-level functions:
     def natural_key                  (name)
     def is_log_path                  (path: str)
     def log_stem                     (path: str)
     def log_date                     (path: str)
     def open_log                     (path: str)
     def looks_like_log               (path: str)
     def group_by_day                 (paths: list[str])
     def _ts                          (values)
     def _scan                        (paths: list[str])
     def _roles                       (callbacks: list[dict])
     def parse_logs                   (paths: str | list[str])
     def point_xy                     (point: str)
     def station_points               (arrivals: pd.DataFrame)
     def scan_stations                (path: str, settle: int = 2000, limit: int = 200_000)

## metrics.py  (1399 lines)
   metrics.py — everything about one day that does not depend on the report's

   Top-level functions:
     def _r                           (v, nd=1)
     def _q                           (series: pd.Series, p: float)
     def _hist                        (values: pd.Series, width: float, bins: int)
     def _ds                          (values)
     def _robot_num                   (name)
     def _id_range                    (ids)
     def _pair_visits                 (data: LogData)
     def visits                       (data: LogData, day: pd.Timestamp)
     def _switch_core                 (v: pd.DataFrame)
     def _station_block               (v: pd.DataFrame, day: pd.Timestamp, stations: list[str])
     def _arrivals_by_robot           (data: LogData)
     def k50_cycles                   (data: LogData)
     def _cycle_metrics               (data: LogData, day: pd.Timestamp, cycles: pd.DataFrame)
     def _multi_station               (v: pd.DataFrame, ho: pd.DataFrame, full: list[int])
     def _travel_hist                 (seconds)
     def _bands                       (values: np.ndarray, bands, rows: pd.DataFrame)
     def _busy_intervals              (data: LogData, moves: pd.DataFrame)
     def _sweep                       (start_s: np.ndarray, end_s: np.ndarray)
     def _day_seconds                 (ts: pd.Series, day: pd.Timestamp)
     def _robot_states                (iv: pd.DataFrame, day: pd.Timestamp, full: list[int])
     def _utilization                 (data: LogData, day: pd.Timestamp, full: list[int])
     def _task_table                  (data: LogData)
     def _task_flow                   (data: LogData, day: pd.Timestamp, full: list[int], statio...)
     def _alloc_trips                 (data: LogData)
     def _ready_totes                 (tt: pd.DataFrame | None)
     def _open_at                     (start: np.ndarray, end: np.ndarray, t: np.ndarray)
     def _rack                        (loc: pd.Series)
     def _with_alloc                  (moves: pd.DataFrame, data: LogData, robots: set[str])
     def _crowding                    (trips: pd.DataFrame, day: pd.Timestamp)
     def _spatial                     (data: LogData, day: pd.Timestamp, cycles: pd.DataFrame | ...)
     def _full_hours                  (hourly: list[dict] | None)
     def _idle_window                 (arrivals: pd.Series, day: pd.Timestamp)
     def day_base                     (data: LogData, starved_s: float = STARVED_S_DEFAULT)
     def zones_for                    (bases: list[dict])

## report.py  (144 lines)
   report.py — write the single-file report.

   Top-level functions:
     def _template                    (name: str)
     def _payload_json                (obj)
     def _zone_list                   (zones: dict[str, str])
     def default_targets              (bases: list[dict], zones: dict[str, str], s: Settings)
     def build_payload                (bases: list[dict], s: Settings)
     def _title                       (payload: dict)
     def write_report                 (bases: list[dict], s: Settings, path: str)

## pipeline.py  (228 lines)
   pipeline.py — parse → measure → report, shared by the CLI and the web UI.

   class RunCancelled(Exception)

   class DayResult
     def ok                           (self)

   class RunResult
     def completed                    (self)

   class _RunLogHandler(logging.Handler)
     def __init__                     (self, on_log: LogFn, thread_id: int)
     def emit                         (self, record: logging.LogRecord)
     def check_cancel                 ()
     def warn                         (msg: str)

   Top-level functions:
     def find_logs                    (paths: list[str])
     def default_output_root          ()
     def _safe_name                   (text: str, fallback: str)

## server.py  (506 lines)
   server.py — local web server behind the desktop UI (webui/).

   class Job

   class Session
     def __init__                     (self, output_root: str | None = None)
     def add                          (self, paths: list[str])
     def _scan                        (self, paths: list[str])
     def stations_json                (self)
     def remove                       (self, names: list[str])
     def clear                        (self)
     def groups                       (self)
     def days_json                    (self)
     def start                        (self, settings: Settings)
     def _run                         (self, job: Job, groups: list[list[str]], settings: Settings)
     def on_progress                  (pct: float, step: str)
     def on_log                       (level: str, msg: str)
     def state                        (self, log_from: int = 0)
     def index                        ()
     def meta                         ()
     def state                        (log_from: int = 0)
     def upload                       (files: list[UploadFile] = File(...)
     def add_paths                    (payload: dict = Body(...)
     def remove_day                   (payload: dict = Body(...)
     def clear_days                   ()
     def native_dialog                ()
     def start_run                    (payload: dict = Body(...)
     def cancel_run                   ()
     def results                      ()
     def runs                         ()
     def open_path                    (payload: dict = Body(...)

   Top-level functions:
     def _resource_dir                ()
     def _rel_url                     (result: RunResult, path: str | None, root: str)
     def _open_in_os                  (path: str)
     def create_app                   (output_root: str | None = None, native: bool = False)
     def _headline                    (day)
     def _free_port                   ()
     def launch                       (port: int = 0, output_root: str | None = None, mode: str ...)
