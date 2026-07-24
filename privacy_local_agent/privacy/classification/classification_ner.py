"""基于 ONNX Runtime 的本地轻量级命名实体识别（Small-NER）引擎。

中文说明：
提供纯 Python 实现的 BERT Tokenizer 以及高效的 BIO 标记解析器。
支持 ONNX Runtime 和 ModelScope 两种推理后端，均具备延迟加载与自动降级能力。

本模块是三层分类漏斗的第二层（Layer-2），在规则引擎（Layer-1）之后执行。
当规则引擎无法确定分类结果（置信度不足或等级 <= L3）时，NER 引擎通过
识别文本中的医疗实体（疾病、药物、手术、身体部位等）来辅助分类决策。

架构设计：
- SimpleChineseBertTokenizer：纯 Python 分词器，无第三方依赖
- ONNXSmallNerEngine：ONNX Runtime 推理后端（推荐，轻量高效）
- ModelScopeSmallNerEngine：ModelScope 管道推理后端（兼容，需 PyTorch）

降级策略：
- onnxruntime 未安装或模型文件不存在 → 回退到 ModelScope 引擎
- modelscope 未安装或 PyTorch 不可用 → 回退到 NoOpSmallNerEngine（空实现）

English Description:
Local lightweight Named Entity Recognition (Small-NER) engine based on ONNX Runtime.
Provides a pure-Python BERT Tokenizer and an efficient BIO tag parser.
Supports both ONNX Runtime and ModelScope inference backends with lazy-loading
and graceful degradation capabilities.
"""

# 启用延迟注解求值，允许在类型提示中引用尚未定义的类名
from __future__ import annotations

# 导入操作系统接口，用于文件路径拼接和存在性检查
import os
# 导入时间模块，用于测量推理耗时
import time
# 导入类型注解工具：Any 用于通用类型，cast 用于类型断言
from typing import Any, cast

# 导入结构化日志工厂函数
from ...observability.logging_config import get_logger
# 导入 Prometheus 指标：
# - CLASSIFICATION_NER_DURATION：NER 推理延迟直方图（按引擎标签）
# - CLASSIFICATION_NER_TOTAL：NER 调用次数计数器（按状态标签）
from ...observability.metrics import (
    CLASSIFICATION_NER_DURATION,
    CLASSIFICATION_NER_TOTAL,
)
# 导入 Small-NER 引擎抽象基类
from .classification_models import SmallNerEngine

# 创建模块级结构化日志器
logger = get_logger(__name__)


