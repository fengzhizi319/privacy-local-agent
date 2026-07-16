# 本地轻量级 Small-NER 模块 API 参考

## 1. Python SDK

### 1.1 `ONNXSmallNerEngine`

位置：`privacy_local_agent.privacy.classification_ner.ONNXSmallNerEngine`

基于 ONNX Runtime 的本地医疗 NER 推理引擎，延迟低、无 `transformers` 依赖。

#### 构造函数

```python
ONNXSmallNerEngine(
    model_path: Optional[str] = None,
    vocab_path: Optional[str] = None,
)
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `model_path` | `Optional[str]` | 否 | ONNX 模型文件路径，默认 `.models/raner_cmeee.onnx` |
| `vocab_path` | `Optional[str]` | 否 | BERT 词表文件路径，默认 `.models/vocab.txt` |

#### `extract`

```python
extract(text: str) -> List[Dict[str, Any]]
```

提取输入文本中的命名实体。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `text` | `str` | 是 | 待提取文本 |

**返回值**：实体字典列表，格式如下：

```python
[
    {"text": "急性心肌梗死", "label": "MEDICAL_DISEASE", "confidence": 0.98},
    {"text": "阿司匹林", "label": "MEDICATION", "confidence": 0.95},
]
```

**实体标签映射**：

| 原始类型 | 映射标签 |
|---|---|
| `dis` / `sym` / `mic` | `MEDICAL_DISEASE` |
| `dru` | `MEDICATION` |
| `pro` | `SURGERY` |
| `bod` | `BODY_PART` |
| `GENE` | `GENOMIC_HINT` |

> 若 ONNX 模型或 `onnxruntime` 缺失，`extract` 会记录 WARNING 并返回空列表，不抛出异常。

---

### 1.2 `ModelScopeSmallNerEngine`

位置：`privacy_local_agent.privacy.classification_ner.ModelScopeSmallNerEngine`

基于 ModelScope 官方管道的本地医疗 NER 引擎，精度更高，依赖 `modelscope` / `torch` / `transformers`。

#### 构造函数

```python
ModelScopeSmallNerEngine(
    model_id: str = "damo/nlp_raner_named-entity-recognition_chinese-base-cmeee",
)
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `model_id` | `str` | 否 | ModelScope 模型 ID |

#### `extract`

```python
extract(text: str) -> List[Dict[str, Any]]
```

输出格式与 `ONNXSmallNerEngine.extract` 一致。若依赖缺失或模型加载失败，返回空列表。

---

### 1.3 `ClassificationAPI`

位置：`privacy_local_agent.privacy.classification.ClassificationAPI`

三层分类漏斗的编排入口，自动集成 Small-NER 作为第二层。

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
| `profile_path` | `Optional[str]` | 否 | YAML 参数配置文件路径 |
| `rule_engine` | `Optional[RuleEngine]` | 否 | 自定义规则引擎 |
| `small_ner` | `Optional[SmallNerEngine]` | 否 | 自定义 NER 引擎；默认自动选择 ONNX → ModelScope → NoOp |
| `llm` | `Optional[LlmClassifier]` | 否 | 自定义 LLM 分类器 |
| `resolver` | `Optional[ParameterResolver]` | 否 | 参数解析器 |

#### `classify_field`

```python
classify_field(
    field_name: str,
    value: Any,
    params: Optional[Dict[str, Any]] = None,
) -> FieldClassificationResult
```

对单个字段进行分类。通过 `params` 可动态启用 Small-NER：

```python
{"enable_small_ner": True}
```

NER 命中后会更新结果中的 `tags`、`final_level`、`confidence`、`engine_layer` 和 `needs_human_review`。

**Small-NER 联动定级**：

| NER 标签 | 触发条件 | 定级 |
|---|---|---|
| `MEDICAL_DISEASE` | 普通疾病/症状 | L3 |
| `MEDICAL_DISEASE` | 含 HIV、艾滋、梅毒、肿瘤、癌症、白血病、精神分裂、抑郁症等敏感关键字 | L4 |
| `MEDICATION` | 任意药物实体 | L3 |
| `SURGERY` | 任意手术/操作实体 | L3 |
| `BODY_PART` | 任意解剖部位实体 | L3 |
| `GENOMIC_HINT` | 基因相关实体 | L5 + `needs_human_review=True` |

