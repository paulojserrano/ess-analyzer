/* ESS Analyzer — desktop UI (vanilla JS, no build step). */
"use strict";

// ── tiny DOM helpers ───────────────────────────────────────────────────────
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "html") el.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat(Infinity)) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

const ICONS = {
  files: '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v5h5"/><path d="M8 13h8M8 17h5"/>',
  sliders: '<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6"/>',
  play: '<path d="m7 4 13 8-13 8V4z"/>',
  chart: '<path d="M3 3v18h18"/><path d="M7 16v-4M12 16V8M17 16v-7"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  moon: '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M6.3 17.7l-1.4 1.4M19.1 4.9l-1.4 1.4"/>',
  upload: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m17 8-5-5-5 5"/><path d="M12 3v12"/>',
  x: '<path d="M18 6 6 18M6 6l12 12"/>',
  trash: '<path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  alert: '<path d="m21.7 18-8-14a2 2 0 0 0-3.4 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.7-3Z"/><path d="M12 9v4M12 17h.01"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 16v-4M12 8h.01"/>',
  folder: '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.7-.9L9.6 3.9A2 2 0 0 0 7.9 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>',
  external: '<path d="M15 3h6v6M10 14 21 3M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
  stop: '<rect x="6" y="6" width="12" height="12" rx="2"/>',
  calendar: '<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>',
};

function icon(name, cls = "") {
  const span = h("span", { class: "ico " + cls });
  span.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + (ICONS[name] || "") + "</svg>";
  return span;
}
function hydrateIcons(root = document) {
  $$("[data-icon]", root).forEach((el) => {
    const i = icon(el.dataset.icon, el.className.replace("ico", "").trim());
    el.replaceWith(i);
  });
}

const fmt = {
  int: (n) => (n == null ? "—" : Number(n).toLocaleString()),
  secs: (s) => {
    if (s == null) return "—";
    s = Math.round(s);
    return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
  },
  bytes: (b) => (b >= 1 << 30 ? (b / (1 << 30)).toFixed(1) + " GB"
    : b >= 1 << 20 ? Math.round(b / (1 << 20)) + " MB"
    : Math.max(1, Math.round(b / 1024)) + " KB"),
};

// ── API ────────────────────────────────────────────────────────────────────
async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", headers: {} };
  if (opts.json !== undefined) {
    init.method = opts.method || "POST";
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(opts.json);
  } else if (opts.body) {
    init.method = opts.method || "POST";
    init.body = opts.body;
  }
  let res;
  try {
    res = await fetch(path, init);
  } catch (e) {
    throw new Error("Lost connection to the analyzer. Is it still running?");
  }
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch (e) { data = text; }
  if (!res.ok) throw new Error((data && data.detail) || `Request failed (${res.status})`);
  return data;
}

function toast(msg, kind = "info", ms = 4200) {
  const el = h("div", { class: `toast ${kind}` },
    icon(kind === "error" ? "alert" : kind === "success" ? "check" : "info"),
    h("div", {}, msg));
  $("#toasts").append(el);
  setTimeout(() => el.remove(), ms);
}

// ── state ──────────────────────────────────────────────────────────────────
const store = {
  get(key, fallback) {
    try { const v = localStorage.getItem("ess." + key); return v == null ? fallback : JSON.parse(v); }
    catch (e) { return fallback; }
  },
  set(key, v) { try { localStorage.setItem("ess." + key, JSON.stringify(v)); } catch (e) { /* ignore */ } },
};

const S = {
  meta: null, state: null, page: "data", logs: [], logCursor: 0,
  results: null, renderedResults: null, settings: null, sig: {}, lastJobStatus: "idle",
};

function defaultSettings(meta) {
  return { door_s: meta.defaults.door_s, target_rate: meta.defaults.target_rate, starved_s: meta.defaults.starved_s, targets: {}, pick_s: {}, switch_s: {}, no_door: {}, no_door_days: {} };
}
function saveSettings() { store.set("settings", S.settings); }

