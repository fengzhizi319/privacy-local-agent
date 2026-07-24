"""数据分类原语公共 API 入口 / Data Classification Primitive Public API Entry.

中文说明：
提供 ClassificationAPI 以及向后兼容的公共符号导出。
支持字段、记录、表多级分类，内置默认规则引擎，可插拔 Small-NER 与 LLM，
并提供 dict/JSON/DataFrame/Arrow/SQL 结果集等多种格式适配器。

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

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Protocol, cast

from ...observability.logging_config import get_logger
from ...observability.metrics import (
    CLASSIFICATION_DURATION,
    CLASSIFICATION_LLM_TOTAL,
    CLASSIFICATION_NER_TOTAL,
    CLASSIFICATION_TOTAL,
)
from ..profile import ParameterResolver, get_resolver
from .classification_composite import CompositeRuleEngine, apply_composite_tags
from .classification_models import (
    AuditInfo,
    ClassificationJob,
    ClassificationParams,
    ClassificationResult,
    CompositeRule,
    EngineLayer,
    FieldClassificationResult,
    LlmClassifier,
    NoOpLlmClassifier,
    NoOpSmallNerEngine,
    RecordClassificationResult,
    RuleEngineABC,
    SecurityTag,
    SensitivityLevel,
    ShadowDiff,
    SmallNerEngine,
    TableClassificationResult,
    max_level,
    parse_level,
)
from .classification_rule_engine import DefaultRuleEngine, RuleEngine, _unique_tags
from .classification_utils import classify_secretflow, get_template_params

# Module-level structured logger for classification events
logger = get_logger(__name__)

_PRIMITIVE_VERSION = "1.0.0"
_RULE_ENGINE_VERSION = "1.0.0"

# base64 data URI 前缀正则：匹配 data:image/png;base64, 等格式
_DATA_URI_RE = re.compile(r"^data:image/[a-zA-Z]+;base64,", re.ASCII)


def _summarize_field_value(value: Any) -> str | None:
    """将字段值转换为适合返回给前端的摘要字符串。

    对于图片类 base64 数据（data URI 或超长纯 base64），不返回原始内容，
    仅返回形如 ``[image data, ~123 KB]`` 的摘要，避免响应体过大、
    前端显示冗长。

    Args:
        value: 原始字段值。

    Returns:
        摘要字符串，或 None（值为 None 时）。
    """
    if value is None:
        return None
    text = str(value)
    # 1. data:image/xxx;base64,... 格式
    m = _DATA_URI_RE.match(text)
    if m:
        raw_len = len(text) - m.end()
        kb = max(1, round(raw_len * 3 / 4 / 1024))  # base64 → 近似原始字节
        return f"[image data, ~{kb} KB]"
    # 2. 纯 base64 且长度超过 512 字符：极可能是图片或其他二进制数据
    if len(text) > 512 and not text.startswith("http") and not text.startswith("/"):
        # 简单检测是否为合法 base64 字符集
        sample = text[:128]
        if all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=\n\r" for c in sample):
            kb = max(1, round(len(text) * 3 / 4 / 1024))
            return f"[binary data, ~{kb} KB]"
    return text

__all__ = [
    "ClassificationAPI",
    "DefaultRuleEngine",
    "LlmClassifier",
    "NoOpLlmClassifier",
    "NoOpSmallNerEngine",
    "RuleEngine",
    "RuleEngineABC",
    "SmallNerEngine",
]


class _SupportsEvaluateSeries(Protocol):
    """Protocol for rule engines that provide vectorized ``evaluate_series``.

    仅用于向量化表分类路径中的类型断言，避免在顶层引入 pandas 依赖。
    Used only for type assertions in the vectorized table classification path
    to avoid importing pandas at the top level.
    """

    def evaluate_series(
        self, field_name: str, series: Any, params: ClassificationParams
    ) -> list[list[SecurityTag]]:
        ...


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
    params: dict[str, Any] = {}
    source = "default"

    # Step 1: Resolve base parameters from YAML profile
    if resolver is not None:
        profile_params = resolver.resolve("classification", request_params=None) or {}
        if profile_params:
            params.update(profile_params)
            source = "profile"

    # Step 2: Activate compliance template defaults (only fill unset keys)
    template_name = None
    if request_params and request_params.get("template"):
        template_name = request_params.get("template")
    elif params.get("template"):
        template_name = params.get("template")
    if template_name:
        template_defaults = get_template_params(template_name)
        # Template defaults only apply when the key is not already set
        for key, value in template_defaults.items():
            if key not in params:
                params[key] = value

    # Step 3: Apply request-level parameter overrides (highest priority)
    if request_params:
        params.update(request_params)
        source = "request"

    # Step 4: Validate and construct ClassificationParams via Pydantic
    classification_params = ClassificationParams.model_validate(params)

    # Manual override takes precedence over all other sources
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
        # Step 1: Initialize parameter resolver
        self.resolver = resolver or get_resolver(profile_path)

        # Step 2: Initialize Layer-1 rule engine
        if rule_engine is None:
            if use_vectorized:
                try:
                    from .classification_vectorized import VectorizedRuleEngine

                    self.rule_engine = VectorizedRuleEngine()
                except ImportError:
                    logger.warning(
                        "vectorized_engine_fallback",
                        extra={
                            "reason": "pandas not installed",
                            "fallback": "DefaultRuleEngine",
                        },
                    )
                    self.rule_engine = DefaultRuleEngine()
            else:
                self.rule_engine = DefaultRuleEngine()
        else:
            self.rule_engine = rule_engine

        # Step 3: Initialize composite engine, async manager, review store
        self.composite_engine = composite_engine or CompositeRuleEngine()
        # Lazy import to avoid circular dependencies
        if async_manager is None:
            from .classification_async import AsyncClassificationManager
            async_manager = AsyncClassificationManager()
        self.async_manager = async_manager
        if review_store is None:
            from .classification_review import ReviewStore
            review_store = ReviewStore()
        self.review_store = review_store

        # Step 4: Auto-select Layer-2 Small-NER engine (ONNX > ModelScope > NoOp)
        if small_ner is None:
            current_dir = os.path.dirname(os.path.abspath(__file__))
            project_root = os.path.dirname(os.path.dirname(current_dir))
            onnx_path = os.path.join(project_root, ".models", "raner_cmeee.onnx")

            if os.path.exists(onnx_path):
                try:
                    from .classification_ner import ONNXSmallNerEngine
                    self.small_ner = ONNXSmallNerEngine()
                except ImportError:
                    self.small_ner = NoOpSmallNerEngine()
            else:
                try:
                    from .classification_ner import ModelScopeSmallNerEngine
                    self.small_ner = ModelScopeSmallNerEngine()
                except ImportError:
                    self.small_ner = NoOpSmallNerEngine()
        else:
            self.small_ner = small_ner

        # Step 5: Auto-select Layer-3 LLM classifier (Qwen2-VL > NoOp)
        if llm is None:
            try:
                from .classification_llm import Qwen2VLClassifier

                candidate = Qwen2VLClassifier()
                # Degrade to NoOp if local model directory does not exist
                if not os.path.isdir(candidate.model_path):
                    self.llm = NoOpLlmClassifier()
                else:
                    self.llm = candidate
            except ImportError:
                self.llm = NoOpLlmClassifier()
        else:
            self.llm = llm

        # Log initialization summary for observability
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
        start_time = time.monotonic()
        result = self._classify_field_internal(field_name, value, params)
        duration = time.monotonic() - start_time

        # Record metrics for observability
        CLASSIFICATION_TOTAL.labels(
            final_level=result.final_level.value,
            layer=result.engine_layer.value,
        ).inc()
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
        # Step 1: Resolve classification parameters
        cp, _source = _resolve_classification_params(self.resolver, params)

        tags: list[SecurityTag] = list(initial_tags) if initial_tags is not None else []
        engine_layer = EngineLayer.L1_RULE
        reasoning = ""

        # Step 2: Layer-1 Rule Engine evaluation
        if initial_tags is None and cp.enable_rule_engine:
            tags = self.rule_engine.evaluate(field_name, value, cp)

        final_level = max_level(*(t.level for t in tags)) if tags else cp.default_level
        confidence = 1.0 if tags else 0.0

        if tags:
            rule_ids = [t.rule_id for t in tags if t.rule_id]
            reasoning = "命中规则: " + ", ".join(rule_ids)

        # Step 3: Layer-2 Small-NER entity extraction
        if cp.enable_small_ner and (not tags or final_level.value <= SensitivityLevel.L3.value):
            ner_tags = self._run_small_ner(field_name, value)
            if ner_tags:
                tags.extend(ner_tags)
                final_level = max_level(final_level, *(t.level for t in ner_tags))
                confidence = max(confidence, max(t.confidence for t in ner_tags))
                engine_layer = EngineLayer.L2_SMALL_NER

        # Step 4: Layer-3 LLM classification fallback
        if cp.enable_llm or confidence < cp.llm_confidence_threshold:
            llm_result = self.llm.classify(str(value), final_level, confidence)
            if llm_result:
                final_level = parse_level(llm_result.get("final_level", final_level))
                confidence = float(llm_result.get("confidence", confidence))
                reasoning = str(llm_result.get("reasoning", reasoning))
                engine_layer = EngineLayer.L3_LLM
                CLASSIFICATION_LLM_TOTAL.labels(status="hit").inc()
            else:
                CLASSIFICATION_LLM_TOTAL.labels(status="miss").inc()

        # Step 5: Apply manual override if configured
        overridden_level = cp.apply_manual_override(field_name, final_level)
        if overridden_level != final_level:
            manual_tag = SecurityTag(
                level=overridden_level,
                category="MANUAL_OVERRIDE",
                confidence=1.0,
                source_engine="MANUAL",
                rule_id="MANUAL_001",
                needs_human_review=False,
            )
            tags = _unique_tags([manual_tag, *tags])
            final_level = overridden_level
            engine_layer = EngineLayer.L1_RULE
            reasoning = f"人工覆盖为 {final_level.value}"
            logger.info(
                "classification_manual_override_applied",
                extra={
                    "field_name": field_name,
                    "overridden_level": final_level.value,
                },
            )

        return FieldClassificationResult(
            field_name=field_name,
            field_value=_summarize_field_value(value) if cp.return_field_values else None,
            tags=tags,
            final_level=final_level,
            confidence=confidence,
            engine_layer=engine_layer,
            needs_human_review=any(t.needs_human_review for t in tags),
            reasoning=reasoning,
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
        entities = self.small_ner.extract(str(value) if value is not None else "")
        tags: list[SecurityTag] = []

        # Record NER invocation metrics
        if entities:
            CLASSIFICATION_NER_TOTAL.labels(status="hit").inc()
        else:
            CLASSIFICATION_NER_TOTAL.labels(status="miss").inc()

        # Sensitive disease keywords for L4 escalation determination
        sensitive_keywords = ["hiv", "精神分裂", "艾滋", "梅毒", "肿瘤", "癌症", "白血病", "抑郁症"]

        for ent in entities:
            label = ent.get("label", "")
            text = str(ent.get("text", "")).lower()
            conf = float(ent.get("confidence", 0.0))

            # Genomic hint: highest sensitivity L5, requires human review
            if label == "GENOMIC_HINT":
                tags.append(
                    self._make_tag(
                        SensitivityLevel.L5,
                        "GENOMIC_HINT",
                        conf,
                        "NER_GENE_001",
                        needs_human_review=True,
                    )
                )
            # Medical disease: check sensitive keywords for L4 vs L3 determination
            elif label == "MEDICAL_DISEASE":
                if any(kw in text for kw in sensitive_keywords):
                    tags.append(
                        self._make_tag(
                            SensitivityLevel.L4,
                            "MEDICAL_SENSITIVE_DISEASE",
                            conf,
                            "NER_DIS_SENSITIVE",
                        )
                    )
                else:
                    tags.append(
                        self._make_tag(
                            SensitivityLevel.L3,
                            "MEDICAL_DISEASE",
                            conf,
                            "NER_DIS_NORMAL",
                        )
                    )
            # Medication: standard L3 sensitivity
            elif label == "MEDICATION":
                tags.append(
                    self._make_tag(
                        SensitivityLevel.L3,
                        "MEDICATION",
                        conf,
                        "NER_DRU_001",
                    )
                )
            # Surgery/procedure: standard L3 sensitivity
            elif label == "SURGERY":
                tags.append(
                    self._make_tag(
                        SensitivityLevel.L3,
                        "SURGERY",
                        conf,
                        "NER_PRO_001",
                    )
                )
            # Body part: standard L3 sensitivity
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
        start_time = time.monotonic()
        field_results: dict[str, FieldClassificationResult] = {}
        aggregated_tags: list[SecurityTag] = []

        # Step 1: Classify each field in the record
        for field_name, value in record.items():
            field_result = self.classify_field(field_name, value, params)
            field_results[field_name] = field_result
            aggregated_tags.extend(field_result.tags)

        # Step 2: Aggregate tags and compute final level/confidence
        aggregated_tags = _unique_tags(aggregated_tags)
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

        record_result = RecordClassificationResult(
            record_index=record_index,
            field_results=field_results,
            aggregated_tags=aggregated_tags,
            final_level=final_level,
            confidence=confidence,
            needs_human_review=any(fr.needs_human_review for fr in field_results.values()),
        )

        # Step 3: Apply composite/context-aware rule post-processing
        cp, _ = _resolve_classification_params(self.resolver, params)
        custom_rules = None
        if cp.composite_rules:
            custom_rules = [CompositeRule.model_validate(r) for r in cp.composite_rules]
        engine = (
            CompositeRuleEngine(custom_rules)
            if custom_rules
            else self.composite_engine
        )
        composite_tags = engine.evaluate(record, field_results)
        record_result = cast(
            "RecordClassificationResult",
            apply_composite_tags(record_result, composite_tags),
        )

        # Record metrics for observability
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
        start_time = time.monotonic()
        cp, _ = _resolve_classification_params(self.resolver, params)

        # Use vectorized path if rule engine supports evaluate_series
        if rows and hasattr(self.rule_engine, "evaluate_series"):
            result = self._classify_table_vectorized(schema, rows, params, cp)
            CLASSIFICATION_DURATION.labels(operation="table").observe(time.monotonic() - start_time)
            return result

        record_results: list[RecordClassificationResult] = []
        aggregated_tags: list[SecurityTag] = []
        review_entries: list[Any] = []

        # Step 2: Classify each record and aggregate results
        for idx, row in enumerate(rows):
            # Keep only columns present in schema
            filtered = {col: row.get(col) for col in schema if col in row}
            record_result = self.classify_record(filtered, params, record_index=idx)
            record_results.append(record_result)
            aggregated_tags.extend(record_result.aggregated_tags)
            # Collect review entries if enabled
            if cp.enable_review:
                review_entries.extend(
                    self.review_store.add_from_record(record_result, original_record=row)
                )

        # Aggregate final level and confidence across all records
        aggregated_tags = _unique_tags(aggregated_tags)
        final_level = (
            max_level(*(rr.final_level for rr in record_results))
            if record_results
            else SensitivityLevel.L1
        )
        confidence = (
            max(rr.confidence for rr in record_results) if record_results else 0.0
        )

        # Step 3: Shadow mode comparison (optional)
        shadow_diff: list[ShadowDiff] = []
        if cp.shadow_mode and cp.shadow_version:
            shadow_keys = {
                "shadow_mode", "shadowMode", "shadow_version", "shadowVersion",
                "rule_set_version", "ruleSetVersion",
            }
            shadow_params = {k: v for k, v in (params or {}).items() if k not in shadow_keys}
            shadow_params["rule_set_version"] = cp.shadow_version
            shadow_params["shadow_mode"] = False
            shadow_result = self.classify_table(schema, rows, shadow_params)
            shadow_diff = self._compute_shadow_diff(record_results, shadow_result.record_results)

        # Record metrics for observability
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
        """
        import pandas as pd

        df = pd.DataFrame(rows, columns=schema)
        record_results: list[RecordClassificationResult] = []
        aggregated_tags: list[SecurityTag] = []
        review_entries: list[Any] = []

        # 复合规则只解析一次，避免每行循环重复构造引擎
        custom_rules = None
        if cp.composite_rules:
            custom_rules = [CompositeRule.model_validate(r) for r in cp.composite_rules]
        engine = CompositeRuleEngine(custom_rules) if custom_rules else self.composite_engine

        # 批量计算每列的 Layer-1 标签
        rule_engine = cast("_SupportsEvaluateSeries", self.rule_engine)
        column_tags: dict[str, list[list[SecurityTag]]] = {}
        if cp.enable_rule_engine:
            for field_name in schema:
                column_tags[field_name] = rule_engine.evaluate_series(
                    field_name, df[field_name], cp
                )
        else:
            empty: list[list[SecurityTag]] = [[] for _ in range(len(rows))]
            for field_name in schema:
                column_tags[field_name] = empty

        for idx, row in enumerate(rows):
            field_results: dict[str, FieldClassificationResult] = {}
            for field_name in schema:
                value = row.get(field_name)
                initial_tags = column_tags[field_name][idx]
                field_result = self._classify_field_internal(
                    field_name, value, params, initial_tags=initial_tags
                )
                field_results[field_name] = field_result

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

            # 复合/上下文敏感规则后处理（引擎已在循环外构造）
            composite_tags = engine.evaluate(row, field_results)
            record_result = apply_composite_tags(record_result, composite_tags)

            record_results.append(record_result)
            aggregated_tags.extend(record_result.aggregated_tags)
            if cp.enable_review:
                review_entries.extend(
                    self.review_store.add_from_record(record_result, original_record=row)
                )

        aggregated_tags = _unique_tags(aggregated_tags)
        final_level = (
            max_level(*(rr.final_level for rr in record_results))
            if record_results
            else SensitivityLevel.L1
        )
        confidence = (
            max(rr.confidence for rr in record_results) if record_results else 0.0
        )

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
        """计算当前结果与影子结果的差异。"""
        diffs: list[ShadowDiff] = []
        for cur, shw in zip(current_results, shadow_results):
            for field_name in set(cur.field_results.keys()) | set(shw.field_results.keys()):
                cur_field = cur.field_results.get(field_name)
                shw_field = shw.field_results.get(field_name)
                if not cur_field or not shw_field:
                    continue
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
        data = json.loads(json_input) if isinstance(json_input, str) else json_input

        cp, source = _resolve_classification_params(self.resolver, params)

        if isinstance(data, dict):
            record_result = self.classify_record(data, params)
            return ClassificationResult(
                record_result=record_result,
                audit_info=_build_audit_info(cp, source),
            )

        if isinstance(data, list) and data:
            schema = sorted({col for row in data for col in row})
            table_result = self.classify_table(schema, data, params)
            return ClassificationResult(
                table_result=table_result,
                audit_info=_build_audit_info(cp, source),
            )

        if isinstance(data, list) and not data:
            return ClassificationResult(
                table_result=TableClassificationResult(schema=[], final_level=SensitivityLevel.L1),
                audit_info=_build_audit_info(cp, source),
            )

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
        import pandas as pd

        if not isinstance(df, pd.DataFrame):
            raise TypeError("classify_dataframe expects a pandas.DataFrame")
        schema = list(df.columns)
        rows = df.to_dict(orient="records")
        table_result = self.classify_table(schema, rows, params)
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
        import pyarrow as pa

        if not isinstance(table, pa.Table):
            raise TypeError("classify_arrow expects a pyarrow.Table")
        schema = list(table.column_names)
        rows = table.to_pandas().to_dict(orient="records")
        table_result = self.classify_table(schema, rows, params)
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
        if not result_set:
            cp, source = _resolve_classification_params(self.resolver, params)
            return ClassificationResult(
                table_result=TableClassificationResult(
                    schema=[], final_level=SensitivityLevel.L1
                ),
                audit_info=_build_audit_info(cp, source),
            )
        schema = sorted({col for row in result_set for col in row})
        table_result = self.classify_table(schema, result_set, params)
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
        table_result = classify_secretflow(self, sf_data, params=params, party=party)
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
        return self.async_manager.submit(self.classify_table, schema, rows, params)

    def get_job_result(self, job_id: str) -> ClassificationJob:
        """查询异步分类任务结果 / Get Async Classification Job Result.

        Args:
            job_id: 任务 ID / Job ID.

        Returns:
            ClassificationJob / Classification job status and result.
        """
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
        if isinstance(self.llm, NoOpLlmClassifier):
            return True
        try:
            from .classification_llm import Qwen2VLClassifier
        except ImportError:
            return True
        if isinstance(self.llm, Qwen2VLClassifier):
            # 仅当大模型成功初始化且无错误时，才视为就绪
            return self.llm.is_ready
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
        if isinstance(self.llm, NoOpLlmClassifier):
            return True
        try:
            from .classification_llm import Qwen2VLClassifier
        except ImportError:
            return True
        if not isinstance(self.llm, Qwen2VLClassifier):
            return True

        import asyncio

        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(None, self.llm.warmup)
        except Exception as exc:
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
        return self.review_store.confirm(review_id, corrected_level, reviewer, comment)

    def export_reviews(self, format: str = "jsonl", mask_input: bool = False) -> str:  # noqa: A002
        """导出复核样本 / Export Review Samples.

        Args:
            format: `jsonl` 或 `csv` / Export format (`jsonl` or `csv`).
            mask_input: 是否对 input 掩码 / Whether to mask input values.

        Returns:
            导出内容字符串 / Exported content string.
        """
        return self.review_store.export(format=format, mask_input=mask_input)


def _build_audit_info(params: ClassificationParams, parameter_source: str) -> AuditInfo:
    """构建审计信息 / Build Audit Info.

    中文说明：记录分类请求的执行元数据，包括版本、时间戳、参数来源等。
    English Description: Records execution metadata for classification requests.

    Args:
        params: 分类参数 / Classification parameters.
        parameter_source: 参数来源 / Parameter source indicator.

    Returns:
        AuditInfo / Audit information.
    """
    return AuditInfo(
        version=_PRIMITIVE_VERSION,
        profile_version=params.version,
        timestamp=datetime.now(timezone.utc).isoformat(),
        rule_engine_version=_RULE_ENGINE_VERSION,
        rule_set_version=params.rule_set_version,
        parameter_source=parameter_source,
    )
