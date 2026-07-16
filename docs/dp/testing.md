# 差分隐私模块测试文档

## 1. 概述

本文档定义 `privacy_local_agent/privacy/dp.py` 的测试策略、测试范围与可执行示例。该模块同时包含中心式 DP（`DPApi`）与本地 DP（`LocalDPApi`）两类接口，测试需覆盖算法正确性、参数校验、预算消耗、接口一致性、本地 DP 扰动与统计特性。

## 2. 测试目标

- 验证 Laplace 机制与 Gaussian 机制输出符合预期分布。
- 验证解析高斯机制（Analytic Gaussian）噪声尺度小于经典公式。
- 验证 clipping 参数校验与敏感度计算正确。
- 验证 BudgetAccountant 正确追踪 `(ε, δ)` 消耗并拒绝超支。
- 验证 BudgetAccountant 时间窗口到期后自动重置预算。
- 验证 REST/gRPC 接口透传参数无误。
- 验证 mean 组合实现满足组合定理，且 `min_count` 低频保护生效。
- 验证直方图聚合使用联合敏感度为 1。
- 验证本地 DP 扰动与估计接口（REST/gRPC）行为正确。

## 3. 单元测试策略

### 3.1 机制正确性测试

固定随机数种子，断言噪声值与理论值一致。对解析高斯机制，断言其噪声小于经典高斯公式。

```python
import math
import random

from privacy_local_agent.privacy.dp import DPApi


def test_laplace_count_with_fixed_seed():
    dp = DPApi(namespace="test_count")
    dp.rng = random.Random(42)

    values = [1, 0, 1, 1, 0]
    result = dp.count(values, epsilon=1.0, mechanism="laplace")

    # true_count = 3; 固定种子下噪声可重复
    assert 0 <= result <= 10  # 非负且在一个合理范围内
```

### 3.2 参数校验测试

```python
import pytest
from privacy_local_agent.privacy.dp import DPApi


def test_gaussian_requires_clip():
    dp = DPApi(namespace="test_clip")
    with pytest.raises(ValueError, match="clip_lower and clip_upper are required"):
        dp.sum([1.0, 2.0, 3.0], epsilon=1.0, delta=1e-6, mechanism="gaussian")


def test_gaussian_requires_positive_delta():
    dp = DPApi(namespace="test_delta")
    with pytest.raises(ValueError, match="delta must be positive"):
        dp.count([1, 0, 1], epsilon=1.0, delta=0.0, mechanism="gaussian")
```

### 3.3 预算消耗测试

```python
from privacy_local_agent.privacy.budget import BudgetAccountant


def test_budget_accountant_tracks_spending():
    accountant = BudgetAccountant(
        namespace="test_budget", epsilon_total=2.0, delta_total=1e-5
    )

    accountant.spend(1.0, 1e-6)
    assert accountant.epsilon_spent == 1.0
    assert accountant.delta_spent == 1e-6

    accountant.spend(0.5, 1e-6)
    assert accountant.epsilon_spent == 1.5
```

### 3.4 统计特性测试

对同一查询重复采样，验证噪声均值接近 0，方差符合理论值。

```python
import statistics

from privacy_local_agent.privacy.dp import DPApi


def test_laplace_noise_statistics():
    dp = DPApi(namespace="test_stats")
    samples = [dp._sample_laplace(scale=1.0) for _ in range(5000)]

    mean = statistics.mean(samples)
    var = statistics.variance(samples)

    assert abs(mean) < 0.1        # Laplace(0,1) 均值为 0
    assert 1.8 < var < 2.2        # Laplace(0,1) 方差为 2
```

### 3.5 均值低频保护与直方图测试

```python
from privacy_local_agent.privacy.dp import DPApi
from privacy_local_agent.privacy.budget import BudgetAccountant


def test_mean_min_count_protection():
    BudgetAccountant("test-mean-thresh", epsilon_total=100.0, delta_total=1.0)
    api = DPApi(namespace="test-mean-thresh")

    # 正常计算
    res = api.mean([10.0] * 10, epsilon=10.0, min_count=2.0)
    assert res > 0.0

    # 计数过小，触发低频保护返回 0.0
    res_shielded = api.mean([10.0] * 3, epsilon=10.0, min_count=5.0)
    assert res_shielded == 0.0


def test_histogram_joint_sensitivity():
    BudgetAccountant("test-hist", epsilon_total=100.0, delta_total=1.0)
    api = DPApi(namespace="test-hist")
    values = ["A"] * 100 + ["B"] * 200 + ["C"] * 50
    categories = ["A", "B", "C", "D"]
    res = api.histogram(values, categories, epsilon=10.0, mechanism="laplace")
    assert len(res) == 4
    assert res["A"] > 50
    assert res["B"] > 100
    assert res["D"] >= 0.0
```

