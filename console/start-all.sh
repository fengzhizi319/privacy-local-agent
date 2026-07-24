#!/usr/bin/env bash
# 一键启动「双后端」隐私测试控制台：
#   同时启动 privacy_local_agent（REST + gRPC）、Python REST 代理后端（8080）
#   与 Go gRPC 代理后端（8081）。
#
# 启动后，前端顶部的 Backend Selector 可在两个后端间自由切换：
#   - Python REST (8080)：经 Python FastAPI 代理调用 agent REST 接口；
#   - Go gRPC    (8081)：经 Go 代理把请求转换为 gRPC 调用 agent。
#
# 用法：./console/start-all.sh [--rebuild]
#   --rebuild  强制重新编译前端、后端与 agent（即使构建产物已存在）

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

REBUILD=false
for arg in "$@"; do
    case "$arg" in
        --rebuild) REBUILD=true ;;
    esac
done

AGENT_VENV="$PROJECT_ROOT/.venv"
BACKEND_VENV="$SCRIPT_DIR/backend/.venv"

AGENT_URL="http://127.0.0.1:8079"
PY_CONSOLE_URL="http://127.0.0.1:8080"
GO_CONSOLE_URL="http://127.0.0.1:8081"

# ── 端口占用预检 ───────────────────────────────────────────────────────
check_port_available() {
    local port="$1"
    local name="$2"
    python3 - <<PY
import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    s.bind(("127.0.0.1", $port))
except OSError:
    print("错误：端口 " + str($port) + " 已被占用（$name），请先释放或修改环境变量。", file=sys.stderr)
    sys.exit(1)
finally:
    s.close()
PY
}

# ── 自动补全缺失的依赖 / 构建产物 ─────────────────────────────────────

# 1. Agent 虚拟环境：缺失或 --rebuild 时自动创建并安装项目依赖
if [[ ! -d "$AGENT_VENV" ]]; then
    echo "未找到 agent 虚拟环境，自动创建并安装依赖：$AGENT_VENV"
    python3 -m venv "$AGENT_VENV"
    (
        source "$AGENT_VENV/bin/activate"
        cd "$PROJECT_ROOT"
        pip install --upgrade pip >/dev/null
        pip install -e .
    )
    echo "agent 依赖安装完成。"
elif [[ "$REBUILD" == true ]]; then
    echo "--rebuild：重新安装 agent 依赖..."
    (
        source "$AGENT_VENV/bin/activate"
        cd "$PROJECT_ROOT"
        pip install -e .
    )
    echo "agent 依赖重装完成。"
fi

# 2. Python 控制台后端虚拟环境：缺失或 --rebuild 时自动创建并安装依赖
if [[ ! -d "$BACKEND_VENV" ]]; then
    echo "未找到 Python 后端虚拟环境，自动创建并安装依赖：$BACKEND_VENV"
    python3 -m venv "$BACKEND_VENV"
    (
        source "$BACKEND_VENV/bin/activate"
        pip install --upgrade pip >/dev/null
        pip install -r "$SCRIPT_DIR/backend/requirements.txt"
    )
    echo "Python 后端依赖安装完成。"
elif [[ "$REBUILD" == true ]]; then
    echo "--rebuild：重新安装 Python 后端依赖..."
    (
        source "$BACKEND_VENV/bin/activate"
        pip install -r "$SCRIPT_DIR/backend/requirements.txt"
    )
    echo "Python 后端依赖重装完成。"
fi

# 3. Go 后端目录与工具链检查
if [[ ! -d "$SCRIPT_DIR/backend-go" ]]; then
    echo "错误：未找到 Go 后端目录 $SCRIPT_DIR/backend-go"
    exit 1
fi

if ! command -v go >/dev/null 2>&1; then
    echo "错误：未找到 Go 工具链，请先安装 Go。"
    exit 1
fi

# 4. 前端构建产物：缺失或使用 --rebuild 时自动执行 install + build（两个后端均基于该产物提供 UI）
if [[ "$REBUILD" == true && -d "$SCRIPT_DIR/web/dist" ]]; then
    echo "--rebuild：删除旧的前端构建产物并重新构建..."
    rm -rf "$SCRIPT_DIR/web/dist"
