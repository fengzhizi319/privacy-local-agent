"""Built-in compliance rule templates for data classification.

内置合规规则模板：JR/T 0197（金融）、GB/T 35273（通用个人信息）、GDPR。
模板通过扩展默认参数实现，可被 profile 和 request 参数覆盖。
"""

from typing import Any, Dict, Optional


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
