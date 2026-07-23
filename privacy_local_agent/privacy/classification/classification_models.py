"""数据分类原语的数据模型与抽象接口。

定义敏感度等级、安全标签、字段/记录/表级分类结果、审计信息，
以及 RuleEngine / Small-NER / LLM 分类器的抽象基类，
供具体引擎、ClassificationAPI、REST/gRPC 接口统一使用。

Data classification primitive models and abstract interfaces. Defines sensitivity
levels, security tags, field/record/table classification results, audit metadata,
and abstract base classes for rule engine, Small-NER and LLM classifiers.
"""

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class SensitivityLevel(str, Enum):
    """敏感度等级枚举。

    Sensitivity level enum ordered from L1 (public) to L5 (extremely sensitive).
    """

    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    L4 = "L4"
    L5 = "L5"


class EngineLayer(str, Enum):
    """分类引擎层级枚举。

    Engine layer enum: rule-based, small NER, or LLM classifier.
    """

    L1_RULE = "L1_RULE"
    L2_SMALL_NER = "L2_SMALL_NER"
    L3_LLM = "L3_LLM"


_LEVEL_ORDER = {
    SensitivityLevel.L1: 1,
    SensitivityLevel.L2: 2,
    SensitivityLevel.L3: 3,
    SensitivityLevel.L4: 4,
    SensitivityLevel.L5: 5,
}


def max_level(*levels: SensitivityLevel) -> SensitivityLevel:
    """返回等级集合中的最高敏感度。

    Args:
        *levels: 一个或多个敏感度等级。

    Returns:
        最高敏感度等级；若无输入则返回 L1。
    """
    if not levels:
        return SensitivityLevel.L1
    return max(levels, key=lambda lvl: _LEVEL_ORDER[lvl])


def parse_level(value: Any) -> SensitivityLevel:
    """将字符串或其他值解析为 SensitivityLevel。

    Args:
        value: 待解析的值。

    Returns:
        对应的敏感度等级。

    Raises:
        ValueError: 当值无法解析时抛出。
    """
    if isinstance(value, SensitivityLevel):
        return value
    if not isinstance(value, str):
        value = str(value)
    try:
        return SensitivityLevel(value.upper())
    except ValueError as exc:
        raise ValueError(f"invalid sensitivity level: {value}") from exc


class SecurityTag(BaseModel):
    """安全标签，描述单个分类命中。

    Security tag representing a single classification hit with level, category,
    confidence, source engine, rule id, version and human-review flag.
    """

    model_config = ConfigDict(populate_by_name=True)

    level: SensitivityLevel
    category: str
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)
    source_engine: str = Field(default="RULE", alias="sourceEngine")
    rule_id: str = Field(default="", alias="ruleId")
    version: str = Field(default="1.0.0")
    needs_human_review: bool = Field(default=False, alias="needsHumanReview")

    def __str__(self) -> str:
        return f"{self.level.value}_{self.category}"


class FieldClassificationResult(BaseModel):
    """单个字段的分类结果。

    Classification result for a single field, including tags, final level,
    confidence, engine layer, human-review flag and reasoning.
    """

    model_config = ConfigDict(populate_by_name=True)

    field_name: str = Field(alias="fieldName")
    field_value: Optional[str] = Field(default=None, alias="fieldValue")
    tags: List[SecurityTag] = Field(default_factory=list)
    final_level: SensitivityLevel = Field(alias="finalLevel")
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    engine_layer: EngineLayer = Field(default=EngineLayer.L1_RULE, alias="engineLayer")
    needs_human_review: bool = Field(default=False, alias="needsHumanReview")
    reasoning: str = ""


class RecordClassificationResult(BaseModel):
    """单条记录（多个字段）的分类结果。

    Classification result for a record, aggregating field results and tags.
    """

    model_config = ConfigDict(populate_by_name=True)

    record_index: int = Field(alias="recordIndex")
    field_results: Dict[str, FieldClassificationResult] = Field(
        default_factory=dict, alias="fieldResults"
    )
    aggregated_tags: List[SecurityTag] = Field(default_factory=list, alias="aggregatedTags")
    final_level: SensitivityLevel = Field(alias="finalLevel")
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    needs_human_review: bool = Field(default=False, alias="needsHumanReview")


