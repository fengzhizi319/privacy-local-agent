"""基于本地多模态大模型 Qwen2-VL-2B-Instruct 的数据分类分级器。

中文说明：
支持本地病例图像、手写病例图片以及纯文本数据的智能 OCR 识别与零样本敏感定级推理。
具备延迟加载、自动降级、多模态输入检测等企业级能力。

架构设计：
- Qwen2VLClassifier：多模态分类器主类，继承 LlmClassifier 抽象基类
- 延迟加载：首次调用 classify() 时才加载模型权重（避免启动阻塞和显存浪费）
- 双重检查锁定：线程安全的模型初始化（gRPC 多线程环境）
- 专用推理线程池：隔离推理与 gRPC 工作线程，配合超时机制防止永久阻塞
- 三级图片检测：本地路径 → Data URI Base64 → 纯 Base64 数据
- JSON 结果解析：正则提取 + 容错降级

降级策略：
- torch/transformers 未安装 → 初始化失败 → classify() 返回 None → 上层降级
- 模型目录不存在 → FileNotFoundError → 降级
- 推理超时（默认 180s）→ 放弃本次推理 → 返回 None → 降级
- JSON 解析失败 → 返回 None → 降级

English Description:
Data classification and grading engine based on local multimodal LLM Qwen2-VL-2B-Instruct.
Supports intelligent OCR recognition and zero-shot sensitivity grading for local medical
images, handwritten records, and plain text data. Features lazy-loading, graceful
degradation, and multimodal input detection capabilities.
"""

# 启用延迟注解求值，允许在类型提示中引用尚未定义的类名（如 Image.Image）
from __future__ import annotations

# 导入 base64 编解码模块，用于解码 Base64 格式的图片数据
import base64
# 导入 JSON 解析模块，用于解析大模型返回的 JSON 结构化结果
import json
# 导入操作系统接口，用于文件路径拼接、目录存在性检查、环境变量读取
import os
# 导入正则表达式模块，用于 Data URI 匹配和 JSON 提取
import re
# 导入线程模块，用于创建互斥锁保护模型初始化和推理的线程安全
import threading
# 导入时间模块，用于测量推理耗时（monotonic 单调时钟）
import time
# 导入线程池执行器，用于将模型推理隔离到专用线程（配合超时机制）
from concurrent.futures import ThreadPoolExecutor
# 导入线程池超时异常类型，用于捕获推理超时事件
from concurrent.futures import TimeoutError as FuturesTimeoutError
# 导入字节流 IO，用于将 Base64 解码后的字节包装为文件对象供 PIL 读取
from io import BytesIO
# 导入类型注解工具：TYPE_CHECKING 用于条件导入，Any 通用类型，cast 类型断言
from typing import TYPE_CHECKING, Any, cast

# 仅在类型检查时导入 PIL Image 类型（运行时不导入，避免硬依赖）
if TYPE_CHECKING:
    from PIL import Image

# 导入结构化日志工厂函数（支持 JSON 格式日志输出）
from ...observability.logging_config import get_logger
# 导入 Prometheus 指标：
# - CLASSIFICATION_LLM_DURATION：LLM 推理延迟直方图（按引擎标签）
# - CLASSIFICATION_LLM_TOTAL：LLM 调用次数计数器（按状态标签：success/error/timeout/init_failed）
from ...observability.metrics import (
    CLASSIFICATION_LLM_DURATION,
    CLASSIFICATION_LLM_TOTAL,
)
# 导入 LLM 分类器抽象基类和敏感度等级枚举
from .classification_models import LlmClassifier, SensitivityLevel
# 导入日志脱敏工具函数（对敏感路径/值进行掩码处理后再记录日志）
from .classification_utils import redact

# 创建模块级结构化日志器，用于记录 LLM 分类器相关事件
logger = get_logger(__name__)



