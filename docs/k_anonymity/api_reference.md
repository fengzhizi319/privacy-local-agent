# K-匿名模块 API 参考

## 1. Python SDK

### `k_anonymize_table`

位置：`privacy_local_agent.privacy.kano_table.k_anonymize_table`

对整张表执行 Mondrian 多维分区 K-匿名泛化。

```python
def k_anonymize_table(
    rows: List[Dict[str, Any]],
    qi_cols: List[str],
    k: int = 5,
    max_depth: int = 10,
) -> List[Dict[str, Any]]
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `rows` | `List[Dict[str, Any]]` | 是 | 原始记录列表 |
| `qi_cols` | `List[str]` | 是 | 准标识符列名列表 |
| `k` | `int` | 否 | K-匿名阈值，每个等价组至少包含 `k` 条记录，默认 `5` |
| `max_depth` | `int` | 否 | 最大递归深度，默认 `10` |

**返回值**：泛化后的记录列表（顺序可能与输入不同）。

**异常**：
- `ValueError`: 输入记录数不足 `k`。
- `ValueError`: `qi_cols` 包含输入中不存在的列。
- `ValueError`: `qi_cols` 为空。

**泛化规则**：
- 数值型 QI：输出区间字符串，如 `[25-30]`；当组内取值全部相同时保持原值。
- 分类型 QI：输出取值集合字符串，如 `{M,F}`；当组内取值全部相同时保持原值。
- 非 QI 字段：原样保留。

---

### `anonymize_record`

位置：`privacy_local_agent.privacy.kano.anonymize_record`

对单条记录按内置泛化层次结构进行启发式泛化。

```python
def anonymize_record(
    record: Dict[str, Any],
    qi_cols: List[str],
    hierarchies: Dict[str, GeneralizationHierarchy],
    k: int,
) -> Dict[str, Any]
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `record` | `Dict[str, Any]` | 是 | 原始记录字典 |
| `qi_cols` | `List[str]` | 是 | 准标识符列名列表 |
| `hierarchies` | `Dict[str, GeneralizationHierarchy]` | 是 | 列名到泛化层次函数的映射 |
| `k` | `int` | 是 | K-匿名参数，用于决定泛化层级 |

**返回值**：泛化后的新记录字典（不修改原始字典）。

> 当前 MVP 版本主要依赖 `BUILTIN_HIERARCHIES`；自定义层次结构参数已预留，但尚未实际合并到内置层次中。

---

### `BUILTIN_HIERARCHIES`

位置：`privacy_local_agent.privacy.kano.BUILTIN_HIERARCHIES`

内置准标识符泛化层次结构映射表，包含 `age`、`zipcode`、`gender` 三个字段。

| 字段 | 层级 | 说明 |
|---|---|---|
| `age` | 0 | 原始值 |
| | 1 | 5 岁区间，如 `[25-30]` |
| | 2 | 10 岁区间，如 `[20-30]` |
| | 3 | 20 岁区间，如 `[20-40]` |
| | ≥4 | `*` |
| `zipcode` | 0 | 原始值 |
| | 1 | 保留前 3 位，如 `518***` |
| | 2 | 保留前 2 位，如 `51****` |
| | 3 | 保留前 1 位，如 `5*****` |
| | ≥4 或长度不足 | `*` |
| `gender` | 0 | 原始值 |
| | ≥1 | `*` |

泛化层级由 `choose_level(k, max_level)` 启发式决定：`level = max(1, min(k // 5, max_level))`。

---

## 2. REST API

### POST `/v1/privacy/k_anonymize/record`

单条记录 K-匿名泛化。

请求体：

```json
{
  "record": {"age": "28", "zipcode": "518057", "gender": "女", "disease": "胃癌"},
  "qi_cols": ["age", "zipcode", "gender"],
  "k": 5
}
```

响应体：

```json
{
  "result": {
    "age": "[25-30]",
    "zipcode": "518***",
    "gender": "*",
    "disease": "胃癌"
  }
}
```

### POST `/v1/privacy/k_anonymize/table`

整张表 K-匿名泛化。

请求体：

```json
{
  "rows": [
    {"age": 25, "zipcode": "100001", "gender": "M", "disease": "A"},
    {"age": 26, "zipcode": "100002", "gender": "M", "disease": "B"},
    {"age": 27, "zipcode": "100003", "gender": "M", "disease": "C"}
  ],
  "qi_cols": ["age", "zipcode", "gender"],
  "k": 3,
  "max_depth": 10
}
```

响应体：

```json
{
  "result": [
    {"age": "[25-27]", "zipcode": "{100001,100002,100003}", "gender": "M", "disease": "A"},
    {"age": "[25-27]", "zipcode": "{100001,100002,100003}", "gender": "M", "disease": "B"},
    {"age": "[25-27]", "zipcode": "{100001,100002,100003}", "gender": "M", "disease": "C"}
  ]
}
```

---

## 3. gRPC API

### 方法列表

| 方法 | 请求 | 响应 | 说明 |
|---|---|---|---|
| `KAnonymizeRecord` | `KAnonymizeRequest` | `KAnonymizeResponse` | 单条记录泛化 |
| `KAnonymizeTable` | `KAnonymizeTableRequest` | `KAnonymizeTableResponse` | 整张表泛化 |

### `KAnonymizeRequest` 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `record` | `map<string, string>` | 原始记录 |
| `qi_cols` | `repeated string` | 准标识符列名 |
| `k` | `int32` | K-匿名阈值 |

### `KAnonymizeResponse` 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `result` | `map<string, string>` | 泛化后的记录 |

### `KAnonymizeTableRequest` 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `rows` | `repeated RecordEntry` | 原始记录列表 |
| `qi_cols` | `repeated string` | 准标识符列名 |
| `k` | `int32` | K-匿名阈值 |
| `max_depth` | `int32` | 最大递归深度 |

### `KAnonymizeTableResponse` 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `rows` | `repeated RecordEntry` | 泛化后的记录列表 |

### `RecordEntry` 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `fields` | `map<string, string>` | 单条记录的字段键值对 |

---

## 4. 异常与错误码

| 异常/错误 | 触发条件 | HTTP 状态码 | gRPC 状态码 |
|---|---|---|---|
| `ValueError: Input table has N rows, but k-anonymity requires at least k` | 表级输入记录数 < `k` | 400 | `INVALID_ARGUMENT` |
| `ValueError: qi_cols must not be empty` | `qi_cols` 为空 | 400 | `INVALID_ARGUMENT` |
| `ValueError: qi_cols not found in rows: [...]` | `qi_cols` 包含不存在列 | 400 | `INVALID_ARGUMENT` |
