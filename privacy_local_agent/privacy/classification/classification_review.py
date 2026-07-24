"""分类复核存储与样本导出 / Classification Review Store and Sample Export.

中文说明：
提供轻量复核队列：自动收集 `needs_human_review=True` 的字段/记录，
支持确认/修正等级，并导出 JSONL/CSV 用于模型微调。

设计目标：
- 自动收集：分类结果中标记 needs_human_review=True 的字段自动入队
- 人工确认：安全专家可修正预测等级，记录复核人和说明
- 样本导出：支持 JSONL/CSV 格式导出，用于 LLM 微调数据集构建
- 双模式存储：内存模式（轻量、重启丢失）和 SQLite 模式（持久化）

存储模式选择：
- 设置 PRIVACY_REVIEW_DB 环境变量 → SQLite 持久化模式
- 未设置 → 纯内存模式（进程重启后数据丢失）

English Description:
Provides a lightweight review queue that automatically collects fields/records with
`needs_human_review=True`, supports level confirmation/correction, and exports
JSONL/CSV for model fine-tuning.
"""

# 启用延迟注解求值，允许在类型提示中引用尚未定义的类名
from __future__ import annotations

# 导入 CSV 写入模块，用于导出 CSV 格式的复核样本
import csv
# 导入字符串 IO 模块，用于在内存中构建 CSV 输出
import io
# 导入 JSON 模块，用于导出 JSONL 格式和 SQLite 中标签的序列化/反序列化
import json
# 导入操作系统接口，用于读取环境变量（PRIVACY_REVIEW_DB）
import os
# 导入 SQLite3 数据库模块，用于持久化存储复核记录
import sqlite3
# 导入线程模块，用于创建互斥锁保护内存字典的并发访问
import threading
# 导入 UUID 模块，用于生成唯一的复核条目 ID
import uuid
# 导入日期时间模块，用于生成 UTC ISO 格式的创建/更新时间戳
from datetime import datetime, timezone
# 导入 Any 通用类型注解
from typing import Any

# 导入结构化日志工厂函数
from ...observability.logging_config import get_logger
# 导入 Prometheus 指标：复核队列中待处理条目数量（Gauge）
from ...observability.metrics import CLASSIFICATION_REVIEW_QUEUE_SIZE
# 导入数据模型：复核条目、复核状态枚举、敏感度等级枚举
from .classification_models import ReviewEntry, ReviewStatus, SensitivityLevel
# 导入日志脱敏工具函数（导出时可选对字段值进行掩码处理）
from .classification_utils import redact

# 创建模块级结构化日志器，用于记录复核存储相关事件
logger = get_logger(__name__)


