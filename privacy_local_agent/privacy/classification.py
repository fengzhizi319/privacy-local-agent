"""数据分类原语核心实现。

提供基于规则的多层分类引擎（RuleEngine）、可插拔 Small-NER 与 LLM 分类器、
ClassificationAPI（支持 dict/JSON/DataFrame/Arrow/SQL 等多种输入格式）
以及参数治理（default → profile → request → manual_override）。

Core implementation of the data classification primitive. Provides a rule-based
multi-layer engine, pluggable small-NER and LLM classifiers, ClassificationAPI
with multi-format adapters, and parameter governance.
"""

import json
import re
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .classification_models import (
    AuditInfo,
    ClassificationJob,
    ClassificationParams,
    ClassificationResult,
    CompositeRule,
    EngineLayer,
    FieldClassificationResult,
    RecordClassificationResult,
    SecurityTag,
    SensitivityLevel,
    ShadowDiff,
    TableClassificationResult,
    max_level,
    parse_level,
)
from .classification_composite import CompositeRuleEngine, apply_composite_tags
from .classification_zero_knowledge import redact
from .profile import ParameterResolver, get_resolver

# ---------------------------------------------------------------------------
# 默认值与常量 / Defaults and constants
# ---------------------------------------------------------------------------

_PRIMITIVE_VERSION = "1.0.0"
_RULE_ENGINE_VERSION = "1.0.0"

_ID_CARD_WEIGHTS = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
_ID_CARD_CHARS = ["1", "0", "X", "9", "8", "7", "6", "5", "4", "3", "2"]

_SH_MEDICAL_WEIGHTS = [7, 9, 10, 5, 8, 4, 2, 1]


# ---------------------------------------------------------------------------
# 工具函数 / Utility functions
# ---------------------------------------------------------------------------

def _normalize_field_name(name: str) -> str:
    """规范化字段名：转小写并移除下划线与空格。

    Normalizes a field name for pattern matching (case-insensitive, strip
    underscores and spaces).
    """
    return str(name).lower().replace("_", "").replace(" ", "")


def _normalize_icd10(code: str) -> Optional[Tuple[str, int]]:
    """解析并归一化 ICD-10 编码。

    Returns a tuple of (letter, two-digit number) or None if invalid.
    """
    match = re.match(r"^([A-Z])(\d{2})(?:\.\d{0,2})?$", code.upper())
    if not match:
        return None
    return match.group(1), int(match.group(2))


def _in_icd10_interval(code: Tuple[str, int], start: str, end: str) -> bool:
    """判断 ICD-10 编码是否落在闭区间 [start, end]。

    Args:
        code: 归一化后的 (letter, number) 元组。
        start: 区间起点字符串，如 "B20"。
        end: 区间终点字符串，如 "B24"。

    Returns:
        是否在区间内。
    """
    start_norm = _normalize_icd10(start)
    end_norm = _normalize_icd10(end)
    if not start_norm or not end_norm:
        return False
    return start_norm <= code <= end_norm


def _id_card_checksum(value: str) -> bool:
    """校验中国大陆 18 位身份证号校验码。

    Validates the checksum of an 18-digit Chinese ID card number.
    """
    if len(value) != 18:
        return False
    if not re.match(r"^[1-9]\d{5}(18|19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\d{3}[\dXx]$", value):
        return False
    try:
        total = sum(int(value[i]) * _ID_CARD_WEIGHTS[i] for i in range(17))
        expected = _ID_CARD_CHARS[total % 11]
        return value[17].upper() == expected
    except (ValueError, IndexError):
        return False


def _shanghai_medical_card_checksum(value: str) -> bool:
    """校验上海医保卡号 9 位数字校验码。

    Validates the checksum of a 9-digit Shanghai medical card number.
    """
    if not re.match(r"^\d{9}$", value):
        return False
    digits = [int(c) for c in value]
    total = sum(digits[i] * _SH_MEDICAL_WEIGHTS[i] for i in range(8))
    expected = (10 - total % 10) % 10
    return digits[8] == expected


