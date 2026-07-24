"""控制台后端可选安全中间件：API Key 鉴权 + 内存限流。

中文说明：
    本模块提供 :class:`ConsoleSecurityMiddleware`，为控制台后端补充两道
    可选防线。两者均**默认关闭 / 宽松**，仅在配置了相应环境变量时生效，
    以保证本地开发体验与现有批量测试流程不受影响：

    - **API Key 鉴权**：``CONSOLE_API_KEY`` 设置时，``/api/*``（除 ``/api/health``）
      需携带 ``Authorization: Bearer <key>``；未设置则完全放行。
    - **限流**：``CONSOLE_RATE_LIMIT`` 设置每分钟每客户端 IP 的最大请求数
      （默认 600，设为 0 关闭）；超限返回 429。``/api/health`` 与 CORS 预检豁免。

    限流采用进程内滑动窗口（deque 记录时间戳），适用于单进程本地场景；
    如需多副本部署的分布式限流，应替换为 Redis 等共享存储后端。
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import TYPE_CHECKING, cast

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

if TYPE_CHECKING:
    from starlette.requests import Request


def _extract_bearer(header_value: str | None) -> str | None:
    """从 Authorization 头中提取 Bearer token，格式不符时返回 None。"""
    if not header_value:
        return None
    parts = header_value.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return None


class ConsoleSecurityMiddleware(BaseHTTPMiddleware):
    """可选的 API Key 鉴权 + 限流中间件（默认关闭 / 宽松）。

    Args:
        app: ASGI 应用（由 Starlette 注入）。
        api_key: 控制台 API Key；为 ``None`` 时不做鉴权（默认）。
        rate_limit: 每分钟每 IP 最大请求数；``<= 0`` 时关闭限流。
    """

    def __init__(self, app, api_key: str | None = None, rate_limit: int = 600) -> None:
        super().__init__(app)
        self._api_key = api_key
        self._rate_limit = rate_limit
        # 每个客户端 IP 的请求时间戳队列（60 秒滑动窗口）。
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    @staticmethod
    def _client_ip(request: Request) -> str:
        """取客户端 IP 作为限流键；缺失时退化为固定键。"""
        if request.client:
            return request.client.host
        return "unknown"

    def _rate_limited(self, ip: str) -> bool:
        """判断指定 IP 当前是否触发限流（未触发则记录本次请求时间戳）。"""
        if self._rate_limit <= 0:
            return False
        now = time.monotonic()
        window = self._hits[ip]
        # 清除 60 秒窗口外的旧记录。
        while window and now - window[0] > 60.0:
            window.popleft()
        if len(window) >= self._rate_limit:
            return True
        window.append(now)
        return False

    async def dispatch(self, request: Request, call_next) -> Response:
        """请求入口：按序执行 CORS 预检放行、鉴权、限流。"""
        # CORS 预检请求直接放行，交由 CORSMiddleware 处理。
        if request.method == "OPTIONS":
            return cast("Response", await call_next(request))

        path = request.url.path
        # 仅对 /api/* 生效（静态资源等不拦截）；健康检查豁免。
        if not path.startswith("/api/") or path == "/api/health":
            return cast("Response", await call_next(request))

        # API Key 鉴权（配置了才校验）。
        if self._api_key is not None:
            token = _extract_bearer(request.headers.get("authorization"))
            if token != self._api_key:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Unauthorized: invalid console api key"},
                )

        # 限流。
        if self._rate_limited(self._client_ip(request)):
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests"},
            )

        return cast("Response", await call_next(request))
