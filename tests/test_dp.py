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
        BudgetAccountant._instances.pop(ns, None)
        api = DPApi(namespace=ns)
        accountant = BudgetAccountant(ns, epsilon_total=10.0, delta_total=1e-4)
        api.sum([1.0, 2.0], epsilon=1.0, mechanism="laplace", clip_lower=0.0, clip_upper=10.0)
        remaining = accountant.remaining()
        assert remaining["epsilon"] == pytest.approx(9.0, abs=1e-9)
        assert remaining["delta"] == pytest.approx(1e-4, abs=1e-9)

    def test_gaussian_consumes_delta(self) -> None:
        ns = "test-budget-gaussian"
        BudgetAccountant._instances.pop(ns, None)
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

    def test_mean_composition_consumes_full_budget(self, monkeypatch) -> None:
        ns = "test-budget-mean"
        BudgetAccountant._instances.pop(ns, None)
        api = DPApi(namespace=ns)
        accountant = BudgetAccountant(ns, epsilon_total=10.0, delta_total=1e-4)
        # 固定高斯噪声，避免 count 被截断为 0 导致低频保护提前返回。
        monkeypatch.setattr(api.rng, "gauss", lambda mu, sigma: 0.1)
        api.mean(
            [1.0, 2.0, 3.0],
            epsilon=2.0,
            delta=1e-5,
            mechanism="gaussian",
            clip_lower=0.0,
            clip_upper=10.0,
            min_count=0.0,  # 避免因计数较小触发低频保护提前返回，确保消耗完整预算
        )
        remaining = accountant.remaining()
        assert remaining["epsilon"] == pytest.approx(8.0, abs=1e-9)
        assert remaining["delta"] == pytest.approx(9e-5, abs=1e-9)


    def test_exhausted_budget_raises(self) -> None:
        ns = "test-budget-exhaust"
        BudgetAccountant._instances.pop(ns, None)
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


class TestAnalyticGaussianAndMeanThreshold:
    def test_calibrate_analytic_gaussian_basic(self) -> None:
        from privacy_local_agent.privacy.dp import calibrate_analytic_gaussian
        sigma = calibrate_analytic_gaussian(epsilon=1.0, delta=1e-5, sensitivity=1.0)
        assert sigma > 0
        # 经典公式为 ~4.84，解析高斯一般更小
        assert sigma < 5.0

    def test_mean_thresholding_protection(self) -> None:
        ns = "test-mean-thresh"
        BudgetAccountant(ns, epsilon_total=100.0, delta_total=1.0)
        api = DPApi(namespace=ns)
        # 正常计算
        res = api.mean([10.0] * 10, epsilon=10.0, min_count=2.0)
        assert res > 0.0
        # 计数过小，触发低频保护返回 0.0
        res_shielded = api.mean([10.0] * 3, epsilon=10.0, min_count=5.0)
        assert res_shielded == 0.0

    def test_dp_histogram_joint_sensitivity(self) -> None:
        ns = "test-hist-joint"
        BudgetAccountant(ns, epsilon_total=100.0, delta_total=1.0)
        api = DPApi(namespace=ns)
        values = ["A"] * 100 + ["B"] * 200 + ["C"] * 50
        categories = ["A", "B", "C", "D"]
        res = api.histogram(values, categories, epsilon=10.0, mechanism="laplace")
        assert len(res) == 4
        assert res["A"] > 50
        assert res["B"] > 100
        assert res["C"] > 20
        assert res["D"] >= 0.0
        
        # 测试高斯机制下直方图
        res_g = api.histogram(values, categories, epsilon=10.0, delta=1e-5, mechanism="gaussian")
        assert len(res_g) == 4
        assert res_g["A"] > 50




class TestVectorizedClip:
    """测试 _clip_values 的向量化裁剪与回退路径。"""

    def test_clip_values_basic(self):
        import numpy as np
        api = DPApi(namespace="test-clip-basic")
        result = api._clip_values([1.0, 2.0, 100.0, -5.0], 0.0, 10.0)
        assert isinstance(result, np.ndarray)
        np.testing.assert_array_equal(result, [1.0, 2.0, 10.0, 0.0])

    def test_clip_values_with_nan(self):
        import math

        api = DPApi(namespace="test-clip-nan")
        result = api._clip_values([1.0, float("nan"), 100.0], 0.0, 10.0)
        assert result[0] == 1.0
        assert result[2] == 10.0
        assert math.isnan(result[1])


