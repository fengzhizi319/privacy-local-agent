"""K-匿名路由（k_anonymize/record|table|dataframe）。"""

from fastapi import APIRouter

from ..deps import SECURITY_DEPS, handle_request_exception, service
from ..schemas import KAnonDataFrameRequest, KAnonRequest, KAnonTableRequest
from ..security.auth import require_permission

router = APIRouter()


@router.post("/v1/privacy/k_anonymize/record", dependencies=[*SECURITY_DEPS, require_permission("privacy:kano")])
def k_anonymize_record(req: KAnonRequest):
    """单条记录 K-匿名泛化接口。

    Args:
        req: KAnonRequest 请求体。

    Returns:
        {"result": <泛化后的记录字典>}
    """
    return {"result": service.k_anonymize_record(req.record, req.qi_cols, req.k)}


@router.post("/v1/privacy/k_anonymize/table", dependencies=[*SECURITY_DEPS, require_permission("privacy:kano")])
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
        handle_request_exception(e)


@router.post("/v1/privacy/k_anonymize/dataframe", dependencies=[*SECURITY_DEPS, require_permission("privacy:kano")])
def k_anonymize_dataframe(req: KAnonDataFrameRequest):
    """DataFrame K-匿名泛化接口。"""
    try:
        import pandas as pd

        df = pd.DataFrame(req.data)
        result = service.k_anonymize_dataframe(df, req.qi_cols, req.k, req.max_depth)
        return {"result": result.to_dict(orient="records")}
    except Exception as e:
        handle_request_exception(e)
