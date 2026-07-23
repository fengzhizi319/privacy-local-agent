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

import asyncio
import base64
import random
import time
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .client import agent_client
from .config import settings
from .fixtures.samples import get_samples

# 本控制台后端的身份标识，随每个响应下发给前端，
# 用于在界面上明确展示“当前请求由哪个后端、以何种协议与 agent 通信”，
# 从而让 Python REST / Go gRPC 两种通信方式的切换可被直观验证。
BACKEND_VIA = "python-rest"
AGENT_PROTOCOL = "REST"


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
    # 处理本请求的控制台后端标识（python-rest）与其和 agent 的通信协议（REST），
    # 供前端展示，便于验证后端切换是否生效。
    via: str = Field(default=BACKEND_VIA)
    protocol: str = Field(default=AGENT_PROTOCOL)


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
    # 同 ProxyResponse：标识处理请求的后端与通信协议。
    via: str = Field(default=BACKEND_VIA)
    protocol: str = Field(default=AGENT_PROTOCOL)


class LbBackend(BaseModel):
    """负载均衡测试中的单个目标后端节点。

    ``name`` 用于在结果分布中标识节点，``url`` 为节点的 REST 基地址
    （如 ``http://127.0.0.1:8079``）。
    """

    name: str
    url: str


class LbTestRequest(BaseModel):
    """负载均衡测试端点 ``POST /api/lb_test`` 的请求体。

    控制台后端按 ``strategy`` 策略把 ``num_requests`` 个探测请求分发到
    ``backends`` 中的各节点，并统计命中与延迟：
        - ``probe_path``：探测路径，默认 ``/health``；
        - ``probe_body``：提供时以 ``POST`` 发送 JSON 体，否则用 ``GET``。
    """

    backends: List[LbBackend] = Field(default_factory=list)
    num_requests: int = Field(default=10, ge=1, le=1000)
    strategy: str = Field(default="round_robin")
    probe_path: str = Field(default="/health")
    probe_body: Optional[Dict[str, Any]] = Field(default=None)


class LbDistItem(BaseModel):
    """负载均衡测试中单个节点的统计结果。

    记录该节点被命中的次数、成功 / 失败数以及延迟分布（毫秒）。
    未被命中的节点 ``count`` 为 0，延迟字段为 0。
    """

    name: str
    url: str
    count: int
    success: int
    failed: int
    avg_latency_ms: float
    min_latency_ms: float
    max_latency_ms: float