class ShadowDiff(BaseModel):
    """影子模式差异信息。

    Records the difference between current rule set and shadow rule set.
    """

    model_config = ConfigDict(populate_by_name=True)

    field_name: str = Field(default="", alias="fieldName")
    record_index: int = Field(default=0, alias="recordIndex")
    current_level: SensitivityLevel = Field(alias="currentLevel")
    shadow_level: SensitivityLevel = Field(alias="shadowLevel")
    current_tags: List[str] = Field(default_factory=list, alias="currentTags")
    shadow_tags: List[str] = Field(default_factory=list, alias="shadowTags")


class ReviewStatus(str, Enum):
    """复核条目状态。"""

    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"


class ReviewEntry(BaseModel):
    """人工复核条目。"""

    model_config = ConfigDict(populate_by_name=True)

    review_id: str = Field(alias="reviewId")
    record_index: int = Field(default=0, alias="recordIndex")
    field_name: str = Field(alias="fieldName")
    field_value: Optional[str] = Field(default=None, alias="fieldValue")
    predicted_level: Optional[SensitivityLevel] = Field(default=None, alias="predictedLevel")
    predicted_tags: List[str] = Field(default_factory=list, alias="predictedTags")
    corrected_level: Optional[str] = Field(default=None, alias="correctedLevel")
    reviewer: str = ""
    comment: str = ""
    status: ReviewStatus = ReviewStatus.PENDING
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat(), alias="createdAt")
    updated_at: Optional[str] = Field(default=None, alias="updatedAt")


class TableClassificationResult(BaseModel):
    """整张表/批次的分类结果。

    Classification result for a table/batch, aggregating record results.
    """

    model_config = ConfigDict(populate_by_name=True)

    schema_: List[str] = Field(default_factory=list, alias="schema")
    record_results: List[RecordClassificationResult] = Field(
        default_factory=list, alias="recordResults"
    )
    aggregated_tags: List[SecurityTag] = Field(default_factory=list, alias="aggregatedTags")
    final_level: SensitivityLevel = Field(alias="finalLevel")
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    needs_human_review: bool = Field(default=False, alias="needsHumanReview")
    review_entries: List[ReviewEntry] = Field(default_factory=list, alias="reviewEntries")
    shadow_diff: List[ShadowDiff] = Field(default_factory=list, alias="shadowDiff")


class AuditInfo(BaseModel):
    """审计信息，记录分类请求的执行元数据。

    Audit metadata for a classification request.
    """

    model_config = ConfigDict(populate_by_name=True)

    version: str = "1.0.0"
    profile_version: str = Field(default="default", alias="profileVersion")
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    rule_engine_version: str = Field(default="1.0.0", alias="ruleEngineVersion")
    rule_set_version: str = Field(default="1.0.0", alias="ruleSetVersion")
    parameter_source: str = Field(default="default", alias="parameterSource")


class ClassificationJobStatus(str, Enum):
    """异步分类任务状态。"""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"


class ClassificationJobResult(BaseModel):
    """异步分类任务结果包装器。"""

    model_config = ConfigDict(populate_by_name=True)

    result: Any = None


class ClassificationJob(BaseModel):
    """异步分类任务状态。"""

    model_config = ConfigDict(populate_by_name=True)

    job_id: str = Field(alias="jobId")
    status: ClassificationJobStatus = ClassificationJobStatus.PENDING
    result: Optional[ClassificationJobResult] = None
    error: Optional[str] = None
    created_at: str = Field(alias="createdAt")
    finished_at: Optional[str] = Field(default=None, alias="finishedAt")


