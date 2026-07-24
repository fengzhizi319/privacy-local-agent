"""gRPC 泛化代理服务模块。

基于 grpc.aio (Python AsyncIO gRPC) 实现，承接所有客户端 gRPC 请求并动态分发给
后端健康的工作节点。支持故障重试、熔断器保护与 Prometheus 指标采集。

本模块不再为每个业务 RPC 手写转发方法，而是在初始化时自动为 ``PrivacyService``
下的全部 RPC 方法绑定统一的转发处理函数。privacy.proto 新增接口后，只要重新生成
Python 存根，网关即可自动转发，无需修改本文件。

gRPC generic proxy service module.

Built on grpc.aio, receives all client gRPC requests and dynamically dispatches
them to healthy backend worker nodes. Supports retry, circuit-breaker protection,
and Prometheus metrics instrumentation.
"""

from __future__ import annotations

import time

import grpc

from privacy_local_agent import privacy_pb2_grpc
from privacy_local_agent.observability.logging_config import get_logger
from privacy_local_agent.observability.metrics import (
    GATEWAY_LATENCY,
    GATEWAY_REQUESTS_TOTAL,
    GATEWAY_RETRIES_TOTAL,
)

from .balancer import LoadBalancer

logger = get_logger(__name__)


class GatewayGrpcServicer(privacy_pb2_grpc.PrivacyServiceServicer):
    """gRPC 网关泛化服务类 / gRPC gateway generic servicer.

    通过动态方法绑定实现 ``PrivacyService`` 下所有 RPC 方法的透明转发。
    Dynamically binds all ``PrivacyService`` RPC methods for transparent proxying.
    """

    def __init__(self, balancer: LoadBalancer):
        """初始化 gRPC Servicer / Initialize gRPC servicer.

        Args:
            balancer: 关联的负载均衡实例。
        """
        self.balancer = balancer
        self._bind_generic_methods()

    def _bind_generic_methods(self) -> None:
        """为 ``PrivacyService`` 中所有 RPC 方法绑定统一转发函数。

        生成后的 ``PrivacyServiceServicer`` 基类已为每个 RPC 方法提供默认的
        ``UNIMPLEMENTED`` 实现。此处遍历这些方法名并将其覆盖为 ``_forward`` 包装器，
        从而实现新增接口的自动转发。

        Bind a unified forwarding function to all RPC methods in ``PrivacyService``.
        """
        base = privacy_pb2_grpc.PrivacyServiceServicer
        for name in dir(base):
            if name.startswith("_"):
                continue
            attr = getattr(base, name)
            if not callable(attr):
                continue
            if name in ("__init__", "_bind_generic_methods", "_forward"):
                continue
            setattr(self, name, self._make_forwarder(name))

    def _make_forwarder(self, method_name: str):
        """构造给定 RPC 方法名的转发协程函数 / Build a forwarding coroutine for a method."""

        async def _generic_method(request, context):
            return await self._forward(method_name, request, context)

        return _generic_method

    async def _forward(self, method_name: str, request, context):
        """通用转发底层适配逻辑 / Generic forwarding adapter with retry and metrics.

        根据负载均衡策略选择后端，使用异步 gRPC 通道转发调用并回传结果。
        包含自适应重试、熔断器保护与故障转移机制。

        Selects a backend by load-balancing strategy, forwards via async gRPC channel,
        and returns the result. Includes adaptive retry, circuit-breaker protection,
        and failover.
        """
        max_retries = 3
        last_exception: Exception | None = None
        start_time = time.perf_counter()

        for attempt in range(max_retries):
            node = await self.balancer.select_node()
            if not node:
                duration = time.perf_counter() - start_time
                GATEWAY_REQUESTS_TOTAL.labels(protocol="grpc", method=method_name, status="UNAVAILABLE").inc()
                GATEWAY_LATENCY.labels(protocol="grpc").observe(duration)
                logger.error(
                    "No healthy gRPC nodes available",
                    extra={"method": method_name},
                )
                await context.abort(
                    grpc.StatusCode.UNAVAILABLE,
                    "No healthy backend nodes available",
                )

            assert node is not None
            node.active_connections += 1
            try:
                stub_method = getattr(node.grpc_stub, method_name)
                # 提取客户端请求中携带的元数据并转发
                metadata = None
                if hasattr(context, "invocation_metadata") and callable(context.invocation_metadata):
                    metadata = context.invocation_metadata()

                call = stub_method(request, timeout=30.0, metadata=metadata)
                response = await call

                # 将后端的响应头与响应尾元数据透传给客户端
                try:
                    initial_md = await call.initial_metadata()
                    if initial_md and hasattr(context, "send_initial_metadata") and callable(
                        context.send_initial_metadata
                    ):
                        await context.send_initial_metadata(initial_md)
                except Exception as e:
                    logger.debug(
                        "Failed to forward initial metadata",
                        extra={"method": method_name, "error": str(e)},
                    )

                try:
                    trailing_md = await call.trailing_metadata()
                    if trailing_md and hasattr(context, "set_trailing_metadata") and callable(
                        context.set_trailing_metadata
                    ):
                        context.set_trailing_metadata(trailing_md)
                except Exception as e:
                    logger.debug(
                        "Failed to forward trailing metadata",
                        extra={"method": method_name, "error": str(e)},
                    )

                # 记录成功指标
                duration = time.perf_counter() - start_time
                GATEWAY_REQUESTS_TOTAL.labels(protocol="grpc", method=method_name, status="OK").inc()
                GATEWAY_LATENCY.labels(protocol="grpc").observe(duration)
                node.circuit_breaker.record_success()
                return response

            except grpc.RpcError as exc:
                if exc.code() == grpc.StatusCode.UNAVAILABLE:
                    last_exception = exc
                    node.circuit_breaker.record_failure()
                    GATEWAY_RETRIES_TOTAL.labels(protocol="grpc", reason="unavailable").inc()
                    logger.warning(
                        "gRPC forward attempt failed (UNAVAILABLE), retrying",
                        extra={
                            "method": method_name,
                            "attempt": attempt + 1,
                            "max_retries": max_retries,
                            "node": node.grpc_address,
                            "circuit_breaker": node.circuit_breaker.state,
                        },
                    )
                    node.is_healthy = False
                else:
                    # 正常的业务级/参数类错误，无需重试，直接透传
                    duration = time.perf_counter() - start_time
                    GATEWAY_REQUESTS_TOTAL.labels(
                        protocol="grpc", method=method_name, status=exc.code().name
                    ).inc()
                    GATEWAY_LATENCY.labels(protocol="grpc").observe(duration)
                    await context.abort(exc.code(), exc.details())
            except Exception as exc:
                last_exception = exc
                node.circuit_breaker.record_failure()
                GATEWAY_RETRIES_TOTAL.labels(protocol="grpc", reason="unexpected_error").inc()
                logger.warning(
                    "gRPC forward attempt failed (unexpected), retrying",
                    extra={
                        "method": method_name,
                        "attempt": attempt + 1,
                        "max_retries": max_retries,
                        "node": node.grpc_address,
                        "error": str(exc),
                        "circuit_breaker": node.circuit_breaker.state,
                    },
                )
                node.is_healthy = False
            finally:
                node.active_connections -= 1

        # 若全部重试机会已耗尽
        duration = time.perf_counter() - start_time
        GATEWAY_REQUESTS_TOTAL.labels(protocol="grpc", method=method_name, status="INTERNAL").inc()
        GATEWAY_LATENCY.labels(protocol="grpc").observe(duration)
        logger.error(
            "gRPC forward failed after all retries",
            extra={"method": method_name, "max_retries": max_retries, "last_error": str(last_exception)},
        )
        if isinstance(last_exception, grpc.RpcError):
            await context.abort(last_exception.code(), last_exception.details())
        else:
            await context.abort(
                grpc.StatusCode.INTERNAL,
                f"Gateway internal error after {max_retries} attempts: {last_exception}",
            )