// ── navigation ─────────────────────────────────────────────────────────────
const PAGES = {
  data: ["Data", "Drop in one Hairobotics log per day — .log or .log.gz, no need to unzip."],
  configure: ["Settings", "Starting values for the report. Door time and targets can also be changed live in the report itself."],
  run: ["Run", "Progress and log of the current run."],
  results: ["Results", "The reports this run produced."],
  history: ["History", "Previous runs saved in the output folder."],
};

function go(page) {
  S.page = page;
  $$(".nav-item").forEach((b) => b.classList.toggle("active", b.dataset.page === page));
  $$(".page").forEach((p) => p.classList.toggle("active", p.id === "page-" + page));
  $("#page-title").textContent = PAGES[page][0];
  $("#page-sub").textContent = PAGES[page][1];
  render(true);
  if (page === "history") renderHistory();
  $(".main").scrollTop = 0;
}

// ── rendering ──────────────────────────────────────────────────────────────
function days() { return (S.state && S.state.days) || []; }
function jobRunning() { return S.state && S.state.job.status === "running"; }
function canRun() { return !!S.state && !jobRunning() && days().length > 0; }

function render(force = false) {
  if (!S.state) return;
  renderChrome();
  const sig = JSON.stringify(days());
  if (S.page === "data" && (force || sig !== S.sig.data)) { renderData(); S.sig.data = sig; }
  const stSig = JSON.stringify([S.state.stations || {}, days().map((d) => d.date)]);
  if (S.page === "configure" && (force || stSig !== S.sig.stations)) { renderConfigure(); S.sig.stations = stSig; }
  if (S.page === "run") renderRun(force);
  if (S.page === "results" && S.renderedResults !== resultsKey()) renderResults();
}

function renderChrome() {
  const st = S.state, n = days().length;
  $("#badge-data").textContent = n ? String(n) : "";
  const runBadge = $("#badge-run");
  runBadge.textContent = jobRunning() ? `${Math.round(st.job.progress)}%` : "";
  runBadge.classList.toggle("live", jobRunning());

  const pill = $("#status-pill");
  let text = "Ready", cls = "ok";
  if (jobRunning()) { text = st.job.step || "Running…"; cls = "busy"; }
  else if (!n) { text = "No logs added"; cls = ""; }
  else { text = `${n} day${n > 1 ? "s" : ""} ready`; }
  pill.className = "status-pill " + cls;
  $("#status-text").textContent = text;

  const rb = $("#run-btn");
  rb.disabled = !canRun();
  rb.title = jobRunning() ? "A run is in progress"
    : !n ? "Add at least one log file" : "Run analysis (Ctrl+Enter)";
}

// ── Data page ──────────────────────────────────────────────────────────────
function step(n, title, body) {
  return h("div", { class: "card step" }, h("span", { class: "n" }, String(n)),
    h("b", {}, title), h("div", { class: "muted" }, body));
}

