"""数据分类规则引擎实现 / Data Classification Rule Engine Implementation.

中文说明：
提供默认规则引擎 DefaultRuleEngine、向后兼容的 RuleEngine 抽象接口，
以及规则匹配所需的内部工具函数（字段名归一化、身份证号/医保卡校验、
ICD-10 区间判断、标签去重等）。

English Description:
Provides the default rule engine DefaultRuleEngine, backward-compatible RuleEngine
abstract interface, and internal utility functions for rule matching (field name
normalization, ID card/medical card checksum validation, ICD-10 interval checking,
tag deduplication, etc.).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from ...observability.logging_config import get_logger
from ...observability.metrics import CLASSIFICATION_RULE_HITS_TOTAL
from .classification_models import ClassificationParams, RuleEngineABC, SecurityTag, SensitivityLevel

# Module-level structured logger for rule engine events
logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# 默认值与常量 / Defaults and Constants
# ---------------------------------------------------------------------------

_ID_CARD_WEIGHTS = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
_ID_CARD_CHARS = ["1", "0", "X", "9", "8", "7", "6", "5", "4", "3", "2"]

_SH_MEDICAL_WEIGHTS = [7, 9, 10, 5, 8, 4, 2, 1]


# ---------------------------------------------------------------------------
# 工具函数 / Utility Functions
# ---------------------------------------------------------------------------


def _normalize_field_name(name: str) -> str:
    """规范化字段名 / Normalize Field Name.

    中文说明：转小写并移除下划线与空格，用于模式匹配。
    English Description: Converts to lowercase and removes underscores and spaces for pattern matching.

    Args:
        name: 原始字段名 / Original field name.

    Returns:
        规范化后的字段名 / Normalized field name.
    """
    return str(name).lower().replace("_", "").replace(" ", "")


def _normalize_icd10(code: str) -> Optional[Tuple[str, int]]:
    """解析并归一化 ICD-10 编码 / Parse and Normalize ICD-10 Code.

    Args:
        code: ICD-10 编码字符串 / ICD-10 code string.

    Returns:
        (letter, two-digit number) 元组，无效时返回 None / Tuple or None if invalid.
    """
    match = re.match(r"^([A-Z])(\d{2})(?:\.\d{0,2})?$", code.upper())
    if not match:
        return None
    return match.group(1), int(match.group(2))


def _in_icd10_interval(code: Tuple[str, int], start: str, end: str) -> bool:
    """判断 ICD-10 编码是否落在闭区间 / Check if ICD-10 Code Falls Within Closed Interval.

    Args:
        code: 归一化后的 (letter, number) 元组 / Normalized (letter, number) tuple.
        start: 区间起点字符串 / Interval start string (e.g. "B20").
        end: 区间终点字符串 / Interval end string (e.g. "B24").

    Returns:
        是否在区间内 / Whether code is within the interval.
    """
    start_norm = _normalize_icd10(start)
    end_norm = _normalize_icd10(end)
    if not start_norm or not end_norm:
        return False
    return start_norm <= code <= end_norm


def _id_card_checksum(value: str) -> bool:
    """校验中国大陆 18 位身份证号校验码 / Validate Chinese ID Card Checksum.

    中文说明：校验 18 位身份证号的格式和校验码是否正确。
    English Description: Validates the format and checksum of an 18-digit Chinese ID card number.

    Args:
        value: 身份证号字符串 / ID card number string.

    Returns:
        校验是否通过 / Whether checksum validation passes.
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
    """校验上海医保卡号 9 位数字校验码 / Validate Shanghai Medical Card Checksum.

    Args:
        value: 医保卡号字符串 / Medical card number string.

    Returns:
        校验是否通过 / Whether checksum validation passes.
    """
    if not re.match(r"^\d{9}$", value):
        return False
    digits = [int(c) for c in value]
    total = sum(digits[i] * _SH_MEDICAL_WEIGHTS[i] for i in range(8))
    expected = (10 - total % 10) % 10
    return digits[8] == expected


