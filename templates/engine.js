/*
 * engine.js — the report's live calculations.
 *
 * Everything that depends on a setting the reader can change in the report —
 * door seconds and station targets — is computed here, from the exact,
 * door-independent numbers metrics.py prepared.  Pure functions only: no DOM,
 * so the same file runs in the page and under Node for the tests.
 *
 * The door model
 *   switch   = measured release→arrival gap + door
 *   picking  = time at the station − door   (the door is still opening)
 *   waiting  = unchanged
 * Stations without a door (settings.no_door) get none of this: their switch
 * and picks stay exactly as logged.
 * The door seconds move from picking to the switch, so a station's hour never
 * counts them twice and the budget still adds up to the hour.  Gaps and
 * operator times arrive pre-sorted in deciseconds; adding the door shifts every
 * gap by the same amount without changing the order, so any percentile or
 * threshold reads straight off the sorted arrays.
 */
(function (root) {
  "use strict";

  // ── array helpers ────────────────────────────────────────────────────────
  /** First index whose value is >= x. */
  function lowerBound(a, x) {
    let lo = 0, hi = a.length;
    while (lo < hi) { const m = (lo + hi) >> 1; if (a[m] < x) lo = m + 1; else hi = m; }
    return lo;
  }
  /** First index whose value is > x. */
  function upperBound(a, x) {
    let lo = 0, hi = a.length;
    while (lo < hi) { const m = (lo + hi) >> 1; if (a[m] <= x) lo = m + 1; else hi = m; }
    return lo;
  }
  /** Linear-interpolated quantile of a sorted array (pandas' default). */
  function quantile(a, p) {
    if (!a.length) return null;
    const pos = (a.length - 1) * p, lo = Math.floor(pos), hi = Math.ceil(pos);
    return a[lo] + (a[hi] - a[lo]) * (pos - lo);
  }
  function mergeSorted(arrays) {
    const out = [];
    for (const a of arrays) for (const v of a) out.push(v);
    return out.sort((x, y) => x - y);
  }
  function prefix(a) {
    const p = new Float64Array(a.length + 1);
    for (let i = 0; i < a.length; i++) p[i + 1] = p[i] + a[i];
    return p;
  }
  const R24 = Array.from({ length: 24 }, (_, h) => h);
  const r = (v, nd = 1) => (v == null || !isFinite(v) ? null : Math.round(v * 10 ** nd) / 10 ** nd);
  const mean = (xs) => { const v = xs.filter((x) => x != null); return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null; };
  const median = (xs) => { const v = xs.filter((x) => x != null).sort((a, b) => a - b); return v.length ? quantile(v, 0.5) : null; };
  function corr(xs, ys) {
    const n = xs.length;
    if (n < 3) return null;
    const mx = xs.reduce((a, b) => a + b, 0) / n, my = ys.reduce((a, b) => a + b, 0) / n;
    let sxy = 0, sxx = 0, syy = 0;
    for (let i = 0; i < n; i++) { const dx = xs[i] - mx, dy = ys[i] - my; sxy += dx * dy; sxx += dx * dx; syy += dy * dy; }
    return sxx && syy ? sxy / Math.sqrt(sxx * syy) : null;
  }
  const natural = (a, b) => String(a).localeCompare(String(b), undefined, { numeric: true });

  // ── deciseconds arrays ───────────────────────────────────────────────────
  /** Σ min(door, op) over a sorted op array — the door seconds inside the visits. */
  function doorSeconds(op, pre, door) {
    if (!op.length || door <= 0) return 0;
    const k = lowerBound(op, door * 10);
    return pre[k] / 10 + door * (op.length - k);
  }
  /** Σ clamp(op − door, 0, cap) — picks with the door removed, capped at `cap`. */
  function cappedPickSeconds(op, pre, door, cap) {
    if (!op.length || cap <= 0) return 0;
    const i0 = upperBound(op, door * 10);
    const i1 = lowerBound(op, (door + cap) * 10);
    return (pre[i1] - pre[i0]) / 10 - door * (i1 - i0) + cap * (op.length - i1);
  }

  /** Build the per-day lookups once; settings changes reuse them. */
  function prepare(day) {
    if (day._p) return day._p;
    const S = day.stations.length;
    const p = { gapStation: [], gapSum: [], gapAll: null, opPre: [], opStation: [], opStationPre: [], zoneGap: {} };
    for (let s = 0; s < S; s++) {
      p.gapStation.push(mergeSorted(day.raw.gap[s]));
      p.gapSum.push(p.gapStation[s].reduce((a, b) => a + b, 0));     // deciseconds, for the mean
      p.opPre.push(day.raw.op[s].map(prefix));
    }
    p.gapAll = mergeSorted(p.gapStation);
    Object.defineProperty(day, "_p", { value: p, enumerable: false });
    return p;
  }

  // ── targets ──────────────────────────────────────────────────────────────
  /**
   * The share of the hour a station must be pickable to hit its target.
   *
   * At `target` totes an hour each tote has 3600 / target seconds.  The switch
   * (door included) takes its median whatever the operator does, so at best the
   * rest is pickable: (3600/target − switch) / (3600/target) = 1 − target ×
   * switch / 3600.  Below this, the station cannot reach target even with no
   * waiting at all.
   */
  function targetUtil(target, switchS) {
    if (!target || switchS == null) return null;
    return r(Math.max(0, 1 - (target * switchS) / 3600) * 100);
  }
  /** A station's setting: its own entry, else its zone's; 0 / blank = none. */
  function targetFor(station, zones, targets) {
    const map = targets || {};
    const own = map[station];
    if (own !== undefined && own !== null && own !== "") return Number(own) > 0 ? Number(own) : null;
    const zone = map[zones[station]];
    if (zone !== undefined && zone !== null && zone !== "") return Number(zone) > 0 ? Number(zone) : null;
    return null;
  }

  /**
   * The three per-station targets, with the defaults filled in.
   *   rate    totes/h — explicit only (no target = not in the budget)
   *   switch  s — explicit, else the station's measured median switch (door included)
   *   pick    s — explicit, else whatever the budget leaves after the switch
   *           (3600 / rate − switch), i.e. no allowance for waiting
   * Whatever is left of the budget after the target pick and switch is the
   * waiting the station can absorb and still make rate.
   */
  function stationTargets(station, zones, settings, swMed) {
    const rate = targetFor(station, zones, settings.targets);
    const swSet = targetFor(station, zones, settings.switch_s);
    const pickSet = targetFor(station, zones, settings.pick_s);
    const sw = swSet != null ? swSet : swMed;
    const budget = rate ? 3600 / rate : null;
    const pick = pickSet != null ? pickSet : budget != null && sw != null ? Math.max(budget - sw, 0) : null;
    return {
      rate, budget, switch: sw, pick,
      switch_from: swSet != null ? "set" : "measured", pick_from: pickSet != null ? "set" : budget != null ? "budget" : null,
      allowance: budget != null && pick != null && sw != null ? budget - pick - sw : null,
    };
  }

  /**
   * Does this station have a door?  no_door maps a station or zone to true (no
   * door) or false (has one).  The station's own entry wins; with none, its
   * zone's; with neither, it has a door.
   */
  function hasDoor(station, zones, settings) {
    const off = settings.no_door || {};
    if (off[station] === true) return false;
    if (off[station] === false) return true;
    return off[zones[station]] !== true;
  }

  /**
   * Switch gaps (deciseconds) across several stations, each shifted by its own
   * door, merged and sorted.  With every station on the same door this is a
   * plain shift of the merged array; mixed doors need a real merge, so the
   * result is cached per combination.
   */
  function shiftedGaps(p, idx, doorOf) {
    const key = idx.map((s) => s + ":" + doorOf[s]).join(",");
    p.shifted = p.shifted || {};
    if (!p.shifted[key]) {
      const out = [];
      for (const s of idx) { const d = Math.round(doorOf[s] * 10); for (const g of p.gapStation[s]) out.push(g + d); }
      p.shifted[key] = out.sort((a, b) => a - b);
    }
    return p.shifted[key];
  }

  // ── one day ──────────────────────────────────────────────────────────────
  function computeDay(day, settings, zones, C) {
    const p = prepare(day);
    // Doors can be switched off for whole days (no_door_days) — e.g. after the
    // doors were disabled on site.  On such a day nothing gets door seconds.
    const doorSet = Math.max(0, Number(settings.door_s) || 0);
    const dayOff = !!(settings.no_door_days || {})[day.date];
    const door = dayOff ? 0 : doorSet;
    const S = day.stations.length;
    const doorOf = day.stations.map((st) => (hasDoor(st, zones, settings) ? door : 0));
    const sw = (ds, d) => (ds == null ? null : ds / 10 + d);
    const gt10 = (a, d) => (a.length ? ((a.length - upperBound(a, (10 - d) * 10)) / a.length) * 100 : null);

    // switch — every station's gaps shifted by its own door
    const all = shiftedGaps(p, day.stations.map((_, s) => s), doorOf);
    // Mean switch: each station's gaps plus its own door (gaps are deciseconds).
    let gapSum = 0, gapN = 0;
    p.gapStation.forEach((a, s) => { gapSum += p.gapSum[s] / 10 + a.length * doorOf[s]; gapN += a.length; });
    const overall = {
      med: r(sw(quantile(all, 0.5), 0)), mean: gapN ? r(gapSum / gapN) : null, p25: r(sw(quantile(all, 0.25), 0)),
      p75: r(sw(quantile(all, 0.75), 0)), p90: r(sw(quantile(all, 0.9), 0)),
      gt10: r(gt10(all, 0)), n: all.length,
    };
    const hist = new Array(C.switch_hist_bins).fill(0);
    for (const g of all) {
      const i = Math.min(Math.floor(g / 10 / C.switch_hist_w), C.switch_hist_bins - 1);
      if (i >= 0) hist[i]++;
    }
    const stationSwitch = p.gapStation.map((a, s) => ({
      med: r(sw(quantile(a, 0.5), doorOf[s])), mean: a.length ? r(p.gapSum[s] / 10 / a.length + doorOf[s]) : null, p90: r(sw(quantile(a, 0.9), doorOf[s])), gt10: r(gt10(a, doorOf[s])),
    }));
    const hmSwMed = [], hmSwGt10 = [];
    for (let s = 0; s < S; s++) {
      hmSwMed.push(day.raw.gap[s].map((a) => (a.length ? r(sw(quantile(a, 0.5), doorOf[s])) : null)));
      hmSwGt10.push(day.raw.gap[s].map((a) => (a.length ? r(gt10(a, doorOf[s]), 0) : null)));
    }

    // the hour budget with the door moved from picking to the switch
    const pick = [], mech = [], wait = [], hmUtil = [], util = [];
    for (let s = 0; s < S; s++) {
      const pr = [], mr = [], wr = [], ur = [];
      let tot = 0;
      for (let h = 0; h < 24; h++) {
        const occ = day.budget0.occ[s][h], core = day.budget0.core[s][h];
        const doorS = Math.min(doorSeconds(day.raw.op[s][h], p.opPre[s][h], doorOf[s]), occ);
        pr.push(occ - doorS); mr.push(core + doorS); wr.push(day.budget0.wait[s][h]);
        ur.push(r((occ - doorS) / 36));
        tot += occ - doorS;
      }
      pick.push(pr); mech.push(mr); wait.push(wr); hmUtil.push(ur);
      util.push(r(tot / 864));
    }

    const slotRow = (st) => (day.slots ? day.slots.stations.find((x) => x.station === st) : null) || {};
    const stationRows = day.stations.map((st, s) => {
      const swMed = stationSwitch[s].med;
      const T = stationTargets(st, zones, settings, swMed);
      const sl = slotRow(st);
      return {
        ...day.station_table[s],
        zone: zones[st] || st, door: doorOf[s] > 0,
        target: T.rate, target_pick: T.pick, target_switch: T.switch, target_allowance: T.allowance,
        pick_from: T.pick_from, switch_from: T.switch_from,
        sw_med: swMed, sw_mean: stationSwitch[s].mean, sw_p90: stationSwitch[s].p90, sw_gt10: stationSwitch[s].gt10,
        util: util[s],
        target_util: T.rate && T.pick != null ? r(Math.min(100, (T.pick * T.rate) / 36)) : null,
        slot_limit: sl.limit != null ? sl.limit : null, at_limit_pct: sl.at_limit_pct != null ? sl.at_limit_pct : null,
        lead_med: sl.lead_med != null ? sl.lead_med : null,
        slots_needed: T.rate && sl.lead_med ? r((T.rate * sl.lead_med) / 3600) : null,
      };
    });

    // Presentations possible each hour if the average pick took the target pick
    // time, with the switch and waiting as measured in that hour.
    const hmPossible = day.stations.map((st, s) => {
      const x = stationRows[s].target_pick;
      return R24.map((h) => {
        const n = day.hm_visits[s][h] || 0;
        if (!n || x == null) return null;
        const used = pick[s][h] + mech[s][h] + wait[s][h];
        const other = (mech[s][h] + wait[s][h]) / n;
        return x + other > 0 ? r(used / (x + other), 0) : null;
      });
    });

    const budget = computeBudget(day, { door, doorOf, pick, mech, wait, stationRows }, zones, C);
    const noDoor = day.stations.filter((st) => !hasDoor(st, zones, settings));
    const out = {
      door, no_door: door > 0 ? noDoor : [],          // stations that would have had a door added
      door_day_off: dayOff && doorSet > 0,
      overall, hist_sw: hist, stations: stationRows,
      hm_sw_med: hmSwMed, hm_sw_gt10: hmSwGt10, hm_util: hmUtil, hm_possible: hmPossible,
      budget,
    };
    Object.defineProperty(out, "_pick", { value: pick, enumerable: false });
    out.hours = hourPoints(day, out);
    out.relations = relations(out.hours);
    out.multi = day.multi ? multiStats([day.multi], [day.robot_k50 && day.robot_k50.stations_hist]) : null;
    out.starve = day.starve ? starveStats([day.starve]) : null;
    out.spatial = day.spatial ? spatialStats([day.spatial]) : null;
    out.text = dayText(day, out, C);
    out.method = methodNotes(day, out, C);
    return out;
  }

  /**
   * Time budget per tote, over the full-production hours.
   *
   * A target of N totes an hour gives each tote 3600 / N seconds.  Each tote's
   * actual cycle splits into picking, switch and waiting — means, because
   * means add up (mean cycle = 3600 / presentations per hour) and medians
   * do not.
   */
  function computeBudget(day, L, zones, C) {
    const F = day.full_hours || [];
    const groups = {};
    const p = prepare(day);
    for (const [s, row] of L.stationRows.entries()) {
      if (!row.target) continue;
      (groups[row.zone] = groups[row.zone] || []).push(s);
    }
    const out = [];
    if (F.length < 2) return { zones: out, full_hours: F, reason: "fewer than two full-production hours" };

    for (const zone of Object.keys(groups).sort(natural)) {
      const idx = groups[zone];
      const rows = [], scatter = [], ops = [], opPres = [];
      for (const s of idx) {
        const row = L.stationRows[s];
        let n = 0, pick = 0, mech = 0, wait = 0, capped = 0;
        const sw = row.sw_med != null ? row.sw_med : 0;
        const budgetS = 3600 / row.target;
        const pickAllow = budgetS - sw;
        for (const h of F) {
          const nh = day.hm_visits[s][h] || 0;
          n += nh;
          pick += L.pick[s][h]; mech += L.mech[s][h]; wait += L.wait[s][h];
          capped += cappedPickSeconds(day.raw.op[s][h], p.opPre[s][h], L.doorOf[s], pickAllow);
          if (nh) {
            scatter.push([r(L.pick[s][h] / nh, 2), nh, r(L.wait[s][h] / nh, 2), row.station]);
            ops.push(day.raw.op[s][h]); opPres.push(p.opPre[s][h]);
          }
        }
        if (!n) continue;
        const pickS = pick / n, swS = mech / n, waitS = wait / n, cycle = pickS + swS + waitS;
        const cappedS = capped / n;
        const tp = row.target_pick, ts = row.target_switch;
        // What the budget leaves for waiting.  If the pick and switch targets
        // already add up to more than the budget, waiting gets nothing and the
        // difference is the targets' own overrun — not charged to waiting.
        const allow = tp != null && ts != null ? budgetS - tp - ts : null;
        const allowEff = allow == null ? null : Math.max(allow, 0);
        rows.push({
          station: row.station, target: row.target, n,
          target_pick_s: tp == null ? null : r(tp, 2), target_switch_s: ts == null ? null : r(ts, 2),
          wait_allowance_s: allow == null ? null : r(allow, 2),
          pick_from: row.pick_from, switch_from: row.switch_from,
          excess_pick_s: tp == null ? null : r(pickS - tp, 2),
          excess_switch_s: ts == null ? null : r(swS - ts, 2),
          excess_wait_s: allow == null ? null : r(waitS - allowEff, 2),
          target_overflow_s: allow == null ? null : r(Math.max(-allow, 0), 2),
          rate: r(n / F.length), actual_cycle: r(3600 / (n / F.length), 2),
          pick_s: r(pickS, 2), switch_s: r(swS, 2), wait_s: r(waitS, 2), cycle_s: r(cycle, 2),
          budget_s: r(budgetS, 2), over_s: r(cycle - budgetS, 2),
          wait_allowed_s: r(budgetS - pickS - swS, 2), pick_allow_s: r(pickAllow, 2),
          sw_med: sw,
          pick_pct: r((pick / (F.length * 3600)) * 100), switch_pct: r((mech / (F.length * 3600)) * 100),
          wait_pct: r((wait / (F.length * 3600)) * 100),
          rns: r(3600 / (pickS + swS)),
          rpb: r(3600 / (cappedS + swS + waitS)),
          both: r(3600 / (cappedS + swS)),
        });
      }
      if (!rows.length) continue;

      // the long-pick tail across the zone's stations
      let nAll = 0, tAll = 0, nLong = 0, tLong = 0;
      ops.forEach((a, i) => {
        const pre = opPres[i], k = upperBound(a, C.long_pick_s * 10);
        nAll += a.length; tAll += pre[a.length] / 10;
        nLong += a.length - k; tLong += (pre[a.length] - pre[k]) / 10;
      });
      const zoneGaps = shiftedGaps(p, idx, L.doorOf);
      const targets = [...new Set(rows.map((x) => x.target))];
      out.push({
        zone, stations: rows.map((x) => x.station), rows, scatter,
        target: targets.length === 1 ? targets[0] : null, targets,
        switch_s: r(quantile(zoneGaps, 0.5) / 10, 2),
        r_pick: r(corr(scatter.map((x) => x[0]), scatter.map((x) => x[1])), 2),
        r_wait: r(corr(scatter.map((x) => x[2]), scatter.map((x) => x[1])), 2),
        long_pick_pct: nAll ? r((nLong / nAll) * 100, 0) : null,
        long_pick_time_pct: tAll ? r((tLong / tAll) * 100, 0) : null,
      });
    }
    return { zones: out, full_hours: F };
  }

  // ── hour by hour ─────────────────────────────────────────────────────────
  /**
   * One point per full-production hour, with every measure that might move
   * together: robots on a task / between tasks / away, task supply, and how
   * the stations fared.  Station measures use the stations that have a target
   * (all stations when none do), so the comparison follows the budget.
   */
  function hourPoints(day, D) {
    const F = day.full_hours || [];
    const U = day.utilization || {}, K = U.K50, A = U.ACR, FL = day.flow, MH = day.multi && day.multi.hourly, SH = day.starve && day.starve.hourly;
    const targeted = D.stations.map((t, i) => (t.target ? i : -1)).filter((i) => i >= 0);
    const use = targeted.length ? targeted : D.stations.map((_, i) => i);
    return F.map((h) => {
      let n = 0, wait = 0, pick = 0, totes = 0;
      for (let s = 0; s < day.stations.length; s++) totes += day.hm_visits[s][h] || 0;
      for (const s of use) { const nh = day.hm_visits[s][h] || 0; n += nh; wait += day.budget0.wait[s][h]; pick += D._pick[s][h]; }
      const k = K ? K.hourly[h] : null, a = A ? A.hourly[h] : null, f = FL ? FL.hourly[h] : null;
      return {
        day: day.date, h,
        totes, wait_s: n ? r(wait / n, 2) : null, pick_s: n ? r(pick / n, 2) : null,
        k50_util: k && k.util_fleet, k50_avail_util: k && k.util_available,
        k50_busy: k && k.busy, k50_idle: k && k.idle, k50_away: k && k.away,
        acr_avail_util: a && a.util_available, acr_busy: a && a.busy,
        created: f && f.created, ready: f && f.ready_wait, supply: f && f.supply_wait,
        ready_wait_s: f && f.ready_to_alloc_med,
        multi_pct: MH && MH[h].handovers ? r((MH[h].multi / MH[h].handovers) * 100) : null,
        starved_pct: SH && SH[h].handovers ? r((SH[h].starved / SH[h].handovers) * 100) : null,
        travel_s: SH ? SH[h].travel_med : null,
      };
    });
  }

  const HOUR_METRICS = [
    { key: "wait_s", label: "Station wait for a robot, per tote", unit: "s" },
    { key: "totes", label: "Totes presented, all stations", unit: "/h" },
    { key: "pick_s", label: "Pick time per tote", unit: "s" },
    { key: "k50_util", label: "K50 utilization, of the day's fleet", unit: "%" },
    { key: "k50_avail_util", label: "K50 utilization, of available robots", unit: "%" },
    { key: "k50_busy", label: "K50s on a task", unit: "" },
    { key: "k50_idle", label: "K50s between tasks", unit: "" },
    { key: "k50_away", label: "K50s away (≥ 5 min without a task)", unit: "" },
    { key: "acr_avail_util", label: "ACR utilization, of available robots", unit: "%" },
    { key: "created", label: "Tasks created", unit: "/h" },
    { key: "ready", label: "Totes ready in the buffer, no K50 yet", unit: "" },
    { key: "supply", label: "Tasks waiting on an ACR", unit: "" },
    { key: "ready_wait_s", label: "Tote ready → K50 allocated (median)", unit: "s" },
    { key: "multi_pct", label: "Handovers involving a multi-station tote", unit: "%" },
    { key: "starved_pct", label: "Starved handovers", unit: "%" },
    { key: "travel_s", label: "Buffer pickup → station arrival (median)", unit: "s" },
  ];
  // Pairs worth reading, in order.
  const HOUR_PRESETS = [
    { x: "k50_util", y: "wait_s", label: "Utilization vs station waiting" },
    { x: "k50_away", y: "wait_s", label: "K50s away vs station waiting" },
    { x: "multi_pct", y: "wait_s", label: "Multi-station vs station waiting" },
    { x: "k50_busy", y: "travel_s", label: "K50s on a task vs travel time" },
    { x: "travel_s", y: "starved_pct", label: "Travel time vs starvation" },
    { x: "created", y: "k50_util", label: "Task creation vs K50 utilization" },
    { x: "k50_away", y: "ready", label: "K50s away vs ready totes" },
    { x: "k50_busy", y: "totes", label: "K50s on a task vs totes presented" },
  ];

  function corrOf(points, x, y) {
    const p = points.filter((q) => q[x] != null && q[y] != null);
    return { r: r(corr(p.map((q) => q[x]), p.map((q) => q[y])), 2), n: p.length };
  }
  function relations(points) {
    const out = {};
    for (const pr of HOUR_PRESETS) out[pr.x + "|" + pr.y] = corrOf(points, pr.x, pr.y);
    const avg = (k) => mean(points.map((q) => q[k]));
    out.avg = { k50_avail_util: avg("k50_avail_util"), k50_away: avg("k50_away"), k50_idle: avg("k50_idle"), ready: avg("ready"), acr_avail_util: avg("acr_avail_util") };
    return out;
  }
  const strength = (rv) => { const a = Math.abs(rv); return a >= 0.6 ? "strongly" : a >= 0.4 ? "moderately" : a >= 0.2 ? "weakly" : null; };

  /** What the hours say about why stations wait — the same reading for a day or a run. */
  function relationText(rel, scope) {
    const a = rel.avg, parts = [];
    if (a.k50_avail_util == null) return "";
    const avail = a.k50_avail_util, ready = a.ready;
    if (avail >= 90 && ready != null && ready >= 10)
      parts.push(`Of the K50s available, ${f0(avail)}% were on a task, and ${f0(ready)} totes on average sat ready in the buffer with no K50 yet. Work was waiting for robots, not robots for work: the constraint ${scope} is K50 availability — the robots away, most likely charging — rather than task supply.`);
    else if (avail < 75 && ready != null && ready < 5)
      parts.push(`Only ${f0(avail)}% of the available K50s were on a task and almost nothing was ready in the buffer (${f1(ready)} totes on average): robots were waiting for work, so supply — task creation or ACR replenishment — is the limit ${scope}.`);
    else if (avail < 75 && ready != null && ready >= 10)
      parts.push(`K50s were free (${f0(avail)}% of the available ones on a task) while ${f0(ready)} totes sat ready in the buffer: robots and work were both there, so allocation is holding robots back — worth checking dispatch rules such as station queue limits.`);
    else if (ready != null)
      parts.push(`Of the K50s available, ${f0(avail)}% were on a task, with ${f0(ready)} totes ready in the buffer on average.`);
    const rw = rel["k50_away|wait_s"], ru = rel["k50_util|wait_s"], rc = rel["created|k50_util"];
    if (rw && rw.r != null && rw.r >= 0.2)
      parts.push(`Hour by hour, station waiting rises ${strength(rw.r)} with the number of K50s away (r = ${f2(rw.r)}, ${rw.n} hours).`);
    if (ru && ru.r != null && Math.abs(ru.r) >= 0.2)
      parts.push(`It ${ru.r < 0 ? "falls" : "rises"} ${strength(ru.r)} as more of the fleet is on a task (r = ${f2(ru.r)}).`);
    if (rc && rc.r != null && rc.r >= 0.4)
      parts.push(`K50 utilization follows task creation (r = ${f2(rc.r)}), so in quieter hours the robots are as busy as the supply of tasks allows.`);
    return parts.join(" ");
  }

  // ── multi-station visits and starvation ──────────────────────────────────
  const HANDOVER_KINDS = [
    { key: "plain", label: "Plain handover", note: "the robot leaving returns to the buffer; the robot arriving came from it" },
    { key: "out", label: "Leaving robot goes on", note: "the robot released takes the tote on to another station" },
    { key: "in", label: "Arriving robot chained in", note: "the robot arriving comes from another station" },
    { key: "both", label: "Both", note: "the robot leaving goes on and the robot arriving comes from another station" },
  ];

  /**
   * Pool the multi-station blocks of one or more days (metrics._multi_station)
   * — they are sums and counts, so a day and the whole run read the same way.
   * `hists` are the same days' robot_k50.stations_hist.
   */
  function multiStats(blocks, hists) {
    blocks = blocks.filter(Boolean);
    if (!blocks.length) return null;
    const pct = (a, b) => (b ? r((a / b) * 100) : null);
    const hist = [];
    for (const h of hists.filter(Boolean)) h.forEach((c, i) => { hist[i] = (hist[i] || 0) + c; });
    const cycles = hist.reduce((a, b) => a + b, 0), multiCycles = cycles - (hist[0] || 0);

    const kinds = HANDOVER_KINDS.map((k) => {
      let n = 0, wait = 0, starved = 0;
      for (const b of blocks) { const x = b.kinds[k.key]; n += x.n; wait += x.wait_s || 0; starved += x.starved; }
      return { ...k, n, wait_s: r(wait), starved, mean_wait_s: n ? r(wait / n, 2) : null, starved_pct: pct(starved, n) };
    });
    const sum = (f) => kinds.filter(f).reduce((a, k) => ({ n: a.n + k.n, w: a.w + k.wait_s, s: a.s + k.starved }), { n: 0, w: 0, s: 0 });
    const all = sum(() => true), mu = sum((k) => k.key !== "plain"), pl = sum((k) => k.key === "plain");
    const excess = blocks.reduce((a, b) => a + (b.excess_s || 0), 0);

    const bands = blocks[0].bands.map((bd, i) => {
      let windows = 0, n = 0, w = 0;
      for (const b of blocks) { const x = b.bands[i]; windows += x.windows; n += x.handovers; w += x.wait_s || 0; }
      return { band: bd.band, windows, handovers: n, mean_wait_s: n ? r(w / n, 2) : null };
    });

    const byStation = {};
    for (const b of blocks) for (const x of b.stations) {
      const t = byStation[x.station] || (byStation[x.station] = { station: x.station, handovers: 0, multi: 0, plain_wait: 0, multi_wait: 0, excess: 0, starved_plain: 0, starved_multi: 0 });
      t.handovers += x.handovers; t.multi += x.multi; t.plain_wait += x.plain_wait_s || 0; t.multi_wait += x.multi_wait_s || 0;
      t.excess += x.excess_s || 0; t.starved_plain += x.starved_plain; t.starved_multi += x.starved_multi;
    }
    const stations = Object.values(byStation).sort((a, b) => natural(a.station, b.station)).map((t) => {
      const np = t.handovers - t.multi, st = t.starved_plain + t.starved_multi;
      return {
        station: t.station, handovers: t.handovers, multi_pct: pct(t.multi, t.handovers),
        plain_wait_s: np ? r(t.plain_wait / np, 2) : null, multi_wait_s: t.multi ? r(t.multi_wait / t.multi, 2) : null,
        starved: st, starved_multi_pct: pct(t.starved_multi, st),
        excess_min: r(t.excess / 60), excess_pct: pct(t.excess, t.plain_wait + t.multi_wait),
      };
    });
    const chained = blocks.reduce((a, b) => a + b.chained_n, 0);
    return {
      days: blocks.length, hist, cycles, multi_cycles: multiCycles, multi_cycles_pct: pct(multiCycles, cycles),
      kinds, handovers: all.n, multi_handovers: mu.n, multi_pct: pct(mu.n, all.n),
      starved: all.s, starved_multi: mu.s, starved_multi_pct: pct(mu.s, all.s),
      // How much more often a starved handover involves a multi-station tote than handovers in general.
      lift: mu.n && all.s ? r((mu.s / all.s) / (mu.n / all.n), 2) : null,
      plain_wait_s: pl.n ? r(pl.w / pl.n, 2) : null, multi_wait_s: mu.n ? r(mu.w / mu.n, 2) : null,
      wait_h: r(all.w / 3600), multi_wait_pct: pct(mu.w, all.w),
      excess_h: r(excess / 3600, 2), excess_pct: pct(excess, all.w),
      bands, stations, window_min: blocks[0].window_min, starved_s: blocks[0].starved_s,
      chained_n: chained,
      transit_med: r(median(blocks.map((b) => b.transit_med)), 0),
      chained_to_free_pct: chained ? r(blocks.reduce((a, b) => a + (b.chained_to_free_pct || 0) * b.chained_n, 0) / chained) : null,
    };
  }

  /** Does starvation come with multi-station visits?  The same reading for a day or a run. */
  function multiText(M, rel, scope) {
    if (!M || !M.handovers) return "";
    const parts = [];
    const two = M.hist[1] || 0, more = M.multi_cycles - two;
    if (M.cycles) parts.push(`${f1(M.multi_cycles_pct)}% of K50 cycles ${scope} were multi-station (${M.multi_cycles.toLocaleString()} of ${M.cycles.toLocaleString()}): ${two.toLocaleString()} at two stations and ${more.toLocaleString()} at three or more.`);
    parts.push(`Stations were starved on ${M.starved.toLocaleString()} handovers — waiting more than ${secs(M.starved_s)} s beyond their usual handover for the next robot. ${f1(M.starved_multi_pct)}% of those involved a multi-station tote, against ${f1(M.multi_pct)}% of all handovers.`);
    const lift = M.lift;
    if (lift != null) {
      if (lift >= 1.5) parts.push(`Multi-station totes are ${f1(lift)} times over-represented among the starved handovers: they are clearly linked to starvation.`);
      else if (lift >= 1.15) parts.push(`So they are over-represented (${f2(lift)}×): a multi-station tote is behind more than its share of starvation.`);
      else if (lift > 0.87) parts.push(`That is about their share of all handovers (${f2(lift)}×): a starved station is no more likely than any other to be waiting around a multi-station tote.`);
      else parts.push(`That is below their share of all handovers (${f2(lift)}×): starvation is, if anything, less common around multi-station totes.`);
    }
    const ch = M.kinds.find((k) => k.key === "in"), pl = M.kinds.find((k) => k.key === "plain");
    if (ch.n && ch.mean_wait_s != null && pl.mean_wait_s != null && ch.mean_wait_s > pl.mean_wait_s * 1.2)
      parts.push(`The mechanism is the robot chained in from another station: the station waits ${f1(ch.mean_wait_s)} s for it on average, against ${f1(pl.mean_wait_s)} s on a plain handover (it left its previous station a median ${f0(M.transit_med)} s before arriving).`);
    const ex = M.excess_pct;
    if (ex != null) {
      const size = ex >= 15 ? "a significant part" : ex >= 5 ? "a modest part" : "only a small part";
      parts.push(`But only ${f1(M.multi_pct)}% of handovers involve one, so like for like at each station they added ${f1(M.excess_h)} h of waiting — ${size} (${f1(ex)}%) of the ${f1(M.wait_h)} h the stations spent waiting for robots. The rest came on plain handovers.`);
      // Where that excess sits: the fewest stations holding two thirds of it.
      const tot = M.stations.reduce((a, t) => a + Math.max(t.excess_min || 0, 0), 0);
      if (tot > 0 && M.stations.length > 2) {
        const top = M.stations.filter((t) => t.excess_min > 0).sort((a, b) => b.excess_min - a.excess_min);
        const pick = []; let acc = 0;
        for (const t of top) { if (acc >= tot * 2 / 3) break; pick.push(t); acc += t.excess_min; }
        if (pick.length <= M.stations.length / 2)
          parts.push(`${f0((acc / tot) * 100)}% of that added waiting is at ${runs(pick.map((t) => t.station).sort(natural))}, where multi-station handovers make up ${f0(mean(pick.map((t) => t.excess_pct)))}% of the waiting on average.`);
      }
    }
    const rm = rel && rel["multi_pct|wait_s"];
    if (rm && rm.r != null && rm.n >= 5)
      parts.push(Math.abs(rm.r) < 0.2
        ? `Hour by hour, station waiting does not move with the share of multi-station handovers (r = ${f2(rm.r)}, ${rm.n} hours).`
        : `Hour by hour, station waiting ${rm.r > 0 ? "rises" : "falls"} ${strength(rm.r)} with the share of multi-station handovers (r = ${f2(rm.r)}, ${rm.n} hours).`);
    return parts.join(" ");
  }

  // ── why stations starve ──────────────────────────────────────────────────
  const STARVE_STAGES = [
    { key: "no_task", label: "No task yet", note: "the warehouse system had not yet created the task that robot brought" },
    { key: "acr", label: "Waiting on an ACR", note: "the task existed but its tote was not yet in the buffer" },
    { key: "ready", label: "Tote ready, no K50", note: "the tote sat in the buffer with no K50 allocated to it" },
    { key: "to_buffer", label: "K50 to the buffer", note: "a K50 was allocated and travelling empty to pick the tote up" },
    { key: "to_station", label: "Carrying the tote", note: "the tote was picked up and on its way to the station, any queueing included" },
    { key: "chained", label: "At another station", note: "a multi-station robot still at, or coming from, its previous station" },
    { key: "unknown", label: "Unknown", note: "the arriving visit could not be tied to a task" },
  ];
  const CONTEXT_KINDS = [
    { key: "dispatch", label: "K50 free and a tote ready", note: "a K50 was free and a tote for this station sat ready, yet neither had been put together" },
    { key: "k50", label: "Tote ready, no K50 free", note: "a tote for this station was ready but every K50 was busy or away" },
    { key: "supply", label: "K50 free, no tote ready", note: "a K50 was free but no tote for this station was ready in the buffer" },
    { key: "neither", label: "Neither", note: "no K50 free and no tote ready for this station" },
  ];

  /** Quantile of values binned `w` wide (the last bin open), interpolated within the bin. */
  function histQuantile(hist, w, p) {
    const tot = hist.reduce((a, b) => a + b, 0);
    if (!tot) return null;
    let acc = 0;
    for (let i = 0; i < hist.length; i++) {
      if (acc + hist[i] >= p * tot && hist[i]) return (i + (p * tot - acc) / hist[i]) * w;
      acc += hist[i];
    }
    return hist.length * w;
  }
  const addInto = (a, b) => { b.forEach((x, i) => { a[i] = (a[i] || 0) + (x || 0); }); return a; };

  /**
   * Pool the starvation blocks of one or more days (metrics._starvation):
   * sums, counts and histograms, so a day and the whole run read the same way.
   */
  function starveStats(blocks) {
    blocks = blocks.filter(Boolean);
    if (!blocks.length) return null;
    const pct = (a, b) => (b ? r((a / b) * 100) : null);
    const sumOf = (f) => blocks.reduce((a, b) => a + (f(b) || 0), 0);
    const handovers = sumOf((b) => b.handovers), starved = sumOf((b) => b.starved), wait = sumOf((b) => b.wait_s);
    const W = blocks[0].travel_w;

    const stages = STARVE_STAGES.map((st) => {
      const sec = sumOf((b) => b.stages[st.key]), at = sumOf((b) => b.starved_at[st.key]);
      return { ...st, wait_h: r(sec / 3600, 2), pct: pct(sec, wait), starved_at: at, starved_at_pct: pct(at, starved) };
    });

    const enN = [], enS = [];
    for (const b of blocks) { addInto(enN, b.en_route.map((x) => x.n)); addInto(enS, b.en_route.map((x) => x.starved)); }
    const en_route = enN.map((n, i) => ({ k: i, n, starved: enS[i], starved_pct: pct(enS[i], n) }));

    const context = CONTEXT_KINDS.map((c) => {
      const n = sumOf((b) => b.context[c.key].n);
      return { ...c, n, pct: pct(n, starved), wait_h: r(sumOf((b) => b.context[c.key].wait_s) / 3600, 2) };
    });

    const hist = (key) => blocks.reduce((a, b) => addInto(a, b[key]), []);
    const tq = (h, p) => r(histQuantile(h, W, p), 0);
    const tAll = hist("travel_hist"), tSt = hist("travel_hist_starved"), tOt = hist("travel_hist_other"), tEm = hist("empty_hist");

    const bands = (key) => blocks[0][key].map((bd, i) => {
      const n = sumOf((b) => b[key][i].n), st = sumOf((b) => b[key][i].starved), w = sumOf((b) => b[key][i].wait_s);
      return { band: bd.band, n, starved: st, starved_pct: pct(st, n), wait_per_s: n ? r(w / n, 2) : null };
    });

    const byStation = {};
    for (const b of blocks) for (const x of b.stations) {
      const t = byStation[x.station] || (byStation[x.station] = { station: x.station, handovers: 0, starved: 0, wait: 0, stages: {}, dispatch: 0, none: 0, travel: [], en_n: [], en_s: [], created: 0, hours: 0 });
      t.handovers += x.handovers; t.starved += x.starved; t.wait += x.wait_s || 0;
      for (const st of STARVE_STAGES) t.stages[st.key] = (t.stages[st.key] || 0) + (x.stages[st.key] || 0);
      t.dispatch += x.starved_dispatch; t.none += x.starved_none_en_route;
      addInto(t.travel, x.travel_hist); addInto(t.en_n, x.en_route_n); addInto(t.en_s, x.en_route_starved);
      t.created += x.created; t.hours += b.full_hours;
    }
    const stations = Object.values(byStation).sort((a, b) => natural(a.station, b.station)).map((t) => {
      // The station's usual pipeline: the median K50s on the way at a release.
      const typical = histQuantile(t.en_n, 1, 0.5);
      const cut = typical == null ? 0 : Math.floor(typical / 2);
      let lowN = 0, lowS = 0, hiN = 0, hiS = 0;
      t.en_n.forEach((n, i) => { if (i < cut) { lowN += n; lowS += t.en_s[i]; } else { hiN += n; hiS += t.en_s[i]; } });
      return {
        station: t.station, handovers: t.handovers, starved: t.starved, starved_pct: pct(t.starved, t.handovers),
        wait_h: r(t.wait / 3600, 2), wait_per_s: t.handovers ? r(t.wait / t.handovers, 2) : null,
        stages: Object.fromEntries(STARVE_STAGES.map((st) => [st.key, pct(t.stages[st.key], t.wait)])),
        dispatch_pct: pct(t.dispatch, t.starved), none_en_route_pct: pct(t.none, t.starved),
        travel_med: tq(t.travel, 0.5), travel_p90: tq(t.travel, 0.9),
        en_route_typical: typical == null ? null : Math.round(typical), en_route_cut: cut,
        starved_pct_low: pct(lowS, lowN), starved_pct_high: pct(hiS, hiN), low_share: pct(lowN, lowN + hiN),
        low_n: lowN, low_starved: lowS, high_n: hiN, high_starved: hiS,
        created_per_h: t.hours ? r(t.created / t.hours) : null,
      };
    });

    const hourly = Array.from({ length: 24 }, (_, h) => {
      const xs = blocks.map((b) => b.hourly[h]);
      return {
        h, handovers: xs.reduce((a, x) => a + x.handovers, 0), starved: xs.reduce((a, x) => a + x.starved, 0),
        stages: Object.fromEntries(STARVE_STAGES.map((st) => [st.key, r(xs.reduce((a, x) => a + (x.stages[st.key] || 0), 0) / 3600, 2)])),
      };
    });

    return {
      days: blocks.length, starved_s: blocks[0].starved_s, travel_w: W,
      handovers, starved, starved_pct: pct(starved, handovers), wait_h: r(wait / 3600, 1), wait_per_s: handovers ? r(wait / handovers, 2) : null,
      stages, en_route, context,
      free_k50_avg: starved ? r(sumOf((b) => b.free_k50_sum) / starved, 1) : null,
      ready_here_avg: starved ? r(sumOf((b) => b.ready_here_sum) / starved, 1) : null,
      travel: { med: tq(tAll, 0.5), p90: tq(tAll, 0.9), starved_med: tq(tSt, 0.5), other_med: tq(tOt, 0.5), empty_med: tq(tEm, 0.5), empty_p90: tq(tEm, 0.9), hist: tAll },
      pace: bands("pace"), resume: bands("resume"), stations, hourly,
    };
  }

  /** The diagnosis in words — the same reading for a day or a run. */
  function starveText(S, scope) {
    if (!S || !S.handovers) return {};
    const out = {}, st = Object.fromEntries(S.stages.map((x) => [x.key, x]));
    const top = S.stages.filter((x) => x.pct).sort((a, b) => b.pct - a.pct);
    const parts = [`Stations ${scope} waited ${f1(S.wait_h)} h for robots in the full-production hours, and ${f1(S.starved_pct)}% of handovers were starved (over ${secs(S.starved_s)} s beyond a normal handover).`];
    if (top.length) {
      const t = top[0];
      parts.push(`For ${f0(t.pct)}% of those waiting seconds the robot that finally arrived was at the stage "${t.label.toLowerCase()}" — ${t.note}.` +
        (top[1] && top[1].pct >= 5 ? ` Next: "${top[1].label.toLowerCase()}" (${f0(top[1].pct)}%)${top[2] && top[2].pct >= 5 ? ` and "${top[2].label.toLowerCase()}" (${f0(top[2].pct)}%)` : ""}.` : ""));
    }
    const demand = S.stations.filter((x) => (x.stages.no_task || 0) >= 25).map((x) => x.station);
    const carrying = S.stations.filter((x) => (x.stages.to_station || 0) >= 70).map((x) => x.station);
    if (carrying.length) parts.push(`At ${runs(carrying)} the next robot was nearly always already carrying the tote: the work and the robot were there, it just did not arrive in time.`);
    if (demand.length) parts.push(`At ${runs(demand)} a large part of the waiting came before the warehouse system had even created the next task: those stations are short of work rather than of robots.`);
    out.stages = parts.join(" ");

    const pp = [];
    // Each station against its own usual pipeline, pooled by handovers.
    const withPipe = S.stations.filter((x) => x.en_route_typical >= 4 && x.low_n && x.high_n);
    if (withPipe.length) {
      const sum = (k) => withPipe.reduce((a, x) => a + x[k], 0);
      const lo = (sum("low_starved") / sum("low_n")) * 100, hi = (sum("high_starved") / sum("high_n")) * 100;
      const share = (sum("low_n") / (sum("low_n") + sum("high_n"))) * 100;
      const typ = withPipe.map((x) => x.en_route_typical);
      pp.push(`When a station releases a robot it normally has ${Math.min(...typ) === Math.max(...typ) ? f0(typ[0]) : `${f0(Math.min(...typ))}–${f0(Math.max(...typ))}`} K50s allocated to it and on the way. In the ${f0(share)}% of handovers where fewer than half its usual number were on the way, ${f1(lo)}% starved, against ${f1(hi)}% otherwise${lo > 1.5 * hi ? ": starvation follows a thinning pipeline of robots" : ""}.`);
    }
    const T = S.travel;
    if (T.starved_med != null && T.other_med != null) {
      pp.push(T.starved_med < T.other_med * 0.95
        ? `The robots that ended a starved wait had a shorter trip from buffer pickup to the station (median ${f0(T.starved_med)} s, against ${f0(T.other_med)} s): they were not slow on the way, they were picked up late.`
        : T.starved_med > T.other_med * 1.05
          ? `The robots that ended a starved wait took longer from buffer pickup to the station (median ${f0(T.starved_med)} s, against ${f0(T.other_med)} s): slow or blocked travel is part of it.`
          : `The robots that ended a starved wait travelled from the buffer in a normal time (median ${f0(T.starved_med)} s, against ${f0(T.other_med)} s).`);
    }
    out.pipeline = pp.join(" ");

    const c = Object.fromEntries(S.context.map((x) => [x.key, x]));
    out.context = `When a station began to starve there were on average ${f1(S.free_k50_avg)} K50s free and ${f1(S.ready_here_avg)} totes for it ready in the buffer. ` +
      `In ${f0(c.dispatch.pct)}% of starved handovers both a free K50 and a ready tote existed — work that could have been sent sooner, unless the station was at its slot limit (see Station slots); in ${f0(c.k50.pct)}% a tote was ready but no K50 was free; in ${f0(c.supply.pct)}% a K50 was free but nothing for that station was ready.`;

    const tp = [];
    const pace = S.pace, fast = pace[0], usual = pace[2];
    if (fast.n && usual.n && fast.starved_pct != null && usual.starved_pct != null) {
      const k = usual.starved_pct ? fast.starved_pct / usual.starved_pct : null;
      tp.push(`After a run of fast picks (the last three under 70% of the station's median) ${f1(fast.starved_pct)}% of handovers starved, against ${f1(usual.starved_pct)}% at the usual pace${k != null && k >= 1.5 ? ` — ${f1(k)} times as often: fast operators empty the queue faster than robots refill it` : ""}.`);
    }
    const rs = S.resume, first = rs[0], settled = rs[rs.length - 1];
    if (first.n && settled.n)
      tp.push(`In the first five minutes after a station comes back from a stand-down, ${f1(first.starved_pct)}% of handovers starved, against ${f1(settled.starved_pct)}% once it had been running over an hour.`);
    out.patterns = tp.join(" ");
    return out;
  }

  // ── rack and buffer locations ────────────────────────────────────────────
  const CROWD_LABELS = ["alone", "1 other", "2 others", "3+ others"];
  // Aisles with fewer trips than this to a station are not read for travel.
  const TRAVEL_MIN_N = 10;

  /**
   * Pool the location blocks of one or more days (metrics._spatial): grids are
   * re-keyed onto the union of aisles, levels and stations, then summed.
   */
  function spatialStats(blocks) {
    blocks = blocks.filter(Boolean);
    if (!blocks.length) return null;
    const pct = (a, b) => (b ? r((a / b) * 100) : null);
    const sumOf = (f) => blocks.reduce((a, b) => a + (f(b) || 0), 0);
    const union = (f, cmp) => Array.from(new Set(blocks.flatMap((b) => f(b) || []))).sort(cmp || ((a, b) => a - b));
    /** Sum each block's grid[row][col] onto the union rows × cols. */
    const pool = (rows, cols, f) => {
      const out = rows.map(() => cols.map(() => 0));
      for (const b of blocks) {
        const g = f(b);
        if (!g) continue;
        g.rows.forEach((rk, i) => { const ri = rows.indexOf(rk); g.cols.forEach((ck, j) => { out[ri][cols.indexOf(ck)] += g.grid[i][j] || 0; }); });
      }
      return out;
    };

    // 1. sources
    const aisles = union((b) => b.aisles), levels = union((b) => b.levels);
    const grid = pool(levels, aisles, (b) => ({ rows: b.levels, cols: b.aisles, grid: b.grid }));
    const puts = sumOf((b) => b.puts);
    const byAisle = aisles.map((a, j) => ({ aisle: a, n: grid.reduce((s, row) => s + row[j], 0) })).sort((x, y) => y.n - x.n);
    const fifth = Math.max(1, Math.round(aisles.length / 5));
    const top = byAisle[0] || { aisle: null, n: 0 };
    const byLevel = levels.map((lv, i) => ({ level: lv, n: grid[i].reduce((s, x) => s + x, 0) }));
    const upper = levels.length ? levels[Math.floor((levels.length * 2) / 3)] : null;
    const sources = {
      puts, aisles, levels, grid, by_aisle: byAisle, by_level: byLevel,
      top_aisle: top.aisle, top_share: pct(top.n, puts), even_share: aisles.length ? r(100 / aisles.length) : null,
      top_fifth_share: pct(byAisle.slice(0, fifth).reduce((s, x) => s + x.n, 0), puts), fifth,
      upper_level: upper, upper_share: pct(byLevel.filter((x) => x.level >= upper).reduce((s, x) => s + x.n, 0), puts),
      locations: blocks.length === 1 ? blocks[0].locations : null,
    };

    // 2. lift
    const lift = levels.map((lv) => {
      const xs = blocks.flatMap((b) => b.lift.filter((x) => x.level === lv));
      const n = xs.reduce((s, x) => s + x.n, 0), sec = xs.reduce((s, x) => s + x.handle_s, 0);
      return { level: lv, n, handle_s: n ? r(sec / n) : null };
    });

    // 3. crowding, per fleet
    const crowd = {};
    for (const fleet of ["K50", "ACR"]) {
      const cs = blocks.map((b) => b.crowd && b.crowd[fleet]).filter(Boolean);
      if (!cs.length) continue;
      const trips = cs.reduce((s, c) => s + c.trips, 0);
      const bins = CROWD_LABELS.map((label, k) => {
        const n = cs.reduce((s, c) => s + c.bins[k].n, 0);
        return { k, label, n, share: pct(n, trips), lead_s: n ? r(cs.reduce((s, c) => s + c.bins[k].lead_s, 0) / n) : null,
                 excess_s: n ? cs.reduce((s, c) => s + c.bins[k].excess_s, 0) / n : null };
      });
      const base = bins[0].excess_s;
      for (const b of bins) b.added_s = b.excess_s == null || base == null ? null : r(b.excess_s - base);
      const addedH = bins.slice(1).reduce((s, b) => s + (b.added_s || 0) * b.n, 0) / 3600;
      const same = cs.reduce((s, c) => s + c.same_sum, 0), exp = cs.reduce((s, c) => s + c.expected_sum, 0);
      crowd[fleet] = {
        fleet, trips, bins, aisles: Math.max(...cs.map((c) => c.aisles)),
        lead_s: trips ? r(cs.reduce((s, c) => s + c.lead_s, 0) / trips) : null,
        same_per: trips ? r(same / trips, 2) : null, expected_per: trips ? r(exp / trips, 2) : null,
        clustering: exp ? r(same / exp, 2) : null,
        added_h: r(addedH, 1), added_h_per_day: r(addedH / cs.length, 1),
      };
    }

    // 4. returns
    const R0 = blocks[0].returns, bandsMin = R0.bands_min;
    const back = blocks.reduce((a, b) => addInto(a, b.returns.back), []);
    const stores = sumOf((b) => b.returns.stores);
    const retaken = back.slice(0, -1).reduce((s, x) => s + x, 0);
    const within = (m) => back.slice(0, bandsMin.indexOf(m) + 1).reduce((s, x) => s + x, 0);
    const bandLabels = bandsMin.map((m, i) => (i ? `${bandsMin[i - 1]}–${m} min` : `< ${m} min`))
      .concat([`over ${bandsMin[bandsMin.length - 1] / 60} h`, "not in the log"]);
    const returns = {
      stores, retaken, retaken_pct: pct(retaken, stores),
      within_60: within(60), within_60_pct: pct(within(60), stores), within_15_pct: pct(within(15), stores),
      same_slot_pct: pct(sumOf((b) => b.returns.same_slot), retaken),
      bands: back.map((n, i) => ({ label: bandLabels[i], n, pct: pct(n, stores) })),
    };
    const repTotes = blocks.reduce((a, b) => addInto(a, b.repeats.totes), []);
    const repPuts = blocks.reduce((a, b) => addInto(a, b.repeats.puts), []);
    const repeats = {
      bins: blocks[0].repeats.labels.map((label, i) => ({ label, totes: repTotes[i], puts: repPuts[i] })),
      // puts of totes taken out three or more times on the same day
      hot_puts_pct: pct(repPuts.slice(2).reduce((s, x) => s + x, 0), repPuts.reduce((s, x) => s + x, 0)),
    };

    // 5. travel: buffer aisle → station
    let travel = null;
    const tb = blocks.filter((b) => b.travel);
    if (tb.length) {
      const ta = union((b) => b.travel && b.travel.aisles), ts = union((b) => b.travel && b.travel.stations, natural);
      const n = pool(ts, ta, (b) => b.travel && { rows: b.travel.stations, cols: b.travel.aisles, grid: b.travel.n });
      const sm = pool(ts, ta, (b) => b.travel && { rows: b.travel.stations, cols: b.travel.aisles, grid: b.travel.sum_s });
      const meanGrid = n.map((row, i) => row.map((c, j) => (c >= TRAVEL_MIN_N ? r(sm[i][j] / c, 0) : null)));
      const stations = ts.map((st, i) => {
        const cells = ta.map((a, j) => ({ a, n: n[i][j], s: sm[i][j], m: meanGrid[i][j] })).filter((x) => x.m != null).sort((x, y) => x.m - y.m);
        const trips = n[i].reduce((s, x) => s + x, 0);
        const q = Math.max(1, Math.round(cells.length / 4));
        const near = cells.slice(0, q), far = cells.slice(-q);
        const avg = (xs) => { const c = xs.reduce((s, x) => s + x.n, 0); return c ? r(xs.reduce((s, x) => s + x.s, 0) / c, 0) : null; };
        return {
          station: st, trips, mean_s: trips ? r(sm[i].reduce((s, x) => s + x, 0) / trips, 0) : null,
          near_aisles: near.map((x) => x.a), near_s: avg(near), far_aisles: far.map((x) => x.a), far_s: avg(far),
          spread_s: cells.length >= 4 ? r(avg(far) - avg(near), 0) : null,
          near_share: cells.length >= 4 ? pct(near.reduce((s, x) => s + x.n, 0), cells.reduce((s, x) => s + x.n, 0)) : null,
          near_share_even: cells.length >= 4 ? pct(q, cells.length) : null,
          // seconds saved per trip had every trip come from the nearest quarter
          gain_s: cells.length >= 4 ? r(avg(cells) - avg(near), 0) : null,
        };
      });
      travel = { aisles: ta, stations: ts, n, mean: meanGrid, by_station: stations };
    }

    return { days: blocks.length, limits: { ...blocks[0].limits, travel_min_n: TRAVEL_MIN_N }, sources, lift, crowd, returns, repeats, travel };
  }

  /** The location reading in words — the same for a day or a run. */
  function spatialText(Sp, scope) {
    if (!Sp) return {};
    const out = {}, s = Sp.sources;
    const ratio = s.even_share ? s.top_share / s.even_share : null;
    out.sources = `ACRs took ${s.puts.toLocaleString()} totes from storage ${scope}, from ${s.aisles.length} aisles${s.locations ? ` and ${s.locations.toLocaleString()} different slots` : ""}. ` +
      `The busiest aisle (${s.top_aisle}) supplied ${f1(s.top_share)}% against the ${f1(s.even_share)}% an even spread would give, and the busiest ${s.fifth} aisles ${f0(s.top_fifth_share)}% against ${f0((s.fifth / s.aisles.length) * 100)}%: ` +
      (ratio == null || ratio < 1.5 ? "the work is spread across the rack, not concentrated in a few aisles."
        : ratio < 2.5 ? "somewhat uneven, but no aisle dominates."
          : "the work is concentrated in a few aisles.") +
      ` ${f0(s.upper_share)}% came from level ${s.upper_level} or above.`;

    const cp = [];
    const say = (c, where) => {
      const b = c.bins, crowded = b.slice(2).reduce((a, x) => a + x.n, 0);
      const worst = b.slice(1).filter((x) => x.added_s != null && x.n >= 30).pop();
      const effect = worst && worst.added_s >= 3;
      let t = `${c.fleet}s: ${f0(b[0].share)}% of trips were allocated with no other ${c.fleet} heading to the same ${where}, ${f0((crowded / c.trips) * 100)}% with two or more. `;
      t += effect
        ? `With the fleet equally busy, a trip with ${worst.label} heading there took ${f1(worst.added_s)} s longer from allocation to pickup than one alone (${b.slice(1, -1).map((x) => `${x.label}: ${x.added_s > 0 ? "+" : ""}${f1(x.added_s)} s`).join(", ")}) — ${f1(c.added_h_per_day)} ${c.fleet}-hours a day lost to sharing the aisle.`
        : `With the fleet equally busy, sharing the aisle made no real difference to the time from allocation to pickup (${b.slice(1).filter((x) => x.added_s != null).map((x) => `${x.label}: ${x.added_s > 0 ? "+" : ""}${f1(x.added_s)} s`).join(", ")}).`;
      if (c.clustering != null)
        t += c.clustering >= 1.2
          ? ` Trips share an aisle ${f1(c.clustering)} times as often as they would by chance: the work arrives clustered.`
          : c.clustering <= 0.85
            ? ` Trips share an aisle less often than chance would have it (${f2(c.clustering)}×): dispatch already spreads them.`
            : ` Trips share an aisle about as often as chance would have it (${f2(c.clustering)}×): there is no hot spot pulling robots together.`;
      return t;
    };
    if (Sp.crowd.ACR) cp.push(say(Sp.crowd.ACR, "storage aisle"));
    if (Sp.crowd.K50) cp.push(say(Sp.crowd.K50, "buffer aisle"));
    out.crowd = cp.join(" ");

    const L = Sp.lift.filter((x) => x.n >= 30 && x.handle_s != null);
    if (L.length >= 2) {
      const lo = L.reduce((a, x) => (x.handle_s < a.handle_s ? x : a)), hi = L.reduce((a, x) => (x.handle_s > a.handle_s ? x : a));
      out.lift = `Handling a tote — the ACR's load to its unload in the buffer — takes ${f1(lo.handle_s)} s on average from level ${lo.level} and ${f1(hi.handle_s)} s from level ${hi.level}.`;
    }

    const R = Sp.returns, P = Sp.repeats;
    out.returns = `Of ${R.stores.toLocaleString()} totes an ACR put back into storage ${scope}, ${f0(R.within_15_pct)}% were taken out again within 15 minutes and ${f0(R.within_60_pct)}% within the hour (${R.within_60.toLocaleString()}); ${f0(R.retaken_pct)}% came out again at some point in the log. ` +
      (R.within_60_pct >= 10 ? `Each of those is a store and a put that could have been saved had the tote stayed in the buffer, buffer space allowing — about ${(2 * R.within_60).toLocaleString()} ACR moves. ` : "") +
      `${f0(P.hot_puts_pct)}% of puts were of totes taken out three or more times on the same day.`;

    const T = Sp.travel;
    if (T) {
      const st = T.by_station.filter((x) => x.spread_s != null);
      if (st.length) {
        const sp = mean(st.map((x) => x.spread_s)), gain = mean(st.map((x) => x.gain_s));
        const ns = mean(st.map((x) => x.near_share)), ne = mean(st.map((x) => x.near_share_even));
        const wide = st.slice().sort((a, b) => b.spread_s - a.spread_s)[0];
        out.travel = `From buffer pickup to the station, the K50's trip takes on average ${f0(sp)} s longer from a station's farthest quarter of buffer aisles than from its nearest (widest at ${wide.station}: ${f0(wide.near_s)} s against ${f0(wide.far_s)} s). ` +
          (Math.abs(ns - ne) < 5
            ? `Yet ${f0(ns)}% of each station's totes come from its nearest quarter — what a random choice would give (${f0(ne)}%): where a tote is buffered takes no account of the station it is going to. Sourcing every tote from a station's nearest quarter would be worth up to about ${f0(gain)} s per trip.`
            : ns > ne
              ? `${f0(ns)}% of each station's totes come from its nearest quarter, more than the ${f0(ne)}% a random choice would give.`
              : `Only ${f0(ns)}% of each station's totes come from its nearest quarter, fewer than the ${f0(ne)}% a random choice would give.`);
      }
    }
    return out;
  }

  // ── wording ──────────────────────────────────────────────────────────────
  /** 'LABOR-1 to 7', 'LABOR-2 and LABOR-9', 'LABOR-1 to 3 and LABOR-7'. */
  function runs(names) {
    if (!names.length) return "";
    let prefixStr = null;
    const nums = [];
    for (const n of names) {
      const m = /^(.*?)(\d+)$/.exec(String(n));
      if (!m || (prefixStr !== null && m[1] !== prefixStr)) return names.join(", ");
      prefixStr = m[1]; nums.push(Number(m[2]));
    }
    nums.sort((a, b) => a - b);
    const parts = [];
    for (let i = 0; i < nums.length;) {
      let j = i;
      while (j + 1 < nums.length && nums[j + 1] === nums[j] + 1) j++;
      if (j - i >= 2) parts.push(`${prefixStr}${nums[i]} to ${nums[j]}`);
      else for (let k = i; k <= j; k++) parts.push(`${prefixStr}${nums[k]}`);
      i = j + 1;
    }
    return parts.length === 1 ? parts[0] : parts.slice(0, -1).join(", ") + " and " + parts[parts.length - 1];
  }
  const hoursPhrase = (hs) => { const s = hs.map((h) => String(h).padStart(2, "0")); return s.length === 1 ? s[0] : s.slice(0, -1).join(", ") + " and " + s[s.length - 1]; };
  const f1 = (v) => Number(v).toFixed(1), f0 = (v) => Number(v).toFixed(0), f2 = (v) => Number(v).toFixed(2);
  const secs = (v) => String(+Number(v).toFixed(1));        // 1 → "1", 0.5 → "0.5"

  function dayText(day, D, C) {
    const out = {};
    const T = D.stations;

    // switch
    const sws = T.filter((t) => t.sw_med != null);
    if (sws.length) {
      const core = median(sws.map((t) => t.sw_med));
      const fastest = sws.reduce((a, b) => (b.sw_med < a.sw_med ? b : a));
      let txt = `The core switch is tight and consistent: about ${f1(core)} s`;
      if (D.door) txt += D.no_door.length ? ` (including ${D.door} s of door travel at the stations that have a door)` : ` (including ${D.door} s of door travel)`;
      if (core - fastest.sw_med >= 0.5) txt += `, or ${f1(fastest.sw_med)} s at ${fastest.station}, which is ${f1(core - fastest.sw_med)} s faster than the rest`;
      txt += ". ";
      const starved = sws.filter((t) => (t.sw_gt10 || 0) > 25).sort((a, b) => b.sw_gt10 - a.sw_gt10);
      if (starved.length) {
        txt += "The tail is not the switch mechanism. It is the next robot not yet queued behind the one leaving, so the station waits. " +
          `That starvation is concentrated at ${runs(starved.map((t) => t.station))}, where more than a quarter of handovers take over 10 s.`;
      } else if (D.overall.gt10 != null) {
        txt += `Only ${f1(D.overall.gt10)}% of handovers take over 10 s, so no station is waiting on robots in any regular way.`;
      }
      out.switch = txt;
    }

    // operator time: split fast and slow at the biggest jump between medians
    const op = T.filter((t) => t.op_med != null).sort((a, b) => a.op_med - b.op_med);
    if (op.length >= 2) {
      let best = 0, cut = 0;
      for (let i = 0; i < op.length - 1; i++) {
        const ratio = op[i + 1].op_med / Math.max(op[i].op_med, 0.1);
        if (ratio > best) { best = ratio; cut = i; }
      }
      if (best >= 2) {
        let fast = op.slice(0, cut + 1);
        const slow = op.slice(cut + 1);
        const tail = (t) => t.op_p90 && t.op_med && t.op_p90 / t.op_med > 4;
        const between = fast.slice(1).filter(tail);
        fast = fast.filter((t) => !between.includes(t));
        const rng = (xs) => `${f0(Math.min(...xs.map((t) => t.op_med)))}–${f0(Math.max(...xs.map((t) => t.op_med)))} s`;
        let txt = `${runs(fast.map((t) => t.station))} run a fast pick pattern, ${rng(fast)} median per tote. ` +
          `${runs(slow.map((t) => t.station))} hold each tote ${rng(slow)}, which points to a different kind of work at those stations.`;
        if (between.length) {
          const w = between.reduce((a, b) => (b.op_p90 / b.op_med > a.op_p90 / a.op_med ? b : a));
          txt += ` ${runs(between.map((t) => t.station))} sit${between.length > 1 ? "" : "s"} in between with a long tail — a ${f0(w.op_med)} s median against a ${f0(w.op_p90)} s p90.`;
        }
        out.operator = txt;
      } else {
        out.operator = `Pick time is even across the stations, ${f0(op[0].op_med)}–${f0(op[op.length - 1].op_med)} s median per tote, with the day's middle 50% between ${f0(day.overall.op_p25)} and ${f0(day.overall.op_p75)} s.`;
      }
    }

    // cycles
    if (day.hourly) {
      const F = day.full_hours || [];
      let txt = "Divided by every robot of that type that logged an event in the hour.";
      if (F.length >= 3) {
        const lo = Math.min(...F), hi = Math.max(...F);
        const typical = median(day.hourly.filter((h) => F.includes(h.h)).map((h) => h.k50_per));
        const dips = day.hourly.filter((h) => h.h > lo && h.h < hi && h.k50_per && h.k50_per < 0.75 * typical).map((h) => h.h);
        if (dips.length) txt += ` The dips at ${hoursPhrase(dips)} are the hours with markedly fewer cycles per robot, which is what breaks look like: stations stop releasing totes and the fleet waits.`;
      }
      out.cycles = txt;
    }

    // robot utilization: on a task / between tasks / away, read with the supply
    const U = day.utilization || {};
    out.utilization = {};
    for (const role of ["K50", "ACR"]) {
      const u = U[role];
      if (!u || u.day.busy_full == null) continue;
      const dd = u.day;
      let txt = `In full-production hours, of the ${u.fleet} ${role}s that worked today an average of ${f0(dd.busy_full)} were on a task, ${f0(dd.idle_full)} between tasks and ${f0(dd.away_full)} away — without a task for 5 minutes or more, most likely charging, which the log does not record.`;
      txt += ` That is ${f0(dd.util_fleet_full)}% of the fleet, and ${f0(dd.util_available_full)}% of the robots available.`;
      if (dd.away_share_of_idle != null)
        txt += ` ${f0(dd.away_share_of_idle)}% of the time ${role}s spent without a task was in those long absences; the typical gap between tasks was ${f0(dd.median_gap_s)} s.`;
      if (role === "ACR" && dd.util_available_full < 70)
        txt += " ACRs spend a lot of time free between tasks, so the ACR fleet has genuine spare capacity.";
      out.utilization[role] = txt;
    }
    if (day.flow) {
      const fd = day.flow.day, st = day.flow.stations.filter((x) => x.ready_to_alloc_med != null);
      const slow = st.slice().sort((a, b) => b.ready_to_alloc_med - a.ready_to_alloc_med).slice(0, 3);
      let txt = `${fd.created.toLocaleString()} tasks were created. ${f0(fd.acr_leg_pct)}% needed an ACR to bring the tote from storage into the buffer, which took a median ${f0(fd.created_to_ready_med)} s. Once a tote was ready, a K50 was allocated within a median ${f0(fd.ready_to_alloc_med)} s, but 10% waited over ${f0(fd.ready_to_alloc_p90)} s.`;
      if (slow.length && slow[0].ready_to_alloc_med > 2 * (fd.ready_to_alloc_med || 1))
        txt += ` Totes for ${runs(slow.map((x) => x.station))} waited longest for a robot (median ${slow.map((x) => f0(x.ready_to_alloc_med) + " s").join(", ")}).`;
      out.supply = txt;
    }
    out.relations = relationText(D.relations, "today");
    if (D.multi) out.multi = multiText(D.multi, D.relations, "today");
    if (D.starve) out.starve = starveText(D.starve, "today");
    if (D.spatial) out.spatial = spatialText(D.spatial, "today");

    // station slots
    const SL = T.filter((t) => t.slot_limit != null);
    if (SL.length) {
      const groups = {};
      for (const t of SL) (groups[t.slot_limit] = groups[t.slot_limit] || []).push(t);
      const parts = Object.keys(groups).sort((a, b) => b - a).map((lim) => {
        const g = groups[lim];
        return `${runs(g.map((t) => t.station))} never hold much more than ${lim} tasks at a time and sit at that limit ${f0(Math.min(...g.map((t) => t.at_limit_pct)))}–${f0(Math.max(...g.map((t) => t.at_limit_pct)))}% of production time`;
      });
      let txt = parts.join("; ") + ". ";
      const st = (day.slots ? day.slots.stations : []).filter((x) => x.limit != null && x.ready_at_limit != null && x.ready_below_limit != null);
      if (st.length) {
        const ratio = mean(st.map((x) => x.ready_at_limit / Math.max(x.ready_below_limit, 0.1)));
        txt += `While a station is at its limit, about ${f1(ratio)}× as many of its totes sit ready in the buffer as when it has a free slot — work is released and retrieved, then held back until a slot opens. `;
      }
      const tight = SL.filter((t) => t.slots_needed != null && t.slots_needed >= 0.9 * t.slot_limit);
      if (tight.length) {
        const t0 = tight[0];
        txt += `With a median ${f0(t0.lead_med)} s from allocation to release, ${t0.station} needs about ${f1(t0.slots_needed)} tasks in flight to present ${f0(t0.target)} an hour (rate × lead time), against a limit of ${t0.slot_limit}: ${runs(tight.map((t) => t.station))} have almost no headroom, so any slowdown between allocation and arrival starves the station.`;
      }
      out.slots = txt.trim();
    }

    // budget, per zone
    out.budget = {};
    for (const z of D.budget.zones) {
      const R = z.rows;
      const over = R.filter((x) => x.over_s > 0), under = R.filter((x) => x.over_s <= 0);
      const avg = (k) => mean(R.map((x) => x[k]));
      const parts = [];
      const tgt = z.target ? `${f0(z.target)} an hour (${f2(3600 / z.target)} s a tote)` : "their targets";
      if (!over.length) parts.push(`Every station is inside ${tgt}.`);
      else if (!under.length) parts.push(`None of ${runs(z.stations)} make ${tgt}.`);
      else parts.push(`${runs(under.map((x) => x.station))} make ${tgt}; ${runs(over.map((x) => x.station))} do not.`);
      parts.push(`On average a tote spends ${f2(avg("pick_s"))} s being picked, ${f2(avg("switch_s"))} s on the switch and ${f2(avg("wait_s"))} s waiting for the next robot.`);
      const neverLate = mean(R.map((x) => x.rns));
      if (z.target) parts.push(`If robots were never late the stations would reach about ${f0(neverLate)} an hour, ${neverLate < z.target ? `still short of ${f0(z.target)}` : `clearing the ${f0(z.target)} target`}.`);
      const fastest = R.reduce((a, b) => (b.pick_s < a.pick_s ? b : a));
      if (fastest.over_s <= 0.05 * fastest.budget_s && R.length > 1)
        parts.push(`${fastest.station} is the useful comparison: its picks are the fastest (${f2(fastest.pick_s)} s), and it is the one closest to rate.`);
      if (z.long_pick_pct) parts.push(`Most of the excess pick time is in the tail: the ${f0(z.long_pick_pct)}% of picks over ${f0(C.long_pick_s)} s use ${f0(z.long_pick_time_pct)}% of all picking time.`);
      const worst = over.filter((x) => x.wait_s > x.wait_allowed_s && x.wait_allowed_s >= 0);
      if (worst.length) parts.push(`At ${runs(worst.map((x) => x.station))} the robots, not the picks, are what tips the tote over budget: waiting exceeds the time the budget has left after picking and switching.`);
      out.budget[z.zone] = parts.join(" ");
    }
    return out;
  }

  function methodNotes(day, D, C) {
    const items = [
      "Arrival is the CALLBACK_OF_ROBOT_REACH_STATION event. The shutter-door open command is issued in the same millisecond, so the door's physical travel never appears in the log.",
      `Release is the EssKubotStationHandleLetRobotGo "will leave" line — not CALLBACK_OF_TASK_FINISHED, which fires on arrival. ${(day.n_arrivals - day.n_unpaired).toLocaleString()} arrivals paired with a release by the same robot at the same station; ${day.n_unpaired.toLocaleString()} were left unpaired.`,
    ];
    if (D.door_day_off) items.push("Doors are switched off for this day (Settings), so every switch and pick time is used exactly as logged.");
    else items.push(D.door
      ? `Switch time is the measured release→arrival gap plus ${D.door} s of door travel. The same ${D.door} s is taken off the front of each visit's pick time, so the hour budget never counts those seconds twice. Operator time is reported as logged (arrival → release) and so still includes the door.` +
        (D.no_door.length ? ` ${runs(D.no_door)} ${D.no_door.length > 1 ? "have" : "has"} no door, so ${D.no_door.length > 1 ? "their" : "its"} switch and pick times are used exactly as logged.` : "")
      : "Switch time is the measured release→arrival gap, as logged. No door travel is added — set the door seconds on the Settings page (bottom of the left panel) to include it.");
    items.push(`Switch time is attributed to the station and hour of the first robot's arrival. Gaps longer than ${C.max_switch_s / 60} minutes are stand-downs rather than handovers and are left out of the switch statistics.`);
    if (day.utilization && (day.utilization.K50 || day.utilization.ACR)) {
      items.push("A robot is on a task from CALLBACK_OF_TASK_ALLOCATED until it puts the tote down — empty travel to the pickup included. ACR stores and relocations are not allocated in the log, so they count from the load and ACR utilization is a slight underestimate. Utilization is robots on task ÷ the robots of that type that did any work that day. The log has no charging or maintenance events, so a robot on charge counts as available: read it as a lower bound.");
    }
    if (day.robot_k50) {
      const k = day.robot_k50, a = day.robot_acr;
      items.push(`Robots are split by what they do, not by their number: a robot that reaches a station or works the haiflex buffer is a K50; the rest only move totes between shelf storage and the kubot buffer and are ACRs (${k.n} K50s, ${a.n} ACRs).`);
      items.push(`K50 cycles are counted when the tote returns to the buffer, and only if it was presented at a station first. ${f1(k.multi_station_pct || 0)}% of cycles (${k.multi_station_n.toLocaleString()}) were multi-station — the tote stopped at two or more stations before going back (${k.stations_per_cycle["2"].toLocaleString()} at two, ${k.stations_per_cycle["3+"].toLocaleString()} at three or more). ${k.no_visit_loads.toLocaleString()} loads went back without a station visit (mostly cancelled tasks) and are excluded.`);
      items.push(`ACR moves pair each unload with the same robot's earlier load of that tote. Besides puts and stores there were ${a.reloc.toLocaleString()} storage-to-storage relocations (${f1(a.reloc_rate || 0)} per robot-hour), which are digging moves to reach buried totes.`);
      items.push(`"Full-production hours" are the hours whose K50 cycles reached ${f0(C.full_hour_share * 100)}% of the busiest hour (${(day.full_hours || []).length} today).`);
    }
    const sk = day.skipped_stations || {};
    if (Object.keys(sk).length) {
      const total = Object.values(sk).reduce((a, b) => a + b, 0);
      items.push(`Non-operator drop points (${Object.keys(sk).sort().join(", ")}) took ${total.toLocaleString()} visits in total and are left out.`);
    }
    return items;
  }

  // ── across days ──────────────────────────────────────────────────────────
  const HEADLINE = [
    { key: "visits", label: "Totes presented", unit: "", better: 1 },
    { key: "rate", label: "Totes per hour, targeted stations", unit: "/h", better: 1 },
    { key: "over_s", label: "Seconds over budget per tote", unit: "s", better: -1 },
    { key: "op_med", label: "Median operator time", unit: "s", better: -1 },
    { key: "op_mean", label: "Average operator time", unit: "s", better: -1 },
    { key: "sw_med", label: "Median switch time", unit: "s", better: -1 },
    { key: "sw_mean", label: "Average switch time", unit: "s", better: -1 },
    { key: "wait_s", label: "Wait for robot per tote", unit: "s", better: -1 },
    { key: "util", label: "Time pickable", unit: "%", better: 1 },
    { key: "k50_per", label: "K50 cycles per robot", unit: "/h", better: 1 },
    { key: "k50_util", label: "K50 utilization", unit: "%", better: 0 },
    { key: "k50_avail_util", label: "K50 utilization, of available", unit: "%", better: 0 },
    { key: "k50_away", label: "K50s away", unit: "", better: -1 },
    { key: "ready", label: "Totes ready, no K50 yet", unit: "", better: -1 },
    { key: "acr_util", label: "ACR utilization", unit: "%", better: 0 },
    { key: "multi_pct", label: "Multi-station cycles", unit: "%", better: 0 },
    { key: "starved_multi_pct", label: "Starved handovers with a multi-station tote", unit: "%", better: -1 },
    { key: "multi_excess_pct", label: "Station waiting added by multi-station", unit: "%", better: -1 },
    { key: "starved_pct", label: "Starved handovers", unit: "%", better: -1 },
    { key: "travel_med", label: "Buffer pickup → station (median)", unit: "s", better: -1 },
  ];
  const BY_STATION = [
    { key: "visits", label: "Totes presented", unit: "", note: "Robot arrivals at the station across the whole day." },
    { key: "rate_full", label: "Totes per hour", unit: "/h", note: "Average totes presented per full-production hour — the like-for-like rate, since breaks would otherwise drag it down." },
    { key: "op_med", label: "Median operator time", unit: "s", note: "Median seconds from a robot reaching the station to the operator releasing it." },
    { key: "op_mean", label: "Average operator time", unit: "s", note: "Average seconds from a robot reaching the station to the operator releasing it — pulled up by long picks, unlike the median." },
    { key: "sw_med", label: "Median switch time", unit: "s", note: "Median seconds from release to the next robot arriving, plus the door seconds." },
    { key: "sw_mean", label: "Average switch time", unit: "s", note: "Average seconds from release to the next robot arriving, plus the door seconds — pulled up by long waits for a robot, unlike the median." },
    { key: "util", label: "Time pickable", unit: "%", note: "Percent of the day a tote was at the station and pickable, with the door seconds removed. Marked cells are below the share the station needs to hit its target." },
    { key: "at_limit_pct", label: "Time at slot limit", unit: "%", note: "Percent of production time the station held its maximum number of assigned tasks, so no more could be sent to it." },
  ];
  const BY_HOUR = [
    { key: "visits", label: "Totes presented", unit: "", note: "Robot arrivals across all stations in the hour." },
    { key: "k50_per", label: "K50 cycles per robot", unit: "/h", note: "Buffer-to-buffer cycles divided by the K50s active in the hour." },
    { key: "k50_util", label: "K50 utilization", unit: "%", note: "Average K50s on a task during the hour, as a percent of the K50s that worked that day." },
    { key: "acr_util", label: "ACR utilization", unit: "%", note: "Average ACRs on a task during the hour, as a percent of the ACRs that worked that day." },
  ];

  function computeSummary(days, derived, stations) {
    if (days.length < 2) return null;
    const rows = days.map((d, i) => {
      const D = derived[i];
      const B = D.budget.zones.flatMap((z) => z.rows);
      return {
        day: d.date, visits: d.overall.visits,
        rate: r(mean(B.map((x) => x.rate))),
        over_s: r(mean(B.map((x) => x.over_s)), 2),
        wait_s: r(mean(B.map((x) => x.wait_s)), 2),
        op_med: d.overall.op_med, op_mean: d.overall.op_mean, sw_med: D.overall.med, sw_mean: D.overall.mean,
        util: r(mean(D.stations.map((t) => t.util))),
        k50_per: d.robot_k50 ? d.robot_k50.rate : null,
        multi_pct: d.robot_k50 ? d.robot_k50.multi_station_pct : null,
        starved_multi_pct: D.multi ? D.multi.starved_multi_pct : null,
        multi_excess_pct: D.multi ? D.multi.excess_pct : null,
        starved_pct: D.starve ? D.starve.starved_pct : null,
        travel_med: D.starve ? D.starve.travel.med : null,
        k50_util: d.utilization && d.utilization.K50 ? d.utilization.K50.day.util_fleet_full : null,
        k50_avail_util: d.utilization && d.utilization.K50 ? d.utilization.K50.day.util_available_full : null,
        k50_away: d.utilization && d.utilization.K50 ? d.utilization.K50.day.away_full : null,
        ready: d.flow ? d.flow.day.ready_wait_full : null,
        acr_util: d.utilization && d.utilization.ACR ? d.utilization.ACR.day.util_fleet_full : null,
      };
    });
    const medians = {};
    for (const h of HEADLINE) medians[h.key] = r(median(rows.map((x) => x[h.key])), 2);

    const byStation = {};
    for (const key of BY_STATION.map((x) => x.key).concat(["target_util"])) {
      byStation[key] = stations.map((st) => days.map((d, i) => {
        const t = derived[i].stations.find((x) => x.station === st);
        return t ? t[key] : null;
      }));
    }
    const byHour = { visits: [], k50_per: [], k50_util: [], acr_util: [] };
    const utilAt = (d, role, h) => {
      const u = d.utilization && d.utilization[role];
      return u && u.hourly[h].busy ? u.hourly[h].util_fleet : null;
    };
    for (let h = 0; h < 24; h++) {
      byHour.visits.push(days.map((d) => { const v = d.hm_visits.map((row) => row[h]).filter((x) => x != null); return v.length ? v.reduce((a, b) => a + b, 0) : null; }));
      byHour.k50_per.push(days.map((d) => (d.hourly ? d.hourly[h].k50_per : null)));
      byHour.k50_util.push(days.map((d) => utilAt(d, "K50", h)));
      byHour.acr_util.push(days.map((d) => utilAt(d, "ACR", h)));
    }
    const hours = derived.flatMap((D) => D.hours);
    const S = { days: days.map((d) => d.date), stations, headline: rows, medians, by_station: byStation, by_hour: byHour,
                hours, relations: relations(hours) };
    S.multi = multiStats(days.map((d) => d.multi), days.map((d) => d.robot_k50 && d.robot_k50.stations_hist));
    S.text = summaryText(S);
    S.text.relations = relationText(S.relations, "across these days");
    if (S.multi) S.text.multi = multiText(S.multi, S.relations, "across these days");
    S.starve = starveStats(days.map((d) => d.starve));
    if (S.starve) S.text.starve = starveText(S.starve, "across these days");
    S.spatial = spatialStats(days.map((d) => d.spatial));
    if (S.spatial) S.text.spatial = spatialText(S.spatial, "across these days");
    return S;
  }

  function summaryText(S) {
    const out = {}, rows = S.headline, n = rows.length;
    const third = Math.max(Math.floor(n / 3), 1);
    const bits = [];
    for (const h of HEADLINE) {
      const a = mean(rows.slice(0, third).map((x) => x[h.key])), b = mean(rows.slice(-third).map((x) => x[h.key]));
      if (a == null || b == null || !a) continue;
      const pct = ((b - a) / Math.abs(a)) * 100;
      if (Math.abs(pct) < 5) continue;
      bits.push(`${h.label.toLowerCase()} ${pct > 0 ? "up" : "down"} ${f0(Math.abs(pct))}%`);
    }
    out.headline = `${n} days, ${rows[0].day} to ${rows[n - 1].day}. ` + (bits.length
      ? "Comparing the first and last third of the run: " + bits.join(", ") + "."
      : "Nothing moved by more than 5% between the first and last third of the run.");

    const spreads = [];
    S.stations.forEach((st, i) => {
      const v = S.by_station.op_med[i].filter((x) => x != null);
      if (v.length >= 3) spreads.push({ st, lo: Math.min(...v), hi: Math.max(...v) });
    });
    if (spreads.length) {
      spreads.sort((a, b) => (b.hi - b.lo) - (a.hi - a.lo));
      const w = spreads[0], s = spreads[spreads.length - 1];
      out.stations = `${w.st} is the least consistent station: its median pick time ranges ${f0(w.lo)}–${f0(w.hi)} s across the run, a ${f0(w.hi - w.lo)} s spread. ${s.st} is the steadiest, ${Math.round(s.lo) === Math.round(s.hi) ? `${f0(s.lo)} s on every day` : `${f0(s.lo)}–${f0(s.hi)} s`}.`;
    }

    const full = [], idle = [];
    S.by_hour.visits.forEach((row, h) => {
      if (row.every((v) => v != null)) full.push({ h, v: mean(row) });
      if (row.every((v) => !v)) idle.push(h);
    });
    if (full.length) {
      full.sort((a, b) => b.v - a.v);
      const busy = full[0], quiet = full[full.length - 1];
      out.hours = `Over the hours every day worked, ${String(busy.h).padStart(2, "0")}:00 is the busiest (${Math.round(busy.v).toLocaleString()} totes on an average day) and ${String(quiet.h).padStart(2, "0")}:00 the quietest (${Math.round(quiet.v).toLocaleString()}).`;
      if (idle.length) out.hours += ` No station ran at ${idle.map((h) => String(h).padStart(2, "0") + ":00").join(", ")} on any day of the run.`;
    }
    return out;
  }

  const ENGINE = {
    lowerBound, upperBound, quantile, prefix, doorSeconds, cappedPickSeconds, mergeSorted, hasDoor,
    prepare, targetFor, stationTargets, targetUtil, computeDay, hourPoints, relations, multiStats, multiText, HANDOVER_KINDS, starveStats, starveText, spatialStats, spatialText, CROWD_LABELS, histQuantile, STARVE_STAGES, CONTEXT_KINDS, HOUR_METRICS, HOUR_PRESETS, computeBudget, computeSummary, runs, natural, r, mean, median, corr,
    HEADLINE, BY_STATION, BY_HOUR,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = ENGINE;
  else root.ENGINE = ENGINE;
})(typeof window !== "undefined" ? window : globalThis);
