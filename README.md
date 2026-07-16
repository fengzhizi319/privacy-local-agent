# privacy-local-agent

> Python 本地隐私保护 Agent，提供 REST + gRPC 双协议 Sidecar 服务，用于无法直接嵌入 Java/Go SDK 的场景，或多语言统一接入。

## 快速开始

### 本地运行（开发）

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# REST 服务
python -m privacy_local_agent.main

# gRPC 服务
python -m privacy_local_agent.grpc_server

# 同时启动 REST + gRPC
python -m privacy_local_agent.server
```

默认端口：

| 协议 | 端口 | 说明 |
|---|---|---|
| REST | 8079 | FastAPI + Uvicorn |
| gRPC | 50051 | grpcio |

### Docker 运行

```bash
# core 镜像（默认推荐，不含 torch/transformers/onnxruntime）
docker build --target core -t privacy-local-agent:0.1.0 .
docker run -p 8079:8079 -p 50051:50051 privacy-local-agent:0.1.0

# ml 镜像（含完整本地分类模型依赖）
docker build --target ml -t privacy-local-agent:0.1.0-ml .
```

## 生产安全（可选）

生产环境建议开启 TLS、认证鉴权和速率限制。所有安全能力默认关闭，通过环境变量启用：

```bash
PRIVACY_TLS_ENABLED=true \
PRIVACY_TLS_CERT_FILE=./certs/server.crt \
PRIVACY_TLS_KEY_FILE=./certs/server.key \
PRIVACY_AUTH_ENABLED=true \
PRIVACY_AUTH_INTERNAL_KEYS_JSON='{"sk-internal":{"name":"secretpad","scopes":["*"]}}' \
PRIVACY_RATE_LIMIT_ENABLED=true \
python -m privacy_local_agent.server
```

详细配置、证书生成和调用示例请参考：

- [生产安全 PRD](./docs/production_security/prd.md)
- [生产安全设计文档](./docs/production_security/design.md)
- [生产安全运维手册](./docs/production_security/ops.md)

### 可观测性

- [Observability PRD](./docs/production_observability/prd.md)
- [Observability Design](./docs/production_observability/design.md)
- [Observability Ops](./docs/production_observability/ops.md)

### K8s / Helm 部署

- [Deployment PRD](./docs/deployment/prd.md)
- [Deployment Design](./docs/deployment/design.md)
- [Deployment Ops](./docs/deployment/ops.md)

## 能力概览

本项目将能力划分为两大类：

1. **处理原语（Processing Primitives）**：对数据进行直接变换或隐私保护计算。
2. **数据分类（Data Classification）**：识别数据敏感度等级，为后续处理原语或访问控制提供决策依据。

### 处理原语 / Processing Primitives

| 能力 | REST | gRPC | 本地 SDK |
|---|---|---|---|
| 数据脱敏（masking） | `POST /v1/privacy/mask` | `Mask` | `PrivacyService.mask` |
| 整记录脱敏 | `POST /v1/privacy/mask_record` | `MaskRecord` | `PrivacyService.mask_record` |
| HMAC 哈希 | `POST /v1/privacy/hash` | `Hash` | `PrivacyService.hash` |
| 差分隐私计数 | `POST /v1/privacy/dp/count` | `DPCount` | `PrivacyService.dp_count` |
| 差分隐私求和 | `POST /v1/privacy/dp/sum` | `DPSum` | `PrivacyService.dp_sum` |
| 差分隐私均值 | `POST /v1/privacy/dp/mean` | `DPMean` | `PrivacyService.dp_mean` |
| K-匿名 | `POST /v1/privacy/k_anonymize/record` | `KAnonymizeRecord` | `PrivacyService.k_anonymize_record` |
| 查询混淆 | `POST /v1/privacy/qol/obfuscate` | `ObfuscateQuery` | `PrivacyService.obfuscate_query` |
| 隐私预算查询 | `GET /v1/privacy/budget` | `Health` | `PrivacyService.budget_remaining` |

处理原语统一由 `privacy_local_agent.service.PrivacyService` 编排，
并通过 `privacy_local_agent.main`（REST）和 `privacy_local_agent.grpc_server`（gRPC）暴露。

#### REST 示例

```bash
curl -X POST http://127.0.0.1:8079/v1/privacy/mask \
  -H "Content-Type: application/json" \
  -d '{"field_name":"mobile","value":"13812345678","context":"doctor_query"}'
