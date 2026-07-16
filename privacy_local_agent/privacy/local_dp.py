"""本地差分隐私（Local Differential Privacy, LDP）API 实现。

本地 DP 与中心式 DP 的信任模型不同：
- 中心式 DP：用户把原始数据交给可信的数据管理者，由管理者在聚合结果上加噪声。
- 本地 DP：每个用户在数据离开自己的设备前，先用随机化算法对数据进行扰动，
  服务器只收到扰动后的值，无法反推用户的真实值。

本模块提供两类基础机制：
1. 随机响应（Randomized Response, RR）：适用于二值或类别型数据。
2. 本地直方图估计：基于 RR 对扰动后的值进行聚合与纠偏，估计真实分布。

每个用户的扰动操作独立消耗本地隐私预算 ε。与中心式 DP 相比，本地 DP 提供更强的
隐私保证（不信任服务器），但通常需要更大的噪声，统计效用较低。
"""

from __future__ import annotations

import math
import random
from typing import Any, Dict, List, Optional, Sequence


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
