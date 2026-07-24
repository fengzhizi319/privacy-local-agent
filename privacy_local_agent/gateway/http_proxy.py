import asyncio
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel

from .balancer import LoadBalancer

logger = logging.getLogger("gateway.http")


# RFC 7230 规定的逐段传输头 (Hop-by-hop headers)，在代理转发时不应向下传递
EXCLUDE_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "content-length",
    "host",
}


class RegisterRequest(BaseModel):
    """节点动态注册请求模型。"""
    http_url: str
    grpc_address: str
    weight: int = 1


class DeregisterRequest(BaseModel):
    """节点动态销毁请求模型。"""
    http_url: str
    grpc_address: str


def create_http_gateway_app(balancer: LoadBalancer) -> FastAPI:
    """创建并初始化 HTTP 网关 FastAPI 应用。

    Args:
        balancer: 关联的负载均衡实例。

    Returns:
        初始化后的 FastAPI 应用实例。
    """
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # 初始化应用级单例 HTTP 客户端，并优化连接池配置
        app.state.http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            limits=httpx.Limits(max_keepalive_connections=100, max_connections=500),
            trust_env=False,  # 禁用环境变量代理，防止本地转发流量被拦截
        )
        yield
        # 优雅释放连接池
        await app.state.http_client.aclose()

    app = FastAPI(title="SecretFlow Local Privacy Agent REST Gateway", lifespan=lifespan)

    @app.post("/v1/gateway/register")
    async def register_node(req: RegisterRequest):
        """动态注册一个新的工作节点到地址池中。"""
        balancer.add_node(req.http_url, req.grpc_address, req.weight)
        return {"status": "registered"}

    @app.post("/v1/gateway/deregister")
    async def deregister_node(req: DeregisterRequest):
        """从地址池中安全移除注销的工作节点。"""
        balancer.remove_node(req.http_url, req.grpc_address)
        return {"status": "deregistered"}

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD", "PATCH"])
    async def proxy_request(path: str, request: Request):
        """通配路由，代理并转发所有 HTTP 方法的请求，支持故障重试与被动检测。"""
        max_retries = 3
        method = request.method
        query_params = request.query_params

        # 提取原请求 headers，排除 Hop-by-hop 头
        headers = {}
        for k, v in request.headers.items():
            if k.lower() not in EXCLUDE_HEADERS:
                headers[k] = v

        # 仅读取一次请求 body，供重试使用
        body = await request.body()
        last_exception = None

        for attempt in range(max_retries):
            node = await balancer.select_node()
            if not node:
                logger.error("HTTP proxy request failed: No healthy backend nodes available")
                raise HTTPException(status_code=503, detail="No healthy backend nodes available")

            # 获取或延迟初始化应用级单例 HTTP 客户端，以兼容未触发 lifespan 的测试环境
            # 若当前协程运行的 Event Loop 与缓存客户端创建时的 Event Loop 不同（多测试环境常见），则重建连接池
            current_loop = asyncio.get_running_loop()
            client = getattr(request.app.state, "http_client", None)
            cached_loop = getattr(request.app.state, "http_client_loop", None)

            if client is None or cached_loop is not current_loop:
                if client is not None:
                    # 在后台将已闭合 Event Loop 的旧客户端优雅释放（fire-and-forget）
                    asyncio.create_task(client.aclose())  # noqa: RUF006

                client = httpx.AsyncClient(
                    timeout=httpx.Timeout(30.0),
                    limits=httpx.Limits(max_keepalive_connections=100, max_connections=500),
                    trust_env=False,
                )
                request.app.state.http_client = client
                request.app.state.http_client_loop = current_loop


            # 增加节点活跃连接计数
            node.active_connections += 1
            url = f"{node.http_url}/{path}"
            try:
                resp = await client.request(
                    method=method,
                    url=url,
                    headers=headers,
                    params=query_params,
                    content=body,
                )


                # 构建并清洗响应 headers
                resp_headers = {}
                for k, v in resp.headers.items():
                    if k.lower() not in EXCLUDE_HEADERS:
                        resp_headers[k] = v

                return Response(
                    content=resp.content,
                    status_code=resp.status_code,
                    headers=resp_headers,
                )
            except Exception as exc:
                last_exception = exc
                logger.warning(
                    f"Attempt {attempt+1}/{max_retries} failed to forward HTTP request to {url}: {exc}. "
                    f"Marking node as unhealthy and retrying on another backend."
                )
                # 被动健康检查更新：立即将该节点置为不健康，使其从 select_node 候选池中消失
                node.is_healthy = False

            finally:
                # 递减连接计数
                node.active_connections -= 1

        # 若重试全部耗尽
        logger.error(f"HTTP proxy request failed after {max_retries} attempts. Last error: {last_exception}")
        raise HTTPException(
            status_code=502,
            detail=f"Bad Gateway: All {max_retries} backend retry attempts failed. Last error: {last_exception!s}",
        )

    return app
