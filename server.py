"""
server.py — local web server behind the desktop UI (webui/).

The UI is a single-page app talking to this JSON API on 127.0.0.1.  All heavy
work runs on background threads; the page polls /api/state.  The server is
single-user by design: it keeps one session's loaded days and the latest run
in memory.

    launch(mode="auto")   native window via pywebview when installed,
                          otherwise the default web browser.
"""
from __future__ import annotations

import atexit
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import uuid
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from fastapi import Body, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from analyses._common import natural_key
from config import (
    ANALYSIS_MODULES,
    DEFAULT_CHECKED,
    MAX_OPERATIONAL_SWITCH_S,
    PICK_START_EVENTS,
    SWITCH_S_FALLBACK,
)
from data_loader import PREFLIGHT_OK, best_preflight_status, validate_user_config
from pipeline import (
    LoadError,
    LoadedDay,
    RunCancelled,
    RunResult,
    RunSettings,
    default_output_root,
    group_input_paths,
    load_day,
    run_pipeline,
)

_log = logging.getLogger(__name__)

APP_NAME = "ESS Analyzer"
VERSION = "3.0"
_ALLOWED_EXT = {".xlsx", ".xlsm", ".log", ".json"}


def _resource_dir() -> str:
    """Folder holding webui/ — the PyInstaller unpack dir when frozen."""
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


WEBUI_DIR = os.path.join(_resource_dir(), "webui")


def _plotly_js_path() -> str:
    import plotly
    return os.path.join(os.path.dirname(plotly.__file__), "package_data", "plotly.min.js")


# ══════════════════════════════════════════════════════════════════════════════
# Session state
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class DayEntry:
    id: str
    paths: list[str]
    label: str
    status: str = "loading"            # loading | ready | error
    error: str = ""
    messages: list[tuple[str, str]] = field(default_factory=list)
    day: LoadedDay | None = None
    label_edited: bool = False


@dataclass
class Job:
    id: str = ""
    status: str = "idle"               # idle | running | done | error | cancelled
    progress: float = 0.0
    step: str = ""
    started: float = 0.0
    finished: float = 0.0
    logs: list[dict] = field(default_factory=list)
    error: str = ""
    cancel: threading.Event = field(default_factory=threading.Event)


