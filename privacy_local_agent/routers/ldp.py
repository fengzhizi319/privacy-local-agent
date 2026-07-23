"""本地差分隐私路由（ldp/perturb|estimate/*）。"""

from fastapi import APIRouter

from ..deps import SECURITY_DEPS, handle_request_exception, service
from ..schemas import (
    LdpEstimateBinaryRequest,
    LdpEstimateCategoricalRequest,
    LdpPerturbBinaryRequest,
    LdpPerturbCategoricalRequest,
)
from ..security.auth import require_permission

router = APIRouter()


@router.post("/v1/privacy/ldp/perturb/binary", dependencies=[*SECURITY_DEPS, require_permission("privacy:dp")])
def ldp_perturb_binary(req: LdpPerturbBinaryRequest):
    """二值本地 DP 扰动接口。"""
    try:
        return {"results": service.perturb_binary_batch(req.values, req.epsilon)}
    except Exception as e:
        handle_request_exception(e)


@router.post("/v1/privacy/ldp/perturb/categorical", dependencies=[*SECURITY_DEPS, require_permission("privacy:dp")])
def ldp_perturb_categorical(req: LdpPerturbCategoricalRequest):
    """类别型本地 DP 扰动接口。"""
    try:
        return {"results": service.perturb_categorical_batch(req.values, req.categories, req.epsilon)}
    except Exception as e:
        handle_request_exception(e)


@router.post("/v1/privacy/ldp/estimate/binary", dependencies=[*SECURITY_DEPS, require_permission("privacy:dp")])
def ldp_estimate_binary(req: LdpEstimateBinaryRequest):
    """二值本地 DP 估计接口。"""
    try:
        return {"estimated_frequency": service.estimate_binary_frequency(req.reported_values, req.epsilon)}
    except Exception as e:
        handle_request_exception(e)


@router.post("/v1/privacy/ldp/estimate/categorical", dependencies=[*SECURITY_DEPS, require_permission("privacy:dp")])
def ldp_estimate_categorical(req: LdpEstimateCategoricalRequest):
    """类别型本地 DP 估计接口。"""
    try:
        return {"estimated_histogram": service.estimate_categorical_histogram(req.reported_values, req.categories, req.epsilon)}
    except Exception as e:
        handle_request_exception(e)
