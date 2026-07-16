# privacy-local-agent 生产安全运维手册

> Scope: P0 — TLS/mTLS、认证鉴权、速率限制的部署与运维。
> 对应 PRD/设计: `docs/production_security/prd.md`, `design.md`

---

## 1. 环境变量速查

### TLS

| 变量 | 默认值 | 说明 |
|---|---|---|
| `PRIVACY_TLS_ENABLED` | `false` | 是否启用 REST/gRPC TLS。 |
| `PRIVACY_TLS_CERT_FILE` | — | 服务器证书 PEM 路径。 |
| `PRIVACY_TLS_KEY_FILE` | — | 服务器私钥 PEM 路径。 |
| `PRIVACY_TLS_CA_FILE` | — | CA 证书 PEM 路径；`optional`/`require` 模式必需。 |
| `PRIVACY_TLS_CLIENT_AUTH` | `none` | 客户端认证模式：`none` / `optional` / `require`。 |
| `PRIVACY_TLS_KEY_PASSWORD` | — | 加密的私钥口令。 |

### 认证

| 变量 | 默认值 | 说明 |
|---|---|---|
| `PRIVACY_AUTH_ENABLED` | `false` | 是否启用认证鉴权。 |
| `PRIVACY_AUTH_INTERNAL_KEYS_JSON` | `{}` | 内部服务 API Key 映射。 |
| `PRIVACY_AUTH_EXTERNAL_KEYS_JSON` | `{}` | 外部服务 API Key 映射。 |
| `PRIVACY_AUTH_INTERNAL_MTLS_ENABLED` | `true` | gRPC 是否允许 mTLS 客户端作为内部服务。 |

JSON 格式示例：

```bash
PRIVACY_AUTH_INTERNAL_KEYS_JSON='{
  "sk-internal-abc": {"name": "secretpad", "scopes": ["*"]}
}'

PRIVACY_AUTH_EXTERNAL_KEYS_JSON='{
  "sk-external-xyz": {"name": "portal", "scopes": ["privacy:mask", "classification:read"]}
}'
```

### 速率限制

| 变量 | 默认值 | 说明 |
|---|---|---|
| `PRIVACY_RATE_LIMIT_ENABLED` | `false` | 是否启用限速。 |
| `PRIVACY_RATE_LIMIT_DEFAULT_RPS` | `10` | 默认每秒请求数。 |
| `PRIVACY_RATE_LIMIT_DEFAULT_BURST` | `20` | 默认突发容量。 |
| `PRIVACY_RATE_LIMIT_PER_ENDPOINT_JSON` | `{}` | 按接口覆盖限速。 |
| `PRIVACY_RATE_LIMIT_REDIS_URL` | — | 多副本时共享计数器，例 `redis://redis:6379/0`。 |

覆盖示例：

```bash
PRIVACY_RATE_LIMIT_PER_ENDPOINT_JSON='{
  "/v1/privacy/dp/count": {"rps": 2, "burst": 5},
  "DPCount": {"rps": 2, "burst": 5}
}'
```

### 健康检查

| 变量 | 默认值 | 说明 |
|---|---|---|
| `PRIVACY_HEALTH_NO_AUTH` | `true` | `/health` 与 `Health` 是否免认证。 |
| `PRIVACY_HEALTH_NO_RATE_LIMIT` | `true` | `/health` 与 `Health` 是否免限速。 |

---

## 2. 证书生成（自签名开发示例）

```bash
# 1. 生成 CA
openssl genrsa -out ca.key 2048
openssl req -x509 -new -nodes -key ca.key -sha256 -days 365 -out ca.crt \
  -subj "/CN=privacy-local-agent-ca"

# 2. 生成服务器证书
openssl genrsa -out server.key 2048
openssl req -new -key server.key -out server.csr \
  -subj "/CN=privacy-local-agent"
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -out server.crt -days 365 -sha256

# 3. 生成客户端证书（mTLS 用）
openssl genrsa -out client.key 2048
openssl req -new -key client.key -out client.csr \
  -subj "/CN=internal-client"
openssl x509 -req -in client.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -out client.crt -days 365 -sha256
```

---

## 3. 本地启动示例

### 仅 TLS

