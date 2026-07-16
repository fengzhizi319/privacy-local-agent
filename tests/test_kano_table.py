"""数据集级 K-匿名（Mondrian）算法测试。"""

from __future__ import annotations

import pytest

from privacy_local_agent.privacy.kano_table import k_anonymize_table


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
