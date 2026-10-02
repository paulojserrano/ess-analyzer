"""
tests/synthetic.py — realistic synthetic ESS exports for tests and demos.

Simulates one operational day of K50 deliveries to LABOR stations and writes
the same four sheets the Hairobotics export contains (callback, station,
lifecycle, efficiency).  The simulation is deterministic for a given seed.

Deliberate quirks that mirror real exports (each one guards a past bug):
  • a handful of events on the previous calendar day (export spans midnight);
  • the lifecycle '创建时间' column can be left completely blank;
  • the real station names (callback 位置类型) are NOT in coordinate order,
    so coordinate-based auto-naming would mislabel stations;
  • a few starvation gaps (station idle > 5 min) and lost 'arrived' events.

Run directly to write a demo workbook:
    python -m tests.synthetic out.xlsx
"""
from __future__ import annotations

import sys
from dataclasses import dataclass

import numpy as np
import pandas as pd

DAY = pd.Timestamp("2026-06-12")


@dataclass
class SynthOptions:
    seed: int = 7
    n_stations: int = 6
    n_robots: int = 40
    start_hour: int = 6
    end_hour: int = 14
    create_time_fill: float = 0.8    # share of tasks with 创建时间 populated
    include_prev_day: bool = True    # a few events before midnight
    drop_arrived_frac: float = 0.01  # lost 'arrived' events
    include_efficiency: bool = True
    include_lifecycle: bool = True
    day: pd.Timestamp = DAY


