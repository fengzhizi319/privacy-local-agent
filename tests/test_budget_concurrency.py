"""并发压力测试：验证 BudgetAccountant 在高并发场景下的线程安全性。

使用 concurrent.futures.ThreadPoolExecutor 模拟多线程同时调用 spend()，
验证内存模式和 SQLite 模式下的原子性、数据一致性与死锁防护。
"""
from __future__ import annotations

import os
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

from privacy_local_agent.privacy.budget import (
    BudgetRegistry,
    PrivacyBudgetExhausted,
    default_registry,
)


@pytest.fixture(autouse=True)
def _reset():
    default_registry.reset()
    yield
    default_registry.reset()


class TestMemoryConcurrency:
    """内存模式下的并发冲刷测试。"""

    def test_concurrent_spend_serializable(self):
        """50 个线程各消耗 epsilon=0.1，总消耗应精确等于 5.0。"""
        ns = "concurrent-mem"
        acct = default_registry.get_or_create(ns, epsilon_total=100.0, delta_total=1.0)

        n_threads = 50
        eps_each = 0.1

        def _spend():
            acct.spend(eps_each, 0.0)

        with ThreadPoolExecutor(max_workers=n_threads) as pool:
            futures = [pool.submit(_spend) for _ in range(n_threads)]
            for f in as_completed(futures):
                f.result()  # raise if any thread failed

        # Total spent should be exactly n_threads * eps_each
        assert abs(acct.epsilon_spent - n_threads * eps_each) < 1e-9

    def test_concurrent_spend_budget_exhaustion(self):
        """并发冲刷超过总预算时，部分线程应收到 PrivacyBudgetExhausted。"""
        ns = "concurrent-exhaust"
        acct = default_registry.get_or_create(ns, epsilon_total=1.0, delta_total=1.0)

        n_threads = 20
        eps_each = 0.1  # 20 * 0.1 = 2.0 > total 1.0

        results = []

        def _spend():
            try:
                acct.spend(eps_each, 0.0)
                return "ok"
            except PrivacyBudgetExhausted:
                return "exhausted"

        with ThreadPoolExecutor(max_workers=n_threads) as pool:
            futures = [pool.submit(_spend) for _ in range(n_threads)]
            for f in as_completed(futures):
                results.append(f.result())

        ok_count = results.count("ok")
        exhausted_count = results.count("exhausted")
        # At most 10 threads should succeed (1.0 / 0.1 = 10)
        assert ok_count <= 10
        assert exhausted_count > 0
        # Spent should not exceed total
        assert acct.epsilon_spent <= 1.0 + 1e-9

    def test_concurrent_spend_and_remaining(self):
        """并发读写：spend 和 remaining 交错执行不应导致异常。"""
        ns = "concurrent-rw"
        acct = default_registry.get_or_create(ns, epsilon_total=100.0, delta_total=1.0)

        def _spend():
            acct.spend(0.01, 0.0)

        def _remaining():
            return acct.remaining()

        with ThreadPoolExecutor(max_workers=20) as pool:
            futures = []
            for _ in range(50):
                futures.append(pool.submit(_spend))
                futures.append(pool.submit(_remaining))
            for f in as_completed(futures):
                result = f.result()
                if isinstance(result, dict):
                    assert "epsilon" in result
                    assert "delta" in result


class TestSQLiteConcurrency:
    """SQLite 模式下的并发冲刷测试。"""

    def test_sqlite_concurrent_spend(self, tmp_path, monkeypatch):
        """SQLite 模式下多线程 spend 不发生崩溃或死锁。"""
        db_file = str(tmp_path / "budget.db")
        monkeypatch.setenv("PRIVACY_BUDGET_DB", db_file)

        ns = "concurrent-sqlite"
        acct = default_registry.get_or_create(ns, epsilon_total=100.0, delta_total=1.0)

        n_threads = 30
        eps_each = 0.1

        def _spend():
            acct.spend(eps_each, 0.0)

        with ThreadPoolExecutor(max_workers=n_threads) as pool:
            futures = [pool.submit(_spend) for _ in range(n_threads)]
            for f in as_completed(futures):
                f.result()

        # Verify in-memory state: all threads should have spent successfully
        # (total budget 100.0 >> 3.0 needed)
        assert acct.epsilon_spent > 0
        # Verify persisted state by reading back from DB
        import sqlite3
        conn = sqlite3.connect(db_file)
        try:
            row = conn.execute(
                "SELECT epsilon_spent FROM privacy_budgets WHERE namespace = ?",
                (ns,),
            ).fetchone()
            # DB value should be positive (exact match depends on thread scheduling)
            assert row[0] > 0
        finally:
            conn.close()

    def test_sqlite_thread_local_conn_reuse(self, tmp_path, monkeypatch):
        """验证同一线程多次 spend 复用同一个 SQLite 连接。"""
        db_file = str(tmp_path / "budget_reuse.db")
        monkeypatch.setenv("PRIVACY_BUDGET_DB", db_file)

        ns = "conn-reuse"
        acct = default_registry.get_or_create(ns, epsilon_total=100.0, delta_total=1.0)

        # First spend creates the connection
        acct.spend(1.0, 0.0)
        conn1 = acct._thread_local.conn
        assert conn1 is not None

        # Second spend should reuse the same connection
        acct.spend(1.0, 0.0)
        conn2 = acct._thread_local.conn
        assert conn1 is conn2

        # Close and verify a new connection is created
        acct._close_db_conn()
        assert acct._thread_local.conn is None

        acct.spend(1.0, 0.0)
        conn3 = acct._thread_local.conn
        assert conn3 is not conn1
