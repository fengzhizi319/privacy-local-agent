# 本地轻量级 Small-NER 使用示例

## 1. 概述

本文档提供 Small-NER 引擎的典型使用示例，覆盖：

- 直接使用 ONNX 极速模式提取实体。
- 直接使用 ModelScope 官方管道模式提取实体。
- 通过 `ClassificationAPI` 启用 NER 进行分类定级。
- 通过 REST API 调用分类接口。

所有示例均已考虑模型/依赖缺失场景，缺失时会优雅降级并给出提示。

## 2. Python SDK 示例

### 2.1 ONNX 极速模式提取实体

```python
from privacy_local_agent.privacy.classification_ner import ONNXSmallNerEngine

engine = ONNXSmallNerEngine()
text = "患者因急性心肌梗死入院，行冠状动脉介入治疗。"
entities = engine.extract(text)

print("提取实体:")
for ent in entities:
    print(f"  {ent['text']:12s} -> {ent['label']:20s} (置信度: {ent['confidence']:.2f})")
```

**预期输出**（模型已下载时）：

```text
提取实体:
  急性心肌梗死     -> MEDICAL_DISEASE        (置信度: 0.98)
  冠状动脉介入治疗   -> SURGERY                (置信度: 0.85)
```

### 2.2 ModelScope 官方管道模式提取实体

```python
from privacy_local_agent.privacy.classification_ner import ModelScopeSmallNerEngine

engine = ModelScopeSmallNerEngine()
text = "患者诊断为2型糖尿病，处方开具二甲双胍，每日两次。"
entities = engine.extract(text)

print("提取实体:")
for ent in entities:
    print(f"  {ent['text']:12s} -> {ent['label']:20s} (置信度: {ent['confidence']:.2f})")
```

### 2.3 通过 ClassificationAPI 启用 NER 进行分类

```python
from privacy_local_agent.privacy.classification import ClassificationAPI

api = ClassificationAPI()

# 普通疾病，定级 L3
text = "患者诊断为2型糖尿病，建议控制饮食。"
res = api.classify_field("clinical_note", text, params={"enable_small_ner": True})
print(f"字段: {res.field_name}")
print(f"定级: {res.final_level.value}")
print(f"引擎层: {res.engine_layer.value}")
print("命中标签:", [str(t) for t in res.tags])
```

### 2.4 敏感病种升级至 L4

```python
from privacy_local_agent.privacy.classification import ClassificationAPI

api = ClassificationAPI()

# 敏感疾病关键字触发 L4
text = "患者张三，诊断为HIV阳性，需长期抗病毒治疗。"
res = api.classify_field("clinical_note", text, params={"enable_small_ner": True})
print(f"定级: {res.final_level.value}")
print("命中标签:", [str(t) for t in res.tags])
```

**预期输出**（NER 命中敏感病种时）：

```text
定级: L4
命中标签: ['L4_MEDICAL_SENSITIVE_DISEASE', 'L3_MEDICATION']
```

### 2.5 基因相关实体标记为 L5

```python
from privacy_local_agent.privacy.classification import ClassificationAPI

api = ClassificationAPI()

text = "基因检测报告显示BRCA1突变，建议遗传咨询。"
res = api.classify_field("genetic_report", text, params={"enable_small_ner": True})
print(f"定级: {res.final_level.value}")
print(f"需要人工复核: {res.needs_human_review}")
print("命中标签:", [str(t) for t in res.tags])
```

**预期输出**（NER 命中基因实体时）：

```text
定级: L5
需要人工复核: True
命中标签: ['L5_GENOMIC_HINT']
```

## 3. REST API 示例

### 3.1 单字段分类（启用 Small-NER）

```bash
curl -X POST http://127.0.0.1:8079/v1/privacy/classify/field \
  -H "Content-Type: application/json" \
  -d '{
    "field_name": "clinical_note",
    "value": "患者诊断为2型糖尿病，处方开具二甲双胍。",
    "params": {"enable_small_ner": true}
  }'
```

### 3.2 单记录分类

```bash
curl -X POST http://127.0.0.1:8079/v1/privacy/classify/record \
  -H "Content-Type: application/json" \
  -d '{
    "record": {
      "name": "张三",
      "diagnosis": "患者HIV阳性，伴有发热症状。"
    },
    "params": {"enable_small_ner": true}
  }'
```

### 3.3 表级分类

```bash
curl -X POST http://127.0.0.1:8079/v1/privacy/classify/table \
  -H "Content-Type: application/json" \
  -d '{
    "schema": ["id", "clinical_note"],
    "rows": [
      {"id": "1", "clinical_note": "检查报告显示BRCA1基因突变。"}
    ],
    "params": {"enable_small_ner": true}
  }'
```

## 4. 最佳实践

1. **优先使用 ONNX 模式**：CPU 环境下延迟最低，且不依赖 `transformers`，适合 Sidecar 部署。
2. **动态启用 NER**：默认 Small-NER 关闭，仅在需要医疗实体识别的请求中传入 `{"enable_small_ner": true}`。
3. **预先下载模型**：生产环境应在镜像构建或启动阶段运行 `download_ner_model.py`，避免首次请求时加载。
4. **关注敏感病种升级**：NER 识别到 HIV、肿瘤、癌症等敏感疾病时会自动升级为 L4，需配套更严格的脱敏策略。
5. **基因数据人工复核**：任何 `GENOMIC_HINT` 标签都会触发 `needs_human_review=True`，建议接入人工审批流。

## 5. 常见错误

| 错误 | 原因 | 解决 |
|---|---|---|
| 实体列表为空 | 模型未下载或依赖未安装 | 运行 `download_ner_model.py` 或安装 `onnxruntime` |
| `ModuleNotFoundError: No module named 'onnxruntime'` | ONNX 模式依赖缺失 | `pip install onnxruntime` |
| `ModuleNotFoundError: No module named 'modelscope'` | ModelScope 模式依赖缺失 | `pip install modelscope torch transformers` |
| 敏感病种未升级到 L4 | 文本中未命中内置敏感关键字 | 检查关键字或补充 profile 中的规则 |
