"""数据分类规则引擎实现。

提供默认规则引擎 DefaultRuleEngine、向后兼容的 RuleEngine 抽象接口，
以及规则匹配所需的内部工具函数（字段名归一化、身份证号/医保卡校验、
ICD-10 区间判断、标签去重等）。
"""

import re
from typing import Any, Dict, List, Optional, Tuple

from .classification_models import ClassificationParams, RuleEngineABC, SecurityTag, SensitivityLevel


# ---------------------------------------------------------------------------
# 默认值与常量 / Defaults and constants
# ---------------------------------------------------------------------------

_ID_CARD_WEIGHTS = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
_ID_CARD_CHARS = ["1", "0", "X", "9", "8", "7", "6", "5", "4", "3", "2"]

_SH_MEDICAL_WEIGHTS = [7, 9, 10, 5, 8, 4, 2, 1]


# ---------------------------------------------------------------------------
# 工具函数 / Utility functions
# ---------------------------------------------------------------------------


def _normalize_field_name(name: str) -> str:
    """规范化字段名：转小写并移除下划线与空格。

    Normalizes a field name for pattern matching (case-insensitive, strip
    underscores and spaces).
    """
    return str(name).lower().replace("_", "").replace(" ", "")


def _normalize_icd10(code: str) -> Optional[Tuple[str, int]]:
    """解析并归一化 ICD-10 编码。

    Returns a tuple of (letter, two-digit number) or None if invalid.
    """
    match = re.match(r"^([A-Z])(\d{2})(?:\.\d{0,2})?$", code.upper())
    if not match:
        return None
    return match.group(1), int(match.group(2))


def _in_icd10_interval(code: Tuple[str, int], start: str, end: str) -> bool:
    """判断 ICD-10 编码是否落在闭区间 [start, end]。

    Args:
        code: 归一化后的 (letter, number) 元组。
        start: 区间起点字符串，如 "B20"。
        end: 区间终点字符串，如 "B24"。

    Returns:
        是否在区间内。
    """
    start_norm = _normalize_icd10(start)
    end_norm = _normalize_icd10(end)
    if not start_norm or not end_norm:
        return False
    return start_norm <= code <= end_norm


def _id_card_checksum(value: str) -> bool:
    """校验中国大陆 18 位身份证号校验码。

    Validates the checksum of an 18-digit Chinese ID card number.
    """
    if len(value) != 18:
        return False
    if not re.match(r"^[1-9]\d{5}(18|19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\d{3}[\dXx]$", value):
        return False
    try:
        total = sum(int(value[i]) * _ID_CARD_WEIGHTS[i] for i in range(17))
        expected = _ID_CARD_CHARS[total % 11]
        return value[17].upper() == expected
    except (ValueError, IndexError):
        return False


def _shanghai_medical_card_checksum(value: str) -> bool:
    """校验上海医保卡号 9 位数字校验码。

    Validates the checksum of a 9-digit Shanghai medical card number.
    """
    if not re.match(r"^\d{9}$", value):
        return False
    digits = [int(c) for c in value]
    total = sum(digits[i] * _SH_MEDICAL_WEIGHTS[i] for i in range(8))
    expected = (10 - total % 10) % 10
    return digits[8] == expected


def _unique_tags(tags: List[SecurityTag]) -> List[SecurityTag]:
    """去重安全标签，以 level+category 为键保留顺序。"""
    seen: set = set()
    result: List[SecurityTag] = []
    for tag in tags:
        key = (tag.level.value, tag.category)
        if key in seen:
            continue
        seen.add(key)
        result.append(tag)
    return result


# ---------------------------------------------------------------------------
# 规则引擎 / Rule engine
# ---------------------------------------------------------------------------


class RuleEngine(RuleEngineABC):
    """向后兼容的 RuleEngine 抽象接口（RuleEngineABC 的别名子类）。"""