```bash
export PRIVACY_TLS_ENABLED=true
export PRIVACY_TLS_CERT_FILE=./certs/server.crt
export PRIVACY_TLS_KEY_FILE=./certs/server.key
python -m privacy_local_agent.server
```

REST: `https://127.0.0.1:8079`
gRPC: `127.0.0.1:50051`（需 gRPCs）

### TLS + mTLS + Auth + Rate Limit

```bash
export PRIVACY_TLS_ENABLED=true
export PRIVACY_TLS_CERT_FILE=./certs/server.crt
export PRIVACY_TLS_KEY_FILE=./certs/server.key
export PRIVACY_TLS_CA_FILE=./certs/ca.crt
export PRIVACY_TLS_CLIENT_AUTH=require

export PRIVACY_AUTH_ENABLED=true
export PRIVACY_AUTH_INTERNAL_KEYS_JSON='{"sk-internal":{"name":"secretpad","scopes":["*"]}}'
export PRIVACY_AUTH_EXTERNAL_KEYS_JSON='{"sk-external":{"name":"portal","scopes":["privacy:mask"]}}'

export PRIVACY_RATE_LIMIT_ENABLED=true
export PRIVACY_RATE_LIMIT_DEFAULT_RPS=10
export PRIVACY_RATE_LIMIT_DEFAULT_BURST=20
export PRIVACY_RATE_LIMIT_PER_ENDPOINT_JSON='{"/v1/privacy/dp/count":{"rps":2,"burst":5}}'

python -m privacy_local_agent.server
```

---

## 4. 调用示例

### REST（TLS + 外部 API Key）

```bash
curl --cacert certs/ca.crt \
  -H "Authorization: Bearer sk-external" \
  -X POST https://127.0.0.1:8079/v1/privacy/mask \
  -H "Content-Type: application/json" \
  -d '{"field_name":"mobile","value":"13812345678"}'
```

### REST（TLS + mTLS + 内部 API Key）

```bash
curl --cacert certs/ca.crt \
  --cert certs/client.crt --key certs/client.key \
  -H "Authorization: Bearer sk-internal" \
  -X POST https://127.0.0.1:8079/v1/privacy/dp/count \
  -H "Content-Type: application/json" \
  -d '{"values":[1,0,1],"params":{"epsilon":1.0}}'
```

### gRPC（Python 客户端，mTLS + 内部 token）

```python
import grpc
from privacy_local_agent import privacy_pb2, privacy_pb2_grpc

with open("certs/ca.crt", "rb") as f:
    ca = f.read()
with open("certs/client.crt", "rb") as f:
    client_cert = f.read()
with open("certs/client.key", "rb") as f:
    client_key = f.read()

creds = grpc.ssl_channel_credentials(
    root_certificates=ca,
    private_key=client_key,
    certificate_chain=client_cert,
)
with grpc.secure_channel("127.0.0.1:50051", creds) as channel:
    stub = privacy_pb2_grpc.PrivacyServiceStub(channel)
    resp = stub.Mask(
        privacy_pb2.MaskRequest(field_name="mobile", value="13812345678"),
        metadata=(("authorization", "Bearer sk-internal"),),
    )
    print(resp.result)
```

---

## 5. K8s 探针配置

```yaml
livenessProbe:
  httpGet:
    path: /health
    port: 8079
    scheme: HTTPS   # 若 TLS 开启
readinessProbe:
  httpGet:
    path: /health
    port: 8079
    scheme: HTTPS
```

保持 `PRIVACY_HEALTH_NO_AUTH=true`，探针无需携带 `Authorization`。

---

## 6. 常见问题

**Q: 开启 TLS 后本地 `curl http://...` 失败？**
A: 使用 `https://` 并指定 `--cacert`。

**Q: mTLS 模式下客户端没有证书？**
A: 服务端会拒绝握手；请为客户端生成受信 CA 签发的证书，并在调用时携带。

**Q: 多副本限速不生效？**
A: 默认使用进程内存计数器，副本间不共享。配置 `PRIVACY_RATE_LIMIT_REDIS_URL`。

**Q: 外部服务访问了越权接口返回什么？**
A: REST 返回 `403 Forbidden`，gRPC 返回 `PERMISSION_DENIED`。

**Q: 是否需要更新 gRPC proto？**
A: 本次 P0 不改 proto，认证通过 metadata 完成。
