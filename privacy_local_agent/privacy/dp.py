"""差分隐私（Differential Privacy, DP）API 实现。

支持拉普拉斯（Laplace）机制与高斯（Gaussian）机制下的 count、sum、mean 聚合。
每次调用都会先向 BudgetAccountant 申请消耗 (epsilon, delta) 预算，再对真实结果
注入符合差分隐私要求的 calibrated 噪声。

支持显式 clip_lower / clip_upper，确保 sum/mean 的敏感度在 seeing data 之前即
已确定，满足差分隐私的形式化要求。

扩展能力：
- 向量化 clip：优先使用 NumPy 加速大规模数据裁剪。
- Noisify 接口：对已由外部引擎聚合好的中间结果直接加噪。
- Chunked 接口：支持分块流式聚合，避免一次性加载全部数据到内存。
"""

from __future__ import annotations

import math
import random
import secrets
import warnings
from typing import Any, Dict, Iterable, List, Optional, Sequence

from ..observability.metrics import DP_QUERIES_TOTAL
from .budget import BudgetAccountant, PrivacyBudgetExhausted
from .data_adapters import extract_chunks, extract_values


def calibrate_analytic_gaussian(epsilon: float, delta: float, sensitivity: float, tol: float = 1e-12) -> float:
    """使用 Balle & Wang (ICML'18) 提出的解析高斯机制计算噪声的标准差 sigma。

    在任意 epsilon 和 delta > 0 下计算出理论最小的噪声方差，比经典高斯机制的界更紧。
    """
    if sensitivity == 0.0:
        return 0.0

    def Phi(t: float) -> float:
        return 0.5 * (1.0 + math.erf(float(t) / math.sqrt(2.0)))

    def caseA(eps: float, s: float) -> float:
        return Phi(math.sqrt(eps * s)) - math.exp(eps) * Phi(-math.sqrt(eps * (s + 2.0)))

    def caseB(eps: float, s: float) -> float:
        return Phi(-math.sqrt(eps * s)) - math.exp(eps) * Phi(-math.sqrt(eps * (s + 2.0)))

    def doubling_trick(predicate_stop, s_inf: float, s_sup: float) -> tuple[float, float]:
        while not predicate_stop(s_sup):
            s_inf = s_sup
            s_sup = 2.0 * s_inf
        return s_inf, s_sup

    def binary_search(predicate_stop, predicate_left, s_inf: float, s_sup: float) -> float:
        s_mid = s_inf + (s_sup - s_inf) / 2.0
        while not predicate_stop(s_mid):
            if predicate_left(s_mid):
                s_sup = s_mid
            else:
                s_inf = s_mid
            s_mid = s_inf + (s_sup - s_inf) / 2.0
        return s_mid

    delta_thr = caseA(epsilon, 0.0)
    if delta == delta_thr:
        alpha = 1.0
    else:
        if delta > delta_thr:
            predicate_stop_DT = lambda s: caseA(epsilon, s) >= delta
            function_s_to_delta = lambda s: caseA(epsilon, s)
            predicate_left_BS = lambda s: function_s_to_delta(s) > delta
        else:
            predicate_stop_DT = lambda s: caseB(epsilon, s) <= delta
            function_s_to_delta = lambda s: caseB(epsilon, s)
            predicate_left_BS = lambda s: function_s_to_delta(s) < delta

        predicate_stop_BS = lambda s: abs(function_s_to_delta(s) - delta) <= tol

        s_inf, s_sup = doubling_trick(predicate_stop_DT, 0.0, 1.0)
        s_final = binary_search(predicate_stop_BS, predicate_left_BS, s_inf, s_sup)

        if delta > delta_thr:
            alpha = math.sqrt(1.0 + s_final / 2.0) - math.sqrt(s_final / 2.0)
        else:
            alpha = math.sqrt(1.0 + s_final / 2.0) + math.sqrt(s_final / 2.0)

    return alpha * sensitivity / math.sqrt(2.0 * epsilon)


