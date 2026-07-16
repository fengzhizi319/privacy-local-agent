# 差分隐私（DP）产品设计 PRD

## 1. 概述

本文档定义 `privacy-local-agent` 差分隐私（DP）模块的产品需求与验收标准。DP 模块为 count、sum、mean 等聚合查询提供隐私化输出能力，并通过隐私预算 accountant 控制累计披露风险。

## 2. 设计目标

- 提供基于 Laplace 机制的纯 `ε-DP` 统计查询。
- 提供基于 Gaussian 机制的 `(ε, δ)-DP` 统计查询。
- 提供基于随机响应的本地差分隐私（Local DP）扰动与频率估计。
- 通过显式 clipping 控制 sum/mean 的敏感度。
- 提供 BudgetAccountant 统一追踪 `(ε, δ)` 消耗。
- 暴露一致的 REST 与 gRPC 接口。

## 3. 功能需求

| ID | 需求 |
|---|---|
| DP-COUNT-1 | count 查询敏感度固定为 1，支持 Laplace 与 Gaussian 机制。 |
| DP-SUM-1 | sum 查询必须提供 `clip_lower` / `clip_upper`，敏感度按 clip 区间计算。 |
| DP-MEAN-1 | mean 查询必须提供 `clip_lower` / `clip_upper`，敏感度按 clip 区间与记录数计算。 |
| DP-CLIP-1 | clip 参数可由请求 `params` 传入，也可从 profile 配置读取。 |
| DP-CLIP-2 | sum/mean 若未提供 clip 参数且 profile 未配置，则返回明确错误。 |
| DP-GAUSS-1 | 支持 `mechanism=gaussian`，使用标准 Gaussian 机制。 |
| DP-GAUSS-2 | Gaussian 机制必须传入 `delta > 0`，并消耗对应 delta 预算。 |
| DP-LAPLACE-1 | 支持 `mechanism=laplace`，提供纯 ε-DP 保证。 |
| DP-LOCAL-1 | 提供二值随机响应（Binary Randomized Response），支持单个值与批量扰动。 |
| DP-LOCAL-2 | 提供类别型随机响应（k-ary Randomized Response），支持单个值与批量扰动。 |
| DP-LOCAL-3 | 提供基于扰动样本的二值频率估计与类别直方图估计。 |
| DP-BUDGET-1 | 提供 BudgetAccountant，按 namespace 追踪总 `(ε, δ)` 消耗。 |
| DP-BUDGET-2 | 支持内存与 SQLite 两种预算存储后端。 |
| DP-BUDGET-3 | 预算一旦消耗即不可回退。 |
| DP-DATASET-1 | DP 接口以**聚合查询**为单位，不直接对整个数据表（如 CSV）做中心式 DP 加噪；调用方需按列提取字段值作为 `values`，并显式指定查询类型（count/sum/mean）与 clip 参数。 |

## 4. 接口定义

### 4.1 REST 请求示例

`values` 是**单个字段（列）**的样本值列表，而不是整张数据表。对 CSV 等数据表做 DP 查询时，调用方应先按列提取数据，再针对具体聚合查询调用对应接口。

```json
{
  "values": [1.0, 2.0, 3.0],
  "params": {
    "epsilon": 1.0,
    "delta": 1e-6,
    "mechanism": "gaussian",
    "clip_lower": 0.0,
    "clip_upper": 10.0
  }
}
```

例如，对 `data.csv` 的 `salary` 列求和：

```python
import pandas as pd
import requests

df = pd.read_csv("data.csv")
resp = requests.post(
    "http://127.0.0.1:8079/v1/privacy/dp/sum",
    json={
        "values": df["salary"].tolist(),
        "params": {
            "epsilon": 1.0,
            "delta": 1e-6,
            "mechanism": "gaussian",
            "clip_lower": 0.0,
            "clip_upper": 100000.0,
        },
    },
)
```

### 4.2 gRPC 字段

`DPRequest` 包含 `epsilon`、`delta`、`mechanism`、`clip_lower`、`clip_upper`。

## 5. 隐私预算设定指南

### 5.1 ε 取值参考

| 场景 | 推荐 ε |
|---|---|
| 高隐私（医疗、金融） | 0.1 ~ 1.0 |
| 通用数据发布 | 1.0 ~ 3.0 |
| 统计聚合/低敏感度 | 3.0 ~ 10.0 |

### 5.2 δ 取值参考

- 一般规则：`δ < 1 / n`，`n` 为数据集大小。
- 常见默认值：`1e-5`、`1e-6`。

### 5.3 预算分配建议

1. 确定总预算 `total_epsilon` 与 `total_delta`。
2. 按查询次数拆分或按业务重要性加权分配。
3. 纯 ε-DP 场景选择 Laplace；需要更紧致组合分析时选择 Gaussian。
4. clip 区间基于业务先验预先设定。

## 6. 验收标准

- [ ] count/sum/mean 的 Laplace 与 Gaussian 机制单元测试通过。
- [ ] 本地 DP 二值/类别型随机响应与频率估计测试通过。
- [ ] clipping 参数校验与敏感度计算测试通过。
- [ ] delta 预算正确消耗与超支拒绝测试通过。
- [ ] REST/gRPC 接口支持新参数。
- [ ] 文档（PRD/design/ops/examples/testing）与 `AGENTS.md` 已更新。
