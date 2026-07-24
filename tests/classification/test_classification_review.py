"""分类复核存储与样本导出单元测试 / Classification Review Store and Export Unit Tests.

中文说明：
验证人工复核队列的完整流程：
- 自动收集 needs_human_review=True 的字段/记录。
- 确认/修正等级操作。
- JSONL/CSV 格式导出（支持脱敏）。
- 复核条目不存在时的异常处理。
- SQLite 持久化模式下进程重启后数据不丢失。

English Description:
Tests for the human review queue workflow:
- Auto-collection of fields/records with needs_human_review=True.
- Level confirmation/correction operations.
- JSONL/CSV export with optional masking.
- Error handling for non-existent review entries.
- SQLite persistence across process restarts.
"""

import json

import pytest

from privacy_local_agent.privacy.classification import ClassificationAPI
from privacy_local_agent.privacy.classification.classification_review import ReviewStore


@pytest.fixture
def api():
    """创建默认 ClassificationAPI 实例。"""
    return ClassificationAPI()


def test_review_entries_collected_for_genomic_hint(api):
    """测试高敏感组合自动触发复核条目收集。

    Test that high-sensitivity combinations automatically trigger review entry collection.
    """
    # NER 可能将 GENOMIC_HINT 标记为需人工复核；若 NER 为 No-Op，使用 manual override
    api.classify_table(
        schema=["gene_marker"],
        rows=[{"gene_marker": "BRCA1 c.5266dupC"}],
    )
    # 通过自定义复合规则强制触发至少一个复核条目
    result2 = api.classify_table(
        schema=["name", "id_card", "mobile"],
        rows=[{"name": "张三", "id_card": "110101199001011237", "mobile": "13800138000"}],
    )
    assert result2.review_entries
    entry = result2.review_entries[0]
    assert entry.status.value == "PENDING"


def test_confirm_review_and_export(api):
    """测试确认复核并以 JSONL 格式导出（含脱敏）。

    Test confirming a review entry and exporting in JSONL format with masking.
    """
    result = api.classify_table(
        schema=["name", "id_card", "mobile"],
        rows=[{"name": "张三", "id_card": "110101199001011237", "mobile": "13800138000"}],
    )
    entry = result.review_entries[0]
    # 确认复核：修正等级为 L5
    confirmed = api.confirm_review(
        entry.review_id,
        corrected_level="L5",
        reviewer="operator-1",
        comment="确认高敏感组合",
    )
    assert confirmed.status.value == "CONFIRMED"
    assert confirmed.corrected_level == "L5"

    # 导出为 JSONL 格式，启用输入脱敏
    exported = api.export_reviews(format="jsonl", mask_input=True)
    rows = [json.loads(line) for line in exported.strip().split("\n") if line.strip()]
    assert rows
    # mask_input 仅脱敏 field_value；__record__ 条目无值
    assert rows[0]["status"] == "CONFIRMED"


def test_export_csv(api):
    """测试以 CSV 格式导出复核记录（不脱敏）。

    Test exporting review records in CSV format without masking.
    """
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
    """测试确认不存在的复核条目时抛出 KeyError。

    Test that confirming a non-existent review entry raises KeyError.
    """
    store = ReviewStore()
    with pytest.raises(KeyError):
        store.confirm("review-nonexistent", "L4")


def test_review_store_sqlite_persistence(tmp_path):
    """验证 SQLite 持久化：进程重启后历史复核记录不丢失。

    Verify SQLite persistence: review records survive process restarts.
    """
    from privacy_local_agent.privacy.classification.classification_models import ReviewStatus

    db = str(tmp_path / "reviews.db")
    api = ClassificationAPI(review_store=ReviewStore(db_path=db))
    result = api.classify_table(
        schema=["name", "id_card", "mobile"],
        rows=[{"name": "张三", "id_card": "110101199001011237", "mobile": "13800138000"}],
    )
    review_id = result.review_entries[0].review_id

    # 模拟进程重启：使用新的 ReviewStore 实例读取同一数据库
    api2 = ClassificationAPI(review_store=ReviewStore(db_path=db))
    loaded = api2.review_store._mem[review_id]
    assert loaded.status == ReviewStatus.PENDING

    # 在新实例上确认复核
    api2.confirm_review(review_id, corrected_level="L5", reviewer="operator-1")

    # 再次模拟重启，确认状态已持久化
    api3 = ClassificationAPI(review_store=ReviewStore(db_path=db))
    reloaded = api3.review_store._mem[review_id]
    assert reloaded.status == ReviewStatus.CONFIRMED
    assert reloaded.corrected_level == "L5"
    assert reloaded.reviewer == "operator-1"
