"""本地多模态大模型分类分级器单元测试。

验证多模态图片识别、Base64 解码、文本推断、大模型输出 JSON 解析以及加载失败时的自动降级机制。
"""

import base64
import os
import sys
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

# 模拟 torch 模块以在无 PyTorch 环境下支持单元测试运行
sys.modules["torch"] = MagicMock()


# 如果运行测试的环境没安装 Pillow，自动跳过这些多模态测试用例
try:
    from PIL import Image
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False

# The following imports must stay after the torch mock and Pillow availability
# check so that heavy ML modules are stubbed/optional handling is applied.
from privacy_local_agent.privacy.classification.classification_llm import Qwen2VLClassifier  # noqa: E402
from privacy_local_agent.privacy.classification.classification_models import SensitivityLevel  # noqa: E402


@pytest.mark.skipif(not HAS_PILLOW, reason="需要 Pillow 库来测试图像加载与解码")
def test_detect_image_local_path(tmp_path):
    """测试通过本地存在的文件路径检测图片。"""
    classifier = Qwen2VLClassifier()

    # 创建一个临时的 PNG 图像文件
    img_path = os.path.join(tmp_path, "medical_report.png")
    img = Image.new("RGB", (50, 50), color="white")
    img.save(img_path)

    detected = classifier._detect_image(str(img_path))
    assert detected is not None
    assert isinstance(detected, Image.Image)


@pytest.mark.skipif(not HAS_PILLOW, reason="需要 Pillow 库来测试图像加载与解码")
def test_detect_image_base64():
    """测试通过 Base64 编码检测并还原图片。"""
    classifier = Qwen2VLClassifier()

    buffered = BytesIO()
    img = Image.new("RGB", (50, 50), color="blue")
    img.save(buffered, format="JPEG")
    img_base64 = base64.b64encode(buffered.getvalue()).decode("utf-8")

    # 1. 测试纯 Base64 字符串
    detected1 = classifier._detect_image(img_base64)
    assert detected1 is not None
    assert isinstance(detected1, Image.Image)

    # 2. 测试 Data URI 格式的 Base64 字符串
    data_uri = f"data:image/jpeg;base64,{img_base64}"
    detected2 = classifier._detect_image(data_uri)
    assert detected2 is not None
    assert isinstance(detected2, Image.Image)


def test_detect_image_raw_text():
    """测试纯文本输入，此时应返回 None，表示无需多模态图像处理。"""
    classifier = Qwen2VLClassifier()
    detected = classifier._detect_image("患者主诉：反复胸闷 2 周，诊断为冠心病。")
    assert detected is None


@patch("privacy_local_agent.privacy.classification.classification_llm.Qwen2VLClassifier._lazy_init")
def test_classify_success(mock_lazy_init):
    """模拟大模型成功推理并输出合法 JSON 的场景。"""
    classifier = Qwen2VLClassifier()

    # 模拟初始化与模型/处理器实例
    mock_model = MagicMock()
    mock_processor = MagicMock()

    # 模拟 apply_chat_template 渲染系统 Prompt
    mock_processor.apply_chat_template.return_value = "<prompt>"
    # 模拟 processor 将输入转换为 tensors
    mock_processor.return_value = {"input_ids": MagicMock()}
    # 模拟大模型输出标准的 JSON 定级响应
    mock_processor.batch_decode.return_value = [
        '{\n  "final_level": "L4",\n  "sub_category": "MEDICAL_HIV",\n  "confidence": 0.95,\n  "reasoning": "含有抗艾滋病用药，确认为L4",\n  "needs_human_review": false\n}'  # noqa: E501
    ]

    classifier._model = mock_model
    classifier._processor = mock_processor
    classifier._initialized = True

    # 执行分类
    res = classifier.classify("测试病历", SensitivityLevel.L3, 0.5)

    assert res is not None
    assert res["final_level"] == "L4"
    assert res["confidence"] == 0.95
    assert res["sub_category"] == "MEDICAL_HIV"


@patch("privacy_local_agent.privacy.classification.classification_llm.Qwen2VLClassifier._lazy_init")
def test_classify_failure_fallback(mock_lazy_init):
    """测试大模型加载/推理崩溃时的安全防御与降级。"""
    classifier = Qwen2VLClassifier()

    # 模拟 lazy_init 抛出 CUDA 显存溢出等异常
    mock_lazy_init.side_effect = RuntimeError("CUDA out of memory")

    # 执行分类时，应该优雅捕获异常并返回 None，从而使外层 ClassificationAPI 自动回退到 Small-NER/Rules 模式
    res = classifier.classify("测试文本", SensitivityLevel.L3, 0.5)
    assert res is None


