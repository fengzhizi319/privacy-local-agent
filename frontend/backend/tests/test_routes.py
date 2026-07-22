"""Unit tests for the Python FastAPI proxy backend.

These tests mock the upstream privacy-local-agent client so they do not require
a running agent. They cover the public API surface: health, samples, and proxy.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.fixtures.samples import get_samples


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def mock_agent_client():
    """Patch the module-level agent_client.request async method."""
    with patch("app.main.agent_client.request", new_callable=AsyncMock) as mocked:
        yield mocked


def test_health_ok(client: TestClient, mock_agent_client: AsyncMock) -> None:
    mock_agent_client.return_value = {"status": "ok", "namespace": "default"}

    response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["backend"] == "ok"
    assert body["agent"]["status"] == "ok"
    assert body["agent_url"] == "http://127.0.0.1:8079"
    assert "latency_ms" in body


def test_health_agent_unreachable(client: TestClient, mock_agent_client: AsyncMock) -> None:
    from fastapi import HTTPException

    mock_agent_client.side_effect = HTTPException(status_code=502, detail="connection refused")

    response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["backend"] == "ok"
    assert body["agent"] == "unreachable"
    assert "error" in body


def test_samples(client: TestClient) -> None:
    response = client.get("/api/samples")

    assert response.status_code == 200
    body = response.json()
    assert "samples" in body
    assert len(body["samples"]) == len(get_samples())
    assert body["samples"][0]["path"]


def test_proxy_json(client: TestClient, mock_agent_client: AsyncMock) -> None:
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


def test_proxy_invalid_body(client: TestClient) -> None:
    response = client.post("/api/proxy", json={"method": "POST"})

    # Pydantic v2 returns 422 for missing required fields by default.
    assert response.status_code == 422
    body = response.json()
    assert "detail" in body


def test_proxy_upstream_error(client: TestClient, mock_agent_client: AsyncMock) -> None:
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
