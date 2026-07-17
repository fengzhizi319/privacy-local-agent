"""Composite / context-aware rule engine for data classification.

复合规则引擎用于识别“单字段不敏感、多字段组合后敏感”的上下文场景。
它在单条记录的字段级分类完成后执行，根据字段名组合升级敏感度等级。
"""

import re
from typing import Any, Dict, List, Optional

from .classification_models import (
    CompositeRule,
    FieldClassificationResult,
    SecurityTag,
    SensitivityLevel,
    max_level,
)


def _normalize(name: str) -> str:
    """规范化字段名用于模式匹配。"""
    return str(name).lower().replace("_", "").replace(" ", "")


class CompositeRuleEngine:
    """复合规则引擎。

    维护一组复合规则，对单条记录及其字段分类结果进行后处理，
    当命中复合条件时返回额外的 SecurityTag 并升级最终等级。
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
        """初始化复合规则引擎。

        Args:
            rules: 自定义复合规则列表；None 时使用默认规则。
        """
        self.rules = list(rules) if rules else list(self.DEFAULT_RULES)

    def evaluate(
        self,
        record: Dict[str, Any],
        field_results: Dict[str, FieldClassificationResult],
    ) -> List[SecurityTag]:
        """评估单条记录是否命中复合规则。

        Args:
            record: 原始记录字典。
            field_results: 字段名到字段分类结果的映射。

        Returns:
            命中的 SecurityTag 列表。
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
        return tags


def apply_composite_tags(
    record_result,
    composite_tags: List[SecurityTag],
):
    """将复合规则标签合并到记录结果中并升级最终等级。

    Args:
        record_result: RecordClassificationResult 实例。
        composite_tags: 复合规则产生的 SecurityTag 列表。

    Returns:
        更新后的 RecordClassificationResult。
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
