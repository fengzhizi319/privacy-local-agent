# 数据分类分级模块使用示例

## 1. 概述

本文档提供 `ClassificationAPI` 与 REST API 的典型使用示例，覆盖字段级、记录级、表级分类，参数治理，以及三层引擎调用。所有示例均基于实际代码，可直接复制运行或改造成业务逻辑。

## 2. Python SDK 示例

### 2.1 字段级分类

```python
from privacy_local_agent.privacy.classification import ClassificationAPI

api = ClassificationAPI()

# 身份证号命中规则引擎，返回 L3
result = api.classify_field("id_card", "110101199001011237")
print(result.final_level)   # L3
print(result.tags[0].category)  # PII_ID_CARD
```

### 2.2 记录级分类

```python
record = {
    "id_card": "110101199001011237",
    "mobile": "13800138000",
    "diagnosis": "B21.1",
    "public_report": "2023 annual summary",
}

result = api.classify_record(record)
print(result.final_level)  # L4，由 diagnosis B21.1 升级
print(result.needs_human_review)  # False
```

### 2.3 表级分类

```python
schema = ["id_card", "mobile", "diagnosis", "brca1_status"]
rows = [
    {
        "id_card": "110101199001011237",
        "mobile": "13800138000",
        "diagnosis": "J18.9",
        "brca1_status": "positive",
    },
    {
        "id_card": "110101199001011238",
        "mobile": "13800138001",
        "diagnosis": "C78.0",
        "brca1_status": "negative",
    },
]

result = api.classify_table(schema, rows)
print(result.final_level)  # L5，由 brca1_status 升级
```

### 2.4 JSON 输入自动识别

```python
import json

# 单条记录
json_str = json.dumps({
    "id_card": "110101199001011237",
    "mobile": "13800138000",
})
result = api.classify_json(json_str)
print(result.record_result.final_level)  # L3

# 表数据
table_json = [
    {"id_card": "110101199001011237", "diagnosis": "C78.0"},
    {"id_card": "110101199001011238", "diagnosis": "J18.9"},
]
result = api.classify_json(table_json)
print(result.table_result.final_level)  # L4
```

### 2.5 pandas DataFrame 分类

```python
import pandas as pd

df = pd.DataFrame({
    "id_card": ["110101199001011237", "110101199001011238"],
    "diagnosis": ["B21.1", "J18.9"],
})

result = api.classify_dataframe(df)
print(result.table_result.final_level)  # L4
```

### 2.6 参数治理：请求级覆盖

```python
# 默认 ICD-10 L4 区间包含 B20-B24、F20-F29、C00-C97
# 请求参数可覆盖区间、开关与人工覆盖
params = {
    "icd10L4Intervals": [
        {"start": "J10", "end": "J18"},  # 将流感肺炎区间也升级为 L4
    ],
    "manualOverride": {
        "mobile": "L4",  # 强制手机号字段为 L4
    },
}

result = api.classify_record(
    {"mobile": "13800138000", "diagnosis": "J18.9"},
    params=params,
)
print(result.field_results["mobile"].final_level)  # L4
print(result.field_results["diagnosis"].final_level)  # L4
```

### 2.7 三层引擎调用

```python
# 启用 Small-NER 与 LLM（若模型/依赖缺失会自动降级为 No-Op，不会报错）
params = {
    "enableSmallNer": True,
    "enableLlm": True,
}

result = api.classify_field(
    "clinical_note",
    "患者诊断为 HIV 感染，使用拉米夫定治疗。",
    params=params,
)
print(result.engine_layer)  # 若 NER/LLM 可用则为 L2/L3，否则为 L1_RULE
```

### 2.8 自定义规则引擎