class Session:
    def __init__(self, output_root: str | None = None):
        self.lock = threading.RLock()
        self.days: dict[str, DayEntry] = {}
        self.order: list[str] = []
        self.job = Job()
        self.result: RunResult | None = None
        self.output_root = output_root or default_output_root()
        self.upload_dir = tempfile.mkdtemp(prefix="ess_uploads_")
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ess-load")
        atexit.register(shutil.rmtree, self.upload_dir, True)

    # ── loading ───────────────────────────────────────────────────────────────

    def add_paths(self, paths: list[str]) -> list[str]:
        data_paths = [p for p in paths if os.path.splitext(p)[1].lower() != ".json"]
        known = {p for e in self.days.values() for p in e.paths}
        ids: list[str] = []
        for group in group_input_paths([p for p in data_paths if p not in known]):
            entry = DayEntry(id=uuid.uuid4().hex[:10], paths=group,
                             label=os.path.splitext(os.path.basename(group[0]))[0])
            with self.lock:
                self.days[entry.id] = entry
                self.order.append(entry.id)
            ids.append(entry.id)
            self.pool.submit(self._load, entry.id)
        return ids

    def _load(self, day_id: str) -> None:
        entry = self.days.get(day_id)
        if entry is None:
            return
        try:
            day = load_day(entry.paths)
            with self.lock:
                if day_id not in self.days:
                    return
                labels = {e.label for e in self.days.values()
                          if e.id != day_id and e.status == "ready"}
                msgs = list(day.messages)
                if day.label in labels:
                    msgs.insert(0, ("warning", f"Another loaded file is also dated {day.label} — "
                                               "rename one of the days to tell them apart."))
                if not entry.label_edited:
                    entry.label = day.label
                entry.day, entry.messages, entry.status = day, msgs, "ready"
        except LoadError as exc:
            with self.lock:
                entry.status, entry.error = "error", str(exc)
                entry.messages = exc.messages or [("error", str(exc))]
        except Exception as exc:  # unexpected — keep the app alive
            _log.exception("Loading %s failed", entry.paths)
            with self.lock:
                entry.status, entry.error = "error", f"{type(exc).__name__}: {exc}"
                entry.messages = [("error", entry.error)]

    # ── running ───────────────────────────────────────────────────────────────

    def start_run(self, settings: RunSettings) -> str:
        with self.lock:
            if self.job.status == "running":
                raise HTTPException(409, "A run is already in progress.")
            entries = [self.days[i] for i in self.order if i in self.days]
            if any(e.status == "loading" for e in entries):
                raise HTTPException(409, "Files are still loading.")
            ready = [e for e in entries if e.status == "ready" and e.day is not None]
            if not ready:
                raise HTTPException(400, "Load at least one file first.")
            if not settings.enabled:
                raise HTTPException(400, "Select at least one analysis.")
            days = []
            for e in ready:
                e.day.label = e.label
                days.append(e.day)
            self.job = Job(id=uuid.uuid4().hex[:10], status="running", started=time.time())
            job = self.job
        settings.output_root = self.output_root
        threading.Thread(target=self._run, args=(job, days, settings),
                         daemon=True, name="ess-run").start()
        return job.id

    def _run(self, job: Job, days: list[LoadedDay], settings: RunSettings) -> None:
        def on_progress(pct: float, step: str) -> None:
            job.progress, job.step = round(pct, 1), step

        def on_log(level: str, msg: str) -> None:
            with self.lock:
                job.logs.append({"t": round(time.time() - job.started, 1),
                                 "level": level, "msg": msg})

        try:
            result = run_pipeline(days, settings, on_progress, on_log, job.cancel)
            with self.lock:
                self.result = result
                job.status, job.progress = "done", 100.0
        except RunCancelled:
            on_log("warning", "Run cancelled.")
            job.status = "cancelled"
        except Exception as exc:
            _log.exception("Run failed")
            on_log("error", f"Run failed — {type(exc).__name__}: {exc}")
            job.status, job.error = "error", f"{type(exc).__name__}: {exc}"
        finally:
            job.finished = time.time()

    # ── views ─────────────────────────────────────────────────────────────────

    def stations(self) -> list[dict]:
        """Union of stations across ready days (first day wins for defaults)."""
        rows: dict[str, dict] = {}
        for i in self.order:
            e = self.days.get(i)
            if not e or e.status != "ready" or e.day is None:
                continue
            cfg = e.day.cfg
            for ws in cfg["ws_order"]:
                if ws in rows:
                    continue
                rows[ws] = {
                    "station": ws,
                    "zone": cfg["type_map"].get(ws, ""),
                    "design_rate": cfg["design_rate"].get(ws),
                    "measured_switch_s": _round(cfg.get("switch_measured", {}).get(ws), 1),
                }
        return [rows[k] for k in sorted(rows, key=natural_key)]

    def state(self, log_from: int = 0) -> dict:
        with self.lock:
            entries = [self.days[i] for i in self.order if i in self.days]
            days = [_day_json(e) for e in entries]
            ready = [e.day for e in entries if e.status == "ready" and e.day]
            availability = {}
            for key, label in ANALYSIS_MODULES:
                statuses = [next((f for f in d.preflight if f["key"] == key), None) for d in ready]
                statuses = [f for f in statuses if f]
                best = best_preflight_status([f["status"] for f in statuses]) if statuses else None
                reason = next((f["reason"] for f in statuses if f["status"] == best and f["reason"]), "")
                availability[key] = {"status": best, "reason": reason}
            pick = {
                "any_missing_arrived": any(not d.pick_source.get("arrived") for d in ready),
                "ppready_possible": any(d.pick_source.get("ppready") for d in ready),
            }
            job = self.job
            return {
                "days": days,
                "loading": sum(1 for e in entries if e.status == "loading"),
                "stations": self.stations(),
                "availability": availability,
                "pick_source": pick,
                "job": {
                    "id": job.id, "status": job.status, "progress": job.progress,
                    "step": job.step, "error": job.error,
                    "elapsed": round((job.finished or time.time()) - job.started, 1) if job.started else 0,
                    "log_total": len(job.logs),
                    "logs": job.logs[log_from:],
                },
                "has_result": self.result is not None,
            }


def _round(v, nd):
    return None if v is None else round(float(v), nd)


def _day_json(e: DayEntry) -> dict:
    d = e.day
    out: dict[str, Any] = {
        "id": e.id, "label": e.label, "status": e.status, "error": e.error,
        "files": [os.path.basename(p) for p in e.paths],
        "messages": [{"level": lvl, "text": m} for lvl, m in e.messages],
    }
    if d is not None:
        out.update({
            "date": d.date,
            "sheets": d.sheet_rows,
            "stations": len(d.cfg["ws_order"]),
            "zones": len(set(d.cfg["type_map"].values())),
            "amr_type": d.cfg.get("amr_type"),
            "pick_source": d.pick_source,
            "limited": sum(1 for f in d.preflight if f["status"] != PREFLIGHT_OK),
            "config_file": bool(d.user_cfg),
        })
    return out