class SecureRandom(random.Random):
    """一个安全的随机数生成器。

    在生产环境中默认使用 secrets.SystemRandom() 以确保密码学安全（不可预测）；
    在测试或显式调用 seed(x) 时，切换到基类 random.Random 的确定性伪随机数生成，
    保证单元测试的可复现性与确定性边界校验。
    """

    def __init__(self):
        super().__init__()
        self._system_rng = secrets.SystemRandom()
        self._seeded = False

    def seed(self, a=None, version=2):
        if a is not None:
            super().seed(a, version=version)
            self._seeded = True
        else:
            self._seeded = False

    def random(self) -> float:
        if self._seeded:
            return super().random()
        return self._system_rng.random()

    def gauss(self, mu: float, sigma: float) -> float:
        if self._seeded:
            return super().gauss(mu, sigma)
        return self._system_rng.gauss(mu, sigma)


class DPApi:
    """差分隐私计算接口。

    封装了 Laplace/Gaussian 采样与预算扣减逻辑，提供 count/sum/mean 三种聚合方法。

    Attributes:
        budget: 当前命名空间对应的 BudgetAccountant 实例。
        rng: 私有随机数生成器，用于噪声采样。
    """

    def __init__(self, namespace: str = "default", random_state: Optional[int] = None):
        """初始化 DPApi。

        Args:
            namespace: 命名空间，用于关联隐私预算账户。
            random_state: 可选随机种子，用于可复现测试。与旧参数 ``seed`` 等价。
        """
        self.budget = BudgetAccountant(namespace)
        self.rng = SecureRandom()
        if random_state is not None:
            self.rng.seed(random_state)

    @classmethod
    def from_seed(cls, namespace: str = "default", seed: Optional[int] = None):
        """兼容旧构造方式：通过 seed 创建 DPApi 实例。"""
        return cls(namespace=namespace, random_state=seed)

    def _sample_laplace(self, scale: float) -> float:
        """从拉普拉斯分布 Laplace(0, scale) 中采样一个随机值。

        使用逆变换采样（inverse transform sampling）。
        """
        u = self.rng.random() - 0.5
        sign = -1.0 if u < 0 else 1.0
        return -scale * sign * math.log(1 - 2 * abs(u))

    def _sample_gaussian(self, sigma: float) -> float:
        """从高斯分布 N(0, sigma^2) 中采样一个随机值。"""
        return self.rng.gauss(0.0, sigma)

    @staticmethod
    def _clip_values(values: List[float], lower: float, upper: float) -> List[float]:
        """对输入值做截断，将其限制在 [lower, upper] 区间内。

        优先使用 NumPy 向量化 ``np.clip`` 以提升大规模数据处理效率；
        当 numpy 不可用或转换失败时回退到纯 Python 列表推导。
        """
        try:
            import numpy as np

            arr = np.asarray(values, dtype=float)
            # np.clip 对 NaN/inf 的行为：NaN 保持 NaN，inf 被截断到边界
            clipped = np.clip(arr, lower, upper)
            return clipped.tolist()
        except Exception:
            return [min(upper, max(lower, float(v))) for v in values]

    def _resolve_clip_bounds(
        self,
        values: List[float],
        clip_lower: Optional[float],
        clip_upper: Optional[float],
        mechanism: str,
    ) -> tuple[float, float]:
        """解析并返回 clip 上下界。

        Gaussian 机制必须提供显式 clip 区间；Laplace 在未提供时允许使用数据推断
        的区间作为向后兼容，但会发出警告。
        """
        if clip_lower is not None and clip_upper is not None:
            if clip_lower > clip_upper:
                raise ValueError("clip_lower must be <= clip_upper")
            return float(clip_lower), float(clip_upper)

        if mechanism == "gaussian":
            raise ValueError(
                "clip_lower and clip_upper are required for Gaussian mechanism"
            )

        # Laplace 向后兼容：未提供 clip 时，从数据推断 [min, max] 作为敏感度估计。
        # 注意：严格差分隐私要求 clip 必须独立于数据集，因此生产环境应显式配置。
        if values:
            lower = min(values)
            upper = max(values)
        else:
            lower, upper = 0.0, 0.0
        warnings.warn(
            "clip_lower/clip_upper not provided; inferring bounds from data. "
            "This is not recommended for production.",
            stacklevel=3,
        )
        return lower, upper

    def _validate_mechanism(self, mechanism: str, delta: float) -> str:
        """校验 mechanism 与 delta 参数，返回规范化后的 mechanism 名称。"""
        mechanism = mechanism.lower()
        if mechanism not in ("laplace", "gaussian"):
            raise ValueError("mechanism must be 'laplace' or 'gaussian'")
        if mechanism == "gaussian" and delta <= 0:
            raise ValueError("delta must be positive for Gaussian mechanism")
        return mechanism

    def _sample_count_noise(self, epsilon: float, delta: float, mechanism: str) -> float:
        """采样 count / histogram bin 所需的 DP 噪声。

        count 与直方图分桶的 L1 敏感度为 1（Laplace）或 L2 敏感度为 1（Gaussian）。
        """
        if mechanism == "laplace":
            return self._sample_laplace(1.0 / epsilon)
        # count L2 sensitivity = 1，使用更紧的解析高斯机制
        sigma = calibrate_analytic_gaussian(epsilon, delta, 1.0)
        return self._sample_gaussian(sigma)

    def _sample_sum_noise(
        self, sensitivity: float, epsilon: float, delta: float, mechanism: str
    ) -> float:
        """采样 sum 所需的 DP 噪声。

        sum 的敏感度由调用方提供的 sensitivity 决定。
        """
        if mechanism == "laplace":
            scale = sensitivity / epsilon if epsilon > 0 else 0.0
            return self._sample_laplace(scale)
        if sensitivity == 0:
            return 0.0
        sigma = calibrate_analytic_gaussian(epsilon, delta, sensitivity)
        return self._sample_gaussian(sigma)

    def count(
        self,
        values: Any,
        epsilon: float,
        delta: float = 0.0,
        mechanism: str = "laplace",
        column: Optional[str] = None,
        party: Optional[str] = None,
    ) -> float:
        """差分隐私计数。

        Args:
            values: 输入数值，支持 list/tuple/ndarray/Series/DataFrame/SecretFlow 格式。
            epsilon: 隐私预算参数。
            delta: 隐私预算 delta 参数；Gaussian 机制必须大于 0。
            mechanism: "laplace" 或 "gaussian"。
            column: 当 values 为 DataFrame 等表格类型时，指定目标列名。
            party: 当 values 为 SecretFlow HDataFrame 时，指定参与方。

        Returns:
            带噪声的计数值，经 max(0, ...) 截断保证非负。
        """
        mechanism = self._validate_mechanism(mechanism, delta)
        values = extract_values(values, column=column, party=party)
        self.budget.spend(epsilon, delta)
        true_count = sum(1 for v in values if v)
        DP_QUERIES_TOTAL.labels(mechanism=mechanism, aggregation="count").inc()
        noise = self._sample_count_noise(epsilon, delta, mechanism)
        return max(0.0, true_count + noise)

    def sum(
        self,
        values: Any,
        epsilon: float,
        delta: float = 0.0,
        mechanism: str = "laplace",
        clip_lower: Optional[float] = None,
        clip_upper: Optional[float] = None,
        column: Optional[str] = None,
        party: Optional[str] = None,
    ) -> float:
        """差分隐私求和。

        Args:
            values: 输入数值，支持 list/tuple/ndarray/Series/DataFrame/SecretFlow 格式。
            epsilon: 隐私预算参数。
            delta: 隐私预算 delta 参数。
            mechanism: "laplace" 或 "gaussian"。
            clip_lower: 截断下界；Gaussian 必须提供。
            clip_upper: 截断上界；Gaussian 必须提供。
            column: 当 values 为表格类型时，指定目标列名。
            party: 当 values 为 SecretFlow HDataFrame 时，指定参与方。

        Returns:
            带噪声的求和结果。
        """
        mechanism = self._validate_mechanism(mechanism, delta)

        values = extract_values(values, column=column, party=party)
        lower, upper = self._resolve_clip_bounds(
            values, clip_lower, clip_upper, mechanism
        )
        clipped = self._clip_values(values, lower, upper)
        true_sum = sum(clipped)
        sensitivity = max(0.0, upper - lower)

        self.budget.spend(epsilon, delta)
        DP_QUERIES_TOTAL.labels(mechanism=mechanism, aggregation="sum").inc()
        noise = self._sample_sum_noise(sensitivity, epsilon, delta, mechanism)
        return true_sum + noise

    def mean(
        self,
        values: Any,
        epsilon: float,
        delta: float = 0.0,
        mechanism: str = "laplace",
        clip_lower: Optional[float] = None,
        clip_upper: Optional[float] = None,
        min_count: float = 5.0,
        column: Optional[str] = None,
        party: Optional[str] = None,
    ) -> float:
        """差分隐私均值。

        使用组合定理：将 (epsilon, delta) 拆分为两份，分别用于 count 与 sum，
        最后用 noisy_sum / noisy_count 得到差分隐私均值。
        为了防除以接近零的值导致结果发散，当 noisy_count < min_count 时返回 0.0。
        """
        values = extract_values(values, column=column, party=party)
        if not values:
            return 0.0

        mechanism = self._validate_mechanism(mechanism, delta)

        # 组合定理：总预算 = epsilon/2 + epsilon/2；delta/2 + delta/2
        noisy_count = self.count(
            [1.0] * len(values), epsilon / 2.0, delta / 2.0, mechanism
        )
        if noisy_count < min_count or noisy_count <= 0.0:
            return 0.0

        noisy_sum = self.sum(
            values, epsilon / 2.0, delta / 2.0, mechanism, clip_lower, clip_upper
        )
        DP_QUERIES_TOTAL.labels(mechanism=mechanism, aggregation="mean").inc()
        return noisy_sum / noisy_count

    def histogram(
        self,
        values: Any,
        categories: Sequence[Any],
        epsilon: float,
        delta: float = 0.0,
        mechanism: str = "laplace",
        column: Optional[str] = None,
        party: Optional[str] = None,
    ) -> Dict[Any, float]:
        """差分隐私直方图计数（使用联合敏感度为 1）。

        对于互斥分类，每个个体最多贡献到一个分桶。因此，整个直方图的 L1 / L2 敏感度均为 1。
        只需消耗一次 (epsilon, delta) 预算即可对所有类别并行加噪，大幅提升多分类查询效用。
        """
        mechanism = self._validate_mechanism(mechanism, delta)
        values = extract_values(values, column=column, party=party)

        self.budget.spend(epsilon, delta)
        DP_QUERIES_TOTAL.labels(mechanism=mechanism, aggregation="histogram").inc()

        # 计算真实计数
        counts = {c: 0.0 for c in categories}
        for v in values:
            if v in counts:
                counts[v] += 1.0

        # 由于联合敏感度为 1，直接对每个 Bin 加噪
        for c in counts:
            noise = self._sample_count_noise(epsilon, delta, mechanism)
            counts[c] = max(0.0, counts[c] + noise)

        return counts

    def noisy_count(
        self,
        true_count: float,
        epsilon: float,
        delta: float = 0.0,
        mechanism: str = "laplace",
    ) -> float:
        """对已经聚合好的计数结果直接注入 DP 噪声。

        适用于数据引擎（Spark/SQL/DuckDB 等）已在数据源侧完成 count，
        仅需 sidecar 完成最终加噪与预算扣减的场景。

        Args:
            true_count: 真实计数值。
            epsilon: 隐私预算参数。
            delta: 隐私预算 delta 参数；Gaussian 机制必须大于 0。
            mechanism: "laplace" 或 "gaussian"。

        Returns:
            带噪声的计数值，经 max(0, ...) 截断保证非负。
        """
        mechanism = self._validate_mechanism(mechanism, delta)
        self.budget.spend(epsilon, delta)
        DP_QUERIES_TOTAL.labels(mechanism=mechanism, aggregation="count").inc()
        noise = self._sample_count_noise(epsilon, delta, mechanism)
        return max(0.0, float(true_count) + noise)

    def noisy_sum(
        self,
        true_sum: float,
        sensitivity: float,
        epsilon: float,
        delta: float = 0.0,
        mechanism: str = "laplace",
    ) -> float:
        """对已经聚合好的求和结果直接注入 DP 噪声。

        调用方必须提供已知的敏感度（通常为 clip_upper - clip_lower），
        因为本方法不再接触原始数据，无法自行推断边界。

        Args:
            true_sum: 真实求和值（已由外部引擎完成 clip 后聚合）。
            sensitivity: 求和的 L1/L2 敏感度。
            epsilon: 隐私预算参数。
            delta: 隐私预算 delta 参数。
            mechanism: "laplace" 或 "gaussian"。

        Returns:
            带噪声的求和结果。
        """
        mechanism = self._validate_mechanism(mechanism, delta)
        if sensitivity < 0:
            raise ValueError("sensitivity must be non-negative")
        self.budget.spend(epsilon, delta)
        DP_QUERIES_TOTAL.labels(mechanism=mechanism, aggregation="sum").inc()
        noise = self._sample_sum_noise(sensitivity, epsilon, delta, mechanism)
        return float(true_sum) + noise

    def noisy_mean(
        self,
        true_sum: float,
        true_count: float,
        sensitivity: float,
        epsilon: float,
        delta: float = 0.0,
        mechanism: str = "laplace",
        min_count: float = 5.0,
    ) -> float:
        """对已经聚合好的 sum/count 分别注入 DP 噪声后得到均值。

        使用组合定理：将 (epsilon, delta) 拆分为两份，分别用于 count 与 sum。
        当 noisy_count < min_count 时返回 0.0，防止除以接近零的值导致结果发散。
        """
        mechanism = self._validate_mechanism(mechanism, delta)
        if sensitivity < 0:
            raise ValueError("sensitivity must be non-negative")
        noisy_count = self.noisy_count(
            true_count, epsilon / 2.0, delta / 2.0, mechanism
        )
        if noisy_count < min_count or noisy_count <= 0.0:
            return 0.0
        noisy_sum = self.noisy_sum(
            true_sum, sensitivity, epsilon / 2.0, delta / 2.0, mechanism
        )
        DP_QUERIES_TOTAL.labels(mechanism=mechanism, aggregation="mean").inc()
        return noisy_sum / noisy_count

    def noisy_histogram(
        self,
        true_counts: Dict[Any, float],
        epsilon: float,
        delta: float = 0.0,
        mechanism: str = "laplace",
    ) -> Dict[Any, float]:
        """对已经聚合好的直方图计数直接注入 DP 噪声。

        适用于外部引擎已完成分桶计数，仅需 sidecar 加噪的场景。
        联合敏感度为 1，因此所有分桶共享一次 (epsilon, delta) 预算。

        Args:
            true_counts: 分桶名到真实计数的字典。
            epsilon: 隐私预算参数。
            delta: 隐私预算 delta 参数。
            mechanism: "laplace" 或 "gaussian"。

        Returns:
            分桶名到带噪计数的字典。
        """
        mechanism = self._validate_mechanism(mechanism, delta)
        self.budget.spend(epsilon, delta)
        DP_QUERIES_TOTAL.labels(mechanism=mechanism, aggregation="histogram").inc()

        result = {c: float(true_counts.get(c, 0.0)) for c in true_counts}
        for c in result:
            noise = self._sample_count_noise(epsilon, delta, mechanism)
            result[c] = max(0.0, result[c] + noise)
        return result

    def chunked_count(
        self,
        chunks: Iterable[Any],
        epsilon: float,
        delta: float = 0.0,
        mechanism: str = "laplace",
        column: Optional[str] = None,
        party: Optional[str] = None,
    ) -> float:
        """分块流式差分隐私计数。

        允许调用方以多个 chunk（生成器/迭代器）分批传入数据，
        sidecar 增量聚合真实计数后只加一次噪声、只消耗一次 (epsilon, delta) 预算。
        适用于上亿级数据无法一次性加载内存的场景。

        Args:
            chunks: 数据块的可迭代对象，每块支持 list/tuple/ndarray/Series/DataFrame/SecretFlow 格式。
            epsilon: 隐私预算参数。
            delta: 隐私预算 delta 参数。
            mechanism: "laplace" 或 "gaussian"。
            column: 当 chunk 为表格类型时，指定目标列名。
            party: 当 chunk 为 SecretFlow HDataFrame 时，指定参与方。

        Returns:
            带噪声的计数值。
        """
        mechanism = self._validate_mechanism(mechanism, delta)
        chunks = extract_chunks(chunks, column=column, party=party)
        true_count = 0.0
        for chunk in chunks:
            true_count += sum(1 for v in chunk if v)
        self.budget.spend(epsilon, delta)
        DP_QUERIES_TOTAL.labels(mechanism=mechanism, aggregation="count").inc()
        noise = self._sample_count_noise(epsilon, delta, mechanism)
        return max(0.0, true_count + noise)

    def chunked_sum(
        self,
        chunks: Iterable[Any],
        epsilon: float,
        delta: float = 0.0,
        mechanism: str = "laplace",
        clip_lower: Optional[float] = None,
        clip_upper: Optional[float] = None,
        column: Optional[str] = None,
        party: Optional[str] = None,
    ) -> float:
        """分块流式差分隐私求和。

        调用方必须显式提供 clip 边界；本方法按块 clip 并累加局部和，
        最终只消耗一次 (epsilon, delta) 预算并注入噪声。

        Args:
            chunks: 数据块的可迭代对象，每块支持多种数据格式。
            epsilon: 隐私预算参数。
            delta: 隐私预算 delta 参数。
            mechanism: "laplace" 或 "gaussian"。
            clip_lower: 截断下界（必须显式提供）。
            clip_upper: 截断上界（必须显式提供）。
            column: 当 chunk 为表格类型时，指定目标列名。
            party: 当 chunk 为 SecretFlow HDataFrame 时，指定参与方。

        Returns:
            带噪声的求和结果。
        """
        mechanism = self._validate_mechanism(mechanism, delta)
        if clip_lower is None or clip_upper is None:
            raise ValueError(
                "chunked_sum requires explicit clip_lower and clip_upper"
            )
        if clip_lower > clip_upper:
            raise ValueError("clip_lower must be <= clip_upper")
        lower, upper = float(clip_lower), float(clip_upper)
        sensitivity = max(0.0, upper - lower)

        chunks = extract_chunks(chunks, column=column, party=party)
        true_sum = 0.0
        for chunk in chunks:
            clipped = self._clip_values(chunk, lower, upper)
            true_sum += sum(clipped)

        self.budget.spend(epsilon, delta)
        DP_QUERIES_TOTAL.labels(mechanism=mechanism, aggregation="sum").inc()
        noise = self._sample_sum_noise(sensitivity, epsilon, delta, mechanism)
        return true_sum + noise

    def chunked_mean(
        self,
        chunks: Iterable[Any],
        epsilon: float,
        delta: float = 0.0,
        mechanism: str = "laplace",
        clip_lower: Optional[float] = None,
        clip_upper: Optional[float] = None,
        min_count: float = 5.0,
        column: Optional[str] = None,
        party: Optional[str] = None,
    ) -> float:
        """分块流式差分隐私均值。

        使用组合定理：将 (epsilon, delta) 拆分为两份，分别用于 count 与 sum。
        每个 chunk 只被遍历一次，内存占用与总数据量解耦。
        """
        mechanism = self._validate_mechanism(mechanism, delta)
        if clip_lower is None or clip_upper is None:
            raise ValueError(
                "chunked_mean requires explicit clip_lower and clip_upper"
            )
        if clip_lower > clip_upper:
            raise ValueError("clip_lower must be <= clip_upper")
        lower, upper = float(clip_lower), float(clip_upper)
        sensitivity = max(0.0, upper - lower)

        true_count = 0.0
        true_sum = 0.0
        for chunk in chunks:
            chunk_list = list(chunk)
            true_count += len(chunk_list)
            clipped = self._clip_values(chunk_list, lower, upper)
            true_sum += sum(clipped)

        noisy_count = self.noisy_count(
            true_count, epsilon / 2.0, delta / 2.0, mechanism
        )
        if noisy_count < min_count or noisy_count <= 0.0:
            return 0.0
        noisy_sum = self.noisy_sum(
            true_sum, sensitivity, epsilon / 2.0, delta / 2.0, mechanism
        )
        DP_QUERIES_TOTAL.labels(mechanism=mechanism, aggregation="mean").inc()
        return noisy_sum / noisy_count

    def chunked_histogram(
        self,
        chunks: Iterable[Any],
        categories: Sequence[Any],
        epsilon: float,
        delta: float = 0.0,
        mechanism: str = "laplace",
        column: Optional[str] = None,
        party: Optional[str] = None,
    ) -> Dict[Any, float]:
        """分块流式差分隐私直方图计数。

        按块统计各分类计数并合并，最终对所有分桶加噪，只消耗一次预算。
        """
        mechanism = self._validate_mechanism(mechanism, delta)
        counts = {c: 0.0 for c in categories}
        for chunk in chunks:
            for v in chunk:
                if v in counts:
                    counts[v] += 1.0

        self.budget.spend(epsilon, delta)
        DP_QUERIES_TOTAL.labels(mechanism=mechanism, aggregation="histogram").inc()
        for c in counts:
            noise = self._sample_count_noise(epsilon, delta, mechanism)
            counts[c] = max(0.0, counts[c] + noise)
        return counts


