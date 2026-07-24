"""负载均衡与健康检查引擎模块。

定义后端工作节点、负载均衡调度策略、异步健康检查循环及熔断器。

Load-balancing and health-check engine.

Defines backend worker nodes, scheduling strategies, async health-check loop,
and a per-node circuit breaker for fault isolation.
"""

from __future__ import annotations

import asyncio
import random
import threading
import time

import grpc
import httpx

from privacy_local_agent import privacy_pb2, privacy_pb2_grpc
from privacy_local_agent.observability.logging_config import get_logger
from privacy_local_agent.observability.metrics import (
    GATEWAY_HEALTHY_NODES,
    GATEWAY_RETRIES_TOTAL,
)

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Circuit Breaker (P2: 熔断器)
# ---------------------------------------------------------------------------


class CircuitBreaker:
    """Per-node circuit breaker with half-open recovery.

    每个后端节点配备独立的熔断器，连续失败达到阈值后打开（拒绝请求），
    经过恢复窗口后进入半开状态允许探测。

    States: closed (normal) → open (rejecting) → half_open (probing).
    """

    def __init__(self, failure_threshold: int = 5, recovery_timeout: float = 30.0):
        """Initialize circuit breaker.

        Args:
            failure_threshold: 连续失败次数阈值，达到后熔断器打开。
            recovery_timeout: 熔断后恢复窗口（秒），过后进入半开状态。
        """
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._failure_count = 0
        self._state = "closed"
        self._opened_at: float = 0.0

    @property
    def state(self) -> str:
        """Return current state, transitioning open → half_open if timeout elapsed."""
        if self._state == "open" and (time.monotonic() - self._opened_at) >= self.recovery_timeout:
            self._state = "half_open"
        return self._state

    def record_success(self) -> None:
        """Record a successful call; reset breaker to closed."""
        self._failure_count = 0
        self._state = "closed"

    def record_failure(self) -> None:
        """Record a failed call; open breaker if threshold reached."""
        self._failure_count += 1
        if self._failure_count >= self.failure_threshold:
            self._state = "open"
            self._opened_at = time.monotonic()

    def allow_request(self) -> bool:
        """Return True if the breaker allows a request through."""
        state = self.state
        return state in ("closed", "half_open")


# ---------------------------------------------------------------------------
# Backend Node
# ---------------------------------------------------------------------------


class BackendNode:
    """后端工作节点。

    维护单个后端实例的地址信息、健康状态、长连接通道、活跃连接数及熔断器。

    Backend worker node maintaining address info, health status, gRPC channel,
    active connection count, and a per-node circuit breaker.
    """

    def __init__(self, http_url: str, grpc_address: str, weight: int = 1):
        """初始化工作节点 / Initialize worker node.

        Args:
            http_url: 后端 HTTP/REST 基准 URL (例如 "http://127.0.0.1:8079")。
            grpc_address: 后端 gRPC 地址 (例如 "127.0.0.1:50051")。
            weight: 权重 (预留用于加权轮询/随机)。
        """
        self.http_url = http_url.rstrip("/")
        self.grpc_address = grpc_address
        self.weight = weight
        self.is_healthy = True
        self.active_connections = 0
        self.circuit_breaker = CircuitBreaker()
        self._grpc_channel: grpc.aio.Channel | None = None
        self._grpc_stub: privacy_pb2_grpc.PrivacyServiceStub | None = None

    @property
    def grpc_stub(self) -> privacy_pb2_grpc.PrivacyServiceStub:
        """延迟初始化并获取 gRPC Stub / Lazily initialize gRPC stub."""
        if self._grpc_stub is None:
            self._grpc_channel = grpc.aio.insecure_channel(self.grpc_address)
            self._grpc_stub = privacy_pb2_grpc.PrivacyServiceStub(self._grpc_channel)
        return self._grpc_stub

    async def close(self) -> None:
        """关闭 gRPC 连接通道 / Close gRPC channel."""
        if self._grpc_channel is not None:
            await self._grpc_channel.close()
            self._grpc_channel = None
            self._grpc_stub = None


# ---------------------------------------------------------------------------
# Load Balancer
# ---------------------------------------------------------------------------


