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
  table: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M3 15h18M9 3v18"/>',
  download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5"/><path d="M12 15V3"/>',
  expand: '<path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/>',
  folder: '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.7-.9L9.6 3.9A2 2 0 0 0 7.9 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>',
  external: '<path d="M15 3h6v6M10 14 21 3M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
  stop: '<rect x="6" y="6" width="12" height="12" rx="2"/>',
  calendar: '<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  image: '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3-3a2 2 0 0 0-3 0L6 21"/>',
  file: '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v5h5"/>',
};

function icon(name, cls = "") {
  const span = h("span", { class: "ico " + cls });
  span.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${ICONS[name] || ""}</svg>`;
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
  meta: null,
  state: null,
  page: "data",
  logs: [],
  logCursor: 0,
  results: null,
  renderedResults: null,
  resultsTab: null,
  settings: null,
  sig: {},
  lastJobStatus: "idle",
};

function defaultSettings(meta) {
  return {
    enabled: meta.analyses.filter((a) => a.default).map((a) => a.key),
    switch_mode: "fixed",
    switch_s_fixed: meta.switch_s_default,
    pick_start_event: "",
    excel_exports: true,
    station_types: {},
    design_rates: {},
  };
}
function saveSettings() { store.set("settings", S.settings); }

// ── navigation ─────────────────────────────────────────────────────────────
const PAGES = {
  data: ["Data", "Load one Excel export (or a set of .log files) per operational day."],
  configure: ["Configure", "Choose analyses and tune the station and timing model."],
  run: ["Run", "Progress and log of the current analysis run."],
  results: ["Results", "Interactive charts — the same content as the HTML report."],
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
  if (page === "results") resizeVisiblePlots();
  $(".main").scrollTop = 0;
}

// ── rendering ──────────────────────────────────────────────────────────────
function readyDays() { return (S.state?.days || []).filter((d) => d.status === "ready"); }
function jobRunning() { return S.state?.job.status === "running"; }

function render(force = false) {
  if (!S.state) return;
  renderChrome();
  const sigs = {
    data: JSON.stringify(S.state.days),
    configure: JSON.stringify([S.state.stations, S.state.availability, S.state.pick_source, readyDays().length]),
  };
  if (S.page === "data" && (force || sigs.data !== S.sig.data)) {
    if (!$("#page-data").contains(document.activeElement) || force) renderData();
    S.sig.data = sigs.data;
  }
  if (S.page === "configure" && (force || sigs.configure !== S.sig.configure)) {
    renderConfigure();
    S.sig.configure = sigs.configure;
  }
  if (S.page === "run") renderRun(force);
  if (S.page === "results" && S.renderedResults !== resultsKey()) renderResults();
}

function canRun() {
  const st = S.state;
  return st && !jobRunning() && st.loading === 0 && readyDays().length > 0 && S.settings.enabled.length > 0;
}

function renderChrome() {
  const st = S.state;
  const nReady = readyDays().length;
  $("#badge-data").textContent = st.days.length ? String(st.days.length) : "";
  const runBadge = $("#badge-run");
  runBadge.textContent = jobRunning() ? `${Math.round(st.job.progress)}%` : "";
  runBadge.classList.toggle("live", jobRunning());

  const pill = $("#status-pill");
  let text = "Ready", cls = "ok";
  if (jobRunning()) { text = st.job.step || "Running…"; cls = "busy"; }
  else if (st.loading) { text = `Loading ${st.loading} file${st.loading > 1 ? "s" : ""}…`; cls = "busy"; }
  else if (!st.days.length) { text = "No data loaded"; cls = ""; }
  else if (!nReady) { text = "No valid files"; cls = "err"; }
  else { text = `${nReady} day${nReady > 1 ? "s" : ""} ready`; }
  pill.className = "status-pill " + cls;
  $("#status-text").textContent = text;

  const rb = $("#run-btn");
  rb.disabled = !canRun();
  rb.title = jobRunning() ? "A run is in progress" :
    st.loading ? "Wait for files to finish loading" :
    !nReady ? "Load at least one file" :
    !S.settings.enabled.length ? "Select at least one analysis" : "Run analysis (Ctrl+Enter)";
}

// ── Data page ──────────────────────────────────────────────────────────────
function renderData() {
  const page = $("#page-data");
  page.replaceChildren();
  const days = S.state.days;

  if (!days.length) {
    page.append(
      h("div", { class: "dropzone", id: "dz", onclick: browse },
        icon("upload", "xl"),
        h("h3", {}, "Drop ESS exports here, or click to browse"),
        h("div", {}, "Excel workbooks (.xlsx / .xlsm) — one per day — or Hairobotics .log files. ",
          "Split logs of the same day are merged automatically."),
        h("div", { class: "help" }, "Tip: drop an asrs_config.json together with the files to apply station types and design rates.")),
      h("div", { class: "steps" },
        step(1, "Load data", "Sheets are detected by their columns; each file is validated and its stations mapped automatically."),
        step(2, "Configure", "Pick analyses, zone names, design rates and the switch-time model."),
        step(3, "Explore", "Interactive results here, plus a shareable HTML report and Excel workbooks.")));
    return;
  }

  page.append(
    h("div", { class: "dropzone compact", onclick: browse },
      icon("plus", "xl"),
      h("div", {}, h("h3", {}, "Add more days"), h("div", { class: "help" }, "Drop files anywhere in the window, or click to browse."))),
    h("div", { style: { height: "18px" } }),
    h("div", { class: "days" }, days.map(dayCard)));
}

function step(n, title, text) {
  return h("div", { class: "card step" }, h("span", { class: "n" }, n), h("b", {}, title), h("span", { class: "muted" }, text));
}

const SHEETS = [["callback", "Callback"], ["station", "Station"], ["lifecycle", "Lifecycle"], ["efficiency", "HPS3"]];

function dayCard(d) {
  const msgs = d.messages || [];
  const warnings = msgs.filter((m) => m.level === "warning").length;
  const errors = msgs.filter((m) => m.level === "error").length;
  const label = h("input", {
    class: "day-label", value: d.label, title: "Rename this day (used as the report tab name)",
    onchange: async (e) => {
      try { await api(`/api/days/${d.id}`, { method: "PATCH", json: { label: e.target.value } }); poll(); }
      catch (err) { toast(err.message, "error"); }
    },
    onkeydown: (e) => { if (e.key === "Enter") e.target.blur(); },
  });
  const card = h("div", { class: `card day ${d.status}` },
    d.status === "loading" ? h("div", { class: "loading-bar" }) : null,
    h("div", { class: "day-top" },
      h("div", { class: "day-icon" }, icon(d.status === "error" ? "alert" : "calendar", "lg")),
      h("div", { class: "day-title" }, label,
        h("div", { class: "day-files", title: d.files.join("\n") }, d.files.join(", "))),
      h("button", {
        class: "icon-btn", title: "Remove", disabled: jobRunning() || null,
        onclick: async () => { await api(`/api/days/${d.id}`, { method: "DELETE" }); poll(); },
      }, icon("trash"))));

  if (d.status === "loading") {
    card.append(h("div", { class: "chips" }, h("span", { class: "chip info" }, "Reading and validating…")));
  } else if (d.status === "ready") {
    card.append(h("div", { class: "chips" },
      SHEETS.map(([k, name]) => {
        const n = d.sheets[k];
        return h("span", { class: "chip " + (n == null ? "off" : "ok"), title: n == null ? `${name} sheet not found` : `${fmt.int(n)} rows` },
          icon(n == null ? "x" : "check"), name, n != null ? h("span", { class: "num", style: { opacity: .75 } }, fmt.int(n)) : null);
      }),
      d.config_file ? h("span", { class: "chip info" }, "asrs_config.json") : null,
      !d.pick_source.arrived && d.pick_source.ppready ? h("span", { class: "chip warn", title: "No 'arrived' events — pick time will use ppReady → triggerGo" }, "ppReady fallback") : null));
    card.append(h("div", { class: "day-stats" },
      h("div", {}, h("div", { class: "k" }, "Date"), h("div", { class: "v" }, d.date || "—")),
      h("div", {}, h("div", { class: "k" }, "Stations"), h("div", { class: "v" }, `${d.stations}`, h("span", { class: "muted", style: { fontWeight: 500, fontSize: "12px" } }, ` · ${d.zones} zone${d.zones === 1 ? "" : "s"}`))),
      h("div", {}, h("div", { class: "k" }, "AMR"), h("div", { class: "v" }, d.amr_type || "—"))));
  } else {
    card.append(h("div", { class: "chips" }, h("span", { class: "chip err" }, "Could not load")));
  }

  if (msgs.length) {
    const summary = errors ? `${errors} error${errors > 1 ? "s" : ""}` :
      `${warnings} note${warnings === 1 ? "" : "s"}${d.limited ? ` · ${d.limited} analyses limited` : ""}`;
    card.append(h("details", { class: "day-msgs", open: d.status === "error" || null },
      h("summary", {}, icon(errors ? "alert" : "info"), summary),
      h("ul", {}, msgs.map((m) => h("li", { class: m.level }, m.text)))));
  }
  return card;
}

// ── file input / drag & drop ───────────────────────────────────────────────
async function browse() {
  if (S.meta?.native) {
    try { const r = await api("/api/dialog/open", { method: "POST" }); afterAdd(r); }
    catch (e) { toast(e.message, "error"); }
    return;
  }
  $("#file-input").click();
}

async function uploadFiles(fileList) {
  const files = Array.from(fileList || []);
  if (!files.length) return;
  if (jobRunning()) { toast("Wait for the current run to finish before adding files.", "error"); return; }
  const fd = new FormData();
  files.forEach((f) => fd.append("files", f, f.name));
  const mb = files.reduce((a, f) => a + f.size, 0) / 1e6;
  toast(`Uploading ${files.length} file${files.length > 1 ? "s" : ""} (${mb.toFixed(1)} MB)…`, "info", 2500);
  try { afterAdd(await api("/api/files", { body: fd })); }
  catch (e) { toast(e.message, "error"); }
}

function afterAdd(r) {
  (r.rejected || []).forEach((m) => toast(m, "error", 7000));
  if (r.added?.length) { if (S.page !== "data") go("data"); poll(); }
}

function setupDragDrop() {
  const overlay = $("#drop-overlay");
  let depth = 0;
  const hasFiles = (e) => Array.from(e.dataTransfer?.types || []).includes("Files");
  window.addEventListener("dragenter", (e) => { if (!hasFiles(e)) return; e.preventDefault(); depth++; overlay.classList.add("show"); });
  window.addEventListener("dragleave", (e) => { if (!hasFiles(e)) return; depth = Math.max(0, depth - 1); if (!depth) overlay.classList.remove("show"); });
  window.addEventListener("dragover", (e) => { if (hasFiles(e)) e.preventDefault(); });
  window.addEventListener("drop", (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault(); depth = 0; overlay.classList.remove("show");
    uploadFiles(e.dataTransfer.files);
  });
  $("#file-input").addEventListener("change", (e) => { uploadFiles(e.target.files); e.target.value = ""; });
}

// ── Configure page ─────────────────────────────────────────────────────────
function renderConfigure() {
  const page = $("#page-configure");
  page.replaceChildren();
  const st = S.state, set = S.settings, meta = S.meta;
  const avail = st.availability || {};
  const haveData = readyDays().length > 0;

  // Analyses
  const anGrid = h("div", { class: "analyses" });
  for (const a of meta.analyses) {
    const av = avail[a.key] || {};
    const status = haveData ? (av.status || "none") : "none";
    const on = set.enabled.includes(a.key);
    const badgeText = { ok: "Ready", degraded: "Limited", unavailable: "Unavailable", none: "Load data" }[status];
    const cb = h("input", { type: "checkbox", checked: on || null });
    const el = h("label", { class: `an ${on ? "on" : ""} ${status}`, title: av.reason || "" },
      h("span", { class: "switch" }, cb, h("span")),
      h("div", {},
        h("div", { class: "t" }, a.label),
        a.requires ? h("div", { class: "r" }, "Needs " + a.requires) : null,
        av.reason && status !== "ok" ? h("div", { class: "r" }, av.reason) : null,
        h("span", { class: `badge ${status}` }, badgeText)));
    cb.addEventListener("change", () => {
      set.enabled = cb.checked ? [...new Set([...set.enabled, a.key])] : set.enabled.filter((k) => k !== a.key);
      el.classList.toggle("on", cb.checked);
      saveSettings(); renderChrome(); countLabel.textContent = enabledCount();
    });
    anGrid.append(el);
  }
  const enabledCount = () => `${set.enabled.length} of ${meta.analyses.length} selected`;
  const countLabel = h("span", { class: "sub" }, enabledCount());
  const selectAll = (mode) => {
    set.enabled = mode === "none" ? [] :
      mode === "available" ? meta.analyses.filter((a) => (avail[a.key]?.status || "ok") !== "unavailable").map((a) => a.key) :
      meta.analyses.map((a) => a.key);
    saveSettings(); renderConfigure(); renderChrome();
  };
  page.append(h("div", { class: "card" },
    h("div", { class: "card-head" },
      h("div", {}, h("h2", {}, "Analyses"), countLabel),
      h("div", { class: "row" },
        h("button", { class: "btn sm ghost", onclick: () => selectAll("available") }, "All available"),
        h("button", { class: "btn sm ghost", onclick: () => selectAll("all") }, "All"),
        h("button", { class: "btn sm ghost", onclick: () => selectAll("none") }, "None"))),
    anGrid));

  page.append(h("div", { style: { height: "18px" } }));

  // Timing model + output
  const swInput = h("input", { class: "input num", type: "number", min: 0, max: meta.max_operational_switch_s, step: 0.5, value: set.switch_s_fixed });
  swInput.addEventListener("input", () => {
    const v = parseFloat(swInput.value);
    const ok = Number.isFinite(v) && v >= 0 && v <= meta.max_operational_switch_s;
    swInput.classList.toggle("invalid", !ok);
    if (ok) { set.switch_s_fixed = v; saveSettings(); }
  });
  const seg = (options, value, onPick) => {
    const wrap = h("div", { class: "segmented" });
    for (const [v, lbl] of options) {
      wrap.append(h("button", { class: v === value ? "on" : "", onclick: (e) => { onPick(v); $$("button", wrap).forEach((b) => b.classList.toggle("on", b === e.currentTarget)); } }, lbl));
    }
    return wrap;
  };
  const pick = st.pick_source || {};
  const pickNote = pick.any_missing_arrived && pick.ppready_possible
    ? h("div", { class: "callout warn" }, icon("alert"), h("div", {}, h("b", {}, "Some days have no 'arrived' events. "),
        "In Automatic mode those days measure pick time from ppReady → triggerGo — a slightly wider window than the standard pick. Treat absolute values as approximate."))
    : pick.any_missing_arrived
      ? h("div", { class: "callout err" }, icon("alert"), h("div", {}, "Some days have neither 'arrived' nor enough 'ppReady' events — pick-time charts will be empty for them."))
      : null;

  page.append(h("div", { class: "grid-2" },
    h("div", { class: "card" },
      h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Timing model"), h("div", { class: "sub" }, "Used by implied-throughput and utilisation metrics."))),
      h("div", { class: "card-pad stack" },
        h("div", { class: "field" },
          h("label", {}, "Robot switch time"),
          seg([["fixed", "Fixed value"], ["measured", "Measured per station"]], set.switch_mode, (v) => { set.switch_mode = v; saveSettings(); }),
          h("div", { class: "help" }, "Measured mode uses each station's median release → next-arrival gap (operational swaps ≤ ",
            `${meta.max_operational_switch_s / 60} min); stations without measurements fall back to the fixed value.`)),
        h("div", { class: "field" },
          h("label", {}, "Fixed switch time"),
          h("div", { class: "input-unit" }, swInput, h("span", {}, "s"))),
        h("div", { class: "field" },
          h("label", {}, "Pick-time start event"),
          seg([["", "Automatic"], ["arrived", "arrived"], ["ppReady", "ppReady"]], set.pick_start_event, (v) => { set.pick_start_event = v; saveSettings(); }),
          h("div", { class: "help" }, "Pick time runs from this event to the robot's triggerGo. Automatic uses 'arrived', falling back to 'ppReady' only for days that lack 'arrived' events.")),
        pickNote)),
    h("div", { class: "card" },
      h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Output"), h("div", { class: "sub" }, "Every run gets its own timestamped folder."))),
      h("div", { class: "card-pad stack" },
        h("div", { class: "field" }, h("label", {}, "Output folder"),
          h("div", { class: "row" }, h("code", { class: "mono", style: { wordBreak: "break-all" } }, meta.output_root),
            h("button", { class: "btn sm", onclick: () => openTarget({ target: "output_root" }) }, icon("folder"), "Open"))),
        h("label", { class: "toggle-row" },
          (() => { const s = h("span", { class: "switch" }, h("input", { type: "checkbox", checked: set.excel_exports || null, onchange: (e) => { set.excel_exports = e.target.checked; saveSettings(); } }), h("span")); return s; })(),
          h("div", {}, h("b", {}, "Excel exports"), h("div", { class: "help" }, "Per-day workbooks, per-chart data and the formula-driven all_data.xlsx. Turn off for faster runs."))),
        h("div", { class: "help" }, "Settings are remembered between sessions. An asrs_config.json next to a data file is applied automatically; values set here take precedence.")))));

  page.append(h("div", { style: { height: "18px" } }));
  page.append(stationsCard());
}

function stationsCard() {
  const st = S.state, set = S.settings;
  const rows = st.stations || [];
  const zones = [...new Set(rows.map((r) => set.station_types[r.station] || r.zone).filter(Boolean))];
  const palette = ["#2563eb", "#16a34a", "#d97706", "#7c3aed", "#0891b2", "#dc2626"];
  const zoneColor = (z) => palette[Math.max(0, zones.indexOf(z)) % palette.length];
  const datalist = h("datalist", { id: "zone-list" }, zones.map((z) => h("option", { value: z })));

  const body = h("tbody");
  for (const r of rows) {
    const zoneVal = set.station_types[r.station] ?? r.zone;
    const rateVal = set.design_rates[r.station] ?? r.design_rate ?? "";
    const dot = h("span", { class: "zone-dot", style: { background: zoneColor(zoneVal) } });
    const zone = h("input", { class: "input sm", list: "zone-list", value: zoneVal, placeholder: r.zone });
    zone.addEventListener("change", () => {
      const v = zone.value.trim();
      if (!v || v === r.zone) delete set.station_types[r.station]; else set.station_types[r.station] = v;
      saveSettings(); renderConfigure();
    });
    const rate = h("input", { class: "input sm num", type: "number", min: 0, step: 1, value: rateVal, placeholder: "—", style: { width: "110px" } });
    rate.addEventListener("input", () => {
      const v = rate.value.trim();
      const ok = v === "" || (Number.isFinite(+v) && +v >= 0);
      rate.classList.toggle("invalid", !ok);
      if (!ok) return;
      if (v === "") delete set.design_rates[r.station]; else set.design_rates[r.station] = +v;
      saveSettings();
    });
    body.append(h("tr", {},
      h("td", {}, h("b", {}, r.station)),
      h("td", {}, h("div", { class: "row", style: { flexWrap: "nowrap" } }, dot, zone)),
      h("td", {}, rate),
      h("td", { class: "num muted" }, r.measured_switch_s == null ? "—" : `${r.measured_switch_s} s`)));
  }
  const allRate = h("input", { class: "input sm num", type: "number", min: 0, step: 1, placeholder: "tasks/h", style: { width: "96px" }, title: "Design rate to apply to every station" });
  const applyRateAll = () => {
    const v = allRate.value.trim();
    const n = v === "" ? 0 : +v;
    if (!Number.isFinite(n) || n < 0) { toast("Enter a positive number.", "error"); return; }
    rows.forEach((r) => { if (n) set.design_rates[r.station] = n; else delete set.design_rates[r.station]; });
    saveSettings(); renderConfigure();
  };
  return h("div", { class: "card" },
    h("div", { class: "card-head" },
      h("div", {}, h("h2", {}, "Stations"),
        h("div", { class: "sub" }, rows.length ? `${rows.length} stations detected. Zones group stations in charts; design rates enable the target views.` : "Load data to see the detected stations.")),
      rows.length ? h("div", { class: "row" },
        allRate,
        h("button", { class: "btn sm", onclick: applyRateAll }, "Apply to all"),
        h("button", { class: "btn sm ghost", onclick: () => { set.station_types = {}; set.design_rates = {}; saveSettings(); renderConfigure(); } }, "Reset to detected")) : null),
    rows.length ? h("div", { class: "table-wrap" }, datalist,
      h("table", { class: "tbl" },
        h("thead", {}, h("tr", {}, h("th", {}, "Station"), h("th", {}, "Zone"), h("th", {}, "Design rate (tasks/h)"), h("th", {}, "Measured switch"))),
        body)) : h("div", { class: "empty", style: { padding: "36px" } }, "No stations yet."));
}

// ── Run page ───────────────────────────────────────────────────────────────
async function startRun() {
  if (!canRun()) return;
  const set = S.settings, pick = S.state.pick_source;
  if (set.enabled.includes("dwell") && set.pick_start_event === "" && pick.any_missing_arrived && pick.ppready_possible) {
    const ok = await confirmDialog("Use the ppReady fallback?",
      "Some loaded days have no 'arrived' events, so pick time for those days will be measured from 'ppReady' → 'triggerGo'. " +
      "This is an approximation (a slightly wider window than arrived → triggerGo); the report will flag it.",
      "Continue");
    if (!ok) return;
  }
  if (set.enabled.includes("dwell") && set.pick_start_event === "ppReady" && !pick.ppready_possible) {
    toast("The ppReady start event was chosen, but no loaded day has enough ppReady events — pick time will be empty.", "error", 7000);
  }
  try {
    await api("/api/run", { json: { ...set } });
    S.logs = []; S.logCursor = 0;
    go("run");
    poll();
  } catch (e) { toast(e.message, "error"); }
}

function renderRun(force) {
  const page = $("#page-run");
  const job = S.state.job;
  if (job.status === "idle") {
    if (force || !page.dataset.idle) {
      page.dataset.idle = "1";
      page.replaceChildren(h("div", { class: "card empty" }, icon("play", "xl"), h("h3", {}, "No run yet"),
        h("p", {}, "Load data, check the configuration, then press ", h("b", {}, "Run analysis"), "."),
        h("button", { class: "btn primary", disabled: !canRun() || null, onclick: startRun }, icon("play"), "Run analysis")));
    }
    return;
  }
  delete page.dataset.idle;
  if (!$("#run-hero", page) || page.dataset.job !== job.id) {
    page.dataset.job = job.id;
    page.replaceChildren(
      h("div", { class: "card", id: "run-hero" }),
      h("div", { style: { height: "18px" } }),
      h("div", { class: "card" },
        h("div", { class: "card-head" }, h("h3", {}, "Log"), h("span", { class: "sub", id: "log-count" })),
        h("div", { class: "console", id: "console" })));
    $("#console").replaceChildren(...S.logs.map(logLine));
  }
  const titles = { running: "Analysing…", done: "Analysis complete", error: "Run failed", cancelled: "Run cancelled" };
  const pct = Math.round(job.progress);
  const hero = $("#run-hero");
  hero.replaceChildren(h("div", { class: "run-hero" },
    h("div", {},
      h("h2", {}, titles[job.status] || job.status),
      h("div", { class: "muted" }, job.status === "running" ? job.step : job.status === "error" ? job.error : job.status === "done" ? "Charts, report and workbooks are ready." : "Stopped before completion."),
      h("div", { class: "progress " + (job.status === "done" ? "done" : job.status === "error" ? "err" : "") }, h("div", { style: { width: `${job.status === "done" ? 100 : pct}%` } })),
      h("div", { class: "run-meta" },
        h("span", {}, "Progress ", h("b", {}, `${job.status === "done" ? 100 : pct}%`)),
        h("span", {}, "Elapsed ", h("b", {}, fmt.secs(job.elapsed))),
        h("span", {}, "Warnings ", h("b", {}, String(S.logs.filter((l) => l.level === "warning").length))),
        h("span", {}, "Errors ", h("b", {}, String(S.logs.filter((l) => l.level === "error").length))))),
    h("div", { class: "row" },
      job.status === "running"
        ? h("button", { class: "btn", onclick: async () => { await api("/api/run/cancel", { method: "POST" }); toast("Cancelling after the current step…"); } }, icon("stop"), "Cancel")
        : job.status === "done"
          ? [h("button", { class: "btn", onclick: () => openTarget({ target: "run_dir" }) }, icon("folder"), "Open folder"),
             h("button", { class: "btn primary lg", onclick: () => go("results") }, icon("chart"), "View results")]
          : h("button", { class: "btn primary", disabled: !canRun() || null, onclick: startRun }, icon("play"), "Run again"))));
  $("#log-count").textContent = `${S.logs.length} lines`;
}

function logLine(l) {
  return h("div", { class: "l" }, h("span", { class: "ts" }, `${l.t.toFixed(1)}s`), h("span", { class: l.level }, l.msg));
}

// ── Results page ───────────────────────────────────────────────────────────
let plotObserver = null;

async function loadResults() {
  try {
    S.results = await api("/api/results");
    S.resultsTab = S.results.summary.length ? "summary" : "0";
    if (S.page === "results") renderResults();
  } catch (e) { S.results = null; }
}

function resultsKey() { return S.results ? `${S.results.run_dir}|${S.resultsTab}` : "none"; }

function renderResults() {
  S.renderedResults = resultsKey();
  const page = $("#page-results");
  page.replaceChildren();
  purgePlots(page);
  const r = S.results;
  if (!r) {
    page.append(h("div", { class: "card empty" }, icon("chart", "xl"), h("h3", {}, "No results yet"),
      h("p", {}, "Run an analysis to explore its charts here. Earlier reports are under ", h("a", { href: "#", onclick: (e) => { e.preventDefault(); go("history"); } }, "History"), ".")));
    return;
  }
  const tabs = h("div", { class: "tabs" });
  const tabList = [];
  if (r.summary.length) tabList.push(["summary", "Summary"]);
  r.days.forEach((d, i) => tabList.push([String(i), d.label]));
  for (const [key, label] of tabList) {
    tabs.append(h("button", { class: "tab " + (key === S.resultsTab ? "on" : ""), onclick: () => { S.resultsTab = key; renderResults(); } }, label));
  }
  const search = h("input", { class: "input", placeholder: "Filter charts…", type: "search" });
  page.append(h("div", { class: "res-bar" },
    tabList.length > 1 ? tabs : null,
    h("div", { class: "search" }, icon("search"), search),
    h("span", { class: "spacer" }),
    r.report_url ? h("button", { class: "btn", onclick: () => openFile(r.report_url) }, icon("external"), "HTML report") : null,
    r.combined_url ? h("button", { class: "btn", onclick: () => openFile(r.combined_url) }, icon("table"), "all_data.xlsx") : null,
    h("button", { class: "btn", onclick: () => openTarget({ target: "run_dir" }) }, icon("folder"), "Folder")));

  r.warnings.filter((w) => !/^.*: skipped \(no lifecycle/.test(w)).slice(0, 4).forEach((w) =>
    page.append(h("div", { class: "callout warn", style: { marginBottom: "12px" } }, icon("alert"), h("div", {}, w))));

  const isSummary = S.resultsTab === "summary";
  const day = isSummary ? null : r.days[+S.resultsTab];
  const charts = isSummary ? r.summary : day.charts;

  if (day) {
    if (day.kpis?.length) {
      page.append(h("div", { class: "kpis" }, day.kpis.map((k) =>
        h("div", { class: "card kpi" }, h("div", { class: "k" }, k.label), h("div", { class: "v" }, k.value), h("div", { class: "h" }, k.hint || "")))));
    }
    day.failures.forEach((f) => page.append(h("div", { class: "callout err", style: { marginBottom: "12px" } }, icon("alert"),
      h("div", {}, h("b", {}, `${f.label} failed. `), f.error))));
  }

  if (!charts.length) {
    page.append(h("div", { class: "card empty" }, h("h3", {}, "No charts for this day"), h("p", {}, "The selected analyses had no usable inputs in this file.")));
    return;
  }

  const toc = h("nav", { class: "card toc" });
  const list = h("div", { class: "charts" });
  charts.forEach((c) => {
    const anchor = `chart-${S.resultsTab}-${c.idx}`;
    const card = chartCard(c, anchor);
    list.append(card);
    toc.append(h("a", { href: "#" + anchor, "data-anchor": anchor, onclick: (e) => { e.preventDefault(); card.scrollIntoView({ behavior: "smooth", block: "start" }); } }, c.title));
  });
  page.append(h("div", { class: "res-layout" }, toc, list));

  search.addEventListener("input", () => {
    const q = search.value.trim().toLowerCase();
    $$(".chart-card", list).forEach((el) => { el.style.display = !q || el.dataset.title.includes(q) ? "" : "none"; });
    $$("a", toc).forEach((a) => { a.style.display = !q || a.textContent.toLowerCase().includes(q) ? "" : "none"; });
  });
  observePlots(list, toc);
}

function chartCard(c, anchor) {
  const day = S.resultsTab;
  const method = h("div", { class: "method" }, h("p", {}, c.method), h("div", { class: "src" }, h("b", {}, "Source: "), c.source));
  const plot = h("div", { class: "plot", "data-day": day, "data-idx": c.idx });
  const body = h("div", { class: "chart-body" }, plot, h("div", { class: "placeholder" }, h("div", { class: "spinner" })));
  return h("article", { class: "card chart-card", id: anchor, "data-title": c.title.toLowerCase() },
    h("div", { class: "card-head" },
      h("div", {}, h("h3", {}, c.title), h("div", { class: "sub" }, c.source)),
      h("div", { class: "chart-actions" },
        h("button", { class: "icon-btn", title: "How this is calculated", onclick: () => method.classList.toggle("open") }, icon("info")),
        c.rows ? h("button", { class: "icon-btn", title: `View data (${fmt.int(c.rows)} rows)`, onclick: () => showRows(c) }, icon("table")) : null,
        h("button", { class: "icon-btn", title: "Download PNG", onclick: () => downloadPng(plot, c) }, icon("image")),
        h("button", { class: "icon-btn", title: "Expand", onclick: () => expandChart(c) }, icon("expand")))),
    method, body);
}

const figureCache = new Map();
async function fetchFigure(day, idx) {
  const key = `${S.results.run_dir}|${day}|${idx}`;
  if (!figureCache.has(key)) figureCache.set(key, api(`/api/results/${day}/${idx}/figure`));
  return figureCache.get(key);
}

const PLOT_CONFIG = { displaylogo: false, responsive: true, modeBarButtonsToRemove: ["lasso2d", "select2d"], toImageButtonOptions: { format: "png", scale: 2 } };

async function drawPlot(el) {
  if (el.dataset.drawn) return;
  el.dataset.drawn = "1";
  try {
    const fig = await fetchFigure(el.dataset.day, el.dataset.idx);
    const layout = { ...fig.layout, autosize: true };
    if (layout.height) el.style.height = layout.height + "px";
    delete layout.width;
    await Plotly.newPlot(el, fig.data, layout, PLOT_CONFIG);
    el.parentElement.querySelector(".placeholder")?.remove();
  } catch (e) {
    const ph = el.parentElement.querySelector(".placeholder");
    if (ph) ph.textContent = "Chart could not be drawn: " + e.message;
  }
}

function observePlots(list, toc) {
  if (plotObserver) plotObserver.disconnect();
  plotObserver = new IntersectionObserver((entries) => {
    for (const en of entries) {
      if (en.isIntersecting) { drawPlot(en.target); }
    }
  }, { root: $(".main"), rootMargin: "600px 0px" });
  $$(".plot", list).forEach((p) => plotObserver.observe(p));

  // Highlight the chart in view in the table of contents.
  const tocObs = new IntersectionObserver((entries) => {
    for (const en of entries) {
      if (en.isIntersecting) {
        $$("a", toc).forEach((a) => a.classList.toggle("on", a.dataset.anchor === en.target.id));
      }
    }
  }, { root: $(".main"), rootMargin: "-40% 0px -55% 0px" });
  $$(".chart-card", list).forEach((c) => tocObs.observe(c));
}

function purgePlots(root) {
  $$(".plot", root).forEach((p) => { try { Plotly.purge(p); } catch (e) { /* ignore */ } });
}
function resizeVisiblePlots() {
  setTimeout(() => $$("#page-results .plot[data-drawn]").forEach((p) => { try { Plotly.Plots.resize(p); } catch (e) { /* ignore */ } }), 30);
}

function downloadPng(el, c) {
  if (!el.dataset.drawn || !el.data) { toast("The chart is still loading."); return; }
  Plotly.downloadImage(el, { format: "png", scale: 2, width: el.clientWidth, height: el.clientHeight, filename: c.id });
}

async function expandChart(c) {
  const day = S.resultsTab;
  const body = openModal(c.title);
  const el = h("div", { class: "plot" });
  body.append(el);
  try {
    const fig = await fetchFigure(day, c.idx);
    const layout = { ...fig.layout, autosize: true };
    delete layout.height; delete layout.width;
    await Plotly.newPlot(el, fig.data, layout, PLOT_CONFIG);
  } catch (e) { el.textContent = e.message; }
}

async function showRows(c) {
  const day = S.resultsTab;
  const body = openModal(`${c.title} — data`);
  body.classList.add("themed");
  body.append(h("div", { class: "empty" }, h("div", { class: "spinner", style: { margin: "0 auto" } })));
  try {
    const res = await api(`/api/results/${day}/${c.idx}/rows`);
    const rows = res.rows || [];
    const cols = [...new Set(rows.flatMap((r) => Object.keys(r)))];
    const shown = rows.slice(0, 1000);
    body.replaceChildren(
      h("div", { class: "row", style: { padding: "12px 18px", borderBottom: "1px solid var(--border)" } },
        h("span", {}, `${fmt.int(rows.length)} rows${rows.length > shown.length ? ` · showing the first ${shown.length}` : ""}`),
        h("span", { class: "spacer" }),
        h("button", { class: "btn sm", onclick: () => downloadCsv(rows, cols, c.id) }, icon("download"), "Download CSV")),
      h("div", { class: "table-wrap" }, h("table", { class: "tbl" },
        h("thead", {}, h("tr", {}, cols.map((k) => h("th", {}, k)))),
        h("tbody", {}, shown.map((r) => h("tr", {}, cols.map((k) => h("td", { class: typeof r[k] === "number" ? "num" : "" }, r[k] == null ? "" : String(r[k])))))))));
  } catch (e) { body.replaceChildren(h("div", { class: "empty" }, e.message)); }
}

function downloadCsv(rows, cols, name) {
  const esc = (v) => { if (v == null) return ""; const s = String(v); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
  const csv = "﻿" + [cols.join(","), ...rows.map((r) => cols.map((k) => esc(r[k])).join(","))].join("\r\n");
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  const a = h("a", { href: url, download: `${name}.csv` });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}

// ── modal / confirm ────────────────────────────────────────────────────────
function openModal(title) {
  const m = $("#chart-modal");
  $("#modal-title").textContent = title;
  const body = $("#modal-body");
  purgePlots(body);
  body.replaceChildren();
  body.classList.remove("themed");
  m.hidden = false;
  return body;
}
function closeModal() {
  const m = $("#chart-modal");
  if (m.hidden) return;
  purgePlots($("#modal-body"));
  m.hidden = true;
  if (m._resolve) { const r = m._resolve; m._resolve = null; r(false); }
}

function confirmDialog(title, text, okLabel) {
  return new Promise((resolve) => {
    const m = $("#chart-modal");
    const card = $(".modal-card", m);
    const body = openModal(title);
    card.classList.add("small");
    // closeModal() (Esc, backdrop, ×) resolves false through m._resolve.
    m._resolve = (v) => { card.classList.remove("small"); resolve(v); };
    const finish = (v) => { const r = m._resolve; m._resolve = null; closeModal(); r(v); };
    body.append(h("div", { class: "card-pad stack" },
      h("p", { style: { margin: 0, color: "var(--text-2)" } }, text),
      h("div", { class: "row" },
        h("button", { class: "btn primary", onclick: () => finish(true) }, okLabel),
        h("button", { class: "btn", onclick: () => finish(false) }, "Cancel"))));
  });
}

// ── History page ───────────────────────────────────────────────────────────
async function renderHistory() {
  const page = $("#page-history");
  page.replaceChildren(h("div", { class: "card empty" }, h("div", { class: "spinner", style: { margin: "0 auto" } })));
  try {
    const res = await api("/api/runs");
    if (!res.runs.length) {
      page.replaceChildren(h("div", { class: "card empty" }, icon("clock", "xl"), h("h3", {}, "No saved runs"), h("p", {}, "Reports appear here after your first run."),
        h("code", { class: "mono" }, res.root)));
      return;
    }
    page.replaceChildren(h("div", { class: "card" },
      h("div", { class: "card-head" }, h("div", {}, h("h2", {}, `${res.runs.length} saved run${res.runs.length > 1 ? "s" : ""}`), h("div", { class: "sub mono" }, res.root)),
        h("button", { class: "btn sm", onclick: () => openTarget({ target: "output_root" }) }, icon("folder"), "Open folder")),
      res.runs.map((run) => h("div", { class: "hist-item" },
        h("div", { class: "day-icon" }, icon("chart", "lg")),
        h("div", { style: { flex: 1, minWidth: 0 } },
          h("b", {}, prettyRunName(run.name)),
          h("div", { class: "muted", style: { fontSize: "12.5px" } }, run.days.length ? run.days.join(" · ") : "—")),
        h("button", { class: "btn sm", onclick: () => openFile(run.report_url) }, icon("external"), "Report"),
        h("button", { class: "btn sm ghost", onclick: () => openTarget({ target: "run", name: run.name }) }, icon("folder"))))));
  } catch (e) { page.replaceChildren(h("div", { class: "callout err" }, e.message)); }
}

function prettyRunName(name) {
  const m = /^(\d{4}-\d{2}-\d{2})_(\d{2})(\d{2})(\d{2})/.exec(name);
  return m ? `${m[1]}  ${m[2]}:${m[3]}:${m[4]}` : name;
}

// ── open files / folders ───────────────────────────────────────────────────
async function openTarget(payload) {
  try { await api("/api/open", { json: payload }); }
  catch (e) { toast(e.message, "error"); }
}
function openFile(url) {
  // In the desktop window, hand files to the operating system (default browser
  // for the report, Excel for workbooks); in a browser, open a new tab.
  if (S.meta?.native) { openTarget({ target: "file", url }); return; }
  window.open(url, "_blank", "noopener");
}

// ── polling ────────────────────────────────────────────────────────────────
let pollTimer = null;
let pollFailures = 0;
async function poll() {
  clearTimeout(pollTimer);
  try {
    const st = await api(`/api/state?log_from=${S.logCursor}`);
    pollFailures = 0;
    if (st.job.log_total < S.logCursor) {
      // A new run started since the last poll — restart the log from the top.
      S.logs = []; S.logCursor = 0; st.job.logs = [];
    }
    if (st.job.logs.length) {
      S.logs.push(...st.job.logs);
      S.logCursor = st.job.log_total;
      const con = $("#console");
      if (con) {
        const atBottom = con.scrollHeight - con.scrollTop - con.clientHeight < 40;
        st.job.logs.forEach((l) => con.append(logLine(l)));
        if (atBottom) con.scrollTop = con.scrollHeight;
      }
    }
    S.state = st;
    render();
    const status = st.job.status;
    if (status !== S.lastJobStatus) {
      if (status === "done" && S.lastJobStatus === "running") {
        toast("Analysis complete.", "success");
        figureCache.clear();
        await loadResults();
      } else if (status === "error" && S.lastJobStatus === "running") {
        toast("The run failed — see the log.", "error", 8000);
      }
      S.lastJobStatus = status;
    }
    if (st.has_result && !S.results) await loadResults();
  } catch (e) {
    pollFailures++;
    if (pollFailures === 3) toast(e.message, "error", 8000);
  }
  const busy = S.state && (S.state.loading || jobRunning());
  pollTimer = setTimeout(poll, busy ? 500 : 2500);
}

// ── theme ──────────────────────────────────────────────────────────────────
function applyThemeLabel() {
  const dark = document.documentElement.dataset.theme === "dark";
  $("#theme-label").textContent = dark ? "Light theme" : "Dark theme";
  const btn = $("#theme-btn");
  btn.querySelector(".ico")?.replaceWith(icon(dark ? "sun" : "moon"));
}
function toggleTheme() {
  const dark = document.documentElement.dataset.theme !== "dark";
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  try { localStorage.setItem("ess.theme", dark ? "dark" : "light"); } catch (e) { /* ignore */ }
  applyThemeLabel();
}

// ── boot ───────────────────────────────────────────────────────────────────
async function boot() {
  hydrateIcons();
  $$(".nav-item").forEach((b) => b.addEventListener("click", () => go(b.dataset.page)));
  $("#run-btn").addEventListener("click", startRun);
  $("#theme-btn").addEventListener("click", toggleTheme);
  $("#modal-close").addEventListener("click", closeModal);
  $("#chart-modal").addEventListener("click", (e) => { if (e.target.id === "chart-modal") closeModal(); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeModal();
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); startRun(); }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "o") { e.preventDefault(); browse(); }
  });
  window.addEventListener("resize", resizeVisiblePlots);
  setupDragDrop();
  applyThemeLabel();

  try {
    S.meta = await api("/api/meta");
  } catch (e) {
    toast(e.message, "error", 10000);
    return;
  }
  $("#version").textContent = `v${S.meta.version}`;
  const keys = new Set(S.meta.analyses.map((a) => a.key));
  const saved = store.get("settings", null);
  S.settings = { ...defaultSettings(S.meta), ...(saved || {}) };
  S.settings.enabled = S.settings.enabled.filter((k) => keys.has(k));
  go("data");
  poll();
}

boot();