@patch("privacy_local_agent.privacy.classification.classification_llm.Qwen2VLClassifier._lazy_init")
def test_classify_handwritten_medical_note(mock_lazy_init):
    """测试大模型对手写体医疗文本/病历进行 OCR 提取与敏感定级。"""
    classifier = Qwen2VLClassifier()

    mock_model = MagicMock()
    mock_processor = MagicMock()
    mock_processor.apply_chat_template.return_value = "<prompt>"
    mock_processor.return_value = {"input_ids": MagicMock()}

    # 模拟手写病历识别出的 JSON 返回结构，识别出的手写字迹包含发热及扁桃体炎诊断
    mock_processor.batch_decode.return_value = [
        '{\n  "final_level": "L3",\n  "sub_category": "MEDICAL_OUTPATIENT",\n  "confidence": 0.90,\n  "reasoning": "OCR 识别出手写病历字迹：主诉发热3天，诊断为急性扁桃体炎，签名包含医生李某。判定为普通门诊诊疗记录，定级为 L3 级中风险数据",\n  "needs_human_review": false\n}'  # noqa: E501
    ]

    classifier._model = mock_model
    classifier._processor = mock_processor
    classifier._initialized = True

    # 模拟输入手写处方字迹
    handwritten_input = "[手写字迹病历] 主诉: 畏寒、发热3天。查体: T 38.9℃。诊断: 急性扁桃体炎。签名: 医生李某(手写)"
    res = classifier.classify(handwritten_input, SensitivityLevel.L1, 0.1)

    assert res is not None
    assert res["final_level"] == "L3"
    assert res["confidence"] == 0.90
    assert "手写" in res["reasoning"]
    assert res["sub_category"] == "MEDICAL_OUTPATIENT"


@patch("privacy_local_agent.privacy.classification.classification_llm.Qwen2VLClassifier._lazy_init")
def test_classify_printed_structured_report(mock_lazy_init):
    """测试大模型对带有结构化排版和表格格式的印刷体医疗报告进行定级评估。"""
    classifier = Qwen2VLClassifier()

    mock_model = MagicMock()
    mock_processor = MagicMock()
    mock_processor.apply_chat_template.return_value = "<prompt>"
    mock_processor.return_value = {"input_ids": MagicMock()}

    # 模拟表格排版印刷报告的 JSON 返回结构，识别出白细胞等化验常规指标
    mock_processor.batch_decode.return_value = [
        '{\n  "final_level": "L3",\n  "sub_category": "MEDICAL_LAB_REPORT",\n  "confidence": 0.94,\n  "reasoning": "OCR 识别出印刷体血常规结构化表格：白细胞计数 11.2 (10^9/L)，高于参考区间，判定为常规检验指标数值，定级为 L3 级中风险数据",\n  "needs_human_review": false\n}'  # noqa: E501
    ]

    classifier._model = mock_model
    classifier._processor = mock_processor
    classifier._initialized = True

    # 模拟输入印刷体检验报告表格
    printed_table_input = (
        "| 检查项目 | 测定值 | 单位 | 参考区间 |\n"
        "|---|---|---|---|\n"
        "| 白细胞计数 (WBC) | 11.2 | 10^9/L | 4.0 - 10.0 |\n"
        "| 红细胞计数 (RBC) | 4.5  | 10^12/L | 3.5 - 5.0 |"
    )
    res = classifier.classify(printed_table_input, SensitivityLevel.L1, 0.1)

    assert res is not None
    assert res["final_level"] == "L3"
    assert res["confidence"] == 0.94
    assert "印刷体" in res["reasoning"]
    assert res["sub_category"] == "MEDICAL_LAB_REPORT"


def test_qwen_classifier_not_ready_by_default():
    """未初始化时 Qwen2VLClassifier.is_ready 应为 False。"""
    classifier = Qwen2VLClassifier()
    assert classifier.is_ready is False


@patch("privacy_local_agent.privacy.classification.classification_llm.Qwen2VLClassifier._lazy_init")
def test_qwen_classifier_warmup_success(mock_lazy_init):
    """warmup 成功后 is_ready 返回 True。"""
    classifier = Qwen2VLClassifier()

    def _fake_init():
        classifier._initialized = True

    mock_lazy_init.side_effect = _fake_init
    assert classifier.warmup() is True
    mock_lazy_init.assert_called_once()
    assert classifier.is_ready is True


@patch("privacy_local_agent.privacy.classification.classification_llm.Qwen2VLClassifier._lazy_init")
def test_qwen_classifier_warmup_failure(mock_lazy_init):
    """warmup 失败时返回 False 且 is_ready 保持 False。"""
    mock_lazy_init.side_effect = RuntimeError("CUDA out of memory")
    classifier = Qwen2VLClassifier()
    assert classifier.warmup() is False
    assert classifier.is_ready is False
