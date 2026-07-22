# 数据脱敏设计文档

## 1. 概述

本文档定义 `privacy-local-agent` 数据脱敏模块的算法原理、字段识别规则与实现细节。脱敏模块通过字段名推断敏感类型，并应用格式保留的掩码规则，降低敏感信息泄露风险。

## 2. 设计目标

- 基于字段名关键字匹配自动识别敏感类型。
- 对常见 PII（手机号、身份证、姓名、银行卡、邮箱、地址）提供默认掩码规则。
- 支持单字段、整记录、批量字段、DataFrame、流式分块多种调用方式。
- **多格式输入适配**：参考 DP 模块 `extract_values` 设计，统一支持 pandas DataFrame、numpy ndarray、PyArrow Table/RecordBatch、Arrow IPC 字节流、Polars、SecretFlow、list of dict 等多种输入格式。
- 提供 HMAC 哈希与字符串截断作为补充工具。
- 暴露 `privacy_masking_operations_total` 指标。
- **工业化增强**：结构化日志、输入校验、枚举类型安全、向量化批处理。

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
- **性能优化**：使用 Pandas 向量化操作或 PyArrow 列式计算内核提升大数据集处理效率，但仍为逐行独立处理

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
| `mobile` / `phone` / `tel` | `mobile` | `mask_mobile` |
| `id_card` / `idcard` / `身份证` / `identity` | `id_card` | `mask_id_card` |
| `email` / `mail` / `邮箱` | `email` | `mask_email` |
| `addr` / `address` / `地址` | `address` | `mask_address` |
| `name` / `姓名` | `name` | `mask_name` |
| `bank` / `card_no` | `bank_card` | `mask_bank_card` |
| 其他 | `default` | `mask_default` |

### 4.1 枚举类型安全

模块提供 `FieldType` 枚举用于类型安全的字段类型判断：

```python
from privacy_local_agent.privacy.masking import FieldType

assert FieldType.MOBILE == "mobile"
assert FieldType.EMAIL == "email"
```

## 5. 脱敏规则

### 5.1 手机号

保留前 3 位与后 4 位，中间 4 位替换为 `****`。

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

### 5.5 邮箱

保留用户名首尾字符，中间替换为 `***`，域名完整保留。

```text
zhangsan@example.com -> z***n@example.com
ab@test.com -> a***@test.com
```

### 5.6 地址

保留前 6 个字符（通常包含省/市/区信息），剩余部分替换为 `****`。

```text
北京市朝阳区某某街道123号 -> 北京市朝阳区****
```

### 5.7 默认策略

保留前后指定位数（默认各 3 位），中间用 `*` 填充。

## 6. 批量与 DataFrame 支持

### 6.1 批量字段脱敏

`mask_value_batch(field_names, values)` 对两个列表按位置一一对应脱敏。长度不一致时抛出 `ValueError`。

### 6.2 DataFrame 脱敏

`mask_dataframe(df, columns=None)`：

1. **向量化加速（Pandas 专用通道）**：
   - 检测 `df` 是否为 `pandas.DataFrame` 实例。
   - 若是，对需要脱敏的列采用 Pandas 列式向量化 `.apply(lambda v: mask_value(...) if pd.notna(v) else v)`。这避免了行级 Dict 转换开销，在 C 语言层面由 Pandas 引擎执行，有效防止大数据集下的内存溢出。
2. **列式计算加速（PyArrow 专用通道）**：
   - 检测 `df` 是否为 `pyarrow.Table` 或 `pyarrow.RecordBatch` 实例。
   - 若是，通过 `_mask_arrow_column` 利用 `pyarrow.compute` 的 UTF-8 内核（`utf8_slice_codeunits`、`binary_join_element_wise`、`replace_substring_regex` 等）在列式内存中直接完成脱敏。
   - **避免 `to_pylist()` 全量物化**：数据始终留在 Arrow 列式 buffer，无 Python 对象 GC 压力。
   - 返回值保持为 `pyarrow.Table`，调用方可继续用于 Arrow 生态下游处理。
   - 支持 null 值透传：null 元素在脱敏后仍为 null。
