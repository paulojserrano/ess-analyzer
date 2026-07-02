# Python Modules Map (generated 2026-07-01)
# Root-level modules: classes, methods, top-level functions

## app.py  (1867 lines)
   app.py — Tkinter GUI for the ESS / ASRS Log Analyser.

   class AnalyzerApp
     def __init__                     (self)
     def _setup_styles                (self)
     def _build                       (self)
     def _make_card                   (self, parent, row, col, title, colspan=1, right_fn=None)
     def _build_files_card            (self, parent, row, col)
     def _fc_right                    (sh)
     def _build_stations_card         (self, parent, row, col)
     def _stn_right                   (sh)
     def _build_analyses_card         (self, parent, row, col)
     def _an_right                    (sh)
     def _build_run_card              (self, parent, row, col)
     def _build_log_card              (self, parent, row)
     def _log_right                   (sh)
     def _log_write                   (self, text: str, tag: str | None = None)
     def _clear_log                   (self)
     def _set_status                  (self, text: str)
     def _maybe_enable_run            (self)
     def _update_file_count           (self)
     def _on_ft_select                (self, _=None)
     def _set_all_checks              (self, value: bool)
     def _browse                      (self)
     def _add_files                   (self, paths: list[str])
     def _task                        ()
     def _remove_file                 (self)
     def _file_label_edit             (self, event)
     def _commit                      (e=None)
     def _tree_clear                  (self)
     def _populate_tree               (self, cfg: dict)
     def _tree_edit                   (self, event)
     def _commit                      (e=None)
     def _poll                        (self)
     def _cfg_from_tree               (self)
     def _run                         (self)
     def __init__                     (self, q)
     def write                        (self, text)
     def flush                        (self)
     def _save_excel                  (data: dict, cfg: dict, outdir: str)
     def _fmt                         (p: pd.DataFrame)
     def _save_combined_excel         (completed_days: list[dict], base_outdir: str)
     def _style_header                (ws)
     def _note_cell                   (ws, row, col, text)
     def _method_cell                 (ws, row, col, text)
     def _sn                          (base: str, prefix: str)
     def _on_done                     (self, html_path: str)
     def _open_report                 (self)
     def _open_folder                 (self)
     def _open_combined               (self)
     def run                          (self)

   Top-level functions:
     def _sort_registry               (registry: list[dict])
     def pick_file                    ()
     def _run_headless                (path: str)
     def main                         ()

## config.py  (47 lines)
   config.py — Single source of truth for all constants and palette definitions.

## data_loader.py  (591 lines)
   data_loader.py — Excel ingestion, sheet-signature detection, and runtime config

   class ValidationResult
     def ok                           (self)
     def add_error                    (self, msg: str)
     def add_warning                  (self, msg: str)

   Top-level functions:
     def validate_file_path           (path: str)
     def validate_data                (data: dict[str, pd.DataFrame | None])
     def validate_user_config         (cfg: dict)
     def df_to_store                  (df: pd.DataFrame)
     def df_from_store                (json_str: str)
     def _read_sheets                 (xl: pd.ExcelFile)
     def load_log_day                 (paths: list[str])
     def load_data                    (path: str)
     def load_data_from_bytes         (content_bytes: bytes)
     def load_user_config             (xlsx_path: str)
     def detect_data_date             (data: dict[str, pd.DataFrame | None])
     def _parse_labor_points          (lsr: pd.DataFrame)
     def _format_stage_label          (col: str)
     def _detect_amr_type             (lsr: pd.DataFrame)

## report_builder.py  (1100 lines)
   report_builder.py — Generate a self-contained HTML report with interactive

   Top-level functions:
     def _json_safe                   (obj)
     def _chart_json_payload          (entry: dict)
     def _raw_data_payload            (entry: dict)
     def _export_bar                  (entry: dict, outdir: str, json_id: str)
     def _methodology_html            ()