class Qwen2VLClassifier(LlmClassifier):
    """基于本地部署 Qwen2-VL-2B-Instruct 的多模态分类器 / Qwen2-VL Multimodal Classifier.

    中文说明：
    支持对图片路径、Base64 图片以及纯文本进行 OCR、理解与敏感等级评估。
    本类是三层分类漏斗的第三层（Layer-3），在规则引擎（Layer-1）和 NER（Layer-2）
    之后执行，作为最终的兜底分类手段。

    线程安全设计：
    - _lock：互斥锁，保护模型初始化（双重检查锁定）和推理过程（串行化）
    - _executor：单线程池，将推理隔离到独立线程，配合超时机制

    English Description:
    Supports OCR, understanding, and sensitivity level assessment for image paths,
    Base64-encoded images, and plain text inputs.
    """

    # VLM 推理超时（秒）：Qwen2-VL-2B 在 CPU 上单张图片推理可能需要 60-120 秒，
    # 超时后放弃本次推理并返回 None 触发降级，避免无限阻塞 gRPC 工作线程。
    # 可通过环境变量 PRIVACY_VLM_TIMEOUT 覆盖，默认 180 秒。
    _INFERENCE_TIMEOUT = int(os.environ.get("PRIVACY_VLM_TIMEOUT", "180"))

    def __init__(self, model_path: str | None = None):
        """初始化分类器 / Initialize Classifier.

        仅设置路径和状态标志，不实际加载模型（延迟加载策略）。
        模型权重加载推迟到首次 classify() 调用时的 _lazy_init() 中执行。

        Args:
            model_path: 模型本地路径 / Local model path.
                如果不指定，默认使用项目根目录下的 .models/Qwen2-VL-2B-Instruct。
                (Defaults to .models/Qwen2-VL-2B-Instruct under project root)
        """
        # 如果未指定模型路径，自动计算默认路径
        if not model_path:
            # 获取当前文件所在目录（classification/）
            current_dir = os.path.dirname(os.path.abspath(__file__))
            # 向上两级得到项目根目录（privacy_local_agent/ 的父目录）
            project_root = os.path.dirname(os.path.dirname(current_dir))
            # 拼接默认模型目录路径
            model_path = os.path.join(project_root, ".models", "Qwen2-VL-2B-Instruct")

        # 保存模型路径供后续 _lazy_init 使用
        self.model_path = model_path
        # 模型实例占位（延迟初始化后赋值）
        self._model: Any = None
        # 处理器实例占位（用于构建模型输入张量）
        self._processor: Any = None
        # 初始化完成标志（False 表示尚未加载模型）
        self._initialized = False
        # 初始化错误缓存（记录首次失败原因，后续直接抛出不重试）
        self._init_error: Exception | None = None
        # 线程锁：gRPC 使用线程池处理请求，多个工作线程可能并发调用
        # _lazy_init / classify，需要互斥保护以防止：
        #   1. 多线程同时初始化模型导致重复加载或竞态
        #   2. 多线程同时推理导致显存/内存争用引发 OOM 崩溃
        self._lock = threading.Lock()
        # 专用推理线程池：将模型推理隔离到单独线程，配合超时机制，
        # 即使推理卡死也不会永久阻塞 gRPC 工作线程。
        # max_workers=1 确保同一时刻只有一个推理任务在执行（串行化）。
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vlm-infer")

    def _lazy_init(self):
        """延迟初始化模型 / Lazy-Initialize Model.

        中文说明：避免导入时或非 LLM 运行时占用显存或因缺少依赖报错。
        使用双重检查锁定（double-checked locking）确保线程安全：
        仅首次调用时加锁初始化，后续调用直接返回，避免锁竞争开销。

        双重检查锁定流程：
        1. 第一次检查（无锁）：快速路径，已初始化则直接返回
        2. 获取锁
        3. 第二次检查（有锁）：防止等锁期间另一线程已完成初始化
        4. 执行实际初始化逻辑

        English Description: Avoids occupying GPU memory at import time or when LLM
        is not needed, and prevents errors from missing dependencies.
        Uses double-checked locking for thread safety.

        Raises:
            FileNotFoundError: 本地模型目录不存在 / Local model directory not found.
        """
        # === 第一次检查（无锁快速路径）===
        # 已初始化则直接返回，避免不必要的锁竞争
        if self._initialized:
            return
        # 之前初始化失败过，直接抛出缓存的错误（不重复尝试加载）
        if self._init_error:
            raise self._init_error

        # === 获取互斥锁 ===
        with self._lock:
            # === 第二次检查（有锁）===
            # 另一个线程可能已在等锁期间完成初始化
            if self._initialized:
                return
            if self._init_error:
                raise self._init_error

            try:
                # 延迟导入 PyTorch（避免模块顶层导入导致的启动延迟和依赖问题）
                import torch
                # 延迟导入 transformers 库中的 Qwen2-VL 模型类和处理器
                from transformers import AutoProcessor, Qwen2VLForConditionalGeneration

                # 验证模型目录是否存在（用户需先运行下载脚本）
                if not os.path.exists(self.model_path) or not os.path.isdir(self.model_path):
                    raise FileNotFoundError(
                        f"本地模型未找到，请先运行下载脚本或下载模型至: {self.model_path}"
                    )

                # 检测计算设备，优先级：CUDA GPU > macOS MPS > CPU
                if torch.cuda.is_available():
                    # NVIDIA GPU 可用，使用 CUDA 加速
                    device = "cuda"
                elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                    # macOS Apple Silicon 的 Metal Performance Shaders 可用
                    device = "mps"
                else:
                    # 回退到 CPU 推理（速度较慢但兼容性最好）
                    device = "cpu"

                # 记录模型加载开始的结构化日志
                logger.info(
                    "qwen2vl_model_loading",
                    extra={"model_path": self.model_path, "device": device},
                )

                # 选择模型精度：CUDA 使用 FP16 节省显存，CPU/MPS 使用 FP32 保证精度
                torch_dtype = torch.float16 if device == "cuda" else torch.float32

                # 从本地目录加载预训练模型权重
                self._model = Qwen2VLForConditionalGeneration.from_pretrained(
                    self.model_path,
                    torch_dtype=torch_dtype,  # 模型精度
                    # CUDA/MPS 使用自动设备映射（多层分配到不同设备），CPU 不需要
                    device_map="auto" if device in ("cuda", "mps") else None,
                )
                # CPU/MPS 模式下手动将模型移动到目标设备
                if device in ("cpu", "mps"):
                    self._model = self._model.to(device)

                # 加载模型对应的处理器（tokenizer + image processor）
                self._processor = AutoProcessor.from_pretrained(self.model_path)
                # 标记初始化完成
                self._initialized = True
                # 记录模型初始化成功的结构化日志
                logger.info(
                    "qwen2vl_model_initialized",
                    extra={"model_path": self.model_path, "device": device, "engine": "qwen2vl"},
                )

            except Exception as e:
                # 初始化失败：缓存错误对象，后续调用直接抛出（不重复尝试）
                self._init_error = e
                # 记录初始化失败的警告日志
                logger.warning(
                    "qwen2vl_model_init_failed",
                    extra={"error": str(e), "model_path": self.model_path},
                )
                # 重新抛出异常，让调用方（classify）捕获并触发降级
                raise e

    @property
    def is_ready(self) -> bool:
        """模型是否已完成初始化且未发生错误 / Whether Model Is Ready.

        用于健康检查接口 /readyz/llm 判断 LLM 层是否可用。

        Returns:
            模型就绪状态 / Model readiness status.
        """
        # 两个条件同时满足才视为就绪：已初始化 且 无错误
        return self._initialized and self._init_error is None

    def warmup(self) -> bool:
        """主动触发模型加载 / Proactively Trigger Model Loading.

        中文说明：同步阻塞调用，建议在后台线程/协程中调用。
        服务启动时可通过 PRIVACY_WARMUP_LLM=true 环境变量触发异步预热，
        避免首次请求时因模型加载导致的高延迟。

        English Description: Synchronous blocking call; recommended to invoke in a
        background thread or coroutine.

        Returns:
            是否成功完成初始化 / Whether initialization succeeded.
        """
        try:
            # 触发延迟初始化（加载模型权重）
            self._lazy_init()
            return True  # 初始化成功
        except Exception:
            return False  # 初始化失败（依赖缺失/模型不存在等）

    def _detect_image(self, text: str) -> Image.Image | None:
        """检测输入是否为图片 / Detect if Input is an Image.

        中文说明：检测输入文本是否为本地图片路径或 Base64 编码图片，
        如果是，加载并返回 PIL.Image 实例。

        三级检测策略（按优先级）：
        1. 本地文件路径：以常见图片扩展名结尾且文件存在
        2. Data URI 格式：data:image/xxx;base64,... 前缀
        3. 纯 Base64 数据：长度 > 100 且可成功解码为图片

        English Description: Detects whether input text is a local image path or
        Base64-encoded image. If so, loads and returns a PIL.Image instance.

        Args:
            text: 输入文本 / Input text.

        Returns:
            PIL.Image 实例或 None（非图片输入） / PIL.Image instance or None.
        """
        # 尝试导入 PIL 库（未安装时返回 None，退化为纯文本处理）
        try:
            from PIL import Image
        except ImportError:
            return None

        # 去除首尾空白字符
        text_stripped = text.strip()

        # === 第 1 级检测：本地图片文件路径 ===
        # 条件：长度 < 512（路径不会太长）+ 以图片扩展名结尾 + 文件实际存在
        if (
            len(text_stripped) < 512  # 路径长度通常有限
            and any(
                text_stripped.lower().endswith(ext)
                for ext in (".jpg", ".jpeg", ".png", ".bmp", ".webp")
            )
            and os.path.exists(text_stripped)
            and os.path.isfile(text_stripped)
        ):
            try:
                # 使用 PIL 打开本地图片文件
                return Image.open(text_stripped)
            except Exception as e:
                # 文件存在但无法解析为图片（损坏/格式不支持）
                logger.warning(
                    "llm_image_load_failed",
                    extra={"path": redact(text_stripped), "error": str(e)},
                )

        # === 第 2 级检测：Data URI 格式的 Base64 图片 ===
        # 匹配格式：data:image/png;base64,iVBORw0KGgoAAA...
        data_uri_match = re.match(r"^data:image\/[a-zA-Z]+;base64,(.+)$", text_stripped)
        if data_uri_match:
            try:
                # 提取 Base64 编码部分（去掉 data:image/xxx;base64, 前缀）
                base64_data = data_uri_match.group(1)
                # 解码 Base64 为原始字节
                image_bytes = base64.b64decode(base64_data)
                # 将字节流包装为文件对象并用 PIL 打开
                return Image.open(BytesIO(image_bytes))
            except Exception as e:
                # Base64 解码失败或数据不是有效图片
                logger.warning(
                    "llm_base64_decode_failed",
                    extra={"error": str(e)},
                )

        # === 第 3 级检测：纯 Base64 数据（无 Data URI 前缀）===
        # 条件：长度 > 100（排除短文本）且不以 http 开头（排除 URL）
        if len(text_stripped) > 100 and not text_stripped.startswith("http"):
            try:
                # 使用 validate=True 严格校验 Base64 字符集
                image_bytes = base64.b64decode(text_stripped, validate=True)
                # 尝试将解码后的字节解析为图片
                return Image.open(BytesIO(image_bytes))
            except Exception as e:
                # 不是有效的 Base64 图片数据（可能是普通长文本），使用 debug 级别日志
                logger.debug(
                    "llm_direct_base64_decode_failed",
                    extra={"error": str(e)},
                )

        # 三级检测均未命中，输入为纯文本
        return None

    def classify(
        self, text: str, upstream_level: SensitivityLevel, upstream_confidence: float
    ) -> dict[str, Any] | None:
        """使用本地 Qwen2-VL 大模型对输入进行分类 / Classify Input via Local Qwen2-VL LLM.

        这是 LlmClassifier 抽象基类的核心接口实现。
        由 ClassificationAPI._classify_field_internal() 在 Layer-3 阶段调用。

        执行步骤 / Execution Steps:
        1. 延迟初始化模型（若尚未加载）。
           (Lazy-initialize model if not yet loaded)
        2. 将实际推理提交到专用线程池并设置超时，防止推理卡死阻塞 gRPC 线程。
           (Submit inference to a dedicated thread pool with timeout)
        3. 检测并加载多模态图像输入。
           (Detect and load multimodal image input)
        4. 构建 system/user prompt 并调用模型生成。
           (Build system/user prompt and invoke model generation)
        5. 解析生成文本中的 JSON 结构。
           (Parse JSON structure from generated text)

        Args:
            text: 待分类文本或图片路径 / Text or image path to classify.
            upstream_level: 上游引擎给出的敏感度等级 / Upstream sensitivity level.
            upstream_confidence: 上游引擎置信度 / Upstream confidence score.

        Returns:
            分类结果字典（含 final_level/confidence/reasoning）或 None（降级）。
            (Classification result dict or None for degradation)
        """
        # 尝试延迟初始化模型
        try:
            self._lazy_init()
        except Exception:
            # 初始化失败（依赖缺失/模型不存在），递增失败计数并返回 None 触发降级
            CLASSIFICATION_LLM_TOTAL.labels(status="init_failed").inc()
            return None  # 初始化失败，直接返回 None，自动触发底层降级逻辑

        # 记录推理开始时间（monotonic 单调时钟，不受系统时间调整影响）
        start_time = time.monotonic()
        try:
            # 将实际推理提交到专用线程池，设置超时保护。
            # 如果推理超时（如模型卡死），放弃本次推理并返回 None 触发降级，
            # 避免永久阻塞 gRPC 工作线程导致后续所有请求排队失败。
            future = self._executor.submit(
                self._do_classify, text, upstream_level, upstream_confidence
            )
            # 等待推理结果，超过 _INFERENCE_TIMEOUT 秒则抛出 FuturesTimeoutError
            result = future.result(timeout=self._INFERENCE_TIMEOUT)

            # 计算推理耗时并记录到 Prometheus 直方图指标
            duration = time.monotonic() - start_time
            CLASSIFICATION_LLM_DURATION.labels(engine="qwen2vl").observe(duration)
            # 记录推理完成的 debug 日志
            logger.debug(
                "llm_classify_completed",
                extra={
                    "duration_s": round(duration, 4),
                    "has_result": result is not None,
                },
            )
            return result

        except FuturesTimeoutError:
            # 推理超时：模型可能卡死或输入过于复杂
            duration = time.monotonic() - start_time
            # 递增超时状态计数器
            CLASSIFICATION_LLM_TOTAL.labels(status="timeout").inc()
            # 记录超时耗时到直方图
            CLASSIFICATION_LLM_DURATION.labels(engine="qwen2vl").observe(duration)
            # 记录超时错误日志
            logger.error(
                "llm_classify_timeout",
                extra={
                    "timeout_s": self._INFERENCE_TIMEOUT,
                    "duration_s": round(duration, 4),
                },
            )
            return None  # 返回 None 触发降级

        except Exception as e:
            # 推理过程中发生其他异常（OOM/模型错误等）
            duration = time.monotonic() - start_time
            # 递增错误状态计数器
            CLASSIFICATION_LLM_TOTAL.labels(status="error").inc()
            # 记录错误耗时到直方图
            CLASSIFICATION_LLM_DURATION.labels(engine="qwen2vl").observe(duration)
            # 记录错误详情日志
            logger.error(
                "llm_classify_error",
                extra={"error": str(e), "duration_s": round(duration, 4)},
            )
            return None  # 返回 None 触发降级

    def _do_classify(
        self, text: str, upstream_level: SensitivityLevel, upstream_confidence: float
    ) -> dict[str, Any] | None:
        """实际执行模型推理的内部方法（在专用线程中运行）。

        使用 self._lock 保护推理过程，确保同一时刻只有一个线程在执行
        模型推理，避免多线程并发推理导致显存/内存争用引发 OOM 崩溃。

        注意：此方法在 _executor 线程池的工作线程中执行，而非 gRPC 线程。
        锁的获取可能阻塞（当另一个推理正在进行时），但外层的超时机制
        会确保 gRPC 线程不会永久等待。

        Args:
            text: 待分类文本或图片路径。
            upstream_level: 上游敏感度等级。
            upstream_confidence: 上游置信度。

        Returns:
            分类结果字典或 None。
        """
        # 获取互斥锁，串行化推理（防止并发推理导致 OOM）
        with self._lock:
            # 委托给 _classify_inner 执行实际的推理逻辑
            return self._classify_inner(text, upstream_level, upstream_confidence)

    def _classify_inner(
        self, text: str, upstream_level: SensitivityLevel, upstream_confidence: float
    ) -> dict[str, Any] | None:
        """模型推理核心逻辑（已持有锁）。

        执行步骤：
        1. 检测输入是否为图片（三级检测）
        2. 构建 system prompt（定义评估标准和输出格式）
        3. 构建 user content（文本或图片+文本）
        4. 使用 processor 构建模型输入张量
        5. 执行模型 generate 推理
        6. 解码生成 token 为文本
        7. 从文本中提取 JSON 结构化结果

        Args:
            text: 待分类文本或图片路径。
            upstream_level: 上游敏感度等级（供 prompt 参考）。
            upstream_confidence: 上游置信度（供 prompt 参考）。

        Returns:
            解析后的分类结果字典或 None。
        """
        try:
            # 检测并加载多模态图像输入（三级检测策略）
            image = self._detect_image(text)

            # 构建 system prompt：定义角色、评估标准（L1-L5）和输出 JSON 格式
            system_prompt = (
                "你是一个医疗数据分类分级领域的资深安全专家。请对输入的医疗数据进行敏感等级评估。\n"
                "评估标准如下：\n"
                "- L5 (极高风险): 包含人类基因序列、遗传信息、基因突变（如 BRCA1/TP53）或罕见病样本。\n"
                "- L4 (高风险): 包含精神疾病（如精神分裂）、敏感传染病（如 HIV/AIDS/梅毒）或完整的住院病历。\n"
                "- L3 (中风险): 包含个人身份信息（PII，如身份证号、手机号）、普通的门诊诊疗记录或常规检验指标数值（如血常规）。\n"  # noqa: E501
                "- L2 (低风险): 仅包含医院科室运营、设备使用率或脱敏后的去标识化统计数据。\n"
                "- L1 (公开级): 年度门诊总量等医院公开宣传、无任何敏感和特征的统计指标。\n\n"
                "请严格根据上述标准进行定级，并仅输出符合以下 JSON 格式的结构化内容，不要包含额外的解释文字或 ``` 块：\n"  # noqa: E501
                "{\n"
                '  "final_level": "L1/L2/L3/L4/L5",\n'
                '  "sub_category": "分类标签简称",\n'
                '  "confidence": 0.0到1.0之间的浮点数,\n'
                '  "reasoning": "定级判别的推理过程说明",\n'
                '  "needs_human_review": true/false\n'
                "}"
            )

            # 构建 user content：根据是否为图片选择不同的输入格式
            user_content = []
            if image is not None:
                # 图片输入：添加图片对象 + 文字指令（OCR + 评估）
                user_content.append({"type": "image", "image": image})
                user_content.append({"type": "text", "text": "请提取该图片中的文字并评估其敏感数据等级。"})
            else:
                # 纯文本输入：直接嵌入待评估文本
                user_content.append({"type": "text", "text": f"请评估以下文本数据的敏感数据等级：\n{text}"})

            # 组装完整的对话消息列表（system + user）
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ]

            # 使用处理器将对话消息转换为模型可接受的文本 prompt 格式
            # apply_chat_template 会按照 Qwen2-VL 的对话模板格式化消息
            text_prompt = self._processor.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )

            # 处理图片输入：如果有图片，需要额外处理视觉信息
            image_inputs = None
            if image is not None:
                try:
                    # 优先使用 qwen_vl_utils 的 process_vision_info 处理图片
                    from qwen_vl_utils import process_vision_info

                    # 从消息中提取并处理视觉信息（图片缩放、归一化等）
                    image_inputs, _video_inputs = process_vision_info(messages)
                except ImportError:
                    # 兼容未安装 qwen-vl-utils 的情况：直接使用 PIL Image 对象
                    image_inputs = [image]

            # 使用处理器将文本和图片转换为模型输入张量（input_ids, pixel_values 等）
            inputs = self._processor(
                text=[text_prompt], images=image_inputs, padding=True, return_tensors="pt"
            )

            # 将所有输入张量移动到模型所在设备（CUDA/MPS/CPU）
            inputs = {k: v.to(self._model.device) for k, v in inputs.items()}

            # 执行模型推理生成
            import torch

            # 禁用梯度计算（推理模式，节省显存和计算资源）
            with torch.no_grad():
                # 调用模型 generate 方法，最多生成 512 个新 token
                generated_ids = self._model.generate(**inputs, max_new_tokens=512)

            # 裁剪生成结果：去掉输入 prompt 部分，只保留新生成的 token
            generated_ids_trimmed = [
                out_ids[len(in_ids) :]  # 从输入长度之后开始截取
                for in_ids, out_ids in zip(inputs["input_ids"], generated_ids)
            ]
            # 将生成的 token ID 解码为人类可读文本
            output_text = self._processor.batch_decode(
                generated_ids_trimmed,
                skip_special_tokens=True,  # 跳过特殊 token（如 </s>）
                clean_up_tokenization_spaces=False,  # 保留原始空格格式
            )[0]  # 取第一个（也是唯一一个）结果

            # 从生成文本中提取 JSON 结构化分类结果
            result = self._parse_json_result(output_text, upstream_level, upstream_confidence)

            # 递增成功状态计数器
            CLASSIFICATION_LLM_TOTAL.labels(status="success").inc()
            return result

        except Exception as e:
            # 推理过程中任何异常都捕获并降级
            CLASSIFICATION_LLM_TOTAL.labels(status="error").inc()
            logger.error(
                "llm_classify_inner_error",
                extra={"error": str(e)},
            )
            return None  # 返回 None 触发上层降级逻辑

    def _parse_json_result(
        self, output_text: str, upstream_level: SensitivityLevel, upstream_confidence: float
    ) -> dict[str, Any] | None:
        """解析大模型返回的 JSON / Parse LLM JSON Output.

        中文说明：使用正则表达式清洗并解析大模型返回的 JSON。
        大模型可能在 JSON 前后包含额外文字或 ```json``` 代码块标记，
        因此使用正则提取 {} 区间内容进行解析。

        容错策略：
        - 优先提取 {} 包裹的 JSON 内容
        - 如果提取失败，尝试直接解析整个输出文本
        - 解析失败或关键字段缺失则返回 None 触发降级

        English Description: Cleans and parses JSON from LLM output using regex.

        Args:
            output_text: 模型生成的原始文本 / Raw generated text from model.
            upstream_level: 上游敏感度等级 / Upstream sensitivity level.
            upstream_confidence: 上游置信度 / Upstream confidence.

        Returns:
            解析后的结果字典（含 final_level 等字段）或 None / Parsed result dict or None.
        """
        # 使用正则表达式提取第一个 {} 包裹的 JSON 内容（DOTALL 匹配跨行）
        json_match = re.search(r"(\{.*\})", output_text, re.DOTALL)
        # 如果匹配到则提取 JSON 字符串，否则使用整个输出文本尝试解析
        json_str = json_match.group(1) if json_match else output_text

        try:
            # 尝试解析 JSON 字符串为 Python 字典
            res = json.loads(json_str)
            # 校验关键字段 final_level 是否存在（确保是有效的分类结果）
            if "final_level" in res:
                # 类型断言并返回有效的分类结果字典
                return cast("dict[str, Any]", res)
        except Exception as e:
            # JSON 解析失败（格式不合法/字段类型错误等）
            logger.warning(
                "llm_json_parse_failed",
                extra={"error": str(e)},
            )

        # 解析失败则返回 None 触发降级（上层使用 Layer-1/Layer-2 的结果）
        return None
