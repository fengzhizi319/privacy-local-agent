"""数据分类原语公共 API 入口 / Data Classification Primitive Public API Entry.

中文说明：
提供 ClassificationAPI 以及向后兼容的公共符号导出。
支持字段、记录、表多级分类，内置默认规则引擎，可插拔 Small-NER 与 LLM，
并提供 dict/JSON/DataFrame/Arrow/SQL 结果集等多种格式适配器。

本模块是三层分类漏斗的主入口，编排 Layer-1/2/3 的执行顺序和降级逻辑：
- Layer-1（规则引擎）：基于字段名和值的确定性规则匹配，零延迟
- Layer-2（Small-NER）：医疗实体识别，毫秒级延迟
- Layer-3（LLM）：多模态大模型推理，秒级延迟，作为兜底

English Description:
Provides ClassificationAPI and backward-compatible public symbol exports.
Supports field/record/table multi-level classification with a built-in default rule engine,
pluggable Small-NER and LLM classifiers, and multiple format adapters
(dict/JSON/DataFrame/Arrow/SQL result sets).

具体实现已拆分到 / Implementation split into:
- classification_rule_engine.py（规则引擎 / Rule Engine）
- classification_utils.py（ZK 工具、合规模板、SecretFlow 适配 / ZK Utils, Templates, SecretFlow Adapter）
- classification_composite.py（复合规则 / Composite Rules）
- classification_async.py（异步任务 / Async Jobs）
- classification_review.py（复核存储 / Review Store）
- classification_models.py（数据模型与抽象基类 / Data Models & ABCs）
"""

# 启用延迟注解求值，允许在类型提示中引用尚未定义的类名
from __future__ import annotations

# 导入 JSON 模块，用于 classify_json 接口的输入解析
import json
# 导入操作系统接口，用于文件路径拼接和模型目录检测
import os
# 导入正则表达式模块，用于 Base64 Data URI 前缀匹配
import re
# 导入时间模块，用于测量分类操作耗时（monotonic 单调时钟）
import time
# 导入日期时间模块，用于构建审计信息中的 UTC 时间戳
from datetime import datetime, timezone
# 导入类型注解工具：Any 通用类型，Protocol 结构化子类型，cast 类型断言
from typing import Any, Protocol, cast

# 导入结构化日志工厂函数
from ...observability.logging_config import get_logger
# 导入 Prometheus 指标：
# - CLASSIFICATION_DURATION：分类操作耗时直方图（按操作类型：field/record/table）
# - CLASSIFICATION_LLM_TOTAL：LLM 调用计数器（按状态：hit/miss）
# - CLASSIFICATION_NER_TOTAL：NER 调用计数器（按状态：hit/miss）
# - CLASSIFICATION_TOTAL：分类总次数计数器（按最终等级和决策层）
from ...observability.metrics import (
    CLASSIFICATION_DURATION,
    CLASSIFICATION_LLM_TOTAL,
    CLASSIFICATION_NER_TOTAL,
    CLASSIFICATION_TOTAL,
)
# 导入参数解析器：从 YAML profile 文件解析分类参数
from ..profile import ParameterResolver, get_resolver
# 导入复合规则引擎和标签应用函数
from .classification_composite import CompositeRuleEngine, apply_composite_tags
# 导入所有数据模型和抽象基类
from .classification_models import (
    AuditInfo,                    # 审计信息模型
    ClassificationJob,            # 异步任务模型
    ClassificationParams,         # 分类参数模型
    ClassificationResult,         # 分类结果包装模型
    CompositeRule,                # 复合规则模型
    EngineLayer,                  # 引擎层枚举（L1_RULE/L2_SMALL_NER/L3_LLM）
    FieldClassificationResult,    # 字段级分类结果
    LlmClassifier,                # LLM 分类器抽象基类
    NoOpLlmClassifier,            # 空操作 LLM 分类器（降级用）
    NoOpSmallNerEngine,           # 空操作 NER 引擎（降级用）
    RecordClassificationResult,   # 记录级分类结果
    RuleEngineABC,                # 规则引擎抽象基类
    SecurityTag,                  # 安全标签模型
    SensitivityLevel,             # 敏感度等级枚举（L1-L5）
    ShadowDiff,                   # 影子模式差异模型
    SmallNerEngine,               # NER 引擎抽象基类
    TableClassificationResult,    # 表级分类结果
    max_level,                    # 取最高等级工具函数
    parse_level,                  # 等级字符串解析工具函数
)
# 导入默认规则引擎、规则引擎基类和标签去重函数
from .classification_rule_engine import DefaultRuleEngine, RuleEngine, _unique_tags
# 导入 SecretFlow 适配器和合规模板参数获取函数
from .classification_utils import classify_secretflow, get_template_params

# 创建模块级结构化日志器
logger = get_logger(__name__)

# 分类原语版本号（记录在审计信息中，用于追溯分类逻辑版本）
_PRIMITIVE_VERSION = "1.0.0"
# 规则引擎版本号（用于影子模式对比和审计追溯）
_RULE_ENGINE_VERSION = "1.0.0"

# base64 data URI 前缀正则：匹配 data:image/png;base64, 等格式
# 用于 _summarize_field_value 中检测图片数据并生成摘要
_DATA_URI_RE = re.compile(r"^data:image/[a-zA-Z]+;base64,", re.ASCII)


def _summarize_field_value(value: Any) -> str | None:
    """将字段值转换为适合返回给前端的摘要字符串。

    对于图片类 base64 数据（data URI 或超长纯 base64），不返回原始内容，
    仅返回形如 ``[image data, ~123 KB]`` 的摘要，避免响应体过大、
    前端显示冗长。

    检测策略：
    1. data:image/xxx;base64,... 格式 → 计算近似原始大小并返回摘要
    2. 纯 base64 且长度 > 512 → 检测字符集是否为 base64 → 返回摘要
    3. 其他情况 → 直接返回原始字符串

    Args:
        value: 原始字段值。

    Returns:
        摘要字符串，或 None（值为 None 时）。
    """
    # 空值直接返回 None
    if value is None:
        return None
    # 将值转为字符串
    text = str(value)
    # 检测策略 1：data:image/xxx;base64,... 格式的图片数据
    m = _DATA_URI_RE.match(text)
    if m:
        # 计算 base64 编码部分的长度
        raw_len = len(text) - m.end()
        # base64 编码膨胀率约 4/3，反推近似原始字节数并转为 KB
        kb = max(1, round(raw_len * 3 / 4 / 1024))  # base64 → 近似原始字节
        return f"[image data, ~{kb} KB]"
    # 检测策略 2：纯 base64 且长度超过 512 字符（极可能是图片或其他二进制数据）
    if len(text) > 512 and not text.startswith("http") and not text.startswith("/"):
        # 取前 128 个字符检测是否全为合法 base64 字符集
        sample = text[:128]
        if all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=\n\r" for c in sample):
            # 计算近似原始大小
            kb = max(1, round(len(text) * 3 / 4 / 1024))
            return f"[binary data, ~{kb} KB]"
    # 普通文本值：直接返回
    return text