```

#### gRPC 示例

```python
import grpc
from privacy_local_agent import privacy_pb2, privacy_pb2_grpc

channel = grpc.insecure_channel("127.0.0.1:50051")
stub = privacy_pb2_grpc.PrivacyServiceStub(channel)
resp = stub.Mask(privacy_pb2.MaskRequest(field_name="mobile", value="13812345678", context="doctor_query"))
print(resp.result)
```

### 数据分类 / Data Classification

| 能力 | REST | gRPC | 本地 SDK |
|---|---|---|---|
| 字段级分类 | `POST /v1/privacy/classify/field` | `ClassifyField` | `ClassificationService.classify_field` |
| 记录级分类 | `POST /v1/privacy/classify/record` | `ClassifyRecord` | `ClassificationService.classify_record` |
| 表级分类 | `POST /v1/privacy/classify/table` | `ClassifyTable` | `ClassificationService.classify_table` |

数据分类拥有独立的服务层与路由层：

- 服务编排：`privacy_local_agent.classification_service.ClassificationService`
- REST 路由：`privacy_local_agent.classification_routes`
- gRPC 实现：`privacy_local_agent.classification_grpc.ClassificationGrpcServicer`
- 底层原语：`privacy_local_agent.privacy.classification.ClassificationAPI`

#### 本地 SDK

```python
from privacy_local_agent.classification_service import ClassificationService

service = ClassificationService()

# 字段级 / Field level
result = service.classify_field("id_card", "110101199001011237")
print(result["finalLevel"], result["tags"])

# 记录级 / Record level
result = service.classify_record({
    "id_card": "110101199001011237",
    "mobile": "13800138000",
    "diagnosis": "B21.1",
})
print(result["finalLevel"])

# 表级 / Table level
result = service.classify_table(
    schema=["id_card", "brca1_status", "diagnosis"],
    rows=[{
        "id_card": "110101199001011237",
        "brca1_status": "positive",
        "diagnosis": "C78.0",
    }],
)
print(result["finalLevel"])
```

#### REST

```bash
curl -X POST http://127.0.0.1:8079/v1/privacy/classify/field \
  -H "Content-Type: application/json" \
  -d '{"field_name":"id_card","value":"110101199001011237","params":{}}'

curl -X POST http://127.0.0.1:8079/v1/privacy/classify/table \
  -H "Content-Type: application/json" \
  -d '{
    "schema": ["id_card", "brca1_status", "diagnosis"],
    "rows": [{
      "id_card": "110101199001011237",
      "brca1_status": "positive",
      "diagnosis": "C78.0"
    }],
    "params": {}
  }'
```

#### gRPC

```python
import json
import grpc
from privacy_local_agent import privacy_pb2, privacy_pb2_grpc

channel = grpc.insecure_channel("127.0.0.1:50051")
stub = privacy_pb2_grpc.PrivacyServiceStub(channel)

resp = stub.ClassifyField(privacy_pb2.ClassifyFieldRequest(
    field_name="id_card",
    value="110101199001011237",
    params_json=json.dumps({}),
))
print(json.loads(resp.result_json))
```

## 运行测试

```bash
PYTHONPATH=. pytest tests -q
```

## 文档

### 处理原语

- [PRD 产品需求文档](./docs/prd.md)
- [Design 设计文档](./docs/design.md)
- [Implementation 实现文档](./docs/implementation.md)
- [Testing 测试文档](./docs/testing.md)
- [User Manual 使用手册](./docs/user-manual.md)

### 数据分类

- [PRD 产品需求文档](./docs/classification/prd.md)
- [Design 设计文档](./docs/classification/design.md)
- [Operations 运维文档](./docs/classification/ops.md)
- [Testing 测试文档](./docs/classification/testing.md)

### 生产安全

- [PRD 产品需求文档](./docs/production_security/prd.md)
- [Design 设计文档](./docs/production_security/design.md)
- [Operations 运维手册](./docs/production_security/ops.md)

### 可观测性

- [PRD 产品需求文档](./docs/production_observability/prd.md)
- [Design 设计文档](./docs/production_observability/design.md)
- [Operations 运维手册](./docs/production_observability/ops.md)

### K8s / Helm 部署

- [PRD 产品需求文档](./docs/deployment/prd.md)
- [Design 设计文档](./docs/deployment/design.md)
- [Operations 运维手册](./docs/deployment/ops.md)