class ReviewStore:
    """复核样本存储 / Review Sample Store.

    中文说明：
    支持内存模式与 SQLite 持久化模式（通过 `PRIVACY_REVIEW_DB` 环境变量）。
    所有公开方法都是线程安全的（通过 _lock 互斥锁保护）。

    数据流：
    1. ClassificationAPI.classify_table() → add_from_record() → 自动收集需复核字段
    2. 安全专家通过 REST API → confirm() → 修正等级
    3. 导出工具通过 REST API → export() → 生成微调数据集

    English Description:
    Supports in-memory mode and SQLite persistence mode (via `PRIVACY_REVIEW_DB` env var).

    Attributes:
        db_path: SQLite 数据库路径（None 表示纯内存模式） / SQLite database path.
    """

    def __init__(self, db_path: str | None = None):
        """初始化复核存储 / Initialize Review Store.

        根据是否配置 db_path 选择存储模式：
        - 有 db_path：SQLite 持久化模式（建表 + 加载历史数据）
        - 无 db_path：纯内存模式（轻量但重启丢失）

        Args:
            db_path: SQLite 数据库路径 / SQLite database path (None for in-memory mode).
        """
        # 确定数据库路径：优先使用参数，其次读取环境变量
        self.db_path = db_path or os.environ.get("PRIVACY_REVIEW_DB")
        # 内存存储字典：review_id → ReviewEntry 实例
        self._mem: dict[str, ReviewEntry] = {}
        # 互斥锁：保护 _mem 字典的并发读写安全
        self._lock = threading.Lock()
        # 根据是否配置了数据库路径选择初始化模式
        if self.db_path:
            # SQLite 模式：创建表结构 + 加载已有数据到内存
            self._init_sqlite()
            self._load_sqlite()
            logger.info(
                "review_store_initialized",
                extra={"mode": "sqlite", "db_path": self.db_path},
            )
        else:
            # 纯内存模式：无需额外初始化
            logger.info(
                "review_store_initialized",
                extra={"mode": "memory"},
            )

    def _init_sqlite(self) -> None:
        """初始化 SQLite 数据库表结构 / Initialize SQLite Schema.

        创建 classification_reviews 表（如果不存在），包含复核条目的所有字段。
        使用 IF NOT EXISTS 确保幂等性（多次调用不报错）。
        """
        # 断言 db_path 不为 None（调用方已确保）
        assert self.db_path is not None
        # 连接 SQLite 数据库（timeout=10s 防止并发写入死锁）
        with sqlite3.connect(self.db_path, timeout=10.0) as conn:
            # 创建复核记录表（12 个字段对应 ReviewEntry 模型的所有属性）
            conn.execute(
                "CREATE TABLE IF NOT EXISTS classification_reviews ("
                "review_id TEXT PRIMARY KEY, "      # 复核条目唯一 ID
                "record_index INTEGER, "            # 所属记录索引
                "field_name TEXT, "                 # 字段名
                "field_value TEXT, "                # 字段原始值
                "predicted_level TEXT, "            # 预测敏感度等级
                "predicted_tags TEXT, "             # 预测标签列表（JSON 序列化）
                "corrected_level TEXT, "            # 修正后等级（人工确认）
                "reviewer TEXT, "                   # 复核人标识
                "comment TEXT, "                    # 复核说明
                "status TEXT, "                     # 状态（PENDING/CONFIRMED）
                "created_at TEXT, "                 # 创建时间（UTC ISO）
                "updated_at TEXT)"                  # 更新时间（UTC ISO）
            )

    def add_from_record(
        self,
        record_result: Any,
        original_record: dict[str, Any] | None = None,
    ) -> list[ReviewEntry]:
        """从记录分类结果中自动提取需要人工复核的字段 / Auto-Extract Fields Needing Human Review.

        执行步骤 / Execution Steps:
        1. 遍历字段分类结果，收集 needs_human_review=True 的字段。
           (Iterate field results and collect fields with needs_human_review=True)
        2. 若记录整体需要复核但没有具体字段，创建记录级复核条目。
           (If record needs review but no specific fields, create record-level review entry)
        3. 将复核条目存储到内存和 SQLite（若启用）。
           (Store review entries to memory and SQLite if enabled)
        4. 更新 Prometheus 队列大小指标。
           (Update Prometheus queue size metric)

        Args:
            record_result: RecordClassificationResult 实例 / RecordClassificationResult instance.
            original_record: 原始记录字典（用于提取字段原始值） / Original record dictionary.

        Returns:
            新增复核条目列表 / List of new review entries.
        """
        # 收集本次新增的复核条目
        entries: list[ReviewEntry] = []
        # 遍历记录中每个字段的分类结果
        for field_name, field_result in record_result.field_results.items():
            # 跳过不需要人工复核的字段
            if not field_result.needs_human_review:
                continue
            # 生成唯一的复核条目 ID
            review_id = f"review-{uuid.uuid4().hex[:12]}"
            # 尝试从原始记录中获取字段值（用于复核时参考）
            field_value = None
            if original_record is not None:
                field_value = original_record.get(field_name)
            # 构造复核条目对象
            entry = ReviewEntry(
                review_id=review_id,
                record_index=record_result.record_index,  # 所属记录索引
                field_name=field_name,                     # 字段名
                field_value=str(field_value) if field_value is not None else None,  # 字段值字符串化
                predicted_level=field_result.final_level,  # 预测等级
                predicted_tags=[str(tag) for tag in field_result.tags],  # 预测标签列表
                status=ReviewStatus.PENDING,               # 初始状态：待复核
                created_at=datetime.now(timezone.utc).isoformat(),  # 创建时间
            )
            entries.append(entry)

        # 记录级复核：当记录整体需要复核但没有具体字段需要复核时
        # （例如复合规则触发的记录级升级）
        if record_result.needs_human_review and not entries:
            review_id = f"review-{uuid.uuid4().hex[:12]}"
            entry = ReviewEntry(
                review_id=review_id,
                record_index=record_result.record_index,
                field_name="__record__",  # 特殊标记：表示记录级复核（非字段级）
                field_value=None,
                predicted_level=record_result.final_level,
                predicted_tags=[str(tag) for tag in record_result.aggregated_tags],
                status=ReviewStatus.PENDING,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            entries.append(entry)

        # 在锁内将新条目写入内存和 SQLite
        with self._lock:
            for entry in entries:
                # 写入内存字典
                self._mem[entry.review_id] = entry
                # 如果启用了 SQLite，同步写入数据库
                if self.db_path:
                    self._insert_sqlite(entry)
            # 更新 Prometheus 指标：当前待复核（PENDING）条目数量
            CLASSIFICATION_REVIEW_QUEUE_SIZE.set(
                sum(1 for e in self._mem.values() if e.status == ReviewStatus.PENDING)
            )
        # 返回本次新增的复核条目列表
        return entries

    def confirm(
        self,
        review_id: str,
        corrected_level: str,
        reviewer: str = "",
        comment: str = "",
    ) -> ReviewEntry:
        """确认或修正复核条目 / Confirm or Correct a Review Entry.

        安全专家通过此方法完成人工复核：
        - 确认预测等级正确：corrected_level 与 predicted_level 相同
        - 修正预测等级：corrected_level 为新的等级值

        Args:
            review_id: 复核条目 ID / Review entry ID.
            corrected_level: 修正后的敏感度等级 / Corrected sensitivity level.
            reviewer: 复核人标识 / Reviewer identifier.
            comment: 复核说明 / Review comment.

        Returns:
            更新后的 ReviewEntry / Updated ReviewEntry.

        Raises:
            KeyError: 复核条目不存在 / Review entry not found.
        """
        # 获取锁保护状态更新
        with self._lock:
            # 检查复核条目是否存在
            if review_id not in self._mem:
                raise KeyError(f"review not found: {review_id}")
            # 获取复核条目对象
            entry = self._mem[review_id]
            # 更新修正等级
            entry.corrected_level = corrected_level
            # 记录复核人
            entry.reviewer = reviewer
            # 记录复核说明
            entry.comment = comment
            # 更新状态为已确认
            entry.status = ReviewStatus.CONFIRMED
            # 记录更新时间
            entry.updated_at = datetime.now(timezone.utc).isoformat()
            # 如果启用了 SQLite，同步更新数据库
            if self.db_path:
                self._update_sqlite(entry)
            # 更新 Prometheus 指标：重新计算待复核条目数
            CLASSIFICATION_REVIEW_QUEUE_SIZE.set(
                sum(1 for e in self._mem.values() if e.status == ReviewStatus.PENDING)
            )
            # 返回更新后的条目
            return entry

    def export(self, format: str = "jsonl", mask_input: bool = False) -> str:  # noqa: A002
        """导出复核样本 / Export Review Samples.

        支持两种导出格式：
        - JSONL：每行一个 JSON 对象，适合 LLM 微调数据管道
        - CSV：表格格式，适合 Excel 查看和传统 ML 训练

        导出字段包含：
        - review_id, input, predicted_level, predicted_tags
        - corrected_level, reviewer, comment, status
        - fine_tuning_text：预格式化的微调训练文本

        Args:
            format: 导出格式 / Export format (`jsonl` or `csv`).
            mask_input: 是否对 input 字段脱敏（隐私保护） / Whether to mask input field values.

        Returns:
            导出内容字符串 / Exported content string.

        Raises:
            ValueError: 不支持的导出格式 / Unsupported export format.
        """
        # 在锁内获取所有复核条目的快照（避免导出过程中数据变更）
        with self._lock:
            entries = list(self._mem.values())

        # 构建导出行列表
        rows = []
        for entry in entries:
            # 获取字段原始值
            value = entry.field_value
            # 如果启用脱敏，对字段值进行掩码处理
            if mask_input and value is not None:
                value = redact(value)
            # 构造导出行字典
            row = {
                "review_id": entry.review_id,
                # input 格式：字段名|字段值（或仅字段名）
                "input": f"{entry.field_name}|{value}" if value is not None else entry.field_name,
                # 预测等级（枚举值转字符串）
                "predicted_level": entry.predicted_level.value if entry.predicted_level else None,
                "predicted_tags": entry.predicted_tags,
                "corrected_level": entry.corrected_level,
                "reviewer": entry.reviewer,
                "comment": entry.comment,
                "status": entry.status.value,
                # 预构建的微调训练文本
                "fine_tuning_text": self._build_fine_tuning_text(entry, value),
            }
            rows.append(row)

        # 根据格式输出
        if format == "jsonl":
            # JSONL：每行一个 JSON 对象（ensure_ascii=False 保留中文）
            return "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
        if format == "csv":
            # CSV：使用 DictWriter 写入内存 StringIO
            output = io.StringIO()
            writer = csv.DictWriter(output, fieldnames=rows[0].keys() if rows else [])
            writer.writeheader()  # 写入表头
            writer.writerows(rows)  # 写入数据行
            return output.getvalue()
        # 不支持的格式抛出异常
        raise ValueError(f"unsupported export format: {format}")

    def _build_fine_tuning_text(self, entry: ReviewEntry, value: str | None) -> str:
        """构造 LLM 微调格式文本 / Build Fine-Tuning Text for LLM Training.

        生成结构化的训练样本格式：
        ### Input
        字段名: xxx
        字段值: xxx
        ### Predicted
        L3
        ### Corrected
        L4
        ### Comment
        包含敏感传染病关键字

        Args:
            entry: 复核条目。
            value: 字段值（可能已脱敏）。

        Returns:
            格式化的微调训练文本。
        """
        # 构造输入部分
        input_text = f"字段名: {entry.field_name}\n字段值: {value if value else ''}"
        # 获取预测等级字符串
        predicted = entry.predicted_level.value if entry.predicted_level else "UNKNOWN"
        # 修正等级：如果人工未修正则使用预测值
        corrected = entry.corrected_level or predicted
        # 组装完整的微调训练文本
        return (
            f"### Input\n{input_text}\n"
            f"### Predicted\n{predicted}\n"
            f"### Corrected\n{corrected}\n"
            f"### Comment\n{entry.comment or ''}"
        )

    def _load_sqlite(self) -> None:
        """从 SQLite 加载已有复核记录到内存 / Load Existing Reviews from SQLite.

        进程重启后调用，保证历史复核数据不丢失。
        将所有记录加载到内存字典中，后续查询直接走内存（高性能）。
        """
        # 断言 db_path 不为 None
        assert self.db_path is not None
        # 连接数据库并读取所有复核记录
        with sqlite3.connect(self.db_path, timeout=10.0) as conn:
            rows = conn.execute("SELECT * FROM classification_reviews").fetchall()
        # 遍历每行数据，构造 ReviewEntry 对象并存入内存
        for row in rows:
            entry = ReviewEntry(
                review_id=row[0],           # 复核 ID
                record_index=row[1],        # 记录索引
                field_name=row[2],          # 字段名
                field_value=row[3],         # 字段值
                predicted_level=SensitivityLevel(row[4]) if row[4] else None,  # 预测等级
                predicted_tags=json.loads(row[5]) if row[5] else [],  # 标签列表（JSON 反序列化）
                corrected_level=row[6],     # 修正等级
                reviewer=row[7] or "",      # 复核人
                comment=row[8] or "",       # 说明
                status=ReviewStatus(row[9]),  # 状态
                created_at=row[10],         # 创建时间
                updated_at=row[11],         # 更新时间
            )
            # 存入内存字典
            self._mem[row[0]] = entry
        # 更新 Prometheus 指标：待复核条目数
        CLASSIFICATION_REVIEW_QUEUE_SIZE.set(
            sum(1 for e in self._mem.values() if e.status == ReviewStatus.PENDING)
        )

    def _insert_sqlite(self, entry: ReviewEntry) -> None:
        """将新复核条目插入 SQLite / Insert New Review Entry to SQLite.

        注意：此方法在 _lock 锁内调用，无需额外加锁。

        Args:
            entry: 待插入的复核条目。
        """
        assert self.db_path is not None
        # 连接数据库并执行 INSERT
        with sqlite3.connect(self.db_path, timeout=10.0) as conn:
            conn.execute(
                "INSERT INTO classification_reviews VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    entry.review_id,        # 复核 ID
                    entry.record_index,     # 记录索引
                    entry.field_name,       # 字段名
                    entry.field_value,      # 字段值
                    # 预测等级：枚举值转字符串（None 保持 None）
                    entry.predicted_level.value if entry.predicted_level else None,
                    # 标签列表：JSON 序列化存储
                    json.dumps(entry.predicted_tags, ensure_ascii=False),
                    entry.corrected_level,  # 修正等级
                    entry.reviewer,         # 复核人
                    entry.comment,          # 说明
                    entry.status.value,     # 状态
                    entry.created_at,       # 创建时间
                    entry.updated_at,       # 更新时间
                ),
            )

    def _update_sqlite(self, entry: ReviewEntry) -> None:
        """更新已有复核条目到 SQLite / Update Existing Review Entry in SQLite.

        仅更新人工复核相关字段（corrected_level, reviewer, comment, status, updated_at），
        不修改原始预测字段。

        注意：此方法在 _lock 锁内调用，无需额外加锁。

        Args:
            entry: 已更新的复核条目。
        """
        assert self.db_path is not None
        # 连接数据库并执行 UPDATE（按 review_id 定位）
        with sqlite3.connect(self.db_path, timeout=10.0) as conn:
            conn.execute(
                "UPDATE classification_reviews SET corrected_level=?, reviewer=?, comment=?, "
                "status=?, updated_at=? WHERE review_id=?",
                (
                    entry.corrected_level,  # 修正等级
                    entry.reviewer,         # 复核人
                    entry.comment,          # 说明
                    entry.status.value,     # 状态
                    entry.updated_at,       # 更新时间
                    entry.review_id,        # WHERE 条件：复核 ID
                ),
            )