class LbTestResponse(BaseModel):
    """负载均衡测试的汇总结果。

    ``distribution`` 按 ``backends`` 顺序给出各节点的统计，
    三者关系恒为 ``total == success + failed``。
    """

    strategy: str
    total: int
    success: int
    failed: int
    duration_ms: float
    distribution: List[LbDistItem]


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
            "via": BACKEND_VIA,
            "protocol": AGENT_PROTOCOL,
        }
    except HTTPException as exc:
        return JSONResponse(
            status_code=200,
            content={
                "backend": "ok",
                "agent": "unreachable",
                "agent_url": settings.privacy_agent_url,
                "error": exc.detail,
                "via": BACKEND_VIA,
                "protocol": AGENT_PROTOCOL,
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


@app.post("/api/upload")
async def upload(
    file: UploadFile = File(...),
    operation: str = Form(...),
    params: str = Form("{}"),
):
    """数据文件隐私处理：接收上传文件并转发到 agent 的 ``process_file`` 端点。

    前端以 multipart 上传 CSV/JSON 文件与操作类型，后端读取文件内容后
    以 multipart 透传给 agent ``/v1/privacy/process_file``，并把 agent 返回
    的 ``{operation, rows_in, rows_out, result}`` 包装为 :class:`ProxyResponse`。
    具体的文件解析与隐私算法均由 agent 负责，后端仅做转发与包装。
    """
    content = await file.read()
    files = {"file": (file.filename or "upload.bin", content, file.content_type or "application/octet-stream")}
    data = {"operation": operation, "params": params}

    start = time.perf_counter()
    result = await agent_client.request_multipart(
        "/v1/privacy/process_file", files=files, data=data
    )
    duration_ms = (time.perf_counter() - start) * 1000

    return ProxyResponse(status=200, duration_ms=round(duration_ms, 2), data=result)


# 负载均衡测试支持的三种分发策略。
_LB_STRATEGIES = ("round_robin", "random", "least_connections")


def _lb_pick_backends(strategy: str, n: int, num_backends: int) -> List[int]:
    """按策略生成 ``n`` 个探测请求对应的后端下标序列。

    - ``round_robin``：依次轮询，分发最均匀；
    - ``random``：独立随机选择；
    - ``least_connections``：每次选当前累计命中最少的节点（同数取下标小者），
      效果上亦趋于均匀。
    """
    if num_backends <= 0:
        return []
    if strategy == "round_robin":
        return [i % num_backends for i in range(n)]
    if strategy == "random":
        return [random.randrange(num_backends) for _ in range(n)]
    if strategy == "least_connections":
        counts = [0] * num_backends
        seq: List[int] = []
        for _ in range(n):
            idx = min(range(num_backends), key=lambda i: (counts[i], i))
            counts[idx] += 1
            seq.append(idx)
        return seq
    raise HTTPException(
        status_code=400,
        detail=f"不支持的策略 '{strategy}'，可选: {list(_LB_STRATEGIES)}",
    )


async def _run_lb_test(
    req: LbTestRequest,
    transport: Optional[httpx.AsyncBaseTransport] = None,
) -> LbTestResponse:
    """执行负载均衡探测并统计各节点命中与延迟。

    探测逻辑与端点解耦，``transport`` 可注入（测试时用 ``httpx.MockTransport``
    伪造后端），生产环境为 ``None`` 即走真实网络。
    """
    backends = req.backends
    if not backends:
        raise HTTPException(status_code=400, detail="backends 不能为空")

    seq = _lb_pick_backends(req.strategy, req.num_requests, len(backends))
    latencies: Dict[int, List[float]] = {i: [] for i in range(len(backends))}
    success: Dict[int, int] = {i: 0 for i in range(len(backends))}
    failed: Dict[int, int] = {i: 0 for i in range(len(backends))}

    probe_path = req.probe_path or "/health"

    async def probe(idx: int) -> None:
        backend = backends[idx]
        url = backend.url.rstrip("/") + probe_path
        start = time.perf_counter()
        ok = False
        try:
            if req.probe_body is not None:
                resp = await lb_client.post(url, json=req.probe_body)
            else:
                resp = await lb_client.get(url)
            ok = resp.status_code < 400
        except httpx.HTTPError:
            ok = False
        latencies[idx].append((time.perf_counter() - start) * 1000)
        if ok:
            success[idx] += 1
        else:
            failed[idx] += 1

    overall_start = time.perf_counter()
    async with httpx.AsyncClient(
        transport=transport, timeout=10.0, trust_env=False
    ) as lb_client:
        await asyncio.gather(*(probe(i) for i in seq))
    total_ms = (time.perf_counter() - overall_start) * 1000

    distribution: List[LbDistItem] = []
    total_success = 0
    total_failed = 0
    for i, backend in enumerate(backends):
        lats = latencies[i]
        count = len(lats)
        total_success += success[i]
        total_failed += failed[i]
        distribution.append(
            LbDistItem(
                name=backend.name,
                url=backend.url,
                count=count,
                success=success[i],
                failed=failed[i],
                avg_latency_ms=round(sum(lats) / count, 2) if count else 0.0,
                min_latency_ms=round(min(lats), 2) if lats else 0.0,
                max_latency_ms=round(max(lats), 2) if lats else 0.0,
            )
        )

    return LbTestResponse(
        strategy=req.strategy,
        total=req.num_requests,
        success=total_success,
        failed=total_failed,
        duration_ms=round(total_ms, 2),
        distribution=distribution,
    )


@app.post("/api/lb_test")
async def lb_test(req: LbTestRequest):
    """负载均衡测试：按策略向多个后端节点分发探测请求并统计结果。

    由控制台后端自行实现策略分发（round_robin / random / least_connections），
    探测目标为用户填写的各 agent REST 地址，返回各节点命中数与延迟分布，
    供前端可视化对比。
    """
    return await _run_lb_test(req)


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

        index.html 不带内容哈希，必须禁止浏览器缓存（no-cache），
        否则重新构建前端后浏览器仍会加载旧版本。
        带哈希的 /assets/* 资源则由浏览器正常缓存（内容变则 URL 变）。
        """
        index_file = static_dir / "index.html"
        if index_file.exists():
            return FileResponse(
                str(index_file),
                headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
            )
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
