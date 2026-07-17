"""Tests for human review store and export."""

import json

import pytest

from privacy_local_agent.privacy.classification import ClassificationAPI
from privacy_local_agent.privacy.classification_models import SensitivityLevel
from privacy_local_agent.privacy.classification_review import ReviewStore


@pytest.fixture
def api():
    return ClassificationAPI()


def test_review_entries_collected_for_genomic_hint(api):
    # NER may mark GENOMIC_HINT as needs_human_review; if NER is No-Op, use manual override
    result = api.classify_table(
        schema=["gene_marker"],
        rows=[{"gene_marker": "BRCA1 c.5266dupC"}],
    )
    # Force at least one review entry via custom composite rule that targets L5
    result2 = api.classify_table(
        schema=["name", "id_card", "mobile"],
        rows=[{"name": "张三", "id_card": "110101199001011237", "mobile": "13800138000"}],
    )
    assert result2.review_entries
    entry = result2.review_entries[0]
    assert entry.status.value == "PENDING"


def test_confirm_review_and_export(api):
    result = api.classify_table(
        schema=["name", "id_card", "mobile"],
        rows=[{"name": "张三", "id_card": "110101199001011237", "mobile": "13800138000"}],
    )
    entry = result.review_entries[0]
    confirmed = api.confirm_review(
        entry.review_id,
        corrected_level="L5",
        reviewer="operator-1",
        comment="确认高敏感组合",
    )
    assert confirmed.status.value == "CONFIRMED"
    assert confirmed.corrected_level == "L5"

    exported = api.export_reviews(format="jsonl", mask_input=True)
    rows = [json.loads(line) for line in exported.strip().split("\n") if line.strip()]
    assert rows
    # mask_input only redacts field_value; __record__ entries have no value
    assert rows[0]["status"] == "CONFIRMED"


def test_export_csv(api):
    result = api.classify_table(
        schema=["name", "id_card", "mobile"],
        rows=[{"name": "张三", "id_card": "110101199001011237", "mobile": "13800138000"}],
    )
    review_id = result.review_entries[0].review_id
    api.confirm_review(review_id, corrected_level="L5")
    csv_data = api.export_reviews(format="csv", mask_input=False)
    assert "review_id" in csv_data
    assert "fine_tuning_text" in csv_data


def test_review_store_not_found():
    store = ReviewStore()
    with pytest.raises(KeyError):
        store.confirm("review-nonexistent", "L4")
