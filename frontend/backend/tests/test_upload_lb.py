"""文件上传（/api/upload）与负载均衡测试（/api/lb_test）端点的单元测试。

测试策略：
    - ``/api/upload``：对 ``agent_client.request_multipart`` 打桩，验证后端
      正确以 multipart 转发到 agent 并包装为 ProxyResponse，无需真实 agent；
    - ``/api/lb_test``：用 ``httpx.MockTransport`` 注入两个假后端，直接调用
      ``_run_lb_test`` 验证 round_robin 均匀分发与统计字段，并测试端点接线。
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import LbBackend, LbTestRequest, _lb_pick_backends, _run_lb_test, app


@pytest.fixture
def client() -> TestClient:
    """提供包裹 FastAPI 应用的测试客户端。"""
    return TestClient(app)


@pytest.fixture
def mock_multipart():
    """对模块级 ``agent_client.request_multipart`` 异步方法打桩。"""
    with patch(
        "app.main.agent_client.request_multipart", new_callable=AsyncMock
    ) as mocked:
        yield mocked


# --------------------------------------------------------------------------- #
# /api/upload
# --------------------------------------------------------------------------- #
def test_upload_forwards_multipart(client: TestClient, mock_multipart: AsyncMock) -> None:
    """上传 CSV 应经 request_multipart 转发到 agent 并包装为 ProxyResponse。"""
    mock_multipart.return_value = {
        "operation": "mask_dataframe",
        "rows_in": 2,
        "rows_out": 2,
        "result": [{"email": "a***@example.com"}],
    }

    csv_bytes = b"email,phone\nalice@example.com,13800138000\nbob@example.com,13900139000\n"
    response = client.post(
        "/api/upload",
        files={"file": ("data.csv", csv_bytes, "text/csv")},
        data={"operation": "mask_dataframe", "params": json.dumps({"columns": ["email"]})},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == 200
    assert body["data"]["operation"] == "mask_dataframe"
    assert body["data"]["rows_out"] == 2
    assert "duration_ms" in body
    # 后端身份标识随上传响应一同下发，供前端验证切换生效。
    assert body["via"] == "python-rest"
    assert body["protocol"] == "REST"

    # 验证转发参数：目标路径与表单字段
    args, kwargs = mock_multipart.call_args
    assert args[0] == "/v1/privacy/process_file"
    assert kwargs["data"]["operation"] == "mask_dataframe"
    # files 中携带了文件名与内容
    forwarded = kwargs["files"]["file"]
    assert forwarded[0] == "data.csv"
    assert forwarded[1] == csv_bytes


def test_upload_upstream_error(client: TestClient, mock_multipart: AsyncMock) -> None:
    """agent 返回错误时，/api/upload 应透传状态码与 detail。"""
    from fastapi import HTTPException

    mock_multipart.side_effect = HTTPException(status_code=400, detail="仅支持 .csv 与 .json 文件")

    response = client.post(
        "/api/upload",
        files={"file": ("data.txt", b"hello", "text/plain")},
        data={"operation": "mask_dataframe"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "仅支持 .csv 与 .json 文件"


# --------------------------------------------------------------------------- #
# /api/lb_test
# --------------------------------------------------------------------------- #
def _mock_transport() -> httpx.MockTransport:
    """构造一个对所有探测请求返回 200 的假后端 transport。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok"})

    return httpx.MockTransport(handler)


@pytest.mark.anyio
async def test_run_lb_test_round_robin_distribution() -> None:
    """round_robin 策略下 6 个请求应均匀分发到 2 个节点，统计字段完整。"""
    req = LbTestRequest(
        backends=[
            LbBackend(name="a", url="http://backend-a"),
            LbBackend(name="b", url="http://backend-b"),
        ],
        num_requests=6,
        strategy="round_robin",
    )

    resp = await _run_lb_test(req, transport=_mock_transport())

    assert resp.strategy == "round_robin"
    assert resp.total == 6
    assert resp.success == 6
    assert resp.failed == 0
    assert len(resp.distribution) == 2
    # 均匀分发：每个节点各命中 3 次
    counts = {d.name: d.count for d in resp.distribution}
    assert counts == {"a": 3, "b": 3}
    for item in resp.distribution:
        assert item.success == item.count
        assert item.failed == 0
        assert item.avg_latency_ms >= 0
        assert item.min_latency_ms <= item.avg_latency_ms <= item.max_latency_ms


@pytest.mark.anyio
async def test_run_lb_test_failed_probe() -> None:
    """探测返回 500 时应计入 failed。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    req = LbTestRequest(
        backends=[LbBackend(name="a", url="http://backend-a")],
        num_requests=3,
        strategy="round_robin",
    )
    resp = await _run_lb_test(req, transport=httpx.MockTransport(handler))

    assert resp.total == 3
    assert resp.success == 0
    assert resp.failed == 3
    assert resp.distribution[0].failed == 3


@pytest.mark.anyio
async def test_run_lb_test_empty_backends() -> None:
    """backends 为空时应抛出 400。"""
    from fastapi import HTTPException

    req = LbTestRequest(backends=[], num_requests=3, strategy="round_robin")
    with pytest.raises(HTTPException) as excinfo:
        await _run_lb_test(req, transport=_mock_transport())
    assert excinfo.value.status_code == 400


def test_lb_pick_backends_strategies() -> None:
    """三种策略生成的下标序列均合法且长度正确。"""
    for strategy in ("round_robin", "random", "least_connections"):
        seq = _lb_pick_backends(strategy, 10, 3)
        assert len(seq) == 10
        assert all(0 <= i < 3 for i in seq)


def test_lb_pick_backends_invalid_strategy() -> None:
    """未知策略应抛出 400。"""
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        _lb_pick_backends("foobar", 5, 2)
    assert excinfo.value.status_code == 400


def test_lb_test_endpoint_empty_backends(client: TestClient) -> None:
    """端点层：backends 为空时返回 400。"""
    response = client.post(
        "/api/lb_test",
        json={"backends": [], "num_requests": 3, "strategy": "round_robin"},
    )
    assert response.status_code == 400
