# ESS_Analyzer.spec  — PyInstaller build spec
# Run:  pyinstaller ESS_Analyzer.spec --clean --noconfirm   (or build.bat)
#
# Produces:  dist/ESS_Analyzer.exe  (single-file, no console window)
#
#   ESS_Analyzer.exe                 → desktop UI (native window via pywebview,
#                                      or the default browser if unavailable)
#   ESS_Analyzer.exe day1.log.gz ... → headless analysis (no window)
#
# Reports are written to an "asrs_analysis_output" folder next to the .exe.

from PyInstaller.utils.hooks import collect_all, collect_submodules

block_cipher = None

datas, binaries, hiddenimports = [], [], []

# Native window (optional at runtime — skipped if not installed at build time).
try:
    d, b, h = collect_all("webview")
    datas += d; binaries += b; hiddenimports += h
except Exception:
    pass

# The UI's static files and the report templates (read at runtime by report.py).
datas += [("webui", "webui"), ("templates", "templates")]

hiddenimports += [
    # Web server stack
    *collect_submodules("uvicorn"),
    "fastapi", "starlette", "multipart", "python_multipart",
]

a = Analysis(
    ["app.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # not used — keep the exe lean
        "tkinter",
        "matplotlib",
        "scipy",
        "PIL",
        "IPython",
        "jupyter",
        "notebook",
        "pytest",
        "plotly",
        "openpyxl",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="ESS_Analyzer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,     # no black terminal window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,         # set to "your_icon.ico" if you have one
)
