# ESS Analyser — Analysis Methodology

This document describes how every analysis in the ESS / ASRS Log Analyser is computed: data sources, event definitions, formulas, thresholds, and interpretation guidance.

---

## Data Sources

The analyser auto-detects three sheets inside each Excel workbook by their column signatures.

| Internal key | Typical sheet name | Detected by |
|---|---|---|
| `callback` | 回调明细 | Columns `动作类型` + `位置类型` |
| `station` | labor_station_record | Column `事件类型` (without lifecycle columns) |
| `lifecycle` | 任务生命周期 | Column `任务全程耗时(秒)` or `K50完成耗时(秒)` |

### Key event definitions

**Labor station record — `事件类型` values**

| Event | Meaning |
|---|---|
| `arrived` | Robot docks at the LABOR station; operator begins picking |
| `triggerGo` | Operator finishes pick and releases the robot |
| `release` | Robot physically leaves the docking bay |
| `ppReady` | Station signals readiness for the next robot |

> **Important**: The callback sheet's `complete` event fires at robot *arrival* (same timestamp as `arrived` in the station record), **not** at pick completion. All pick-completion and throughput counts therefore use `triggerGo` from the station record.

---

## 1. Throughput per Station per Hour

**Source sheet**: `station` (labor_station_record)  
**Robot filter**: K50 robots only (`机器人类型 == amr_type`)

### 1.1 Throughput count

A task is counted as complete when a `triggerGo` event is logged for a K50 robot at a LABOR station.

```
T(station, hour) = count of triggerGo events
                   where floor(timestamp, 1 h) == hour
                   AND   机器人类型 == K50
```

The hour is bucketed by the `triggerGo` timestamp (moment of pick completion).

### 1.2 Implied throughput ceiling

The theoretical maximum throughput a station can sustain, given how long each pick actually takes:

```
ImpliedTPH(station, hour) = 3 600 / (AvgPickTime(station, hour) + 6)
```

- `AvgPickTime(station, hour)` — mean of all `arrived → triggerGo` durations (s) in that station-hour, bucketed by the `triggerGo` timestamp (see §2 for pick time detail)
- `6` — fixed robot handoff overhead (seconds) added to every cycle

Because both the throughput count and the pick-time average use the `triggerGo` timestamp as the hour boundary, the numerator and denominator are perfectly aligned.

### 1.3 Utilisation %

```
Utilisation(station, hour) = T(station, hour) / ImpliedTPH(station, hour) × 100 %
```

- **~100 %** → station is robot-supply-limited (robots are arriving as fast as picks allow)
- **< 100 %** → pick capacity exists but robots are not arriving fast enough (upstream supply gap)
- **> 100 %** → should not occur under the single-robot-per-station model; investigate data quality

### 1.4 Design-rate utilisation (if configured)

```
DesignUtil(station, hour) = T(station, hour) / DesignRate(station) × 100 %
```

where `DesignRate(station)` is set in `asrs_config.json`.

### Charts produced

| Chart ID | Description |
|---|---|
| `throughput_total` | Total triggerGo count across all stations per hour. Peak hour highlighted. |
| `throughput_heatmap` | Station × hour heatmap of actual counts. Toggle to Utilisation % view. |
| `throughput_utilisation` | Station × hour heatmap of % of configured design rate. |

---

## 2. Operator Pick Time (Dwell Time)

**Source sheet**: `station` (labor_station_record)  
**Robot filter**: K50 robots only

### 2.1 Pick time measurement

Pick time is the duration from robot docking to operator release, measured per robot visit:

```
PickTime = triggerGo.timestamp − arrived.timestamp   (seconds)
```

Valid range: `0 < PickTime < 3 600` seconds.

Events are paired per robot (`机器人编号`) in chronological order: each `arrived` event opens a pair, closed by the next `triggerGo` for the same robot. If two `arrived` events occur without an intervening `triggerGo`, the later `arrived` overwrites the earlier one.

Bucketing uses the `arrived` timestamp so the pick-time distribution reflects when work started.