function renderData() {
  const page = $("#page-data");
  page.replaceChildren();
  const list = days();

  page.append(h("div", {
    class: "dropzone" + (list.length ? " compact" : ""), role: "button", tabindex: "0",
    onclick: () => pickFiles(),
    onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pickFiles(); } },
  },
    icon("upload", "xl"),
    h("div", {},
      h("h3", {}, list.length ? "Add more logs" : "Drop Hairobotics logs here, or click to browse"),
      h("div", {}, "One .log or .log.gz per operational day — gzipped files are read as they are. Files sharing a date in their name are merged into one day. You can also drop a whole folder."))));

  if (!list.length) {
    page.append(h("div", { class: "steps" },
      step(1, "Add logs", "Every .log or .log.gz file is one operational day."),
      step(2, "Set targets", "Per zone or station — rate, pick and switch — plus the door travel the log cannot see. All of them can be changed later inside the report."),
      step(3, "Run", "One report: a summary across the days plus every day, recalculating live as you change the settings.")));
    return;
  }

  const total = list.reduce((a, d) => a + d.bytes, 0);
  const rows = list.map((d) => h("tr", {},
    h("td", {}, h("b", {}, d.date || d.label)),
    h("td", { class: "muted" }, d.files.join(", ")),
    h("td", { class: "num right" }, fmt.bytes(d.bytes)),
    h("td", { class: "right" }, h("button", {
      class: "icon-btn", title: `Remove ${d.date || d.label}`, "aria-label": `Remove ${d.date || d.label}`,
      onclick: async () => { await api("/api/days/remove", { json: { files: d.files } }); refresh(true); },
    }, icon("x")))));
  page.append(h("div", { class: "card", style: { marginTop: "18px" } },
    h("div", { class: "card-head" },
      h("div", {}, h("h3", {}, `${list.length} day${list.length > 1 ? "s" : ""} ready`),
        h("div", { class: "sub" }, `${fmt.bytes(total)} of logs`)),
      h("button", {
        class: "btn sm ghost danger",
        onclick: async () => { await api("/api/days/clear", { json: {} }); refresh(true); },
      }, icon("trash"), "Remove all")),
    h("div", { class: "table-wrap" }, h("table", { class: "tbl" },
      h("thead", {}, h("tr", {}, h("th", {}, "Day"), h("th", {}, "Files"), h("th", { class: "right" }, "Size"), h("th", {}, ""))),
      h("tbody", {}, rows)))));
}

