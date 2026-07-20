"""数据集级 K-匿名（Mondrian）算法测试。"""

from __future__ import annotations

import pytest
from prometheus_client import REGISTRY

from privacy_local_agent.privacy.kano_table import (
    k_anonymize_dataframe,
    k_anonymize_table,
)
from privacy_local_agent.privacy.kano import (
    anonymize_record,
    KAnonymityRecordResult,
)


class TestKAnonymizeTable:
    """Mondrian 算法单元测试。"""

    def test_numeric_qi_generalizes_to_intervals(self) -> None:
        rows = [
            {"age": 25, "zipcode": "100001", "disease": "A"},
            {"age": 26, "zipcode": "100002", "disease": "B"},
            {"age": 27, "zipcode": "100003", "disease": "C"},
            {"age": 55, "zipcode": "200001", "disease": "D"},
            {"age": 56, "zipcode": "200002", "disease": "E"},
            {"age": 57, "zipcode": "200003", "disease": "F"},
        ]
        result = k_anonymize_table(rows, ["age", "zipcode"], k=3)
        assert len(result) == len(rows)
        # 敏感字段应保持不变
        assert {r["disease"] for r in result} == {"A", "B", "C", "D", "E", "F"}
        # age 应被泛化为区间
        for r in result:
            assert "[" in str(r["age"])

    def test_categorical_qi_generalizes_to_set(self) -> None:
        rows = [
            {"gender": "M", "age": 25, "salary": 5000},
            {"gender": "M", "age": 26, "salary": 6000},
            {"gender": "F", "age": 35, "salary": 7000},
            {"gender": "F", "age": 36, "salary": 8000},
        ]
        result = k_anonymize_table(rows, ["gender", "age"], k=2)
        assert len(result) == 4
        # 分类型 gender 可能被泛化为 {F,M} 或保持原值
        gender_values = {str(r["gender"]) for r in result}
        assert all(v in {"M", "F", "{F,M}", "{M,F}"} for v in gender_values)

    def test_each_equivalence_group_size_at_least_k(self) -> None:
        rows = [
            {"age": i, "gender": "M" if i % 2 == 0 else "F"}
            for i in range(20)
        ]
        result = k_anonymize_table(rows, ["age", "gender"], k=5)
        # 按 age/gender 泛化结果统计等价组大小
        from collections import Counter

        group_counts = Counter(
            (str(r["age"]), str(r["gender"])) for r in result
        )
        assert all(c >= 5 for c in group_counts.values())

    def test_empty_input(self) -> None:
        assert k_anonymize_table([], ["age"], k=2) == []

    def test_input_smaller_than_k_raises(self) -> None:
        with pytest.raises(ValueError, match="at least"):
            k_anonymize_table([{"age": 1}], ["age"], k=2)

    def test_missing_qi_cols_raises(self) -> None:
        with pytest.raises(ValueError, match="not found"):
            k_anonymize_table([{"age": 1}], ["gender"], k=1)

    def test_k_anonymize_dataframe(self) -> None:
        pd = pytest.importorskip("pandas")
        df = pd.DataFrame(
            {
                "age": [25, 26, 27, 55, 56, 57],
                "zipcode": ["100001", "100002", "100003", "200001", "200002", "200003"],
                "disease": ["A", "B", "C", "D", "E", "F"],
            }
        )
        result = k_anonymize_dataframe(df, ["age", "zipcode"], k=3)
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 6
        assert set(result["disease"]) == {"A", "B", "C", "D", "E", "F"}

    def test_k_anonymize_table_records_metric(self) -> None:
        before = REGISTRY.get_sample_value(
            "privacy_kano_operations_total", {"operation": "table"}
        ) or 0.0
        k_anonymize_table(
            [{"age": 25, "zipcode": "100001"}, {"age": 26, "zipcode": "100002"}],
            ["age", "zipcode"],
            k=1,
        )
        after = REGISTRY.get_sample_value(
            "privacy_kano_operations_total", {"operation": "table"}
        )
        assert after == before + 1

    def test_k_anonymize_table_return_details(self) -> None:
        from privacy_local_agent.privacy.kano_table import KAnonymityResult

        rows = [
            {"age": 25, "zipcode": "100001", "disease": "A"},
            {"age": 26, "zipcode": "100002", "disease": "B"},
            {"age": 27, "zipcode": "100003", "disease": "C"},
            {"age": 55, "zipcode": "200001", "disease": "D"},
            {"age": 56, "zipcode": "200002", "disease": "E"},
            {"age": 57, "zipcode": "200003", "disease": "F"},
        ]
        result = k_anonymize_table(rows, ["age", "zipcode"], k=3, return_details=True)
        assert isinstance(result, KAnonymityResult)
        assert result.k == 3
        assert result.qi_cols == ["age", "zipcode"]
        assert result.equivalence_classes_count >= 1
        assert isinstance(result.value, list)
        assert len(result.value) == 6

    def test_k_anonymize_dataframe_return_details(self) -> None:
        pd = pytest.importorskip("pandas")
        from privacy_local_agent.privacy.kano_table import KAnonymityResult

        df = pd.DataFrame(
            {
                "age": [25, 26, 27, 55, 56, 57],
                "zipcode": ["100001", "100002", "100003", "200001", "200002", "200003"],
                "disease": ["A", "B", "C", "D", "E", "F"],
            }
        )
        result = k_anonymize_dataframe(df, ["age", "zipcode"], k=3, return_details=True)
        assert isinstance(result, KAnonymityResult)
        assert result.k == 3
        assert result.qi_cols == ["age", "zipcode"]
        assert isinstance(result.value, list)
        assert len(result.value) == 6

    def test_k_anonymity_result_to_arrow(self) -> None:
        pa = pytest.importorskip("pyarrow")
        from privacy_local_agent.privacy.kano_table import KAnonymityResult

        result = KAnonymityResult(
            value=[{"age": "[25-30]", "zipcode": "100***"}],
            k=3,
            qi_cols=["age", "zipcode"],
            equivalence_classes_count=2,
        )
        table = result.to_arrow()
        assert isinstance(table, pa.Table)
        assert b"k_anonymity_metadata" in table.schema.metadata


