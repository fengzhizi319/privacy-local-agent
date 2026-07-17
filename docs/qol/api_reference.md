# 查询混淆模块 API 参考

## 1. Python SDK

### `obfuscate_query`

位置：`privacy_local_agent.privacy.qol.obfuscate_query`

```python
def obfuscate_query(
    query: str,
    num_dummies: int = 3,
    domain: str = "medical",
    medical_pool: List[str] = None,
    generic_pool: List[str] = None,
    seed: Optional[int] = None,
) -> List[str]
```

对单个查询进行混淆。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `query` | `str` | 是 | 真实查询 |
| `num_dummies` | `int` | 否 | 虚假查询数量，默认 3 |
| `domain` | `str` | 否 | `"medical"` 或 `"generic"` |
| `medical_pool` | `List[str]` | 否 | 自定义医疗 dummy 池 |
| `generic_pool` | `List[str]` | 否 | 自定义通用 dummy 池 |
| `seed` | `Optional[int]` | 否 | 随机种子 |

### `obfuscate_query_batch`

位置：`privacy_local_agent.privacy.qol.obfuscate_query_batch`

```python
def obfuscate_query_batch(
    queries: List[str],
    num_dummies: int = 3,
    domain: str = "medical",
    medical_pool: List[str] = None,
    generic_pool: List[str] = None,
    seed: Optional[int] = None,
) -> List[List[str]]
```

批量查询混淆。

---

## 2. REST API

### POST `/v1/privacy/qol/obfuscate`

```json
{
  "query": "糖尿病患者用药趋势",
  "num_dummies": 3,
  "domain": "medical"
}
```

响应：

```json
{
  "result": ["虚假查询1", "糖尿病患者用药趋势", "虚假查询2", "虚假查询3"]
}
```

### POST `/v1/privacy/qol/obfuscate/batch`

```json
{
  "queries": ["查询1", "查询2"],
  "num_dummies": 2,
  "domain": "generic"
}
```

响应：

```json
{
  "results": [
    ["查询1", "dummy1", "dummy2"],
    ["dummy3", "查询2", "dummy4"]
  ]
}
```

---

## 3. gRPC API

| 方法 | 请求 | 响应 | 说明 |
|---|---|---|---|
| `ObfuscateQuery` | `ObfuscateQueryRequest` | `ObfuscateQueryResponse` | 单条查询混淆 |
| `ObfuscateQueryBatch` | `ObfuscateQueryBatchRequest` | `ObfuscateQueryBatchResponse` | 批量查询混淆 |

### `ObfuscateQueryRequest` 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `query` | `string` | 真实查询 |
| `num_dummies` | `int32` | 虚假查询数量 |
| `domain` | `string` | 领域 |
| `medical_pool` | `repeated string` | 自定义医疗池 |
| `generic_pool` | `repeated string` | 自定义通用池 |
| `seed` | `int32` | 随机种子 |

### `ObfuscateQueryBatchRequest` 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `queries` | `repeated string` | 真实查询列表 |
| `num_dummies` | `int32` | 虚假查询数量 |
| `domain` | `string` | 领域 |
| `medical_pool` | `repeated string` | 自定义医疗池 |
| `generic_pool` | `repeated string` | 自定义通用池 |
| `seed` | `int32` | 随机种子 |
