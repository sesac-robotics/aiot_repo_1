#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT/service/main_service"
export GUI_EVENT_ENABLED=true
export GUI_EVENT_URL="${GUI_EVENT_URL:-http://127.0.0.1:8767}"
export GUI_EVENT_TOKEN_FILE="${GUI_EVENT_TOKEN_FILE:-$REPO_ROOT/ui/gui/Current/data/gui_event.token}"
if [ ! -x .venv/bin/python ]; then
    echo 'Create service/main_service/.venv and install its requirements.txt first.' >&2
    exit 1
fi
exec .venv/bin/python run.py "$@"
