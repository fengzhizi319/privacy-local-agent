"""基于本地多模态大模型 Qwen2-VL-2B-Instruct 的数据分类分级器。

中文说明：
支持本地病例图像、手写病例图片以及纯文本数据的智能 OCR 识别与零样本敏感定级推理。
具备延迟加载、自动降级、多模态输入检测等企业级能力。

English Description:
Data classification and grading engine based on local multimodal LLM Qwen2-VL-2B-Instruct.
Supports intelligent OCR recognition and zero-shot sensitivity grading for local medical
images, handwritten records, and plain text data. Features lazy-loading, graceful
degradation, and multimodal input detection capabilities.
"""

from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from io import BytesIO
from typing import Any, Dict, Optional

from ...observability.logging_config import get_logger
from ...observability.metrics import (
    CLASSIFICATION_LLM_DURATION,
    CLASSIFICATION_LLM_TOTAL,
)
from .classification_models import LlmClassifier, SensitivityLevel
from .classification_utils import redact

# Module-level structured logger for LLM classifier events
logger = get_logger(__name__)



class Qwen2VLClassifier(LlmClassifier):
    """基于本地部署 Qwen2-VL-2B-Instruct 的多模态分类器 / Qwen2-VL Multimodal Classifier.

    中文说明：
    支持对图片路径、Base64 图片以及纯文本进行 OCR、理解与敏感等级评估。

    English Description:
    Supports OCR, understanding, and sensitivity level assessment for image paths,
    Base64-encoded images, and plain text inputs.
    """

    # VLM 推理超时（秒）：Qwen2-VL-2B 在 CPU 上单张图片推理可能需要 60-120 秒，
    # 超时后放弃本次推理并返回 None 触发降级，避免无限阻塞 gRPC 工作线程。
    _INFERENCE_TIMEOUT = int(os.environ.get("PRIVACY_VLM_TIMEOUT", "180"))

    def __init__(self, model_path: Optional[str] = None):
        """初始化分类器 / Initialize Classifier.

        Args:
            model_path: 模型本地路径 / Local model path.
                如果不指定，默认使用项目根目录下的 .models/Qwen2-VL-2B-Instruct。
                (Defaults to .models/Qwen2-VL-2B-Instruct under project root)
        """
        if not model_path:
            current_dir = os.path.dirname(os.path.abspath(__file__))
            project_root = os.path.dirname(os.path.dirname(current_dir))
            model_path = os.path.join(project_root, ".models", "Qwen2-VL-2B-Instruct")

        self.model_path = model_path
        self._model = None
        self._processor = None
        self._initialized = False
        self._init_error = None
        # 线程锁：gRPC 使用线程池处理请求，多个工作线程可能并发调用
        # _lazy_init / classify，需要互斥保护以防止：
        #   1. 多线程同时初始化模型导致重复加载或竞态
        #   2. 多线程同时推理导致显存/内存争用引发 OOM 崩溃
        self._lock = threading.Lock()
        # 专用推理线程池：将模型推理隔离到单独线程，配合超时机制，
        # 即使推理卡死也不会永久阻塞 gRPC 工作线程。
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vlm-infer")

    def _lazy_init(self):
        """延迟初始化模型 / Lazy-Initialize Model.

        中文说明：避免导入时或非 LLM 运行时占用显存或因缺少依赖报错。
        使用双重检查锁定（double-checked locking）确保线程安全：
        仅首次调用时加锁初始化，后续调用直接返回，避免锁竞争开销。

        English Description: Avoids occupying GPU memory at import time or when LLM
        is not needed, and prevents errors from missing dependencies.
        Uses double-checked locking for thread safety.

        Raises:
            FileNotFoundError: 本地模型目录不存在 / Local model directory not found.
        """
        # 快速路径：已初始化或已记录错误时无需加锁
        if self._initialized:
            return
        if self._init_error:
            raise self._init_error

        with self._lock:
            # 双重检查：另一个线程可能已在等锁期间完成初始化
            if self._initialized:
                return
            if self._init_error:
                raise self._init_error

            try:
                import torch
                from transformers import AutoProcessor, Qwen2VLForConditionalGeneration

                if not os.path.exists(self.model_path) or not os.path.isdir(self.model_path):
                    raise FileNotFoundError(
                        f"本地模型未找到，请先运行下载脚本或下载模型至: {self.model_path}"
                    )

                # 检测设备，优先使用 GPU CUDA，其次为 macOS ARM 的 MPS 硬件加速，最后为 CPU
                if torch.cuda.is_available():
                    device = "cuda"
                elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                    device = "mps"
                else:
                    device = "cpu"

                logger.info(
                    "qwen2vl_model_loading",
                    extra={"model_path": self.model_path, "device": device},
                )

                torch_dtype = torch.float16 if device == "cuda" else torch.float32

                self._model = Qwen2VLForConditionalGeneration.from_pretrained(
                    self.model_path,
                    torch_dtype=torch_dtype,
                    device_map="auto" if device in ("cuda", "mps") else None,
                )
                if device in ("cpu", "mps"):
                    self._model = self._model.to(device)

                self._processor = AutoProcessor.from_pretrained(self.model_path)
                self._initialized = True
                logger.info(
                    "qwen2vl_model_initialized",
                    extra={"model_path": self.model_path, "device": device, "engine": "qwen2vl"},
                )

            except Exception as e:
                self._init_error = e
                logger.warning(
                    "qwen2vl_model_init_failed",
                    extra={"error": str(e), "model_path": self.model_path},
                )
                raise e

    @property
    def is_ready(self) -> bool:
        """模型是否已完成初始化且未发生错误 / Whether Model Is Ready.

        Returns:
            模型就绪状态 / Model readiness status.
        """
        return self._initialized and self._init_error is None

    def warmup(self) -> bool:
        """主动触发模型加载 / Proactively Trigger Model Loading.

        中文说明：同步阻塞，建议在后台线程/协程中调用。
        English Description: Synchronous blocking call; recommended to invoke in a
        background thread or coroutine.

        Returns:
            是否成功完成初始化 / Whether initialization succeeded.
        """
        try:
            self._lazy_init()
            return True
        except Exception:
            return False

    def _detect_image(self, text: str) -> Optional["Image.Image"]:
        """检测输入是否为图片 / Detect if Input is an Image.

        中文说明：检测输入文本是否为本地图片路径或 Base64 编码图片，
        如果是，加载并返回 PIL.Image 实例。

        English Description: Detects whether input text is a local image path or
        Base64-encoded image. If so, loads and returns a PIL.Image instance.

        Args:
            text: 输入文本 / Input text.

        Returns:
            PIL.Image 实例或 None / PIL.Image instance or None.
        """
        try:
            from PIL import Image
        except ImportError:
            return None

        text_stripped = text.strip()

        # 1. 检测本地图片路径
        if len(text_stripped) < 512:  # 路径长度通常有限
            # 常见图片格式后缀
            if any(
                text_stripped.lower().endswith(ext)
                for ext in (".jpg", ".jpeg", ".png", ".bmp", ".webp")
            ):
                if os.path.exists(text_stripped) and os.path.isfile(text_stripped):
                    try:
                        return Image.open(text_stripped)
                    except Exception as e:
                        logger.warning(
                            "llm_image_load_failed",
                            extra={"path": redact(text_stripped), "error": str(e)},
                        )

        # 2. 检测 Base64 图片 (Data URI)
        # 例如 data:image/png;base64,iVBORw0KGgoAAA...
        data_uri_match = re.match(r"^data:image\/[a-zA-Z]+;base64,(.+)$", text_stripped)
        if data_uri_match:
            try:
                base64_data = data_uri_match.group(1)
                image_bytes = base64.b64decode(base64_data)
                return Image.open(BytesIO(image_bytes))
            except Exception as e:
                logger.warning(
                    "llm_base64_decode_failed",
                    extra={"error": str(e)},
                )


        # 3. 尝试直接进行 Base64 解码并检测是否为图像 (用于纯 base64 数据)
        if len(text_stripped) > 100 and not text_stripped.startswith("http"):
            try:
                image_bytes = base64.b64decode(text_stripped, validate=True)
                return Image.open(BytesIO(image_bytes))
            except Exception:
                pass  # 说明不是合法的 base64 图片数据

        return None

    def classify(
        self, text: str, upstream_level: SensitivityLevel, upstream_confidence: float
    ) -> Optional[Dict[str, Any]]:
        """使用本地 Qwen2-VL 大模型对输入进行分类 / Classify Input via Local Qwen2-VL LLM.

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
            分类结果字典或 None（降级） / Classification result dict or None (degraded).
        """
        try:
            self._lazy_init()
        except Exception:
            CLASSIFICATION_LLM_TOTAL.labels(status="init_failed").inc()
            return None  # 初始化失败，直接返回 None，自动触发底层降级逻辑

        start_time = time.monotonic()
        try:
            # 将实际推理提交到专用线程池，设置超时保护。
            # 如果推理超时（如模型卡死），放弃本次推理并返回 None 触发降级，
            # 避免永久阻塞 gRPC 工作线程导致后续所有请求排队失败。
            future = self._executor.submit(
                self._do_classify, text, upstream_level, upstream_confidence
            )
            result = future.result(timeout=self._INFERENCE_TIMEOUT)

            duration = time.monotonic() - start_time
            CLASSIFICATION_LLM_DURATION.labels(engine="qwen2vl").observe(duration)
            logger.debug(
                "llm_classify_completed",
                extra={
                    "duration_s": round(duration, 4),
                    "has_result": result is not None,
                },
            )
            return result

        except FuturesTimeoutError:
            duration = time.monotonic() - start_time
            CLASSIFICATION_LLM_TOTAL.labels(status="timeout").inc()
            CLASSIFICATION_LLM_DURATION.labels(engine="qwen2vl").observe(duration)
            logger.error(
                "llm_classify_timeout",
                extra={
                    "timeout_s": self._INFERENCE_TIMEOUT,
                    "duration_s": round(duration, 4),
                },
            )
            return None

        except Exception as e:
            duration = time.monotonic() - start_time
            CLASSIFICATION_LLM_TOTAL.labels(status="error").inc()
            CLASSIFICATION_LLM_DURATION.labels(engine="qwen2vl").observe(duration)
            logger.error(
                "llm_classify_error",
                extra={"error": str(e), "duration_s": round(duration, 4)},
            )
            return None

    def _do_classify(
        self, text: str, upstream_level: SensitivityLevel, upstream_confidence: float
    ) -> Optional[Dict[str, Any]]:
        """实际执行模型推理的内部方法（在专用线程中运行）。

        使用 self._lock 保护推理过程，确保同一时刻只有一个线程在执行
        模型推理，避免多线程并发推理导致显存/内存争用引发 OOM 崩溃。

        Args:
            text: 待分类文本或图片路径。
            upstream_level: 上游敏感度等级。
            upstream_confidence: 上游置信度。

        Returns:
            分类结果字典或 None。
        """
        with self._lock:
            return self._classify_inner(text, upstream_level, upstream_confidence)

    def _classify_inner(
        self, text: str, upstream_level: SensitivityLevel, upstream_confidence: float
    ) -> Optional[Dict[str, Any]]:
        """模型推理核心逻辑（已持有锁）。"""
        try:
            # 检测并加载多模态图像输入
            image = self._detect_image(text)

            system_prompt = (
                "你是一个医疗数据分类分级领域的资深安全专家。请对输入的医疗数据进行敏感等级评估。\n"
                "评估标准如下：\n"
                "- L5 (极高风险): 包含人类基因序列、遗传信息、基因突变（如 BRCA1/TP53）或罕见病样本。\n"
                "- L4 (高风险): 包含精神疾病（如精神分裂）、敏感传染病（如 HIV/AIDS/梅毒）或完整的住院病历。\n"
                "- L3 (中风险): 包含个人身份信息（PII，如身份证号、手机号）、普通的门诊诊疗记录或常规检验指标数值（如血常规）。\n"
                "- L2 (低风险): 仅包含医院科室运营、设备使用率或脱敏后的去标识化统计数据。\n"
                "- L1 (公开级): 年度门诊总量等医院公开宣传、无任何敏感和特征的统计指标。\n\n"
                "请严格根据上述标准进行定级，并仅输出符合以下 JSON 格式的结构化内容，不要包含额外的解释文字或 ``` 块：\n"
                "{\n"
                '  "final_level": "L1/L2/L3/L4/L5",\n'
                '  "sub_category": "分类标签简称",\n'
                '  "confidence": 0.0到1.0之间的浮点数,\n'
                '  "reasoning": "定级判别的推理过程说明",\n'
                '  "needs_human_review": true/false\n'
                "}"
            )

            user_content = []
            if image is not None:
                user_content.append({"type": "image", "image": image})
                user_content.append({"type": "text", "text": "请提取该图片中的文字并评估其敏感数据等级。"})
            else:
                user_content.append({"type": "text", "text": f"请评估以下文本数据的敏感数据等级：\n{text}"})

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ]

            # 使用处理器构建模型输入
            text_prompt = self._processor.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )

            # 如果有图片，我们需要加载 qwen_vl_utils 中的辅助方法处理图片，否则用处理器直接转换
            image_inputs = None
            if image is not None:
                try:
                    from qwen_vl_utils import process_vision_info

                    image_inputs, video_inputs = process_vision_info(messages)
                except ImportError:
                    # 兼容未安装 qwen-vl-utils 的情况
                    image_inputs = [image]

            inputs = self._processor(
                text=[text_prompt], images=image_inputs, padding=True, return_tensors="pt"
            )

            # 将张量移动到模型所在设备
            inputs = {k: v.to(self._model.device) for k, v in inputs.items()}

            # 生成模型推理输出
            import torch

            with torch.no_grad():
                generated_ids = self._model.generate(**inputs, max_new_tokens=512)

            generated_ids_trimmed = [
                out_ids[len(in_ids) :]
                for in_ids, out_ids in zip(inputs["input_ids"], generated_ids)
            ]
            output_text = self._processor.batch_decode(
                generated_ids_trimmed,
                skip_special_tokens=True,
                clean_up_tokenization_spaces=False,
            )[0]

            # 从生成文本中提取 JSON 结构
            result = self._parse_json_result(output_text, upstream_level, upstream_confidence)

            CLASSIFICATION_LLM_TOTAL.labels(status="success").inc()
            return result

        except Exception as e:
            CLASSIFICATION_LLM_TOTAL.labels(status="error").inc()
            logger.error(
                "llm_classify_inner_error",
                extra={"error": str(e)},
            )
            return None

    def _parse_json_result(
        self, output_text: str, upstream_level: SensitivityLevel, upstream_confidence: float
    ) -> Optional[Dict[str, Any]]:
        """解析大模型返回的 JSON / Parse LLM JSON Output.

        中文说明：使用正则表达式清洗并解析大模型返回的 JSON。
        English Description: Cleans and parses JSON from LLM output using regex.

        Args:
            output_text: 模型生成的原始文本 / Raw generated text from model.
            upstream_level: 上游敏感度等级 / Upstream sensitivity level.
            upstream_confidence: 上游置信度 / Upstream confidence.

        Returns:
            解析后的结果字典或 None / Parsed result dict or None.
        """
        # 1. 尝试直接提取 JSON {} 区间内容
        json_match = re.search(r"(\{.*\})", output_text, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
        else:
            json_str = output_text

        try:
            res = json.loads(json_str)
            # 校验关键字段是否存在
            if "final_level" in res:
                return res
        except Exception as e:
            logger.warning(
                "llm_json_parse_failed",
                extra={"error": str(e)},
            )

        # 解析失败则返回 None 触发降级
        return None
