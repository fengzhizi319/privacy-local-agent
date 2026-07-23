"""REST API 入口模块。

基于 FastAPI 构建，提供健康检查、数据脱敏、差分隐私聚合、K-匿名、
查询混淆、隐私预算查询等 HTTP 接口。请求/响应模型使用 Pydantic 定义。
数据分类接口已拆分到 classification_routes 模块，并通过 include_router 挂载。

REST API entrypoint built with FastAPI. Exposes endpoints for health checks,
masking, differential privacy, K-anonymity, query obfuscation and budget queries.
Data classification endpoints are defined in classification_routes and mounted
via include_router.
"""

import asyncio
import json
import os
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from .classification_routes import classification_router, classification_service
from .observability.logging_config import configure_logging, get_logger
from .observability.middleware import ObservabilityMiddleware
from .observability.metrics import make_asgi_app
from .observability.tracing import init_tracing
from .privacy.budget import PrivacyBudgetExhausted
from .security.auth import get_current_identity, require_permission
from .security.ratelimit import rate_limit_dependency
from .service import PrivacyService

# 从环境变量读取配置文件路径与命名空间，便于在不同部署环境间切换
PROFILE_PATH = os.environ.get("PRIVACY_PROFILE", "privacy-profile.yaml")
NAMESPACE = os.environ.get("PRIVACY_NAMESPACE", "default")

# 模块级单例服务，供各路由处理函数复用
service = PrivacyService(profile_path=PROFILE_PATH, namespace=NAMESPACE)

# Per-namespace PrivacyService cache to avoid re-initializing on every recommend request
_service_cache: Dict[str, PrivacyService] = {NAMESPACE: service}

