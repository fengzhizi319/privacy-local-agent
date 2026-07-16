# 可观测性设计文档

> 对应 PRD: `docs/production_observability/prd.md`

## 1. 概述

本文档定义 `privacy-local-agent` 可观测性模块的技术架构、设计原理与实现细节。该模块为生产环境提供结构化日志、Prometheus 指标与分布式追踪能力。

## 2. 设计目标

- 提供结构化日志（JSON），包含 request_id、接口、调用者身份、耗时等字段。
- 在 REST 端口暴露 `/metrics` endpoint，输出 Prometheus exposition 格式指标。
- 支持可选的 OpenTelemetry OTLP 导出，便于在微服务体系中定位延迟。
- 默认对本地开发影响最小：日志默认文本、`/metrics` 默认开启、tracing 默认关闭。

## 3. 架构设计

```text
                Request
                   │
    ┌──────────────┼──────────────┐
    ▼              ▼              ▼
 REST Router   gRPC Interceptor  /metrics
    │              │              │
    ▼              ▼              ▼
RequestContext  RequestContext  prometheus-client
    │              │
    ▼              ▼
Structured Log   Structured Log
    │
    ▼
OpenTelemetry (optional)
```

可观测层贯穿 REST 与 gRPC：

- `observability/context.py`：通过 `contextvars` 维护请求上下文（request_id、identity 等）。
- `observability/logging_config.py`：统一配置 root logger 与 JSON formatter。
- `observability/middleware.py`：FastAPI middleware + gRPC interceptor，负责生成/读取 `x-request-id`、记录 access log、更新 Prometheus metrics、打印审计日志。
- `observability/metrics.py`：集中定义所有指标。
- `observability/tracing.py`：可选 OpenTelemetry 初始化与 span helper。

## 4. 日志设计

### 4.1 配置

```python
def configure_logging(
    log_level: str = "INFO",
    json_format: bool = False,
    service_name: str = "privacy-local-agent",
) -> None
```

- 调用 `logging.basicConfig` 配置 root handler。
- `json_format=False`：文本格式。
- `json_format=True`：使用 `pythonjsonlogger.jsonlogger.JsonFormatter`。

### 4.2 上下文字段

自定义 `ContextFilter` 从 `contextvars` 读取当前 `RequestContext`，注入每条日志：

```python
class ContextFilter(logging.Filter):
    def filter(self, record):
        ctx = request_context.get()
        record.request_id = ctx.request_id if ctx else ""
        record.identity_name = ctx.identity_name if ctx else ""
        record.method = ctx.method if ctx else ""
        record.path = ctx.path if ctx else ""
        return True
```

### 4.3 统一 logger 入口

```python
from privacy_local_agent.observability import get_logger
logger = get_logger(__name__)
```

## 5. Metrics 设计

使用 `prometheus-client` 的默认 registry。

| 指标名 | 类型 | labels | 说明 |
|---|---|---|---|
| `privacy_requests_total` | Counter | method, path, status | REST/gRPC 请求总数 |
| `privacy_request_duration_seconds` | Histogram | method, path | 请求处理耗时 |
| `privacy_dp_queries_total` | Counter | mechanism, aggregation | DP 查询数 |
| `privacy_budget_remaining` | Gauge | namespace, budget_type | 剩余预算 |
| `privacy_classification_total` | Counter | final_level, layer | 分类结果数 |
| `privacy_auth_denials_total` | Counter | reason | 认证/鉴权/限速拒绝数 |

REST：`/metrics` 通过 `prometheus_client.make_asgi_app()` 挂载到 FastAPI。gRPC 拦截器内更新 Counter/Histogram，`/metrics` 仍通过 REST 端口暴露。

## 6. Tracing 设计

可选依赖：

```toml
[project.optional-dependencies]
observability = [
    "opentelemetry-api>=1.24.0",
    "opentelemetry-sdk>=1.24.0",
    "opentelemetry-instrumentation-fastapi>=0.45b0",
    "opentelemetry-instrumentation-grpc>=0.45b0",
    "opentelemetry-exporter-otlp>=1.24.0",
]
```

初始化逻辑：

```python
def init_tracing(endpoint: str | None, service_name: str):
    if not endpoint or not _HAS_OTEL:
        return NoOpTracerProvider()
    ...
```

- 未安装 opentelemetry 或环境变量未设置时，使用 NoOpTracerProvider，零开销。
- REST 与 gRPC instrumentation 仅在 tracer 为真实 provider 时挂载。

## 7. 接入点

### REST (`main.py`)

```python
from privacy_local_agent.observability.middleware import RequestContextMiddleware, MetricsMiddleware

app.add_middleware(RequestContextMiddleware)
app.add_middleware(MetricsMiddleware)
app.mount("/metrics", make_asgi_app())
```

注意：`/metrics` 本身不经过 metrics middleware，避免自引用。

### gRPC (`grpc_server.py`)

```python
from privacy_local_agent.observability.middleware import GrpcContextInterceptor, GrpcMetricsInterceptor

interceptors = [GrpcContextInterceptor(), GrpcMetricsInterceptor()]
if auth/rate-limit enabled: interceptors.extend(...)
```

### 统一启动器 (`server.py`)

```python
from privacy_local_agent.observability.logging_config import configure_logging
configure_logging()
```

## 8. 与安全层的协同

- 认证/鉴权/限速拦截器优先于 metrics 拦截器执行；拒绝事件直接由对应拦截器调用 `record_auth_denial(reason)`。
- `RequestContextMiddleware` 在认证依赖之前运行，确保 `request_id` 可用于日志。

## 9. 错误处理

- 日志/metrics 初始化失败不应阻止服务启动；使用 stderr 打印降级提示。
- metrics 更新异常吞掉并打印 error 日志，不中断请求。

## 10. 测试策略

- JSON 日志字段完整性测试。
- `/metrics` 返回指标正确性测试。
- gRPC 请求 metrics 与日志记录测试。
- 认证失败/越权/超速事件结构化日志测试。
- OpenTelemetry 可选初始化测试。
