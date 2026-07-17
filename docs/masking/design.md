# 数据脱敏设计文档

## 1. 概述

本文档定义 `privacy-local-agent` 数据脱敏模块的算法原理、字段识别规则与实现细节。脱敏模块通过字段名推断敏感类型，并应用格式保留的掩码规则,降低敏感信息泄露风险。

## 2. 设计目标

- 基于字段名关键字匹配自动识别敏感类型。
- 对常见 PII（手机号、身份证、姓名、银行卡）提供默认掩码规则。
- 支持单字段、整记录、批量字段、DataFrame 多种调用方式。
- 提供 HMAC 哈希与字符串截断作为补充工具。
- 暴露 `privacy_masking_operations_total` 指标。

## 3. 应用场景

### 3.1 适用场景：单条数据实时脱敏

本模块**主要面向单条数据或流式数据的实时脱敏**，典型应用场景包括：

1. **API 响应脱敏**：在 REST/gRPC 接口返回前对用户敏感信息脱敏
2. **日志脱敏**：在写入日志前对请求参数中的敏感字段脱敏
3. **消息队列处理**：消费消息时对单条消息的敏感字段脱敏
4. **流式数据处理**：对实时流入的数据逐条脱敏后转发
5. **前端展示脱敏**：用户界面展示时隐藏部分敏感信息

### 3.2 批量处理能力

虽然模块提供了 `mask_value_batch` 和 `mask_dataframe` 等批量接口，但本质上是**对多条单条记录的并行处理**，而非数据库级的批量匿名化：

- **mask_value_batch**：对多个独立字段值并行脱敏，各字段之间无关联
- **mask_dataframe**：对 DataFrame 中每行记录独立脱敏，行间不产生联动
- **性能优化**：使用 Pandas 向量化操作提升大数据集处理效率，但仍为逐行独立处理

### 3.3 不适用场景：数据库级 K-匿名

如需对**整个数据集进行 K-匿名化处理**（保证每个等价类至少 k 条记录），应使用专门的 [K-匿名模块](../k_anonymity/design.md)：

| 特性 | 脱敏模块 (masking) | K-匿名模块 (kano) |
|---|---|---|
| 处理方式 | 逐条独立处理 | 全局分析后泛化 |
| 隐私保证 | 格式保留掩码 | K-匿名性保证 |
| 适用场景 | 实时流式处理 | 批量数据发布 |
| 算法复杂度 | O(n) | O(n log n) |
| 数据可用性 | 降低（部分字符替换） | 可控（层级泛化） |

### 3.4 选择建议

- **选择脱敏模块**：需要快速处理单条/流式数据、仅需格式保留掩码、无需严格隐私模型保证
- **选择 K-匿名模块**：需要对整个数据集进行隐私保护、要求严格的 K-匿名性保证、用于数据发布或共享

## 4. 字段识别规则

`guess_field_type(field_name)` 通过子串匹配（大小写不敏感）识别类型：

| 字段名关键字 | 识别类型 | 脱敏函数 |
|---|---|---|
| `mobile` / `phone` | `mobile` | `mask_mobile` |
| `id_card` / `idcard` / `身份证` | `id_card` | `mask_id_card` |
| `name` / `姓名` | `name` | `mask_name` |
| `bank` / `card_no` | `bank_card` | `mask_bank_card` |
| 其他 | `default` | `mask_default` |

## 5. 脱敏规则

### 5.1 手机号

保留前 3 位与后 2 位，中间替换为 `****`。

```text
13812345678 -> 138****5678
```

### 5.2 身份证

保留前 6 位与后 4 位，中间替换为 `********`。

```text
110101199001011234 -> 110101********1234
```

### 5.3 姓名

- 2 字姓名：保留首字，后接 `*`。
- 其他：保留首尾字，中间替换为 `**`。

```text
张三 -> 张*
张三丰 -> 张**丰
```

### 5.4 银行卡

保留前 4 位与后 4 位，中间以空格分隔的 `****` 填充。

```text
6222021234567890123 -> 6222 **** **** 0123
```

### 5.5 默认策略

保留前后指定位数（默认各 3 位），中间用 `*` 填充。

## 6. 批量与 DataFrame 支持

### 6.1 批量字段脱敏

`mask_value_batch(field_names, values)` 对两个列表按位置一一对应脱敏。长度不一致时抛出 `ValueError`。

### 6.2 DataFrame 脱敏

`mask_dataframe(df, columns=None)`：

1. **向量化加速（Pandas 专用通道）**：
   - 检测 `df` 是否为 `pandas.DataFrame` 实例。
   - 若是，对需要脱敏的列采用 Pandas 列式向量化 `.apply(lambda v: mask_value(...) if pd.notna(v) else v)`。这避免了行级 Dict 转换开销，在 C 语言层面由 Pandas 引擎执行，有效防止大数据集下的内存溢出。
2. **平滑降级（兜底通道）**：
   - 对于非 `pandas.DataFrame` 或未安装 Pandas 的环境，平滑降级为先通过 `data_adapters.to_records` 转化为 Dict 记录列表，逐行调用 `mask_value` 脱敏，再通过 `from_records` 还原为原 DataFrame 的处理模式。

## 7. 指标

`privacy_masking_operations_total{operation}`：

| `operation` | 触发接口 |
|---|---|
| `mask_value` | `mask_value` |
| `mask_record` | `mask_record` |
| `mask_value_batch` | `mask_value_batch` |
| `mask_dataframe` | `mask_dataframe` |
| `hash` | `hash_value` |
| `truncate` | `truncate` |

## 8. 模块设计

- `privacy_local_agent/privacy/masking.py`：核心脱敏逻辑。
- `privacy_local_agent/privacy/data_adapters.py`：DataFrame 与记录列表互转。
- `privacy_local_agent/service.py`：`PrivacyService` 封装。
- `privacy_local_agent/main.py` / `grpc_server.py`：REST / gRPC 接口。

## 9. 测试策略

- 各字段类型脱敏规则单元测试。
- 整记录脱敏不修改原记录测试。
- 批量字段脱敏长度校验测试。
- DataFrame 脱敏列选择与默认列测试。
- HMAC 哈希与截断测试。
- 指标递增测试。
- REST/gRPC 接口测试。
