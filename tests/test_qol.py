"""查询混淆模块单元测试。"""

from __future__ import annotations

import pytest
from prometheus_client import REGISTRY

from privacy_local_agent.privacy.qol import (
    MEDICAL_DUMMY,
    ObfuscationDomain,
    ObfuscationStrategy,
    obfuscate_query,
    obfuscate_query_batch,
)


class TestObfuscateQuery:
    """单条查询混淆测试。"""

    def test_obfuscate_query_includes_real_query(self) -> None:
        result = obfuscate_query("真实查询", num_dummies=3, domain="medical", seed=42)
        assert "真实查询" in result
        assert len(result) == 4

    def test_obfuscate_query_uses_domain_pool(self) -> None:
        result = obfuscate_query("真实查询", num_dummies=2, domain="generic", seed=42)
        assert "真实查询" in result
        assert len(result) == 3

    def test_obfuscate_query_custom_pool(self) -> None:
        custom = ["自定义虚假查询1", "自定义虚假查询2"]
        result = obfuscate_query(
            "真实查询", num_dummies=2, domain="medical", medical_pool=custom, seed=42
        )
        assert "真实查询" in result
        # 虚假查询应全部来自自定义 pool
        for q in result:
            if q != "真实查询":
                assert q in custom

    def test_obfuscate_query_records_metric(self) -> None:
        before = REGISTRY.get_sample_value(
            "privacy_qol_operations_total", {"domain": "medical"}
        ) or 0.0
        obfuscate_query("真实查询", num_dummies=1, domain="medical", seed=42)
        after = REGISTRY.get_sample_value(
            "privacy_qol_operations_total", {"domain": "medical"}
        )
        assert after == before + 1


class TestObfuscateQueryBatch:
    """批量查询混淆测试。"""

    def test_obfuscate_query_batch(self) -> None:
        results = obfuscate_query_batch(
            ["查询1", "查询2"], num_dummies=2, domain="medical", seed=42
        )
        assert len(results) == 2
        for r, q in zip(results, ["查询1", "查询2"]):
            assert q in r
            assert len(r) == 3

    def test_obfuscate_query_batch_empty(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            obfuscate_query_batch([])

    def test_obfuscate_query_batch_records_metric(self) -> None:
        before = REGISTRY.get_sample_value(
            "privacy_qol_operations_total", {"domain": "generic"}
        ) or 0.0
        obfuscate_query_batch(["查询1"], num_dummies=1, domain="generic", seed=42)
        after = REGISTRY.get_sample_value(
            "privacy_qol_operations_total", {"domain": "generic"}
        )
        assert after == before + 1


class TestObfuscationDomainEnum:
    """混淆领域枚举测试。"""

    def test_obfuscation_domain_enum_values(self) -> None:
        assert ObfuscationDomain.MEDICAL == "medical"
        assert ObfuscationDomain.GENERIC == "generic"


class TestObfuscationStrategyEnum:
    """混淆策略枚举测试。"""

    def test_obfuscation_strategy_enum_values(self) -> None:
        assert ObfuscationStrategy.SLOT_FILLING == "slot_filling"
        assert ObfuscationStrategy.LENGTH_SIMILARITY == "length_similarity"
        assert ObfuscationStrategy.HYBRID == "hybrid"


class TestInputValidationQoL:
    """输入校验测试。"""

    def test_obfuscate_query_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="query must not be empty"):
            obfuscate_query("")

    def test_obfuscate_query_whitespace_only_raises(self) -> None:
        with pytest.raises(ValueError, match="query must not be empty"):
            obfuscate_query("   ")

    def test_obfuscate_query_invalid_num_dummies_raises(self) -> None:
        with pytest.raises(ValueError, match="num_dummies must be at least 1"):
            obfuscate_query("测试查询", num_dummies=0)

    def test_obfuscate_query_invalid_domain_raises(self) -> None:
        with pytest.raises(ValueError, match="domain must be"):
            obfuscate_query("测试查询", domain="invalid")

    def test_obfuscate_query_batch_non_list_raises(self) -> None:
        with pytest.raises(ValueError, match="queries must be a list"):
            obfuscate_query_batch("not a list")  # type: ignore
