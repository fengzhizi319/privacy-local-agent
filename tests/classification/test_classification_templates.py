"""内置合规模板分类测试 / Built-in Compliance Classification Template Tests.

中文说明：
验证内置合规模板的分类行为：
- JR/T 0197 金融行业标准：金融账户字段升级为 L4。
- GB/T 35273 个人信息安全规范：联系信息字段标记为 L3。
- GDPR 欧盟通用数据保护条例：特殊类别数据标记为 L4。
- 模板默认等级不覆盖已有内置规则。
- 请求级模板参数覆盖 profile 级配置。
- 模板使用指标计数。

English Description:
Tests for built-in compliance classification templates:
- JR/T 0197 (Finance): financial account fields upgraded to L4.
- GB/T 35273 (Personal Info Security): contact fields marked as L3.
- GDPR: special category data marked as L4.
- Template default level does not override existing built-in rules.
- Request-level template params override profile-level config.
- Template usage metric counting.
"""

import pytest

from privacy_local_agent.privacy.classification import ClassificationAPI
from privacy_local_agent.privacy.classification.classification_models import SensitivityLevel


@pytest.fixture
def api():
    """创建默认 ClassificationAPI 实例。"""
    return ClassificationAPI()


def test_jrt0197_finance_field(api):
    """测试 JR/T 0197 金融模板：银行卡字段升级为 L4。

    Test JR/T 0197 finance template: bank card field upgraded to L4.
    """
    result = api.classify_field(
        "bank_card",
        "6222021234567890123",
        params={"template": "jrt0197"},
    )
    assert result.final_level == SensitivityLevel.L4
    assert any(t.category == "FINANCE_ACCOUNT" for t in result.tags)


def test_gbt35273_email_field(api):
    """测试 GB/T 35273 模板：邮箱字段标记为个人联系信息 L3。

    Test GB/T 35273 template: email field marked as PII contact L3.
    """
    result = api.classify_field(
        "email",
        "user@example.com",
        params={"template": "gbt35273"},
    )
    assert result.final_level == SensitivityLevel.L3
    assert any(t.category == "PII_CONTACT_LOCATION" for t in result.tags)


def test_gdpr_special_category(api):
    """测试 GDPR 模板：生物识别字段标记为特殊类别 L4。

    Test GDPR template: biometric field marked as special category L4.
    """
    result = api.classify_field(
        "biometric_fingerprint",
        "...",
        params={"template": "gdpr"},
    )
    assert result.final_level == SensitivityLevel.L4
    assert any(t.category == "GDPR_SPECIAL_CATEGORY" for t in result.tags)


def test_template_default_level_does_not_override_existing_rule(api):
    """测试模板默认等级不覆盖已有内置规则。

    Test that template default level does not override existing built-in rules.
    """
    # id_card 已有内置规则 (L3)；模板不应降低其等级
    result = api.classify_field(
        "id_card",
        "110101199001011237",
        params={"template": "gbt35273"},
    )
    assert result.final_level == SensitivityLevel.L3


def test_template_request_overrides_profile(api):
    """测试请求级模板参数覆盖 profile 级配置。

    Test that request-level template params override profile-level config.
    """
    # profile 级模板可能为 gbt35273；请求使用 gdpr
    result = api.classify_field(
        "political_opinion",
        "democrat",
        params={"template": "gdpr"},
    )
    assert result.final_level == SensitivityLevel.L4


def test_template_metric():
    """测试模板使用指标计数正确递增。

    Test that template usage metric increments correctly.
    """
    from privacy_local_agent.classification_service import ClassificationService
    from privacy_local_agent.observability.metrics import CLASSIFICATION_TEMPLATES_TOTAL
    service = ClassificationService()
    before = CLASSIFICATION_TEMPLATES_TOTAL.labels(template="jrt0197")._value.get()
    service.classify_field("bank_card", "123", params={"template": "jrt0197"})
    after = CLASSIFICATION_TEMPLATES_TOTAL.labels(template="jrt0197")._value.get()
    assert after > before