class TestNoisyAggregation:
    """测试对已聚合中间结果直接加噪的接口。"""

    def test_noisy_count_laplace(self):
        ns = "test-noisy-count"
        BudgetAccountant(ns, epsilon_total=10.0, delta_total=1.0)
        api = DPApi(namespace=ns)
        api.rng.seed(42)
        result = api.noisy_count(100.0, epsilon=1.0, mechanism="laplace")
        assert result >= 0.0
        accountant = BudgetAccountant(ns)
        assert accountant.remaining()["epsilon"] == pytest.approx(9.0, abs=1e-9)

    def test_noisy_sum_requires_non_negative_sensitivity(self):
        ns = "test-noisy-sum-validation"
        api = DPApi(namespace=ns)
        with pytest.raises(ValueError, match="sensitivity"):
            api.noisy_sum(100.0, sensitivity=-1.0, epsilon=1.0)

    def test_noisy_sum_laplace(self):
        ns = "test-noisy-sum"
        BudgetAccountant(ns, epsilon_total=10.0, delta_total=1.0)
        api = DPApi(namespace=ns)
        api.rng.seed(42)
        result = api.noisy_sum(
            100.0, sensitivity=10.0, epsilon=1.0, mechanism="laplace"
        )
        assert result >= 80.0  # 噪声尺度为 10，大致范围
        accountant = BudgetAccountant(ns)
        assert accountant.remaining()["epsilon"] == pytest.approx(9.0, abs=1e-9)

    def test_noisy_mean_low_count_shield(self):
        ns = "test-noisy-mean-shield"
        BudgetAccountant(ns, epsilon_total=10.0, delta_total=1.0)
        api = DPApi(namespace=ns)
        api.rng.seed(42)
        result = api.noisy_mean(
            true_sum=100.0,
            true_count=2.0,
            sensitivity=10.0,
            epsilon=1.0,
            mechanism="laplace",
            min_count=5.0,
        )
        assert result == 0.0

    def test_noisy_histogram(self):
        ns = "test-noisy-hist"
        BudgetAccountant(ns, epsilon_total=10.0, delta_total=1.0)
        api = DPApi(namespace=ns)
        api.rng.seed(42)
        result = api.noisy_histogram(
            {"A": 100.0, "B": 200.0, "C": 50.0},
            epsilon=10.0,
            mechanism="laplace",
        )
        assert len(result) == 3
        assert result["A"] > 50
        assert result["B"] > 150
        assert result["C"] > 20


class TestChunkedAggregation:
    """测试分块流式 DP 聚合接口。"""

    def test_chunked_count(self):
        ns = "test-chunked-count"
        BudgetAccountant(ns, epsilon_total=10.0, delta_total=1.0)
        api = DPApi(namespace=ns)
        api.rng.seed(42)
        chunks = [[1.0, 0.0, 1.0], [0.0, 1.0, 1.0, 1.0], [0.0]]
        result = api.chunked_count(chunks, epsilon=10.0, mechanism="laplace")
        assert 4 <= result <= 7
        accountant = BudgetAccountant(ns)
        assert accountant.remaining()["epsilon"] == pytest.approx(0.0, abs=1e-9)

    def test_chunked_sum_requires_clip(self):
        ns = "test-chunked-sum-clip"
        api = DPApi(namespace=ns)
        with pytest.raises(ValueError, match="clip_lower and clip_upper"):
            api.chunked_sum([[1.0, 2.0]], epsilon=1.0)

    def test_chunked_sum(self):
        ns = "test-chunked-sum"
        BudgetAccountant(ns, epsilon_total=10.0, delta_total=1.0)
        api = DPApi(namespace=ns)
        api.rng.seed(42)
        chunks = [[1.0, 2.0, 3.0], [100.0, 5.0], [8.0]]
        result = api.chunked_sum(
            chunks,
            epsilon=10.0,
            mechanism="laplace",
            clip_lower=0.0,
            clip_upper=10.0,
        )
        # clipped sum = 1+2+3+10+5+8 = 29
        assert 20 <= result <= 40

    def test_chunked_mean(self):
        ns = "test-chunked-mean"
        BudgetAccountant(ns, epsilon_total=10.0, delta_total=1.0)
        api = DPApi(namespace=ns)
        api.rng.seed(42)
        chunks = [[1.0, 2.0, 3.0], [4.0, 5.0]]
        result = api.chunked_mean(
            chunks,
            epsilon=10.0,
            mechanism="laplace",
            clip_lower=0.0,
            clip_upper=10.0,
        )
        assert 0 <= result <= 10

    def test_chunked_histogram(self):
        ns = "test-chunked-hist"
        BudgetAccountant(ns, epsilon_total=10.0, delta_total=1.0)
        api = DPApi(namespace=ns)
        api.rng.seed(42)
        chunks = [["A", "B", "A"], ["B", "C", "A"], ["B"]]
        result = api.chunked_histogram(
            chunks, categories=["A", "B", "C"], epsilon=10.0, mechanism="laplace"
        )
        assert result["A"] > 1
        assert result["B"] > 2
        assert result["C"] > 0


