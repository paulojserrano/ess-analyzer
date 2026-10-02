# ESS_Analyzer.spec  — PyInstaller build spec
# Run:  pyinstaller ESS_Analyzer.spec --clean --noconfirm   (or build.bat)
#
# Produces:  dist/ESS_Analyzer.exe  (single-file, no console window)
#
#   ESS_Analyzer.exe                 → desktop UI (native window via pywebview,
#                                      or the default browser if unavailable)
#   ESS_Analyzer.exe day1.xlsx ...   → headless analysis (no window)
#
# Reports are written to an "asrs_analysis_output" folder next to the .exe.

import glob
import os

from PyInstaller.utils.hooks import collect_all, collect_submodules

block_cipher = None

datas, binaries, hiddenimports = [], [], []

# Plotly ships data files (validators, templates, plotly.min.js) used at runtime.
for pkg in ("plotly", "jaraco"):
    d, b, h = collect_all(pkg)
    datas += d; binaries += b; hiddenimports += h

# Native window (optional at runtime — skipped if not installed at build time).
try:
    d, b, h = collect_all("webview")
    datas += d; binaries += b; hiddenimports += h
except Exception:
    pass

# The UI's static files.
datas += [("webui", "webui")]

hiddenimports += [
    # Analysis modules are resolved with importlib (config.ANALYSIS_IMPLEMENTATIONS),
    # so PyInstaller cannot see them through static imports.  Listed from the
    # folder: collect_submodules() cannot import this local package.
    *sorted("analyses." + os.path.splitext(os.path.basename(f))[0]
            for f in glob.glob(os.path.join("analyses", "*.py"))
            if not f.endswith("__init__.py")),
    "log_converter",
    # Web server stack
    *collect_submodules("uvicorn"),
    "fastapi", "starlette", "multipart", "python_multipart",
    # pandas loads the Excel engines dynamically
    "python_calamine",
    "openpyxl", "openpyxl.styles", "openpyxl.utils", "openpyxl.workbook",
    "openpyxl.writer.excel", "pandas.io.formats.excel",
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
        "kaleido",
        "pytest",
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
