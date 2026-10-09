/*
 * report.js — the single-file report: navigation, live settings, visuals.
 *
 * REPORT (inlined by report.py) holds every day's door-independent numbers;
 * ENGINE (engine.js) turns them into everything that depends on the door
 * seconds and the station targets.  Changing either recomputes every day and
 * re-renders the open page.
 *
 * Every visual is drawn as SVG with resolved colours, so "Copy PNG" can
 * serialise it as-is.  Tables are drawn as SVG on demand for the same reason.
 */
(function () {
  "use strict";
  const E = ENGINE, R = REPORT, C = R.constants;
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const FONT = "'Source Sans 3','Segoe UI',Arial,sans-serif";

  // ── formatting ─────────────────────────────────────────────────────────
  const num = (v, nd = 1) => (v == null ? "–" : Number(v).toLocaleString(undefined, { maximumFractionDigits: nd }));
  const fix = (v, nd = 2) => (v == null ? "–" : Number(v).toFixed(nd));
  const int = (v) => (v == null ? "–" : Math.round(v).toLocaleString());
  const hh = (h) => String(h).padStart(2, "0");
  const pctl = (q) => { const n = Math.round(q * 100), t = n % 100 >= 11 && n % 100 <= 13 ? "th" : ["th", "st", "nd", "rd"][n % 10] || "th"; return n + t; };
  const prettyDate = (iso, opts = { weekday: "short", day: "numeric", month: "short", year: "numeric" }) =>
    new Date(iso + "T12:00:00").toLocaleDateString(undefined, opts);
  const slug = (s) => String(s).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");

  // ── palette (resolved, so exported SVG needs no stylesheet) ─────────────
  const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  let P = {};
  function readPalette() {
    P = {};
    for (const k of ["bg", "panel", "ink", "muted", "rule", "steel", "deep", "amber", "amber-soft", "slate", "bad", "bad-soft", "good", "h0", "h1"])
      P[k.replace("-", "_")] = css("--" + k);
  }
  function mix(a, b, t) {
    const p = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
    const A = p(a), B = p(b);
    return "rgb(" + A.map((x, i) => Math.round(x + (B[i] - x) * t)).join(",") + ")";
  }

  // ── state ──────────────────────────────────────────────────────────────
  const STORE = "ess-report:" + R.id;
  const clone = (o) => JSON.parse(JSON.stringify(o));
  function loadSettings() {
    let out = clone(R.defaults);
    try { const s = JSON.parse(localStorage.getItem(STORE)); if (s && typeof s.door_s === "number") out = s; } catch (e) { /* private window */ }
    for (const k of ["targets", "pick_s", "switch_s", "no_door", "no_door_days"]) out[k] = out[k] || {};
    return out;
  }
  function saveSettings() { try { localStorage.setItem(STORE, JSON.stringify(settings)); } catch (e) { /* ignore */ } }
  let settings = loadSettings();
  let derived = [], summary = null;
  const tabs = { dayHm: 0, sumLine: 0, sumSt: 1, sumHr: 0 };
  let budgetMode = "measured";                 // "measured" | "targets"

  function recompute() {
    derived = R.days.map((d) => E.computeDay(d, settings, R.zones, C));
    summary = E.computeSummary(R.days, derived, R.stations);
  }
  const isModified = () => JSON.stringify(settings) !== JSON.stringify(R.defaults);

  // ── toast + clipboard ──────────────────────────────────────────────────
  let toastTimer;
  function toast(msg) {
    const t = $("toast");
    t.textContent = msg; t.classList.add("on");
    clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove("on"), 2400);
  }
  function download(blob, name) {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = name;
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  }
  async function copyJson(obj, name) {
    const text = JSON.stringify(obj, null, 2);
    try { await navigator.clipboard.writeText(text); toast("JSON copied to the clipboard"); }
    catch (e) { download(new Blob([text], { type: "application/json" }), name + ".json"); toast("Clipboard unavailable — JSON downloaded instead"); }
  }

  // ── SVG building ───────────────────────────────────────────────────────
  let uid = 0;
  const T = (x, y, s, a = {}) => `<text x="${x}" y="${y}"${Object.entries(a).map(([k, v]) => ` ${k}="${v}"`).join("")}>${esc(s)}</text>`;
  /** Inline SVG that scales down to fit, but never up past its drawn size
   *  (a three-day heatmap would otherwise balloon to the full page width). */
  function svgMarkup(ch, minWidth = 0) {
    const style = `max-width:${ch.w}px` + (minWidth ? `;min-width:${Math.min(minWidth, ch.w)}px` : "");
    return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${ch.w} ${ch.h}" width="100%" font-family="${FONT}" font-size="12" style="${style}" role="img"${ch.label ? ` aria-label="${esc(ch.label)}"` : ""}>${ch.body}</svg>`;
  }
  function niceStep(range, target = 5) {
    const raw = range / target, mag = 10 ** Math.floor(Math.log10(raw || 1)), n = raw / mag;
    return (n >= 5 ? 10 : n >= 2 ? 5 : n >= 1 ? 2 : 1) * mag;
  }

  /** Render chart markup to a high-resolution PNG blob, with a title strip. */
  function pngBlob(ch, title, subtitle, scale = 3) {
    const pad = 20, head = subtitle ? 56 : 40, W = ch.w + pad * 2, H = ch.h + head + pad;
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${W * scale}" height="${H * scale}" viewBox="0 0 ${W} ${H}" font-family="${FONT}" font-size="12">`
      + `<rect width="${W}" height="${H}" fill="${P.panel}"/>`
      + `<text x="${pad}" y="${pad + 12}" font-size="17" font-weight="600" fill="${P.ink}">${esc(title)}</text>`
      + (subtitle ? `<text x="${pad}" y="${pad + 30}" font-size="12" fill="${P.muted}">${esc(subtitle)}</text>` : "")
      + `<svg x="${pad}" y="${head}" width="${ch.w}" height="${ch.h}" viewBox="0 0 ${ch.w} ${ch.h}">${ch.body}</svg></svg>`;
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => {
        const cv = document.createElement("canvas");
        cv.width = W * scale; cv.height = H * scale;
        cv.getContext("2d").drawImage(img, 0, 0);
        cv.toBlob((b) => (b ? resolve(b) : reject(new Error("PNG encoding failed"))), "image/png");
      };
      img.onerror = () => reject(new Error("Could not render the chart"));
      img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
    });
  }
  async function copyPng(ch, title, subtitle) {
    const name = slug(title + " " + (subtitle || ""));
    const blob = pngBlob(ch, title, subtitle);
    try {
      // A promise inside ClipboardItem keeps the user gesture alive (Safari).
      await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
      toast("PNG copied to the clipboard");
    } catch (e) {
      try { download(await blob, name + ".png"); toast("Clipboard unavailable — PNG downloaded instead"); }
      catch (err) { toast(err.message); }
    }
  }

  // ── chart renderers (all return {w, h, body}) ──────────────────────────
  function histChart(counts, width, unit) {
    const W = 520, H = 210, p = { l: 46, r: 12, t: 12, b: 36 }, n = counts.length, mx = Math.max(1, ...counts);
    const bw = (W - p.l - p.r) / n;
    let b = "";
    counts.forEach((c, i) => {
      const bh = ((H - p.t - p.b) * c) / mx;
      b += `<rect x="${p.l + i * bw + 0.5}" y="${H - p.b - bh}" width="${Math.max(bw - 1, 1)}" height="${bh}" fill="${P.steel}"><title>${num(i * width)}–${num((i + 1) * width)} ${unit}${i === n - 1 ? " and over" : ""}: ${c.toLocaleString()}</title></rect>`;
    });
    const step = Math.ceil(n / 10);
    for (let i = 0; i <= n; i += step) b += T(p.l + i * bw, H - 18, num(i * width) + (i === n ? "+" : ""), { "text-anchor": "middle", fill: P.muted });
    b += T(W - p.r, H - 2, unit, { "text-anchor": "end", fill: P.muted });
    b += T(p.l - 6, p.t + 10, mx.toLocaleString(), { "text-anchor": "end", fill: P.muted });
    b += T(p.l - 6, H - p.b, "0", { "text-anchor": "end", fill: P.muted });
    b += `<line x1="${p.l}" x2="${W - p.r}" y1="${H - p.b + 0.5}" y2="${H - p.b + 0.5}" stroke="${P.rule}"/>`;
    return { w: W, h: H, body: b, label: "Histogram" };
  }

  function cyclesChart(hourly) {
    const W = 1000, H = 300, p = { l: 40, r: 12, t: 14, b: 66 }, n = 24, bw = (W - p.l - p.r) / n;
    const vals = hourly.flatMap((h) => [h.k50_per, h.put_per, h.store_per]).filter((v) => v != null);
    const ymax = Math.max(5, Math.ceil((Math.max(...vals) * 1.1) / 5) * 5);
    const y = (v) => H - p.b - ((H - p.t - p.b) * v) / ymax;
    let b = "";
    for (let v = 0; v <= ymax; v += 5)
      b += `<line x1="${p.l}" x2="${W - p.r}" y1="${y(v)}" y2="${y(v)}" stroke="${P.rule}"/>` + T(p.l - 6, y(v) + 4, v, { "text-anchor": "end", fill: P.muted });
    hourly.forEach((r, i) => {
      if (r.k50_per != null)
        b += `<rect x="${p.l + i * bw + bw * 0.2}" y="${y(r.k50_per)}" width="${bw * 0.6}" height="${y(0) - y(r.k50_per)}" fill="${P.steel}"><title>${hh(r.h)}:00  K50 ${r.k50_per}/robot (${r.k50_cycles} cycles, ${r.k50_active} robots)</title></rect>`;
      b += T(p.l + i * bw + bw / 2, H - p.b + 16, hh(r.h), { "text-anchor": "middle", fill: P.muted });
    });
    const line = (k, col, what) => {
      let d = "", pen = false;
      hourly.forEach((r, i) => { if (r[k] == null) { pen = false; return; } d += (pen ? "L" : "M") + (p.l + i * bw + bw / 2) + "," + y(r[k]); pen = true; });
      return `<path d="${d}" fill="none" stroke="${col}" stroke-width="2.5"/>` +
        hourly.filter((r) => r[k] != null).map((r) => `<circle cx="${p.l + r.h * bw + bw / 2}" cy="${y(r[k])}" r="3.5" fill="${col}"><title>${hh(r.h)}:00  ${what} ${r[k]}/ACR</title></circle>`).join("");
    };
    b += line("put_per", P.amber, "puts") + line("store_per", P.deep, "stores");
    b += legend([[P.steel, "K50 cycles per robot"], [P.amber, "ACR puts per robot"], [P.deep, "ACR stores per robot"]], p.l, H - 18);
    return { w: W, h: H, body: b, label: "Cycles per robot by hour" };
  }

  /** A time-of-day frame: 288 five-minute slots, y in robots/totes. */
  function dayFrame(W, H, p, ymax, opts = {}) {
    const n = 288;
    const X = (i) => p.l + ((W - p.l - p.r) * i) / n;
    const Y = (v) => H - p.b - ((H - p.t - p.b) * v) / (ymax || 1);
    let b = "";
    const step = opts.pctOf ? opts.pctOf / 4 : niceStep(ymax, 5);
    for (let v = 0; v <= ymax + 1e-9; v += step) {
      b += `<line x1="${p.l}" x2="${W - p.r}" y1="${Y(v)}" y2="${Y(v)}" stroke="${P.rule}"/>` + T(p.l - 6, Y(v) + 4, Math.round(v), { "text-anchor": "end", fill: P.muted });
      if (opts.pctOf) b += T(W - p.r + 6, Y(v) + 4, Math.round((v / opts.pctOf) * 100) + "%", { fill: P.muted });
    }
    for (let hr = 0; hr < 24; hr++) b += T(X(hr * 12 + 6), H - p.b + 16, hh(hr), { "text-anchor": "middle", fill: P.muted });
    return { X, Y, b };
  }

  /**
   * Robots through the day, stacked: on a task, between tasks, away.  Whatever
   * is left up to the dashed fleet line is robots off shift.
   */
  function statesChart(u, role) {
    const W = 1000, H = 300, p = { l: 46, r: 58, t: 18, b: 62 };
    const ymax = Math.max(u.fleet, ...u.peak5) * 1.08;
    const { X, Y, b: grid } = dayFrame(W, H, p, ymax, { pctOf: u.fleet });
    let b = grid;
    const layers = [["busy5", P.steel, "on a task"], ["idle5", P.amber, "between tasks"], ["away5", P.slate, "away (≥ 5 min)"]];
    const base = new Array(288).fill(0);
    for (const [k, col] of layers) {
      const top = base.map((v, i) => v + (u[k][i] || 0));
      const up = top.map((v, i) => (i ? "L" : "M") + X(i + 0.5) + "," + Y(v)).join("");
      const down = base.map((v, i) => "L" + X(i + 0.5) + "," + Y(v)).reverse().join("");
      b += `<path d="${up}${down}Z" fill="${col}" fill-opacity="${k === "busy5" ? ".75" : ".55"}"/>`;
      top.forEach((v, i) => { base[i] = v; });
    }
    b += `<line x1="${p.l}" x2="${W - p.r}" y1="${Y(u.fleet)}" y2="${Y(u.fleet)}" stroke="${P.ink}" stroke-width="1.2" stroke-dasharray="6 4"/>`;
    b += T(p.l + 6, Y(u.fleet) - 6, `${u.fleet} ${role}s worked today`, { fill: P.ink, "font-weight": "600" });
    u.hourly.forEach((h, i) => {
      b += `<rect x="${X(i * 12)}" y="${p.t}" width="${X(12) - X(0)}" height="${H - p.t - p.b}" fill="transparent"><title>${hh(h.h)}:00  on a task ${num(h.busy)} · between tasks ${num(h.idle)} · away ${num(h.away)} · ${num(h.util_available)}% of available on a task</title></rect>`;
    });
    b += legend(layers.map(([, c, l]) => [c, l]).concat([[P.ink, "fleet that worked today"]]), p.l, H - 16);
    return { w: W, h: H, body: b, label: `${role} robots by state` };
  }

  /** Where idle robot-time goes, by the length of the gap between tasks. */
  function gapChart(bands, role) {
    const W = 760, rowH = 26, lw = 90, top = 8, H = top + bands.length * rowH + 40;
    const mx = Math.max(1, ...bands.map((x) => x.share || 0));
    const X = (v) => lw + ((W - lw - 170) * v) / mx;
    let b = "";
    bands.forEach((x, i) => {
      const y = top + i * rowH, col = x.away ? P.slate : P.amber;
      b += T(lw - 8, y + 16, x.band, { "text-anchor": "end", fill: P.ink, "font-size": "12" });
      b += `<rect x="${lw}" y="${y + 4}" width="${Math.max(X(x.share || 0) - lw, 0)}" height="${rowH - 9}" rx="2" fill="${col}"><title>${x.band}: ${x.gaps.toLocaleString()} gaps, ${num(x.robot_hours)} robot-hours</title></rect>`;
      b += T(X(x.share || 0) + 6, y + 16, `${num(x.share)}%  ·  ${x.gaps.toLocaleString()} gaps`, { fill: P.muted, "font-size": "12" });
    });
    b += legend([[P.amber, "between tasks (available)"], [P.slate, "away — 5 min or more"]], lw, H - 12);
    return { w: W, h: H, body: b, label: `${role} idle time by gap length` };
  }

  /** Multi-station cycles by the number of stations the tote visited (2, 3, … 8+). */
  function stationsHistChart(hist) {
    const bins = hist.slice(1), total = bins.reduce((a, b) => a + b, 0);
    const W = 620, H = 240, p = { l: 50, r: 12, t: 30, b: 40 }, n = bins.length, mx = Math.max(1, ...bins);
    const bw = (W - p.l - p.r) / n, Y = (v) => H - p.b - ((H - p.t - p.b) * v) / mx;
    let b = "";
    bins.forEach((c, i) => {
      const label = i === n - 1 ? `${i + 2}+` : String(i + 2), x = p.l + i * bw;
      b += `<rect x="${x + bw * 0.15}" y="${Y(c)}" width="${bw * 0.7}" height="${Y(0) - Y(c)}" fill="${P.amber}"><title>${label} stations: ${c.toLocaleString()} cycles (${num(total ? (c / total) * 100 : 0)}% of multi-station)</title></rect>`;
      b += T(x + bw / 2, Y(c) - 16, c.toLocaleString(), { "text-anchor": "middle", fill: P.ink, "font-size": "12", "font-weight": "600" });
      b += T(x + bw / 2, Y(c) - 3, total ? num((c / total) * 100) + "%" : "", { "text-anchor": "middle", fill: P.muted, "font-size": "11" });
      b += T(x + bw / 2, H - p.b + 16, label, { "text-anchor": "middle", fill: P.muted });
    });
    b += `<line x1="${p.l}" x2="${W - p.r}" y1="${Y(0) + 0.5}" y2="${Y(0) + 0.5}" stroke="${P.rule}"/>`;
    b += T(W - p.r, H - 4, "stations visited before returning to the buffer", { "text-anchor": "end", fill: P.muted });
    return { w: W, h: H, body: b, label: "Multi-station cycles by stations visited" };
  }

  const KIND_COL = () => ({ plain: P.slate, out: P.amber_soft, in: P.amber, both: P.bad });

  /** All handovers against the starved ones, each split by kind — is multi-station over-represented? */
  function handoverMixChart(M) {
    const W = 760, lw = 150, rowH = 44, top = 10, H = top + 2 * rowH + 44, col = KIND_COL();
    const rows = [["All handovers", (k) => k.n, M.handovers], [`Starved (> ${num(M.starved_s, 1)} s wait)`, (k) => k.starved, M.starved]];
    let b = "";
    rows.forEach(([label, val, tot], i) => {
      const y = top + i * rowH;
      b += T(lw - 10, y + 20, label, { "text-anchor": "end", fill: P.ink, "font-size": "12" });
      b += T(lw - 10, y + 34, `${tot.toLocaleString()}`, { "text-anchor": "end", fill: P.muted, "font-size": "11" });
      let x = lw;
      for (const k of M.kinds) {
        const w = tot ? ((W - lw - 10) * val(k)) / tot : 0;
        b += `<rect x="${x}" y="${y + 6}" width="${Math.max(w, 0)}" height="${rowH - 14}" fill="${col[k.key]}"><title>${esc(k.label)}: ${val(k).toLocaleString()} (${num(tot ? (val(k) / tot) * 100 : 0)}%)</title></rect>`;
        x += w;
      }
      const mul = tot ? ((tot - val(M.kinds[0])) / tot) * 100 : 0;
      const xm = lw + ((W - lw - 10) * (100 - mul)) / 100;
      b += `<line x1="${xm}" x2="${xm}" y1="${y + 2}" y2="${y + rowH - 4}" stroke="${P.ink}" stroke-width="1.5"/>`;
      b += T(xm - 6, y + 24, `${num(mul)}% multi-station →`, { "text-anchor": "end", fill: P.panel, "font-size": "12", "font-weight": "600" });
    });
    b += legend(M.kinds.map((k) => [col[k.key], k.label]), lw, H - 14);
    return { w: W, h: H, body: b, label: "Handovers and starved handovers by kind" };
  }

  /** Station waiting per handover, in station windows banded by their share of multi-station handovers. */
  function bandsChart(M) {
    const W = 620, H = 240, p = { l: 50, r: 12, t: 30, b: 44 }, bands = M.bands, n = bands.length;
    const mx = Math.max(1, ...bands.map((x) => x.mean_wait_s || 0));
    const bw = (W - p.l - p.r) / n, Y = (v) => H - p.b - ((H - p.t - p.b) * v) / mx;
    let b = "";
    bands.forEach((x, i) => {
      const v = x.mean_wait_s || 0, cx = p.l + i * bw;
      b += `<rect x="${cx + bw * 0.15}" y="${Y(v)}" width="${bw * 0.7}" height="${Y(0) - Y(v)}" fill="${i ? mix(P.h0, P.amber, 0.35 + 0.65 * (i / (n - 1))) : P.slate}"><title>${esc(x.band)}: ${num(x.mean_wait_s, 2)} s per handover over ${x.windows.toLocaleString()} windows (${x.handovers.toLocaleString()} handovers)</title></rect>`;
      b += T(cx + bw / 2, Y(v) - 16, `${num(x.mean_wait_s)} s`, { "text-anchor": "middle", fill: P.ink, "font-size": "12", "font-weight": "600" });
      b += T(cx + bw / 2, Y(v) - 3, `${x.windows.toLocaleString()} windows`, { "text-anchor": "middle", fill: P.muted, "font-size": "11" });
      b += T(cx + bw / 2, H - p.b + 16, x.band, { "text-anchor": "middle", fill: P.muted });
    });
    b += `<line x1="${p.l}" x2="${W - p.r}" y1="${Y(0) + 0.5}" y2="${Y(0) + 0.5}" stroke="${P.rule}"/>`;
    b += T(W - p.r, H - 4, `share of a ${M.window_min}-minute station window's handovers involving a multi-station tote`, { "text-anchor": "end", fill: P.muted });
    return { w: W, h: H, body: b, label: "Station waiting by multi-station share" };
  }

  /** The multi-station section — the same cards for a day and for the whole run. */
  function multiSection(M, text) {
    const s = section("Multi-station visits and starvation", "A multi-station cycle takes one tote to two or more stations before it goes back to the buffer. A station is starved when it waits for the next robot more than " + num(M.starved_s, 1) + " s beyond its usual handover. These cards ask whether the starved handovers are the ones with a multi-station tote.");
    s.append(card({
      title: "Multi-station and starvation, headline",
      tiles: () => [
        { v: num(M.multi_cycles_pct), u: "%", l: `of K50 cycles are multi-station (${int(M.multi_cycles)} of ${int(M.cycles)})` },
        { v: num(M.starved_multi_pct), u: "%", l: `of starved handovers involve a multi-station tote (${int(M.starved_multi)} of ${int(M.starved)})` },
        { v: num(M.multi_pct), u: "%", l: "of all handovers do — the share to compare against" },
        { v: num(M.lift, 2), u: "×", l: "over-representation among starved handovers", tone: M.lift >= 1.5 ? "bad" : null },
        { v: num(M.excess_pct), u: "%", l: `of station waiting added by multi-station (${num(M.excess_h)} h of ${num(M.wait_h)} h)`, tone: M.excess_pct >= 15 ? "bad" : null },
      ],
      data: () => { const { stations, bands, kinds, ...rest } = M; return rest; },
      explain: () => X.multiTiles(M),
    }));
    s.append(two(
      card({ title: "Stations per multi-station cycle", sub: `${int(M.hist[0])} cycles visited one station`, chart: () => stationsHistChart(M.hist),
        data: () => ({ stations_visited: M.hist.map((c, i) => ({ stations: i === M.hist.length - 1 ? `${i + 1}+` : i + 1, cycles: c })) }),
        explain: () => X.multiHist(M) }),
      card({ title: "Station waiting by multi-station share", sub: `${M.window_min}-minute station windows`, chart: () => bandsChart(M),
        data: () => ({ window_min: M.window_min, bands: M.bands }), explain: () => X.multiBands(M) })));
    s.append(card({ title: "Starved handovers: how often is a multi-station tote involved?", chart: () => handoverMixChart(M), minWidth: 620,
      data: () => ({ starved_over_s: M.starved_s, kinds: M.kinds.map(({ note, ...k }) => k) }), explain: () => X.multiMix(M) }));
    s.append(para(text));
    s.append(card({
      title: "Station waiting by kind of handover",
      table: () => ({
        columns: ["Handover", "Handovers", "Share", "Wait per handover s", "Starved", "% starved", "Share of starved"],
        rows: M.kinds.map((k) => [k.label, int(k.n), num(M.handovers ? (k.n / M.handovers) * 100 : null) + "%", num(k.mean_wait_s, 2), int(k.starved), num(k.starved_pct) + "%", num(M.starved ? (k.starved / M.starved) * 100 : null) + "%"]),
        flags: M.kinds.map((k) => [null, null, null, k.key !== "plain" && M.plain_wait_s != null && k.mean_wait_s > 1.5 * M.plain_wait_s ? "over" : null, null, null, null]),
      }),
      data: () => M.kinds, explain: () => X.multiKinds(),
    }));
    s.append(card({
      title: "Multi-station starvation by station",
      table: () => ({
        columns: ["Station", "Handovers", "% multi-station", "Wait, plain s", "Wait, multi-station s", "Starved", "Starved with multi-station", "Waiting added, min", "of station waiting"],
        rows: M.stations.map((t) => [t.station, int(t.handovers), num(t.multi_pct) + "%", num(t.plain_wait_s, 2), num(t.multi_wait_s, 2), int(t.starved), num(t.starved_multi_pct) + "%", num(Math.round(t.excess_min) || 0, 0), num(Math.round(t.excess_pct * 10) / 10 || 0) + "%"]),
        flags: M.stations.map((t) => [null, null, null, null, null, null, t.starved_multi_pct != null && t.multi_pct && t.starved_multi_pct >= 1.5 * t.multi_pct ? "over" : null, null, t.excess_pct >= 10 ? "over" : null]),
      }),
      data: () => M.stations, explain: () => X.multiStations(),
    }));
    return s;
  }

  /** The run's starvation threshold, as text — every day of a run is measured with the same one. */
  const starvedLabel = () => { const d = R.days.find((x) => x.starve); return d ? `${num(d.starve.starved_s, 1)} s` : "the starvation threshold"; };

  // ── why stations starve ────────────────────────────────────────────────
  const STAGE_COL = () => ({ no_task: P.slate, acr: P.deep, ready: P.bad, to_buffer: P.amber_soft, to_station: P.steel, chained: P.amber, unknown: P.rule });

  /** One 100% bar per row (all stations, then each), split by the stage the arriving robot was in. */
  function stageRowsChart(S) {
    const col = STAGE_COL(), stages = E.STARVE_STAGES;
    const rows = [{ station: "All stations", stages: Object.fromEntries(S.stages.map((x) => [x.key, x.pct])), wait_h: S.wait_h, bold: true }].concat(S.stations);
    const W = 900, lw = 110, rw = 70, rowH = 24, top = 6, H = top + rows.length * rowH + 50;
    let b = "";
    rows.forEach((row, i) => {
      const y = top + i * rowH + (i ? 8 : 0);
      b += T(lw - 8, y + 15, row.station, { "text-anchor": "end", fill: P.ink, "font-size": "12", ...(row.bold ? { "font-weight": "600" } : {}) });
      let x = lw;
      for (const st of stages) {
        const v = row.stages[st.key] || 0, w = ((W - lw - rw) * v) / 100;
        if (w > 0) b += `<rect x="${x}" y="${y + 3}" width="${w}" height="${rowH - 7}" fill="${col[st.key]}"><title>${esc(row.station)} — ${esc(st.label)}: ${num(v)}% of the waiting</title></rect>`;
        if (w > 34) b += T(x + w / 2, y + 16, `${num(v, 0)}%`, { "text-anchor": "middle", fill: st.key === "to_buffer" || st.key === "unknown" ? P.ink : P.panel, "font-size": "11" });
        x += w;
      }
      b += T(W - rw + 8, y + 15, `${num(row.wait_h)} h`, { fill: P.muted, "font-size": "12" });
    });
    b += legend(stages.filter((st) => rows.some((r) => (r.stages[st.key] || 0) > 0)).map((st) => [col[st.key], st.label]), lw, H - 14);
    return { w: W, h: H, body: b, label: "Where the next robot was while the station waited" };
  }

  /** Hours of station waiting in each hour of the day, stacked by stage. */
  function stageHoursChart(S) {
    const col = STAGE_COL(), stages = E.STARVE_STAGES, hasClosed = S.hourly.some((h) => h.closed > 0);
    const W = 1000, H = hasClosed ? 320 : 300, p = { l: 46, r: 12, t: 14, b: hasClosed ? 80 : 60 }, n = 24, bw = (W - p.l - p.r) / n;
    const tot = S.hourly.map((h) => stages.reduce((a, st) => a + (h.stages[st.key] || 0), 0) + (h.closed || 0));
    const ymax = Math.max(0.1, ...tot) * 1.08, step = niceStep(ymax, 5);
    const Y = (v) => H - p.b - ((H - p.t - p.b) * v) / ymax;
    let b = "";
    for (let v = 0; v <= ymax; v += step) b += `<line x1="${p.l}" x2="${W - p.r}" y1="${Y(v)}" y2="${Y(v)}" stroke="${P.rule}"/>` + T(p.l - 6, Y(v) + 4, num(v, 1), { "text-anchor": "end", fill: P.muted });
    S.hourly.forEach((h, i) => {
      let acc = 0;
      for (const st of stages) {
        const v = h.stages[st.key] || 0;
        if (v > 0) b += `<rect x="${p.l + i * bw + bw * 0.15}" y="${Y(acc + v)}" width="${bw * 0.7}" height="${Y(acc) - Y(acc + v)}" fill="${col[st.key]}"><title>${hh(h.h)}:00 — ${esc(st.label)}: ${num(v, 2)} h</title></rect>`;
        acc += v;
      }
      if (h.closed > 0) b += `<rect x="${p.l + i * bw + bw * 0.15}" y="${Y(acc + h.closed)}" width="${bw * 0.7}" height="${Y(acc) - Y(acc + h.closed)}" fill="none" stroke="${P.muted}" stroke-dasharray="3 2"><title>${hh(h.h)}:00 — station closed or disabled (not starvation): ${num(h.closed, 2)} h</title></rect>`;
      b += T(p.l + i * bw + bw / 2, H - p.b + 16, hh(h.h), { "text-anchor": "middle", fill: P.muted });
    });
    b += T(p.l, p.t - 2, "hours of station waiting", { fill: P.muted, "font-size": "11", dy: "8", dx: "4" });
    b += legend(stages.filter((st) => S.hourly.some((h) => h.stages[st.key] > 0)).map((st) => [col[st.key], st.label]), p.l, H - (hasClosed ? 34 : 14));
    if (hasClosed) b += `<rect x="${p.l}" y="${H - 24}" width="12" height="12" fill="none" stroke="${P.muted}" stroke-dasharray="3 2"/>` + T(p.l + 18, H - 14, "Station closed or disabled — not counted as starvation", { fill: P.muted, "font-size": "13" });
    return { w: W, h: H, body: b, label: "Station waiting by hour and stage" };
  }

  /** Starved share of handovers against a count or band — one bar per item. */
  function rateBarsChart(items, xLabel, o = {}) {
    const W = o.w || 620, H = 240, p = { l: 46, r: 12, t: 26, b: 44 }, n = items.length, bw = (W - p.l - p.r) / n;
    const mx = Math.max(1, ...items.map((x) => (x.n >= (o.minN || 1) ? x.starved_pct || 0 : 0))) * 1.12;
    const Y = (v) => H - p.b - ((H - p.t - p.b) * v) / mx;
    let b = "";
    const step = niceStep(mx, 5);
    for (let v = 0; v <= mx; v += step) b += `<line x1="${p.l}" x2="${W - p.r}" y1="${Y(v)}" y2="${Y(v)}" stroke="${P.rule}"/>` + T(p.l - 6, Y(v) + 4, num(v, 0) + "%", { "text-anchor": "end", fill: P.muted });
    items.forEach((x, i) => {
      const thin = x.n < (o.minN || 1), v = x.starved_pct || 0, cx = p.l + i * bw;
      b += `<rect x="${cx + bw * 0.12}" y="${Y(v)}" width="${bw * 0.76}" height="${Y(0) - Y(v)}" fill="${thin ? "none" : o.color || P.amber}" stroke="${o.color || P.amber}"><title>${esc(x.label)}: ${num(x.starved_pct)}% starved of ${x.n.toLocaleString()} handovers${thin ? " (too few to read)" : ""}</title></rect>`;
      if (!o.dense) b += T(cx + bw / 2, Y(v) - 4, x.n ? num(x.starved_pct) + "%" : "", { "text-anchor": "middle", fill: P.ink, "font-size": "12", "font-weight": "600" });
      if (!o.dense || i % 5 === 0 || i === n - 1) b += T(cx + bw / 2, H - p.b + 16, x.label, { "text-anchor": "middle", fill: P.muted, "font-size": o.dense ? "11" : "12" });
    });
    b += T(W - p.r, H - 4, xLabel, { "text-anchor": "end", fill: P.muted });
    b += T(p.l, 12, "% of handovers starved", { fill: P.muted, "font-size": "11" });
    return { w: W, h: H, body: b, label: `Starved handovers by ${xLabel}` };
  }

  /** Starved handovers by what was free at the moment the station began to wait. */
  function contextChart(S) {
    const W = 520, lw = 170, rowH = 34, top = 6, H = top + S.context.length * rowH + 10;
    const mx = Math.max(1, ...S.context.map((x) => x.pct || 0));
    const cols = { dispatch: P.bad, k50: P.amber, supply: P.slate, neither: P.rule };
    let b = "";
    S.context.forEach((x, i) => {
      const y = top + i * rowH, w = ((W - lw - 110) * (x.pct || 0)) / mx;
      b += T(lw - 8, y + 18, x.label, { "text-anchor": "end", fill: P.ink, "font-size": "12" });
      b += `<rect x="${lw}" y="${y + 5}" width="${w}" height="${rowH - 10}" rx="2" fill="${cols[x.key]}"><title>${esc(x.note)}: ${x.n.toLocaleString()} starved handovers, ${num(x.wait_h)} h of waiting</title></rect>`;
      b += T(lw + w + 6, y + 18, `${num(x.pct)}%  ·  ${x.n.toLocaleString()}`, { fill: P.muted, "font-size": "12" });
    });
    return { w: W, h: H, body: b, label: "Starved handovers by what was free" };
  }

  /** The starvation section — the same cards for a day and for the whole run. */
  function starveSection(S, text) {
    text = text || {};
    const s = section("Why stations starve", `Every handover — one robot released, the next arriving — traced back through the arriving robot's timeline: task created, tote ready in the buffer, K50 allocated, tote picked up, arrival. Waiting is the gap beyond the station's usual handover; starved means over ${num(S.starved_s, 1)} s of it. Full-production hours.`);
    const st = Object.fromEntries(S.stages.map((x) => [x.key, x]));
    const ctx = Object.fromEntries(S.context.map((x) => [x.key, x]));
    s.append(card({
      title: "Starvation, headline",
      tiles: () => [
        { v: num(S.starved_pct), u: "%", l: `of handovers starved (${int(S.starved)} of ${int(S.handovers)})` },
        { v: num(S.wait_h), u: "h", l: "stations spent waiting for robots" },
        { v: num(st.to_station.pct, 0), u: "%", l: "of that with the next robot already carrying the tote" },
        { v: num(st.no_task.pct, 0), u: "%", l: "before the next task was even created" },
        { v: num(ctx.dispatch.pct, 0), u: "%", l: "of starved moments had a free K50 and a ready tote" },
      ].concat(S.closed ? [{ v: num(S.closed.wait_h_full), u: "h", l: `left out: station closed or disabled (${int(S.closed.n_full)} handovers)` }] : []),
      data: () => ({ handovers: S.handovers, starved: S.starved, starved_pct: S.starved_pct, wait_h: S.wait_h, stages: S.stages, context: S.context, closed: S.closed }),
      explain: () => X.starveTiles(S),
    }));
    s.append(card({ title: "Where the next robot was while the station waited", sub: "share of waiting seconds", chart: () => stageRowsChart(S), minWidth: 700,
      data: () => ({ all: S.stages, stations: S.stations.map((x) => ({ station: x.station, wait_h: x.wait_h, stages_pct: x.stages })) }), explain: () => X.starveStages() }));
    s.append(para(text.stages));
    s.append(card({ title: "Station waiting by hour", sub: S.days > 1 ? `summed over ${S.days} days` : "", chart: () => stageHoursChart(S), minWidth: 680,
      data: () => S.hourly, explain: () => X.starveHours(S) }));
    const er = S.en_route.map((x) => ({ ...x, label: x.k === S.en_route.length - 1 ? `${x.k}+` : String(x.k) }));
    s.append(card({ title: "Robots on the way when a robot is released", sub: "K50s allocated to the station, not yet there", chart: () => rateBarsChart(er, "K50s on the way", { dense: true, minN: 30, w: 760 }), minWidth: 600,
      data: () => S.en_route, explain: () => X.starveEnRoute() }));
    s.append(para(text.pipeline));
    s.append(two(
      card({ title: "Buffer pickup → station arrival", sub: `median ${num(S.travel.med, 0)} s · p90 ${num(S.travel.p90, 0)} s`, chart: () => histChart(S.travel.hist, S.travel_w, "seconds"),
        data: () => ({ bin_width_s: S.travel_w, last_bin_is_open: true, counts: S.travel.hist, median_s: S.travel.med, p90_s: S.travel.p90, median_ending_starved_s: S.travel.starved_med, median_other_s: S.travel.other_med, allocation_to_pickup_median_s: S.travel.empty_med }),
        explain: () => X.starveTravel(S) }),
      card({ title: "What was free when starvation began", chart: () => contextChart(S),
        data: () => ({ context: S.context, free_k50_avg: S.free_k50_avg, ready_totes_avg: S.ready_here_avg }), explain: () => X.starveContext() })));
    s.append(para(text.context));
    s.append(two(
      card({ title: "Operator pace before the release", sub: "last 3 picks against the station's median", chart: () => rateBarsChart(S.pace.map((x) => ({ ...x, label: x.band })), "pace of the last 3 picks", { color: P.steel }),
        data: () => S.pace, explain: () => X.starvePace() }),
      card({ title: "Time since the station resumed", sub: "after a gap of 10 minutes or more", chart: () => rateBarsChart(S.resume.map((x) => ({ ...x, label: x.band })), "since the station resumed", { color: P.deep }),
        data: () => S.resume, explain: () => X.starveResume() })));
    s.append(para(text.patterns));
    if (S.pick && S.refill) {
      s.append(two(
        card({ title: "Starvation by the pick just released", sub: "operator time of the visit before the handover", chart: () => rateBarsChart(S.pick.bands.map((x) => ({ ...x, label: x.band.replace(" or more", "+") })), "operator time of the visit released", { color: P.steel, w: 640, minN: 30 }),
          data: () => S.pick, explain: () => X.starvePick(S) }),
        card({ title: "How soon the next robot gets in", sub: `starved after a pick under ${num(S.refill.pick_max_s, 0)} s · median ${num(S.refill.med, 0)} s`, chart: () => histChart(S.refill.hist, S.refill.w, "seconds, previous arrival → next arrival"),
          data: () => ({ bin_width_s: S.refill.w, last_bin_is_open: true, counts: S.refill.hist, n: S.refill.n, median_s: S.refill.med, p25_s: S.refill.p25, p75_s: S.refill.p75, pick_under_s: S.refill.pick_max_s }), explain: () => X.starveRefill(S) })));
      s.append(para(text.picks));
    }
    s.append(card({
      title: "Starvation by station",
      table: () => ({
        columns: ["Station", "Handovers", "Starved", "Waiting h", "per handover s", "Carrying the tote", "No task yet", "Usual K50s on the way", "Starved, pipeline under half", "Starved otherwise", "Starved after fast pick", "Starved after steady pick", "Next robot after a starve s", "Pickup → arrival s", "Tasks created /h", "Closed h"],
        rows: S.stations.map((x) => [x.station, int(x.handovers), num(x.starved_pct) + "%", num(x.wait_h), num(x.wait_per_s, 2), num(x.stages.to_station, 0) + "%", num(x.stages.no_task, 0) + "%", x.en_route_typical == null ? "–" : String(x.en_route_typical), x.starved_pct_low == null ? "–" : num(x.starved_pct_low) + "%", num(x.starved_pct_high) + "%", x.starved_pct_fast_pick == null ? "–" : num(x.starved_pct_fast_pick) + "%", x.starved_pct_steady_pick == null ? "–" : num(x.starved_pct_steady_pick) + "%", num(x.refill_med, 0), `${num(x.travel_med, 0)} (p90 ${num(x.travel_p90, 0)})`, num(x.created_per_h), num(x.closed_h)]),
        flags: S.stations.map((x) => [null, null, null, null, null, null, x.stages.no_task >= 25 ? "over" : null, null, x.starved_pct_low != null && x.starved_pct_high != null && x.starved_pct_low > 1.5 * x.starved_pct_high ? "over" : null, null, x.starved_pct_fast_pick != null && x.starved_pct_steady_pick != null && x.starved_pct_fast_pick >= 2 * x.starved_pct_steady_pick ? "over" : null, null, null, null, null, null]),
      }),
      data: () => S.stations, explain: () => X.starveStations(S),
    }));
    return s;
  }

  // ── where a K50 cycle's time goes ──────────────────────────────────────
  const SEG_COL = () => ({ fetch: P.slate, travel: P.steel, queue: P.bad, at_station: P.amber, return: P.deep });

  /** One 100% bar per row (all stations, then each), split by cycle segment; seconds per cycle on the right. */
  function segmentRowsChart(C) {
    const col = SEG_COL(), segs = E.K50_SEGMENTS;
    const rows = [{ station: "All stations", pct: Object.fromEntries(C.segments.map((x) => [x.key, x.pct])), per_cycle: Object.fromEntries(C.segments.map((x) => [x.key, x.per_cycle_s])), cycle_s: C.cycle_s, bold: true }].concat(C.stations);
    const W = 900, lw = 110, rw = 80, rowH = 24, top = 6, H = top + rows.length * rowH + 50;
    let b = "";
    rows.forEach((row, i) => {
      const y = top + i * rowH + (i ? 8 : 0);
      b += T(lw - 8, y + 15, row.station, { "text-anchor": "end", fill: P.ink, "font-size": "12", ...(row.bold ? { "font-weight": "600" } : {}) });
      let x = lw;
      for (const sg of segs) {
        const v = row.pct[sg.key] || 0, w = ((W - lw - rw) * v) / 100;
        if (w > 0) b += `<rect x="${x}" y="${y + 3}" width="${w}" height="${rowH - 7}" fill="${col[sg.key]}"><title>${esc(row.station)} — ${esc(sg.label)}: ${num(v)}% of the cycle, ${num(row.per_cycle[sg.key], 0)} s</title></rect>`;
        if (w > 34) b += T(x + w / 2, y + 16, `${num(v, 0)}%`, { "text-anchor": "middle", fill: P.panel, "font-size": "11" });
        x += w;
      }
      b += T(W - rw + 8, y + 15, `${num(row.cycle_s, 0)} s`, { fill: P.muted, "font-size": "12" });
    });
    b += legend(segs.map((sg) => [col[sg.key], sg.label]), lw, H - 14);
    return { w: W, h: H, body: b, label: "Where a K50 cycle's time goes" };
  }

  /** The cycle-time section — the same cards for a day and for the whole run. */
  function cycleTimeSection(C, text) {
    const sg = Object.fromEntries(C.segments.map((x) => [x.key, x]));
    const s = section("Where a K50 cycle's time goes", `Every K50 cycle split from its allocation to the tote's return to the buffer. Buffer pickup → station arrival is split into travel, at the free-flow pace for that station and buffer aisle, and queueing — the rest. Full-production hours.`);
    s.append(card({
      title: "Cycle time, headline",
      tiles: () => [
        { v: num(C.cycle_s, 0), u: "s", l: `average K50 cycle, allocation → back in the buffer (${int(C.n)} cycles)` },
        { v: num(sg.queue.pct, 0), u: "%", l: "of it queueing for the station", tone: sg.queue.pct >= 20 ? "bad" : null },
        { v: num(C.queue.med, 0), u: "s", l: "median queueing per cycle" },
        { v: num(C.rate_on_task, 1), u: "/h", l: "cycles per robot-hour on a task" },
        { v: num(C.rate_no_queue, 1), u: "/h", l: "the same without the queueing" },
      ],
      data: () => ({ n: C.n, cycle_s: C.cycle_s, rate_on_task: C.rate_on_task, rate_no_queue: C.rate_no_queue, segments: C.segments.map(({ note, ...x }) => x), queue_median_s: C.queue.med, queue_p90_s: C.queue.p90 }),
      explain: () => X.cycleTimeTiles(C),
    }));
    s.append(card({ title: "Where a K50 cycle's time goes", sub: "share of cycle time · average seconds per cycle on the right", chart: () => segmentRowsChart(C), minWidth: 700,
      data: () => ({ all: C.segments.map(({ note, ...x }) => x), stations: C.stations }), explain: () => X.cycleTimeSegments(C) }));
    s.append(para(text));
    s.append(two(
      card({ title: "Queueing per cycle", sub: `median ${num(C.queue.med, 0)} s · p90 ${num(C.queue.p90, 0)} s`, chart: () => histChart(C.queue.hist, C.queue_w, "seconds"),
        data: () => ({ bin_width_s: C.queue_w, last_bin_is_open: true, counts: C.queue.hist, median_s: C.queue.med, p90_s: C.queue.p90 }), explain: () => X.cycleTimeQueue(C) }),
      card({
        title: "Cycle time by station",
        table: () => ({
          columns: ["Station", "Cycles", "Multi-station", "Cycle s", "Fetch s", "Travel s", "Queueing s", "At station s", "Return s", "Queueing", "Per robot-hour", "Without queueing"],
          rows: C.stations.map((x) => [x.station, int(x.n), num(x.multi_pct) + "%", num(x.cycle_s, 0), num(x.per_cycle.fetch, 0), num(x.per_cycle.travel, 0), num(x.per_cycle.queue, 0), num(x.per_cycle.at_station, 0), num(x.per_cycle.return, 0), num(x.pct.queue, 0) + "%", num(x.rate_on_task, 1), num(x.rate_no_queue, 1)]),
          flags: C.stations.map((x) => [null, null, null, null, null, null, null, null, null, x.pct.queue >= 30 ? "over" : null, null, null]),
        }),
        data: () => C.stations, explain: () => X.cycleTimeStations(),
      })));
    return s;
  }

  /** One bar per item ({label, v, tip}), value printed on top. o: yLabel, xLabel, fmt, color, w. */
  function valueBarsChart(items, o = {}) {
    const W = o.w || 520, H = 240, p = { l: 46, r: 12, t: 26, b: 44 }, n = items.length, bw = (W - p.l - p.r) / Math.max(n, 1);
    const fmt = o.fmt || ((v) => num(v, 0)), dense = n > 12;
    const mx = Math.max(1e-9, ...items.map((x) => x.v || 0)) * 1.12;
    const Y = (v) => H - p.b - ((H - p.t - p.b) * v) / mx;
    let b = "";
    const step = niceStep(mx, 5);
    for (let v = 0; v <= mx; v += step) b += `<line x1="${p.l}" x2="${W - p.r}" y1="${Y(v)}" y2="${Y(v)}" stroke="${P.rule}"/>` + T(p.l - 6, Y(v) + 4, fmt(v), { "text-anchor": "end", fill: P.muted });
    items.forEach((x, i) => {
      const v = x.v || 0, cx = p.l + i * bw;
      b += `<rect x="${cx + bw * 0.12}" y="${Y(v)}" width="${bw * 0.76}" height="${Y(0) - Y(v)}" fill="${o.color || P.steel}"><title>${esc(x.tip || `${x.label}: ${fmt(v)}`)}</title></rect>`;
      if (!dense) b += T(cx + bw / 2, Y(v) - 4, x.v == null ? "" : fmt(v), { "text-anchor": "middle", fill: P.ink, "font-size": "12", "font-weight": "600" });
      b += T(cx + bw / 2, H - p.b + 16, x.label, { "text-anchor": "middle", fill: P.muted, "font-size": dense ? "11" : "12" });
    });
    if (o.xLabel) b += T(W - p.r, H - 4, o.xLabel, { "text-anchor": "end", fill: P.muted });
    if (o.yLabel) b += T(p.l, 12, o.yLabel, { fill: P.muted, "font-size": "11" });
    return { w: W, h: H, body: b, label: o.label || "Bars" };
  }

  // ── problem locations ──────────────────────────────────────────────────
  /** After a flagged pickup vs after a clean one: the next pickup by the same slot, tote and robot. */
  function repeatChart(B) {
    const groups = [["Same slot", "another tote", B.repeat.slot], ["Same tote", "another slot", B.repeat.tote], ["Same robot", "another slot", B.repeat.robot]];
    const W = 560, H = 250, p = { l: 46, r: 12, t: 26, b: 58 }, gw = (W - p.l - p.r) / groups.length;
    const mx = Math.max(1, ...groups.flatMap((g) => [g[2].after_flag_pct || 0, g[2].after_clean_pct || 0])) * 1.15;
    const Y = (v) => H - p.b - ((H - p.t - p.b) * v) / mx;
    let b = "";
    const step = niceStep(mx, 5);
    for (let v = 0; v <= mx; v += step) b += `<line x1="${p.l}" x2="${W - p.r}" y1="${Y(v)}" y2="${Y(v)}" stroke="${P.rule}"/>` + T(p.l - 6, Y(v) + 4, num(v, 0) + "%", { "text-anchor": "end", fill: P.muted });
    groups.forEach(([name, sub, g], i) => {
      const x0 = p.l + i * gw, bw = gw * 0.3;
      [[g.after_flag_pct, P.bad, "after a flagged pickup", g.after_flag_n], [g.after_clean_pct, P.steel, "after a clean pickup", g.after_clean_n]].forEach(([v, col, lbl, n], j) => {
        const x = x0 + gw * 0.17 + j * (bw + 6);
        b += `<rect x="${x}" y="${Y(v || 0)}" width="${bw}" height="${Y(0) - Y(v || 0)}" fill="${col}"><title>${esc(name)} (${esc(sub)}), ${lbl}: ${num(v)}% flagged of ${int(n)}</title></rect>`;
        b += T(x + bw / 2, Y(v || 0) - 4, num(v) + "%", { "text-anchor": "middle", fill: P.ink, "font-size": "12", "font-weight": "600" });
      });
      b += T(x0 + gw / 2, H - p.b + 18, name, { "text-anchor": "middle", fill: P.ink, "font-size": "12", "font-weight": "600" });
      b += T(x0 + gw / 2, H - p.b + 33, sub, { "text-anchor": "middle", fill: P.muted, "font-size": "11" });
    });
    b += legend([[P.bad, "after a flagged pickup"], [P.steel, "after a clean one"]], p.l, H - 4);
    b += T(p.l, 12, "% of next pickups flagged", { fill: P.muted, "font-size": "11" });
    return { w: W, h: H, body: b, label: "Does the trouble stay with the slot or the tote?" };
  }

  /** Problem locations — the same cards for a day and for the whole run. */
  function faultSection(F, text) {
    text = text || {};
    const B = F.buffer, St = F.storage;
    const s = section("Problem locations", "Do totes from particular places run into trouble? At the K50 buffer, a pickup is flagged when the slot reported that the robot's tries to take the tote went over the limit; in storage, put-aways that failed and totes the ACRs could not take out.");
    const tiles = [];
    if (B) tiles.push(
      { v: num(B.flagged_pct), u: "%", l: `of K50 buffer pickups needed extra tries (${int(B.flagged)} of ${int(B.pickups)})` },
      { v: int(B.bad_slots.length), u: "", l: `buffer slots flagged far above chance (${num(B.bad_slots_flag_pct, 0)}% of flags)`, tone: B.bad_slots.length ? "bad" : null },
      { v: int(B.bad_robots.length), u: "", l: `K50s flagged far above chance (${num(B.bad_robots_flag_pct, 0)}% of flags)` });
    if (St) {
      const d2 = St.depth[1], all = St.depth[0].failed + d2.failed;
      tiles.push(
        { v: all ? num((d2.failed / all) * 100, 0) : "–", u: all ? "%" : "", l: `of failed put-aways at rear (depth-2) slots (${int(all)} failed)` },
        { v: num(St.stuck_acr_h), u: "h", l: `ACR time on ${int(St.stuck.length)} stuck storage slot${St.stuck.length === 1 ? "" : "s"} (${int(St.stuck_attempts)} failed loads)`, tone: St.stuck_acr_h >= 1 ? "bad" : null });
    }
    s.append(card({ title: "Problem locations, headline", tiles: () => tiles, data: () => ({ buffer: B ? { pickups: B.pickups, flagged: B.flagged, flagged_pct: B.flagged_pct, bad_slots: B.bad_slots.length, bad_robots: B.bad_robots.length } : null, storage: St ? { depth: St.depth, stuck_attempts: St.stuck_attempts, stuck_acr_h: St.stuck_acr_h } : null }), explain: () => X.faultTiles(F) }));
    if (B) {
      s.append(card({
        title: "Where pickups need extra tries", sub: `share of K50 buffer pickups flagged, by aisle and bay · blank under ${B.cell_min_n} pickups`, minWidth: 900,
        chart: () => heatmapChart(B.aisles.map((a) => "aisle " + a), B.bays.map((y, j) => (j % 6 === 0 ? String(y) : "")), B.rate, { cw: 11, chh: 13, lw: 70, warn: true, cap: Math.max(10, Math.ceil(B.base_pct * 3)), fmt: () => "", legendFmt: (v) => num(v, 0) + "%", colTitle: (j) => "bay " + B.bays[j], label: "Flagged pickups by aisle and bay" }),
        data: () => ({ aisles: B.aisles, bays: B.bays, flagged_pct: B.rate, pickups: B.n, flagged: B.k }), explain: () => X.faultMap(B),
      }));
      s.append(para(text.buffer));
      s.append(two(
        card({ title: "Slot or tote?", sub: "the next pickup after a flagged one", chart: () => repeatChart(B), data: () => B.repeat, explain: () => X.faultRepeat() }),
        card({ title: "Flagged pickups by buffer aisle", chart: () => valueBarsChart(B.by_aisle.map((x) => ({ label: String(x.aisle), v: x.pct, tip: `aisle ${x.aisle}: ${num(x.pct)}% of ${int(x.n)} pickups` })), { fmt: (v) => num(v, 0) + "%", xLabel: "buffer aisle", yLabel: "% of pickups flagged", color: P.amber, w: 620 }),
          data: () => B.by_aisle, explain: () => X.faultAisle() })));
      s.append(two(
        card({ title: "Slots flagged far above chance", sub: `${int(B.bad_slots.length)} of ${int(B.slots_read)} slots read`, tall: true,
          table: () => ({ columns: ["Slot", "Pickups", "Flagged", "Rate", "× usual", "Days flagged"], rows: B.bad_slots.map((x) => [x.slot, int(x.n), int(x.k), num(x.pct) + "%", num(x.times_base, 1) + "×", int(x.days_flagged)]) }),
          data: () => B.bad_slots, explain: () => X.faultSlots(B) }),
        card({ title: "K50s flagged far above chance", sub: `${int(B.bad_robots.length)} of ${int(B.robots_read)} robots read`, tall: true,
          table: () => ({ columns: ["Robot", "Pickups", "Flagged", "Rate", "× usual"], rows: B.bad_robots.map((x) => [x.robot, int(x.n), int(x.k), num(x.pct) + "%", num(x.times_base, 1) + "×"]) }),
          data: () => B.bad_robots, explain: () => X.faultRobots(B) })));
      s.append(para(text.robots));
    }
    if (St) {
      s.append(two(
        card({ title: "Failed put-aways by slot depth", sub: "share of ACR put-aways into storage that failed",
          chart: () => valueBarsChart(St.depth.map((x) => ({ label: x.depth === "1" ? "front (depth 1)" : "rear (depth 2)", v: x.pct, tip: `${x.depth === "1" ? "front" : "rear"}: ${int(x.failed)} failed of ${int(x.putaways)} put-aways` })), { fmt: (v) => num(v, 2) + "%", yLabel: "% of put-aways failed", color: P.bad, w: 420 }),
          data: () => St.depth, explain: () => X.faultDepth() }),
        card({ title: "Stuck storage slots", sub: `${St.stuck_min}+ failed loads in a day`, tall: true,
          table: () => ({ columns: ["Slot", "Failed loads", "Days", "ACRs (busiest day)", "ACR h", "Tote"], rows: St.stuck.map((x) => [x.loc, int(x.attempts), int(x.days), int(x.robots_max), num(x.acr_h), x.totes.join(", ")]) }),
          data: () => St.stuck, explain: () => X.faultStuck(St) })));
      s.append(para(text.storage));
    }
    return s;
  }

  // ── robot health ───────────────────────────────────────────────────────
  /** Every robot's value, sorted, as thin bars around a median line; outliers in the warning colour. */
  function rankChart(items, o = {}) {
    const W = o.w || 620, H = 220, p = { l: 50, r: 12, t: 18, b: 34 }, n = items.length, bw = (W - p.l - p.r) / Math.max(n, 1);
    const vals = items.map((x) => x.v), lo = Math.min(...vals), hi = Math.max(...vals);
    const y0 = o.y0 != null ? Math.min(o.y0, lo) : lo - (hi - lo) * 0.1, y1 = hi + (hi - lo) * 0.1 || hi + 1;
    const Y = (v) => H - p.b - ((H - p.t - p.b) * (v - y0)) / (y1 - y0 || 1);
    let b = "";
    const step = niceStep(y1 - y0, 4);
    for (let v = Math.ceil(y0 / step) * step; v <= y1; v += step) b += `<line x1="${p.l}" x2="${W - p.r}" y1="${Y(v)}" y2="${Y(v)}" stroke="${P.rule}"/>` + T(p.l - 6, Y(v) + 4, (o.fmt || ((x) => num(x, 2)))(v), { "text-anchor": "end", fill: P.muted });
    items.forEach((x, i) => {
      const cx = p.l + i * bw, top = Y(Math.max(x.v, y0)), base = Y(y0);
      b += `<rect x="${cx + bw * 0.1}" y="${top}" width="${Math.max(bw * 0.8, 1)}" height="${Math.max(base - top, 1)}" fill="${x.warn ? P.bad : P.steel}"><title>${esc(x.tip)}</title></rect>`;
    });
    if (o.ref != null) b += `<line x1="${p.l}" x2="${W - p.r}" y1="${Y(o.ref)}" y2="${Y(o.ref)}" stroke="${P.ink}" stroke-dasharray="4 3"/>` + T(p.l + 4, Y(o.ref) - 4, o.refLabel || "", { fill: P.ink, "font-size": "11" });
    b += T(W - p.r, H - 6, o.xLabel || "robots, sorted", { "text-anchor": "end", fill: P.muted });
    if (o.yLabel) b += T(p.l, 10, o.yLabel, { fill: P.muted, "font-size": "11" });
    return { w: W, h: H, body: b, label: o.label || "By robot" };
  }

  /** Robot health — the same cards for a day and for the whole run. */
  function robotSection(R, text) {
    text = text || {};
    const K = R.k50, A = R.acr, many = R.days >= 4;
    const s = section("Robot health", "Robot by robot: how fast each one works against the usual time for the same work, and whether its faults are its own or the fleet's. A fault kind that piles up on a few robots points at those robots; one spread like chance points at the system or the process.");
    const bound = R.kinds.filter((k) => k.robot_bound);
    s.append(card({
      title: "Robot health, headline",
      tiles: () => [
        { v: K.robots ? `${num(K.cph_p5, 1)}–${num(K.cph_p95, 1)}` : "–", u: "/h", l: "K50 cycles per hour on a task, 5th–95th percentile of robots" },
        { v: int(K.slow.length + A.slow.length), u: "", l: `robots ${num((R.slow_index - 1) * 100, 0)}%+ slower than usual for their work`, tone: K.slow.length + A.slow.length ? "bad" : null },
        { v: int(bound.length), u: "", l: `fault kinds that pile up on particular robots (of ${R.kinds.length})` },
        { v: int(R.robots_flagged), u: "", l: "robots with a fault kind well above their fleet", tone: R.robots_flagged ? "bad" : null },
      ],
      data: () => ({ k50: K, acr: A, kinds: R.kinds.length, robot_bound: bound.map((k) => k.key), robots_flagged: R.robots_flagged }),
      explain: () => X.robotTiles(R),
    }));
    const k50rows = R.rows.filter((x) => x.fleet === "K50" && x.cph != null && x.trips >= R.min_trips).sort((a, b) => a.cph - b.cph);
    const acrRows = R.rows.filter((x) => x.fleet === "ACR" && x.index != null && x.trips >= R.min_trips).sort((a, b) => a.index - b.index);
    s.append(two(
      card({ title: "K50 cycles per hour on a task", sub: `${int(k50rows.length)} robots, sorted`, chart: () => rankChart(k50rows.map((x) => ({ v: x.cph, tip: `${x.robot}: ${num(x.cph, 2)} cycles per hour on a task (${int(x.trips)} cycles)` })), { ref: K.cph_med, refLabel: "median", fmt: (v) => num(v, 1), yLabel: "cycles per hour on a task", label: "K50 cycles per hour by robot" }),
        data: () => k50rows.map((x) => ({ robot: x.robot, cycles_per_hour_on_task: x.cph, cycles: x.trips })), explain: () => X.robotCph(R) }),
      card({ title: "ACR handling speed", sub: `load → unload in the buffer · 1 = usual for the rack level`, chart: () => rankChart(acrRows.map((x) => ({ v: x.index, warn: x.index >= R.slow_index, tip: `${x.robot}: ${num(x.index, 3)} × usual (${int(x.trips)} puts)` })), { ref: 1, refLabel: "usual", fmt: (v) => num(v, 2), yLabel: "× usual handling time", label: "ACR handling speed by robot" }),
        data: () => acrRows.map((x) => ({ robot: x.robot, handling_index: x.index, puts: x.trips })), explain: () => X.robotHandle(R) })));
    s.append(para(text.perf));
    s.append(card({
      title: "Which faults belong to particular robots?",
      table: () => ({
        columns: ["Fault", "Fleet", "Events", "Robots with any", "Concentration", ...(many ? ["Same robots, both halves of the run"] : []), "Robots above chance", "Most"],
        rows: R.kinds.map((k) => [k.label, k.fleet, int(k.events), `${int(k.robots_with)} of ${int(k.robots)}`, num(k.dispersion, 1) + "×", ...(many ? [k.consistency == null ? "–" : num(k.consistency, 2)] : []), int(k.above), k.top.join(", ") || "–"]),
        flags: R.kinds.map((k) => [null, null, null, null, k.robot_bound ? "over" : null, ...(many ? [k.consistency >= 0.5 ? "over" : null] : []), null, null]),
      }),
      data: () => R.kinds, explain: () => X.robotKinds(R),
    }));
    s.append(para(text.faults));
    s.append(two(
      card({ title: "Robots to check", sub: "a fault kind well above the fleet's rate for the robot's work", tall: true,
        table: () => ({ columns: ["Robot", "Fleet", "Fault", "Events", "Expected", "× expected"], rows: R.flagged.map((x) => [x.robot, x.fleet, x.label, int(x.events), num(x.expected, 1), num(x.times, 1) + "×"]) }),
        data: () => R.flagged, explain: () => X.robotFlagged(R) }),
      card({ title: "Every robot", tall: true,
        table: () => ({ columns: ["Robot", "Fleet", "Tasks", "Cycles / puts", "Cycles /h on task", "Speed (× usual)", "Faults /1000 tasks", "Most common fault", "Buffer pickups flagged"],
          rows: R.rows.map((x) => [x.robot, x.fleet, int(x.tasks), int(x.trips), x.cph == null ? "–" : num(x.cph, 2), x.index == null ? "–" : num(x.index, 3), num(x.per_1000_tasks, 1), x.top_fault || "–", x.buffer_flag_pct == null ? "–" : num(x.buffer_flag_pct) + "%"]),
          flags: R.rows.map((x) => [x.above.length ? "over" : null, null, null, null, null, x.index >= R.slow_index ? "over" : null, null, null, null]) }),
        data: () => R.rows, explain: () => X.robotRows(R) })));
    return s;
  }

  /** Rack and buffer locations — the same cards for a day and for the whole run. */
  function spatialSection(Sp, text) {
    text = text || {};
    const S = Sp.sources, Rt = Sp.returns, T_ = Sp.travel, CR = Sp.crowd;
    const s = section("Where totes come from", "Where in the rack the ACRs take totes from, whether robots heading to the same aisle slow each other down, and how far the buffer aisle a tote waits in is from the station it goes to.");
    const travelSt = T_ ? T_.by_station.filter((x) => x.spread_s != null) : [];
    s.append(card({
      title: "Locations, headline",
      tiles: () => [
        { v: num(S.top_share), u: "%", l: `of puts from the busiest aisle (even: ${num(S.even_share)}%)` },
        { v: num(Rt.within_60_pct, 0), u: "%", l: "of stored totes taken out again within the hour" },
      ].concat(CR.ACR ? [{ v: num(CR.ACR.added_h_per_day), u: "h", l: "ACR time a day lost to sharing an aisle" }] : [])
        .concat(CR.K50 ? [{ v: num(CR.K50.added_h_per_day), u: "h", l: "K50 time a day lost to sharing a buffer aisle" }] : [])
        .concat(travelSt.length ? [{ v: num(E.mean(travelSt.map((x) => x.spread_s)), 0), u: "s", l: "farthest vs nearest buffer aisles, per trip" }] : []),
      data: () => ({ sources: { puts: S.puts, aisles: S.aisles.length, top_aisle: S.top_aisle, top_share: S.top_share, even_share: S.even_share }, returns: Rt, crowd: CR, travel_spread_s: travelSt.map((x) => ({ station: x.station, spread_s: x.spread_s })) }),
      explain: () => X.spatialTiles(),
    }));
    const lv = S.levels.slice().reverse();
    s.append(card({ title: "Where ACRs took totes from", sub: "puts by storage aisle and level", minWidth: 760,
      chart: () => heatmapChart(lv.map((x) => "level " + x), S.aisles.map(String), lv.map((x) => S.grid[S.levels.indexOf(x)]), { cw: 26, chh: 18, lw: 64, fmt: (x) => (x ? int(x) : ""), colTitle: (j) => "aisle " + S.aisles[j], label: "Puts by aisle and level" }),
      data: () => ({ levels: S.levels, aisles: S.aisles, puts_by_level_then_aisle: S.grid, by_aisle: S.by_aisle, by_level: S.by_level }),
      explain: () => X.spatialSources() }));
    s.append(para(text.sources));
    const cols = ["Fleet", "Trips", "Allocation → pickup s"].concat(E.CROWD_LABELS.map((l) => l === "alone" ? "Alone" : `With ${l}`)).concat(["Overlap vs chance", "Hours lost"]);
    const fleets = ["ACR", "K50"].filter((f) => CR[f]);
    if (fleets.length) {
      s.append(card({
        title: "Robots heading to the same aisle",
        sub: "share of trips · seconds added against a trip alone, fleet equally busy",
        table: () => ({
          columns: cols,
          rows: fleets.map((f) => { const c = CR[f]; return [f === "ACR" ? "ACR (storage aisle)" : "K50 (buffer aisle)", int(c.trips), num(c.lead_s, 0)]
            .concat(c.bins.map((b) => (b.k === 0 ? `${num(b.share, 0)}%` : `${num(b.share, 0)}% · ${b.added_s == null ? "–" : (b.added_s > 0 ? "+" : "") + num(b.added_s)} s`)))
            .concat([c.clustering == null ? "–" : num(c.clustering, 2) + "×", num(c.added_h)]); }),
          flags: fleets.map((f) => [null, null, null].concat(CR[f].bins.map((b) => (b.k && b.added_s >= 3 && b.n >= 30 ? "over" : null))).concat([CR[f].clustering >= 1.2 ? "over" : null, null])),
        }),
        data: () => fleets.map((f) => CR[f]), explain: () => X.spatialCrowd(Sp.limits),
      }));
      s.append(para(text.crowd));
    }
    s.append(two(
      card({ title: "Handling time by level", sub: "ACR load → unload, mean", chart: () => valueBarsChart(Sp.lift.map((x) => ({ label: String(x.level), v: x.handle_s, tip: `level ${x.level}: ${num(x.handle_s)} s mean over ${int(x.n)} puts` })), { xLabel: "storage level", yLabel: "seconds", fmt: (v) => num(v, 0) }),
        data: () => Sp.lift, explain: () => X.spatialLift(Sp.limits) }),
      card({ title: "Stored, then taken out again", sub: "time until the tote's next put", chart: () => valueBarsChart(Rt.bands.map((x) => ({ label: x.label.replace(" min", "").replace("not in the log", "never"), v: x.pct, tip: `${x.label}: ${int(x.n)} stores (${num(x.pct)}%)` })), { xLabel: "minutes until taken out again", yLabel: "% of stores", fmt: (v) => num(v, 0) + "%", color: P.amber }),
        data: () => ({ returns: Rt, totes_by_puts: Sp.repeats }), explain: () => X.spatialReturns() })));
    s.append(para(text.lift));
    s.append(para(text.returns));
    if (T_) {
      s.append(card({ title: "Buffer aisle → station", sub: "K50 pickup → arrival, mean seconds", minWidth: 760,
        chart: () => heatmapChart(T_.stations, T_.aisles.map(String), T_.mean, { cw: 30, chh: 22, fromZero: false, fmt: (x) => num(x, 0), colTitle: (j) => "buffer aisle " + T_.aisles[j], warn: true, label: "Pickup → arrival by buffer aisle" }),
        data: () => ({ stations: T_.stations, aisles: T_.aisles, mean_s: T_.mean, trips: T_.n, min_trips: Sp.limits.travel_min_n }),
        explain: () => X.spatialTravel(T_, Sp.limits) }));
      s.append(card({
        title: "Travel by station",
        table: () => ({
          columns: ["Station", "Trips", "Mean s", "Nearest quarter s", "Farthest quarter s", "Spread s", "From nearest quarter", "Random", "Saved if nearest s"],
          rows: T_.by_station.map((x) => [x.station, int(x.trips), num(x.mean_s, 0), num(x.near_s, 0), num(x.far_s, 0), num(x.spread_s, 0), x.near_share == null ? "–" : num(x.near_share, 0) + "%", x.near_share_even == null ? "–" : num(x.near_share_even, 0) + "%", num(x.gain_s, 0)]),
        }),
        data: () => T_.by_station, explain: () => X.spatialTravelTable(Sp.limits),
      }));
      s.append(para(text.travel));
    }
    return s;
  }

  /** Task supply through the day, against the K50s that were free or away. */
  function supplyChart(flow, k) {
    const W = 1000, H = 300, p = { l: 46, r: 20, t: 18, b: 62 };
    const series = [
      ["ready5", flow.ready5, P.amber, "totes ready in the buffer, no K50 yet", true],
      ["supply5", flow.supply5, P.deep, "tasks waiting on an ACR", false],
    ];
    if (k) series.push(["away5", k.away5, P.slate, "K50s away", false], ["idle5", k.idle5, P.steel, "K50s between tasks", false]);
    const ymax = Math.max(5, ...series.flatMap((x) => x[1].filter((v) => v != null))) * 1.1;
    const { X, Y, b: grid } = dayFrame(W, H, p, ymax);
    let b = grid;
    for (const [, arr, col, , fill] of series) {
      const d = arr.map((v, i) => (i ? "L" : "M") + X(i + 0.5) + "," + Y(v || 0)).join("");
      if (fill) b += `<path d="${d}L${X(287.5)},${Y(0)}L${X(0.5)},${Y(0)}Z" fill="${col}" fill-opacity=".3"/>`;
      b += `<path d="${d}" fill="none" stroke="${col}" stroke-width="${fill ? 2 : 1.6}"/>`;
    }
    flow.hourly.forEach((h, i) => {
      b += `<rect x="${X(i * 12)}" y="${p.t}" width="${X(12) - X(0)}" height="${H - p.t - p.b}" fill="transparent"><title>${hh(h.h)}:00  created ${h.created} · ready, no K50 ${num(h.ready_wait)} · waiting on ACR ${num(h.supply_wait)}${k ? ` · K50s away ${num(k.hourly[i].away)} · between tasks ${num(k.hourly[i].idle)}` : ""}</title></rect>`;
    });
    b += legend(series.map((x) => [x[2], x[3]]), p.l, H - 16);
    return { w: W, h: H, body: b, label: "Task supply through the day" };
  }

  /** Any two hour-level measures against each other, coloured by totes per hour. */
  function hourScatter(points, xk, yk) {
    const M = Object.fromEntries(E.HOUR_METRICS.map((m) => [m.key, m]));
    const pts = points.filter((q) => q[xk] != null && q[yk] != null);
    const W = 900, H = 420, p = { l: 60, r: 20, t: 34, b: 52 };
    if (pts.length < 2) return { w: W, h: 60, body: T(W / 2, 30, "Not enough full-production hours", { "text-anchor": "middle", fill: P.muted }) };
    const xs = pts.map((q) => q[xk]), ys = pts.map((q) => q[yk]), ts = pts.map((q) => q.totes);
    const pad = (lo, hi) => { const d = (hi - lo) || Math.abs(hi) || 1; return [lo - d * 0.06, hi + d * 0.06]; };
    const [x0, x1] = pad(Math.min(...xs), Math.max(...xs)), [y0, y1] = pad(Math.min(...ys), Math.max(...ys));
    const X = (v) => p.l + ((W - p.l - p.r) * (v - x0)) / (x1 - x0), Y = (v) => H - p.b - ((H - p.t - p.b) * (v - y0)) / (y1 - y0);
    let b = "";
    const sy = niceStep(y1 - y0, 5), sx = niceStep(x1 - x0, 6);
    for (let v = Math.ceil(y0 / sy) * sy; v <= y1; v += sy) b += `<line x1="${p.l}" x2="${W - p.r}" y1="${Y(v)}" y2="${Y(v)}" stroke="${P.rule}"/>` + T(p.l - 6, Y(v) + 4, num(v, 2), { "text-anchor": "end", fill: P.muted });
    for (let v = Math.ceil(x0 / sx) * sx; v <= x1; v += sx) b += T(X(v), H - p.b + 16, num(v, 2), { "text-anchor": "middle", fill: P.muted });
    // least-squares line
    const mx = E.mean(xs), my = E.mean(ys);
    let sxy = 0, sxx = 0;
    xs.forEach((x, i) => { sxy += (x - mx) * (ys[i] - my); sxx += (x - mx) ** 2; });
    if (sxx) { const k = sxy / sxx, c0 = my - k * mx; b += `<line x1="${X(x0)}" y1="${Y(c0 + k * x0)}" x2="${X(x1)}" y2="${Y(c0 + k * x1)}" stroke="${P.amber}" stroke-width="2" stroke-dasharray="6 4"/>`; }
    const tlo = Math.min(...ts), thi = Math.max(...ts);
    pts.forEach((q) => {
      const t = thi === tlo ? 0.6 : 0.25 + 0.75 * ((q.totes - tlo) / (thi - tlo));
      b += `<circle cx="${X(q[xk])}" cy="${Y(q[yk])}" r="5" fill="${mix(P.h0, P.h1, t)}" stroke="${P.panel}" stroke-width=".8"><title>${q.day} ${hh(q.h)}:00 — ${esc(M[xk].label)} ${num(q[xk], 2)}${M[xk].unit} · ${esc(M[yk].label)} ${num(q[yk], 2)}${M[yk].unit} · ${q.totes} totes</title></circle>`;
    });
    const rv = E.corr(xs, ys);
    b += T(p.l, 18, `r = ${rv == null ? "–" : rv.toFixed(2)} · ${pts.length} full-production hours`, { fill: P.ink, "font-weight": "600", "font-size": "13" });
    b += T(W - p.r, H - 8, `${M[xk].label}${M[xk].unit ? ` (${M[xk].unit})` : ""} →`, { "text-anchor": "end", fill: P.muted, "font-size": "12" });
    b += T(p.l, p.t - 4, `↑ ${M[yk].label}${M[yk].unit ? ` (${M[yk].unit})` : ""}`, { fill: P.muted, "font-size": "12", dy: "-14", dx: "260" });
    const gid = "t" + ++uid;
    b += `<defs><linearGradient id="${gid}"><stop offset="0" stop-color="${mix(P.h0, P.h1, 0.25)}"/><stop offset="1" stop-color="${P.h1}"/></linearGradient></defs>`;
    b += `<rect x="${W - p.r - 150}" y="10" width="90" height="8" rx="2" fill="url(#${gid})"/>` + T(W - p.r - 156, 18, `${tlo}`, { "text-anchor": "end", fill: P.muted, "font-size": "11" }) + T(W - p.r - 54, 18, `${thi} totes/h`, { fill: P.muted, "font-size": "11" });
    return { w: W, h: H, body: b, label: `${M[yk].label} against ${M[xk].label}` };
  }
  const hourSel = { x: E.HOUR_PRESETS[0].x, y: E.HOUR_PRESETS[0].y };

  /** The hour-by-hour comparison card: presets plus free choice of axes. */
  function hourCard(points, key, scope) {
    let c;
    const M = Object.fromEntries(E.HOUR_METRICS.map((m) => [m.key, m]));
    const build = () => card({
      key, title: "Hour by hour: what moves together", sub: scope,
      chart: () => hourScatter(points, hourSel.x, hourSel.y),
      data: () => ({ x: hourSel.x, y: hourSel.y, metrics: E.HOUR_METRICS, points: points.map((q) => ({ day: q.day, hour: q.h, x: q[hourSel.x], y: q[hourSel.y], totes: q.totes })), all_measures: points }),
      explain: () => X.hourScatter(),
      fill: (body) => {
        const rebuild = () => { const n = build(); c.replaceWith(n); c = n; };
        const presets = document.createElement("div");
        presets.className = "tabs";
        E.HOUR_PRESETS.forEach((pr) => {
          const b = document.createElement("button");
          b.type = "button"; b.textContent = pr.label;
          b.setAttribute("aria-pressed", String(pr.x === hourSel.x && pr.y === hourSel.y));
          b.onclick = () => { hourSel.x = pr.x; hourSel.y = pr.y; rebuild(); };
          presets.append(b);
        });
        const pick = document.createElement("div");
        pick.className = "axis-pick";
        const sel = (axis) => {
          const sl = document.createElement("select");
          sl.setAttribute("aria-label", axis + " axis");
          E.HOUR_METRICS.forEach((m) => { const o = document.createElement("option"); o.value = m.key; o.textContent = m.label; if (m.key === hourSel[axis]) o.selected = true; sl.append(o); });
          sl.onchange = () => { hourSel[axis] = sl.value; rebuild(); };
          const l = document.createElement("label"); l.append(axis === "x" ? "Across: " : "Up: ", sl); return l;
        };
        pick.append(sel("y"), sel("x"));
        const wrap = document.createElement("div");
        wrap.className = "scroll";
        wrap.innerHTML = svgMarkup(hourScatter(points, hourSel.x, hourSel.y), 600);
        body.append(presets, pick, wrap);
      },
    });
    c = build();
    return c;
  }

  function legend(items, x, y) {
    let b = "", cx = x;
    for (const [col, label, hatch] of items) {
      b += hatch
        ? `<rect x="${cx}" y="${y - 10}" width="12" height="12" fill="url(#${hatch})" stroke="${P.bad}"/>`
        : `<rect x="${cx}" y="${y - 10}" width="12" height="12" rx="2" fill="${col}"/>`;
      b += T(cx + 18, y, label, { fill: P.muted, "font-size": "13" });
      cx += 30 + label.length * 6.6;
    }
    return b;
  }

  /**
   * Heatmap.  Options: cw/lw (cell / label width), cap (colour scale stops
   * here; the number still shows the true value), warn (amber scale), fmt,
   * fromZero, colTitle(j), and targets — a value per row (o.targets) or per
   * cell (o.targetGrid).  Cells below their target get a red corner mark, and
   * per-row targets are listed in an extra column on the right.
   */
  function heatmapChart(rows, cols, grid, o = {}) {
    const cw = o.cw || 40, chh = o.chh || 26, lw = o.lw || 78, top = 26, gap = 2;
    const tgtOf = (i, j) => (o.targetGrid ? (o.targetGrid[i] || [])[j] : o.targets ? o.targets[i] : null);
    const hasTgt = (o.targets && o.targets.some((t) => t != null)) || (o.targetGrid && o.targetGrid.flat().some((t) => t != null));
    const tcol = o.targets && hasTgt ? 70 : 0;
    const W = lw + cols.length * (cw + gap) + 8 + tcol, Hgrid = top + rows.length * (chh + gap);
    const all = grid.flat().filter((v) => v != null);
    const vmax = all.length ? Math.max(...all) : 1, vmin = o.fromZero === false && all.length ? Math.min(...all) : 0;
    const top_ = o.cap ? Math.min(o.cap, vmax) : vmax;
    const lo = P.h0, hi = o.warn ? P.amber : P.h1;
    const fmtv = o.fmt || ((v) => num(v, v >= 100 ? 0 : 1));
    let b = "";
    cols.forEach((c, j) => { b += T(lw + j * (cw + gap) + cw / 2, 16, c, { "text-anchor": "middle", fill: P.muted, "font-weight": "600", "font-size": "11" }); });
    rows.forEach((label, i) => {
      const yy = top + i * (chh + gap);
      b += T(lw - 8, yy + chh / 2 + 4, label, { "text-anchor": "end", fill: P.ink, "font-size": "12" });
      grid[i].forEach((v, j) => {
        const x = lw + j * (cw + gap);
        if (v == null) { b += T(x + cw / 2, yy + chh / 2 + 4, "·", { "text-anchor": "middle", fill: P.rule }); return; }
        if (o.ofGrid) {
          // Two numbers per cell: presented, and possible at the target pick time.
          const of = (o.ofGrid[i] || [])[j], ratio = of ? v / of : null;
          const tt = ratio == null ? 0 : Math.max(0, Math.min(ratio, 1));
          b += `<rect x="${x}" y="${yy}" width="${cw}" height="${chh}" rx="2" fill="${ratio == null ? P.h0 : mix(lo, hi, tt)}"><title>${esc(label)} · ${esc(o.colTitle ? o.colTitle(j) : cols[j])} — ${v} presented${of ? ` of ${of} possible (${Math.round(ratio * 100)}%)` : ", no target pick time"}</title></rect>`;
          const ink = tt > 0.55 ? P.panel : P.ink;
          b += T(x + cw / 2, yy + 13, String(v), { "text-anchor": "middle", fill: ink, "font-weight": "700", "font-size": "11" });
          if (of) b += T(x + cw / 2, yy + 25, `of ${of}`, { "text-anchor": "middle", fill: ink, "font-size": "9", opacity: ".85" });
          return;
        }
        const t = top_ === vmin ? 0.5 : Math.max(0, Math.min((v - vmin) / (top_ - vmin), 1));
        const tg = tgtOf(i, j), below = tg != null && v < tg;
        b += `<rect x="${x}" y="${yy}" width="${cw}" height="${chh}" rx="2" fill="${mix(lo, hi, t)}"><title>${esc(label)} · ${esc(o.colTitle ? o.colTitle(j) : cols[j])} — ${esc(fmtv(v))}${tg != null ? ` (target ≥ ${esc(fmtv(tg))}${below ? ", below" : ""})` : ""}</title></rect>`;
        b += T(x + cw / 2, yy + chh / 2 + 4, fmtv(v), { "text-anchor": "middle", fill: t > 0.55 ? P.panel : P.ink, "font-weight": "600", "font-size": "11" });
        if (below) b += `<path d="M${x + cw - 10},${yy} L${x + cw},${yy} L${x + cw},${yy + 10} Z" fill="${P.bad}"/>`;
      });
      if (tcol) {
        const tg = o.targets[i];
        b += T(lw + cols.length * (cw + gap) + 12, yy + chh / 2 + 4, tg == null ? "–" : "≥ " + fmtv(tg), { fill: tg == null ? P.muted : P.deep, "font-weight": "700", "font-size": "12" });
      }
    });
    if (tcol) b += T(lw + cols.length * (cw + gap) + 12, 16, "Target", { fill: P.deep, "font-weight": "700", "font-size": "11" });
    const gid = "g" + ++uid, ly = Hgrid + 16;
    b += `<defs><linearGradient id="${gid}"><stop offset="0" stop-color="${lo}"/><stop offset="1" stop-color="${hi}"/></linearGradient></defs>`;
    const lfmt = o.legendFmt || fmtv;
    b += T(lw, ly + 9, o.ofGrid ? "0%" : lfmt(vmin), { "text-anchor": "end", fill: P.muted, "font-size": "12", dx: "-6" });
    b += `<rect x="${lw}" y="${ly}" width="180" height="10" rx="2" fill="url(#${gid})"/>`;
    b += T(lw + 186, ly + 9, o.ofGrid ? "100% of possible" : lfmt(top_) + (o.cap && vmax > o.cap ? "+" : ""), { fill: P.muted, "font-size": "12" });
    if (hasTgt) {
      const lx = lw + 250;
      b += `<path d="M${lx},${ly - 1} L${lx + 11},${ly - 1} L${lx + 11},${ly + 10} Z" fill="${P.bad}"/>` +
        T(lx + 18, ly + 9, "below the share needed to hit target", { fill: P.muted, "font-size": "12" });
    }
    return { w: W, h: ly + 18, body: b, label: o.label || "Heatmap" };
  }

  /**
   * The time budget per tote: one bar per station split into picking, switch
   * and waiting (mean seconds per tote), against the target's seconds per
   * tote.  A scale along the top runs 0 → target → slowest station.
   */
  function budgetChart(z) {
    const rows = z.rows, W = 1000, lw = 96, rw = 230, x0 = lw, x1 = W - rw;
    const slowest = rows.reduce((a, b) => (b.cycle_s > a.cycle_s ? b : a));
    const maxV = Math.max(slowest.cycle_s, ...rows.map((r) => r.budget_s)) * 1.04;
    const x = (v) => x0 + ((x1 - x0) * v) / maxV;
    const rowH = 38, barH = 22, top = 96, H = top + rows.length * rowH + 44;
    const sameTarget = z.target != null;
    const hatch = "h" + ++uid;
    let b = `<defs><pattern id="${hatch}" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="6" height="6" fill="${P.bad_soft}"/><line x1="0" y1="0" x2="0" y2="6" stroke="${P.bad}" stroke-width="2.2"/></pattern></defs>`;

    // ── the scale: 0 ─── target ─── slowest ──
    const tY = 46, tH = 12, budget = sameTarget ? 3600 / z.target : null;
    const step = niceStep(maxV, 6);
    for (let v = 0; v <= maxV + 1e-9; v += step) {
      b += `<line x1="${x(v)}" x2="${x(v)}" y1="${top - 8}" y2="${top + rows.length * rowH - 6}" stroke="${P.rule}"/>`;
      b += T(x(v), tY + tH + 16, num(v, 1) + " s", { "text-anchor": "middle", fill: P.muted, "font-size": "11" });
    }
    b += `<rect x="${x0}" y="${tY}" width="${x(slowest.cycle_s) - x0}" height="${tH}" rx="6" fill="${P.h0}" stroke="${P.rule}"/>`;
    if (budget) {
      b += `<rect x="${x0}" y="${tY}" width="${x(Math.min(budget, slowest.cycle_s)) - x0}" height="${tH}" rx="6" fill="${P.steel}" fill-opacity=".28" stroke="${P.steel}"/>`;
      if (slowest.cycle_s > budget)
        b += `<rect x="${x(budget)}" y="${tY}" width="${x(slowest.cycle_s) - x(budget)}" height="${tH}" fill="url(#${hatch})" stroke="${P.bad}"/>`;
      const lbl = `Target ${fix(budget)} s · ${num(z.target, 0)}/h`, lw_ = lbl.length * 6.6 + 18;
      const lx = Math.min(Math.max(x(budget) - lw_ / 2, x0), x1 - lw_);
      b += `<rect x="${lx}" y="6" width="${lw_}" height="22" rx="11" fill="${P.deep}"/>` + T(lx + lw_ / 2, 21, lbl, { "text-anchor": "middle", fill: P.panel, "font-weight": "600", "font-size": "12" });
      b += `<path d="M${x(budget) - 5},${tY - 9} L${x(budget) + 5},${tY - 9} L${x(budget)},${tY - 2} Z" fill="${P.deep}"/>`;
    }
    b += T(x0, tY - 6, "0", { "text-anchor": "middle", fill: P.muted, "font-size": "11", "font-weight": "600" });
    b += T(x(slowest.cycle_s), tY - 6, `slowest ${fix(slowest.cycle_s)} s`, { "text-anchor": "end", fill: slowest.over_s > 0 ? P.bad : P.muted, "font-size": "11", "font-weight": "600", dx: "4" });
    if (budget) b += `<line x1="${x(budget)}" x2="${x(budget)}" y1="${tY + tH}" y2="${top + rows.length * rowH - 6}" stroke="${P.deep}" stroke-width="1.5" stroke-dasharray="5 4"/>`;
    b += T(W - rw + 12, top - 14, "Per tote", { fill: P.muted, "font-size": "11", "font-weight": "600" });
    b += T(W - 70, top - 14, "Actual", { fill: P.muted, "font-size": "11", "font-weight": "600" });

    // ── rows ──
    rows.forEach((r, i) => {
      const yy = top + i * rowH, by = yy + (rowH - barH) / 2 - 4;
      b += T(lw - 10, by + barH / 2 + 5, r.station, { "text-anchor": "end", fill: P.ink, "font-size": "13", "font-weight": "600" });
      let cx = x0;
      for (const [k, col, txt, name] of [["pick_s", P.steel, P.panel, "Picking"], ["switch_s", P.slate, P.ink, "Switch"], ["wait_s", P.amber, P.ink, "Waiting for robot"]]) {
        const w = Math.max(x(r[k]) - x0, 0);
        b += `<rect x="${cx}" y="${by}" width="${w}" height="${barH}" fill="${col}"><title>${r.station} · ${name}: ${fix(r[k])} s per tote</title></rect>`;
        if (w >= 38) b += T(cx + w / 2, by + barH / 2 + 4, fix(r[k]), { "text-anchor": "middle", fill: txt, "font-size": "11", "font-weight": "600", stroke: col, "stroke-width": "3", "paint-order": "stroke" });
        cx += w;
      }
      if (r.over_s > 0) {
        // Outline and a hatched strip under the bar, so the segment numbers stay readable.
        const ox = x(r.budget_s), ow = x(r.cycle_s) - x(r.budget_s);
        b += `<rect x="${ox}" y="${by - 2}" width="${ow}" height="${barH + 4}" fill="${P.bad}" fill-opacity=".12" stroke="${P.bad}" stroke-width="1.5"><title>${r.station}: ${fix(r.over_s)} s over the ${fix(r.budget_s)} s budget</title></rect>`;
        b += `<rect x="${ox}" y="${by + barH + 4}" width="${ow}" height="6" fill="url(#${hatch})" stroke="${P.bad}" stroke-width=".8"/>`;
      }
      if (!sameTarget)
        b += `<line x1="${x(r.budget_s)}" x2="${x(r.budget_s)}" y1="${by - 6}" y2="${by + barH + 6}" stroke="${P.deep}" stroke-width="2.5"><title>${r.station} target ${num(r.target, 0)}/h = ${fix(r.budget_s)} s</title></line>`;
      const rx = W - rw + 12;
      b += T(rx, by + barH / 2 + 5, fix(r.cycle_s) + " s", { fill: P.ink, "font-size": "14", "font-weight": "700" });
      b += r.over_s > 0
        ? T(rx + 66, by + barH / 2 + 5, `+${fix(r.over_s)} s over`, { fill: P.bad, "font-size": "13", "font-weight": "600" })
        : T(rx + 66, by + barH / 2 + 5, `${fix(-r.over_s)} s spare`, { fill: P.good, "font-size": "13", "font-weight": "600" });
      b += T(W - 70, by + barH / 2 + 5, `${num(r.rate, 0)}/h`, { fill: P.muted, "font-size": "13" });
      b += `<line x1="${lw - 90}" x2="${W}" y1="${yy + rowH - 2}" y2="${yy + rowH - 2}" stroke="${P.rule}" stroke-opacity=".5"/>`;
    });
    const leg = [[P.steel, "Picking"], [P.slate, switchLegend(rows)], [P.amber, "Waiting for robot"], [null, "Over budget", hatch]];
    if (!sameTarget) leg.push([P.deep, "Station target"]);
    b += legend(leg, x0, H - 12);
    return { w: W, h: H, body: b, label: `Time budget per tote, ${z.zone}` };
  }
  const dayDoorsOff = (date) => !!(settings.no_door_days || {})[date];
  /** The door seconds that apply on the page being viewed (0 on a day with doors off). */
  const settingsDoor = () => (currentRoute && dayDoorsOff(currentRoute) ? 0 : Number(settings.door_s) || 0);
  const stationHasDoor = (st) => E.hasDoor(st, R.zones, settings);
  /** "Switch (incl. 1.5 s door)", or which stations it applies to when not all have one. */
  function switchLegend(rows) {
    const d = settingsDoor();
    if (!d) return "Switch";
    const without = rows.filter((r) => !stationHasDoor(r.station));
    if (!without.length) return `Switch (incl. ${d} s door)`;
    if (without.length === rows.length) return "Switch (no doors here)";
    return `Switch (incl. ${d} s door, not at ${E.runs(without.map((r) => r.station))})`;
  }

  /**
   * The same budget, read against the targets.  Each station gets two strips on
   * one seconds scale: the target (target pick | target switch | buffer — what
   * the target cycle leaves after them) and what happened (picking | switch |
   * waiting for robot).  Equal seconds are equal widths on both; past the
   * target line the actual strip is outlined red.
   */
  function budgetTargetChart(z) {
    const rows = z.rows.filter((r) => r.target_pick_s != null && r.target_switch_s != null);
    const W = 1000, lw = 96, rw = 250, x0 = lw, x1 = W - rw;
    const buffer = (r) => Math.max(r.wait_allowance_s, 0);
    const targetEnd = (r) => r.target_pick_s + r.target_switch_s + buffer(r);
    const maxV = Math.max(...rows.map((r) => Math.max(r.budget_s, targetEnd(r), r.cycle_s))) * 1.04;
    const x = (v) => x0 + ((x1 - x0) * v) / maxV;
    const overflow = rows.filter((r) => r.target_overflow_s > 0);
    const rowH = 50, tH = 14, aH = 22, gap = 3, top = overflow.length ? 92 : 70, H = top + rows.length * rowH + 52;
    const pale = (c) => mix(c, P.panel, 0.45);
    const bufCol = P.good;
    let b = "";
    const step = niceStep(maxV, 6);
    for (let v = 0; v <= maxV + 1e-9; v += step) {
      b += `<line x1="${x(v)}" x2="${x(v)}" y1="${top - 8}" y2="${top + rows.length * rowH - 6}" stroke="${P.rule}"/>`;
      b += T(x(v), top - 14, num(v, 1) + " s", { "text-anchor": "middle", fill: P.muted, "font-size": "11" });
    }
    if (z.target) {
      const bx = x(3600 / z.target), lbl = `Target ${fix(3600 / z.target)} s · ${num(z.target, 0)}/h`, lw_ = lbl.length * 6.6 + 18;
      const lx = Math.min(Math.max(bx - lw_ / 2, x0), x1 - lw_);
      b += `<rect x="${lx}" y="8" width="${lw_}" height="22" rx="11" fill="${P.deep}"/>` + T(lx + lw_ / 2, 23, lbl, { "text-anchor": "middle", fill: P.panel, "font-weight": "600", "font-size": "12" });
      b += `<line x1="${bx}" x2="${bx}" y1="32" y2="${top + rows.length * rowH - 6}" stroke="${P.deep}" stroke-width="1.5" stroke-dasharray="5 4"/>`;
    }
    const rx = W - rw + 12;
    b += T(rx, top - 30, "Actual", { fill: P.muted, "font-size": "11", "font-weight": "600" });
    b += T(rx, top - 14, "s per tote", { fill: P.muted, "font-size": "11" }) + T(rx + 74, top - 14, "totes/h", { fill: P.muted, "font-size": "11" });
    if (overflow.length) {
      const lo = Math.min(...overflow.map((r) => r.target_overflow_s)), hi = Math.max(...overflow.map((r) => r.target_overflow_s));
      b += T(x0, 52, `At ${E.runs(overflow.map((r) => r.station))} the pick and switch targets alone exceed the target cycle (by ${fix(lo)}${hi > lo ? "–" + fix(hi) : ""} s), so there is no buffer.`, { fill: P.bad, "font-size": "12", "font-weight": "600" });
    }
    // Bars first, labels after the red outline, so the outline never covers a number.
    let labels = "";
    const fits = (txt, w) => txt && w >= txt.length * 6.2 + 6;
    const seg = (cx, y, w, h, fill, tip, label, ink, alt) => {
      if (w <= 0) return "";
      const txt = fits(label, w) ? label : fits(alt, w) ? alt : null;
      if (txt) labels += T(cx + w / 2, y + h / 2 + 4, txt, { "text-anchor": "middle", fill: ink, "font-size": h < 16 ? "10" : "11", "font-weight": "600", stroke: fill, "stroke-width": "3", "paint-order": "stroke" });
      return `<rect x="${cx}" y="${y}" width="${w}" height="${h}" fill="${fill}"><title>${esc(tip)}</title></rect>`;
    };
    rows.forEach((r, i) => {
      const yy = top + i * rowH, ty = yy + 2, ay = ty + tH + gap;
      b += T(lw - 10, ty + (tH + gap + aH) / 2 + 5, r.station, { "text-anchor": "end", fill: P.ink, "font-size": "13", "font-weight": "600" });
      // target: pick | switch | buffer
      let cx = x0;
      for (const [name, v, col] of [["Target pick", r.target_pick_s, P.steel], ["Target switch", r.target_switch_s, P.slate], ["Buffer", buffer(r), bufCol]]) {
        const w = x(v) - x0;
        const lbl = name === "Buffer" ? `buffer ${fix(v)}` : fix(v);
        b += seg(cx, ty, w, tH, pale(col), `${r.station} · ${name}: ${fix(v)} s per tote${name === "Buffer" ? " — what the target cycle leaves for waiting after the pick and switch targets" : ""}`, lbl, P.ink, fix(v));
        cx += w;
      }
      // actual: picking | switch | waiting
      cx = x0;
      const diff = (a, t) => (t == null ? "" : ` (target ${fix(t)} s, ${a >= t ? "+" : "−"}${fix(Math.abs(a - t))})`);
      for (const [name, v, col, ink, t] of [["Picking", r.pick_s, P.steel, P.panel, r.target_pick_s], ["Switch", r.switch_s, P.slate, P.ink, r.target_switch_s], ["Waiting for robot", r.wait_s, P.amber, P.ink, buffer(r)]]) {
        const w = x(v) - x0;
        b += seg(cx, ay, w, aH, col, `${r.station} · ${name}: ${fix(v)} s per tote${diff(v, t)}${name === "Waiting for robot" ? " — against the buffer" : ""}`, fix(v), ink);
        cx += w;
      }
      if (r.cycle_s > r.budget_s) {
        const ox = x(r.budget_s);
        b += `<rect x="${ox}" y="${ay - 1}" width="${x(r.cycle_s) - ox}" height="${aH + 2}" fill="none" stroke="${P.bad}" stroke-width="2"><title>${r.station}: ${fix(r.over_s)} s per tote over the ${fix(r.budget_s)} s target</title></rect>`;
      }
      b += labels; labels = "";
      const yv = ay + aH / 2 + 5;
      b += T(rx, yv, fix(r.cycle_s), { fill: P.ink, "font-size": "14", "font-weight": "700" });
      b += T(rx + 74, yv, num(r.rate, 0), { fill: r.rate >= r.target ? P.good : P.ink, "font-size": "14", "font-weight": "700" });
      b += r.over_s > 0
        ? T(rx + 132, yv, `+${fix(r.over_s)} s over`, { fill: P.bad, "font-size": "13", "font-weight": "600" })
        : T(rx + 132, yv, `${fix(-r.over_s)} s spare`, { fill: P.good, "font-size": "13", "font-weight": "600" });
      b += `<line x1="${lw - 90}" x2="${W}" y1="${yy + rowH - 2}" y2="${yy + rowH - 2}" stroke="${P.rule}" stroke-opacity=".5"/>`;
    });
    b += legend([[P.steel, "Picking"], [P.slate, switchLegend(rows)], [P.amber, "Waiting for robot"], [pale(bufCol), "Buffer"]], x0, H - 28);
    b += T(x0, H - 8, "Top strip of each station: the target — pick, switch and the buffer left for waiting. Bottom strip: what happened. Outlined red: past the target.", { fill: P.muted, "font-size": "11" });
    return { w: W, h: H, body: b, label: `Time budget against targets, ${z.zone}` };
  }

  /** Tasks assigned to each station over the day, one panel per station. */
  function slotsChart(slots) {
    const S = slots.stations, cols = 2, pw = 480, ph = 132, gx = 20, gy = 16;
    const rowsN = Math.ceil(S.length / cols), W = cols * pw + (cols - 1) * gx, H = rowsN * (ph + gy) + 30;
    let b = "";
    S.forEach((st, k) => {
      const ox = (k % cols) * (pw + gx), oy = Math.floor(k / cols) * (ph + gy);
      const p = { l: 30, r: 8, t: 22, b: 18 };
      const ymax = Math.max(st.limit || 0, ...st.assigned5, ...st.ready5) * 1.12 || 1;
      const X = (i) => ox + p.l + ((pw - p.l - p.r) * i) / 288, Y = (v) => oy + ph - p.b - ((ph - p.t - p.b) * v) / ymax;
      b += `<rect x="${ox}" y="${oy}" width="${pw}" height="${ph}" fill="none" stroke="${P.rule}" rx="4"/>`;
      b += T(ox + 8, oy + 15, st.station, { fill: P.ink, "font-weight": "700", "font-size": "12" });
      b += T(ox + pw - 8, oy + 15, st.limit != null ? `limit ${st.limit} · at it ${num(st.at_limit_pct, 0)}% of production time` : "no limit reached", { "text-anchor": "end", fill: P.muted, "font-size": "11" });
      for (const v of st.limit != null ? [0, st.limit] : [0, Math.round(ymax / 1.12)]) b += T(ox + p.l - 4, Y(v) + 4, v, { "text-anchor": "end", fill: v && st.limit != null ? P.bad : P.muted, "font-size": "10", "font-weight": v && st.limit != null ? "700" : "400" });
      for (let h = 0; h <= 24; h += 6) b += T(X(h * 12), oy + ph - 4, hh(h % 24), { "text-anchor": "middle", fill: P.muted, "font-size": "10" });
      const area = st.assigned5.map((v, i) => (i ? "L" : "M") + X(i + 0.5) + "," + Y(v || 0)).join("");
      b += `<path d="${area}L${X(287.5)},${Y(0)}L${X(0.5)},${Y(0)}Z" fill="${P.steel}" fill-opacity=".35"/><path d="${area}" fill="none" stroke="${P.steel}" stroke-width="1.4"/>`;
      b += `<path d="${st.ready5.map((v, i) => (i ? "L" : "M") + X(i + 0.5) + "," + Y(v || 0)).join("")}" fill="none" stroke="${P.amber}" stroke-width="1.4"/>`;
      if (st.limit != null) b += `<line x1="${X(0)}" x2="${X(288)}" y1="${Y(st.limit)}" y2="${Y(st.limit)}" stroke="${P.bad}" stroke-width="1.2" stroke-dasharray="5 3"/>`;
    });
    b += legend([[P.steel, "tasks assigned to the station (K50 allocated, not yet released)"], [P.amber, "its totes ready in the buffer, no K50 yet"], [P.bad, "slot limit"]], 0, H - 8);
    return { w: W, h: H, body: b, label: "Tasks assigned to each station" };
  }

  // One colour per station, stable across days and zones.
  const CATEGORICAL = ["#2F6F8F", "#C98A00", "#3A8F5C", "#8B5CB8", "#C2410C", "#0E7C86", "#B5487A", "#6B7A1F", "#4F6BD8", "#9C5B2E", "#2E8B8B", "#A33B3B"];
  const stationColor = (st) => CATEGORICAL[Math.max(0, R.stations.indexOf(st)) % CATEGORICAL.length];
  const scatterSel = {};                       // zone → [stations]; empty = all

  /** Each station-hour as a dot.  `sel` isolates stations; the rest fade. */
  function scatterChart(z, sel = []) {
    const S = z.scatter, W = 900, H = 400, p = { l: 50, r: 16, t: 14, b: 42 };
    if (!S.length) return { w: W, h: 60, body: T(W / 2, 30, "No full-production hours", { "text-anchor": "middle", fill: P.muted }) };
    const on = (d) => !sel.length || sel.includes(d[3]);
    const allow = z.target ? 3600 / z.target - z.switch_s : null;
    const xm = Math.max(5, Math.ceil(Math.max(...S.map((d) => d[0]), allow || 0) * 1.1));
    const ys = S.map((d) => d[1]).concat(z.target ? [z.target] : []);
    const ymn = Math.floor((Math.min(...ys) * 0.9) / 50) * 50, ymx = Math.ceil((Math.max(...ys) * 1.08) / 50) * 50;
    const X = (v) => p.l + ((W - p.l - p.r) * Math.min(v, xm)) / xm;
    const Y = (v) => H - p.b - ((H - p.t - p.b) * (v - ymn)) / Math.max(ymx - ymn, 1);
    let b = "";
    const ys_ = niceStep(ymx - ymn, 5);
    for (let v = ymn; v <= ymx; v += ys_) b += `<line x1="${p.l}" x2="${W - p.r}" y1="${Y(v)}" y2="${Y(v)}" stroke="${P.rule}"/>` + T(p.l - 6, Y(v) + 4, v, { "text-anchor": "end", fill: P.muted });
    const xs_ = niceStep(xm, 5);
    for (let v = 0; v <= xm; v += xs_) b += T(X(v), H - 22, v, { "text-anchor": "middle", fill: P.muted });
    if (z.target) {
      b += `<line x1="${p.l}" x2="${W - p.r}" y1="${Y(z.target)}" y2="${Y(z.target)}" stroke="${P.amber}" stroke-dasharray="4 3"/>` + T(W - p.r, Y(z.target) - 5, `target ${num(z.target, 0)}`, { "text-anchor": "end", fill: P.amber });
      if (allow > 0) b += `<line x1="${X(allow)}" x2="${X(allow)}" y1="${p.t}" y2="${H - p.b}" stroke="${P.amber}" stroke-dasharray="4 3"/>` + T(X(allow) + 4, H - p.b - 6, `${fix(allow, 1)} s pick allowance`, { fill: P.amber });
    }
    // Faded context first, so the isolated stations draw on top.
    for (const d of S.filter((x) => !on(x)))
      b += `<circle cx="${X(d[0])}" cy="${Y(d[1])}" r="3.5" fill="${P.slate}" fill-opacity=".35"><title>${esc(d[3])}: ${d[1]}/h, pick ${d[0]} s, waiting ${d[2]} s per tote</title></circle>`;
    for (const d of S.filter(on))
      b += `<circle cx="${X(d[0])}" cy="${Y(d[1])}" r="4.5" fill="${stationColor(d[3])}" fill-opacity=".85" stroke="${P.panel}" stroke-width=".8"><title>${esc(d[3])}: ${d[1]}/h, pick ${d[0]} s, waiting ${d[2]} s per tote</title></circle>`;
    const shown = S.filter(on), rp = E.corr(shown.map((d) => d[0]), shown.map((d) => d[1]));
    b += T(p.l + 4, p.t + 12, `${sel.length ? E.runs(sel) : "All stations"} · r(pick, rate) = ${rp == null ? "–" : rp.toFixed(2)}`, { fill: P.muted, "font-size": "12", "font-weight": "600" });
    b += T(W - p.r, H - 4, "mean pick time per tote, s", { "text-anchor": "end", fill: P.muted });
    return { w: W, h: H, body: b, label: "Presentations per hour against pick time" };
  }

  /** The budget card: "as measured" or "against targets", one toggle for the page. */
  function budgetCard(z, D, door) {
    let c;
    const key = "where-each-tote-s-time-goes-" + slug(z.zone);
    const build = () => card({
      key, title: "Where each tote's time goes", sub: z.zone, minWidth: 820,
      chart: () => (budgetMode === "targets" ? budgetTargetChart(z) : budgetChart(z)),
      data: () => ({ zone: z.zone, view: budgetMode, full_hours: D.budget.full_hours, door_s: door,
        stations: z.rows.map((r) => ({ station: r.station, target_per_h: r.target, budget_s: r.budget_s,
          pick_s: r.pick_s, switch_s: r.switch_s, wait_s: r.wait_s, cycle_s: r.cycle_s, over_s: r.over_s,
          target_pick_s: r.target_pick_s, target_switch_s: r.target_switch_s, buffer_s: r.wait_allowance_s,
          excess_pick_s: r.excess_pick_s, excess_switch_s: r.excess_switch_s, excess_wait_s: r.excess_wait_s, actual_per_h: r.rate })) }),
      explain: () => (budgetMode === "targets" ? X.budgetTargets(z) : X.budgetChart(z, D)),
      fill: (body) => {
        const t = document.createElement("div");
        t.className = "tabs";
        for (const [mode, label] of [["measured", "As measured"], ["targets", "Against targets — over by cause"]]) {
          const b = document.createElement("button");
          b.type = "button"; b.textContent = label; b.setAttribute("aria-pressed", String(budgetMode === mode));
          b.onclick = () => { budgetMode = mode; document.querySelectorAll(".card[data-key^='where-each-tote']").forEach((el) => el.dispatchEvent(new Event("rebuild"))); };
          t.append(b);
        }
        const wrap = document.createElement("div");
        wrap.className = "scroll";
        wrap.innerHTML = svgMarkup(budgetMode === "targets" ? budgetTargetChart(z) : budgetChart(z), 820);
        body.append(t, wrap);
      },
    });
    c = build();
    c.addEventListener("rebuild", function handler() { const n = build(); n.addEventListener("rebuild", handler); c.replaceWith(n); c = n; });
    return c;
  }

  /** The scatter card, with a chip per station to isolate it. */
  function scatterCard(z, key) {
    let c;
    const sel = () => (scatterSel[z.zone] || []).filter((s) => z.stations.includes(s));
    const shown = () => z.scatter.filter((d) => !sel().length || sel().includes(d[3]));
    const build = () => card({
      key, title: "Each station-hour", sub: z.zone,
      chart: () => scatterChart(z, sel()),
      data: () => ({
        columns: ["pick_s_per_tote", "totes_per_hour", "wait_s_per_tote", "station"],
        stations: sel().length ? sel() : z.stations,
        points: shown(),
        r_pick: E.r(E.corr(shown().map((d) => d[0]), shown().map((d) => d[1])), 2),
        r_wait: E.r(E.corr(shown().map((d) => d[2]), shown().map((d) => d[1])), 2),
      }),
      explain: () => X.budgetScatter(z, sel(), shown()),
      fill: (body) => {
        const chips = document.createElement("div");
        chips.className = "tabs chips";
        const mk = (label, pressed, color, onclick) => {
          const b = document.createElement("button");
          b.type = "button"; b.setAttribute("aria-pressed", String(pressed)); b.onclick = onclick;
          b.innerHTML = (color ? `<i style="background:${color}"></i>` : "") + esc(label);
          chips.append(b);
        };
        const rebuild = () => { const n = build(); c.replaceWith(n); c = n; };
        mk("All", !sel().length, null, () => { scatterSel[z.zone] = []; rebuild(); });
        for (const st of z.stations) {
          mk(st, sel().includes(st), stationColor(st), () => {
            const cur = sel();
            scatterSel[z.zone] = cur.includes(st) ? cur.filter((x) => x !== st) : cur.concat(st);
            rebuild();
          });
        }
        const wrap = document.createElement("div");
        wrap.innerHTML = svgMarkup(scatterChart(z, sel()));
        body.append(chips, wrap);
      },
    });
    c = build();
    return c;
  }

  function lineChart(days, vals, med, unit, label) {
    const W = 1000, H = 280, p = { l: 56, r: 14, t: 14, b: 46 }, n = days.length;
    const seen = vals.filter((v) => v != null);
    if (!seen.length) return { w: W, h: 60, body: T(W / 2, 30, "No data", { "text-anchor": "middle", fill: P.muted }) };
    const lo = Math.min(...seen), hi = Math.max(...seen), pad = hi - lo || Math.abs(hi) || 1;
    const y0 = lo - pad * 0.15, y1 = hi + pad * 0.15, bw = (W - p.l - p.r) / Math.max(n, 1);
    const Y = (v) => H - p.b - ((H - p.t - p.b) * (v - y0)) / (y1 - y0 || 1);
    let b = "";
    for (let i = 0; i <= 4; i++) { const v = y0 + ((y1 - y0) * i) / 4; b += `<line x1="${p.l}" x2="${W - p.r}" y1="${Y(v)}" y2="${Y(v)}" stroke="${P.rule}"/>` + T(p.l - 6, Y(v) + 4, num(v, Math.abs(v) < 20 ? 2 : 0), { "text-anchor": "end", fill: P.muted }); }
    if (med != null && med >= y0 && med <= y1) b += `<line x1="${p.l}" x2="${W - p.r}" y1="${Y(med)}" y2="${Y(med)}" stroke="${P.amber}" stroke-dasharray="4 3"/>` + T(W - p.r, Y(med) - 5, "median day", { "text-anchor": "end", fill: P.amber });
    let d = "", pen = false;
    vals.forEach((v, i) => { if (v == null) { pen = false; return; } d += (pen ? "L" : "M") + (p.l + i * bw + bw / 2) + "," + Y(v); pen = true; });
    b += `<path d="${d}" fill="none" stroke="${P.steel}" stroke-width="2.5"/>`;
    vals.forEach((v, i) => {
      const x = p.l + i * bw + bw / 2;
      if (v != null) b += `<circle cx="${x}" cy="${Y(v)}" r="4" fill="${P.steel}"><title>${days[i]}  ${esc(label)} ${num(v, 2)}${unit}</title></circle>`;
      if (n <= 16 || i % Math.ceil(n / 16) === 0) b += T(x, H - 26, days[i].slice(8), { "text-anchor": "middle", fill: P.muted }) + T(x, H - 10, days[i].slice(5, 7), { "text-anchor": "middle", fill: P.muted, "font-size": "11" });
    });
    return { w: W, h: H, body: b, label };
  }

  let measureCtx;
  function textWidth(s, bold) {
    measureCtx = measureCtx || document.createElement("canvas").getContext("2d");
    measureCtx.font = `${bold ? "600 " : ""}13px ${FONT}`;
    return measureCtx.measureText(String(s)).width;
  }
  /** A table drawn as SVG, for the PNG copy. */
  function tableChart(tb) {
    const padX = 12, rowH = 26, headH = 30;
    const widths = tb.columns.map((c, j) => Math.max(textWidth(c, true), ...tb.rows.map((r) => textWidth(r[j]))) + padX * 2);
    const W = widths.reduce((a, b) => a + b, 0), H = headH + tb.rows.length * rowH + 4;
    let b = "", x = 0;
    tb.columns.forEach((c, j) => {
      const right = j > 0;
      b += T(right ? x + widths[j] - padX : x + padX, 20, c, { "text-anchor": right ? "end" : "start", fill: P.muted, "font-weight": "600", "font-size": "13" });
      x += widths[j];
    });
    b += `<line x1="0" x2="${W}" y1="${headH - 0.5}" y2="${headH - 0.5}" stroke="${P.rule}"/>`;
    tb.rows.forEach((r, i) => {
      const yy = headH + i * rowH;
      if (tb.flags && tb.flags[i]) tb.flags[i].forEach((f, j) => { if (f) { let xx = 0; for (let k = 0; k < j; k++) xx += widths[k]; b += `<rect x="${xx}" y="${yy}" width="${widths[j]}" height="${rowH}" fill="${f === "over" ? P.bad_soft : P.amber_soft}"/>`; } });
      x = 0;
      r.forEach((v, j) => {
        const right = j > 0, f = tb.flags && tb.flags[i] && tb.flags[i][j];
        b += T(right ? x + widths[j] - padX : x + padX, yy + 18, v, { "text-anchor": right ? "end" : "start", fill: f === "over" ? P.bad : f === "spare" ? P.good : P.ink, "font-size": "13", "font-weight": f === "over" || f === "spare" ? "600" : "400" });
        x += widths[j];
      });
      b += `<line x1="0" x2="${W}" y1="${yy + rowH - 0.5}" y2="${yy + rowH - 0.5}" stroke="${P.rule}"/>`;
    });
    return { w: Math.ceil(W), h: H, body: b, label: "Table" };
  }

  function tilesChart(tiles) {
    const tw = 210, th = 76, per = Math.min(tiles.length, 5), rowsN = Math.ceil(tiles.length / per);
    let b = "";
    tiles.forEach((t, i) => {
      const x = (i % per) * tw, y = Math.floor(i / per) * th;
      b += `<rect x="${x + 0.5}" y="${y + 0.5}" width="${tw - 1}" height="${th - 1}" fill="${P.panel}" stroke="${P.rule}"/>`;
      b += `<text x="${x + 14}" y="${y + 38}" font-size="28" font-weight="600" fill="${t.tone === "bad" ? P.bad : t.tone === "good" ? P.good : P.ink}">${esc(t.v)}<tspan font-size="15" fill="${P.muted}" dx="3">${esc(t.u || "")}</tspan></text>`;
      b += T(x + 14, y + 60, t.l, { fill: P.muted, "font-size": "12" });
    });
    return { w: per * tw, h: rowsN * th, body: b, label: "Headline figures" };
  }

  // ── the card wrapper: title, How it's calculated, PNG, JSON ────────────
  const ICON = {
    info: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M12 16v-4M12 8h.01"/></svg>',
    img: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3-3a2 2 0 0 0-3 0L6 21"/></svg>',
    json: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M8 3H7a2 2 0 0 0-2 2v5a2 2 0 0 1-2 2 2 2 0 0 1 2 2v5a2 2 0 0 0 2 2h1M16 3h1a2 2 0 0 1 2 2v5a2 2 0 0 0 2 2 2 2 0 0 0-2 2v5a2 2 0 0 1-2 2h-1"/></svg>',
  };

  /**
   * opts: { title, sub, explain(): html, data(): object, chart(): {w,h,body},
   *         table(): {columns, rows, flags}, tiles(): [...], fill(body) }
   */
  const openExplain = new Set();               // card keys whose explanation is open

  function card(o) {
    const el = document.createElement("div");
    el.className = "card";
    const key = o.key || slug(o.title + " " + (o.sub || ""));
    el.dataset.key = key;
    const head = document.createElement("header");
    head.innerHTML = `<h4>${esc(o.title)}${o.sub ? `<span class="sub">${esc(o.sub)}</span>` : ""}</h4>`;
    const tools = document.createElement("div");
    tools.className = "tools";
    const mk = (icon, label, title) => { const b = document.createElement("button"); b.type = "button"; b.className = "tool"; b.innerHTML = icon + label; b.title = title; tools.append(b); return b; };
    const exp = document.createElement("div");
    exp.className = "explain"; exp.hidden = true;
    const bInfo = mk(ICON.info, "How it's calculated", "Show the calculation behind this visual");
    const showExplain = (open) => {
      exp.hidden = !open;
      bInfo.setAttribute("aria-expanded", String(open));
      if (open) { exp.innerHTML = o.explain(); openExplain.add(key); } else openExplain.delete(key);
    };
    bInfo.onclick = () => showExplain(exp.hidden);
    showExplain(openExplain.has(key));
    const pageSub = currentSubtitle();
    mk(ICON.img, "PNG", "Copy a high-resolution PNG to the clipboard").onclick = () => {
      const ch = o.chart ? o.chart() : o.table ? tableChart(o.table()) : tilesChart(o.tiles());
      copyPng(ch, o.title + (o.sub ? " · " + o.sub : ""), pageSub);
    };
    el._json = () => ({ visual: o.title + (o.sub ? " · " + o.sub : ""), data: o.data() });   // for section copies
    mk(ICON.json, "JSON", "Copy the data behind this visual as JSON").onclick = () =>
      copyJson({ visual: o.title + (o.sub ? " · " + o.sub : ""), page: pageSub, settings: { door_s: settings.door_s, targets: settings.targets }, data: o.data() }, slug(o.title + " " + pageSub));
    head.append(tools);
    const body = document.createElement("div");
    body.className = "body";
    el.append(head, exp, body);

    if (o.fill) o.fill(body);
    else if (o.chart) body.innerHTML = `<div class="scroll">${svgMarkup(o.chart(), o.minWidth || 0)}</div>`;
    else if (o.table) body.append(htmlTable(o.table(), o.tall));
    else if (o.tiles) body.innerHTML = `<div class="figs">${o.tiles().map((t) => `<div class="fig${t.tone ? " " + t.tone : ""}"><b>${esc(t.v)}<small>${esc(t.u || "")}</small></b><span>${esc(t.l)}</span></div>`).join("")}</div>`;
    return el;
  }

  function htmlTable(tb, tall) {
    const wrap = document.createElement("div");
    wrap.className = tall ? "tall" : "scroll";
    const cls = (i, j) => { const f = tb.flags && tb.flags[i] && tb.flags[i][j]; return f ? ` class="${f === "warn" ? "flag" : f}"` : ""; };
    wrap.innerHTML = `<table><thead><tr>${tb.columns.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead><tbody>${tb.rows.map((r, i) => `<tr>${r.map((v, j) => `<td${cls(i, j)}>${esc(v)}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
    return wrap;
  }

  /** A card whose visual switches between metrics with tab buttons. */
  function tabbedCard(o) {
    let c;
    const build = () => {
      const spec = o.specs[tabs[o.tab]];
      const nc = card({
        key: o.key || slug(o.title), title: o.title, sub: spec.label, explain: () => o.explain(spec), data: () => o.data(spec), chart: () => o.chart(spec), minWidth: o.minWidth,
        fill: (body) => {
          const t = document.createElement("div");
          t.className = "tabs"; t.setAttribute("role", "group");
          o.specs.forEach((s, i) => {
            const b = document.createElement("button");
            b.type = "button"; b.textContent = s.label; b.setAttribute("aria-pressed", String(i === tabs[o.tab]));
            b.onclick = () => { tabs[o.tab] = i; const n = build(); c.replaceWith(n); c = n; };
            t.append(b);
          });
          const note = document.createElement("p");
          note.className = "hmnote"; note.textContent = spec.note || "";
          const wrap = document.createElement("div");
          wrap.className = "scroll";
          wrap.innerHTML = svgMarkup(o.chart(spec), o.minWidth || 0);
          body.append(t, note, wrap);
        },
      });
      return nc;
    };
    c = build();
    return c;
  }

  // ── page scaffolding ───────────────────────────────────────────────────
  const view = () => $("view");
  let currentRoute = null;
  function currentSubtitle() {
    if (!currentRoute || currentRoute === "summary") return summaryRange();
    const d = R.days.find((x) => x.date === currentRoute);
    return d ? prettyDate(d.date) : "";
  }
  const summaryRange = () => R.days.length ? `${prettyDate(R.days[0].date, { day: "numeric", month: "short" })} – ${prettyDate(R.days[R.days.length - 1].date, { day: "numeric", month: "short", year: "numeric" })}` : "";

  function section(title, def) {
    const s = document.createElement("section");
    if (title) { s.id = "sec-" + slug(title); s.dataset.title = title; }
    s.innerHTML = (title ? `<h3 class="sec-h">${esc(title)}</h3>` : "") + (def ? `<p class="def">${esc(def)}</p>` : "");
    return s;
  }

  // ── categories: the page's top level, mirrored in the left panel ──────
  const DAY_CATEGORIES = [
    { key: "stations", title: "Stations", def: "How each station performed: the switch between robots, the operator's time, hour by hour, and each tote's time against its target." },
    { key: "starvation", title: "Starvation", def: "Why stations wait for robots — traced back through each arriving robot's timeline — and what moves with it." },
    { key: "robots", title: "Robots", def: "What the K50s and ACRs did: cycles per hour and how much of the time each fleet was on a task." },
    { key: "supply", title: "Supply and dispatch", def: "How work reaches the stations: task creation, ACR replenishment, and the tasks assigned to each station." },
    { key: "locations", title: "Rack and buffer locations", def: "Where totes come from in the rack and the buffer, and whether location crowds the robots or lengthens their trips." },
    { key: "method", title: "Method", def: null },
  ];
  const SUMMARY_CATEGORIES = [
    { key: "trends", title: "Trends", def: "Each day against the others: the headline numbers, every station and every hour." },
    { key: "starvation", title: "Starvation", def: "Why stations wait for robots, pooled over every day of the run." },
    { key: "robots", title: "Robots", def: "Where the K50s' cycle time goes, and each robot's speed and faults, pooled over every day of the run." },
    { key: "locations", title: "Rack and buffer locations", def: "Where totes come from in the rack and the buffer, pooled over every day of the run." },
  ];

  /** Lay the page out as categories, each holding its sections in the order given. */
  function layout(v, cats, buckets) {
    for (const c of cats) {
      const secs = (buckets[c.key] || []).filter(Boolean);
      if (!secs.length) continue;
      const el = document.createElement("div");
      el.className = "category";
      el.id = "cat-" + c.key;
      el.dataset.title = c.title;
      el.innerHTML = `<h2 class="cat-h">${esc(c.title)}</h2>` + (c.def ? `<p class="cat-def">${esc(c.def)}</p>` : "");
      el.append(...secs);
      v.append(el);
    }
  }
  const para = (txt) => { const p = document.createElement("p"); p.textContent = txt || ""; return p; };
  const two = (...cards) => { const d = document.createElement("div"); d.className = "two"; d.append(...cards); return d; };

  // ── the day page ───────────────────────────────────────────────────────
  function renderDay(i) {
    const d = R.days[i], D = derived[i], v = view();
    v.replaceChildren();
    const put = {};
    const add = (cat, sec) => (put[cat] = put[cat] || []).push(sec);
    const door = D.door, nNo = D.no_door.length;
    const doorTxt = door ? ` plus ${door} s of door travel${nNo ? ` at the stations with a door (${d.stations.length - nNo} of ${d.stations.length})` : ""}` : "";
    const where = prettyDate(d.date);

    // Switch time
    const sSw = section("Switch time", `From operator release of robot 1 ("will leave") to robot 2 reaching the same station${doorTxt}.`);
    sSw.append(card({
      title: "Switch time, all stations",
      tiles: () => [
        { v: num(D.overall.med), u: "s", l: "median switch" },
        { v: num(D.overall.mean), u: "s", l: "average switch" },
        { v: `${num(D.overall.p25)}–${num(D.overall.p75)}`, u: "s", l: "middle 50%" },
        { v: num(D.overall.p90), u: "s", l: "90th percentile" },
        { v: num(D.overall.gt10), u: "%", l: "of switches over 10 s" },
      ].concat(door ? [{ v: num(door), u: "s", l: nNo ? `door travel added at ${d.stations.length - nNo} of ${d.stations.length} stations` : "door travel added to every switch" }] : []),
      data: () => ({ ...D.overall, door_s: door, stations_without_door: D.no_door }),
      explain: () => X.switchTiles(d, D),
    }));
    sSw.append(two(
      card({ title: "Switch time distribution", chart: () => histChart(D.hist_sw, C.switch_hist_w, "seconds"), data: () => ({ bin_width_s: C.switch_hist_w, last_bin_is_open: true, counts: D.hist_sw }), explain: () => X.switchHist(D) }),
      card({
        title: "Switch time by station",
        table: () => ({
          columns: ["Station", "Median s", "Average s", "p90 s", "% over 10 s"],
          rows: D.stations.map((t) => [t.station, num(t.sw_med), num(t.sw_mean), num(t.sw_p90), num(t.sw_gt10)]),
          flags: D.stations.map((t) => [null, null, null, t.sw_p90 > 20 ? "warn" : null, t.sw_gt10 > 10 ? "warn" : null]),
        }),
        data: () => D.stations.map((t) => ({ station: t.station, median_s: t.sw_med, mean_s: t.sw_mean, p90_s: t.sw_p90, pct_over_10s: t.sw_gt10 })),
        explain: () => X.switchTable(D),
      })));
    sSw.append(para(D.text.switch));
    add("stations", sSw);

    // Operator time
    const sOp = section("Operator time", "From a robot reaching the station (tote pickable) to the operator releasing it.");
    sOp.append(card({
      title: "Operator time, all stations",
      tiles: () => [
        { v: num(d.overall.op_med), u: "s", l: "median operator time" },
        { v: num(d.overall.op_mean), u: "s", l: "average operator time" },
        { v: `${num(d.overall.op_p25)}–${num(d.overall.op_p75)}`, u: "s", l: "middle 50%" },
        { v: num(d.overall.op_p90), u: "s", l: "90th percentile" },
        { v: int(d.overall.visits), u: "", l: "totes presented" },
      ],
      data: () => d.overall, explain: () => X.opTiles(d),
    }));
    sOp.append(two(
      card({ title: "Operator time distribution", chart: () => histChart(d.hist_op, 2, "seconds"), data: () => ({ bin_width_s: 2, last_bin_is_open: true, counts: d.hist_op }), explain: () => X.opHist() }),
      card({
        title: "Operator time by station",
        table: () => ({
          columns: ["Station", "Totes", "Totes/h", "Median s", "Average s", "p90 s", "% of day pickable", "Target pickable %"],
          rows: D.stations.map((t) => [t.station, int(t.visits), num(t.rate_full, 0), num(t.op_med), num(t.op_mean), num(t.op_p90), num(t.util), t.target_util == null ? "–" : "≥ " + num(t.target_util)]),
          flags: D.stations.map((t) => [null, null, null, null, null, null, t.target_util == null ? null : t.util < t.target_util ? "over" : "spare", null]),
        }),
        data: () => D.stations.map((t) => ({ station: t.station, totes: t.visits, totes_per_full_hour: t.rate_full, median_s: t.op_med, mean_s: t.op_mean, p90_s: t.op_p90, pct_day_pickable: t.util, target_per_h: t.target, median_switch_s: t.sw_med, target_pickable_pct: t.target_util })),
        explain: () => X.opTable(d, D),
      })));
    sOp.append(para(D.text.operator));
    add("stations", sOp);

    // Robot cycles
    if (d.hourly) {
      const F = d.full_hours, hr = d.hourly;
      const full = hr.filter((h) => F.includes(h.h));
      const avg = (k) => E.mean(full.map((h) => h[k]));
      const k = d.robot_k50, sp = k.stations_per_cycle;
      const sCy = section("Robot cycles per hour", "K50: one cycle is buffer pickup, one or more station visits, return to buffer. ACR: puts are storage to buffer; stores are buffer back to storage.");
      sCy.append(card({
        title: "Cycles, headline",
        tiles: () => [
          { v: num(avg("k50_per")), u: "/h", l: "K50 cycles per robot, full-production hours" },
          { v: num(k.rate, 2), u: "/h", l: "K50 cycles per robot, whole day" },
          { v: num(avg("put_per")), u: "/h", l: "ACR buffer puts per robot, full hours" },
          { v: num(avg("store_per")), u: "/h", l: "ACR buffer-to-storage per robot, full hours" },
          { v: num(k.multi_station_pct), u: "%", l: `of K50 cycles are multi-station (${int(k.multi_station_n)} of ${int(k.total)})` },
          { v: `${int(sp["2"])} / ${int(sp["3+"])}`, u: "", l: "cycles with 2 / 3+ stations before returning" },
        ],
        data: () => ({ robot_k50: d.robot_k50, robot_acr: d.robot_acr, full_hours: F }),
        explain: () => X.cycleTiles(d),
      }));
      sCy.append(card({ title: "Per robot, by hour", chart: () => cyclesChart(hr), minWidth: 680, data: () => hr, explain: () => X.cyclesChart(d) }));
      sCy.append(para(D.text.cycles));
      sCy.append(card({
        title: "Hourly cycles", tall: true,
        table: () => ({
          columns: ["Hour", "K50 active", "K50 cycles", "per K50", "Multi-station", "% multi", "ACR active", "Puts", "Stores", "Relocations", "puts per ACR", "stores per ACR"],
          rows: hr.map((r) => [`${hh(r.h)}:00`, r.k50_active, r.k50_cycles, num(r.k50_per, 2), r.multi, num(r.multi_pct), r.acr_active, r.put, r.store, r.reloc, num(r.put_per, 2), num(r.store_per, 2)].map(String)),
        }),
        data: () => hr, explain: () => X.hourly(),
      }));
      sCy.append(two(
        card({ title: "K50 robots", sub: "cycles per active hour", tall: true, table: () => ({ columns: ["Robot", "Cycles", "Active h", "Per h"], rows: d.k50_robots.map((r) => [r[0], int(r[1]), String(r[2]), num(r[3], 2)]) }), data: () => d.k50_robots.map((r) => ({ robot: r[0], cycles: r[1], active_hours: r[2], per_hour: r[3] })), explain: () => X.k50Table() }),
        card({ title: "ACRs", sub: "puts + stores per active hour", tall: true, table: () => ({ columns: ["Robot", "Puts", "Stores", "Relocs", "Active h", "Per h"], rows: d.acr_robots.map((r) => [r[0], int(r[1]), int(r[2]), int(r[3]), String(r[4]), num(r[5], 2)]) }), data: () => d.acr_robots.map((r) => ({ robot: r[0], puts: r[1], stores: r[2], relocations: r[3], active_hours: r[4], per_hour: r[5] })), explain: () => X.acrTable() })));
      add("robots", sCy);
    }
    if (D.k50_time && D.k50_time.n) add("robots", cycleTimeSection(D.k50_time, D.text.k50_time));
    if (D.robots && D.robots.rows.length) add("robots", robotSection(D.robots, D.text.robots));

    if (D.starve && D.starve.handovers) add("starvation", starveSection(D.starve, D.text.starve));
    if (D.multi && D.multi.handovers) add("starvation", multiSection(D.multi, D.text.multi));

    // Robot utilization
    const U = d.utilization || {};
    if (U.K50 || U.ACR) {
      const sU = section("Robot utilization", "Every robot at every moment is on a task (from allocation until it puts the tote down), between tasks (free, under 5 minutes since its last task) or away (5 minutes or more without a task — most likely charging, which the log does not record).");
      for (const role of ["K50", "ACR"]) {
        const u = U[role];
        if (!u) continue;
        const dd = u.day;
        sU.append(card({
          title: `${role} utilization`, key: "util-tiles-" + role,
          tiles: () => [
            { v: num(dd.util_available_full, 0), u: "%", l: "of available robots on a task, full-production hours" },
            { v: num(dd.busy_full, 0), u: ` of ${u.fleet}`, l: "on a task at an average moment" },
            { v: num(dd.idle_full, 0), u: "", l: "between tasks — free" },
            { v: num(dd.away_full, 0), u: "", l: "away — 5 min or more without a task" },
            { v: num(dd.util_fleet_full, 0), u: "%", l: "of the whole fleet (lower bound)" },
            { v: String(dd.peak), u: "", l: `most on a task at once (${dd.peak_at})` },
          ],
          data: () => ({ role, fleet: u.fleet, ...dd }),
          explain: () => X.utilTiles(role, u),
        }));
        sU.append(card({ title: `${role}s through the day`, key: "util-chart-" + role, chart: () => statesChart(u, role), minWidth: 680,
          data: () => ({ role, fleet: u.fleet, minutes_per_point: 5, on_task: u.busy5, between_tasks: u.idle5, away: u.away5, on_task_peak: u.peak5 }),
          explain: () => X.statesChart(role) }));
        sU.append(card({ title: `Where ${role} idle time goes`, key: "util-gaps-" + role, chart: () => gapChart(u.gap_bands, role),
          data: () => u.gap_bands, explain: () => X.gapChart(role, u) }));
        sU.append(para(D.text.utilization[role]));
      }
      const roles = ["K50", "ACR"].filter((r) => U[r]);
      sU.append(card({
        title: "Utilization by hour", tall: true,
        table: () => ({
          columns: ["Hour"].concat(...roles.map((r) => [`${r} on task`, `${r} between`, `${r} away`, `${r} % of available`, `${r} % of fleet`])),
          rows: R.hours.map((h) => [`${hh(h)}:00`].concat(...roles.map((r) => { const x = U[r].hourly[h]; return [num(x.busy), num(x.idle), num(x.away), num(x.util_available), num(x.util_fleet)]; }))),
        }),
        data: () => Object.fromEntries(roles.map((r) => [r, { fleet: U[r].fleet, hourly: U[r].hourly }])),
        explain: () => X.utilTable(),
      }));
      add("robots", sU);
    }

    // Task supply
    if (d.flow) {
      const F = d.flow, fd = F.day;
      const sF = section("Task supply", "How work reaches the K50s: the warehouse system creates a task, an ACR brings the tote from storage into the buffer, and only then is a K50 allocated to deliver it.");
      sF.append(card({
        title: "Task supply, headline",
        tiles: () => [
          { v: int(fd.created), u: "", l: "tasks created" },
          { v: num(fd.acr_leg_pct, 0), u: "%", l: "needed an ACR to bring the tote" },
          { v: num(fd.created_to_ready_med, 0), u: "s", l: "created → tote in the buffer (median)" },
          { v: num(fd.ready_to_alloc_med, 0), u: "s", l: `tote ready → K50 allocated (median; p90 ${num(fd.ready_to_alloc_p90, 0)} s)` },
          { v: num(fd.ready_wait_full, 0), u: "", l: "totes ready with no K50 yet, average in full hours" },
          { v: num(fd.supply_wait_full, 0), u: "", l: "tasks waiting on an ACR, average in full hours" },
        ],
        data: () => fd, explain: () => X.supplyTiles(),
      }));
      sF.append(card({ title: "Supply through the day", chart: () => supplyChart(F, U.K50), minWidth: 680,
        data: () => ({ minutes_per_point: 5, ready_no_k50: F.ready5, waiting_on_acr: F.supply5, k50_away: U.K50 && U.K50.away5, k50_between_tasks: U.K50 && U.K50.idle5, hourly: F.hourly }),
        explain: () => X.supplyChart() }));
      sF.append(card({
        title: "Ready totes by destination station",
        table: () => ({
          columns: ["Station", "Tasks", "Ready → K50, median s", "p90 s", "Ready, no K50 (avg)"],
          rows: F.stations.map((x) => [x.station, int(x.tasks), num(x.ready_to_alloc_med, 0), num(x.ready_to_alloc_p90, 0), num(x.ready_backlog)]),
        }),
        data: () => F.stations, explain: () => X.supplyTable(),
      }));
      sF.append(para(D.text.supply));
      add("supply", sF);
    }

    // Station slots
    if (d.slots) {
      const sS = section("Station slots", "Tasks assigned to each station at once — from a K50 being allocated to it until that robot is released there. A station that only takes a new task when a slot frees up piles up against a ceiling.");
      const SL = D.stations;
      sS.append(card({
        title: "Slots by station",
        table: () => ({
          columns: ["Station", "Slot limit", "Time at limit", "Assigned (avg)", "Ready totes at limit", "below limit", "Allocation → release s", "Slots needed at target"],
          rows: d.slots.stations.map((x) => { const t = SL.find((y) => y.station === x.station) || {}; return [x.station, x.limit == null ? "none" : String(x.limit), x.at_limit_pct == null ? "–" : num(x.at_limit_pct) + "%", num(x.mean_assigned), num(x.ready_at_limit), num(x.ready_below_limit), num(x.lead_med, 0), t.slots_needed == null ? "–" : num(t.slots_needed)]; }),
          flags: d.slots.stations.map((x) => { const t = SL.find((y) => y.station === x.station) || {}; return [null, null, null, null, null, null, null, t.slots_needed != null && x.limit != null ? (t.slots_needed >= 0.9 * x.limit ? "over" : "spare") : null]; }),
        }),
        data: () => d.slots.stations.map((x) => { const { assigned5, ready5, ...rest } = x; const t = SL.find((y) => y.station === x.station) || {}; return { ...rest, target_per_h: t.target, slots_needed_at_target: t.slots_needed }; }),
        explain: () => X.slotsTable(),
      }));
      sS.append(card({ title: "Tasks assigned to each station through the day", chart: () => slotsChart(d.slots), minWidth: 760,
        data: () => ({ minutes_per_point: 5, stations: d.slots.stations.map((x) => ({ station: x.station, limit: x.limit, assigned: x.assigned5, ready_no_k50: x.ready5 })) }),
        explain: () => X.slotsChart() }));
      sS.append(para(D.text.slots));
      add("supply", sS);
    }

    if (D.spatial) add("locations", spatialSection(D.spatial, D.text.spatial));
    if (D.faults) add("locations", faultSection(D.faults, D.text.faults));

    // What limits the stations
    if (D.hours.length >= 3) {
      const sR = section("What limits the stations", "Every full-production hour of the day as one dot, so you can see which measures move together.");
      sR.append(hourCard(D.hours, "hour-scatter", prettyDate(d.date)));
      sR.append(para(D.text.relations));
      add("starvation", sR);
    }

    // Stations by hour
    const sHm = section("Stations by hour", null);
    const specs = [
      { key: "visits", label: "Totes presented", note: "Robot arrivals per station per hour.", grid: d.hm_visits, fmt: (x) => int(x) },
      { key: "op", label: "Median operator time", note: "Median seconds a tote is pickable, by hour of arrival. The colour scale stops at 60 s.", grid: d.hm_op_med, cap: 60, unit: "s" },
      { key: "sw", label: "Median switch time", note: `Median seconds from release to the next robot arriving${doorTxt}, by hour of the first robot's arrival. The colour scale stops at 10 s.`, grid: D.hm_sw_med, cap: 10, unit: "s" },
      { key: "swmean", label: "Average switch time", note: `Average seconds from release to the next robot arriving${doorTxt}, by hour of the first robot's arrival. Pulled up by long waits for a robot, unlike the median. The colour scale stops at 20 s.`, grid: D.hm_sw_mean, cap: 20, unit: "s" },
      { key: "gt10", label: "Switches over 10 s", note: "Percent of handovers where the next robot took more than 10 s — a sign the station was waiting for work.", grid: D.hm_sw_gt10, cap: 100, warn: true, fmt: (x) => num(x, 0) + "%" },
      { key: "possible", label: "Presented vs possible", note: "Totes presented, and how many were possible in that hour had the average pick taken the station's target pick time — with the switch and waiting as they actually were. Colour is the share of possible achieved.", grid: d.hm_visits, ofGrid: D.hm_possible, cw: 50, chh: 32 },
      { key: "util", label: "Time pickable", note: "Percent of the hour a tote was at the station and pickable. The rest is the switch (door included) or waiting. The Target column is the share a station needs to hit its target; marked cells fall short of it.", grid: D.hm_util, cap: 100, fmt: (x) => num(x, 0) + "%", targets: D.stations.map((t) => t.target_util) },
    ];
    sHm.append(tabbedCard({
      tab: "dayHm", title: "Stations by hour", specs, minWidth: 760,
      chart: (s) => heatmapChart(d.stations, R.hours.map(hh), s.grid, { cap: s.cap, warn: s.warn, fmt: s.fmt, colTitle: (j) => hh(j) + ":00", label: s.label, targets: s.targets, ofGrid: s.ofGrid, cw: s.cw, chh: s.chh }),
      data: (s) => ({ metric: s.label, unit: s.unit || "", stations: d.stations, hours: R.hours, grid: s.grid, ...(s.targets ? { target_by_station: s.targets } : {}), ...(s.ofGrid ? { possible: s.ofGrid, target_pick_s: Object.fromEntries(D.stations.map((t) => [t.station, t.target_pick])) } : {}) }),
      explain: (s) => X.dayHeat(s, D),
    }));
    add("stations", sHm);

    // Time budget per tote
    const sB = section("Time budget per tote", "A target of N totes an hour gives each tote 3600 ÷ N seconds. Each tote's real cycle splits into picking, the switch to the next robot, and any wait for that robot. Measured over the full-production hours, so breaks don't count against anyone.");
    if (!D.budget.zones.length) {
      const n = document.createElement("div");
      n.className = "notice";
      n.textContent = D.budget.reason ? `No time budget: ${D.budget.reason}.` : "No station has a target. Set one on the Settings page (bottom of the left panel) to see its time budget.";
      sB.append(n);
    }
    for (const z of D.budget.zones) {
      const h = document.createElement("h4");
      h.style.margin = "18px 0 10px";
      h.textContent = `${z.zone} · ${E.runs(z.stations)}` + (z.target ? ` · target ${num(z.target, 0)}/h = ${fix(3600 / z.target)} s per tote` : " · mixed targets");
      sB.append(h);
      const rows = z.rows, m = (k) => E.mean(rows.map((x) => x[k]));
      const overAvg = m("over_s");
      sB.append(card({
        title: "Budget, headline", sub: z.zone,
        tiles: () => [
          { v: num(m("rate"), 0), u: "/h", l: `actual, average of ${rows.length} station${rows.length > 1 ? "s" : ""}` },
          { v: fix(m("cycle_s")), u: "s", l: "mean cycle per tote" },
          { v: (overAvg > 0 ? "+" : "") + fix(overAvg), u: "s", l: overAvg > 0 ? "over budget per tote, on average" : "inside budget per tote, on average", tone: overAvg > 0 ? "bad" : "good" },
          { v: fix(m("wait_s")), u: "s", l: "waiting for a robot per tote" },
          { v: num(m("rns"), 0), u: "/h", l: "if robots were never late" },
        ],
        data: () => ({ zone: z.zone, target: z.target, means: { rate: m("rate"), cycle_s: m("cycle_s"), over_s: overAvg, wait_s: m("wait_s"), no_wait_rate: m("rns") } }),
        explain: () => X.budgetTiles(z),
      }));
      sB.append(budgetCard(z, D, door));
      sB.append(
        card({
          title: "What each fix would be worth", sub: z.zone,
          table: () => ({
            columns: ["Station", "Target /h", "Actual /h", "Target pick s", "Target switch s", "Buffer s", "Wait actual s", "No waiting", "Picks within allowance", "Both"],
            rows: rows.map((r) => [r.station, num(r.target, 0), num(r.rate, 0), fix(r.target_pick_s) + (r.pick_from === "set" ? "" : "*"), fix(r.target_switch_s) + (r.switch_from === "set" ? "" : "*"), fix(r.wait_allowance_s), fix(r.wait_s), num(r.rns, 0), num(r.rpb, 0), num(r.both, 0)]),
            flags: rows.map((r) => [null, null, r.rate >= r.target ? "spare" : "over", null, null, null, r.wait_allowance_s != null && r.wait_s > Math.max(r.wait_allowance_s, 0) ? "over" : "spare", null, null, null]),
          }),
          data: () => rows.map((r) => ({ station: r.station, target: r.target, actual: r.rate, target_pick_s: r.target_pick_s, target_switch_s: r.target_switch_s, buffer_s: r.wait_allowance_s, wait_actual_s: r.wait_s, no_waiting: r.rns, picks_within_allowance: r.rpb, both: r.both, pick_allowance_s: r.pick_allow_s })),
          explain: () => X.budgetWhatIf(z),
        }),
        scatterCard(z, "scatter-" + slug(z.zone)));
      sB.append(para(D.text.budget[z.zone]));
    }
    add("stations", sB);

    // Method
    const sM = section("How these were measured", null);
    const ol = document.createElement("ol");
    ol.className = "notes";
    D.method.forEach((t) => { const li = document.createElement("li"); li.textContent = t; ol.append(li); });
    sM.append(ol);
    add("method", sM);
    layout(v, DAY_CATEGORIES, put);
  }

  // ── the summary page ───────────────────────────────────────────────────
  function renderSummary() {
    const S = summary, v = view();
    v.replaceChildren();
    if (!S) { v.innerHTML = `<p class="empty">The summary needs at least two days.</p>`; return; }
    const put = {};
    const add = (cat, sec) => (put[cat] = put[cat] || []).push(sec);

    const sH = section("Day by day", "Each day's headline number, against the median day of the run.");
    sH.append(card({
      title: "The median day",
      tiles: () => [
        { v: int(S.medians.visits), u: "", l: "totes presented" },
        { v: num(S.medians.op_med), u: "s", l: "median operator time" },
        { v: num(S.medians.sw_med), u: "s", l: "median switch time" },
        { v: num(S.medians.k50_per), u: "/h", l: "K50 cycles per robot" },
        { v: S.days.length, u: "", l: "days in this run" },
      ],
      data: () => S.medians, explain: () => X.sumTiles(),
    }));
    const specs = E.HEADLINE.filter((h) => S.headline.some((r) => r[h.key] != null));
    sH.append(tabbedCard({
      tab: "sumLine", title: "Day by day", specs, minWidth: 640,
      chart: (h) => lineChart(S.days, S.headline.map((r) => r[h.key]), S.medians[h.key], h.unit, h.label),
      data: (h) => ({ metric: h.label, unit: h.unit, median_day: S.medians[h.key], days: S.headline.map((r) => ({ day: r.day, value: r[h.key] })) }),
      explain: (h) => X.sumLine(h),
    }));
    sH.append(para(S.text.headline));
    sH.append(card({
      title: "All metrics, all days",
      table: () => ({
        columns: ["Day"].concat(specs.map((h) => h.label + (h.unit ? ` (${h.unit})` : ""))),
        rows: S.headline.map((r) => [r.day].concat(specs.map((h) => num(r[h.key], 2)))),
        flags: S.headline.map((r) => [null].concat(specs.map((h) => {
          const val = r[h.key], med = S.medians[h.key];
          if (val == null || !med || !h.better) return null;
          const better = h.better > 0 ? val > med * 1.05 : val < med * 0.95;
          const worse = h.better > 0 ? val < med * 0.95 : val > med * 1.05;
          return better ? "spare" : worse ? "over" : null;
        }))),
      }),
      data: () => S.headline, explain: () => X.sumTable(),
    }));
    add("trends", sH);

    if (S.hours.length >= 3) {
      const sR = section("What limits the stations", "Every full-production hour of every day as one dot, so you can see which measures move together across the run.");
      sR.append(hourCard(S.hours, "hour-scatter", `${S.days.length} days`));
      sR.append(para(S.text.relations));
      add("starvation", sR);
    }

    if (S.starve && S.starve.handovers) add("starvation", starveSection(S.starve, S.text.starve));
    if (S.multi && S.multi.handovers) add("starvation", multiSection(S.multi, S.text.multi));
    if (S.spatial) add("locations", spatialSection(S.spatial, S.text.spatial));
    if (S.faults) add("locations", faultSection(S.faults, S.text.faults));
    if (S.k50_time && S.k50_time.n) add("robots", cycleTimeSection(S.k50_time, S.text.k50_time));
    if (S.robots && S.robots.rows.length) add("robots", robotSection(S.robots, S.text.robots));

    const sS = section("By station, across the days", "One row per station, one column per day — so a station that drifts stands out from one that was always slow.");
    sS.append(tabbedCard({
      tab: "sumSt", title: "By station, across the days", specs: E.BY_STATION, minWidth: 640,
      chart: (s) => heatmapChart(S.stations, S.days.map((x) => x.slice(5)), S.by_station[s.key], { cw: 46, fromZero: false, fmt: (x) => num(x, x >= 100 ? 0 : 1), colTitle: (j) => S.days[j], label: s.label, targetGrid: s.key === "util" ? S.by_station.target_util : null }),
      data: (s) => ({ metric: s.label, unit: s.unit, stations: S.stations, days: S.days, grid: S.by_station[s.key], ...(s.key === "util" ? { target_grid: S.by_station.target_util } : {}) }),
      explain: (s) => X.sumStation(s),
    }));
    sS.append(para(S.text.stations));
    add("trends", sS);

    const sSw = section("Average switch time by station and hour", `Every day pooled: all switch seconds in that station-hour ÷ its handovers, each station with its own door seconds.`);
    sSw.append(card({
      title: "Average switch time by station and hour", sub: `${S.days.length} days pooled · seconds`, minWidth: 760,
      chart: () => heatmapChart(S.stations, R.hours.map(hh), S.hm_sw_mean, { cap: 20, colTitle: (j) => hh(j) + ":00", label: "Average switch time by station and hour" }),
      data: () => ({ metric: "Average switch time", unit: "s", days: S.days, stations: S.stations, hours: R.hours, grid: S.hm_sw_mean }),
      explain: () => X.sumSwitchHour(),
    }));
    add("trends", sSw);

    const sR = section("By hour, across the days", "One row per hour of the day, one column per day — the shift pattern, and whether it held.");
    sR.append(tabbedCard({
      tab: "sumHr", title: "By hour, across the days", specs: E.BY_HOUR, minWidth: 640,
      chart: (s) => heatmapChart(R.hours.map((h) => hh(h) + ":00"), S.days.map((x) => x.slice(5)), S.by_hour[s.key], { cw: 46, lw: 60, fromZero: false, fmt: (x) => num(x, x >= 100 ? 0 : 1), colTitle: (j) => S.days[j], label: s.label }),
      data: (s) => ({ metric: s.label, unit: s.unit, hours: R.hours, days: S.days, grid: S.by_hour[s.key] }),
      explain: (s) => X.sumHour(s),
    }));
    sR.append(para(S.text.hours));
    add("trends", sR);
    // What limits the stations reads best after the starvation detail.
    if (put.starvation && put.starvation.length > 1 && put.starvation[0].id === "sec-what-limits-the-stations") put.starvation.push(put.starvation.shift());
    layout(v, SUMMARY_CATEGORIES, put);
  }

  // ── the settings page ──────────────────────────────────────────────────
  /** Report-wide settings: door travel and, per zone or station, the targets. */
  function renderSettings() {
    const v = view();
    v.replaceChildren();
    const sec = section(null, "These apply to the whole report — every day and the summary — and everything recalculates as you change them. They are saved in this browser for this report.");

    // door
    const door = document.createElement("div");
    door.className = "card";
    door.id = "set-door"; door.dataset.title = "Door travel";
    door.innerHTML = `<header><h4>Door travel</h4></header><div class="body"><p class="muted-p">The shutter-door open command is logged in the same millisecond as the robot's arrival, so the door's physical travel never appears in the log. These seconds are added to every switch and taken off the front of every pick — at stations that have a door. Switch doors off per station in the table below.</p>
      <div class="setrow"><label for="setDoor">Seconds added to each switch</label><input type="number" id="setDoor" min="0" max="60" step="0.1" value="${settings.door_s}"><span class="unit">s</span></div>
      <h4 class="sub-h">Doors by day</h4>
      <p class="muted-p">Switch a day off if the doors were not in use that day — for example after they were disabled. On a day that is off, no station gets door seconds.</p>
      <div class="daydoors">${R.days.map((d) => `<label class="daydoor"><span class="switch-mini"><input type="checkbox" data-doorday="${d.date}"${dayDoorsOff(d.date) ? "" : " checked"} aria-label="Doors in use on ${d.date}"><span></span></span><span>${esc(prettyDate(d.date, { weekday: "short", day: "numeric", month: "short" }))}</span></label>`).join("")}</div>
      ${R.days.length > 1 ? `<div class="row"><button class="btn sm" type="button" id="daysOn">All days on</button><button class="btn sm" type="button" id="daysOff">All days off</button></div>` : ""}</div>`;
    sec.append(door);

    // targets
    const tcard = document.createElement("div");
    tcard.className = "card";
    tcard.id = "set-targets"; tcard.dataset.title = "Targets by zone and station";
    let h = `<header><h4>Targets by zone and station</h4></header><div class="body">
      <p class="muted-p">Stations are grouped into zones by the row they sit on. A zone's values apply to every station in it unless the station has its own. Leave a box blank to inherit (or use the default shown in grey); enter 0 to switch a target off.</p>
      <div class="scroll"><table class="settings"><thead><tr><th>Zone / station</th><th>Door</th><th>Target totes / h</th><th>Target pick s</th><th>Target switch s</th></tr></thead><tbody>`;
    const ph = (kind, name, isZone) => {
      if (!isZone) {
        const z = settings[kind][R.zones[name]];
        if (z !== undefined && z !== "") return String(z);
      }
      return kind === "targets" ? "none" : kind === "pick_s" ? "auto" : "measured";
    };
    const input = (kind, name, isZone) => `<input type="number" min="0" step="${kind === "targets" ? 1 : 0.1}" data-kind="${kind}" data-key="${esc(name)}" value="${settings[kind][name] ?? ""}" placeholder="${esc(ph(kind, name, isZone))}" aria-label="${esc(name)} ${kind}">`;
    const doorBox = (name, zone) => `<td class="door"><label class="switch-mini"><input type="checkbox" data-door="${esc(name)}"${zone ? ` data-zone="1"` : ""} aria-label="${esc(name)} has a door"><span></span></label></td>`;
    for (const z of R.zone_list) {
      h += `<tr class="zone"><td><b>${esc(z.zone)}</b> <span class="muted">${esc(E.runs(z.stations))}</span></td>${doorBox(z.zone, true)}<td>${input("targets", z.zone, true)}</td><td>${input("pick_s", z.zone, true)}</td><td>${input("switch_s", z.zone, true)}</td></tr>`;
      for (const st of z.stations)
        h += `<tr><td class="indent">${esc(st)}</td>${doorBox(st, false)}<td>${input("targets", st, false)}</td><td>${input("pick_s", st, false)}</td><td>${input("switch_s", st, false)}</td></tr>`;
    }
    h += `</tbody></table></div>
      <ul class="muted-p"><li><b>Door</b> — on for every station unless switched off. Stations without a door get no door seconds added to their switch and none taken off their picks. A zone's switch sets all of its stations.</li>
      <li><b>Target totes / h</b> — the rate a station should present. A blank zone gets ${num(R.target_rate, 0)}/h automatically if it is a high-rate zone, and no target otherwise.</li>
      <li><b>Target pick</b> — seconds a pick should take. Blank ("auto"): whatever the budget leaves after the switch (3600 ÷ rate − switch), so no time is allowed for waiting.</li>
      <li><b>Target switch</b> — seconds a handover should take, door included. Blank: the station's measured median switch that day.</li></ul>
      <div class="row"><button class="btn" type="button" id="resetBtn">Reset to run defaults</button><button class="btn" type="button" id="cfgBtn">Copy as ess_config.json</button></div></div>`;
    tcard.innerHTML = h;
    sec.append(tcard);
    v.append(sec);

    door.querySelectorAll("input[data-doorday]").forEach((cb) => {
      cb.onchange = () => {
        if (cb.checked) delete settings.no_door_days[cb.dataset.doorday]; else settings.no_door_days[cb.dataset.doorday] = true;
        changed(true);
      };
    });
    const allDays = (on) => {
      R.days.forEach((d) => { if (on) delete settings.no_door_days[d.date]; else settings.no_door_days[d.date] = true; });
      door.querySelectorAll("input[data-doorday]").forEach((cb) => { cb.checked = on; });
      changed(true);
    };
    if ($("daysOn")) { $("daysOn").onclick = () => allDays(true); $("daysOff").onclick = () => allDays(false); }
    const doorIn = $("setDoor");
    doorIn.oninput = () => {
      const n = Number(doorIn.value);
      if (!isFinite(n) || n < 0 || n > 60) return;
      settings.door_s = Math.round(n * 10) / 10; changed(true);
    };
    tcard.querySelectorAll("input[data-kind]").forEach((inp) => {
      inp.oninput = () => {
        const kind = inp.dataset.kind, k = inp.dataset.key, val = inp.value.trim();
        if (val === "") delete settings[kind][k];
        else { const n = Number(val); if (!isFinite(n) || n < 0) return; settings[kind][k] = n; }
        if (R.zone_list.some((z) => z.zone === k))
          tcard.querySelectorAll(`input[data-kind="${kind}"]`).forEach((o) => { if (R.zones[o.dataset.key] === k) o.placeholder = ph(kind, o.dataset.key, false); });
        changed(true);
      };
    });
    // Door switches: the zone switch sets every station in it; a station switch
    // overrides its zone.  Mixed zones show as indeterminate.
    const syncDoorBoxes = () => {
      tcard.querySelectorAll("input[data-door]").forEach((cb) => {
        const name = cb.dataset.door;
        if (cb.dataset.zone) {
          const z = R.zone_list.find((x) => x.zone === name);
          const on = z.stations.filter(stationHasDoor).length;
          cb.checked = on === z.stations.length; cb.indeterminate = on > 0 && on < z.stations.length;
        } else cb.checked = stationHasDoor(name);
      });
    };
    syncDoorBoxes();
    tcard.querySelectorAll("input[data-door]").forEach((cb) => {
      cb.onchange = () => {
        const name = cb.dataset.door, on = cb.checked;
        if (cb.dataset.zone) {
          const z = R.zone_list.find((x) => x.zone === name);
          z.stations.forEach((st) => delete settings.no_door[st]);
          if (on) delete settings.no_door[name]; else settings.no_door[name] = true;
        } else {
          const zoneOff = settings.no_door[R.zones[name]] === true;
          if (on) { if (zoneOff) settings.no_door[name] = false; else delete settings.no_door[name]; }
          else { if (zoneOff) delete settings.no_door[name]; else settings.no_door[name] = true; }
        }
        syncDoorBoxes();
        changed(true);
      };
    });
    $("resetBtn").onclick = () => { settings = loadDefaults(); changed(); renderSettings(); };
    $("cfgBtn").onclick = () => copyJson({ door_s: settings.door_s, target_rate: R.target_rate, targets: settings.targets, pick_s: settings.pick_s, switch_s: settings.switch_s,
      no_door: R.stations.filter((st) => !stationHasDoor(st)),
      no_door_days: Object.keys(settings.no_door_days).filter((d) => settings.no_door_days[d]).sort() }, "ess_config");
  }
  const loadDefaults = () => { const d = clone(R.defaults); for (const k of ["targets", "pick_s", "switch_s", "no_door", "no_door_days"]) d[k] = d[k] || {}; return d; };

  // ── technical explanations ─────────────────────────────────────────────
  const doorClause = () => settingsDoor()
    ? `<p>The door setting is <b>${settingsDoor()} s</b>. The shutter-door open command is logged in the same millisecond as the arrival, so the door's travel never appears in the log; that many seconds are added to every measured gap at a station with a door.${(() => { const n = R.stations.filter((st) => !stationHasDoor(st)); return n.length ? ` ${esc(E.runs(n))} ${n.length > 1 ? "have" : "has"} no door (Settings), so ${n.length > 1 ? "their" : "its"} switch and pick times are used exactly as logged.` : ""; })()}</p>`
    : `<p>The door setting is <b>0 s</b>, so switch times are exactly as logged. The door's travel is not in the log; set the door seconds on the Settings page (bottom of the left panel) to add it.</p>`;
  const X = {
    switchTiles: (d, D) => `<p><b>Switch time</b> = <code>arrival of the next robot at the station − release of the previous robot</code> + door seconds.</p>
      <ul><li><b>Release</b> is the <code>EssKubotStationHandleLetRobotGo … will leave</code> log line.</li>
      <li><b>Arrival</b> is the <code>CALLBACK_OF_ROBOT_REACH_STATION</code> event.</li>
      <li>Visits are put in arrival order per station; each visit's release is paired with the next visit's arrival at the same station.</li>
      <li>Gaps longer than ${C.max_switch_s / 60} minutes are stand-downs (breaks, no demand), not handovers, and are excluded. ${D.overall.n.toLocaleString()} handovers remain.</li></ul>
      <p>Median and percentiles use linear interpolation over all of them. <b>Average</b> is the plain mean of the same handovers, door included. A few long waits for a robot pull the average up while the median stays put, so a gap between the two is the tail. <b>% over 10 s</b> is the share whose switch, door included, is above 10 s.</p>${doorClause()}`,
    switchHist: () => `<p>Every handover of the day, binned by switch time (door included) in ${C.switch_hist_w} s bins from 0 to ${C.switch_hist_bins * C.switch_hist_w} s. The last bar collects everything at or above ${(C.switch_hist_bins - 1) * C.switch_hist_w} s.</p><p>Adding door seconds moves the whole distribution right by exactly that amount — its shape does not change.</p>`,
    switchTable: () => `<p>Per station, the same handovers as the headline: median, average and 90th percentile switch time (door included), and the share above 10 s.</p><p>Highlighted: p90 above 20 s or more than 10% of handovers over 10 s. A long tail here is the next robot not yet queued behind the one leaving, not the switch mechanism itself.</p>`,
    opTiles: (d) => `<p><b>Operator time</b> = <code>release − arrival</code> for the same robot at the same station.</p>
      <ul><li>Each arrival is paired with that robot's next release at that station, provided the release comes before the robot's own next arrival there. ${d.n_unpaired.toLocaleString()} arrivals had no matching release and are left out.</li>
      <li>Pairing runs over the whole file so visits spanning midnight still pair; the day covers visits whose arrival falls on it.</li>
      <li><b>Totes presented</b> = paired visits on the day.</li>
      <li><b>Average</b> is the plain mean of every visit's operator time; long picks pull it above the median.</li></ul>
      <p>Operator time is exactly as logged and so includes the door's travel and any screen or scan delay. It does <b>not</b> change with the door setting.</p>`,
    opHist: () => `<p>Every visit binned by operator time in 2 s bins from 0 to 60 s; the last bar collects 58 s and above.</p>`,
    targetUtil: () => `<p><b>Target pickable %</b> = <code>1 − target × median switch ÷ 3600</code>. At the target rate each tote has 3600 ÷ target seconds; the switch takes its median (door included) whatever the operator does, so at best the rest of that time is pickable. A station pickable for less than this cannot reach its target even if it never waits for a robot. It moves with the door setting, because a longer switch leaves less of each tote's budget to pick in.</p>`,
    opTable: (d) => `<ul><li><b>Totes</b>: paired visits on the day.</li>
      <li><b>Totes/h</b>: visits arriving in full-production hours ÷ the number of those hours (${(d.full_hours || []).length} today). Breaks would otherwise drag the hourly rate down.</li>
      <li><b>Median / average / p90</b>: operator time, as logged.</li>
      <li><b>% of day pickable</b>: Σ over visits of <code>(arrival + door → release)</code>, clipped to the day, ÷ 86,400 s. The door seconds are removed because the tote is not pickable while the door is still opening; they are charged to the hour the robot arrived. Green meets the target share, red falls short.</li></ul>${X.targetUtil()}`,
    cycleTiles: (d) => `<ul><li><b>K50 cycle</b>: a tote loaded from the haiflex buffer (<code>coop_haiflex</code>) and unloaded back into it, by the same robot, with at least one station arrival by that robot in between. A K50 that chains a second station re-loads the tote there; that re-load does not start a new cycle.</li>
      <li><b>Per robot per hour</b>: cycles in the hour (by return time) ÷ K50s that logged any load, unload or arrival in that hour.</li>
      <li><b>Full-production hours</b>: hours whose K50 cycles reached ${C.full_hour_share * 100}% of the busiest hour (${(d.full_hours || []).length} today).</li>
      <li><b>ACR puts / stores</b>: each unload paired with the same robot's earlier load of that tote — storage → kubot buffer (<code>coop_kubot</code>) is a put, buffer → storage a store.</li>
      <li><b>Multi-station</b>: cycles with two or more station arrivals between pickup and return.</li></ul>`,
    starveTiles: (S) => `<ul><li><b>Handover</b>: a robot's release at a station (<code>will leave</code>) and the next robot's arrival there (<code>CALLBACK_OF_ROBOT_REACH_STATION</code>). Stand-downs over ${C.max_switch_s / 60} minutes are left out; full-production hours only.</li>
      <li><b>Waiting</b> = <code>max(0, gap − the station's median gap)</code> — the hour budget's waiting share. It does not move with the door setting.</li>
      <li><b>Starved</b>: waiting over ${num(S.starved_s, 1)} s.</li>
      <li><b>Left out — station closed or disabled</b>: handovers whose leaving robot was held over ${S.closed ? num(S.closed.hold_s / 60, 0) : "10"} minutes (a break or shift change; the log shows every station released in the same second afterwards), or during whose gap a task bound for the station was refused with <code>DISABLED_TARGET</code> (<code>CALLBACK_OF_TASK_EXCEPTION</code>). They are not starvation and are in none of the figures here.</li>
      <li>The other figures are explained on their own charts below.</li></ul>`,
    spatialTiles: () => `<ul><li><b>Put</b>: an ACR's load from a storage slot paired with its unload of that tote into the kubot buffer (<code>…_coop_kubot</code>). Storage and buffer slots are read as <code>HAI-&lt;aisle&gt;-&lt;bay&gt;-&lt;level&gt;_&lt;depth&gt;</code>; the aisle is the first number.</li>
      <li><b>Even share</b> = 100% ÷ the aisles used.</li>
      <li><b>Taken out again within the hour</b>: stores (buffer → storage) whose tote's next put started within 60 minutes of the store's unload.</li>
      <li><b>Time lost to sharing an aisle</b>: explained on the table below.</li>
      <li><b>Farthest vs nearest</b>: explained on the travel table below, averaged over stations.</li></ul>`,
    spatialSources: () => `<p>Every put on the day (by load time), counted by the aisle and level of the storage slot the tote came from (<code>CALLBACK_OF_TOTE_LOADED_BY_ROBOT</code>, <code>locationCode</code>). Highest level at the top. Colour runs from none to the busiest cell.</p><p>A hot aisle shows as a dark column; stock stored high as dark upper rows.</p>`,
    spatialCrowd: (L) => `<p>Each trip runs from the robot's allocation (<code>CALLBACK_OF_TASK_ALLOCATED</code>, matched on robot and task, latest before the pickup) to its pickup: for an ACR, the load from the storage slot of a put; for a K50, the load from the haiflex buffer that starts a cycle. Leads of ${num(L.lead_max_s / 60)} minutes or more are not counted.</p>
      <ul><li>At each allocation, the fleet's other trips already allocated and not yet picked up are counted — all of them (<b>fleet load</b>) and those bound for the same aisle (<b>with N others</b>).</li>
      <li>A busy fleet slows every trip, so each trip's lead is compared with the <b>median lead of all trips made at the same fleet load</b>. Seconds added = the mean of that difference for trips with N others − the same for trips alone.</li>
      <li><b>Overlap vs chance</b> = the mean same-aisle count ÷ what it would be if robots picked aisles independently: <code>fleet load × Σ (aisle share)²</code>. 1× is chance, above 1× the work clusters in place and time.</li>
      <li><b>Hours lost</b> = Σ over trips with others of the seconds added, ÷ 3600.</li></ul><p>Highlighted: 3 s or more added (with 30+ trips), or 1.2× chance or more. It shows association: a trip that shares an aisle is slower even with the fleet equally busy, which is what congestion in the aisle would do.</p>`,
    spatialLift: (L) => `<p>Mean seconds from an ACR loading a tote from a storage slot to unloading it in the kubot buffer, by the slot's level. The robot's lift has to climb to the slot and back down, so higher levels take longer. Handling over ${num(L.handle_max_s / 60)} minutes (interrupted) is left out.</p>`,
    spatialReturns: () => `<p>Every store on the day (an ACR's buffer → storage move, by unload time) is paired with the same tote's next put (storage → buffer) anywhere later in the log. Bars are the share of stores by the time until that next put; <b>never</b> is a tote not taken out again before the log ends.</p><p>A tote taken out again soon after being stored made a round trip that keeping it in the buffer would have saved. The JSON also counts totes by how many times they were put on the day.</p>`,
    spatialTravel: (T_, L) => `<p>Mean seconds from a K50's buffer pickup (<code>…_coop_haiflex</code>, the start of a cycle) to its first station arrival, by the buffer aisle it picked the tote up from. Cells with fewer than ${L.travel_min_n} trips are left blank. It includes any queueing in front of the station; trips over ${num(L.travel_max_s / 60)} minutes are left out.</p><p>Read along a row: a station's near aisles are its light cells.</p>`,
    spatialTravelTable: (L) => `<ul><li>Each station's buffer aisles (those with ${L.travel_min_n}+ trips) are ranked by its mean travel from them. <b>Nearest / farthest quarter</b>: the trip-weighted mean travel from its fastest and slowest quarter of aisles; <b>spread</b> is the difference.</li>
      <li><b>From nearest quarter</b>: the share of its trips that came from those aisles. <b>Random</b>: the share a random aisle would give (a quarter of them). Near equal means the buffer aisle is chosen without regard to the station.</li>
      <li><b>Saved if nearest</b>: the station's mean travel minus its nearest-quarter mean — the seconds per trip at stake if its totes were buffered near it.</li></ul>`,
    starveStages: () => `<p>For every handover with waiting, the robot that ended it is traced back through its own timeline: <code>task created</code> (the <code>wmsTask … is created</code> line) → <code>tote ready</code> (an ACR puts it in the buffer; at creation if it was already there) → <code>K50 allocated</code> (<code>CALLBACK_OF_TASK_ALLOCATED</code>) → <code>tote picked up</code> (the K50's buffer load) → <code>arrival</code>.</p>
      <p>The station waits from the release plus its usual handover until that arrival. Each second of the wait is charged to the stage the robot was in at that second, so the stages add up exactly to the waiting. A robot on the second or later station of a multi-station cycle is charged to <b>at another station</b>. A missing step (no ACR leg, say) is a zero-length stage.</p>
      <p>Read it as: <b>carrying the tote</b> — work and robot were there, the robot was not close enough; <b>K50 to the buffer / tote ready</b> — allocated too late; <b>waiting on an ACR</b> — supply; <b>no task yet</b> — the station had no more work. Bar ends show the hours of waiting.</p>`,
    starveHours: (S) => `<p>The same stages, in hours of station waiting, for each hour of the day (by the hour of the release)${S.days > 1 ? ", summed over the days" : ""}. All hours are shown, not only full-production ones.</p><p>Dashed outlines on top: gaps after a station was closed (its robot held over ${S.closed ? num(S.closed.hold_s / 60, 0) : "–"} minutes) or disabled — shown for scale, but not starvation and in none of the stages.</p>`,
    starveEnRoute: () => `<p>At each release, the K50s already allocated to that station (<code>CALLBACK_OF_TASK_ALLOCATED</code> naming it) that had not yet arrived there. Each bar is the share of handovers that then starved. Hollow bars have fewer than 30 handovers.</p><p>All stations are pooled, so the slow stations, which have few robots heading to them, sit on the left. The station table compares each station with its own usual pipeline instead.</p>`,
    starveTravel: (S) => `<p>Seconds from the K50's buffer pickup to its arrival at the first station of its cycle, in ${S.travel_w} s bins (the last bin collects the rest), arrivals in full-production hours. It includes any queueing in front of the station.</p><p>Robots that ended a starved wait: median ${num(S.travel.starved_med, 0)} s; all others: ${num(S.travel.other_med, 0)} s. Allocation → pickup (empty travel): median ${num(S.travel.empty_med, 0)} s, p90 ${num(S.travel.empty_p90, 0)} s.</p>`,
    starveContext: () => `<p>At the moment each starved station began to wait: the K50s <b>between tasks</b> (free; under 5 minutes since their last task — robots away are not counted), and the totes for that station <b>ready in the buffer with no K50 allocated</b>.</p><ul>${E.CONTEXT_KINDS.map((k) => `<li><b>${esc(k.label)}</b>: ${esc(k.note)}.</li>`).join("")}</ul><p>A free K50 and a ready tote at the same moment is work dispatch could have sent sooner — or a station at its slot limit.</p>`,
    starvePace: () => `<p>For each release, the mean operator time of that visit and the two before it at the station, as a share of the station's median operator time for the day. Under 100% means the operator had been picking faster than usual. Bars are the share of handovers that then starved.</p>`,
    starveResume: () => `<p>Minutes from the station resuming — its first arrival of the day, or its first arrival after a gap of 10 minutes or more — to the release. Bars are the share of handovers that starved. High bars on the left are a ramp-up effect: the robot pipeline has to refill after a stand-down.</p>`,
    sumSwitchHour: () => `<p><b>Switch</b> = <code>next arrival at the station − release</code> (<code>will leave</code> → <code>CALLBACK_OF_ROBOT_REACH_STATION</code>) + the station's door seconds (0 for stations without a door, and on days the doors were off). Gaps over ${C.max_switch_s / 60} minutes are stand-downs and left out.</p>
      <p>Each cell pools every day of the run: the sum of switch seconds of handovers whose first robot arrived in that hour, ÷ the number of those handovers. So a busy day weighs more than a quiet one. The colour scale stops at 20 s; blank cells had no handovers.</p>`,
    robotTiles: (R) => `<ul><li><b>Cycles per hour on a task</b> (K50): the robot's cycles ÷ the hours from each cycle's allocation to its tote back in the buffer. Time between tasks and away is not in it.</li>
      <li><b>Slower than usual</b>: the robot's speed index is ${num(R.slow_index, 2)} or more, with ${R.min_trips}+ trips. K50: Σ its return trips (last release → tote back in the buffer) ÷ Σ the day's mean return for the same station and buffer aisle. ACR: Σ its handling (storage load → buffer unload) ÷ Σ the day's mean for the same rack level. Each trip is first capped at ${num(R.speed_cap, 0)}× its group's median, so one jammed trip does not decide a robot; over the whole fleet the index averages 1. The return trip is used for K50s because queueing at the stations does not reach it.</li>
      <li><b>Fault kinds</b>: explained on the table below.</li></ul>`,
    robotCph: (R) => `<p>Each K50 with ${R.min_trips}+ cycles: cycles × 3600 ÷ the seconds from each cycle's allocation to its tote back in the buffer. The dashed line is the median robot. Most of a cycle is set by the stations (queueing, picking), so this spreads little unless a robot is genuinely slow.</p>`,
    robotHandle: (R) => `<p>Each ACR with ${R.min_trips}+ puts: Σ its handling seconds (storage load → buffer unload) ÷ Σ the day's mean handling for the same rack level, each put capped at ${num(R.speed_cap, 0)}× the level's median. 1 = usual; red at ${num(R.slow_index, 2)}× or more.</p>`,
    robotKinds: (R) => `<p>Each fault kind (<code>CALLBACK_OF_ROBOT_ABNORMAL</code> by its message, <code>CALLBACK_OF_TASK_SUSPENDED</code> by its message, <code>CALLBACK_OF_TOTE_LOAD_FAILED</code>, <code>CALLBACK_OF_TASK_CANCELLED</code>) is read within the fleet that has it. Each robot's <b>expected</b> count = the kind's total × the robot's share of the fleet's tasks (<code>CALLBACK_OF_TASK_ALLOCATED</code>).</p>
      <ul><li><b>Concentration</b> = Σ (events − expected)² ÷ expected, ÷ (robots − 1): about 1× when the kind falls on robots by chance; ${R.dispersion_cut}× or more (highlighted) means it piles up on particular robots.</li>
      ${R.days >= 4 ? "<li><b>Same robots, both halves of the run</b>: rank correlation of each robot's rate on alternate days. Near 1 = the same robots every time, a robot trait; near 0 = no lasting pattern.</li>" : ""}
      <li><b>Robots above chance</b>: ${R.min_events}+ events, ${R.min_times}× or more expected, and a Poisson tail below ${R.alpha} ÷ the robots tested.</li>
      <li>Failed loads at a stuck storage slot (5+ in a day) are left out — the slot is the problem there, not whichever robot was sent (${int(R.stuck_left_out)} left out).</li></ul>`,
    robotFlagged: () => `<p>Robot and fault kind pairs above chance (see the table above): its events, what its share of the fleet's tasks would give it, and the ratio. A robot listed for a hardware fault (chassis, lift, box dropped) is worth a maintenance check; one listed for "could not put the tote down" fails put-aways other robots manage.</p>`,
    robotRows: (R) => `<ul><li><b>Tasks</b>: <code>CALLBACK_OF_TASK_ALLOCATED</code> to the robot. <b>Cycles / puts</b>: K50 buffer → station → buffer cycles, or ACR storage → buffer puts.</li><li><b>Speed (× usual)</b>: see the headline (highlighted at ${num(R.slow_index, 2)}×). <b>Faults /1000 tasks</b>: every fault kind together.</li><li><b>Buffer pickups flagged</b> (K50): its pickups needing extra tries (see Problem locations). Robots highlighted have a fault kind above chance.</li></ul>`,
    faultTiles: (F) => `<ul><li><b>Flagged pickup</b>: a K50's load from the haiflex buffer (<code>CALLBACK_OF_TOTE_LOADED_BY_ROBOT</code>, <code>…_coop_haiflex</code>) that a <code>CALLBACK_OF_LOCATION_ABNORMAL</code> with <code>LOAD_FAILED_COUNT_EXCEEDED_THE_LIMIT</code> for the same tote and slot preceded by up to 10 minutes. The robot's tries to take the tote went over the limit — a slot marker or tote label it could not read, or a tote it could not line up with — and it then succeeded.</li>
      <li><b>Far above chance</b>: a one-sided binomial test of the slot's (or robot's) flagged pickups against the run's flag rate, Bonferroni-corrected for the number tested (p &lt; ${F.buffer ? F.buffer.alpha : 0.05} ÷ that number). Slots need ${F.buffer ? F.buffer.slot_min_n : 20}+ pickups and robots ${F.buffer ? F.buffer.robot_min_n : 50}+ to be read.</li>
      <li><b>Failed put-away</b>: <code>LOCATION_ABNORMAL</code> <code>UNLOAD_FAILED_COUNT_EXCEEDED_THE_LIMIT</code> or <code>CALLBACK_OF_TASK_SUSPENDED</code> "hooked failed, fail to put the box" at a storage slot (<code>HAI-a-b-l_d</code>, not a buffer).</li>
      <li><b>Stuck storage slot</b>: ${F.storage ? F.storage.stuck_min : 5}+ <code>CALLBACK_OF_TOTE_LOAD_FAILED</code> at one storage slot in a day. <b>ACR time</b> = Σ the failing robot's allocation to that task → the failure (attempts over 30 minutes not counted).</li></ul>`,
    faultMap: (B) => `<p>Each cell is one buffer slot (the K50 buffer is a single level, one deep, so aisle × bay is the slot): its flagged pickups ÷ its pickups. Blank: under ${B.cell_min_n} pickups. The colour scale stops at ${num(Math.max(10, Math.ceil(B.base_pct * 3)), 0)}% (about three times the usual rate of ${num(B.base_pct, 1)}%). Hover a cell for its bay.</p><p>Isolated hot cells are single slots; blocks of hot cells across neighbouring aisles and bays point at something physical in that part of the rack.</p>`,
    faultRepeat: () => `<p>Each pickup is paired with the next pickup by the same slot (a different tote), by the same tote (from a different slot), and by the same robot (from a different slot), on the same day. Bars are the share of those next pickups that were flagged, after a flagged pickup and after a clean one.</p><p>If the trouble belonged to the slot, the slot's next pickup would be flagged more often after a flag; if it belonged to the tote (a bad label), the tote's next pickup elsewhere would.</p>`,
    faultAisle: () => `<p>Flagged pickups ÷ pickups, all slots of each buffer aisle (the first number of <code>HAI-&lt;aisle&gt;-&lt;bay&gt;-01_1_coop_haiflex</code>).</p>`,
    faultSlots: (B) => `<p>Slots with ${B.slot_min_n}+ pickups whose flagged share is far above the usual ${num(B.base_pct, 1)}% (binomial test, Bonferroni over the ${int(B.slots_read)} slots read). <b>× usual</b> = the slot's rate ÷ the usual rate. <b>Days flagged</b>: days with at least one flagged pickup there — a slot flagged every day is a fixed fault, not bad luck.</p>`,
    faultRobots: (B) => `<p>K50s with ${B.robot_min_n}+ buffer pickups whose flagged share is far above the usual ${num(B.base_pct, 1)}%. Robots pick up from every part of the buffer, so a robot high everywhere points at the robot (its camera or scanner), not the slots.</p>`,
    faultDepth: () => `<p>ACR put-aways into storage (unloads at <code>HAI-a-b-l_1</code> front or <code>…_2</code> rear slots) and the failed ones (see the headline), by depth. A rear slot is reached past the front one.</p>`,
    faultStuck: (St) => `<p>Storage slots with ${St.stuck_min}+ failed loads (<code>CALLBACK_OF_TOTE_LOAD_FAILED</code>) in a day: one tote the ACRs could not take out, retried again and again by different robots. <b>ACR h</b>: the failing robots' allocation → failure time, summed. <b>ACRs (busiest day)</b>: distinct robots that tried on its busiest day.</p>`,
    starvePick: (S) => `<p>Each handover by the operator time of the visit just released (arrival → <code>will leave</code>, as logged). Bars are the share of handovers that then starved; hollow bars have fewer than 30 handovers.</p>
      <p>A fast pick leaves the next robot less time to move up to the station. <b>Fast</b> = under ${S.pick.fast_s} s, <b>steady</b> = ${S.pick.steady_s[0]}–${S.pick.steady_s[1]} s (very long picks are left out of that comparison: they are mostly the slow stations and interrupted picks).</p>`,
    starveRefill: (S) => `<p>For starved handovers after a pick under ${num(S.refill.pick_max_s, 0)} s: seconds from the previous robot's arrival to the next robot's arrival, in ${num(S.refill.w, 0)} s bins (the last bin collects the rest).</p>
      <p>When no robot is waiting at the station, this is how soon one gets in after the previous one arrived — the fastest the station can be fed in that state. A pick shorter than this minus the usual handover always starves. Longer picks are left out, because after one the next robot has had time and a late arrival is a supply matter.</p>`,
    cycleTimeTiles: (C) => `<ul><li><b>K50 cycle</b>: a buffer load (<code>…_coop_haiflex</code>) paired with the robot's next unload of that tote in the buffer, with one or more station arrivals between. Its start is the K50's <code>CALLBACK_OF_TASK_ALLOCATED</code> for that task (cycles with none under 30 minutes before the pickup are left out: ${int(C.no_alloc)}).</li>
      <li>Cycles whose first station arrival falls in a full-production hour.</li>
      <li><b>Per robot-hour on a task</b> = <code>cycles × 3600 ÷ Σ cycle seconds</code>. <b>Without the queueing</b> takes the queueing seconds out of that sum. Time between tasks and away is not in either; the whole-day rate in "Robot cycles per hour" counts every hour a robot was active.</li></ul>`,
    cycleTimeSegments: (C) => `<ul>${E.K50_SEGMENTS.map((x) => `<li><b>${esc(x.label)}</b>: ${esc(x.note)}.</li>`).join("")}</ul>
      <p><b>Free-flow trip</b>: for each station and buffer aisle (the first number of <code>HAI-&lt;aisle&gt;-…_coop_haiflex</code>), the ${pctl(C.free_flow_q)} percentile of the day's pickup → first arrival times (${int(C.free_flow_min_n)} trips needed, else the station's own ${pctl(C.free_flow_q)} percentile). Travel = <code>min(pickup → arrival, free-flow)</code>; queueing = the rest. That percentile is the trip with little or nothing ahead of it — the log has no positions, so queueing also takes in any slowdown on the way.</p>`,
    cycleTimeQueue: (C) => `<p>Seconds of queueing per cycle (pickup → first arrival beyond the free-flow trip), in ${num(C.queue_w, 0)} s bins; the last bin collects the rest.</p>`,
    cycleTimeStations: () => `<p>Per first station of the cycle: average seconds per cycle in each segment, the share queueing (highlighted at 30% or more), and the cycle rate per robot-hour on a task with and without the queueing.</p>`,
    starveStations: (S) => `<ul><li><b>Carrying the tote / No task yet</b>: share of the station's waiting in those stages (no task highlighted at 25% or more — short of work).</li>
      <li><b>Usual K50s on the way</b>: median count at a release. <b>Starved, pipeline under half</b>: share of handovers that starved when fewer than half that number were on the way; <b>otherwise</b>: the rest. Highlighted when 1.5× or more.</li>
      <li><b>Pickup → arrival</b>: median and p90 seconds, first station of a cycle.</li>
      <li><b>Starved after fast / steady pick</b>: share of handovers that starved after a pick under ${S.pick ? S.pick.fast_s : "–"} s / of ${S.pick ? `${S.pick.steady_s[0]}–${S.pick.steady_s[1]}` : "–"} s (highlighted at 2× or more). <b>Next robot after a starve</b>: median seconds from the previous arrival to the next, starved handovers after a pick under ${S.refill ? num(S.refill.pick_max_s, 0) : "–"} s.</li>
      <li><b>Closed h</b>: gaps beyond the usual handover left out of starvation because the station was closed (robot held over ${S.closed ? num(S.closed.hold_s / 60, 0) : "–"} minutes) or disabled (<code>CALLBACK_OF_TASK_EXCEPTION</code> DISABLED_TARGET for a task bound there during the gap), every hour of the day.</li>
      <li><b>Tasks created /h</b>: tasks with that destination created in full-production hours ÷ those hours.</li></ul>`,
    multiTiles: (M) => `<ul><li><b>Handover</b>: one robot released at a station (<code>will leave</code>) and the next robot's arrival there (<code>CALLBACK_OF_ROBOT_REACH_STATION</code>). Stand-downs over ${C.max_switch_s / 60} minutes are left out, and only full-production hours count.</li>
      <li><b>Wait</b> = <code>max(0, gap − the station's median gap)</code>: the gap beyond a normal handover, which is the hour budget's waiting share. It does not move with the door setting.</li>
      <li><b>Starved</b>: a handover whose wait is over ${num(M.starved_s, 1)} s.</li>
      <li><b>Involves a multi-station tote</b>: the robot leaving goes on to another station with the tote, or the robot arriving has come from one. Each visit is matched to its K50 cycle (buffer pickup → buffer return, same robot) and numbered by its place in it.</li>
      <li><b>Over-representation</b> = <code>share of starved handovers involving one ÷ share of all handovers involving one</code>. 1× means starvation is no more common around multi-station totes than anywhere else.</li>
      <li><b>Waiting added</b>: per station, Σ over multi-station handovers of <code>(wait − that station's mean wait on plain handovers)</code>, summed over stations, as a share of all waiting. Comparing within each station keeps slow stations from skewing it.</li>
      <li><b>Release → next station</b>: for each robot chained in, its release at the previous station to its arrival here (median${M.days > 1 ? " of the daily medians" : ""}). The station it went to was already free by then in ${num(M.chained_to_free_pct)}% of cases.</li></ul>`,
    multiHist: (M) => `<p>K50 cycles by the number of station arrivals the robot made between the buffer pickup and the return, counted by return time. Only multi-station cycles are drawn, because single-station cycles (${int(M.hist[0])}) would flatten the rest. The last bar collects ${M.hist.length} or more stations. Percentages are of multi-station cycles.</p>`,
    multiBands: (M) => `<p>Each station's full-production time is cut into ${M.window_min}-minute windows; windows with fewer than 5 handovers are left out. Each window is placed in a band by the share of its handovers that involve a multi-station tote, and each bar is the mean wait per handover over the windows in that band.</p><p>If multi-station visits caused starvation, the bars would rise from left to right. The <b>none</b> band is mostly made up of slow stations, which have few handovers per window and long waits anyway, so read it with care.</p>`,
    multiMix: (M) => `<p>Top bar: every handover, split by kind. Bottom bar: only the starved ones (wait over ${num(M.starved_s, 1)} s). If multi-station totes had nothing to do with starvation, the two bars would split the same way. The marker shows the share involving a multi-station tote.</p>
      <ul>${M.kinds.map((k) => `<li><b>${esc(k.label)}</b>: ${esc(k.note)}.</li>`).join("")}</ul>`,
    multiKinds: () => `<p>Per kind of handover: how many, the mean wait (beyond the station's median handover), how many were starved, the share of that kind that starved, and that kind's share of all starved handovers. Highlighted: a multi-station kind waiting more than 1.5× a plain handover.</p>`,
    multiStations: () => `<p>Per station, full-production hours. <b>Wait</b> is the mean wait per handover, plain and multi-station. <b>Starved with multi-station</b> is the share of the station's starved handovers that involved a multi-station tote; it is highlighted when it is 1.5× or more the station's overall multi-station share. <b>Waiting added</b> is the station's multi-station wait above its own plain average, in minutes and as a share of all its waiting (highlighted at 10% or more).</p>`,
    cyclesChart: () => `<p>Bars: K50 cycles per active K50, by the hour the tote returned to the buffer. Lines: ACR puts and stores per active ACR, by the hour of the unload.</p><p>"Active" means the robot logged any load, unload or station arrival in the hour, so idle robots that were switched on but never moved are not counted.</p>`,
    hourly: () => `<p>The numbers behind the chart. Multi-station counts cycles returned in that hour whose tote was presented at two or more stations. Relocations are ACR storage-to-storage moves — digging to reach a buried tote.</p>`,
    k50Table: () => `<p>Per K50: buffer-to-buffer cycles on the day ÷ hours in which that robot logged any event. Sorted high to low.</p>`,
    acrTable: () => `<p>Per ACR: puts and stores on the day ÷ hours in which that robot logged any event. Relocations are shown but not counted in the rate. Sorted high to low.</p>`,
    dayHeat: (s) => ({
      visits: `<p>Count of paired visits per station by the hour of arrival.</p>`,
      op: `<p>Median operator time (release − arrival) of the visits arriving in each station-hour. Colour is capped at 60 s; the number shows the true value.</p>`,
      sw: `<p>Median switch time (gap + door) of the handovers whose first robot arrived in each station-hour.</p>${doorClause()}`,
      swmean: `<p>Average switch time = Σ (gap + door) ÷ handovers, over the handovers whose first robot arrived in each station-hour (gaps over ${C.max_switch_s / 60} minutes are stand-downs and left out). Long waits for a robot pull it up, so where it sits well above the median the station was waiting, not switching. Colour is capped at 20 s; the number shows the true value.</p>${doorClause()}`,
      possible: `<p><b>Presented</b>: paired visits arriving in the hour. <b>Possible</b> = the station's operating time that hour ÷ (target pick + its measured switch and wait per tote that hour), where operating time is its picking + switch + waiting seconds in the hour. In other words: had each pick taken the target time and everything else stayed as it was, this many totes would have been presented. A low share points at pick time; a drop in possible as well points at breaks, starvation or switch.</p><p>The target pick time comes from the Settings page — or, if blank, the station's budget minus its target switch. Stations with no target show presented only.</p>`,
      gt10: `<p>Share of those handovers whose switch (door included) is above 10 s. Raising the door seconds lowers the threshold the measured gap has to cross, so this rises with the door.</p>`,
      util: `<p>Σ <code>(arrival + door → release)</code> for the station, each interval clipped exactly to the hour, ÷ 3,600 s. The door seconds come off the visit that arrived in that hour.</p>${X.targetUtil()}`,
    })[s.key],
    budgetTiles: (z) => `<p>Means across the ${z.rows.length} station${z.rows.length > 1 ? "s" : ""} shown, over the full-production hours.</p><ul><li><b>Mean cycle</b> = picking + switch + waiting per tote = 3600 ÷ actual totes per hour.</li><li><b>Over / inside budget</b> = mean cycle − 3600 ÷ target.</li><li><b>If robots were never late</b> = 3600 ÷ (picking + switch): the rate with every wait removed.</li></ul>`,
    budgetChart: (z, D) => `<p>For each station, over the ${D.budget.full_hours.length} full-production hours:</p>
      <ul><li><b>Budget</b> = 3600 ÷ target (the dashed line, and the scale at the top: 0 → target → the slowest station's cycle).</li>
      <li><b>Picking</b> = Σ <code>(arrival + door → release)</code> ÷ totes.</li>
      <li><b>Switch</b> = Σ <code>(door + release → release + that station's median gap)</code>, stopped early if the next robot arrives sooner, ÷ totes.</li>
      <li><b>Waiting for robot</b> = Σ the rest of the gap until the next arrival ÷ totes.</li></ul>
      <p>Every interval is clipped to the hour it falls in, so nothing outside the full-production hours leaks in. These are <b>means, not medians</b>: means add up, so picking + switch + waiting is exactly the station's real cycle (3600 ÷ its actual rate) and can be compared with the budget. Medians of the three parts would not sum to anything.</p>
      <p>The hatched part of a bar is the time over budget. ${settingsDoor() ? `The ${settingsDoor()} s door setting moves that much from picking into the switch; the cycle and the overrun do not change.` : ""}</p>`,
    budgetTargets: (z) => `<p>The same tote cycle, read against each station's targets. Each station has two strips on one seconds scale, so equal seconds are equal widths:</p>
      <ul><li><b>Top — the target</b>, adding up to the target cycle (3600 ÷ target totes/h): <b>target pick</b> (set on the Settings page, or if blank, whatever the target cycle leaves after the target switch), <b>target switch</b> (set, or if blank, the station's measured median switch, door included), and the <b>buffer</b> — what is left for waiting for the next robot.</li>
      <li><b>Bottom — what happened</b>: mean picking, switch and waiting for robot per tote. Their sum is the station's real cycle, 3600 ÷ its actual totes/h.</li></ul>
      <p>Where the bottom strip runs past the dashed target line it is outlined red: that is the time per tote over target. Hover a part for its seconds against its target. On the right: actual seconds per tote, actual totes per full-production hour (green at or above target), and the time over or spare.</p>
      <p>If the pick and switch targets alone add up to more than the target cycle, there is no buffer; the chart says so above the bars.</p>
      <p>Values are mean seconds per tote over the full-production hours. In the table, targets marked * are the defaults described above rather than set values.</p>`,
    slotsTable: () => `<p>A task is <b>assigned</b> to a station from the K50's <code>CALLBACK_OF_TASK_ALLOCATED</code> (which names the destination station) until that robot's "will leave" release there — travel, queueing and picking included. The count is taken second by second.</p>
      <ul><li><b>Slot limit</b>: the highest count the station held for at least 10% of production time, provided it is almost never exceeded (under 2% of the time above it). That shape — a pile-up against a ceiling — is what a dispatcher cap looks like; a station that never piles up shows none.</li>
      <li><b>Time at limit</b>: share of full-production time at or above the limit, when no more tasks could be sent.</li>
      <li><b>Ready totes at / below limit</b>: average of that station's totes sitting ready in the buffer with no K50, while it is at its limit versus while it has a free slot. Much higher at the limit means released work is being held back by the slots.</li>
      <li><b>Slots needed at target</b> = target totes/h × allocation-to-release time ÷ 3600 (Little's law): tasks that must be in flight to present at target. Red when that is within 10% of the limit — no headroom for travel delays.</li></ul>`,
    slotsChart: () => `<p>Per station, averaged over 5 minutes: tasks assigned to it (blue), its totes ready in the buffer still waiting for a K50 (amber), and the slot limit (red, dashed). Ready totes rising while the blue line sits on the limit is released work held back by the station's slots.</p>`,
    budgetWhatIf: () => `<ul><li><b>Wait allowed</b> = budget − picking − switch: how long a tote can wait for the next robot and still make target. Negative means target is out of reach even with no waiting.</li>
      <li><b>No waiting</b> = 3600 ÷ (picking + switch).</li>
      <li><b>Picks within allowance</b>: every pick (door removed) capped at <code>budget − the station's median switch</code>, everything else as measured.</li>
      <li><b>Both</b>: capped picks and no waiting.</li></ul><p>Each column removes one cause and keeps everything else as measured, so they show what fixing that cause alone would be worth.</p>`,
    budgetScatter: (z, sel, shown) => {
      const rp = E.corr(shown.map((d) => d[0]), shown.map((d) => d[1])), rw = E.corr(shown.map((d) => d[2]), shown.map((d) => d[1]));
      return `<p>One dot per station per full-production hour. x = mean pick seconds per tote in that hour (door removed); y = totes presented in the hour. The dashed lines are the target rate and the pick allowance (budget − the zone's median switch). Each station has its own colour; use the buttons above the chart to isolate stations — the others fade to grey for context.</p>
        <p>For ${sel.length ? esc(E.runs(sel)) : "all stations"} (${shown.length} station-hours), the correlation with the hourly rate is r = ${rp == null ? "–" : rp.toFixed(2)} for pick time and r = ${rw == null ? "–" : rw.toFixed(2)} for waiting. The stronger the (negative) correlation, the more that factor drives the rate.</p>`;
    },
    utilTiles: (role, u) => `<p>Every ${role}, every second, is in one of three states:</p>
      <ul><li><b>On a task</b> — from <code>CALLBACK_OF_TASK_ALLOCATED</code> until it puts the tote down, empty travel to the pickup included.${role === "K50" ? " For a K50 that is the whole buffer → station(s) → buffer cycle, queuing at a station included." : " ACR stores and relocations are not allocated in the log, so they count from the load."} A robot carrying several totes is counted once.</li>
      <li><b>Between tasks</b> — less than ${C.away_min_s / 60} minutes since its last task: free and waiting for its next allocation.</li>
      <li><b>Away</b> — ${C.away_min_s / 60} minutes or more without a task. The log has no charging, maintenance or fault events, so this is most likely charging; either way the robot was not available.</li></ul>
      <p>Before its first task and after its last task of the day a robot is off shift and counted in none of these.</p>
      <p><b>% of available</b> = on a task ÷ (on a task + between tasks). <b>% of fleet</b> = on a task ÷ the ${u.fleet} ${role}s that did any work today — a lower bound, because robots away still count. Averages are over the full-production hours (K50 cycles ≥ ${C.full_hour_share * 100}% of the busiest hour).</p>`,
    statesChart: (role) => `<p>Average ${role}s in each state over each 5 minutes, stacked: on a task (bottom), between tasks, away. The dashed line is the fleet that worked today; the gap above the stack is robots off shift. Right-hand scale: percent of that fleet.</p>`,
    gapChart: (role, u) => `<p>Every gap between one task and the robot's next, in full-production hours, grouped by length. Bar length is the share of all idle robot-time spent in gaps of that length.</p><p>Dispatch delays show up as many short gaps; charging shows up as fewer, long ones. Here the typical gap is ${num(u.day.median_gap_s, 0)} s, and ${num(u.day.away_share_of_idle, 0)}% of idle time is in gaps of ${C.away_min_s / 60} minutes or more.</p>`,
    utilTable: () => `<p>Per hour and fleet: average robots on a task, between tasks and away, and the share of available robots (and of the whole fleet) on a task.</p>`,
    supplyTiles: () => `<ul><li><b>Created</b>: <code>wmsTask[…]: ND… is created</code> lines — a task issued by the warehouse system.</li>
      <li><b>Tote in the buffer</b>: the ACR's unload at the <code>coop_kubot</code> slot for that task. That slot is the same physical position as the K50's <code>coop_haiflex</code> pickup, so the tote is ready the moment the ACR puts it down. Tasks whose tote was already in the buffer count as ready when created.</li>
      <li><b>K50 allocated</b>: the first K50 <code>CALLBACK_OF_TASK_ALLOCATED</code> for the task. A K50 is never allocated before its tote is ready.</li></ul>
      <p>Backlogs are time-weighted averages of how many tasks were in each step. Tasks that never complete a step (cancelled, or open when the log ends) are left out of that step.</p>`,
    supplyChart: () => `<p>Over each 5 minutes: totes ready in the buffer with no K50 allocated (amber), tasks waiting on an ACR (dark), and K50s away and between tasks.</p><p>Ready totes building up while K50s are <i>between tasks</i> means robots are free but not being sent; while K50s are <i>away</i>, it means robots are missing. Robots between tasks with nothing ready means they are waiting for supply.</p>`,
    supplyTable: () => `<p>Per destination station, for K50 allocations in full-production hours: how long a ready tote waited for a K50 (median, p90), and how many of that station's totes were ready and waiting on average. Totes for slow stations wait longest, since a station takes a new robot only as fast as it releases the last.</p>`,
    hourScatter: () => `<p>Each dot is one full-production hour${summary ? " of one day" : ""}; its colour is the totes presented in that hour. Pick a preset, or any two measures. The dashed line is the least-squares fit and r the correlation: near ±1 the two move together, near 0 they do not. Correlation is not cause — read it with the other charts.</p>
      <ul><li><b>Station wait per tote</b>: seconds a station spent waiting for its next robot beyond the normal handover, per tote, for stations with a target (all stations if none have one).</li>
      <li><b>K50 utilization, of the day's fleet</b>: on a task ÷ K50s that worked that day. <b>Of available</b>: on a task ÷ (on a task + between tasks).</li>
      <li><b>K50s away</b>: without a task for ${C.away_min_s / 60} minutes or more — most likely charging.</li>
      <li><b>Totes ready, no K50 yet</b>: average count of totes in the buffer awaiting a K50.</li>
      <li><b>Tasks created</b>: tasks the warehouse system issued in the hour.</li></ul>
      <p>How to read the key presets: station waiting that rises with K50s away points to robot availability; K50 utilization that follows task creation points to supply; free K50s alongside ready totes points to dispatch.</p>`,
    sumTiles: () => `<p>The median across the days of each day's headline value. Medians rather than means, so one bad day does not move the "normal day" picture.</p>`,
    sumLine: (h) => `<p>${esc(h.label)} for each day, with the median day dashed.</p>` + ({
      visits: "<p>Paired visits on the day.</p>",
      rate: "<p>Mean, across the stations that have a target, of totes presented per full-production hour.</p>",
      over_s: "<p>Mean, across the stations that have a target, of (mean cycle per tote − 3600 ÷ target). Positive is over budget.</p>",
      op_med: "<p>Median operator time of every visit on the day, as logged.</p>",
      sw_med: "<p>Median switch time of every handover on the day, door included.</p>",
      wait_s: "<p>Mean, across the stations that have a target, of seconds per tote spent waiting for the next robot.</p>",
      util: "<p>Mean across stations of the share of the day a tote was pickable (door removed).</p>",
      k50_per: "<p>K50 cycles on the day ÷ K50 robot-hours.</p>",
      multi_pct: "<p>Share of K50 cycles whose tote was presented at two or more stations before returning to the buffer.</p>",
      starved_multi_pct: `<p>Of the handovers where the station waited for the next robot more than ${starvedLabel()} beyond its usual handover, the share where the robot leaving went on to another station or the robot arriving came from one. Full-production hours.</p>`,
      starved_pct: `<p>Share of handovers in full-production hours where the station waited more than ${starvedLabel()} beyond its usual handover for the next robot. The threshold is a run setting (Settings page of the app, or <code>starved_s</code> in ess_config.json).</p>`,
      travel_med: "<p>Median seconds from a K50 picking the tote up in the buffer to arriving at its first station, full-production hours.</p>",
      multi_excess_pct: "<p>Waiting at multi-station handovers above each station's own plain-handover average, as a share of all the stations' waiting for robots. Full-production hours.</p>",
      k50_util: "<p>Average K50s on a task (allocation → tote put down) during the full-production hours ÷ the K50s that worked that day. A lower bound: robots away (most likely charging) still count.</p>",
      k50_avail_util: "<p>Average K50s on a task ÷ K50s on a task or between tasks (free, under 5 minutes since the last task), over the full-production hours.</p>",
      k50_away: "<p>Average K50s without a task for 5 minutes or more during the full-production hours — most likely charging.</p>",
      ready: "<p>Average totes sitting ready in the buffer with no K50 allocated, during the full-production hours.</p>",
      acr_util: "<p>Average ACRs on a task during the full-production hours ÷ the ACRs that worked that day. Stores and relocations count from the load, as they are not allocated.</p>",
    }[h.key] || ""),
    sumTable: () => `<p>Every headline metric for every day. Green: better than the median day by more than 5%. Red: worse by more than 5%. Multi-station share is not judged either way.</p>`,
    sumStation: (s) => `<p>${esc(s.note)}</p><p>Colour runs from the lowest to the highest value across the whole grid, so the scale is shared by every station and day.</p>${s.key === "util" ? X.targetUtil() + "<p>The target share is worked out per day from that day's median switch, so the mark tracks each day's own switch time.</p>" : ""}`,
    sumHour: (s) => `<p>${esc(s.note)}</p><p>Hours are the hour of arrival (totes) or of return to the buffer (cycles). Blank cells: nothing happened.</p>`,
  };

  // ── navigation ─────────────────────────────────────────────────────────
  /** The left panel: the current page's categories and their sections, read off the page. */
  function renderNav() {
    const nav = $("nav"), pageName = currentRoute === "summary" ? "Summary" : currentRoute === "settings" ? "Settings"
      : prettyDate(currentRoute, { weekday: "short", day: "numeric", month: "short" });
    let h = `<div class="nav-page">${esc(pageName)}</div>`;
    const cats = [...document.querySelectorAll("#view .category")];
    const link = (el, cls, label) => `<div class="nav-row"><a href="#${el.id}" class="${cls}" data-target="${el.id}">${esc(label)}</a>` +
      (hasJson(el) ? `<button type="button" class="nav-json" data-json="${el.id}" title="Copy everything in “${esc(label)}” as JSON" aria-label="Copy ${esc(label)} as JSON">${ICON.json}</button>` : "") + "</div>";
    if (cats.length) {
      for (const c of cats) {
        h += `<div class="nav-cat">${link(c, "cat", c.dataset.title)}`;
        const secs = [...c.querySelectorAll(":scope > section[id]")];
        if (secs.length > 1 || (secs[0] && secs[0].dataset.title !== c.dataset.title))
          h += `<div class="nav-subs">${secs.map((x) => link(x, "subl", x.dataset.title)).join("")}</div>`;
        h += "</div>";
      }
    } else {
      // A page without categories (Settings) lists its titled sections and cards.
      h += [...document.querySelectorAll("#view section[id], #view .card[id]")].map((x) => link(x, "subl", x.dataset.title)).join("");
    }
    nav.innerHTML = h;
    nav.querySelectorAll("a[data-target]").forEach((x) => {
      x.onclick = (e) => {
        e.preventDefault();            // the hash is the page; scrolling stays within it
        const t = document.getElementById(x.dataset.target);
        if (t) t.scrollIntoView({ behavior: "smooth", block: "start" });
      };
    });
    nav.querySelectorAll("button[data-json]").forEach((b) => {
      b.onclick = () => {
        const el = document.getElementById(b.dataset.json);
        if (el) copyJson(blockJson(el), slug((el.dataset.title || "section") + " " + currentSubtitle()));
      };
    });
    $("settingsLink").setAttribute("aria-current", currentRoute === "settings" ? "page" : "false");
    spy();
  }

  // ── JSON for a whole section or category (the left panel's copy buttons) ──
  const hasJson = (el) => el.classList.contains("category") || el.tagName === "SECTION" || !!el._json;
  function sectionJson(sec) {
    const out = { section: sec.dataset.title };
    const def = sec.querySelector(":scope > .def");
    if (def) out.definition = def.textContent;
    const findings = [...sec.querySelectorAll(":scope > p:not(.def)")].map((p) => p.textContent.trim()).filter(Boolean);
    if (findings.length) out.findings = findings;
    const notes = [...sec.querySelectorAll(":scope > ol li")].map((li) => li.textContent.trim());
    if (notes.length) out.notes = notes;
    out.visuals = [...sec.querySelectorAll(".card")].filter((c) => c._json).map((c) => c._json());
    return out;
  }
  function blockJson(el) {
    const head = { page: currentSubtitle(), settings: { door_s: settings.door_s, targets: settings.targets } };
    if (el.classList.contains("category"))
      return { ...head, category: el.dataset.title, sections: [...el.querySelectorAll(":scope > section[id]")].map(sectionJson) };
    if (el.tagName === "SECTION") return { ...head, ...sectionJson(el) };
    return { ...head, ...(el._json ? el._json() : {}) };
  }

  /** Highlight the section (and its category) under the top bar. */
  let spyPending = false;
  function spy() {
    const top = document.querySelector(".bar").getBoundingClientRect().bottom + 24;
    let cur = null, curCat = null;
    for (const el of document.querySelectorAll("#view .category, #view section[id]")) {
      if (el.getBoundingClientRect().top > top) break;
      if (el.classList.contains("category")) { curCat = el.id; cur = null; } else cur = el.id;
    }
    // At the very bottom the last section may never reach the bar: it is the current one.
    if (window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4) {
      const all = document.querySelectorAll("#view section[id]"), last = all[all.length - 1];
      if (last) { cur = last.id; const c = last.closest(".category"); curCat = c ? c.id : curCat; }
    }
    document.querySelectorAll("#nav a[data-target]").forEach((a) => {
      const on = a.dataset.target === cur || (a.classList.contains("cat") && a.dataset.target === curCat);
      if (on) a.setAttribute("aria-current", "location"); else a.removeAttribute("aria-current");
    });
  }
  window.addEventListener("scroll", () => {
    if (spyPending) return;
    spyPending = true;
    requestAnimationFrame(() => { spyPending = false; spy(); });
  }, { passive: true });

  /** The day switcher, top right: Summary and every day, plus previous / next. */
  function pages() { return (summary ? ["summary"] : []).concat(R.days.map((d) => d.date)); }
  function setupDateSwitcher() {
    const sel = $("dateSel");
    sel.innerHTML = (summary ? `<option value="summary">Summary · ${R.days.length} days</option>` : "") +
      R.days.map((d) => `<option value="${d.date}">${esc(prettyDate(d.date, { weekday: "short", day: "numeric", month: "short", year: "numeric" }))} · ${d.overall.visits.toLocaleString()} totes</option>`).join("") +
      `<option value="settings" hidden>Settings</option>`;
    const go = (r) => { location.hash = r === "summary" || r === "settings" ? r : "day/" + r; };
    sel.onchange = () => go(sel.value);
    const step = (k) => {
      const P = pages(), i = P.indexOf(currentRoute);
      const j = i < 0 ? (k > 0 ? 0 : P.length - 1) : i + k;
      if (j >= 0 && j < P.length) go(P[j]);
    };
    $("prevDay").onclick = () => step(-1);
    $("nextDay").onclick = () => step(1);
  }
  function syncDateSwitcher() {
    const P = pages(), i = P.indexOf(currentRoute);
    $("dateSel").value = currentRoute;
    $("prevDay").disabled = i === 0;
    $("nextDay").disabled = i === P.length - 1;
  }

  function route() {
    const hash = location.hash.replace(/^#/, "");
    let r = hash.startsWith("day/") ? hash.slice(4) : hash || (summary ? "summary" : R.days[0].date);
    if (r === "settings") return r;
    if (r !== "summary" && !R.days.some((d) => d.date === r)) r = summary ? "summary" : R.days[0].date;
    if (r === "summary" && !summary) r = R.days[0].date;
    return r;
  }

  function render(keepScroll) {
    readPalette();
    const r = route(), y = window.scrollY;
    currentRoute = r;
    if (r === "settings") {
      $("viewTitle").textContent = "Settings";
      $("viewSub").textContent = `Apply to all ${R.days.length} day${R.days.length > 1 ? "s" : ""} and the summary`;
      renderSettings();
    } else if (r === "summary") {
      $("viewTitle").textContent = "Summary";
      $("viewSub").textContent = `${R.days.length} days, ${summaryRange()} · ${R.stations.length} stations`;
      renderSummary();
    } else {
      const i = R.days.findIndex((d) => d.date === r), d = R.days[i];
      $("viewTitle").textContent = prettyDate(d.date, { weekday: "long", day: "numeric", month: "long", year: "numeric" });
      let sub = `${d.source} · ${d.stations.length} stations`;
      if (d.robot_k50) sub += ` · ${d.robot_k50.n} K50s (${d.k50_id_range}), ${d.robot_acr.n} ACRs (${d.acr_id_range})`;
      if (d.idle) sub += ` · idle ${d.idle[0]}–${d.idle[1]}`;
      if (dayDoorsOff(d.date) && Number(settings.door_s) > 0) sub += " · doors off this day";
      $("viewSub").textContent = sub;
      renderDay(i);
    }
    if (keepScroll) window.scrollTo(0, y);
    syncDateSwitcher();
    renderNav();
  }

  /** The first card visible under the sticky bar, and where it sits. */
  function captureAnchor() {
    const barBottom = document.querySelector(".bar").getBoundingClientRect().bottom;
    for (const c of document.querySelectorAll("#view .card[data-key]")) {
      const rect = c.getBoundingClientRect();
      if (rect.bottom > barBottom + 8) return { key: c.dataset.key, top: rect.top };
    }
    return null;
  }
  function restoreAnchor(a) {
    if (!a) return;
    const c = [...document.querySelectorAll("#view .card[data-key]")].find((x) => x.dataset.key === a.key);
    if (c) window.scrollBy(0, c.getBoundingClientRect().top - a.top);
  }

  // ── live controls ──────────────────────────────────────────────────────
  let pending = false;
  function changed(keepPage) {
    saveSettings();
    $("modified").hidden = !isModified();
    targetsLabel();
    if (pending) return;
    pending = true;
    requestAnimationFrame(() => {
      pending = false;
      recompute();
      if (keepPage && currentRoute === "settings") return;   // keep focus in the box being typed in
      render(true);
    });
  }

  function targetsLabel() {
    const zoneVals = R.zone_list.filter((z) => settings.targets[z.zone]).map((z) => `${z.zone.replace("Zone ", "")} ${settings.targets[z.zone]}/h`);
    const n = R.stations.filter((st) => ["targets", "pick_s", "switch_s"].some((k) => settings[k][st] !== undefined)).length
      + R.zone_list.filter((z) => settings.pick_s[z.zone] !== undefined || settings.switch_s[z.zone] !== undefined).length;
    const without = R.stations.filter((st) => !E.hasDoor(st, R.zones, settings)).length;
    const daysOff = R.days.filter((d) => dayDoorsOff(d.date)).length;
    const txt = (zoneVals.length ? zoneVals.join(", ") : "no targets") + (n ? ` · ${n} more` : "") + (without ? ` · ${without} without door` : "") + (daysOff ? ` · doors off ${daysOff} day${daysOff > 1 ? "s" : ""}` : "");
    const ns = $("navSettings");
    if (ns) ns.textContent = txt;
  }

  function init() {
    if (!R.days.length) { $("view").innerHTML = `<p class="empty">No days in this report.</p>`; return; }
    $("brandSub").textContent = R.days.length > 1 ? summaryRange() : prettyDate(R.days[0].date);
    $("generated").textContent = "Generated " + R.generated;
    $("modified").hidden = !isModified();

    const themeBtn = $("themeBtn");
    const applyTheme = (t) => {
      if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme;
      const dark = t ? t === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
      themeBtn.textContent = dark ? "Light" : "Dark";
    };
    let theme = null;
    try { theme = localStorage.getItem("ess-report-theme"); } catch (e) { /* ignore */ }
    applyTheme(theme);
    themeBtn.onclick = () => {
      const dark = document.documentElement.dataset.theme ? document.documentElement.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
      const t = dark ? "light" : "dark";
      try { localStorage.setItem("ess-report-theme", t); } catch (e) { /* ignore */ }
      applyTheme(t); render(true);
    };
    try { matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => render(true)); } catch (e) { /* old browser */ }

    recompute();
    setupDateSwitcher();          // after recompute: the summary exists only once it has run
    targetsLabel();
    window.addEventListener("hashchange", () => {
      // Moving between days keeps the same visual under the eye, so two days
      // can be compared by flicking between them.  Summary <-> day starts at the top.
      const prev = currentRoute, anchor = captureAnchor(), y = window.scrollY;
      render(false);
      if (prev && !["summary", "settings"].includes(prev) && !["summary", "settings"].includes(currentRoute)) {
        window.scrollTo(0, y);
        restoreAnchor(anchor);
      } else window.scrollTo(0, 0);
    });
    render(false);
  }
  init();
})();
