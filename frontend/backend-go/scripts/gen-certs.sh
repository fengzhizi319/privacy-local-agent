#!/usr/bin/env bash
# 生成 mTLS 测试证书链（CA + 服务端证书 + 客户端证书）。
#
# 用途：
#   为 Go gRPC 代理（客户端）与 privacy-local-agent（gRPC 服务端）之间的
#   mTLS 双向认证生成一套自签名测试证书，方便本地联调与集成测试。
#
# 生成的文件（默认输出到 frontend/backend-go/certs/）：
#   ca.crt / ca.key         受信任根 CA（签发服务端与客户端证书）
#   server.crt / server.key 服务端证书（Python agent，SAN: localhost/127.0.0.1）
#   client.crt / client.key 客户端证书（Go 代理，EKU: clientAuth）
#
# 用法：
#   ./scripts/gen-certs.sh [输出目录]
#   CERT_DAYS=730 ./scripts/gen-certs.sh        # 自定义有效期（默认 365 天）
#
# 注意：生成的证书仅用于测试/开发，请勿用于生产环境，也不要提交到 git。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="${1:-$SCRIPT_DIR/../certs}"
DAYS="${CERT_DAYS:-365}"
SERVER_CN="${SERVER_CN:-localhost}"

if ! command -v openssl >/dev/null 2>&1; then
    echo "错误：未找到 openssl，请先安装。" >&2
    exit 1
fi

mkdir -p "$OUT_DIR"
cd "$OUT_DIR"

echo ">> 输出目录: $OUT_DIR"
echo ">> 有效期:   ${DAYS} 天"
echo ">> 服务端 CN: ${SERVER_CN}"

# ── 1. 根 CA ──────────────────────────────────────────────────────────
echo ">> [1/3] 生成根 CA..."
openssl genrsa -out ca.key 4096
openssl req -x509 -new -nodes -key ca.key -sha256 -days "$DAYS" \
    -subj "/CN=privacy-local-agent-test-ca" \
    -out ca.crt

# ── 2. 服务端证书（Python agent gRPC 服务端）──────────────────────────
echo ">> [2/3] 生成服务端证书（含 localhost/127.0.0.1 SAN）..."
openssl genrsa -out server.key 2048
openssl req -new -key server.key -subj "/CN=${SERVER_CN}" -out server.csr
cat > server.ext <<EOF
authorityKeyIdentifier=keyid,issuer
basicConstraints=CA:FALSE
keyUsage=digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=@alt_names

[alt_names]
DNS.1=localhost
IP.1=127.0.0.1
EOF
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
    -out server.crt -days "$DAYS" -sha256 -extfile server.ext

# ── 3. 客户端证书（Go 代理 gRPC 客户端）──────────────────────────────
echo ">> [3/3] 生成客户端证书（EKU: clientAuth）..."
openssl genrsa -out client.key 2048
openssl req -new -key client.key -subj "/CN=privacy-console-go-client" -out client.csr
cat > client.ext <<EOF
authorityKeyIdentifier=keyid,issuer
basicConstraints=CA:FALSE
keyUsage=digitalSignature,keyEncipherment
extendedKeyUsage=clientAuth
EOF
openssl x509 -req -in client.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
    -out client.crt -days "$DAYS" -sha256 -extfile client.ext

# ── 清理中间文件并收紧私钥权限 ────────────────────────────────────────
rm -f server.csr client.csr server.ext client.ext ca.srl
chmod 600 ./*.key

echo ""
echo ">> 完成，生成文件："
ls -1 "$OUT_DIR"
echo ""
echo ">> Python agent 端（服务端）启用 mTLS："
echo "   PRIVACY_TLS_ENABLED=true \\"
echo "   PRIVACY_TLS_CERT_FILE=$OUT_DIR/server.crt \\"
echo "   PRIVACY_TLS_KEY_FILE=$OUT_DIR/server.key \\"
echo "   PRIVACY_TLS_CA_FILE=$OUT_DIR/ca.crt \\"
echo "   PRIVACY_TLS_CLIENT_AUTH=require"
echo ""
echo ">> Go 代理端（客户端）启用 mTLS："
echo "   PRIVACY_AGENT_TLS_ENABLED=true \\"
echo "   PRIVACY_AGENT_TLS_CERT_FILE=$OUT_DIR/client.crt \\"
echo "   PRIVACY_AGENT_TLS_KEY_FILE=$OUT_DIR/client.key \\"
echo "   PRIVACY_AGENT_TLS_CA_FILE=$OUT_DIR/ca.crt \\"
echo "   PRIVACY_AGENT_TLS_SERVER_NAME=localhost"
