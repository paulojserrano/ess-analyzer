# Analyses Index (generated 2026-07-01)
# Each module exports run(data, cfg) -> list[ChartResult]
# ChartResult keys: id, title, figure, source, method, export_hint

## backlog.py  (225 lines)
   analyses/backlog.py — allocation wait and task backlog from the lifecycle sheet.

   Charts:
     backlog_alloc_wait                  Allocation Wait — How Long Tasks Queue for a Robot
     backlog_queue_depth                 Task Backlog — Created but Not Yet Assigned

   Functions:
     def _find_col                  (df: pd.DataFrame, needle: str)
     def run                        (data: dict, cfg: dict)

## cycle_time.py  (511 lines)
   analyses/cycle_time.py — full container journey from task-create to complete.

   Charts:
     cycle_histogram                     Distribution of Container Cycle Times
     cycle_by_hour                       Median Cycle Time by Hour of Day
     demand_vs_cycle                     Throughput Demand vs Cycle Time
     cycle_stage_donut                   Mean Cycle Time Stage Composition
     cycle_stage_by_station              Cycle Time Stage Composition by Workstation

   Functions:
     def _base_layout               (fig: go.Figure, title: str)
     def _histogram                 (tj: pd.DataFrame, n_over_2h: int = 0)
     def _by_hour                   (tj: pd.DataFrame)
     def _stage_donut               (tlc: pd.DataFrame, cfg: dict)
     def _stage_by_station          (tlc: pd.DataFrame, cfg: dict)
     def run                        (data: dict, cfg: dict)

## data_quality.py  (366 lines)
   analyses/data_quality.py — data-quality panel and pick-time cross-validation.

   Charts:
     dq_panel                            Data Quality Panel
     dq_pick_crossval                    Pick Time Cross-Validation

   Functions:
     def _find_col                  (df: pd.DataFrame, needle: str)
     def _pct                       (part: int, whole: int)
     def _pairing_stats             (lsr: pd.DataFrame, cfg: dict)
     def _dq_table                  (data: dict, cfg: dict)
     def _pick_crossval             (data: dict, cfg: dict)
     def run                        (data: dict, cfg: dict)

## dwell_time.py  (537 lines)
   analyses/dwell_time.py — operator pick time at workstations.

   Charts:
     dwell_heatmap                       Operator Pick Time at Workstations
     dwell_pick_distribution             Pick Time Distribution by Workstation

   Functions:
     def extract_picks              (lsr: pd.DataFrame, cfg: dict)
     def _clipped_occupancy         (events, start_col, end_col, ws_order, all_hours)
     def _pick_time_distribution    (d: pd.DataFrame, cfg: dict)
     def run                        (data: dict, cfg: dict)

## efficiency.py  (270 lines)
   analyses/efficiency.py — HPS3 bottleneck attribution from the efficiency sheet.

   Charts:
     efficiency_target_vs_actual         System Flows vs Target — Declared Bottleneck per Hour
     efficiency_bottleneck_share         Bottleneck Share of the Day

   Functions:
     def _find_col                  (df: pd.DataFrame, needle: str)
     def _bottleneck_color_map      (labels: list[str])
     def run                        (data: dict, cfg: dict)

## fleet_utilization.py  (756 lines)
   analyses/fleet_utilization.py — station queue depth and global fleet utilisation.

   Charts:
     queue_depth_leg_map                 Robots assigned per station vs delivery time
     fleet_utilization                   Real-Time Fleet Utilisation Profile
     fleet_wip_littles_law               Work-in-Progress vs Little's Law

   Functions:
     def _base_layout               (fig: go.Figure, title: str)
     def _delivery_leg_col          (tlc: pd.DataFrame, cfg: dict)
     def _queue_depth_leg_map       (tlc: pd.DataFrame, cfg: dict)
     def _fleet_utilization_timeseries (lsr: pd.DataFrame, cfg: dict, tlc: pd.DataFrame | None = ...)
     def _littles_law               (tlc: pd.DataFrame, cfg: dict)
     def run                        (data: dict, cfg: dict)

