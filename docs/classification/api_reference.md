# 数据分类分级模块 API 参考

## 1. Python SDK

### 1.1 `ClassificationAPI`

位置：`privacy_local_agent.privacy.classification.ClassificationAPI`

数据分类原语核心入口类，内置默认规则引擎，可插拔 Small-NER 与 LLM 分类器，支持字段级、记录级、表级分类及多种输入格式适配。

#### 构造函数

```python
ClassificationAPI(
    profile_path: Optional[str] = None,
    rule_engine: Optional[RuleEngine] = None,
    small_ner: Optional[SmallNerEngine] = None,
    llm: Optional[LlmClassifier] = None,
    resolver: Optional[ParameterResolver] = None,
)
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `profile_path` | `Optional[str]` | 否 | YAML 配置文件路径，用于覆盖默认参数 |
| `rule_engine` | `Optional[RuleEngine]` | 否 | 规则引擎实例，默认 `DefaultRuleEngine` |
| `small_ner` | `Optional[SmallNerEngine]` | 否 | Small-NER 引擎实例，默认自动检测本地模型并降级 |
| `llm` | `Optional[LlmClassifier]` | 否 | LLM 分类器实例，默认自动检测并降级 |
| `resolver` | `Optional[ParameterResolver]` | 否 | 共享的参数解析器 |

#### `classify_field`

```python
classify_field(
    field_name: str,
    value: Any,
    params: Optional[Dict[str, Any]] = None,
) -> FieldClassificationResult
```

对单个字段进行分类。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `field_name` | `str` | 是 | 字段名 |
| `value` | `Any` | 是 | 字段值，会被转换为字符串处理 |
| `params` | `Optional[Dict[str, Any]]` | 否 | 请求级参数，可覆盖默认与 profile 配置 |

**返回值**：`FieldClassificationResult`，包含标签、最终等级、置信度、引擎层级、人工复核标志与推理说明。

---

#### `classify_record`

```python
classify_record(
    record: Dict[str, Any],
    params: Optional[Dict[str, Any]] = None,
    record_index: int = 0,
) -> RecordClassificationResult
```

对单条记录进行分类。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `record` | `Dict[str, Any]` | 是 | 字段名到字段值的映射 |
| `params` | `Optional[Dict[str, Any]]` | 否 | 请求级参数 |
| `record_index` | `int` | 否 | 记录索引，用于输出，默认 `0` |

**返回值**：`RecordClassificationResult`，聚合字段结果与标签，含复合规则后处理结果。

---

#### `classify_table`

```python
classify_table(
    schema: List[str],
    rows: List[Dict[str, Any]],
    params: Optional[Dict[str, Any]] = None,
) -> TableClassificationResult
```

对整张表进行分类。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema` | `List[str]` | 是 | 列名列表，决定输出顺序 |
| `rows` | `List[Dict[str, Any]]` | 是 | 记录列表，每条记录是字段名到字段值的字典 |
| `params` | `Optional[Dict[str, Any]]` | 否 | 请求级参数 |

**返回值**：`TableClassificationResult`，聚合记录结果，可含 `shadow_diff` 与 `review_entries`。

---

#### `classify_json`

```python
classify_json(
    json_input: Any,
    params: Optional[Dict[str, Any]] = None,
) -> ClassificationResult
```

解析 JSON 字符串或字典并分类。顶层为 `dict` 时按单条记录分类；为 `list` 时按表分类（schema 取并集）。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `json_input` | `Any` | 是 | JSON 字符串、字典或列表 |
| `params` | `Optional[Dict[str, Any]]` | 否 | 请求级参数 |

**返回值**：`ClassificationResult`，包含 `recordResult` 或 `tableResult` 与 `auditInfo`。

---

#### `classify_dataframe`

```python
classify_dataframe(
    df: Any,
    params: Optional[Dict[str, Any]] = None,
) -> ClassificationResult
```

对 `pandas.DataFrame` 进行分类（需安装 pandas）。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `df` | `pandas.DataFrame` | 是 | DataFrame 实例 |
| `params` | `Optional[Dict[str, Any]]` | 否 | 请求级参数 |

---

#### `classify_arrow`

```python
classify_arrow(
    table: Any,
    params: Optional[Dict[str, Any]] = None,
) -> ClassificationResult
```

对 `pyarrow.Table` 进行分类（需安装 pyarrow）。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `table` | `pyarrow.Table` | 是 | Arrow Table 实例 |
| `params` | `Optional[Dict[str, Any]]` | 否 | 请求级参数 |

