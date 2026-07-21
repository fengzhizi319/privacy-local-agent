import pytest

from privacy_local_agent.privacy.budget import default_registry

# ---------------------------------------------------------------------------
# Patch scipy's is_torch_array to tolerate MagicMock-based torch stubs.
# test_classification_llm.py sets sys.modules["torch"] = MagicMock(), which
# causes scipy._external.array_api_compat's issubclass() check to raise
# TypeError.  We patch is_torch_array once at import time so that both
# collection and execution are safe.
# ---------------------------------------------------------------------------
try:
    from scipy._external.array_api_compat.common import _helpers as _scipy_helpers

    _orig_is_torch_array = _scipy_helpers.is_torch_array

    def _safe_is_torch_array(x):
        try:
            return _orig_is_torch_array(x)
        except TypeError:
            return False

    _scipy_helpers.is_torch_array = _safe_is_torch_array

    # Also patch in the _array_api module which imports is_torch_array at top level
    try:
        import scipy._lib._array_api as _scipy_array_api
        _scipy_array_api.is_torch_array = _safe_is_torch_array
    except Exception:
        pass
except Exception:
    pass  # scipy not installed or internal API changed — nothing to patch


@pytest.fixture(autouse=True)
def reset_all_budgets():
    """每个测试前清空默认注册表中的所有 BudgetAccountant 实例，防止跨测试/跨文件干扰。"""
    # 1. 清空默认注册表中的所有实例
    default_registry.reset()

    # 2. REST 全局单例服务（若已导入）需重新从注册表获取预算实例，
    #    避免继续持有已从注册表移除的旧实例导致预算状态不同步。
    try:
        from privacy_local_agent.main import service
        if hasattr(service, "dp_api"):
            service.dp_api.budget = default_registry.get_or_create(service.namespace)
    except (ImportError, AttributeError):
        pass

    yield
