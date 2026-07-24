"""复合/上下文感知规则引擎 / Composite / Context-Aware Rule Engine.

中文说明：
复合规则引擎用于识别"单字段不敏感、多字段组合后敏感"的上下文场景。
它在单条记录的字段级分类完成后执行，根据字段名组合升级敏感度等级。

典型场景举例：
- 单独一个 "name" 字段可能只是 L3，但如果同一条记录中同时存在
  "name" + "id_card" + "mobile"，则组合后应升级为 L5（可直接定位到个人）。
- 单独的 "diagnosis" 字段是 L3/L4，但如果同时存在 "gene"/"brca" 字段，
  则组合后应升级为 L5（医疗基因组合）。

执行时机：
在 ClassificationAPI._classify_record() 中，所有字段的 Layer-1/2/3 分类完成后，
调用 CompositeRuleEngine.evaluate() 进行后处理。

English Description:
Composite rule engine identifies context scenarios where "individual fields are not sensitive,
but become sensitive when combined". It executes after field-level classification of a single
record and upgrades sensitivity levels based on field name combinations.
"""

# 启用延迟注解求值，允许在类型提示中引用尚未定义的类名
from __future__ import annotations

# 导入正则表达式模块，用于字段名模式匹配（支持正则语法如 ^name$、id_card|idcard）
import re
# 导入类型注解：Any 用于通用类型，ClassVar 用于声明类级别变量
from typing import Any, ClassVar

# 导入结构化日志工厂函数
from ...observability.logging_config import get_logger
# 导入 Prometheus Counter 指标，用于统计复合规则的命中次数
from ...observability.metrics import CLASSIFICATION_COMPOSITE_HITS_TOTAL
# 从数据模型模块导入所需类型
from .classification_models import (
    CompositeRule,              # 复合规则定义模型
    FieldClassificationResult,  # 字段级分类结果
    SecurityTag,                # 安全标签模型
    SensitivityLevel,           # 敏感度等级枚举
    max_level,                  # 取最高等级工具函数
)

# 创建模块级结构化日志器
logger = get_logger(__name__)


def _normalize(name: str) -> str:
    """规范化字段名用于模式匹配 / Normalize Field Name for Pattern Matching.

    将字段名统一为小写并移除下划线和空格，使得不同命名风格
    （如 id_card、idCard、ID Card）都能被同一正则模式匹配。

    Args:
        name: 原始字段名 / Original field name.

    Returns:
        规范化后的字段名（全小写、无下划线、无空格） / Normalized field name.
    """
    # 转字符串 → 转小写 → 去下划线 → 去空格
    return str(name).lower().replace("_", "").replace(" ", "")