class SimpleChineseBertTokenizer:
    """纯 Python 实现的轻量级中文 BERT 分词器 / Lightweight Chinese BERT Tokenizer.

    设计目标：
    - 零第三方依赖：不依赖 transformers / tokenizers / jieba 等库
    - 毫秒级分词：简单的字符级切分 + 词表查找
    - 大小写折叠：兼容医学缩写（如 HIV → hiv）

    分词策略：
    - 中文：逐字切分（每个汉字为一个 token）
    - 英文：逐字母切分 + 大小写折叠
    - 未登录词：映射为 [UNK]

    English Description:
    A pure-Python lightweight Chinese BERT tokenizer with no third-party tokenization
    library dependencies (e.g. transformers / tokenizers), ensuring millisecond-level
    inference efficiency and compatibility.
    """

    def __init__(self, vocab_path: str):
        """初始化分词器 / Initialize Tokenizer.

        执行步骤 / Execution Steps:
        1. 逐行读取 vocab.txt 构建 token→id 映射（行号即为 ID）。
        2. 缓存特殊 token ID（[UNK], [CLS], [SEP], [PAD]）。

        vocab.txt 格式：每行一个 token，行号（从0开始）即为该 token 的 ID。
        例如第 0 行是 [PAD]，第 101 行是 [CLS]。

        Args:
            vocab_path: vocab.txt 词表文件路径 / Path to vocab.txt vocabulary file.
        """
        # 初始化词表字典：token字符串 → 整数ID
        self.vocab: dict[str, int] = {}
        # 逐行读取词表文件，行号即为 token ID
        with open(vocab_path, encoding="utf-8") as f:
            for idx, line in enumerate(f):
                token = line.strip()  # 去除行尾换行符
                self.vocab[token] = idx  # 建立 token → ID 映射

        # 缓存特殊 token 的 ID（带默认值防止词表不完整）
        self.unk_id = self.vocab.get("[UNK]", 100)  # 未登录词 ID
        self.cls_id = self.vocab.get("[CLS]", 101)  # 序列起始标记 ID
        self.sep_id = self.vocab.get("[SEP]", 102)  # 序列结束标记 ID
        self.pad_id = self.vocab.get("[PAD]", 0)    # 填充标记 ID

    def tokenize(self, text: str) -> list[str]:
        """对中文进行单字/字符级切分 / Tokenize Chinese Text at Character Level.

        分词逻辑：
        1. 遍历文本中的每个字符
        2. 如果字符在词表中 → 直接使用
        3. 如果是字母且小写形式在词表中 → 使用小写形式（大小写折叠）
        4. 否则 → 替换为 [UNK]

        大小写折叠说明：
        中文 BERT 词表通常只包含小写英文字母，但医学文本中常出现
        大写缩写（如 HIV、AIDS、BRCA1），折叠为小写可提升识别稳定性。

        Args:
            text: 待分词的文本 / Text to tokenize.

        Returns:
            token 列表 / List of tokens.
        """
        tokens: list[str] = []  # 存放分词结果
        for char in text:
            # 情况1：字符直接在词表中（中文汉字、小写字母、数字等）
            if char in self.vocab:
                tokens.append(char)
            # 情况2：字母的大写形式不在词表，但小写形式在（大小写折叠）
            elif char.isalpha() and char.lower() in self.vocab:
                tokens.append(char.lower())
            # 情况3：完全未登录的字符（特殊符号等）
            else:
                tokens.append("[UNK]")
        return tokens

    def encode(self, text: str, max_len: int = 128) -> tuple[list[int], list[int], list[int]]:
        """将文本编码为 BERT 输入张量数据结构 / Encode Text to BERT Input Tensors.

        BERT 输入格式：[CLS] token1 token2 ... tokenN [SEP] [PAD] [PAD] ...

        执行步骤 / Execution Steps:
        1. 分词并在首尾添加 [CLS] 和 [SEP] 特殊标记。
        2. 将 token 映射为 vocab ID（input_ids）。
        3. 生成 attention_mask（有效位置为1，填充位置为0）。
        4. 生成 token_type_ids（单句输入全为0）。
        5. 按 max_len 进行右侧 padding 对齐。

        Args:
            text: 待编码文本 / Text to encode.
            max_len: 最大序列长度（默认128） / Maximum sequence length.

        Returns:
            (input_ids, attention_mask, token_type_ids) 三元组，每个都是长度为 max_len 的整数列表。
        """
        # 构造 token 序列：[CLS] + 分词结果（截断到 max_len-2）+ [SEP]
        tokens = ["[CLS]", *self.tokenize(text)[:max_len - 2], "[SEP]"]
        # 将 token 映射为词表 ID，未登录词使用 unk_id
        input_ids = [self.vocab.get(t, self.unk_id) for t in tokens]
        # attention_mask：有效 token 位置为 1（模型应关注这些位置）
        attention_mask = [1] * len(input_ids)
        # token_type_ids：单句输入全为 0（区分句子A/句子B，此处只有句子A）
        token_type_ids = [0] * len(input_ids)

        # 右侧 Padding：将序列补齐到 max_len 长度
        padding_len = max_len - len(input_ids)
        if padding_len > 0:
            input_ids += [self.pad_id] * padding_len      # 填充位使用 pad_id
            attention_mask += [0] * padding_len            # 填充位 mask 为 0（忽略）
            token_type_ids += [0] * padding_len            # 填充位 type 为 0

        return input_ids, attention_mask, token_type_ids