class LoadBalancer:
    """负载均衡调度器。

    支持对健康后端的轮询、随机、最小连接数等分发策略。

    Load-balancer scheduler supporting round-robin, random, and
    least-connections strategies over healthy backend nodes.
    """

    def __init__(self, strategy: str = "round_robin"):
        """初始化负载均衡器 / Initialize load balancer.

        Args:
            strategy: 负载均衡策略 ("round_robin", "random", "least_connections")。
        """
        self.strategy = strategy.lower()
        self.nodes: list[BackendNode] = []
        self.rr_index = 0
        self.lock = asyncio.Lock()
        self.modify_lock = threading.Lock()

    def add_node(self, http_url: str, grpc_address: str, weight: int = 1) -> None:
        """添加工作节点到地址池 / Add worker node to pool (thread-safe, dedup)."""
        with self.modify_lock:
            clean_url = http_url.rstrip("/")
            for node in self.nodes:
                if node.http_url == clean_url and node.grpc_address == grpc_address:
                    node.is_healthy = True
                    node.weight = weight
                    node.active_connections = 0
                    node.circuit_breaker.record_success()
                    logger.info(
                        "Updated existing backend node",
                        extra={"http_url": http_url, "grpc_address": grpc_address},
                    )
                    return

            node = BackendNode(http_url, grpc_address, weight)
            self.nodes.append(node)
            logger.info(
                "Added backend node",
                extra={"http_url": http_url, "grpc_address": grpc_address},
            )
            self._update_healthy_gauge()

    def remove_node(self, http_url: str, grpc_address: str) -> None:
        """安全地从节点池中移除工作节点 / Safely remove a node from pool."""
        with self.modify_lock:
            clean_url = http_url.rstrip("/")
            new_nodes = []
            removed = False
            for node in self.nodes:
                if node.http_url == clean_url and node.grpc_address == grpc_address:
                    asyncio.create_task(node.close())  # noqa: RUF006
                    removed = True
                else:
                    new_nodes.append(node)
            self.nodes = new_nodes
            if removed:
                logger.info(
                    "Removed backend node",
                    extra={"http_url": http_url, "grpc_address": grpc_address},
                )
                self._update_healthy_gauge()

    def get_healthy_nodes(self) -> list[BackendNode]:
        """获取当前健康且熔断器允许的节点列表 / Get healthy + circuit-closed nodes."""
        return [
            node
            for node in self.nodes
            if node.is_healthy and node.circuit_breaker.allow_request()
        ]

    async def select_node(self) -> BackendNode | None:
        """按策略选择一个可用后端节点 / Select a backend node by strategy.

        Returns:
            若有可用节点返回 BackendNode，否则返回 None。
        """
        async with self.lock:
            healthy = self.get_healthy_nodes()
            if not healthy:
                return None

            if self.strategy == "random":
                return random.choice(healthy)

            elif self.strategy == "least_connections":
                return min(healthy, key=lambda n: n.active_connections)

            else:  # round_robin
                node = healthy[self.rr_index % len(healthy)]
                self.rr_index = (self.rr_index + 1) % len(healthy)
                return node

    async def close_all(self) -> None:
        """关闭所有后端的 gRPC 通道 / Close all backend gRPC channels."""
        for node in self.nodes:
            await node.close()

    def _update_healthy_gauge(self) -> None:
        """Update Prometheus healthy-nodes gauge."""
        count = len(self.get_healthy_nodes())
        GATEWAY_HEALTHY_NODES.set(count)


# ---------------------------------------------------------------------------
# Health Check Loop
# ---------------------------------------------------------------------------


