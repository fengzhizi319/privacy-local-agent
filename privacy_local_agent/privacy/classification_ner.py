"""基于 ONNX Runtime 的本地轻量级命名实体识别（Small-NER）引擎。

中文说明：
提供纯 Python 实现的 BERT Tokenizer 以及高效的 BIO 标记解析器。
支持 ONNX Runtime 和 ModelScope 两种推理后端，均具备延迟加载与自动降级能力。

English Description:
Local lightweight Named Entity Recognition (Small-NER) engine based on ONNX Runtime.
Provides a pure-Python BERT Tokenizer and an efficient BIO tag parser.
Supports both ONNX Runtime and ModelScope inference backends with lazy-loading
and graceful degradation capabilities.
"""

from __future__ import annotations

import os
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple

from ..observability.logging_config import get_logger
from ..observability.metrics import (
    CLASSIFICATION_NER_DURATION,
    CLASSIFICATION_NER_TOTAL,
)
from .classification_models import SmallNerEngine

# Module-level structured logger for NER engine events
logger = get_logger(__name__)


class SimpleChineseBertTokenizer:
    """纯 Python 实现的轻量级中文 BERT 分词器 / Lightweight Chinese BERT Tokenizer.

    中文说明：
    无任何第三方分词库（如 transformers / tokenizers）依赖，确保毫秒级推理的高效与兼容性。

    English Description:
    A pure-Python lightweight Chinese BERT tokenizer with no third-party tokenization
    library dependencies (e.g. transformers / tokenizers), ensuring millisecond-level
    inference efficiency and compatibility.
    """

    def __init__(self, vocab_path: str):
        """初始化分词器 / Initialize Tokenizer.

        执行步骤 / Execution Steps:
        1. 逐行读取 vocab.txt 构建 token→id 映射。
           (Read vocab.txt line-by-line to build token→id mapping)
        2. 缓存特殊 token ID（[UNK], [CLS], [SEP], [PAD]）。
           (Cache special token IDs)

        Args:
            vocab_path: vocab.txt 词表文件路径 / Path to vocab.txt vocabulary file.
        """
        self.vocab: Dict[str, int] = {}
        with open(vocab_path, "r", encoding="utf-8") as f:
            for idx, line in enumerate(f):
                token = line.strip()
                self.vocab[token] = idx

        self.unk_id = self.vocab.get("[UNK]", 100)
        self.cls_id = self.vocab.get("[CLS]", 101)
        self.sep_id = self.vocab.get("[SEP]", 102)
        self.pad_id = self.vocab.get("[PAD]", 0)

    def tokenize(self, text: str) -> List[str]:
        """对中文进行单字/字符级切分 / Tokenize Chinese Text at Character Level.

        中文说明：
        对英文字符做大小写折叠后切分。中文 BERT 词表通常只包含小写英文字母，
        为提升对医学缩写（如 HIV、AIDS）的识别稳定性，当大写字母不在词表中时，
        尝试使用其小写形式。

        English Description:
        Performs character-level tokenization for Chinese text with case-folding for
        English characters. When an uppercase letter is not in the vocabulary, its
        lowercase form is used to improve recognition of medical abbreviations.

        Args:
            text: 待分词的文本 / Text to tokenize.

        Returns:
            token 列表 / List of tokens.
        """
        tokens: List[str] = []
        for char in text:
            # 基础字符处理，如果在词表中直接加入，否则归为 UNK
            if char in self.vocab:
                tokens.append(char)
            elif char.isalpha() and char.lower() in self.vocab:
                # 大小写不折叠：医学缩写大写输入在中文词表中通常只注册小写形式
                tokens.append(char.lower())
            else:
                tokens.append("[UNK]")
        return tokens

    def encode(self, text: str, max_len: int = 128) -> Tuple[List[int], List[int], List[int]]:
        """将文本编码为 BERT 输入张量数据结构 / Encode Text to BERT Input Tensors.

        执行步骤 / Execution Steps:
        1. 添加 [CLS] 和 [SEP] 特殊标记。
           (Add [CLS] and [SEP] special tokens)
        2. 将 token 映射为 vocab ID。
           (Map tokens to vocabulary IDs)
        3. 生成 attention_mask 和 token_type_ids。
           (Generate attention_mask and token_type_ids)
        4. 按 max_len 进行 padding 对齐。
           (Pad to max_len alignment)

        Args:
            text: 待编码文本 / Text to encode.
            max_len: 最大序列长度 / Maximum sequence length.

        Returns:
            (input_ids, attention_mask, token_type_ids) 元组 / Tuple of input tensors.
        """
        tokens = ["[CLS]"] + self.tokenize(text)[: max_len - 2] + ["[SEP]"]
        input_ids = [self.vocab.get(t, self.unk_id) for t in tokens]
        attention_mask = [1] * len(input_ids)
        token_type_ids = [0] * len(input_ids)

        # Padding
        padding_len = max_len - len(input_ids)
        if padding_len > 0:
            input_ids += [self.pad_id] * padding_len
            attention_mask += [0] * padding_len
            token_type_ids += [0] * padding_len

        return input_ids, attention_mask, token_type_ids


