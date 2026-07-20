# 差分隐私运维手册

## 1. 调用示例

### Laplace count

```bash
curl -X POST http://127.0.0.1:8079/v1/privacy/dp/count \
  -H "Content-Type: application/json" \
  -d '{"values":[1,0,1,1,0],"params":{"epsilon":1.0}}'
```

### Gaussian sum with clipping

```bash
curl -X POST http://127.0.0.1:8079/v1/privacy/dp/sum \
  -H "Content-Type: application/json" \
  -d '{
    "values":[1,2,3,100],
    "params":{
      "epsilon":1.0,
      "delta":1e-6,
      "mechanism":"gaussian",
      "clip_lower":0.0,
      "clip_upper":10.0
    }
  }'
```

### Mean with low-count threshold

```bash
curl -X POST http://127.0.0.1:8079/v1/privacy/dp/mean \
  -H "Content-Type: application/json" \
  -d '{
    "values":[25,34,45,52,29,61],
    "params":{
      "epsilon":2.0,
      "delta":1e-6,
      "mechanism":"gaussian",
      "clip_lower":0.0,
      "clip_upper":120.0,
      "min_count":3.0
    }
  }'
```

> `min_count` 用于防止噪声计数过小时均值结果发散。当 `noisy_count < min_count` 时接口返回 0.0。

### Histogram

```bash
curl -X POST http://127.0.0.1:8079/v1/privacy/dp/histogram \
  -H "Content-Type: application/json" \
  -d '{
    "values":["A","B","A","C"],
    "categories":["A","B","C","D"],
    "params":{
      "epsilon":10.0,
      "mechanism":"laplace"
    }
  }'
```

### Noisify sum

适用于外部引擎已完成聚合，仅需 sidecar 加噪的场景。

```bash
curl -X POST http://127.0.0.1:8079/v1/privacy/dp/noisy_sum \
  -H "Content-Type: application/json" \
  -d '{
    "true_sum": 5000000.0,
    "params": {
      "epsilon": 1.0,
      "delta": 1e-6,
      "mechanism": "gaussian",
      "sensitivity": 100000.0
    }
  }'
```

### Chunked sum

适用于数据量过大、需要分块传入的场景。

```bash
curl -X POST http://127.0.0.1:8079/v1/privacy/dp/chunked_sum \
  -H "Content-Type: application/json" \
  -d '{
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
  }'
```

### Local DP perturbation (binary)

```bash
curl -X POST http://127.0.0.1:8079/v1/privacy/ldp/perturb/binary \
  -H "Content-Type: application/json" \
  -d '{"values":[1,0,1,1],"epsilon":10.0}'
```

### Local DP perturbation (categorical)

```bash
curl -X POST http://127.0.0.1:8079/v1/privacy/ldp/perturb/categorical \
  -H "Content-Type: application/json" \
  -d '{"values":["A","B","A"],"categories":["A","B","C"],"epsilon":10.0}'
```

### Local DP estimation

```bash
curl -X POST http://127.0.0.1:8079/v1/privacy/ldp/estimate/binary \
  -H "Content-Type: application/json" \
  -d '{"reported_values":[1,1,0,1],"epsilon":5.0}'

curl -X POST http://127.0.0.1:8079/v1/privacy/ldp/estimate/categorical \
  -H "Content-Type: application/json" \
  -d '{"reported_values":["A","B","C","A"],"categories":["A","B","C"],"epsilon":5.0}'
```

## 2. 环境变量

| 变量 | 说明 | 示例 |
|---|---|---|
| `PRIVACY_BUDGET_DB` | SQLite 持久化预算数据库路径 | `/data/budget.db` |
| `PRIVACY_BUDGET_WINDOW_SECONDS` | 隐私预算自动重置时间窗口（秒） | `86400`（每天重置） |

## 3. 参数建议

| 参数 | 建议 |
|---|---|
| `epsilon` | 1.0 为常用默认值；敏感数据建议 0.1~0.5。 |
| `delta` | 必须 `< 1/n^2`；典型值 `1e-6`。 |
| `clip_lower/upper` | 根据业务先验设置；可通过离线分位数估计。 |
| `mechanism` | 小敏感度用 Laplace；需要更小噪声且可接受 delta 时用 Gaussian。Gaussian 默认使用解析高斯机制（Analytic Gaussian）。 |
| `min_count` | mean 查询建议根据最小可接受样本量设置，默认 5.0。 |
| `sensitivity` | noisify sum/mean 必填，通常为 `clip_upper - clip_lower`；也可直接提供 `clip_lower`/`clip_upper` 由 sidecar 计算。 |
| `chunks` | chunked 接口中每个 chunk 为同类型数据列表；sum/mean 必须提供全局 clip 边界。 |
| `column` / `party` | 表格型或 SecretFlow 联邦数据输入时使用。 |
| `window_seconds` | 长期运行 Sidecar 建议设置，例如 86400（每天重置）。 |