class CompositeRuleEngine:
    """复合规则引擎 / Composite Rule Engine.

    维护一组复合规则，对单条记录及其字段分类结果进行后处理。
    当记录中的字段名组合满足某条复合规则的 min_matches 阈值时，
    生成对应的 SecurityTag 并升级记录的最终敏感度等级。

    工作流程：
    1. 遍历所有复合规则
    2. 对每条规则，检查记录中的字段名是否匹配其 field_patterns
    3. 统计匹配数，达到 min_matches 时生成标签
    4. 返回所有命中的标签列表

    Attributes:
        rules: 当前生效的复合规则列表 / List of active composite rules.
    """

    # 内置默认复合规则列表（类变量，所有实例共享）
    # 这些规则覆盖了最常见的组合敏感场景
    DEFAULT_RULES: ClassVar[list[CompositeRule]] = [
        # 规则 COMP_001：高敏感个人信息组合
        # 当记录同时包含 姓名 + 身份证号 + 手机号 三个字段时，
        # 可直接定位到具体个人，升级为 L5（极敏感）
        CompositeRule(
            name="高敏感个人信息组合",
            # 字段名正则模式列表：精确匹配 name、模糊匹配 idcard/identity、模糊匹配 mobile/phone
            field_patterns=[r"^name$", r"id_card|idcard|identity", r"mobile|phone|cell"],
            min_matches=3,  # 需要同时命中 3 个模式
            target_level=SensitivityLevel.L5,  # 升级为极敏感
            category="COMPOSITE_PII_COMBO",   # 标签类别
            rule_id="COMP_001",               # 规则唯一标识
        ),
        # 规则 COMP_002：医疗基因组合
        # 当记录同时包含 诊断/疾病 字段 + 基因/变异 字段时，
        # 构成可关联到个人健康隐私的组合，升级为 L5
        CompositeRule(
            name="医疗基因组合",
            # 匹配诊断/疾病类字段 + 基因/变异类字段
            field_patterns=[r"diagnosis|disease|illness", r"gene|genomic|mutation|brca|tp53|rs\d+"],
            min_matches=2,  # 需要同时命中 2 个模式
            target_level=SensitivityLevel.L5,  # 升级为极敏感
            category="COMPOSITE_MEDICAL_GENOMIC",  # 标签类别
            rule_id="COMP_002",
        ),
        # 规则 COMP_003：金融账户组合
        # 只要记录中包含任何金融账户相关字段，即升级为 L4
        # （金融数据单独就具有高敏感性）
        CompositeRule(
            name="金融账户组合",
            # 匹配银行卡号、账户、信用、交易等金融字段
            field_patterns=[r"bank_card|bankcard|card_no|account|credit|transaction"],
            min_matches=1,  # 只需命中 1 个模式即触发
            target_level=SensitivityLevel.L4,  # 升级为高敏感
            category="COMPOSITE_FINANCE_COMBO",  # 标签类别
            rule_id="COMP_003",
        ),
    ]

    def __init__(self, rules: list[CompositeRule] | None = None):
        """初始化复合规则引擎 / Initialize Composite Rule Engine.

        如果传入自定义规则列表则使用自定义规则，否则使用内置默认规则。
        传入的规则列表会被浅拷贝，避免外部修改影响引擎内部状态。

        Args:
            rules: 自定义复合规则列表；None 时使用 DEFAULT_RULES / Custom rules or None.
        """
        # 使用自定义规则（拷贝）或默认规则（拷贝）
        self.rules = list(rules) if rules else list(self.DEFAULT_RULES)

    def evaluate(
        self,
        record: dict[str, Any],
        field_results: dict[str, FieldClassificationResult],
    ) -> list[SecurityTag]:
        """评估单条记录是否命中复合规则 / Evaluate if Record Matches Composite Rules.

        执行步骤 / Execution Steps:
        1. 规范化记录中的所有字段名，构建 {规范化名: 原始名} 映射。
        2. 遍历每条复合规则，对每个 field_pattern 检查是否有字段匹配。
        3. 统计匹配的模式数，达到 min_matches 时生成 SecurityTag。
        4. 记录 Prometheus 指标和调试日志。

        Args:
            record: 原始记录字典（字段名 → 字段值） / Original record dictionary.
            field_results: 字段名到字段分类结果的映射（本方法中未直接使用，
                          但保留参数以便未来扩展基于字段结果的复合逻辑）。

        Returns:
            命中的 SecurityTag 列表 / List of matched SecurityTags.
        """
        # 初始化标签收集列表
        tags: list[SecurityTag] = []
        # 构建规范化字段名 → 原始字段名的映射字典
        # 例如 {"idcard": "id_card", "phonenumber": "phone_number"}
        norm_fields = {_normalize(name): name for name in record}

        # 遍历每条复合规则
        for rule in self.rules:
            matched = 0  # 当前规则已匹配的模式计数
            matched_names: list[str] = []  # 已匹配的原始字段名列表（用于日志）

            # 遍历规则中的每个字段模式
            for pattern in rule.field_patterns:
                # 编译正则表达式（忽略大小写，因为字段名已规范化为小写）
                compiled = re.compile(pattern, re.IGNORECASE)
                # 在所有规范化字段名中搜索匹配
                for norm_name, original_name in norm_fields.items():
                    if compiled.search(norm_name):
                        # 找到匹配：计数 +1，记录原始字段名
                        matched += 1
                        matched_names.append(original_name)
                        break  # 每个模式只需匹配一个字段即可，继续下一个模式
                # 提前退出优化：如果已达到 min_matches，无需继续检查剩余模式
                if matched >= rule.min_matches:
                    break

            # 判断是否满足最低匹配数阈值
            if matched >= rule.min_matches:
                # 生成复合规则安全标签
                tags.append(
                    SecurityTag(
                        level=rule.target_level,       # 规则指定的目标等级
                        category=rule.category,        # 规则类别标识
                        confidence=1.0,                # 复合规则命中置信度为 1.0（确定性规则）
                        source_engine="COMPOSITE",     # 来源引擎标识
                        rule_id=rule.rule_id,          # 规则唯一 ID
                        # L5 及以上等级标记需要人工复核
                        needs_human_review=rule.target_level.value >= SensitivityLevel.L5.value,
                    )
                )
                # 递增 Prometheus 指标：记录该复合规则被命中的次数
                CLASSIFICATION_COMPOSITE_HITS_TOTAL.labels(rule_id=rule.rule_id).inc()
                # 输出调试日志，包含规则 ID、类别和匹配的字段名列表
                logger.debug(
                    "composite_rule_hit",
                    extra={
                        "rule_id": rule.rule_id,
                        "category": rule.category,
                        "matched_fields": matched_names,
                    },
                )
        # 返回所有命中的复合规则标签
        return tags


