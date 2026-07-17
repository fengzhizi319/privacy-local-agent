"""基于本地多模态大模型 Qwen2-VL-2B-Instruct 的数据分类分级器。

支持本地病例图像、手写病例图片以及纯文本数据的智能 OCR 识别与零样本敏感定级推理。
"""

import base64
import json
import logging
import os
import re
from io import BytesIO
from typing import Any, Dict, Optional

from .classification_models import LlmClassifier, SensitivityLevel
from .classification_utils import redact

logger = logging.getLogger("privacy.classification_llm")



class Qwen2VLClassifier(LlmClassifier):
    """基于本地部署 Qwen2-VL-2B-Instruct 的多模态分类器。

    支持对图片路径、Base64 图片以及纯文本进行 OCR、理解与敏感等级评估。
    """

    def __init__(self, model_path: Optional[str] = None):
        """初始化分类器。

        Args:
            model_path: 模型本地路径，如果不指定，默认使用项目根目录下的 .models/Qwen2-VL-2B-Instruct
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

    def _lazy_init(self):
        """延迟初始化模型，避免导入时或非 LLM 运行时占用显存或因缺少依赖报错。"""
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

            logger.info(f"正在从本地加载 Qwen2-VL 模型: {self.model_path} ...")

            # 检测设备，优先使用 GPU CUDA，其次为 macOS ARM 的 MPS 硬件加速，最后为 CPU
            if torch.cuda.is_available():
                device = "cuda"
            elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                device = "mps"
            else:
                device = "cpu"

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
            logger.info("Qwen2-VL 模型及处理器本地初始化成功。")

        except Exception as e:
            self._init_error = e
            logger.warning(f"本地大模型初始化失败（自动降级为 NoOp）: {e}")
            raise e

    @property
    def is_ready(self) -> bool:
        """模型是否已完成初始化且未发生错误。"""
        return self._initialized and self._init_error is None

    def warmup(self) -> bool:
        """主动触发模型加载（同步阻塞，建议在后台线程/协程中调用）。

        Returns:
            是否成功完成初始化。
        """
        try:
            self._lazy_init()
            return True
        except Exception:
            return False

    def _detect_image(self, text: str) -> Optional["Image.Image"]:
        """检测输入文本是否为本地图片路径或 Base64 编码图片。如果是，加载并返回 PIL.Image 实例。"""
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
                        logger.warning(f"加载本地图片失败 {redact(text_stripped)}: {e}")

        # 2. 检测 Base64 图片 (Data URI)
        # 例如 data:image/png;base64,iVBORw0KGgoAAA...
        data_uri_match = re.match(r"^data:image\/[a-zA-Z]+;base64,(.+)$", text_stripped)
        if data_uri_match:
            try:
                base64_data = data_uri_match.group(1)
                image_bytes = base64.b64decode(base64_data)
                return Image.open(BytesIO(image_bytes))
            except Exception as e:
                logger.warning(f"解析 Base64 Data URI 图像失败: {e}")


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
        """使用本地 Qwen2-VL 大模型对输入进行分类。"""
        try:
            self._lazy_init()
        except Exception:
            return None  # 初始化失败，直接返回 None，自动触发底层降级逻辑

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
            return self._parse_json_result(output_text, upstream_level, upstream_confidence)

        except Exception as e:
            logger.error(f"本地大模型推理出错: {e}")
            return None

    def _parse_json_result(
        self, output_text: str, upstream_level: SensitivityLevel, upstream_confidence: float
    ) -> Optional[Dict[str, Any]]:
        """使用正则表达式清洗并解析大模型返回的 JSON。"""
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
            logger.warning(f"大模型输出 JSON 解析失败，错误: {e}")

        # 解析失败则返回 None 触发降级
        return None