class CompositeRule(BaseModel):
    """复合规则定义。

    当记录中同时命中 `min_matches` 个字段模式时，
    将记录敏感度升级为 `target_level`，并附加 `category` 标签。
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str = ""
    field_patterns: List[str] = Field(alias="fieldPatterns")
    min_matches: int = Field(alias="minMatches")
    target_level: SensitivityLevel = Field(alias="targetLevel")
    category: str = "COMPOSITE"
    rule_id: str = Field(default="COMPOSITE_001", alias="ruleId")


# ---------------------------------------------------------------------------
# 抽象基类 / Abstract base classes
# ---------------------------------------------------------------------------

class RuleEngineABC(ABC):
    """规则引擎抽象接口。"""

    @abstractmethod
    def evaluate(
        self, field_name: str, value: Any, params: "ClassificationParams"
    ) -> List["SecurityTag"]:
        """评估单个字段，返回命中的安全标签列表。"""


class SmallNerEngine(ABC):
    """Small-NER 引擎抽象接口（Layer 2）。"""

    @abstractmethod
    def extract(self, text: str) -> List[Dict[str, Any]]:
        """从文本中提取实体，返回包含 label、confidence 等字段的字典列表。"""


class NoOpSmallNerEngine(SmallNerEngine):
    """默认空实现，不返回任何实体。"""

    def extract(self, text: str) -> List[Dict[str, Any]]:
        return []


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


class ClassificationResult(BaseModel):
    """分类结果包装器，可包含记录或表级结果。

    Wrapper that holds either a record or a table classification result along
    with audit information.
    """

    model_config = ConfigDict(populate_by_name=True)

    record_result: Optional[RecordClassificationResult] = Field(
        default=None, alias="recordResult"
    )
    table_result: Optional[TableClassificationResult] = Field(default=None, alias="tableResult")
    audit_info: AuditInfo = Field(default_factory=AuditInfo, alias="auditInfo")


class ClassificationParams(BaseModel):
    """分类原语参数模型，支持配置与请求级覆盖。

    Parameter model for the classification primitive, supporting built-in
    defaults, YAML profile overrides and request-level overrides.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    version: str = "1.0.0"
    default_level: SensitivityLevel = Field(default=SensitivityLevel.L3, alias="defaultLevel")
    enable_rule_engine: bool = Field(default=True, alias="enableRuleEngine")
    enable_small_ner: bool = Field(default=False, alias="enableSmallNer")
    enable_llm: bool = Field(default=False, alias="enableLlm")
    llm_confidence_threshold: float = Field(
        default=0.6, ge=0.0, le=1.0, alias="llmConfidenceThreshold"
    )
    icd10_l4_intervals: List[Dict[str, str]] = Field(
        default_factory=lambda: [
            {"start": "B20", "end": "B24"},
            {"start": "F20", "end": "F29"},
            {"start": "C00", "end": "C97"},
        ],
        alias="icd10L4Intervals",
    )
    genomic_keywords: List[str] = Field(
        default_factory=lambda: [
            "brca1",
            "brca2",
            "tp53",
            "rs",
            "snp",
            "cnv",
            "genome",
            "genomic",
            "gene",
            "mutation",
            "variant",
        ],
        alias="genomicKeywords",
    )
    public_field_whitelist: List[str] = Field(
        default_factory=lambda: ["public_report", "annual_summary", "科普"],
        alias="publicFieldWhitelist",
    )
    operational_field_patterns: List[str] = Field(
        default_factory=lambda: ["turnover_rate", "device_usage", "inventory"],
        alias="operationalFieldPatterns",
    )
    manual_override: Dict[str, SensitivityLevel] = Field(
        default_factory=dict, alias="manualOverride"
    )
    template: Optional[str] = Field(default=None, alias="template")
    rule_set_version: str = Field(default="1.0.0", alias="ruleSetVersion")
    shadow_mode: bool = Field(default=False, alias="shadowMode")
    shadow_version: Optional[str] = Field(default=None, alias="shadowVersion")
    return_field_values: bool = Field(default=True, alias="returnFieldValues")
    composite_rules: List[Any] = Field(default_factory=list, alias="compositeRules")
    enable_review: bool = Field(default=True, alias="enableReview")
    review_export_mask: bool = Field(default=False, alias="reviewExportMask")

    def apply_manual_override(self, field_name: str, level: SensitivityLevel) -> SensitivityLevel:
        """应用字段级人工覆盖。

        Args:
            field_name: 字段名。
            level: 当前计算的等级。

        Returns:
            若存在人工覆盖则返回覆盖等级，否则返回原等级。
        """
        if field_name in self.manual_override:
            return self.manual_override[field_name]
        return level
