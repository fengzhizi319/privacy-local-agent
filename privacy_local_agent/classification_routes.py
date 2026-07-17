"""数据分类 REST 路由模块。

基于 FastAPI APIRouter 提供字段级、记录级、表级数据分类的 HTTP 接口，
与处理原语（masking/dp/k-anonymity/qol）在代码层面完全分离。
"""

import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from .classification_service import ClassificationService
from .security.auth import get_current_identity, require_permission
from .security.ratelimit import rate_limit_dependency

# 从环境变量读取配置文件路径，便于在不同部署环境间切换
PROFILE_PATH = os.environ.get("PRIVACY_PROFILE", "privacy-profile.yaml")

# 模块级单例服务，供各路由处理函数复用
classification_service = ClassificationService(profile_path=PROFILE_PATH)

# 数据分类专用路由；主应用在 main.py 中通过 include_router 挂载。
# 认证、限速、分类读权限统一在路由器级别应用，避免每个端点重复声明。
classification_router = APIRouter(
    dependencies=[
        Depends(get_current_identity),
        Depends(rate_limit_dependency),
        require_permission("classification:read"),
    ]
)


class ClassifyFieldRequest(BaseModel):
    """单字段分类请求模型。"""

    field_name: str
    value: Any
    params: Dict[str, Any] = {}


class ClassifyRecordRequest(BaseModel):
    """单条记录分类请求模型。"""

    record: Dict[str, Any]
    params: Dict[str, Any] = {}


class ClassifyTableRequest(BaseModel):
    """整张表分类请求模型。"""

    schema_: List[str] = Field(alias="schema")
    rows: List[Dict[str, Any]]
    params: Dict[str, Any] = {}


class ClassifySecretFlowRequest(BaseModel):
    """SecretFlow 分类请求模型。"""

    party: Optional[str] = None
    params_json: str = "{}"
    data_json: str = "{}"


class ConfirmReviewRequest(BaseModel):
    """复核确认请求模型。"""

    review_id: str
    corrected_level: str
    reviewer: str = ""
    comment: str = ""


class ExportReviewsRequest(BaseModel):
    """复核导出请求模型。"""

    format: str = "jsonl"
    mask_input: bool = False


@classification_router.post("/v1/privacy/classify/field")
def classify_field(req: ClassifyFieldRequest):
    """单字段分类接口。"""
    return {"result": classification_service.classify_field(req.field_name, req.value, req.params)}


@classification_router.post("/v1/privacy/classify/record")
def classify_record(req: ClassifyRecordRequest):
    """单条记录分类接口。"""
    return {"result": classification_service.classify_record(req.record, req.params)}


@classification_router.post("/v1/privacy/classify/table")
def classify_table(req: ClassifyTableRequest):
    """整张表分类接口（同步）。"""
    return {"result": classification_service.classify_table(req.schema_, req.rows, req.params)}


@classification_router.post("/v1/privacy/classify/table/async")
def classify_table_async(req: ClassifyTableRequest):
    """整张表分类接口（异步）。"""
    job_id = classification_service.submit_classify_table_async(
        req.schema_, req.rows, req.params
    )
    return {"job_id": job_id}


@classification_router.get("/v1/privacy/classify/jobs/{job_id}")
def get_classification_job(job_id: str):
    """查询异步分类任务结果。"""
    return classification_service.get_job_result(job_id)


@classification_router.post("/v1/privacy/classify/secretflow")
def classify_secretflow(req: ClassifySecretFlowRequest):
    """SecretFlow 数据结构分类接口。"""
    import json
    params = json.loads(req.params_json) if req.params_json else {}
    data = json.loads(req.data_json) if req.data_json else {}
    result = classification_service.classify_table(
        schema=list(data.get("schema", [])),
        rows=data.get("rows", []),
        params=params,
    )
    return {"result": result}


@classification_router.post("/v1/privacy/classify/review/confirm")
def confirm_review(req: ConfirmReviewRequest):
    """确认或修正复核结果。"""
    return {
        "result": classification_service.confirm_review(
            req.review_id,
            req.corrected_level,
            req.reviewer,
            req.comment,
        )
    }


@classification_router.post("/v1/privacy/classify/review/export")
def export_reviews(req: ExportReviewsRequest):
    """导出复核样本。"""
    return {
        "data": classification_service.export_reviews(
            format=req.format, mask_input=req.mask_input
        )
    }
