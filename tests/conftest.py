import pytest
from privacy_local_agent.privacy.budget import BudgetAccountant, default_registry

@pytest.fixture(autouse=True)
def reset_all_budgets():
    """每个测试前重置所有 BudgetAccountant 实例的已消耗预算，防止跨测试/跨文件干扰。"""
    # 1. 重置默认注册表中的所有实例
    default_registry.reset()

    # 2. 重置 REST 全局单例服务的预算（若已导入）
    try:
        from privacy_local_agent.main import service
        if hasattr(service, "dp_api") and hasattr(service.dp_api, "budget"):
            service.dp_api.budget.epsilon_spent = 0.0
            service.dp_api.budget.delta_spent = 0.0
    except (ImportError, AttributeError):
        pass

    yield
