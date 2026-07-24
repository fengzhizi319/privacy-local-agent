"""数据分类原语的数据模型与抽象接口。

定义敏感度等级、安全标签、字段/记录/表级分类结果、审计信息，
以及 RuleEngine / Small-NER / LLM 分类器的抽象基类，
供具体引擎、ClassificationAPI、REST/gRPC 接口统一使用。

Data classification primitive models and abstract interfaces. Defines sensitivity
levels, security tags, field/record/table classification results, audit metadata,
and abstract base classes for rule engine, Small-NER and LLM classifiers.
"""

# 导入抽象基类模块，用于定义 RuleEngine / NER / LLM 的抽象接口
from abc import ABC, abstractmethod
# 导入日期时间工具，用于生成审计时间戳和复核条目的创建时间
from datetime import datetime, timezone
# 导入枚举基类，用于定义 SensitivityLevel / EngineLayer / ReviewStatus 等枚举
from enum import Enum
# 导入 Any 类型注解，用于声明不确定类型的字段（如字段值、任务结果等）
from typing import Any

# 导入 Pydantic v2 核心组件：
# - BaseModel：所有数据模型的基类
# - ConfigDict：模型配置（如 populate_by_name 允许通过字段名或别名赋值）
# - Field：字段元数据声明（默认值、别名、约束等）
from pydantic import BaseModel, ConfigDict, Field


# ===========================================================================
# 枚举定义
# ===========================================================================


class SensitivityLevel(str, Enum):
    """敏感度等级枚举。

    继承 str 使得枚举值可直接序列化为 JSON 字符串。
    等级从 L1（公开）到 L5（极敏感）递增，用于标识数据字段的敏感程度。

    Sensitivity level enum ordered from L1 (public) to L5 (extremely sensitive).
    """

    L1 = "L1"  # 公开数据，无隐私风险（如科普文章、公开报告）
    L2 = "L2"  # 内部数据，低敏感度（如运营指标、设备使用率）
    L3 = "L3"  # 敏感数据，中敏感度（如姓名、手机号、普通病历）
    L4 = "L4"  # 高敏感数据（如 HIV 诊断、精神疾病、ICD-10 特定区间）
    L5 = "L5"  # 极敏感数据（如基因信息 BRCA1/TP53、SNP 变异）


class EngineLayer(str, Enum):
    """分类引擎层级枚举。

    标识分类结果由哪一层引擎产出，用于审计追踪和结果溯源。
    三层漏斗架构：规则引擎 → Small-NER → LLM，逐层递进。

    Engine layer enum: rule-based, small NER, or LLM classifier.
    """

    L1_RULE = "L1_RULE"            # 第一层：基于规则引擎（字段名匹配 + 值模式匹配）
    L2_SMALL_NER = "L2_SMALL_NER"  # 第二层：小型 NER 模型（ONNX/ModelScope 实体识别）
    L3_LLM = "L3_LLM"             # 第三层：本地大语言模型（Qwen2-VL 多模态分类）


# 敏感度等级排序映射表，用于比较不同等级的高低。
# 数值越大表示敏感度越高，供 max_level() 函数使用。
_LEVEL_ORDER = {
    SensitivityLevel.L1: 1,  # L1 排序值为 1（最低）
    SensitivityLevel.L2: 2,  # L2 排序值为 2
    SensitivityLevel.L3: 3,  # L3 排序值为 3
    SensitivityLevel.L4: 4,  # L4 排序值为 4
    SensitivityLevel.L5: 5,  # L5 排序值为 5（最高）
}


def max_level(*levels: SensitivityLevel) -> SensitivityLevel:
    """返回等级集合中的最高敏感度。

    在多条规则同时命中时，取最高等级作为最终结果（"就高不就低"原则）。

    Args:
        *levels: 一个或多个敏感度等级。

    Returns:
        最高敏感度等级；若无输入则返回 L1（默认最低等级）。
    """
    # 如果没有传入任何等级，返回默认的 L1（公开）
    if not levels:
        return SensitivityLevel.L1
    # 使用 _LEVEL_ORDER 映射表作为排序键，取数值最大的等级
    return max(levels, key=lambda lvl: _LEVEL_ORDER[lvl])