// ── Settings page ──────────────────────────────────────────────────────────
function renderConfigure() {
  const page = $("#page-configure");
  page.replaceChildren();
  const s = S.settings, def = S.meta.defaults;
  for (const k of ["targets", "pick_s", "switch_s", "no_door", "no_door_days"]) s[k] = s[k] || {};

  const unitInput = (attrs, unit) => h("span", { class: "input-unit" }, h("input", { class: "input", type: "number", ...attrs }), h("span", {}, unit));

  // door
  const doorNote = h("p", { class: "help" });
  const updateDoorNote = () => {
    const d = Number(s.door_s) || 0;
    doorNote.textContent = d
      ? `A switch logged at 5.0 s counts as ${(5 + d).toFixed(1)} s. The same ${d} s comes off the front of every pick, so each hour still adds up.`
      : "Switch times are used exactly as logged; the door's travel is not included.";
  };
  updateDoorNote();
  const door = h("div", { class: "card" },
    h("div", { class: "card-head" }, h("div", {}, h("h3", {}, "Shutter-door travel"),
      h("div", { class: "sub" }, "The door-open command is logged in the same millisecond as the robot's arrival, so the door's travel never appears in the log."))),
    h("div", { class: "card-pad stack" },
      h("div", { class: "field" }, h("label", { for: "doorIn" }, "Seconds added to each switch"),
        unitInput({ id: "doorIn", min: "0", max: String(def.door_s_max), step: "0.1", value: String(s.door_s),
          oninput: (e) => { s.door_s = Number(e.target.value); saveSettings(); updateDoorNote(); } }, "s")),
      doorNote,
      h("div", { class: "field" }, h("label", {}, "Doors by day"),
        h("div", { class: "help" }, "Switch a day off if the doors were not in use — for example after they were disabled. That day gets no door seconds at any station."),
        days().filter((d) => d.date).length
          ? h("div", { class: "daydoors" }, days().filter((d) => d.date).map((d) =>
              h("label", { class: "toggle-row daydoor" },
                h("span", { class: "switch" }, h("input", {
                  type: "checkbox", checked: s.no_door_days[d.date] ? null : true, "aria-label": `Doors in use on ${d.date}`,
                  onchange: (e) => { if (e.target.checked) delete s.no_door_days[d.date]; else s.no_door_days[d.date] = true; saveSettings(); },
                }), h("span", {})),
                h("span", {}, d.date))))
          : h("div", { class: "help" }, "Add logs on the Data page to list their dates here."))));

  // starvation
  const starveNote = h("p", { class: "help" });
  const updateStarveNote = () => {
    const x = Number(s.starved_s) || 0;
    starveNote.textContent = `A station whose usual handover takes 4.0 s counts as starved when the next robot arrives more than ${(4 + x).toFixed(1)} s after the release.`;
  };
  updateStarveNote();
  const starve = h("div", { class: "card" },
    h("div", { class: "card-head" }, h("div", {}, h("h3", {}, "Starvation"),
      h("div", { class: "sub" }, "When a station counts as starved for a robot. Set before the run — the report's starvation figures are measured with it."))),
    h("div", { class: "card-pad stack" },
      h("div", { class: "field" }, h("label", { for: "starveIn" }, "Seconds beyond the station's median handover"),
        unitInput({ id: "starveIn", min: "0", max: String(def.starved_s_max), step: "0.5", value: String(s.starved_s),
          oninput: (e) => { s.starved_s = Number(e.target.value); saveSettings(); updateStarveNote(); } }, "s")),
      starveNote));

  // targets
  const st = S.state.stations || { zones: [], scanning: false };
  const ph = (kind, name, zone) => {
    if (zone && s[kind][zone] !== undefined && s[kind][zone] !== "") return String(s[kind][zone]);
    return kind === "targets" ? (zone ? "zone" : "auto") : kind === "pick_s" ? "auto" : "measured";
  };
  const cell = (kind, name, zone) => h("td", { class: "right" }, h("input", {
    class: "input sm num", type: "number", min: "0", step: kind === "targets" ? "1" : "0.1",
    value: s[kind][name] ?? "", placeholder: ph(kind, name, zone), "aria-label": `${name} ${kind}`,
    "data-kind": kind, "data-key": name,
    oninput: (e) => {
      const v = e.target.value.trim();
      if (v === "") delete s[kind][name]; else s[kind][name] = Number(v);
      saveSettings();
      if (!zone) page.querySelectorAll(`input[data-kind="${kind}"]`).forEach((o) => {
        const z = st.zones.find((zz) => zz.stations.includes(o.dataset.key));
        if (z && z.zone === name) o.placeholder = ph(kind, o.dataset.key, name);
      });
    },
  }));
  // Door switches: a zone's switch sets all of its stations; a station's overrides its zone.
  const hasDoor = (station, zone) => (s.no_door[station] === true ? false : s.no_door[station] === false ? true : s.no_door[zone] !== true);
  const doorBoxes = [];
  const syncDoors = () => doorBoxes.forEach(({ cb, name, zone, stations }) => {
    if (stations) {
      const on = stations.filter((x) => hasDoor(x, name)).length;
      cb.checked = on === stations.length; cb.indeterminate = on > 0 && on < stations.length;
    } else cb.checked = hasDoor(name, zone);
  });
  const doorCell = (name, zone, stations) => {
    const cb = h("input", {
      type: "checkbox", "aria-label": `${name} has a door`,
      onchange: () => {
        if (stations) {
          stations.forEach((x) => delete s.no_door[x]);
          if (cb.checked) delete s.no_door[name]; else s.no_door[name] = true;
        } else {
          const zoneOff = s.no_door[zone] === true;
          if (cb.checked) { if (zoneOff) s.no_door[name] = false; else delete s.no_door[name]; }
          else { if (zoneOff) delete s.no_door[name]; else s.no_door[name] = true; }
        }
        saveSettings(); syncDoors();
      },
    });
    doorBoxes.push({ cb, name, zone, stations });
    return h("td", { class: "center" }, h("label", { class: "switch" }, cb, h("span", {})));
  };
  const rows = [];
  for (const z of st.zones) {
    rows.push(h("tr", { class: "zone-row" },
      h("td", {}, h("b", {}, z.zone), " ", h("span", { class: "muted" }, z.stations.join(", "))),
      doorCell(z.zone, null, z.stations), cell("targets", z.zone, null), cell("pick_s", z.zone, null), cell("switch_s", z.zone, null)));
    for (const station of z.stations)
      rows.push(h("tr", {}, h("td", { class: "indent" }, station),
        doorCell(station, z.zone, null), cell("targets", station, z.zone), cell("pick_s", station, z.zone), cell("switch_s", station, z.zone)));
  }
  queueMicrotask(syncDoors);
  const targets = h("div", { class: "card" },
    h("div", { class: "card-head" }, h("div", {}, h("h3", {}, "Station targets"),
      h("div", { class: "sub" }, "Starting values for the report — they can all be changed inside it."))),
    h("div", { class: "card-pad stack" },
      h("div", { class: "field" }, h("label", { for: "rateIn" }, "Default totes per hour for high-rate zones"),
        unitInput({ id: "rateIn", min: "1", step: "1", value: String(s.target_rate),
          oninput: (e) => { s.target_rate = Number(e.target.value); saveSettings(); } }, "/h")),
      st.zones.length
        ? h("div", { class: "table-wrap" }, h("table", { class: "tbl settings-tbl" },
            h("thead", {}, h("tr", {}, h("th", {}, "Zone / station"), h("th", { class: "center" }, "Door"), h("th", { class: "right" }, "Totes / h"), h("th", { class: "right" }, "Pick s"), h("th", { class: "right" }, "Switch s"))),
            h("tbody", {}, rows)))
        : h("div", { class: "callout info" }, icon("info"), h("div", {},
            st.scanning ? "Finding the stations in your logs…" : "Add logs on the Data page and their stations and zones will appear here.")),
      h("ul", { class: "help bullets" },
        h("li", {}, h("b", {}, "Door"), " — on for every station unless switched off; stations without a door get no door seconds added to their switch or taken off their picks. A zone's switch sets all of its stations."),
        h("li", {}, h("b", {}, "Totes / h"), " — a zone left blank gets the default above if it is a high-rate zone, and no target otherwise. 0 switches a target off."),
        h("li", {}, h("b", {}, "Pick s"), " — blank (auto): whatever the budget leaves after the switch, so no time is allowed for waiting."),
        h("li", {}, h("b", {}, "Switch s"), " — blank (measured): the station's measured median switch, door included."),
        h("li", {}, "A station's own value overrides its zone's."))));

  // output
  const output = h("div", { class: "card" },
    h("div", { class: "card-head" }, h("div", {}, h("h3", {}, "Output folder"),
      h("div", { class: "sub mono" }, S.state.output_root)),
      h("button", { class: "btn sm", onclick: () => openTarget({ target: "output_root" }) }, icon("folder"), "Open")));

  page.append(h("div", { class: "stack" }, door, starve, targets, output,
    h("div", { class: "row" }, h("button", {
      class: "btn ghost", onclick: () => { S.settings = defaultSettings(S.meta); saveSettings(); renderConfigure(); },
    }, "Reset all settings to defaults"))));
}

