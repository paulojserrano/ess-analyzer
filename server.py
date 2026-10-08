"""
server.py — local web server behind the desktop UI (webui/).

A single-page app talking to this JSON API on 127.0.0.1.  The run happens on a
background thread and the page polls /api/state.  Single-user by design: one
session's files and its latest run live in memory.

Unlike the old Excel pipeline there is no load-and-validate step — a log is only
read when the run starts — so adding files is instant and the UI stays simple.

    launch(mode="auto")   native window via pywebview when installed,
                          otherwise the default browser.
"""
from __future__ import annotations

import atexit
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
from dataclasses import dataclass, field

from fastapi import Body, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from config import DOOR_S_DEFAULT, DOOR_S_MAX, STARVED_S_DEFAULT, STARVED_S_MAX, TARGET_RATE_DEFAULT, Settings
from log_parser import LogError, assign_zones, is_log_path, log_date, log_stem, natural_key, scan_stations
from pipeline import (
    RunCancelled,
    RunResult,
    default_output_root,
    find_logs,
    run,
)
from report import REPORT_NAME

_log = logging.getLogger(__name__)

APP_NAME = "ESS Analyzer"
VERSION = "4.0"
_ALLOWED_EXT = (".log", ".log.gz")


def _resource_dir() -> str:
    """Folder holding webui/ — the PyInstaller unpack dir when frozen."""
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


WEBUI_DIR = os.path.join(_resource_dir(), "webui")


# ══════════════════════════════════════════════════════════════════════════════
# Session state
# ══════════════════════════════════════════════════════════════════════════════

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
        self.paths: list[str] = []          # every log file the user added
        self.job = Job()
        self.result: RunResult | None = None
        self.settings = Settings()
        self.points: dict[str, tuple[int, int]] = {}    # station -> (x, y), from quick scans
        self.scanned: set[str] = set()
        self.scanning = 0
        self.output_root = output_root or default_output_root()
        self.upload_dir = tempfile.mkdtemp(prefix="ess_uploads_")
        atexit.register(shutil.rmtree, self.upload_dir, True)

    # ── files ─────────────────────────────────────────────────────────────────

    def add(self, paths: list[str]) -> tuple[int, list[str]]:
        """Add files or folders.  Returns (added, rejected messages)."""
        rejected: list[str] = []
        wanted: list[str] = []
        for p in paths:
            if os.path.isdir(p):
                hits = [f for f in (os.path.join(p, n) for n in sorted(os.listdir(p)))
                        if is_log_path(f) and os.path.isfile(f)]
                if not hits:
                    rejected.append(f"{os.path.basename(p)}: no .log or .log.gz files inside")
                wanted += hits
            elif is_log_path(p) and os.path.isfile(p):
                wanted.append(p)
            else:
                rejected.append(f"{os.path.basename(p)}: not a .log or .log.gz file")

        with self.lock:
            known = set(self.paths)
            fresh = [p for p in wanted if p not in known]
            self.paths += fresh
            self.paths.sort(key=os.path.basename)
        if fresh:
            threading.Thread(target=self._scan, args=(fresh,), daemon=True, name="ess-scan").start()
        return len(fresh), rejected

    def _scan(self, paths: list[str]) -> None:
        """Find the stations in newly added logs, so targets can be set per
        station before a run.  Stops a few percent into each file."""
        with self.lock:
            self.scanning += 1
        try:
            for p in paths:
                if p in self.scanned:
                    continue
                try:
                    found = scan_stations(p)
                except LogError:
                    found = {}
                with self.lock:
                    self.scanned.add(p)
                    for st, xy in found.items():
                        self.points.setdefault(st, xy)
        finally:
            with self.lock:
                self.scanning -= 1

    def stations_json(self) -> dict:
        with self.lock:
            points = dict(self.points)
            scanning = self.scanning > 0
        zones = assign_zones(points)
        by_zone: dict[str, list[str]] = {}
        for st in sorted(zones, key=natural_key):
            by_zone.setdefault(zones[st], []).append(st)
        return {
            "scanning": scanning,
            "zones": [{"zone": z, "stations": s}
                      for z, s in sorted(by_zone.items(), key=lambda kv: natural_key(kv[0]))],
        }

    def remove(self, names: list[str]) -> None:
        with self.lock:
            drop = set(names)
            self.paths = [p for p in self.paths if os.path.basename(p) not in drop]

    def clear(self) -> None:
        with self.lock:
            self.paths = []

    def groups(self) -> list[list[str]]:
        with self.lock:
            return find_logs(list(self.paths))

    def days_json(self) -> list[dict]:
        out = []
        for g in self.groups():
            size = sum(os.path.getsize(p) for p in g if os.path.isfile(p))
            out.append({
                "label": log_stem(g[0]),
                "date": log_date(g[0]),
                "files": [os.path.basename(p) for p in g],
                "paths": g,
                "bytes": size,
            })
        return out

    # ── running ───────────────────────────────────────────────────────────────

    def start(self, settings: Settings) -> str:
        groups = self.groups()
        if not groups:
            raise HTTPException(400, "Add at least one log file first.")
        with self.lock:
            if self.job.status == "running":
                raise HTTPException(409, "A run is already in progress.")
            problems = settings.validate()
            if problems:
                raise HTTPException(400, "; ".join(problems))
            self.settings = settings
            self.job = Job(id=uuid.uuid4().hex[:8], status="running",
                           started=time.time(), step="Starting")
            job = self.job
        threading.Thread(target=self._run, args=(job, groups, settings),
                         daemon=True, name="ess-run").start()
        return job.id

    def _run(self, job: Job, groups: list[list[str]], settings: Settings) -> None:
        def on_progress(pct: float, step: str) -> None:
            job.progress, job.step = pct, step

        def on_log(level: str, msg: str) -> None:
            job.logs.append({"t": round(time.time() - job.started, 1),
                             "level": level, "msg": msg})

        try:
            settings.output_root = self.output_root
            result = run(groups, settings, on_progress=on_progress,
                         on_log=on_log, cancel=job.cancel)
            with self.lock:
                self.result = result
                job.status = "done"
                job.progress = 100.0
        except RunCancelled:
            job.status = "cancelled"
            job.step = "Cancelled"
        except Exception as exc:
            job.status = "error"
            job.error = f"{type(exc).__name__}: {exc}"
            on_log("error", job.error)
            _log.exception("Run failed")
        finally:
            job.finished = time.time()

    def state(self, log_from: int = 0) -> dict:
        j = self.job
        elapsed = ((j.finished or time.time()) - j.started) if j.started else 0.0
        return {
            "days": self.days_json(),
            "settings": self.settings.to_dict(),
            "output_root": self.output_root,
            "job": {
                "id": j.id, "status": j.status, "progress": round(j.progress, 1),
                "step": j.step, "error": j.error, "elapsed": round(elapsed, 1),
                "logs": j.logs[log_from:], "log_total": len(j.logs),
            },
            "has_results": self.result is not None,
            "stations": self.stations_json(),
        }