class DefaultRuleEngine(RuleEngine):
    """默认规则引擎，实现规范中全部 Layer-1 规则。"""

    def evaluate(
        self, field_name: str, value: Any, params: ClassificationParams
    ) -> List[SecurityTag]:
        """按字段名与字段值评估规则并收集标签。

        Args:
            field_name: 字段名。
            value: 字段值，会被转换为字符串处理。
            params: 分类参数，包含 ICD-10 区间、基因关键字等配置。

        Returns:
            命中的 SecurityTag 列表。
        """
        tags: List[SecurityTag] = []
        norm_name = _normalize_field_name(field_name)
        str_value = str(value) if value is not None else ""

        # 4.1 字段名规则 / Field-name based rules
        norm_value = _normalize_field_name(str_value)

        if any(kw in norm_name for kw in ("brca1", "brca2", "tp53")):
            tags.append(
                SecurityTag(
                    level=SensitivityLevel.L5,
                    category="GENOMIC_BRCA_TP53",
                    source_engine="RULE",
                    rule_id="RULE_ID_G_001",
                )
            )

        if re.search(r"rs\d+", norm_name) or re.search(r"rs\d+", norm_value) or any(
            kw in norm_name for kw in ("snp", "cnv", "genome", "genomic")
        ):
            tags.append(
                SecurityTag(
                    level=SensitivityLevel.L5,
                    category="GENOMIC_VARIANT",
                    source_engine="RULE",
                    rule_id="RULE_ID_G_002",
                )
            )

        if any(kw in norm_name for kw in ("gene", "mutation", "variant")):
            tags.append(
                SecurityTag(
                    level=SensitivityLevel.L5,
                    category="GENOMIC_HINT",
                    source_engine="RULE",
                    rule_id="RULE_ID_G_003",
                )
            )

        if any(kw in norm_name for kw in ("bam", "vcf", "fastq")):
            tags.append(
                SecurityTag(
                    level=SensitivityLevel.L5,
                    category="GENOMIC_FILE",
                    source_engine="RULE",
                    rule_id="RULE_ID_G_004",
                )
            )

        # 4.2 值规则 / Value-based rules
        if _id_card_checksum(str_value):
            tags.append(
                SecurityTag(
                    level=SensitivityLevel.L3,
                    category="PII_ID_CARD",
                    source_engine="RULE",
                    rule_id="RULE_ID_001",
                )
            )

        if re.match(r"^1[3-9]\d{9}$", str_value):
            tags.append(
                SecurityTag(
                    level=SensitivityLevel.L3,
                    category="PII_MOBILE",
                    source_engine="RULE",
                    rule_id="RULE_ID_002",
                )
            )

        if _shanghai_medical_card_checksum(str_value):
            tags.append(
                SecurityTag(
                    level=SensitivityLevel.L3,
                    category="PII_MEDICAL_CARD",
                    source_engine="RULE",
                    rule_id="RULE_ID_003",
                )
            )

        icd = _normalize_icd10(str_value)
        if icd:
            level = SensitivityLevel.L3
            category = "MEDICAL_ICD10_GENERAL"
            for interval in params.icd10_l4_intervals:
                start = interval.get("start", "")
                end = interval.get("end", "")
                if _in_icd10_interval(icd, start, end):
                    level = SensitivityLevel.L4
                    if start.upper().startswith("B"):
                        category = "MEDICAL_ICD10_HIV"
                    elif start.upper().startswith("F"):
                        category = "MEDICAL_ICD10_PSYCHIATRIC"
                    elif start.upper().startswith("C"):
                        category = "MEDICAL_ICD10_CANCER"
                    break
            tags.append(
                SecurityTag(
                    level=level,
                    category=category,
                    source_engine="RULE",
                    rule_id="RULE_ID_004",
                )
            )

        # BAM/VCF/FASTQ 文件头
        if str_value.startswith("BAM\x01") or str_value.startswith("@SQ"):
            tags.append(
                SecurityTag(
                    level=SensitivityLevel.L5,
                    category="GENOMIC_BAM",
                    source_engine="RULE",
                    rule_id="RULE_ID_G_010",
                )
            )

        if str_value.startswith("##fileformat=VCF"):
            tags.append(
                SecurityTag(
                    level=SensitivityLevel.L5,
                    category="GENOMIC_VCF",
                    source_engine="RULE",
                    rule_id="RULE_ID_G_011",
                )
            )

        lines = str_value.splitlines()
        if str_value.startswith("@") and (
            any(token in str_value for token in ("SRR", "ERR", "DRR"))
            or (len(lines) >= 3 and lines[2].strip() == "+")
        ):
            tags.append(
                SecurityTag(
                    level=SensitivityLevel.L5,
                    category="GENOMIC_FASTQ",
                    source_engine="RULE",
                    rule_id="RULE_ID_G_012",
                )
            )

        if re.search(r"[ATCGNatcgn]{50,}", str_value):
            tags.append(
                SecurityTag(
                    level=SensitivityLevel.L5,
                    category="GENOMIC_SEQUENCE",
                    source_engine="RULE",
                    rule_id="RULE_ID_G_013",
                )
            )

        # 合规模板扩展字段名规则
        tags.extend(self._apply_template_field_rules(norm_name, params))

        # 白名单与运营统计字段（按字段名匹配，与测试用例一致）
        norm_public_whitelist = [_normalize_field_name(kw) for kw in params.public_field_whitelist]
        if any(kw in norm_name for kw in norm_public_whitelist):
            tags.append(
                SecurityTag(
                    level=SensitivityLevel.L1,
                    category="PUBLIC_REPORT",
                    source_engine="RULE",
                    rule_id="RULE_ID_L1_001",
                )
            )

        norm_operational = [_normalize_field_name(kw) for kw in params.operational_field_patterns]
        if any(kw in norm_name for kw in norm_operational):
            tags.append(
                SecurityTag(
                    level=SensitivityLevel.L2,
                    category="OPERATIONAL_STAT",
                    source_engine="RULE",
                    rule_id="RULE_ID_L2_001",
                )
            )

        return _unique_tags(tags)

    def _apply_template_field_rules(
        self, norm_name: str, params: ClassificationParams
    ) -> List[SecurityTag]:
        """根据合规模板扩展字段名规则。"""
        tags: List[SecurityTag] = []
        template = params.template
        if not template:
            return tags

        template = str(template).lower()

        if template == "jrt0197":
            if any(kw in norm_name for kw in ("bankcard", "bankcard", "cardno", "credit", "transaction", "asset", "balance", "account")):
                tags.append(
                    SecurityTag(
                        level=SensitivityLevel.L4,
                        category="FINANCE_ACCOUNT",
                        source_engine="RULE",
                        rule_id="RULE_ID_JRT_001",
                    )
                )

        if template in ("gbt35273", "gdpr"):
            if any(kw in norm_name for kw in ("email", "address", "location", "轨迹")):
                tags.append(
                    SecurityTag(
                        level=SensitivityLevel.L3,
                        category="PII_CONTACT_LOCATION",
                        source_engine="RULE",
                        rule_id="RULE_ID_GBT_001",
                    )
                )

        if template == "gdpr":
            if any(kw in norm_name for kw in ("biometric", "fingerprint", "face", "health", "genetic", "race", "ethnicity", "political", "religion", "sexual")):
                tags.append(
                    SecurityTag(
                        level=SensitivityLevel.L4,
                        category="GDPR_SPECIAL_CATEGORY",
                        source_engine="RULE",
                        rule_id="RULE_ID_GDPR_001",
                    )
                )

        return tags


__all__ = ["RuleEngine", "DefaultRuleEngine", "_unique_tags"]