### 2.2 Aggregation

Per station per hour:

```
MedianPickTime(station, hour)  = median  { PickTime_i : station_i == station, hour_i == hour }
MeanPickTime(station, hour)    = mean    { PickTime_i : station_i == station, hour_i == hour }
```

### 2.3 Implied throughput

```
ImpliedTPH(station, hour) = 3 600 / (MeanPickTime(station, hour) + 6)
```

### 2.4 Utilisation with actual switch time

When actual switch time data is available (§3), a second ceiling is computed:

```
ImpliedTPH_actual(station, hour) = 3 600 / (MeanPickTime + MeanSwitchTime(station, hour))
```

This replaces the fixed 6-second overhead with the observed inter-robot gap.

### 2.5 Distribution

A Gaussian-smoothed histogram of pick times is shown per station:

- Bin width: 2 seconds
- Smoothing kernel: Gaussian, σ = 2.5 bins
- X-axis cap: min(99th percentile of all pick times, 180 s)
- Outlier removal: Tukey IQR fencing (1.5 × IQR) applied independently to pick times and to implied throughput values

### 2.6 OLS regression (Pick Time vs Throughput)

An ordinary least-squares line is fitted per station:

```
Y = a + b · X

  X = MeanPickTime(station, hour)   (seconds)
  Y = actual triggerGo count        (tasks / hr)
```

The R² coefficient of determination measures the fraction of throughput variance explained by pick time alone.

### Charts produced

| Chart ID | Description |
|---|---|
| `dwell_heatmap` | Median (default) / average pick time per station per hour. Toggle to Implied Throughput. |
| `dwell_pick_distribution` | Gaussian-smoothed pick-time histogram per station. |
| `dwell_capacity_gap` | Utilisation % heatmap — fixed switch vs actual switch toggle. |
| `dwell_pick_vs_throughput` | Scatter + OLS fit: pick time (X) vs actual throughput (Y). |

---

## 3. Robot Switch Time

**Source sheet**: `station` (labor_station_record)

### 3.1 Switch time definition

Switch time is the gap between one robot leaving a station and the next robot arriving:

```
SwitchTime = next_arrived.timestamp − release.timestamp   (seconds)
```

Measured per station: for each `release` event at a station, the switch time is the gap to the immediately following `arrived` event at the same station, regardless of robot identity.

Valid range: `0 ≤ SwitchTime < 7 200` seconds.

### 3.2 Aggregation

```
MedianSwitchTime(station, hour) = median { SwitchTime_i : station_i == station, hour_i == hour }
MeanSwitchTime(station, hour)   = mean   { SwitchTime_i : station_i == station, hour_i == hour }
```

Colour scale anchored to the 95th percentile of all valid values, preventing extreme outliers from compressing the colour range.

### Charts produced

| Chart ID | Description |
|---|---|
| `switch_heatmap` | Median (default) / average switch time per station per hour. |

---

## 4. Cycle Time

**Source sheets**: `lifecycle` (primary), `callback` (for demand correlation)

### 4.1 Total cycle time

```
CycleTime = 任务全程耗时(秒) / 60   (minutes)
```

Valid range: `0 ≤ CycleTime < 7 200` seconds (raw). Histogram capped at 30 minutes.

Percentiles computed: **median**, **p90**, **p99**.

Only tasks whose `目标位置` (destination) starts with `LABOR` are included.

### 4.2 Stage durations

The lifecycle sheet records each sub-stage duration in separate columns. Columns whose name contains `耗时(秒)` (excluding `任务全程耗时(秒)`) are treated as stage columns.

| Column pattern | Stage |
|---|---|
| `分配耗时(秒)` | Allocation wait |
| `A42取箱耗时(秒)` | A42 retrieve (shuttle picks from shelf) |
| `A42放箱耗时(秒)` | A42 deposit (shuttle places at handoff point) |
| `K50完成耗时(秒)` | K50 deliver + dwell (AMR delivers to LABOR station and waits for pick) |
| `拣选耗时(秒)` | Picking (operator pick time) |

