# 差分隐私（DP）算法正确性 PRD

## 1. 背景

当前 DP 实现仅支持 Laplace 机制，且存在以下问题：
- sum 的敏感度从输入数据推断，违反差分隐私的敏感度应在看到数据前确定的原则。
- 没有 clipping，异常值会放大噪声尺度。
- delta 参数未参与预算消耗，Gaussian 机制无法使用。

## 2. 目标

- 引入显式 `clip_lower` / `clip_upper` 参数，sum/mean 必须基于 clip 区间计算敏感度。
- 新增 Gaussian 机制，正确消耗 `(epsilon, delta)` 预算。
- 保持 Laplace 机制向后兼容。

## 3. 功能需求

| ID | 需求 |
|---|---|
| DP-CLIP-1 | sum/mean 支持 `clip_lower`、`clip_upper`，默认从 profile 读取。 |
| DP-CLIP-2 | 当 clip 参数未提供且未在 profile 配置时，对 sum/mean 抛出错误。 |
| DP-GAUSS-1 | 支持 `mechanism=gaussian`，使用标准 Gaussian 机制噪声尺度。 |
| DP-GAUSS-2 | Gaussian 机制必须传入 `delta > 0`，并消耗对应 delta 预算。 |
| DP-BUDGET-1 | count 的敏感度为 1；sum 的 L2 敏感度为 `clip_upper - clip_lower`；mean 的 L2 敏感度为 `(clip_upper - clip_lower) / n`。 |
| DP-BACKWARD-1 | 未提供 clip 时，Laplace 机制仍可使用数据推断敏感度（兼容旧接口），但文档标记为不推荐。 |

## 4. 接口定义

REST 请求参数在 `params` 中新增字段：

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

gRPC `DPRequest` 新增 `delta`、`clip_lower`、`clip_upper`。

## 5. 验收标准

- [ ] clipping 与 Gaussian 机制单元测试通过。
- [ ] delta 预算正确消耗测试通过。
- [ ] REST/gRPC 接口支持新参数。
- [ ] 文档与 `AGENTS.md` 已更新。
