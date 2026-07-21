import pytest

from privacy_local_agent.privacy.budget import default_registry


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