```python
from privacy_local_agent.privacy.classification import (
    ClassificationAPI, RuleEngine, DefaultRuleEngine, SecurityTag, SensitivityLevel
)

class CustomRuleEngine(DefaultRuleEngine):
    def evaluate(self, field_name, value, params):
        tags = super().evaluate(field_name, value, params)
        if "password" in str(field_name).lower():
            tags.append(SecurityTag(
                level=SensitivityLevel.L5,
                category="CREDENTIAL",
                source_engine="RULE",
                rule_id="CUSTOM_001",
            ))
        return tags

api = ClassificationAPI(rule_engine=CustomRuleEngine())
result = api.classify_field("user_password", "P@ssw0rd")
print(result.final_level)  # L5
```

## 3. REST API 示例

### 3.1 字段分类

```bash
curl -X POST http://127.0.0.1:8079/v1/privacy/classify/field \
  -H "Content-Type: application/json" \
  -d '{
    "field_name": "id_card",
    "value": "110101199001011237",
    "params": {}
  }'
```

### 3.2 记录分类

```bash
curl -X POST http://127.0.0.1:8079/v1/privacy/classify/record \
  -H "Content-Type: application/json" \
  -d '{
    "record": {
      "id_card": "110101199001011237",
      "mobile": "13800138000",
      "diagnosis": "B21.1"
    },
    "params": {}
  }'
```

### 3.3 表分类

```bash
curl -X POST http://127.0.0.1:8079/v1/privacy/classify/table \
  -H "Content-Type: application/json" \
  -d '{
    "schema": ["id_card", "mobile", "diagnosis"],
    "rows": [
      {"id_card": "110101199001011237", "mobile": "13800138000", "diagnosis": "B21.1"}
    ],
    "params": {}
  }'
```

### 3.4 参数治理示例

```bash
curl -X POST http://127.0.0.1:8079/v1/privacy/classify/field \
  -H "Content-Type: application/json" \
  -d '{
    "field_name": "mobile",
    "value": "13800138000",
    "params": {
      "manualOverride": {"mobile": "L4"}
    }
  }'
```

## 4. 最佳实践

1. **优先使用规则引擎处理高并发流量**：Layer 1 规则引擎微秒级延迟、万级 QPS，适合网关全量拦截。
2. **按需启用 Small-NER**：Layer 2 适合半结构化临床文本，延迟在百毫秒级；不建议对所有请求开启。
3. **谨慎启用 Layer 3 LLM**：Qwen2-VL 推理秒级，建议作为旁路审计队列或人工复核触发器，避免阻塞同步 API。
4. **使用 YAML profile 管理默认参数**：通过 `PRIVACY_PROFILE` 环境变量统一配置，避免每个请求重复传参。
5. **利用 `manualOverride` 做兜底策略**：对已知敏感字段（如 `patient_id`、`genome_seq`）强制定级。
6. **关注 `needsHumanReview`**：LLM / Small-NER 命中的高敏感或置信度低的结果应进入人工复核队列。
7. **记录 `auditInfo`**：分类结果中的版本、时间戳、参数来源可用于审计与故障排查。

## 5. 常见错误

| 错误 | 原因 | 解决 |
|---|---|---|
| `ValueError: JSON input must be a dict or a list of dicts` | `classify_json` 传入非法结构 | 传入 dict 或 list[dict] |
| `TypeError: classify_dataframe expects a pandas.DataFrame` | 未安装 pandas 或传入类型错误 | 安装 pandas 并传入 DataFrame |
| `TypeError: classify_arrow expects a pyarrow.Table` | 未安装 pyarrow 或传入类型错误 | 安装 pyarrow 并传入 Table |
| 返回 `engineLayer` 为 `L1_RULE` 但已开启 `enableSmallNer` | 模型未下载或依赖缺失，已降级 | 下载模型或安装 ML 依赖 |
| `finalLevel` 与预期不符 | 参数被 profile 或 `manualOverride` 覆盖 | 检查 `auditInfo.parameterSource` |
| REST 返回 401/403 | 认证/授权/限速未通过 | 配置 API Key 与权限，或关闭相关环境变量 |