def parse_level(value: Any) -> SensitivityLevel:
    """将字符串或其他值解析为 SensitivityLevel 枚举。

    支持从 REST/gRPC 请求中传入的字符串（如 "L3"、"l3"）转换为枚举值。

    Args:
        value: 待解析的值（字符串、枚举实例或其他可转字符串的类型）。

    Returns:
        对应的敏感度等级枚举。

    Raises:
        ValueError: 当值无法解析为有效等级时抛出。
    """
    # 如果已经是 SensitivityLevel 实例，直接返回（避免重复转换）
    if isinstance(value, SensitivityLevel):
        return value
    # 如果不是字符串类型，先转为字符串（兼容整数等输入）
    if not isinstance(value, str):
        value = str(value)
    try:
        # 统一转大写后尝试匹配枚举值（如 "l3" → "L3"）
        return SensitivityLevel(value.upper())
    except ValueError as exc:
        # 匹配失败时抛出带有明确提示的 ValueError
        raise ValueError(f"invalid sensitivity level: {value}") from exc


# ===========================================================================
# 核心数据模型
# ===========================================================================


class SecurityTag(BaseModel):
    """安全标签，描述单个分类命中。

    每次规则/NER/LLM 命中都会产出一个 SecurityTag，记录：
    - 命中了什么等级（level）
    - 属于什么类别（category，如 PHONE_NUMBER、ICD10）
    - 置信度（confidence）
    - 来源引擎（source_engine）
    - 触发的规则 ID（rule_id）

    Security tag representing a single classification hit with level, category,
    confidence, source engine, rule id, version and human-review flag.
    """

    # 允许通过 Python 字段名或 JSON 别名（camelCase）两种方式赋值
    model_config = ConfigDict(populate_by_name=True)

    level: SensitivityLevel  # 该标签对应的敏感度等级（L1~L5）
    category: str  # 分类类别标识（如 "PHONE_NUMBER"、"ID_CARD"、"ICD10"）
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)  # 置信度 [0,1]，规则引擎默认 1.0
    source_engine: str = Field(default="RULE", alias="sourceEngine")  # 来源引擎标识
    rule_id: str = Field(default="", alias="ruleId")  # 触发的规则 ID（用于审计追踪）
    version: str = Field(default="1.0.0")  # 标签版本号
    needs_human_review: bool = Field(default=False, alias="needsHumanReview")  # 是否需要人工复核

    def __str__(self) -> str:
        """字符串表示，格式为 '等级_类别'（如 'L3_PHONE_NUMBER'）。"""
        return f"{self.level.value}_{self.category}"


class FieldClassificationResult(BaseModel):
    """单个字段的分类结果。

    记录对数据集中某一个字段（列）的完整分类信息，包括：
    - 所有命中的安全标签列表
    - 最终裁定的敏感度等级
    - 产出该结果的引擎层级
    - 是否需要人工复核

    Classification result for a single field, including tags, final level,
    confidence, engine layer, human-review flag and reasoning.
    """

    # 允许通过字段名或 camelCase 别名赋值
    model_config = ConfigDict(populate_by_name=True)

    field_name: str = Field(alias="fieldName")  # 字段名称（如 "phone"、"diagnosis"）
    field_value: str | None = Field(default=None, alias="fieldValue")  # 字段示例值（可选，用于审计）
    tags: list[SecurityTag] = Field(default_factory=list)  # 该字段命中的所有安全标签
    final_level: SensitivityLevel = Field(alias="finalLevel")  # 最终裁定的敏感度等级
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)  # 最终置信度
    engine_layer: EngineLayer = Field(default=EngineLayer.L1_RULE, alias="engineLayer")  # 产出结果的引擎层
    needs_human_review: bool = Field(default=False, alias="needsHumanReview")  # 是否需人工复核
    reasoning: str = ""  # 分类推理说明（LLM 层会填充详细推理过程）