// ── Run page ───────────────────────────────────────────────────────────────
function logLine(l) {
  return h("div", { class: "l" }, h("span", { class: "ts" }, `${l.t.toFixed(1)}s`),
    h("span", { class: l.level }, l.msg));
}

function renderRun(force) {
  const page = $("#page-run");
  const job = S.state.job;
  if (force || !$("#run-head")) {
    page.replaceChildren(h("div", { class: "stack" },
      h("div", { class: "card run-hero", id: "run-head" }),
      h("div", { class: "card" },
        h("div", { class: "card-head" }, h("h3", {}, "Run log"), h("span", { class: "muted", id: "log-count" })),
        h("div", { class: "console", id: "console" }))));
    const box = $("#console");
    S.logs.forEach((l) => box.append(logLine(l)));
    box.scrollTop = box.scrollHeight;
  }
  const titles = { idle: "Ready to run", running: "Running", done: "Finished", error: "Run failed", cancelled: "Cancelled" };
  const pct = job.status === "done" ? 100 : Math.round(job.progress);
  const nWarn = S.logs.filter((l) => l.level === "warning").length, nErr = S.logs.filter((l) => l.level === "error").length;
  $("#run-head").replaceChildren(
    h("div", {},
      h("h2", {}, titles[job.status] || job.status),
      h("div", { class: "muted" },
        job.status === "running" ? job.step
          : job.status === "error" ? job.error
          : job.status === "done" ? "The report is ready."
          : job.status === "cancelled" ? "Stopped before completion."
          : `${days().length} day${days().length === 1 ? "" : "s"} queued · door ${S.settings.door_s} s · default ${S.settings.target_rate}/h · starved over ${S.settings.starved_s} s`),
      h("div", { class: "progress " + (job.status === "done" ? "done" : job.status === "error" ? "err" : "") },
        h("div", { style: { width: `${pct}%` } })),
      h("div", { class: "run-meta" },
        h("span", {}, "Progress ", h("b", {}, `${pct}%`)),
        h("span", {}, "Elapsed ", h("b", {}, fmt.secs(job.elapsed))),
        h("span", {}, "Warnings ", h("b", {}, String(nWarn))),
        h("span", {}, "Errors ", h("b", {}, String(nErr))))),
    h("div", { class: "row" },
      job.status === "running"
        ? h("button", { class: "btn", onclick: async () => { await api("/api/run/cancel", { method: "POST" }); toast("Cancelling after the current day…"); } }, icon("stop"), "Cancel")
        : job.status === "done"
          ? [h("button", { class: "btn", onclick: () => openTarget({ target: "run_dir" }) }, icon("folder"), "Open folder"),
             h("button", { class: "btn primary lg", onclick: () => go("results") }, icon("chart"), "View results")]
          : h("button", { class: "btn primary lg", disabled: !canRun() || null, onclick: startRun }, icon("play"), "Run")));
  $("#log-count").textContent = `${S.logs.length} line${S.logs.length === 1 ? "" : "s"}`;
}

