#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
if [ ! -f .env ]; then
    cp .env.example .env
    echo "[INFO] .env 생성. PC2_HOST를 확인하세요."
fi
exec python3 "$ROOT/pc4_udp_camera.py" "$@"
