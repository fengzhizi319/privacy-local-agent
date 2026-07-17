"""Zero-Knowledge scanning utilities for classification.

提供日志脱敏、字段值掩码等工具，确保原始数据不进入日志、指标、
持久化存储或任何外部系统。
"""

import hashlib
import logging
from typing import Any, Dict, Optional


def redact(value: Any, max_len: int = 8, placeholder: str = "***") -> str:
    """对原始值进行脱敏，仅保留前 `max_len` 个字符，其余替换为占位符。

    Args:
        value: 待脱敏的原始值。
        max_len: 保留的最大明文长度。
        placeholder: 替换后缀的占位符。

    Returns:
        脱敏后的字符串。若 value 为 None 则返回空字符串。
    """
    if value is None:
        return ""
    text = str(value)
    if len(text) <= max_len:
        return text
    return text[:max_len] + placeholder


def hash_value(value: Any, algorithm: str = "sha256") -> str:
    """对原始值进行哈希，用于复核导出等需要唯一标识但不需要明文的场景。

    Args:
        value: 待哈希的原始值。
        algorithm: 哈希算法，默认 sha256。

    Returns:
        十六进制哈希字符串。
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
    """判断一个值是否可以安全地完整打印到日志中。

    仅允许元数据（字段名、枚举值、数字、短布尔值等）完整打印；
    字符串长度超过 16 或包含疑似敏感内容时不建议完整打印。

    Args:
        value: 待判断的值。

    Returns:
        是否可以安全完整打印。
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
    """安全地记录日志，自动对所有字符串字段值进行脱敏。

    Args:
        logger: 日志记录器。
        level: 日志级别，如 logging.INFO。
        msg: 日志消息模板。
        **fields: 需要格式化的字段，字符串字段会被自动 redact。
    """
    safe_fields: Dict[str, Any] = {}
    for key, value in fields.items():
        if isinstance(value, str):
            safe_fields[key] = redact(value)
        else:
            safe_fields[key] = value
    logger.log(level, msg, safe_fields)


def mask_record_values(record: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """将记录中的所有字段值脱敏，用于复核导出等场景。

    Args:
        record: 原始记录。

    Returns:
        所有值均已脱敏的记录副本。
    """
    if record is None:
        return {}
    return {key: redact(value) for key, value in record.items()}
