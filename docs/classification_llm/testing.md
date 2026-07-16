# 本地多模态大模型分类分级测试文档

## 1. 概述

本文档定义 `privacy_local_agent/privacy/classification_llm.py` 的测试策略、测试范围与可执行示例。LLM 层测试需覆盖输入类型检测、模型加载降级、JSON 解析以及 REST/gRPC 接口一致性。

## 2. 测试目标

- 验证本地图片路径、Base64 图片、纯文本三种输入类型被正确识别。
- 验证大模型初始化失败时自动降级为 `NoOpLlmClassifier` 或规则引擎。
- 验证大模型输出 JSON 被正确解析并返回结构化字典。
- 验证 JSON 解析失败时返回 `None` 触发降级。
- 验证 REST/gRPC 分类接口可透传图片路径与 Base64 输入。

## 3. 单元测试策略

### 3.1 输入类型检测测试

```python
import base64
import os
from io import BytesIO
from unittest.mock import patch
import pytest

try:
    from PIL import Image
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False

from privacy_local_agent.privacy.classification_llm import Qwen2VLClassifier


@pytest.mark.skipif(not HAS_PILLOW, reason="需要 Pillow 库来测试图像加载与解码")
def test_detect_image_local_path(tmp_path):
    """测试通过本地存在的文件路径检测图片。"""
    classifier = Qwen2VLClassifier()

    img_path = os.path.join(tmp_path, "medical_report.png")
    img = Image.new("RGB", (50, 50), color="white")
    img.save(img_path)

    detected = classifier._detect_image(str(img_path))
    assert detected is not None
    assert isinstance(detected, Image.Image)
```

### 3.2 Base64 图片检测测试

```python
@pytest.mark.skipif(not HAS_PILLOW, reason="需要 Pillow 库来测试图像加载与解码")
def test_detect_image_base64():
    """测试通过 Base64 编码检测并还原图片。"""
    classifier = Qwen2VLClassifier()

    buffered = BytesIO()
    img = Image.new("RGB", (50, 50), color="blue")
    img.save(buffered, format="JPEG")
    img_base64 = base64.b64encode(buffered.getvalue()).decode("utf-8")

    # 纯 Base64 字符串
    detected1 = classifier._detect_image(img_base64)
    assert detected1 is not None

    # Data URI 格式
    data_uri = f"data:image/jpeg;base64,{img_base64}"
    detected2 = classifier._detect_image(data_uri)
    assert detected2 is not None
```

### 3.3 纯文本输入测试

```python
def test_detect_image_raw_text():
    """测试纯文本输入返回 None，表示无需多模态图像处理。"""
    classifier = Qwen2VLClassifier()
    detected = classifier._detect_image("患者主诉：反复胸闷 2 周，诊断为冠心病。")
    assert detected is None
```

### 3.4 大模型成功推理测试

```python
from unittest.mock import MagicMock, patch
from privacy_local_agent.privacy.classification_models import SensitivityLevel


@patch("privacy_local_agent.privacy.classification_llm.Qwen2VLClassifier._lazy_init")
def test_classify_success(mock_lazy_init):
    """模拟大模型成功推理并输出合法 JSON 的场景。"""
    classifier = Qwen2VLClassifier()

    mock_processor = MagicMock()
    mock_processor.apply_chat_template.return_value = "<prompt>"
    mock_processor.return_value = {"input_ids": MagicMock()}
    mock_processor.batch_decode.return_value = [
        '{\n  "final_level": "L4",\n  "sub_category": "MEDICAL_HIV",\n  "confidence": 0.95,\n  "reasoning": "含有抗艾滋病用药，确认为L4",\n  "needs_human_review": false\n}'
    ]

    classifier._model = MagicMock()
    classifier._processor = mock_processor
    classifier._initialized = True

    res = classifier.classify("测试病历", SensitivityLevel.L3, 0.5)

    assert res is not None
    assert res["final_level"] == "L4"
    assert res["confidence"] == 0.95
```

### 3.5 模型加载失败降级测试

```python
@patch("privacy_local_agent.privacy.classification_llm.Qwen2VLClassifier._lazy_init")
def test_classify_failure_fallback(mock_lazy_init):
    """测试大模型加载/推理崩溃时的安全防御与降级。"""
    classifier = Qwen2VLClassifier()
    mock_lazy_init.side_effect = RuntimeError("CUDA out of memory")

    res = classifier.classify("测试文本", SensitivityLevel.L3, 0.5)
    assert res is None
```

### 3.6 JSON 解析失败降级测试

```python
@patch("privacy_local_agent.privacy.classification_llm.Qwen2VLClassifier._lazy_init")
def test_classify_malformed_json_fallback(mock_lazy_init):
    """测试大模型输出非 JSON 时返回 None。"""
    classifier = Qwen2VLClassifier()

    mock_processor = MagicMock()
    mock_processor.apply_chat_template.return_value = "<prompt>"
    mock_processor.return_value = {"input_ids": MagicMock()}
    mock_processor.batch_decode.return_value = ["这不是一个合法的 JSON 输出"]

    classifier._model = MagicMock()
    classifier._processor = mock_processor
    classifier._initialized = True

    res = classifier.classify("测试文本", SensitivityLevel.L3, 0.5)
    assert res is None
```

## 4. 集成测试策略

### 4.1 REST 接口测试

```python
from fastapi.testclient import TestClient
from privacy_local_agent.main import app


client = TestClient(app)


def test_rest_classify_field_text():
    resp = client.post(
        "/v1/privacy/classify/field",
        json={
            "field_name": "diagnosis_note",
            "value": "患者诊断为 HIV 阳性，正在接受抗逆转录病毒治疗。",
            "params": {"enable_llm": False},
        },
    )
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["finalLevel"] in {"L3", "L4"}
```

### 4.2 gRPC 接口测试

```python
import json
from privacy_local_agent import privacy_pb2
from privacy_local_agent.grpc_server import PrivacyServicer


def test_grpc_classify_field():
    servicer = PrivacyServicer()
    request = privacy_pb2.ClassifyFieldRequest(
        field_name="mobile",
        value="13800138000",
        params_json='{}',
    )
    response = servicer.ClassifyField(request, None)
    result = json.loads(response.result_json)
    assert result["finalLevel"] == "L3"
    assert any(t["category"] == "PII_MOBILE" for t in result["tags"])
```

## 5. 测试执行命令

```bash
# 运行 LLM 分类器相关单元测试
PYTHONPATH=. pytest tests/test_classification_llm.py -v

# 运行分类模块全部测试
PYTHONPATH=. pytest tests/test_classification.py tests/test_classification_rest.py tests/test_classification_grpc.py tests/test_classification_llm.py -v

# 运行全部测试
PYTHONPATH=. pytest tests -q
```

## 6. 持续集成建议

- 在无 GPU 的 CI 环境中，使用 `unittest.mock` 模拟模型加载与推理。
- 不强制要求下载 Qwen2-VL 模型权重，单元测试应独立于模型文件。
- 图片相关测试在未安装 Pillow 时自动跳过。

## 7. 验收检查清单

- [ ] 本地图片路径、Base64 图片、纯文本输入检测测试通过。
- [ ] 大模型成功输出 JSON 时解析结果正确。
- [ ] 模型加载失败或输出异常时返回 `None` 并触发降级。
- [ ] REST/gRPC 分类接口参数透传与序列化测试通过。
- [ ] 测试不依赖已下载的模型权重。
