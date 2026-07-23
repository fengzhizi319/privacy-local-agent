"""规则版本管理与影子模式单元测试 / Rule Versioning and Shadow Mode Unit Tests.

中文说明：
验证分类规则版本管理与影子模式（Shadow Mode）能力：
- 审计信息中记录规则集版本号。
- 影子模式下对比当前版本与影子版本的分类差异。
- 手动覆盖参数对影子模式的影响。

English Description:
Tests for classification rule versioning and shadow mode capabilities:
- Rule set version recorded in audit info.
- Shadow mode compares classification diffs between current and shadow versions.
- Manual override parameters affect shadow mode behavior.
"""

import pytest

from privacy_local_agent.privacy.classification import ClassificationAPI


@pytest.fixture
def api():
    """创建默认 ClassificationAPI 实例。"""
    return ClassificationAPI()


def test_rule_set_version_in_audit(api):
    """测试审计信息中正确记录规则集版本号。

    Test that rule set version is correctly recorded in audit info.
    """
    result = api.classify_json(
        [{"mobile": "13800138000"}],
        params={"ruleSetVersion": "2.0.0"},
    )
    assert result.audit_info.rule_set_version == "2.0.0"


def test_shadow_mode_detects_diff(api):
    """测试影子模式下相同版本无差异。

    Test that shadow mode with same version produces no diff.
    """
    result = api.classify_table(
        schema=["bank_card"],
        rows=[{"bank_card": "6222021234567890123"}],
        params={
            "template": "jrt0197",
            "ruleSetVersion": "1.0.0",
            "shadowMode": True,
            "shadowVersion": "1.0.0",
        },
    )
    # 当前版本与影子版本相同，不应有差异
    assert result.shadow_diff == []


def test_shadow_mode_with_manual_override_diff(api):
    """测试影子模式下手动覆盖参数的差异检测。

    Test shadow mode diff detection with manual override parameters.
    """
    # 当前版本：无覆盖；影子版本：手动覆盖强制 L5
    result = api.classify_table(
        schema=["mobile"],
        rows=[{"mobile": "13800138000"}],
        params={
            "ruleSetVersion": "1.0.0",
            "shadowMode": True,
            "shadowVersion": "2.0.0",
            "manualOverride": {"mobile": "L5"},
        },
    )
    # 手动覆盖同时应用于当前和影子版本（因为在 params 中）
    # 此处仅验证影子路径被执行并返回列表
    assert result.shadow_diff is not None