Stage medians are computed **independently** — not summed — so that tasks with one missing stage do not distort the others.

### 4.3 Demand vs cycle time correlation

Hourly demand (callback `complete` events at LABOR stations) is correlated with median cycle time per hour:

```
Pearson r  = Σ[(D_i − D̄)(C_i − C̄)] / (n · σ_D · σ_C)
Spearman ρ = Pearson r of the rank-transformed series
```

where `D_i` = demand in hour i, `C_i` = median cycle time in hour i.

### Charts produced

| Chart ID | Description |
|---|---|
| `cycle_histogram` | Histogram of cycle times (30-min cap, 2-min bins). |
| `cycle_by_hour` | Median cycle time by hour of day. |
| `demand_vs_cycle` | Dual-axis: demand bars + cycle time line, plus demand-vs-cycle scatter. |
| `cycle_stage_donut` | Donut of median stage durations. |
| `cycle_stage_by_station` | Stacked bar of stage medians per destination station. |

---

## 5. Retrieval Demand

**Source sheet**: `lifecycle`

### 5.1 Source location parsing

Each task's origin is recorded in `起始位置` in the format `HAI-<aisle>-<bay>-<level>-<col>`:

```
Aisle  = split[1]
Bay    = split[2]
Level  = split[3]
```

Non-storage origins (not starting with `HAI`) are excluded.

### 5.2 Hot-aisle threshold

```
Hot aisle: retrievals(aisle) > 1.5 × mean(retrievals across all aisles)
```

Hot aisles are highlighted in the accent colour on the bar chart.

### 5.3 Bay heatmap colour scale

The colour scale maximum is anchored to the **97th percentile** of bay retrieval counts. This prevents a single extremely busy bay from washing out the rest of the grid.

### 5.4 Tote Pareto analysis

Totes are ranked by retrieval frequency (highest to lowest). The Pareto curve shows:

```
X axis: cumulative % of unique tote IDs  (ranked most → least frequent)
Y axis: cumulative % of total retrievals
```

Reference lines mark the **5 %** and **20 %** tote thresholds — the fraction of totes that account for disproportionate retrieval load.

Tote retrieval counts are binned into up to 20 frequency buckets for the distribution bar chart.

### Charts produced

| Chart ID | Description |
|---|---|
| `retrieval_aisle_bar` | Retrieval count per aisle; hot aisles highlighted. |
| `retrieval_bay_heatmap` | Aisle × bay heatmap (97th-percentile colour scale). |
| `retrieval_tote_pareto` | Cumulative retrieval curve vs unique tote population. |

---

## 6. Fleet Utilisation

**Source sheets**: `lifecycle` (primary), `station` (fallback for queue depth)

### 6.1 Station queue depth

Queue depth measures how many K50 robots are concurrently assigned to the same LABOR station at any moment during the shift.

**Delivery interval per task** (from lifecycle sheet):

```
interval_start = complete_ts − K50完成耗时(秒)
interval_end   = complete_ts
```

where `complete_ts = complete(任务完成时间)`.

**Concurrent robot count** (vectorised overlap detection):

```
concurrent(i) = count of tasks j ≠ i at same station where:
    interval_start[j] ≤ interval_end[i]
    AND
    interval_end[j]   ≥ interval_start[i]
```

This is an O(n²) comparison, vectorised with NumPy for n ≤ 5 000 rows and falling back to a row-by-row loop for larger datasets.

**Correlation**:

```
Pearson r = linear correlation between concurrent(i) and leg_duration(i)
```

Measures how strongly robot pile-up at a station lengthens each individual delivery.

**Fallback** (when lifecycle is absent): delivery intervals are constructed from `arrived → release` pairs in the station record.

### 6.2 Real-time fleet utilisation

**Bin width**: 5 minutes.

**K50 utilisation** (from lifecycle sheet):

```
K50_concurrent(t) = count of tasks where interval_start ≤ t ≤ interval_end
K50_util%(t)      = K50_concurrent(t) / total_K50_fleet × 100 %
```

