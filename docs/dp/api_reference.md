# 差分隐私模块 API 参考

## 1. Python SDK

### `DPApi`

位置：`privacy_local_agent.privacy.dp.DPApi`

差分隐私计算入口类，封装 Laplace/Gaussian 采样与预算扣减。

#### 构造函数

```python
DPApi(namespace: str = "default")
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `namespace` | `str` | 否 | 隐私预算命名空间，默认 `"default"` |

#### `count`

```python
count(
    values: List[float],
    epsilon: float,
    delta: float = 0.0,
    mechanism: str = "laplace",
) -> float
```

差分隐私计数。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `values` | `List[float]` | 是 | 输入值列表，非零/非空元素被计入 |
| `epsilon` | `float` | 是 | 隐私预算 ε |
| `delta` | `float` | 否 | 隐私预算 δ；Gaussian 机制必须 > 0 |
| `mechanism` | `str` | 否 | `"laplace"` 或 `"gaussian"` |

**返回值**：带噪声的计数值（已做 `max(0, ...)` 截断）。

**敏感度**：L1 = 1，L2 = 1。

---

#### `sum`

```python
sum(
    values: List[float],
    epsilon: float,
    delta: float = 0.0,
    mechanism: str = "laplace",
    clip_lower: Optional[float] = None,
    clip_upper: Optional[float] = None,
) -> float
```

差分隐私求和。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `values` | `List[float]` | 是 | 输入值列表 |
| `epsilon` | `float` | 是 | 隐私预算 ε |
| `delta` | `float` | 否 | 隐私预算 δ；Gaussian 机制必须 > 0 |
| `mechanism` | `str` | 否 | `"laplace"` 或 `"gaussian"` |
| `clip_lower` | `Optional[float]` | 否 | 截断下界；Gaussian 必须提供 |
| `clip_upper` | `Optional[float]` | 否 | 截断上界；Gaussian 必须提供 |

**返回值**：带噪声的求和结果。

**敏感度**：L1 = L2 = `clip_upper - clip_lower`。

---

#### `mean`

```python
mean(
    values: List[float],
    epsilon: float,
    delta: float = 0.0,
    mechanism: str = "laplace",
    clip_lower: Optional[float] = None,
    clip_upper: Optional[float] = None,
) -> float
```

差分隐私均值。内部将 `(epsilon, delta)` 平分为两份，分别用于 count 与 sum，再用 `noisy_sum / noisy_count` 得到结果。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `values` | `List[float]` | 是 | 输入值列表 |
| `epsilon` | `float` | 是 | 隐私预算 ε |
| `delta` | `float` | 否 | 隐私预算 δ；Gaussian 机制必须 > 0 |
| `mechanism` | `str` | 否 | `"laplace"` 或 `"gaussian"` |
| `clip_lower` | `Optional[float]` | 否 | 截断下界；Gaussian 必须提供 |
| `clip_upper` | `Optional[float]` | 否 | 截断上界；Gaussian 必须提供 |

**返回值**：带噪声的均值。

**敏感度**：count 部分为 1；sum 部分为 `clip_upper - clip_lower`。

**总隐私消耗**：$(\varepsilon, \delta)$。

---

### `LocalDPApi`

位置：`privacy_local_agent.privacy.dp.LocalDPApi`

本地差分隐私入口类，支持单条记录的随机响应扰动与服务端的频率/直方图纠偏估计。

#### 构造函数

```python
LocalDPApi(seed: Optional[int] = None)
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `seed` | `Optional[int]` | 否 | 随机数种子，用于可复现测试 |

#### `perturb_binary`

```python
perturb_binary(value: int, epsilon: float) -> int
```

二值随机响应。以概率 $p = \frac{e^\varepsilon}{1 + e^\varepsilon}$ 保留真值，否则翻转为相反值。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `value` | `int` | 是 | `0` 或 `1` |
| `epsilon` | `float` | 是 | 隐私预算 ε，必须 > 0 |

**返回值**：扰动后的 `0` 或 `1`。

---

#### `perturb_binary_batch`

```python
perturb_binary_batch(values: List[int], epsilon: float) -> List[int]
```

对二值列表逐条执行随机响应。

---

#### `perturb_categorical`

```python
perturb_categorical(value: Any, categories: List[Any], epsilon: float) -> Any
```

类别型随机响应。以概率 $p = \frac{e^\varepsilon}{|C| - 1 + e^\varepsilon}$ 保留真值，否则从其余类别均匀随机选择。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `value` | `Any` | 是 | 真实类别值，必须属于 `categories` |
| `categories` | `List[Any]` | 是 | 所有可能类别 |
| `epsilon` | `float` | 是 | 隐私预算 ε，必须 > 0 |

**返回值**：扰动后的类别值。

---

#### `perturb_categorical_batch`

