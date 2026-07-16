# 可观测性产品设计 PRD

> Scope: P0 — 结构化日志、Prometheus metrics、分布式 tracing。

## 1. 概述

本文档定义 `privacy-local-agent` 可观测性模块的产品需求与验收标准。该模块为生产环境提供机器可解析的日志、性能指标与请求链路追踪能力，满足排障、SLI/SLO 监控与安全审计需求。

## 2. 设计目标

- 提供结构化日志（JSON），包含 request_id、接口、调用者身份、耗时等字段。
- 在 REST 端口暴露 `/metrics` endpoint，返回 Prometheus exposition 格式指标。
- 支持可选的 OpenTelemetry OTLP 导出，便于在微服务体系中定位延迟。
- 所有能力默认对本地开发影响最小：日志默认文本、`/metrics` 默认开启、tracing 默认关闭。

## 3. 用户故事

| 角色 | 故事 |
|---|---|
| SRE | 通过 `/metrics` 抓取 QPS/P99/错误率，用于告警。 |
| 运维 | 日志是 JSON，便于接入 ELK/Loki 做检索与审计。 |
| 开发 | 每个请求带有 request_id，并在错误日志中透传，便于定位。 |
| 安全 | 记录认证失败、越权、超速等事件，用于审计。 |
| 架构师 | 可选接入 Jaeger/Tempo，追踪跨服务调用链。 |

## 4. 功能需求

### 4.1 结构化日志

| ID | 需求 |
|---|---|
| OB-LOG-1 | 支持 `PRIVACY_LOG_FORMAT=json|text`，默认 `text`。 |
| OB-LOG-2 | JSON 日志包含字段：`timestamp`、`level`、`logger`、`message`、`request_id`、`method`、`path`、`status`、`duration_ms`、`identity_name`、`lineno`、`funcName`。 |
| OB-LOG-3 | 当未处于请求上下文时，`request_id` 等字段可为空，不报错。 |
| OB-LOG-4 | 关键事件（认证失败、越权、超速、预算耗尽）以 warning/error 级别打印结构化日志。 |

### 4.2 Prometheus Metrics

| ID | 需求 |
|---|---|
| OB-METRIC-1 | REST 端口挂载 `/metrics` endpoint，返回 Prometheus exposition 格式。 |
| OB-METRIC-2 | 指标 `privacy_requests_total`：Counter，labels = method, path, status。 |
| OB-METRIC-3 | 指标 `privacy_request_duration_seconds`：Histogram，labels = method, path。 |
| OB-METRIC-4 | 指标 `privacy_dp_queries_total`：Counter，labels = mechanism, aggregation。 |
| OB-METRIC-5 | 指标 `privacy_budget_remaining`：Gauge，labels = namespace, budget_type。 |
| OB-METRIC-6 | 指标 `privacy_classification_total`：Counter，labels = final_level, layer。 |
| OB-METRIC-7 | 指标 `privacy_auth_denials_total`：Counter，labels = reason。 |

### 4.3 Tracing

| ID | 需求 |
|---|---|
| OB-TRACE-1 | 当 `OTEL_EXPORTER_OTLP_ENDPOINT` 设置时初始化 OpenTelemetry。 |
| OB-TRACE-2 | REST 请求创建 span，包含 method、path、status、identity。 |
| OB-TRACE-3 | gRPC 请求创建 span，包含 method、status、identity。 |
| OB-TRACE-4 | opentelemetry 作为可选依赖，未安装时不报错。 |

### 4.4 探针

| ID | 需求 |
|---|---|
| OB-PROBE-1 | `/health` 继续作为 liveness/readiness 探针。 |
| OB-PROBE-2 | 新增 `/ready` 可选探针，在模型加载完成后返回 200（P1 后实现）。 |

## 5. 非功能需求

| 维度 | 要求 |
|---|---|
| 性能 | 日志/metrics 处理增加的 P99 延迟 < 1ms。 |
| 向后兼容 | 默认文本日志；`/metrics` 不影响业务接口。 |
| 可配置 | 全部通过环境变量开关，无需改代码。 |
| 无外部依赖 | tracing 可选；默认不依赖外部 collector。 |

## 6. 验收标准

- [ ] `docs/production_observability/{prd,design,ops}.md` 完成。
- [ ] 新增 `privacy_local_agent/observability/` 模块。
- [ ] REST `/metrics` 可访问并返回预期指标。
- [ ] JSON 日志输出包含指定字段。
- [ ] gRPC 请求记录 access log 与 metrics。
- [ ] 认证失败/越权/超速事件打印结构化日志。
- [ ] OpenTelemetry 可选初始化。
- [ ] 新增观测性测试通过。
- [ ] `pyproject.toml` 增加 `python-json-logger`、`prometheus-client`、可选 `opentelemetry` 依赖。