def _unique_tags(tags: List[SecurityTag]) -> List[SecurityTag]:
    """去重安全标签，以 level+category 为键保留顺序。"""
    seen: set = set()
    result: List[SecurityTag] = []
    for tag in tags:
        key = (tag.level.value, tag.category)
        if key in seen:
            continue
        seen.add(key)
        result.append(tag)
    return result


# ---------------------------------------------------------------------------
# 规则引擎 / Rule engine
# ---------------------------------------------------------------------------

class RuleEngine(ABC):
    """规则引擎抽象接口。"""

    @abstractmethod
    def evaluate(
        self, field_name: str, value: Any, params: ClassificationParams
    ) -> List[SecurityTag]:
        """评估单个字段，返回命中的安全标签列表。"""


class DefaultRuleEngine(RuleEngine):
    """默认规则引擎，实现规范中全部 Layer-1 规则。"""

    def evaluate(
        self, field_name: str, value: Any, params: ClassificationParams
    ) -> List[SecurityTag]:
        """按字段名与字段值评估规则并收集标签。

        Args:
            field_name: 字段名。
            value: 字段值，会被转换为字符串处理。
            params: 分类参数，包含 ICD-10 区间、基因关键字等配置。

        Returns:
            命中的 SecurityTag 列表。
        """
        tags: List[SecurityTag] = []
        norm_name = _normalize_field_name(field_name)
        str_value = str(value) if value is not None else ""

        # 4.1 字段名规则 / Field-name based rules
        norm_value = _normalize_field_name(str_value)

        if any(kw in norm_name for kw in ("brca1", "brca2", "tp53")):
            tags.append(
                SecurityTag(
                    level=SensitivityLevel.L5,
                    category="GENOMIC_BRCA_TP53",
                    source_engine="RULE",
                    rule_id="RULE_ID_G_001",
                )
            )

        if re.search(r"rs\d+", norm_name) or re.search(r"rs\d+", norm_value) or any(
            kw in norm_name for kw in ("snp", "cnv", "genome", "genomic")
        ):
            tags.append(
                SecurityTag(
                    level=SensitivityLevel.L5,
                    category="GENOMIC_VARIANT",
                    source_engine="RULE",
                    rule_id="RULE_ID_G_002",
                )
            )

        if any(kw in norm_name for kw in ("gene", "mutation", "variant")):
            tags.append(
                SecurityTag(
                    level=SensitivityLevel.L5,
                    category="GENOMIC_HINT",
                    source_engine="RULE",
                    rule_id="RULE_ID_G_003",
                )
            )

        if any(kw in norm_name for kw in ("bam", "vcf", "fastq")):
            tags.append(
                SecurityTag(
                    level=SensitivityLevel.L5,
                    category="GENOMIC_FILE",
                    source_engine="RULE",
                    rule_id="RULE_ID_G_004",
                )
            )

        # 4.2 值规则 / Value-based rules
        if _id_card_checksum(str_value):
            tags.append(
                SecurityTag(
                    level=SensitivityLevel.L3,
                    category="PII_ID_CARD",
                    source_engine="RULE",
                    rule_id="RULE_ID_001",
                )
            )

        if re.match(r"^1[3-9]\d{9}$", str_value):
            tags.append(
                SecurityTag(
                    level=SensitivityLevel.L3,
                    category="PII_MOBILE",
                    source_engine="RULE",
                    rule_id="RULE_ID_002",
                )
            )

        if _shanghai_medical_card_checksum(str_value):
            tags.append(
                SecurityTag(
                    level=SensitivityLevel.L3,
                    category="PII_MEDICAL_CARD",
                    source_engine="RULE",
                    rule_id="RULE_ID_003",
                )
            )

        icd = _normalize_icd10(str_value)
        if icd:
            level = SensitivityLevel.L3
            category = "MEDICAL_ICD10_GENERAL"
            for interval in params.icd10_l4_intervals:
                start = interval.get("start", "")
                end = interval.get("end", "")
                if _in_icd10_interval(icd, start, end):
                    level = SensitivityLevel.L4
                    if start.upper().startswith("B"):
                        category = "MEDICAL_ICD10_HIV"
                    elif start.upper().startswith("F"):
                        category = "MEDICAL_ICD10_PSYCHIATRIC"
                    elif start.upper().startswith("C"):
                        category = "MEDICAL_ICD10_CANCER"
                    break
            tags.append(
                SecurityTag(
                    level=level,
                    category=category,
                    source_engine="RULE",
                    rule_id="RULE_ID_004",
                )
            )

        # BAM/VCF/FASTQ 文件头
        if str_value.startswith("BAM\x01") or str_value.startswith("@SQ"):
            tags.append(
                SecurityTag(
                    level=SensitivityLevel.L5,
                    category="GENOMIC_BAM",
                    source_engine="RULE",
                    rule_id="RULE_ID_G_010",
                )
            )

        if str_value.startswith("##fileformat=VCF"):
            tags.append(
                SecurityTag(
                    level=SensitivityLevel.L5,
                    category="GENOMIC_VCF",
                    source_engine="RULE",
                    rule_id="RULE_ID_G_011",
                )
            )

        lines = str_value.splitlines()
        if str_value.startswith("@") and (
            any(token in str_value for token in ("SRR", "ERR", "DRR"))
            or (len(lines) >= 3 and lines[2].strip() == "+")
        ):
            tags.append(
                SecurityTag(
                    level=SensitivityLevel.L5,
                    category="GENOMIC_FASTQ",
                    source_engine="RULE",
                    rule_id="RULE_ID_G_012",
                )
            )

        if re.search(r"[ATCGNatcgn]{50,}", str_value):
            tags.append(
                SecurityTag(
                    level=SensitivityLevel.L5,
                    category="GENOMIC_SEQUENCE",
                    source_engine="RULE",
                    rule_id="RULE_ID_G_013",
                )
            )

        # 白名单与运营统计字段（按字段名匹配，与测试用例一致）
        norm_public_whitelist = [_normalize_field_name(kw) for kw in params.public_field_whitelist]
        if any(kw in norm_name for kw in norm_public_whitelist):
            tags.append(
                SecurityTag(
                    level=SensitivityLevel.L1,
                    category="PUBLIC_REPORT",
                    source_engine="RULE",
                    rule_id="RULE_ID_L1_001",
                )
            )

        norm_operational = [_normalize_field_name(kw) for kw in params.operational_field_patterns]
        if any(kw in norm_name for kw in norm_operational):
            tags.append(
                SecurityTag(
                    level=SensitivityLevel.L2,
                    category="OPERATIONAL_STAT",
                    source_engine="RULE",
                    rule_id="RULE_ID_L2_001",
                )
            )

        return _unique_tags(tags)


