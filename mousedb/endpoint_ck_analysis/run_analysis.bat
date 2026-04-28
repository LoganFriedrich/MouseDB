@echo off
REM ============================================================================
REM  Endpoint CK Analysis - Windows launcher
REM  ------------------------------
REM  Double-click this file to open JupyterLab pointed at the notebooks/ folder.
REM
REM  Prerequisite: you have already run install.bat at least once.
REM
REM  If JupyterLab does not open, or you get a "kernel not found" error,
REM  re-run install.bat. For other errors, open TROUBLESHOOTING.md.
REM ============================================================================

setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo ERROR: Virtual environment not found at .venv
    echo.
    echo Run install.bat first.
    echo.
    pause
    exit /b 1
)

echo.
echo Launching JupyterLab in notebooks\
echo Close this window to stop the server.
echo.

".venv\Scripts\python.exe" -m jupyterlab --notebook-dir="%~dp0notebooks"
