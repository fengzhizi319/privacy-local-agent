"""复合/上下文感知规则引擎 / Composite / Context-Aware Rule Engine.

中文说明：
复合规则引擎用于识别“单字段不敏感、多字段组合后敏感”的上下文场景。
它在单条记录的字段级分类完成后执行，根据字段名组合升级敏感度等级。

English Description:
Composite rule engine identifies context scenarios where "individual fields are not sensitive,
but become sensitive when combined". It executes after field-level classification of a single
record and upgrades sensitivity levels based on field name combinations.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from ...observability.logging_config import get_logger
from ...observability.metrics import CLASSIFICATION_COMPOSITE_HITS_TOTAL
from .classification_models import (
    CompositeRule,
    FieldClassificationResult,
    SecurityTag,
    SensitivityLevel,
    max_level,
)

# Module-level structured logger for composite rule events
logger = get_logger(__name__)


def _normalize(name: str) -> str:
    """规范化字段名用于模式匹配 / Normalize Field Name for Pattern Matching.

    Args:
        name: 原始字段名 / Original field name.

    Returns:
        规范化后的字段名 / Normalized field name.
    """
    return str(name).lower().replace("_", "").replace(" ", "")


class CompositeRuleEngine:
    """复合规则引擎 / Composite Rule Engine.

    中文说明：
    维护一组复合规则，对单条记录及其字段分类结果进行后处理，
    当命中复合条件时返回额外的 SecurityTag 并升级最终等级。

    English Description:
    Maintains a set of composite rules and post-processes a single record and its
    field classification results. Returns additional SecurityTags and upgrades the
    final level when composite conditions are met.

    Attributes:
        rules: 复合规则列表 / List of composite rules.
    """

    DEFAULT_RULES: List[CompositeRule] = [
        CompositeRule(
            name="高敏感个人信息组合",
            field_patterns=[r"^name$", r"id_card|idcard|identity", r"mobile|phone|cell"],
            min_matches=3,
            target_level=SensitivityLevel.L5,
            category="COMPOSITE_PII_COMBO",
            rule_id="COMP_001",
        ),
        CompositeRule(
            name="医疗基因组合",
            field_patterns=[r"diagnosis|disease|illness", r"gene|genomic|mutation|brca|tp53|rs\d+"],
            min_matches=2,
            target_level=SensitivityLevel.L5,
            category="COMPOSITE_MEDICAL_GENOMIC",
            rule_id="COMP_002",
        ),
        CompositeRule(
            name="金融账户组合",
            field_patterns=[r"bank_card|bankcard|card_no|account|credit|transaction"],
            min_matches=1,
            target_level=SensitivityLevel.L4,
            category="COMPOSITE_FINANCE_COMBO",
            rule_id="COMP_003",
        ),
    ]

    def __init__(self, rules: Optional[List[CompositeRule]] = None):
        """初始化复合规则引擎 / Initialize Composite Rule Engine.

        Args:
            rules: 自定义复合规则列表 / Custom composite rule list (None uses default rules).
        """
        self.rules = list(rules) if rules else list(self.DEFAULT_RULES)

    def evaluate(
        self,
        record: Dict[str, Any],
        field_results: Dict[str, FieldClassificationResult],
    ) -> List[SecurityTag]:
        """评估单条记录是否命中复合规则 / Evaluate if Record Matches Composite Rules.

        执行步骤 / Execution Steps:
        1. 规范化记录中的所有字段名。
           (Normalize all field names in the record)
        2. 对每条复合规则，检查字段模式匹配数。
           (For each composite rule, check field pattern match count)
        3. 若匹配数达到 min_matches，生成对应的 SecurityTag。
           (If match count reaches min_matches, generate corresponding SecurityTag)

        Args:
            record: 原始记录字典 / Original record dictionary.
            field_results: 字段名到字段分类结果的映射 / Field name to classification result mapping.

        Returns:
            命中的 SecurityTag 列表 / List of matched SecurityTags.
        """
        tags: List[SecurityTag] = []
        norm_fields = {_normalize(name): name for name in record.keys()}

        for rule in self.rules:
            matched = 0
            matched_names: List[str] = []
            for pattern in rule.field_patterns:
                compiled = re.compile(pattern, re.IGNORECASE)
                for norm_name, original_name in norm_fields.items():
                    if compiled.search(norm_name):
                        matched += 1
                        matched_names.append(original_name)
                        break
                if matched >= rule.min_matches:
                    break

            if matched >= rule.min_matches:
                tags.append(
                    SecurityTag(
                        level=rule.target_level,
                        category=rule.category,
                        confidence=1.0,
                        source_engine="COMPOSITE",
                        rule_id=rule.rule_id,
                        needs_human_review=rule.target_level.value >= SensitivityLevel.L5.value,
                    )
                )
                # Record composite rule hit metrics
                CLASSIFICATION_COMPOSITE_HITS_TOTAL.labels(rule_id=rule.rule_id).inc()
                logger.debug(
                    "composite_rule_hit",
                    extra={
                        "rule_id": rule.rule_id,
                        "category": rule.category,
                        "matched_fields": matched_names,
                    },
                )
        return tags


def apply_composite_tags(
    record_result,
    composite_tags: List[SecurityTag],
):
    """将复合规则标签合并到记录结果中并升级最终等级 / Merge Composite Tags and Upgrade Level.

    中文说明：将复合规则产生的标签合并到记录结果中，并升级最终敏感度等级。
    English Description: Merges composite rule tags into record result and upgrades final sensitivity level.

    Args:
        record_result: RecordClassificationResult 实例 / RecordClassificationResult instance.
        composite_tags: 复合规则产生的 SecurityTag 列表 / List of SecurityTags from composite rules.

    Returns:
        更新后的 RecordClassificationResult / Updated RecordClassificationResult.
    """
    if not composite_tags:
        return record_result

    new_tags = list(record_result.aggregated_tags) + composite_tags
    # 去重：以 (level, category) 为键
    seen = set()
    unique_tags = []
    for tag in new_tags:
        key = (tag.level.value, tag.category)
        if key in seen:
            continue
        seen.add(key)
        unique_tags.append(tag)

    new_level = max_level(
        record_result.final_level,
        *(t.level for t in composite_tags),
    )
    record_result.aggregated_tags = unique_tags
    record_result.final_level = new_level
    record_result.needs_human_review = record_result.needs_human_review or any(
        t.needs_human_review for t in composite_tags
    )
    return record_result
