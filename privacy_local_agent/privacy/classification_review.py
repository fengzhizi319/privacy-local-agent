"""Human review store and sample export for classification.

提供轻量复核队列：自动收集 `needs_human_review=True` 的字段/记录，
支持确认/修正等级，并导出 JSONL/CSV 用于模型微调。
"""

import csv
import io
import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..observability.metrics import CLASSIFICATION_REVIEW_QUEUE_SIZE
from .classification_models import ReviewEntry, ReviewStatus, SensitivityLevel
from .classification_utils import hash_value, redact


class ReviewStore:
    """复核样本存储。

    支持内存模式与 SQLite 持久化模式（通过 `PRIVACY_REVIEW_DB` 环境变量）。
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or os.environ.get("PRIVACY_REVIEW_DB")
        self._mem: Dict[str, ReviewEntry] = {}
        self._lock = threading.Lock()
        if self.db_path:
            self._init_sqlite()
            self._load_sqlite()

    def _init_sqlite(self) -> None:
        with sqlite3.connect(self.db_path, timeout=10.0) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS classification_reviews ("
                "review_id TEXT PRIMARY KEY, "
                "record_index INTEGER, "
                "field_name TEXT, "
                "field_value TEXT, "
                "predicted_level TEXT, "
                "predicted_tags TEXT, "
                "corrected_level TEXT, "
                "reviewer TEXT, "
                "comment TEXT, "
                "status TEXT, "
                "created_at TEXT, "
                "updated_at TEXT)"
            )

    def add_from_record(
        self,
        record_result: Any,
        original_record: Optional[Dict[str, Any]] = None,
    ) -> List[ReviewEntry]:
        """从记录分类结果中自动提取需要人工复核的字段。

        Args:
            record_result: RecordClassificationResult 实例。
            original_record: 原始记录字典，用于获取字段值。

        Returns:
            新增复核条目列表。
        """
        entries: List[ReviewEntry] = []
        for field_name, field_result in record_result.field_results.items():
            if not field_result.needs_human_review:
                continue
            review_id = f"review-{uuid.uuid4().hex[:12]}"
            field_value = None
            if original_record is not None:
                field_value = original_record.get(field_name)
            entry = ReviewEntry(
                review_id=review_id,
                record_index=record_result.record_index,
                field_name=field_name,
                field_value=str(field_value) if field_value is not None else None,
                predicted_level=field_result.final_level,
                predicted_tags=[str(tag) for tag in field_result.tags],
                status=ReviewStatus.PENDING,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            entries.append(entry)

        # 记录级复核：当记录整体需要复核但没有具体字段需要复核时
        if record_result.needs_human_review and not entries:
            review_id = f"review-{uuid.uuid4().hex[:12]}"
            entry = ReviewEntry(
                review_id=review_id,
                record_index=record_result.record_index,
                field_name="__record__",
                field_value=None,
                predicted_level=record_result.final_level,
                predicted_tags=[str(tag) for tag in record_result.aggregated_tags],
                status=ReviewStatus.PENDING,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            entries.append(entry)

        with self._lock:
            for entry in entries:
                self._mem[entry.review_id] = entry
                if self.db_path:
                    self._insert_sqlite(entry)
            CLASSIFICATION_REVIEW_QUEUE_SIZE.set(
                sum(1 for e in self._mem.values() if e.status == ReviewStatus.PENDING)
            )
        return entries

    def confirm(
        self,
        review_id: str,
        corrected_level: str,
        reviewer: str = "",
        comment: str = "",
    ) -> ReviewEntry:
        """确认或修正复核条目。

        Args:
            review_id: 复核条目 ID。
            corrected_level: 修正后的敏感度等级。
            reviewer: 复核人标识。
            comment: 复核说明。

        Returns:
            更新后的 ReviewEntry。

        Raises:
            KeyError: 复核条目不存在。
        """
        with self._lock:
            if review_id not in self._mem:
                raise KeyError(f"review not found: {review_id}")
            entry = self._mem[review_id]
            entry.corrected_level = corrected_level
            entry.reviewer = reviewer
            entry.comment = comment
            entry.status = ReviewStatus.CONFIRMED
            entry.updated_at = datetime.now(timezone.utc).isoformat()
            if self.db_path:
                self._update_sqlite(entry)
            CLASSIFICATION_REVIEW_QUEUE_SIZE.set(
                sum(1 for e in self._mem.values() if e.status == ReviewStatus.PENDING)
            )
            return entry

    def export(self, format: str = "jsonl", mask_input: bool = False) -> str:
        """导出复核样本。

        Args:
            format: `jsonl` 或 `csv`。
            mask_input: 是否对 input 字段脱敏。

        Returns:
            导出内容字符串。
        """
        with self._lock:
            entries = list(self._mem.values())

        rows = []
        for entry in entries:
            value = entry.field_value
            if mask_input and value is not None:
                value = redact(value)
            row = {
                "review_id": entry.review_id,
                "input": f"{entry.field_name}|{value}" if value is not None else entry.field_name,
                "predicted_level": entry.predicted_level.value if entry.predicted_level else None,
                "predicted_tags": entry.predicted_tags,
                "corrected_level": entry.corrected_level,
                "reviewer": entry.reviewer,
                "comment": entry.comment,
                "status": entry.status.value,
                "fine_tuning_text": self._build_fine_tuning_text(entry, value),
            }
            rows.append(row)

        if format == "jsonl":
            return "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
        if format == "csv":
            output = io.StringIO()
            writer = csv.DictWriter(output, fieldnames=rows[0].keys() if rows else [])
            writer.writeheader()
            writer.writerows(rows)
            return output.getvalue()
        raise ValueError(f"unsupported export format: {format}")

    def _build_fine_tuning_text(self, entry: ReviewEntry, value: Optional[str]) -> str:
        """构造 LLM 微调格式文本。"""
        input_text = f"字段名: {entry.field_name}\n字段值: {value if value else ''}"
        predicted = entry.predicted_level.value if entry.predicted_level else "UNKNOWN"
        corrected = entry.corrected_level or predicted
        return (
            f"### Input\n{input_text}\n"
            f"### Predicted\n{predicted}\n"
            f"### Corrected\n{corrected}\n"
            f"### Comment\n{entry.comment or ''}"
        )

    def _load_sqlite(self) -> None:
        """从 SQLite 加载已有复核记录到内存，保证进程重启后历史复核不丢失。"""
        with sqlite3.connect(self.db_path, timeout=10.0) as conn:
            rows = conn.execute("SELECT * FROM classification_reviews").fetchall()
        for row in rows:
            entry = ReviewEntry(
                review_id=row[0],
                record_index=row[1],
                field_name=row[2],
                field_value=row[3],
                predicted_level=SensitivityLevel(row[4]) if row[4] else None,
                predicted_tags=json.loads(row[5]) if row[5] else [],
                corrected_level=row[6],
                reviewer=row[7] or "",
                comment=row[8] or "",
                status=ReviewStatus(row[9]),
                created_at=row[10],
                updated_at=row[11],
            )
            self._mem[row[0]] = entry
        CLASSIFICATION_REVIEW_QUEUE_SIZE.set(
            sum(1 for e in self._mem.values() if e.status == ReviewStatus.PENDING)
        )

    def _insert_sqlite(self, entry: ReviewEntry) -> None:
        with sqlite3.connect(self.db_path, timeout=10.0) as conn:
            conn.execute(
                "INSERT INTO classification_reviews VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    entry.review_id,
                    entry.record_index,
                    entry.field_name,
                    entry.field_value,
                    entry.predicted_level.value if entry.predicted_level else None,
                    json.dumps(entry.predicted_tags, ensure_ascii=False),
                    entry.corrected_level,
                    entry.reviewer,
                    entry.comment,
                    entry.status.value,
                    entry.created_at,
                    entry.updated_at,
                ),
            )

    def _update_sqlite(self, entry: ReviewEntry) -> None:
        with sqlite3.connect(self.db_path, timeout=10.0) as conn:
            conn.execute(
                "UPDATE classification_reviews SET corrected_level=?, reviewer=?, comment=?, "
                "status=?, updated_at=? WHERE review_id=?",
                (
                    entry.corrected_level,
                    entry.reviewer,
                    entry.comment,
                    entry.status.value,
                    entry.updated_at,
                    entry.review_id,
                ),
            )