class RecordClassificationResult(BaseModel):
    """单条记录（多个字段）的分类结果。

    聚合一条数据记录中所有字段的分类结果，并给出记录级的综合等级。
    记录级等级 = 所有字段等级中的最高值（"就高不就低"）。

    Classification result for a record, aggregating field results and tags.
    """

    # 允许通过字段名或 camelCase 别名赋值
    model_config = ConfigDict(populate_by_name=True)

    record_index: int = Field(alias="recordIndex")  # 记录在数据集中的索引位置
    field_results: dict[str, FieldClassificationResult] = Field(
        default_factory=dict, alias="fieldResults"
    )  # 字段名 → 字段分类结果的映射
    aggregated_tags: list[SecurityTag] = Field(default_factory=list, alias="aggregatedTags")  # 记录级聚合标签
    final_level: SensitivityLevel = Field(alias="finalLevel")  # 记录级最终等级（取所有字段最高）
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)  # 记录级综合置信度
    needs_human_review: bool = Field(default=False, alias="needsHumanReview")  # 是否需人工复核


class ShadowDiff(BaseModel):
    """影子模式差异信息。

    当启用 shadow_mode 时，系统会同时用当前规则集和影子规则集进行分类，
    并记录两者结果的差异，用于规则变更前的影响评估。

    Records the difference between current rule set and shadow rule set.
    """

    # 允许通过字段名或 camelCase 别名赋值
    model_config = ConfigDict(populate_by_name=True)

    field_name: str = Field(default="", alias="fieldName")  # 产生差异的字段名
    record_index: int = Field(default=0, alias="recordIndex")  # 产生差异的记录索引
    current_level: SensitivityLevel = Field(alias="currentLevel")  # 当前规则集的分级结果
    shadow_level: SensitivityLevel = Field(alias="shadowLevel")  # 影子规则集的分级结果
    current_tags: list[str] = Field(default_factory=list, alias="currentTags")  # 当前规则集命中的标签
    shadow_tags: list[str] = Field(default_factory=list, alias="shadowTags")  # 影子规则集命中的标签


class ReviewStatus(str, Enum):
    """复核条目状态枚举。

    标识人工复核队列中条目的处理进度。
    """

    PENDING = "PENDING"      # 待复核：尚未被审核人员处理
    CONFIRMED = "CONFIRMED"  # 已确认：审核人员已完成复核并确认/修正


class ReviewEntry(BaseModel):
    """人工复核条目。

    当分类结果标记 needs_human_review=True 时，系统自动生成复核条目，
    存入审核队列等待人工确认或修正。

    包含原始预测结果和人工修正结果两部分信息。
    """

    # 允许通过字段名或 camelCase 别名赋值
    model_config = ConfigDict(populate_by_name=True)

    review_id: str = Field(alias="reviewId")  # 复核条目唯一标识
    record_index: int = Field(default=0, alias="recordIndex")  # 关联的记录索引
    field_name: str = Field(alias="fieldName")  # 待复核的字段名
    field_value: str | None = Field(default=None, alias="fieldValue")  # 字段原始值（可选）
    predicted_level: SensitivityLevel | None = Field(default=None, alias="predictedLevel")  # 系统预测的等级
    predicted_tags: list[str] = Field(default_factory=list, alias="predictedTags")  # 系统预测的标签列表
    corrected_level: str | None = Field(default=None, alias="correctedLevel")  # 人工修正后的等级
    reviewer: str = ""  # 审核人员标识
    comment: str = ""  # 审核备注
    status: ReviewStatus = ReviewStatus.PENDING  # 当前复核状态（默认待处理）
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat(), alias="createdAt")  # 创建时间（UTC ISO格式）
    updated_at: str | None = Field(default=None, alias="updatedAt")  # 最后更新时间


