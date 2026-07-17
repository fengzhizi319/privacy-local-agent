"""Tests for composite / context-aware classification rules."""

import pytest

from privacy_local_agent.privacy.classification import ClassificationAPI
from privacy_local_agent.privacy.classification_models import SensitivityLevel


@pytest.fixture
def api():
    return ClassificationAPI()


def test_composite_pii_combo_upgrades_to_l5(api):
    record = {
        "name": "张三",
        "id_card": "110101199001011237",
        "mobile": "13800138000",
    }
    result = api.classify_record(record)
    assert result.final_level == SensitivityLevel.L5
    assert any(t.category == "COMPOSITE_PII_COMBO" for t in result.aggregated_tags)


def test_composite_not_triggered_with_two_fields(api):
    record = {
        "name": "张三",
        "mobile": "13800138000",
    }
    result = api.classify_record(record)
    assert result.final_level.value < "L5"
    assert not any(t.source_engine == "COMPOSITE" for t in result.aggregated_tags)


def test_composite_medical_genomic_combo(api):
    record = {
        "diagnosis": "B21.1",
        "gene_marker": "BRCA1",
    }
    result = api.classify_record(record)
    assert result.final_level == SensitivityLevel.L5
    assert any(t.category == "COMPOSITE_MEDICAL_GENOMIC" for t in result.aggregated_tags)


def test_custom_composite_rule(api):
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
    record = {
        "name": "张三",
        "id_card": "110101199001011237",
        "mobile": "13800138000",
    }
    result = api.classify_record(record)
    composite_tags = [t for t in result.aggregated_tags if t.source_engine == "COMPOSITE"]
    assert composite_tags
    assert composite_tags[0].needs_human_review is True