class TestDataAdapters:
    """测试 DP 输入支持多种数据格式（list/ndarray/pandas DataFrame）。"""

    def test_count_with_pandas_series(self):
        pd = pytest.importorskip("pandas")
        ns = "test-count-pd-series"
        BudgetAccountant(ns, epsilon_total=10.0, delta_total=1.0)
        api = DPApi(namespace=ns)
        api.rng.seed(42)
        series = pd.Series([1.0, 0.0, 1.0, 1.0])
        result = api.count(series, epsilon=10.0, mechanism="laplace")
        assert 0 <= result <= 5

    def test_sum_with_pandas_dataframe_column(self):
        pd = pytest.importorskip("pandas")
        ns = "test-sum-pd-df"
        BudgetAccountant(ns, epsilon_total=10.0, delta_total=1.0)
        api = DPApi(namespace=ns)
        api.rng.seed(42)
        df = pd.DataFrame({"salary": [1.0, 2.0, 3.0, 100.0], "age": [20, 30, 40, 50]})
        result = api.sum(
            df,
            epsilon=10.0,
            mechanism="laplace",
            clip_lower=0.0,
            clip_upper=10.0,
            column="salary",
        )
        assert 10 <= result <= 25

    def test_sum_with_numpy_array(self):
        np = pytest.importorskip("numpy")
        ns = "test-sum-np"
        BudgetAccountant(ns, epsilon_total=10.0, delta_total=1.0)
        api = DPApi(namespace=ns)
        api.rng.seed(42)
        arr = np.array([1.0, 2.0, 3.0, 100.0])
        result = api.sum(
            arr, epsilon=10.0, mechanism="laplace", clip_lower=0.0, clip_upper=10.0
        )
        assert 10 <= result <= 25

    def test_histogram_with_pandas_series(self):
        pd = pytest.importorskip("pandas")
        ns = "test-hist-pd"
        BudgetAccountant(ns, epsilon_total=10.0, delta_total=1.0)
        api = DPApi(namespace=ns)
        api.rng.seed(42)
        series = pd.Series(["A", "B", "A", "C"])
        result = api.histogram(
            series, categories=["A", "B", "C", "D"], epsilon=10.0, mechanism="laplace"
        )
        assert len(result) == 4
        assert result["A"] > 0.5

    def test_dataframe_requires_column(self):
        pd = pytest.importorskip("pandas")
        ns = "test-df-requires-column"
        api = DPApi(namespace=ns)
        df = pd.DataFrame({"a": [1.0, 2.0]})
        with pytest.raises(ValueError, match="column"):
            api.count(df, epsilon=1.0)


class TestDPResultAndPostProcessing:
    def test_dp_result_metadata(self):
        from privacy_local_agent.privacy.dp import DPResult

        ns = "test-dp-result"
        BudgetAccountant(ns, epsilon_total=10.0, delta_total=1.0)
        api = DPApi(namespace=ns)
        api.rng.seed(42)
        res = api.sum(
            [1.0, 2.0, 3.0],
            epsilon=1.0,
            mechanism="laplace",
            clip_lower=0.0,
            clip_upper=10.0,
            return_details=True,
        )
        assert isinstance(res, DPResult)
        assert res.noise_mechanism == "laplace"
        assert res.epsilon_spent == 1.0
        assert len(res.confidence_interval) == 2
        assert res.confidence_interval[0] < res.value < res.confidence_interval[1]

    def test_post_processing_options(self):
        ns = "test-dp-postprocess"
        BudgetAccountant(ns, epsilon_total=10.0, delta_total=1.0)
        api = DPApi(namespace=ns)
        api.rng.seed(42)
        res = api.count(
            [1.0, 2.0, 3.0],
            epsilon=1.0,
            round_int=True,
            clip_non_negative=True,
        )
        assert isinstance(res, float)
        assert res.is_integer()
        assert res >= 0.0


