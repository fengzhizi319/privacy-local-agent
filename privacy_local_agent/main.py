"""REST API 入口模块。

基于 FastAPI 构建，提供健康检查、数据脱敏、差分隐私聚合、K-匿名、
查询混淆、隐私预算查询等 HTTP 接口。请求/响应模型使用 Pydantic 定义。
数据分类接口已拆分到 classification_routes 模块，并通过 include_router 挂载。

REST API entrypoint built with FastAPI. Exposes endpoints for health checks,
masking, differential privacy, K-anonymity, query obfuscation and budget queries.
Data classification endpoints are defined in classification_routes and mounted
via include_router.
"""

import os
from contextlib import asynccontextmanager
from typing import Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from .classification_routes import classification_router
from .observability.logging_config import configure_logging
from .observability.middleware import ObservabilityMiddleware
from .observability.metrics import make_asgi_app
from .observability.tracing import init_tracing
from .security.auth import get_current_identity, require_permission
from .security.ratelimit import rate_limit_dependency
from .service import PrivacyService

# 从环境变量读取配置文件路径与命名空间，便于在不同部署环境间切换
PROFILE_PATH = os.environ.get("PRIVACY_PROFILE", "privacy-profile.yaml")
NAMESPACE = os.environ.get("PRIVACY_NAMESPACE", "default")

# 模块级单例服务，供各路由处理函数复用
service = PrivacyService(profile_path=PROFILE_PATH, namespace=NAMESPACE)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理器。

    启动时初始化结构化日志与可选的 OpenTelemetry tracing。
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
    yield


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


class MaskRequest(BaseModel):
    """单字段脱敏请求模型。"""

    field_name: str
    value: str
    context: str = ""


class MaskRecordRequest(BaseModel):
    """整记录脱敏请求模型。"""

    record: Dict[str, str]
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


class QolRequest(BaseModel):
    """查询混淆请求模型。"""

    query: str
    num_dummies: int = 3
    domain: str = "medical"
    medical_pool: Optional[List[str]] = None
    generic_pool: Optional[List[str]] = None


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
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"Database check failed: {e}")
            
    return {"status": "ready"}


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
        raise HTTPException(status_code=400, detail=str(e))


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
        raise HTTPException(status_code=400, detail=str(e))


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
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/v1/privacy/dp/histogram", dependencies=[*SECURITY_DEPS, require_permission("privacy:dp")])
def dp_histogram(req: DPHistogramRequest):
    """差分隐私直方图聚合接口。"""
    try:
        return {"result": service.dp_histogram(req.values, req.categories, req.params)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))



@app.post("/v1/privacy/ldp/perturb/binary", dependencies=[*SECURITY_DEPS, require_permission("privacy:dp")])
def ldp_perturb_binary(req: LdpPerturbBinaryRequest):
    """二值本地 DP 扰动接口。"""
    try:
        return {"results": service.perturb_binary_batch(req.values, req.epsilon)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/v1/privacy/ldp/perturb/categorical", dependencies=[*SECURITY_DEPS, require_permission("privacy:dp")])
def ldp_perturb_categorical(req: LdpPerturbCategoricalRequest):
    """类别型本地 DP 扰动接口。"""
    try:
        return {"results": service.perturb_categorical_batch(req.values, req.categories, req.epsilon)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/v1/privacy/ldp/estimate/binary", dependencies=[*SECURITY_DEPS, require_permission("privacy:dp")])
def ldp_estimate_binary(req: LdpEstimateBinaryRequest):
    """二值本地 DP 估计接口。"""
    try:
        return {"estimated_frequency": service.estimate_binary_frequency(req.reported_values, req.epsilon)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/v1/privacy/ldp/estimate/categorical", dependencies=[*SECURITY_DEPS, require_permission("privacy:dp")])
def ldp_estimate_categorical(req: LdpEstimateCategoricalRequest):
    """类别型本地 DP 估计接口。"""
    try:
        return {"estimated_histogram": service.estimate_categorical_histogram(req.reported_values, req.categories, req.epsilon)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))



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
        raise HTTPException(status_code=400, detail=str(e))
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
    )}


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
    rec_service = PrivacyService(profile_path=PROFILE_PATH, namespace=req.namespace)
    recommended = rec_service.recommend_and_save_params(req.values, req.rows, req.qi_cols)
    return {
        "status": "success",
        "namespace": req.namespace,
        "recommended_params": recommended
    }


if __name__ == "__main__":
    import uvicorn

    # 直接运行本模块时使用 uvicorn 启动开发服务器，监听 127.0.0.1:8079
    uvicorn.run(app, host="127.0.0.1", port=8079)