# 公共 API 导出列表：控制 from classification import * 时暴露的符号
__all__ = [
    "ClassificationAPI",       # 分类统一 API 主类
    "DefaultRuleEngine",       # 默认规则引擎
    "LlmClassifier",           # LLM 分类器抽象基类
    "NoOpLlmClassifier",       # 空操作 LLM（降级用）
    "NoOpSmallNerEngine",      # 空操作 NER（降级用）
    "RuleEngine",              # 规则引擎基类
    "RuleEngineABC",           # 规则引擎抽象基类
    "SmallNerEngine",          # NER 引擎抽象基类
]


class _SupportsEvaluateSeries(Protocol):
    """Protocol for rule engines that provide vectorized ``evaluate_series``.

    仅用于向量化表分类路径中的类型断言，避免在顶层引入 pandas 依赖。
    通过 Protocol 结构化子类型检查，确保规则引擎提供了 evaluate_series 方法。

    Used only for type assertions in the vectorized table classification path
    to avoid importing pandas at the top level.
    """

    def evaluate_series(
        self, field_name: str, series: Any, params: ClassificationParams
    ) -> list[list[SecurityTag]]:
        ...  # Protocol 方法无需实现体


# ---------------------------------------------------------------------------
# 参数治理 / Parameter Governance
# ---------------------------------------------------------------------------

def _resolve_classification_params(
    resolver: ParameterResolver | None = None,
    request_params: dict[str, Any] | None = None,
) -> tuple[ClassificationParams, str]:
    """合并默认、合规模板、YAML profile、请求参数得到最终分类参数。

    Merge defaults, compliance template, YAML profile and request params
    to produce the final classification parameters.

    参数优先级（从低到高）：
    1. Pydantic 模型默认值（ClassificationParams 字段 default）
    2. YAML profile 配置文件
    3. 合规模板默认值（仅填充未设置的 key）
    4. 请求级参数（最高优先级，覆盖一切）

    执行步骤 / Execution Steps:
    1. 从 YAML profile 解析器获取基础参数。
       (Resolve base parameters from YAML profile resolver)
    2. 根据请求或 profile 中的 template 名称激活合规模板默认值。
       (Activate compliance template defaults based on request/profile template name)
    3. 应用请求级参数覆盖。
       (Apply request-level parameter overrides)
    4. 通过 Pydantic 模型校验并构造 ClassificationParams。
       (Validate and construct ClassificationParams via Pydantic model)

    Args:
        resolver: YAML 参数解析器实例 / YAML parameter resolver instance.
        request_params: 请求级参数字典 / Request-level parameter dictionary.

    Returns:
        (ClassificationParams, parameter_source) 元组 / Tuple of params and source indicator.
    """
    # 初始化参数字典和来源标识
    params: dict[str, Any] = {}
    source = "default"  # 默认来源标识

    # Step 1: 从 YAML profile 解析基础参数
    if resolver is not None:
        # 解析 classification 命名空间的参数
        profile_params = resolver.resolve("classification", request_params=None) or {}
        if profile_params:
            # 将 profile 参数合并到参数字典
            params.update(profile_params)
            source = "profile"  # 更新来源标识

    # Step 2: 激活合规模板默认值（仅填充未设置的 key，不覆盖已有值）
    template_name = None
    # 优先从请求参数中获取模板名
    if request_params and request_params.get("template"):
        template_name = request_params.get("template")
    # 其次从 profile 参数中获取模板名
    elif params.get("template"):
        template_name = params.get("template")
    if template_name:
        # 获取模板默认参数（如 JR/T 0197、GB/T 35273、GDPR）
        template_defaults = get_template_params(template_name)
        # 模板默认值仅应用于尚未设置的 key（不覆盖 profile 或请求参数）
        for key, value in template_defaults.items():
            if key not in params:
                params[key] = value

    # Step 3: 应用请求级参数覆盖（最高优先级）
    if request_params:
        params.update(request_params)
        source = "request"  # 更新来源标识

    # Step 4: 通过 Pydantic 模型校验并构造 ClassificationParams
    classification_params = ClassificationParams.model_validate(params)

    # 手动覆盖标志：如果参数中标记了 manual_override，来源记为 "manual"
    if classification_params.manual_override:
        source = "manual"

    return classification_params, source


# ---------------------------------------------------------------------------
# 分类 API / Classification API
# ---------------------------------------------------------------------------

