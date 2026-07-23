"""真实模型不降级集成测试 / Real-Model Non-Degraded Integration Tests.

中文说明：
本文件验证 NER 与 Qwen2-VL 大模型在 **真实加载、不降级** 情况下的推理能力，
覆盖纯文本定级、图片病例 OCR 定级以及完整三层漏斗（规则 → NER → LLM）。

这些测试需要：
1. 已通过 ``download_model.py`` / ``download_ner_model.py`` 下载模型至 ``.models/``；
2. 已安装 ML 依赖（torch / transformers / accelerate / onnxruntime）。

重要：本文件必须在 **独立进程** 中运行，避免与 ``test_classification_llm.py`` /
``test_classification_ner.py`` 在模块级注入的 ``sys.modules["torch"] = MagicMock()``
/ ``sys.modules["onnxruntime"] = MagicMock()`` 发生污染冲突。当检测到 torch 被 mock
或依赖/模型缺失时，整文件自动跳过，因此在完整 ``pytest tests`` 套件中不会失败。

运行方式 / Usage::

    PYTHONPATH=. pytest tests/test_classification_real_models.py -v -m real_models

English Description:
Integration tests that verify NER and Qwen2-VL inference under real (non-degraded)
model loading, covering plain-text grading, image-case OCR grading, and the full
3-layer funnel. Must run in a separate process to avoid sys.modules mock pollution
from the mocked unit tests; auto-skips when deps/models are unavailable.
"""

from __future__ import annotations

import base64
import importlib
import sys
from pathlib import Path
from typing import Optional

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
LLM_MODEL_DIR = PROJECT_ROOT / ".models" / "Qwen2-VL-2B-Instruct"
NER_MODEL_DIR = PROJECT_ROOT / ".models" / "raner_cmeee"
NER_ONNX = PROJECT_ROOT / ".models" / "raner_cmeee.onnx"
MEDICAL_IMAGE_DIR = PROJECT_ROOT / "frontend" / "web" / "src" / "assets" / "medical"


def _load_real_module(name: str):
    """导入真实模块；若未安装或被其它测试文件 mock（MagicMock）则返回 None。

    MagicMock 没有真实的 ``__file__`` 字符串路径，据此可区分真实模块与 mock 占位。
    """
    try:
        mod = importlib.import_module(name)
    except Exception:
        return None
    if not isinstance(getattr(mod, "__file__", None), str):
        return None
    return mod


_torch = _load_real_module("torch")
_transformers = _load_real_module("transformers")

# torch 是否被其它测试模块在 sys.modules 中污染为 MagicMock
_TORCH_MOCKED = "torch" in sys.modules and not isinstance(
    getattr(sys.modules["torch"], "__file__", None), str
)

HAS_ML = (_torch is not None and _transformers is not None and not _TORCH_MOCKED)
HAS_LLM_MODEL = LLM_MODEL_DIR.is_dir() and any(LLM_MODEL_DIR.glob("*.safetensors"))
HAS_NER_MODEL = NER_MODEL_DIR.is_dir() or NER_ONNX.exists()

_SKIP_ML = "需要真实 torch/transformers（非 mock），请先安装 ML 依赖"
_SKIP_LLM = "需要 Qwen2-VL 模型与 ML 依赖，请先运行 download_model.py"
_SKIP_NER = "需要 NER 模型与 ML 依赖，请先运行 download_ner_model.py"


def _image_data_uri(filename: str) -> Optional[str]:
    """将前端打包的病例图片读取为 base64 Data URI（与前端行为一致）。"""
    path = MEDICAL_IMAGE_DIR / filename
    if not path.exists():
        return None
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("utf-8")


# ---------------------------------------------------------------------------
# 模块级 fixture：模型仅加载一次，跨用例复用以节省显存与时间
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def qwen_classifier():
    """真实加载 Qwen2-VL 分类器（不降级）。"""
    from privacy_local_agent.privacy.classification.classification_llm import Qwen2VLClassifier

    clf = Qwen2VLClassifier()
    if not clf.warmup():
        pytest.skip("Qwen2-VL 模型加载失败（可能显存不足或依赖缺失）")
    assert clf.is_ready is True
    return clf


@pytest.fixture(scope="module")
def ner_engine():
    """按 ClassificationAPI 相同策略选择真实 NER 引擎（ONNX 优先，否则 ModelScope）。"""
    if NER_ONNX.exists():
        from privacy_local_agent.privacy.classification.classification_ner import ONNXSmallNerEngine

        return ONNXSmallNerEngine()
    from privacy_local_agent.privacy.classification.classification_ner import ModelScopeSmallNerEngine

    return ModelScopeSmallNerEngine()


# ---------------------------------------------------------------------------
# Layer-2 Small-NER 真实推理测试
# ---------------------------------------------------------------------------


@pytest.mark.real_models
@pytest.mark.skipif(not (HAS_ML and HAS_NER_MODEL), reason=_SKIP_NER)
def test_ner_real_extraction(ner_engine):
    """NER 引擎真实抽取医疗实体（疾病/药物/手术），不降级为空列表。"""
    text = "患者确诊急性心肌梗死，给予阿司匹林治疗，并行冠状动脉支架植入术。"
    entities = ner_engine.extract(text)

    assert isinstance(entities, list)
    assert len(entities) > 0, "NER 引擎应真实抽取出医疗实体（不降级）"

    labels = {ent["label"] for ent in entities}
    valid_labels = {"MEDICAL_DISEASE", "MEDICATION", "SURGERY", "BODY_PART", "GENOMIC_HINT"}
    assert labels & valid_labels, f"应识别出标准医疗实体标签，实际: {labels}"


