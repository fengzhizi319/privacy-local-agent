"""Agent 优化特性单元测试 / Agent Optimization Feature Unit Tests.

中文说明：
验证 privacy-local-agent 的核心优化特性：
- ParameterResolver 缓存复用：相同路径的解析器实例应复用，避免重复解析 YAML。
- PrivacyService 统一分类接口：验证服务层封装的 classify_field/record/table 方法。

English Description:
Tests for core optimization features of privacy-local-agent:
- ParameterResolver caching: same path should reuse resolver instance.
- PrivacyService unified classification interface: verify classify_field/record/table wrappers.
"""

import unittest
from privacy_local_agent.privacy.profile import get_resolver
from privacy_local_agent.service import PrivacyService
from privacy_local_agent.privacy.classification import SensitivityLevel


class TestAgentOptimizations(unittest.TestCase):
    """Agent 优化特性测试集 / Agent Optimization Feature Test Suite.

    中文说明：
    覆盖参数解析器缓存机制和 PrivacyService 统一分类接口的正确性验证。

    English Description:
    Covers parameter resolver caching mechanism and PrivacyService
    unified classification interface correctness.
    """

    def test_resolver_caching(self):
        """验证 get_resolver 对相同路径返回同一实例（缓存复用）。

        Verify that get_resolver returns the same instance for the same path,
        avoiding redundant YAML parsing overhead.
        """
        r1 = get_resolver("nonexistent-profile.yaml")
        r2 = get_resolver("nonexistent-profile.yaml")
        self.assertIs(r1, r2)

    def test_privacy_service_unified_classification(self):
        """验证 PrivacyService 统一分类接口的字段/记录/表级分类能力。

        Verify that the unified PrivacyService can perform field/record/table
        classification through its wrapper methods.
        """
        service = PrivacyService()
        self.assertIsNotNone(service.classification_api)

        # 测试字段级分类封装：手机号应命中 PII_MOBILE，等级 L3
        res_field = service.classify_field("mobile", "13800138000")
        self.assertEqual(res_field["finalLevel"], "L3")

        # 测试记录级分类封装：记录中最高等级为 L3
        res_record = service.classify_record({"mobile": "13800138000"})
        self.assertEqual(res_record["finalLevel"], "L3")

        # 测试表级分类封装：表中所有记录聚合后最高等级为 L3
        res_table = service.classify_table(["mobile"], [{"mobile": "13800138000"}])
        self.assertEqual(res_table["finalLevel"], "L3")
