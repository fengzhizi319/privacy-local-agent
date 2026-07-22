#!/usr/bin/env bash
# 启动前端测试控制台后端（默认 127.0.0.1:8080）
set -euo pipefail

cd "$(dirname "$0")"

HOST="${PRIVACY_CONSOLE_HOST:-127.0.0.1}"
PORT="${PRIVACY_CONSOLE_PORT:-8080}"

exec uvicorn app.main:app --host "$HOST" --port "$PORT" --reload
