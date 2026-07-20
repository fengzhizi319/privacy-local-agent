"""测试 privacy 模块下 (masking, kano_table, qol) 的统一数据输入输出处理与 PyArrow Metadata 导出。
"""

import pandas as pd
import pytest

from privacy_local_agent.privacy.kano import anonymize_record
from privacy_local_agent.privacy.kano_table import KAnonymityResult, k_anonymize_dataframe, k_anonymize_table
from privacy_local_agent.privacy.masking import MaskingResult, mask_dataframe, mask_record, mask_value
from privacy_local_agent.privacy.qol import QoLResult, obfuscate_query, obfuscate_query_batch


class TestMaskingUnifiedAdapters:
    def test_mask_value_return_details_and_to_arrow(self):
        res = mask_value("mobile", "13812345678", return_details=True)
        assert isinstance(res, MaskingResult)
        assert res.value == "138****5678"
        assert res.operation == "mask_value:mobile"

        table = res.to_arrow()
        assert b"masking_metadata" in table.schema.metadata
        assert table.column("masked_value")[0].as_py() == "138****5678"

    def test_mask_dataframe_return_details_and_to_arrow(self):
        df = pd.DataFrame({"user_name": ["张三", "李四"], "mobile": ["13812345678", "13987654321"]})
        res = mask_dataframe(df, return_details=True)
        assert isinstance(res, MaskingResult)
        assert isinstance(res.value, pd.DataFrame)
        assert res.value.iloc[0]["user_name"] == "张*"

        table = res.to_arrow()
        assert b"masking_metadata" in table.schema.metadata
        assert "user_name" in table.column_names


class TestKAnonymityUnifiedAdapters:
    def test_k_anonymize_table_return_details_and_to_arrow(self):
        rows = [
            {"age": "25", "zipcode": "310000", "gender": "M"},
            {"age": "26", "zipcode": "310001", "gender": "M"},
        ]
        res = k_anonymize_table(rows, qi_cols=["age", "zipcode"], k=2, return_details=True)
        assert isinstance(res, KAnonymityResult)
        assert res.k == 2

        table = res.to_arrow()
        assert b"k_anonymity_metadata" in table.schema.metadata
        assert "age" in table.column_names


class TestQoLUnifiedAdapters:
    def test_obfuscate_query_return_details_and_to_arrow(self):
        res = obfuscate_query("高血压饮食", num_dummies=3, domain="medical", return_details=True)
        assert isinstance(res, QoLResult)
        assert len(res.queries) == 4
        assert res.queries[res.real_query_index] == "高血压饮食"

        table = res.to_arrow()
        assert b"qol_metadata" in table.schema.metadata
        assert "obfuscated_query" in table.column_names