---

#### `classify_sql_result`

```python
classify_sql_result(
    result_set: List[Dict[str, Any]],
    params: Optional[Dict[str, Any]] = None,
) -> ClassificationResult
```

对 SQL 结果集（`list[dict]`）进行分类。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `result_set` | `List[Dict[str, Any]]` | 是 | 查询结果列表 |
| `params` | `Optional[Dict[str, Any]]` | 否 | 请求级参数 |

---

#### `classify_secretflow`

```python
classify_secretflow(
    sf_data: Any,
    params: Optional[Dict[str, Any]] = None,
    party: Optional[str] = None,
) -> ClassificationResult
```

对 SecretFlow 联邦数据结构进行分类（需安装 secretflow）。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `sf_data` | `Any` | 是 | SecretFlow DataFrame / HDataFrame / VDataFrame / FedNdarray |
| `params` | `Optional[Dict[str, Any]]` | 否 | 请求级参数 |
| `party` | `Optional[str]` | 否 | HDataFrame 参与方；单一 partition 时可省略 |

---

#### `submit_classify_table_async`

```python
submit_classify_table_async(
    schema: List[str],
    rows: List[Dict[str, Any]],
    params: Optional[Dict[str, Any]] = None,
) -> str
```

提交异步表分类任务，返回 `job_id`。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema` | `List[str]` | 是 | 列名列表 |
| `rows` | `List[Dict[str, Any]]` | 是 | 记录列表 |
| `params` | `Optional[Dict[str, Any]]` | 否 | 请求级参数 |

---

#### `get_job_result`

```python
get_job_result(
    job_id: str,
) -> ClassificationJob
```

查询异步任务状态与结果。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `job_id` | `str` | 是 | 异步任务 ID |

---

#### `confirm_review`

```python
confirm_review(
    review_id: str,
    corrected_level: str,
    reviewer: str = "",
    comment: str = "",
) -> ReviewEntry
```

确认或修正复核样本。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `review_id` | `str` | 是 | 复核条目 ID |
| `corrected_level` | `str` | 是 | 修正后的敏感度等级，如 `L3` |
| `reviewer` | `str` | 否 | 复核人标识 |
| `comment` | `str` | 否 | 复核说明 |

---

#### `export_reviews`

```python
export_reviews(
    format: str = "jsonl",
    mask_input: bool = False,
) -> str
```

导出复核样本。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `format` | `str` | 否 | `jsonl` 或 `csv` |
| `mask_input` | `bool` | 否 | 是否对 input 字段掩码 |

---

### 1.2 `ClassificationService`

位置：`privacy_local_agent.classification_service.ClassificationService`

REST/gRPC 的统一业务编排入口，持有 `ClassificationAPI` 实例并记录 Prometheus 指标。

#### 构造函数

```python
ClassificationService(
    profile_path: Optional[str] = None,
    resolver: Optional[ParameterResolver] = None,
)
```

#### 主要方法

| 方法 | 签名 | 说明 |
|---|---|---|
| `classify_field` | `classify_field(field_name, value, params=None) -> dict` | 字段分类 |
| `classify_record` | `classify_record(record, params=None) -> dict` | 记录分类 |
| `classify_table` | `classify_table(schema, rows, params=None) -> dict` | 表分类 |
| `classify_secretflow` | `classify_secretflow(sf_data, params=None, party=None) -> dict` | SecretFlow 分类 |
| `submit_classify_table_async` | `submit_classify_table_async(schema, rows, params=None) -> str` | 提交异步任务 |
| `get_job_result` | `get_job_result(job_id) -> dict` | 查询异步任务 |
| `confirm_review` | `confirm_review(review_id, corrected_level, reviewer, comment) -> dict` | 确认复核 |
| `export_reviews` | `export_reviews(format="jsonl", mask_input=False) -> str` | 导出复核样本 |

---

### 1.3 数据模型

位置：`privacy_local_agent.privacy.classification_models`

| 模型 | 说明 |
|---|---|
| `SensitivityLevel` | `L1` / `L2` / `L3` / `L4` / `L5` 枚举 |
| `EngineLayer` | `L1_RULE` / `L2_SMALL_NER` / `L3_LLM` 枚举 |
| `SecurityTag` | 单个分类标签：等级、类别、置信度、来源引擎、规则 ID、版本、人工复核标志 |
| `FieldClassificationResult` | 字段级结果 |
| `RecordClassificationResult` | 记录级结果，聚合字段结果 |
| `TableClassificationResult` | 表级结果，聚合记录结果 |
| `ClassificationResult` | 包装器，含 `recordResult` / `tableResult` + `auditInfo` |
| `ClassificationParams` | 参数治理模型 |
| `CompositeRule` | 复合规则定义 |
| `ShadowDiff` | 影子模式差异 |
| `ClassificationJob` | 异步任务状态模型 |
| `ReviewEntry` | 复核条目模型 |
| `RuleEngineABC` | 规则引擎抽象基类 |
| `SmallNerEngine` | Small-NER 引擎抽象基类 |
| `LlmClassifier` | LLM 分类器抽象基类 |

---

### 1.4 规则引擎

位置：`privacy_local_agent.privacy.classification_rule_engine`

| 符号 | 说明 |
|---|---|
| `RuleEngine` | 向后兼容的抽象接口（`RuleEngineABC` 子类） |
| `DefaultRuleEngine` | 默认 Layer-1 规则引擎，实现字段名/值匹配、ICD-10 区间、身份证与医保卡校验、合规模板扩展字段规则 |
| `_unique_tags` | 内部工具：按 `(level, category)` 去重并保留顺序 |

---

### 1.5 向量化规则引擎（可选）

位置：`privacy_local_agent.privacy.classification_vectorized`

| 符号 | 说明 |
|---|---|
| `VectorizedRuleEngine` | 基于 pandas Series 的批量 Layer-1 规则引擎；语义与 `DefaultRuleEngine` 一致，适合大数据集表分类 |
| `evaluate_series(field_name, series, params)` | 对整列批量评估，返回每行的 `List[SecurityTag]` |

使用方式：

```python
from privacy_local_agent.privacy.classification import ClassificationAPI

