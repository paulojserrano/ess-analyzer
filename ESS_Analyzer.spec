# ESS_Analyzer.spec  — PyInstaller build spec
# Run:  pyinstaller ESS_Analyzer.spec
#
# Produces:  dist/ESS_Analyzer.exe  (single-file, no console window)

from PyInstaller.utils.hooks import collect_all

block_cipher = None

# Plotly ships data files (validators, templates) that are loaded at runtime.
# collect_all() picks up the package's datas, binaries, and hidden submodules.
_plotly_datas, _plotly_binaries, _plotly_hidden = collect_all("plotly")

# jaraco is a namespace package — collect_all handles the namespace correctly
_jaraco_datas, _jaraco_binaries, _jaraco_hidden = collect_all("jaraco")

a = Analysis(
    ["app.py"],
    pathex=["."],
    binaries=_plotly_binaries + _jaraco_binaries,
    datas=_plotly_datas + _jaraco_datas,
    hiddenimports=_plotly_hidden + _jaraco_hidden + [
        # pandas loads the Excel engine dynamically
        "openpyxl",
        "openpyxl.styles",
        "openpyxl.utils",
        "openpyxl.workbook",
        "openpyxl.writer.excel",
        "pandas.io.formats.excel",
        # numpy internals sometimes missed
        "numpy.core._methods",
        "numpy.lib.format",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # not used — keep the exe lean
        "matplotlib",
        "scipy",
        "PIL",
        "IPython",
        "jupyter",
        "notebook",
        "kaleido",          # plotly static-image renderer — not needed
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
    upx=True,          # compress with UPX if available (reduces size ~30 %)
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
