"""Zero-Knowledge 分类扫描安全工具单元测试 / Zero-Knowledge Classification Scanning Security Utility Tests.

中文说明：
验证分类子系统的 Zero-Knowledge 安全工具：
- 值脱敏（redact）：长值截断 + 占位符。
- 值哈希（hash_value）：确定性 SHA-256 哈希。
- 日志安全判断（should_log_value）：短元数据可打印，长字符串禁止。
- 记录值批量脱敏（mask_record_values）。
- returnFieldValues=False 时不返回原始值。
- 日志中不泄露原始敏感值。

English Description:
Tests for Zero-Knowledge security utilities in the classification subsystem:
- Value redaction: long values truncated with placeholder.
- Value hashing: deterministic SHA-256 hash.
- Log safety check (should_log_value): short metadata printable, long strings forbidden.
- Batch record value masking (mask_record_values).
- returnFieldValues=False suppresses raw value in results.
- No raw sensitive values leaked in logs.
"""

import logging

import pytest

from privacy_local_agent.privacy.classification import ClassificationAPI


@pytest.fixture
def api():
    """创建默认 ClassificationAPI 实例。"""
    return ClassificationAPI()
from privacy_local_agent.privacy.classification_utils import (
    hash_value,
    mask_record_values,
    redact,
    should_log_value,
)


def test_redact_long_value():
    """测试长值脱敏：保留前 8 位 + 占位符。"""
    assert redact("110101199001011237") == "11010119***"


def test_redact_short_value():
    """测试短值不脱敏：长度 <= max_len 原样返回。"""
    assert redact("abc") == "abc"


def test_hash_value():
    """测试值哈希的确定性和唯一性。"""
    h1 = hash_value("sensitive")
    h2 = hash_value("sensitive")
    h3 = hash_value("other")
    # 相同输入产生相同哈希
    assert h1 == h2
    # 不同输入产生不同哈希
    assert h1 != h3
    # SHA-256 输出为 64 位十六进制字符串
    assert len(h1) == 64


def test_should_log_value():
    """测试日志安全判断：数字/短字符串可打印，长字符串禁止。"""
    assert should_log_value(123) is True
    assert should_log_value("short") is True
    assert should_log_value("x" * 20) is False


def test_mask_record_values():
    """测试记录值批量脱敏。"""
    record = {"id_card": "110101199001011237", "mobile": "13800138000"}
    masked = mask_record_values(record)
    assert masked["id_card"].endswith("***")
    assert masked["mobile"].endswith("***")


def test_return_field_values_false():
    """测试 returnFieldValues=False 时不返回原始值。"""
    api = ClassificationAPI()
    result = api.classify_field(
        "id_card",
        "110101199001011237",
        params={"returnFieldValues": False},
    )
    assert result.field_value is None


def test_no_raw_value_in_logs(api, caplog):
    """测试日志中不泄露原始敏感值。"""
    with caplog.at_level(logging.WARNING):
        api.classify_field("id_card", "110101199001011237")
    assert "110101199001011237" not in caplog.text
