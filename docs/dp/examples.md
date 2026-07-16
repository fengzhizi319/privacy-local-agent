# 差分隐私模块使用示例

## 1. 概述

本文档提供 `privacy_local_agent.privacy.dp.DPApi` 与 REST API 的典型使用示例，帮助开发者快速上手 count、sum、mean 三种差分隐私聚合。

## 2. Python SDK 示例

### 2.1 计数查询（Count）

```python
from privacy_local_agent.privacy.dp import DPApi

# 使用独立命名空间管理预算
dp = DPApi(namespace="hospital_cohort_a")

values = [1, 0, 1, 1, 0, 1, 0, 0, 1, 1]
result = dp.count(values, epsilon=1.0, mechanism="laplace")
print(f"带噪声计数: {result}")
```

### 2.2 求和查询（Sum）

```python
from privacy_local_agent.privacy.dp import DPApi

dp = DPApi(namespace="hospital_cohort_a")

# 医疗费用，先截断到 [0, 100000]
charges = [1200.0, 5800.0, 300.0, 99999.0, 15000.0]
result = dp.sum(
    charges,
    epsilon=1.0,
    mechanism="laplace",
    clip_lower=0.0,
    clip_upper=100000.0,
)
print(f"带噪声总费用: {result}")
```

### 2.3 均值查询（Mean）

```python
from privacy_local_agent.privacy.dp import DPApi

dp = DPApi(namespace="hospital_cohort_a")

ages = [
    25.0, 34.0, 45.0, 52.0, 29.0, 61.0,
    38.0, 41.0, 27.0, 55.0, 33.0, 48.0,
    36.0, 50.0, 31.0, 44.0, 39.0, 58.0,
]
result = dp.mean(
    ages,
    epsilon=2.0,
    mechanism="gaussian",
    delta=1e-6,
    clip_lower=0.0,
    clip_upper=120.0,
)
print(f"带噪声平均年龄: {result}")
```

### 2.4 Gaussian 机制示例

```python
from privacy_local_agent.privacy.dp import DPApi

dp = DPApi(namespace="financial_data")

# Gaussian 机制必须提供 delta > 0 与 clip 区间
revenues = [100.0, 200.0, 150.0, 300.0, 250.0]
result = dp.sum(
    revenues,
    epsilon=1.0,
    delta=1e-6,
    mechanism="gaussian",
    clip_lower=0.0,
    clip_upper=500.0,
)
print(f"Gaussian 带噪声总收入: {result}")
```

### 2.5 预算管理示例

```python
from privacy_local_agent.privacy.budget import BudgetAccountant

# 初始化预算账户
accountant = BudgetAccountant(
    namespace="monthly_report", epsilon_total=4.0, delta_total=1e-5
)

# 执行若干查询...
accountant.spend(1.0, 1e-6)
accountant.spend(1.0, 1e-6)

print(f"已用 epsilon: {accountant.epsilon_spent}")
print(f"已用 delta: {accountant.delta_spent}")
print(f"剩余 epsilon: {accountant.epsilon_total - accountant.epsilon_spent}")
```

## 3. REST API 示例

### 3.1 Count

```bash
curl -X POST http://127.0.0.1:8079/v1/privacy/dp/count \
  -H "Content-Type: application/json" \
  -d '{
    "values": [1, 0, 1, 1, 0],
    "params": {"epsilon": 1.0}
  }'
```

### 3.2 Sum with Clipping

```bash
curl -X POST http://127.0.0.1:8079/v1/privacy/dp/sum \
  -H "Content-Type: application/json" \
  -d '{
    "values": [1200, 5800, 300, 99999, 15000],
    "params": {
      "epsilon": 1.0,
      "mechanism": "laplace",
      "clip_lower": 0.0,
      "clip_upper": 100000.0
    }
  }'
```

### 3.3 Mean with Gaussian

```bash
curl -X POST http://127.0.0.1:8079/v1/privacy/dp/mean \
  -H "Content-Type: application/json" \
  -d '{
    "values": [25, 34, 45, 52, 29, 61],
    "params": {
      "epsilon": 2.0,
      "delta": 1e-6,
      "mechanism": "gaussian",
      "clip_lower": 0.0,
      "clip_upper": 120.0
    }
  }'
```

## 4. 本地差分隐私示例

本地 DP 中，每个用户在数据离开设备前完成扰动，服务器只收到扰动后的值。

### 4.1 二值随机响应

```python
from privacy_local_agent.privacy.local_dp import LocalDPApi

api = LocalDPApi(seed=42)

# 真实样本：30% 为 1（例如"患有某疾病"）
n = 2000
true_values = [1 if i < 0.30 * n else 0 for i in range(n)]

# 每个用户本地扰动
reported = api.perturb_binary_batch(true_values, epsilon=1.0)

# 服务器纠偏估计真实频率
estimated = api.estimate_binary_frequency(reported, epsilon=1.0)
print(f"真实患病率: {sum(true_values)/n:.2%}")
print(f"纠偏估计患病率: {estimated:.2%}")
```

### 4.2 类别型随机响应与本地直方图

```python
from privacy_local_agent.privacy.local_dp import LocalDPApi

api = LocalDPApi(seed=42)

categories = ["Apple", "Samsung", "Xiaomi"]
true_brands = ["Apple"] * 5000 + ["Samsung"] * 3000 + ["Xiaomi"] * 2000
api.rng.shuffle(true_brands)

reported = api.perturb_categorical_batch(true_brands, categories, epsilon=1.0)
hist = api.estimate_categorical_histogram(reported, categories, epsilon=1.0)

for c in categories:
    print(f"{c}: {hist[c]:.2%}")
```

### 4.3 运行完整示例

```bash
PYTHONPATH=. python docs/dp/examples/local_dp_usage.py
```

## 5. 最佳实践

1. **始终使用显式 clip 区间**：生产环境中不要依赖 Laplace 的向后兼容数据推断，避免违反 DP 形式化要求。
2. **为不同数据集使用不同 namespace**：防止跨数据集共享隐私预算。
3. **优先使用 Laplace 进行纯 ε-DP**：若业务不能接受 δ，选择 Laplace。
4. **Gaussian 适合大量组合查询**：在需要高级组合定理时，Gaussian 机制更紧致。
5. **本地 DP 适合大样本频率估计**：样本量较小时统计误差较大，不适合需要精确聚合的场景。
6. **记录每次查询的预算消耗**：便于审计与后续预算调整。

## 6. 常见错误

| 错误 | 原因 | 解决 |
|---|---|---|
| `ValueError: clip_lower and clip_upper are required for Gaussian mechanism` | Gaussian sum/mean 未提供 clip | 提供 `clip_lower` 与 `clip_upper` |
| `ValueError: delta must be positive for Gaussian mechanism` | Gaussian 请求中 `delta=0` | 设置 `delta > 0`，典型值 `1e-6` |
| `PrivacyBudgetExhausted` | 命名空间预算已用完 | 提高总预算或减少查询次数 |
| 结果出现负数 | 计数被噪声拉低 | count 已做 `max(0, ...)` 截断；其他场景可后处理 |
