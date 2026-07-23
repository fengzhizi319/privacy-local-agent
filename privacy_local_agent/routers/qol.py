"""查询混淆路由（qol/obfuscate|batch）。"""

from fastapi import APIRouter

from ..deps import SECURITY_DEPS, handle_request_exception, service
from ..schemas import QolBatchRequest, QolRequest
from ..security.auth import require_permission

router = APIRouter()


@router.post("/v1/privacy/qol/obfuscate", dependencies=[*SECURITY_DEPS, require_permission("privacy:qol")])
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


@router.post("/v1/privacy/qol/obfuscate/batch", dependencies=[*SECURITY_DEPS, require_permission("privacy:qol")])
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
        handle_request_exception(e)
