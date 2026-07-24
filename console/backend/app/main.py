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
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .client import agent_client
from .config import settings
from .fixtures.samples import get_samples
from .security import ConsoleSecurityMiddleware

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
    body: dict[str, Any] | None = Field(default=None)
    raw_payload_b64: str | None = Field(default=None)
    content_type: str | None = Field(default=None)


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
    body: dict[str, Any] | None = Field(default=None)


class BatchRequest(BaseModel):
    """批量代理端点 ``POST /api/batch`` 的请求体。

    ``requests`` 中的子请求会被**顺序**逐个转发到 agent。
    默认工厂为空列表，避免可变默认参数陷阱。
    """

    requests: list[BatchRequestItem] = Field(default_factory=list)


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
    error: str | None = None


class BatchResponse(BaseModel):
    """批量执行的汇总结果。

    ``passed`` 统计状态码在 2xx 区间的子请求数，``failed`` 为其余部分，
    三者关系恒为 ``total == passed + failed``。
    """

    total: int
    passed: int
    failed: int
    results: list[BatchResultItem]
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

    backends: list[LbBackend] = Field(default_factory=list)
    num_requests: int = Field(default=10, ge=1, le=1000)
    strategy: str = Field(default="round_robin")
    probe_path: str = Field(default="/health")
    probe_body: dict[str, Any] | None = Field(default=None)


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
    distribution: list[LbDistItem]


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理：启动时预热 HTTP 客户端，关闭时释放连接池。

    在 ``yield`` 之前创建 ``httpx.AsyncClient``（连接池），避免首个请求
    才懒初始化带来的额外延迟；``yield`` 之后（应用退出时）优雅关闭客户端。
    """
    # 启动阶段：提前创建 httpx 连接池（预热），
    # 避免首个真实请求才懒初始化连接带来的额外延迟。
    _ = await agent_client._get_client()
    # yield 之前是启动逻辑，之后是关闭逻辑；
    # FastAPI 会在应用退出时恢复执行 yield 之后的代码。
    yield
    # 关闭阶段：若连接池仍存在则优雅关闭，释放底层 TCP 连接。
    if agent_client._client is not None:
        await agent_client._client.aclose()


app = FastAPI(title="Privacy Test Console", lifespan=lifespan)

# 可选安全中间件（API Key 鉴权 + 限流）：默认关闭 / 宽松，
# 仅在配置了 CONSOLE_API_KEY / CONSOLE_RATE_LIMIT 时生效。
# 先于 CORS 加入（位于 CORS 内层），使 CORS 处于最外层，
# 从而为所有响应（含 401/429）附加跨域头。
app.add_middleware(
    ConsoleSecurityMiddleware,
    api_key=settings.console_api_key,
    rate_limit=settings.console_rate_limit,
)

# 允许 Vite 开发服务器（默认 5173 端口）跨域调用本后端。
# 本控制台为本地工具，不依赖 cookie/凭证，故 allow_credentials=False
# （避免与 allow_origins=["*"] 冲突，后者在携带凭证时会被浏览器拒绝）。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
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
    # 记录起始时刻，用于计算探测 agent 的往返耗时。
    start = time.perf_counter()
    try:
        # 经代理客户端转发 GET /health 到 agent，返回其健康信息。
        agent_health = await agent_client.request("GET", "/health")
        # 计算耗时（秒 → 毫秒）。
        duration_ms = (time.perf_counter() - start) * 1000
        # agent 可达：返回双正常结构与延迟、后端身份标识。
        return {
            "backend": "ok",                 # 后端自身存活
            "agent": agent_health,           # agent 的 /health 原始返回
            "agent_url": settings.privacy_agent_url,  # 当前连接的 agent 地址
            "latency_ms": round(duration_ms, 2),      # 探测耗时（保留两位小数）
            "via": BACKEND_VIA,              # 后端标识：python-rest
            "protocol": AGENT_PROTOCOL,      # 通信协议：REST
        }
    except HTTPException as exc:
        # agent 不可达（client 已把网络错误包装为 502 HTTPException）：
        # 仍返回 HTTP 200，让前端能读取 agent=="unreachable" 做友好提示。
        return JSONResponse(
            status_code=200,                 # 刻意返回 200 而非 5xx
            content={
                "backend": "ok",             # 后端自身仍正常
                "agent": "unreachable",      # 标记 agent 不可达
                "agent_url": settings.privacy_agent_url,
                "error": exc.detail,         # 附带不可达的具体原因
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
    # 统一转为大写，容忍前端传入小写方法名（如 "post"）。
    method = req.method.upper()
    # 目标 agent 路径，原样透传（如 /v1/privacy/mask）。
    path = req.path

    # 默认无二进制载荷（走 JSON 或无请求体分支）。
    raw_content: bytes | None = None
    if req.raw_payload_b64:
        # 携带二进制载荷（如 Arrow IPC）：先做 base64 解码。
        try:
            raw_content = base64.b64decode(req.raw_payload_b64)
        except Exception as exc:
            # 解码失败属于客户端错误，返回 400 并说明原因。
            raise HTTPException(status_code=400, detail=f"Invalid base64 payload: {exc}") from exc

    # 记录起始时刻用于统计转发耗时。
    start = time.perf_counter()
    try:
        # 经代理客户端转发到 agent；client 内部自动区分
        # 二进制（raw_content）/ JSON（body）/ 无请求体三种形态，
        # 并按响应 Content-Type 解析为 JSON 友好结构。
        result = await agent_client.request(
            method=method,
            path=path,
            body=req.body,
            raw_content=raw_content,
            content_type=req.content_type,
        )
    except HTTPException:
        # client 已把网络错误（502）与 agent 非 2xx（透传状态码）
        # 包装为 HTTPException，这里直接重新抛出交给统一异常处理器。
        raise
    # 计算转发耗时（秒 → 毫秒）。
    duration_ms = (time.perf_counter() - start) * 1000

    # 包装为统一的 ProxyResponse（自动携带 via/protocol 默认值）。
    return ProxyResponse(status=200, duration_ms=round(duration_ms, 2), data=result)


@app.post("/api/batch")
async def batch(req: BatchRequest):
    """Run a list of requests against the agent sequentially.

    用于前端“一键批量测试”：逐个转发请求并汇总成功 / 失败统计，
    单个请求失败不会中断整个批次。
    """
    # 收集每个子请求的执行结果。
    results: list[BatchResultItem] = []
    # 顺序逐个转发（非并发），避免给 agent 造成瞬时压力。
    for item in req.requests:
        # 统一方法名为大写。
        method = item.method.upper()
        # 记录该子请求的起始时刻。
        start = time.perf_counter()
        try:
            # 转发 JSON 请求到 agent。
            data = await agent_client.request(method=method, path=item.path, body=item.body)
            # 计算该子请求耗时。
            duration_ms = (time.perf_counter() - start) * 1000
            # 成功：记录 200 与返回数据。
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
            # agent 返回非 2xx 或网络错误：透传状态码与 detail，
            # 单个失败不中断整个批次。
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
        except Exception as exc:
            # 其他未预期异常：记为 500，同样不中断批次。
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

    # 统计状态码落在 2xx 区间的子请求数为 passed。
    passed = sum(1 for r in results if 200 <= r.status < 300)
    # 汇总为 BatchResponse（total == passed + failed 恒成立）。
    return BatchResponse(
        total=len(results),
        passed=passed,
        failed=len(results) - passed,
        results=results,
    )


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
    # 一次性读出上传文件的全部字节。
    content = await file.read()
    # 上传大小限制：超限返回 413，避免大文件耗尽内存（DoS 防护）。
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"文件过大（{len(content)} 字节），上限 {settings.max_upload_bytes} 字节",
        )
    # 构造 httpx 的 files 映射：(文件名, 内容, Content-Type)，
    # 文件名缺失时兑底为 upload.bin，类型缺失时兑底为通用二进制流。
    files = {
        "file": (
            file.filename or "upload.bin",
            content,
            file.content_type or "application/octet-stream",
        ),
    }
    # 随附的表单字段：操作类型与参数 JSON 字符串。
    data = {"operation": operation, "params": params}

    # 记录起始时刻用于统计转发耗时。
    start = time.perf_counter()
    # 以 multipart/form-data 透传到 agent 的 process_file 端点；
    # 文件解析与隐私算法均由 agent 负责，后端仅转发。
    result = await agent_client.request_multipart(
        "/v1/privacy/process_file", files=files, data=data
    )
    # 计算转发耗时（秒 → 毫秒）。
    duration_ms = (time.perf_counter() - start) * 1000

    # 包装为统一的 ProxyResponse（自动携带 via/protocol）。
    return ProxyResponse(status=200, duration_ms=round(duration_ms, 2), data=result)


# 负载均衡探测允许的 URL scheme（拦截 file:// / gopher:// 等，SSRF 防护）。
_LB_ALLOWED_SCHEMES = ("http", "https")


def _validate_lb_url(url: str) -> None:
    """校验负载均衡探测目标 URL 的合法性（SSRF 防护）。

    - scheme 必须为 ``http``/``https``；
    - 配置了 ``LB_ALLOWED_HOSTS`` 白名单时，host 必须命中白名单。

    说明：lb_test 的设计目的就是探测用户指定地址（含本地 ``127.0.0.1``），
    故**不**屏蔽私有/回环 IP；如需生产收紧，通过 ``LB_ALLOWED_HOSTS`` 白名单约束。
    非法时抛出 400。
    """
    parsed = urlparse(url)
    if parsed.scheme not in _LB_ALLOWED_SCHEMES:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的探测地址 scheme '{parsed.scheme}'，仅允许 {list(_LB_ALLOWED_SCHEMES)}",
        )
    if not parsed.hostname:
        raise HTTPException(status_code=400, detail=f"探测地址缺少 host: {url}")
    allowed = settings.lb_allowed_hosts
    if allowed:
        hosts = {h.strip().lower() for h in allowed.split(",") if h.strip()}
        if parsed.hostname.lower() not in hosts:
            raise HTTPException(
                status_code=400,
                detail=f"探测地址 host '{parsed.hostname}' 不在白名单内",
            )


# 负载均衡测试支持的三种分发策略。
_LB_STRATEGIES = ("round_robin", "random", "least_connections")


def _lb_pick_backends(strategy: str, n: int, num_backends: int) -> list[int]:
    """按策略生成 ``n`` 个探测请求对应的后端下标序列。

    - ``round_robin``：依次轮询，分发最均匀；
    - ``random``：独立随机选择；
    - ``least_connections``：每次选当前累计命中最少的节点（同数取下标小者），
      效果上亦趋于均匀。
    """
    # 无可用后端时返回空序列（上层会报 400）。
    if num_backends <= 0:
        return []
    if strategy == "round_robin":
        # 轮询：下标依次取模，分发最均匀（0,1,...,n-1,0,1,...）。
        return [i % num_backends for i in range(n)]
    if strategy == "random":
        # 随机：每个请求独立随机选一个节点。
        return [random.randrange(num_backends) for _ in range(n)]
    if strategy == "least_connections":
        # 最少连接：每次选当前累计命中最少的节点。
        counts = [0] * num_backends          # 各节点当前累计命中数
        seq: list[int] = []                  # 生成的下标序列
        for _ in range(n):
            # 选 (命中数, 下标) 最小者，同数取下标小者，保证确定性。
            idx = min(range(num_backends), key=lambda i: (counts[i], i))
            counts[idx] += 1                 # 更新该节点的累计命中数
            seq.append(idx)
        return seq
    # 未知策略：返回 400 并提示可选值。
    raise HTTPException(
        status_code=400,
        detail=f"不支持的策略 '{strategy}'，可选: {list(_LB_STRATEGIES)}",
    )


async def _run_lb_test(
    req: LbTestRequest,
    transport: httpx.AsyncBaseTransport | None = None,
) -> LbTestResponse:
    """执行负载均衡探测并统计各节点命中与延迟。

    探测逻辑与端点解耦，``transport`` 可注入（测试时用 ``httpx.MockTransport``
    伪造后端），生产环境为 ``None`` 即走真实网络。
    """
    backends = req.backends
    # backends 为空属于参数错误，返回 400。
    if not backends:
        raise HTTPException(status_code=400, detail="backends 不能为空")

    # SSRF 防护：逐个校验探测目标 URL 的 scheme / host 白名单。
    for backend in backends:
        _validate_lb_url(backend.url)

    # 按策略生成 num_requests 个探测请求对应的后端下标序列。
    seq = _lb_pick_backends(req.strategy, req.num_requests, len(backends))
    # 以下标为键，分别记录各节点的延迟样本 / 成功数 / 失败数。
    latencies: dict[int, list[float]] = {i: [] for i in range(len(backends))}
    success: dict[int, int] = dict.fromkeys(range(len(backends)), 0)
    failed: dict[int, int] = dict.fromkeys(range(len(backends)), 0)

    # 探测路径兑底为 /health。
    probe_path = req.probe_path or "/health"

    async def probe(idx: int) -> None:
        """对指定下标的节点发一次探测请求并记录统计。"""
        backend = backends[idx]
        # 拼接完整探测 URL（去掉基地址尾部斜杠避免双斜杠）。
        url = backend.url.rstrip("/") + probe_path
        # 记录该次探测的起始时刻。
        start = time.perf_counter()
        ok = False                            # 默认失败，成功后置 True
        try:
            if req.probe_body is not None:
                # 提供了探测体：用 POST 发送 JSON。
                resp = await lb_client.post(url, json=req.probe_body)
            else:
                # 未提供探测体：用 GET。
                resp = await lb_client.get(url)
            # 状态码 < 400 视为成功。
            ok = resp.status_code < 400
        except httpx.HTTPError:
            # 网络异常（连不上 / 超时等）计为失败。
            ok = False
        # 记录该次探测耗时（毫秒）到对应节点的延迟样本。
        latencies[idx].append((time.perf_counter() - start) * 1000)
        # 按结果累加成功 / 失败计数。
        if ok:
            success[idx] += 1
        else:
            failed[idx] += 1

    # 记录整体起始时刻（用于统计总耗时）。
    overall_start = time.perf_counter()
    # 创建临时 httpx 客户端：transport 可注入（测试用 MockTransport），
    # trust_env=False 保证直连、不走系统代理。
    async with httpx.AsyncClient(
        transport=transport, timeout=10.0, trust_env=False
    ) as lb_client:
        # 并发发出所有探测请求（按 seq 中的下标）。
        await asyncio.gather(*(probe(i) for i in seq))
    # 计算整体耗时（毫秒）。
    total_ms = (time.perf_counter() - overall_start) * 1000

    # 汇总各节点的统计为 distribution（保持 backends 顺序）。
    distribution: list[LbDistItem] = []
    total_success = 0                         # 全局成功总数
    total_failed = 0                          # 全局失败总数
    for i, backend in enumerate(backends):
        lats = latencies[i]                   # 该节点的延迟样本列表
        count = len(lats)                     # 该节点被命中的次数
        total_success += success[i]           # 累加全局成功数
        total_failed += failed[i]             # 累加全局失败数
        distribution.append(
            LbDistItem(
                name=backend.name,
                url=backend.url,
                count=count,
                success=success[i],
                failed=failed[i],
                # 平均延迟：总延迟 / 次数；未命中时为 0。
                avg_latency_ms=round(sum(lats) / count, 2) if count else 0.0,
                # 最小 / 最大延迟：无样本时为 0。
                min_latency_ms=round(min(lats), 2) if lats else 0.0,
                max_latency_ms=round(max(lats), 2) if lats else 0.0,
            )
        )

    # 汇总为最终的 LbTestResponse。
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
        # index.html 的绝对路径。
        index_file = static_dir / "index.html"
        if index_file.exists():
            # 返回 index.html 并禁用缓存（no-cache）：
            # index.html 不带内容哈希，若被缓存会导致重新构建后
            # 浏览器仍加载旧版本；带哈希的 /assets/* 则正常缓存。
            return FileResponse(
                str(index_file),
                headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
            )
        # index.html 不存在（前端未构建）：返回 404。
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
