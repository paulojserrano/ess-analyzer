# Data Schema Reference (generated 2026-06-26)
# Expected Excel sheets, column names, and runtime config keys

## Excel Sheets (auto-detected by signature columns)
  callback     Robot event log (complete, arrive events at stations)
               • 動作類型
               • 位置類型
               • 時間戳
  station      Labor station robot events (arrived, triggerGo, release)
               • 事件類型
               • 機器人編號
               • 位置編號
  lifecycle    Container task lifecycle with duration per stage
               • 任務全程耗時
               • 目標位置
               • 起始位置

## Key Column Names (Chinese field names in source data)
  動作類型                         Action type (complete, etc.) — callback sheet
  位置類型                         Location type (LABOR, storage) — callback sheet
  時間戳                          Timestamp — all sheets
  機器人編號                        Robot ID — station sheet
  機器人類型                        Robot model/type (e.g. K50) — station sheet
  位置編號                         Location code (LABOR:0:X:Y) — station sheet
  事件類型                         Event type (arrived, triggerGo, release, ppReady) — station sheet
  任務全程耗時(秒)                    Total task duration in seconds — lifecycle sheet
  complete(任務完成時間)             Task completion timestamp — lifecycle sheet
  起始位置                         Source location (HAI-aisle-bay-level-...) — lifecycle sheet
  目標位置                         Destination location (LABOR-N) — lifecycle sheet
  容器編號                         Container / tote ID — lifecycle sheet
  *耗時(秒)                       Stage duration columns (suffix pattern) — lifecycle sheet

## Stage Label Map (Chinese → English, from config.py)
  # ── Chinese column-prefix → English stage label ──────────────────────────────

## Runtime cfg Dict Keys (built by data_loader.build_config)
  key                  type               description
  -------------------- ------------------ ------------------------------
  ws_order             list[str]          Ordered station names
  type_map             dict[str, str]     Station → zone type
  type_colors          dict[str, str]     Zone type → hex colour
  design_rate          dict[str, int]     Station → target tasks/hr
  design_total_rate    int | None         Sum of all design rates
  point2ws             dict[str, str]     Location code → station name
  stages               list[str]          Lifecycle stage column names
  stage_lbl            list[str]          Human-readable stage labels
  stage_col            list[str]          Hex colour per stage
  amr_type             str | None         Delivery AMR type string, e.g. "K50"

## asrs_config.json (optional, placed next to .xlsx)
  Keys: station_types, design_rates, type_colors, amr_type
  Example:
    { "station_types": {"LABOR-1": "Zone A"},
      "design_rates":  {"LABOR-1": 120},
      "type_colors":   {"Zone A": "#ff6b6b"},
      "amr_type":      "K50" }

## Location Code Formats
  LABOR station:   LABOR:0:<x>:<y>   (grouped by Y coord into zones A,B,C,...)
  Storage grid:    HAI-<aisle>-<bay>-<level>-<col>
  Destination:     LABOR-<N>          (matches station record label)
