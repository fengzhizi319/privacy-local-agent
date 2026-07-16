# 本地多模态大模型分类分级 API 参考

## 1. Python SDK

### `Qwen2VLClassifier`

位置：`privacy_local_agent.privacy.classification_llm.Qwen2VLClassifier`

基于本地部署 `Qwen2-VL-2B-Instruct` 的多模态分类器，支持本地图片路径、Base64 图片与纯文本输入。

#### 构造函数

```python
Qwen2VLClassifier(model_path: Optional[str] = None)
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `model_path` | `Optional[str]` | 否 | 模型本地路径，默认使用项目根目录下的 `.models/Qwen2-VL-2B-Instruct` |

#### `classify`

```python
classify(
    text: str,
    upstream_level: SensitivityLevel,
    upstream_confidence: float,
) -> Optional[Dict[str, Any]]
```

对输入进行分类分级。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `text` | `str` | 是 | 输入文本、本地图片路径或 Base64 图片字符串 |
| `upstream_level` | `SensitivityLevel` | 是 | 上游规则/Small-NER 输出的等级 |
| `upstream_confidence` | `float` | 是 | 上游输出的置信度 |

**返回值**：成功时返回固定 JSON 字典，失败时返回 `None` 触发外层降级。

返回示例：

```json
{
  "final_level": "L4",
  "sub_category": "MEDICAL_SENSITIVE_DISEASE",
  "confidence": 0.92,
  "reasoning": "图片中包含‘抗逆转录病毒治疗’等字样，推断属于 HIV 敏感病史，评定为 L4 级高风险数据",
  "needs_human_review": false
}
```

#### 输入类型说明

`classify` 内部通过 `_detect_image` 自动识别输入类型：

| 输入形式 | 识别条件 | 处理方式 |
|---|---|---|
| 本地图片路径 | 长度 < 512 且后缀为 `.jpg/.jpeg/.png/.bmp/.webp`，文件存在 | `PIL.Image.open(path)` |
| Base64 Data URI | 匹配 `data:image/...;base64,...` | base64 解码后加载为图片 |
| 纯 Base64 | 长度 > 100 且可合法 base64 解码为图片 | base64 解码后加载为图片 |
| 纯文本 | 其他情况 | 直接作为文本输入 |

---

### `LlmClassifier`（抽象接口）

位置：`privacy_local_agent.privacy.classification.LlmClassifier`

所有 LLM 分类器需实现的抽象基类。

```python
class LlmClassifier(ABC):
    @abstractmethod
    def classify(
        self, text: str, upstream_level: SensitivityLevel, upstream_confidence: float
    ) -> Optional[Dict[str, Any]]:
        ...
```

### `NoOpLlmClassifier`（兜底实现）

位置：`privacy_local_agent.privacy.classification.NoOpLlmClassifier`

当模型未下载或加载失败时的默认兜底分类器。若 `upstream_confidence < 0.6`，返回保守定级结果并标记需人工复核。

---

### `ClassificationAPI`

位置：`privacy_local_agent.privacy.classification.ClassificationAPI`

统一分类 API，内置规则引擎、Small-NER 与 LLM 三层漏斗。默认会自动尝试实例化 `Qwen2VLClassifier`，失败则回退到 `NoOpLlmClassifier`。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `profile_path` | `Optional[str]` | 否 | YAML 配置文件路径 |
| `rule_engine` | `Optional[RuleEngine]` | 否 | 自定义规则引擎 |
| `small_ner` | `Optional[SmallNerEngine]` | 否 | 自定义 Small-NER 引擎 |
| `llm` | `Optional[LlmClassifier]` | 否 | 自定义 LLM 分类器 |

关键分类参数：

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `enable_llm` | `bool` | `false` | 是否启用大模型定级 |
| `enable_small_ner` | `bool` | `false` | 是否启用 Small-NER |
| `enable_rule_engine` | `bool` | `true` | 是否启用规则引擎 |
| `default_level` | `str` | `L3` | 默认敏感度等级 |

## 2. REST API

分类接口由 `classification_routes.py` 提供，挂载在 `/v1/privacy/classify/*`。

### POST `/v1/privacy/classify/field`

单字段分类。

请求体：

```json
{
  "field_name": "medical_image",
  "value": "/path/to/report.png",
  "params": {"enable_llm": true}
}
```

响应体：

```json
{
  "result": {
    "fieldName": "medical_image",
    "fieldValue": "/path/to/report.png",
    "tags": [],
    "finalLevel": "L4",
    "confidence": 0.92,
    "engineLayer": "L3_LLM",
    "needsHumanReview": false,
    "reasoning": "..."
  }
}
```

### POST `/v1/privacy/classify/record`

单条记录分类。

请求体：

```json
{
  "record": {
    "id_card": "110101199001011237",
    "medical_image": "/path/to/report.png"
  },
  "params": {"enable_llm": true}
}
```

### POST `/v1/privacy/classify/table`

整张表分类。

请求体：

```json
{
  "schema": ["id_card", "medical_image"],
  "rows": [
    {"id_card": "110101199001011237", "medical_image": "/path/to/report.png"}
  ],
  "params": {"enable_llm": true}
}
```

## 3. gRPC API

### 方法列表

| 方法 | 请求 | 响应 | 说明 |
|---|---|---|---|
| `ClassifyField` | `ClassifyFieldRequest` | `ClassifyFieldResponse` | 单字段分类 |
| `ClassifyRecord` | `ClassifyRecordRequest` | `ClassifyRecordResponse` | 单条记录分类 |
| `ClassifyTable` | `ClassifyTableRequest` | `ClassifyTableResponse` | 整张表分类 |

### `ClassifyFieldRequest` 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `field_name` | `string` | 字段名 |
| `value` | `string` | 字段值，可为文本、图片路径或 Base64 图片 |
| `params_json` | `string` | JSON 序列化的分类参数 |

### `ClassifyFieldResponse` 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `result_json` | `string` | JSON 序列化的字段分类结果 |

### `ClassifyRecordRequest` 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `record` | `RecordEntry` | 字段名到字段值的映射 |
| `params_json` | `string` | JSON 序列化的分类参数 |

### `ClassifyTableRequest` 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `schema` | `repeated string` | 列名列表 |
| `rows` | `repeated RecordEntry` | 记录列表 |
| `params_json` | `string` | JSON 序列化的分类参数 |

## 4. 异常与降级

| 场景 | 行为 |
|---|---|
| 模型目录不存在 | `Qwen2VLClassifier._lazy_init` 抛出 `FileNotFoundError`，`classify` 捕获后返回 `None` |
| torch/transformers 未安装 | 实例化或初始化时捕获 `ImportError`，降级为 `NoOpLlmClassifier` |
| CUDA/MPS 加载失败 | 抛出 `RuntimeError`，`classify` 返回 `None` |
| 输出 JSON 解析失败 | 返回 `None`，外层使用规则/Small-NER 结果保守处理 |
