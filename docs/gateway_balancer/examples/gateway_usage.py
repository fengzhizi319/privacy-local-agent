"""网关 HTTP 转发与负载均衡使用示例。

本脚本不依赖真实后端服务，而是通过 ASGI transport 在内存中构建一个 Mock Worker，
演示负载均衡器的初始化、HTTP 网关应用的创建、请求转发以及动态节点注册。

运行方式：

    cd /home/charles/code/sfwork/privacy-local-agent
    source .venv/bin/activate
    PYTHONPATH=. python docs/gateway_balancer/examples/gateway_usage.py
"""

import asyncio
from typing import Any, Dict

import httpx
from fastapi import FastAPI, Request

from privacy_local_agent.gateway.balancer import LoadBalancer
from privacy_local_agent.gateway.http_proxy import create_http_gateway_app


# ---------------------------------------------------------------------------
# Mock Worker：模拟后端 Agent 的 REST 行为，无需启动真实网络服务。
# ---------------------------------------------------------------------------
mock_worker = FastAPI(title="Mock Privacy Agent Worker")


@mock_worker.get("/health")
async def worker_health() -> Dict[str, str]:
    return {"status": "ok"}


@mock_worker.post("/v1/privacy/echo")
async def worker_echo(request: Request) -> Dict[str, Any]:
    """将请求体与部分头部回显，用于验证网关转发是否透传。"""
    body = await request.body()
    return {
        "status": "ok",
        "path": str(request.url),
        "method": request.method,
        "body": body.decode("utf-8"),
        "received_content_type": request.headers.get("content-type"),
    }


# ---------------------------------------------------------------------------
# 主流程：创建网关、注册后端、发起转发请求并验证结果。
# ---------------------------------------------------------------------------
async def main() -> None:
    # 1. 创建负载均衡器，默认使用轮询策略。
    balancer = LoadBalancer(strategy="round_robin")

    # 2. 注册一个模拟后端节点。http_url 使用任意 host，实际请求会进入 ASGI transport。
    balancer.add_node(
        http_url="http://mock-worker",
        grpc_address="127.0.0.1:50051",
        weight=1,
    )

    # 3. 创建 HTTP 网关 FastAPI 应用。
    gateway_app = create_http_gateway_app(balancer)

    # 4. 手动注入使用 ASGI transport 的 httpx 客户端，使网关转发直接命中 mock worker，
    #    避免依赖真实后端进程或网络端口。
    mock_transport = httpx.ASGITransport(app=mock_worker)
    gateway_app.state.http_client = httpx.AsyncClient(
        transport=mock_transport,
        base_url="http://mock-worker",
        timeout=httpx.Timeout(30.0),
    )
    gateway_app.state.http_client_loop = asyncio.get_running_loop()

    # 5. 使用 ASGI transport 作为客户端访问网关本身。
    gateway_transport = httpx.ASGITransport(app=gateway_app)
    async with httpx.AsyncClient(
        transport=gateway_transport, base_url="http://gateway"
    ) as client:
        print(">>> 示例 1：通过网关转发 /health")
        resp = await client.get("/health")
        print(f"    status={resp.status_code}, body={resp.json()}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

        print(">>> 示例 2：通过网关转发 /v1/privacy/echo")
        payload = {"message": "hello gateway"}
        resp = await client.post("/v1/privacy/echo", json=payload)
        data = resp.json()
        print(f"    status={resp.status_code}, body={data}")
        assert resp.status_code == 200
        assert data["method"] == "POST"
        assert '"message":"hello gateway"' in data["body"]

        print(">>> 示例 3：动态注册第二个后端节点")
        resp = await client.post(
            "/v1/gateway/register",
            json={
                "http_url": "http://mock-worker-2",
                "grpc_address": "127.0.0.1:50052",
                "weight": 2,
            },
        )
        print(f"    status={resp.status_code}, body={resp.json()}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "registered"

        nodes = [(n.http_url, n.grpc_address, n.weight) for n in balancer.nodes]
        print(f"    当前节点池: {nodes}")
        assert len(balancer.nodes) == 2

        print(">>> 示例 4：切换负载均衡策略为 least_connections")
        balancer.strategy = "least_connections"
        print(f"    当前策略: {balancer.strategy}")
        node = await balancer.select_node()
        print(f"    选中节点: {node.http_url}")

        print(">>> 示例 5：动态注销第二个后端节点")
        resp = await client.post(
            "/v1/gateway/deregister",
            json={
                "http_url": "http://mock-worker-2",
                "grpc_address": "127.0.0.1:50052",
            },
        )
        print(f"    status={resp.status_code}, body={resp.json()}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deregistered"
        assert len(balancer.nodes) == 1

    print(">>> 所有断言通过，示例运行结束。")


if __name__ == "__main__":
    asyncio.run(main())
