"""差分隐私（Differential Privacy, DP）API 实现。

支持拉普拉斯（Laplace）机制与高斯（Gaussian）机制下的 count、sum、mean 聚合。
每次调用都会先向 BudgetAccountant 申请消耗 (epsilon, delta) 预算，再对真实结果
注入符合差分隐私要求的 calibrated 噪声。

支持显式 clip_lower / clip_upper，确保 sum/mean 的敏感度在 seeing data 之前即
已确定，满足差分隐私的形式化要求。
"""

from __future__ import annotations

import math
import random
import warnings
from typing import List, Optional

from ..observability.metrics import DP_QUERIES_TOTAL
from .budget import BudgetAccountant, PrivacyBudgetExhausted


class DPApi:
    """差分隐私计算接口。

    封装了 Laplace/Gaussian 采样与预算扣减逻辑，提供 count/sum/mean 三种聚合方法。

    Attributes:
        budget: 当前命名空间对应的 BudgetAccountant 实例。
        rng: 私有随机数生成器，用于噪声采样。
    """

    def __init__(self, namespace: str = "default"):
        """初始化 DPApi。

        Args:
            namespace: 命名空间，用于关联隐私预算账户。
        """
        self.budget = BudgetAccountant(namespace)
        self.rng = random.Random()

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
        """对输入值做截断，将其限制在 [lower, upper] 区间内。"""
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

    def count(
        self,
        values: List[float],
        epsilon: float,
        delta: float = 0.0,
        mechanism: str = "laplace",
    ) -> float:
        """差分隐私计数。

        Args:
            values: 输入数值列表。
            epsilon: 隐私预算参数。
            delta: 隐私预算 delta 参数；Gaussian 机制必须大于 0。
            mechanism: "laplace" 或 "gaussian"。

        Returns:
            带噪声的计数值，经 max(0, ...) 截断保证非负。
        """
        mechanism = mechanism.lower()
        if mechanism not in ("laplace", "gaussian"):
            raise ValueError("mechanism must be 'laplace' or 'gaussian'")
        if mechanism == "gaussian" and delta <= 0:
            raise ValueError("delta must be positive for Gaussian mechanism")

        self.budget.spend(epsilon, delta)
        true_count = sum(1 for v in values if v)
        DP_QUERIES_TOTAL.labels(mechanism=mechanism, aggregation="count").inc()

        if mechanism == "laplace":
            noise = self._sample_laplace(1.0 / epsilon)
        else:
            # count L2 sensitivity = 1
            sigma = math.sqrt(2.0 * math.log(1.25 / delta)) * 1.0 / epsilon
            noise = self._sample_gaussian(sigma)
        return max(0.0, true_count + noise)

    def sum(
        self,
        values: List[float],
        epsilon: float,
        delta: float = 0.0,
        mechanism: str = "laplace",
        clip_lower: Optional[float] = None,
        clip_upper: Optional[float] = None,
    ) -> float:
        """差分隐私求和。

        Args:
            values: 输入数值列表。
            epsilon: 隐私预算参数。
            delta: 隐私预算 delta 参数。
            mechanism: "laplace" 或 "gaussian"。
            clip_lower: 截断下界；Gaussian 必须提供。
            clip_upper: 截断上界；Gaussian 必须提供。

        Returns:
            带噪声的求和结果。
        """
        mechanism = mechanism.lower()
        if mechanism not in ("laplace", "gaussian"):
            raise ValueError("mechanism must be 'laplace' or 'gaussian'")
        if mechanism == "gaussian" and delta <= 0:
            raise ValueError("delta must be positive for Gaussian mechanism")

        lower, upper = self._resolve_clip_bounds(
            values, clip_lower, clip_upper, mechanism
        )
        clipped = self._clip_values(values, lower, upper)
        true_sum = sum(clipped)
        sensitivity = upper - lower
        if sensitivity <= 0:
            sensitivity = 0.0

        self.budget.spend(epsilon, delta)
        DP_QUERIES_TOTAL.labels(mechanism=mechanism, aggregation="sum").inc()

        if mechanism == "laplace":
            scale = sensitivity / epsilon if epsilon > 0 else 0.0
            noise = self._sample_laplace(scale)
        else:
            # sum L2 sensitivity = upper - lower
            if sensitivity == 0:
                noise = 0.0
            else:
                sigma = (
                    math.sqrt(2.0 * math.log(1.25 / delta)) * sensitivity / epsilon
                )
                noise = self._sample_gaussian(sigma)
        return true_sum + noise

    def mean(
        self,
        values: List[float],
        epsilon: float,
        delta: float = 0.0,
        mechanism: str = "laplace",
        clip_lower: Optional[float] = None,
        clip_upper: Optional[float] = None,
    ) -> float:
        """差分隐私均值。

        使用组合定理：将 (epsilon, delta) 拆分为两份，分别用于 count 与 sum，
        最后用 noisy_sum / noisy_count 得到差分隐私均值。
        """
        if not values:
            return 0.0

        mechanism = mechanism.lower()
        if mechanism not in ("laplace", "gaussian"):
            raise ValueError("mechanism must be 'laplace' or 'gaussian'")
        if mechanism == "gaussian" and delta <= 0:
            raise ValueError("delta must be positive for Gaussian mechanism")

        # 组合定理：总预算 = epsilon/2 + epsilon/2；delta/2 + delta/2
        noisy_count = self.count(
            [1.0] * len(values), epsilon / 2.0, delta / 2.0, mechanism
        )
        noisy_sum = self.sum(
            values, epsilon / 2.0, delta / 2.0, mechanism, clip_lower, clip_upper
        )
        DP_QUERIES_TOTAL.labels(mechanism=mechanism, aggregation="mean").inc()
        return noisy_sum / noisy_count if noisy_count > 0 else 0.0