## 4. 故障排查

| 现象 | 原因 |
|---|---|
| `400 clip bounds required` | Gaussian sum/mean 未提供 clip。 |
| `400 chunked_sum requires explicit clip_lower and clip_upper` | chunked sum/mean 未提供 clip。 |
| `400 dp_noisy_sum requires 'sensitivity' or both 'clip_lower' and 'clip_upper'` | noisify sum/mean 未提供敏感度或 clip。 |
| `Privacy budget exhausted` | 当前时间窗口内累计 epsilon 或 delta 超过命名空间上限。 |
| `delta must be positive for gaussian` | Gaussian 请求 delta=0。 |
| `column must be specified when input is a pandas DataFrame` | DataFrame 输入未指定 `column`。 |
| mean 返回 0.0 | 噪声计数低于 `min_count` 阈值，触发低频保护。 |

## 5. 安全注意事项

- 当前 Gaussian 机制使用解析高斯机制（Analytic Gaussian），噪声界比经典公式更紧。
- 连续 Laplace/Gaussian 采样基于 Python 浮点数与伪随机数生成器，存在 Mironov 浮点精度攻击的理论风险。高安全场景应评估是否需要迁移到离散机制。
- 预算时间窗口重置可避免长期运行后预算耗尽，但窗口到期前的超支仍会被拒绝。

## 6. 流量监控

REST 中间件与 gRPC 拦截器会自动记录 `privacy_traffic_bytes_total` 指标：

```text
privacy_traffic_bytes_total{method="POST",path="/v1/privacy/dp/sum",direction="request"}
privacy_traffic_bytes_total{method="POST",path="/v1/privacy/dp/sum",direction="response"}
privacy_traffic_bytes_total{method="gRPC",path="/privacy.local.PrivacyService/DPSum",direction="request"}
```

可用于：
- 审计各接口请求/响应体量。
- 发现异常大请求或潜在滥用。
- 容量规划与带宽估算。

> gRPC stream 调用的字节数不可预知，当前实现中 request/response 字节数计为 0。

## 7. 高级特性运维指南

### 7.1 adaptive_clip 运维

- `adaptive_clip` 会消耗全部传入的 epsilon 预算，按 `num_iterations` 次 DP count 拆分。
- 建议 `num_iterations=15`，`target_quantile=0.95`，`initial_clip` 设为预期数据范围的 10 倍。
- 返回的 clip bounds 用于后续聚合调用，后续调用需额外消耗独立预算。
- 若数据范围已知，建议直接指定 clip 区间，跳过 adaptive_clip 以节省预算。

### 7.2 dp_aggregate 运维

- 预算按列数均分：`epsilon_per_col = epsilon / num_specs`。
- 若某些列重要性更高，可拆分为多次 `dp_aggregate` 调用，手动控制预算分配。
- 当前仅支持 pandas DataFrame 输入。

### 7.3 vector_sum / vector_mean 运维

- 推荐 `mechanism="gaussian"`，高维场景下噪声界更紧。
- `max_norm` 应基于梯度分布设定，过小会截断过多信息，过大会增加噪声。
- `vector_mean` 的 `min_count` 用于防止低频数据发散，默认 5.0。

### 7.4 dp_groupby 运维

- 预算按 `(num_groups × 2)` 拆分，分组数越多，每组分配的预算越少。
- 若分组数很大（>100），建议增加总 epsilon 或减少查询精度要求。
- Tau 阈值会自动过滤稀有分组，避免泄漏低频分组信息。

### 7.5 Accumulator 运维

- Worker 端 `create_accumulator` 不消耗预算，仅本地累加。
- Master 端 `finalize_dp` 消耗 epsilon 预算并注入噪声。
- 序列化格式为 JSON bytes，可跨网络传输。
- 合并操作 (`+`) 符合交换律/结合律，支持任意顺序合并。

### 7.6 RDPAccountant 运维

- `RDPAccountant` 是独立工具，不与 `BudgetAccountant` 自动集成。
- 可同时使用两者：`BudgetAccountant` 追踪保守上界，`RDPAccountant` 提供紧致参考估计。
- 默认搜索 11 个 Rényi 阶数，自动选择最优 α 使 ε 最小。
