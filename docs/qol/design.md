# 查询混淆设计文档

## 1. 概述

本文档定义 `privacy-local-agent` 查询混淆模块的算法原理与实现细节。查询混淆通过将真实查询与虚假查询混合，降低查询日志分析攻击导致的隐私泄露风险。

## 2. 设计目标

- 将单个真实查询混入若干虚假查询。
- 支持批量查询混淆。
- 提供可扩展的 dummy 查询池机制。
- 暴露 `privacy_qol_operations_total` 指标。

## 3. 算法原理

1. 根据 `domain` 选择 dummy 查询池。
2. 从池中随机抽取 `num_dummies` 条虚假查询（允许重复）。
3. 在 `[0, num_dummies]` 范围内随机选择插入位置。
4. 将真实查询插入该位置，返回长度为 `num_dummies + 1` 的列表。

> 当前为 POC/MVP 级别保护，主要防御基于简单查询日志分析的推断攻击；对高级对手可能需要更复杂的混淆策略。

## 4. Dummy 查询池

### 4.1 内置池

- **medical**：高血压、糖尿病、冠心病、流感疫苗、儿童过敏等医疗相关查询。
- **generic**：天气预报、医院挂号、健康档案、医保报销、体检报告等通用查询。

### 4.2 自定义池

通过 `medical_pool` / `generic_pool` 参数传入自定义列表，覆盖内置池。

## 5. 批量混淆

`obfuscate_query_batch(queries, ...)` 对每个查询独立调用 `obfuscate_query`，返回列表的列表。

## 6. 指标

`privacy_qol_operations_total{domain}`：

| `domain` | 触发场景 |
|---|---|
| `medical` | `domain="medical"` 时 |
| `generic` | `domain="generic"` 或默认其他值时 |

## 7. 模块设计

- `privacy_local_agent/privacy/qol.py`：核心混淆逻辑。
- `privacy_local_agent/service.py`：`PrivacyService` 封装。
- `privacy_local_agent/main.py` / `grpc_server.py`：REST / gRPC 接口。

## 8. 测试策略

- 单条查询混淆结果包含真实查询。
- 批量查询混淆返回数量正确。
- 自定义 pool 生效。
- `seed` 参数可复现结果。
- 指标递增测试。
- REST/gRPC 接口测试。