class ONNXSmallNerEngine(SmallNerEngine):
    """基于 ONNX Runtime 的本地医疗 NER 模型推理引擎 / ONNX Runtime Medical NER Engine.

    中文说明：
    使用 ONNX Runtime 加载本地 CMeEE 医疗实体识别模型，支持延迟加载与自动降级。

    English Description:
    Loads a local CMeEE medical entity recognition model via ONNX Runtime,
    with lazy-loading and graceful degradation support.
    """

    def __init__(self, model_path: Optional[str] = None, vocab_path: Optional[str] = None):
        """初始化 ONNX NER 引擎 / Initialize ONNX NER Engine.

        Args:
            model_path: ONNX 模型文件路径 / Path to ONNX model file.
            vocab_path: vocab.txt 词表文件路径 / Path to vocab.txt file.
        """
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(os.path.dirname(current_dir))

        self.model_path = model_path or os.path.join(project_root, ".models", "raner_cmeee.onnx")
        self.vocab_path = vocab_path or os.path.join(project_root, ".models", "vocab.txt")
        self.session = None
        self.tokenizer = None
        self._initialized = False
        self._init_error = None

    def _lazy_init(self):
        """延迟加载模型 / Lazy-Load ONNX Model.

        中文说明：保障在未安装 onnxruntime 或文件未就绪时的向下兼容性。
        English Description: Ensures backward compatibility when onnxruntime is not
        installed or model files are not yet available.

        Raises:
            FileNotFoundError: 模型或词表文件不存在 / Model or vocab file not found.
        """
        if self._initialized:
            return

        if self._init_error:
            raise self._init_error

        try:
            import onnxruntime as ort

            if not os.path.exists(self.model_path):
                raise FileNotFoundError(f"未找到本地 ONNX 模型文件: {self.model_path}")
            if not os.path.exists(self.vocab_path):
                raise FileNotFoundError(f"未找到本地 vocab 词表文件: {self.vocab_path}")

            self.session = ort.InferenceSession(self.model_path)
            self.tokenizer = SimpleChineseBertTokenizer(self.vocab_path)
            self._initialized = True
            logger.info(
                "onnx_ner_engine_initialized",
                extra={"model_path": self.model_path, "engine": "onnx"},
            )
        except Exception as e:
            self._init_error = e
            logger.warning(
                "onnx_ner_engine_init_failed",
                extra={"error": str(e), "model_path": self.model_path},
            )
            raise e

    def _parse_bio_tags(self, tokens: List[str], label_indices: List[int], probs: List[float]) -> List[Dict[str, Any]]:
        """解析 BIO 序列标注 / Parse BIO Sequence Labels.

        中文说明：将相邻的 B- 和 I- 标记合并为完整的命名实体。
        English Description: Merges adjacent B- and I- tags into complete named entities.

        Args:
            tokens: token 序列 / Token sequence.
            label_indices: 每个 token 的预测标签索引 / Predicted label index per token.
            probs: 每个 token 的预测概率 / Prediction probability per token.

        Returns:
            命名实体字典列表 / List of named entity dictionaries.
        """
        # CMeEE 典型标签的映射
        label_map = {
            1: "B-dis", 2: "I-dis",
            3: "B-dru", 4: "I-dru",
            5: "B-pro", 6: "I-pro",
            7: "B-sym", 8: "I-sym",
            9: "B-ite", 10: "I-ite",
            11: "B-bod", 12: "I-bod",
        }

        entities: List[Dict[str, Any]] = []
        current_entity: Optional[Dict[str, Any]] = None

        # 忽略 index 0 的 [CLS] 以及最后的 [SEP]/[PAD]
        for idx in range(1, len(tokens) - 1):
            token = tokens[idx]
            if token == "[SEP]" or token == "[PAD]":
                break

            label_idx = label_indices[idx]
            prob = probs[idx]
            tag = label_map.get(label_idx, "O")

            if tag.startswith("B-"):
                if current_entity:
                    entities.append(current_entity)
                ent_type = tag.split("-")[1]
                current_entity = {
                    "text": token,
                    "label": ent_type,
                    "confidence": prob,
                }
            elif tag.startswith("I-") and current_entity:
                ent_type = tag.split("-")[1]
                if ent_type == current_entity["label"]:
                    # 合并当前中文字符
                    current_entity["text"] += token
                    current_entity["confidence"] = min(current_entity["confidence"], prob)
                else:
                    entities.append(current_entity)
                    current_entity = None
            else:
                if current_entity:
                    entities.append(current_entity)
                    current_entity = None

        if current_entity:
            entities.append(current_entity)

        return entities

    def extract(self, text: str) -> List[Dict[str, Any]]:
        """提取输入文本中的医疗实体 / Extract Medical Entities from Text.

        执行步骤 / Execution Steps:
        1. 延迟初始化 ONNX 会话（若尚未加载）。
           (Lazy-initialize ONNX session if not yet loaded)
        2. 使用 BERT Tokenizer 对文本进行分词编码。
           (Tokenize and encode text using BERT Tokenizer)
        3. 执行 ONNX 推理并计算 Softmax 概率。
           (Run ONNX inference and compute Softmax probabilities)
        4. 解析 BIO 标签并映射为统一标准规范。
           (Parse BIO tags and map to unified standard labels)

        Args:
            text: 目标文本片段 / Target text segment.

        Returns:
            表示命名实体的字典列表 / List of named entity dictionaries.
        """
        try:
            self._lazy_init()
        except Exception:
            CLASSIFICATION_NER_TOTAL.labels(status="init_failed").inc()
            return []

        start_time = time.monotonic()
        try:
            # 分词编码
            max_len = 128
            input_ids, attention_mask, token_type_ids = self.tokenizer.encode(text, max_len=max_len)

            # 组装 ONNX 输入参数
            inputs = {
                "input_ids": [input_ids],
                "attention_mask": [attention_mask],
                "token_type_ids": [token_type_ids],
            }

            # 执行推理
            outputs = self.session.run(None, inputs)
            logits = outputs[0][0]  # 取出 batch 第一维，shape 为 (seq_len, num_labels)

            # 计算各维度的 Softmax 概率
            import numpy as np

            exp_logits = np.exp(logits - np.max(logits, axis=-1, keepdims=True))
            probs = exp_logits / np.sum(exp_logits, axis=-1, keepdims=True)

            label_indices = np.argmax(probs, axis=-1).tolist()
            token_probs = [probs[i, label_indices[i]] for i in range(len(label_indices))]

            tokens = ["[CLS]"] + self.tokenizer.tokenize(text)[: max_len - 2] + ["[SEP]"]

            # 解析实体
            entities = self._parse_bio_tags(tokens, label_indices, token_probs)

            # 映射实体标签至统一标准规范
            for ent in entities:
                raw_label = ent["label"]
                if raw_label in ("dis", "sym", "mic"):
                    ent["label"] = "MEDICAL_DISEASE"
                elif raw_label == "dru":
                    ent["label"] = "MEDICATION"
                elif raw_label == "pro":
                    ent["label"] = "SURGERY"
                elif raw_label == "bod":
                    ent["label"] = "BODY_PART"

            duration = time.monotonic() - start_time
            CLASSIFICATION_NER_TOTAL.labels(status="success").inc()
            CLASSIFICATION_NER_DURATION.labels(engine="onnx").observe(duration)
            logger.debug(
                "onnx_ner_extract_completed",
                extra={"entity_count": len(entities), "duration_s": round(duration, 4)},
            )
            return entities

        except Exception as e:
            duration = time.monotonic() - start_time
            CLASSIFICATION_NER_TOTAL.labels(status="error").inc()
            CLASSIFICATION_NER_DURATION.labels(engine="onnx").observe(duration)
            logger.warning(
                "onnx_ner_extract_error",
                extra={"error": str(e), "duration_s": round(duration, 4)},
            )
            return []


