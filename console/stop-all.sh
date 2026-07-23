#!/usr/bin/env bash
# 停止由 ./console/start-all.sh 启动的服务
# 用法：./console/stop-all.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_DIR="$SCRIPT_DIR/.pids"

AGENT_PID_FILE="$PID_DIR/agent-all.pid"
PY_CONSOLE_PID_FILE="$PID_DIR/console-all.pid"
GO_CONSOLE_PID_FILE="$PID_DIR/console-go-all.pid"

stop_by_pid_file() {
    local file="$1"
    local name="$2"
    if [[ -f "$file" ]]; then
        local pid
        pid="$(cat "$file")"
        # Kill the recorded process and any children it may have spawned.
        # This is important for `go run` which starts a child binary.
        if kill "$pid" 2>/dev/null; then
            kill -9 $(pgrep -P "$pid" 2>/dev/null) 2>/dev/null || true
            echo "已停止 $name (PID: $pid)"
        else
            echo "$name (PID: $pid) 不存在或已停止"
        fi
        rm -f "$file"
    else
        echo "未找到 $name 的 PID 文件，可能未启动或已停止"
    fi
}

stop_by_pid_file "$AGENT_PID_FILE" "privacy_local_agent"
stop_by_pid_file "$PY_CONSOLE_PID_FILE" "Python REST 代理后端"
stop_by_pid_file "$GO_CONSOLE_PID_FILE" "Go gRPC 代理后端"

echo "所有由 start-all.sh 启动的服务已处理完毕。"