## retrieval.py  (572 lines)
   analyses/retrieval.py — where containers are fetched from in the storage grid.

   Charts:
     retrieval_aisle_bar                 Retrieval Demand by Storage Aisle
     retrieval_bay_heatmap               Retrieval Demand Heatmap — Every Storage Bay
     retrieval_tote_pareto               Tote-Level Retrieval Concentration (Pareto)
     retrieval_level_profile             Retrieval Demand and Delivery Time by Storage Level
     retrieval_leg_by_aisle              Delivery Leg Duration by Source Aisle
     retrieval_tote_reinterval           Tote Re-Retrieval Intervals

   Functions:
     def _parse_source              (tlc: pd.DataFrame)
     def _aisle_bar                 (src: pd.DataFrame)
     def _bay_heatmap               (src: pd.DataFrame)
     def _tote_pareto               (tlc: pd.DataFrame, bin_col: str)
     def _level_profile             (src: pd.DataFrame, leg_col: str | None)
     def _leg_by_aisle              (src: pd.DataFrame, leg_col: str | None)
     def _tote_reretrieval          (tlc: pd.DataFrame, bin_col: str)
     def run                        (data: dict, cfg: dict)

## return_flow.py  (148 lines)
   analyses/return_flow.py — outbound vs return task mix from the callback sheet.

   Charts:
     return_flow_mix                     Outbound vs Return Task Mix by Hour

   Functions:
     def _find_col                  (df: pd.DataFrame, needle: str)
     def run                        (data: dict, cfg: dict)

## robot_performance.py  (208 lines)
   analyses/robot_performance.py — per-robot workload and speed profile.

   Charts:
     robot_performance_profile           Per-Robot Performance Profile

   Functions:
     def _find_col                  (df: pd.DataFrame, needle: str)
     def _robot_table               (data: dict, cfg: dict)
     def run                        (data: dict, cfg: dict)

## station_readiness.py  (198 lines)
   analyses/station_readiness.py — who waits for whom at the workstations.

   Charts:
     readiness_who_waits                 Who Waits for Whom? — ppReady vs Robot Arrival

   Functions:
     def run                        (data: dict, cfg: dict)

## summary.py  (1209 lines)
   analyses/summary.py — cross-day trend charts for the Summary tab.

   Charts:
     summary_delta_heatmap               Day-over-Day Change in Key Metrics
     summary_pick_time                   Average Operator Pick Time Trend
     summary_util_pct_trend              Average % of Implied Pick Rate per Station per Day
     summary_tail_severity               Cycle Time Tail Severity (p90 ÷ Median)
     summary_throughput_consistency      Within-Day Throughput Consistency
     summary_distribution_overlay        Cycle Time Distribution Shift Across Days
     summary_pick_r2_trend               How Much Does Pick Time Explain Throughput? (R² per Day)
     summary_trends                      Day-over-Day Performance Trends

   Functions:
     def _collect_stats             (all_days: list[dict])
     def _delta_heatmap             (stats: list[dict])
     def _pick_time_per_station     (stats: list[dict])
     def _avg_util_pct_trend        (stats: list[dict])
     def _tail_severity             (stats: list[dict])
     def _throughput_consistency    (all_days: list[dict])
     def _distribution_overlay      (all_days: list[dict])
     def _pick_r2_trend             (all_days: list[dict])
     def export_xlsx                (all_days: list[dict], outdir: str)
     def run                        (all_days: list[dict])

## switch_time.py  (392 lines)
   analyses/switch_time.py — robot handoff / switch time and station starvation.

   Charts:
     switch_starvation                   Station Starvation — Minutes Without a Robot
     switch_heatmap                      Robot Switch Time at Workstations

   Functions:
     def _release_arrived_gaps      (lsr: pd.DataFrame, point2ws: dict)
     def operational_switch_by_station (lsr: pd.DataFrame, point2ws: dict)
     def _prep_arrays               (pivot: pd.DataFrame, ws_order: list, fmt: str)
     def _starvation_heatmap        (starved: pd.DataFrame, cfg: dict, all_hours: pd.DatetimeI...)
     def run                        (data: dict, cfg: dict)

## throughput.py  (767 lines)
   analyses/throughput.py — completions per LABOR station per hour.

   Charts:
     throughput_total                    Total Throughput by Hour
     throughput_heatmap                  Effective Tasks per Station per Hour
     throughput_utilisation              Station Utilisation vs Design Rate (%)

   Functions:
     def _zone_colorscale           (type_map: dict, type_colors: dict, ws_order: list[str])
     def _heatmap_layout            (fig: go.Figure, hour_labels: list[str], title: str)
     def run                        (data: dict, cfg: dict)

## __init__.py — ChartResult schema
   Keys expected in every dict returned by run():
     id
     title
     figure
     source
     method
     export_hint