def _station_points(n: int, rng: np.random.Generator) -> tuple[list[str], dict[str, str]]:
    """Point codes in two rows plus a shuffled real-name mapping."""
    points = []
    for i in range(n):
        y = 161371 if i < (n + 1) // 2 else 170500
        x = 101000 + (i % ((n + 1) // 2)) * 2500
        points.append(f"LT_LABOR:POINT:{x}:{y}")
    names = [f"LABOR-{i + 1}" for i in range(n)]
    rng.shuffle(names)
    return points, dict(zip(points, names))


def generate(opts: SynthOptions | None = None) -> dict[str, pd.DataFrame]:
    o = opts or SynthOptions()
    rng = np.random.default_rng(o.seed)
    points, real_name = _station_points(o.n_stations, rng)
    robots = [f"kubot-{i}" for i in range(1, o.n_robots + 1)]
    robot_free = {r: o.day - pd.Timedelta(hours=1) for r in robots}

    st_rows, cb_rows, lc_rows = [], [], []
    task_no = 517000
    totes = [f"A{n:09d}" for n in range(70000, 70000 + 900)]
    tote_w = 1.0 / np.arange(1, len(totes) + 1) ** 0.9
    tote_w /= tote_w.sum()

    def ts(t: pd.Timestamp) -> str:
        return t.strftime("%Y-%m-%d %H:%M:%S")

    start = o.day + pd.Timedelta(hours=o.start_hour)
    end = o.day + pd.Timedelta(hours=o.end_hour)
    if o.include_prev_day:
        start_prev = o.day - pd.Timedelta(minutes=12)
    # Event-driven: always advance the station whose next arrival is earliest,
    # so robot availability is shared realistically across stations.
    next_t: dict[int, pd.Timestamp] = {}
    for si in range(len(points)):
        base = start_prev if (o.include_prev_day and si == 0) else start
        next_t[si] = base + pd.Timedelta(seconds=float(rng.uniform(0, 120)))
    while next_t:
        si = min(next_t, key=next_t.get)
        t = next_t[si]
        if t >= end:
            del next_t[si]
            continue
        pt = points[si]
        st_name = real_name[pt]
        pick_mu = 3.1 + 0.08 * si           # stations differ slightly
        if True:
            # Starvation episodes / breaks
            if rng.random() < 0.006:
                t += pd.Timedelta(minutes=float(rng.uniform(8, 35)))
            if o.include_prev_day and si == 0 and o.day <= t < start:
                t = start   # the pre-midnight burst ends; resume at shift start
            free = [r for r in robots if robot_free[r] <= t]
            if not free:
                next_t[si] = max(t, min(robot_free.values()))
                continue
            rb = free[int(rng.integers(len(free)))]
            arrived = t
            pick_s = float(np.clip(rng.lognormal(pick_mu, 0.35), 4, 400))
            trigger = arrived + pd.Timedelta(seconds=round(pick_s))
            release = trigger + pd.Timedelta(seconds=1)
            pp_offset = float(rng.normal(-6, 5))  # mostly before arrival
            ppready = arrived + pd.Timedelta(seconds=round(pp_offset))

            if rng.random() >= o.drop_arrived_frac:
                st_rows.append((ts(arrived), "arrived", pt, rb, "K50"))
            st_rows.append((ts(ppready), "ppReady", None, rb, "K50"))
            st_rows.append((ts(trigger), "triggerGo", pt, rb, "K50"))
            st_rows.append((ts(release), "release", pt, rb, "K50"))

            task_no += 1
            tid = f"ND{task_no:010d}"
            tote = totes[int(rng.choice(len(totes), p=tote_w))]
            cb_rows.append((ts(trigger), "complete", "DEFAULT_GROUP", tid, rb, tote,
                            pt, st_name, str(10**18 + task_no), "K50"))
            k50_leg = float(rng.uniform(40, 140)) + pick_s
            a42_put = float(rng.uniform(10, 25))
            a42_get = float(rng.uniform(25, 70))
            put_t = trigger - pd.Timedelta(seconds=round(k50_leg))
            get_t = put_t - pd.Timedelta(seconds=round(a42_put))
            alloc_t = get_t - pd.Timedelta(seconds=round(a42_get))
            alloc_wait = float(rng.exponential(25))
            create_t = alloc_t - pd.Timedelta(seconds=round(alloc_wait))
            has_create = rng.random() < o.create_time_fill
            aisle = int(rng.integers(1, 25))
            bay = int(rng.integers(1, 60))
            lvl = int(rng.integers(1, 12))
            cb_rows.append((ts(get_t), "unload", "DEFAULT_GROUP", tid, f"haipick-{aisle}",
                            tote, f"HAI-{aisle:03d}-{bay:03d}-{lvl:02d}_1",
                            "LA_SHELF_STORAGE", str(2 * 10**18 + task_no), "A42"))
            if rng.random() < 0.15:
                rid = f"return:task-{task_no}"
                cb_rows.append((ts(release + pd.Timedelta(seconds=30)), "complete",
                                "DEFAULT_GROUP", rid, rb, tote, pt, st_name,
                                str(3 * 10**18 + task_no), "K50"))
            if o.include_lifecycle:
                pick_done = trigger + pd.Timedelta(seconds=int(rng.integers(3, 15)))
                lc_rows.append({
                    "任务编号": tid,
                    "创建时间": ts(create_t) if has_create else None,
                    "分配时间": ts(alloc_t),
                    "取箱时间": ts(get_t),
                    "放箱时间": ts(put_t),
                    "complete(任务完成时间)": ts(trigger),
                    "分配耗时(秒)": round(alloc_wait) if has_create else None,
                    "A42取箱耗时(秒)": round(a42_get),
                    "A42放箱耗时(秒)": round(a42_put),
                    "K50完成耗时(秒)": round(k50_leg),
                    "任务全程耗时(秒)": round(a42_get) + round(a42_put) + round(k50_leg),
                    "机器人编号": rb,
                    "容器编号": tote,
                    "任务组编号": "DEFAULT_GROUP",
                    "起始位置": f"HAI-{aisle:03d}-{bay:03d}-{lvl:02d}_1",
                    "目标位置": st_name,
                    "初始位置类型": "storage",
                    "拣选完成时间(finishPicking)": ts(pick_done),
                    "拣选耗时(秒)": int(round(pick_s + rng.normal(2, 1.5))),
                })
            robot_free[rb] = release + pd.Timedelta(seconds=float(rng.uniform(90, 300)))
            switch = float(rng.lognormal(1.9, 0.4))
            next_t[si] = release + pd.Timedelta(seconds=round(switch))

    station = pd.DataFrame(st_rows, columns=["时间戳", "事件类型", "位置编号", "机器人编号", "机器人类型"])
    station = station.sort_values("时间戳", kind="stable").reset_index(drop=True)
    callback = pd.DataFrame(cb_rows, columns=[
        "时间戳", "动作类型", "任务组编号", "任务编号", "机器人编号", "容器编号",
        "位置编号", "位置类型", "回调ID", "机器人类型"])
    callback = callback.sort_values("时间戳", kind="stable").reset_index(drop=True)
    out: dict[str, pd.DataFrame] = {"callback": callback, "station": station}
    if o.include_lifecycle:
        out["lifecycle"] = pd.DataFrame(lc_rows)
    if o.include_efficiency:
        hours = pd.date_range(o.day + pd.Timedelta(hours=o.start_hour),
                              o.day + pd.Timedelta(hours=o.end_hour - 1), freq="h")
        out["efficiency"] = pd.DataFrame({
            "自然小时": [h.strftime("%Y-%m-%d %H:%M") for h in hours],
            "A42下移次数": rng.integers(300, 500, len(hours)),
            "K50平移次数": rng.integers(280, 480, len(hours)),
            "上游创建数量": rng.integers(300, 520, len(hours)),
            "系统总目标出库效率(TPH)": 600,
            "效率瓶颈": rng.choice(["Upstream", "K50", "Labor"], len(hours)),
        })
    return out


SHEET_NAMES = {
    "callback": "回调明细",
    "station": "labor_station_record",
    "lifecycle": "任务生命周期",
    "efficiency": "HPS3效率对比",
}


def write_workbook(path: str, opts: SynthOptions | None = None) -> str:
    sheets = generate(opts)
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        for key, df in sheets.items():
            df.to_excel(w, sheet_name=SHEET_NAMES[key], index=False)
    return path


if __name__ == "__main__":
    write_workbook(sys.argv[1] if len(sys.argv) > 1 else "synthetic_ess.xlsx")
