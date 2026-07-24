#!/usr/bin/env bash
# 一键启动 mTLS 模式的 Go gRPC 代理控制台。
#
# 与 start-go.sh 的区别：
#   - agent 的 gRPC 服务端启用 mTLS（PRIVACY_TLS_CLIENT_AUTH=require，要求客户端证书）
#   - Go 代理的 gRPC 客户端启用 mTLS（出示客户端证书并校验服务端证书）
#   - 若证书缺失，自动调用 backend-go/scripts/gen-certs.sh 生成一套自签名测试证书
#
# 用法：
#   ./console/start-go-mtls.sh [--rebuild]
#
# 说明：
#   本脚本面向本地测试/联调，使用自签名证书。生产环境请使用正式 CA 签发的证书，
#   并通过环境变量显式指定各证书路径（参见 backend-go/docs/ops.md）。

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
CERT_DIR="$SCRIPT_DIR/backend-go/certs"
GEN_CERTS="$SCRIPT_DIR/backend-go/scripts/gen-certs.sh"

# mTLS 模式下 Go 代理控制台仍为 HTTP（仅代理到 agent 的 gRPC 链路为 mTLS）
CONSOLE_URL="http://127.0.0.1:8081"
AGENT_GRPC_ADDR="127.0.0.1:50051"

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

# ── 1. 准备证书 ───────────────────────────────────────────────────────
if [[ ! -f "$CERT_DIR/ca.crt" || ! -f "$CERT_DIR/server.crt" || ! -f "$CERT_DIR/client.crt" ]]; then
    echo "未找到 mTLS 证书，自动生成测试证书链..."
    bash "$GEN_CERTS" "$CERT_DIR"
else
    echo "复用已有 mTLS 证书：$CERT_DIR"
fi

# ── 2. 准备 agent 虚拟环境 ────────────────────────────────────────────
if [[ ! -d "$AGENT_VENV" ]]; then
    echo "未找到 agent 虚拟环境，自动创建并安装依赖：$AGENT_VENV"
    python3 -m venv "$AGENT_VENV"
    (
        source "$AGENT_VENV/bin/activate"
        cd "$PROJECT_ROOT"
        pip install --upgrade pip >/dev/null
        pip install -e .
    )
fi

if ! command -v go >/dev/null 2>&1; then
    echo "错误：未找到 Go 工具链，请先安装 Go。"
    exit 1
fi

# ── 3. 编译 Go 代理 ───────────────────────────────────────────────────
echo "编译 Go gRPC 代理后端..."
(cd "$SCRIPT_DIR/backend-go" && go build -o bin/backend-go ./cmd/server)

# ── 4. 启动服务 ───────────────────────────────────────────────────────
mkdir -p "$SCRIPT_DIR/.pids"
AGENT_PID_FILE="$SCRIPT_DIR/.pids/agent-go-mtls.pid"
CONSOLE_PID_FILE="$SCRIPT_DIR/.pids/console-go-mtls.pid"

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
check_port_available 50051 "privacy_local_agent gRPC (mTLS)"
check_port_available 8081 "Go gRPC 代理后端"

# 4.1 启动 agent（gRPC 服务端启用 mTLS，要求客户端证书）
echo "启动 privacy_local_agent (gRPC mTLS: $AGENT_GRPC_ADDR, client_auth=require)..."
(
    source "$AGENT_VENV/bin/activate"
    cd "$PROJECT_ROOT"
    export PRIVACY_TLS_ENABLED=true
    export PRIVACY_TLS_CERT_FILE="$CERT_DIR/server.crt"
    export PRIVACY_TLS_KEY_FILE="$CERT_DIR/server.key"
    export PRIVACY_TLS_CA_FILE="$CERT_DIR/ca.crt"
    export PRIVACY_TLS_CLIENT_AUTH=require
    exec python -m privacy_local_agent.server
) &
AGENT_PID=$!
PIDS+=("$AGENT_PID")
echo "$AGENT_PID" > "$AGENT_PID_FILE"

# 4.2 启动 Go 代理（gRPC 客户端启用 mTLS，出示客户端证书）
echo "启动 Go gRPC 代理后端 (mTLS -> $AGENT_GRPC_ADDR, Console: $CONSOLE_URL)..."
(
    cd "$SCRIPT_DIR/backend-go"
    export PRIVACY_AGENT_TLS_ENABLED=true
    export PRIVACY_AGENT_TLS_CERT_FILE="$CERT_DIR/client.crt"
    export PRIVACY_AGENT_TLS_KEY_FILE="$CERT_DIR/client.key"
    export PRIVACY_AGENT_TLS_CA_FILE="$CERT_DIR/ca.crt"
    # 连接目标为 127.0.0.1，但证书 SAN 含 localhost，覆盖校验主机名
    export PRIVACY_AGENT_TLS_SERVER_NAME=localhost
    exec ./bin/backend-go
) &
CONSOLE_PID=$!
PIDS+=("$CONSOLE_PID")
echo "$CONSOLE_PID" > "$CONSOLE_PID_FILE"

# ── 5. 等待就绪 ───────────────────────────────────────────────────────
echo -n "等待 Go 代理就绪"
for _ in $(seq 1 30); do
    if curl -s -o /dev/null -w "%{http_code}" "$CONSOLE_URL/api/health" | grep -q '^200$'; then
        echo " OK"
        break
    fi
    echo -n "."
    sleep 1
done

echo ""
echo "======================================"
echo "Go gRPC 代理控制台已启动（mTLS 模式）"
echo "Agent gRPC:  $AGENT_GRPC_ADDR (mTLS, 要求客户端证书)"
echo "Console UI:  $CONSOLE_URL (Go 后端 -> agent 全程 mTLS)"
echo "证书目录:    $CERT_DIR"
echo "按 Ctrl+C 停止所有服务"
echo "======================================"

wait
