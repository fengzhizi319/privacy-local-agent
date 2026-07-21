"""基于 pandas 的向量化规则引擎可选插件。

中文说明：
`VectorizedRuleEngine` 保持与 `DefaultRuleEngine` 相同的规则语义，但针对
pandas Series/DataFrame 做批量匹配，适合大数据集表分类场景。
未安装 pandas 时，构造本引擎会抛出 ImportError，调用方可据此回退到标量引擎。

English Description:
Optional pandas-based vectorized rule engine plugin.
`VectorizedRuleEngine` maintains the same rule semantics as `DefaultRuleEngine` but
performs batch matching on pandas Series/DataFrame, suitable for large dataset
table classification scenarios. Raises ImportError when pandas is not installed,
allowing callers to fall back to the scalar engine.
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List

from ..observability.logging_config import get_logger
from ..observability.metrics import (
    CLASSIFICATION_RULE_HITS_TOTAL,
    CLASSIFICATION_VECTORIZED_BATCH_SIZE,
    CLASSIFICATION_VECTORIZED_BATCH_TOTAL,
)
from .classification_models import ClassificationParams, SecurityTag, SensitivityLevel
from .classification_rule_engine import (
    RuleEngine,
    _id_card_checksum,
    _in_icd10_interval,
    _normalize_field_name,
    _normalize_icd10,
    _shanghai_medical_card_checksum,
    _unique_tags,
)

# Module-level structured logger for vectorized engine events
logger = get_logger(__name__)


class VectorizedRuleEngine(RuleEngine):
    """向量化规则引擎 / Vectorized Rule Engine.

    中文说明：
    通过 pandas Series 批量执行 Layer-1 规则，显著降低 Python 行级循环开销。
    同时保留标量 `evaluate` 接口，兼容 `ClassificationAPI.classify_field`。

    English Description:
    Executes Layer-1 rules in batch via pandas Series, significantly reducing
    Python row-level loop overhead. Also retains the scalar `evaluate` interface
    for compatibility with `ClassificationAPI.classify_field`.

    Raises:
        ImportError: 当前环境未安装 pandas / pandas is not installed.
    """

    def __init__(self) -> None:
        """初始化向量化引擎 / Initialize Vectorized Engine.

        Raises:
            ImportError: pandas 未安装 / pandas not installed.
        """
        import pandas as pd

        self._pd = pd
        logger.info("vectorized_rule_engine_initialized", extra={"backend": "pandas"})

    def evaluate(self, field_name: str, value: Any, params: ClassificationParams) -> List[SecurityTag]:
        """标量兼容接口 / Scalar Compatibility Interface.

        中文说明：单值包装为 Series 后批量评估。
        English Description: Wraps a single value into a Series for batch evaluation.

        Args:
            field_name: 字段名 / Field name.
            value: 单个字段值 / Single field value.
            params: 分类参数 / Classification parameters.

        Returns:
            命中的 SecurityTag 列表 / List of matched SecurityTags.
        """
        series = self._pd.Series([value], dtype=object).fillna("")
        return self.evaluate_series(field_name, series, params)[0]

    def evaluate_series(
        self,
        field_name: str,
        series: Any,
        params: ClassificationParams,
    ) -> List[List[SecurityTag]]:
        """对整列批量执行 Layer-1 规则 / Batch Execute Layer-1 Rules on Column.

        执行步骤 / Execution Steps:
        1. 字段名规则匹配（基因组、金融、PII 等）。
           (Field-name rule matching: genomic, finance, PII, etc.)
        2. 值规则匹配（身份证、手机号、ICD-10 等）。
           (Value-based rule matching: ID card, mobile, ICD-10, etc.)
        3. 合规模板扩展字段名规则。
           (Compliance template extended field-name rules)
        4. 白名单与运营统计字段过滤。
           (Whitelist and operational statistics field filtering)
        5. 每行去重并返回。
           (Deduplicate per row and return)

        Args:
            field_name: 字段名 / Field name.
            series: pandas Series，长度 N / pandas Series of length N.
            params: 分类参数 / Classification parameters.

        Returns:
            长度为 N 的列表，每个元素对应该行的 SecurityTag 列表。
            (List of length N, each element is the SecurityTag list for that row)
        """
        pd = self._pd
        n = len(series)
        tags: List[List[SecurityTag]] = [[] for _ in range(n)]

        start_time = time.monotonic()
        CLASSIFICATION_VECTORIZED_BATCH_TOTAL.labels(field_name=field_name).inc()
        CLASSIFICATION_VECTORIZED_BATCH_SIZE.observe(n)

        norm_name = _normalize_field_name(field_name)
        str_series = series.astype(str).where(series.notna(), "")

        # ------------------------------------------------------------------
        # 4.1 字段名规则 / Field-name based rules
        # ------------------------------------------------------------------

        if any(kw in norm_name for kw in ("brca1", "brca2", "tp53")):
            self._add_all(
                tags,
                level=SensitivityLevel.L5,
                category="GENOMIC_BRCA_TP53",
                rule_id="RULE_ID_G_001",
            )

        if re.search(r"rs\d+", norm_name) or any(
            kw in norm_name for kw in ("snp", "cnv", "genome", "genomic")
        ):
            self._add_all(
                tags,
                level=SensitivityLevel.L5,
                category="GENOMIC_VARIANT",
                rule_id="RULE_ID_G_002",
            )
        else:
            # 字段值中也可能出现 rs 编号
            norm_value_series = str_series.apply(_normalize_field_name)
            mask = norm_value_series.str.contains(r"rs\d+", regex=True, na=False)
            self._add_where(
                tags,
                mask,
                level=SensitivityLevel.L5,
                category="GENOMIC_VARIANT",
                rule_id="RULE_ID_G_002",
            )

        if any(kw in norm_name for kw in ("gene", "mutation", "variant")):
            self._add_all(
                tags,
                level=SensitivityLevel.L5,
                category="GENOMIC_HINT",
                rule_id="RULE_ID_G_003",
            )

        if any(kw in norm_name for kw in ("bam", "vcf", "fastq")):
            self._add_all(
                tags,
                level=SensitivityLevel.L5,
                category="GENOMIC_FILE",
                rule_id="RULE_ID_G_004",
            )

        # ------------------------------------------------------------------
        # 4.2 值规则 / Value-based rules
        # ------------------------------------------------------------------

        id_card_mask = str_series.apply(_id_card_checksum)
        self._add_where(
            tags,
            id_card_mask,
            level=SensitivityLevel.L3,
            category="PII_ID_CARD",
            rule_id="RULE_ID_001",
        )

        mobile_mask = str_series.str.match(r"^1[3-9]\d{9}$", na=False)
        self._add_where(
            tags,
            mobile_mask,
            level=SensitivityLevel.L3,
            category="PII_MOBILE",
            rule_id="RULE_ID_002",
        )

        medical_card_mask = str_series.apply(_shanghai_medical_card_checksum)
        self._add_where(
            tags,
            medical_card_mask,
            level=SensitivityLevel.L3,
            category="PII_MEDICAL_CARD",
            rule_id="RULE_ID_003",
        )

        # ICD-10：先解析，再逐区间判断，最后未命中区间的有效编码标记为 GENERAL
        codes = str_series.apply(_normalize_icd10)
        valid_mask = codes.notna()
        if valid_mask.any():
            assigned = self._pd.Series([False] * n)
            for interval in params.icd10_l4_intervals:
                start = interval.get("start", "")
                end = interval.get("end", "")
                if not start or not end:
                    continue
                mask = ~assigned & valid_mask & codes.apply(
                    lambda c: bool(c is not None and _in_icd10_interval(c, start, end))
                )
                if mask.any():
                    start_upper = start.upper()
                    if start_upper.startswith("B"):
                        category = "MEDICAL_ICD10_HIV"
                    elif start_upper.startswith("F"):
                        category = "MEDICAL_ICD10_PSYCHIATRIC"
                    elif start_upper.startswith("C"):
                        category = "MEDICAL_ICD10_CANCER"
                    else:
                        category = "MEDICAL_ICD10_GENERAL"
                    self._add_where(
                        tags,
                        mask,
                        level=SensitivityLevel.L4,
                        category=category,
                        rule_id="RULE_ID_004",
                    )
                    assigned |= mask
            general_mask = valid_mask & ~assigned
            self._add_where(
                tags,
                general_mask,
                level=SensitivityLevel.L3,
                category="MEDICAL_ICD10_GENERAL",
                rule_id="RULE_ID_004",
            )

        # BAM/VCF/FASTQ 文件头
        bam_mask = str_series.str.startswith(("BAM\x01", "@SQ"), na=False)
        self._add_where(
            tags,
            bam_mask,
            level=SensitivityLevel.L5,
            category="GENOMIC_BAM",
            rule_id="RULE_ID_G_010",
        )

        vcf_mask = str_series.str.startswith("##fileformat=VCF", na=False)
        self._add_where(
            tags,
            vcf_mask,
            level=SensitivityLevel.L5,
            category="GENOMIC_VCF",
            rule_id="RULE_ID_G_011",
        )

        fastq_mask = str_series.str.startswith("@", na=False) & (
            str_series.str.contains("SRR|ERR|DRR", regex=True, na=False)
            | str_series.apply(
                lambda s: len(s.splitlines()) >= 3 and s.splitlines()[2].strip() == "+"
            )
        )
        self._add_where(
            tags,
            fastq_mask,
            level=SensitivityLevel.L5,
            category="GENOMIC_FASTQ",
            rule_id="RULE_ID_G_012",
        )

        sequence_mask = str_series.str.contains(r"[ATCGNatcgn]{50,}", regex=True, na=False)
        self._add_where(
            tags,
            sequence_mask,
            level=SensitivityLevel.L5,
            category="GENOMIC_SEQUENCE",
            rule_id="RULE_ID_G_013",
        )

        # ------------------------------------------------------------------
        # 合规模板扩展字段名规则
        # ------------------------------------------------------------------

        self._apply_template_field_rules(tags, norm_name, params)

        # ------------------------------------------------------------------
        # 白名单与运营统计字段（按字段名匹配）
        # ------------------------------------------------------------------

        norm_public_whitelist = [_normalize_field_name(kw) for kw in params.public_field_whitelist]
        if any(kw in norm_name for kw in norm_public_whitelist):
            self._add_all(
                tags,
                level=SensitivityLevel.L1,
                category="PUBLIC_REPORT",
                rule_id="RULE_ID_L1_001",
            )

        norm_operational = [_normalize_field_name(kw) for kw in params.operational_field_patterns]
        if any(kw in norm_name for kw in norm_operational):
            self._add_all(
                tags,
                level=SensitivityLevel.L2,
                category="OPERATIONAL_STAT",
                rule_id="RULE_ID_L2_001",
            )

        # 每行去重
        result = [_unique_tags(row_tags) for row_tags in tags]

        duration = time.monotonic() - start_time
        hit_count = sum(1 for row_tags in result if row_tags)
        logger.debug(
            "vectorized_batch_evaluated",
            extra={
                "field_name": field_name,
                "batch_size": n,
                "hit_rows": hit_count,
                "duration_s": round(duration, 4),
            },
        )
        return result

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _apply_template_field_rules(
        self,
        tags: List[List[SecurityTag]],
        norm_name: str,
        params: ClassificationParams,
    ) -> None:
        """根据合规模板扩展字段名规则 / Apply Compliance Template Field Rules.

        中文说明：应用于所有行。
        English Description: Applied to all rows.

        Args:
            tags: 标签结果矩阵 / Tag result matrix.
            norm_name: 归一化字段名 / Normalized field name.
            params: 分类参数 / Classification parameters.
        """
        template = params.template
        if not template:
            return

        template = str(template).lower()

        if template == "jrt0197":
            if any(
                kw in norm_name
                for kw in ("bankcard", "cardno", "credit", "transaction", "asset", "balance", "account")
            ):
                self._add_all(
                    tags,
                    level=SensitivityLevel.L4,
                    category="FINANCE_ACCOUNT",
                    rule_id="RULE_ID_JRT_001",
                )

        if template in ("gbt35273", "gdpr"):
            if any(kw in norm_name for kw in ("email", "address", "location", "轨迹")):
                self._add_all(
                    tags,
                    level=SensitivityLevel.L3,
                    category="PII_CONTACT_LOCATION",
                    rule_id="RULE_ID_GBT_001",
                )

        if template == "gdpr":
            if any(
                kw in norm_name
                for kw in (
                    "biometric",
                    "fingerprint",
                    "face",
                    "health",
                    "genetic",
                    "race",
                    "ethnicity",
                    "political",
                    "religion",
                    "sexual",
                )
            ):
                self._add_all(
                    tags,
                    level=SensitivityLevel.L4,
                    category="GDPR_SPECIAL_CATEGORY",
                    rule_id="RULE_ID_GDPR_001",
                )

    def _add_all(
        self,
        tags: List[List[SecurityTag]],
        level: SensitivityLevel,
        category: str,
        rule_id: str,
    ) -> None:
        """为每一行添加同一个规则标签 / Add Same Rule Tag to All Rows.

        Args:
            tags: 标签结果矩阵 / Tag result matrix.
            level: 敏感度等级 / Sensitivity level.
            category: 分类类别 / Classification category.
            rule_id: 规则 ID / Rule ID.
        """
        tag = SecurityTag(
            level=level,
            category=category,
            source_engine="RULE",
            rule_id=rule_id,
        )
        for row_tags in tags:
            row_tags.append(tag)
        CLASSIFICATION_RULE_HITS_TOTAL.labels(rule_id=rule_id).inc()

    def _add_where(
        self,
        tags: List[List[SecurityTag]],
        mask: Any,
        level: SensitivityLevel,
        category: str,
        rule_id: str,
    ) -> None:
        """为 mask 为 True 的行添加规则标签 / Add Rule Tag Where Mask is True.

        Args:
            tags: 标签结果矩阵 / Tag result matrix.
            mask: 布尔掩码 / Boolean mask.
            level: 敏感度等级 / Sensitivity level.
            category: 分类类别 / Classification category.
            rule_id: 规则 ID / Rule ID.
        """
        tag = SecurityTag(
            level=level,
            category=category,
            source_engine="RULE",
            rule_id=rule_id,
        )
        hit_count = 0
        for i, hit in enumerate(mask):
            if hit:
                tags[i].append(tag)
                hit_count += 1
        if hit_count > 0:
            CLASSIFICATION_RULE_HITS_TOTAL.labels(rule_id=rule_id).inc(hit_count)


__all__ = ["VectorizedRuleEngine"]