**A42 (shuttle) utilisation**: Derived from shuttle stage columns (`A42取箱耗时(秒)` + `A42放箱耗时(秒)`), or from `arrived → release` station-record pairs as a fallback.

### Charts produced

| Chart ID | Description |
|---|---|
| `queue_depth_leg_map` | Box plots of queue depth per station + median leg duration vs queue depth. |
| `fleet_utilization` | 5-minute time series of K50 and A42 fleet utilisation %. |

---

## 7. Summary — Cross-Day Trends

*Generated only when ≥ 2 days are loaded.*

**Sources**: all three sheets across all loaded days.

### 7.1 Per-day metrics

| Metric | Formula |
|---|---|
| Total tasks | Count of `triggerGo` events at LABOR stations (station record) |
| Median cycle time (min) | median(`任务全程耗时(秒)`) / 60, filtered to 0–7 200 s |
| p90 cycle time (min) | 90th percentile of the same series |
| Avg pick time (s) | Mean of all `arrived → triggerGo` durations, 0–3 600 s |
| Median switch time (s) | Median of all `release → arrived` gaps, 0–7 200 s |

### 7.2 Day-over-day % change

```
Δ%(metric, day) = (value_today − value_yesterday) / |value_yesterday| × 100 %
```

For metrics where **lower is better** (pick time, cycle time, switch time) the sign is **flipped** so that improvement always appears as positive (green) in the heatmap.

### 7.3 Tail severity

```
TailSeverity(day) = p90_cycle_time / median_cycle_time
```

- **≈ 1.0** — tight distribution, most tasks finish in similar time
- **> 2.0** — long tail; a significant minority of tasks take much longer than the median

### 7.4 Throughput consistency (within-day)

```
CV(day)          = std(hourly_completions) / mean(hourly_completions)
PeakToMean(day)  = max(hourly_completions) / mean(hourly_completions)
```

Lower CV = more even throughput across the shift. Lower peak-to-mean = less demand spiking.

### 7.5 R² — how much does pick time explain throughput?

OLS regression across all station-hours within a day:

```
Y = a + b · X
  X = MeanPickTime(station, hour)   (seconds)
  Y = actual triggerGo count        (tasks / hr)

R² = coefficient of determination
```

- **R² → 1.0** — pick time is the dominant throughput driver; faster picks = proportionally higher output
- **R² → 0.0** — other factors dominate (robot supply gaps, upstream demand, station imbalance)

### Charts produced

| Chart ID | Description |
|---|---|
| `summary_delta_heatmap` | Day-over-day Δ% for each key metric. |
| `summary_pick_time` | Average pick time trend per station and overall. |
| `summary_tail_severity` | p90 ÷ median cycle time per day. |
| `summary_throughput_consistency` | CV and peak-to-mean trend per day. |
| `summary_distribution_overlay` | Overlaid cycle-time histograms (30-min cap) per day. |
| `summary_pick_r2_trend` | R² (pick time explains throughput) per day. |

---

## Constants Reference

| Symbol | Value | Used in |
|---|---|---|
| Switch overhead | 6 s | Throughput §1.2, Pick Time §2.3 |
| Pick time valid range | 0–3 600 s | Pick Time §2.1, Summary §7.1 |
| Switch time valid range | 0–7 200 s | Switch Time §3.1, Summary §7.1 |
| Cycle time valid range | 0–7 200 s | Cycle Time §4.1 |
| Histogram cap | 30 min | Cycle Time §4.1, Summary §7.4 |
| Hot-aisle multiplier | 1.5× mean | Retrieval §5.2 |
| Bay heatmap colour cap | 97th percentile | Retrieval §5.3 |
| Switch-time colour cap | 95th percentile | Switch Time §3.2 |
| IQR fence multiplier | 1.5× | Pick Time §2.5 |
| Pick distribution σ | 2.5 bins (2 s each) | Pick Time §2.5 |
| Fleet utilisation bin | 5 min | Fleet §6.2 |
| Overlap detection threshold | n ≤ 5 000 vectorised | Fleet §6.1 |
