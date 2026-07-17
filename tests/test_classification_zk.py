"""Tests for Zero-Knowledge classification scanning."""

import logging

import pytest

from privacy_local_agent.privacy.classification import ClassificationAPI


@pytest.fixture
def api():
    return ClassificationAPI()
from privacy_local_agent.privacy.classification_zero_knowledge import (
    hash_value,
    mask_record_values,
    redact,
    should_log_value,
)


def test_redact_long_value():
    assert redact("110101199001011237") == "11010119***"


def test_redact_short_value():
    assert redact("abc") == "abc"


def test_hash_value():
    h1 = hash_value("sensitive")
    h2 = hash_value("sensitive")
    h3 = hash_value("other")
    assert h1 == h2
    assert h1 != h3
    assert len(h1) == 64


def test_should_log_value():
    assert should_log_value(123) is True
    assert should_log_value("short") is True
    assert should_log_value("x" * 20) is False


def test_mask_record_values():
    record = {"id_card": "110101199001011237", "mobile": "13800138000"}
    masked = mask_record_values(record)
    assert masked["id_card"].endswith("***")
    assert masked["mobile"].endswith("***")


def test_return_field_values_false():
    api = ClassificationAPI()
    result = api.classify_field(
        "id_card",
        "110101199001011237",
        params={"returnFieldValues": False},
    )
    assert result.field_value is None


def test_no_raw_value_in_logs(api, caplog):
    with caplog.at_level(logging.WARNING):
        api.classify_field("id_card", "110101199001011237")
    assert "110101199001011237" not in caplog.text
