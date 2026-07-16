# 生产安全加固使用示例

## 1. 概述

本文档提供 TLS、API Key 认证、速率限制在 REST/gRPC 场景下的典型配置与调用示例。示例假设已按 [ops.md](./ops.md) 生成或挂载证书。

## 2. 环境变量配置示例

### 2.1 仅 TLS（无认证、不限速）

```bash
export PRIVACY_TLS_ENABLED=true
export PRIVACY_TLS_CERT_FILE=./certs/server.crt
export PRIVACY_TLS_KEY_FILE=./certs/server.key

python -m privacy_local_agent.server
```

### 2.2 TLS + mTLS + 认证 + 限速

```bash
export PRIVACY_TLS_ENABLED=true
export PRIVACY_TLS_CERT_FILE=./certs/server.crt
export PRIVACY_TLS_KEY_FILE=./certs/server.key
export PRIVACY_TLS_CA_FILE=./certs/ca.crt
export PRIVACY_TLS_CLIENT_AUTH=require

export PRIVACY_AUTH_ENABLED=true
export PRIVACY_AUTH_INTERNAL_KEYS_JSON='{"sk-internal":{"name":"secretpad","scopes":["*"]}}'
export PRIVACY_AUTH_EXTERNAL_KEYS_JSON='{"sk-external":{"name":"portal","scopes":["privacy:mask","classification:read"]}}'

export PRIVACY_RATE_LIMIT_ENABLED=true
export PRIVACY_RATE_LIMIT_DEFAULT_RPS=10
export PRIVACY_RATE_LIMIT_DEFAULT_BURST=20
export PRIVACY_RATE_LIMIT_PER_ENDPOINT_JSON='{"/v1/privacy/dp/count":{"rps":2,"burst":5}}'

python -m privacy_local_agent.server
```

## 3. REST 调用示例

### 3.1 仅服务端 TLS + 外部 API Key

```bash
curl --cacert certs/ca.crt \
  -H "Authorization: Bearer sk-external" \
  -X POST https://127.0.0.1:8079/v1/privacy/mask \
  -H "Content-Type: application/json" \
  -d '{"field_name":"mobile","value":"13812345678"}'
```

### 3.2 mTLS + 内部 API Key

```bash
curl --cacert certs/ca.crt \
  --cert certs/client.crt --key certs/client.key \
  -H "Authorization: Bearer sk-internal" \
  -X POST https://127.0.0.1:8079/v1/privacy/dp/count \
  -H "Content-Type: application/json" \
  -d '{"values":[1,0,1],"params":{"epsilon":1.0}}'
```

### 3.3 Python 客户端（httpx）

```python
import httpx

cert = "certs/ca.crt"
headers = {"Authorization": "Bearer sk-internal"}

with httpx.Client(verify=cert) as client:
    resp = client.post(
        "https://127.0.0.1:8079/v1/privacy/mask",
        headers=headers,
        json={"field_name": "mobile", "value": "13812345678"},
    )
    print(resp.status_code, resp.json())
```

### 3.4 mTLS Python 客户端（httpx）

```python
import httpx

client_cert = ("certs/client.crt", "certs/client.key")
with httpx.Client(
    verify="certs/ca.crt", cert=client_cert
) as client:
    resp = client.post(
        "https://127.0.0.1:8079/v1/privacy/dp/count",
        headers={"Authorization": "Bearer sk-internal"},
        json={"values": [1, 0, 1], "params": {"epsilon": 1.0}},
    )
    print(resp.status_code, resp.json())
```

## 4. gRPC 调用示例

### 4.1 仅服务端 TLS

```python
import grpc
from privacy_local_agent import privacy_pb2, privacy_pb2_grpc

with open("certs/ca.crt", "rb") as f:
    ca = f.read()

creds = grpc.ssl_channel_credentials(root_certificates=ca)
with grpc.secure_channel("127.0.0.1:50051", creds) as channel:
    stub = privacy_pb2_grpc.PrivacyServiceStub(channel)
    resp = stub.Health(privacy_pb2.HealthRequest())
    print(resp.status)
```

### 4.2 mTLS + 内部 API Key

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

## 5. 速率限制配置示例

### 5.1 默认限速

```bash
export PRIVACY_RATE_LIMIT_ENABLED=true
export PRIVACY_RATE_LIMIT_DEFAULT_RPS=10
export PRIVACY_RATE_LIMIT_DEFAULT_BURST=20
```

### 5.2 按接口覆盖

```bash
export PRIVACY_RATE_LIMIT_PER_ENDPOINT_JSON='{
  "/v1/privacy/mask": {"rps": 5, "burst": 10},
  "/v1/privacy/dp/count": {"rps": 2, "burst": 5},
  "Mask": {"rps": 5, "burst": 10}
}'
```

### 5.3 多副本 Redis 限速

```bash
export PRIVACY_RATE_LIMIT_ENABLED=true
export PRIVACY_RATE_LIMIT_REDIS_URL=redis://redis:6379/0
```

## 6. 健康检查

默认情况下 `/health` 与 `Health` RPC 免认证、不限速，便于 K8s 探针和负载均衡器使用。

```bash
# REST
curl https://127.0.0.1:8079/health --cacert certs/ca.crt
```

```python
# gRPC
import grpc
from privacy_local_agent import privacy_pb2, privacy_pb2_grpc

with open("certs/ca.crt", "rb") as f:
    ca = f.read()
creds = grpc.ssl_channel_credentials(root_certificates=ca)
with grpc.secure_channel("127.0.0.1:50051", creds) as channel:
    stub = privacy_pb2_grpc.PrivacyServiceStub(channel)
    resp = stub.Health(privacy_pb2.HealthRequest())
    print(resp.status)
```

## 7. 权限最小化实践

- 内部服务（如 SecretPad 后端）使用 `scopes: ["*"]`。
- 外部服务（如数据门户）仅授予必要 scope，例如：
  - 只读脱敏：`["privacy:mask"]`
  - 分类查询：`["classification:read"]`
- 禁止外部服务直接调用消耗隐私预算的接口（`privacy:dp`、`privacy:budget`）。
