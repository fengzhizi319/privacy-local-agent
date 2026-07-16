# 本地多模态大模型分类分级使用示例

## 1. 概述

本文档提供 `Qwen2VLClassifier` 与分类 REST API 的典型使用示例，覆盖本地图片路径、Base64 图片与纯文本三种输入。

> 若本地未下载 `Qwen2-VL-2B-Instruct` 模型权重或缺少 ML 依赖，SDK 会自动降级为规则引擎，示例仍可运行。

## 2. Python SDK 示例

### 2.1 纯文本输入

```python
from privacy_local_agent.privacy.classification_llm import Qwen2VLClassifier
from privacy_local_agent.privacy.classification_models import SensitivityLevel

classifier = Qwen2VLClassifier()

result = classifier.classify(
    text="患者诊断为 HIV 阳性，正在接受抗逆转录病毒治疗。",
    upstream_level=SensitivityLevel.L3,
    upstream_confidence=0.5,
)

if result:
    print(f"等级: {result['final_level']}")
    print(f"类别: {result['sub_category']}")
    print(f"置信度: {result['confidence']}")
    print(f"推理: {result['reasoning']}")
else:
    print("大模型未命中或已降级，请参考规则/Small-NER 结果。")
```

### 2.2 本地图片路径输入

```python
from privacy_local_agent.privacy.classification_llm import Qwen2VLClassifier
from privacy_local_agent.privacy.classification_models import SensitivityLevel

classifier = Qwen2VLClassifier()

result = classifier.classify(
    text="/path/to/medical_report.png",
    upstream_level=SensitivityLevel.L1,
    upstream_confidence=0.1,
)

if result:
    print(result)
```

### 2.3 Base64 图片输入

```python
import base64
from privacy_local_agent.privacy.classification_llm import Qwen2VLClassifier
from privacy_local_agent.privacy.classification_models import SensitivityLevel

with open("/path/to/medical_report.png", "rb") as f:
    image_base64 = base64.b64encode(f.read()).decode("utf-8")

# 方式一：Data URI 格式
data_uri = f"data:image/png;base64,{image_base64}"

classifier = Qwen2VLClassifier()
result = classifier.classify(
    text=data_uri,
    upstream_level=SensitivityLevel.L1,
    upstream_confidence=0.1,
)

if result:
    print(result)
```

### 2.4 通过 `ClassificationAPI` 启用 LLM 层

```python
from privacy_local_agent.privacy.classification import ClassificationAPI

api = ClassificationAPI()

# 启用大模型层并传入图片路径
result = api.classify_field(
    field_name="medical_image",
    value="/path/to/medical_report.png",
    params={"enable_llm": True},
)

print(f"最终等级: {result.final_level.value}")
print(f"命中引擎: {result.engine_layer.value}")
print(f"推理: {result.reasoning}")
```

## 3. REST API 示例

### 3.1 文本输入

```bash
curl -X POST http://127.0.0.1:8079/v1/privacy/classify/field \
  -H "Content-Type: application/json" \
  -d '{
    "field_name": "diagnosis_note",
    "value": "患者诊断为 HIV 阳性，正在接受抗逆转录病毒治疗。",
    "params": {"enable_llm": true}
  }'
```

### 3.2 本地图片路径

```bash
curl -X POST http://127.0.0.1:8079/v1/privacy/classify/field \
  -H "Content-Type: application/json" \
  -d '{
    "field_name": "medical_image",
    "value": "/path/to/medical_report.png",
    "params": {"enable_llm": true}
  }'
```

### 3.3 Base64 图片

```bash
IMAGE_BASE64=$(base64 -w 0 /path/to/medical_report.png)

curl -X POST http://127.0.0.1:8079/v1/privacy/classify/field \
  -H "Content-Type: application/json" \
  -d "{
    \"field_name\": \"medical_image\",
    \"value\": \"data:image/png;base64,${IMAGE_BASE64}\",
    \"params\": {\"enable_llm\": true}
  }"
```

### 3.4 单条记录

```bash
curl -X POST http://127.0.0.1:8079/v1/privacy/classify/record \
  -H "Content-Type: application/json" \
  -d '{
    "record": {
      "id_card": "110101199001011237",
      "diagnosis_note": "患者诊断为 HIV 阳性。"
    },
    "params": {"enable_llm": true}
  }'
```

## 4. 最佳实践

1. **优先使用图片路径**：服务器本地路径开销最小，避免大 Base64 传输。
2. **生产环境按需开启 LLM**：`enable_llm=true` 会显著增加推理耗时与资源占用。
3. **对 `needs_human_review=true` 的结果进入人工复核队列**。
4. **高敏感字段（L4/L5）务必记录审计日志**。