// ── Results page ───────────────────────────────────────────────────────────
function resultsKey() { return S.results ? S.results.run_dir : "none"; }

function stat(label, value) {
  return h("div", {}, h("div", { class: "k" }, label), h("div", { class: "v num" }, value));
}

async function renderResults() {
  const page = $("#page-results");
  if (!S.state.has_results) {
    page.replaceChildren(h("div", { class: "empty" }, icon("chart", "xl"),
      h("h3", {}, "No results yet"),
      h("div", {}, "Run an analysis to see its report here.")));
    return;
  }
  if (!S.results) {
    try { S.results = await api("/api/results"); } catch (e) { return; }
  }
  const r = S.results;
  S.renderedResults = resultsKey();
  page.replaceChildren();

  const ok = r.days.filter((d) => d.ok), bad = r.days.filter((d) => !d.ok);
  const wrap = h("div", { class: "stack" });
  wrap.append(h("div", { class: "card run-hero" },
    h("div", {},
      h("h2", {}, `${ok.length} day${ok.length === 1 ? "" : "s"} in one report`),
      h("div", { class: "muted" }, `Analysed in ${fmt.secs(r.seconds)}. ` +
        (ok.length > 1 ? "A summary page plus every day, switched from the panel on the left of the report. " : "") +
        "Door time and station targets can be changed inside the report and everything recalculates.")),
    h("div", { class: "row" },
      h("button", { class: "btn", onclick: () => openTarget({ target: "run_dir" }) }, icon("folder"), "Folder"),
      h("button", { class: "btn primary lg", onclick: () => window.open(r.report_url, "_blank") }, icon("external"), "Open report"))));

  for (const w of r.warnings || []) wrap.append(h("div", { class: "callout warn" }, icon("alert"), h("div", {}, w)));
  for (const d of bad) wrap.append(h("div", { class: "callout err" }, icon("alert"), h("div", {}, h("b", {}, d.label), " — ", d.error)));

  const grid = h("div", { class: "days" });
  for (const d of ok) {
    const k = d.headline || {};
    grid.append(h("div", { class: "card day" },
      h("div", { class: "day-top" },
        h("div", { class: "day-icon" }, icon("calendar")),
        h("div", { class: "day-title" }, h("div", { class: "day-name" }, d.date || d.label),
          h("div", { class: "day-files" }, `${d.label} · ${k.stations ?? "—"} stations`))),
      h("div", { class: "day-stats" },
        stat("Totes presented", fmt.int(k.visits)),
        stat("Median pick", k.op_med == null ? "—" : `${k.op_med} s`),
        stat("Multi-station", k.multi_pct == null ? "—" : `${k.multi_pct}%`)),
      h("div", { class: "day-foot" },
        h("button", { class: "btn sm", onclick: () => window.open(`${r.report_url}#day/${d.date}`, "_blank") }, icon("external"), "Open this day"))));
  }
  wrap.append(grid);
  page.append(wrap);
}

