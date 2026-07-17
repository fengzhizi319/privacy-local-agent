"""Tests for built-in compliance classification templates."""

import pytest

from privacy_local_agent.privacy.classification import ClassificationAPI
from privacy_local_agent.privacy.classification_models import SensitivityLevel


@pytest.fixture
def api():
    return ClassificationAPI()


def test_jrt0197_finance_field(api):
    result = api.classify_field(
        "bank_card",
        "6222021234567890123",
        params={"template": "jrt0197"},
    )
    assert result.final_level == SensitivityLevel.L4
    assert any(t.category == "FINANCE_ACCOUNT" for t in result.tags)


def test_gbt35273_email_field(api):
    result = api.classify_field(
        "email",
        "user@example.com",
        params={"template": "gbt35273"},
    )
    assert result.final_level == SensitivityLevel.L3
    assert any(t.category == "PII_CONTACT_LOCATION" for t in result.tags)


def test_gdpr_special_category(api):
    result = api.classify_field(
        "biometric_fingerprint",
        "...",
        params={"template": "gdpr"},
    )
    assert result.final_level == SensitivityLevel.L4
    assert any(t.category == "GDPR_SPECIAL_CATEGORY" for t in result.tags)


def test_template_default_level_does_not_override_existing_rule(api):
    # id_card already has a built-in rule; template should not lower it
    result = api.classify_field(
        "id_card",
        "110101199001011237",
        params={"template": "gbt35273"},
    )
    assert result.final_level == SensitivityLevel.L3


def test_template_request_overrides_profile(api):
    # profile-level template could be gbt35273; request uses gdpr
    result = api.classify_field(
        "political_opinion",
        "democrat",
        params={"template": "gdpr"},
    )
    assert result.final_level == SensitivityLevel.L4


def test_template_metric():
    from privacy_local_agent.classification_service import ClassificationService
    from privacy_local_agent.observability.metrics import CLASSIFICATION_TEMPLATES_TOTAL
    service = ClassificationService()
    before = CLASSIFICATION_TEMPLATES_TOTAL.labels(template="jrt0197")._value.get()
    service.classify_field("bank_card", "123", params={"template": "jrt0197"})
    after = CLASSIFICATION_TEMPLATES_TOTAL.labels(template="jrt0197")._value.get()
    assert after > before