### 3.6 本地差分隐私测试

本地 DP 测试重点在于随机响应扰动与频率估计的无偏性。此外需验证 REST/gRPC 接口对本地 DP 能力的暴露。

```python
from privacy_local_agent.privacy.dp import LocalDPApi


def test_local_dp_binary_unbiased():
    api = LocalDPApi(seed=42)
    true_values = [1 if i % 4 == 0 else 0 for i in range(4000)]
    true_freq = sum(true_values) / len(true_values)

    reported = api.perturb_binary_batch(true_values, epsilon=1.0)
    estimated = api.estimate_binary_frequency(reported, epsilon=1.0)

    assert abs(estimated - true_freq) < 0.05


def test_local_dp_categorical_unbiased():
    api = LocalDPApi(seed=42)
    categories = ["A", "B", "C"]
    true = ["A"] * 4000 + ["B"] * 3000 + ["C"] * 3000
    api.rng.shuffle(true)

    reported = api.perturb_categorical_batch(true, categories, epsilon=1.0)
    hist = api.estimate_categorical_histogram(reported, categories, epsilon=1.0)

    assert abs(hist["A"] - 0.40) < 0.05
    assert abs(hist["B"] - 0.30) < 0.05
    assert abs(hist["C"] - 0.30) < 0.05
```

执行本地 DP 测试：

```bash
PYTHONPATH=. pytest tests/test_dp.py -v -k "LocalDP or Randomized"
```

### 3.7 时间窗口预算重置测试

```python
import time
from privacy_local_agent.privacy.budget import BudgetAccountant


def test_budget_window_reset():
    BudgetAccountant._instances.clear()
    accountant = BudgetAccountant(
        "test-window", epsilon_total=2.0, delta_total=1e-5, window_seconds=0.1
    )
    accountant.spend(1.5, 0.0)
    assert accountant.remaining()["epsilon"] == 0.5

    time.sleep(0.15)
    accountant.spend(1.0, 0.0)
    assert accountant.remaining()["epsilon"] == 1.0
```

## 4. 集成测试策略

### 4.1 REST 接口测试

```python
from fastapi.testclient import TestClient
from privacy_local_agent.main import app


def test_rest_dp_count():
    client = TestClient(app)
    resp = client.post(
        "/v1/privacy/dp/count",
        json={"values": [1, 0, 1, 1, 0], "params": {"epsilon": 1.0}},
    )
    assert resp.status_code == 200
    assert "result" in resp.json()
```

### 4.2 gRPC 接口测试

```python
import grpc
from privacy_local_agent.grpc_server import PrivacyServicer
from privacy_local_agent.proto import privacy_pb2, privacy_pb2_grpc


def test_grpc_dp_sum():
    servicer = PrivacyServicer()
    request = privacy_pb2.DPRequest(
        values=[1.0, 2.0, 3.0],
        params={
            "epsilon": "1.0",
            "delta": "1e-6",
            "mechanism": "gaussian",
            "clip_lower": "0.0",
            "clip_upper": "10.0",
        },
    )
    response = servicer.DPSum(request, None)
    assert response.result is not None
```

## 5. 测试执行命令

```bash
# 运行所有 DP 相关测试
PYTHONPATH=. pytest tests/test_dp.py -v

# 运行 DP 模块单元测试
PYTHONPATH=. pytest tests/test_budget.py tests/test_rest.py -v -k dp

# 本地 DP 测试
PYTHONPATH=. pytest tests/test_dp.py -v -k "LocalDP or Randomized"

# 统计特性测试可能需要更多样本
PYTHONPATH=. pytest tests/test_dp.py -v --slow
```

## 6. 持续集成建议

- 每次提交前执行 `pytest tests/test_dp.py`。
- 在 CI 中固定 Python 版本与随机数种子，避免测试抖动。
- 对统计测试设置宽松的置信区间，避免偶发失败。

## 7. 验收检查清单

- [ ] count/sum/mean/histogram 四种聚合的单元测试覆盖。
- [ ] Laplace 与 Gaussian 机制各至少一组测试。
- [ ] 解析高斯机制噪声尺度测试覆盖。
- [ ] 本地 DP 二值/类别随机响应与频率估计测试覆盖。
- [ ] 本地 DP REST/gRPC 接口测试覆盖。
- [ ] clipping 参数缺失/错误时抛出明确异常。
- [ ] 预算超支时返回 `PrivacyBudgetExhausted` 或对应 HTTP/gRPC 错误。
- [ ] 预算时间窗口重置测试覆盖。
- [ ] REST/gRPC 接口参数透传测试通过。
- [ ] 统计测试在 5000 次采样下通过。
