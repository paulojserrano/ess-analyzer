# ESS Analyzer — AI Agent Instructions

## MANDATORY FIRST STEPS — DO NOT SKIP

STOP. Before using Glob, Grep, Read, or any other tool, you MUST read these index files first:

1. Read `.ai-codex/python-modules.md` — every module's classes, methods and function signatures
2. Read `.ai-codex/log-schema.md` — the log events consumed, the metric definitions, and the settings keys

They exist to stop you re-deriving the data model from source. Reading them is non-negotiable.
Regenerate both with `python ai_codex.py` after any structural change.

---

## Project Overview

Desktop application (local web UI + headless CLI) that turns Hairobotics **play_extract**
application logs (`.log` or `.log.gz`) into a *Station and robot cycle analysis* report:
switch time, operator time, robot cycles, robot utilization (on a task / between tasks /
away), task supply, station slots, stations by hour, an hour-by-hour comparison of what
moves together, and a time budget per tote against each station's targets.

One run writes **one self-contained HTML file**. A left-hand panel switches between a
cross-day summary, a **Settings** page and each day. Settings are report-wide: door seconds,
and per zone or station a target rate, target pick time and target switch time. Every number
recalculates live in the browser as they change.

**The log is the only input.** The old Excel exports (`回调明细` / `labor_station_record` /
`任务生命周期`), the old `task_chain` log format, the Plotly chart modules and the Excel
workbook exports were all removed — do not reintroduce them, and do not expect Chinese
column names anywhere in this codebase.

---

## Project Structure

```
ess_analyzer/
├── app.py            # CLI entry — opens the UI, or runs headless when given logs
├── config.py         # Settings dataclass + the tuning constants
├── log_parser.py     # .log/.log.gz → native frames (arrivals, releases, tote_events, moves)
├── metrics.py        # one day's door/target-independent numbers + compact raw arrays
├── report.py         # builds the payload, inlines templates, writes the single HTML
├── pipeline.py       # parse → measure → report, shared by the CLI and the UI
├── server.py         # FastAPI backend + pywebview/browser launcher
├── webui/            # UI front end (index.html, app.css, app.js — no build step)
├── templates/
│   ├── report.html   # page shell: left panel (page outline), day switcher, slots
│   ├── report.css    # styles (inlined)
│   ├── engine.js     # live calculations — pure, no DOM, tested under Node
│   └── report.js     # navigation, visuals (all SVG), PNG/JSON copy, explanations
├── ai_codex.py       # regenerates .ai-codex/
├── tests/            # pytest suite + tests/synthetic.py (log generator)
└── asrs_analysis_output/
    └── {YYYY-MM-DD_HHMMSS}/station_robot_cycle_report.html
```

---

## File Descriptions

**`log_parser.py`** — The only ingestion path. `parse_logs(paths)` streams the file(s) once and
returns a `LogData` with `arrivals`, `releases`, `tote_events`, `moves` and `roles`.
Handles `.log` and `.log.gz` transparently (`open_log`). Robot roles come from behaviour, not
numbering: a robot that reaches a station, reports a HAIFLEX type, or works the haiflex buffer
is a **K50**; anything else is an **ACR**. `pair_moves(events, load_contains=…)` pairs unloads
with the robot's most recent earlier load of that tote — pass `load_contains=BUFFER_K50` for
whole buffer-to-buffer cycles, because a K50 chaining a second station re-loads the tote at the
first one and that re-load must not start a new cycle.

**`metrics.py`** — `day_base(data)` returns everything about a day that does **not** depend
on the door seconds or targets: operator time, visit counts, K50 cycles and ACR moves, robot
utilization, totes per full-production hour, the zero-door hour budget (`budget0`: occ/core/
wait seconds per station-hour, clipped exactly to the hour), and `raw` — each station-hour's
operator times and release→arrival gaps, **sorted, in deciseconds**. `zones_for(bases)` maps
stations to zones.

**`templates/engine.js`** — everything that moves with a setting: switch statistics (gap +
door), time pickable and the target pickable share, the time budget per tote, the cross-day
summary, and all narrative and method text. Pure functions, exported for Node, so
`tests/test_engine.py` runs it. Because adding door seconds shifts every gap equally and keeps
the order, percentiles and thresholds read straight off the sorted arrays; door sums use
prefix sums — so a slider move recomputes 18 days instantly.