# Module-level logger for unexpected server errors
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理器。

    启动时初始化结构化日志、可选的 OpenTelemetry tracing 以及 LLM 异步预热。
    关闭时执行清理逻辑。

    Args:
        app: FastAPI 应用实例。
    """
    configure_logging(
        log_level=os.environ.get("PRIVACY_LOG_LEVEL", "INFO"),
        json_format=os.environ.get("PRIVACY_LOG_FORMAT", "text").lower() == "json",
        service_name=os.environ.get("PRIVACY_SERVICE_NAME", "privacy-local-agent"),
    )
    init_tracing(
        endpoint=os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"),
        service_name=os.environ.get(
            "OTEL_SERVICE_NAME",
            os.environ.get("PRIVACY_SERVICE_NAME", "privacy-local-agent"),
        ),
    )

    # 在后台异步预热本地大模型（若启用），避免首个请求阻塞
    warmup_task = None
    if os.environ.get("PRIVACY_WARMUP_LLM", "false").lower() == "true":
        warmup_task = asyncio.create_task(service.classification_api.warmup_async())
        app.state.warmup_task = warmup_task

    try:
        yield
    finally:
        if warmup_task is not None:
            warmup_task.cancel()


# FastAPI 应用实例；title 用于 OpenAPI 文档，lifespan 用于生命周期钩子
app = FastAPI(title="SecretFlow Local Privacy Agent", lifespan=lifespan)

# 注册可观测性中间件：request_id 透传、访问日志、Prometheus metrics。
# 注意：/metrics 本身会被中间件排除，避免自引用。
app.add_middleware(ObservabilityMiddleware)

# 暴露 Prometheus metrics。
app.mount("/metrics", make_asgi_app())

# 挂载数据分类路由；分类路由自身已声明认证、限速与权限依赖
app.include_router(classification_router)

# 通用安全依赖：认证 + 限速。健康检查端点单独声明，不使用本依赖列表。
SECURITY_DEPS = [Depends(get_current_identity), Depends(rate_limit_dependency)]


def _handle_request_exception(exc: Exception) -> None:
    """将隐私计算异常映射到合适的 HTTP 状态码，避免服务器错误误报为 400。"""
    if isinstance(exc, PrivacyBudgetExhausted):
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.exception("unexpected_request_error")
    raise HTTPException(status_code=500, detail="Internal server error") from exc


class MaskRequest(BaseModel):
    """单字段脱敏请求模型。"""

    field_name: str
    value: str
    context: str = ""


class MaskRecordRequest(BaseModel):
    """整记录脱敏请求模型。"""

    record: Dict[str, str]
    context: str = ""


class MaskBatchRequest(BaseModel):
    """批量字段脱敏请求模型。"""

    field_names: List[str]
    values: List[str]
    context: str = ""


class MaskDataFrameRequest(BaseModel):
    """DataFrame 脱敏请求模型。

    data 为 records 列表（可来自 pandas/SecretFlow DataFrame 的转换）。
    columns 指定需要脱敏的列；未指定则对所有字符串列脱敏。
    """

    data: List[Dict[str, Any]]
    columns: Optional[List[str]] = None
    context: str = ""


class HashRequest(BaseModel):
    """HMAC 哈希请求模型。"""

    value: str
    salt: str


class DPRequest(BaseModel):
    """差分隐私聚合请求模型。

    values 为输入数据列表；params 为可选参数，用于覆盖默认或 profile 中的配置。
    """

    values: List[float]
    params: Dict[str, object] = {}


class DPHistogramRequest(BaseModel):
    """差分隐私直方图请求模型。"""

    values: List[str]
    categories: List[str]
    params: Dict[str, object] = {}


class DPNoisyCountRequest(BaseModel):
    """对已聚合计数进行 DP 加噪的请求模型。"""

    true_count: float
    params: Dict[str, object] = {}


class DPNoisySumRequest(BaseModel):
    """对已聚合求和进行 DP 加噪的请求模型。

    params 中需提供 sensitivity，或同时提供 clip_lower 与 clip_upper。
    """

    true_sum: float
    params: Dict[str, object] = {}


class DPNoisyMeanRequest(BaseModel):
    """对已聚合 sum/count 进行 DP 加噪得到均值的请求模型。"""

    true_sum: float
    true_count: float
    params: Dict[str, object] = {}


class DPNoisyHistogramRequest(BaseModel):
    """对已聚合直方图计数进行 DP 加噪的请求模型。"""

    true_counts: Dict[str, float]
    params: Dict[str, object] = {}


class DPChunkedCountRequest(BaseModel):
    """分块流式 DP 计数请求模型。"""

    chunks: List[List[float]]
    params: Dict[str, object] = {}


class DPChunkedSumRequest(BaseModel):
    """分块流式 DP 求和请求模型。"""

    chunks: List[List[float]]
    params: Dict[str, object] = {}


class DPChunkedMeanRequest(BaseModel):
    """分块流式 DP 均值请求模型。"""

    chunks: List[List[float]]
    params: Dict[str, object] = {}


class DPAggregateRequest(BaseModel):
    """表格级原位 DP 聚合请求模型。"""

    rows: List[Dict[str, Any]]
    specs: Dict[str, Any]
    params: Dict[str, object] = {}


class DPVectorSumRequest(BaseModel):
    """高维向量 / 梯度 $L_2$ 范数截断加噪请求模型。"""

    vectors: List[List[float]]
    params: Dict[str, object] = {}


class DPAdaptiveClipRequest(BaseModel):
    """差分隐私自适应二分搜索估计上下界请求模型。"""

    values: List[float]
    params: Dict[str, object] = {}


class DPGroupByRequest(BaseModel):
    """Tau-Thresholding 差分隐私 SQL Group-By 请求模型。"""

    rows: List[Dict[str, Any]]
    group_col: str
    target_col: str
    agg: str
    params: Dict[str, object] = {}


class DPChunkedHistogramRequest(BaseModel):
    """分块流式 DP 直方图请求模型。"""

    chunks: List[List[str]]
    categories: List[str]
    params: Dict[str, object] = {}



class KAnonRequest(BaseModel):
    """K-匿名单条记录请求模型。"""

    record: Dict[str, object]
    qi_cols: List[str]
    k: int = 5


class KAnonTableRequest(BaseModel):
    """K-匿名整张表请求模型。"""

    rows: List[Dict[str, object]]
    qi_cols: List[str]
    k: int = 5
    max_depth: int = 10


class KAnonDataFrameRequest(BaseModel):
    """K-匿名 DataFrame 请求模型。

    data 为 records 列表（可来自 pandas/SecretFlow DataFrame）。
    """

    data: List[Dict[str, Any]]
    qi_cols: List[str]
    k: int = 5
    max_depth: int = 10


class QolRequest(BaseModel):
    """查询混淆请求模型。"""

    query: str
    num_dummies: int = 3
    domain: str = "medical"
    medical_pool: Optional[List[str]] = None
    generic_pool: Optional[List[str]] = None
    seed: Optional[int] = None


class QolBatchRequest(BaseModel):
    """批量查询混淆请求模型。"""

    queries: List[str]
    num_dummies: int = 3
    domain: str = "medical"
    medical_pool: Optional[List[str]] = None
    generic_pool: Optional[List[str]] = None
    seed: Optional[int] = None


class LdpPerturbBinaryRequest(BaseModel):
    """二值本地 DP 扰动请求模型。"""
    values: List[int]
    epsilon: float


class LdpPerturbCategoricalRequest(BaseModel):
    """类别型本地 DP 扰动请求模型。"""
    values: List[str]
    categories: List[str]
    epsilon: float


class LdpEstimateBinaryRequest(BaseModel):
    """二值本地 DP 估计请求模型。"""
    reported_values: List[int]
    epsilon: float


class LdpEstimateCategoricalRequest(BaseModel):
    """类别型本地 DP 估计请求模型。"""
    reported_values: List[str]
    categories: List[str]
    epsilon: float



@app.get("/health", dependencies=[Depends(get_current_identity), Depends(rate_limit_dependency)])
def health():
    """健康检查接口。

    Returns:
        包含状态与当前命名空间的 JSON 字典。
    """
    return {"status": "ok", "namespace": NAMESPACE}


@app.get("/livez", dependencies=[Depends(get_current_identity), Depends(rate_limit_dependency)])
def livez():
    """存活探针接口。"""
    return {"status": "alive"}


@app.get("/readyz", dependencies=[Depends(get_current_identity), Depends(rate_limit_dependency)])
def readyz():
    """就绪探针接口。

    验证关键依赖（配置与隐私预算持久化存储）是否可访问。
    """
    if not service.resolver:
        raise HTTPException(status_code=503, detail="Configuration resolver not initialized")
    
    db_path = os.environ.get("PRIVACY_BUDGET_DB")
    if db_path:
        import sqlite3
        try:
            conn = sqlite3.connect(db_path, timeout=2.0)
            conn.execute("SELECT 1")
            conn.close()
        except sqlite3.Error as e:
            raise HTTPException(status_code=503, detail=f"Database check failed: {e}")
            
    return {"status": "ready", "llm_ready": service.classification_api.is_llm_ready()}


@app.get("/readyz/llm", dependencies=[Depends(get_current_identity), Depends(rate_limit_dependency)])
def readyz_llm():
    """LLM 分类器就绪探针接口。

    供 K8s 等编排工具单独探测本地大模型是否已完成预热。
    若未启用 LLM、使用 NoOp 分类器或模型已加载成功，均返回 200；
    若模型正在预热或初始化失败，则返回 503。
    """
    if service.classification_api.is_llm_ready():
        return {"status": "ready", "llm_ready": True}
    raise HTTPException(status_code=503, detail="LLM classifier not ready")


@app.post("/v1/privacy/mask", dependencies=[*SECURITY_DEPS, require_permission("privacy:mask")])
def mask(req: MaskRequest):
    """对单个字段值进行脱敏。

    Args:
        req: MaskRequest 请求体。

    Returns:
        {"result": <脱敏后的值>}
    """
    return {"result": service.mask(req.field_name, req.value, req.context)}


@app.post("/v1/privacy/mask_record", dependencies=[*SECURITY_DEPS, require_permission("privacy:mask")])
def mask_record(req: MaskRecordRequest):
    """对整条记录进行脱敏。

    Args:
        req: MaskRecordRequest 请求体。

    Returns:
        {"result": <脱敏后的记录字典>}
    """
    return {"result": service.mask_record(req.record, req.context)}


@app.post("/v1/privacy/mask/batch", dependencies=[*SECURITY_DEPS, require_permission("privacy:mask")])
def mask_batch(req: MaskBatchRequest):
    """批量字段脱敏接口。"""
    try:
        return {"result": service.mask_batch(req.field_names, req.values, req.context)}
    except Exception as e:
        _handle_request_exception(e)


@app.post("/v1/privacy/mask/dataframe", dependencies=[*SECURITY_DEPS, require_permission("privacy:mask")])
def mask_dataframe(req: MaskDataFrameRequest):
    """DataFrame 脱敏接口。

    接受 records 列表，返回脱敏后的 records 列表。
    调用方可将 pandas/SecretFlow DataFrame 转为 records 后调用。
    """
    try:
        import pandas as pd

        df = pd.DataFrame(req.data)
        result = service.mask_dataframe(df, columns=req.columns, context=req.context)
        return {"result": result.to_dict(orient="records")}
    except Exception as e:
        _handle_request_exception(e)


@app.post("/v1/privacy/hash", dependencies=[*SECURITY_DEPS, require_permission("privacy:hash")])
def hash_value(req: HashRequest):
    """对单个值进行 HMAC-SHA256 哈希。

    Args:
        req: HashRequest 请求体。

    Returns:
        {"result": <16 位 base64 摘要>}
    """
    return {"result": service.hash(req.value, req.salt)}


@app.post("/v1/privacy/dp/count", dependencies=[*SECURITY_DEPS, require_permission("privacy:dp")])
def dp_count(req: DPRequest):
    """差分隐私计数接口。

    当参数校验或预算不足时，捕获异常并返回 400 Bad Request。

    Args:
        req: DPRequest 请求体。

    Returns:
        {"result": <带噪声计数值>}

    Raises:
        HTTPException: 参数非法或预算耗尽时返回 400。
    """
    try:
        return {"result": service.dp_count(req.values, req.params)}
    except Exception as e:
        _handle_request_exception(e)


@app.post("/v1/privacy/dp/sum", dependencies=[*SECURITY_DEPS, require_permission("privacy:dp")])
def dp_sum(req: DPRequest):
    """差分隐私求和接口。

    Args:
        req: DPRequest 请求体。

    Returns:
        {"result": <带噪声求和值>}

    Raises:
        HTTPException: 参数非法或预算耗尽时返回 400。
    """
    try:
        return {"result": service.dp_sum(req.values, req.params)}
    except Exception as e:
        _handle_request_exception(e)


@app.post("/v1/privacy/dp/mean", dependencies=[*SECURITY_DEPS, require_permission("privacy:dp")])
def dp_mean(req: DPRequest):
    """差分隐私均值接口。

    Args:
        req: DPRequest 请求体。

    Returns:
        {"result": <带噪声均值>}

    Raises:
        HTTPException: 参数非法或预算耗尽时返回 400。
    """
    try:
        return {"result": service.dp_mean(req.values, req.params)}
    except Exception as e:
        _handle_request_exception(e)


@app.post("/v1/privacy/dp/histogram", dependencies=[*SECURITY_DEPS, require_permission("privacy:dp")])
def dp_histogram(req: DPHistogramRequest):
    """差分隐私直方图聚合接口。"""
    try:
        return {"result": service.dp_histogram(req.values, req.categories, req.params)}
    except Exception as e:
        _handle_request_exception(e)


@app.post("/v1/privacy/dp/noisy_count", dependencies=[*SECURITY_DEPS, require_permission("privacy:dp")])
def dp_noisy_count(req: DPNoisyCountRequest):
    """对已聚合计数注入 DP 噪声。"""
    try:
        return {"result": service.dp_noisy_count(req.true_count, req.params)}
    except Exception as e:
        _handle_request_exception(e)


@app.post("/v1/privacy/dp/noisy_sum", dependencies=[*SECURITY_DEPS, require_permission("privacy:dp")])
def dp_noisy_sum(req: DPNoisySumRequest):
    """对已聚合求和注入 DP 噪声。"""
    try:
        return {"result": service.dp_noisy_sum(req.true_sum, req.params)}
    except Exception as e:
        _handle_request_exception(e)


@app.post("/v1/privacy/dp/noisy_mean", dependencies=[*SECURITY_DEPS, require_permission("privacy:dp")])
def dp_noisy_mean(req: DPNoisyMeanRequest):
    """对已聚合 sum/count 注入 DP 噪声后得到均值。"""
    try:
        return {"result": service.dp_noisy_mean(req.true_sum, req.true_count, req.params)}
    except Exception as e:
        _handle_request_exception(e)


@app.post("/v1/privacy/dp/noisy_histogram", dependencies=[*SECURITY_DEPS, require_permission("privacy:dp")])
def dp_noisy_histogram(req: DPNoisyHistogramRequest):
    """对已聚合直方图计数注入 DP 噪声。"""
    try:
        return {"result": service.dp_noisy_histogram(req.true_counts, req.params)}
    except Exception as e:
        _handle_request_exception(e)


@app.post("/v1/privacy/dp/aggregate", dependencies=[*SECURITY_DEPS, require_permission("privacy:dp")])
def dp_aggregate(req: DPAggregateRequest):
    """表格级原位 DP 聚合接口。"""
    try:
        import pandas as pd

        df = pd.DataFrame(req.rows)
        return {"result": service.dp_aggregate(df, req.specs, req.params)}
    except Exception as e:
        _handle_request_exception(e)


@app.post("/v1/privacy/dp/vector_sum", dependencies=[*SECURITY_DEPS, require_permission("privacy:dp")])
def dp_vector_sum(req: DPVectorSumRequest):
    """高维向量 / 梯度 $L_2$ 范数截断加噪接口。"""
    try:
        import numpy as np

        vectors = np.array(req.vectors)
        res = service.dp_vector_sum(vectors, req.params)
        if hasattr(res, "value"):
            return {"result": list(res.value), "details": str(res)}
        return {"result": list(res)}
    except Exception as e:
        _handle_request_exception(e)


@app.post("/v1/privacy/dp/vector_mean", dependencies=[*SECURITY_DEPS, require_permission("privacy:dp")])
def dp_vector_mean(req: DPVectorSumRequest):
    """高维向量 DP 均值：L2 范数截断 + 各向同性加噪 + noisy_count 归一化。"""
    try:
        import numpy as np

        vectors = np.array(req.vectors)
        res = service.dp_vector_mean(vectors, req.params)
        if hasattr(res, "value"):
            return {"result": list(res.value), "details": str(res)}
        return {"result": list(res)}
    except Exception as e:
        _handle_request_exception(e)


@app.post("/v1/privacy/dp/adaptive_clip", dependencies=[*SECURITY_DEPS, require_permission("privacy:dp")])
def dp_adaptive_clip(req: DPAdaptiveClipRequest):
    """差分隐私自适应二分搜索估计上下界接口。"""
    try:
        lower, upper = service.dp_adaptive_clip(req.values, req.params)
        return {"clip_lower": lower, "clip_upper": upper}
    except Exception as e:
        _handle_request_exception(e)


@app.post("/v1/privacy/dp/groupby", dependencies=[*SECURITY_DEPS, require_permission("privacy:dp")])
def dp_groupby(req: DPGroupByRequest):
    """Tau-Thresholding 差分隐私 SQL Group-By 接口。"""
    try:
        import pandas as pd

        df = pd.DataFrame(req.rows)
        return {"result": service.dp_groupby(df, req.group_col, req.target_col, req.agg, req.params)}
    except Exception as e:
        _handle_request_exception(e)


@app.post("/v1/privacy/dp/chunked_count", dependencies=[*SECURITY_DEPS, require_permission("privacy:dp")])
def dp_chunked_count(req: DPChunkedCountRequest):
    """分块流式差分隐私计数。"""
    try:
        return {"result": service.dp_chunked_count(req.chunks, req.params)}
    except Exception as e:
        _handle_request_exception(e)


@app.post("/v1/privacy/dp/chunked_sum", dependencies=[*SECURITY_DEPS, require_permission("privacy:dp")])
def dp_chunked_sum(req: DPChunkedSumRequest):
    """分块流式差分隐私求和。"""
    try:
        return {"result": service.dp_chunked_sum(req.chunks, req.params)}
    except Exception as e:
        _handle_request_exception(e)


@app.post("/v1/privacy/dp/chunked_mean", dependencies=[*SECURITY_DEPS, require_permission("privacy:dp")])
def dp_chunked_mean(req: DPChunkedMeanRequest):
    """分块流式差分隐私均值。"""
    try:
        return {"result": service.dp_chunked_mean(req.chunks, req.params)}
    except Exception as e:
        _handle_request_exception(e)


@app.post("/v1/privacy/dp/chunked_histogram", dependencies=[*SECURITY_DEPS, require_permission("privacy:dp")])
def dp_chunked_histogram(req: DPChunkedHistogramRequest):
    """分块流式差分隐私直方图计数。"""
    try:
        return {"result": service.dp_chunked_histogram(req.chunks, req.categories, req.params)}
    except Exception as e:
        _handle_request_exception(e)



@app.post("/v1/privacy/ldp/perturb/binary", dependencies=[*SECURITY_DEPS, require_permission("privacy:dp")])
def ldp_perturb_binary(req: LdpPerturbBinaryRequest):
    """二值本地 DP 扰动接口。"""
    try:
        return {"results": service.perturb_binary_batch(req.values, req.epsilon)}
    except Exception as e:
        _handle_request_exception(e)


@app.post("/v1/privacy/ldp/perturb/categorical", dependencies=[*SECURITY_DEPS, require_permission("privacy:dp")])
def ldp_perturb_categorical(req: LdpPerturbCategoricalRequest):
    """类别型本地 DP 扰动接口。"""
    try:
        return {"results": service.perturb_categorical_batch(req.values, req.categories, req.epsilon)}
    except Exception as e:
        _handle_request_exception(e)


@app.post("/v1/privacy/ldp/estimate/binary", dependencies=[*SECURITY_DEPS, require_permission("privacy:dp")])
def ldp_estimate_binary(req: LdpEstimateBinaryRequest):
    """二值本地 DP 估计接口。"""
    try:
        return {"estimated_frequency": service.estimate_binary_frequency(req.reported_values, req.epsilon)}
    except Exception as e:
        _handle_request_exception(e)


@app.post("/v1/privacy/ldp/estimate/categorical", dependencies=[*SECURITY_DEPS, require_permission("privacy:dp")])
def ldp_estimate_categorical(req: LdpEstimateCategoricalRequest):
    """类别型本地 DP 估计接口。"""
    try:
        return {"estimated_histogram": service.estimate_categorical_histogram(req.reported_values, req.categories, req.epsilon)}
    except Exception as e:
        _handle_request_exception(e)



@app.post("/v1/privacy/k_anonymize/record", dependencies=[*SECURITY_DEPS, require_permission("privacy:kano")])
def k_anonymize_record(req: KAnonRequest):
    """单条记录 K-匿名泛化接口。

    Args:
        req: KAnonRequest 请求体。

    Returns:
        {"result": <泛化后的记录字典>}
    """
    return {"result": service.k_anonymize_record(req.record, req.qi_cols, req.k)}


@app.post("/v1/privacy/k_anonymize/table", dependencies=[*SECURITY_DEPS, require_permission("privacy:kano")])
def k_anonymize_table(req: KAnonTableRequest):
    """整张表 K-匿名泛化接口。

    Args:
        req: KAnonTableRequest 请求体。

    Returns:
        {"result": <泛化后的记录列表>}
    """
    try:
        return {"result": service.k_anonymize_table(req.rows, req.qi_cols, req.k, req.max_depth)}
    except Exception as e:
        _handle_request_exception(e)


@app.post("/v1/privacy/k_anonymize/dataframe", dependencies=[*SECURITY_DEPS, require_permission("privacy:kano")])
def k_anonymize_dataframe(req: KAnonDataFrameRequest):
    """DataFrame K-匿名泛化接口。"""
    try:
        import pandas as pd

        df = pd.DataFrame(req.data)
        result = service.k_anonymize_dataframe(df, req.qi_cols, req.k, req.max_depth)
        return {"result": result.to_dict(orient="records")}
    except Exception as e:
        _handle_request_exception(e)
@app.post("/v1/privacy/qol/obfuscate", dependencies=[*SECURITY_DEPS, require_permission("privacy:qol")])
def qol_obfuscate(req: QolRequest):
    """查询混淆接口。

    Args:
        req: QolRequest 请求体。

    Returns:
        {"result": <混淆后的查询列表>}
    """
    return {"result": service.obfuscate_query(
        req.query,
        req.num_dummies,
        req.domain,
        medical_pool=req.medical_pool,
        generic_pool=req.generic_pool,
        seed=req.seed,
    )}


@app.post("/v1/privacy/qol/obfuscate/batch", dependencies=[*SECURITY_DEPS, require_permission("privacy:qol")])
def qol_obfuscate_batch(req: QolBatchRequest):
    """批量查询混淆接口。"""
    try:
        return {"results": service.obfuscate_query_batch(
            req.queries,
            req.num_dummies,
            req.domain,
            medical_pool=req.medical_pool,
            generic_pool=req.generic_pool,
            seed=req.seed,
        )}
    except Exception as e:
        _handle_request_exception(e)


@app.get("/v1/privacy/budget", dependencies=[*SECURITY_DEPS, require_permission("privacy:budget")])
def budget():
    """查询剩余隐私预算。

    Returns:
        当前命名空间下 epsilon 与 delta 的剩余量字典。
    """
    return service.budget_remaining()


class RecommendRequest(BaseModel):
    """隐私参数推荐请求模型。"""
    namespace: str
    values: Optional[List[float]] = None
    rows: Optional[List[Dict[str, object]]] = None
    qi_cols: Optional[List[str]] = None


@app.post("/v1/privacy/profile/recommend", dependencies=[*SECURITY_DEPS, require_permission("privacy:profile")])
def recommend_profile(req: RecommendRequest):
    """根据输入数据自动推荐差分隐私等隐私处理参数并保存。"""
    rec_service = _service_cache.get(req.namespace)
    if rec_service is None:
        rec_service = PrivacyService(profile_path=PROFILE_PATH, namespace=req.namespace)
        _service_cache[req.namespace] = rec_service
    recommended = rec_service.recommend_and_save_params(req.values, req.rows, req.qi_cols)
    return {
        "status": "success",
        "namespace": req.namespace,
        "recommended_params": recommended
    }


# 文件处理支持的操作类型：DataFrame 脱敏 / K-匿名 / 整表分类。
_FILE_OPERATIONS = {"mask_dataframe", "k_anonymize", "classify_table"}


def _parse_upload_to_records(content: bytes, filename: str) -> List[Dict[str, Any]]:
    """把上传的 CSV/JSON 文件字节解析为 records 列表。

    Args:
        content: 文件原始字节。
        filename: 原始文件名（用于按扩展名判定格式）。

    Returns:
        记录列表，每条记录为“列名 -> 值”字典（缺失值统一为空字符串）。

    Raises:
        HTTPException(400): 文件格式不受支持或内容无法解析。
    """
    import io

    import pandas as pd

    name = (filename or "").lower()
    try:
        if name.endswith(".csv"):
            df = pd.read_csv(io.BytesIO(content), dtype=str)
        elif name.endswith(".json"):
            # JSON 文件需为记录数组（list of objects）
            data = json.loads(content.decode("utf-8"))
            if not isinstance(data, list):
                raise ValueError("JSON 文件需为记录数组（list of objects）")
            df = pd.DataFrame(data)
        else:
            raise HTTPException(status_code=400, detail="仅支持 .csv 与 .json 文件")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"文件解析失败: {exc}") from exc

    # 缺失值统一为空字符串，与下游字符串语义保持一致
    df = df.fillna("")
    return df.to_dict(orient="records")


@app.post(
    "/v1/privacy/process_file",
    dependencies=[*SECURITY_DEPS, require_permission("privacy:mask")],
)
async def process_file(
    file: UploadFile = File(...),
    operation: str = Form(...),
    params: str = Form("{}"),
):
    """数据文件隐私处理接口。

    接收上传的 CSV/JSON 数据文件，按 ``operation`` 对其内容执行 DataFrame 脱敏、
    K-匿名或整表分类，返回处理后的记录。

    表单字段：
        - ``file``：CSV/JSON 数据文件；
        - ``operation``：操作类型，``mask_dataframe`` / ``k_anonymize`` / ``classify_table``；
        - ``params``：操作参数 JSON 字符串，例如
          ``{"columns": ["email"], "context": ""}``（脱敏）、
          ``{"qi_cols": ["age", "zip"], "k": 2, "max_depth": 10}``（K-匿名）、
          ``{"params": {}}``（分类）。

    Returns:
        ``{"operation", "rows_in", "rows_out", "result"}``；分类时 ``result`` 为表分类结果字典。
    """
    if operation not in _FILE_OPERATIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的操作 '{operation}'，可选: {sorted(_FILE_OPERATIONS)}",
        )

    try:
        options = json.loads(params or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"params 需为合法 JSON: {exc}") from exc
    if not isinstance(options, dict):
        raise HTTPException(status_code=400, detail="params 需为 JSON 对象")

    content = await file.read()
    records = _parse_upload_to_records(content, file.filename or "")
    rows_in = len(records)

    try:
        import pandas as pd

        if operation == "mask_dataframe":
            df = pd.DataFrame(records)
            result_df = service.mask_dataframe(
                df, columns=options.get("columns"), context=options.get("context", "")
            )
            result: Any = result_df.to_dict(orient="records")
        elif operation == "k_anonymize":
            qi_cols = options.get("qi_cols")
            if not qi_cols:
                raise ValueError("k_anonymize 操作需提供 qi_cols 参数")
            df = pd.DataFrame(records)
            result_df = service.k_anonymize_dataframe(
                df,
                qi_cols,
                k=int(options.get("k", 5)),
                max_depth=int(options.get("max_depth", 10)),
            )
            result = result_df.to_dict(orient="records")
        else:  # classify_table
            schema = options.get("schema")
            if not schema:
                schema = list(records[0].keys()) if records else []
            result = classification_service.classify_table(
                schema, records, options.get("params", {})
            )
    except HTTPException:
        raise
    except Exception as exc:
        _handle_request_exception(exc)

    rows_out = len(result) if isinstance(result, list) else rows_in
    return {
        "operation": operation,
        "rows_in": rows_in,
        "rows_out": rows_out,
        "result": result,
    }


@app.post(
    "/v1/privacy/dp/arrow_ipc",
    dependencies=[*SECURITY_DEPS, require_permission("privacy:dp")],
)
async def dp_arrow_ipc(
    request: Request,
    aggregation: str = "count",
    epsilon: float = 1.0,
    delta: float = 0.0,
    mechanism: str = "laplace",
    clip_lower: Optional[float] = None,
    clip_upper: Optional[float] = None,
    max_norm: Optional[float] = None,
    column: Optional[str] = None,
):
    """高效二进制 REST 端点：接收 application/vnd.apache.arrow.stream 字节载荷并返回带 DP Metadata 的 Arrow IPC Stream。

    跳过 JSON 序列化/反序列化开销，直接通过零拷贝 PyArrow 二进制 Stream 进行交互。

    Query Parameters:
        aggregation: 聚合类型，支持 count / sum / mean / vector_sum / vector_mean。
        epsilon: 隐私预算 epsilon。
        delta: 隐私预算 delta。
        mechanism: 噪声机制（"laplace" 或 "gaussian"）。
        clip_lower: 数值截断下界（sum / mean 必填）。
        clip_upper: 数值截断上界（sum / mean 必填；vector_sum / vector_mean 用作 max_norm 回退）。
        max_norm: 向量 L2 范数截断阈值（vector_sum / vector_mean 专用，优先于 clip_upper）。
        column: Arrow Table 中的目标列名（可选，默认取第一列）。
    """
    try:
        from fastapi.responses import Response
        from .privacy.data_adapters import parse_arrow_ipc_bytes, table_to_arrow_ipc_bytes
        from .privacy.dp import DPResult, AggregationType, compute_confidence_interval

        # Step 1: Read raw Arrow IPC Stream bytes from request body
        body_bytes = await request.body()
        arr = parse_arrow_ipc_bytes(body_bytes, column=column)

        # Step 2: Dispatch to the corresponding DPApi method based on aggregation type
        if aggregation == AggregationType.COUNT:
            dp_res = service.dp_api.count(
                arr, epsilon=epsilon, delta=delta, mechanism=mechanism,
                return_details=True,
            )
        elif aggregation == AggregationType.SUM:
            dp_res = service.dp_api.sum(
                arr, epsilon=epsilon, delta=delta, mechanism=mechanism,
                clip_lower=clip_lower, clip_upper=clip_upper,
                return_details=True,
            )
        elif aggregation == AggregationType.MEAN:
            dp_res = service.dp_api.mean(
                arr, epsilon=epsilon, delta=delta, mechanism=mechanism,
                clip_lower=clip_lower, clip_upper=clip_upper,
                return_details=True,
            )
        elif aggregation == AggregationType.VECTOR_SUM:
            # max_norm explicitly provided takes priority; fall back to clip_upper for backward compatibility
            resolved_norm = max_norm if max_norm is not None else (clip_upper or 1.0)
            dp_res = service.dp_api.vector_sum(
                arr, max_norm=resolved_norm,
                epsilon=epsilon, delta=delta, mechanism=mechanism,
                return_details=True,
            )
        elif aggregation == AggregationType.VECTOR_MEAN:
            resolved_norm = max_norm if max_norm is not None else (clip_upper or 1.0)
            dp_res = service.dp_api.vector_mean(
                arr, max_norm=resolved_norm,
                epsilon=epsilon, delta=delta, mechanism=mechanism,
                return_details=True,
            )
        else:
            raise ValueError(
                f"Unsupported aggregation '{aggregation}'. "
                "Supported: count, sum, mean, vector_sum, vector_mean"
            )

        from .privacy.dp import calibrate_analytic_gaussian

        # Step 3: Ensure dp_res is a DPResult (all branches above return DPResult via return_details=True)
        if isinstance(dp_res, (int, float)):
            # Scalar fallback: compute a valid noise scale from the aggregation parameters
            # rather than reporting a zero-scale (non-DP) CI.
            if aggregation == AggregationType.COUNT:
                sensitivity = 1.0
            elif aggregation in (AggregationType.SUM, AggregationType.MEAN):
                if clip_lower is None or clip_upper is None:
                    raise ValueError(f"clip_lower and clip_upper are required for {aggregation}")
                sensitivity = clip_upper - clip_lower
            elif aggregation in (AggregationType.VECTOR_SUM, AggregationType.VECTOR_MEAN):
                sensitivity = resolved_norm
            else:
                sensitivity = 1.0

            noise_scale = (
                sensitivity / epsilon
                if mechanism == "laplace"
                else calibrate_analytic_gaussian(epsilon, delta, sensitivity)
            )
            dp_res = DPResult(
                value=dp_res,
                noise_mechanism=mechanism,
                noise_scale=noise_scale,
                epsilon_spent=epsilon,
                delta_spent=delta,
                confidence_interval=compute_confidence_interval(
                    float(dp_res), noise_scale, mechanism, 0.95
                ),
            )

        # Step 4: Export DPResult to Arrow Table with embedded DP metadata, then serialize to IPC bytes
        table = dp_res.to_arrow()
        ipc_bytes = table_to_arrow_ipc_bytes(table)
        return Response(content=ipc_bytes, media_type="application/vnd.apache.arrow.stream")
    except Exception as e:
        _handle_request_exception(e)


if __name__ == "__main__":
    import uvicorn

    # 直接运行本模块时使用 uvicorn 启动开发服务器，监听 127.0.0.1:8079
    uvicorn.run(app, host="127.0.0.1", port=8079)