class TestSparseMatrixDP:
    def test_sparse_matrix_count_and_sum(self):
        sp = pytest.importorskip("scipy.sparse")
        ns = "test-sparse-dp"
        BudgetAccountant(ns, epsilon_total=20.0, delta_total=1.0)
        api = DPApi(namespace=ns)
        api.rng.seed(42)

        sparse_arr = sp.csr_matrix([1.0, 0.0, 2.0, 0.0, 3.0])
        count_res = api.count(sparse_arr, epsilon=5.0)
        assert count_res >= 0.0

        sum_res = api.sum(
            sparse_arr, epsilon=5.0, clip_lower=0.0, clip_upper=10.0
        )
        assert sum_res > 0.0


class TestBatchDP:
    def test_batch_count_and_sum(self):
        np = pytest.importorskip("numpy")
        from privacy_local_agent.privacy.dp import DPResult

        ns = "test-batch-dp"
        BudgetAccountant(ns, epsilon_total=100.0, delta_total=1.0)
        api = DPApi(namespace=ns)
        api.rng.seed(42)

        data = np.array([
            [1.0, 2.0],
            [3.0, 4.0],
            [5.0, 6.0]
        ])

        batch_counts = api.batch_count(data, epsilon=2.0)
        assert len(batch_counts) == 2

        batch_sums = api.batch_sum(
            data,
            epsilon=2.0,
            clip_lower=[0.0, 0.0],
            clip_upper=[10.0, 10.0],
            return_details=True,
        )
        assert isinstance(batch_sums, DPResult)
        assert len(batch_sums.value) == 2
        assert len(batch_sums.confidence_interval) == 2


class TestRefinedDPFixes:
    def test_mean_sparse_matrix_shape_and_delta_ci(self):
        sp = pytest.importorskip("scipy.sparse")
        from privacy_local_agent.privacy.dp import DPResult

        ns = "test-sparse-mean-delta"
        BudgetAccountant(ns, epsilon_total=50.0, delta_total=1.0)
        api = DPApi(namespace=ns)
        api.rng.seed(42)

        # 1000 行，但只有 3 个非零值（全零数据包含 997 个零）
        dense_data = [0.0] * 997 + [10.0, 20.0, 30.0]
        sparse_arr = sp.csr_matrix(dense_data).T  # 1000x1 矩阵

        res_details = api.mean(
            sparse_arr,
            epsilon=2.0,
            clip_lower=0.0,
            clip_upper=100.0,
            min_count=1.0,
            return_details=True,
        )
        assert isinstance(res_details, DPResult)
        # 确认 mean noise_scale > 0 且置信区间非退化
        assert res_details.noise_scale > 0.0
        assert res_details.confidence_interval[0] < res_details.value < res_details.confidence_interval[1]

    def test_gaussian_confidence_interval_arbitrary_level(self):
        from privacy_local_agent.privacy.dp import compute_confidence_interval

        # 测试 Gaussian 机制在任意置信水平 (如 0.90 和 0.99) 时的严格 CI 单调增加
        low90, high90 = compute_confidence_interval(
            10.0, noise_scale=2.0, mechanism="gaussian", confidence_level=0.90
        )
        low99, high99 = compute_confidence_interval(
            10.0, noise_scale=2.0, mechanism="gaussian", confidence_level=0.99
        )
        assert (high99 - low99) > (high90 - low90)