fi
if [[ ! -d "$SCRIPT_DIR/web/dist" ]]; then
    echo "未找到前端构建产物，自动构建：$SCRIPT_DIR/web/dist"
    (
        cd "$SCRIPT_DIR/web"
        if command -v pnpm >/dev/null 2>&1; then
            pnpm install && pnpm build
        elif command -v npm >/dev/null 2>&1; then
            npm install && npm run build
        else
            echo "警告：未找到 pnpm/npm，跳过前端构建，后端将以 API 模式运行。"
        fi
    )
fi

if [[ ! -d "$SCRIPT_DIR/web/dist" ]]; then
    echo "警告：前端构建产物 $SCRIPT_DIR/web/dist 不存在，后端将以 API 模式运行。"
fi

# 5. 预编译 Go gRPC 代理后端二进制，编译失败时提前暴露错误
echo "编译 Go gRPC 代理后端..."
(cd "$SCRIPT_DIR/backend-go" && go build -o bin/backend-go ./cmd/server)
echo "Go 后端编译完成。"

AGENT_PID_FILE="$SCRIPT_DIR/.pids/agent-all.pid"
PY_CONSOLE_PID_FILE="$SCRIPT_DIR/.pids/console-all.pid"
GO_CONSOLE_PID_FILE="$SCRIPT_DIR/.pids/console-go-all.pid"

mkdir -p "$SCRIPT_DIR/.pids"

write_pid() {
    local file="$1"
    local pid="$2"
    echo "$pid" > "$file"
}

# 清理子进程
PIDS=()
cleanup() {
    echo ""
    echo "正在停止服务..."
    for pid in "${PIDS[@]}"; do
        kill "$pid" 2>/dev/null || true
    done
    wait 2>/dev/null || true
    rm -f "$AGENT_PID_FILE" "$PY_CONSOLE_PID_FILE" "$GO_CONSOLE_PID_FILE"
    echo "已停止。"
}
trap cleanup INT TERM EXIT

check_port_available 8079 "privacy_local_agent REST"
check_port_available 50051 "privacy_local_agent gRPC"
check_port_available 8080 "Python REST 代理后端"
check_port_available 8081 "Go gRPC 代理后端"

# 启动 privacy_local_agent（同时监听 REST 8079 与 gRPC 50051）
echo "启动 privacy_local_agent (REST: $AGENT_URL, gRPC: 127.0.0.1:50051)..."
(
    source "$AGENT_VENV/bin/activate"
    cd "$PROJECT_ROOT"
    exec python -m privacy_local_agent.server
) &
AGENT_PID=$!
PIDS+=("$AGENT_PID")
write_pid "$AGENT_PID_FILE" "$AGENT_PID"

# 启动 Python REST 代理后端
echo "启动 Python REST 代理后端 (Console: $PY_CONSOLE_URL)..."
(
    source "$BACKEND_VENV/bin/activate"
    cd "$SCRIPT_DIR/backend"
    exec uvicorn app.main:app --host 127.0.0.1 --port 8080
) &
PY_CONSOLE_PID=$!
PIDS+=("$PY_CONSOLE_PID")
write_pid "$PY_CONSOLE_PID_FILE" "$PY_CONSOLE_PID"

# 启动 Go gRPC 代理后端
echo "启动 Go gRPC 代理后端 (Console: $GO_CONSOLE_URL)..."
(
    cd "$SCRIPT_DIR/backend-go"
    exec ./bin/backend-go
) &
GO_CONSOLE_PID=$!
PIDS+=("$GO_CONSOLE_PID")
write_pid "$GO_CONSOLE_PID_FILE" "$GO_CONSOLE_PID"

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
wait_for_service "$PY_CONSOLE_URL/api/health" "Python REST 代理后端"
wait_for_service "$GO_CONSOLE_URL/api/health" "Go gRPC 代理后端"

echo ""
echo "======================================"
echo "双后端隐私测试控制台已启动"
echo "Agent REST:        $AGENT_URL"
echo "Agent gRPC:        127.0.0.1:50051"
echo "Python REST 后端:  $PY_CONSOLE_URL  (Console UI + API)"
echo "Go gRPC 后端:      $GO_CONSOLE_URL  (Console UI + API)"
echo ""
echo "打开任一 Console 地址，顶部 Backend Selector 可在两后端间切换。"
if [[ ! -d "$SCRIPT_DIR/web/dist" ]]; then
    echo ""
    echo "警告：前端尚未构建，Console 仅以 API 模式运行。"
    echo "请先构建前端：cd $SCRIPT_DIR/web && npm install && npm run build"
fi
echo "按 Ctrl+C 停止所有服务"
echo "======================================"

# 保持脚本运行
wait