**`templates/report.js`** — the page: hash routing (`#summary`, `#day/2026-10-01`, `#settings`), the
day switcher (top right), and pages laid out as **categories → sections** (`DAY_CATEGORIES`,
`SUMMARY_CATEGORIES`, `layout()`): every section goes into a category with `add(cat, section)`, and
the left panel (`renderNav`) is built from those headings, jumps to them and tracks the scroll
(`spy`); each entry has a hover JSON button (`blockJson`) that copies every card's `data()` in that
section or category, with the findings and notes. Door seconds and targets are edited on the Settings page (saved per report in `localStorage`), every visual as SVG drawn with *resolved* colours (so PNG
export needs no stylesheet), the card wrapper with *How it's calculated* / *PNG* / *JSON*,
scroll anchoring between days, and the station-isolating scatter.

**`report.py`** — `build_payload(bases, settings)` and `write_report(...)`. Resolves the default
targets (explicit station/zone targets from settings; high-rate zones auto-get `target_rate`;
slow zones none) and inlines CSS, engine, app and data into `report.html`.

**`pipeline.py`** — `find_logs(paths)` expands folders and groups files into days;
`run(groups, settings, …)` analyses each day, isolates failures, and writes the reports.
Output root: `ESS_OUTPUT_DIR`, else next to the exe when frozen, else the project folder.

**`config.py`** — `Settings(door_s, target_rate, targets, starved_s, output_root)` with `validate()`, plus
`load_settings(folder)` which merges `ess_config.json` found next to the logs. These are the
report's *starting* values; readers can change them in the report — except `starved_s` (seconds
beyond a station's median handover that count as starved, default 1), which is a run setting:
`day_base(data, starved_s)` counts with it and stores it in the `multi` and `starve` blocks.