class ModelScopeSmallNerEngine(SmallNerEngine):
    """基于 ModelScope 官方推理管道的本地医疗 NER 引擎 / ModelScope Medical NER Engine.

    中文说明：
    使用达摩院 RaNER 医疗实体识别微调模型，支持延迟加载与自动降级。

    English Description:
    Uses DAMO Academy RaNER medical entity recognition fine-tuned model via ModelScope
    pipeline, with lazy-loading and graceful degradation support.
    """

    def __init__(self, model_id: str = "damo/nlp_raner_named-entity-recognition_chinese-base-cmeee"):
        """初始化 ModelScope NER 引擎 / Initialize ModelScope NER Engine.

        Args:
            model_id: ModelScope 上的模型 ID / Model ID on ModelScope,
                默认使用达摩院 RaNER 医疗实体识别微调模型。
                (Defaults to DAMO Academy RaNER medical NER fine-tuned model)
        """
        self.model_id = model_id
        # 优先使用 download_ner_model.py 下载到本地的模型仓库目录，避免推理时
        # 再次从 ModelScope Hub 拉取（离线/内网部署友好）。
        # (Prefer the local snapshot downloaded by download_ner_model.py to avoid
        #  re-fetching from the ModelScope Hub at inference time.)
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(os.path.dirname(current_dir))
        local_model_dir = os.path.join(project_root, ".models", "raner_cmeee")
        self.local_model_dir = local_model_dir
        self.pipeline = None
        self._initialized = False
        self._init_error = None

    def _lazy_init(self):
        """延迟加载 ModelScope 管道 / Lazy-Load ModelScope Pipeline.

        中文说明：保障未安装 modelscope 或无 PyTorch 时的向下兼容性。
        English Description: Ensures backward compatibility when modelscope or
        PyTorch is not installed.

        Raises:
            Exception: 初始化失败时抛出 / Raised when initialization fails.
        """
        if self._initialized:
            return

        if self._init_error:
            raise self._init_error

        try:
            # 兼容性适配：较新版本的 transformers 移除了 transformers.onnx，
            # 但 ModelScope 官方预置的医学 NER 推理脚本中仍有该 legacy 导入。
            # 我们在 sys.modules 中动态注入一个 Dummy 模块，并提供所需的 OnnxConfig 占位以确保向下兼容。
            import sys
            import types

            if "transformers.onnx" not in sys.modules:
                dummy_onnx = types.ModuleType("transformers.onnx")

                class DummyOnnxConfig:
                    pass

                dummy_onnx.OnnxConfig = DummyOnnxConfig
                dummy_onnx.OnnxConfigWithPast = DummyOnnxConfig
                sys.modules["transformers.onnx"] = dummy_onnx

            # 兼容性适配：有些 ModelScope 模型的自研 Config 未正确初始化 PretrainedConfig 相关的类属性，
            # 导致在较新版本的 transformers 下读取 BertModel 时报错。我们在此动态添加类默认属性以规避异常。
            from transformers import PretrainedConfig, PreTrainedModel
            PretrainedConfig.is_decoder = False
            PretrainedConfig.add_cross_attention = False
            PretrainedConfig.bad_words_ids = None
            PretrainedConfig.chunk_size_feed_forward = 0
            PretrainedConfig.pruned_heads = {}
            PretrainedConfig.tie_word_embeddings = True

            # 运行时动态参数修正适配：ModelScope 的 BertModel 推理时会将 torch.device 作为第三位位置参数传入，
            # 较新版本 transformers 的 get_extended_attention_mask 已经彻底移除了 device 参数（直接由 tensor.device 自动推导），
            # 并将第三位形参改为了 dtype。我们在此对该方法进行切面拦截，自动丢弃传入的 device 传参。
            orig_get_extended_attention = PreTrainedModel.get_extended_attention_mask

            def patched_get_extended_attention_mask(self, attention_mask, input_shape, *args, **kwargs):
                import torch
                new_args = list(args)
                if len(new_args) > 0 and isinstance(new_args[0], torch.device):
                    new_args = new_args[1:]
                kwargs.pop("device", None)
                return orig_get_extended_attention(self, attention_mask, input_shape, *new_args, **kwargs)

            PreTrainedModel.get_extended_attention_mask = patched_get_extended_attention_mask

            # 兼容性适配：ModelScope 的 BertModel 未继承 PreTrainedModel，缺失 get_head_mask 方法。
            # 我们在此动态将 PreTrainedModel 的 get_head_mask 绑定至 ModelScope 的 BertModel 类上。
            try:
                from modelscope.models.nlp.bert.backbone import BertModel
                if not hasattr(BertModel, "get_head_mask"):
                    BertModel.get_head_mask = PreTrainedModel.get_head_mask
            except ImportError:
                pass

            from modelscope.pipelines import pipeline
            from modelscope.utils.constant import Tasks

            # 优先加载本地已下载的模型目录，否则回退至 ModelScope Hub 模型 ID。
            # (Load the locally downloaded model directory first; fall back to the Hub ID.)
            model_ref = self.model_id
            if os.path.isdir(self.local_model_dir):
                model_ref = self.local_model_dir

            logger.info(
                "modelscope_ner_pipeline_loading",
                extra={"model_id": self.model_id, "model_ref": model_ref},
            )
            self.pipeline = pipeline(Tasks.named_entity_recognition, model=model_ref)
            self._initialized = True
            logger.info(
                "modelscope_ner_engine_initialized",
                extra={"model_id": self.model_id, "engine": "modelscope"},
            )
        except Exception as e:
            self._init_error = e
            logger.warning(
                "modelscope_ner_engine_init_failed",
                extra={"error": str(e), "model_id": self.model_id},
            )
            raise e

    def extract(self, text: str) -> List[Dict[str, Any]]:
        """调用 ModelScope pipeline 提取命名实体 / Extract Entities via ModelScope Pipeline.

        执行步骤 / Execution Steps:
        1. 延迟初始化 ModelScope 管道（若尚未加载）。
           (Lazy-initialize ModelScope pipeline if not yet loaded)
        2. 调用 pipeline 获取 NER 输出。
           (Invoke pipeline to get NER output)
        3. 映射原始标签至统一标准规范。
           (Map raw labels to unified standard categories)

        Args:
            text: 目标文本 / Target text.

        Returns:
            命名实体字典列表 / List of named entity dictionaries.
        """
        try:
            self._lazy_init()
        except Exception:
            CLASSIFICATION_NER_TOTAL.labels(status="init_failed").inc()
            return []

        start_time = time.monotonic()
        try:
            # ModelScope 命名实体识别管道输出示例：
            # {'output': [{'type': 'dis', 'start': 11, 'end': 17, 'span': '急性心肌梗死'}]}
            res = self.pipeline(text)
            output = res.get("output", [])

            entities: List[Dict[str, Any]] = []
            for item in output:
                raw_label = item.get("type", "")
                span = item.get("span", "")

                # 映射到标准化的敏感标签类别
                label = raw_label
                if raw_label in ("dis", "sym", "mic"):
                    label = "MEDICAL_DISEASE"
                elif raw_label == "dru":
                    label = "MEDICATION"
                elif raw_label == "pro":
                    label = "SURGERY"
                elif raw_label == "bod":
                    label = "BODY_PART"
                elif raw_label == "GENE":
                    label = "GENOMIC_HINT"

                entities.append(
                    {
                        "text": span,
                        "label": label,
                        "confidence": 1.0,  # 默认置信度归一化为 1.0
                    }
                )

            duration = time.monotonic() - start_time
            CLASSIFICATION_NER_TOTAL.labels(status="success").inc()
            CLASSIFICATION_NER_DURATION.labels(engine="modelscope").observe(duration)
            logger.debug(
                "modelscope_ner_extract_completed",
                extra={"entity_count": len(entities), "duration_s": round(duration, 4)},
            )
            return entities
        except Exception as e:
            duration = time.monotonic() - start_time
            CLASSIFICATION_NER_TOTAL.labels(status="error").inc()
            CLASSIFICATION_NER_DURATION.labels(engine="modelscope").observe(duration)
            logger.warning(
                "modelscope_ner_extract_error",
                extra={"error": str(e), "duration_s": round(duration, 4)},
            )
            return []
