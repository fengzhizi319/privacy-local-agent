# 数据脱敏模块 API 参考

## 1. Python SDK

### `mask_value`

位置：`privacy_local_agent.privacy.masking.mask_value`

```python
def mask_value(field_name: str, value: str, context: str = "") -> str
```

根据字段名推断敏感类型并脱敏。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `field_name` | `str` | 是 | 字段名 |
| `value` | `str` | 是 | 原始值 |
| `context` | `str` | 否 | 上下文信息，预留 |

### `mask_record`

位置：`privacy_local_agent.privacy.masking.mask_record`

```python
def mask_record(record: Dict[str, str], context: str = "") -> Dict[str, str]
```

对记录字典中的每个字符串值按字段名脱敏。

### `mask_value_batch`

位置：`privacy_local_agent.privacy.masking.mask_value_batch`

```python
def mask_value_batch(
    field_names: List[str], values: List[str], context: str = ""
) -> List[str]
```

批量字段脱敏。`field_names` 与 `values` 长度必须一致。

### `mask_dataframe`

位置：`privacy_local_agent.privacy.masking.mask_dataframe`

```python
def mask_dataframe(
    df: Any,
    columns: Optional[List[str]] = None,
    context: str = "",
) -> Any
```

对 DataFrame 中的指定列脱敏。支持 pandas / SecretFlow DataFrame。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `df` | `Any` | 是 | 输入 DataFrame |
| `columns` | `Optional[List[str]]` | 否 | 目标列名；None 则对所有字符串列脱敏 |
| `context` | `str` | 否 | 上下文信息 |

### `hash_value`

位置：`privacy_local_agent.privacy.masking.hash_value`

```python
def hash_value(value: str, salt: str) -> str
```

HMAC-SHA256 哈希，输出 16 位 base64 摘要。

### `truncate`

位置：`privacy_local_agent.privacy.masking.truncate`

```python
def truncate(value: str, keep_prefix: int) -> str
```

保留前 `keep_prefix` 位并追加 `***`。

---

## 2. REST API

### POST `/v1/privacy/mask`

```json
{
  "field_name": "mobile",
  "value": "13812345678"
}
```

响应：

```json
{
  "result": "138****5678"
}
```

### POST `/v1/privacy/mask_record`

```json
{
  "record": {"mobile": "13812345678", "name": "张三丰"}
}
```

### POST `/v1/privacy/mask/batch`

```json
{
  "field_names": ["mobile", "name"],
  "values": ["13812345678", "张三丰"]
}
```

响应：

```json
{
  "result": ["138****5678", "张**丰"]
}
```

### POST `/v1/privacy/mask/dataframe`

```json
{
  "data": [
    {"mobile": "13812345678", "name": "张三"}
  ],
  "columns": ["mobile", "name"]
}
```

响应：

```json
{
  "result": [
    {"mobile": "138****5678", "name": "张*"}
  ]
}
```

### POST `/v1/privacy/hash`

```json
{
  "value": "hello",
  "salt": "salt"
}
```

---

## 3. gRPC API

| 方法 | 请求 | 响应 | 说明 |
|---|---|---|---|
| `Mask` | `MaskRequest` | `MaskResponse` | 单字段脱敏 |
| `MaskRecord` | `MaskRecordRequest` | `MaskRecordResponse` | 整记录脱敏 |
| `MaskBatch` | `MaskBatchRequest` | `MaskBatchResponse` | 批量字段脱敏 |
| `MaskDataFrame` | `MaskDataFrameRequest` | `MaskDataFrameResponse` | DataFrame 脱敏 |
| `Hash` | `HashRequest` | `HashResponse` | HMAC 哈希 |

---

## 4. 异常与错误码

| 异常/错误 | 触发条件 | HTTP 状态码 | gRPC 状态码 |
|---|---|---|---|
| `ValueError: field_names and values must have the same length` | 批量脱敏长度不一致 | 400 | `INVALID_ARGUMENT` |
| `TypeError: Unsupported table input type: ...` | DataFrame 类型不支持 | 400 | `INVALID_ARGUMENT` |