# 自动选择向量化引擎（需安装 pandas）
api = ClassificationAPI(use_vectorized=True)

# 或直接传入实例
from privacy_local_agent.privacy.classification_vectorized import VectorizedRuleEngine
api = ClassificationAPI(rule_engine=VectorizedRuleEngine())
```

> 未安装 pandas 时 `use_vectorized=True` 会自动回退到 `DefaultRuleEngine`。

---

### 1.6 工具函数与适配器

位置：`privacy_local_agent.privacy.classification_utils`

| 符号 | 说明 |
|---|---|
| `redact` | 字段值脱敏，保留前 N 个字符 |
| `hash_value` | 对原始值做 SHA256/MD5 哈希 |
| `should_log_value` | 判断值是否可以完整打印到日志 |
| `safe_log` | 自动脱敏字符串字段后记录日志 |
| `mask_record_values` | 对整记录逐字段脱敏 |
| `TEMPLATES` | 内置合规模板字典：`jrt0197`、`gbt35273`、`gdpr` |
| `get_template_params` | 按模板名返回默认参数字典 |
| `classify_secretflow` | SecretFlow 联邦数据结构分类适配器 |

---

### 1.7 `ClassificationParams` 字段

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `version` | `str` | `"1.0.0"` | 参数版本 |
| `defaultLevel` | `SensitivityLevel` | `L3` | 未命中任何规则时的默认等级 |
| `enableRuleEngine` | `bool` | `true` | 是否启用规则引擎 |
| `enableSmallNer` | `bool` | `false` | 是否启用 Small-NER |
| `enableLlm` | `bool` | `false` | 是否启用 LLM |
| `icd10L4Intervals` | `List[Dict[str, str]]` | B20-B24, F20-F29, C00-C97 | 需要升级为 L4 的 ICD-10 区间 |
| `genomicKeywords` | `List[str]` | 基因相关关键字 | 用于规则引擎的基因字段名匹配 |
| `publicFieldWhitelist` | `List[str]` | `public_report`, `annual_summary`, `科普` | 公开报表字段白名单 |
| `operationalFieldPatterns` | `List[str]` | `turnover_rate`, `device_usage`, `inventory` | 运营统计字段模式 |
| `manualOverride` | `Dict[str, SensitivityLevel]` | `{}` | 字段名 → 等级的最终覆盖 |
| `template` | `Optional[str]` | `null` | 合规模板：`jrt0197` / `gbt35273` / `gdpr` |
| `ruleSetVersion` | `str` | `"1.0.0"` | 当前规则集版本 |
| `shadowMode` | `bool` | `false` | 是否启用影子模式 |
| `shadowVersion` | `Optional[str]` | `null` | 影子规则集版本 |
| `returnFieldValues` | `bool` | `true` | 是否在结果中返回 `fieldValue` |
| `compositeRules` | `List[CompositeRule]` | `[]` | 请求级自定义复合规则 |

---

## 2. REST API

### POST `/v1/privacy/classify/field`

请求体：

```json
{
  "field_name": "id_card",
  "value": "110101199001011237",
  "params": {}
}
```

响应体：

```json
{
  "result": {
    "fieldName": "id_card",
    "fieldValue": "110101199001011237",
    "tags": [
      {
        "level": "L3",
        "category": "PII_ID_CARD",
        "confidence": 1.0,
        "sourceEngine": "RULE",
        "ruleId": "RULE_ID_001",
        "version": "1.0.0",
        "needsHumanReview": false
      }
    ],
    "finalLevel": "L3",
    "confidence": 1.0,
    "engineLayer": "L1_RULE",
    "needsHumanReview": false,
    "reasoning": "命中规则: RULE_ID_001"
  }
}
```

### POST `/v1/privacy/classify/record`

请求体：

```json
{
  "record": {
    "id_card": "110101199001011237",
    "mobile": "13800138000",
    "diagnosis": "B21.1"
  },
  "params": {}
}
```

### POST `/v1/privacy/classify/table`

请求体：

```json
{
  "schema": ["id_card", "mobile", "diagnosis"],
  "rows": [
    {
      "id_card": "110101199001011237",
      "mobile": "13800138000",
      "diagnosis": "B21.1"
    }
  ],
  "params": {}
}
```

### POST `/v1/privacy/classify/table/async`

提交异步表分类任务。

请求体：

```json
{
  "schema": ["id_card", "mobile", "diagnosis"],
  "rows": [
    {
      "id_card": "110101199001011237",
      "mobile": "13800138000",
      "diagnosis": "B21.1"
    }
  ],
  "params": {"enable_llm": true}
}
```

响应体：

```json
{
  "job_id": "cls-018f-4c2a-9e3b",
  "status": "PENDING",
  "created_at": "2026-07-17T10:00:00Z"
}
```

### GET `/v1/privacy/classify/jobs/{job_id}`

响应体：

```json
{
  "job_id": "cls-018f-4c2a-9e3b",
  "status": "DONE",
  "result": { ... },
  "error": null,
  "created_at": "2026-07-17T10:00:00Z",
  "finished_at": "2026-07-17T10:00:05Z"
}
```

### POST `/v1/privacy/classify/secretflow`

请求体：

```json
{
  "party": "alice",
  "params_json": "{\"enable_rule_engine\": true}",
  "data_json": "..."
}
```

> SecretFlow REST 接口接受序列化后的数据表示；gRPC 使用专用消息结构。

### POST `/v1/privacy/classify/review/confirm`

请求体：

```json
{
  "review_id": "review-001",
  "corrected_level": "L5",
  "reviewer": "operator-1",
  "comment": "确认为基因组合敏感数据"
}
```

### POST `/v1/privacy/classify/review/export`

请求体：

```json
{
  "format": "jsonl",
  "mask_input": true
}
```

响应体：

```json
{
  "data": "{\"input\":\"brca1_status|***\",...}\n..."
}
```

---

## 3. gRPC API

### 方法列表

| 方法 | 请求 | 响应 | 说明 |
|---|---|---|---|
| `ClassifyField` | `ClassifyFieldRequest` | `ClassifyFieldResponse` | 单字段分类 |
| `ClassifyRecord` | `ClassifyRecordRequest` | `ClassifyRecordResponse` | 单条记录分类 |
| `ClassifyTable` | `ClassifyTableRequest` | `ClassifyTableResponse` | 整张表分类 |
| `ClassifyTableAsync` | `ClassifyTableAsyncRequest` | `ClassifyTableAsyncResponse` | 提交异步表分类任务 |
| `GetClassificationJob` | `GetClassificationJobRequest` | `GetClassificationJobResponse` | 查询异步任务结果 |
| `ClassifySecretFlow` | `ClassifySecretFlowRequest` | `ClassifySecretFlowResponse` | SecretFlow 分类 |
| `ConfirmReview` | `ConfirmReviewRequest` | `ConfirmReviewResponse` | 确认复核 |
| `ExportReviews` | `ExportReviewsRequest` | `ExportReviewsResponse` | 导出复核样本 |

### `ClassifyFieldRequest` 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `field_name` | `string` | 字段名 |
| `value` | `string` | 字段值 |
| `params_json` | `string` | JSON 序列化的请求参数 |

### `ClassifyFieldResponse` 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `result_json` | `string` | JSON 序列化的字段分类结果 |

### `ClassifyRecordRequest` 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `record` | `RecordEntry` | 记录，字段名为 key，字段值为 value |
| `params_json` | `string` | JSON 序列化的请求参数 |

### `ClassifyRecordResponse` 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `result_json` | `string` | JSON 序列化的记录分类结果 |

### `ClassifyTableRequest` 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `schema` | `repeated string` | 列名列表 |
| `rows` | `repeated RecordEntry` | 记录列表 |
| `params_json` | `string` | JSON 序列化的请求参数 |

### `ClassifyTableResponse` 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `result_json` | `string` | JSON 序列化的表分类结果 |

### `ClassifyTableAsyncRequest` / `ClassifyTableAsyncResponse` 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `schema` | `repeated string` | 列名列表 |
| `rows` | `repeated RecordEntry` | 记录列表 |
| `params_json` | `string` | 请求参数 |
| `job_id` | `string` | 任务 ID |
| `status` | `string` | 任务状态 |
| `created_at` | `string` | 创建时间 |

### `GetClassificationJobRequest` / `GetClassificationJobResponse` 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `job_id` | `string` | 任务 ID |
| `status` | `string` | 任务状态 |
| `result_json` | `string` | 结果 JSON |
| `error` | `string` | 错误信息 |
| `created_at` | `string` | 创建时间 |
| `finished_at` | `string` | 完成时间 |

### `ClassifySecretFlowRequest` / `ClassifySecretFlowResponse` 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `party` | `string` | 参与方 |
| `params_json` | `string` | 请求参数 |
| `data_json` | `string` | SecretFlow 数据序列化表示 |
| `result_json` | `string` | 结果 JSON |

### `ConfirmReviewRequest` / `ConfirmReviewResponse` 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `review_id` | `string` | 复核条目 ID |
| `corrected_level` | `string` | 修正等级 |
| `reviewer` | `string` | 复核人 |
| `comment` | `string` | 说明 |
| `result_json` | `string` | 结果 JSON |

### `ExportReviewsRequest` / `ExportReviewsResponse` 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `format` | `string` | `jsonl` / `csv` |
| `mask_input` | `bool` | 是否掩码 |
| `data` | `string` | 导出内容 |

### gRPC 调用示例

```python
import json
import grpc
from privacy_local_agent import privacy_pb2, privacy_pb2_grpc

