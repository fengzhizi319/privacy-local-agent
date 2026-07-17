# privacy-local-agent 可观测性运维手册

> 对应 PRD/设计: `docs/production_observability/prd.md`, `design.md`

---

## 1. 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `PRIVACY_LOG_LEVEL` | `INFO` | 日志级别：DEBUG/INFO/WARNING/ERROR/CRITICAL。 |
| `PRIVACY_LOG_FORMAT` | `text` | `text` 或 `json`。 |
| `PRIVACY_SERVICE_NAME` | `privacy-local-agent` | 日志/tracing 中的服务名。 |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | — | 设置后启用 OpenTelemetry OTLP 导出，例 `http://jaeger:4317`。 |
| `OTEL_SERVICE_NAME` | — | OpenTelemetry service name；未设置时使用 `PRIVACY_SERVICE_NAME`。 |

---

## 2. 日志样例

### 文本格式（默认）

```text
2026-07-11 14:30:27,123 [INFO] privacy_local_agent.main: POST /v1/privacy/mask 200 1.2ms request=45B response=32B request_id=abc identity=portal
```

### JSON 格式

```bash
PRIVACY_LOG_FORMAT=json python -m privacy_local_agent.server
```

```json
{
  "timestamp": "2026-07-11T14:30:27.123Z",
  "level": "INFO",
  "logger": "privacy_local_agent.main",
  "message": "POST /v1/privacy/mask 200 1.2ms",
  "request_id": "abc",
  "method": "POST",
  "path": "/v1/privacy/mask",
  "status": 200,
  "duration_ms": 1.2,
  "identity_name": "portal",
  "lineno": 120,
  "funcName": "mask"
}
```

---

## 3. Prometheus 指标抓取

REST 端口（默认 8079）直接访问 `/metrics`：

```bash
curl http://127.0.0.1:8079/metrics
```

K8s ServiceMonitor 示例：

```yaml
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: privacy-local-agent
spec:
  selector:
    matchLabels:
      app: privacy-local-agent
  endpoints:
    - port: rest
      path: /metrics
      interval: 15s
```

---

## 4. Grafana Dashboard 关键面板

| 面板 | PromQL |
|---|---|
| QPS | `sum(rate(privacy_requests_total[1m]))` |
| P99 延迟 | `histogram_quantile(0.99, sum(rate(privacy_request_duration_seconds_bucket[5m])) by (le))` |
| 错误率 | `sum(rate(privacy_requests_total{status!~"2.."}[5m])) / sum(rate(privacy_requests_total[5m]))` |
| DP 查询速率 | `sum(rate(privacy_dp_queries_total[1m])) by (mechanism)` |
| 剩余预算 | `privacy_budget_remaining` |
| 拒绝事件 | `sum(rate(privacy_auth_denials_total[1m])) by (reason)` |
| 入站流量 | `sum(rate(privacy_traffic_bytes_total{direction="request"}[1m])) by (path)` |
| 出站流量 | `sum(rate(privacy_traffic_bytes_total{direction="response"}[1m])) by (path)` |
| 大流量接口 | `sum by (path) (rate(privacy_traffic_bytes_total[5m])) > 1e6` |

---

## 5. Jaeger / Tempo Tracing

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger-collector:4317
export OTEL_SERVICE_NAME=privacy-local-agent
python -m privacy_local_agent.server
```

需先安装可选依赖：

```bash
pip install -e ".[observability]"
```

---

## 6. 审计事件

以下事件会以 warning/error 级别打印日志，并累加 `privacy_auth_denials_total`：

- 认证失败（401 / `UNAUTHENTICATED`）
- 越权（403 / `PERMISSION_DENIED`）
- 限速（429 / `RESOURCE_EXHAUSTED`）

可直接在日志平台搜索：`level:ERROR OR reason:unauthenticated`。
