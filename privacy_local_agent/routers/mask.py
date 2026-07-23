"""数据脱敏与哈希路由（mask / mask_record / mask/batch / mask/dataframe / hash）。"""

from fastapi import APIRouter

from ..deps import SECURITY_DEPS, handle_request_exception, service
from ..schemas import (
    HashRequest,
    MaskBatchRequest,
    MaskDataFrameRequest,
    MaskRecordRequest,
    MaskRequest,
)
from ..security.auth import require_permission

router = APIRouter()


@router.post("/v1/privacy/mask", dependencies=[*SECURITY_DEPS, require_permission("privacy:mask")])
def mask(req: MaskRequest):
    """对单个字段值进行脱敏。

    Args:
        req: MaskRequest 请求体。

    Returns:
        {"result": <脱敏后的值>}
    """
    return {"result": service.mask(req.field_name, req.value, req.context)}


@router.post("/v1/privacy/mask_record", dependencies=[*SECURITY_DEPS, require_permission("privacy:mask")])
def mask_record(req: MaskRecordRequest):
    """对整条记录进行脱敏。

    Args:
        req: MaskRecordRequest 请求体。

    Returns:
        {"result": <脱敏后的记录字典>}
    """
    return {"result": service.mask_record(req.record, req.context)}


@router.post("/v1/privacy/mask/batch", dependencies=[*SECURITY_DEPS, require_permission("privacy:mask")])
def mask_batch(req: MaskBatchRequest):
    """批量字段脱敏接口。"""
    try:
        return {"result": service.mask_batch(req.field_names, req.values, req.context)}
    except Exception as e:
        handle_request_exception(e)


@router.post("/v1/privacy/mask/dataframe", dependencies=[*SECURITY_DEPS, require_permission("privacy:mask")])
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
        handle_request_exception(e)


@router.post("/v1/privacy/hash", dependencies=[*SECURITY_DEPS, require_permission("privacy:hash")])
def hash_value(req: HashRequest):
    """对单个值进行 HMAC-SHA256 哈希。

    Args:
        req: HashRequest 请求体。

    Returns:
        {"result": <16 位 base64 摘要>}
    """
    return {"result": service.hash(req.value, req.salt)}