@pytest.mark.real_models
@pytest.mark.skipif(not (HAS_ML and HAS_NER_MODEL), reason=_SKIP_NER)
def test_ner_real_sensitive_disease_escalation(ner_engine):
    """NER 抽取敏感疾病实体，经漏斗应升级至 L4。"""
    from privacy_local_agent.privacy.classification import ClassificationAPI
    from privacy_local_agent.privacy.classification.classification_models import SensitivityLevel

    api = ClassificationAPI(small_ner=ner_engine)
    tags = api._run_small_ner("diagnosis", "患者确诊精神分裂症，长期服用抗精神病药物。")
    assert tags, "应抽取出敏感疾病相关实体标签"
    levels = {t.level for t in tags}
    # 精神分裂属于敏感疾病，应升级至 L4
    assert SensitivityLevel.L4 in levels or SensitivityLevel.L5 in levels


# ---------------------------------------------------------------------------
# Layer-3 Qwen2-VL 真实推理测试（文本）
# ---------------------------------------------------------------------------


@pytest.mark.real_models
@pytest.mark.skipif(not (HAS_ML and HAS_LLM_MODEL), reason=_SKIP_LLM)
def test_llm_real_classify_text(qwen_classifier):
    """Qwen2-VL 真实加载并对含 PII 的文本定级，不降级为 None。"""
    from privacy_local_agent.privacy.classification.classification_models import SensitivityLevel

    text = "患者身份证号 110101199003072316，手机号 13800138000，诊断高血压，门诊病历记录。"
    res = qwen_classifier.classify(text, SensitivityLevel.L1, 0.1)

    assert res is not None, "LLM 应真实返回定级结果（不降级）"
    assert res.get("final_level") in {"L1", "L2", "L3", "L4", "L5"}
    assert 0.0 <= float(res.get("confidence", 0.0)) <= 1.0


# ---------------------------------------------------------------------------
# Layer-3 Qwen2-VL 真实推理测试（图片病例 —— 前端多模态链路）
# ---------------------------------------------------------------------------


@pytest.mark.real_models
@pytest.mark.skipif(not (HAS_ML and HAS_LLM_MODEL), reason=_SKIP_LLM)
@pytest.mark.parametrize(
    "filename,expected_levels",
    [
        ("hiv_lab_report.png", {"L3", "L4", "L5"}),
        ("genetic_report.png", {"L3", "L4", "L5"}),
        ("lab_blood_routine.png", {"L2", "L3", "L4", "L5"}),
    ],
)
def test_llm_real_classify_image(qwen_classifier, filename, expected_levels):
    """Qwen2-VL 对前端输入的图片病例进行 OCR + 定级（多模态不降级）。"""
    from privacy_local_agent.privacy.classification.classification_models import SensitivityLevel

    data_uri = _image_data_uri(filename)
    if data_uri is None:
        pytest.skip(f"测试图片不存在: {filename}")

    res = qwen_classifier.classify(data_uri, SensitivityLevel.L1, 0.1)

    assert res is not None, f"LLM 应对图片 {filename} 真实返回定级结果（不降级）"
    assert res.get("final_level") in expected_levels, (
        f"{filename} 预期等级范围 {expected_levels}，实际 {res.get('final_level')}"
    )


# ---------------------------------------------------------------------------
# 完整三层漏斗：图片病例端到端不降级
# ---------------------------------------------------------------------------


@pytest.mark.real_models
@pytest.mark.skipif(not (HAS_ML and HAS_LLM_MODEL), reason=_SKIP_LLM)
def test_classification_api_image_funnel_not_degraded(qwen_classifier):
    """ClassificationAPI 端到端：图片病例经漏斗由真实 LLM 定级（engine_layer=L3_LLM）。

    复用模块级 fixture 已加载的 Qwen2-VL 实例（通过 ``llm`` 注入），避免在同一
    进程中重复加载第二份模型导致显存溢出；同时另建一个未注入的 API 实例（仅
    构造不触发模型加载）以验证自动选择逻辑未降级为 NoOp。
    """
    from privacy_local_agent.privacy.classification import ClassificationAPI
    from privacy_local_agent.privacy.classification.classification_models import EngineLayer

    # 1. 验证自动选择逻辑：构造（不触发模型加载）即应选中真实 Qwen2VLClassifier
    auto_api = ClassificationAPI()
    assert type(auto_api.llm).__name__ == "Qwen2VLClassifier", "LLM 自动选择应为真实 Qwen2VLClassifier 而非 NoOp 降级"

    # 2. 复用已加载模型执行端到端推理，避免显存溢出
    api = ClassificationAPI(llm=qwen_classifier)

    data_uri = _image_data_uri("hiv_lab_report.png")
    if data_uri is None:
        pytest.skip("测试图片不存在: hiv_lab_report.png")

    result = api.classify_field(
        "medical_image", data_uri, {"enable_llm": True, "enable_small_ner": False}
    )

    assert result.engine_layer == EngineLayer.L3_LLM, "图片病例应由真实 LLM 层定级（不降级）"
    assert result.final_level.value in {"L3", "L4", "L5"}, "HIV 检验报告应为中高敏感等级"
