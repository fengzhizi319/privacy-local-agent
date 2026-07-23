"""本地轻量级 ONNX Small-NER 命名实体识别引擎单元测试。

验证纯 Python 分词器、BIO 状态机解析合并、ONNX 会话输出概率转换以及故障降级逻辑。
"""

import os
import sys
from unittest.mock import MagicMock, patch
import pytest

# 模拟 onnxruntime 模块以在无 onnxruntime 依赖环境下支持单元测试执行
sys.modules["onnxruntime"] = MagicMock()

from privacy_local_agent.privacy.classification_ner import (
    ONNXSmallNerEngine,
    SimpleChineseBertTokenizer,
)


def test_simple_bert_tokenizer(tmp_path):
    """测试纯 Python 实现的中文 BERT 分词编码器。"""
    # 建立测试临时 vocab 词表文件
    vocab_path = os.path.join(tmp_path, "vocab.txt")
    vocab_tokens = [
        "[PAD]", "[UNK]", "[CLS]", "[SEP]",
        "阿", "司", "匹", "林", "感", "冒",
        "h", "i", "v", "a", "d", "s",
    ]
    with open(vocab_path, "w", encoding="utf-8") as f:
        f.write("\n".join(vocab_tokens) + "\n")

    tokenizer = SimpleChineseBertTokenizer(vocab_path)

    # 1. 验证魔数 ID 映射
    assert tokenizer.pad_id == 0
    assert tokenizer.unk_id == 1
    assert tokenizer.cls_id == 2
    assert tokenizer.sep_id == 3

    # 2. 验证字符分词
    tokens = tokenizer.tokenize("阿司匹林")
    assert tokens == ["阿", "司", "匹", "林"]

    # 3. 验证未登录词映射为 UNK
    tokens_unk = tokenizer.tokenize("发热")
    assert tokens_unk == ["[UNK]", "[UNK]"]

    # 4. 验证英文字母大小写折叠（中文词表通常只含小写，大写医学缩写应能命中）
    tokens_upper = tokenizer.tokenize("HIV")
    assert tokens_upper == ["h", "i", "v"]
    tokens_mixed = tokenizer.tokenize("AiDs")
    assert tokens_mixed == ["a", "i", "d", "s"]

    # 5. 验证编码填充截断
    input_ids, attention_mask, token_type_ids = tokenizer.encode("阿司", max_len=6)
    # [CLS], 阿, 司, [SEP], [PAD], [PAD]
    assert input_ids == [2, 4, 5, 3, 0, 0]
    assert attention_mask == [1, 1, 1, 1, 0, 0]
    assert token_type_ids == [0, 0, 0, 0, 0, 0]


def test_parse_bio_tags():
    """测试 BIO 标签合并逻辑，提取实体文本与计算置信度。"""
    engine = ONNXSmallNerEngine()
    tokens = ["[CLS]", "阿", "司", "匹", "林", "[SEP]"]
    label_indices = [0, 3, 4, 4, 4, 0]  # 3: B-dru, 4: I-dru
    probs = [0.9, 0.98, 0.99, 0.99, 0.99, 0.9]

    entities = engine._parse_bio_tags(tokens, label_indices, probs)
    assert len(entities) == 1
    assert entities[0]["text"] == "阿司匹林"
    assert entities[0]["label"] == "dru"
    assert entities[0]["confidence"] == 0.98  # 置信度取内部所有 token 最小概率


@patch("privacy_local_agent.privacy.classification_ner.ONNXSmallNerEngine._lazy_init")
def test_ner_extract_success(mock_lazy_init):
    """测试成功加载模型时执行 ONNX 推理及标签标准化映射。"""
    engine = ONNXSmallNerEngine()

    mock_session = MagicMock()
    mock_tokenizer = MagicMock()

    mock_tokenizer.encode.return_value = ([2, 4, 5, 3], [1, 1, 1, 1], [0, 0, 0, 0])
    mock_tokenizer.tokenize.return_value = ["阿", "司"]

    # 模拟 ONNX 模型输出的 Logits Tensor (batch_size=1, seq_len=4, num_labels=15)
    import numpy as np

    dummy_logits = np.zeros((1, 4, 15))
    dummy_logits[0, 1, 3] = 10.0  # 第一个字 -> B-dru
    dummy_logits[0, 2, 4] = 10.0  # 第二个字 -> I-dru

    mock_session.run.return_value = [dummy_logits]

    engine.session = mock_session
    engine.tokenizer = mock_tokenizer
    engine._initialized = True

    entities = engine.extract("阿司")
    assert len(entities) == 1
    assert entities[0]["text"] == "阿司"
    # 标签从 "dru" 映射为标准化的 "MEDICATION"
    assert entities[0]["label"] == "MEDICATION"


def test_ner_fallback_when_uninitialized():
    """测试模型文件缺失时，引擎自动捕获异常并返回空列表（安全降级不崩溃）。"""
    # 设定不存在的文件
    engine = ONNXSmallNerEngine(model_path="nonexistent.onnx", vocab_path="nonexistent.txt")
    entities = engine.extract("阿司匹林")
    assert entities == []


@patch("privacy_local_agent.privacy.classification_ner.ModelScopeSmallNerEngine._lazy_init")
def test_modelscope_ner_extract_success(mock_lazy_init):
    """测试 ModelScope NER 引擎在 pipeline 返回数据时的提取与映射逻辑。"""
    from privacy_local_agent.privacy.classification_ner import ModelScopeSmallNerEngine

    engine = ModelScopeSmallNerEngine()

    mock_pipeline = MagicMock()
    mock_pipeline.return_value = {
        "output": [
            {"type": "dis", "start": 0, "end": 2, "span": "感冒"},
            {"type": "dru", "start": 3, "end": 7, "span": "阿司匹林"},
        ]
    }

    engine.pipeline = mock_pipeline
    engine._initialized = True

    entities = engine.extract("感冒 阿司匹林")
    assert len(entities) == 2
    assert entities[0]["text"] == "感冒"
    assert entities[0]["label"] == "MEDICAL_DISEASE"
    assert entities[1]["text"] == "阿司匹林"
    assert entities[1]["label"] == "MEDICATION"