class TableClassificationResult(BaseModel):
    """整张表/批次的分类结果。

    最顶层的分类结果容器，聚合所有记录的分类结果，
    并给出表级的综合等级、审核条目和影子模式差异。

    Classification result for a table/batch, aggregating record results.
    """

    # 允许通过字段名或 camelCase 别名赋值
    model_config = ConfigDict(populate_by_name=True)

    schema_: list[str] = Field(default_factory=list, alias="schema")  # 表的字段名列表（列名）
    record_results: list[RecordClassificationResult] = Field(
        default_factory=list, alias="recordResults"
    )  # 所有记录的分类结果列表
    aggregated_tags: list[SecurityTag] = Field(default_factory=list, alias="aggregatedTags")  # 表级聚合标签
    final_level: SensitivityLevel = Field(alias="finalLevel")  # 表级最终等级（取所有记录最高）
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)  # 表级综合置信度
    needs_human_review: bool = Field(default=False, alias="needsHumanReview")  # 是否需人工复核
    review_entries: list[ReviewEntry] = Field(default_factory=list, alias="reviewEntries")  # 关联的复核条目
    shadow_diff: list[ShadowDiff] = Field(default_factory=list, alias="shadowDiff")  # 影子模式差异列表


class AuditInfo(BaseModel):
    """审计信息，记录分类请求的执行元数据。

    每次分类请求都会附带审计信息，用于事后追溯：
    - 使用了哪个版本的规则引擎
    - 使用了哪个参数配置
    - 请求发生的时间戳

    Audit metadata for a classification request.
    """

    # 允许通过字段名或 camelCase 别名赋值
    model_config = ConfigDict(populate_by_name=True)

    version: str = "1.0.0"  # 审计信息格式版本
    profile_version: str = Field(default="default", alias="profileVersion")  # 使用的 Profile 版本
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())  # 请求时间戳（UTC）
    rule_engine_version: str = Field(default="1.0.0", alias="ruleEngineVersion")  # 规则引擎版本
    rule_set_version: str = Field(default="1.0.0", alias="ruleSetVersion")  # 规则集版本
    parameter_source: str = Field(default="default", alias="parameterSource")  # 参数来源标识


# ===========================================================================
# 异步任务相关模型
# ===========================================================================


class ClassificationJobStatus(str, Enum):
    """异步分类任务状态枚举。

    异步任务的生命周期：PENDING → RUNNING → DONE/FAILED。
    """

    PENDING = "PENDING"  # 排队中：任务已创建但尚未开始执行
    RUNNING = "RUNNING"  # 执行中：任务正在被线程池处理
    DONE = "DONE"        # 已完成：任务成功执行并产出结果
    FAILED = "FAILED"    # 已失败：任务执行过程中发生异常


class ClassificationJobResult(BaseModel):
    """异步分类任务结果包装器。

    封装异步任务的执行结果，result 字段可存放任意类型的分类输出。
    """

    # 允许通过字段名或 camelCase 别名赋值
    model_config = ConfigDict(populate_by_name=True)

    result: Any = None  # 任务执行结果（通常为 TableClassificationResult 的字典形式）


class ClassificationJob(BaseModel):
    """异步分类任务状态模型。

    记录一个异步分类任务的完整生命周期信息，
    包括任务 ID、当前状态、执行结果、错误信息和时间戳。
    """

    # 允许通过字段名或 camelCase 别名赋值
    model_config = ConfigDict(populate_by_name=True)

    job_id: str = Field(alias="jobId")  # 任务唯一标识（UUID）
    status: ClassificationJobStatus = ClassificationJobStatus.PENDING  # 当前任务状态
    result: ClassificationJobResult | None = None  # 执行结果（仅 DONE 时有值）
    error: str | None = None  # 错误信息（仅 FAILED 时有值）
    created_at: str = Field(alias="createdAt")  # 任务创建时间
    finished_at: str | None = Field(default=None, alias="finishedAt")  # 任务完成时间


