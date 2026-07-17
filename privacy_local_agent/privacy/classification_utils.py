"""分类子系统公共工具与适配器。

本模块合并三类能力：
- Zero-Knowledge 日志/导出安全工具（redact、hash_value、safe_log 等）
- 合规模板默认参数（JR/T 0197、GB/T 35273、GDPR）
- SecretFlow 联邦数据结构适配器
"""

import hashlib
import logging
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Zero-Knowledge 安全工具
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# 合规模板默认参数
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

TEMPLATES: Dict[str, Dict[str, Any]] = {
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


def get_template_params(template: Optional[str]) -> Dict[str, Any]:
    """获取指定合规模板的默认参数。

    Args:
        template: 模板名称，如 `gbt35273`、`gdpr`、`jrt0197`。

    Returns:
        模板参数字典；模板不存在时返回空字典。
    """
    if template is None:
        return {}
    return dict(TEMPLATES.get(template, {}))


# ---------------------------------------------------------------------------
# SecretFlow 联邦数据结构适配器
# ---------------------------------------------------------------------------


from .data_adapters import to_records


def classify_secretflow(
    api: Any,
    sf_data: Any,
    params: Optional[Dict[str, Any]] = None,
    party: Optional[str] = None,
) -> Any:
    """对 SecretFlow 数据结构进行分类。

    Args:
        api: ClassificationAPI 实例。
        sf_data: SecretFlow DataFrame / HDataFrame / VDataFrame / FedNdarray。
        params: 请求级分类参数。
        party: HDataFrame 参与方；单 partition 时可省略。

    Returns:
        ClassificationResult，与同步表分类结果结构一致。

    Raises:
        ImportError: 未安装 secretflow 或 pandas。
        TypeError: 传入不支持的 SecretFlow 类型。
    """
    records = to_records(sf_data, party=party)
    return api.classify_table(
        schema=list(records[0].keys()) if records else [],
        rows=records,
        params=params,
    )


__all__ = [
    "redact",
    "hash_value",
    "should_log_value",
    "safe_log",
    "mask_record_values",
    "TEMPLATES",
    "get_template_params",
    "classify_secretflow",
]