class ClassificationAPI:
    """数据分类统一 API / Unified Data Classification API.

    中文说明：
    支持字段、记录、表多级分类，内置默认规则引擎，可插拔 Small-NER 与 LLM，
    并提供 dict/JSON/DataFrame/Arrow/SQL 结果集等多种格式适配器。

    English Description:
    Supports field/record/table multi-level classification with a built-in default
    rule engine, pluggable Small-NER and LLM classifiers, and multiple format adapters.

    Attributes:
        resolver: YAML 参数解析器 / YAML parameter resolver.
        rule_engine: Layer-1 规则引擎实例 / Layer-1 rule engine instance.
        composite_engine: 复合规则引擎 / Composite rule engine.
        async_manager: 异步任务管理器 / Async job manager.
        review_store: 复核存储 / Human review store.
        small_ner: Layer-2 Small-NER 引擎 / Layer-2 Small-NER engine.
        llm: Layer-3 LLM 分类器 / Layer-3 LLM classifier.

    Args:
        profile_path: YAML 配置文件路径 / YAML profile file path.
        rule_engine: 规则引擎实例 / Rule engine instance (default: DefaultRuleEngine).
        small_ner: Small-NER 引擎实例 / Small-NER engine instance.
        llm: LLM 分类器实例 / LLM classifier instance.
        resolver: 参数解析器 / Parameter resolver.
        composite_engine: 复合规则引擎 / Composite rule engine.
        async_manager: 异步任务管理器 / Async job manager.
        review_store: 复核存储 / Review store.
        use_vectorized: 是否启用向量化规则引擎 / Whether to enable vectorized rule engine.
    """

    rule_engine: RuleEngineABC
    small_ner: SmallNerEngine
    llm: LlmClassifier

    def __init__(
        self,
        profile_path: str | None = None,
        rule_engine: RuleEngine | None = None,
        small_ner: SmallNerEngine | None = None,
        llm: LlmClassifier | None = None,
        resolver: ParameterResolver | None = None,
        composite_engine: CompositeRuleEngine | None = None,
        async_manager: Any | None = None,
        review_store: Any | None = None,
        use_vectorized: bool = False,
    ):
        """初始化 ClassificationAPI / Initialize ClassificationAPI.

        执行步骤 / Execution Steps:
        1. 初始化参数解析器（YAML profile）。
           (Initialize parameter resolver from YAML profile)
        2. 初始化 Layer-1 规则引擎（支持向量化引擎可选）。
           (Initialize Layer-1 rule engine with optional vectorized engine)
        3. 初始化复合规则引擎、异步管理器、复核存储。
           (Initialize composite engine, async manager, review store)
        4. 自动选择并初始化 Layer-2 Small-NER 引擎（ONNX > ModelScope > NoOp）。
           (Auto-select and initialize Layer-2 Small-NER engine)
        5. 自动选择并初始化 Layer-3 LLM 分类器（Qwen2-VL > NoOp）。
           (Auto-select and initialize Layer-3 LLM classifier)
        """
        # Step 1: 初始化参数解析器（从 YAML profile 文件加载配置）
        self.resolver = resolver or get_resolver(profile_path)

        # Step 2: 初始化 Layer-1 规则引擎
        if rule_engine is None:
            # 未显式传入规则引擎，根据 use_vectorized 标志自动选择
            if use_vectorized:
                try:
                    # 尝试导入并初始化向量化规则引擎（需要 pandas）
                    from .classification_vectorized import VectorizedRuleEngine

                    self.rule_engine = VectorizedRuleEngine()
                except ImportError:
                    # pandas 未安装，回退到标量规则引擎
                    logger.warning(
                        "vectorized_engine_fallback",
                        extra={
                            "reason": "pandas not installed",
                            "fallback": "DefaultRuleEngine",
                        },
                    )
                    self.rule_engine = DefaultRuleEngine()
            else:
                # 默认使用标量规则引擎
                self.rule_engine = DefaultRuleEngine()
        else:
            # 使用调用方显式传入的规则引擎
            self.rule_engine = rule_engine

        # Step 3: 初始化复合规则引擎、异步管理器、复核存储
        self.composite_engine = composite_engine or CompositeRuleEngine()
        # 延迟导入避免循环依赖
        if async_manager is None:
            from .classification_async import AsyncClassificationManager
            async_manager = AsyncClassificationManager()
        self.async_manager = async_manager
        if review_store is None:
            from .classification_review import ReviewStore
            review_store = ReviewStore()
        self.review_store = review_store

        # Step 4: 自动选择 Layer-2 Small-NER 引擎（优先级：ONNX > ModelScope > NoOp）
        if small_ner is None:
            # 计算项目根目录和 ONNX 模型文件路径
            current_dir = os.path.dirname(os.path.abspath(__file__))
            project_root = os.path.dirname(os.path.dirname(current_dir))
            onnx_path = os.path.join(project_root, ".models", "raner_cmeee.onnx")

            if os.path.exists(onnx_path):
                # ONNX 模型文件存在，优先使用 ONNX Runtime 引擎（轻量高效）
                try:
                    from .classification_ner import ONNXSmallNerEngine
                    self.small_ner = ONNXSmallNerEngine()
                except ImportError:
                    # onnxruntime 未安装，降级为空实现
                    self.small_ner = NoOpSmallNerEngine()
            else:
                # ONNX 模型不存在，尝试 ModelScope 引擎（需要 PyTorch）
                try:
                    from .classification_ner import ModelScopeSmallNerEngine
                    self.small_ner = ModelScopeSmallNerEngine()
                except ImportError:
                    # ModelScope 也不可用，降级为空实现
                    self.small_ner = NoOpSmallNerEngine()
        else:
            # 使用调用方显式传入的 NER 引擎
            self.small_ner = small_ner

        # Step 5: 自动选择 Layer-3 LLM 分类器（优先级：Qwen2-VL > NoOp）
        if llm is None:
            try:
                from .classification_llm import Qwen2VLClassifier

                candidate = Qwen2VLClassifier()
                # 检查本地模型目录是否存在，不存在则降级为 NoOp
                if not os.path.isdir(candidate.model_path):
                    self.llm = NoOpLlmClassifier()
                else:
                    self.llm = candidate
            except ImportError:
                # torch/transformers 未安装，降级为空实现
                self.llm = NoOpLlmClassifier()
        else:
            # 使用调用方显式传入的 LLM 分类器
            self.llm = llm

        # 记录初始化摘要日志（可观测性：确认各层引擎类型）
        logger.info(
            "classification_api_initialized",
            extra={
                "rule_engine": type(self.rule_engine).__name__,
                "small_ner": type(self.small_ner).__name__,
                "llm": type(self.llm).__name__,
                "use_vectorized": use_vectorized,
            },
        )

    # ------------------------------------------------------------------
    # 核心方法 / Core Methods
    # ------------------------------------------------------------------

    def classify_field(
        self,
        field_name: str,
        value: Any,
        params: dict[str, Any] | None = None,
    ) -> FieldClassificationResult:
        """对单个字段进行分类 / Classify a Single Field.

        执行步骤 / Execution Steps:
        1. 解析并合并分类参数。
           (Resolve and merge classification parameters)
        2. 委托给 _classify_field_internal 执行三层漏斗分类。
           (Delegate to _classify_field_internal for 3-layer funnel classification)

        Args:
            field_name: 字段名 / Field name.
            value: 字段值 / Field value.
            params: 请求级参数 / Request-level parameters.

        Returns:
            FieldClassificationResult / Field classification result.
        """
        # 记录分类开始时间（用于计算耗时）
        start_time = time.monotonic()
        # 委托给内部方法执行三层漏斗分类
        result = self._classify_field_internal(field_name, value, params)
        # 计算分类耗时
        duration = time.monotonic() - start_time

        # 记录 Prometheus 指标：分类总次数（按最终等级和决策层标签）
        CLASSIFICATION_TOTAL.labels(
            final_level=result.final_level.value,
            layer=result.engine_layer.value,
        ).inc()
        # 记录字段级分类耗时到直方图
        CLASSIFICATION_DURATION.labels(operation="field").observe(duration)

        return result

    def _classify_field_internal(
        self,
        field_name: str,
        value: Any,
        params: dict[str, Any] | None = None,
        initial_tags: list[SecurityTag] | None = None,
    ) -> FieldClassificationResult:
        """对单个字段执行三层漏斗分类 / Execute 3-Layer Funnel Classification for a Field.

        中文说明：
        允许调用方预计算 Layer-1 规则标签（向量化批量模式），否则按常规流程评估。
        三层漏斗：Layer-1 规则引擎 → Layer-2 Small-NER → Layer-3 LLM。

        English Description:
        Allows callers to pre-compute Layer-1 rule tags (vectorized batch mode),
        otherwise evaluates through the standard 3-layer funnel:
        Layer-1 Rule Engine → Layer-2 Small-NER → Layer-3 LLM.

        执行步骤 / Execution Steps:
        1. 解析分类参数并初始化标签列表。
           (Resolve classification params and initialize tag list)
        2. Layer-1: 执行规则引擎评估（若未提供预计算标签）。
           (Layer-1: Execute rule engine evaluation if no pre-computed tags)
        3. Layer-2: 执行 Small-NER 实体提取（当启用且置信度不足时）。
           (Layer-2: Execute Small-NER entity extraction when enabled and confidence insufficient)
        4. Layer-3: 执行 LLM 分类回退（当启用或置信度低于阈值时）。
           (Layer-3: Execute LLM classification fallback when enabled or confidence below threshold)
        5. 应用人工覆盖并构造最终结果。
           (Apply manual override and construct final result)

        Args:
            field_name: 字段名 / Field name.
            value: 字段值 / Field value.
            params: 请求级参数 / Request-level parameters.
            initial_tags: 预计算的 Layer-1 标签 / Pre-computed Layer-1 tags.

        Returns:
            FieldClassificationResult / Field classification result.
        """
        # Step 1: 解析分类参数（合并默认值/profile/模板/请求参数）
        cp, _source = _resolve_classification_params(self.resolver, params)

        # 初始化标签列表：如果调用方提供了预计算标签（向量化模式）则使用，否则为空
        tags: list[SecurityTag] = list(initial_tags) if initial_tags is not None else []
        # 初始决策层为 Layer-1 规则引擎
        engine_layer = EngineLayer.L1_RULE
        # 初始化推理说明文本
        reasoning = ""

        # Step 2: Layer-1 规则引擎评估（仅当未提供预计算标签且启用规则引擎时）
        if initial_tags is None and cp.enable_rule_engine:
            tags = self.rule_engine.evaluate(field_name, value, cp)

        # 计算当前最高等级：有标签则取最高，否则使用默认等级
        final_level = max_level(*(t.level for t in tags)) if tags else cp.default_level
        # 计算置信度：有规则命中则为 1.0，否则为 0.0
        confidence = 1.0 if tags else 0.0

        # 如果有规则命中，构建推理说明（列出命中的规则 ID）
        if tags:
            rule_ids = [t.rule_id for t in tags if t.rule_id]
            reasoning = "命中规则: " + ", ".join(rule_ids)

        # Step 3: Layer-2 Small-NER 实体提取
        # 触发条件：启用 NER 且（无规则命中 或 当前等级 <= L3）
        if cp.enable_small_ner and (not tags or final_level.value <= SensitivityLevel.L3.value):
            ner_tags = self._run_small_ner(field_name, value)
            if ner_tags:
                # 将 NER 标签追加到标签列表
                tags.extend(ner_tags)
                # 重新计算最高等级
                final_level = max_level(final_level, *(t.level for t in ner_tags))
                # 更新置信度为 NER 标签中的最高置信度
                confidence = max(confidence, max(t.confidence for t in ner_tags))
                # 更新决策层为 Layer-2
                engine_layer = EngineLayer.L2_SMALL_NER

        # Step 4: Layer-3 LLM 分类回退
        # 触发条件：显式启用 LLM 或 置信度低于阈值
        if cp.enable_llm or confidence < cp.llm_confidence_threshold:
            llm_result = self.llm.classify(str(value), final_level, confidence)
            if llm_result:
                # LLM 返回有效结果：更新等级、置信度、推理说明和决策层
                final_level = parse_level(llm_result.get("final_level", final_level))
                confidence = float(llm_result.get("confidence", confidence))
                reasoning = str(llm_result.get("reasoning", reasoning))
                engine_layer = EngineLayer.L3_LLM
                # 记录 LLM 命中指标
                CLASSIFICATION_LLM_TOTAL.labels(status="hit").inc()
            else:
                # LLM 返回 None（降级/失败）：记录未命中指标
                CLASSIFICATION_LLM_TOTAL.labels(status="miss").inc()

        # Step 5: 应用人工覆盖（如果配置了 manual_override 规则）
        overridden_level = cp.apply_manual_override(field_name, final_level)
        if overridden_level != final_level:
            # 创建人工覆盖标签
            manual_tag = SecurityTag(
                level=overridden_level,
                category="MANUAL_OVERRIDE",
                confidence=1.0,
                source_engine="MANUAL",
                rule_id="MANUAL_001",
                needs_human_review=False,
            )
            # 将人工标签置于标签列表最前并去重
            tags = _unique_tags([manual_tag, *tags])
            # 更新最终等级为人工覆盖值
            final_level = overridden_level
            # 人工覆盖视为 Layer-1 决策
            engine_layer = EngineLayer.L1_RULE
            reasoning = f"人工覆盖为 {final_level.value}"
            # 记录人工覆盖事件日志
            logger.info(
                "classification_manual_override_applied",
                extra={
                    "field_name": field_name,
                    "overridden_level": final_level.value,
                },
            )

        # 构造并返回字段级分类结果
        return FieldClassificationResult(
            field_name=field_name,
            # 如果参数要求返回字段值，生成摘要（图片数据不会返回原始内容）
            field_value=_summarize_field_value(value) if cp.return_field_values else None,
            tags=tags,                          # 所有命中的安全标签
            final_level=final_level,            # 最终敏感度等级
            confidence=confidence,              # 综合置信度
            engine_layer=engine_layer,          # 最终决策层
            needs_human_review=any(t.needs_human_review for t in tags),  # 是否需人工复核
            reasoning=reasoning,                # 推理说明
        )

    @staticmethod
    def _make_tag(
        level: SensitivityLevel,
        category: str,
        confidence: float,
        rule_id: str,
        needs_human_review: bool = False,
    ) -> SecurityTag:
        """构造 Small-NER 输出 SecurityTag，减少重复字段构造。"""
        return SecurityTag(
            level=level,
            category=category,
            confidence=confidence,
            source_engine="SMALL_NER",
            rule_id=rule_id,
            needs_human_review=needs_human_review,
        )

    def _run_small_ner(self, field_name: str, value: Any) -> list[SecurityTag]:
        """执行 Small-NER 实体提取并转换为 SecurityTag / Execute Small-NER and Convert to SecurityTags.

        中文说明：
        调用 Layer-2 Small-NER 引擎提取医疗实体（疾病、药物、手术、身体部位、基因提示），
        并根据实体类型和敏感关键字判定敏感度等级。

        English Description:
        Invokes Layer-2 Small-NER engine to extract medical entities (disease, medication,
        surgery, body part, genomic hints) and determines sensitivity level based on
        entity type and sensitive keyword matching.

        执行步骤 / Execution Steps:
        1. 调用 NER 引擎提取实体列表。
           (Invoke NER engine to extract entity list)
        2. 根据实体标签类型映射到对应的敏感度等级和类别。
           (Map entity label types to corresponding sensitivity levels and categories)
        3. 检查敏感疾病关键字以确定是否升级至 L4。
           (Check sensitive disease keywords to determine L4 escalation)

        Args:
            field_name: 字段名 / Field name.
            value: 字段值 / Field value.

        Returns:
            SecurityTag 列表 / List of SecurityTags.
        """
        # 调用 NER 引擎提取医疗实体（将字段值转为字符串，空值转为空字符串）
        entities = self.small_ner.extract(str(value) if value is not None else "")
        tags: list[SecurityTag] = []

        # 记录 NER 调用指标：命中/未命中
        if entities:
            CLASSIFICATION_NER_TOTAL.labels(status="hit").inc()
        else:
            CLASSIFICATION_NER_TOTAL.labels(status="miss").inc()

        # 敏感疾病关键字列表：包含这些关键字的疾病将升级至 L4
        sensitive_keywords = ["hiv", "精神分裂", "艾滋", "梅毒", "肿瘤", "癌症", "白血病", "抑郁症"]

        # 遍历每个识别出的实体，根据实体类型映射到对应的敏感度等级
        for ent in entities:
            label = ent.get("label", "")       # 实体标签（如 MEDICAL_DISEASE/MEDICATION）
            text = str(ent.get("text", "")).lower()  # 实体文本（小写化用于关键字匹配）
            conf = float(ent.get("confidence", 0.0))  # 实体识别置信度

            # 基因组提示：最高敏感度 L5，需人工复核
            if label == "GENOMIC_HINT":
                tags.append(
                    self._make_tag(
                        SensitivityLevel.L5,
                        "GENOMIC_HINT",
                        conf,
                        "NER_GENE_001",
                        needs_human_review=True,  # 基因组数据必须人工确认
                    )
                )
            # 医学疾病：检查敏感关键字决定 L4 还是 L3
            elif label == "MEDICAL_DISEASE":
                if any(kw in text for kw in sensitive_keywords):
                    # 包含敏感关键字（HIV/精神分裂/梅毒等）→ L4
                    tags.append(
                        self._make_tag(
                            SensitivityLevel.L4,
                            "MEDICAL_SENSITIVE_DISEASE",
                            conf,
                            "NER_DIS_SENSITIVE",
                        )
                    )
                else:
                    # 普通疾病 → L3
                    tags.append(
                        self._make_tag(
                            SensitivityLevel.L3,
                            "MEDICAL_DISEASE",
                            conf,
                            "NER_DIS_NORMAL",
                        )
                    )
            # 药物：标准 L3 敏感度
            elif label == "MEDICATION":
                tags.append(
                    self._make_tag(
                        SensitivityLevel.L3,
                        "MEDICATION",
                        conf,
                        "NER_DRU_001",
                    )
                )
            # 手术/操作：标准 L3 敏感度
            elif label == "SURGERY":
                tags.append(
                    self._make_tag(
                        SensitivityLevel.L3,
                        "SURGERY",
                        conf,
                        "NER_PRO_001",
                    )
                )
            # 身体部位：标准 L3 敏感度
            elif label == "BODY_PART":
                tags.append(
                    self._make_tag(
                        SensitivityLevel.L3,
                        "BODY_PART",
                        conf,
                        "NER_BOD_001",
                    )
                )
        return tags

    def classify_record(
        self,
        record: dict[str, Any],
        params: dict[str, Any] | None = None,
        record_index: int = 0,
    ) -> RecordClassificationResult:
        """对单条记录进行分类 / Classify a Single Record.

        中文说明：
        对记录中的每个字段执行分类，聚合结果并应用复合规则后处理。

        English Description:
        Classifies each field in the record, aggregates results and applies
        composite rule post-processing.

        执行步骤 / Execution Steps:
        1. 对记录中每个字段执行 classify_field。
           (Execute classify_field for each field in the record)
        2. 聚合所有字段标签并计算最终等级和置信度。
           (Aggregate all field tags and compute final level and confidence)
        3. 应用复合/上下文敏感规则后处理。
           (Apply composite/context-aware rule post-processing)

        Args:
            record: 字段名到字段值的映射 / Field name to value mapping.
            params: 请求级参数 / Request-level parameters.
            record_index: 记录索引 / Record index for output.

        Returns:
            RecordClassificationResult / Record classification result.
        """
        # 记录分类开始时间
        start_time = time.monotonic()
        # 存储每个字段的分类结果
        field_results: dict[str, FieldClassificationResult] = {}
        # 聚合所有字段的标签
        aggregated_tags: list[SecurityTag] = []

        # Step 1: 对记录中每个字段执行分类
        for field_name, value in record.items():
            field_result = self.classify_field(field_name, value, params)
            field_results[field_name] = field_result
            # 收集字段标签用于记录级聚合
            aggregated_tags.extend(field_result.tags)

        # Step 2: 聚合标签并计算记录级最终等级/置信度
        aggregated_tags = _unique_tags(aggregated_tags)  # 去重
        # 记录级等级 = 所有字段中的最高等级
        final_level = (
            max_level(*(fr.final_level for fr in field_results.values()))
            if field_results
            else SensitivityLevel.L1  # 空记录默认 L1
        )
        # 记录级置信度 = 所有字段中的最高置信度
        confidence = (
            max(fr.confidence for fr in field_results.values())
            if field_results
            else 0.0
        )

        # 构造记录级分类结果
        record_result = RecordClassificationResult(
            record_index=record_index,
            field_results=field_results,
            aggregated_tags=aggregated_tags,
            final_level=final_level,
            confidence=confidence,
            # 任一字段需复核则记录级也需复核
            needs_human_review=any(fr.needs_human_review for fr in field_results.values()),
        )

        # Step 3: 应用复合/上下文敏感规则后处理
        # 解析参数获取复合规则配置
        cp, _ = _resolve_classification_params(self.resolver, params)
        custom_rules = None
        # 如果请求中携带自定义复合规则，解析并构造临时引擎
        if cp.composite_rules:
            custom_rules = [CompositeRule.model_validate(r) for r in cp.composite_rules]
        # 使用自定义规则引擎或默认复合规则引擎
        engine = (
            CompositeRuleEngine(custom_rules)
            if custom_rules
            else self.composite_engine
        )
        # 执行复合规则评估（如：身份证+手机号同时出现 → 升级）
        composite_tags = engine.evaluate(record, field_results)
        # 将复合规则标签应用到记录结果（可能升级等级）
        record_result = cast(
            "RecordClassificationResult",
            apply_composite_tags(record_result, composite_tags),
        )

        # 记录记录级分类耗时指标
        duration = time.monotonic() - start_time
        CLASSIFICATION_DURATION.labels(operation="record").observe(duration)

        return record_result

    def classify_table(
        self,
        schema: list[str],
        rows: list[dict[str, Any]],
        params: dict[str, Any] | None = None,
    ) -> TableClassificationResult:
        """对整张表进行分类 / Classify an Entire Table.

        中文说明：
        对表中每条记录执行分类，支持向量化引擎加速，并可选启用影子模式对比。

        English Description:
        Classifies each record in the table with optional vectorized engine acceleration
        and shadow mode comparison.

        执行步骤 / Execution Steps:
        1. 解析参数并判断是否使用向量化引擎。
           (Resolve params and determine whether to use vectorized engine)
        2. 对每条记录执行 classify_record 并聚合结果。
           (Execute classify_record for each row and aggregate results)
        3. 可选启用复核条目收集和影子模式对比。
           (Optionally enable review entry collection and shadow mode comparison)

        Args:
            schema: 列名列表 / Column name list (determines output order).
            rows: 记录列表 / Record list (each record is a field name to value dict).
            params: 请求级参数 / Request-level parameters.

        Returns:
            TableClassificationResult / Table classification result.
        """
        # 记录分类开始时间
        start_time = time.monotonic()
        # 解析分类参数
        cp, _ = _resolve_classification_params(self.resolver, params)

        # 如果规则引擎支持向量化 evaluate_series，使用向量化路径加速
        if rows and hasattr(self.rule_engine, "evaluate_series"):
            result = self._classify_table_vectorized(schema, rows, params, cp)
            # 记录表级分类耗时
            CLASSIFICATION_DURATION.labels(operation="table").observe(time.monotonic() - start_time)
            return result

        # 非向量化路径：逐记录分类
        record_results: list[RecordClassificationResult] = []
        aggregated_tags: list[SecurityTag] = []
        review_entries: list[Any] = []

        # Step 2: 逐条记录分类并聚合结果
        for idx, row in enumerate(rows):
            # 仅保留 schema 中存在的列（过滤多余字段）
            filtered = {col: row.get(col) for col in schema if col in row}
            # 对单条记录执行分类
            record_result = self.classify_record(filtered, params, record_index=idx)
            record_results.append(record_result)
            aggregated_tags.extend(record_result.aggregated_tags)
            # 如果启用复核，收集需人工复核的条目
            if cp.enable_review:
                review_entries.extend(
                    self.review_store.add_from_record(record_result, original_record=row)
                )

        # 聚合所有记录的标签并计算表级最终等级/置信度
        aggregated_tags = _unique_tags(aggregated_tags)
        final_level = (
            max_level(*(rr.final_level for rr in record_results))
            if record_results
            else SensitivityLevel.L1  # 空表默认 L1
        )
        confidence = (
            max(rr.confidence for rr in record_results) if record_results else 0.0
        )

        # Step 3: 影子模式对比（可选，用于规则版本升级前的 A/B 测试）
        shadow_diff: list[ShadowDiff] = []
        if cp.shadow_mode and cp.shadow_version:
            # 排除影子模式相关参数，避免无限递归
            shadow_keys = {
                "shadow_mode", "shadowMode", "shadow_version", "shadowVersion",
                "rule_set_version", "ruleSetVersion",
            }
            # 构造影子参数：使用影子版本号，关闭影子模式
            shadow_params = {k: v for k, v in (params or {}).items() if k not in shadow_keys}
            shadow_params["rule_set_version"] = cp.shadow_version
            shadow_params["shadow_mode"] = False
            # 使用影子版本规则重新分类
            shadow_result = self.classify_table(schema, rows, shadow_params)
            # 计算当前版本与影子版本的差异
            shadow_diff = self._compute_shadow_diff(record_results, shadow_result.record_results)

        # 记录表级分类耗时和摘要日志
        duration = time.monotonic() - start_time
        CLASSIFICATION_DURATION.labels(operation="table").observe(duration)
        logger.info(
            "classification_table_completed",
            extra={
                "num_records": len(record_results),
                "final_level": final_level.value,
                "duration_seconds": round(duration, 4),
                "shadow_diffs": len(shadow_diff),
            },
        )

        # 构造并返回表级分类结果
        return TableClassificationResult(
            schema=schema,
            record_results=record_results,
            aggregated_tags=aggregated_tags,
            final_level=final_level,
            confidence=confidence,
            needs_human_review=any(rr.needs_human_review for rr in record_results),
            review_entries=review_entries,
            shadow_diff=shadow_diff,
        )

    def _classify_table_vectorized(
        self,
        schema: list[str],
        rows: list[dict[str, Any]],
        params: dict[str, Any] | None,
        cp: ClassificationParams,
    ) -> TableClassificationResult:
        """使用向量化规则引擎对整张表进行分类。

        仅当 ``self.rule_engine`` 提供 ``evaluate_series`` 时由 ``classify_table`` 调用。
        规则引擎批量计算 Layer-1 标签，NER/LLM/复合规则/复核仍按记录处理。

        性能优势：
        - Layer-1 规则引擎使用 pandas 向量化操作，一次处理整列 N 行
        - 避免 N 次 Python 函数调用开销
        """
        # 导入 pandas 并构造 DataFrame
        import pandas as pd

        # 将记录列表转为 DataFrame（列顺序按 schema）
        df = pd.DataFrame(rows, columns=schema)
        record_results: list[RecordClassificationResult] = []
        aggregated_tags: list[SecurityTag] = []
        review_entries: list[Any] = []

        # 复合规则只解析一次，避免每行循环重复构造引擎
        custom_rules = None
        if cp.composite_rules:
            custom_rules = [CompositeRule.model_validate(r) for r in cp.composite_rules]
        engine = CompositeRuleEngine(custom_rules) if custom_rules else self.composite_engine

        # 批量计算每列的 Layer-1 标签（向量化核心优势所在）
        rule_engine = cast("_SupportsEvaluateSeries", self.rule_engine)
        column_tags: dict[str, list[list[SecurityTag]]] = {}
        if cp.enable_rule_engine:
            # 对每列调用 evaluate_series 批量评估（一次处理 N 行）
            for field_name in schema:
                column_tags[field_name] = rule_engine.evaluate_series(
                    field_name, df[field_name], cp
                )
        else:
            # 规则引擎禁用时，所有列的标签为空
            empty: list[list[SecurityTag]] = [[] for _ in range(len(rows))]
            for field_name in schema:
                column_tags[field_name] = empty

        # 逐行处理：使用预计算的 Layer-1 标签，继续执行 Layer-2/3 和复合规则
        for idx, row in enumerate(rows):
            field_results: dict[str, FieldClassificationResult] = {}
            for field_name in schema:
                value = row.get(field_name)
                # 获取该列该行的预计算 Layer-1 标签
                initial_tags = column_tags[field_name][idx]
                # 使用预计算标签执行三层漏斗分类（跳过 Layer-1 重复计算）
                field_result = self._classify_field_internal(
                    field_name, value, params, initial_tags=initial_tags
                )
                field_results[field_name] = field_result

            # 聚合字段标签并计算记录级等级/置信度
            aggregated = _unique_tags(
                [tag for fr in field_results.values() for tag in fr.tags]
            )
            final_level = (
                max_level(*(fr.final_level for fr in field_results.values()))
                if field_results
                else SensitivityLevel.L1
            )
            confidence = (
                max(fr.confidence for fr in field_results.values())
                if field_results
                else 0.0
            )

            # 构造记录级分类结果
            record_result = RecordClassificationResult(
                record_index=idx,
                field_results=field_results,
                aggregated_tags=aggregated,
                final_level=final_level,
                confidence=confidence,
                needs_human_review=any(
                    fr.needs_human_review for fr in field_results.values()
                ),
            )

            # 复合/上下文敏感规则后处理（引擎已在循环外构造，避免重复初始化）
            composite_tags = engine.evaluate(row, field_results)
            record_result = apply_composite_tags(record_result, composite_tags)

            record_results.append(record_result)
            aggregated_tags.extend(record_result.aggregated_tags)
            # 收集复核条目
            if cp.enable_review:
                review_entries.extend(
                    self.review_store.add_from_record(record_result, original_record=row)
                )

        # 聚合表级结果
        aggregated_tags = _unique_tags(aggregated_tags)
        final_level = (
            max_level(*(rr.final_level for rr in record_results))
            if record_results
            else SensitivityLevel.L1
        )
        confidence = (
            max(rr.confidence for rr in record_results) if record_results else 0.0
        )

        # 影子模式对比（与非向量化路径相同逻辑）
        shadow_diff: list[ShadowDiff] = []
        if cp.shadow_mode and cp.shadow_version:
            shadow_keys = {
                "shadow_mode",
                "shadowMode",
                "shadow_version",
                "shadowVersion",
                "rule_set_version",
                "ruleSetVersion",
            }
            shadow_params = {
                k: v for k, v in (params or {}).items() if k not in shadow_keys
            }
            shadow_params["rule_set_version"] = cp.shadow_version
            shadow_params["shadow_mode"] = False
            shadow_result = self.classify_table(schema, rows, shadow_params)
            shadow_diff = self._compute_shadow_diff(
                record_results, shadow_result.record_results
            )

        # 构造并返回表级分类结果
        return TableClassificationResult(
            schema=schema,
            record_results=record_results,
            aggregated_tags=aggregated_tags,
            final_level=final_level,
            confidence=confidence,
            needs_human_review=any(rr.needs_human_review for rr in record_results),
            review_entries=review_entries,
            shadow_diff=shadow_diff,
        )

    def _compute_shadow_diff(
        self,
        current_results: list[RecordClassificationResult],
        shadow_results: list[RecordClassificationResult],
    ) -> list[ShadowDiff]:
        """计算当前结果与影子结果的差异。

        用于规则版本升级前的 A/B 测试：对比新旧规则集的分类结果，
        找出等级发生变化的字段，供运维人员评估规则变更影响。

        Args:
            current_results: 当前规则版本的分类结果。
            shadow_results: 影子规则版本的分类结果。

        Returns:
            差异列表（仅包含等级发生变化的字段）。
        """
        diffs: list[ShadowDiff] = []
        # 逐记录对比
        for cur, shw in zip(current_results, shadow_results):
            # 遍历两个版本中的所有字段（取并集）
            for field_name in set(cur.field_results.keys()) | set(shw.field_results.keys()):
                cur_field = cur.field_results.get(field_name)
                shw_field = shw.field_results.get(field_name)
                # 跳过仅在一个版本中存在的字段
                if not cur_field or not shw_field:
                    continue
                # 仅记录等级发生变化的字段
                if cur_field.final_level != shw_field.final_level:
                    diffs.append(
                        ShadowDiff(
                            field_name=field_name,
                            record_index=cur.record_index,
                            current_level=cur_field.final_level,
                            shadow_level=shw_field.final_level,
                            current_tags=[str(tag) for tag in cur_field.tags],
                            shadow_tags=[str(tag) for tag in shw_field.tags],
                        )
                    )
        return diffs

    # ------------------------------------------------------------------
    # 多格式适配器 / Format Adapters
    # ------------------------------------------------------------------

    def classify_json(
        self,
        json_input: Any,
        params: dict[str, Any] | None = None,
    ) -> ClassificationResult:
        """解析 JSON 字符串或字典并分类 / Parse JSON Input and Classify.

        中文说明：
        若顶层为 dict 则按单条记录分类；若为 list 则按表分类（schema 取并集）。

        English Description:
        If top-level is dict, classifies as single record; if list, classifies as table
        (schema is the union of all keys).

        Args:
            json_input: JSON 字符串、字典或列表 / JSON string, dict or list.
            params: 请求级参数 / Request-level parameters.

        Returns:
            ClassificationResult / Classification result wrapper.

        Raises:
            ValueError: 当输入不是 dict 或 list 时 / When input is not dict or list.
        """
        # 解析 JSON 输入：如果是字符串则先解析为 Python 对象
        data = json.loads(json_input) if isinstance(json_input, str) else json_input

        # 解析分类参数并记录来源
        cp, source = _resolve_classification_params(self.resolver, params)

        # 情况 1：顶层为 dict → 按单条记录分类
        if isinstance(data, dict):
            record_result = self.classify_record(data, params)
            return ClassificationResult(
                record_result=record_result,
                audit_info=_build_audit_info(cp, source),
            )

        # 情况 2：顶层为非空 list → 按表分类（schema 取所有 key 的并集）
        if isinstance(data, list) and data:
            # 从所有记录中收集全部字段名作为 schema（排序保证确定性）
            schema = sorted({col for row in data for col in row})
            table_result = self.classify_table(schema, data, params)
            return ClassificationResult(
                table_result=table_result,
                audit_info=_build_audit_info(cp, source),
            )

        # 情况 3：空列表 → 返回空表结果
        if isinstance(data, list) and not data:
            return ClassificationResult(
                table_result=TableClassificationResult(schema=[], final_level=SensitivityLevel.L1),
                audit_info=_build_audit_info(cp, source),
            )

        # 其他情况：不支持的输入类型
        raise ValueError("JSON input must be a dict or a list of dicts")

    def classify_dataframe(
        self,
        df: Any,
        params: dict[str, Any] | None = None,
    ) -> ClassificationResult:
        """对 pandas DataFrame 进行分类 / Classify a pandas DataFrame.

        中文说明：可选依赖，未安装 pandas 时抛出 ImportError。
        English Description: Optional dependency; raises ImportError if pandas not installed.

        Args:
            df: pandas.DataFrame 实例 / pandas.DataFrame instance.
            params: 请求级参数 / Request-level parameters.

        Returns:
            ClassificationResult / Classification result wrapper.

        Raises:
            TypeError: 当输入不是 DataFrame 时 / When input is not a DataFrame.
        """
        # 导入 pandas（未安装时此处抛出 ImportError）
        import pandas as pd

        # 类型校验：确保输入是 DataFrame
        if not isinstance(df, pd.DataFrame):
            raise TypeError("classify_dataframe expects a pandas.DataFrame")
        # 提取列名作为 schema
        schema = list(df.columns)
        # 将 DataFrame 转为记录列表（每行为一个 dict）
        rows = df.to_dict(orient="records")
        # 委托给 classify_table 执行实际分类
        table_result = self.classify_table(schema, rows, params)
        # 解析参数并构建审计信息
        cp, source = _resolve_classification_params(self.resolver, params)
        return ClassificationResult(
            table_result=table_result,
            audit_info=_build_audit_info(cp, source),
        )

    def classify_arrow(
        self,
        table: Any,
        params: dict[str, Any] | None = None,
    ) -> ClassificationResult:
        """对 pyarrow Table 进行分类 / Classify a pyarrow Table.

        中文说明：可选依赖，未安装 pyarrow 时抛出 ImportError。
        English Description: Optional dependency; raises ImportError if pyarrow not installed.

        Args:
            table: pyarrow.Table 实例 / pyarrow.Table instance.
            params: 请求级参数 / Request-level parameters.

        Returns:
            ClassificationResult / Classification result wrapper.

        Raises:
            TypeError: 当输入不是 pyarrow.Table 时 / When input is not a pyarrow.Table.
        """
        # 导入 pyarrow（未安装时此处抛出 ImportError）
        import pyarrow as pa

        # 类型校验：确保输入是 pyarrow.Table
        if not isinstance(table, pa.Table):
            raise TypeError("classify_arrow expects a pyarrow.Table")
        # 提取列名作为 schema
        schema = list(table.column_names)
        # 先转为 pandas DataFrame 再转为记录列表
        rows = table.to_pandas().to_dict(orient="records")
        # 委托给 classify_table 执行实际分类
        table_result = self.classify_table(schema, rows, params)
        # 解析参数并构建审计信息
        cp, source = _resolve_classification_params(self.resolver, params)
        return ClassificationResult(
            table_result=table_result,
            audit_info=_build_audit_info(cp, source),
        )

    def classify_sql_result(
        self,
        result_set: list[dict[str, Any]],
        params: dict[str, Any] | None = None,
    ) -> ClassificationResult:
        """对 SQL 结果集进行分类 / Classify a SQL Result Set.

        Args:
            result_set: 查询结果列表 / Query result list (each record is a field name to value dict).
            params: 请求级参数 / Request-level parameters.

        Returns:
            ClassificationResult / Classification result wrapper.
        """
        # 空结果集：直接返回空表结果
        if not result_set:
            cp, source = _resolve_classification_params(self.resolver, params)
            return ClassificationResult(
                table_result=TableClassificationResult(
                    schema=[], final_level=SensitivityLevel.L1
                ),
                audit_info=_build_audit_info(cp, source),
            )
        # 从所有记录中收集全部字段名作为 schema
        schema = sorted({col for row in result_set for col in row})
        # 委托给 classify_table 执行实际分类
        table_result = self.classify_table(schema, result_set, params)
        # 解析参数并构建审计信息
        cp, source = _resolve_classification_params(self.resolver, params)
        return ClassificationResult(
            table_result=table_result,
            audit_info=_build_audit_info(cp, source),
        )

    def classify_secretflow(
        self,
        sf_data: Any,
        params: dict[str, Any] | None = None,
        party: str | None = None,
    ) -> ClassificationResult:
        """对 SecretFlow 联邦数据结构进行分类 / Classify SecretFlow Federated Data.

        Args:
            sf_data: SecretFlow DataFrame / HDataFrame / VDataFrame / FedNdarray.
            params: 请求级分类参数 / Request-level classification parameters.
            party: HDataFrame 参与方 / HDataFrame party identifier.

        Returns:
            ClassificationResult / Classification result wrapper.
        """
        # 委托给 classification_utils 中的 SecretFlow 适配器处理联邦数据结构
        table_result = classify_secretflow(self, sf_data, params=params, party=party)
        # 解析参数并构建审计信息
        cp, source = _resolve_classification_params(self.resolver, params)
        return ClassificationResult(
            table_result=table_result,
            audit_info=_build_audit_info(cp, source),
        )

    def submit_classify_table_async(
        self,
        schema: list[str],
        rows: list[dict[str, Any]],
        params: dict[str, Any] | None = None,
    ) -> str:
        """提交异步表分类任务 / Submit Async Table Classification Job.

        Args:
            schema: 列名列表 / Column name list.
            rows: 记录列表 / Record list.
            params: 请求级参数 / Request-level parameters.

        Returns:
            异步任务 ID / Async job ID.
        """
        # 将 classify_table 方法及其参数提交到异步任务管理器
        return self.async_manager.submit(self.classify_table, schema, rows, params)

    def get_job_result(self, job_id: str) -> ClassificationJob:
        """查询异步分类任务结果 / Get Async Classification Job Result.

        Args:
            job_id: 任务 ID / Job ID.

        Returns:
            ClassificationJob / Classification job status and result.
        """
        # 委托给异步任务管理器查询任务状态
        return self.async_manager.get(job_id)

    def is_llm_ready(self) -> bool:
        """判断 LLM 分类器是否已预热就绪 / Check if LLM Classifier is Ready.

        中文说明：
        - ``NoOpLlmClassifier`` 视为始终就绪（无需预热）。
        - ``Qwen2VLClassifier`` 根据其内部初始化状态返回。
        - 其他自定义 LLM 分类器默认视为就绪。

        English Description:
        - ``NoOpLlmClassifier`` is always ready (no warmup needed).
        - ``Qwen2VLClassifier`` returns based on its internal initialization state.
        - Other custom LLM classifiers are considered ready by default.

        Returns:
            True 表示 LLM 已就绪或无需预热 / True if LLM is ready or no warmup needed.
        """
        # NoOpLlmClassifier 始终就绪（无需预热）
        if isinstance(self.llm, NoOpLlmClassifier):
            return True
        try:
            from .classification_llm import Qwen2VLClassifier
        except ImportError:
            # 无法导入 Qwen2VL 类，视为就绪
            return True
        if isinstance(self.llm, Qwen2VLClassifier):
            # 仅当大模型成功初始化且无错误时，才视为就绪
            return self.llm.is_ready
        # 其他自定义 LLM 分类器默认视为就绪
        return True

    async def warmup_async(self) -> bool:
        """异步预热 LLM 分类器 / Async Warmup LLM Classifier.

        中文说明：
        对真实本地模型（如 Qwen2-VL）在后台线程中加载权重，避免阻塞事件循环。
        对 ``NoOpLlmClassifier`` 不执行任何操作并直接返回 True。

        English Description:
        Loads model weights in a background thread for real local models (e.g. Qwen2-VL)
        to avoid blocking the event loop. Does nothing for ``NoOpLlmClassifier``.

        Returns:
            是否成功完成预热 / Whether warmup completed successfully.
        """
        # NoOpLlmClassifier 无需预热，直接返回 True
        if isinstance(self.llm, NoOpLlmClassifier):
            return True
        try:
            from .classification_llm import Qwen2VLClassifier
        except ImportError:
            return True
        # 非 Qwen2VL 分类器无需预热
        if not isinstance(self.llm, Qwen2VLClassifier):
            return True

        # 使用 asyncio 将同步的 warmup() 放到线程池执行，避免阻塞事件循环
        import asyncio

        loop = asyncio.get_running_loop()
        try:
            # 在默认线程池中执行模型加载（不阻塞 FastAPI 事件循环）
            return await loop.run_in_executor(None, self.llm.warmup)
        except Exception as exc:
            # 预热失败记录警告日志（不影响服务启动）
            logger.warning(
                "llm_warmup_failed",
                extra={"error": str(exc)},
            )
            return False

    def confirm_review(
        self,
        review_id: str,
        corrected_level: str,
        reviewer: str = "",
        comment: str = "",
    ) -> Any:
        """确认或修正复核条目 / Confirm or Correct a Review Entry.

        Args:
            review_id: 复核条目 ID / Review entry ID.
            corrected_level: 修正后的等级 / Corrected sensitivity level.
            reviewer: 复核人 / Reviewer identifier.
            comment: 说明 / Comment.

        Returns:
            ReviewEntry / Updated review entry.
        """
        # 委托给复核存储执行确认操作
        return self.review_store.confirm(review_id, corrected_level, reviewer, comment)

    def export_reviews(self, format: str = "jsonl", mask_input: bool = False) -> str:  # noqa: A002
        """导出复核样本 / Export Review Samples.

        Args:
            format: `jsonl` 或 `csv` / Export format (`jsonl` or `csv`).
            mask_input: 是否对 input 掩码 / Whether to mask input values.

        Returns:
            导出内容字符串 / Exported content string.
        """
        # 委托给复核存储执行导出操作
        return self.review_store.export(format=format, mask_input=mask_input)


def _build_audit_info(params: ClassificationParams, parameter_source: str) -> AuditInfo:
    """构建审计信息 / Build Audit Info.

    中文说明：记录分类请求的执行元数据，包括版本、时间戳、参数来源等。
    审计信息随分类结果一起返回，用于合规追溯和问题排查。

    English Description: Records execution metadata for classification requests.

    Args:
        params: 分类参数 / Classification parameters.
        parameter_source: 参数来源（default/profile/request/manual） / Parameter source indicator.

    Returns:
        AuditInfo / Audit information.
    """
    return AuditInfo(
        version=_PRIMITIVE_VERSION,              # 分类原语版本
        profile_version=params.version,          # 参数配置版本
        timestamp=datetime.now(timezone.utc).isoformat(),  # 执行时间戳（UTC）
        rule_engine_version=_RULE_ENGINE_VERSION,  # 规则引擎版本
        rule_set_version=params.rule_set_version,  # 规则集版本
        parameter_source=parameter_source,       # 参数来源标识
    )
