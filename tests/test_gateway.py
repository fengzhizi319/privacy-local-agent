"""网关与负载均衡集成测试。

在后台启动真实的 Agent 实例，并通过网关的 HTTP 与 gRPC 代理进行请求分发与连通性验证。
"""

import asyncio
import socket
import threading
import time
import pytest
from fastapi.testclient import TestClient
import grpc
import uvicorn

from privacy_local_agent.gateway.balancer import LoadBalancer
from privacy_local_agent.gateway.http_proxy import create_http_gateway_app
from privacy_local_agent.gateway.grpc_proxy import GatewayGrpcServicer
from privacy_local_agent import privacy_pb2, privacy_pb2_grpc
from privacy_local_agent.main import app as rest_app
from privacy_local_agent.grpc_server import serve as grpc_serve


def find_free_port() -> int:
    """寻找本地空闲端口。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def backend_agent():
    """启动一个真实的后台 Agent Worker。"""
    rest_port = find_free_port()
    grpc_port = find_free_port()

    # 直接使用 lambda 调用，动态传入分配的空闲端口，避开 import-time 环境变量求值陷阱
    rest_thread = threading.Thread(
        target=lambda: uvicorn.run(rest_app, host="127.0.0.1", port=rest_port, log_level="error"),
        daemon=True,
    )
    rest_thread.start()

    grpc_thread = threading.Thread(
        target=lambda: grpc_serve(port=grpc_port),
        daemon=True,
    )
    grpc_thread.start()

    # 等待后台线程初始化并开始监听端口
    time.sleep(1.5)

    yield {
        "http_url": f"http://127.0.0.1:{rest_port}",
        "grpc_address": f"127.0.0.1:{grpc_port}",
    }


def test_load_balancer_strategies(backend_agent):
    """测试负载均衡选择算法。"""
    balancer = LoadBalancer(strategy="round_robin")
    balancer.add_node(backend_agent["http_url"], backend_agent["grpc_address"])

    loop = asyncio.new_event_loop()
    try:
        node = loop.run_until_complete(balancer.select_node())
        assert node is not None
        assert node.http_url == backend_agent["http_url"]
        assert node.grpc_address == backend_agent["grpc_address"]
        assert node.is_healthy is True

        # 测试最小连接数策略
        balancer.strategy = "least_connections"
        node.active_connections = 5

        # 添加另一个活跃连接为 0 的节点
        balancer.add_node("http://127.0.0.1:9999", "127.0.0.1:9999")
        selected = loop.run_until_complete(balancer.select_node())
        assert selected.grpc_address == "127.0.0.1:9999"

        # 关闭 gRPC 连接
        loop.run_until_complete(balancer.close_all())
    finally:
        loop.close()


def test_http_proxy_forwarding(backend_agent):
    """测试 HTTP 代理路由请求转发。"""
    balancer = LoadBalancer(strategy="round_robin")
    balancer.add_node(backend_agent["http_url"], backend_agent["grpc_address"])

    # 创建网关 HTTP FastAPI app 并使用 TestClient 进行请求
    gateway_app = create_http_gateway_app(balancer)
    client = TestClient(gateway_app)

    try:
        # 1. 验证健康检查转发
        res_health = client.get("/health")
        assert res_health.status_code == 200
        assert res_health.json()["status"] == "ok"

        # 2. 验证脱敏接口转发
        res_mask = client.post(
            "/v1/privacy/mask",
            json={"field_name": "mobile", "value": "13812345678", "context": ""},
        )
        assert res_mask.status_code == 200
        assert res_mask.json()["result"] == "138****5678"

        # 3. 验证无效节点时返回 503
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(balancer.close_all())
        finally:
            loop.close()

        # 清空可用节点
        balancer.nodes = []
        res_err = client.get("/health")
        assert res_err.status_code == 503
    finally:
        # 释放负载均衡连接
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(balancer.close_all())
        finally:
            loop.close()


def test_grpc_proxy_forwarding(backend_agent):
    """测试 gRPC 代理请求转发与反射。"""
    balancer = LoadBalancer(strategy="round_robin")
    balancer.add_node(backend_agent["http_url"], backend_agent["grpc_address"])

    servicer = GatewayGrpcServicer(balancer)

    # 创建模拟 gRPC context
    class MockContext:
        def __init__(self):
            self.code = None
            self.details = None

        def abort(self, code, details):
            self.code = code
            self.details = details
            raise grpc.RpcError("Aborted")

    context = MockContext()

    async def run_grpc_tests():
        # 1. 测试 Mask 方法转发
        req = privacy_pb2.MaskRequest(field_name="mobile", value="13812345678", context="")
        res = await servicer.Mask(req, context)
        assert res.result == "138****5678"

        # 2. 测试 Health 方法转发
        req_health = privacy_pb2.HealthRequest()
        res_health = await servicer.Health(req_health, context)
        assert res_health.status == "ok"

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(run_grpc_tests())
    finally:
        loop.run_until_complete(balancer.close_all())
        loop.close()


def test_dynamic_registration(backend_agent):
    """测试动态注册与注销 API。"""
    balancer = LoadBalancer(strategy="round_robin")
    # 初始节点池为空
    assert len(balancer.nodes) == 0

    gateway_app = create_http_gateway_app(balancer)
    client = TestClient(gateway_app)

    # 1. 发送注册请求
    res_reg = client.post(
        "/v1/gateway/register",
        json={
            "http_url": backend_agent["http_url"],
            "grpc_address": backend_agent["grpc_address"],
            "weight": 2,
        },
    )
    assert res_reg.status_code == 200
    assert res_reg.json()["status"] == "registered"
    assert len(balancer.nodes) == 1
    assert balancer.nodes[0].weight == 2

    # 2. 重复注册，验证不增加节点但更新属性
    res_reg_dup = client.post(
        "/v1/gateway/register",
        json={
            "http_url": backend_agent["http_url"],
            "grpc_address": backend_agent["grpc_address"],
            "weight": 5,
        },
    )
    assert res_reg_dup.status_code == 200
    assert len(balancer.nodes) == 1
    assert balancer.nodes[0].weight == 5

    # 3. 注销节点
    res_dereg = client.post(
        "/v1/gateway/deregister",
        json={
            "http_url": backend_agent["http_url"],
            "grpc_address": backend_agent["grpc_address"],
        },
    )
    assert res_dereg.status_code == 200
    assert res_dereg.json()["status"] == "deregistered"
    assert len(balancer.nodes) == 0


def test_http_retry_and_passive_failover(backend_agent):
    """测试 HTTP 代理下某一节点崩溃时的自适应故障重试与被动下线。"""
    balancer = LoadBalancer(strategy="round_robin")

    # 1. 注册一个故障节点（无法连接的端口）
    balancer.add_node("http://127.0.0.1:59999", "127.0.0.1:59999")

    # 2. 注册真实的健康节点作为备用
    balancer.add_node(backend_agent["http_url"], backend_agent["grpc_address"])

    gateway_app = create_http_gateway_app(balancer)
    client = TestClient(gateway_app)

    # 发送请求，网关第一轮若选到故障节点，将自动进行被动健康检查（设为 unhealthy），并重试到健康节点
    res = client.post(
        "/v1/privacy/mask",
        json={"field_name": "mobile", "value": "13812345678", "context": ""},
    )

    # 验证最终请求是成功的（故障转移成功）
    assert res.status_code == 200
    assert res.json()["result"] == "138****5678"

    # 验证故障节点已被被动标记为不健康
    bad_node = [n for n in balancer.nodes if "59999" in n.http_url][0]
    assert bad_node.is_healthy is False

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(balancer.close_all())
    finally:
        loop.close()


def test_grpc_retry_and_passive_failover(backend_agent):
    """测试 gRPC 代理下故障转移重试与被动下线。"""
    balancer = LoadBalancer(strategy="round_robin")

    # 1. 注册故障节点
    balancer.add_node("http://127.0.0.1:59999", "127.0.0.1:59999")
    # 2. 注册健康节点
    balancer.add_node(backend_agent["http_url"], backend_agent["grpc_address"])

    servicer = GatewayGrpcServicer(balancer)

    # 模拟 gRPC context
    class MockContext:
        def abort(self, code, details):
            raise grpc.RpcError("Aborted")

    context = MockContext()

    async def run_test():
        req = privacy_pb2.MaskRequest(field_name="mobile", value="13812345678", context="")
        # 即使轮询击中故障节点，也应该被动下线它并成功重试到健康节点，顺利返回结果
        res = await servicer.Mask(req, context)
        assert res.result == "138****5678"

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(run_test())
        # 验证故障节点已下线
        bad_node = [n for n in balancer.nodes if "59999" in n.grpc_address][0]
        assert bad_node.is_healthy is False
    finally:
        loop.run_until_complete(balancer.close_all())
        loop.close()