async def start_grpc_gateway(
    host: str,
    port: int,
    balancer: LoadBalancer,
    tls_enabled: bool = False,
    tls_cert_file: str = "",
    tls_key_file: str = "",
    tls_ca_file: str = "",
) -> grpc.aio.Server:
    """初始化并启动异步 gRPC 网关服务器 / Start async gRPC gateway server.

    支持可选的 TLS 终结与 mTLS 客户端证书验证。
    Supports optional TLS termination and mTLS client certificate verification.

    Args:
        host: 绑定监听的主机名。
        port: 端口号。
        balancer: 关联的负载均衡实例。
        tls_enabled: 是否启用 TLS 终结。
        tls_cert_file: 服务器证书文件路径。
        tls_key_file: 服务器私钥文件路径。
        tls_ca_file: CA 证书文件路径（用于 mTLS 客户端验证）。

    Returns:
        启动后的 gRPC 异步服务器实例。
    """
    server = grpc.aio.server()
    privacy_pb2_grpc.add_PrivacyServiceServicer_to_server(
        GatewayGrpcServicer(balancer), server
    )

    if tls_enabled and tls_cert_file and tls_key_file:
        # 读取证书和私钥 / Read cert and key
        with open(tls_cert_file, "rb") as f:
            cert_chain = f.read()
        with open(tls_key_file, "rb") as f:
            private_key = f.read()

        # 如果提供 CA 证书，启用 mTLS 客户端验证
        root_certificates = None
        if tls_ca_file:
            with open(tls_ca_file, "rb") as f:
                root_certificates = f.read()

        credentials = grpc.ssl_server_credentials(
            [(private_key, cert_chain)],
            root_certificates=root_certificates,
            require_client_auth=bool(root_certificates),
        )
        server.add_secure_port(f"{host}:{port}", credentials)
        logger.info(
            "Gateway gRPC server started with TLS",
            extra={"host": host, "port": port, "mtls": bool(root_certificates)},
        )
    else:
        server.add_insecure_port(f"{host}:{port}")
        logger.info(
            "Gateway gRPC server started",
            extra={"host": host, "port": port},
        )

    await server.start()
    return server
