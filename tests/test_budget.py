"""隐私预算记账模块测试。

验证单机内存预算模式与多进程 SQLite 持久化预算模式的正确性。
"""

import os
import pytest
from privacy_local_agent.privacy.budget import BudgetAccountant, PrivacyBudgetExhausted


def test_memory_budget_accountant():
    """测试常规内存单例记账模式。"""
    # 清空缓存单例，防止跨测试干扰
    BudgetAccountant._instances.clear()

    accountant = BudgetAccountant("test-ns-memory", epsilon_total=5.0, delta_total=1e-5)
    assert accountant.remaining()["epsilon"] == 5.0

    accountant.spend(2.0, 0.0)
    assert accountant.remaining()["epsilon"] == 3.0

    # 尝试超出预算限制，应该抛出异常
    with pytest.raises(PrivacyBudgetExhausted):
        accountant.spend(4.0, 0.0)


def test_sqlite_budget_accountant(tmp_path):
    """测试基于 SQLite 的持久化记账模式，模拟多进程/实例数据共享。"""
    BudgetAccountant._instances.clear()

    # 指定 SQLite 临时数据库路径
    db_file = os.path.join(tmp_path, "budget_test.db")
    os.environ["PRIVACY_BUDGET_DB"] = db_file

    try:
        acc1 = BudgetAccountant("test-ns-sqlite", epsilon_total=8.0, delta_total=1e-5)
        # 验证数据库及表文件自动创建成功
        assert os.path.exists(db_file)

        # 消费 3.0 预算
        acc1.spend(3.0, 0.0)
        assert acc1.remaining()["epsilon"] == 5.0

        # 清除单例内存缓存，模拟另一进程实例启动加载该预算
        BudgetAccountant._instances.clear()

        # 第二个实例读取相同 namespace
        acc2 = BudgetAccountant("test-ns-sqlite", epsilon_total=8.0, delta_total=1e-5)
        # 应当能够读取到数据库中已扣减的状态 (剩余 5.0)
        assert acc2.remaining()["epsilon"] == 5.0

        # 第二个实例再次扣减 2.0
        acc2.spend(2.0, 0.0)
        assert acc2.remaining()["epsilon"] == 3.0

        # 超出预算应该抛出异常并回滚
        with pytest.raises(PrivacyBudgetExhausted):
            acc2.spend(4.0, 0.0)

        # 验证异常后数据库内数据没有被超扣 (应该还是剩余 3.0)
        assert acc2.remaining()["epsilon"] == 3.0

    finally:
        # 清理环境变量，防止干扰其他测试
        os.environ.pop("PRIVACY_BUDGET_DB", None)