class TestAdvancedDPFeatures:
    def test_dp_aggregate_dataframe(self):
        pd = pytest.importorskip("pandas")
        ns = "test-dp-aggregate"
        BudgetAccountant(ns, epsilon_total=20.0, delta_total=1.0)
        api = DPApi(namespace=ns)

        df = pd.DataFrame({
            "age": [20, 30, 40, 50],
            "salary": [1000.0, 2000.0, 3000.0, 4000.0],
            "dept": ["eng", "hr", "eng", "sales"]
        })

        specs = {
            "age": ("mean", {"clip_lower": 0, "clip_upper": 100}),
            "salary": ("sum", {"clip_lower": 0, "clip_upper": 10000}),
            "dept": ("histogram", {"categories": ["eng", "hr", "sales"]}),
        }

        res = api.dp_aggregate(df, specs, epsilon=3.0)
        assert "age" in res and "salary" in res and "dept" in res

    def test_adaptive_clip(self):
        ns = "test-adaptive-clip"
        api = DPApi(namespace=ns)
        lower, upper = api.adaptive_clip([1.0, 5.0, 10.0, 15.0, 20.0], epsilon=1.0, initial_clip=10.0)
        assert lower == 0.0
        assert upper > 0.0

    def test_accumulator_serialize_merge_finalize(self):
        from privacy_local_agent.privacy.dp import Accumulator

        ns = "test-accumulator"
        BudgetAccountant(ns, epsilon_total=20.0, delta_total=1.0)
        api = DPApi(namespace=ns)
        api.rng.seed(42)

        acc1 = api.create_accumulator([1.0, 2.0], clip_lower=0.0, clip_upper=10.0)
        acc2 = api.create_accumulator([3.0, 4.0], clip_lower=0.0, clip_upper=10.0)

        # 序列化/反序列化测试
        serialized = acc2.serialize()
        acc2_deser = Accumulator.deserialize(serialized)

        merged = acc1 + acc2_deser
        assert merged.count == 4.0
        assert merged.sum == 10.0

        final_sum = api.finalize_dp(merged, aggregation="sum", epsilon=1.0)
        assert isinstance(final_sum, float)

    def test_vector_sum_dpsgd(self):
        np = pytest.importorskip("numpy")
        ns = "test-vector-sum"
        BudgetAccountant(ns, epsilon_total=20.0, delta_total=1.0)
        api = DPApi(namespace=ns)

        grads = np.array([
            [1.0, 2.0, 3.0],
            [4.0, 5.0, 6.0]
        ])
        noisy_grads = api.vector_sum(grads, max_norm=5.0, epsilon=1.0, delta=1e-4)
        assert noisy_grads.shape == (3,)

    def test_rdp_accountant(self):
        from privacy_local_agent.privacy.budget import RDPAccountant

        rdp = RDPAccountant(target_delta=1e-5)
        # 记录 10 次高斯查询
        for _ in range(10):
            rdp.record_gaussian(sigma=2.0, sensitivity=1.0)

        eps = rdp.get_epsilon()
        assert 0.0 < eps < 50.0

    def test_dp_groupby(self):
        pd = pytest.importorskip("pandas")
        ns = "test-dp-groupby"
        BudgetAccountant(ns, epsilon_total=100.0, delta_total=1.0)
        api = DPApi(namespace=ns)

        df = pd.DataFrame({
            "city": ["Beijing"] * 50 + ["Shanghai"] * 40 + ["TinyVillage"] * 1,
            "income": [100.0] * 91,
        })

        res = api.dp_groupby(df, group_col="city", target_col="income", agg="count", epsilon=2.0, delta=1e-3)
        assert "Beijing" in res
        assert "TinyVillage" not in res

    def test_user_level_dp_bounding(self):
        np = pytest.importorskip("numpy")
        ns = "test-user-level-dp"
        BudgetAccountant(ns, epsilon_total=20.0, delta_total=1.0)
        api = DPApi(namespace=ns)

        # 某用户 user_A 在日志中产生了 100 条数据，下采样绑定为最多 2 条
        vals = np.array([10.0] * 100)
        uids = ["user_A"] * 100
        res = api.count(vals, epsilon=1.0, user_ids=uids, max_contributions=2)
        assert res >= 0.0

    def test_discrete_laplace(self):
        ns = "test-discrete-laplace"
        api = DPApi(namespace=ns)
        res = api.count([1.0, 2.0, 3.0], epsilon=1.0, discrete=True)
        assert isinstance(res, (int, float))

    def test_dp_result_to_arrow(self):
        pa = pytest.importorskip("pyarrow")
        ns = "test-arrow-metadata"
        api = DPApi(namespace=ns)
        res_details = api.count([1.0, 2.0, 3.0], epsilon=1.0, return_details=True)

        arrow_table = res_details.to_arrow()
        assert isinstance(arrow_table, pa.Table)
        assert b"dp_metadata" in arrow_table.schema.metadata

    def test_hmac_budget_audit(self):
        import os
        from privacy_local_agent.privacy.budget import BudgetAuditLogger

        audit_path = "/tmp/test_budget_audit.log"
        if os.path.exists(audit_path):
            os.remove(audit_path)

        logger = BudgetAuditLogger(log_file=audit_path)
        sig = logger.log_spend("test_ns", 1.0, 0.0, 1.0, 0.0)
        assert len(sig) == 64
        assert os.path.exists(audit_path)
        if os.path.exists(audit_path):
            os.remove(audit_path)



