@echo off
cd /d "%~dp0"
echo Building ESS_Analyzer.exe ...
pyinstaller ESS_Analyzer.spec --clean --noconfirm
echo.
if exist dist\ESS_Analyzer.exe (
    echo Done.  Executable: dist\ESS_Analyzer.exe
) else (
    echo Build failed — check output above.
)
pause
