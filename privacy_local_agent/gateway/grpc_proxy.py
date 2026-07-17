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

            # 增加活跃连接数计数
            node.active_connections += 1
            try:
                # 使用反射获取所选节点的客户端对应 RPC 方法
                stub_method = getattr(node.grpc_stub, method_name)
                # 发起异步转发调用，指定 30 秒超时
                response = await stub_method(request, timeout=30.0)
                return response
            except grpc.RpcError as exc:
                # 如果是连接性不可用错误，进行故障转移与重试
                if exc.code() == grpc.StatusCode.UNAVAILABLE:
                    last_exception = exc
                    logger.warning(
                        f"Attempt {attempt+1}/{max_retries} failed to forward gRPC {method_name} to {node.grpc_address}: UNAVAILABLE. "
                        f"Marking node as unhealthy and retrying."
                    )
                    node.is_healthy = False
                else:
                    # 正常的业务级/参数类错误，无需重试，直接透传
                    await context.abort(exc.code(), exc.details())
            except Exception as exc:
                last_exception = exc
                logger.warning(
                    f"Attempt {attempt+1}/{max_retries} failed with unexpected exception forwarding gRPC {method_name} to {node.grpc_address}: {exc}. "
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
