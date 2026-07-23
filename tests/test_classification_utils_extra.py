"""分类工具函数补充分支测试 / Classification Utils Extra Branch Tests.

中文说明：
补充 test_classification_zk.py 未覆盖的分支：
- redact(None)、hash_value(None/md5/不支持算法)、should_log_value(None/bool/其他类型)
- safe_log 自动脱敏、mask_record_values(None)、get_template_params(None/未知模板)

English Description:
Extra branch coverage for classification_utils pure helpers.
"""

from __future__ import annotations

import logging
from unittest.mock import Mock

import pytest

from privacy_local_agent.privacy.classification_utils import (
    get_template_params,
    hash_value,
    mask_record_values,
    redact,
    safe_log,
    should_log_value,
)


class TestRedactBranches:
    def test_none_returns_empty(self):
        assert redact(None) == ""


class TestHashValueBranches:
    def test_none_treated_as_empty(self):
        assert hash_value(None) == hash_value("")

    def test_md5(self):
        assert hash_value("abc", algorithm="md5") == "900150983cd24fb0d6963f7d28e17f72"

    def test_unsupported_algorithm(self):
        with pytest.raises(ValueError, match="unsupported hash algorithm"):
            hash_value("abc", algorithm="sha1")


class TestShouldLogValueBranches:
    def test_none(self):
        assert should_log_value(None) is True

    def test_bool(self):
        assert should_log_value(True) is True

    def test_float(self):
        assert should_log_value(1.5) is True

    def test_other_type(self):
        assert should_log_value(object()) is False


class TestSafeLog:
    def test_redacts_string_fields(self):
        logger = Mock()
        safe_log(logger, logging.INFO, "msg", secret="110101199001011237", count=3)
        logger.log.assert_called_once()
        args = logger.log.call_args[0]
        assert args[0] == logging.INFO
        fields = args[2]
        assert fields["secret"] == "11010119***"
        assert fields["count"] == 3


class TestMaskRecordValuesBranches:
    def test_none_returns_empty(self):
        assert mask_record_values(None) == {}


class TestGetTemplateParams:
    def test_none_returns_empty(self):
        assert get_template_params(None) == {}

    def test_known_template(self):
        params = get_template_params("gbt35273")
        assert params["default_level"] == "L3"

    def test_unknown_template(self):
        assert get_template_params("does-not-exist") == {}