# ══════════════════════════════════════════════════════════════════════════════
# Result serialisation
# ══════════════════════════════════════════════════════════════════════════════

def _chart_meta(entry: dict, idx: int) -> dict:
    rows = (entry.get("raw_data") or {}).get("rows") or []
    return {
        "idx": idx, "id": entry["id"], "title": entry["title"],
        "source": entry.get("source", ""), "method": entry.get("method", ""),
        "rows": len(rows),
    }


def _registry(result: RunResult, day: str) -> list[dict]:
    if day == "summary":
        return result.summary_registry
    try:
        return result.days[int(day)].registry
    except (ValueError, IndexError):
        raise HTTPException(404, "Unknown day")


def _rel_url(result: RunResult, path: str | None, root: str) -> str | None:
    if not path or not os.path.isfile(path):
        return None
    rel = os.path.relpath(path, root).replace("\\", "/")
    return "/runs/" + rel


def _open_in_os(path: str) -> None:
    if sys.platform.startswith("win"):
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


# ══════════════════════════════════════════════════════════════════════════════
# App
# ══════════════════════════════════════════════════════════════════════════════

def create_app(output_root: str | None = None, native: bool = False) -> FastAPI:
    session = Session(output_root)
    os.makedirs(session.output_root, exist_ok=True)
    app = FastAPI(title=APP_NAME, docs_url=None, redoc_url=None)
    app.state.session = session
    app.state.native = native
    app.state.window = None

    @app.get("/")
    def index():
        return FileResponse(os.path.join(WEBUI_DIR, "index.html"),
                            headers={"Cache-Control": "no-store"})

    @app.get("/vendor/plotly.min.js")
    def plotly_js():
        return FileResponse(_plotly_js_path(), media_type="application/javascript",
                            headers={"Cache-Control": "max-age=86400"})

    app.mount("/static", StaticFiles(directory=WEBUI_DIR), name="static")
    app.mount("/runs", StaticFiles(directory=session.output_root, html=True), name="runs")

    @app.get("/api/meta")
    def meta():
        return {
            "app": APP_NAME, "version": VERSION,
            "analyses": [{"key": k, "label": lbl.split("  (")[0].strip(),
                          "requires": (lbl.split("  (")[1].rstrip(")") if "  (" in lbl else ""),
                          "default": k in DEFAULT_CHECKED} for k, lbl in ANALYSIS_MODULES],
            "switch_s_default": SWITCH_S_FALLBACK,
            "max_operational_switch_s": MAX_OPERATIONAL_SWITCH_S,
            "pick_start_events": list(PICK_START_EVENTS),
            "output_root": session.output_root,
            "native": bool(app.state.native),
        }

    app.state.last_seen = 0.0

    @app.get("/api/state")
    def state(log_from: int = 0):
        app.state.last_seen = time.time()
        return session.state(log_from)

    @app.post("/api/files")
    async def upload(files: list[UploadFile] = File(...)):
        batch = os.path.join(session.upload_dir, uuid.uuid4().hex[:8])
        os.makedirs(batch, exist_ok=True)
        saved, rejected = [], []
        # Config files first, so data files in the same batch pick them up
        # (load_user_config looks next to the data file).
        for f in sorted(files, key=lambda f: not (f.filename or "").lower().endswith(".json")):
            name = os.path.basename(f.filename or "upload")
            ext = os.path.splitext(name)[1].lower()
            if ext not in _ALLOWED_EXT:
                rejected.append(f"{name}: unsupported file type")
                continue
            dest = os.path.join(batch, "asrs_config.json" if ext == ".json" else name)
            with open(dest, "wb") as out:
                shutil.copyfileobj(f.file, out, length=1 << 20)
            if ext == ".json":
                try:
                    with open(dest, encoding="utf-8") as fh:
                        vr = validate_user_config(json.load(fh))
                    if not vr.ok:
                        rejected.append(f"{name}: " + "; ".join(vr.errors))
                        os.remove(dest)
                except Exception as exc:
                    rejected.append(f"{name}: not valid JSON ({exc})")
                    os.remove(dest)
                continue
            saved.append(dest)
        ids = session.add_paths(saved)
        return {"added": ids, "rejected": rejected}

    @app.post("/api/files/paths")
    def add_paths(payload: dict = Body(...)):
        paths = [p for p in payload.get("paths", []) if isinstance(p, str)]
        missing = [p for p in paths if not os.path.isfile(p)]
        if missing:
            raise HTTPException(400, f"File not found: {', '.join(missing)}")
        return {"added": session.add_paths(paths), "rejected": []}

    @app.post("/api/dialog/open")
    def native_dialog():
        win = app.state.window
        if win is None:
            raise HTTPException(400, "Native file dialog is only available in the desktop window.")
        import webview  # type: ignore
        res = win.create_file_dialog(
            webview.OPEN_DIALOG, allow_multiple=True,
            file_types=("ESS data files (*.xlsx;*.xlsm;*.log)", "All files (*.*)"),
        )
        paths = list(res or [])
        return {"added": session.add_paths(paths) if paths else [], "rejected": []}

    @app.patch("/api/days/{day_id}")
    def rename_day(day_id: str, payload: dict = Body(...)):
        label = str(payload.get("label", "")).strip()
        with session.lock:
            e = session.days.get(day_id)
            if e is None:
                raise HTTPException(404, "Unknown day")
            if label:
                e.label, e.label_edited = label[:60], True
        return {"ok": True}

    @app.delete("/api/days/{day_id}")
    def remove_day(day_id: str):
        with session.lock:
            session.days.pop(day_id, None)
            session.order = [i for i in session.order if i != day_id]
        return {"ok": True}

    @app.post("/api/days/reorder")
    def reorder(payload: dict = Body(...)):
        ids = [i for i in payload.get("order", []) if i in session.days]
        with session.lock:
            session.order = ids + [i for i in session.order if i not in ids]
        return {"ok": True}

    @app.post("/api/run")
    def run(payload: dict = Body(...)):
        valid = {k for k, _ in ANALYSIS_MODULES}
        try:
            sw = float(payload.get("switch_s_fixed", SWITCH_S_FALLBACK))
        except (TypeError, ValueError):
            raise HTTPException(400, "Switch time must be a number.")
        if not (0 <= sw <= MAX_OPERATIONAL_SWITCH_S):
            raise HTTPException(400, f"Switch time must be between 0 and {MAX_OPERATIONAL_SWITCH_S:g} s.")
        pse = payload.get("pick_start_event") or None
        if pse is not None and pse not in PICK_START_EVENTS:
            raise HTTPException(400, "Unknown pick-time start event.")
        rates: dict[str, float] = {}
        for ws, v in (payload.get("design_rates") or {}).items():
            if v in (None, ""):
                continue
            try:
                v = float(v)
            except (TypeError, ValueError):
                raise HTTPException(400, f"Design rate for {ws} must be a number.")
            if v < 0:
                raise HTTPException(400, f"Design rate for {ws} must be positive.")
            if v > 0:
                rates[str(ws)] = v
        settings = RunSettings(
            enabled={k for k in payload.get("enabled", []) if k in valid},
            switch_s_fixed=sw,
            switch_mode="measured" if payload.get("switch_mode") == "measured" else "fixed",
            pick_start_event=pse,
            station_types={str(k): str(v).strip() for k, v in (payload.get("station_types") or {}).items()
                           if str(v).strip()},
            design_rates=rates,
            excel_exports=bool(payload.get("excel_exports", True)),
        )
        return {"job": session.start_run(settings)}

    @app.post("/api/run/cancel")
    def cancel():
        session.job.cancel.set()
        return {"ok": True}

    @app.get("/api/results")
    def results():
        r = session.result
        if r is None:
            raise HTTPException(404, "No results yet.")
        root = session.output_root
        return {
            "run_dir": r.run_dir,
            "seconds": round(r.seconds, 1),
            "warnings": r.warnings,
            "report_url": _rel_url(r, r.html_path, root),
            "combined_url": _rel_url(r, r.combined_path, root),
            "summary": [_chart_meta(c, i) for i, c in enumerate(r.summary_registry)],
            "days": [{
                "label": d.label, "date": d.date, "kpis": d.kpis, "failures": d.failures,
                "exports": [{"name": os.path.basename(p), "url": _rel_url(r, p, root)}
                            for p in d.exports if os.path.isfile(p)],
                "charts": [_chart_meta(c, i) for i, c in enumerate(d.registry)],
            } for d in r.days],
        }

    @app.get("/api/results/{day}/{idx}/figure")
    def figure(day: str, idx: int):
        r = session.result
        if r is None:
            raise HTTPException(404, "No results yet.")
        reg = _registry(r, day)
        if not 0 <= idx < len(reg):
            raise HTTPException(404, "Unknown chart")
        return Response(reg[idx]["figure"].to_json(), media_type="application/json")

    @app.get("/api/results/{day}/{idx}/rows")
    def rows(day: str, idx: int):
        r = session.result
        if r is None:
            raise HTTPException(404, "No results yet.")
        reg = _registry(r, day)
        if not 0 <= idx < len(reg):
            raise HTTPException(404, "Unknown chart")
        from report_builder import _json_safe
        raw = reg[idx].get("raw_data") or {}
        return JSONResponse(_json_safe({"id": reg[idx]["id"], "rows": raw.get("rows") or [],
                                        "meta": {k: v for k, v in raw.items() if k != "rows"}}))

    @app.get("/api/runs")
    def runs():
        out = []
        root = session.output_root
        if os.path.isdir(root):
            for name in sorted(os.listdir(root), reverse=True)[:50]:
                rep = os.path.join(root, name, "asrs_analysis_report.html")
                if os.path.isfile(rep):
                    days = [d for d in sorted(os.listdir(os.path.join(root, name)))
                            if os.path.isdir(os.path.join(root, name, d))]
                    out.append({"name": name, "report_url": f"/runs/{name}/asrs_analysis_report.html",
                                "path": os.path.join(root, name), "days": days,
                                "modified": os.path.getmtime(rep)})
        return {"root": root, "runs": out}

    @app.post("/api/open")
    def open_path(payload: dict = Body(...)):
        target = payload.get("target")
        r = session.result
        if target == "output_root":
            path = session.output_root
        elif target == "run_dir" and r:
            path = r.run_dir
        elif target == "report" and r:
            path = r.html_path
        elif target == "file" and str(payload.get("url", "")).startswith("/runs/"):
            rel = urllib.parse.unquote(str(payload["url"])[len("/runs/"):])
            root = os.path.realpath(session.output_root)
            path = os.path.realpath(os.path.join(root, rel))
            if os.path.commonpath([root, path]) != root:
                raise HTTPException(400, "Path is outside the output folder.")
        elif target == "run" and payload.get("name"):
            path = os.path.join(session.output_root, os.path.basename(str(payload["name"])))
        else:
            raise HTTPException(400, "Nothing to open.")
        if not os.path.exists(path):
            raise HTTPException(404, "Path no longer exists.")
        try:
            _open_in_os(path)
        except Exception as exc:
            raise HTTPException(500, f"Could not open: {exc}")
        return {"ok": True, "path": path}

    return app


