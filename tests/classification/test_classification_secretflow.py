"""SecretFlow 联邦数据分类适配器单元测试 / SecretFlow Classification Adapter Unit Tests.

中文说明：
使用 Mock 对象验证 SecretFlow 联邦数据结构（HDataFrame/VDataFrame）的分类适配能力：
- 模拟 secretflow.data.DataFrame 进行表级分类。
- 不支持的数据类型抛出 TypeError。

English Description:
Tests for SecretFlow classification adapter using mocks:
- Mock secretflow.data.DataFrame for table-level classification.
- Unsupported data types raise TypeError.
"""

import pytest

from privacy_local_agent.privacy.classification import ClassificationAPI
from privacy_local_agent.privacy.classification.classification_utils import classify_secretflow


class FakeSecretFlowDataFrame:
    """模拟 secretflow.data.DataFrame 的 Mock 对象（底层使用 pandas）。

    Mock secretflow.data.DataFrame-like object backed by pandas.
    """

    def __init__(self, df):
        self._df = df

    def to_csv(self, **kwargs):
        return self._df.to_csv(**kwargs)


@pytest.fixture
def api():
    """创建默认 ClassificationAPI 实例。"""
    return ClassificationAPI()


def test_classify_secretflow_dataframe(api, monkeypatch):
    """测试模拟 SecretFlow DataFrame 的表级分类。

    Test table-level classification with a mocked SecretFlow DataFrame.
    """
    pd = pytest.importorskip("pandas")
    df = pd.DataFrame({
        "id_card": ["110101199001011237"],
        "diagnosis": ["B21.1"],
    })
    sf_data = FakeSecretFlowDataFrame(df)

    # Mock to_records 从 fake 对象中提取记录
    def fake_to_records(data, party=None):
        return data._df.to_dict(orient="records")

    monkeypatch.setattr(
        "privacy_local_agent.privacy.classification.classification_utils.to_records",
        fake_to_records,
    )

    # 身份证(L3) + ICD-10 HIV(L4) → 表级最终等级 L4
    table_result = classify_secretflow(api, sf_data)
    assert table_result.final_level.value == "L4"


def test_classify_secretflow_unsupported_type(api):
    """测试不支持的数据类型抛出 TypeError。

    Test that unsupported data types raise TypeError.
    """
    with pytest.raises(TypeError):
        classify_secretflow(api, object())
