"""分类子系统公共工具与适配器 / Classification Subsystem Utilities and Adapters.

中文说明：
本模块合并三类能力：
- Zero-Knowledge 日志/导出安全工具（redact、hash_value、safe_log 等）
- 合规模板默认参数（JR/T 0197、GB/T 35273、GDPR）
- SecretFlow 联邦数据结构适配器

English Description:
This module combines three capabilities:
- Zero-Knowledge logging/export security utilities (redact, hash_value, safe_log, etc.)
- Compliance template default parameters (JR/T 0197, GB/T 35273, GDPR)
- SecretFlow federated data structure adapter
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

from ...observability.logging_config import get_logger
from ..data_adapters import to_records

if TYPE_CHECKING:
    import logging

# Module-level structured logger for classification utils events
logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Zero-Knowledge 安全工具 / Zero-Knowledge Security Utilities
# ---------------------------------------------------------------------------


def redact(value: Any, max_len: int = 8, placeholder: str = "***") -> str:
    """对原始值进行脱敏 / Redact Original Value.

    中文说明：仅保留前 `max_len` 个字符，其余替换为占位符。
    English Description: Keeps only the first `max_len` characters, replaces the rest with placeholder.

    Args:
        value: 待脱敏的原始值 / Original value to redact.
        max_len: 保留的最大明文长度 / Maximum plaintext length to keep.
        placeholder: 替换后缀的占位符 / Placeholder for replaced suffix.

    Returns:
        脱敏后的字符串 / Redacted string (empty string if value is None).
    """
    if value is None:
        return ""
    text = str(value)
    if len(text) <= max_len:
        return text
    return text[:max_len] + placeholder


def hash_value(value: Any, algorithm: str = "sha256") -> str:
    """对原始值进行哈希 / Hash Original Value.

    中文说明：用于复核导出等需要唯一标识但不需要明文的场景。
    English Description: Used for scenarios requiring unique identification without plaintext.

    Args:
        value: 待哈希的原始值 / Original value to hash.
        algorithm: 哈希算法 / Hash algorithm (default: sha256).

    Returns:
        十六进制哈希字符串 / Hexadecimal hash string.

    Raises:
        ValueError: 不支持的哈希算法 / Unsupported hash algorithm.
    """
    if value is None:
        value = ""
    data = str(value).encode("utf-8")
    if algorithm == "sha256":
        return hashlib.sha256(data).hexdigest()
    if algorithm == "md5":
        return hashlib.md5(data).hexdigest()
    raise ValueError(f"unsupported hash algorithm: {algorithm}")


def should_log_value(value: Any) -> bool:
    """判断一个值是否可以安全地完整打印到日志中 / Check if Value Can Be Safely Logged.

    中文说明：
    仅允许元数据（字段名、枚举值、数字、短布尔值等）完整打印；
    字符串长度超过 16 或包含疑似敏感内容时不建议完整打印。

    English Description:
    Only allows metadata (field names, enum values, numbers, short booleans) to be
    fully logged; strings longer than 16 or containing suspected sensitive content
    are not recommended for full logging.

    Args:
        value: 待判断的值 / Value to check.

    Returns:
        是否可以安全完整打印 / Whether it can be safely fully logged.
    """
    if value is None:
        return True
    if isinstance(value, bool):
        return True
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        return len(value) <= 16
    return False


def safe_log(
    logger: logging.Logger,
    level: int,
    msg: str,
    **fields: Any,
) -> None:
    """安全地记录日志 / Safely Log with Auto-Redaction.

    中文说明：自动对所有字符串字段值进行脱敏。
    English Description: Automatically redacts all string field values.

    Args:
        logger: 日志记录器 / Logger instance.
        level: 日志级别 / Log level (e.g. logging.INFO).
        msg: 日志消息模板 / Log message template.
        **fields: 需要格式化的字段 / Fields to format (strings auto-redacted).
    """
    safe_fields: dict[str, Any] = {}
    for key, value in fields.items():
        if isinstance(value, str):
            safe_fields[key] = redact(value)
        else:
            safe_fields[key] = value
    logger.log(level, msg, safe_fields)


def mask_record_values(record: dict[str, Any] | None) -> dict[str, str]:
    """将记录中的所有字段值脱敏 / Mask All Field Values in Record.

    中文说明：用于复核导出等场景。
    English Description: Used for review export scenarios.

    Args:
        record: 原始记录 / Original record.

    Returns:
        所有值均已脱敏的记录副本 / Record copy with all values redacted.
    """
    if record is None:
        return {}
    return {key: redact(value) for key, value in record.items()}


# ---------------------------------------------------------------------------
# 合规模板默认参数 / Compliance Template Default Parameters
# ---------------------------------------------------------------------------


# 通用个人信息字段模式（GB/T 35273 核心）
_GBT35273_FIELD_PATTERNS = [
    "name",
    "id_card",
    "mobile",
    "phone",
    "address",
    "email",
    "location",
    "轨迹",
]

# GDPR 敏感字段模式
_GDPR_FIELD_PATTERNS = [
    "biometric",
    "health",
    "genetic",
    "race",
    "ethnicity",
    "political",
    "religion",
    "sexual",
]

# JR/T 0197 金融字段模式
_JRT0197_FIELD_PATTERNS = [
    "bank_card",
    "bankcard",
    "account",
    "card_no",
    "credit",
    "transaction",
    "asset",
    "balance",
]

TEMPLATES: dict[str, dict[str, Any]] = {
    "gbt35273": {
        "version": "gbt35273-1.0.0",
        "default_level": "L3",
        # 扩展基因关键字，加强对个人生物识别/行踪的识别
        "genomic_keywords": [
            "brca1", "brca2", "tp53", "rs", "snp", "cnv", "genome", "genomic",
            "gene", "mutation", "variant", "biometric", "fingerprint", "face",
        ],
        # 扩展 ICD-10 L4 区间，覆盖更多敏感疾病
        "icd10_l4_intervals": [
            {"start": "B20", "end": "B24"},
            {"start": "F20", "end": "F29"},
            {"start": "C00", "end": "C97"},
            {"start": "E10", "end": "E14"},  # 糖尿病
        ],
    },
    "gdpr": {
        "version": "gdpr-1.0.0",
        "default_level": "L3",
        "genomic_keywords": [
            "brca1", "brca2", "tp53", "rs", "snp", "cnv", "genome", "genomic",
            "gene", "mutation", "variant", "biometric", "health", "genetic",
            "race", "ethnicity", "political", "religion", "sexual",
        ],
        "icd10_l4_intervals": [
            {"start": "B20", "end": "B24"},
            {"start": "F20", "end": "F29"},
            {"start": "C00", "end": "C97"},
        ],
    },
    "jrt0197": {
        "version": "jrt0197-1.0.0",
        "default_level": "L3",
        # 金融场景下银行卡号、交易账号等需要更高敏感度
        "genomic_keywords": [
            "brca1", "brca2", "tp53", "rs", "snp", "cnv", "genome", "genomic",
            "gene", "mutation", "variant", "bank_card", "bankcard", "card_no",
            "account", "credit", "transaction", "asset", "balance",
        ],
        "icd10_l4_intervals": [
            {"start": "B20", "end": "B24"},
            {"start": "F20", "end": "F29"},
            {"start": "C00", "end": "C97"},
        ],
    },
}


def get_template_params(template: str | None) -> dict[str, Any]:
    """获取指定合规模板的默认参数 / Get Compliance Template Default Parameters.

    Args:
        template: 模板名称 / Template name (e.g. `gbt35273`, `gdpr`, `jrt0197`).

    Returns:
        模板参数字典 / Template parameter dictionary (empty dict if template not found).
    """
    if template is None:
        return {}
    return dict(TEMPLATES.get(template, {}))


# ---------------------------------------------------------------------------
# SecretFlow 联邦数据结构适配器 / SecretFlow Federated Data Adapter
# ---------------------------------------------------------------------------


def classify_secretflow(
    api: Any,
    sf_data: Any,
    params: dict[str, Any] | None = None,
    party: str | None = None,
) -> Any:
    """对 SecretFlow 数据结构进行分类 / Classify SecretFlow Data Structure.

    Args:
        api: ClassificationAPI 实例 / ClassificationAPI instance.
        sf_data: SecretFlow 数据结构 / SecretFlow data structure.
        params: 请求级分类参数 / Request-level classification parameters.
        party: HDataFrame 参与方 / HDataFrame party identifier.

    Returns:
        ClassificationResult / Classification result.

    Raises:
        ImportError: 未安装 secretflow 或 pandas / secretflow or pandas not installed.
        TypeError: 传入不支持的 SecretFlow 类型 / Unsupported SecretFlow type.
    """
    records = to_records(sf_data, party=party)
    return api.classify_table(
        schema=list(records[0].keys()) if records else [],
        rows=records,
        params=params,
    )


__all__ = [
    "TEMPLATES",
    "classify_secretflow",
    "get_template_params",
    "hash_value",
    "mask_record_values",
    "redact",
    "safe_log",
    "should_log_value",
]