// ── History page ───────────────────────────────────────────────────────────
async function renderHistory() {
  const page = $("#page-history");
  page.replaceChildren(h("div", { class: "empty" }, h("div", { class: "spinner", style: { margin: "0 auto" } })));
  let data;
  try { data = await api("/api/runs"); }
  catch (e) { page.replaceChildren(h("div", { class: "callout err" }, icon("alert"), h("div", {}, e.message))); return; }
  page.replaceChildren();
  const head = h("div", { class: "card-head" },
    h("div", {}, h("h3", {}, "Previous runs"), h("div", { class: "sub mono" }, data.root)),
    h("button", { class: "btn sm", onclick: () => openTarget({ target: "output_root" }) }, icon("folder"), "Open folder"));
  if (!data.runs.length) {
    page.append(h("div", { class: "card" }, head, h("div", { class: "empty" }, icon("clock", "xl"),
      h("h3", {}, "No previous runs"), h("div", {}, "Reports you run appear here."))));
    return;
  }
  const rows = data.runs.map((run) => h("tr", {},
    h("td", {}, h("b", {}, run.name)),
    h("td", { class: "muted" }, new Date(run.modified * 1000).toLocaleString()),
    h("td", { class: "right" }, h("div", { class: "row end" },
      h("button", { class: "btn sm", onclick: () => window.open(run.url, "_blank") }, icon("external"), "Open"),
      h("button", { class: "btn sm ghost", onclick: () => openTarget({ target: "run", name: run.name }) }, icon("folder"), "Folder")))));
  page.append(h("div", { class: "card" }, head, h("div", { class: "table-wrap" }, h("table", { class: "tbl" },
    h("thead", {}, h("tr", {}, h("th", {}, "Run"), h("th", {}, "Written"), h("th", {}, ""))),
    h("tbody", {}, rows)))));
}

// ── actions ────────────────────────────────────────────────────────────────
async function openTarget(payload) {
  try { await api("/api/open", { json: payload }); }
  catch (e) { toast(e.message, "error"); }
}

async function startRun() {
  if (!canRun()) return;
  try {
    S.results = null; S.renderedResults = null; S.logs = []; S.logCursor = 0;
    await api("/api/run", { json: { door_s: S.settings.door_s, target_rate: S.settings.target_rate, starved_s: S.settings.starved_s,
      targets: S.settings.targets || {}, pick_s: S.settings.pick_s || {}, switch_s: S.settings.switch_s || {},
      no_door: S.settings.no_door || {},
      no_door_days: Object.keys(S.settings.no_door_days || {}).filter((d) => S.settings.no_door_days[d]) } });
    go("run");
    refresh(true);
  } catch (e) { toast(e.message, "error"); }
}

function reportAdded(r) {
  if (r.added) toast(`Added ${r.added} file${r.added > 1 ? "s" : ""}.`, "success");
  for (const msg of r.rejected || []) toast(msg, "error");
  if (!r.added && !(r.rejected || []).length) toast("Nothing new to add.", "info");
}

