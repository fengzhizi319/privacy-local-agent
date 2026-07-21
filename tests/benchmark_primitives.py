"""Performance benchmarks for privacy primitives.

使用 pytest-benchmark 测量核心隐私算子的吞吐量与延迟。
运行方式: pytest tests/benchmark_primitives.py --benchmark-only
"""

import numpy as np
import pytest

from privacy_local_agent.privacy.dp import DPApi
from privacy_local_agent.privacy.kano import KAnonApi
from privacy_local_agent.privacy.masking import MaskingApi
from privacy_local_agent.privacy.qol import QolApi


@pytest.fixture
def dp_api():
    return DPApi()


@pytest.fixture
def masking_api():
    return MaskingApi()


@pytest.fixture
def kano_api():
    return KAnonApi()


@pytest.fixture
def qol_api():
    return QolApi()


# ── DP Benchmarks ──────────────────────────────────────────────


class TestDPBenchmarks:
    """Differential privacy operation benchmarks."""

    def test_dp_count(self, dp_api, benchmark):
        """Benchmark DP count with 10k values."""
        values = np.random.default_rng(42).normal(50, 10, size=10000)
        result = benchmark(dp_api.count, values, epsilon=1.0)
        assert result is not None

    def test_dp_sum(self, dp_api, benchmark):
        """Benchmark DP sum with clipping."""
        values = np.random.default_rng(42).normal(50, 10, size=10000)
        result = benchmark(
            dp_api.sum, values, epsilon=1.0, clip_lower=0.0, clip_upper=100.0
        )
        assert result is not None

    def test_dp_mean(self, dp_api, benchmark):
        """Benchmark DP mean."""
        values = np.random.default_rng(42).normal(50, 10, size=10000)
        result = benchmark(
            dp_api.mean, values, epsilon=1.0, clip_lower=0.0, clip_upper=100.0
        )
        assert result is not None

    def test_dp_histogram(self, dp_api, benchmark):
        """Benchmark DP histogram with 5 categories."""
        rng = np.random.default_rng(42)
        categories = ["A", "B", "C", "D", "E"]
        values = rng.choice(categories, size=10000).tolist()
        result = benchmark(dp_api.histogram, values, categories=categories, epsilon=1.0)
        assert result is not None

    def test_dp_vector_sum(self, dp_api, benchmark):
        """Benchmark DP vector sum with 128-dim vectors."""
        rng = np.random.default_rng(42)
        vectors = rng.normal(0, 1, size=(1000, 128))
        result = benchmark(dp_api.vector_sum, vectors, max_norm=1.0, epsilon=1.0)
        assert result is not None


# ── Masking Benchmarks ─────────────────────────────────────────


class TestMaskingBenchmarks:
    """Data masking operation benchmarks."""

    def test_mask_mobile(self, masking_api, benchmark):
        """Benchmark single mobile masking."""
        result = benchmark(masking_api.mask_value, "mobile", "13812345678", "")
        assert "****" in result

    def test_mask_id_card(self, masking_api, benchmark):
        """Benchmark single ID card masking."""
        result = benchmark(masking_api.mask_value, "id_card", "110105199001011234", "")
        assert "********" in result

    def test_mask_batch_100(self, masking_api, benchmark):
        """Benchmark batch masking of 100 records."""
        field_names = ["mobile", "id_card", "name", "email", "address"] * 20
        values = [
            "13812345678", "110105199001011234", "张三丰",
            "test@example.com", "北京市朝阳区建国路88号",
        ] * 20
        result = benchmark(masking_api.mask_batch, field_names, values, "")
        assert len(result) == 100

    def test_mask_record(self, masking_api, benchmark):
        """Benchmark record masking with 10 fields."""
        record = {
            "mobile": "13812345678",
            "id_card": "110105199001011234",
            "name": "张三丰",
            "email": "test@example.com",
            "address": "北京市朝阳区建国路88号",
            "bank_card": "6222021234567890123",
            "phone": "13998765432",
            "user_name": "李四",
            "mail": "user@domain.org",
            "addr": "上海市浦东新区陆家嘴环路1000号",
        }
        result = benchmark(masking_api.mask_record, record, "")
        assert len(result) == 10


# ── K-Anonymity Benchmarks ─────────────────────────────────────


class TestKAnonBenchmarks:
    """K-anonymity operation benchmarks."""

    def test_kano_table_small(self, kano_api, benchmark):
        """Benchmark K-anonymity on 100-row table."""
        rng = np.random.default_rng(42)
        rows = [
            {
                "age": int(rng.integers(20, 70)),
                "zipcode": f"{rng.integers(100000, 999999)}",
                "gender": rng.choice(["M", "F"]),
            }
            for _ in range(100)
        ]
        result = benchmark(
            kano_api.k_anonymize_table, rows, ["age", "zipcode", "gender"], k=5, max_depth=5
        )
        assert result is not None


# ── QoL Benchmarks ─────────────────────────────────────────────


class TestQolBenchmarks:
    """Query obfuscation benchmarks."""

    def test_obfuscate_single(self, qol_api, benchmark):
        """Benchmark single query obfuscation."""
        result = benchmark(
            qol_api.obfuscate_query,
            "糖尿病用药指南",
            num_dummies=3,
            domain="medical",
        )
        assert len(result) >= 1

    def test_obfuscate_batch_10(self, qol_api, benchmark):
        """Benchmark batch obfuscation of 10 queries."""
        queries = [
            "糖尿病用药", "高血压治疗", "感冒药推荐",
            "抗生素使用", "疫苗接种", "体检项目",
            "中医调理", "营养补充", "康复训练", "心理咨询",
        ]
        result = benchmark(
            qol_api.obfuscate_query_batch,
            queries,
            num_dummies=3,
            domain="medical",
        )
        assert len(result) == 10
