"""
metrics.py — everything about one day that does not depend on the report's
live settings (door seconds, station targets).

    day_base(LogData) -> dict          JSON-able; one entry in the report payload

The report recalculates switch time, time pickable and the rate breakdown in the
browser whenever the door time or a target changes (templates/engine.js).  To
make that instant, this module does the expensive, exact work once:

* operator time, visit counts, robot cycles and ACR moves — door-independent
* the hour budget at zero door time, clipped exactly to hour boundaries:
      occ0   seconds a tote sat at the station   (arrival → release)
      core0  seconds of mechanical handover      (release → +station median gap)
      wait0  seconds waiting for the next robot  (the rest of the gap)
* per station-hour, the operator times and release→arrival gaps of the visits
  that arrived in that hour, sorted, in deciseconds.  Adding door time shifts
  every gap by the same amount and keeps the order, so the browser can read
  any percentile or threshold straight off these arrays.

Definitions
-----------
visit          one robot arriving at a station and being released again
operator time  release − arrival, as logged (so it includes door travel)
gap            next arrival at that station − release, as logged
K50 cycle      buffer pickup → one or more station visits → return to the buffer
multi-station  a cycle whose tote was presented at two or more stations
starvation     a handover's gap beyond the station's median handover — the
               station waiting for a robot (the hour budget's waiting share)
ACR move       a load paired with the same robot's next unload of that tote:
               storage→buffer = put, buffer→storage = store, else relocation
aisle          the first number of a rack slot, HAI-<aisle>-<bay>-<level>_<depth>
"""
from __future__ import annotations

import logging
import re

import numpy as np
import pandas as pd

from config import (
    AWAY_MIN_S,
    FULL_HOUR_SHARE,
    IDLE_MIN_MINUTES,
    MAX_SWITCH_S,
    OPERATOR_HIST_BINS,
    OPERATOR_HIST_W,
    STARVED_S_DEFAULT,
)
from log_parser import (
    BUFFER_ACR,
    BUFFER_K50,
    ROLE_ACR,
    ROLE_K50,
    LogData,
    assign_zones,
    natural_key,
    pair_moves,
    station_points,
)

_log = logging.getLogger(__name__)

DAY = pd.Timedelta(days=1)


# ── Small helpers ─────────────────────────────────────────────────────────────

def _r(v, nd=1):
    """Round for JSON; NaN/None → None."""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return round(float(v), nd)


def _q(series: pd.Series, p: float) -> float | None:
    return _r(series.quantile(p)) if len(series) else None


