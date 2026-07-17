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
    values: Any,
    epsilon: float,
    delta: float = 0.0,
    mechanism: str = "laplace",
    column: Optional[str] = None,
    party: Optional[str] = None,
) -> float
```

差分隐私计数。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `values` | `Any` | 是 | 输入值，支持 list/tuple/ndarray/Series/DataFrame/SecretFlow 格式 |
| `epsilon` | `float` | 是 | 隐私预算 ε |
| `delta` | `float` | 否 | 隐私预算 δ；Gaussian 机制必须 > 0 |
| `mechanism` | `str` | 否 | `"laplace"` 或 `"gaussian"` |
| `column` | `Optional[str]` | 否 | 表格型输入的目标列名 |
| `party` | `Optional[str]` | 否 | SecretFlow HDataFrame 的参与方标识 |

**返回值**：带噪声的计数值（已做 `max(0, ...)` 截断）。

**敏感度**：L1 = 1，L2 = 1。

---

#### `sum`

```python
sum(
    values: Any,
    epsilon: float,
    delta: float = 0.0,
    mechanism: str = "laplace",
    clip_lower: Optional[float] = None,
    clip_upper: Optional[float] = None,
    column: Optional[str] = None,
    party: Optional[str] = None,
) -> float
```

差分隐私求和。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `values` | `Any` | 是 | 输入值，支持多种数据格式 |
| `epsilon` | `float` | 是 | 隐私预算 ε |
| `delta` | `float` | 否 | 隐私预算 δ；Gaussian 机制必须 > 0 |
| `mechanism` | `str` | 否 | `"laplace"` 或 `"gaussian"` |
| `clip_lower` | `Optional[float]` | 否 | 截断下界；Gaussian 必须提供 |
| `clip_upper` | `Optional[float]` | 否 | 截断上界；Gaussian 必须提供 |
| `column` | `Optional[str]` | 否 | 表格型输入的目标列名 |
| `party` | `Optional[str]` | 否 | SecretFlow HDataFrame 的参与方标识 |

**返回值**：带噪声的求和结果。

**敏感度**：L1 = L2 = `clip_upper - clip_lower`。

---

#### `mean`

```python
mean(
    values: Any,
    epsilon: float,
    delta: float = 0.0,
    mechanism: str = "laplace",
    clip_lower: Optional[float] = None,
    clip_upper: Optional[float] = None,
    min_count: float = 5.0,
    column: Optional[str] = None,
    party: Optional[str] = None,
) -> float
```

差分隐私均值。内部将 `(epsilon, delta)` 平分为两份，分别用于 count 与 sum，再用 `noisy_sum / noisy_count` 得到结果。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `values` | `Any` | 是 | 输入值，支持多种数据格式 |
| `epsilon` | `float` | 是 | 隐私预算 ε |
| `delta` | `float` | 否 | 隐私预算 δ；Gaussian 机制必须 > 0 |
| `mechanism` | `str` | 否 | `"laplace"` 或 `"gaussian"` |
| `clip_lower` | `Optional[float]` | 否 | 截断下界；Gaussian 必须提供 |
| `clip_upper` | `Optional[float]` | 否 | 截断上界；Gaussian 必须提供 |
| `min_count` | `float` | 否 | 低频计数阈值，当估计的计数小于此值时返回 0.0 避免结果发散，默认 `5.0` |
| `column` | `Optional[str]` | 否 | 表格型输入的目标列名 |
| `party` | `Optional[str]` | 否 | SecretFlow HDataFrame 的参与方标识 |

**返回值**：带噪声的均值。

**敏感度**：count 部分为 1；sum 部分为 `clip_upper - clip_lower`。

**总隐私消耗**：$(\varepsilon, \delta)$。

---

#### `histogram`

```python
histogram(
    values: Any,
    categories: Sequence[Any],
    epsilon: float,
    delta: float = 0.0,
    mechanism: str = "laplace",
    column: Optional[str] = None,
    party: Optional[str] = None,
) -> Dict[Any, float]
```

差分隐私直方图计数。使用联合敏感度为 1（若一个记录仅能属于一个分桶），仅消耗单次 `(epsilon, delta)` 预算。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `values` | `Any` | 是 | 原始类别特征值，支持多种数据格式 |
| `categories` | `Sequence[Any]` | 是 | 分桶的目标类别集合 |
| `epsilon` | `float` | 是 | 隐私预算 ε |
| `delta` | `float` | 否 | 隐私预算 δ；Gaussian 机制必须 > 0 |
| `mechanism` | `str` | 否 | `"laplace"` 或 `"gaussian"` |
| `column` | `Optional[str]` | 否 | 表格型输入的目标列名 |
| `party` | `Optional[str]` | 否 | SecretFlow HDataFrame 的参与方标识 |

**返回值**：类别名称到带噪计数的字典（已做 `max(0, ...)` 截断）。

**敏感度**：L1 = L2 = 1。

---

#### `noisy_count`

```python
noisy_count(
    true_count: float,
    epsilon: float,
    delta: float = 0.0,
    mechanism: str = "laplace",
) -> float
```

对已经聚合好的计数结果直接注入 DP 噪声。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `true_count` | `float` | 是 | 真实计数值 |
| `epsilon` | `float` | 是 | 隐私预算 ε |
| `delta` | `float` | 否 | 隐私预算 δ；Gaussian 机制必须 > 0 |
| `mechanism` | `str` | 否 | `"laplace"` 或 `"gaussian"` |

**返回值**：带噪声的计数值（已做 `max(0, ...)` 截断）。

**敏感度**：1。

---

#### `noisy_sum`

```python
noisy_sum(
    true_sum: float,
    sensitivity: float,
    epsilon: float,
    delta: float = 0.0,
    mechanism: str = "laplace",
) -> float
```

对已经聚合好的求和结果直接注入 DP 噪声。调用方必须提供敏感度。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `true_sum` | `float` | 是 | 真实求和值 |
| `sensitivity` | `float` | 是 | L1/L2 敏感度（通常为 clip_upper - clip_lower） |
| `epsilon` | `float` | 是 | 隐私预算 ε |
| `delta` | `float` | 否 | 隐私预算 δ；Gaussian 机制必须 > 0 |
| `mechanism` | `str` | 否 | `"laplace"` 或 `"gaussian"` |

**返回值**：带噪声的求和结果。

---

#### `noisy_mean`

```python
noisy_mean(
    true_sum: float,
    true_count: float,
    sensitivity: float,
    epsilon: float,
    delta: float = 0.0,
    mechanism: str = "laplace",
    min_count: float = 5.0,
) -> float
```

对已经聚合好的 sum/count 分别注入 DP 噪声后得到均值。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `true_sum` | `float` | 是 | 真实求和值 |
| `true_count` | `float` | 是 | 真实计数值 |
| `sensitivity` | `float` | 是 | sum 部分的敏感度 |
| `epsilon` | `float` | 是 | 隐私预算 ε |
| `delta` | `float` | 否 | 隐私预算 δ |
| `mechanism` | `str` | 否 | `"laplace"` 或 `"gaussian"` |
| `min_count` | `float` | 否 | 低频计数阈值，默认 `5.0` |

**返回值**：带噪声的均值；当 `noisy_count < min_count` 时返回 `0.0`。

---

#### `noisy_histogram`

```python
noisy_histogram(
    true_counts: Dict[Any, float],
    epsilon: float,
    delta: float = 0.0,
    mechanism: str = "laplace",
) -> Dict[Any, float]
```

对已经聚合好的直方图计数直接注入 DP 噪声。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `true_counts` | `Dict[Any, float]` | 是 | 分桶名到真实计数的字典 |
| `epsilon` | `float` | 是 | 隐私预算 ε |
| `delta` | `float` | 否 | 隐私预算 δ |
| `mechanism` | `str` | 否 | `"laplace"` 或 `"gaussian"` |

**返回值**：分桶名到带噪计数的字典。

---

#### `chunked_count`

```python
chunked_count(
    chunks: Iterable[Any],
    epsilon: float,
    delta: float = 0.0,
    mechanism: str = "laplace",
    column: Optional[str] = None,
    party: Optional[str] = None,
) -> float
```

分块流式差分隐私计数。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `chunks` | `Iterable[Any]` | 是 | 数据块可迭代对象 |
| `epsilon` | `float` | 是 | 隐私预算 ε |
| `delta` | `float` | 否 | 隐私预算 δ |
| `mechanism` | `str` | 否 | `"laplace"` 或 `"gaussian"` |
| `column` | `Optional[str]` | 否 | 表格型 chunk 的目标列名 |
| `party` | `Optional[str]` | 否 | SecretFlow HDataFrame 参与方标识 |

---

#### `chunked_sum`

```python
chunked_sum(
    chunks: Iterable[Any],
    epsilon: float,
    delta: float = 0.0,
    mechanism: str = "laplace",
    clip_lower: float,
    clip_upper: float,
    column: Optional[str] = None,
    party: Optional[str] = None,
) -> float
```

分块流式差分隐私求和。**必须显式提供** `clip_lower` / `clip_upper`。

---

#### `chunked_mean`

```python
chunked_mean(
    chunks: Iterable[Any],
    epsilon: float,
    delta: float = 0.0,
    mechanism: str = "laplace",
    clip_lower: float,
    clip_upper: float,
    min_count: float = 5.0,
    column: Optional[str] = None,
    party: Optional[str] = None,
) -> float
```

分块流式差分隐私均值。**必须显式提供** `clip_lower` / `clip_upper`。

---

#### `chunked_histogram`

```python
chunked_histogram(
    chunks: Iterable[Any],
    categories: Sequence[Any],
    epsilon: float,
    delta: float = 0.0,
    mechanism: str = "laplace",
    column: Optional[str] = None,
    party: Optional[str] = None,
) -> Dict[Any, float]
```

分块流式差分隐私直方图计数。

---

### `extract_values`（数据适配器）

位置：`privacy_local_agent.privacy.data_adapters.extract_values`

```python
extract_values(
    data: Any,
    column: Optional[str] = None,
    party: Optional[str] = None,
) -> List[float]
```

将多种数据格式统一转换为 Python 列表，供 DP 模块消费。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `data` | `Any` | 是 | 输入数据 |
| `column` | `Optional[str]` | 否 | 表格型输入的目标列名 |
| `party` | `Optional[str]` | 否 | SecretFlow HDataFrame 参与方标识 |

**支持格式**：

| 类型 | 所需参数 |
|---|---|
| `list` / `tuple` | 无 |
| `np.ndarray` | 无 |
| `pd.Series` | 无 |
| `pd.DataFrame` | `column` |
| `sf.data.DataFrame` | `column` |
| `HDataFrame` | `column`，多 partition 时需提供 `party` |
| `VDataFrame` | `column`（自动定位 partition） |
| `MixDataFrame` | 不支持直接提取 |
| `FedNdarray` | 按 H/V 方式处理 |

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

### POST `/v1/privacy/dp/histogram`

请求体：

```json
{
  "values": ["A", "B", "A", "C"],
  "categories": ["A", "B", "C", "D"],
  "params": {
    "epsilon": 10.0,
    "mechanism": "laplace"
  }
}
```

响应体：

```json
{
  "result": {
    "A": 2.14,
    "B": 0.95,
    "C": 1.05,
    "D": 0.0
  }
}
```

### POST `/v1/privacy/dp/noisy_count`

对已由外部引擎聚合好的计数结果加噪。

请求体：
```json
{
  "true_count": 1000.0,
  "params": {
    "epsilon": 1.0,
    "mechanism": "laplace"
  }
}
```

响应体：
```json
{
  "result": 1001.5
}
```

### POST `/v1/privacy/dp/noisy_sum`

对已由外部引擎聚合好的求和结果加噪。`params` 中需提供 `sensitivity`，或同时提供 `clip_lower` 与 `clip_upper`。

请求体：
```json
{
  "true_sum": 5000000.0,
  "params": {
    "epsilon": 1.0,
    "delta": 1e-6,
    "mechanism": "gaussian",
    "sensitivity": 100000.0
  }
}
```

### POST `/v1/privacy/dp/noisy_mean`

对已由外部引擎聚合好的 sum/count 加噪后得到均值。

请求体：
```json
{
  "true_sum": 5000000.0,
  "true_count": 1000.0,
  "params": {
    "epsilon": 2.0,
    "delta": 1e-6,
    "mechanism": "gaussian",
    "sensitivity": 100000.0,
    "min_count": 5.0
  }
}
```

### POST `/v1/privacy/dp/noisy_histogram`

对已由外部引擎聚合好的直方图计数加噪。

请求体：
```json
{
  "true_counts": {
    "A": 100.0,
    "B": 80.0,
    "C": 50.0
  },
  "params": {
    "epsilon": 1.0,
    "mechanism": "laplace"
  }
}
```

### POST `/v1/privacy/dp/chunked_count`

分块流式差分隐私计数。

请求体：
```json
{
  "chunks": [
    [1, 0, 1],
    [1, 0, 1, 1]
  ],
  "params": {
    "epsilon": 1.0,
    "mechanism": "laplace"
  }
}
```

### POST `/v1/privacy/dp/chunked_sum`

分块流式差分隐私求和。`params` 中必须提供 `clip_lower` / `clip_upper`。

请求体：
```json
{
  "chunks": [
    [1.0, 2.0, 3.0],
    [4.0, 5.0, 6.0]
  ],
  "params": {
    "epsilon": 1.0,
    "delta": 1e-6,
    "mechanism": "gaussian",
    "clip_lower": 0.0,
    "clip_upper": 10.0
  }
}
```

### POST `/v1/privacy/dp/chunked_mean`

分块流式差分隐私均值。

请求体：
```json
{
  "chunks": [
    [1.0, 2.0, 3.0],
    [4.0, 5.0, 6.0]
  ],
  "params": {
    "epsilon": 2.0,
    "delta": 1e-6,
    "mechanism": "gaussian",
    "clip_lower": 0.0,
    "clip_upper": 10.0,
    "min_count": 2.0
  }
}
```

### POST `/v1/privacy/dp/chunked_histogram`

分块流式差分隐私直方图计数。

请求体：
```json
{
  "chunks": [
    ["A", "B", "A"],
    ["C", "A", "B"]
  ],
  "categories": ["A", "B", "C", "D"],
  "params": {
    "epsilon": 10.0,
    "mechanism": "laplace"
  }
}
```

### POST `/v1/privacy/ldp/perturb/binary`

二值本地差分隐私扰动。

请求体：
```json
{
  "values": [1, 0, 1, 1],
  "epsilon": 10.0
}
```

响应体：
```json
{
  "results": [1, 0, 1, 1]
}
```

### POST `/v1/privacy/ldp/perturb/categorical`

类别型本地差分隐私扰动。

请求体：
```json
{
  "values": ["A", "B", "A"],
  "categories": ["A", "B", "C"],
  "epsilon": 10.0
}
```

响应体：
```json
{
  "results": ["A", "B", "C"]
}
```

### POST `/v1/privacy/ldp/estimate/binary`

二值扰动样本频率估计。

请求体：
```json
{
  "reported_values": [1, 1, 0, 1],
  "epsilon": 5.0
}
```

响应体：
```json
{
  "estimated_frequency": 0.75
}
```

### POST `/v1/privacy/ldp/estimate/categorical`

类别型扰动样本直方图估计。

请求体：
```json
{
  "reported_values": ["A", "B", "C", "A"],
  "categories": ["A", "B", "C"],
  "epsilon": 5.0
}
```

响应体：
```json
{
  "estimated_histogram": {
    "A": 0.5,
    "B": 0.25,
    "C": 0.25
  }
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
| `DPHistogram` | `DPHistogramRequest` | `DPHistogramResponse` | 差分隐私直方图 |
| `DPNoisyCount` | `DPNoisyCountRequest` | `DPResponse` | 对已聚合计数加噪 |
| `DPNoisySum` | `DPNoisySumRequest` | `DPResponse` | 对已聚合求和加噪 |
| `DPNoisyMean` | `DPNoisyMeanRequest` | `DPResponse` | 对已聚合 sum/count 加噪得均值 |
| `DPNoisyHistogram` | `DPNoisyHistogramRequest` | `DPHistogramResponse` | 对已聚合直方图加噪 |
| `DPChunkedCount` | `DPChunkedCountRequest` | `DPResponse` | 分块流式计数 |
| `DPChunkedSum` | `DPChunkedSumRequest` | `DPResponse` | 分块流式求和 |
| `DPChunkedMean` | `DPChunkedMeanRequest` | `DPResponse` | 分块流式均值 |
| `DPChunkedHistogram` | `DPChunkedHistogramRequest` | `DPHistogramResponse` | 分块流式直方图 |
| `PerturbBinaryBatch` | `PerturbBinaryBatchRequest` | `PerturbBinaryBatchResponse` | 二值本地 DP 扰动 |
| `PerturbCategoricalBatch` | `PerturbCategoricalBatchRequest` | `PerturbCategoricalBatchResponse` | 类别型本地 DP 扰动 |
| `EstimateBinaryFrequency` | `EstimateBinaryFrequencyRequest` | `EstimateBinaryFrequencyResponse` | 二值扰动样本频率估计 |
| `EstimateCategoricalHistogram` | `EstimateCategoricalHistogramRequest` | `EstimateCategoricalHistogramResponse` | 类别型扰动直方图估计 |

### `DPRequest` 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `values` | `repeated double` | 输入值列表 |
| `params` | `map<string, string>` | 参数映射，包含 `epsilon`、`delta`、`mechanism`、`clip_lower`、`clip_upper` |

### `DPResponse` 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `result` | `double` | 带噪声的查询结果 |

### `DPHistogramRequest` 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `values` | `repeated string` | 输入值列表 |
| `categories` | `repeated string` | 目标类别集合 |
| `epsilon` | `double` | 隐私预算 ε |
| `mechanism` | `string` | `"laplace"` 或 `"gaussian"` |
| `delta` | `double` | 隐私预算 δ |

### `DPHistogramResponse` 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `result` | `map<string, double>` | 直方图分类计数结果 |

### Noisify 相关 Message 字段

#### `DPNoisyCountRequest`
*   `true_count` (`double`): 真实计数值。
*   `epsilon` (`double`): 隐私预算 ε。
*   `mechanism` (`string`): `"laplace"` 或 `"gaussian"`。
*   `delta` (`double`): 隐私预算 δ。

#### `DPNoisySumRequest`
*   `true_sum` (`double`): 真实求和值。
*   `epsilon` (`double`): 隐私预算 ε。
*   `mechanism` (`string`): `"laplace"` 或 `"gaussian"`。
*   `delta` (`double`): 隐私预算 δ。
*   `sensitivity` (`double`): 敏感度；若为 0 则尝试通过 `clip_lower`/`clip_upper` 推导。
*   `clip_lower` (`double`): 截断下界。
*   `clip_upper` (`double`): 截断上界。

#### `DPNoisyMeanRequest`
*   `true_sum` (`double`): 真实求和值。
*   `true_count` (`double`): 真实计数值。
*   `epsilon` (`double`): 隐私预算 ε。
*   `mechanism` (`string`): `"laplace"` 或 `"gaussian"`。
*   `delta` (`double`): 隐私预算 δ。
*   `sensitivity` (`double`): sum 部分敏感度。
*   `clip_lower` (`double`): 截断下界。
*   `clip_upper` (`double`): 截断上界。
*   `min_count` (`double`): 低频计数阈值。

#### `DPNoisyHistogramRequest`
*   `true_counts` (`map<string, double>`): 分桶名到真实计数的映射。
*   `epsilon` (`double`): 隐私预算 ε。
*   `mechanism` (`string`): `"laplace"` 或 `"gaussian"`。
*   `delta` (`double`): 隐私预算 δ。

### Chunked 相关 Message 字段

#### `DoubleChunk`
*   `values` (`repeated double`): 数值型数据块。

#### `StringChunk`
*   `values` (`repeated string`): 类别型数据块。

#### `DPChunkedCountRequest`
*   `chunks` (`repeated DoubleChunk`): 数值型数据块列表。
*   `epsilon` (`double`): 隐私预算 ε。
*   `mechanism` (`string`): `"laplace"` 或 `"gaussian"`。
*   `delta` (`double`): 隐私预算 δ。

#### `DPChunkedSumRequest`
*   `chunks` (`repeated DoubleChunk`): 数值型数据块列表。
*   `epsilon` (`double`): 隐私预算 ε。
*   `mechanism` (`string`): `"laplace"` 或 `"gaussian"`。
*   `delta` (`double`): 隐私预算 δ。
*   `clip_lower` (`double`): 截断下界。
*   `clip_upper` (`double`): 截断上界。

#### `DPChunkedMeanRequest`
*   字段同 `DPChunkedSumRequest`，额外包含：
*   `min_count` (`double`): 低频计数阈值。

#### `DPChunkedHistogramRequest`
*   `chunks` (`repeated StringChunk`): 类别型数据块列表。
*   `categories` (`repeated string`): 目标类别集合。
*   `epsilon` (`double`): 隐私预算 ε。
*   `mechanism` (`string`): `"laplace"` 或 `"gaussian"`。
*   `delta` (`double`): 隐私预算 δ。

### LDP 相关 Message 字段

#### `PerturbBinaryBatchRequest`
*   `values` (`repeated int32`): 待扰动二值列表。
*   `epsilon` (`double`): 本地隐私预算。

#### `PerturbBinaryBatchResponse`
*   `results` (`repeated int32`): 扰动后二值结果列表。

#### `PerturbCategoricalBatchRequest`
*   `values` (`repeated string`): 待扰动类别列表。
*   `categories` (`repeated string`): 所有可用类别集合。
*   `epsilon` (`double`): 本地隐私预算。

#### `PerturbCategoricalBatchResponse`
*   `results` (`repeated string`): 扰动后类别结果列表。

#### `EstimateBinaryFrequencyRequest`
*   `reported_values` (`repeated int32`): 已扰动的报告样本列表。
*   `epsilon` (`double`): 扰动时使用的本地隐私预算。

#### `EstimateBinaryFrequencyResponse`
*   `estimated_frequency` (`double`): 估计的真实频率（0~1）。

#### `EstimateCategoricalHistogramRequest`
*   `reported_values` (`repeated string`): 已扰动的报告样本列表。
*   `categories` (`repeated string`): 所有可用类别集合。
*   `epsilon` (`double`): 扰动时使用的本地隐私预算。

#### `EstimateCategoricalHistogramResponse`
*   `estimated_histogram` (`map<string, double>`): 各类别的纠偏估计频率。


---

## 4. 异常与错误码

| 异常/错误 | 触发条件 | HTTP 状态码 | gRPC 状态码 |
|---|---|---|---|
| `ValueError: clip_lower and clip_upper are required for Gaussian mechanism` | Gaussian sum/mean 缺少 clip | 400 | `INVALID_ARGUMENT` |
| `ValueError: chunked_sum requires explicit clip_lower and clip_upper` | chunked sum/mean 缺少 clip | 400 | `INVALID_ARGUMENT` |
| `ValueError: dp_noisy_sum requires 'sensitivity' or both 'clip_lower' and 'clip_upper'` | noisify sum/mean 缺少敏感度 | 400 | `INVALID_ARGUMENT` |
| `ValueError: delta must be positive for Gaussian mechanism` | Gaussian 请求 delta ≤ 0 | 400 | `INVALID_ARGUMENT` |
| `ValueError: sensitivity must be non-negative` | noisify sum/mean 敏感度为负 | 400 | `INVALID_ARGUMENT` |
| `ValueError: column must be specified when input is a pandas DataFrame` | DataFrame 未指定 `column` | 400 | `INVALID_ARGUMENT` |
| `TypeError: Unsupported data type for DP values` | 不支持的 `values` 输入类型 | 400 | `INVALID_ARGUMENT` |
| `PrivacyBudgetExhausted` | 累计预算超过命名空间上限 | 429 | `RESOURCE_EXHAUSTED` |
| `ValueError: mechanism must be 'laplace' or 'gaussian'` | mechanism 参数非法 | 400 | `INVALID_ARGUMENT` |
