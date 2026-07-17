"""数据脱敏模块单元测试。"""

from __future__ import annotations

import pytest
from prometheus_client import REGISTRY

from privacy_local_agent.privacy.masking import (
    hash_value,
    mask_dataframe,
    mask_default,
    mask_id_card,
    mask_mobile,
    mask_name,
    mask_record,
    mask_value,
    mask_value_batch,
    truncate,
)


class TestMaskValue:
    """单字段脱敏测试。"""

    def test_mask_mobile(self) -> None:
        assert mask_mobile("13812345678") == "138****5678"
        assert mask_mobile("123") == "123"  # 非 11 位原样返回

    def test_mask_id_card(self) -> None:
        assert mask_id_card("110101199001011234") == "110101********1234"

    def test_mask_name(self) -> None:
        assert mask_name("张三丰") == "张**丰"
        assert mask_name("李四") == "李*"

    def test_mask_default(self) -> None:
        assert mask_default("abcdefgh") == "abc**fgh"

    def test_mask_value_routes_by_field_name(self) -> None:
        assert mask_value("mobile", "13812345678") == "138****5678"
        assert mask_value("id_card", "110101199001011234") == "110101********1234"
        assert mask_value("name", "张三丰") == "张**丰"
        assert mask_value("unknown", "abcdefgh") == "abc**fgh"

    def test_mask_value_records_metric(self) -> None:
        counter = REGISTRY.get_sample_value(
            "privacy_masking_operations_total", {"operation": "mask_value"}
        )
        before = counter or 0.0
        mask_value("mobile", "13812345678")
        after = REGISTRY.get_sample_value(
            "privacy_masking_operations_total", {"operation": "mask_value"}
        )
        assert after == before + 1


class TestMaskRecord:
    """整记录脱敏测试。"""

    def test_mask_record(self) -> None:
        record = {"mobile": "13812345678", "name": "张三丰", "age": 30}
        result = mask_record(record)
        assert result["mobile"] == "138****5678"
        assert result["name"] == "张**丰"
        assert result["age"] == 30
        assert record["mobile"] == "13812345678"  # 不修改原记录


class TestMaskBatch:
    """批量字段脱敏测试。"""

    def test_mask_value_batch(self) -> None:
        results = mask_value_batch(
            ["mobile", "name", "id_card"],
            ["13812345678", "张三丰", "110101199001011234"],
        )
        assert results == ["138****5678", "张**丰", "110101********1234"]

    def test_mask_value_batch_length_mismatch(self) -> None:
        with pytest.raises(ValueError, match="same length"):
            mask_value_batch(["mobile"], ["13812345678", "13812345679"])


class TestMaskDataFrame:
    """DataFrame 脱敏测试。"""

    def test_mask_dataframe(self) -> None:
        pd = pytest.importorskip("pandas")
        df = pd.DataFrame(
            {
                "mobile": ["13812345678", "13912345678"],
                "name": ["张三", "李四"],
                "age": [25, 34],
            }
        )
        result = mask_dataframe(df)
        assert result["mobile"].tolist() == ["138****5678", "139****5678"]
        assert result["name"].tolist() == ["张*", "李*"]
        assert result["age"].tolist() == [25, 34]

    def test_mask_dataframe_with_columns(self) -> None:
        pd = pytest.importorskip("pandas")
        df = pd.DataFrame(
            {
                "mobile": ["13812345678"],
                "name": ["张三"],
            }
        )
        result = mask_dataframe(df, columns=["mobile"])
        assert result["mobile"].tolist() == ["138****5678"]
        assert result["name"].tolist() == ["张三"]

    def test_mask_dataframe_empty(self) -> None:
        pd = pytest.importorskip("pandas")
        df = pd.DataFrame({"mobile": []})
        result = mask_dataframe(df)
        assert result.empty


class TestHashAndTruncate:
    """哈希与截断测试。"""

    def test_hash_value(self) -> None:
        result = hash_value("hello", "salt")
        assert len(result) == 16
        assert hash_value("hello", "salt") == result  # 确定性
        assert hash_value("hello", "other") != result

    def test_truncate(self) -> None:
        assert truncate("abcdef", 3) == "abc***"
        assert truncate("ab", 3) == "ab"
