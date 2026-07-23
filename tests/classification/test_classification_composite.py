"""复合/上下文感知规则引擎单元测试 / Composite / Context-Aware Rule Engine Unit Tests.

中文说明：
验证复合规则引擎的核心能力：
- 多字段组合升级敏感度（PII 三字段组合 → L5）。
- 未达最小匹配数时不触发复合规则。
- 医疗基因组复合规则（诊断+基因标记 → L5）。
- 自定义复合规则注入。
- L5 复合标签自动标记为需人工复核。

English Description:
Tests for composite rule engine capabilities:
- Multi-field combination upgrades sensitivity (PII 3-field combo → L5).
- Composite rules not triggered below min_matches threshold.
- Medical-genomic composite rule (diagnosis + gene marker → L5).
- Custom composite rule injection.
- L5 composite tags automatically flagged for human review.
"""

import pytest

from privacy_local_agent.privacy.classification import ClassificationAPI
from privacy_local_agent.privacy.classification.classification_models import SensitivityLevel


@pytest.fixture
def api():
    """创建默认 ClassificationAPI 实例。"""
    return ClassificationAPI()


def test_composite_pii_combo_upgrades_to_l5(api):
    """测试 PII 三字段组合（姓名+身份证+手机号）升级到 L5。

    Test that PII 3-field combination (name + id_card + mobile) upgrades to L5.
    """
    record = {
        "name": "张三",
        "id_card": "110101199001011237",
        "mobile": "13800138000",
    }
    result = api.classify_record(record)
    assert result.final_level == SensitivityLevel.L5
    assert any(t.category == "COMPOSITE_PII_COMBO" for t in result.aggregated_tags)


def test_composite_not_triggered_with_two_fields(api):
    """测试仅两个字段时不触发 PII 复合规则（需 3 个字段）。

    Test that composite rule is not triggered with only 2 fields (requires 3).
    """
    record = {
        "name": "张三",
        "mobile": "13800138000",
    }
    result = api.classify_record(record)
    assert result.final_level.value < "L5"
    assert not any(t.source_engine == "COMPOSITE" for t in result.aggregated_tags)


def test_composite_medical_genomic_combo(api):
    """测试医疗基因组复合规则（诊断+基因标记 → L5）。

    Test medical-genomic composite rule (diagnosis + gene marker → L5).
    """
    record = {
        "diagnosis": "B21.1",
        "gene_marker": "BRCA1",
    }
    result = api.classify_record(record)
    assert result.final_level == SensitivityLevel.L5
    assert any(t.category == "COMPOSITE_MEDICAL_GENOMIC" for t in result.aggregated_tags)


def test_custom_composite_rule(api):
    """测试通过请求参数注入自定义复合规则。

    Test injecting custom composite rules via request parameters.
    """
    record = {
        "user_name": "张三",
        "user_email": "zhangsan@example.com",
    }
    params = {
        "compositeRules": [
            {
                "name": "name_email_combo",
                "fieldPatterns": [r"username", r"useremail"],
                "minMatches": 2,
                "targetLevel": "L4",
                "category": "COMPOSITE_NAME_EMAIL",
                "ruleId": "COMP_TEST_001",
            }
        ]
    }
    result = api.classify_record(record, params=params)
    assert result.final_level == SensitivityLevel.L4
    assert any(t.category == "COMPOSITE_NAME_EMAIL" for t in result.aggregated_tags)


def test_composite_tag_needs_review_when_l5(api):
    """测试 L5 复合标签自动标记为需人工复核。

    Test that L5 composite tags are automatically flagged for human review.
    """
    record = {
        "name": "张三",
        "id_card": "110101199001011237",
        "mobile": "13800138000",
    }
    result = api.classify_record(record)
    composite_tags = [t for t in result.aggregated_tags if t.source_engine == "COMPOSITE"]
    assert composite_tags
    assert composite_tags[0].needs_human_review is True
