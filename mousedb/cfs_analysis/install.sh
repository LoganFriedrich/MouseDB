#!/usr/bin/env bash
# ============================================================================
#  CFS Analysis - macOS/Linux installer
#  ------------------------------------
#  Run from Terminal: cd to the cfs_analysis folder, then `./install.sh`
#
#  What this does:
#    1. Checks that Python 3.11+ is available.
#    2. Creates a virtual environment in .venv/ if one doesn't exist.
#    3. Installs the required Python packages into that environment.
#    4. Registers a Jupyter kernel called "Python (cfs_analysis)".
#
#  Run once per folder per computer. After that, use run_analysis.command.
#
#  If anything fails, open TROUBLESHOOTING.md.
# ============================================================================

set -e

cd "$(dirname "$0")"

echo
echo "=== CFS Analysis installer ==="
echo

# Step 1: check for Python 3.11+
if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: python3 not found."
    echo "Install Python 3.11 or newer from https://www.python.org/downloads/"
    exit 1
fi

PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')
if ! python3 -c 'import sys; exit(0 if sys.version_info >= (3, 11) else 1)'; then
    echo "ERROR: Python 3.11+ required, found $PY_VERSION."
    exit 1
fi
echo "Python $PY_VERSION found."

# Step 2: create virtual environment
if [ ! -x ".venv/bin/python" ]; then
    echo "Creating virtual environment in .venv/ ..."
    python3 -m venv .venv
else
    echo "Virtual environment already exists in .venv/"
fi

# Step 3: install dependencies
echo
echo "Installing Python packages (this may take a few minutes) ..."
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e .

# Step 4: register kernel
echo
echo 'Registering Jupyter kernel "Python (cfs_analysis)" ...'
.venv/bin/python -m ipykernel install --user --name cfs_analysis --display-name "Python (cfs_analysis)"

echo
echo "=== Install complete ==="
echo
echo "Next step: run ./run_analysis.command (double-click on macOS)"
echo