# ---------------------------------------------------------------------------
# Small-NER 引擎 / Small-NER engine
# ---------------------------------------------------------------------------

class SmallNerEngine(ABC):
    """Small-NER 引擎抽象接口（Layer 2）。"""

    @abstractmethod
    def extract(self, text: str) -> List[Dict[str, Any]]:
        """从文本中提取实体，返回包含 label、confidence 等字段的字典列表。"""


class NoOpSmallNerEngine(SmallNerEngine):
    """默认空实现，不返回任何实体。"""

    def extract(self, text: str) -> List[Dict[str, Any]]:
        return []


# ---------------------------------------------------------------------------
# LLM 分类器 / LLM classifier
# ---------------------------------------------------------------------------

class LlmClassifier(ABC):
    """LLM 分类器抽象接口（Layer 3）。"""

    @abstractmethod
    def classify(
        self, text: str, upstream_level: SensitivityLevel, upstream_confidence: float
    ) -> Optional[Dict[str, Any]]:
        """基于上游结果对文本进行分类，返回结构化输出或 None。"""


class NoOpLlmClassifier(LlmClassifier):
    """默认空实现：当上游置信度低于阈值时给出保守回退结果。"""

    def classify(
        self, text: str, upstream_level: SensitivityLevel, upstream_confidence: float
    ) -> Optional[Dict[str, Any]]:
        if upstream_confidence < 0.6:
            return {
                "final_level": upstream_level,
                "sub_category": "LLM_FALLBACK",
                "confidence": upstream_confidence,
                "reasoning": "LLM 未启用，按上游最高等级降级/保守处理",
                "suggested_action": "review",
                "needs_human_review": True,
            }
        return None


