# Log Schema Reference (generated 2026-10-07)
# Input: Hairobotics play_extract application logs (.log / .log.gz)

## Log line format
  [thread] YYYY-MM-DD HH:MM:SS,mmm [LEVEL] from <class>-line:<n> - <body>
  Timestamps are the log line's own local time, millisecond precision.

## Lines consumed
  CALLBACK_OF_ROBOT_REACH_STATION      arrival     robot reaches a station (the tote becomes pickable)
  EssKubotStationHandleLetRobotGo      release     "station: X robot: Y will leave" - the operator release
  CALLBACK_OF_TOTE_LOADED_BY_ROBOT     move start  tote picked up
  CALLBACK_OF_TOTE_UNLOADED_BY_ROBOT   move end    tote put down
  CALLBACK_OF_TASK_ALLOCATED           busy from   robot given a task; stationCode = K50 destination
  wmsTask[...]: ND... is created       supply      task created by the warehouse system (destinationCodes)

  NOTE  CALLBACK_OF_TASK_FINISHED fires in the same millisecond as the arrival,
        so it is never the operator release. Only the 'will leave' line is.

## Native frames (log_parser.LogData)
  arrivals     ts, station, robot, tote, point
  releases     ts, station, robot
  tote_events  ts, kind (load/unload), robot, tote, loc, task
  allocations  ts, robot, task, station
  created      ts, task, dest
  moves        robot, tote, t_load, t_unload, from_loc, to_loc, task
  roles        {robot: 'K50' | 'ACR'}

## Robot roles (read from behaviour, not from the robot numbering)
  K50   reaches a station, reports a HAIFLEX type, or works the haiflex buffer
  ACR   everything else - shelf storage to and from the kubot buffer

## Location tokens
  BUFFER_K50       "coop_haiflex"
  BUFFER_ACR       "coop_kubot"
  STATION_PREFIX   "LABOR"

## Metric definitions
  operator time   release - arrival, as logged (so it includes door travel)
  gap             next arrival at that station - release, as logged
  switch time     gap + door_s (the door-open command is logged at the arrival,
                  so the door's physical travel never appears in the log)
  K50 cycle       buffer pickup -> one or more station visits -> buffer return
  multi-station   a cycle whose tote was presented at 2+ stations before returning
  ACR move        storage->buffer = put, buffer->storage = store, else relocation
  hour budget     door + picking + switch + waiting, every interval clipped to the
                  hour, so the shares always sum to the hour
  time budget     3600 / target seconds per tote vs mean pick + switch + wait
                  (means, because they add up to the real cycle = 3600 / rate)
  target pickable 1 - target x median switch / 3600
  robot states    on a task (allocation -> tote put down) / between tasks (gap < AWAY_MIN_S)
                  / away (gap >= AWAY_MIN_S, most likely charging - not in the log)
  utilization     on a task / available (on task + between); also / day fleet (lower bound)
  task supply     created -> tote ready in buffer (ACR put; same slot as the K50 pickup)
                  -> K50 allocated; a K50 is never allocated before the tote is ready
  handover        a release and the next arrival at that station; its wait is the gap
                  beyond the station median gap; starved = wait > starved_s (default 1 s)
  starve stages   where the arriving robot was in each waiting second: no task yet /
                  ACR / tote ready, no K50 / K50 to buffer / carrying the tote /
                  at another station - split exactly, so they sum to the wait
  en route        K50s allocated to the station, not yet arrived, at the release
  station slots   tasks assigned to a station (K50 alloc -> release); a limit is a
                  ceiling it sits at while its ready totes pile up behind it
  zone            stations on the same row (same Y of their LT_LABOR:POINT)
  rack slot       HAI-<aisle>-<bay>-<level>_<depth>[_coop_kubot|_coop_haiflex]
  aisle crowding  allocation -> pickup vs other trips of that fleet bound for the same
                  aisle at the allocation; excess over the median lead at the same
                  fleet-wide load; overlap vs chance = same / (fleet x sum share^2)
  return trip     a store (buffer -> storage) whose tote is put again later; minutes
                  until that next put, in bands
  buffer travel   K50 buffer pickup -> first arrival, by buffer aisle x station

## Settings (config.Settings; ess_config.json next to the logs)
  door_s         default 0.0      seconds of door travel added to every switch
  target_rate    default 270.0    auto target for high-rate zones
  starved_s      default 1.0      wait beyond the median handover that counts as starved (run setting)
  targets  {station|zone: totes/h}
  pick_s   {station|zone: s}  blank = budget - switch
  switch_s {station|zone: s}  blank = measured median switch
  no_door  {station|zone: bool} True = no door (no door seconds there); default: door
  no_door_days [YYYY-MM-DD]   days the doors were not in use: no door seconds at all
  station entries beat zone entries; 0 = none

## Tuning constants (config.py)
  FULL_HOUR_SHARE      0.75
  HIGH_RATE_SHARE      0.5
  LONG_PICK_S          20.0
  IDLE_MIN_MINUTES     30
  MAX_SWITCH_S         3600.0
  DOOR_S_MAX           60.0
  AWAY_MIN_S           300.0

## Output per run
  <run>/station_robot_cycle_report.html   one self-contained file: summary + every day

## Where each number is computed
  metrics.py (Python)          door- and target-independent: operator time, cycles,
                               utilization, zero-door hour budget, sorted raw arrays
  templates/engine.js (browser) everything that moves with door_s or a target:
                               switch stats, time pickable, time budget, summary