```python
perturb_categorical_batch(values: List[Any], categories: List[Any], epsilon: float) -> List[Any]
```

对类别列表逐条执行随机响应。

---

#### `estimate_binary_frequency`

```python
estimate_binary_frequency(reported_values: List[int], epsilon: float) -> float
```

从扰动后的二值报告中纠偏估计真实 `1` 的频率。

**返回值**：`[0, 1]` 区间内的频率估计值。

---

#### `estimate_categorical_histogram`

```python
estimate_categorical_histogram(
    reported_values: List[Any],
    categories: List[Any],
    epsilon: float,
) -> Dict[Any, float]
```

从扰动后的类别报告中纠偏估计真实分布。

**返回值**：每个类别的频率字典，频率之和近似为 1。

---

### `BudgetAccountant`

位置：`privacy_local_agent.privacy.budget.BudgetAccountant`

隐私预算账户，追踪命名空间级别的累计 `(ε, δ)` 消耗。

#### 构造函数

```python
BudgetAccountant(
    namespace: str,
    epsilon_total: float = 10.0,
    delta_total: float = 1e-4,
)
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `namespace` | `str` | 是 | 命名空间标识 |
| `epsilon_total` | `float` | 否 | epsilon 总预算，默认 10.0 |
| `delta_total` | `float` | 否 | delta 总预算，默认 1e-4 |

> 注意：`BudgetAccountant` 为单例模式，首次创建后传入的 `epsilon_total`/`delta_total` 会被保留，后续同 namespace 调用将忽略这些参数。

#### 主要属性

| 属性 | 类型 | 说明 |
|---|---|---|
| `epsilon_total` | `float` | epsilon 总预算 |
| `delta_total` | `float` | delta 总预算 |
| `epsilon_spent` | `float` | 已消耗 epsilon |
| `delta_spent` | `float` | 已消耗 delta |

#### 主要方法

| 方法 | 签名 | 说明 |
|---|---|---|
| `spend` | `spend(epsilon: float, delta: float = 0.0)` | 消耗预算；超支时抛出 `PrivacyBudgetExhausted` |
| `remaining` | `remaining() -> dict[str, float]` | 返回剩余 `{"epsilon": float, "delta": float}` |

---

## 2. REST API

### POST `/v1/privacy/dp/count`

请求体：

```json
{
  "values": [1, 0, 1, 1, 0],
  "params": {
    "epsilon": 1.0,
    "delta": 0.0,
    "mechanism": "laplace"
  }
}
```

响应体：

```json
{
  "result": 3.142
}
```

### POST `/v1/privacy/dp/sum`

请求体：

```json
{
  "values": [1200, 5800, 300, 99999, 15000],
  "params": {
    "epsilon": 1.0,
    "delta": 1e-6,
    "mechanism": "gaussian",
    "clip_lower": 0.0,
    "clip_upper": 100000.0
  }
}
```

响应体：

```json
{
  "result": 122345.678
}
```

### POST `/v1/privacy/dp/mean`

请求体：

```json
{
  "values": [25, 34, 45, 52, 29, 61],
  "params": {
    "epsilon": 2.0,
    "delta": 1e-6,
    "mechanism": "gaussian",
    "clip_lower": 0.0,
    "clip_upper": 120.0
  }
}
```

响应体：

```json
{
  "result": 41.234
}
```

---

## 3. gRPC API

### 方法列表

| 方法 | 请求 | 响应 | 说明 |
|---|---|---|---|
| `DPCount` | `DPRequest` | `DPResponse` | 差分隐私计数 |
| `DPSum` | `DPRequest` | `DPResponse` | 差分隐私求和 |
| `DPMean` | `DPRequest` | `DPResponse` | 差分隐私均值 |

### `DPRequest` 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `values` | `repeated double` | 输入值列表 |
| `params` | `map<string, string>` | 参数映射，包含 `epsilon`、`delta`、`mechanism`、`clip_lower`、`clip_upper` |

### `DPResponse` 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `result` | `double` | 带噪声的查询结果 |

---

## 4. 异常与错误码

| 异常/错误 | 触发条件 | HTTP 状态码 | gRPC 状态码 |
|---|---|---|---|
| `ValueError: clip_lower and clip_upper are required for Gaussian mechanism` | Gaussian sum/mean 缺少 clip | 400 | `INVALID_ARGUMENT` |
| `ValueError: delta must be positive for Gaussian mechanism` | Gaussian 请求 delta ≤ 0 | 400 | `INVALID_ARGUMENT` |
| `PrivacyBudgetExhausted` | 累计预算超过命名空间上限 | 429 | `RESOURCE_EXHAUSTED` |
| `ValueError: mechanism must be 'laplace' or 'gaussian'` | mechanism 参数非法 | 400 | `INVALID_ARGUMENT` |
