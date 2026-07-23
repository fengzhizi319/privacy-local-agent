"""Python FastAPI 代理后端的单元测试。

测试策略：
    - 使用 ``fastapi.testclient.TestClient`` 直接调用应用路由，无需真实启动服务；
    - 通过 ``unittest.mock`` 对上游 ``agent_client.request`` 打桩，
      因此**不需要**运行中的 privacy-local-agent；
    - 覆盖公开 API 面：``/api/health``、``/api/samples``、``/api/proxy`` 的
      正常、上游不可达、参数校验与上游错误透传等场景。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.fixtures.samples import get_samples


@pytest.fixture
def client() -> TestClient:
    """提供包裹 FastAPI 应用的测试客户端。"""
    return TestClient(app)


@pytest.fixture
def mock_agent_client():
    """对模块级 ``agent_client.request`` 异步方法打桩。

    使用 ``AsyncMock`` 以便能用 ``return_value`` / ``side_effect`` 控制
    异步调用的返回与异常，隔离对真实 agent 的依赖。
    """
    with patch("app.main.agent_client.request", new_callable=AsyncMock) as mocked:
        yield mocked


def test_health_ok(client: TestClient, mock_agent_client: AsyncMock) -> None:
    """agent 可达时，/api/health 应返回 backend/agent 双正常与延迟字段。"""
    mock_agent_client.return_value = {"status": "ok", "namespace": "default"}

    response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["backend"] == "ok"
    assert body["agent"]["status"] == "ok"
    assert body["agent_url"] == "http://127.0.0.1:8079"
    assert "latency_ms" in body
    # 后端身份标识：Python 后端恒为 python-rest / REST，供前端验证切换生效。
    assert body["via"] == "python-rest"
    assert body["protocol"] == "REST"


def test_health_agent_unreachable(client: TestClient, mock_agent_client: AsyncMock) -> None:
    """agent 不可达时，/api/health 仍返回 200，但 agent 字段为 unreachable。"""
    from fastapi import HTTPException

    mock_agent_client.side_effect = HTTPException(status_code=502, detail="connection refused")

    response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["backend"] == "ok"
    assert body["agent"] == "unreachable"
    assert "error" in body
    # 即使 agent 不可达，身份标识仍应下发。
    assert body["via"] == "python-rest"
    assert body["protocol"] == "REST"


def test_samples(client: TestClient) -> None:
    """/api/samples 应返回与 get_samples() 数量一致的示例列表。"""
    response = client.get("/api/samples")

    assert response.status_code == 200
    body = response.json()
    assert "samples" in body
    assert len(body["samples"]) == len(get_samples())
    assert body["samples"][0]["path"]


def test_proxy_json(client: TestClient, mock_agent_client: AsyncMock) -> None:
    """/api/proxy 转发 JSON 请求，应包装为 status/duration_ms/data 结构。"""
    mock_agent_client.return_value = {"result": "a***@example.com"}

    response = client.post(
        "/api/proxy",
        json={
            "method": "POST",
            "path": "/v1/privacy/mask",
            "body": {"field_name": "email", "value": "alice@example.com"},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == 200
    assert body["data"]["result"] == "a***@example.com"
    assert "duration_ms" in body
    # 后端身份标识随代理响应一同下发。
    assert body["via"] == "python-rest"
    assert body["protocol"] == "REST"


def test_proxy_invalid_body(client: TestClient) -> None:
    """缺少必填字段（path）时，Pydantic v2 应返回 422 校验错误。"""
    response = client.post("/api/proxy", json={"method": "POST"})

    # Pydantic v2 默认对缺失必填字段返回 422。
    assert response.status_code == 422
    body = response.json()
    assert "detail" in body


def test_proxy_upstream_error(client: TestClient, mock_agent_client: AsyncMock) -> None:
    """上游 agent 返回错误时，/api/proxy 应透传状态码与 detail。"""
    from fastapi import HTTPException

    mock_agent_client.side_effect = HTTPException(status_code=422, detail="invalid field")

    response = client.post(
        "/api/proxy",
        json={
            "method": "POST",
            "path": "/v1/privacy/mask",
            "body": {"field_name": "unknown", "value": "x"},
        },
    )

    assert response.status_code == 422
    body = response.json()
    assert body["detail"] == "invalid field"