# ══════════════════════════════════════════════════════════════════════════════
# App
# ══════════════════════════════════════════════════════════════════════════════

def _rel_url(result: RunResult, path: str | None, root: str) -> str | None:
    if not path:
        return None
    rel = os.path.relpath(path, root).replace(os.sep, "/")
    return "/runs/" + urllib.parse.quote(rel)


def _open_in_os(path: str) -> None:
    if sys.platform.startswith("win"):
        os.startfile(path)                                    # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def create_app(output_root: str | None = None, native: bool = False) -> FastAPI:
    app = FastAPI(title=APP_NAME, docs_url=None, redoc_url=None)
    session = Session(output_root)
    os.makedirs(session.output_root, exist_ok=True)
    app.state.session = session

    app.mount("/static", StaticFiles(directory=WEBUI_DIR), name="static")
    app.mount("/runs", StaticFiles(directory=session.output_root), name="runs")

    @app.get("/")
    def index():
        return FileResponse(os.path.join(WEBUI_DIR, "index.html"))

    @app.get("/api/meta")
    def meta():
        return {
            "app": APP_NAME, "version": VERSION, "native": native,
            "output_root": session.output_root,
            "defaults": {"door_s": DOOR_S_DEFAULT, "door_s_max": DOOR_S_MAX,
                         "target_rate": TARGET_RATE_DEFAULT,
                         "starved_s": STARVED_S_DEFAULT, "starved_s_max": STARVED_S_MAX},
        }

    @app.get("/api/state")
    def state(log_from: int = 0):
        return session.state(log_from)

    @app.post("/api/upload")
    async def upload(files: list[UploadFile] = File(...)):
        batch = os.path.join(session.upload_dir, uuid.uuid4().hex[:8])
        os.makedirs(batch, exist_ok=True)
        saved, rejected = [], []
        for f in files:
            name = os.path.basename(f.filename or "upload")
            if not name.lower().endswith(_ALLOWED_EXT):
                rejected.append(f"{name}: not a .log or .log.gz file")
                continue
            dest = os.path.join(batch, name)
            with open(dest, "wb") as out:
                shutil.copyfileobj(f.file, out, length=1 << 20)
            saved.append(dest)
        added, more = session.add(saved)
        return {"added": added, "rejected": rejected + more}

    @app.post("/api/paths")
    def add_paths(payload: dict = Body(...)):
        paths = [p for p in payload.get("paths", []) if isinstance(p, str)]
        missing = [p for p in paths if not os.path.exists(p)]
        added, rejected = session.add([p for p in paths if os.path.exists(p)])
        return {"added": added,
                "rejected": rejected + [f"{os.path.basename(p)}: not found" for p in missing]}

    @app.post("/api/days/remove")
    def remove_day(payload: dict = Body(...)):
        session.remove(payload.get("files", []))
        return {"ok": True}

    @app.post("/api/days/clear")
    def clear_days():
        session.clear()
        return {"ok": True}

    @app.get("/api/native-dialog")
    def native_dialog():
        """Open the OS file picker — only possible inside a pywebview window."""
        try:
            import webview
        except ImportError:
            raise HTTPException(400, "The native file dialog needs the desktop window.")
        windows = getattr(webview, "windows", None)
        if not windows:
            raise HTTPException(400, "No desktop window is open.")
        picked = windows[0].create_file_dialog(
            webview.OPEN_DIALOG, allow_multiple=True,
            file_types=("ESS logs (*.log;*.log.gz)", "All files (*.*)"),
        )
        added, rejected = session.add(list(picked or []))
        return {"added": added, "rejected": rejected}

    @app.post("/api/run")
    def start_run(payload: dict = Body(...)):
        s = Settings()
        for key in ("door_s", "target_rate", "starved_s"):
            if payload.get(key) is not None:
                try:
                    setattr(s, key, float(payload[key]))
                except (TypeError, ValueError):
                    raise HTTPException(400, f"'{key}' must be a number.")
        nd = payload.get("no_door") or {}
        if isinstance(nd, list):
            nd = {str(x): True for x in nd}
        if not isinstance(nd, dict) or not all(isinstance(v, bool) for v in nd.values()):
            raise HTTPException(400, "'no_door' must list stations or zones without a door.")
        s.no_door = {str(k): v for k, v in nd.items()}
        days_off = payload.get("no_door_days") or []
        if isinstance(days_off, dict):
            days_off = [d for d, off in days_off.items() if off]
        if not isinstance(days_off, list):
            raise HTTPException(400, "'no_door_days' must be a list of dates.")
        s.no_door_days = [str(d) for d in days_off]
        for key, table in (("targets", s.targets), ("pick_s", s.pick_s), ("switch_s", s.switch_s)):
            given = payload.get(key) or {}
            if not isinstance(given, dict):
                raise HTTPException(400, f"'{key}' must be an object of zone or station -> value.")
            for name, val in given.items():
                if val in (None, ""):
                    continue
                try:
                    table[str(name)] = float(val)
                except (TypeError, ValueError):
                    raise HTTPException(400, f"{key} for '{name}' must be a number.")
        return {"job": session.start(s)}

    @app.post("/api/run/cancel")
    def cancel_run():
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
            "report_url": _rel_url(r, r.report, root),
            "days": [{
                "label": d.label,
                "date": d.date,
                "ok": d.ok,
                "error": d.error,
                "seconds": round(d.seconds, 1),
                "headline": _headline(d),
            } for d in r.days],
        }

    @app.get("/api/runs")
    def runs():
        out, root = [], session.output_root
        if os.path.isdir(root):
            for name in sorted(os.listdir(root), reverse=True)[:50]:
                folder = os.path.join(root, name)
                if not os.path.isdir(folder):
                    continue
                entry = os.path.join(folder, REPORT_NAME)
                if not os.path.isfile(entry):
                    continue
                out.append({
                    "name": name, "path": folder,
                    "url": "/runs/" + urllib.parse.quote(
                        os.path.relpath(entry, root).replace(os.sep, "/")),
                    "modified": os.path.getmtime(entry),
                })
        return {"root": root, "runs": out}

    @app.post("/api/open")
    def open_path(payload: dict = Body(...)):
        target, r = payload.get("target"), session.result
        if target == "output_root":
            path = session.output_root
        elif target == "run_dir" and r:
            path = r.run_dir
        elif target == "report" and r:
            path = r.report
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
        if not path or not os.path.exists(path):
            raise HTTPException(404, "Path no longer exists.")
        try:
            _open_in_os(path)
        except Exception as exc:
            raise HTTPException(500, f"Could not open: {exc}")
        return {"ok": True}

    return app


