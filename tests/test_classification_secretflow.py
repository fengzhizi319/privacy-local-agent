"""Tests for SecretFlow classification adapter using mocks."""

import pytest

from privacy_local_agent.privacy.classification import ClassificationAPI
from privacy_local_agent.privacy.classification_secretflow import classify_secretflow


class FakeSecretFlowDataFrame:
    """Mock secretflow.data.DataFrame-like object backed by pandas."""

    def __init__(self, df):
        self._df = df

    def to_csv(self, **kwargs):
        return self._df.to_csv(**kwargs)


@pytest.fixture
def api():
    return ClassificationAPI()


def test_classify_secretflow_dataframe(api, monkeypatch):
    pd = pytest.importorskip("pandas")
    df = pd.DataFrame({
        "id_card": ["110101199001011237"],
        "diagnosis": ["B21.1"],
    })
    sf_data = FakeSecretFlowDataFrame(df)

    # Mock to_records to extract records from our fake object
    def fake_to_records(data, party=None):
        return data._df.to_dict(orient="records")

    monkeypatch.setattr(
        "privacy_local_agent.privacy.classification_secretflow.to_records",
        fake_to_records,
    )

    table_result = classify_secretflow(api, sf_data)
    assert table_result.final_level.value == "L4"


def test_classify_secretflow_unsupported_type(api):
    with pytest.raises(TypeError):
        classify_secretflow(api, object())