def _hist(values: pd.Series, width: float, bins: int) -> list[int]:
    """Counts per `width`-wide bin; the last bin collects everything above."""
    if not len(values):
        return [0] * bins
    idx = np.minimum((values.to_numpy() // width).astype(int), bins - 1)
    return np.bincount(idx[idx >= 0], minlength=bins).tolist()


def _ds(values) -> list[int]:
    """Seconds → sorted deciseconds (ints keep the payload small)."""
    return sorted(int(round(float(x) * 10)) for x in values)


def _robot_num(name) -> int:
    m = re.search(r"(\d+)$", str(name))
    return int(m.group(1)) if m else 0


def _id_range(ids) -> str:
    nums = sorted(_robot_num(i) for i in ids)
    return f"kubot-{nums[0]} to {nums[-1]}" if nums else ""


def _hours_of(start: pd.Series, end: pd.Series, station: pd.Series,
              day: pd.Timestamp) -> pd.DataFrame:
    """Seconds of each [start, end) interval falling in each hour of *day*,
    summed per station.  Index = station, columns = 0…23."""
    s = start.to_numpy("datetime64[ns]")
    e = end.to_numpy("datetime64[ns]")
    base = np.datetime64(day, "ns")
    out = {}
    for h in range(24):
        h0 = base + np.timedelta64(h, "h")
        h1 = h0 + np.timedelta64(1, "h")
        sec = (np.minimum(e, h1) - np.maximum(s, h0)) / np.timedelta64(1, "s")
        out[h] = np.clip(np.nan_to_num(sec, nan=0.0), 0, None)
    df = pd.DataFrame(out)
    df["station"] = station.to_numpy()
    return df.groupby("station").sum()


# ── Visits ────────────────────────────────────────────────────────────────────

def _pair_visits(data: LogData) -> pd.DataFrame:
    """Pair each arrival with the same robot's next release at that station.

    The release has to land before that robot's next arrival at the same
    station, otherwise two visits would be merged into one.
    """
    arr = data.arrivals.rename(columns={"ts": "arr"}).sort_values(["station", "robot", "arr"])
    arr["next_own"] = arr.groupby(["station", "robot"])["arr"].shift(-1)
    rel = data.releases.rename(columns={"ts": "rel"})[["rel", "station", "robot"]]
    m = pd.merge_asof(
        arr.sort_values("arr"), rel.sort_values("rel"),
        left_on="arr", right_on="rel", by=["station", "robot"], direction="forward",
    )
    ok = m["rel"].notna() & (m["next_own"].isna() | (m["rel"] < m["next_own"]))
    return m[ok].drop(columns="next_own").reset_index(drop=True)


def visits(data: LogData, day: pd.Timestamp) -> tuple[pd.DataFrame, int, int]:
    """The day's visits with operator time and the gap to the next arrival.

    Pairing runs over the whole file, so visits spanning midnight still pair;
    the result holds the visits whose arrival falls on *day*.
    """
    v = _pair_visits(data)
    v["op_s"] = (v["rel"] - v["arr"]).dt.total_seconds()
    v = v[v["op_s"] >= 0]
    v = v.sort_values(["station", "arr"]).reset_index(drop=True)
    v["next_arr"] = v.groupby("station")["arr"].shift(-1)
    v["gap_s"] = (v["next_arr"] - v["rel"]).dt.total_seconds()
    v.loc[v["gap_s"] < 0, "gap_s"] = np.nan

    in_day = (v["arr"] >= day) & (v["arr"] < day + DAY)
    n_arrivals = int(((data.arrivals["ts"] >= day) & (data.arrivals["ts"] < day + DAY)).sum())
    n_unpaired = n_arrivals - int(in_day.sum())
    v = v[in_day].reset_index(drop=True)
    v["hour"] = v["arr"].dt.hour
    return v, n_arrivals, n_unpaired


def _switch_core(v: pd.DataFrame) -> tuple[pd.Series, float]:
    """Each station's median release→arrival gap (stand-downs left out): the
    mechanical handover.  Anything beyond it is the station waiting."""
    switch_ok = v["gap_s"].notna() & (v["gap_s"] <= MAX_SWITCH_S)
    core = v[switch_ok].groupby("station")["gap_s"].median()
    overall_core = float(v.loc[switch_ok, "gap_s"].median()) if switch_ok.any() else 0.0
    return core, overall_core


def _station_block(v: pd.DataFrame, day: pd.Timestamp, stations: list[str]) -> dict:
    """Operator time, the zero-door hour budget, and the raw arrays."""
    core, overall_core = _switch_core(v)

    # Hour budget at zero door time, exactly clipped to the hour.
    nxt = v["next_arr"].fillna(v["rel"])
    core_td = pd.to_timedelta(v["station"].map(core).fillna(overall_core), unit="s")
    sw_end = pd.concat([v["rel"] + core_td, nxt], axis=1).min(axis=1)
    occ0 = _hours_of(v["arr"], v["rel"], v["station"], day).reindex(stations).fillna(0.0)
    core0 = _hours_of(v["rel"], sw_end, v["station"], day).reindex(stations).fillna(0.0)
    wait0 = _hours_of(sw_end, nxt, v["station"], day).reindex(stations).fillna(0.0)

    visits_grid, op_med, raw_op, raw_gap = [], [], [], []
    table = []
    for st in stations:
        a = v[v["station"] == st]
        by_h = dict(tuple(a.groupby("hour")))
        visits_grid.append([int(len(by_h[h])) if h in by_h else None for h in range(24)])
        op_med.append([_r(by_h[h]["op_s"].median()) if h in by_h else None for h in range(24)])
        raw_op.append([_ds(by_h[h]["op_s"]) if h in by_h else [] for h in range(24)])
        raw_gap.append([
            _ds(by_h[h].loc[by_h[h]["gap_s"].notna() & (by_h[h]["gap_s"] <= MAX_SWITCH_S), "gap_s"])
            if h in by_h else [] for h in range(24)])
        table.append({
            "station": st,
            "visits": int(len(a)),
            "op_med": _q(a["op_s"], .5),
            "op_mean": _r(a["op_s"].mean()),
            "op_p90": _q(a["op_s"], .9),
            "core_s": _r(core.get(st, overall_core), 2),
        })

    return {
        "overall": {
            "visits": int(len(v)),
            "op_med": _q(v["op_s"], .5), "op_mean": _r(v["op_s"].mean()), "op_p25": _q(v["op_s"], .25),
            "op_p75": _q(v["op_s"], .75), "op_p90": _q(v["op_s"], .9),
        },
        "station_table": table,
        "hm_visits": visits_grid,
        "hm_op_med": op_med,
        "hist_op": _hist(v["op_s"], OPERATOR_HIST_W, OPERATOR_HIST_BINS),
        "budget0": {
            "occ": occ0.round(1).values.tolist(),
            "core": core0.round(1).values.tolist(),
            "wait": wait0.round(1).values.tolist(),
        },
        "raw": {"op": raw_op, "gap": raw_gap},
    }


# ── Robot cycles ──────────────────────────────────────────────────────────────

def _arrivals_by_robot(data: LogData) -> dict[str, np.ndarray]:
    return {r: np.sort(g["ts"].to_numpy("datetime64[ns]")) for r, g in data.arrivals.groupby("robot")}


def k50_cycles(data: LogData) -> pd.DataFrame:
    """Every K50 buffer → station(s) → buffer cycle in the file, with ``n_st``,
    the station arrivals the robot made between the pickup and the return.

    Pairs from buffer loads only: a K50 chaining a second station re-loads the
    tote at the first one, and that re-load does not start a new cycle.
    """
    k = pair_moves(data.tote_events, load_contains=BUFFER_K50, robots=data.robots(ROLE_K50))
    k = k[k["to_loc"].str.contains(BUFFER_K50, na=False)].reset_index(drop=True)
    k["n_st"] = 0
    arr = _arrivals_by_robot(data)
    for robot, idx in k.groupby("robot").groups.items():
        a = arr.get(robot)
        if a is None:
            continue
        lo = np.searchsorted(a, k.loc[idx, "t_load"].to_numpy("datetime64[ns]"), side="left")
        hi = np.searchsorted(a, k.loc[idx, "t_unload"].to_numpy("datetime64[ns]"), side="right")
        k.loc[idx, "n_st"] = hi - lo
    return k


# Stations per cycle in the histogram: 1, 2, … and the last bin collects the rest.
STATIONS_HIST_BINS = 8


def _cycle_metrics(data: LogData, day: pd.Timestamp, cycles: pd.DataFrame) -> dict | None:
    mv = data.moves
    if mv.empty:
        return None
    k50, acr = data.robots(ROLE_K50), data.robots(ROLE_ACR)

    # Robot-hours: a robot counts for an hour if it logged anything in it.
    act = pd.concat([
        mv[["robot", "t_unload"]].rename(columns={"t_unload": "ts"}),
        mv[["robot", "t_load"]].rename(columns={"t_load": "ts"}),
        data.arrivals[["robot", "ts"]],
    ]).dropna()
    act = act[(act["ts"] >= day) & (act["ts"] < day + DAY)]
    act = act.assign(hour=act["ts"].dt.hour).drop_duplicates(["robot", "hour"])
    k_act = act[act["robot"].isin(k50)]
    a_act = act[act["robot"].isin(acr)]
    k_hours = k_act.groupby("robot").size()
    a_hours = a_act.groupby("robot").size()

    # ── K50: buffer → station(s) → buffer ──
    k = cycles[(cycles["t_unload"] >= day) & (cycles["t_unload"] < day + DAY)].copy()
    no_visit = int((k["n_st"] == 0).sum()) if len(k) else 0
    kc = k[k["n_st"] >= 1].copy()
    kc["hour"] = kc["t_unload"].dt.hour
    kc["cyc_s"] = (kc["t_unload"] - kc["t_load"]).dt.total_seconds()

    # ── ACR: put / store / relocation ──
    a = mv[mv["robot"].isin(acr)].copy()
    a = a[(a["t_unload"] >= day) & (a["t_unload"] < day + DAY)]
    from_buf = a["from_loc"].str.contains(BUFFER_ACR, na=False)
    to_buf = a["to_loc"].str.contains(BUFFER_ACR, na=False)
    a["kind"] = np.select([~from_buf & to_buf, from_buf & ~to_buf, ~from_buf & ~to_buf],
                          ["put", "store", "reloc"], default="")
    a = a[a["kind"] != ""]
    a["hour"] = a["t_unload"].dt.hour

    hourly = []
    for h in range(24):
        ka = int((k_act["hour"] == h).sum())
        aa = int((a_act["hour"] == h).sum())
        kn = int((kc["hour"] == h).sum())
        multi = int(((kc["hour"] == h) & (kc["n_st"] >= 2)).sum())
        sub = a[a["hour"] == h]
        put, store, reloc = (int((sub["kind"] == x).sum()) for x in ("put", "store", "reloc"))
        hourly.append({
            "h": h, "k50_active": ka, "k50_cycles": kn,
            "k50_per": _r(kn / ka, 2) if ka and kn else None,
            "multi": multi, "multi_pct": _r(multi / kn * 100) if kn else None,
            "acr_active": aa, "put": put, "store": store, "reloc": reloc,
            "put_per": _r(put / aa, 2) if aa and put else None,
            "store_per": _r(store / aa, 2) if aa and store else None,
        })

    k_cyc = kc.groupby("robot").size()
    k_rows = sorted(
        ([r, int(k_cyc.get(r, 0)), int(k_hours[r]), _r(k_cyc.get(r, 0) / k_hours[r], 2)]
         for r in k_hours.index),
        key=lambda x: -x[3])
    a_cnt = a.groupby(["robot", "kind"]).size().unstack(fill_value=0)
    a_rows = []
    for r in a_hours.index:
        g = a_cnt.loc[r] if r in a_cnt.index else {}
        put, store, reloc = (int(g.get(x, 0)) for x in ("put", "store", "reloc"))
        a_rows.append([r, put, store, reloc, int(a_hours[r]), _r((put + store) / a_hours[r], 2)])
    a_rows.sort(key=lambda x: -x[5])

    n_st = kc["n_st"] if len(kc) else pd.Series(dtype=int)
    k_rates = pd.Series([x[3] for x in k_rows], dtype=float)
    put_n, store_n, reloc_n = (int((a["kind"] == x).sum()) for x in ("put", "store", "reloc"))
    a_robot_h, k_robot_h = int(a_hours.sum()), int(k_hours.sum())

    return {
        "hourly": hourly,
        "k50_robots": k_rows,
        "acr_robots": a_rows,
        "k50_id_range": _id_range(k_hours.index),
        "acr_id_range": _id_range(a_hours.index),
        "robot_k50": {
            "n": len(k_rows), "total": int(len(kc)), "robot_hours": k_robot_h,
            "rate": _r(len(kc) / k_robot_h, 2) if k_robot_h else None,
            "p10": _r(k_rates.quantile(.1), 2) if len(k_rates) else None,
            "med": _r(k_rates.median(), 2) if len(k_rates) else None,
            "p90": _r(k_rates.quantile(.9), 2) if len(k_rates) else None,
            "cyc_med_s": _r(kc["cyc_s"].median(), 0) if len(kc) else None,
            "no_visit_loads": no_visit,
            "multi_station_n": int((n_st >= 2).sum()),
            "multi_station_pct": _r((n_st >= 2).mean() * 100) if len(n_st) else None,
            "stations_per_cycle": {"1": int((n_st == 1).sum()), "2": int((n_st == 2).sum()),
                                   "3+": int((n_st >= 3).sum())},
            # Cycles by stations visited: index 0 = one station … last = STATIONS_HIST_BINS or more.
            "stations_hist": np.bincount(np.minimum(n_st.to_numpy(dtype=int), STATIONS_HIST_BINS) - 1,
                                         minlength=STATIONS_HIST_BINS).tolist() if len(n_st)
            else [0] * STATIONS_HIST_BINS,
        },
        "robot_acr": {
            "n": len(a_rows), "robot_hours": a_robot_h,
            "put": put_n, "store": store_n, "reloc": reloc_n,
            "put_rate": _r(put_n / a_robot_h, 2) if a_robot_h else None,
            "store_rate": _r(store_n / a_robot_h, 2) if a_robot_h else None,
            "reloc_rate": _r(reloc_n / a_robot_h, 2) if a_robot_h else None,
        },
    }


# ── Multi-station visits and station starvation ───────────────────────────────

# Station windows for "does starvation rise when multi-station visits do?".
_MULTI_WINDOW = "10min"
_MULTI_WINDOW_MIN = 5          # handovers a window needs to be read
# Bands of a window's share of handovers involving a multi-station robot, [lo, hi) in %.
MULTI_BANDS = [(0, 0, "none"), (0, 10, "under 10%"), (10, 20, "10–20%"),
               (20, 30, "20–30%"), (30, 101, "30% or more")]
# Handover kinds: does the robot leaving go on to another station, and did the
# robot arriving come from one?
HANDOVER_KINDS = ("plain", "out", "in", "both")


def handovers(data: LogData, v: pd.DataFrame, cycles: pd.DataFrame,
              starved_s: float = STARVED_S_DEFAULT) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The day's visits tied to their K50 cycles, and every handover between them.

    Each visit gets its cycle (``cid``, ``t_load`` — the buffer pickup — and
    ``task``), how many stations that cycle visits (``n_legs``) and which of
    them this one is (``leg``, 1 = the first).

    A handover is one visit's release and the next arrival at that station,
    stand-downs (gaps over MAX_SWITCH_S) left out.  ``wait`` is the gap beyond
    the station's median handover — the hour budget's waiting share; the
    station is waiting from ``ws`` (release + that median) to ``t_arr``, and is
    ``starved`` when the wait exceeds *starved_s*.
    ``nxt`` is the arriving visit's row in the returned visits.
    """
    v = v.copy()
    for col in ("cid", "n_legs", "leg", "t_load", "task"):
        v[col] = np.nan
    v["t_load"] = pd.to_datetime(v["t_load"])
    cyc = cycles[cycles["n_st"] >= 1] if cycles is not None else None
    if cyc is not None and not cyc.empty:
        cyc = cyc.reset_index(drop=True).rename_axis("cid").reset_index()
        m = pd.merge_asof(v[["arr", "robot"]].reset_index().sort_values("arr"),
                          cyc[["cid", "robot", "t_load", "t_unload", "n_st", "task"]].sort_values("t_load"),
                          left_on="arr", right_on="t_load", by="robot", direction="backward")
        m = m[m["arr"] <= m["t_unload"]].set_index("index")
        v["cid"], v["n_legs"], v["t_load"], v["task"] = m["cid"], m["n_st"], m["t_load"], m["task"]
        arr = _arrivals_by_robot(data)
        leg = np.full(len(v), np.nan)
        for robot, idx in v.loc[v["cid"].notna()].groupby("robot").groups.items():
            a = arr[robot]
            lo = np.searchsorted(a, m.loc[idx, "t_load"].to_numpy("datetime64[ns]"), side="left")
            at = np.searchsorted(a, v.loc[idx, "arr"].to_numpy("datetime64[ns]"), side="right")
            leg[v.index.get_indexer(idx)] = at - lo
        v["leg"] = leg
    v["goes_on"] = (v["leg"] < v["n_legs"]).fillna(False).astype(bool)
    v["chained"] = (v["leg"] >= 2).fillna(False).astype(bool)

    core, overall_core = _switch_core(v)
    v["core"] = v["station"].map(core).fillna(overall_core)
    same = (v["station"].shift(-1) == v["station"]) & (v["arr"].shift(-1) == v["next_arr"])
    ok = same & v["gap_s"].notna() & (v["gap_s"] <= MAX_SWITCH_S)
    ho = pd.DataFrame({
        "station": v["station"], "hour": v["hour"], "t": v["rel"], "t_arr": v["next_arr"],
        "nxt": np.arange(1, len(v) + 1),
        "out": v["goes_on"], "in": v["chained"].shift(-1, fill_value=False).astype(bool),
        "wait": (v["gap_s"] - v["core"]).clip(lower=0),
    })
    ho["ws"] = ho["t"] + pd.to_timedelta(v["core"], unit="s")
    ho = ho[ok].copy()
    ho["kind"] = np.select([ho["out"] & ho["in"], ho["out"], ho["in"]], ["both", "out", "in"], "plain")
    ho["multi"] = ho["kind"] != "plain"
    ho["starved"] = ho["wait"] > starved_s
    ho.attrs["starved_s"] = starved_s
    return v, ho


def _multi_station(v: pd.DataFrame, ho: pd.DataFrame, full: list[int]) -> dict | None:
    """Station starvation around multi-station K50 cycles.

    Every handover (see ``handovers``) is classified by the two robots involved:

        plain   the leaving robot returns to the buffer, the arriving one came from it
        out     the leaving robot goes on to another station with the tote
        in      the arriving robot comes from another station
        both    both of the above

    ``excess_s`` is the wait at multi-station handovers above that station's
    own plain-handover average — the starvation multi-station visits add, like
    for like.  Sums and counts, not means, so the report can pool any number of
    days.  Full-production hours only, except ``hourly``.
    """
    if ho.empty or v["cid"].isna().all():
        return None
    hourly = []
    for h in range(24):
        x = ho[ho["hour"] == h]
        hourly.append({"h": h, "handovers": int(len(x)), "multi": int(x["multi"].sum()),
                       "wait_s": _r(x["wait"].sum())})

    f = ho[ho["hour"].isin(full)]
    kinds = {k: {"n": int((f["kind"] == k).sum()),
                 "wait_s": _r(f.loc[f["kind"] == k, "wait"].sum()),
                 "starved": int(((f["kind"] == k) & f["starved"]).sum())}
             for k in HANDOVER_KINDS}

    plain_all = f.loc[~f["multi"], "wait"]
    base_all = float(plain_all.mean()) if len(plain_all) else 0.0
    stations, excess = [], 0.0
    for st, g in f.groupby("station", sort=False):
        pl, mu = g[~g["multi"]], g[g["multi"]]
        base = float(pl["wait"].mean()) if len(pl) else base_all
        ex = float((mu["wait"] - base).sum())
        excess += ex
        stations.append({
            "station": st, "handovers": int(len(g)), "multi": int(len(mu)),
            "plain_wait_s": _r(pl["wait"].sum()), "multi_wait_s": _r(mu["wait"].sum()),
            "wait_s": _r(g["wait"].sum()), "excess_s": _r(ex),
            "starved_plain": int(pl["starved"].sum()),
            "starved_multi": int(mu["starved"].sum()),
        })
    stations.sort(key=lambda x: natural_key(x["station"]))

    # Does a station starve more in the windows where more of its handovers
    # involve a multi-station robot?
    w = f.assign(win=f["t"].dt.floor(_MULTI_WINDOW)).groupby(["station", "win"]).agg(
        n=("wait", "size"), multi=("multi", "sum"), wait=("wait", "sum"))
    w = w[w["n"] >= _MULTI_WINDOW_MIN]
    share = w["multi"] / w["n"] * 100
    bands = []
    for lo, hi, label in MULTI_BANDS:
        sel = w[share == 0] if hi == 0 else w[(share > 0) & (share >= lo) & (share < hi)]
        bands.append({"band": label, "windows": int(len(sel)), "handovers": int(sel["n"].sum()),
                      "wait_s": _r(sel["wait"].sum())})
    # Transit between legs: the chained robot's release at its previous station
    # → its arrival at this one; and whether this station was already free by then.
    vv = v[v["cid"].notna()].sort_values(["cid", "arr"])
    prev_leg_rel = vv.groupby("cid")["rel"].shift(1)
    prev_here = v.groupby("station")["rel"].shift(1).reindex(vv.index)
    ch = vv["chained"] & prev_leg_rel.notna() & vv["hour"].isin(full)
    transit = (vv.loc[ch, "arr"] - prev_leg_rel[ch]).dt.total_seconds()
    free = (prev_here[ch] < prev_leg_rel[ch])
    return {
        "kinds": kinds,
        "wait_s": _r(f["wait"].sum()),
        "excess_s": _r(excess),
        "stations": stations,
        "hourly": hourly,
        "bands": bands,
        "window_min": int(pd.Timedelta(_MULTI_WINDOW).total_seconds() // 60),
        "starved_s": float(ho.attrs["starved_s"]),
        "chained_n": int(ch.sum()),
        "transit_med": _r(transit.median()) if len(transit) else None,
        "transit_p90": _r(transit.quantile(.9)) if len(transit) else None,
        "chained_to_free_pct": _r(free.mean() * 100) if len(free) else None,
    }


# ── Why stations starve ───────────────────────────────────────────────────────

# Where the robot that ended a station's wait was during it.  Its timeline is
# task created → tote ready in the buffer → K50 allocated → buffer pickup →
# arrival, and each waiting second falls in exactly one of these stages.
STARVE_STAGES = ("no_task", "acr", "ready", "to_buffer", "to_station", "chained", "unknown")
EN_ROUTE_BINS = 31           # K50s on the way at the release: 0, 1, … 30+
TRAVEL_HIST_W, TRAVEL_HIST_BINS = 10.0, 60     # seconds; last bin collects the rest
# The operator's pace before a release: mean of the last 3 operator times at
# the station ÷ the station's median, in bands [lo, hi).
PACE_N = 3
PACE_BANDS = [(0, 0.7, "under 70%"), (0.7, 0.9, "70–90%"), (0.9, 1.1, "90–110%"),
              (1.1, 1.3, "110–130%"), (1.3, 1e9, "130% or more")]
# A station resumes after a gap of this long; minutes since then, in bands.
RESUME_GAP_S = 600.0
RESUME_BANDS = [(0, 5, "under 5 min"), (5, 15, "5–15 min"), (15, 30, "15–30 min"),
                (30, 60, "30–60 min"), (60, 1e9, "over 1 h")]
# Free K50s and ready totes at the moment a station starts to starve.
CONTEXT_KINDS = ("dispatch", "k50", "supply", "neither")


def _travel_hist(seconds) -> list[int]:
    return _hist(pd.Series(seconds, dtype=float).dropna(), TRAVEL_HIST_W, TRAVEL_HIST_BINS)


def _bands(values: np.ndarray, bands, rows: pd.DataFrame) -> list[dict]:
    out = []
    for lo, hi, label in bands:
        sel = rows[(values >= lo) & (values < hi)]
        out.append({"band": label, "n": int(len(sel)), "starved": int(sel["starved"].sum()),
                    "wait_s": _r(sel["wait"].sum())})
    return out


def _starvation(data: LogData, v: pd.DataFrame, ho: pd.DataFrame, day: pd.Timestamp,
                full: list[int], stations: list[str], k50_idle: np.ndarray | None) -> dict | None:
    """Why stations wait for robots, handover by handover.

    1. stages     where the arriving robot was during each second of the wait:
                  no task yet · waiting on an ACR · tote ready, no K50 allocated ·
                  K50 on its way to the buffer · carrying the tote to the station ·
                  coming from another station (multi-station) · unknown.  The
                  stages split each wait exactly, so they sum to it.
    2. en_route   K50s already allocated to the station, not yet there, at the release
    3. context    at the moment starvation begins: K50s free, and totes ready for
                  this station in the buffer
                      dispatch  both — a robot and a tote, but no allocation
                      k50       a tote ready, no K50 free
                      supply    a K50 free, no tote ready
                      neither
    4. travel     buffer pickup → arrival (first station of a cycle), allocation →
                  pickup; histograms so days pool
    5. pace       the operator's last few picks against the station's median
    6. demand     tasks created per station per full-production hour
    7. resume     minutes since the station came back from a stand-down

    Sums, counts and histograms only.  Full-production hours, except ``hourly``.
    """
    if ho.empty:
        return None
    d0 = np.datetime64(day, "ns")

    def sec(x) -> np.ndarray:
        a = pd.to_datetime(pd.Series(x)).to_numpy("datetime64[ns]")
        return (a - d0) / np.timedelta64(1, "s")

    ho = ho.copy()
    nv = v.iloc[ho["nxt"].to_numpy()]
    tt = _task_table(data)
    n = len(ho)
    if tt is not None:
        tk = tt.reindex(nv["task"].to_numpy())
        c, rd, a = sec(tk["ts"]), sec(tk["ready"]), sec(tk["k50"])
    else:
        c = rd = a = np.full(n, np.nan)
    load = sec(nv["t_load"])
    ws, we, wait = sec(ho["ws"]), sec(ho["t_arr"]), ho["wait"].to_numpy()
    chained = nv["chained"].to_numpy(dtype=bool)
    no_info = np.isnan(load)

    # ── 1. stages: boundaries made monotone; a missing one collapses into the next
    b = np.column_stack([c, rd, a, load])
    for i in (2, 1, 0):
        b[:, i] = np.where(np.isnan(b[:, i]), b[:, i + 1], np.fmin(b[:, i], b[:, i + 1]))
    lo_edges = [np.full(n, -np.inf), b[:, 0], b[:, 1], b[:, 2], b[:, 3]]
    hi_edges = [b[:, 0], b[:, 1], b[:, 2], b[:, 3], np.full(n, np.inf)]
    secs = {}
    for name, lo, hi in zip(("no_task", "acr", "ready", "to_buffer", "to_station"), lo_edges, hi_edges):
        ov = np.nan_to_num(np.clip(np.fmin(we, hi) - np.fmax(ws, lo), 0, None))
        ov[no_info | chained] = 0
        secs[name] = ov
    pre_unknown = np.isnan(c) & ~no_info & ~chained
    secs["unknown"] = np.where(pre_unknown, secs["no_task"], 0) + np.where(no_info, wait, 0)
    secs["no_task"] = np.where(pre_unknown, 0, secs["no_task"])
    secs["chained"] = np.where(chained, wait, 0)
    for k in STARVE_STAGES:
        ho["s_" + k] = secs[k]
    # The stage the robot was in when the station began to wait.
    idx = np.sum(ws[:, None] >= np.nan_to_num(b, nan=np.inf), axis=1)
    names = np.array(["no_task", "acr", "ready", "to_buffer", "to_station"])
    start = names[idx]
    start = np.where(pre_unknown & (idx == 0), "unknown", start)
    start = np.where(chained, "chained", np.where(no_info, "unknown", start))
    ho["start"] = start

    # ── 2. robots on the way at the release
    trips = _alloc_trips(data)
    en_route = np.zeros(n, dtype=int)
    t_rel = sec(ho["t"])
    if trips is not None:
        for st, g in trips.groupby("station"):
            mask = (ho["station"] == st).to_numpy()
            if mask.any():
                en_route[mask] = _open_at(sec(g["t_alloc"]), sec(g["t_arr"]), t_rel[mask])
    ho["en_route"] = np.minimum(en_route, EN_ROUTE_BINS - 1)

    # ── 3. free K50s and ready totes when starvation begins
    ready = _ready_totes(tt)
    ready_here = np.zeros(n, dtype=int)
    for st, g in ready.groupby("station"):
        mask = (ho["station"] == st).to_numpy()
        if mask.any():
            ready_here[mask] = _open_at(sec(g["ready"]), sec(g["t_k"]), ws[mask])
    if k50_idle is not None:
        i = np.clip(np.nan_to_num(ws, nan=0).astype(int), 0, 86399)
        free = np.where((ws >= 0) & (ws < 86400), k50_idle[i], np.nan)
    else:
        free = np.full(n, np.nan)
    has_free, has_ready = np.nan_to_num(free) >= 1, ready_here >= 1
    ho["context"] = np.select([has_free & has_ready, has_ready, has_free], ["dispatch", "k50", "supply"], "neither")
    ho["free_k50"], ho["ready_here"] = free, ready_here

    # ── 5. pace and 7. resume, per leaving visit
    op_med = v.groupby("station")["op_s"].transform("median")
    pace = v.groupby("station")["op_s"].transform(lambda x: x.rolling(PACE_N, min_periods=PACE_N).mean()) / op_med
    gap_before = v.groupby("station")["gap_s"].shift(1)
    resumed = v["arr"].where(gap_before.isna() | (gap_before >= RESUME_GAP_S))
    resumed = resumed.groupby(v["station"]).ffill()
    ho["pace"] = pace.reindex(ho.index).to_numpy()
    ho["since_min"] = ((ho["t"] - resumed.reindex(ho.index)).dt.total_seconds() / 60).to_numpy()

    f = ho[ho["hour"].isin(full)]

    def stage_sums(g) -> dict:
        return {k: _r(g["s_" + k].sum()) for k in STARVE_STAGES}

    def head(g) -> dict:
        return {"handovers": int(len(g)), "starved": int(g["starved"].sum()), "wait_s": _r(g["wait"].sum())}

    # ── 4. travel (first stations of cycles, arriving in full hours)
    first_all = v[(v["leg"] == 1) & v["t_load"].notna()]
    travel_all = (first_all["arr"] - first_all["t_load"]).dt.total_seconds()
    first = first_all[first_all["hour"].isin(full)]
    travel = travel_all[first.index]
    ends_starved = set(f.loc[f["starved"], "nxt"].to_numpy())
    pos = pd.Series(np.arange(len(v)), index=v.index).reindex(first.index).to_numpy()
    after_starve = np.isin(pos, list(ends_starved))
    empty = pd.Series(dtype=float)
    if tt is not None:
        ka = tt.reindex(first["task"].to_numpy())["k50"]
        empty = (first["t_load"].to_numpy() - pd.to_datetime(ka).to_numpy()) / np.timedelta64(1, "s")
        empty = pd.Series(empty)[lambda x: x >= 0]

    # ── 6. demand
    cr = data.created
    cr = cr[(cr["ts"] >= day) & (cr["ts"] < day + DAY) & cr["ts"].dt.hour.isin(full)] if not cr.empty else cr

    station_rows = []
    for st in stations:
        g = f[f["station"] == st]
        tr = travel[first["station"] == st]
        station_rows.append({
            "station": st, **head(g), "stages": stage_sums(g),
            "starved_dispatch": int((g["starved"] & (g["context"] == "dispatch")).sum()),
            "starved_none_en_route": int((g["starved"] & (g["en_route"] == 0)).sum()),
            "en_route_n": np.bincount(g["en_route"], minlength=EN_ROUTE_BINS).tolist(),
            "en_route_starved": np.bincount(g.loc[g["starved"], "en_route"], minlength=EN_ROUTE_BINS).tolist(),
            "travel_hist": _travel_hist(tr),
            "created": int((cr["dest"] == st).sum()) if not cr.empty else 0,
        })

    hourly = []
    for h in range(24):
        g = ho[ho["hour"] == h]
        tr = travel_all[first_all["hour"] == h]
        hourly.append({"h": h, **head(g), "stages": stage_sums(g),
                       "travel_med": _r(tr.median()) if len(tr) else None})

    st_f = f[f["starved"]]
    return {
        **head(f),
        "starved_s": float(ho.attrs["starved_s"]),
        "full_hours": len(full),
        "stages": stage_sums(f),
        "starved_at": {k: int((st_f["start"] == k).sum()) for k in STARVE_STAGES},
        "en_route": [{"n": int((f["en_route"] == i).sum()), "starved": int(st_f["en_route"].eq(i).sum()),
                      "wait_s": _r(f.loc[f["en_route"] == i, "wait"].sum())} for i in range(EN_ROUTE_BINS)],
        "context": {k: {"n": int((st_f["context"] == k).sum()),
                        "wait_s": _r(st_f.loc[st_f["context"] == k, "wait"].sum())} for k in CONTEXT_KINDS},
        "free_k50_sum": _r(np.nansum(st_f["free_k50"])), "ready_here_sum": int(st_f["ready_here"].sum()),
        "travel_hist": _travel_hist(travel),
        "travel_hist_starved": _travel_hist(travel[after_starve]),
        "travel_hist_other": _travel_hist(travel[~after_starve]),
        "empty_hist": _travel_hist(empty),
        "travel_w": TRAVEL_HIST_W,
        "pace": _bands(f["pace"].to_numpy(), PACE_BANDS, f),
        "resume": _bands(f["since_min"].to_numpy(), RESUME_BANDS, f),
        "stations": station_rows,
        "hourly": hourly,
    }


# ── Robot utilization ─────────────────────────────────────────────────────────

# An allocation more than this far ahead of the load it belongs to is not
# trusted to mark the start of the work (a task re-queued, say).
_ALLOC_LOOKBACK = pd.Timedelta(minutes=30)


def _busy_intervals(data: LogData, moves: pd.DataFrame) -> pd.DataFrame:
    """Each robot's time on task, as merged [start, end) intervals.

    A task runs from the robot's allocation to it (empty travel to the pickup
    included) until the tote is put down.  Moves with no allocation in the log
    — ACR stores and relocations, which are not allocated — start at the load.
    Overlapping tasks on the same robot (an ACR carrying several totes) merge,
    so a robot is counted once however much it is carrying.
    """
    if moves.empty:
        return pd.DataFrame(columns=["robot", "start", "end"])
    m = moves[["robot", "task", "t_load", "t_unload"]].copy()
    alloc = data.allocations
    if not alloc.empty:
        first = alloc.groupby(["robot", "task"], as_index=False)["ts"].min().rename(columns={"ts": "t_alloc"})
        m = m.merge(first, on=["robot", "task"], how="left")
        ok = m["t_alloc"].notna() & (m["t_alloc"] <= m["t_load"]) & (m["t_load"] - m["t_alloc"] <= _ALLOC_LOOKBACK)
        m["start"] = m["t_alloc"].where(ok, m["t_load"])
    else:
        m["start"] = m["t_load"]
    m["end"] = m["t_unload"]
    m = m[m["end"] > m["start"]].sort_values(["robot", "start"])

    merged = []
    for robot, g in m.groupby("robot", sort=False):
        cur_s = cur_e = None
        for s, e in zip(g["start"], g["end"]):
            if cur_e is None or s > cur_e:
                if cur_e is not None:
                    merged.append((robot, cur_s, cur_e))
                cur_s, cur_e = s, e
            elif e > cur_e:
                cur_e = e
        if cur_e is not None:
            merged.append((robot, cur_s, cur_e))
    return pd.DataFrame(merged, columns=["robot", "start", "end"])


def _sweep(start_s: np.ndarray, end_s: np.ndarray) -> np.ndarray:
    """Count of open [start, end) intervals at every second of the day."""
    delta = np.zeros(86401)
    ok = end_s > start_s
    np.add.at(delta, np.floor(start_s[ok]).astype(int), 1)
    np.add.at(delta, np.ceil(end_s[ok]).astype(int), -1)
    return np.cumsum(delta)[:86400]


def _day_seconds(ts: pd.Series, day: pd.Timestamp) -> np.ndarray:
    return ((ts - day).dt.total_seconds().clip(0, 86400)).to_numpy(dtype=float)


# Gap-length bands for "where idle robot time goes".
_GAP_BANDS = [(0, 10, "<10 s"), (10, 30, "10–30 s"), (30, 60, "30–60 s"), (60, 120, "1–2 min"),
              (120, 300, "2–5 min"), (300, 600, "5–10 min"), (600, 1200, "10–20 min"),
              (1200, 3600, "20–60 min"), (3600, 1e12, "over 1 h")]


def _robot_states(iv: pd.DataFrame, day: pd.Timestamp, full: list[int]) -> dict | None:
    """Every robot, every second: on a task, between tasks, or away.

    Between two tasks a robot is *between tasks* if the gap is shorter than
    AWAY_MIN_S — it is free and waiting for its next allocation — and *away*
    otherwise.  The log has no charging events, so a long gap is most likely a
    robot charging (or parked, or faulted); either way it was not available.
    Before its first task and after its last task of the day a robot is off
    shift and counted in nothing.
    """
    iv = iv.sort_values(["robot", "start"])
    on_day = iv[(iv["end"] > day) & (iv["start"] < day + DAY)]
    if on_day.empty:
        return None
    fleet = int(on_day["robot"].nunique())

    g = iv.copy()
    g["next"] = g.groupby("robot")["start"].shift(-1)
    g = g.dropna(subset=["next"])
    g["gap"] = (g["next"] - g["end"]).dt.total_seconds()
    g = g[(g["gap"] > 0) & (g["next"] > day) & (g["end"] < day + DAY)]
    short, long_ = g[g["gap"] < AWAY_MIN_S], g[g["gap"] >= AWAY_MIN_S]

    busy = _sweep(_day_seconds(on_day["start"], day), _day_seconds(on_day["end"], day))
    idle = _sweep(_day_seconds(short["end"], day), _day_seconds(short["next"], day))
    away = _sweep(_day_seconds(long_["end"], day), _day_seconds(long_["next"], day))

    def by(arr, n):
        return arr.reshape(n, 86400 // n)

    b5, i5, a5 = by(busy, 288), by(idle, 288), by(away, 288)
    bh, ih, ah = by(busy, 24), by(idle, 24), by(away, 24)
    hourly = []
    for h in range(24):
        h0, h1 = day + pd.Timedelta(hours=h), day + pd.Timedelta(hours=h + 1)
        active = int(on_day.loc[(on_day["start"] < h1) & (on_day["end"] > h0), "robot"].nunique())
        mb, mi, ma = float(bh[h].mean()), float(ih[h].mean()), float(ah[h].mean())
        hourly.append({
            "h": h, "busy": _r(mb), "peak": int(bh[h].max()), "idle": _r(mi), "away": _r(ma),
            "active": active,
            "util_fleet": _r(mb / fleet * 100) if fleet else None,
            "util_active": _r(mb / active * 100) if active else None,
            "util_available": _r(mb / (mb + mi) * 100) if mb + mi > 0 else None,
        })

    def full_mean(arr):
        return float(np.mean([arr[h].mean() for h in full])) if full else None

    fb, fi, fa = full_mean(bh), full_mean(ih), full_mean(ah)

    # Where the idle robot-time goes, by gap length (gaps ending in full hours).
    gf = g[g["next"].dt.hour.isin(full) & (g["next"] >= day)] if full else g.iloc[0:0]
    bands = []
    total = float(gf["gap"].sum()) or 1.0
    for lo, hi, label in _GAP_BANDS:
        sel = gf[(gf["gap"] >= lo) & (gf["gap"] < hi)]
        bands.append({"band": label, "away": lo >= AWAY_MIN_S, "gaps": int(len(sel)),
                      "robot_hours": _r(sel["gap"].sum() / 3600), "share": _r(sel["gap"].sum() / total * 100)})
    peak_s = int(busy.argmax())
    return {
        "_idle": idle,          # per second; used by day_base, never sent
        "fleet": fleet,
        "busy5": [_r(x) for x in b5.mean(axis=1)],
        "peak5": [int(x) for x in b5.max(axis=1)],
        "idle5": [_r(x) for x in i5.mean(axis=1)],
        "away5": [_r(x) for x in a5.mean(axis=1)],
        "hourly": hourly,
        "gap_bands": bands,
        "day": {
            "busy_mean": _r(busy.mean()),
            "util_fleet_day": _r(busy.mean() / fleet * 100) if fleet else None,
            "busy_full": _r(fb), "idle_full": _r(fi), "away_full": _r(fa),
            "util_fleet_full": _r(fb / fleet * 100) if fleet and fb is not None else None,
            "util_available_full": _r(fb / (fb + fi) * 100) if fb is not None and fb + fi > 0 else None,
            "util_active_full": _r(float(np.mean([x["util_active"] for x in hourly
                                                  if x["h"] in full and x["util_active"] is not None])))
            if full else None,
            "away_share_of_idle": _r(fa / (fa + fi) * 100) if fa is not None and fa + fi > 0 else None,
            "median_gap_s": _r(gf["gap"].median()) if len(gf) else None,
            "peak": int(busy.max()),
            "peak_at": f"{peak_s // 3600:02d}:{peak_s % 3600 // 60:02d}",
            "robot_hours_on_task": _r(busy.sum() / 3600),
        },
    }


def _utilization(data: LogData, day: pd.Timestamp, full: list[int]) -> dict:
    k50, acr = data.robots(ROLE_K50), data.robots(ROLE_ACR)
    out = {}
    k_moves = pair_moves(data.tote_events, load_contains=BUFFER_K50, robots=k50)
    a_moves = data.moves[data.moves["robot"].isin(acr)]
    for role, mv in (("K50", k_moves), ("ACR", a_moves)):
        res = _robot_states(_busy_intervals(data, mv), day, full)
        if res:
            out[role] = res
    return out


# ── Task supply ───────────────────────────────────────────────────────────────

def _task_table(data: LogData) -> pd.DataFrame | None:
    """One row per created task: created, dest, ready (tote in the buffer),
    k50 (first K50 allocation), k50_station, acr_leg.

    The tote is ready when an ACR puts it into the buffer; a task whose tote was
    already there (no ACR leg) is ready the moment it is created.
    """
    c = data.created
    if c.empty:
        return None
    k50, acr = data.robots(ROLE_K50), data.robots(ROLE_ACR)
    te = data.tote_events
    put = te[(te["kind"] == "unload") & te["robot"].isin(acr)
             & te["loc"].str.contains(BUFFER_ACR, na=False) & te["task"].str.len().gt(0)]
    al = data.allocations
    ka = al[al["robot"].isin(k50)].sort_values("ts").groupby("task").agg(k50=("ts", "first"), k50_station=("station", "first"))
    t = c.drop_duplicates("task").set_index("task")
    t["ready"] = put.groupby("task")["ts"].min()
    t = t.join(ka)
    t["acr_leg"] = t["ready"].notna()
    t.loc[t["ready"].isna() & t["k50"].notna(), "ready"] = t["ts"]
    # With no ACR deliveries at all the column starts empty; keep it a datetime.
    for col in ("ready", "k50"):
        t[col] = pd.to_datetime(t[col])
    return t


def _task_flow(data: LogData, day: pd.Timestamp, full: list[int], stations: list[str]) -> dict | None:
    """How work reaches the K50s.

    Each ND task is created by the warehouse system, an ACR brings its tote
    from storage into the buffer (93% of tasks — the rest are already there),
    and only then is a K50 allocated.  The ACR's drop-off slot and the K50's
    pickup slot are the same buffer position, so the tote is *ready* the moment
    the ACR puts it down.

        waiting on supply   created → tote ready in the buffer
        ready for a K50     tote ready → K50 allocated

    A task that never completes a step (cancelled, or still open when the log
    ends) is left out of that step rather than counted as waiting forever.
    """
    t = _task_table(data)
    if t is None:
        return None

    sup = t.dropna(subset=["ready"])
    sup = sup[sup["ready"] >= sup["ts"]]
    rdy = t.dropna(subset=["ready", "k50"])
    rdy = rdy[rdy["k50"] >= rdy["ready"]]
    supply = _sweep(_day_seconds(sup["ts"], day), _day_seconds(sup["ready"], day))
    ready_w = _sweep(_day_seconds(rdy["ready"], day), _day_seconds(rdy["k50"], day))

    def in_hour(ts, h):
        return (ts >= day + pd.Timedelta(hours=h)) & (ts < day + pd.Timedelta(hours=h + 1))

    hourly = []
    for h in range(24):
        alloc_h = rdy[in_hour(rdy["k50"], h)]
        made_h = sup[in_hour(sup["ready"], h)]
        hourly.append({
            "h": h,
            "created": int(in_hour(t["ts"], h).sum()),
            "made_ready": int(len(made_h)),
            "k50_alloc": int(len(alloc_h)),
            "supply_wait": _r(supply[h * 3600:(h + 1) * 3600].mean()),
            "ready_wait": _r(ready_w[h * 3600:(h + 1) * 3600].mean()),
            "ready_to_alloc_med": _r((alloc_h["k50"] - alloc_h["ready"]).dt.total_seconds().median()),
            "created_to_ready_med": _r((made_h["ready"] - made_h["ts"]).dt.total_seconds().median()),
        })

    # Per destination station, over the full-production hours.
    span = len(full) * 3600.0
    per_station = []
    for st in stations:
        r_st = rdy[rdy["dest"] == st]
        if r_st.empty:
            continue
        w = (r_st["k50"] - r_st["ready"]).dt.total_seconds()
        in_full = r_st[r_st["k50"].dt.hour.isin(full) & (r_st["k50"] >= day) & (r_st["k50"] < day + DAY)]
        wf = (in_full["k50"] - in_full["ready"]).dt.total_seconds()
        backlog = None
        if full:
            s0, s1 = _day_seconds(r_st["ready"], day), _day_seconds(r_st["k50"], day)
            series = _sweep(s0, s1)
            backlog = _r(sum(series[h * 3600:(h + 1) * 3600].sum() for h in full) / span)
        per_station.append({
            "station": st, "tasks": int(len(in_full)),
            "ready_to_alloc_med": _r(wf.median()) if len(wf) else None,
            "ready_to_alloc_p90": _r(wf.quantile(.9)) if len(wf) else None,
            "ready_backlog": backlog,
        })

    full_mean = (lambda arr: _r(float(np.mean([arr[h * 3600:(h + 1) * 3600].mean() for h in full])))
                 if full else None)
    day_c = t[(t["ts"] >= day) & (t["ts"] < day + DAY)]
    day_r = rdy[(rdy["k50"] >= day) & (rdy["k50"] < day + DAY)]
    # The ACR leg's duration only means something for tasks that had one.
    day_s = sup[(sup["ready"] >= day) & (sup["ready"] < day + DAY) & sup["acr_leg"]]
    return {
        "hourly": hourly,
        "supply5": [_r(x) for x in supply.reshape(288, 300).mean(axis=1)],
        "ready5": [_r(x) for x in ready_w.reshape(288, 300).mean(axis=1)],
        "stations": per_station,
        "day": {
            "created": int(len(day_c)),
            "acr_leg_pct": _r(day_c["acr_leg"].mean() * 100) if len(day_c) else None,
            "ready_wait_full": full_mean(ready_w),
            "supply_wait_full": full_mean(supply),
            "ready_to_alloc_med": _r((day_r["k50"] - day_r["ready"]).dt.total_seconds().median()),
            "ready_to_alloc_p90": _r((day_r["k50"] - day_r["ready"]).dt.total_seconds().quantile(.9)),
            "created_to_ready_med": _r((day_s["ready"] - day_s["ts"]).dt.total_seconds().median()),
        },
    }


# ── Station slots ─────────────────────────────────────────────────────────────

# The task-count level a station holds for at least this share of production
# time counts as its slot limit — provided it is almost never exceeded, and
# work queues up behind it.  A steady station also sits at one level most of
# the time; what marks a cap is that its ready totes pile up while it is there.
_LIMIT_MIN_SHARE = 0.10
_LIMIT_MAX_OVER = 0.02
_LIMIT_HOLD_RATIO = 1.5


def _alloc_trips(data: LogData) -> pd.DataFrame | None:
    """Each K50 allocation to a station paired with that robot's arrival and
    release there: t_alloc, t_arr, t_rel, station, robot."""
    al = data.allocations
    if al.empty or "station" not in al.columns:
        return None
    k50 = data.robots(ROLE_K50)
    ka = al[al["robot"].isin(k50) & al["station"].str.startswith("LABOR", na=False)]
    if ka.empty:
        return None
    arr = data.arrivals.rename(columns={"ts": "t_arr"})[["t_arr", "station", "robot"]].sort_values("t_arr")
    rel = data.releases.rename(columns={"ts": "t_rel"})[["t_rel", "station", "robot"]].sort_values("t_rel")
    m = pd.merge_asof(ka.rename(columns={"ts": "t_alloc"}).sort_values("t_alloc"), arr,
                      left_on="t_alloc", right_on="t_arr", by=["robot", "station"], direction="forward")
    m = m.dropna(subset=["t_arr"])
    m = pd.merge_asof(m.sort_values("t_arr"), rel, left_on="t_arr", right_on="t_rel",
                      by=["robot", "station"], direction="forward").dropna(subset=["t_rel"])
    # An allocation more than an hour before the arrival is not this trip.
    return m[(m["t_arr"] - m["t_alloc"]).dt.total_seconds() < 3600]


def _ready_totes(tt: pd.DataFrame | None) -> pd.DataFrame:
    """Totes sitting ready in the buffer for a station: ready → K50 allocated."""
    if tt is None:
        return pd.DataFrame(columns=["ready", "t_k", "station"])
    w = tt.dropna(subset=["ready", "k50"]).rename(columns={"k50": "t_k"})
    w = w[w["t_k"] >= w["ready"]].copy()
    w["station"] = w["k50_station"].fillna(w["dest"])
    return w


def _open_at(start: np.ndarray, end: np.ndarray, t: np.ndarray) -> np.ndarray:
    """How many [start, end) intervals are open at each moment of *t* (all sorted-free)."""
    return (np.searchsorted(np.sort(start), t, side="right")
            - np.searchsorted(np.sort(end), t, side="right"))


def _station_slots(data: LogData, day: pd.Timestamp, full: list[int],
                   stations: list[str]) -> dict | None:
    """Tasks assigned to each station at once, and whether that hits a limit.

    A task is assigned to a station from the moment a K50 is allocated to it
    (the allocation names the destination station) until that robot is released
    there.  If the dispatcher only allocates when the station has a free slot,
    the count piles up against a ceiling and ready totes for that station wait
    in the buffer while it is full — which is what this measures.
    """
    m = _alloc_trips(data)
    if m is None:
        return None
    # Ready totes per destination: tote in the buffer → K50 allocated.
    w = _ready_totes(_task_table(data))

    mask = np.isin(np.arange(86400) // 3600, full) if full else np.zeros(86400, dtype=bool)
    out = []
    for st in stations:
        x = m[m["station"] == st]
        if x.empty:
            continue
        assigned = _sweep(_day_seconds(x["t_alloc"], day), _day_seconds(x["t_rel"], day))
        rw = w[w["station"] == st]
        ready_s = _sweep(_day_seconds(rw["ready"], day), _day_seconds(rw["t_k"], day))

        row = {"station": st,
               "assigned5": [_r(v) for v in assigned.reshape(288, 300).mean(axis=1)],
               "ready5": [_r(v) for v in ready_s.reshape(288, 300).mean(axis=1)],
               "limit": None, "at_limit_pct": None}
        if mask.any():
            c = assigned[mask].astype(int)
            vals, cnt = np.unique(c, return_counts=True)
            share = cnt / cnt.sum()
            held = [v for v, sh in zip(vals, share) if sh >= _LIMIT_MIN_SHARE and v >= 2]
            if held:
                top = int(max(held))
                over = float(share[vals > top + 1].sum())
                at = c >= top
                r_full = ready_s[mask]
                r_at = float(r_full[at].mean()) if at.any() else 0.0
                r_below = float(r_full[~at].mean()) if (~at).any() else 0.0
                held_back = r_at >= 1.0 and r_at >= _LIMIT_HOLD_RATIO * max(r_below, 0.1)
                row["level"] = top
                row["ready_at_limit"], row["ready_below_limit"] = _r(r_at), _r(r_below)
                if over < _LIMIT_MAX_OVER and held_back:
                    row["limit"] = top
                    row["at_limit_pct"] = _r(at.mean() * 100)
            row["mean_assigned"] = _r(c.mean())
            row["p90_assigned"] = _r(np.percentile(c, 90))
            row["max_assigned"] = int(c.max())
            row["distribution"] = {int(v): _r(sh * 100) for v, sh in zip(vals, share) if sh >= 0.005}
        xf = x[x["t_alloc"].dt.hour.isin(full) & (x["t_alloc"] >= day) & (x["t_alloc"] < day + DAY)] if full else x
        lead = (xf["t_rel"] - xf["t_alloc"]).dt.total_seconds()
        row["lead_med"] = _r(lead.median()) if len(lead) else None
        row["to_arrive_med"] = _r((xf["t_arr"] - xf["t_alloc"]).dt.total_seconds().median()) if len(xf) else None
        out.append(row)
    return {"stations": out} if out else None


# ── Rack and buffer locations ─────────────────────────────────────────────────

# Storage and buffer slots are coded HAI-<aisle>-<bay>-<level>_<depth>[_buffer].
_RACK_RE = r"^HAI-(\d+)-(\d+)-(\d+)_\d+"
# An allocation further ahead of the pickup than this is not the same trip.
_SPATIAL_LEAD_MAX_S = 1800.0
# An ACR load → unload longer than this was interrupted, not handled.
_HANDLE_MAX_S = 300.0
# A K50 pickup → first arrival longer than this was not a straight trip.
_TRAVEL_MAX_S = 900.0
# Robots heading to the same aisle at once: 0, 1, 2 and the last collects the rest.
_CROWD_BINS = 4
# A tote stored and taken out again: minutes until it was, in these bands.
_RETURN_BANDS_MIN = (5, 15, 30, 60, 240)
# Totes by puts on the day: 1, 2, 3, 4, 5–9, 10+.
_REPEAT_EDGES = (1, 2, 3, 4, 5, 10)


def _rack(loc: pd.Series) -> pd.DataFrame:
    """aisle, bay, level of each location code (NaN where it is not a rack slot)."""
    p = loc.astype(str).str.extract(_RACK_RE)
    p.columns = ["aisle", "bay", "level"]
    return p.apply(pd.to_numeric, errors="coerce")


def _with_alloc(moves: pd.DataFrame, data: LogData, robots: set[str]) -> pd.DataFrame:
    """Each move with its robot's latest allocation to that task before the
    pickup (t_alloc) and the lead, allocation → pickup, in seconds."""
    al = data.allocations
    al = al[al["robot"].isin(robots)].rename(columns={"ts": "t_alloc"})[["t_alloc", "robot", "task"]]
    if moves.empty or al.empty:
        return moves.iloc[0:0].assign(t_alloc=pd.NaT, lead=np.nan)
    m = pd.merge_asof(moves.sort_values("t_load"), al.sort_values("t_alloc"),
                      left_on="t_load", right_on="t_alloc", by=["robot", "task"], direction="backward")
    m = m.dropna(subset=["t_alloc"])
    m["lead"] = (m["t_load"] - m["t_alloc"]).dt.total_seconds()
    return m[m["lead"].between(0, _SPATIAL_LEAD_MAX_S, inclusive="left")].reset_index(drop=True)


def _crowding(trips: pd.DataFrame, day: pd.Timestamp) -> dict | None:
    """Does a robot reach its pickup later when others are heading to the same aisle?

    For each trip, at its allocation: *fleet* — the fleet's other trips between
    allocation and pickup; *same* — those of them bound for the same aisle.
    Busy periods slow every trip, so each lead is compared with the median lead
    of all trips made at the same fleet count; what is left (*excess*) is what
    the aisle adds.  *expected* is the same-aisle count if robots chose aisles
    independently: fleet × Σ (aisle share)².
    """
    if trips.empty:
        return None
    s = trips["t_alloc"].to_numpy("datetime64[ns]").astype("int64")
    e = trips["t_load"].to_numpy("datetime64[ns]").astype("int64")
    fleet = np.clip(_open_at(s, e, s) - 1, 0, None)
    same = np.zeros(len(trips), dtype=int)
    for _, idx in trips.groupby("aisle").indices.items():
        same[idx] = np.clip(_open_at(s[idx], e[idx], s[idx]) - 1, 0, None)
    t = trips.assign(fleet=fleet, same=same)
    t["excess"] = t["lead"] - t.groupby("fleet")["lead"].transform("median")
    share = t["aisle"].value_counts(normalize=True)
    t["expected"] = t["fleet"] * float((share ** 2).sum())
    t = t[(t["t_alloc"] >= day) & (t["t_alloc"] < day + DAY)]
    if t.empty:
        return None
    band = t["same"].clip(upper=_CROWD_BINS - 1)
    return {
        "trips": int(len(t)), "aisles": int(t["aisle"].nunique()),
        "lead_s": _r(t["lead"].sum(), 0), "same_sum": int(t["same"].sum()),
        "expected_sum": _r(t["expected"].sum()), "fleet_sum": int(t["fleet"].sum()),
        "bins": [{"n": int((band == k).sum()), "lead_s": _r(t.loc[band == k, "lead"].sum(), 0),
                  "excess_s": _r(t.loc[band == k, "excess"].sum(), 0)} for k in range(_CROWD_BINS)],
    }


def _spatial(data: LogData, day: pd.Timestamp, cycles: pd.DataFrame | None) -> dict | None:
    """Where totes come from in the rack and the buffer, and whether it matters.

    1. sources    ACR puts (storage → kubot buffer) by aisle and level of the
                  storage slot they were taken from
    2. lift       ACR handling, load → unload, by level (higher slots take longer)
    3. crowding   allocation → pickup against robots bound for the same aisle,
                  K50s at the buffer and ACRs in storage (see _crowding)
    4. returns    totes stored and then taken out again, and how soon
    5. travel     K50 buffer pickup → first station, by the buffer aisle

    Sums and counts only, so engine.spatialStats() pools a day or the run.
    """
    acr, k50 = data.robots(ROLE_ACR), data.robots(ROLE_K50)
    on_day = lambda ts: (ts >= day) & (ts < day + DAY)                 # noqa: E731
    mv = data.moves
    mv = mv[mv["robot"].isin(acr)]
    src, dst = _rack(mv["from_loc"]), _rack(mv["to_loc"])
    from_store = src["aisle"].notna() & ~mv["from_loc"].str.contains("coop", na=False)
    to_store = dst["aisle"].notna() & ~mv["to_loc"].str.contains("coop", na=False)
    puts = mv[from_store & mv["to_loc"].str.contains(BUFFER_ACR, na=False)].join(src)
    stores = mv[mv["from_loc"].str.contains(BUFFER_ACR, na=False) & to_store]
    puts_day = puts[on_day(puts["t_load"])]
    if puts_day.empty:
        return None

    aisles = sorted(int(a) for a in puts_day["aisle"].unique())
    levels = sorted(int(x) for x in puts_day["level"].unique())
    grid = puts_day.groupby(["level", "aisle"]).size()
    out: dict = {
        "puts": int(len(puts_day)),
        "aisles": aisles, "levels": levels,
        # puts[level][aisle], levels and aisles in the order above
        "grid": [[int(grid.get((lv, a), 0)) for a in aisles] for lv in levels],
        "locations": int(puts_day["from_loc"].nunique()),
        "limits": {"lead_max_s": _SPATIAL_LEAD_MAX_S, "handle_max_s": _HANDLE_MAX_S, "travel_max_s": _TRAVEL_MAX_S},
    }

    # 2. lift: handling seconds by level
    hd = puts_day.assign(s=(puts_day["t_unload"] - puts_day["t_load"]).dt.total_seconds())
    hd = hd[hd["s"] <= _HANDLE_MAX_S]
    out["lift"] = [{"level": int(lv), "n": int(len(g)), "handle_s": _r(g["s"].sum(), 0)}
                   for lv, g in hd.groupby("level")]

    # 3. crowding
    crowd = {}
    acr_trips = _with_alloc(puts.reset_index(drop=True), data, acr)
    if not acr_trips.empty:
        crowd["ACR"] = _crowding(acr_trips, day)
    if cycles is not None and not cycles.empty:
        kt = cycles.assign(aisle=_rack(cycles["from_loc"])["aisle"]).dropna(subset=["aisle"])
        k_trips = _with_alloc(kt.reset_index(drop=True), data, k50)
        if not k_trips.empty:
            crowd["K50"] = _crowding(k_trips, day)
    out["crowd"] = {k: v for k, v in crowd.items() if v}

    # 4. returns: each store paired with that tote's next put
    st = stores[on_day(stores["t_unload"])].sort_values("t_unload")
    nxt = puts.rename(columns={"t_load": "t_next", "from_loc": "next_loc"})[["tote", "t_next", "next_loc"]].sort_values("t_next")
    r = pd.merge_asof(st[["tote", "t_unload", "to_loc"]], nxt, left_on="t_unload", right_on="t_next",
                      by="tote", direction="forward")
    back = (r["t_next"] - r["t_unload"]).dt.total_seconds() / 60
    edges = (0,) + _RETURN_BANDS_MIN + (np.inf,)
    out["returns"] = {
        "stores": int(len(r)),
        "bands_min": list(_RETURN_BANDS_MIN),
        # stores re-taken within each band, then later, then not again in the log
        "back": [int(((back >= lo) & (back < hi)).sum()) for lo, hi in zip(edges[:-1], edges[1:])]
                + [int(back.isna().sum())],
        "same_slot": int((r["to_loc"] == r["next_loc"]).sum()),
    }
    per_tote = puts_day["tote"].value_counts()
    rep_edges = _REPEAT_EDGES + (np.inf,)
    out["repeats"] = {
        "labels": ["1", "2", "3", "4", "5–9", "10+"],
        "totes": [int(((per_tote >= lo) & (per_tote < hi)).sum()) for lo, hi in zip(rep_edges[:-1], rep_edges[1:])],
        "puts": [int(per_tote[(per_tote >= lo) & (per_tote < hi)].sum()) for lo, hi in zip(rep_edges[:-1], rep_edges[1:])],
    }

    # 5. travel: K50 buffer pickup → first station arrival, by buffer aisle
    if cycles is not None and not cycles.empty:
        c = cycles[on_day(cycles["t_load"]) & (cycles["n_st"] > 0)]
        c = c.assign(aisle=_rack(c["from_loc"])["aisle"]).dropna(subset=["aisle"])
        arr = data.arrivals.rename(columns={"ts": "t_arr"})[["t_arr", "robot", "station"]].sort_values("t_arr")
        t = pd.merge_asof(c.sort_values("t_load"), arr, left_on="t_load", right_on="t_arr",
                          by="robot", direction="forward").dropna(subset=["t_arr"])
        t["s"] = (t["t_arr"] - t["t_load"]).dt.total_seconds()
        t = t[(t["t_arr"] <= t["t_unload"]) & (t["s"] <= _TRAVEL_MAX_S)]
        if not t.empty:
            ta = sorted(int(a) for a in t["aisle"].unique())
            ts = sorted(t["station"].unique(), key=natural_key)
            g = t.groupby(["station", "aisle"])["s"].agg(["size", "sum"])
            out["travel"] = {
                "aisles": ta, "stations": ts,
                "n": [[int(g["size"].get((s_, a), 0)) for a in ta] for s_ in ts],
                "sum_s": [[_r(g["sum"].get((s_, a), 0), 0) for a in ta] for s_ in ts],
            }
    return out


def _full_hours(hourly: list[dict] | None) -> list[int]:
    """Hours whose K50 cycles reached FULL_HOUR_SHARE of the busiest hour."""
    if not hourly:
        return []
    peak = max(h["k50_cycles"] for h in hourly)
    if peak <= 0:
        return []
    return [h["h"] for h in hourly if h["k50_cycles"] >= FULL_HOUR_SHARE * peak]


def _idle_window(arrivals: pd.Series, day: pd.Timestamp) -> list[str] | None:
    """The longest stretch with no arrival anywhere, if it is a real stand-down
    inside the working day (leading/trailing quiet time does not count)."""
    if arrivals.empty:
        return None
    mins = ((arrivals - day).dt.total_seconds() // 60).astype(int)
    mins = mins[(mins >= 0) & (mins < 1440)]
    if mins.empty:
        return None
    busy = np.zeros(1440, dtype=bool)
    busy[mins.to_numpy()] = True

    best_run = best_start = run = start = 0
    for i, b in enumerate(busy):
        if b:
            run = 0
            continue
        if run == 0:
            start = i
        run += 1
        if run > best_run:
            best_run, best_start = run, start

    first, last = int(np.argmax(busy)), int(1439 - np.argmax(busy[::-1]))
    if best_run < IDLE_MIN_MINUTES or best_start <= first or best_start + best_run - 1 >= last:
        return None
    fmt = lambda m: f"{m // 60:02d}:{m % 60:02d}"
    return [fmt(best_start), fmt(best_start + best_run)]


# ── Public API ────────────────────────────────────────────────────────────────

def day_base(data: LogData, starved_s: float = STARVED_S_DEFAULT) -> dict:
    """The door- and target-independent part of one day, as a JSON-able dict.

    *starved_s*: seconds beyond a station's median handover that count as the
    station starved for a robot (Settings.starved_s)."""
    day = data.date
    v, n_arrivals, n_unpaired = visits(data, day)
    if v.empty:
        raise ValueError(
            "No arrival could be paired with a release, so there is nothing to measure. "
            "The log may be missing its \"will leave\" lines.")
    stations = sorted(set(v["station"]), key=natural_key)
    points = station_points(data.arrivals)

    m = {
        "date": day.strftime("%Y-%m-%d"),
        "source": data.source,
        "files": [p.replace("\\", "/").rsplit("/", 1)[-1] for p in data.paths],
        "stations": stations,
        "points": {s: list(points[s]) for s in stations if s in points},
        "n_arrivals": n_arrivals,
        "n_unpaired": n_unpaired,
        "skipped_stations": data.skipped_stations,
        "idle": _idle_window(
            data.arrivals.loc[(data.arrivals["ts"] >= day)
                              & (data.arrivals["ts"] < day + DAY), "ts"], day),
        **_station_block(v, day, stations),
    }
    cycles = k50_cycles(data) if not data.moves.empty else None
    cyc = _cycle_metrics(data, day, cycles) if cycles is not None else None
    if cyc:
        m.update(cyc)
    m["full_hours"] = _full_hours(m.get("hourly"))
    v_cyc, ho = handovers(data, v, cycles, starved_s)
    multi = _multi_station(v_cyc, ho, m["full_hours"])
    if multi:
        m["multi"] = multi
    m["utilization"] = _utilization(data, day, m["full_hours"])
    idle = {role: u.pop("_idle", None) for role, u in m["utilization"].items()}
    starve = _starvation(data, v_cyc, ho, day, m["full_hours"], stations, idle.get("K50"))
    if starve:
        m["starve"] = starve
    flow = _task_flow(data, day, m["full_hours"], stations)
    if flow:
        m["flow"] = flow
    slots = _station_slots(data, day, m["full_hours"], stations)
    if slots:
        m["slots"] = slots
    spatial = _spatial(data, day, cycles)
    if spatial:
        m["spatial"] = spatial

    # Presentations per hour over the full-production hours: the like-for-like
    # rate, since breaks and stand-downs would otherwise drag it down.
    full = set(m["full_hours"])
    for row in m["station_table"]:
        sub = v[(v["station"] == row["station"]) & v["hour"].isin(full)]
        row["rate_full"] = _r(len(sub) / len(full)) if full else None
    return m


def zones_for(bases: list[dict]) -> dict[str, str]:
    """One station → zone map for the whole run, from every day's points."""
    points: dict[str, tuple[int, int]] = {}
    stations: set[str] = set()
    for b in bases:
        stations |= set(b["stations"])
        for s, xy in b.get("points", {}).items():
            points.setdefault(s, tuple(xy))
    return assign_zones(points, sorted(stations, key=natural_key))