3. **多格式输入适配（统一转换通道）**：
   - 对于非 pandas / 非 PyArrow 输入，通过 `_convert_to_records` 统一转换为记录列表。
   - 支持的格式包括：
     - **numpy ndarray**（1-D 或 2-D）：按列名或自动列名构建记录
     - **Arrow IPC Stream 字节流**（bytes/bytearray）：解析后转换为记录
     - **Polars DataFrame**：调用 `to_dicts()` 转换
     - **SecretFlow DataFrame**：通过 `data_adapters.to_records` 转换
     - **list of dict**：直接作为记录列表使用
4. **平滑降级（兜底通道）**：
   - 对于不支持的格式，抛出 `TypeError` 并提供清晰的错误信息。

### 6.3 整记录脱敏

`mask_record(record)` 支持多种单行数据格式输入：
- **dict**：直接作为记录处理
- **bytes/bytearray**：解析为 Arrow IPC Stream 并取第一行
- **PyArrow Table / RecordBatch**：提取第一行为字典
- **numpy ndarray**：1-D 数组按 `{col_0: val, ...}` 构建记录；2-D 取第一行
- **pandas Series**：调用 `to_dict()` 转换
- **Polars Series**：调用 `to_dict()` 转换

### 6.4 流式分块脱敏

`chunked_mask_records(chunks)` 的每个 chunk 支持多种数据格式（参考 6.2 节），通过 `_convert_to_records` 统一转换为记录列表后处理。

## 7. 指标

`privacy_masking_operations_total{operation}`：

| `operation` | 触发接口 |
|---|---|
| `mask_value` | `mask_value` |
| `mask_record` | `mask_record` |
| `mask_value_batch` | `mask_value_batch` |
| `mask_dataframe` | `mask_dataframe` |
| `hash_value` | `hash_value` |
| `truncate` | `truncate` |
| `chunked_mask_records` | `chunked_mask_records` |

## 8. 工业化增强特性

### 8.1 结构化日志

模块使用 `get_logger(__name__)` 创建结构化日志记录器，每次操作记录上下文信息：

```python
logger.info(
    "mask_value_batch_completed",
    extra={"num_fields": len(field_names), "context": context},
)
```

### 8.2 输入校验

所有公开接口均内置参数校验，快速失败并给出清晰错误信息：

- `mask_value`: 校验 field_name 非空、value 为字符串
- `hash_value`: 校验 salt 非空
- `truncate`: 校验 keep_prefix 非负
- `mask_record`: 校验 record 为非空字典
- `mask_value_batch`: 校验列表非空且长度一致

### 8.3 枚举类型安全

提供 `MaskingOperation` 枚举用于操作类型标识：

```python
from privacy_local_agent.privacy.masking import MaskingOperation

assert MaskingOperation.MASK_VALUE == "mask_value"
assert MaskingOperation.HASH_VALUE == "hash_value"
```

## 9. 模块设计

- `privacy_local_agent/privacy/masking.py`：核心脱敏逻辑。
  - `_mask_arrow_column`：PyArrow 列级向量化脱敏内核（`pyarrow.compute` UTF-8 算子）。
  - `_coerce_to_dict`：单行多格式输入转 dict。
  - `_convert_to_records`：多行多格式输入转记录列表。
- `privacy_local_agent/privacy/data_adapters.py`：DataFrame 与记录列表互转。
- `privacy_local_agent/service.py`：`PrivacyService` 封装。
- `privacy_local_agent/main.py` / `grpc_server.py`：REST / gRPC 接口。

## 10. 测试策略

- 各字段类型脱敏规则单元测试（含 email、address）。
- 整记录脱敏不修改原记录测试。
- 批量字段脱敏长度校验测试。
- DataFrame 脱敏列选择与默认列测试。
- **PyArrow 列式计算快速路径测试**：验证返回类型为 `pa.Table`、null 透传、RecordBatch 输入、columns 过滤。
- HMAC 哈希与截断测试。
- 指标递增测试。
- REST/gRPC 接口测试。
- **枚举类型测试**：FieldType、MaskingOperation 枚举值验证。
- **输入校验测试**：空值、非法类型、边界条件测试。

---

## 11. 工业化评分 / Industrialization Scorecard

| 文件 | 日志 | 指标 | 文档 | 校验 | 规范 | 总分 | 状态 |
|------|------|------|------|------|------|------|------|
| `masking.py` | 5 | 5 | 5 | 5 | 5 | **25/25** | ✅ 标杆 |
