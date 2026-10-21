#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cd "$ROOT"

# Compose reads .env automatically. Do not source it as shell code.
if [ ! -f "$ROOT/.env" ]; then
    if [ -f "$ROOT/.env.example" ]; then
        cp "$ROOT/.env.example" "$ROOT/.env"
        chmod 600 "$ROOT/.env"
        echo "[INFO] .env 생성 완료. DEVICE 토큰을 설정하세요."
    else
        echo "[ERROR] .env 또는 .env.example 파일이 필요합니다."
        exit 1
    fi
fi

# 실행에 필요한 폴더 생성
mkdir -p data/captures data/processed data/output data/outbox data/rejected data/responses

# 웹캠 가상환경 확인
if [ ! -f "$ROOT/webcam/.venv/bin/python" ]; then
    echo "[ERROR] 웹캠 가상환경이 없습니다."
    exit 1
fi

# Docker에서 InsightFace 서비스 실행
echo "[1/2] InsightFace Docker 실행"
docker compose up -d --build insightface

# Docker 컨테이너 상태 확인
if [ "$(docker compose ps --status running -q insightface)" = "" ]; then
    echo "[ERROR] InsightFace 컨테이너가 실행되지 않았습니다."
    docker compose logs --tail=30 insightface
    exit 1
fi

# 웹캠 실행
echo "[2/2] 웹캠 실행"
echo "[INFO] 캡처가 발생하면 Docker에서 자동으로 처리합니다."

cd "$ROOT/webcam"
exec "$ROOT/webcam/.venv/bin/python" detect_yolov3.py "$@"