def _headline(day) -> dict | None:
    """A few numbers for the results card."""
    m = day.metrics
    if not m:
        return None
    k = m.get("robot_k50") or {}
    return {
        "visits": m["overall"]["visits"],
        "stations": len(m["stations"]),
        "op_med": m["overall"]["op_med"],
        "multi_pct": k.get("multi_station_pct"),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Launch
# ══════════════════════════════════════════════════════════════════════════════

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def launch(port: int = 0, output_root: str | None = None, mode: str = "auto") -> None:
    """Serve the UI and open it in a window (or the browser)."""
    import uvicorn

    try:
        import webview
    except ImportError:
        webview = None
    native = mode != "browser" and webview is not None

    port = port or _free_port()
    url = f"http://127.0.0.1:{port}"
    app = create_app(output_root=output_root, native=native)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)

    if native:
        threading.Thread(target=server.run, daemon=True, name="ess-http").start()
        for _ in range(100):                     # wait for the port to answer
            if server.started:
                break
            time.sleep(0.05)
        window = webview.create_window(APP_NAME, url, width=1280, height=860,
                                       min_size=(900, 620))
        webview.start()
        server.should_exit = True
        _ = window
    else:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
        print(f"{APP_NAME} {VERSION} — {url}  (Ctrl+C to stop)")
        server.run()