**What `day_base` measures beyond the station numbers** (all door/target-independent):
`utilization` — per fleet, every robot every second is on a task / between tasks / away
(`AWAY_MIN_S`), with gap-length bands; `flow` — task supply (created → tote ready in the buffer
→ K50 allocated), overall and per destination; `slots` — tasks assigned to each station
(K50 allocation → release), its slot limit (a ceiling it sits at *while its ready totes pile up* —
a steady station is not a limit), and allocation-to-release lead time. In the browser,
"slots needed at target" = target rate × lead time ÷ 3600 (Little's law).
`multi` — station starvation around multi-station K50 cycles: every handover classified as
plain / leaving robot goes on / arriving robot chained in / both, with its wait (gap beyond the
station's median handover, as in the budget) and starved count (wait > `Settings.starved_s`), the
like-for-like excess over each station's plain handovers, 10-minute station windows banded by
multi-station share, and transit between legs. Sums and counts only, so `engine.multiStats()`
pools one day or the whole run; `robot_k50.stations_hist` is the stations-per-cycle histogram.
`starve` — why stations wait, handover by handover (built on `metrics.handovers()`, shared with
`multi`): each waiting second charged to the stage the arriving robot was in (task not created /
waiting on an ACR / tote ready, no K50 / K50 to the buffer / carrying the tote / at another
station — they sum exactly to the wait); K50s already on the way at the release; free K50s and
ready totes when starvation began; pickup→arrival travel histograms; operator pace before the
release; time since the station resumed; tasks created per station. Pooled by
`engine.starveStats()`, worded by `engine.starveText()`.

`spatial` — rack and buffer locations (location codes `HAI-<aisle>-<bay>-<level>_<depth>`): ACR puts by
storage aisle × level, handling time by level, **aisle crowding** per fleet (allocation → pickup against
other trips bound for the same aisle, as excess over the median lead at the same fleet-wide load, so a
busy fleet is not mistaken for a crowded aisle; plus overlap vs chance), **return trips** (stores whose
tote is put again, by minutes until then) and K50 buffer pickup → first arrival per buffer aisle ×
station. Sums and counts only; pooled by `engine.spatialStats()`, worded by `engine.spatialText()`,
drawn by `report.js` `spatialSection()` in the *Rack and buffer locations* category.

**`log_parser.py`** also provides `scan_stations(path)` (a quick partial read the UI uses to
list stations before a run) and `assign_zones(points)` (stations on the same Y row share a zone).

---

## The door-time model — read before touching switch or rate maths

The shutter-door open command is issued in the **same millisecond** as the robot's arrival, so
the door's physical travel never appears in the log. `Settings.door_s` adds it back:

- **Switch time** reported everywhere is `measured gap + door_s`.
- **Operator time** stays exactly as logged (arrival → release), so it still contains the door —
  this matches how the log actually reads.
- In the **hour budget**, `door_s` is taken off the front of each visit's pick window and charged
  to the switch instead. That is what stops the same seconds being counted twice, and it is why
  picking + switch + waiting still sums to the hour.
- **Per-station targets** (`engine.stationTargets`): rate (explicit only), switch (explicit, else
  the measured median switch incl. door), pick (explicit, else budget − switch). Wait allowance =
  budget − pick − switch. If pick + switch exceed the budget, waiting gets no allowance and the
  difference is `target_overflow_s` — never charged to waiting. Excess pick + switch + wait +
  overflow always equals cycle − budget (tested).
- **Target pickable %** = target pick × target rate ÷ 3600. With a blank pick target this equals
  `1 − rate × switch ÷ 3600`. Raising `door_s` lowers it.
- **Presented vs possible** (stations by hour): possible = the station-hour's operating time ÷
  (target pick + its measured switch and wait per tote that hour).
- In the browser the door's seconds are charged to the hour the robot **arrived** (the exact
  zero-door budget comes from Python); the error is at most `door_s` per visit that arrives in
  the last `door_s` seconds of an hour.

`door_s` defaults to `0` (everything as logged). Never invent a door time — it is the user's
measurement.

**Not every station has a door.** `Settings.no_door` maps a station or zone to `True` (no door)
or `False` (has one); a station's own entry beats its zone's, and unlisted stations have a door.
`engine.hasDoor()` is the only place that rule lives. Doors can also be off for whole days
(`Settings.no_door_days`, dates; in the report `settings.no_door_days[date]`), e.g. after they
were disabled on site — on such a day `computeDay` uses a door of 0 everywhere. A doorless station gets no door seconds on
its switch and none off its picks, so every per-station number uses its own door
(`doorOf[s]`), and any distribution spanning stations (overall switch, zone medians) merges
per-station-shifted gaps via `engine.shiftedGaps()` — never a single uniform shift.

---

## Development Rules

### Which file to touch

| Task | File |
|---|---|
| New log line / event consumed | `log_parser.py` |
| Door/target-independent per-day number | `metrics.py` (add it to `day_base`) |
| Anything that moves with door or targets | `templates/engine.js` |
| Cross-day summary metric or grid | `templates/engine.js` (`computeSummary`, `HEADLINE`, `BY_STATION`, `BY_HOUR`) |
| Narrative and method notes | `templates/engine.js` (`dayText`, `summaryText`, `methodNotes`) |
| Visuals, explanations, page layout | `templates/report.js` |
| Page styling | `templates/report.css` |
| Payload, default targets | `report.py` |
| Run orchestration, output folders | `pipeline.py` |
| CLI options | `app.py` |
| UI layout and interactions | `webui/app.js`, `webui/app.css` |
| UI API endpoints, run threading | `server.py` |
| Settings and tuning constants | `config.py` |

### Hard Rules

- **The report pages must stay self-contained** — no CDN scripts, no external data files. Styles
  and the payload are inlined by `report.py`. Only the Google Fonts stylesheet is remote, and the
  pages must still read correctly without it.
- **Charts are hand-written inline SVG** in the templates. There is no Plotly, and no charting
  dependency — do not add one.
- **Every number on a page comes from the payload or the engine.** Never hard-code a figure into
  a template or a narrative string.
- **One source per number.** If a value depends on door or targets it is computed only in
  `engine.js`; if not, only in `metrics.py`. Never compute the same thing in both.
- **Every section belongs to a category.** On the day and summary pages use `add(cat, section)`, never
  `v.append` — `layout()` places it and the left panel picks it up automatically.
- **Every visual goes through `card()`** in `report.js` with `explain`, `data`, and a `chart` /
  `table` / `tiles` renderer — that is what gives it the PNG, JSON and explanation buttons.
- **SVG renderers use resolved colours** (`P.*` from `readPalette()`), never `var(--x)`, so the
  PNG export renders without the stylesheet.
- **Never let the shares of an hour exceed the hour.** Any new interval must be carved out of an
  existing one (see the door model) and clipped with `metrics._hours_of`.
- **Template slots are `__UPPER_SNAKE__`**; a test asserts none survives into the output.
- **The payload is inlined into a `<script>`** — `report._payload` escapes `</` so content can
  never close the tag early. Keep that escaping if you touch it.
- **Sections hide rather than break.** A day without cycles or without a rate breakdown sets
  `display:none` on that section; guard new sections the same way.
- **Use `logging`, never `print()`**, outside `app.py`'s CLI output — the UI shows log records.
- **A failing day must not sink the run** — `pipeline.run` records the error and carries on.

### Adding a metric

1. Door/target-independent → compute in `metrics.day_base`; otherwise in `engine.js`.
2. Render it in `report.js` inside a `card()` with an `explain` (technical: events, formula,
   attribution, filters) and a `data` function for the JSON copy.
3. Add a test against `tests/synthetic.py` (whose day has a known shape) — Python in
   `tests/test_metrics.py`, engine in `tests/test_engine.py`.
4. Run `python ai_codex.py`, then `python -m pytest -q`.

---

## Data Flow

```
.log / .log.gz
    └─► log_parser.parse_logs()        # one streaming pass → LogData
            │
            ▼
    pipeline.run()  [UI: background thread · CLI: main thread]
            │  per day: metrics.day_base(data)  → door/target-independent dict
            │
            └─ report.write_report(bases, settings) → station_robot_cycle_report.html
                    │
                    ▼  in the browser
               engine.computeDay / computeSummary (re-run on every setting change)
```

---

## Source Data Example

```
[application-ess-pinned-dispatcher-276] 2026-09-30 23:47:41,587 [INFO] from callback.EventCallbackHandler-line:1474 - produce callback: 1877828972405915648 message: {"eventCode":"CALLBACK_OF_ROBOT_REACH_STATION","robotCode":"kubot-420","robotTypeCode":"RT_KUBOT_MINI_HAIFLEX","stationCode":"LABOR-3","locationCode":"LT_LABOR:POINT:101294:80448","trays":[{"containerCode":"A000065149"}],"callId":"..."}
[application-ess-pinned-dispatcher-276] 2026-09-30 23:47:42,492 [INFO] from c.h.e.a.s.k.EssKubotStationHandleLetRobotGo-line:104 - station: LABOR-7 robot: kubot-347 will leave
```

- `CALLBACK_OF_TASK_FINISHED` fires in the same millisecond as the arrival — it is **never** the
  operator release. Only the `will leave` line is.
- Station codes arrive ready-made (`LABOR-7`); there is no coordinate inference. Non-operator
  drop points (`CS-003`, `CS-005`, `CS-006`) are counted and excluded.
- Buffer tokens: `coop_haiflex` is the K50 buffer, `coop_kubot` the ACR buffer.
- `CALLBACK_OF_TASK_ALLOCATED` exists for ND tasks for both fleets; its `stationCode` is the
  K50's destination. ACR stores and relocations are not allocated. There are **no charging or
  maintenance events**: a robot 5+ minutes without a task is "away", most likely charging.
- `wmsTask[TMS]: ND… is created … destinationCodes: [LABOR-N]` lines mark task creation.
- The ACR's `…_coop_kubot` drop-off and the K50's `…_coop_haiflex` pickup are the same buffer
  slot, so a tote is ready for a K50 the moment the ACR puts it down.

---

## Running, testing, building

```
pip install -r requirements.txt
python app.py                        # UI (native window if pywebview is installed)
python app.py --browser              # UI in the default browser
python app.py logs/                  # headless, every log in the folder; see --help
python app.py a.log.gz --door 1.5    # start the report with 1.5 s of door travel
python -m pytest -q                  # tests (synthetic logs — no real data needed)
python -m tests.synthetic demo.log   # write a small realistic log
python ai_codex.py                   # regenerate .ai-codex/
build.bat                            # Windows one-file exe via PyInstaller
```
