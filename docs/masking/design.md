# 数据脱敏设计文档

## 1. 概述

本文档定义 `privacy-local-agent` 数据脱敏模块的算法原理、字段识别规则与实现细节。脱敏模块通过字段名推断敏感类型，并应用格式保留的掩码规则，降低敏感信息泄露风险。

## 2. 设计目标

- 基于字段名关键字匹配自动识别敏感类型。
- 对常见 PII（手机号、身份证、姓名、银行卡）提供默认掩码规则。
- 支持单字段、整记录、批量字段、DataFrame 多种调用方式。
- 提供 HMAC 哈希与字符串截断作为补充工具。
- 暴露 `privacy_masking_operations_total` 指标。

## 3. 字段识别规则

`guess_field_type(field_name)` 通过子串匹配（大小写不敏感）识别类型：

| 字段名关键字 | 识别类型 | 脱敏函数 |
|---|---|---|
| `mobile` / `phone` | `mobile` | `mask_mobile` |
| `id_card` / `idcard` / `身份证` | `id_card` | `mask_id_card` |
| `name` / `姓名` | `name` | `mask_name` |
| `bank` / `card_no` | `bank_card` | `mask_bank_card` |
| 其他 | `default` | `mask_default` |

## 4. 脱敏规则

### 4.1 手机号

保留前 3 位与后 2 位，中间替换为 `****`。

```text
13812345678 -> 138****5678
```

### 4.2 身份证

保留前 6 位与后 4 位，中间替换为 `********`。

```text
110101199001011234 -> 110101********1234
```

### 4.3 姓名

- 2 字姓名：保留首字，后接 `*`。
- 其他：保留首尾字，中间替换为 `**`。

```text
张三 -> 张*
张三丰 -> 张**丰
```

### 4.4 银行卡

保留前 4 位与后 4 位，中间以空格分隔的 `****` 填充。

```text
6222021234567890123 -> 6222 **** **** 0123
```

### 4.5 默认策略

保留前后指定位数（默认各 3 位），中间用 `*` 填充。

## 5. 批量与 DataFrame 支持

### 5.1 批量字段脱敏

`mask_value_batch(field_names, values)` 对两个列表按位置一一对应脱敏。长度不一致时抛出 `ValueError`。

### 5.2 DataFrame 脱敏

`mask_dataframe(df, columns=None)`：

1. 通过 `data_adapters.to_records` 将 pandas / SecretFlow DataFrame 转为记录列表。
2. 若未指定 `columns`，对所有字符串列脱敏。
3. 逐行调用 `mask_value(col, val)`。
4. 通过 `data_adapters.from_records` 转回 pandas DataFrame。

## 6. 指标

`privacy_masking_operations_total{operation}`：

| `operation` | 触发接口 |
|---|---|
| `mask_value` | `mask_value` |
| `mask_record` | `mask_record` |
| `mask_value_batch` | `mask_value_batch` |
| `mask_dataframe` | `mask_dataframe` |
| `hash` | `hash_value` |
| `truncate` | `truncate` |

## 7. 模块设计

- `privacy_local_agent/privacy/masking.py`：核心脱敏逻辑。
- `privacy_local_agent/privacy/data_adapters.py`：DataFrame 与记录列表互转。
- `privacy_local_agent/service.py`：`PrivacyService` 封装。
- `privacy_local_agent/main.py` / `grpc_server.py`：REST / gRPC 接口。

## 8. 测试策略

- 各字段类型脱敏规则单元测试。
- 整记录脱敏不修改原记录测试。
- 批量字段脱敏长度校验测试。
- DataFrame 脱敏列选择与默认列测试。
- HMAC 哈希与截断测试。
- 指标递增测试。
- REST/gRPC 接口测试。