def _unique_tags(tags: List[SecurityTag]) -> List[SecurityTag]:
    """去重安全标签 / Deduplicate Security Tags.

    中文说明：以 level+category 为键保留顺序。
    English Description: Deduplicates tags by (level, category) key while preserving order.

    Args:
        tags: 原始标签列表 / Original tag list.

    Returns:
        去重后的标签列表 / Deduplicated tag list.
    """
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
# 规则引擎 / Rule Engine
# ---------------------------------------------------------------------------


class RuleEngine(RuleEngineABC):
    """向后兼容的 RuleEngine 抽象接口 / Backward-Compatible RuleEngine Abstract Interface.

    中文说明：RuleEngineABC 的别名子类，保持向后兼容性。
    English Description: Alias subclass of RuleEngineABC for backward compatibility.
    """


class DefaultRuleEngine(RuleEngine):
    """默认规则引擎 / Default Rule Engine.

    中文说明：
    实现规范中全部 Layer-1 规则，包括字段名规则、值规则、合规模板扩展规则等。

    English Description:
    Implements all Layer-1 rules from the specification, including field-name rules,
    value-based rules, and compliance template extension rules.
    """

    def evaluate(
        self, field_name: str, value: Any, params: ClassificationParams
    ) -> List[SecurityTag]:
        """按字段名与字段值评估规则并收集标签 / Evaluate Rules by Field Name and Value.

        执行步骤 / Execution Steps:
        1. 规范化字段名和字段值。
           (Normalize field name and value)
        2. 执行字段名规则匹配（基因、 genomic 文件等）。
           (Execute field-name rule matching for genomic, etc.)
        3. 执行值规则匹配（身份证、手机号、医保卡、ICD-10 等）。
           (Execute value-based rule matching for ID card, mobile, medical card, ICD-10, etc.)
        4. 执行合规模板扩展字段名规则。
           (Execute compliance template extension field-name rules)
        5. 执行白名单与运营统计字段规则。
           (Execute whitelist and operational statistics field rules)
        6. 去重并返回标签列表。
           (Deduplicate and return tag list)

        Args:
            field_name: 字段名 / Field name.
            value: 字段值 / Field value (will be converted to string).
            params: 分类参数 / Classification parameters.

        Returns:
            命中的 SecurityTag 列表 / List of matched SecurityTags.
        """
        tags: List[SecurityTag] = []
        norm_name = _normalize_field_name(field_name)
        str_value = str(value) if value is not None else ""

        # Step 1: Field-name based rules (genomic indicators)
        norm_value = _normalize_field_name(str_value)

        # Genomic BRCA/TP53 gene indicators (highest sensitivity L5)
        if any(kw in norm_name for kw in ("brca1", "brca2", "tp53")):
            tags.append(
                SecurityTag(
                    level=SensitivityLevel.L5,
                    category="GENOMIC_BRCA_TP53",
                    source_engine="RULE",
                    rule_id="RULE_ID_G_001",
                )
            )
            CLASSIFICATION_RULE_HITS_TOTAL.labels(rule_id="RULE_ID_G_001").inc()

        # Genomic variant indicators (rs numbers, SNP, CNV, etc.)
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
            CLASSIFICATION_RULE_HITS_TOTAL.labels(rule_id="RULE_ID_G_002").inc()

        # Genomic hint indicators (gene, mutation, variant)
        if any(kw in norm_name for kw in ("gene", "mutation", "variant")):
            tags.append(
                SecurityTag(
                    level=SensitivityLevel.L5,
                    category="GENOMIC_HINT",
                    source_engine="RULE",
                    rule_id="RULE_ID_G_003",
                )
            )
            CLASSIFICATION_RULE_HITS_TOTAL.labels(rule_id="RULE_ID_G_003").inc()

        # Genomic file format indicators (BAM, VCF, FASTQ)
        if any(kw in norm_name for kw in ("bam", "vcf", "fastq")):
            tags.append(
                SecurityTag(
                    level=SensitivityLevel.L5,
                    category="GENOMIC_FILE",
                    source_engine="RULE",
                    rule_id="RULE_ID_G_004",
                )
            )
            CLASSIFICATION_RULE_HITS_TOTAL.labels(rule_id="RULE_ID_G_004").inc()

        # Step 2: Value-based rules (PII identifiers)
        # Chinese ID card number (18 digits with checksum)
        if _id_card_checksum(str_value):
            tags.append(
                SecurityTag(
                    level=SensitivityLevel.L3,
                    category="PII_ID_CARD",
                    source_engine="RULE",
                    rule_id="RULE_ID_001",
                )
            )
            CLASSIFICATION_RULE_HITS_TOTAL.labels(rule_id="RULE_ID_001").inc()

        # Chinese mobile phone number (11 digits starting with 1)
        if re.match(r"^1[3-9]\d{9}$", str_value):
            tags.append(
                SecurityTag(
                    level=SensitivityLevel.L3,
                    category="PII_MOBILE",
                    source_engine="RULE",
                    rule_id="RULE_ID_002",
                )
            )
            CLASSIFICATION_RULE_HITS_TOTAL.labels(rule_id="RULE_ID_002").inc()

        # Shanghai medical card number (9 digits with checksum)
        if _shanghai_medical_card_checksum(str_value):
            tags.append(
                SecurityTag(
                    level=SensitivityLevel.L3,
                    category="PII_MEDICAL_CARD",
                    source_engine="RULE",
                    rule_id="RULE_ID_003",
                )
            )
            CLASSIFICATION_RULE_HITS_TOTAL.labels(rule_id="RULE_ID_003").inc()

        # ICD-10 medical code (with L4 escalation for sensitive diseases)
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
            CLASSIFICATION_RULE_HITS_TOTAL.labels(rule_id="RULE_ID_004").inc()

        # Step 3: Genomic file content detection (BAM/VCF/FASTQ headers)
        if str_value.startswith("BAM\x01") or str_value.startswith("@SQ"):
            tags.append(
                SecurityTag(
                    level=SensitivityLevel.L5,
                    category="GENOMIC_BAM",
                    source_engine="RULE",
                    rule_id="RULE_ID_G_010",
                )
            )
            CLASSIFICATION_RULE_HITS_TOTAL.labels(rule_id="RULE_ID_G_010").inc()

        if str_value.startswith("##fileformat=VCF"):
            tags.append(
                SecurityTag(
                    level=SensitivityLevel.L5,
                    category="GENOMIC_VCF",
                    source_engine="RULE",
                    rule_id="RULE_ID_G_011",
                )
            )
            CLASSIFICATION_RULE_HITS_TOTAL.labels(rule_id="RULE_ID_G_011").inc()

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
            CLASSIFICATION_RULE_HITS_TOTAL.labels(rule_id="RULE_ID_G_012").inc()

        # Genomic sequence detection (long ATCGN sequences)
        if re.search(r"[ATCGNatcgn]{50,}", str_value):
            tags.append(
                SecurityTag(
                    level=SensitivityLevel.L5,
                    category="GENOMIC_SEQUENCE",
                    source_engine="RULE",
                    rule_id="RULE_ID_G_013",
                )
            )
            CLASSIFICATION_RULE_HITS_TOTAL.labels(rule_id="RULE_ID_G_013").inc()

        # Step 4: Compliance template extension field-name rules
        tags.extend(self._apply_template_field_rules(norm_name, params))

        # Step 5: Whitelist and operational statistics field rules
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
            CLASSIFICATION_RULE_HITS_TOTAL.labels(rule_id="RULE_ID_L1_001").inc()

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
            CLASSIFICATION_RULE_HITS_TOTAL.labels(rule_id="RULE_ID_L2_001").inc()

        # Step 6: Deduplicate and return
        return _unique_tags(tags)

    def _apply_template_field_rules(
        self, norm_name: str, params: ClassificationParams
    ) -> List[SecurityTag]:
        """根据合规模板扩展字段名规则 / Apply Compliance Template Extension Field Rules.

        中文说明：根据激活的合规模板（JR/T 0197、GB/T 35273、GDPR）添加额外的字段名规则。
        English Description: Adds additional field-name rules based on the active compliance template.

        Args:
            norm_name: 规范化后的字段名 / Normalized field name.
            params: 分类参数 / Classification parameters.

        Returns:
            命中的 SecurityTag 列表 / List of matched SecurityTags.
        """
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
