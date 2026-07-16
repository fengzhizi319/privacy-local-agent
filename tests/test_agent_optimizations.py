import unittest
from privacy_local_agent.privacy.profile import get_resolver
from privacy_local_agent.service import PrivacyService
from privacy_local_agent.privacy.classification import SensitivityLevel


class TestAgentOptimizations(unittest.TestCase):

    def test_resolver_caching(self):
        # Verify that get_resolver returns the same instance for the same path
        r1 = get_resolver("nonexistent-profile.yaml")
        r2 = get_resolver("nonexistent-profile.yaml")
        self.assertIs(r1, r2)

    def test_privacy_service_unified_classification(self):
        # Verify that the unified PrivacyService can perform classification
        service = PrivacyService()
        self.assertIsNotNone(service.classification_api)
        
        # Test classify_field wrapper
        res_field = service.classify_field("mobile", "13800138000")
        self.assertEqual(res_field["finalLevel"], "L3")
        
        # Test classify_record wrapper
        res_record = service.classify_record({"mobile": "13800138000"})
        self.assertEqual(res_record["finalLevel"], "L3")

        # Test classify_table wrapper
        res_table = service.classify_table(["mobile"], [{"mobile": "13800138000"}])
        self.assertEqual(res_table["finalLevel"], "L3")