# ===========================================================================
# 复合规则模型
# ===========================================================================


class CompositeRule(BaseModel):
    """复合规则定义。

    复合规则是一种上下文感知的分类策略：
    当一条记录中同时有 >= min_matches 个字段匹配指定的字段模式时，
    将整条记录的敏感度升级为 target_level。

    典型场景：当记录同时包含 "诊断"、"HIV"、"用药" 字段时，
    即使单个字段仅为 L3，组合后应升级为 L4。

    当记录中同时命中 `min_matches` 个字段模式时，
    将记录敏感度升级为 `target_level`，并附加 `category` 标签。
    """

    # 允许通过字段名或 camelCase 别名赋值
    model_config = ConfigDict(populate_by_name=True)

    name: str = ""  # 规则名称（人类可读描述）
    field_patterns: list[str] = Field(alias="fieldPatterns")  # 字段名匹配模式列表（支持子串匹配）
    min_matches: int = Field(alias="minMatches")  # 最少需要命中的字段数
    target_level: SensitivityLevel = Field(alias="targetLevel")  # 命中后升级到的目标等级
    category: str = "COMPOSITE"  # 标签类别标识
    rule_id: str = Field(default="COMPOSITE_001", alias="ruleId")  # 规则唯一 ID


# ===========================================================================
# 抽象基类 / Abstract base classes
# 定义三层分类引擎的统一接口契约，具体实现由各子模块提供。
# ===========================================================================


class RuleEngineABC(ABC):
    """规则引擎抽象接口（Layer 1）。

    所有规则引擎实现（DefaultRuleEngine、VectorizedRuleEngine）都必须实现此接口。
    规则引擎负责通过字段名匹配和值模式匹配进行快速分类。
    """

    @abstractmethod
    def evaluate(
        self, field_name: str, value: Any, params: "ClassificationParams"
    ) -> list["SecurityTag"]:
        """评估单个字段，返回命中的安全标签列表。

        Args:
            field_name: 待评估的字段名称。
            value: 字段的示例值（可为 None，此时仅做字段名匹配）。
            params: 分类参数（含规则配置、模板、阈值等）。

        Returns:
            命中的安全标签列表；未命中任何规则时返回空列表。
        """


class SmallNerEngine(ABC):
    """Small-NER 引擎抽象接口（Layer 2）。

    小型命名实体识别引擎，用于从文本中提取敏感实体（如人名、地名、疾病名）。
    具体实现包括 ONNXSmallNerEngine 和 ModelScopeSmallNerEngine。
    """

    @abstractmethod
    def extract(self, text: str) -> list[dict[str, Any]]:
        """从文本中提取命名实体。

        Args:
            text: 待分析的文本内容。

        Returns:
            实体字典列表，每个字典包含：
            - label: 实体标签（如 "PER"、"LOC"、"DISEASE"）
            - confidence: 识别置信度
            - start/end: 实体在文本中的位置
        """


class NoOpSmallNerEngine(SmallNerEngine):
    """默认空实现（降级用），不返回任何实体。

    当 NER 模型文件不存在或 onnxruntime/modelscope 未安装时，
    系统自动降级为此空实现，保证服务可用性。
    """

    def extract(self, text: str) -> list[dict[str, Any]]:
        """空实现：始终返回空列表，表示未识别到任何实体。"""
        return []


