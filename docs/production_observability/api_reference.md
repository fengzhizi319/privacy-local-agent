# 可观测性模块 API 参考

## 1. Python SDK

### `configure_logging`

位置：`privacy_local_agent.observability.logging_config.configure_logging`

统一配置 root logger，支持文本或 JSON 格式，并自动注入请求上下文字段。

```python
configure_logging(
    log_level: str = "INFO",
    json_format: bool = False,
    service_name: str = "privacy-local-agent",
) -> None
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `log_level` | `str` | 否 | 日志级别：`DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL`，默认 `INFO` |
| `json_format` | `bool` | 否 | 是否输出 JSON 格式，默认 `False` |
| `service_name` | `str` | 否 | 服务名，预留字段 |

> 注意：`configure_logging` 具有幂等性；重复调用不会重复添加 handler。如需强制重新配置，可重置模块内 `_logging_configured = False`（主要用于测试）。

---

### `get_logger`

位置：`privacy_local_agent.observability.logging_config.get_logger`

返回指定名称的 `logging.Logger` 实例，共享 `configure_logging` 配置的 root handler。

```python
get_logger(name: str) -> logging.Logger
```

---

### `RequestContext`

位置：`privacy_local_agent.observability.context.RequestContext`

通过 `contextvars` 在异步/线程上下文中透传请求级元数据。

```python
RequestContext(
    request_id: str,
    method: str,
    path: str,
    identity_name: str = "",
)
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `request_id` | `str` | 请求关联 ID |
| `method` | `str` | HTTP 方法或 gRPC 方法名 |
| `path` | `str` | REST 路径或 gRPC 完整方法名 |
| `identity_name` | `str` | 已认证调用者名称，可选 |

相关函数：

| 函数 | 签名 | 说明 |
|---|---|---|
| `set_request_context` | `set_request_context(ctx: RequestContext) -> None` | 设置当前上下文 |
| `get_request_context` | `get_request_context() -> RequestContext \| None` | 获取当前上下文 |

---

### `init_tracing`

位置：`privacy_local_agent.observability.tracing.init_tracing`

可选初始化 OpenTelemetry tracing。未安装 `opentelemetry` 或环境变量未设置时返回 NoOp tracer，零开销。

```python
init_tracing(
    endpoint: str | None = None,
    service_name: str = "privacy-local-agent",
) -> Any
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `endpoint` | `str \| None` | 否 | OTLP HTTP endpoint，例 `http://jaeger:4318/v1/traces`；未设置时读取 `OTEL_EXPORTER_OTLP_ENDPOINT` |
| `service_name` | `str` | 否 | 服务名，用于 Resource 与 tracer 标识 |

---

### `start_span`

位置：`privacy_local_agent.observability.tracing.start_span`

创建 span 的上下文管理器，未启用 OpenTelemetry 时等价于 no-op。

```python
start_span(
    name: str,
    attributes: dict[str, Any] | None = None,
) -> Iterator[Any]
```

---

### `make_asgi_app`

位置：`privacy_local_agent.observability.metrics.make_asgi_app`

返回 Prometheus metrics 的 ASGI 应用，可直接挂载到 FastAPI。

```python
make_asgi_app() -> Any
```

---

## 2. 结构化日志字段

### 文本格式（默认）

```text
%(asctime)s [%(levelname)s] %(name)s: %(message)s request_id=%(request_id)s identity=%(identity_name)s
```

### JSON 格式字段

| 字段 | 说明 |
|---|---|
| `timestamp` | ISO 格式时间戳 |
| `level` | 日志级别 |
| `logger` | logger 名称 |
| `message` | 日志消息 |
| `request_id` | 请求 ID；非请求上下文下为空字符串 |
| `identity_name` | 调用者身份；非请求上下文下为空字符串 |
| `method` | HTTP 方法或 gRPC 方法名 |
| `path` | REST 路径或 gRPC 完整方法名 |
| `lineno` | 源码行号 |
| `funcName` | 函数名 |

---

## 3. Prometheus 指标

所有指标均注册在 `prometheus-client` 默认 registry，通过 REST 端口 `/metrics` 暴露。

| 指标名 | 类型 | Labels | 说明 |
|---|---|---|---|
| `privacy_requests_total` | Counter | `method`, `path`, `status` | REST/gRPC 请求总数 |
| `privacy_request_duration_seconds` | Histogram | `method`, `path` | 请求处理耗时（秒） |
| `privacy_dp_queries_total` | Counter | `mechanism`, `aggregation` | 差分隐私查询数 |
| `privacy_budget_remaining` | Gauge | `namespace`, `budget_type` | 剩余隐私预算（`epsilon` 或 `delta`） |
| `privacy_classification_total` | Counter | `final_level`, `layer` | 数据分类结果数 |
| `privacy_auth_denials_total` | Counter | `reason` | 认证/鉴权/限速拒绝数 |

### Histogram 分桶

`privacy_request_duration_seconds` 使用以下 buckets：

```python
[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
```

---

## 4. REST 接口

### GET `/metrics`

返回 Prometheus exposition 格式指标。

```bash
curl http://127.0.0.1:8079/metrics
```

响应头：

| 头 | 值 |
|---|---|
| `Content-Type` | `text/plain; version=0.0.4; charset=utf-8` |

---

## 5. 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `PRIVACY_LOG_LEVEL` | `INFO` | 日志级别 |
| `PRIVACY_LOG_FORMAT` | `text` | `text` 或 `json` |
| `PRIVACY_SERVICE_NAME` | `privacy-local-agent` | 服务名 |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | — | OTLP HTTP endpoint；设置后启用 tracing |
| `OTEL_SERVICE_NAME` | — | OpenTelemetry 服务名；未设置时使用 `PRIVACY_SERVICE_NAME` |

---

## 6. 异常与降级行为

| 场景 | 行为 |
|---|---|
| `PRIVACY_LOG_FORMAT=json` 但未安装 `python-json-logger` | 抛出 `RuntimeError` 并提示安装命令 |
| `OTEL_EXPORTER_OTLP_ENDPOINT` 已设置但未安装 `opentelemetry` | 返回 NoOp tracer，服务正常启动 |
| metrics 更新异常 | 吞掉异常并打印 error 日志，不中断请求 |
| `/metrics` 自身访问 | 被 `ObservabilityMiddleware` 排除，不计入 `privacy_requests_total` |