---

## 2. REST API

### POST `/v1/privacy/classify/field`

请求体：

```json
{
  "field_name": "clinical_note",
  "value": "患者诊断为2型糖尿病，处方开具二甲双胍。",
  "params": {
    "enable_small_ner": true
  }
}
```

响应体：

```json
{
  "result": {
    "fieldName": "clinical_note",
    "fieldValue": "患者诊断为2型糖尿病，处方开具二甲双胍。",
    "tags": [
      {
        "level": "L3",
        "category": "MEDICAL_DISEASE",
        "confidence": 0.92,
        "sourceEngine": "SMALL_NER",
        "ruleId": "NER_DIS_NORMAL",
        "needsHumanReview": false
      },
      {
        "level": "L3",
        "category": "MEDICATION",
        "confidence": 0.88,
        "sourceEngine": "SMALL_NER",
        "ruleId": "NER_DRU_001",
        "needsHumanReview": false
      }
    ],
    "finalLevel": "L3",
    "confidence": 0.92,
    "engineLayer": "L2_SMALL_NER",
    "needsHumanReview": false,
    "reasoning": ""
  }
}
```

### POST `/v1/privacy/classify/record`

请求体：

```json
{
  "record": {
    "name": "张三",
    "diagnosis": "患者HIV阳性，伴有发热症状。"
  },
  "params": {
    "enable_small_ner": true
  }
}
```

响应体结构与 `ClassifyRecord` 对应，聚合各字段标签后给出记录级 `finalLevel`。

### POST `/v1/privacy/classify/table`

请求体：

```json
{
  "schema": ["id", "clinical_note"],
  "rows": [
    {"id": "1", "clinical_note": "检查报告显示BRCA1基因突变。"}
  ],
  "params": {
    "enable_small_ner": true
  }
}
```

响应体结构与 `ClassifyTable` 对应，包含表级聚合标签与 `finalLevel`。

---

## 3. gRPC API

### 方法列表

| 方法 | 请求 | 响应 | 说明 |
|---|---|---|---|
| `ClassifyField` | `ClassifyFieldRequest` | `ClassifyFieldResponse` | 单字段分类 |
| `ClassifyRecord` | `ClassifyRecordRequest` | `ClassifyRecordResponse` | 单记录分类 |
| `ClassifyTable` | `ClassifyTableRequest` | `ClassifyTableResponse` | 表级分类 |

### `ClassifyFieldRequest` 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `field_name` | `string` | 字段名 |
| `value` | `string` | 字段值 |
| `params_json` | `string` | JSON 字符串参数，例如 `{"enable_small_ner": true}` |

### `ClassifyFieldResponse` 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `result_json` | `string` | JSON 字符串格式的 `FieldClassificationResult` |

`ClassifyRecordRequest` / `ClassifyRecordResponse`、`ClassifyTableRequest` / `ClassifyTableResponse` 参见 `proto/privacy.proto`。

---

## 4. 异常与错误码

| 异常/错误 | 触发条件 | HTTP 状态码 | gRPC 状态码 |
|---|---|---|---|
| 模型或依赖缺失 | `.models/raner_cmeee.onnx` / `vocab.txt` 不存在，或 `onnxruntime` 未安装 | 正常返回空列表 | 正常返回空列表 |
| `ImportError` | ModelScope 模式缺少 `modelscope` / `torch` / `transformers` | 正常返回空列表 | 正常返回空列表 |
| `FileNotFoundError` | 构造 `ONNXSmallNerEngine` 后显式调用 `_lazy_init()` 且文件缺失 | 500 | `INTERNAL` |
| `ValueError` | 非法 `params` 或敏感度等级字符串 | 400 | `INVALID_ARGUMENT` |

> Small-NER 设计为“始终可降级”。在推荐路径（直接调用 `extract` 或通过 `ClassificationAPI.classify_field`）中，即使模型文件或依赖缺失，服务也不会崩溃，而是返回空实体列表并继续后续层级或默认定级。
