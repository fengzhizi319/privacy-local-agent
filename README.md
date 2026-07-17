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

## 可观测性（可选）

项目内置结构化日志、Prometheus `/metrics` 端点和可选 OpenTelemetry 链路追踪，详见：

- [Observability PRD](./docs/production_observability/prd.md)
- [Observability Design](./docs/production_observability/design.md)
- [Observability Ops](./docs/production_observability/ops.md)

## K8s / Helm 部署

项目提供 Helm Chart、Kustomize 和 Docker Compose 三种部署方式，详见：

- [Deployment PRD](./docs/deployment/prd.md)
- [Deployment Design](./docs/deployment/design.md)
- [Deployment Ops](./docs/deployment/ops.md)

## 网关 / 负载均衡（可选）

内置 REST + gRPC 反向代理，支持健康检查和加权轮询，详见：

- [Gateway PRD](./docs/gateway_balancer/prd.md)
- [Gateway Design](./docs/gateway_balancer/design.md)
- [Gateway Ops](./docs/gateway_balancer/ops.md)

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

## 构建与分发

### 构建 Python 包

```bash
# 安装构建工具
pip install build

# 构建 wheel 和 sdist（输出到 dist/）
python -m build

# 生成的文件
# dist/privacy_local_agent-0.1.0-py3-none-any.whl  (wheel)
# dist/privacy_local_agent-0.1.0.tar.gz            (源码包)
```

其他人安装：
```bash
pip install privacy_local_agent-0.1.0-py3-none-any.whl
```

### Docker 镜像

```bash
# 构建 core 镜像（推荐，不含 ML 依赖）
make docker-core

# 构建 ml 镜像（含 torch/transformers/onnxruntime）
make docker-ml

# 运行
docker run -p 8079:8079 -p 50051:50051 privacy-local-agent:0.1.0
```

### 可编辑安装（开发用）

```bash
pip install -e .
```

让当前目录成为可导入的包，适合开发调试。

## 文档

### 文档书 (Documentation Book)

项目提供基于 **MkDocs + Material** 的在线文档书，支持导航、搜索、暗色模式和 Mermaid 图表。

```bash
# 安装依赖
pip install mkdocs mkdocs-material mkdocs-minify-plugin

# 本地预览（热重载，浏览器自动打开 http://127.0.0.1:8000）
make docs-serve

# 构建静态站点（输出到 site/index.html，使用浏览器打开index.html即可）
make docs-build

# 清理构建产物
make docs-clean
```

> 构建完成后可通过 `cd site && python3 -m http.server 8000` 启动本地服务器预览。

### 处理原语

#### 数据脱敏 (Masking)
- [概述](./docs/masking/README.md)
- [Design 设计文档](./docs/masking/design.md)
- [PRD 产品需求文档](./docs/masking/prd.md)
- [Operations 运维文档](./docs/masking/ops.md)
- [Testing 测试文档](./docs/masking/testing.md)
- [API Reference](./docs/masking/api_reference.md)
- [Examples 示例](./docs/masking/examples.md)

#### 差分隐私 (Differential Privacy)
- [概述](./docs/dp/README.md)
- [Design 设计文档](./docs/dp/design.md)
- [PRD 产品需求文档](./docs/dp/prd.md)
- [Operations 运维文档](./docs/dp/ops.md)
- [Testing 测试文档](./docs/dp/testing.md)
- [API Reference](./docs/dp/api_reference.md)
- [Examples 示例](./docs/dp/examples.md)

#### K-匿名 (K-Anonymity)
- [概述](./docs/k_anonymity/README.md)
- [Design 设计文档](./docs/k_anonymity/design.md)
- [PRD 产品需求文档](./docs/k_anonymity/prd.md)
- [Operations 运维文档](./docs/k_anonymity/ops.md)
- [Testing 测试文档](./docs/k_anonymity/testing.md)
- [API Reference](./docs/k_anonymity/api_reference.md)
- [Examples 示例](./docs/k_anonymity/examples.md)