async function pickFiles() {
  if (S.meta.native) {
    try {
      reportAdded(await api("/api/native-dialog"));
      return refresh(true);
    } catch (e) { /* fall through to the browser picker */ }
  }
  $("#file-input").click();
}

async function uploadFiles(files) {
  const fd = new FormData();
  for (const f of files) fd.append("files", f, f.name);
  try {
    reportAdded(await api("/api/upload", { body: fd }));
    refresh(true);
  } catch (e) { toast(e.message, "error"); }
}

// ── polling ────────────────────────────────────────────────────────────────
async function refresh(force = false) {
  try {
    const st = await api(`/api/state?log_from=${S.logCursor}`);
    if (st.job.log_total < S.logCursor) { S.logs = []; S.logCursor = 0; st.job.logs = []; }
    if (st.job.logs.length) {
      S.logs.push(...st.job.logs);
      S.logCursor = st.job.log_total;
      const box = $("#console");
      if (box) {
        const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
        st.job.logs.forEach((l) => box.append(logLine(l)));
        if (atBottom) box.scrollTop = box.scrollHeight;
      }
    }
    S.state = st;
    if (st.job.status !== S.lastJobStatus) {
      if (st.job.status === "done") { S.results = null; toast("Run finished.", "success"); }
      if (st.job.status === "error") toast(st.job.error || "The run failed.", "error");
      S.lastJobStatus = st.job.status;
      force = true;
    }
    render(force);
  } catch (e) { /* the window may be closing */ }
}

// ── boot ───────────────────────────────────────────────────────────────────
(async function boot() {
  hydrateIcons();
  S.meta = await api("/api/meta");
  $("#version").textContent = `v${S.meta.version}`;
  const saved = store.get("settings", null);
  S.settings = saved && saved.door_s !== undefined ? saved : defaultSettings(S.meta);
  if (S.settings.starved_s === undefined) S.settings.starved_s = S.meta.defaults.starved_s;   // saved before it existed

  $$(".nav-item").forEach((b) => b.addEventListener("click", () => go(b.dataset.page)));
  $("#run-btn").addEventListener("click", startRun);
  $("#file-input").addEventListener("change", (e) => {
    if (e.target.files.length) uploadFiles(e.target.files);
    e.target.value = "";
  });

  const themeBtn = $("#theme-btn");
  function applyTheme(t) {
    document.documentElement.dataset.theme = t;
    store.set("theme", t);
    $("#theme-label").textContent = t === "dark" ? "Light theme" : "Dark theme";
    themeBtn.replaceChildren(icon(t === "dark" ? "sun" : "moon"), $("#theme-label"));
  }
  applyTheme(store.get("theme", "light"));
  themeBtn.addEventListener("click", () =>
    applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"));

  const overlay = $("#drop-overlay");
  let depth = 0;
  document.addEventListener("dragenter", (e) => { e.preventDefault(); if (++depth === 1) overlay.classList.add("show"); });
  document.addEventListener("dragover", (e) => e.preventDefault());
  document.addEventListener("dragleave", () => { if (--depth <= 0) { depth = 0; overlay.classList.remove("show"); } });
  document.addEventListener("drop", (e) => {
    e.preventDefault(); depth = 0; overlay.classList.remove("show");
    const files = Array.from(e.dataTransfer.files || []);
    const paths = files.map((f) => f.path).filter(Boolean);
    if (files.length && paths.length === files.length) {
      api("/api/paths", { json: { paths } })
        .then((r) => { reportAdded(r); refresh(true); })
        .catch((err) => toast(err.message, "error"));
    } else if (files.length) {
      uploadFiles(files);
    }
  });

  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); startRun(); }
  });

  go("data");
  await refresh(true);
  setInterval(() => { if (document.visibilityState === "visible") refresh(); }, 700);
})();