async def health_check_loop(balancer: LoadBalancer, interval: float = 5.0) -> None:
    """异步健康检查后台任务 / Async background health-check loop.

    定时向所有后端节点发送 HTTP 与 gRPC 健康请求，更新节点在线状态与熔断器。

    Periodically probes all backend nodes via HTTP and gRPC health endpoints,
    updating node health status and circuit breaker state.

    Args:
        balancer: 关联的负载均衡实例。
        interval: 检测间隔时间（秒）。
    """
    logger.info("Starting background health check loop", extra={"interval_seconds": interval})
    async with httpx.AsyncClient() as client:
        while True:
            for node in balancer.nodes:
                # 1. 检查 REST (HTTP) 服务
                http_ok = False
                try:
                    res = await client.get(f"{node.http_url}/health", timeout=2.0)
                    if res.status_code == 200:
                        data = res.json()
                        if data.get("status") == "ok":
                            http_ok = True
                except Exception as e:
                    logger.debug(
                        "HTTP health check failed",
                        extra={"node": node.http_url, "error": str(e)},
                    )

                # 2. 检查 gRPC 服务
                grpc_ok = False
                try:
                    req = privacy_pb2.HealthRequest()
                    res = await node.grpc_stub.Health(req, timeout=2.0)
                    if res.status == "ok":
                        grpc_ok = True
                except Exception as e:
                    logger.debug(
                        "gRPC health check failed",
                        extra={"node": node.grpc_address, "error": str(e)},
                    )

                # 3. 状态决策与更替
                was_healthy = node.is_healthy
                node.is_healthy = http_ok and grpc_ok

                # Update circuit breaker based on health result
                if node.is_healthy:
                    node.circuit_breaker.record_success()
                else:
                    node.circuit_breaker.record_failure()

                if was_healthy != node.is_healthy:
                    status_str = "healthy" if node.is_healthy else "unhealthy"
                    log_func = logger.info if node.is_healthy else logger.warning
                    log_func(
                        "Node status changed",
                        extra={
                            "node": node.grpc_address,
                            "status": status_str,
                            "http": "UP" if http_ok else "DOWN",
                            "grpc": "UP" if grpc_ok else "DOWN",
                            "circuit_breaker": node.circuit_breaker.state,
                        },
                    )

            # Update gauge after each sweep
            balancer._update_healthy_gauge()
            await asyncio.sleep(interval)
"""负载均衡与健康检查引擎模块。

定义后端工作节点、负载均衡调度策略以及异步健康检查循环。
"""

import asyncio
import logging
import random
import threading

import grpc
import httpx

from privacy_local_agent import privacy_pb2, privacy_pb2_grpc

logger = logging.getLogger("gateway.balancer")


class BackendNode:
    """后端工作节点。

    维护单个后端实例的地址信息、健康状态、长连接通道及活跃连接数。
    """

    def __init__(self, http_url: str, grpc_address: str, weight: int = 1):
        """初始化工作节点。

        Args:
            http_url: 后端 HTTP/REST 基准 URL (例如 "http://127.0.0.1:8079")。
            grpc_address: 后端 gRPC 地址 (例如 "127.0.0.1:50051")。
            weight: 权重 (预留用于加权轮询/随机)。
        """
        self.http_url = http_url.rstrip("/")
        self.grpc_address = grpc_address
        self.weight = weight
        self.is_healthy = True
        self.active_connections = 0
        self._grpc_channel = None
        self._grpc_stub = None

    @property
    def grpc_stub(self) -> privacy_pb2_grpc.PrivacyServiceStub:
        """延迟初始化并获取 gRPC Stub，确保其绑定在当前运行协程的 Event Loop 上。"""
        if self._grpc_stub is None:
            self._grpc_channel = grpc.aio.insecure_channel(self.grpc_address)
            self._grpc_stub = privacy_pb2_grpc.PrivacyServiceStub(self._grpc_channel)
        return self._grpc_stub

    async def close(self):
        """关闭 gRPC 连接通道。"""
        if self._grpc_channel is not None:
            await self._grpc_channel.close()
            self._grpc_channel = None
            self._grpc_stub = None



