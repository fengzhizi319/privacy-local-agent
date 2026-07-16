"""差分隐私算法正确性测试。

覆盖 Laplace/Gaussian 机制、clipping、delta 预算消耗以及组合定理。
"""

from __future__ import annotations

import pytest

from privacy_local_agent.privacy.budget import BudgetAccountant, PrivacyBudgetExhausted
from privacy_local_agent.privacy.dp import DPApi


class TestDPCount:
    """计数操作测试。"""

    def test_count_laplace_basic(self) -> None:
        api = DPApi(namespace="test-count-laplace")
        result = api.count([1.0, 0.0, 1.0, 1.0], epsilon=10.0, mechanism="laplace")
        assert 0 <= result <= 5

    def test_count_gaussian_basic(self) -> None:
        api = DPApi(namespace="test-count-gaussian")
        result = api.count([1.0, 0.0, 1.0, 1.0], epsilon=10.0, delta=1e-5, mechanism="gaussian")
        assert 0 <= result <= 5

    def test_count_gaussian_requires_delta(self) -> None:
        api = DPApi(namespace="test-count-gaussian-delta")
        with pytest.raises(ValueError, match="delta must be positive"):
            api.count([1.0, 1.0], epsilon=1.0, delta=0.0, mechanism="gaussian")


class TestDPSum:
    """求和操作测试。"""

    def test_sum_laplace_with_clipping(self) -> None:
        api = DPApi(namespace="test-sum-laplace-clip")
        api.rng.seed(42)
        values = [1.0, 2.0, 3.0, 100.0]
        result = api.sum(
            values,
            epsilon=10.0,
            mechanism="laplace",
            clip_lower=0.0,
            clip_upper=10.0,
        )
        # clipped sum = 1+2+3+10 = 16, noise with scale (10-0)/10 = 1
        assert 10 <= result <= 25

    def test_sum_gaussian_requires_clip(self) -> None:
        api = DPApi(namespace="test-sum-gaussian-clip")
        with pytest.raises(ValueError, match="clip_lower and clip_upper"):
            api.sum([1.0, 2.0, 3.0], epsilon=1.0, delta=1e-6, mechanism="gaussian")

    def test_sum_gaussian_with_clip(self) -> None:
        api = DPApi(namespace="test-sum-gaussian-clip-ok")
        api.rng.seed(42)
        result = api.sum(
            [1.0, 2.0, 3.0],
            epsilon=10.0,
            delta=1e-5,
            mechanism="gaussian",
            clip_lower=0.0,
            clip_upper=10.0,
        )
        assert 0 <= result <= 20

    def test_sum_backward_compat_without_clip(self) -> None:
        api = DPApi(namespace="test-sum-compat")
        api.rng.seed(42)
        with pytest.warns(UserWarning, match="clip_lower/clip_upper not provided"):
            result = api.sum([1.0, 2.0, 3.0], epsilon=10.0, mechanism="laplace")
        assert 0 <= result <= 10


class TestDPMean:
    """均值操作测试。"""

    def test_mean_laplace_basic(self) -> None:
        api = DPApi(namespace="test-mean-laplace")
        api.rng.seed(42)
        result = api.mean(
            [1.0, 2.0, 3.0, 4.0, 5.0],
            epsilon=10.0,
            mechanism="laplace",
            clip_lower=0.0,
            clip_upper=10.0,
        )
        assert 0 <= result <= 10

    def test_mean_gaussian_basic(self) -> None:
        api = DPApi(namespace="test-mean-gaussian")
        api.rng.seed(42)
        result = api.mean(
            [1.0, 2.0, 3.0, 4.0, 5.0],
            epsilon=10.0,
            delta=1e-5,
            mechanism="gaussian",
            clip_lower=0.0,
            clip_upper=10.0,
        )
        assert 0 <= result <= 10

    def test_mean_empty(self) -> None:
        api = DPApi(namespace="test-mean-empty")
        assert api.mean([], epsilon=1.0) == 0.0


class TestDPBudget:
    """预算消耗测试。"""

    def test_laplace_consumes_epsilon_only(self) -> None:
        ns = "test-budget-laplace"
        api = DPApi(namespace=ns)
        accountant = BudgetAccountant(ns, epsilon_total=10.0, delta_total=1e-4)
        api.sum([1.0, 2.0], epsilon=1.0, mechanism="laplace", clip_lower=0.0, clip_upper=10.0)
        remaining = accountant.remaining()
        assert remaining["epsilon"] == pytest.approx(9.0, abs=1e-9)
        assert remaining["delta"] == pytest.approx(1e-4, abs=1e-9)

    def test_gaussian_consumes_delta(self) -> None:
        ns = "test-budget-gaussian"
        api = DPApi(namespace=ns)
        accountant = BudgetAccountant(ns, epsilon_total=10.0, delta_total=1e-4)
        api.sum(
            [1.0, 2.0],
            epsilon=1.0,
            delta=1e-5,
            mechanism="gaussian",
            clip_lower=0.0,
            clip_upper=10.0,
        )
        remaining = accountant.remaining()
        assert remaining["epsilon"] == pytest.approx(9.0, abs=1e-9)
        assert remaining["delta"] == pytest.approx(9e-5, abs=1e-9)

    def test_mean_composition_consumes_full_budget(self) -> None:
        ns = "test-budget-mean"
        api = DPApi(namespace=ns)
        accountant = BudgetAccountant(ns, epsilon_total=10.0, delta_total=1e-4)
        api.mean(
            [1.0, 2.0, 3.0],
            epsilon=2.0,
            delta=1e-5,
            mechanism="gaussian",
            clip_lower=0.0,
            clip_upper=10.0,
        )
        remaining = accountant.remaining()
        assert remaining["epsilon"] == pytest.approx(8.0, abs=1e-9)
        assert remaining["delta"] == pytest.approx(9e-5, abs=1e-9)

    def test_exhausted_budget_raises(self) -> None:
        ns = "test-budget-exhaust"
        # 先创建 BudgetAccountant 以指定较小总预算；DPApi 会复用该单例。
        BudgetAccountant(ns, epsilon_total=0.5, delta_total=1e-4)
        api = DPApi(namespace=ns)
        with pytest.raises(PrivacyBudgetExhausted):
            api.count([1.0, 1.0], epsilon=1.0)