# ---------------------------------------------------------------------------
# 参数治理 / Parameter governance
# ---------------------------------------------------------------------------

def _resolve_classification_params(
    resolver: Optional[ParameterResolver] = None,
    request_params: Optional[Dict[str, Any]] = None,
) -> Tuple[ClassificationParams, str]:
    """合并默认、合规模板、YAML profile、请求参数得到最终分类参数。

    Returns:
        (ClassificationParams, parameter_source) 元组。
    """
    params: Dict[str, Any] = {}
    source = "default"

    # 1. YAML profile
    if resolver is not None:
        profile_params = resolver.resolve("classification", request_params=None) or {}
        if profile_params:
            params.update(profile_params)
            source = "profile"

    # 2. 合规模板：根据 request/template 或 profile 中的 template 激活
    from .classification_templates import get_template_params
    template_name = None
    if request_params and request_params.get("template"):
        template_name = request_params.get("template")
    elif params.get("template"):
        template_name = params.get("template")
    if template_name:
        template_defaults = get_template_params(template_name)
        # 模板默认值只在未设置时使用
        for key, value in template_defaults.items():
            if key not in params:
                params[key] = value

    # 3. 请求参数覆盖
    if request_params:
        params.update(request_params)
        source = "request"

    classification_params = ClassificationParams.model_validate(params)

    if classification_params.manual_override:
        source = "manual"

    return classification_params, source


# ---------------------------------------------------------------------------
# 分类 API / Classification API
# ---------------------------------------------------------------------------

