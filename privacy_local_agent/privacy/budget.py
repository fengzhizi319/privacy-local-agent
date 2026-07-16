"""隐私预算记账模块。

提供单命名空间（namespace）下的隐私预算跟踪，基于单例模式保证同一命名空间
只存在一份预算状态，并通过锁保证并发安全。当累计消耗超过总预算时抛出
PrivacyBudgetExhausted 异常。

Privacy budget accounting module. Tracks per-namespace epsilon/delta consumption
using a thread-safe singleton pattern and raises PrivacyBudgetExhausted when
budget is exhausted.
"""

import os
import sqlite3
import threading
from typing import Dict, Optional

from ..observability.metrics import BUDGET_REMAINING


class PrivacyBudgetExhausted(Exception):
    """隐私预算已耗尽异常。

    当某命名空间下的 epsilon 或 delta 累计消耗超过预设上限时抛出。
    Raised when the privacy budget (epsilon or delta) is exhausted for a namespace.
    """

    pass


class BudgetAccountant:
    """隐私预算会计师（单例/持久化）。

    每个 namespace 对应一个单例实例，记录该命名空间的总预算与已消耗预算。
    通过类级锁 _lock 控制实例创建，实例级锁 _mu 控制 spend/remaining 操作，
    确保多线程环境下的预算扣减是原子的。
    若配置了环境变量 PRIVACY_BUDGET_DB，则将预算存储至 SQLite 中以支持多进程/多节点共享。

    Attributes:
        _instances: 类级字典，保存各 namespace 对应的 BudgetAccountant 实例。
        _lock: 类级锁，用于保护 _instances 的并发读写。
        namespace: 当前实例所属的命名空间。
        epsilon_total: epsilon 总预算。
        delta_total: delta 总预算。
        epsilon_spent: 已消耗 epsilon。
        delta_spent: 已消耗 delta。
        _mu: 实例级锁，保护预算扣减与查询的原子性。
    """

    _instances: Dict[str, "BudgetAccountant"] = {}
    _lock = threading.Lock()

    def __new__(cls, namespace: str, epsilon_total: float = 10.0, delta_total: float = 1e-4):
        """获取或创建指定命名空间的 BudgetAccountant 单例。

        Args:
            namespace: 命名空间标识，用于隔离不同租户/数据集的预算。
            epsilon_total: epsilon 总预算，默认 10.0。
            delta_total: delta 总预算，默认 1e-4。

        Returns:
            该 namespace 对应的 BudgetAccountant 实例。

        Note:
            首次创建实例时才会使用传入的 epsilon_total/delta_total；
            若实例已存在，则直接返回已有实例，忽略后续参数。
        """
        with cls._lock:
            if namespace not in cls._instances:
                instance = super().__new__(cls)
                instance.namespace = namespace
                instance.epsilon_total = epsilon_total
                instance.delta_total = delta_total
                instance.epsilon_spent = 0.0
                instance.delta_spent = 0.0
                instance._mu = threading.Lock()

                # 初始化共享数据库，如果设置了持久化路径
                db_path = os.environ.get("PRIVACY_BUDGET_DB")
                if db_path:
                    conn = sqlite3.connect(db_path, timeout=10.0)
                    try:
                        with conn:
                            conn.execute(
                                "CREATE TABLE IF NOT EXISTS privacy_budgets ("
                                "namespace TEXT PRIMARY KEY, "
                                "epsilon_total REAL, "
                                "delta_total REAL, "
                                "epsilon_spent REAL, "
                                "delta_spent REAL"
                                ")"
                            )
                            # 预插入当前的 budget 信息（如果尚未存在）
                            conn.execute(
                                "INSERT OR IGNORE INTO privacy_budgets "
                                "(namespace, epsilon_total, delta_total, epsilon_spent, delta_spent) "
                                "VALUES (?, ?, ?, 0.0, 0.0)",
                                (namespace, epsilon_total, delta_total),
                            )
                    finally:
                        conn.close()

                cls._instances[namespace] = instance
            return cls._instances[namespace]

    def spend(self, epsilon: float, delta: float = 0.0):
        """消耗隐私预算。

        在加锁环境下计算新的累计消耗量；若超过总预算则抛出异常，
        否则更新已消耗预算。若配置了 SQLite，则通过排他性写入事务保证多进程一致性。

        Args:
            epsilon: 本次操作需要消耗的 epsilon 预算。
            delta: 本次操作需要消耗的 delta 预算，默认 0。

        Raises:
            PrivacyBudgetExhausted: 当累计 epsilon 或 delta 超过总预算时。
        """
        db_path = os.environ.get("PRIVACY_BUDGET_DB")
        if db_path:
            with self._mu:
                conn = sqlite3.connect(db_path, timeout=10.0)
                try:
                    # 使用 BEGIN IMMEDIATE 排他性事务锁定数据库
                    conn.execute("BEGIN IMMEDIATE")
                    cursor = conn.execute(
                        "SELECT epsilon_total, delta_total, epsilon_spent, delta_spent "
                        "FROM privacy_budgets WHERE namespace = ?",
                        (self.namespace,),
                    )
                    row = cursor.fetchone()
                    if row:
                        eps_total, del_total, eps_spent, del_spent = row
                    else:
                        eps_total, del_total, eps_spent, del_spent = (
                            self.epsilon_total,
                            self.delta_total,
                            0.0,
                            0.0,
                        )
                        conn.execute(
                            "INSERT INTO privacy_budgets "
                            "(namespace, epsilon_total, delta_total, epsilon_spent, delta_spent) "
                            "VALUES (?, ?, ?, 0.0, 0.0)",
                            (self.namespace, eps_total, del_total),
                        )

                    new_eps = eps_spent + epsilon
                    new_delta = del_spent + delta

                    if new_eps > eps_total or new_delta > del_total:
                        raise PrivacyBudgetExhausted(
                            f"Privacy budget exhausted in namespace {self.namespace}: "
                            f"epsilon={new_eps}/{eps_total}, delta={new_delta}/{del_total}"
                        )

                    conn.execute(
                        "UPDATE privacy_budgets SET epsilon_spent = ?, delta_spent = ? "
                        "WHERE namespace = ?",
                        (new_eps, new_delta, self.namespace),
                    )
                    conn.commit()
                    self._update_metrics(eps_total, del_total, new_eps, new_delta)
                except Exception as e:
                    conn.rollback()
                    raise e
                finally:
                    conn.close()
        else:
            with self._mu:
                new_eps = self.epsilon_spent + epsilon
                new_delta = self.delta_spent + delta
                if new_eps > self.epsilon_total or new_delta > self.delta_total:
                    raise PrivacyBudgetExhausted(
                        f"Privacy budget exhausted in namespace {self.namespace}: "
                        f"epsilon={new_eps}/{self.epsilon_total}, delta={new_delta}/{self.delta_total}"
                    )
                self.epsilon_spent = new_eps
                self.delta_spent = new_delta
                self._update_metrics(
                    self.epsilon_total, self.delta_total, new_eps, new_delta
                )

    def _update_metrics(
        self,
        epsilon_total: float,
        delta_total: float,
        epsilon_spent: float,
        delta_spent: float,
    ) -> None:
        """Update Prometheus gauges for remaining budget."""
        BUDGET_REMAINING.labels(namespace=self.namespace, budget_type="epsilon").set(
            epsilon_total - epsilon_spent
        )
        BUDGET_REMAINING.labels(namespace=self.namespace, budget_type="delta").set(
            delta_total - delta_spent
        )

    def remaining(self) -> Dict[str, float]:
        """查询剩余隐私预算。

        Returns:
            包含 epsilon 与 delta 剩余量的字典，例如
            {"epsilon": 8.0, "delta": 1e-4}。
        """
        db_path = os.environ.get("PRIVACY_BUDGET_DB")
        if db_path:
            with self._mu:
                conn = sqlite3.connect(db_path, timeout=10.0)
                try:
                    cursor = conn.execute(
                        "SELECT epsilon_total, delta_total, epsilon_spent, delta_spent "
                        "FROM privacy_budgets WHERE namespace = ?",
                        (self.namespace,),
                    )
                    row = cursor.fetchone()
                    if row:
                        eps_total, del_total, eps_spent, del_spent = row
                        self._update_metrics(eps_total, del_total, eps_spent, del_spent)
                        return {
                            "epsilon": eps_total - eps_spent,
                            "delta": del_total - del_spent,
                        }
                    else:
                        self._update_metrics(
                            self.epsilon_total, self.delta_total, 0.0, 0.0
                        )
                        return {
                            "epsilon": self.epsilon_total,
                            "delta": self.delta_total,
                        }
                finally:
                    conn.close()
        else:
            with self._mu:
                self._update_metrics(
                    self.epsilon_total,
                    self.delta_total,
                    self.epsilon_spent,
                    self.delta_spent,
                )
                return {
                    "epsilon": self.epsilon_total - self.epsilon_spent,
                    "delta": self.delta_total - self.delta_spent,
                }