class TestAnonymizeRecord:
    """记录级 K-匿名泛化测试。"""

    def test_anonymize_record_basic(self) -> None:
        record = {"age": "30", "zipcode": "100001", "name": "Alice"}
        result = anonymize_record(record, ["age", "zipcode"], {}, k=10)
        assert isinstance(result, dict)
        # age 应被泛化
        assert result["age"] != "30" or "[" in str(result["age"])
        # name 不在 qi_cols 中，保持不变
        assert result["name"] == "Alice"

    def test_anonymize_record_return_details(self) -> None:
        record = {"age": "30", "zipcode": "100001", "name": "Alice"}
        result = anonymize_record(
            record, ["age", "zipcode"], {}, k=10, return_details=True
        )
        assert isinstance(result, KAnonymityRecordResult)
        assert result.k == 10
        assert result.qi_cols == ["age", "zipcode"]
        assert result.applied_level >= 1
        assert "age" in result.hierarchies_used
        assert isinstance(result.value, dict)

    def test_k_anonymity_record_result_to_arrow(self) -> None:
        pa = pytest.importorskip("pyarrow")
        result = KAnonymityRecordResult(
            value={"age": "[30-35]", "zipcode": "100***"},
            k=10,
            qi_cols=["age", "zipcode"],
            applied_level=2,
            hierarchies_used={"age": "age_hierarchy", "zipcode": "zipcode_hierarchy"},
        )
        table = result.to_arrow()
        assert isinstance(table, pa.Table)
        assert b"k_anonymity_record_metadata" in table.schema.metadata