"""gRPC 泛化代理服务模块。

基于 grpc.aio (Python AsyncIO gRPC) 实现，承接所有客户端 gRPC 请求并动态分发给后端健康的工作节点。

本模块不再为每个业务 RPC 手写转发方法，而是在初始化时自动为 ``PrivacyService``
下的全部 RPC 方法绑定统一的转发处理函数。privacy.proto 新增接口后，只要重新生成
Python 存根，网关即可自动转发，无需修改本文件。
"""

import logging

import grpc

from privacy_local_agent import privacy_pb2_grpc

from .balancer import LoadBalancer

logger = logging.getLogger("gateway.grpc")


class GatewayGrpcServicer(privacy_pb2_grpc.PrivacyServiceServicer):
    """gRPC 网关泛化服务类。

    通过动态方法绑定实现 ``PrivacyService`` 下所有 RPC 方法的透明转发。
    """

    def __init__(self, balancer: LoadBalancer):
        """初始化 gRPC Servicer。

        Args:
            balancer: 关联的负载均衡实例。
        """
        self.balancer = balancer
        self._bind_generic_methods()

    def _bind_generic_methods(self) -> None:
        """为 ``PrivacyService`` 中所有 RPC 方法绑定统一转发函数。

        生成后的 ``PrivacyServiceServicer`` 基类已为每个 RPC 方法提供默认的
        ``UNIMPLEMENTED`` 实现。此处遍历这些方法名并将其覆盖为 ``_forward`` 包装器，
        从而实现新增接口的自动转发。
        """
        base = privacy_pb2_grpc.PrivacyServiceServicer
        for name in dir(base):
            if name.startswith("_"):
                continue
            attr = getattr(base, name)
            if not callable(attr):
                continue
            # 只覆盖基类定义的方法；保留对象自身的特殊属性与方法
            if name in ("__init__", "_bind_generic_methods", "_forward"):
                continue
            setattr(self, name, self._make_forwarder(name))

    def _make_forwarder(self, method_name: str):
        """构造给定 RPC 方法名的转发协程函数。"""
        async def _generic_method(request, context):
            return await self._forward(method_name, request, context)

        return _generic_method

    async def _forward(self, method_name: str, request, context):
        """通用转发底层适配逻辑。

        根据负载均衡策略选择后端，使用异步 gRPC 通道转发调用并回传结果。
        包含自适应重试与故障转移机制，当遇到连接断开时进行被动健康检查下线并重试。
        """
        max_retries = 3
        last_exception = None

        for attempt in range(max_retries):
            node = await self.balancer.select_node()
            if not node:
                logger.error(f"gRPC forward for {method_name} failed: No healthy nodes available")
                await context.abort(
                    grpc.StatusCode.UNAVAILABLE,
                    "No healthy backend nodes available",
                )

            assert node is not None
            # 增加活跃连接数计数
            node.active_connections += 1
            try:
                # 使用反射获取所选节点的客户端对应 RPC 方法
                stub_method = getattr(node.grpc_stub, method_name)
                # 提取客户端请求中携带的元数据并转发（兼容单元测试中的 MockContext）
                metadata = None
                if hasattr(context, "invocation_metadata") and callable(context.invocation_metadata):
                    metadata = context.invocation_metadata()

                # 发起异步转发调用，指定 30 秒超时，返回 Call 对象
                call = stub_method(request, timeout=30.0, metadata=metadata)
                response = await call

                # 将后端的响应头与响应尾元数据透传给客户端
                try:
                    initial_md = await call.initial_metadata()
                    if initial_md and hasattr(context, "send_initial_metadata") and callable(
                        context.send_initial_metadata
                    ):
                        await context.send_initial_metadata(initial_md)
                except Exception as e:
                    logger.debug(f"Failed to forward initial metadata for {method_name}: {e}")

                try:
                    trailing_md = await call.trailing_metadata()
                    if trailing_md and hasattr(context, "set_trailing_metadata") and callable(
                        context.set_trailing_metadata
                    ):
                        context.set_trailing_metadata(trailing_md)
                except Exception as e:
                    logger.debug(f"Failed to forward trailing metadata for {method_name}: {e}")

                return response
            except grpc.RpcError as exc:
                # 如果是连接性不可用错误，进行故障转移与重试
                if exc.code() == grpc.StatusCode.UNAVAILABLE:
                    last_exception = exc
                    logger.warning(
                        f"Attempt {attempt+1}/{max_retries} failed to forward gRPC "
                        f"{method_name} to {node.grpc_address}: UNAVAILABLE. "
                        f"Marking node as unhealthy and retrying."
                    )
                    node.is_healthy = False
                else:
                    # 正常的业务级/参数类错误，无需重试，直接透传
                    await context.abort(exc.code(), exc.details())
            except Exception as exc:
                last_exception = exc
                logger.warning(
                    f"Attempt {attempt+1}/{max_retries} failed with unexpected "
                    f"exception forwarding gRPC {method_name} to {node.grpc_address}: {exc}. "
                    f"Marking node as unhealthy and retrying."
                )
                node.is_healthy = False
            finally:
                # 减少连接数计数
                node.active_connections -= 1

        # 若全部重试机会已耗尽
        logger.error(f"gRPC forward for {method_name} failed after {max_retries} attempts.")
        if isinstance(last_exception, grpc.RpcError):
            await context.abort(last_exception.code(), last_exception.details())
        else:
            await context.abort(
                grpc.StatusCode.INTERNAL,
                f"Gateway internal error after {max_retries} attempts: {last_exception}",
            )


async def start_grpc_gateway(host: str, port: int, balancer: LoadBalancer) -> grpc.aio.Server:
    """初始化并启动异步 gRPC 网关服务器。

    Args:
        host: 绑定监听的主机名。
        port: 端口号。
        balancer: 关联的负载均衡实例。

    Returns:
        启动后的 gRPC 异步服务器实例。
    """
    server = grpc.aio.server()
    privacy_pb2_grpc.add_PrivacyServiceServicer_to_server(
        GatewayGrpcServicer(balancer), server
    )
    server.add_insecure_port(f"{host}:{port}")
    await server.start()
    logger.info(f"Gateway gRPC server started on {host}:{port}")
    return server
