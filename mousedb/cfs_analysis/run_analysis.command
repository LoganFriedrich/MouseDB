#!/usr/bin/env bash
# ============================================================================
#  CFS Analysis - macOS launcher
#  -----------------------------
#  Double-click in Finder to open JupyterLab pointed at notebooks/.
#  (On Linux, run from Terminal: ./run_analysis.command)
#
#  Prerequisite: install.sh has been run at least once.
#
#  If JupyterLab does not open, or a "kernel not found" error appears,
#  re-run install.sh. For other errors, see TROUBLESHOOTING.md.
# ============================================================================

set -e
cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
    echo "ERROR: Virtual environment not found at .venv/"
    echo
    echo "Run ./install.sh first."
    exit 1
fi

echo
echo "Launching JupyterLab in notebooks/"
echo "Close this window to stop the server."
echo

.venv/bin/python -m jupyterlab --notebook-dir="$(pwd)/notebooks"
