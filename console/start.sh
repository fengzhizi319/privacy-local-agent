#!/usr/bin/env bash
# 一键启动隐私测试控制台：同时启动 privacy_local_agent 和前端代理后端
# 用法：./console/start.sh [--rebuild]
#   --rebuild  强制重新编译前端、后端与 agent（即使构建产物已存在）
#
# 设计目标：
# - 尽量“开箱即用”，减少首次启动时的手工准备步骤
# - 在依赖缺失时优雅降级，而不是直接失败
# - 启动后先等待关键健康检查通过，再提示用户访问地址

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
CONSOLE_URL="http://127.0.0.1:8080"

# ── 端口占用预检（冲突时自动诊断并提供 kill 选项）────────────────────
check_port_available() {
    local port="$1"
    local name="$2"

    # 快速检测端口是否可用
    if python3 -c "
import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    s.bind(('127.0.0.1', $port))
except OSError:
    sys.exit(1)
finally:
    s.close()
" 2>/dev/null; then
        return 0
    fi

    # 端口被占用 —— 诊断占用进程
    echo ""
    echo "⚠️  端口 $port 已被占用（$name）"
    echo "────────────────────────────────────────"

    local pids=""
    if command -v lsof >/dev/null 2>&1; then
        echo "诊断信息（lsof -i :$port）："
        lsof -i :"$port" 2>/dev/null || true
        echo ""
        pids=$(lsof -t -i :"$port" 2>/dev/null | sort -u | tr '\n' ' ')
    elif command -v fuser >/dev/null 2>&1; then
        pids=$(fuser "$port"/tcp 2>/dev/null | tr -s ' ')
        echo "占用进程 PID：$pids"
        echo ""
    fi

    if [[ -z "$pids" ]]; then
        echo "错误：无法定位占用端口 $port 的进程，请手动排查："
        echo "  lsof -i :$port"
        echo "  或 ss -tlnp | grep $port"
        exit 1
    fi

    echo "占用端口 $port 的进程 PID：$pids"
    echo ""
    read -rp "是否自动终止上述进程以释放端口？[y/N] " answer
    case "$answer" in
        [yY]|[yY][eE][sS])
            for pid in $pids; do
                echo "  → kill -9 $pid"
                kill -9 "$pid" 2>/dev/null || true
            done
            sleep 1
            # 再次验证端口已释放
            if python3 -c "
import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    s.bind(('127.0.0.1', $port))
except OSError:
    sys.exit(1)
finally:
    s.close()
" 2>/dev/null; then
                echo "✅ 端口 $port 已释放"
            else
                echo "错误：端口 $port 仍被占用，请手动排查。"
                exit 1
            fi
            ;;
        *)
            echo "已取消。请手动释放端口 $port 后重试："
            echo "  kill -9 $pids"
            exit 1
            ;;
    esac
}

# ── 自动补全缺失的依赖 / 构建产物 ─────────────────────────────────────

# 1. Agent 虚拟环境：缺失或 --rebuild 时自动创建并安装项目依赖
# 这里使用项目根目录的 `.venv`，让控制台脚本和主包共享同一套运行时依赖，
# 避免重复安装和版本漂移。
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

# 2. 控制台后端虚拟环境：缺失或 --rebuild 时自动创建并安装依赖
# 控制台后端保留独立虚拟环境，方便它和主 agent 分开升级、调试和回滚。
if [[ ! -d "$BACKEND_VENV" ]]; then
    echo "未找到后端虚拟环境，自动创建并安装依赖：$BACKEND_VENV"
    python3 -m venv "$BACKEND_VENV"
    (
        source "$BACKEND_VENV/bin/activate"
        pip install --upgrade pip >/dev/null
        pip install -r "$SCRIPT_DIR/backend/requirements.txt"
    )
    echo "后端依赖安装完成。"
elif [[ "$REBUILD" == true ]]; then
    echo "--rebuild：重新安装控制台后端依赖..."
    (
        source "$BACKEND_VENV/bin/activate"
        pip install -r "$SCRIPT_DIR/backend/requirements.txt"
    )
    echo "后端依赖重装完成。"
fi

# 3. 前端构建产物：缺失或 --rebuild 时自动执行 install + build
# 前端依赖优先使用 pnpm，其次回退到 npm；如果两者都不存在，则仅保留 API 模式，
# 这样即使本机没有完整 Node 环境，也能继续调试后端。
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
            echo "警告：未找到 pnpm/npm，跳过前端构建，控制台将以 API 模式运行。"
        fi
    )
fi

if [[ ! -d "$SCRIPT_DIR/web/dist" ]]; then
    echo "警告：前端构建产物 $SCRIPT_DIR/web/dist 不存在，后端将以 API 模式运行。"
    echo "如需完整 UI，请执行：cd $SCRIPT_DIR/web && corepack pnpm install && corepack pnpm build"
fi

AGENT_PID_FILE="$SCRIPT_DIR/.pids/agent.pid"
CONSOLE_PID_FILE="$SCRIPT_DIR/.pids/console.pid"

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
    rm -f "$AGENT_PID_FILE" "$CONSOLE_PID_FILE"
    echo "已停止。"
}
trap cleanup INT TERM EXIT

check_port_available 8079 "privacy_local_agent REST"
check_port_available 8080 "Python REST 代理后端"

# 启动 privacy_local_agent
# 先启动主 agent，再启动控制台后端；后者会通过 REST 访问前者，所以顺序不能反过来。
echo "启动 privacy_local_agent (REST: $AGENT_URL)..."
(
    source "$AGENT_VENV/bin/activate"
    cd "$PROJECT_ROOT"
    exec python -m privacy_local_agent.server
) &
AGENT_PID=$!
PIDS+=("$AGENT_PID")
write_pid "$AGENT_PID_FILE" "$AGENT_PID"

# 启动前端后端
# 控制台后端提供给 Web UI 和 smoke test 使用的 API，因此必须在提示页面前完成启动。
echo "启动测试控制台后端 (Console: $CONSOLE_URL)..."
(
    source "$BACKEND_VENV/bin/activate"
    cd "$SCRIPT_DIR/backend"
    exec uvicorn app.main:app --host 127.0.0.1 --port 8080
) &
CONSOLE_PID=$!
PIDS+=("$CONSOLE_PID")
write_pid "$CONSOLE_PID_FILE" "$CONSOLE_PID"

# 等待服务就绪
# 这里轮询 health 接口，避免脚本“进程已启动但服务还没 ready”时误导用户。
# 只有主 agent 和控制台后端都通过健康检查后，才会打印可访问地址。
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
if [[ ! -d "$SCRIPT_DIR/web/dist" ]]; then
    echo ""
    echo "注意：前端尚未构建，访问 $CONSOLE_URL 将显示 {\"detail\":\"Not Found\"}。"
    echo "请先构建前端：cd $SCRIPT_DIR/web && corepack pnpm install && corepack pnpm build"
    echo "构建完成后重新执行 ./console/start.sh 即可打开 Console UI。"
fi
echo "按 Ctrl+C 停止所有服务"
echo "======================================"

# 保持脚本运行
wait