#### 查询混淆 (Query Obfuscation)
- [概述](./docs/qol/README.md)
- [Design 设计文档](./docs/qol/design.md)
- [PRD 产品需求文档](./docs/qol/prd.md)
- [Operations 运维文档](./docs/qol/ops.md)
- [Testing 测试文档](./docs/qol/testing.md)
- [API Reference](./docs/qol/api_reference.md)
- [Examples 示例](./docs/qol/examples.md)

### 数据分类

#### 分类引擎（规则 → NER → LLM 三层漏斗）
- [概述](./docs/classification/README.md)
- [PRD 产品需求文档](./docs/classification/prd.md)
- [Design 设计文档](./docs/classification/design.md)
- [Operations 运维文档](./docs/classification/ops.md)
- [Testing 测试文档](./docs/classification/testing.md)
- [API Reference](./docs/classification/api_reference.md)
- [Examples 示例](./docs/classification/examples.md)
- [Performance 性能基准](./docs/classification/performance.md)

#### LLM / VLM 分类层
- [概述](./docs/classification_llm/README.md)
- [PRD 产品需求文档](./docs/classification_llm/prd.md)
- [Design 设计文档](./docs/classification_llm/design.md)
- [Operations 运维文档](./docs/classification_llm/ops.md)
- [Testing 测试文档](./docs/classification_llm/testing.md)
- [API Reference](./docs/classification_llm/api_reference.md)
- [Examples 示例](./docs/classification_llm/examples.md)

#### NER 分类层
- [概述](./docs/classification_ner/README.md)
- [PRD 产品需求文档](./docs/classification_ner/prd.md)
- [Design 设计文档](./docs/classification_ner/design.md)
- [Operations 运维文档](./docs/classification_ner/ops.md)
- [Testing 测试文档](./docs/classification_ner/testing.md)
- [API Reference](./docs/classification_ner/api_reference.md)
- [Examples 示例](./docs/classification_ner/examples.md)

### 网关 / 负载均衡

- [概述](./docs/gateway_balancer/README.md)
- [PRD 产品需求文档](./docs/gateway_balancer/prd.md)
- [Design 设计文档](./docs/gateway_balancer/design.md)
- [Operations 运维文档](./docs/gateway_balancer/ops.md)
- [Testing 测试文档](./docs/gateway_balancer/testing.md)
- [API Reference](./docs/gateway_balancer/api_reference.md)
- [Examples 示例](./docs/gateway_balancer/examples.md)
- [Optimizations 优化指南](./docs/gateway_balancer/optimizations.md)

### 生产安全

- [概述](./docs/production_security/README.md)
- [PRD 产品需求文档](./docs/production_security/prd.md)
- [Design 设计文档](./docs/production_security/design.md)
- [Operations 运维手册](./docs/production_security/ops.md)
- [Testing 测试文档](./docs/production_security/testing.md)
- [API Reference](./docs/production_security/api_reference.md)
- [Examples 示例](./docs/production_security/examples.md)

### 可观测性

- [概述](./docs/production_observability/README.md)
- [PRD 产品需求文档](./docs/production_observability/prd.md)
- [Design 设计文档](./docs/production_observability/design.md)
- [Operations 运维手册](./docs/production_observability/ops.md)
- [Testing 测试文档](./docs/production_observability/testing.md)
- [API Reference](./docs/production_observability/api_reference.md)
- [Examples 示例](./docs/production_observability/examples.md)

### K8s / Helm 部署

- [概述](./docs/deployment/README.md)
- [PRD 产品需求文档](./docs/deployment/prd.md)
- [Design 设计文档](./docs/deployment/design.md)
- [Operations 运维手册](./docs/deployment/ops.md)
- [Testing 测试文档](./docs/deployment/testing.md)
- [Examples 示例](./docs/deployment/examples.md)

### 其他

- [个性化隐私配置文件](./docs/personalized_profiles.md)
- [生产改进建议](./docs/production_improvements.md)
