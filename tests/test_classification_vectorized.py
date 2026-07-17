"""Tests for the optional pandas-based VectorizedRuleEngine."""

import time

import pandas as pd
import pytest

from privacy_local_agent.privacy.classification import ClassificationAPI
from privacy_local_agent.privacy.classification_models import ClassificationParams, SensitivityLevel
from privacy_local_agent.privacy.classification_rule_engine import DefaultRuleEngine
from privacy_local_agent.privacy.classification_vectorized import VectorizedRuleEngine


@pytest.fixture
def params():
    return ClassificationParams()


def test_vectorized_scalar_matches_default(params):
    """VectorizedRuleEngine.evaluate 标量接口与 DefaultRuleEngine 语义一致。"""
    default_engine = DefaultRuleEngine()
    vector_engine = VectorizedRuleEngine()

    test_values = [
        ("id_card", "110101199001011237"),
        ("id_card", "110101199001011234"),
        ("mobile", "13800138000"),
        ("diagnosis", "B21.1"),
        ("brca1_status", "positive"),
        ("public_report", "annual summary"),
    ]
    for field_name, value in test_values:
        default_tags = default_engine.evaluate(field_name, value, params)
        vector_tags = vector_engine.evaluate(field_name, value, params)
        assert sorted(str(t) for t in default_tags) == sorted(str(t) for t in vector_tags)


def test_vectorized_series_matches_default(params):
    """VectorizedRuleEngine.evaluate_series 批量结果与逐元素 DefaultRuleEngine 一致。"""
    default_engine = DefaultRuleEngine()
    vector_engine = VectorizedRuleEngine()

    test_cases = [
        ("id_card", ["110101199001011237", "110101199001011234", ""]),
        ("mobile", ["13800138000", "12800138000", "13800138001"]),
        ("diagnosis", ["B21.1", "F25", "C78.0", "J18.9"]),
        ("file_content", ["BAM\x01", "##fileformat=VCFv4.2", "@SQ SN:chr1 LN:1000", "plain text"]),
        ("sequence", ["ATCG" * 20, "ATCG", ""]),
    ]
    for field_name, values in test_cases:
        series = pd.Series(values)
        vector_tags_per_row = vector_engine.evaluate_series(field_name, series, params)
        assert len(vector_tags_per_row) == len(values)
        for i, value in enumerate(values):
            default_tags = default_engine.evaluate(field_name, value, params)
            vector_tags = vector_tags_per_row[i]
            assert sorted(str(t) for t in vector_tags) == sorted(
                str(t) for t in default_tags
            ), f"mismatch for {field_name}={value!r}"


def test_classify_table_vectorized_consistency():
    """ClassificationAPI(use_vectorized=True) 与普通 API 对同一张表输出一致。"""
    schema = ["id_card", "mobile", "diagnosis", "brca1_status", "turnover_rate"]
    rows = [
        {"id_card": "110101199001011237", "mobile": "13800138000", "diagnosis": "B21.1", "brca1_status": "positive", "turnover_rate": "0.85"},
        {"id_card": "110101199001011234", "mobile": "12800138000", "diagnosis": "J18.9", "brca1_status": "negative", "turnover_rate": "0.12"},
        {"id_card": "", "mobile": "", "diagnosis": "", "brca1_status": "", "turnover_rate": ""},
    ]

    api_default = ClassificationAPI()
    api_vector = ClassificationAPI(use_vectorized=True)

    result_default = api_default.classify_table(schema, rows)
    result_vector = api_vector.classify_table(schema, rows)

    assert result_default.final_level == result_vector.final_level
    assert {str(t) for t in result_default.aggregated_tags} == {
        str(t) for t in result_vector.aggregated_tags
    }

    for r_default, r_vector in zip(result_default.record_results, result_vector.record_results):
        assert r_default.final_level == r_vector.final_level
        assert r_default.needs_human_review == r_vector.needs_human_review
        assert {str(t) for t in r_default.aggregated_tags} == {
            str(t) for t in r_vector.aggregated_tags
        }
        for field_name in schema:
            fr_default = r_default.field_results[field_name]
            fr_vector = r_vector.field_results[field_name]
            assert fr_default.final_level == fr_vector.final_level
            assert {str(t) for t in fr_default.tags} == {str(t) for t in fr_vector.tags}


def test_vectorized_faster_than_scalar():
    """向量化表分类应明显快于纯标量行级循环。"""
    pytest.importorskip("pandas")

    schema = ["id_card", "mobile", "diagnosis"]
    base_row = {"id_card": "110101199001011237", "mobile": "13800138000", "diagnosis": "B21.1"}
    warmup_rows = [base_row for _ in range(100)]
    rows = [base_row for _ in range(5000)]

    api_scalar = ClassificationAPI()
    api_vector = ClassificationAPI(use_vectorized=True)

    # 预热，消除初始化/缓存差异
    api_scalar.classify_table(schema, warmup_rows)
    api_vector.classify_table(schema, warmup_rows)

    start = time.perf_counter()
    api_scalar.classify_table(schema, rows)
    scalar_time = time.perf_counter() - start

    start = time.perf_counter()
    api_vector.classify_table(schema, rows)
    vector_time = time.perf_counter() - start

    # 向量化应快于标量；阈值仅要求严格小于，避免 CI 噪声导致 flaky
    assert vector_time < scalar_time, (
        f"vectorized {vector_time:.3f}s should be faster than scalar {scalar_time:.3f}s"
    )


def test_use_vectorized_falls_back_when_pandas_missing(monkeypatch):
    """未安装 pandas 时 use_vectorized=True 应回退到 DefaultRuleEngine。"""
    import sys

    # 模拟 pandas 不可用：让 classification_vectorized 的 import pandas 失败
    monkeypatch.setitem(sys.modules, "pandas", None)
    api = ClassificationAPI(use_vectorized=True)
    assert isinstance(api.rule_engine, DefaultRuleEngine)