# ══════════════════════════════════════════════════════════════════════════════
# Launcher
# ══════════════════════════════════════════════════════════════════════════════

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_idle_watchdog(app: FastAPI, server, idle_s: float = 90.0) -> None:
    """Shut the server down when no page has polled for `idle_s` seconds
    (after the first page load) and no run is in progress."""
    def watch() -> None:
        while not server.should_exit:
            time.sleep(5)
            seen = app.state.last_seen
            busy = app.state.session.job.status == "running"
            if seen and not busy and time.time() - seen > idle_s:
                server.should_exit = True
    threading.Thread(target=watch, daemon=True, name="ess-watchdog").start()


def launch(port: int = 0, output_root: str | None = None, mode: str = "auto") -> None:
    """Start the UI.  mode: 'auto' (native window if possible), 'browser', 'none'."""
    import uvicorn

    # A windowed (no-console) PyInstaller build has no stdout/stderr; uvicorn
    # and print() would crash writing to them.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")

    webview = None
    if mode == "auto":
        try:
            import webview  # type: ignore  # noqa: F811
        except Exception:
            webview = None

    port = port or _free_port()
    url = f"http://127.0.0.1:{port}/"
    app = create_app(output_root, native=webview is not None)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                           log_level="warning", access_log=False,
                                           log_config=None))

    if webview is None:
        if mode != "none":
            threading.Timer(1.0, lambda: webbrowser.open(url)).start()
            if getattr(sys, "frozen", False):
                # No console window to Ctrl+C: stop once the page is closed.
                _start_idle_watchdog(app, server)
        print(f"{APP_NAME} is running at {url}  (Ctrl+C to quit)")
        server.run()
        return

    t = threading.Thread(target=server.run, daemon=True, name="ess-server")
    t.start()
    for _ in range(100):           # wait until the server accepts connections
        if server.started:
            break
        time.sleep(0.05)
    window = webview.create_window(APP_NAME, url, width=1440, height=920,
                                   min_size=(1024, 680), background_color="#0f1424")
    app.state.window = window
    webview.start()
    server.should_exit = True
    t.join(timeout=3)