class LlmClassifier(ABC):
    """LLM 分类器抽象接口（Layer 3）。

    本地大语言模型分类器，用于处理规则引擎和 NER 无法确定的复杂场景。
    具体实现为 Qwen2VLClassifier（支持多模态：文本 + 图片）。
    """

    @abstractmethod
    def classify(
        self, text: str, upstream_level: SensitivityLevel, upstream_confidence: float
    ) -> dict[str, Any] | None:
        """基于上游结果对文本进行深度分类。

        Args:
            text: 待分类的文本内容。
            upstream_level: 上游引擎（Layer 1/2）给出的等级。
            upstream_confidence: 上游引擎的置信度。

        Returns:
            结构化分类结果字典（含 final_level、confidence、reasoning 等），
            或 None 表示 LLM 认为上游结果已足够准确无需修正。
        """


class NoOpLlmClassifier(LlmClassifier):
    """默认空实现（降级用）：当上游置信度低于阈值时给出保守回退结果。

    当 LLM 模型未下载或 torch/transformers 未安装时，
    系统自动降级为此实现。对于低置信度的上游结果，
    标记为需要人工复核，避免错误分类。
    """

    def classify(
        self, text: str, upstream_level: SensitivityLevel, upstream_confidence: float
    ) -> dict[str, Any] | None:
        """降级分类逻辑：

        - 若上游置信度 < 0.6，返回保守结果并标记需人工复核
        - 若上游置信度 >= 0.6，返回 None 表示信任上游结果
        """
        # 上游置信度低于 0.6 时，认为结果不可靠，需要人工介入
        if upstream_confidence < 0.6:
            return {
                "final_level": upstream_level,  # 保持上游等级不变
                "sub_category": "LLM_FALLBACK",  # 标记为 LLM 降级回退
                "confidence": upstream_confidence,  # 保持上游置信度
                "reasoning": "LLM 未启用，按上游最高等级降级/保守处理",  # 推理说明
                "suggested_action": "review",  # 建议操作：人工复核
                "needs_human_review": True,  # 标记需要人工复核
            }
        # 上游置信度足够高，无需修正
        return None


# ===========================================================================
# 顶层结果包装与参数模型
# ===========================================================================


class ClassificationResult(BaseModel):
    """分类结果包装器，可包含记录或表级结果。

    REST/gRPC 接口的统一返回格式，根据请求类型填充
    record_result（单记录分类）或 table_result（批量/表级分类），
    并始终附带审计信息。

    Wrapper that holds either a record or a table classification result along
    with audit information.
    """

    # 允许通过字段名或 camelCase 别名赋值
    model_config = ConfigDict(populate_by_name=True)

    record_result: RecordClassificationResult | None = Field(
        default=None, alias="recordResult"
    )  # 单记录分类结果（classify_record 接口使用）
    table_result: TableClassificationResult | None = Field(default=None, alias="tableResult")  # 表级分类结果（classify_table 接口使用）
    audit_info: AuditInfo = Field(default_factory=AuditInfo, alias="auditInfo")  # 审计元数据（自动生成）


