"""数据集级 K-匿名内部辅助函数与回退路径测试 / kano_table Internal Unit Tests.

中文说明：
补充 test_kano_table.py 未覆盖的内部纯函数与 pandas 缺失时的纯 Python 回退路径：
- _is_numeric / _span / _choose_dimension / _median_split / _generalize
- KAnonymityResult.to_arrow 的 DataFrame / 标量分支
- k_anonymize_table / k_anonymize_dataframe 在 pandas 不可用时的回退实现

English Description:
Covers kano_table internal helpers and the pure-Python fallback used when pandas
is unavailable, plus extra to_arrow branches.
"""

from __future__ import annotations

import sys

import pytest

from privacy_local_agent.privacy import kano_table as kt


class TestIsNumeric:
    def test_int_float(self):
        assert kt._is_numeric(1)
        assert kt._is_numeric(1.5)

    def test_bool_excluded(self):
        assert not kt._is_numeric(True)

    def test_non_numeric(self):
        assert not kt._is_numeric("x")
        assert not kt._is_numeric(None)


class TestSpan:
    def test_empty(self):
        assert kt._span([], "age") == 0.0
        assert kt._span([{"age": None}], "age") == 0.0

    def test_numeric(self):
        records = [{"age": 10}, {"age": 30}, {"age": 20}]
        assert kt._span(records, "age") == 20.0

    def test_categorical(self):
        records = [{"c": "a"}, {"c": "b"}, {"c": "a"}]
        assert kt._span(records, "c") == 1.0  # 2 unique - 1


class TestChooseDimension:
    def test_picks_max_span(self):
        records = [
            {"age": 10, "zip": "a"},
            {"age": 50, "zip": "b"},
        ]
        # age span=40, zip span=1 → age
        assert kt._choose_dimension(records, ["age", "zip"]) == "age"


class TestMedianSplit:
    def test_too_small_returns_none(self):
        records = [{"age": i} for i in range(3)]
        assert kt._median_split(records, "age", k=2) is None

    def test_valid_split(self):
        records = [{"age": i} for i in range(10)]
        idx = kt._median_split(records, "age", k=2)
        assert idx is not None
        assert 2 <= idx <= 8

    def test_categorical_split(self):
        records = [{"c": f"v{i}"} for i in range(8)]
        idx = kt._median_split(records, "c", k=2)
        assert idx is not None


class TestGeneralize:
    def test_empty(self):
        assert kt._generalize([], ["age"]) == []

    def test_numeric_interval(self):
        records = [{"age": 10}, {"age": 20}]
        out = kt._generalize(records, ["age"])
        assert all(r["age"] == "[10-20]" for r in out)

    def test_numeric_equal(self):
        records = [{"age": 5}, {"age": 5}]
        out = kt._generalize(records, ["age"])
        assert all(r["age"] == 5 for r in out)

    def test_categorical_set(self):
        records = [{"c": "a"}, {"c": "b"}]
        out = kt._generalize(records, ["c"])
        assert all(r["c"] == "{a,b}" for r in out)

    def test_categorical_single(self):
        records = [{"c": "a"}, {"c": "a"}]
        out = kt._generalize(records, ["c"])
        assert all(r["c"] == "a" for r in out)


class TestToArrowBranches:
    def test_dataframe_value(self):
        pa = pytest.importorskip("pyarrow")
        pd = pytest.importorskip("pandas")
        result = kt.KAnonymityResult(
            value=pd.DataFrame({"age": ["[25-30]"]}),
            k=3,
            qi_cols=["age"],
            equivalence_classes_count=1,
        )
        table = result.to_arrow()
        assert isinstance(table, pa.Table)
        assert b"k_anonymity_metadata" in table.schema.metadata

    def test_scalar_value(self):
        pa = pytest.importorskip("pyarrow")
        result = kt.KAnonymityResult(
            value="single", k=2, qi_cols=["age"], equivalence_classes_count=1
        )
        table = result.to_arrow()
        assert isinstance(table, pa.Table)
        assert table.column_names == ["kanonymity_value"]


class TestPurePythonFallback:
    """通过屏蔽 pandas 触发纯 Python 回退实现。"""

    @pytest.fixture
    def no_pandas(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "pandas", None)
        yield

    def test_table_fallback(self, no_pandas):
        rows = [
            {"age": 25, "zip": "100001", "disease": "A"},
            {"age": 26, "zip": "100002", "disease": "B"},
            {"age": 55, "zip": "200001", "disease": "C"},
            {"age": 56, "zip": "200002", "disease": "D"},
        ]
        result = kt.k_anonymize_table(rows, ["age", "zip"], k=2)
        assert len(result) == 4
        # age 被泛化为区间
        assert all("[" in str(r["age"]) for r in result)

    def test_table_fallback_return_details(self, no_pandas):
        rows = [{"age": i} for i in range(6)]
        result = kt.k_anonymize_table(rows, ["age"], k=3, return_details=True)
        assert isinstance(result, kt.KAnonymityResult)
        assert len(result.value) == 6

    def test_dataframe_fallback_with_records(self, no_pandas):
        records = [
            {"age": 25, "zip": "100001"},
            {"age": 26, "zip": "100002"},
            {"age": 55, "zip": "200001"},
            {"age": 56, "zip": "200002"},
        ]
        result = kt.k_anonymize_dataframe(records, ["age", "zip"], k=2)
        assert isinstance(result, list)
        assert len(result) == 4

    def test_dataframe_fallback_return_details(self, no_pandas):
        records = [{"age": i} for i in range(6)]
        result = kt.k_anonymize_dataframe(
            records, ["age"], k=3, return_details=True
        )
        assert isinstance(result, kt.KAnonymityResult)


class TestDataFrameValidation:
    def test_len_less_than_k_raises(self):
        pd = pytest.importorskip("pandas")
        df = pd.DataFrame({"age": [1]})
        with pytest.raises(ValueError, match="at least"):
            kt.k_anonymize_dataframe(df, ["age"], k=2)

    def test_empty_qi_cols_raises(self):
        pd = pytest.importorskip("pandas")
        df = pd.DataFrame({"age": [1, 2, 3]})
        with pytest.raises(ValueError, match="qi_cols must not be empty"):
            kt.k_anonymize_dataframe(df, [], k=2)

    def test_missing_cols_raises(self):
        pd = pytest.importorskip("pandas")
        df = pd.DataFrame({"age": [1, 2, 3]})
        with pytest.raises(ValueError, match="not found"):
            kt.k_anonymize_dataframe(df, ["gender"], k=2)
