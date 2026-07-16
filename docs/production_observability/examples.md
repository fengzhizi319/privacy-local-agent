# 可观测性模块使用示例

## 1. 概述

本文档提供 `privacy_local_agent.observability` 的典型使用示例，涵盖结构化日志配置、Prometheus 指标抓取以及 OpenTelemetry tracing 初始化。

## 2. 配置 JSON 日志

### 2.1 通过环境变量（推荐）

启动 REST 服务时设置 `PRIVACY_LOG_FORMAT=json`：

```bash
PRIVACY_LOG_FORMAT=json PRIVACY_LOG_LEVEL=INFO python -m privacy_local_agent.main
```

访问任意接口后，控制台将输出 JSON：

```json
{
  "timestamp": "2026-07-11T14:30:27.123Z",
  "level": "INFO",
  "logger": "privacy_local_agent.observability.middleware",
  "message": "POST /v1/privacy/mask 200 1.234ms identity=anonymous",
  "request_id": "a1b2c3d4e5f67890",
  "identity_name": "",
  "method": "POST",
  "path": "/v1/privacy/mask",
  "lineno": 86,
  "funcName": "dispatch"
}
```

### 2.2 通过代码配置

```python
from privacy_local_agent.observability import configure_logging, get_logger

configure_logging(log_level="INFO", json_format=True)
logger = get_logger(__name__)

logger.info("服务启动完成")
```

## 3. 抓取 `/metrics`

### 3.1 使用 curl

```bash
curl -s http://127.0.0.1:8079/metrics | grep privacy_requests_total
```

示例输出：

```text
# HELP privacy_requests_total Total number of REST/gRPC requests handled.
# TYPE privacy_requests_total counter
privacy_requests_total{method="POST",path="/v1/privacy/mask",status="200"} 1.0
```

### 3.2 使用 Python

```python
import requests

resp = requests.get("http://127.0.0.1:8079/metrics")
print(resp.status_code)
print(resp.text[:1000])
```

### 3.3 查询具体指标

```bash
# QPS（按 path 聚合）
curl -s http://127.0.0.1:8079/metrics | grep 'privacy_requests_total'

# 延迟分布
curl -s http://127.0.0.1:8079/metrics | grep 'privacy_request_duration_seconds_bucket'

# DP 查询统计
curl -s http://127.0.0.1:8079/metrics | grep 'privacy_dp_queries_total'

# 剩余预算
curl -s http://127.0.0.1:8079/metrics | grep 'privacy_budget_remaining'
```

## 4. 初始化 OpenTelemetry

### 4.1 通过环境变量（推荐）

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger-collector:4318/v1/traces
export OTEL_SERVICE_NAME=privacy-local-agent
python -m privacy_local_agent.main
```

> 需先安装可选依赖：`pip install -e ".[observability]"`

### 4.2 通过代码初始化

```python
from privacy_local_agent.observability import init_tracing, start_span

# 未设置 endpoint 时返回 NoOp tracer，不会报错
tracer = init_tracing(
    endpoint="http://localhost:4318/v1/traces",
    service_name="privacy-local-agent",
)

with start_span("process_request", attributes={"path": "/v1/privacy/mask"}) as span:
    # 执行业务逻辑
    pass
```

### 4.3 在未安装 opentelemetry 的环境中使用

```python
from privacy_local_agent.observability import init_tracing, start_span

# 即使未安装 opentelemetry，也能正常返回 no-op tracer
tracer = init_tracing()

with start_span("noop_span") as span:
    print("span:", span)  # None
```

## 5. 自定义业务指标

在业务代码中直接引入并更新预定义指标：

```python
from privacy_local_agent.observability import (
    DP_QUERIES_TOTAL,
    BUDGET_REMAINING,
    CLASSIFICATION_TOTAL,
    AUTH_DENIALS_TOTAL,
)

# DP 查询计数
DP_QUERIES_TOTAL.labels(mechanism="laplace", aggregation="count").inc()

# 更新剩余预算
BUDGET_REMAINING.labels(namespace="default", budget_type="epsilon").set(8.0)

# 分类结果计数
CLASSIFICATION_TOTAL.labels(final_level="sensitive", layer="rule").inc()

# 记录拒绝事件
AUTH_DENIALS_TOTAL.labels(reason="unauthenticated").inc()
```

## 6. 在测试中验证可观测性

```python
from fastapi.testclient import TestClient
from privacy_local_agent.main import app

client = TestClient(app)

# 触发请求
resp = client.post("/v1/privacy/mask", json={"field_name": "mobile", "value": "13812345678"})
assert resp.status_code == 200

# 验证 metrics
metrics = client.get("/metrics").text
assert 'privacy_requests_total{method="POST",path="/v1/privacy/mask",status="200"}' in metrics
```

## 7. 最佳实践

1. **生产环境使用 JSON 日志**：便于接入 ELK/Loki 等日志平台。
2. **统一使用 `get_logger(__name__)`**：确保所有日志共享同一 handler 与 formatter。
3. **不要随意重置 `_logging_configured`**：仅在测试场景下需要强制重新配置日志。
4. **Prometheus 指标标签值要稳定**：避免使用高基数值（如用户 ID、request_id）作为 label。
5. **OpenTelemetry 保持可选**：业务代码应兼容 NoOp tracer，不能假设 opentelemetry 已安装。
6. **`/metrics` 不经过 metrics 中间件**：无需担心 metrics 自引用导致请求数虚增。