class ClassificationParams(BaseModel):
    """分类原语参数模型，支持配置与请求级覆盖。

    这是分类引擎的核心配置模型，控制：
    - 三层引擎的启用/禁用开关
    - LLM 触发阈值
    - ICD-10 敏感区间
    - 基因组关键词
    - 字段白名单/模式
    - 人工覆盖映射
    - 合规模板选择
    - 影子模式
    - 复合规则

    参数优先级：请求级覆盖 > YAML Profile > 内置默认值。

    Parameter model for the classification primitive, supporting built-in
    defaults, YAML profile overrides and request-level overrides.
    """

    # 允许通过字段名或别名赋值；extra="allow" 允许传入未定义的额外字段（前向兼容）
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    version: str = "1.0.0"  # 参数模型版本号
    default_level: SensitivityLevel = Field(default=SensitivityLevel.L3, alias="defaultLevel")  # 无规则命中时的默认等级
    enable_rule_engine: bool = Field(default=True, alias="enableRuleEngine")  # 是否启用 Layer-1 规则引擎
    enable_small_ner: bool = Field(default=False, alias="enableSmallNer")  # 是否启用 Layer-2 NER 引擎
    enable_llm: bool = Field(default=False, alias="enableLlm")  # 是否启用 Layer-3 LLM 分类器
    llm_confidence_threshold: float = Field(
        default=0.6, ge=0.0, le=1.0, alias="llmConfidenceThreshold"
    )  # LLM 触发阈值：上游置信度低于此值时触发 LLM 复核
    icd10_l4_intervals: list[dict[str, str]] = Field(
        default_factory=lambda: [
            {"start": "B20", "end": "B24"},   # HIV 相关诊断编码区间
            {"start": "F20", "end": "F29"},   # 精神分裂症等精神疾病区间
            {"start": "C00", "end": "C97"},   # 恶性肿瘤编码区间
        ],
        alias="icd10L4Intervals",
    )  # ICD-10 编码中属于 L4（高敏感）的区间列表
    genomic_keywords: list[str] = Field(
        default_factory=lambda: [
            "brca1",      # 乳腺癌易感基因 1
            "brca2",      # 乳腺癌易感基因 2
            "tp53",       # 肿瘤抑制基因
            "rs",         # SNP 参考序列前缀
            "snp",        # 单核苷酸多态性
            "cnv",        # 拷贝数变异
            "genome",     # 基因组
            "genomic",    # 基因组的
            "gene",       # 基因
            "mutation",   # 突变
            "variant",    # 变异
        ],
        alias="genomicKeywords",
    )  # 基因组相关关键词列表，命中时升级为 L5（极敏感）
    public_field_whitelist: list[str] = Field(
        default_factory=lambda: ["public_report", "annual_summary", "科普"],
        alias="publicFieldWhitelist",
    )  # 公开字段白名单：匹配这些模式的字段直接标记为 L1（公开）
    operational_field_patterns: list[str] = Field(
        default_factory=lambda: ["turnover_rate", "device_usage", "inventory"],
        alias="operationalFieldPatterns",
    )  # 运营字段模式：匹配这些模式的字段标记为 L2（内部）
    manual_override: dict[str, SensitivityLevel] = Field(
        default_factory=dict, alias="manualOverride"
    )  # 字段级人工覆盖映射：{字段名: 强制等级}，优先级最高
    template: str | None = Field(default=None, alias="template")  # 合规模板名称（如 "medical"、"finance"）
    rule_set_version: str = Field(default="1.0.0", alias="ruleSetVersion")  # 规则集版本号
    shadow_mode: bool = Field(default=False, alias="shadowMode")  # 是否启用影子模式（对比新旧规则集）
    shadow_version: str | None = Field(default=None, alias="shadowVersion")  # 影子规则集版本
    return_field_values: bool = Field(default=True, alias="returnFieldValues")  # 是否在结果中返回字段原始值
    composite_rules: list[Any] = Field(default_factory=list, alias="compositeRules")  # 请求级复合规则列表
    enable_review: bool = Field(default=True, alias="enableReview")  # 是否启用人工复核队列
    review_export_mask: bool = Field(default=False, alias="reviewExportMask")  # 导出复核数据时是否脱敏

    def apply_manual_override(self, field_name: str, level: SensitivityLevel) -> SensitivityLevel:
        """应用字段级人工覆盖。

        人工覆盖具有最高优先级：如果管理员为某字段指定了固定等级，
        则无论引擎计算出什么结果，都使用人工指定的等级。

        Args:
            field_name: 字段名。
            level: 当前引擎计算的等级。

        Returns:
            若存在人工覆盖则返回覆盖等级，否则返回原等级。
        """
        # 检查 manual_override 字典中是否有该字段的覆盖配置
        if field_name in self.manual_override:
            # 存在覆盖：返回管理员指定的等级
            return self.manual_override[field_name]
        # 不存在覆盖：保持引擎计算的原始等级
        return level