class LocalDPApi:
    """本地差分隐私计算接口。

    提供二值/类别型随机响应与本地直方图估计。每个实例可绑定一个随机数生成器，
    便于测试复现。

    Attributes:
        rng: 私有随机数生成器，用于随机响应采样。
    """

    def __init__(self, seed: Optional[int] = None):
        """初始化 LocalDPApi。

        Args:
            seed: 可选随机种子，用于测试复现。
        """
        self.rng = random.Random(seed)

    @staticmethod
    def _validate_epsilon(epsilon: float) -> None:
        """校验 epsilon 为正数。"""
        if epsilon <= 0:
            raise ValueError("epsilon must be positive")

    def perturb_binary(self, value: int, epsilon: float) -> int:
        """对单个二值数据进行 ε-本地差分隐私扰动。

        使用经典 Warner 随机响应：以概率 p = e^ε / (1 + e^ε) 保持原值，
        以概率 1 - p 翻转。

        Args:
            value: 输入值，必须为 0 或 1。
            epsilon: 本地隐私预算，必须 > 0。

        Returns:
            扰动后的 0 或 1。
        """
        self._validate_epsilon(epsilon)
        if value not in (0, 1):
            raise ValueError("binary value must be 0 or 1")

        p = math.exp(epsilon) / (1.0 + math.exp(epsilon))
        return value if self.rng.random() < p else 1 - value

    def perturb_binary_batch(self, values: Sequence[int], epsilon: float) -> List[int]:
        """批量对二值数据进行本地 DP 扰动。"""
        return [self.perturb_binary(int(v), epsilon) for v in values]

    def perturb_categorical(
        self, value: Any, categories: Sequence[Any], epsilon: float
    ) -> Any:
        """对单个类别型数据进行 ε-本地差分隐私扰动。

        使用 k-ary 随机响应：设 k = len(categories)，以概率 p = e^ε / (k - 1 + e^ε)
        保持原值，以均匀概率 (1 - p) / (k - 1) 返回其他每个类别。

        Args:
            value: 输入类别，必须属于 categories。
            categories: 所有可能的类别列表。
            epsilon: 本地隐私预算，必须 > 0。

        Returns:
            扰动后的类别值。
        """
        self._validate_epsilon(epsilon)
        if value not in categories:
            raise ValueError("value must be one of the provided categories")

        k = len(categories)
        if k < 2:
            raise ValueError("categories must contain at least 2 items")

        p = math.exp(epsilon) / (k - 1 + math.exp(epsilon))
        if self.rng.random() < p:
            return value

        # 从其他类别中均匀选择
        others = [c for c in categories if c != value]
        return self.rng.choice(others)

    def perturb_categorical_batch(
        self, values: Sequence[Any], categories: Sequence[Any], epsilon: float
    ) -> List[Any]:
        """批量对类别型数据进行本地 DP 扰动。"""
        return [self.perturb_categorical(v, categories, epsilon) for v in values]

    def estimate_binary_frequency(
        self, reported_values: Sequence[int], epsilon: float
    ) -> float:
        """根据扰动后的二值样本估计真实比例为 1 的频率。

        纠偏公式：
            p = e^ε / (1 + e^ε)
            hat_f = (f_reported - (1 - p)) / (2p - 1)

        其中 f_reported = count_1 / n。

        Args:
            reported_values: 扰动后的二值样本列表。
            epsilon: 扰动时使用的本地隐私预算。

        Returns:
            估计的真实频率（0~1 之间），超出范围时自动截断。
        """
        self._validate_epsilon(epsilon)
        n = len(reported_values)
        if n == 0:
            return 0.0

        p = math.exp(epsilon) / (1.0 + math.exp(epsilon))
        f_reported = sum(1 for v in reported_values if v == 1) / n
        est = (f_reported - (1.0 - p)) / (2.0 * p - 1.0)
        return float(max(0.0, min(1.0, est)))

    def estimate_categorical_histogram(
        self,
        reported_values: Sequence[Any],
        categories: Sequence[Any],
        epsilon: float,
    ) -> Dict[Any, float]:
        """根据扰动后的类别样本估计各类别的真实频率。

        纠偏公式（对类别 j）：
            p = e^ε / (k - 1 + e^ε)
            q = (1 - p) / (k - 1)
            hat_f_j = (count_j - n * q) / (p - q)

        Args:
            reported_values: 扰动后的类别样本列表。
            categories: 所有可能的类别列表。
            epsilon: 扰动时使用的本地隐私预算。

        Returns:
            每个类别的估计频率字典，频率自动归一化到和为 1。
        """
        self._validate_epsilon(epsilon)
        n = len(reported_values)
        k = len(categories)
        if k < 2:
            raise ValueError("categories must contain at least 2 items")

        p = math.exp(epsilon) / (k - 1 + math.exp(epsilon))
        q = (1.0 - p) / (k - 1)
        denominator = p - q

        counts: Dict[Any, int] = {c: 0 for c in categories}
        for v in reported_values:
            if v in counts:
                counts[v] += 1

        estimates: Dict[Any, float] = {}
        for c in categories:
            f_reported = counts[c] / n if n > 0 else 0.0
            est = (f_reported - q) / denominator if denominator != 0 else 1.0 / k
            estimates[c] = max(0.0, est)

        # 归一化到和为 1
        total = sum(estimates.values())
        if total > 0:
            estimates = {c: v / total for c, v in estimates.items()}
        else:
            estimates = {c: 1.0 / k for c in categories}

        return estimates
