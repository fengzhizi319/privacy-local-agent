# 差分隐私算法正确性设计文档

## 1. 机制定义

### 1.1 Laplace 机制

对函数 `f`，噪声尺度：

```
scale = L1_sensitivity / epsilon
```

- count: `L1_sensitivity = 1`
- sum: `L1_sensitivity = clip_upper - clip_lower`
- mean: 使用组合定理，拆分为 `count(epsilon/2)` + `sum(epsilon/2)`，再相除。

### 1.2 Gaussian 机制

标准 DP 噪声尺度（Dwork & Roth 附录 A）：

```
sigma = sqrt(2 * ln(1.25 / delta)) * L2_sensitivity / epsilon
```

- count: `L2_sensitivity = 1`
- sum: `L2_sensitivity = clip_upper - clip_lower`
- mean: 拆分为 `count(epsilon/2, delta/2)` + `sum(epsilon/2, delta/2)`。

## 2. 模块改动

### 2.1 `privacy_local_agent/privacy/dp.py`

- 新增 `_sample_gaussian(sigma)`。
- `count/sum/mean` 签名扩展为接受 `delta`, `clip_lower`, `clip_upper`。
- `sum` 先对 `values` 进行 clipping，再计算真实和与敏感度。
- `mean` 组合调用带 clipping 的 `count` 与 `sum`。
- `mechanism` 校验为 `laplace` 或 `gaussian`。

### 2.2 `privacy_local_agent/service.py`

- `dp_count/dp_sum/dp_mean` 从解析后的参数中传递 `delta`, `clip_lower`, `clip_upper`。

### 2.3 proto / REST / gRPC

- `DPRequest` 增加 `delta`, `clip_lower`, `clip_upper`。
- REST `DPRequest.params` 透传上述字段。

## 3. 预算消耗

- Laplace：`spend(epsilon, 0.0)`
- Gaussian：`spend(epsilon, delta)`
- mean 组合：分别对 count 与 sum 调用，总消耗为 `(epsilon, delta)`。

## 4. 安全与兼容性

- 默认 `mechanism=laplace`，`delta=0`，保持旧请求行为不变。
- 对 sum/mean，如果 clip 参数未提供，Laplace 仍回退到数据推断敏感度，但记录 warning；Gaussian 下必须提供 clip。