channel = grpc.insecure_channel("127.0.0.1:50051")
stub = privacy_pb2_grpc.PrivacyServiceStub(channel)

response = stub.ClassifyField(
    privacy_pb2.ClassifyFieldRequest(
        field_name="id_card",
        value="110101199001011237",
        params_json=json.dumps({}),
    )
)
result = json.loads(response.result_json)
print(result["finalLevel"])  # L3
```

---

## 4. 异常与错误码

| 异常/错误 | 触发条件 | HTTP 状态码 | gRPC 状态码 |
|---|---|---|---|
| `ValueError: JSON input must be a dict or a list of dicts` | `classify_json` 输入既不是 dict 也不是 list | 400 | `INVALID_ARGUMENT` |
| `TypeError: classify_dataframe expects a pandas.DataFrame` | `classify_dataframe` 传入非 DataFrame | 400 | `INVALID_ARGUMENT` |
| `TypeError: classify_arrow expects a pyarrow.Table` | `classify_arrow` 传入非 Arrow Table | 400 | `INVALID_ARGUMENT` |
| `ImportError: secretflow is required ...` | `classify_secretflow` 缺少 SecretFlow | 400 | `FAILED_PRECONDITION` |
| `ValueError: invalid sensitivity level: ...` | `parse_level` 收到非法等级字符串 | 400 | `INVALID_ARGUMENT` |
| `RuntimeError: async job queue is full` | 异步任务超过最大并发数 | 429 | `RESOURCE_EXHAUSTED` |
| `KeyError: job not found` | 查询不存在的异步任务 ID | 404 | `NOT_FOUND` |
| 模型加载失败 | 缺少 ONNX / torch / transformers 等依赖 | 正常降级为 No-Op | 正常降级 |
