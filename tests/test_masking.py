"""数据脱敏模块单元测试。"""

from __future__ import annotations

import pytest
from prometheus_client import REGISTRY

from privacy_local_agent.privacy.masking import (
    FieldType,
    MaskingOperation,
    chunked_mask_records,
    guess_field_type,
    hash_value,
    mask_address,
    mask_dataframe,
    mask_default,
    mask_email,
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

    def test_mask_email(self) -> None:
        assert mask_email("zhangsan@example.com") == "z***n@example.com"
        assert mask_email("ab@test.com") == "a***@test.com"
        # 无 @ 使用默认策略（保留前后 3 位）
        assert mask_email("noemail") == "noe*ail"

    def test_mask_address(self) -> None:
        assert mask_address("北京市朝阳区某某街道123号") == "北京市朝阳区****"
        assert mask_address("短地址") == "短地址"  # 长度 <= 6 原样返回

    def test_mask_default(self) -> None:
        assert mask_default("abcdefgh") == "abc**fgh"

    def test_mask_value_routes_by_field_name(self) -> None:
        assert mask_value("mobile", "13812345678") == "138****5678"
        assert mask_value("id_card", "110101199001011234") == "110101********1234"
        assert mask_value("name", "张三丰") == "张**丰"
        assert mask_value("email", "test@example.com") == "t***t@example.com"
        assert mask_value("address", "北京市朝阳区某某街道") == "北京市朝阳区****"
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


class TestFieldTypeEnum:
    """字段类型枚举测试。"""

    def test_field_type_enum_values(self) -> None:
        assert FieldType.MOBILE == "mobile"
        assert FieldType.ID_CARD == "id_card"
        assert FieldType.EMAIL == "email"
        assert FieldType.ADDRESS == "address"

    def test_guess_field_type_email(self) -> None:
        assert guess_field_type("email") == FieldType.EMAIL.value
        assert guess_field_type("user_mail") == FieldType.EMAIL.value
        assert guess_field_type("邮箱") == FieldType.EMAIL.value

    def test_guess_field_type_address(self) -> None:
        assert guess_field_type("address") == FieldType.ADDRESS.value
        assert guess_field_type("home_addr") == FieldType.ADDRESS.value
        assert guess_field_type("地址") == FieldType.ADDRESS.value


class TestMaskingOperationEnum:
    """脱敏操作枚举测试。"""

    def test_masking_operation_enum_values(self) -> None:
        assert MaskingOperation.MASK_VALUE == "mask_value"
        assert MaskingOperation.HASH_VALUE == "hash_value"
        assert MaskingOperation.TRUNCATE == "truncate"


class TestInputValidation:
    """输入校验测试。"""

    def test_mask_value_empty_field_name_raises(self) -> None:
        with pytest.raises(ValueError, match="field_name must not be empty"):
            mask_value("", "test")

    def test_mask_value_non_string_value_raises(self) -> None:
        with pytest.raises(ValueError, match="value must be a string"):
            mask_value("mobile", 12345)  # type: ignore

    def test_hash_value_empty_salt_raises(self) -> None:
        with pytest.raises(ValueError, match="salt must not be empty"):
            hash_value("test", "")

    def test_truncate_negative_prefix_raises(self) -> None:
        with pytest.raises(ValueError, match="keep_prefix must be non-negative"):
            truncate("test", -1)

    def test_mask_record_non_dict_raises(self) -> None:
        with pytest.raises(ValueError, match="record must be a dict"):
            mask_record(["not", "a", "dict"])  # type: ignore

    def test_mask_record_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="record must not be empty"):
            mask_record({})

    def test_mask_value_batch_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            mask_value_batch([], [])


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


class TestChunkedMaskRecords:
    """流式分块脱敏测试。"""

    def test_basic_chunked_masking(self) -> None:
        chunks = [
            [{"mobile": "13812345678", "name": "张三丰", "age": 30}],
            [{"mobile": "13912345678", "name": "李四", "age": 25}],
        ]
        results = list(chunked_mask_records(chunks))
        assert len(results) == 2
        assert results[0][0]["mobile"] == "138****5678"
        assert results[0][0]["name"] == "张**丰"
        assert results[0][0]["age"] == 30
        assert results[1][0]["mobile"] == "139****5678"
        assert results[1][0]["name"] == "李*"

    def test_chunked_with_columns_filter(self) -> None:
        chunks = [
            [{"mobile": "13812345678", "name": "张三丰"}],
        ]
        results = list(chunked_mask_records(chunks, columns=["mobile"]))
        assert results[0][0]["mobile"] == "138****5678"
        assert results[0][0]["name"] == "张三丰"  # 未被脱敏

    def test_chunked_return_details(self) -> None:
        from privacy_local_agent.privacy.masking import MaskingResult

        chunks = [
            [{"mobile": "13812345678", "name": "张三丰"}],
            [{"mobile": "13912345678"}],
        ]
        results = list(chunked_mask_records(chunks, return_details=True))
        assert len(results) == 2
        assert isinstance(results[0], MaskingResult)
        assert results[0].operation == "chunked_mask_records"
        assert "mobile" in results[0].masked_fields
        assert results[0].total_masked >= 1

    def test_chunked_empty_chunks(self) -> None:
        results = list(chunked_mask_records([]))
        assert results == []

    def test_chunked_generator_input(self) -> None:
        def gen():
            yield [{"mobile": "13812345678"}]
            yield [{"mobile": "13912345678"}]

        results = list(chunked_mask_records(gen()))
        assert len(results) == 2
        assert results[0][0]["mobile"] == "138****5678"
