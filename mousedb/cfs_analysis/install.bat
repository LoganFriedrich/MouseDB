@echo off
REM ============================================================================
REM  CFS Analysis - Windows installer
REM  ---------------------------------
REM  Double-click this file (or run it in a Command Prompt from the cfs_analysis/
REM  folder) to set up the Python environment for the analysis notebooks.
REM
REM  What this does:
REM    1. Checks that Python is installed and version 3.11+.
REM    2. Creates a virtual environment in .venv\ if one doesn't exist.
REM    3. Installs the required Python packages into that environment.
REM    4. Registers a Jupyter kernel called "Python (cfs_analysis)" so the
REM       notebooks can find the right Python.
REM
REM  You only need to run this ONCE per folder / per computer. After that,
REM  use run_analysis.bat to launch Jupyter.
REM
REM  If anything fails, open TROUBLESHOOTING.md in this folder.
REM ============================================================================

setlocal enabledelayedexpansion

cd /d "%~dp0"

echo.
echo === CFS Analysis installer ===
echo.

REM Step 1: make sure Python is available
where python >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found on PATH.
    echo.
    echo Install Python 3.11 or newer from https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during install.
    echo.
    pause
    exit /b 1
)

REM Step 2: create the virtual environment if missing
if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment in .venv ...
    python -m venv .venv
    if errorlevel 1 (
        echo ERROR: Failed to create virtual environment.
        pause
        exit /b 1
    )
) else (
    echo Virtual environment already exists in .venv
)

REM Step 3: install dependencies into the venv
echo.
echo Installing Python packages ^(this may take a few minutes^) ...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -e .
if errorlevel 1 (
    echo ERROR: Package installation failed.
    pause
    exit /b 1
)

REM Step 4: register the Jupyter kernel
echo.
echo Registering Jupyter kernel "Python (cfs_analysis)" ...
".venv\Scripts\python.exe" -m ipykernel install --user --name cfs_analysis --display-name "Python (cfs_analysis)"
if errorlevel 1 (
    echo ERROR: Kernel registration failed.
    pause
    exit /b 1
)

echo.
echo === Install complete ===
echo.
echo Next step: double-click run_analysis.bat to launch Jupyter.
echo.
pause
