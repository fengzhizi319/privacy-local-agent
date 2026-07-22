#!/usr/bin/env bash
# 一键启动隐私测试控制台：同时启动 privacy_local_agent 和前端代理后端
# 用法：./frontend/start.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

AGENT_VENV="$PROJECT_ROOT/.venv"
BACKEND_VENV="$SCRIPT_DIR/backend/.venv"

AGENT_URL="http://127.0.0.1:8079"
CONSOLE_URL="http://127.0.0.1:8080"

if [[ ! -d "$AGENT_VENV" ]]; then
    echo "错误：未找到 agent 虚拟环境 $AGENT_VENV，请先安装项目依赖。"
    exit 1
fi

if [[ ! -d "$BACKEND_VENV" ]]; then
    echo "错误：未找到后端虚拟环境 $BACKEND_VENV，请先安装 frontend/backend 依赖。"
    exit 1
fi

if [[ ! -d "$SCRIPT_DIR/web/dist" ]]; then
    echo "警告：前端构建产物 $SCRIPT_DIR/web/dist 不存在，后端将以 API 模式运行。"
    echo "如需完整 UI，请执行：cd $SCRIPT_DIR/web && corepack pnpm install && corepack pnpm build"
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
echo "启动 privacy_local_agent (REST: $AGENT_URL)..."
(
    source "$AGENT_VENV/bin/activate"
    cd "$PROJECT_ROOT"
    exec python -m privacy_local_agent.server
) &
PIDS+=("$!")

# 启动前端后端
echo "启动测试控制台后端 (Console: $CONSOLE_URL)..."
(
    source "$BACKEND_VENV/bin/activate"
    cd "$SCRIPT_DIR/backend"
    exec uvicorn app.main:app --host 127.0.0.1 --port 8080
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
        if curl -s "$url" >/dev/null 2>&1; then
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
wait_for_service "$CONSOLE_URL/api/health" "测试控制台后端"

echo ""
echo "======================================"
echo "隐私测试控制台已启动"
echo "Agent REST:  $AGENT_URL"
echo "Console UI:  $CONSOLE_URL"
echo "按 Ctrl+C 停止所有服务"
echo "======================================"

# 保持脚本运行
wait
