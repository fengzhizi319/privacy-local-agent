"""数据集级 K-匿名（Mondrian）算法测试。"""

from __future__ import annotations

import pytest
from prometheus_client import REGISTRY

from privacy_local_agent.privacy.kano import (
    GeneralizationStrategy,
    KAnonymityRecordResult,
    QIType,
    anonymize_record,
    anonymize_records_batch,
    choose_level,
    education_hierarchy,
    salary_hierarchy,
)
from privacy_local_agent.privacy.kano_table import (
    k_anonymize_dataframe,
    k_anonymize_table,
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
            k_anonymize_table([{"age": 1}, {"age": 2}], ["gender"], k=2)

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
            k=2,
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


class TestQITypeEnum:
    """准标识符类型枚举测试。"""

    def test_qi_type_enum_values(self) -> None:
        assert QIType.AGE == "age"
        assert QIType.ZIPCODE == "zipcode"
        assert QIType.GENDER == "gender"
        assert QIType.SALARY == "salary"
        assert QIType.EDUCATION == "education"


class TestGeneralizationStrategyEnum:
    """泛化策略枚举测试。"""

    def test_generalization_strategy_enum_values(self) -> None:
        assert GeneralizationStrategy.INTERVAL == "interval"
        assert GeneralizationStrategy.SET == "set"
        assert GeneralizationStrategy.SUPPRESSION == "suppression"
        assert GeneralizationStrategy.PREFIX == "prefix"


class TestInputValidationKano:
    """输入校验测试。"""

    def test_anonymize_record_k_less_than_2_raises(self) -> None:
        with pytest.raises(ValueError, match="k must be at least 2"):
            anonymize_record({"age": "25"}, ["age"], {}, k=1)

    def test_anonymize_record_empty_qi_cols_raises(self) -> None:
        with pytest.raises(ValueError, match="qi_cols must not be empty"):
            anonymize_record({"age": "25"}, [], {}, k=5)

    def test_anonymize_record_non_dict_raises(self) -> None:
        with pytest.raises(ValueError, match="record must be a dict"):
            anonymize_record(["not", "a", "dict"], ["age"], {}, k=5)  # type: ignore

    def test_choose_level_invalid_max_level_raises(self) -> None:
        with pytest.raises(ValueError, match="max_level must be at least 1"):
            choose_level(5, 0)

    def test_k_anonymize_table_k_less_than_2_raises(self) -> None:
        with pytest.raises(ValueError, match="k must be at least 2"):
            k_anonymize_table([{"age": 25}], ["age"], k=1)


class TestNewHierarchies:
    """新增泛化层次函数测试。"""

    def test_salary_hierarchy(self) -> None:
        assert salary_hierarchy("15", 0) == "15"
        assert salary_hierarchy("15", 1) == "[15K-20K]"
        assert salary_hierarchy("15", 2) == "[10K-20K]"
        assert salary_hierarchy("15", 3) == "[0K-50K]"
        assert salary_hierarchy("15", 4) == "*"

    def test_education_hierarchy(self) -> None:
        assert education_hierarchy("本科", 0) == "本科"
        assert education_hierarchy("本科", 1) == "高等教育"
        assert education_hierarchy("高中", 1) == "基础教育"
        assert education_hierarchy("本科", 2) == "*"


class TestAnonymizeRecordsBatch:
    """批量记录泛化测试。"""

    def test_anonymize_records_batch_basic(self) -> None:
        records = [
            {"age": "25", "zipcode": "100001"},
            {"age": "30", "zipcode": "100002"},
        ]
        result = anonymize_records_batch(records, ["age", "zipcode"], k=2)
        assert len(result) == 2

    def test_anonymize_records_batch_return_details(self) -> None:
        records = [
            {"age": "25", "zipcode": "100001"},
            {"age": "30", "zipcode": "100002"},
        ]
        result = anonymize_records_batch(records, ["age", "zipcode"], k=2, return_details=True)
        assert isinstance(result, KAnonymityRecordResult)
        assert result.k == 2

    def test_anonymize_records_batch_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="records must not be empty"):
            anonymize_records_batch([], ["age"], k=2)
