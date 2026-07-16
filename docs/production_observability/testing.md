# 可观测性模块测试文档

## 1. 概述

本文档定义 `privacy_local_agent/observability/` 的测试策略、测试范围与可执行示例。可观测性模块的测试需覆盖日志格式、Prometheus 指标、`request_id` 透传、认证拒绝事件以及 OpenTelemetry 可选初始化。

## 2. 测试目标

- 验证文本与 JSON 两种日志格式正确配置。
- 验证 `/metrics` 端点返回 Prometheus exposition 格式。
- 验证 REST/gRPC 请求被正确记录到 `privacy_requests_total` 与 `privacy_request_duration_seconds`。
- 验证 DP、预算、分类、认证拒绝等业务指标被正确记录。
- 验证 `x-request-id` 在请求与响应中透传。
- 验证 OpenTelemetry 未安装或未配置时返回 NoOp tracer，不报错。

## 3. 单元测试策略

### 3.1 日志格式测试

固定重置 `configure_logging` 的幂等守卫，分别验证文本与 JSON formatter。

```python
import logging
import pytest

from privacy_local_agent.observability.logging_config import configure_logging


@pytest.fixture(autouse=True)
def _reset_logging():
    import privacy_local_agent.observability.logging_config as lc

    lc._logging_configured = False
    yield
    lc._logging_configured = False


def test_text_logging_formatter():
    configure_logging(json_format=False)
    handler = logging.getLogger().handlers[0]
    assert isinstance(handler.formatter, logging.Formatter)


def test_json_logging_formatter():
    from pythonjsonlogger.jsonlogger import JsonFormatter

    configure_logging(json_format=True)
    handler = logging.getLogger().handlers[0]
    assert isinstance(handler.formatter, JsonFormatter)
```

### 3.2 request_id 透传测试

```python
from fastapi.testclient import TestClient
from privacy_local_agent.main import app

client = TestClient(app)


def test_request_id_propagation():
    rid = "test-req-123"
    response = client.get("/health", headers={"x-request-id": rid})
    assert response.status_code == 200
    assert response.headers["x-request-id"] == rid
```

### 3.3 Metrics 端点测试

```python
def test_metrics_endpoint_exists():
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "# HELP" in response.text
```

### 3.4 业务请求指标测试

```python
def test_request_metrics_recorded():
    response = client.post(
        "/v1/privacy/mask",
        json={"field_name": "mobile", "value": "13812345678"},
    )
    assert response.status_code == 200

    metrics = client.get("/metrics").text
    assert 'privacy_requests_total{method="POST",path="/v1/privacy/mask",status="200"}' in metrics
    assert 'privacy_request_duration_seconds_count{method="POST",path="/v1/privacy/mask"}' in metrics
```

### 3.5 DP 指标测试

```python
def test_dp_metrics_recorded():
    response = client.post(
        "/v1/privacy/dp/count",
        json={"values": [1.0, 0.0, 1.0], "params": {"epsilon": 1.0}},
    )
    assert response.status_code == 200

    metrics = client.get("/metrics").text
    assert 'privacy_dp_queries_total{aggregation="count",mechanism="laplace"}' in metrics
```

### 3.6 预算指标测试

```python
def test_budget_metrics_recorded():
    response = client.get("/v1/privacy/budget")
    assert response.status_code == 200

    metrics = client.get("/metrics").text
    assert 'privacy_budget_remaining{budget_type="epsilon",namespace="default"}' in metrics
    assert 'privacy_budget_remaining{budget_type="delta",namespace="default"}' in metrics
```

### 3.7 认证拒绝指标测试

```python
def test_auth_denial_metric_recorded(monkeypatch):
    monkeypatch.setenv("PRIVACY_AUTH_ENABLED", "true")
    monkeypatch.setenv(
        "PRIVACY_AUTH_INTERNAL_KEYS_JSON", '{"sk":{"name":"x","scopes":["*"]}}'
    )
    monkeypatch.setenv("PRIVACY_RATE_LIMIT_ENABLED", "false")

    response = client.post(
        "/v1/privacy/mask", json={"field_name": "mobile", "value": "13812345678"}
    )
    assert response.status_code == 401

    metrics = client.get("/metrics").text
    assert 'privacy_auth_denials_total{reason="unauthenticated"}' in metrics
```

### 3.8 OpenTelemetry 可选初始化测试

```python
from privacy_local_agent.observability.tracing import init_tracing


def test_init_tracing_without_otel():
    # 在未安装 opentelemetry 或环境变量未设置时，返回 no-op tracer
    tracer = init_tracing(endpoint=None, service_name="test")
    assert tracer is not None
```

## 4. 集成测试策略

### 4.1 JSON 日志字段完整性

通过捕获 stdout 验证 JSON 日志包含 `timestamp`、`level`、`logger`、`message`、`request_id` 等字段。

```python
import json


def test_json_log_contains_required_fields(capsys, monkeypatch):
    monkeypatch.setenv("PRIVACY_LOG_FORMAT", "json")
    response = client.get("/health", headers={"x-request-id": "log-test"})
    assert response.status_code == 200

    captured = capsys.readouterr()
    for line in captured.out.strip().split("\n"):
        if not line:
            continue
        log_entry = json.loads(line)
        assert "timestamp" in log_entry
        assert "level" in log_entry
        assert "logger" in log_entry
        assert "message" in log_entry
```

### 4.2 gRPC 请求可观测性

启动 gRPC 测试服务器，验证调用后 `/metrics` 端点包含 `method="gRPC"` 的指标。

```python
import grpc
from privacy_local_agent.grpc_server import PrivacyServicer
from privacy_local_agent.proto import privacy_pb2, privacy_pb2_grpc


def test_grpc_request_metrics():
    servicer = PrivacyServicer()
    request = privacy_pb2.MaskRequest(field_name="mobile", value="13812345678")
    response = servicer.Mask(request, None)
    assert response.masked_value is not None
```

## 5. 测试执行命令

```bash
# 运行所有可观测性相关测试
PYTHONPATH=. pytest tests/test_observability.py -v

# 运行所有测试
PYTHONPATH=. pytest tests -q

# 带覆盖率报告
PYTHONPATH=. pytest tests/test_observability.py --cov=privacy_local_agent.observability --cov-report=term-missing
```

## 6. 持续集成建议

- 每次提交前执行 `pytest tests/test_observability.py`。
- CI 中同时覆盖 `PRIVACY_LOG_FORMAT=text` 与 `PRIVACY_LOG_FORMAT=json` 两种启动方式。
- 对 `/metrics` 输出做字符串断言时，避免依赖指标绝对值，优先断言 label 组合存在性。
- 认证拒绝测试需通过 monkeypatch 临时启用 `PRIVACY_AUTH_ENABLED`，并在用例结束后清理环境。

## 7. 验收检查清单

- [ ] `configure_logging` 的文本与 JSON formatter 测试通过。
- [ ] `x-request-id` 在 REST 请求中透传。
- [ ] `/metrics` 端点可访问并返回 Prometheus 格式。
- [ ] REST 业务请求更新 `privacy_requests_total` 与 `privacy_request_duration_seconds`。
- [ ] DP 请求更新 `privacy_dp_queries_total`。
- [ ] 预算查询更新 `privacy_budget_remaining`。
- [ ] 认证失败更新 `privacy_auth_denials_total`。
- [ ] OpenTelemetry 未安装或未配置时不影响服务启动。
