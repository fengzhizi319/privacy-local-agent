"""Privacy 测试控制台的 FastAPI 后端入口。

职责概述：
    1. 以静态资源形式挂载构建好的 React SPA（``web/dist``），使浏览器
       能够直接通过本后端访问控制台页面；
    2. 作为“代理层”，把前端发来的 ``/api/proxy`` / ``/api/batch`` 请求
       透明转发到运行中的 ``privacy-local-agent`` REST 服务；
    3. 提供 ``/api/health``（连通性检查）与 ``/api/samples``（示例数据）两个辅助接口。

设计要点：
    - 后端本身不实现任何隐私算法，仅负责转发与格式适配，保持轻量；
    - 所有请求/响应均使用 Pydantic v2 模型校验，作为输入安全的第一道防线；
    - 静态资源目录不存在时（如仅后端开发场景）应用仍可正常启动，仅提供 API。

端点示例数据定义于 :mod:`app.fixtures.samples`。
"""

from __future__ import annotations

import base64
import time
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .client import agent_client
from .config import settings
from .fixtures.samples import get_samples


class ProxyRequest(BaseModel):
    """通用代理端点 ``POST /api/proxy`` 的请求体。

    前端把“想发给 agent 的请求”封装成该结构：
        - ``method`` / ``path``：目标 HTTP 方法与路径（如 ``/v1/privacy/mask``）；
        - ``body``：JSON 请求体（与 ``raw_payload_b64`` 二选一）；
        - ``raw_payload_b64``：二进制载荷的 base64 编码（如 Arrow IPC），
          配合 ``content_type`` 使用，用于无法用 JSON 表达的场景。
    """

    method: str = Field(..., examples=["POST"])
    path: str = Field(..., examples=["/v1/privacy/mask"])
    body: Optional[Dict[str, Any]] = Field(default=None)
    raw_payload_b64: Optional[str] = Field(default=None)
    content_type: Optional[str] = Field(default=None)


class ProxyResponse(BaseModel):
    """通用代理端点的统一响应包装。

    无论 agent 返回什么内容，都会被包装成该结构，便于前端统一解析：
        - ``status``：转发后的逻辑状态码（成功为 200）；
        - ``duration_ms``：本次转发耗时（毫秒），用于前端性能展示；
        - ``data``：agent 返回的原始数据（JSON / Arrow 解析结果 / base64）。
    """

    status: int
    duration_ms: float
    data: Any


class BatchRequestItem(BaseModel):
    """批量请求中的单个子请求。

    结构与 :class:`ProxyRequest` 类似，但批量场景只支持 JSON 请求体
    （不支持二进制载荷），因为批量测试面向的是常规 JSON 接口。
    """

    method: str = Field(default="POST")
    path: str
    body: Optional[Dict[str, Any]] = Field(default=None)


class BatchRequest(BaseModel):
    """批量代理端点 ``POST /api/batch`` 的请求体。

    ``requests`` 中的子请求会被**顺序**逐个转发到 agent。
    默认工厂为空列表，避免可变默认参数陷阱。
    """

    requests: List[BatchRequestItem] = Field(default_factory=list)


class BatchResultItem(BaseModel):
    """批量执行中单个子请求的结果。

    成功时 ``data`` 存放 agent 返回数据，``error`` 为 ``None``；
    失败时 ``error`` 存放错误描述，``status`` 为对应的 HTTP 状态码。
    """

    method: str
    path: str
    status: int
    duration_ms: float
    data: Any = None
    error: Optional[str] = None


