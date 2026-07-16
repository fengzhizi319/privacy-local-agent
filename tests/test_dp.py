"""差分隐私算法正确性测试。

覆盖 Laplace/Gaussian 机制、clipping、delta 预算消耗、组合定理以及
本地差分隐私（Local DP）随机响应与频率估计。
"""

from __future__ import annotations

import statistics

import pytest

from privacy_local_agent.privacy.budget import BudgetAccountant, PrivacyBudgetExhausted
from privacy_local_agent.privacy.dp import DPApi, LocalDPApi


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


class TestBinaryRandomizedResponse:
    def test_perturb_binary_returns_zero_or_one(self):
        api = LocalDPApi(seed=42)
        for _ in range(100):
            assert api.perturb_binary(1, epsilon=1.0) in (0, 1)
            assert api.perturb_binary(0, epsilon=1.0) in (0, 1)

    def test_perturb_binary_rejects_invalid_value(self):
        api = LocalDPApi()
        with pytest.raises(ValueError, match="binary value must be 0 or 1"):
            api.perturb_binary(2, epsilon=1.0)

    def test_perturb_binary_rejects_invalid_epsilon(self):
        api = LocalDPApi()
        with pytest.raises(ValueError, match="epsilon must be positive"):
            api.perturb_binary(1, epsilon=0.0)
        with pytest.raises(ValueError, match="epsilon must be positive"):
            api.perturb_binary(1, epsilon=-1.0)

    def test_higher_epsilon_preserves_more_truth(self):
        """ε 越大，扰动后保持原值的比例应越高。"""
        api_low = LocalDPApi(seed=42)
        api_high = LocalDPApi(seed=42)

        n = 2000
        low_eps = [api_low.perturb_binary(1, epsilon=0.1) for _ in range(n)]
        high_eps = [api_high.perturb_binary(1, epsilon=5.0) for _ in range(n)]

        assert sum(low_eps) / n < sum(high_eps) / n

    def test_frequency_estimation_unbiased(self):
        """频率估计应接近真实频率（大样本下）。"""
        api = LocalDPApi(seed=42)
        true_values = [1 if i % 4 == 0 else 0 for i in range(4000)]
        true_freq = sum(true_values) / len(true_values)

        reported = api.perturb_binary_batch(true_values, epsilon=1.0)
        est_freq = api.estimate_binary_frequency(reported, epsilon=1.0)

        assert abs(est_freq - true_freq) < 0.05

    def test_frequency_estimation_empty(self):
        api = LocalDPApi()
        assert api.estimate_binary_frequency([], epsilon=1.0) == 0.0


class TestCategoricalRandomizedResponse:
    def test_perturb_categorical_returns_valid_category(self):
        api = LocalDPApi(seed=42)
        categories = ["A", "B", "C"]
        for _ in range(100):
            assert api.perturb_categorical("A", categories, epsilon=1.0) in categories

    def test_perturb_categorical_rejects_unknown_value(self):
        api = LocalDPApi()
        with pytest.raises(ValueError, match="value must be one of the provided categories"):
            api.perturb_categorical("D", ["A", "B", "C"], epsilon=1.0)

    def test_perturb_categorical_rejects_single_category(self):
        api = LocalDPApi()
        with pytest.raises(ValueError, match="categories must contain at least 2 items"):
            api.perturb_categorical("A", ["A"], epsilon=1.0)

    def test_histogram_estimation_unbiased(self):
        """本地直方图估计应接近真实分布（大样本下）。"""
        api = LocalDPApi(seed=42)
        categories = ["A", "B", "C"]
        # 真实分布：A=50%, B=30%, C=20%
        true_values = ["A"] * 5000 + ["B"] * 3000 + ["C"] * 2000
        api.rng.shuffle(true_values)

        reported = api.perturb_categorical_batch(true_values, categories, epsilon=1.0)
        hist = api.estimate_categorical_histogram(reported, categories, epsilon=1.0)

        assert abs(hist["A"] - 0.50) < 0.05
        assert abs(hist["B"] - 0.30) < 0.05
        assert abs(hist["C"] - 0.20) < 0.05
        assert abs(sum(hist.values()) - 1.0) < 1e-9

    def test_histogram_normalizes_when_all_negative_estimates(self):
        """当纠偏出现负值时，应截断并归一化。"""
        api = LocalDPApi()
        hist = api.estimate_categorical_histogram(
            ["A", "A"], categories=["A", "B"], epsilon=0.1
        )
        assert abs(sum(hist.values()) - 1.0) < 1e-9
        assert all(v >= 0 for v in hist.values())


class TestBatchUtilities:
    def test_perturb_binary_batch_length(self):
        api = LocalDPApi(seed=42)
        values = [1, 0, 1, 1, 0]
        reported = api.perturb_binary_batch(values, epsilon=1.0)
        assert len(reported) == len(values)
        assert all(v in (0, 1) for v in reported)

    def test_perturb_categorical_batch_length(self):
        api = LocalDPApi(seed=42)
        values = ["A", "B", "A", "C"]
        reported = api.perturb_categorical_batch(values, ["A", "B", "C"], epsilon=1.0)
        assert len(reported) == len(values)
        assert all(v in ["A", "B", "C"] for v in reported)


class TestStatisticalProperties:
    def test_binary_noise_mean_tends_to_zero(self):
        """大量扰动后，二值估计误差均值应接近 0。"""
        trials = []
        for seed in range(20):
            api = LocalDPApi(seed=seed)
            true_values = [1 if i % 2 == 0 else 0 for i in range(1000)]
            reported = api.perturb_binary_batch(true_values, epsilon=1.0)
            est = api.estimate_binary_frequency(reported, epsilon=1.0)
            trials.append(est - 0.5)

        assert abs(statistics.mean(trials)) < 0.02
