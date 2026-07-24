"""Tests for observability: logging, metrics, request_id propagation.

验证结构化日志、Prometheus metrics、request_id 透传与认证拒绝事件计数。
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from privacy_local_agent.main import app
from privacy_local_agent.observability.logging_config import configure_logging

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_logging(monkeypatch):
    """Ensure logging is reconfigurable across tests."""
    # Force configure_logging to re-run by resetting its guard.
    import privacy_local_agent.observability.logging_config as lc

    lc._logging_configured = False
    yield
    lc._logging_configured = False


def test_request_id_propagation():
    """请求携带的 x-request-id 应在响应中返回。"""
    rid = "test-req-123"
    response = client.get("/health", headers={"x-request-id": rid})
    assert response.status_code == 200
    assert response.headers["x-request-id"] == rid


def test_json_logging_formatter():
    """PRIVACY_LOG_FORMAT=json 应配置 JsonFormatter。"""
    from pythonjsonlogger.jsonlogger import JsonFormatter

    configure_logging(json_format=True)
    handler = logging.getLogger().handlers[0]
    assert isinstance(handler.formatter, JsonFormatter)


def test_text_logging_formatter():
    """默认文本格式应配置标准 Formatter。"""
    configure_logging(json_format=False)
    handler = logging.getLogger().handlers[0]
    assert isinstance(handler.formatter, logging.Formatter)
    assert not isinstance(handler.formatter, type(__import__("pythonjsonlogger").jsonlogger.JsonFormatter))


def test_metrics_endpoint_exists():
    """/metrics 端点应返回 Prometheus 格式数据。"""
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "# HELP" in response.text


def test_request_metrics_recorded():
    """调用业务接口后 /metrics 应包含对应请求指标。"""
    response = client.post(
        "/v1/privacy/mask",
        json={"field_name": "mobile", "value": "13812345678"},
    )
    assert response.status_code == 200

    metrics = client.get("/metrics").text
    assert 'privacy_requests_total{method="POST",path="/v1/privacy/mask",status="200"}' in metrics
    assert 'privacy_request_duration_seconds_count{method="POST",path="/v1/privacy/mask"}' in metrics


def test_dp_metrics_recorded():
    """调用 DP 接口后应记录 privacy_dp_queries_total。"""
    response = client.post(
        "/v1/privacy/dp/count",
        json={"values": [1.0, 0.0, 1.0], "params": {"epsilon": 1.0}},
    )
    assert response.status_code == 200

    metrics = client.get("/metrics").text
    assert 'privacy_dp_queries_total{aggregation="count",mechanism="laplace"}' in metrics


def test_budget_metrics_recorded():
    """调用 budget 接口后应记录 privacy_budget_remaining。"""
    response = client.get("/v1/privacy/budget")
    assert response.status_code == 200

    metrics = client.get("/metrics").text
    assert 'privacy_budget_remaining{budget_type="epsilon",namespace="default"}' in metrics
    assert 'privacy_budget_remaining{budget_type="delta",namespace="default"}' in metrics


def test_auth_denial_metric_recorded(monkeypatch):
    """认证失败时应增加 privacy_auth_denials_total。"""
    monkeypatch.setenv("PRIVACY_AUTH_ENABLED", "true")
    monkeypatch.setenv("PRIVACY_AUTH_INTERNAL_KEYS_JSON", '{"sk":{"name":"x","scopes":["*"]}}')
    monkeypatch.setenv("PRIVACY_RATE_LIMIT_ENABLED", "false")

    response = client.post("/v1/privacy/mask", json={"field_name": "mobile", "value": "13812345678"})
    assert response.status_code == 401

    metrics = client.get("/metrics").text
    assert 'privacy_auth_denials_total{reason="unauthenticated"}' in metrics


def test_traffic_metrics_recorded():
    """调用业务接口后 /metrics 应包含请求与响应流量指标。"""
    response = client.post(
        "/v1/privacy/mask",
        json={"field_name": "mobile", "value": "13812345678"},
    )
    assert response.status_code == 200

    metrics = client.get("/metrics").text
    assert (
        'privacy_traffic_bytes_total{direction="request",method="POST",path="/v1/privacy/mask"}'
        in metrics
    )
    assert (
        'privacy_traffic_bytes_total{direction="response",method="POST",path="/v1/privacy/mask"}'
        in metrics
    )


def test_traffic_request_size_positive():
    """请求体越大，request traffic 指标累计值应越大。"""
    import privacy_local_agent.observability.metrics as metrics_module

    counter = metrics_module.TRAFFIC_BYTES_TOTAL.labels(
        method="POST", path="/v1/privacy/mask", direction="request"
    )
    before = counter._value.get()

    client.post(
        "/v1/privacy/mask",
        json={"field_name": "mobile", "value": "13812345678"},
    )

    after = counter._value.get()
    assert after > before