class ONNXSmallNerEngine(SmallNerEngine):
    """基于 ONNX Runtime 的本地医疗 NER 模型推理引擎 / ONNX Runtime Medical NER Engine.

    使用 ONNX Runtime 加载本地 CMeEE（中文医学命名实体识别）模型。
    模型基于 BERT 架构，输出 BIO 序列标注（B-dis/I-dis/B-dru/I-dru 等）。

    特性：
    - 延迟加载：首次调用 extract() 时才加载模型（避免启动阻塞）
    - 自动降级：onnxruntime 未安装或模型文件不存在时抛出异常，
      调用方捕获后回退到 ModelScope 引擎或 NoOp 空实现
    - 纯 Python 分词：不依赖 transformers 的 tokenizer

    支持的实体类型（CMeEE 标准）：
    - dis: 疾病 → 映射为 MEDICAL_DISEASE
    - dru: 药物 → 映射为 MEDICATION
    - pro: 手术/操作 → 映射为 SURGERY
    - sym: 症状 → 映射为 MEDICAL_DISEASE
    - ite: 检查项目
    - bod: 身体部位 → 映射为 BODY_PART

    English Description:
    Loads a local CMeEE medical entity recognition model via ONNX Runtime,
    with lazy-loading and graceful degradation support.
    """

    def __init__(self, model_path: str | None = None, vocab_path: str | None = None):
        """初始化 ONNX NER 引擎 / Initialize ONNX NER Engine.

        仅设置路径和状态标志，不实际加载模型（延迟加载策略）。

        Args:
            model_path: ONNX 模型文件路径（默认 .models/raner_cmeee.onnx）。
            vocab_path: vocab.txt 词表文件路径（默认 .models/vocab.txt）。
        """
        # 计算项目根目录（从当前文件向上两级）
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(os.path.dirname(current_dir))

        # 设置模型文件路径（使用默认路径或用户指定路径）
        self.model_path = model_path or os.path.join(project_root, ".models", "raner_cmeee.onnx")
        # 设置词表文件路径
        self.vocab_path = vocab_path or os.path.join(project_root, ".models", "vocab.txt")
        # ONNX 推理会话（延迟初始化）
        self.session: Any | None = None
        # BERT 分词器实例（延迟初始化）
        self.tokenizer: SimpleChineseBertTokenizer | None = None
        # 初始化状态标志
        self._initialized = False
        # 初始化错误缓存（避免重复尝试已知失败的初始化）
        self._init_error: Exception | None = None

    def _lazy_init(self):
        """延迟加载模型 / Lazy-Load ONNX Model.

        首次调用时执行实际的模型加载：
        1. 检查 onnxruntime 是否可用
        2. 验证模型文件和词表文件是否存在
        3. 创建 ONNX InferenceSession
        4. 初始化 BERT 分词器

        如果初始化失败，缓存错误并在后续调用中直接抛出（不重复尝试）。

        Raises:
            FileNotFoundError: 模型或词表文件不存在。
            ImportError: onnxruntime 未安装。
        """
        # 已初始化则直接返回（避免重复加载）
        if self._initialized:
            return

        # 之前初始化失败过，直接抛出缓存的错误（不重复尝试）
        if self._init_error:
            raise self._init_error

        try:
            # 尝试导入 onnxruntime（未安装时抛出 ImportError）
            import onnxruntime as ort

            # 验证 ONNX 模型文件存在
            if not os.path.exists(self.model_path):
                raise FileNotFoundError(f"未找到本地 ONNX 模型文件: {self.model_path}")
            # 验证词表文件存在
            if not os.path.exists(self.vocab_path):
                raise FileNotFoundError(f"未找到本地 vocab 词表文件: {self.vocab_path}")

            # 创建 ONNX 推理会话（加载模型到内存）
            self.session = ort.InferenceSession(self.model_path)
            # 初始化纯 Python BERT 分词器
            self.tokenizer = SimpleChineseBertTokenizer(self.vocab_path)
            # 标记初始化成功
            self._initialized = True
            # 记录初始化成功日志
            logger.info(
                "onnx_ner_engine_initialized",
                extra={"model_path": self.model_path, "engine": "onnx"},
            )
        except Exception as e:
            # 缓存初始化错误，后续调用直接抛出
            self._init_error = e
            # 记录初始化失败警告日志
            logger.warning(
                "onnx_ner_engine_init_failed",
                extra={"error": str(e), "model_path": self.model_path},
            )
            raise e

    def _parse_bio_tags(self, tokens: list[str], label_indices: list[int], probs: list[float]) -> list[dict[str, Any]]:
        """解析 BIO 序列标注 / Parse BIO Sequence Labels.

        BIO 标注方案：
        - B-XXX：实体起始（Begin）
        - I-XXX：实体内部（Inside）
        - O：非实体（Outside）

        状态机逻辑：
        - 遇到 B- 标签：开始新实体（如果前一个实体未完成则先保存）
        - 遇到 I- 标签且类型匹配：合并到当前实体
        - 遇到 I- 标签但类型不匹配：结束当前实体，丢弃不匹配的 I-
        - 遇到 O 标签：结束当前实体

        Args:
            tokens: token 序列（含 [CLS]/[SEP]） / Token sequence.
            label_indices: 每个 token 的预测标签索引 / Predicted label index per token.
            probs: 每个 token 的预测概率 / Prediction probability per token.

        Returns:
            命名实体字典列表，每个字典含 text/label/confidence。
        """
        # CMeEE 标签索引映射表（索引 0 为 O，1-12 为 B/I 标签对）
        label_map = {
            1: "B-dis", 2: "I-dis",    # 疾病（disease）
            3: "B-dru", 4: "I-dru",    # 药物（drug）
            5: "B-pro", 6: "I-pro",    # 手术/操作（procedure）
            7: "B-sym", 8: "I-sym",    # 症状（symptom）
            9: "B-ite", 10: "I-ite",   # 检查项目（item）
            11: "B-bod", 12: "I-bod",  # 身体部位（body）
        }

        entities: list[dict[str, Any]] = []  # 已完成的实体列表
        current_entity: dict[str, Any] | None = None  # 当前正在构建的实体

        # 遍历 token 序列（跳过 index 0 的 [CLS] 和末尾的 [SEP]/[PAD]）
        for idx in range(1, len(tokens) - 1):
            token = tokens[idx]
            # 遇到 [SEP] 或 [PAD] 表示有效序列结束
            if token == "[SEP]" or token == "[PAD]":
                break

            # 获取当前 token 的预测标签和概率
            label_idx = label_indices[idx]
            prob = probs[idx]
            # 查表获取 BIO 标签字符串，未命中则为 "O"
            tag = label_map.get(label_idx, "O")

            if tag.startswith("B-"):
                # B- 标签：新实体开始
                # 如果前一个实体尚未保存，先保存
                if current_entity:
                    entities.append(current_entity)
                # 提取实体类型（如 "B-dis" → "dis"）
                ent_type = tag.split("-")[1]
                # 开始构建新实体
                current_entity = {
                    "text": token,        # 实体文本（逐字累积）
                    "label": ent_type,    # 实体类型
                    "confidence": prob,   # 置信度（取所有字的最小值）
                }
            elif tag.startswith("I-") and current_entity:
                # I- 标签：实体内部（需要有正在构建的实体）
                ent_type = tag.split("-")[1]
                if ent_type == current_entity["label"]:
                    # 类型匹配：合并当前字符到实体文本
                    current_entity["text"] += token
                    # 置信度取最小值（木桶原则：整体置信度取决于最不确定的字）
                    current_entity["confidence"] = min(current_entity["confidence"], prob)
                else:
                    # 类型不匹配：结束当前实体，丢弃不匹配的 I- 标签
                    entities.append(current_entity)
                    current_entity = None
            else:
                # O 标签或无当前实体时的 I- 标签：结束当前实体
                if current_entity:
                    entities.append(current_entity)
                    current_entity = None

        # 序列结束后，如果还有未保存的实体则保存
        if current_entity:
            entities.append(current_entity)

        return entities

    def extract(self, text: str) -> list[dict[str, Any]]:
        """提取输入文本中的医疗实体 / Extract Medical Entities from Text.

        完整推理流程：
        1. 延迟初始化 ONNX 会话（首次调用时加载模型）。
        2. 使用 BERT Tokenizer 对文本进行分词编码。
        3. 执行 ONNX 推理获取 logits。
        4. 计算 Softmax 概率分布。
        5. 取 argmax 得到预测标签索引。
        6. 解析 BIO 标签序列为实体列表。
        7. 映射原始标签到统一标准类别。

        Args:
            text: 目标文本片段 / Target text segment.

        Returns:
            命名实体字典列表，每个字典含 text/label/confidence。
            初始化失败或推理异常时返回空列表（优雅降级）。
        """
        try:
            # 延迟初始化（首次调用时加载模型）
            self._lazy_init()
        except Exception:
            # 初始化失败：递增失败计数指标，返回空列表
            CLASSIFICATION_NER_TOTAL.labels(status="init_failed").inc()
            return []

        # 断言初始化成功（类型检查用）
        assert self.tokenizer is not None and self.session is not None
        # 记录推理开始时间
        start_time = time.monotonic()
        try:
            # === 步骤1：分词编码 ===
            max_len = 128  # 最大序列长度
            # 将文本编码为 BERT 输入格式（input_ids, attention_mask, token_type_ids）
            input_ids, attention_mask, token_type_ids = self.tokenizer.encode(text, max_len=max_len)

            # === 步骤2：组装 ONNX 输入 ===
            # ONNX 模型期望 batch 维度，所以包装为列表
            inputs = {
                "input_ids": [input_ids],
                "attention_mask": [attention_mask],
                "token_type_ids": [token_type_ids],
            }

            # === 步骤3：执行 ONNX 推理 ===
            outputs = self.session.run(None, inputs)
            # 取出第一个输出（logits），去掉 batch 维度
            # shape: (seq_len, num_labels)，如 (128, 13)
            logits = outputs[0][0]

            # === 步骤4：计算 Softmax 概率 ===
            import numpy as np

            # 数值稳定的 Softmax：先减去最大值防止 exp 溢出
            exp_logits = np.exp(logits - np.max(logits, axis=-1, keepdims=True))
            # 归一化得到概率分布
            probs = exp_logits / np.sum(exp_logits, axis=-1, keepdims=True)

            # === 步骤5：取 argmax 得到预测标签 ===
            label_indices = np.argmax(probs, axis=-1).tolist()  # 每个位置的最大概率标签索引
            # 提取每个位置对应预测标签的概率值
            token_probs = [probs[i, label_indices[i]] for i in range(len(label_indices))]

            # 重建 token 序列（与编码时一致，用于 BIO 解析）
            tokens = ["[CLS]", *self.tokenizer.tokenize(text)[:max_len - 2], "[SEP]"]

            # === 步骤6：解析 BIO 标签为实体列表 ===
            entities = self._parse_bio_tags(tokens, label_indices, token_probs)

            # === 步骤7：映射实体标签到统一标准类别 ===
            for ent in entities:
                raw_label = ent["label"]
                # 疾病/症状/微生物 → 统一为 MEDICAL_DISEASE
                if raw_label in ("dis", "sym", "mic"):
                    ent["label"] = "MEDICAL_DISEASE"
                # 药物 → MEDICATION
                elif raw_label == "dru":
                    ent["label"] = "MEDICATION"
                # 手术/操作 → SURGERY
                elif raw_label == "pro":
                    ent["label"] = "SURGERY"
                # 身体部位 → BODY_PART
                elif raw_label == "bod":
                    ent["label"] = "BODY_PART"

            # 计算推理耗时并记录指标
            duration = time.monotonic() - start_time
            CLASSIFICATION_NER_TOTAL.labels(status="success").inc()  # 成功计数 +1
            CLASSIFICATION_NER_DURATION.labels(engine="onnx").observe(duration)  # 记录延迟
            # 输出调试日志
            logger.debug(
                "onnx_ner_extract_completed",
                extra={"entity_count": len(entities), "duration_s": round(duration, 4)},
            )
            return entities

        except Exception as e:
            # 推理异常：记录错误指标和日志，返回空列表（优雅降级）
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

    使用达摩院 RaNER 医疗实体识别微调模型（CMeEE 数据集），
    通过 ModelScope pipeline 接口进行推理。

    与 ONNXSmallNerEngine 的区别：
    - 需要 PyTorch + transformers + modelscope 完整依赖
    - 推理速度稍慢（PyTorch 动态图 vs ONNX 静态图）
    - 兼容性更好（不需要手动转换 ONNX 格式）
    - 包含多项兼容性 Patch（适配不同版本的 transformers/modelscope）

    降级策略：
    - modelscope 未安装 → 抛出 ImportError → 回退到 NoOpSmallNerEngine
    - PyTorch 不可用 → 抛出异常 → 回退到 NoOpSmallNerEngine

    English Description:
    Uses DAMO Academy RaNER medical entity recognition fine-tuned model via ModelScope
    pipeline, with lazy-loading and graceful degradation support.
    """

    def __init__(self, model_id: str = "damo/nlp_raner_named-entity-recognition_chinese-base-cmeee"):
        """初始化 ModelScope NER 引擎 / Initialize ModelScope NER Engine.

        仅设置模型引用和状态标志，不实际加载模型（延迟加载策略）。

        Args:
            model_id: ModelScope 上的模型 ID，默认使用达摩院 RaNER CMeEE 微调模型。
        """
        # 保存模型 ID（用于从 Hub 下载或标识本地模型）
        self.model_id = model_id
        # 计算本地模型目录路径（download_ner_model.py 下载的位置）
        # 优先使用本地已下载的模型，避免推理时再次从 Hub 拉取（离线友好）
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(os.path.dirname(current_dir))
        local_model_dir = os.path.join(project_root, ".models", "raner_cmeee")
        self.local_model_dir = local_model_dir
        # ModelScope pipeline 实例（延迟初始化）
        self.pipeline: Any | None = None
        # 初始化状态标志
        self._initialized = False
        # 初始化错误缓存
        self._init_error: Exception | None = None

    def _lazy_init(self):
        """延迟加载 ModelScope 管道 / Lazy-Load ModelScope Pipeline.

        首次调用时执行实际的管道初始化，包含多项兼容性 Patch：

        Patch 1: transformers.onnx Dummy 模块注入
        - 问题：新版 transformers 移除了 transformers.onnx 模块，
          但 ModelScope 的推理脚本仍有该 legacy 导入
        - 方案：在 sys.modules 中注入 Dummy 模块

        Patch 2: PretrainedConfig 属性注入
        - 问题：ModelScope 模型的 Config 未正确初始化某些类属性
        - 方案：动态添加默认属性值

        Patch 3: get_extended_attention_mask 切面拦截
        - 问题：ModelScope 传入 torch.device 作为第三参数，
          但新版 transformers 已将该参数改为 dtype
        - 方案：拦截方法调用，自动丢弃 device 参数

        Patch 4: get_head_mask 方法绑定
        - 问题：ModelScope 的 BertModel 未继承 PreTrainedModel，缺失该方法
        - 方案：动态绑定 PreTrainedModel.get_head_mask

        Raises:
            Exception: 初始化失败时抛出。
        """
        # 已初始化则直接返回
        if self._initialized:
            return

        # 之前初始化失败过，直接抛出缓存的错误
        if self._init_error:
            raise self._init_error

        try:
            # === Patch 1: transformers.onnx Dummy 模块注入 ===
            import sys
            import types

            # 如果 transformers.onnx 不在已加载模块中，注入 Dummy
            if "transformers.onnx" not in sys.modules:
                dummy_onnx = types.ModuleType("transformers.onnx")

                # 创建占位类（ModelScope 脚本只需要这些名字存在）
                class DummyOnnxConfig:
                    pass

                # 注入 OnnxConfig 和 OnnxConfigWithPast 占位
                cast("Any", dummy_onnx).OnnxConfig = DummyOnnxConfig
                cast("Any", dummy_onnx).OnnxConfigWithPast = DummyOnnxConfig
                # 注册到 sys.modules，使 import transformers.onnx 不报错
                sys.modules["transformers.onnx"] = dummy_onnx

            # === Patch 2: PretrainedConfig 属性注入 ===
            from transformers import PretrainedConfig, PreTrainedModel
            # 动态添加 ModelScope 模型可能缺失的类属性默认值
            PretrainedConfig.is_decoder = False                # 是否为解码器
            PretrainedConfig.add_cross_attention = False       # 是否添加交叉注意力
            cast("Any", PretrainedConfig).bad_words_ids = None # 禁止词 ID 列表
            PretrainedConfig.chunk_size_feed_forward = 0       # 前馈分块大小
            PretrainedConfig.pruned_heads = {}                 # 已剪枝的注意力头
            PretrainedConfig.tie_word_embeddings = True        # 是否共享嵌入权重

            # === Patch 3: get_extended_attention_mask 切面拦截 ===
            # 保存原始方法引用
            orig_get_extended_attention = PreTrainedModel.get_extended_attention_mask

            def patched_get_extended_attention_mask(self, attention_mask, input_shape, *args, **kwargs):
                """修补版：自动丢弃 ModelScope 传入的 torch.device 参数。"""
                import torch
                new_args = list(args)
                # 如果第一个位置参数是 torch.device，则丢弃它
                if len(new_args) > 0 and isinstance(new_args[0], torch.device):
                    new_args = new_args[1:]
                # 同时丢弃关键字参数中的 device
                kwargs.pop("device", None)
                # 调用原始方法
                return orig_get_extended_attention(self, attention_mask, input_shape, *new_args, **kwargs)

            # 用修补版替换原始方法
            cast("Any", PreTrainedModel).get_extended_attention_mask = patched_get_extended_attention_mask

            # === Patch 4: get_head_mask 方法绑定 ===
            try:
                from modelscope.models.nlp.bert.backbone import BertModel
                # 如果 ModelScope 的 BertModel 缺少 get_head_mask 方法，则绑定
                if not hasattr(BertModel, "get_head_mask"):
                    BertModel.get_head_mask = PreTrainedModel.get_head_mask
            except ImportError:
                pass  # 如果无法导入 ModelScope 的 BertModel，跳过此 Patch

            # === 加载 ModelScope Pipeline ===
            from modelscope.pipelines import pipeline
            from modelscope.utils.constant import Tasks

            # 优先使用本地已下载的模型目录，否则回退到 Hub 模型 ID
            model_ref = self.model_id
            if os.path.isdir(self.local_model_dir):
                model_ref = self.local_model_dir  # 使用本地目录（离线友好）

            # 记录加载日志
            logger.info(
                "modelscope_ner_pipeline_loading",
                extra={"model_id": self.model_id, "model_ref": model_ref},
            )
            # 创建命名实体识别管道
            self.pipeline = pipeline(Tasks.named_entity_recognition, model=model_ref)
            # 标记初始化成功
            self._initialized = True
            logger.info(
                "modelscope_ner_engine_initialized",
                extra={"model_id": self.model_id, "engine": "modelscope"},
            )
        except Exception as e:
            # 缓存初始化错误
            self._init_error = e
            logger.warning(
                "modelscope_ner_engine_init_failed",
                extra={"error": str(e), "model_id": self.model_id},
            )
            raise e

    def extract(self, text: str) -> list[dict[str, Any]]:
        """调用 ModelScope pipeline 提取命名实体 / Extract Entities via ModelScope Pipeline.

        执行步骤 / Execution Steps:
        1. 延迟初始化 ModelScope 管道（首次调用时加载）。
        2. 调用 pipeline 获取 NER 输出。
        3. 解析输出并映射标签到统一标准类别。

        ModelScope NER 管道输出格式：
        {'output': [{'type': 'dis', 'start': 11, 'end': 17, 'span': '急性心肌梗死'}]}

        Args:
            text: 目标文本 / Target text.

        Returns:
            命名实体字典列表，每个字典含 text/label/confidence。
            初始化失败或推理异常时返回空列表。
        """
        try:
            # 延迟初始化
            self._lazy_init()
        except Exception:
            # 初始化失败：递增失败计数，返回空列表
            CLASSIFICATION_NER_TOTAL.labels(status="init_failed").inc()
            return []

        # 断言管道已初始化
        assert self.pipeline is not None
        # 记录推理开始时间
        start_time = time.monotonic()
        try:
            # 调用 ModelScope NER 管道
            res = self.pipeline(text)
            # 提取输出实体列表
            output = res.get("output", [])

            # 构建标准化实体列表
            entities: list[dict[str, Any]] = []
            for item in output:
                raw_label = item.get("type", "")  # 原始实体类型
                span = item.get("span", "")        # 实体文本

                # 映射原始标签到统一标准类别
                label = raw_label
                if raw_label in ("dis", "sym", "mic"):
                    label = "MEDICAL_DISEASE"   # 疾病/症状/微生物
                elif raw_label == "dru":
                    label = "MEDICATION"        # 药物
                elif raw_label == "pro":
                    label = "SURGERY"           # 手术/操作
                elif raw_label == "bod":
                    label = "BODY_PART"         # 身体部位
                elif raw_label == "GENE":
                    label = "GENOMIC_HINT"      # 基因（特殊处理）

                # 构建标准化实体字典
                entities.append(
                    {
                        "text": span,           # 实体文本
                        "label": label,         # 标准化标签
                        "confidence": 1.0,      # ModelScope 管道不返回逐实体置信度，默认 1.0
                    }
                )

            # 计算推理耗时并记录指标
            duration = time.monotonic() - start_time
            CLASSIFICATION_NER_TOTAL.labels(status="success").inc()
            CLASSIFICATION_NER_DURATION.labels(engine="modelscope").observe(duration)
            logger.debug(
                "modelscope_ner_extract_completed",
                extra={"entity_count": len(entities), "duration_s": round(duration, 4)},
            )
            return entities
        except Exception as e:
            # 推理异常：记录错误指标和日志，返回空列表
            duration = time.monotonic() - start_time
            CLASSIFICATION_NER_TOTAL.labels(status="error").inc()
            CLASSIFICATION_NER_DURATION.labels(engine="modelscope").observe(duration)
            logger.warning(
                "modelscope_ner_extract_error",
                extra={"error": str(e), "duration_s": round(duration, 4)},
            )
            return []