class LoadBalancer:
    """负载均衡调度器。

    支持对健康后端的轮询、随机、最小连接数等分发策略。
    """

    def __init__(self, strategy: str = "round_robin"):
        """初始化负载均衡器。

        Args:
            strategy: 负载均衡策略 ("round_robin", "random", "least_connections")。
        """
        self.strategy = strategy.lower()
        self.nodes: list[BackendNode] = []
        self.rr_index = 0
        self.lock = asyncio.Lock()
        self.modify_lock = threading.Lock()

    def add_node(self, http_url: str, grpc_address: str, weight: int = 1):
        """添加工作节点到地址池（线程/协程安全，自动防止重复）。"""
        with self.modify_lock:
            clean_url = http_url.rstrip("/")
            for node in self.nodes:
                if node.http_url == clean_url and node.grpc_address == grpc_address:
                    node.is_healthy = True
                    node.weight = weight
                    node.active_connections = 0
                    logger.info(f"Updated existing backend node: HTTP={http_url}, gRPC={grpc_address}")
                    return

            node = BackendNode(http_url, grpc_address, weight)
            self.nodes.append(node)
            logger.info(f"Added backend node: HTTP={http_url}, gRPC={grpc_address}")

    def remove_node(self, http_url: str, grpc_address: str):
        """安全地从节点池中注销并移除工作节点。"""
        with self.modify_lock:
            clean_url = http_url.rstrip("/")
            new_nodes = []
            removed = False
            for node in self.nodes:
                if node.http_url == clean_url and node.grpc_address == grpc_address:
                    # 异步关闭该节点的通道（fire-and-forget，无需等待）
                    asyncio.create_task(node.close())  # noqa: RUF006
                    removed = True
                else:
                    new_nodes.append(node)
            self.nodes = new_nodes
            if removed:
                logger.info(f"Removed backend node: HTTP={http_url}, gRPC={grpc_address}")


    def get_healthy_nodes(self) -> list[BackendNode]:
        """获取当前健康的节点列表。"""
        return [node for node in self.nodes if node.is_healthy]

    async def select_node(self) -> BackendNode | None:
        """按策略选择一个健康的后端节点（协程安全）。

        Returns:
            若有可用健康节点返回 BackendNode，否则返回 None。
        """
        async with self.lock:
            healthy = self.get_healthy_nodes()
            if not healthy:
                return None

            if self.strategy == "random":
                return random.choice(healthy)

            elif self.strategy == "least_connections":
                # 选择当前活动连接数最少的一个节点
                return min(healthy, key=lambda n: n.active_connections)

            else:  # round_robin
                node = healthy[self.rr_index % len(healthy)]
                self.rr_index = (self.rr_index + 1) % len(healthy)
                return node

    async def close_all(self):
        """关闭所有后端的 gRPC 通道。"""
        for node in self.nodes:
            await node.close()


async def health_check_loop(balancer: LoadBalancer, interval: float = 5.0):
    """异步健康检查后台任务。

    定时向所有后端节点发送 HTTP 与 gRPC 健康请求，更新节点在线状态。

    Args:
        balancer: 关联的负载均衡实例。
        interval: 检测间隔时间（秒）。
    """
    logger.info("Starting background health check loop...")
    async with httpx.AsyncClient() as client:
        while True:
            for node in balancer.nodes:
                # 1. 检查 REST (HTTP) 服务
                http_ok = False
                try:
                    res = await client.get(f"{node.http_url}/health", timeout=2.0)
                    if res.status_code == 200:
                        data = res.json()
                        if data.get("status") == "ok":
                            http_ok = True
                except Exception as e:
                    logger.debug(f"HTTP health check failed for {node.http_url}: {e}")

                # 2. 检查 gRPC 服务
                grpc_ok = False
                try:
                    req = privacy_pb2.HealthRequest()
                    # 使用 2.0s 读写超时
                    res = await node.grpc_stub.Health(req, timeout=2.0)
                    if res.status == "ok":
                        grpc_ok = True
                except Exception as e:
                    logger.debug(f"gRPC health check failed for {node.grpc_address}: {e}")

                # 3. 状态决策与更替
                was_healthy = node.is_healthy
                node.is_healthy = http_ok and grpc_ok

                if was_healthy != node.is_healthy:
                    status_str = "healthy" if node.is_healthy else "unhealthy"
                    log_func = logger.info if node.is_healthy else logger.warning
                    log_func(
                        f"Node {node.grpc_address} status changed to {status_str} "
                        f"(HTTP: {'UP' if http_ok else 'DOWN'}, gRPC: {'UP' if grpc_ok else 'DOWN'})"
                    )

            await asyncio.sleep(interval)