class BatchResponse(BaseModel):
    """批量执行的汇总结果。

    ``passed`` 统计状态码在 2xx 区间的子请求数，``failed`` 为其余部分，
    三者关系恒为 ``total == passed + failed``。
    """

    total: int
    passed: int
    failed: int
    results: List[BatchResultItem]


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理：启动时预热 HTTP 客户端，关闭时释放连接池。

    在 ``yield`` 之前创建 ``httpx.AsyncClient``（连接池），避免首个请求
    才懒初始化带来的额外延迟；``yield`` 之后（应用退出时）优雅关闭客户端。
    """
    _ = await agent_client._get_client()
    yield
    if agent_client._client is not None:
        await agent_client._client.aclose()


app = FastAPI(title="Privacy Test Console", lifespan=lifespan)

# 允许 Vite 开发服务器（默认 5173 端口）跨域调用本后端。
# 生产环境下控制台与后端同源部署，该中间件不会带来额外风险。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health():
    """检查后端自身与下游 agent 的连通性。

    返回结构：
        - ``backend``：后端自身状态，恒为 ``"ok"``（能响应即存活）；
        - ``agent``：agent 的 ``/health`` 返回内容，不可达时为 ``"unreachable"``；
        - ``agent_url``：配置的 agent REST 地址，便于排查连错目标；
        - ``latency_ms``：探测 agent 的往返耗时。

    注意：agent 不可达时仍返回 **HTTP 200**（而非 5xx），以便前端能
    够读取 ``agent == "unreachable"`` 并展示友好提示，而不是直接报错。
    """
    start = time.perf_counter()
    try:
        agent_health = await agent_client.request("GET", "/health")
        duration_ms = (time.perf_counter() - start) * 1000
        return {
            "backend": "ok",
            "agent": agent_health,
            "agent_url": settings.privacy_agent_url,
            "latency_ms": round(duration_ms, 2),
        }
    except HTTPException as exc:
        return JSONResponse(
            status_code=200,
            content={
                "backend": "ok",
                "agent": "unreachable",
                "agent_url": settings.privacy_agent_url,
                "error": exc.detail,
            },
        )


@app.get("/api/samples")
async def samples():
    """返回所有端点的示例数据（按功能分类）。

    前端启动时调用该接口获取全部可测试的端点及其默认请求体，
    数据源为 :func:`app.fixtures.samples.get_samples`。
    """
    return {"samples": get_samples()}


@app.post("/api/proxy")
async def proxy(req: ProxyRequest):
    """通用代理：把一个请求转发到 privacy-local-agent REST 服务。

    处理流程：
        1. 若携带 ``raw_payload_b64``，先 base64 解码为二进制载荷
           （解码失败返回 400）；
        2. 调用 :meth:`agent_client.request` 转发，自动区分 JSON / 二进制；
        3. 记录转发耗时，包装为 :class:`ProxyResponse` 返回。

    代理透明支持 JSON 与二进制（如 Arrow IPC）载荷；agent 侧的错误
    状态码会被 :class:`HTTPException` 透传给前端。
    """
    method = req.method.upper()
    path = req.path

    raw_content: Optional[bytes] = None
    if req.raw_payload_b64:
        try:
            raw_content = base64.b64decode(req.raw_payload_b64)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid base64 payload: {exc}") from exc

    start = time.perf_counter()
    try:
        result = await agent_client.request(
            method=method,
            path=path,
            body=req.body,
            raw_content=raw_content,
            content_type=req.content_type,
        )
    except HTTPException as exc:
        # Re-raise as HTTPException so FastAPI returns the right status/detail.
        raise
    duration_ms = (time.perf_counter() - start) * 1000

    return ProxyResponse(status=200, duration_ms=round(duration_ms, 2), data=result)


@app.post("/api/batch")
async def batch(req: BatchRequest):
    """Run a list of requests against the agent sequentially.

    用于前端“一键批量测试”：逐个转发请求并汇总成功 / 失败统计，
    单个请求失败不会中断整个批次。
    """
    results: List[BatchResultItem] = []
    for item in req.requests:
        method = item.method.upper()
        start = time.perf_counter()
        try:
            data = await agent_client.request(method=method, path=item.path, body=item.body)
            duration_ms = (time.perf_counter() - start) * 1000
            results.append(
                BatchResultItem(
                    method=method,
                    path=item.path,
                    status=200,
                    duration_ms=round(duration_ms, 2),
                    data=data,
                )
            )
        except HTTPException as exc:
            duration_ms = (time.perf_counter() - start) * 1000
            results.append(
                BatchResultItem(
                    method=method,
                    path=item.path,
                    status=exc.status_code,
                    duration_ms=round(duration_ms, 2),
                    error=str(exc.detail),
                )
            )
        except Exception as exc:  # noqa: BLE001 - 批量执行需吸收单个请求的任何异常
            duration_ms = (time.perf_counter() - start) * 1000
            results.append(
                BatchResultItem(
                    method=method,
                    path=item.path,
                    status=500,
                    duration_ms=round(duration_ms, 2),
                    error=str(exc),
                )
            )

    passed = sum(1 for r in results if 200 <= r.status < 300)
    return BatchResponse(total=len(results), passed=passed, failed=len(results) - passed, results=results)


# 静态 SPA 托管：把构建好的前端挂载到根路径。
# 采用“/assets 静态目录 + 其余路径回退 index.html”的经典 SPA 方案：
#   - ``/assets/*`` 直接返回带哈希的 JS/CSS 等构建产物（强缓存友好）；
#   - 其余非 API 路径一律返回 ``index.html``，由前端路由接管（SPA 回退）。
# 若目录不存在（如仅后端开发场景），应用仍可提供 API，不报错。
static_dir = settings.static_dist_dir.resolve()
if static_dir.exists() and static_dir.is_dir():
    app.mount("/assets", StaticFiles(directory=str(static_dir / "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(request: Request, full_path: str):
        """SPA 回退路由：所有未命中的路径都返回 index.html。

        该路由注册在最后，优先级最低，不会遮挡 ``/api/*`` 与 ``/assets/*``。
        index.html 不存在时返回 404（前端未构建）。
        """
        index_file = static_dir / "index.html"
        if index_file.exists():
            return FileResponse(str(index_file))
        raise HTTPException(status_code=404, detail="Frontend not built")


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """统一异常处理：返回结构化错误，便于前端展示。

    把 FastAPI 默认的错误响应规范化为 ``{"detail": ..., "status": ...}``，
    前端 ResponsePanel 依赖该结构解析并展示错误信息。
    """
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "status": exc.status_code},
    )