class ClassificationAPI:
    """数据分类统一 API。

    支持字段、记录、表多级分类，内置默认规则引擎，可插拔 Small-NER 与 LLM，
    并提供 dict/JSON/DataFrame/Arrow/SQL 结果集等多种格式适配器。

    Args:
        profile_path: YAML 配置文件路径，用于覆盖默认参数。
        rule_engine: 规则引擎实例，默认 DefaultRuleEngine。
        small_ner: Small-NER 引擎实例，默认 NoOpSmallNerEngine。
        llm: LLM 分类器实例，默认 NoOpLlmClassifier。
    """

    def __init__(
        self,
        profile_path: Optional[str] = None,
        rule_engine: Optional[RuleEngine] = None,
        small_ner: Optional[SmallNerEngine] = None,
        llm: Optional[LlmClassifier] = None,
        resolver: Optional[ParameterResolver] = None,
        composite_engine: Optional[CompositeRuleEngine] = None,
        async_manager: Optional[Any] = None,
        review_store: Optional[Any] = None,
    ):
        self.resolver = resolver or get_resolver(profile_path)
        self.rule_engine = rule_engine or DefaultRuleEngine()
        self.composite_engine = composite_engine or CompositeRuleEngine()
        # 延迟导入可选模块，避免循环依赖
        if async_manager is None:
            from .classification_async import AsyncClassificationManager
            async_manager = AsyncClassificationManager()
        self.async_manager = async_manager
        if review_store is None:
            from .classification_review import ReviewStore
            review_store = ReviewStore()
        self.review_store = review_store
        if small_ner is None:
            # 自动选择最合适的本地 NER 引擎 (优先选择高性能 ONNX，其次选择 ModelScope 官方管道)
            import os
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
        if llm is None:
            try:
                from .classification_llm import Qwen2VLClassifier
                self.llm = Qwen2VLClassifier()
            except ImportError:
                self.llm = NoOpLlmClassifier()
        else:
            self.llm = llm

    # ------------------------------------------------------------------
    # 核心方法 / Core methods
    # ------------------------------------------------------------------

    def classify_field(
        self,
        field_name: str,
        value: Any,
        params: Optional[Dict[str, Any]] = None,
    ) -> FieldClassificationResult:
        """对单个字段进行分类。

        Args:
            field_name: 字段名。
            value: 字段值。
            params: 请求级参数，可覆盖默认与 profile 配置。

        Returns:
            FieldClassificationResult。
        """
        cp, source = _resolve_classification_params(self.resolver, params)

        tags: List[SecurityTag] = []
        engine_layer = EngineLayer.L1_RULE
        reasoning = ""

        if cp.enable_rule_engine:
            tags = self.rule_engine.evaluate(field_name, value, cp)

        final_level = max_level(*(t.level for t in tags)) if tags else cp.default_level
        confidence = 1.0 if tags else 0.0

        if tags:
            rule_ids = [t.rule_id for t in tags if t.rule_id]
            reasoning = "命中规则: " + ", ".join(rule_ids)

        # Layer 2: Small-NER
        if cp.enable_small_ner and (not tags or final_level.value <= SensitivityLevel.L3.value):
            ner_tags = self._run_small_ner(field_name, value)
            if ner_tags:
                tags.extend(ner_tags)
                final_level = max_level(final_level, *(t.level for t in ner_tags))
                confidence = max(confidence, max(t.confidence for t in ner_tags))
                engine_layer = EngineLayer.L2_SMALL_NER

        # Layer 3: LLM fallback
        if cp.enable_llm or (not cp.enable_llm and confidence < 0.6):
            llm_result = self.llm.classify(str(value), final_level, confidence)
            if llm_result:
                final_level = parse_level(llm_result.get("final_level", final_level))
                confidence = float(llm_result.get("confidence", confidence))
                reasoning = str(llm_result.get("reasoning", reasoning))
                engine_layer = EngineLayer.L3_LLM

        # 人工覆盖 / Manual override
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
            tags = _unique_tags([manual_tag] + tags)
            final_level = overridden_level
            engine_layer = EngineLayer.L1_RULE
            reasoning = f"人工覆盖为 {final_level.value}"
            source = "manual"

        return FieldClassificationResult(
            field_name=field_name,
            field_value=str(value) if value is not None and cp.return_field_values else None,
            tags=tags,
            final_level=final_level,
            confidence=confidence,
            engine_layer=engine_layer,
            needs_human_review=any(t.needs_human_review for t in tags),
            reasoning=reasoning,
        )

    def _run_small_ner(self, field_name: str, value: Any) -> List[SecurityTag]:
        """执行 Small-NER 并转换为 SecurityTag。"""
        entities = self.small_ner.extract(str(value) if value is not None else "")
        tags: List[SecurityTag] = []

        # 敏感疾病关键字字典，用于判定是否升级至 L4
        sensitive_keywords = ["hiv", "精神分裂", "艾滋", "梅毒", "肿瘤", "癌症", "白血病", "抑郁症"]

        for ent in entities:
            label = ent.get("label", "")
            text = str(ent.get("text", "")).lower()
            conf = float(ent.get("confidence", 0.0))

            if label == "GENOMIC_HINT":
                tags.append(
                    SecurityTag(
                        level=SensitivityLevel.L5,
                        category="GENOMIC_HINT",
                        confidence=conf,
                        source_engine="SMALL_NER",
                        rule_id="NER_GENE_001",
                        needs_human_review=True,
                    )
                )
            elif label == "MEDICAL_DISEASE":
                # 检查是否命中敏感疾病关键字以确定是 L4 还是 L3
                if any(kw in text for kw in sensitive_keywords):
                    tags.append(
                        SecurityTag(
                            level=SensitivityLevel.L4,
                            category="MEDICAL_SENSITIVE_DISEASE",
                            confidence=conf,
                            source_engine="SMALL_NER",
                            rule_id="NER_DIS_SENSITIVE",
                        )
                    )
                else:
                    tags.append(
                        SecurityTag(
                            level=SensitivityLevel.L3,
                            category="MEDICAL_DISEASE",
                            confidence=conf,
                            source_engine="SMALL_NER",
                            rule_id="NER_DIS_NORMAL",
                        )
                    )
            elif label == "MEDICATION":
                tags.append(
                    SecurityTag(
                        level=SensitivityLevel.L3,
                        category="MEDICATION",
                        confidence=conf,
                        source_engine="SMALL_NER",
                        rule_id="NER_DRU_001",
                    )
                )
            elif label == "SURGERY":
                tags.append(
                    SecurityTag(
                        level=SensitivityLevel.L3,
                        category="SURGERY",
                        confidence=conf,
                        source_engine="SMALL_NER",
                        rule_id="NER_PRO_001",
                    )
                )
            elif label == "BODY_PART":
                tags.append(
                    SecurityTag(
                        level=SensitivityLevel.L3,
                        category="BODY_PART",
                        confidence=conf,
                        source_engine="SMALL_NER",
                        rule_id="NER_BOD_001",
                    )
                )
        return tags

    def classify_record(
        self,
        record: Dict[str, Any],
        params: Optional[Dict[str, Any]] = None,
        record_index: int = 0,
    ) -> RecordClassificationResult:
        """对单条记录进行分类。

        Args:
            record: 字段名到字段值的映射。
            params: 请求级参数。
            record_index: 记录索引，用于输出。

        Returns:
            RecordClassificationResult。
        """
        field_results: Dict[str, FieldClassificationResult] = {}
        aggregated_tags: List[SecurityTag] = []

        for field_name, value in record.items():
            field_result = self.classify_field(field_name, value, params)
            field_results[field_name] = field_result
            aggregated_tags.extend(field_result.tags)

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

        # 复合/上下文敏感规则后处理
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
        record_result = apply_composite_tags(record_result, composite_tags)
        return record_result

    def classify_table(
        self,
        schema: List[str],
        rows: List[Dict[str, Any]],
        params: Optional[Dict[str, Any]] = None,
    ) -> TableClassificationResult:
        """对整张表进行分类。

        Args:
            schema: 列名列表，决定输出顺序。
            rows: 记录列表，每条记录是字段名到字段值的字典。
            params: 请求级参数。

        Returns:
            TableClassificationResult。
        """
        cp, _ = _resolve_classification_params(self.resolver, params)
        record_results: List[RecordClassificationResult] = []
        aggregated_tags: List[SecurityTag] = []
        review_entries: List[Any] = []

        for idx, row in enumerate(rows):
            # 仅保留 schema 中存在的字段 / keep only columns present in schema
            filtered = {col: row.get(col) for col in schema if col in row}
            record_result = self.classify_record(filtered, params, record_index=idx)
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

        shadow_diff: List[ShadowDiff] = []
        if cp.shadow_mode and cp.shadow_version:
            shadow_params = dict(params) if params else {}
            shadow_params["rule_set_version"] = cp.shadow_version
            shadow_params["shadow_mode"] = False
            shadow_result = self.classify_table(schema, rows, shadow_params)
            shadow_diff = self._compute_shadow_diff(record_results, shadow_result.record_results)

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
        current_results: List[RecordClassificationResult],
        shadow_results: List[RecordClassificationResult],
    ) -> List[ShadowDiff]:
        """计算当前结果与影子结果的差异。"""
        diffs: List[ShadowDiff] = []
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
    # 多格式适配器 / Format adapters
    # ------------------------------------------------------------------

    def classify_json(
        self,
        json_input: Any,
        params: Optional[Dict[str, Any]] = None,
    ) -> ClassificationResult:
        """解析 JSON 字符串或字典并分类。

        若顶层为 dict 则按单条记录分类；若为 list 则按表分类（schema 取并集）。

        Args:
            json_input: JSON 字符串、字典或列表。
            params: 请求级参数。

        Returns:
            ClassificationResult。
        """
        if isinstance(json_input, str):
            data = json.loads(json_input)
        else:
            data = json_input

        cp, source = _resolve_classification_params(self.resolver, params)

        if isinstance(data, dict):
            record_result = self.classify_record(data, params)
            return ClassificationResult(
                record_result=record_result,
                audit_info=_build_audit_info(cp, source),
            )

        if isinstance(data, list) and data:
            schema = sorted({col for row in data for col in row.keys()})
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
        params: Optional[Dict[str, Any]] = None,
    ) -> ClassificationResult:
        """对 pandas DataFrame 进行分类（可选依赖，未安装时抛出 ImportError）。

        Args:
            df: pandas.DataFrame 实例。
            params: 请求级参数。

        Returns:
            ClassificationResult。
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
        params: Optional[Dict[str, Any]] = None,
    ) -> ClassificationResult:
        """对 pyarrow Table 进行分类（可选依赖，未安装时抛出 ImportError）。

        Args:
            table: pyarrow.Table 实例。
            params: 请求级参数。

        Returns:
            ClassificationResult。
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
        result_set: List[Dict[str, Any]],
        params: Optional[Dict[str, Any]] = None,
    ) -> ClassificationResult:
        """对 SQL 结果集（list[dict]）进行分类。

        Args:
            result_set: 查询结果列表，每条记录为字段名到字段值的字典。
            params: 请求级参数。

        Returns:
            ClassificationResult。
        """
        if not result_set:
            cp, source = _resolve_classification_params(self.resolver, params)
            return ClassificationResult(
                table_result=TableClassificationResult(
                    schema=[], final_level=SensitivityLevel.L1
                ),
                audit_info=_build_audit_info(cp, source),
            )
        schema = sorted({col for row in result_set for col in row.keys()})
        table_result = self.classify_table(schema, result_set, params)
        cp, source = _resolve_classification_params(self.resolver, params)
        return ClassificationResult(
            table_result=table_result,
            audit_info=_build_audit_info(cp, source),
        )

    def classify_secretflow(
        self,
        sf_data: Any,
        params: Optional[Dict[str, Any]] = None,
        party: Optional[str] = None,
    ) -> ClassificationResult:
        """对 SecretFlow 联邦数据结构进行分类。

        Args:
            sf_data: SecretFlow DataFrame / HDataFrame / VDataFrame / FedNdarray。
            params: 请求级分类参数。
            party: HDataFrame 参与方。

        Returns:
            ClassificationResult。
        """
        from .classification_secretflow import classify_secretflow as _classify_secretflow
        table_result = _classify_secretflow(self, sf_data, params=params, party=party)
        cp, source = _resolve_classification_params(self.resolver, params)
        return ClassificationResult(
            table_result=table_result,
            audit_info=_build_audit_info(cp, source),
        )

    def submit_classify_table_async(
        self,
        schema: List[str],
        rows: List[Dict[str, Any]],
        params: Optional[Dict[str, Any]] = None,
    ) -> str:
        """提交异步表分类任务。

        Args:
            schema: 列名列表。
            rows: 记录列表。
            params: 请求级参数。

        Returns:
            异步任务 ID。
        """
        return self.async_manager.submit(self.classify_table, schema, rows, params)

    def get_job_result(self, job_id: str) -> ClassificationJob:
        """查询异步分类任务结果。

        Args:
            job_id: 任务 ID。

        Returns:
            ClassificationJob。
        """
        return self.async_manager.get(job_id)

    def confirm_review(
        self,
        review_id: str,
        corrected_level: str,
        reviewer: str = "",
        comment: str = "",
    ) -> Any:
        """确认或修正复核条目。

        Args:
            review_id: 复核条目 ID。
            corrected_level: 修正后的等级。
            reviewer: 复核人。
            comment: 说明。

        Returns:
            ReviewEntry。
        """
        return self.review_store.confirm(review_id, corrected_level, reviewer, comment)

    def export_reviews(self, format: str = "jsonl", mask_input: bool = False) -> str:
        """导出复核样本。

        Args:
            format: `jsonl` 或 `csv`。
            mask_input: 是否对 input 掩码。

        Returns:
            导出内容字符串。
        """
        return self.review_store.export(format=format, mask_input=mask_input)


def _build_audit_info(params: ClassificationParams, parameter_source: str) -> AuditInfo:
    """构建审计信息。"""
    return AuditInfo(
        version=_PRIMITIVE_VERSION,
        profile_version=params.version,
        timestamp=datetime.now(timezone.utc).isoformat(),
        rule_engine_version=_RULE_ENGINE_VERSION,
        rule_set_version=params.rule_set_version,
        parameter_source=parameter_source,
    )