def apply_composite_tags(
    record_result,
    composite_tags: list[SecurityTag],
):
    """将复合规则标签合并到记录结果中并升级最终等级 / Merge Composite Tags and Upgrade Level.

    执行逻辑：
    1. 如果没有复合标签，直接返回原结果（无修改）。
    2. 将复合标签追加到记录的 aggregated_tags 列表。
    3. 以 (level, category) 为键去重，避免重复标签。
    4. 重新计算 final_level = max(原等级, 所有复合标签等级)。
    5. 如果任何复合标签标记 needs_human_review，则记录也标记。

    Args:
        record_result: RecordClassificationResult 实例 / RecordClassificationResult instance.
        composite_tags: 复合规则产生的 SecurityTag 列表 / List of SecurityTags from composite rules.

    Returns:
        更新后的 RecordClassificationResult（原地修改并返回） / Updated RecordClassificationResult.
    """
    # 没有复合标签时直接返回，避免不必要的处理
    if not composite_tags:
        return record_result

    # 合并原有标签和复合标签
    new_tags = list(record_result.aggregated_tags) + composite_tags

    # 去重：以 (level.value, category) 为唯一键，保留首次出现的标签
    seen = set()        # 已见过的 (level, category) 组合
    unique_tags = []    # 去重后的标签列表
    for tag in new_tags:
        key = (tag.level.value, tag.category)  # 构造去重键
        if key in seen:
            continue  # 跳过重复标签
        seen.add(key)       # 标记为已见
        unique_tags.append(tag)  # 保留该标签

    # 重新计算最终等级：取原等级和所有复合标签等级中的最高值
    new_level = max_level(
        record_result.final_level,          # 原始最终等级
        *(t.level for t in composite_tags),  # 所有复合标签的等级
    )

    # 更新记录结果的聚合标签列表
    record_result.aggregated_tags = unique_tags
    # 更新记录结果的最终等级（可能被升级）
    record_result.final_level = new_level
    # 更新人工复核标记：原有标记 OR 任何复合标签的标记
    record_result.needs_human_review = record_result.needs_human_review or any(
        t.needs_human_review for t in composite_tags
    )
    # 返回更新后的记录结果
    return record_result
