#!/usr/bin/env bash
# 一键启动 Go gRPC 代理控制台：同时启动 privacy_local_agent 和 Go gRPC 后端
# 用法：./frontend/start-go.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

AGENT_VENV="$PROJECT_ROOT/.venv"

AGENT_URL="http://127.0.0.1:8079"
CONSOLE_URL="http://127.0.0.1:8081"

if [[ ! -d "$AGENT_VENV" ]]; then
    echo "错误：未找到 agent 虚拟环境 $AGENT_VENV，请先安装项目依赖。"
    exit 1
fi

if [[ ! -d "$SCRIPT_DIR/backend-go" ]]; then
    echo "错误：未找到 Go 后端目录 $SCRIPT_DIR/backend-go"
    exit 1
fi

if ! command -v go >/dev/null 2>&1; then
    echo "错误：未找到 Go 工具链，请先安装 Go。"
    exit 1
fi

# 清理子进程
PIDS=()
cleanup() {
    echo ""
    echo "正在停止服务..."
    for pid in "${PIDS[@]}"; do
        kill "$pid" 2>/dev/null || true
    done
    wait 2>/dev/null || true
    echo "已停止。"
}
trap cleanup INT TERM EXIT

# 启动 privacy_local_agent
# 默认会同时启动 REST (8079) 和 gRPC (50051)，Go 后端通过 gRPC 调用 agent

echo "启动 privacy_local_agent (REST: $AGENT_URL, gRPC: 127.0.0.1:50051)..."
(
    source "$AGENT_VENV/bin/activate"
    cd "$PROJECT_ROOT"
    exec python -m privacy_local_agent.server
) &
PIDS+=("$!")

# 启动 Go gRPC 代理后端
echo "启动 Go gRPC 代理后端 (Console: $CONSOLE_URL)..."
(
    cd "$SCRIPT_DIR/backend-go"
    exec go run ./cmd/server
) &
PIDS+=("$!")

# 等待服务就绪
wait_for_service() {
    local url="$1"
    local name="$2"
    local max_attempts=30
    local attempt=0
    echo -n "等待 $name 就绪"
    while [[ $attempt -lt $max_attempts ]]; do
        if curl -s -o /dev/null -w "%{http_code}" "$url" | grep -q '^200$'; then
            echo " OK"
            return 0
        fi
        echo -n "."
        sleep 1
        attempt=$((attempt + 1))
    done
    echo " 超时"
    return 1
}

wait_for_service "$AGENT_URL/health" "privacy_local_agent"
wait_for_service "$CONSOLE_URL/api/health" "Go gRPC 代理后端"

echo ""
echo "======================================"
echo "Go gRPC 代理控制台已启动"
echo "Console UI:  $CONSOLE_URL"
echo "Agent REST:  $AGENT_URL"
echo "Agent gRPC:  127.0.0.1:50051"
echo "按 Ctrl+C 停止所有服务"
echo "======================================"

# 保持脚本运行
wait
