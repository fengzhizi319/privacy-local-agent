#!/usr/bin/env bash
# 开发模式启动 Go gRPC 代理后端
set -euo pipefail

cd "$(dirname "$0")"

HOST="${PRIVACY_CONSOLE_HOST:-127.0.0.1}"
PORT="${PRIVACY_CONSOLE_PORT:-8081}"

export PRIVACY_CONSOLE_HOST="$HOST"
export PRIVACY_CONSOLE_PORT="$PORT"

mkdir -p bin
go build -o bin/backend-go ./cmd/server
exec ./bin/backend-go
