#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
GUI_PYTHON="${GUI_PYTHON:-../.venv/bin/python}"
if [ ! -x "$GUI_PYTHON" ]; then
    echo 'Set GUI_PYTHON to a Python with Current/requirements.txt installed, or create ui/gui/.venv.' >&2
    exit 1
fi
exec "$GUI_PYTHON" start_connected.py "$@"
