"""隐私预算记账模块测试。

验证单机内存预算模式、多进程 SQLite 持久化预算模式，以及时间窗口重置机制的正确性。
"""

import os
import time
import pytest
from privacy_local_agent.privacy.budget import (
    BudgetAccountant,
    BudgetRegistry,
    PrivacyBudgetExhausted,
    default_registry,
)


def test_memory_budget_accountant():
    """测试常规内存单例记账模式。"""
    # 清空注册表，防止跨测试干扰
    default_registry.reset()

    accountant = BudgetAccountant("test-ns-memory", epsilon_total=5.0, delta_total=1e-5)
    assert accountant.remaining()["epsilon"] == 5.0

    accountant.spend(2.0, 0.0)
    assert accountant.remaining()["epsilon"] == 3.0

    # 尝试超出预算限制，应该抛出异常
    with pytest.raises(PrivacyBudgetExhausted):
        accountant.spend(4.0, 0.0)


def test_sqlite_budget_accountant(tmp_path):
    """测试基于 SQLite 的持久化记账模式，模拟多进程/实例数据共享。"""
    default_registry.reset()

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

        # 清空注册表，模拟另一进程实例启动加载该预算
        default_registry.reset()

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


def test_memory_budget_window_reset():
    """测试内存模式下预算按时间窗口自动重置。"""
    default_registry.reset()

    accountant = BudgetAccountant(
        "test-ns-window-memory",
        epsilon_total=2.0,
        delta_total=1e-5,
        window_seconds=0.1,
    )
    accountant.spend(1.5, 0.0)
    assert accountant.remaining()["epsilon"] == pytest.approx(0.5, abs=1e-9)

    # 等待窗口过期
    time.sleep(0.15)

    # 窗口到期后应自动重置，可继续消费
    accountant.spend(1.5, 0.0)
    assert accountant.remaining()["epsilon"] == pytest.approx(0.5, abs=1e-9)


def test_sqlite_budget_window_reset(tmp_path):
    """测试 SQLite 模式下预算按时间窗口自动重置，并能在新实例间共享。"""
    default_registry.reset()

    db_file = os.path.join(tmp_path, "budget_window_test.db")
    os.environ["PRIVACY_BUDGET_DB"] = db_file

    try:
        acc1 = BudgetAccountant(
            "test-ns-window-sqlite",
            epsilon_total=2.0,
            delta_total=1e-5,
            window_seconds=0.1,
        )
        acc1.spend(1.5, 0.0)
        assert acc1.remaining()["epsilon"] == pytest.approx(0.5, abs=1e-9)

        # 等待窗口过期
        time.sleep(0.15)

        # 清空注册表，模拟另一个进程的新实例
        default_registry.reset()
        acc2 = BudgetAccountant(
            "test-ns-window-sqlite",
            epsilon_total=2.0,
            delta_total=1e-5,
            window_seconds=0.1,
        )
        # 新实例应读取到已重置的预算
        assert acc2.remaining()["epsilon"] == pytest.approx(2.0, abs=1e-9)

        # 继续消费验证窗口生效
        acc2.spend(1.0, 0.0)
        assert acc2.remaining()["epsilon"] == pytest.approx(1.0, abs=1e-9)

    finally:
        os.environ.pop("PRIVACY_BUDGET_DB", None)


def test_env_budget_window_seconds():
    """测试通过环境变量 PRIVACY_BUDGET_WINDOW_SECONDS 配置时间窗口。"""
    default_registry.reset()
    os.environ["PRIVACY_BUDGET_WINDOW_SECONDS"] = "0.05"

    try:
        accountant = BudgetAccountant(
            "test-ns-env-window",
            epsilon_total=2.0,
            delta_total=1e-5,
        )
        assert accountant.window_seconds == 0.05

        accountant.spend(1.5, 0.0)
        assert accountant.remaining()["epsilon"] == pytest.approx(0.5, abs=1e-9)

        time.sleep(0.08)
        accountant.spend(1.0, 0.0)
        assert accountant.remaining()["epsilon"] == pytest.approx(1.0, abs=1e-9)

    finally:
        os.environ.pop("PRIVACY_BUDGET_WINDOW_SECONDS", None)


def test_budget_registry_crud_and_warning():
    """测试 BudgetRegistry 的完整生命周期方法及参数冲突警告。"""
    registry = BudgetRegistry()
    assert registry.get("hr") is None

    # 1. get_or_create 首次创建
    acct1 = registry.get_or_create("hr", epsilon_total=10.0, delta_total=1e-4)
    assert acct1.epsilon_total == 10.0
    assert registry.get("hr") is acct1

    # 2. 传入重复冲突参数时发出 UserWarning 提示参数被忽略
    with pytest.warns(UserWarning, match="already exists"):
        acct2 = registry.get_or_create("hr", epsilon_total=999.0, delta_total=1e-4)

    assert acct2 is acct1
    assert acct2.epsilon_total == 10.0

    # 3. 显式传入 window_seconds 冲突时也应告警
    acct3 = registry.get_or_create("window-ns", epsilon_total=5.0, window_seconds=60.0)
    with pytest.warns(UserWarning, match="window_seconds"):
        acct4 = registry.get_or_create("window-ns", window_seconds=120.0)
    assert acct4 is acct3
    assert acct3.window_seconds == 60.0

    # 4. 未提供参数时不应触发冲突告警（recwarn 收集当前测试所有警告）
    registry.get_or_create("hr")  # 不指定预算参数，视为"不关心"

    # 5. remove 指定 namespace 实例
    removed = registry.remove("hr")
    assert removed is acct1
    assert registry.get("hr") is None

    # 6. reset 清空所有实例
    registry.get_or_create("sales", epsilon_total=5.0)
    registry.get_or_create("marketing", epsilon_total=5.0)
    assert registry.get("sales") is not None
    assert registry.get("marketing") is not None

    registry.reset()
    assert registry.get("sales") is None
    assert registry.get("marketing") is None


def test_get_or_create_no_warning_when_params_omitted(recwarn):
    """未显式传入预算参数时，即使现有实例配置不同也不应告警。"""
    registry = BudgetRegistry()
    registry.get_or_create("ns-no-warn", epsilon_total=100.0, delta_total=1e-3)
    registry.get_or_create("ns-no-warn")  # 未提供参数，视为不关心
    user_warnings = [w for w in recwarn.list if issubclass(w.category, UserWarning)]
    assert not user_warnings
