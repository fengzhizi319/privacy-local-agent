"""基于 pandas 的向量化规则引擎可选插件。

中文说明：
`VectorizedRuleEngine` 保持与 `DefaultRuleEngine` 相同的规则语义，但针对
pandas Series/DataFrame 做批量匹配，适合大数据集表分类场景。
未安装 pandas 时，构造本引擎会抛出 ImportError，调用方可据此回退到标量引擎。

性能优势：
- 标量引擎（DefaultRuleEngine）：逐行调用 evaluate()，N 行需要 N 次 Python 函数调用
- 向量化引擎（VectorizedRuleEngine）：利用 pandas 的 str 向量化操作，
  一次调用处理整列 N 行数据，减少 Python 循环开销

使用场景：
- classify_table() 接口处理大批量数据时自动选择向量化引擎
- classify_field() 单值接口通过标量兼容路径调用

English Description:
Optional pandas-based vectorized rule engine plugin.
`VectorizedRuleEngine` maintains the same rule semantics as `DefaultRuleEngine` but
performs batch matching on pandas Series/DataFrame, suitable for large dataset
table classification scenarios. Raises ImportError when pandas is not installed,
allowing callers to fall back to the scalar engine.
"""

# 启用延迟注解求值，允许在类型提示中引用尚未定义的类名
from __future__ import annotations

# 导入正则表达式模块，用于字段名模式匹配和值规则
import re
# 导入时间模块，用于测量批量评估耗时
import time
# 导入 Any 类型注解
from typing import Any

# 导入结构化日志工厂函数
from ...observability.logging_config import get_logger
# 导入 Prometheus 指标：
# - CLASSIFICATION_RULE_HITS_TOTAL：规则命中计数
# - CLASSIFICATION_VECTORIZED_BATCH_SIZE：批量大小直方图
# - CLASSIFICATION_VECTORIZED_BATCH_TOTAL：批量评估次数计数
from ...observability.metrics import (
    CLASSIFICATION_RULE_HITS_TOTAL,
    CLASSIFICATION_VECTORIZED_BATCH_SIZE,
    CLASSIFICATION_VECTORIZED_BATCH_TOTAL,
)
# 导入数据模型
from .classification_models import ClassificationParams, SecurityTag, SensitivityLevel
# 从标量规则引擎导入共享的工具函数和基类
from .classification_rule_engine import (
    RuleEngine,                        # 规则引擎基类
    _id_card_checksum,                 # 身份证号校验
    _in_icd10_interval,                # ICD-10 区间判断
    _normalize_field_name,             # 字段名规范化
    _normalize_icd10,                  # ICD-10 编码解析
    _shanghai_medical_card_checksum,   # 上海医保卡校验
    _unique_tags,                      # 标签去重
)

# 创建模块级结构化日志器
logger = get_logger(__name__)


class VectorizedRuleEngine(RuleEngine):
    """向量化规则引擎 / Vectorized Rule Engine.

    通过 pandas Series 批量执行 Layer-1 规则，显著降低 Python 行级循环开销。
    同时保留标量 `evaluate` 接口，兼容 `ClassificationAPI.classify_field`。

    与 DefaultRuleEngine 的规则语义完全一致：
    - 相同的字段名规则（基因组、金融、PII）
    - 相同的值规则（身份证、手机号、医保卡、ICD-10）
    - 相同的文件内容检测（BAM/VCF/FASTQ）
    - 相同的合规模板扩展规则
    - 相同的白名单/运营字段降级规则

    Raises:
        ImportError: 当前环境未安装 pandas / pandas is not installed.
    """

    def __init__(self) -> None:
        """初始化向量化引擎 / Initialize Vectorized Engine.

        尝试导入 pandas，如果未安装则抛出 ImportError。
        调用方（ClassificationAPI）捕获此异常后回退到 DefaultRuleEngine。

        Raises:
            ImportError: pandas 未安装 / pandas not installed.
        """
        # 尝试导入 pandas（未安装时此处抛出 ImportError）
        import pandas as pd

        # 保存 pandas 模块引用为实例属性，供后续方法使用
        self._pd = pd
        # 记录引擎初始化成功的日志
        logger.info("vectorized_rule_engine_initialized", extra={"backend": "pandas"})

    def evaluate(self, field_name: str, value: Any, params: ClassificationParams) -> list[SecurityTag]:
        """标量兼容接口 / Scalar Compatibility Interface.

        将单个值包装为长度为 1 的 pandas Series，然后调用批量评估方法。
        这保证了 VectorizedRuleEngine 可以无缝替换 DefaultRuleEngine。

        Args:
            field_name: 字段名 / Field name.
            value: 单个字段值 / Single field value.
            params: 分类参数 / Classification parameters.

        Returns:
            命中的 SecurityTag 列表 / List of matched SecurityTags.
        """
        # 将单值包装为 pandas Series（dtype=object 避免类型推断），空值填充为空字符串
        series = self._pd.Series([value], dtype=object).fillna("")
        # 调用批量评估方法，取第一行（也是唯一一行）的结果
        return self.evaluate_series(field_name, series, params)[0]

    def evaluate_series(
        self,
        field_name: str,
        series: Any,
        params: ClassificationParams,
    ) -> list[list[SecurityTag]]:
        """对整列批量执行 Layer-1 规则 / Batch Execute Layer-1 Rules on Column.

        这是向量化引擎的核心方法，一次调用处理整列 N 行数据。
        利用 pandas 的 str 向量化操作（str.match、str.contains、str.startswith）
        替代 Python 逐行循环，大幅提升大数据集的处理性能。

        执行步骤 / Execution Steps:
        1. 初始化标签矩阵（N 行 × 空列表）并记录指标。
        2. 字段名规则匹配（基因组关键词 → 全列命中）。
        3. 值规则匹配（身份证/手机号/医保卡/ICD-10 → 按行掩码命中）。
        4. 文件内容检测（BAM/VCF/FASTQ 头 → 按行掩码命中）。
        5. 合规模板扩展字段名规则。
        6. 白名单与运营统计字段过滤。
        7. 每行去重并返回。

        Args:
            field_name: 字段名 / Field name.
            series: pandas Series，长度 N / pandas Series of length N.
            params: 分类参数 / Classification parameters.

        Returns:
            长度为 N 的列表，每个元素对应该行的 SecurityTag 列表。
            (List of length N, each element is the SecurityTag list for that row)
        """
        # 获取 Series 长度（即数据行数）
        n = len(series)
        # 初始化标签矩阵：N 个空列表，每个列表存放对应行的标签
        tags: list[list[SecurityTag]] = [[] for _ in range(n)]

        # 记录批量评估开始时间（用于计算耗时）
        start_time = time.monotonic()
        # 递增 Prometheus 指标：批量评估次数 +1
        CLASSIFICATION_VECTORIZED_BATCH_TOTAL.labels(field_name=field_name).inc()
        # 记录批量大小到直方图指标
        CLASSIFICATION_VECTORIZED_BATCH_SIZE.observe(n)

        # 规范化字段名（小写 + 去下划线/空格）
        norm_name = _normalize_field_name(field_name)
        # 将 Series 统一转为字符串类型，NaN 替换为空字符串
        str_series = series.astype(str).where(series.notna(), "")

        # ==================================================================
        # 4.1 字段名规则 / Field-name based rules
        # 字段名规则是列级别的：如果字段名匹配，则该列所有行都命中
        # ==================================================================

        # 规则 RULE_ID_G_001：BRCA/TP53 基因字段名 → 全列 L5
        if any(kw in norm_name for kw in ("brca1", "brca2", "tp53")):
            self._add_all(
                tags,
                level=SensitivityLevel.L5,
                category="GENOMIC_BRCA_TP53",
                rule_id="RULE_ID_G_001",
            )

        # 规则 RULE_ID_G_002：基因组变异字段名（rs编号/SNP/CNV/genome）
        if re.search(r"rs\d+", norm_name) or any(
            kw in norm_name for kw in ("snp", "cnv", "genome", "genomic")
        ):
            # 字段名匹配：全列命中
            self._add_all(
                tags,
                level=SensitivityLevel.L5,
                category="GENOMIC_VARIANT",
                rule_id="RULE_ID_G_002",
            )
        else:
            # 字段名不匹配时，检查字段值中是否包含 rs 编号（如 "rs12345"）
            # 先对每行值做规范化
            norm_value_series = str_series.apply(_normalize_field_name)
            # 使用 pandas str.contains 向量化匹配 rs+数字 模式
            mask = norm_value_series.str.contains(r"rs\d+", regex=True, na=False)
            # 仅对匹配的行添加标签
            self._add_where(
                tags,
                mask,
                level=SensitivityLevel.L5,
                category="GENOMIC_VARIANT",
                rule_id="RULE_ID_G_002",
            )

        # 规则 RULE_ID_G_003：基因组提示字段名（gene/mutation/variant）→ 全列 L5
        if any(kw in norm_name for kw in ("gene", "mutation", "variant")):
            self._add_all(
                tags,
                level=SensitivityLevel.L5,
                category="GENOMIC_HINT",
                rule_id="RULE_ID_G_003",
            )

        # 规则 RULE_ID_G_004：基因组文件格式字段名（bam/vcf/fastq）→ 全列 L5
        if any(kw in norm_name for kw in ("bam", "vcf", "fastq")):
            self._add_all(
                tags,
                level=SensitivityLevel.L5,
                category="GENOMIC_FILE",
                rule_id="RULE_ID_G_004",
            )

        # ==================================================================
        # 4.2 值规则 / Value-based rules
        # 值规则是行级别的：根据每行的实际值判断是否命中
        # ==================================================================

        # 规则 RULE_ID_001：身份证号校验（逐行调用校验函数生成布尔掩码）
        id_card_mask = str_series.apply(_id_card_checksum)
        self._add_where(
            tags,
            id_card_mask,
            level=SensitivityLevel.L3,
            category="PII_ID_CARD",
            rule_id="RULE_ID_001",
        )

        # 规则 RULE_ID_002：手机号正则匹配（使用 pandas str.match 向量化）
        mobile_mask = str_series.str.match(r"^1[3-9]\d{9}$", na=False)
        self._add_where(
            tags,
            mobile_mask,
            level=SensitivityLevel.L3,
            category="PII_MOBILE",
            rule_id="RULE_ID_002",
        )

        # 规则 RULE_ID_003：上海医保卡校验（逐行调用校验函数）
        medical_card_mask = str_series.apply(_shanghai_medical_card_checksum)
        self._add_where(
            tags,
            medical_card_mask,
            level=SensitivityLevel.L3,
            category="PII_MEDICAL_CARD",
            rule_id="RULE_ID_003",
        )

        # 规则 RULE_ID_004：ICD-10 医疗编码
        # 处理逻辑：先解析所有行的 ICD-10 编码，再逐区间判断敏感等级
        # 最后将未命中任何敏感区间的有效编码标记为 L3 GENERAL
        codes = str_series.apply(_normalize_icd10)  # 解析每行的 ICD-10 编码
        valid_mask = codes.notna()  # 有效 ICD-10 编码的掩码
        if valid_mask.any():
            # assigned 跟踪哪些行已被分配到特定敏感区间（避免重复标记）
            assigned = self._pd.Series([False] * n)
            # 遍历配置的 L4 敏感区间（HIV/精神疾病/恶性肿瘤等）
            for interval in params.icd10_l4_intervals:
                start = interval.get("start", "")  # 区间起始编码
                end = interval.get("end", "")      # 区间结束编码
                # 跳过无效区间配置
                if not start or not end:
                    continue
                # 构建掩码：未分配 & 有效编码 & 落在当前区间内
                mask = ~assigned & valid_mask & codes.apply(
                    lambda c, start=start, end=end: bool(c is not None and _in_icd10_interval(c, start, end))
                )
                if mask.any():
                    # 根据区间首字母确定疾病类别
                    start_upper = start.upper()
                    if start_upper.startswith("B"):
                        category = "MEDICAL_ICD10_HIV"          # B 开头：HIV 相关
                    elif start_upper.startswith("F"):
                        category = "MEDICAL_ICD10_PSYCHIATRIC"  # F 开头：精神疾病
                    elif start_upper.startswith("C"):
                        category = "MEDICAL_ICD10_CANCER"       # C 开头：恶性肿瘤
                    else:
                        category = "MEDICAL_ICD10_GENERAL"      # 其他：一般
                    # 对命中行添加 L4 标签
                    self._add_where(
                        tags,
                        mask,
                        level=SensitivityLevel.L4,
                        category=category,
                        rule_id="RULE_ID_004",
                    )
                    # 标记这些行已分配（后续区间不再重复处理）
                    assigned |= mask
            # 未命中任何敏感区间的有效 ICD-10 编码标记为 L3 一般医疗
            general_mask = valid_mask & ~assigned
            self._add_where(
                tags,
                general_mask,
                level=SensitivityLevel.L3,
                category="MEDICAL_ICD10_GENERAL",
                rule_id="RULE_ID_004",
            )

        # ==================================================================
        # 基因组文件内容检测（BAM/VCF/FASTQ 文件头）
        # ==================================================================

        # 规则 RULE_ID_G_010：BAM 文件头检测（"BAM\x01" 或 "@SQ" 开头）
        bam_mask = str_series.str.startswith(("BAM\x01", "@SQ"), na=False)
        self._add_where(
            tags,
            bam_mask,
            level=SensitivityLevel.L5,
            category="GENOMIC_BAM",
            rule_id="RULE_ID_G_010",
        )

        # 规则 RULE_ID_G_011：VCF 文件头检测（"##fileformat=VCF" 开头）
        vcf_mask = str_series.str.startswith("##fileformat=VCF", na=False)
        self._add_where(
            tags,
            vcf_mask,
            level=SensitivityLevel.L5,
            category="GENOMIC_VCF",
            rule_id="RULE_ID_G_011",
        )

        # 规则 RULE_ID_G_012：FASTQ 文件检测
        # 条件：以 @ 开头 且（包含 SRA 编号 或 第3行为 "+"）
        fastq_mask = str_series.str.startswith("@", na=False) & (
            str_series.str.contains("SRR|ERR|DRR", regex=True, na=False)  # SRA 登录号
            | str_series.apply(
                lambda s: len(s.splitlines()) >= 3 and s.splitlines()[2].strip() == "+"  # FASTQ 第3行
            )
        )
        self._add_where(
            tags,
            fastq_mask,
            level=SensitivityLevel.L5,
            category="GENOMIC_FASTQ",
            rule_id="RULE_ID_G_012",
        )

        # 规则 RULE_ID_G_013：长碱基序列检测（连续 50+ 个 ATCGN 字符）
        sequence_mask = str_series.str.contains(r"[ATCGNatcgn]{50,}", regex=True, na=False)
        self._add_where(
            tags,
            sequence_mask,
            level=SensitivityLevel.L5,
            category="GENOMIC_SEQUENCE",
            rule_id="RULE_ID_G_013",
        )

        # ==================================================================
        # 合规模板扩展字段名规则
        # ==================================================================
        self._apply_template_field_rules(tags, norm_name, params)

        # ==================================================================
        # 白名单与运营统计字段（按字段名匹配，全列生效）
        # ==================================================================

        # 公开字段白名单 → 全列 L1
        norm_public_whitelist = [_normalize_field_name(kw) for kw in params.public_field_whitelist]
        if any(kw in norm_name for kw in norm_public_whitelist):
            self._add_all(
                tags,
                level=SensitivityLevel.L1,
                category="PUBLIC_REPORT",
                rule_id="RULE_ID_L1_001",
            )

        # 运营统计字段 → 全列 L2
        norm_operational = [_normalize_field_name(kw) for kw in params.operational_field_patterns]
        if any(kw in norm_name for kw in norm_operational):
            self._add_all(
                tags,
                level=SensitivityLevel.L2,
                category="OPERATIONAL_STAT",
                rule_id="RULE_ID_L2_001",
            )

        # 每行去重：以 (level, category) 为键去除重复标签
        result = [_unique_tags(row_tags) for row_tags in tags]

        # 计算批量评估耗时并输出调试日志
        duration = time.monotonic() - start_time
        # 统计有标签命中的行数
        hit_count = sum(1 for row_tags in result if row_tags)
        logger.debug(
            "vectorized_batch_evaluated",
            extra={
                "field_name": field_name,   # 字段名
                "batch_size": n,            # 批量大小
                "hit_rows": hit_count,      # 命中行数
                "duration_s": round(duration, 4),  # 耗时（秒）
            },
        )
        return result

    # ------------------------------------------------------------------
    # 内部工具方法
    # ------------------------------------------------------------------

    def _apply_template_field_rules(
        self,
        tags: list[list[SecurityTag]],
        norm_name: str,
        params: ClassificationParams,
    ) -> None:
        """根据合规模板扩展字段名规则 / Apply Compliance Template Field Rules.

        与 DefaultRuleEngine._apply_template_field_rules 逻辑一致，
        但这里是全列生效（字段名规则是列级别的）。

        Args:
            tags: 标签结果矩阵（N 行） / Tag result matrix.
            norm_name: 归一化字段名 / Normalized field name.
            params: 分类参数 / Classification parameters.
        """
        # 获取当前激活的合规模板
        template = params.template
        # 未激活模板时直接返回
        if not template:
            return

        # 模板名统一转小写
        template = str(template).lower()

        # JR/T 0197 金融模板：金融账户字段 → 全列 L4
        if template == "jrt0197" and any(
            kw in norm_name
            for kw in ("bankcard", "cardno", "credit", "transaction", "asset", "balance", "account")
        ):
            self._add_all(
                tags,
                level=SensitivityLevel.L4,
                category="FINANCE_ACCOUNT",
                rule_id="RULE_ID_JRT_001",
            )

        # GB/T 35273 或 GDPR 模板：联系/位置字段 → 全列 L3
        if template in ("gbt35273", "gdpr") and any(
            kw in norm_name for kw in ("email", "address", "location", "轨迹")
        ):
            self._add_all(
                    tags,
                    level=SensitivityLevel.L3,
                    category="PII_CONTACT_LOCATION",
                    rule_id="RULE_ID_GBT_001",
                )

        # GDPR 模板：特殊类别数据字段 → 全列 L4
        if template == "gdpr" and any(
            kw in norm_name
            for kw in (
                "biometric",    # 生物特征
                "fingerprint",  # 指纹
                "face",         # 面部
                "health",       # 健康
                "genetic",      # 遗传
                "race",         # 种族
                "ethnicity",    # 民族
                "political",    # 政治
                "religion",     # 宗教
                "sexual",       # 性取向
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
        tags: list[list[SecurityTag]],
        level: SensitivityLevel,
        category: str,
        rule_id: str,
    ) -> None:
        """为每一行添加同一个规则标签 / Add Same Rule Tag to All Rows.

        用于字段名规则：字段名匹配时，该列所有行都命中同一标签。
        只创建一个 SecurityTag 实例，所有行共享引用（节省内存）。

        Args:
            tags: 标签结果矩阵 / Tag result matrix.
            level: 敏感度等级 / Sensitivity level.
            category: 分类类别 / Classification category.
            rule_id: 规则 ID / Rule ID.
        """
        # 创建单个标签实例（所有行共享）
        tag = SecurityTag(
            level=level,
            category=category,
            source_engine="RULE",
            rule_id=rule_id,
        )
        # 将标签追加到每一行的标签列表
        for row_tags in tags:
            row_tags.append(tag)
        # 递增 Prometheus 规则命中计数（+1，因为是列级命中）
        CLASSIFICATION_RULE_HITS_TOTAL.labels(rule_id=rule_id).inc()

    def _add_where(
        self,
        tags: list[list[SecurityTag]],
        mask: Any,
        level: SensitivityLevel,
        category: str,
        rule_id: str,
    ) -> None:
        """为 mask 为 True 的行添加规则标签 / Add Rule Tag Where Mask is True.

        用于值规则：只有满足条件的行才命中标签。
        mask 是 pandas 布尔 Series 或可迭代的布尔序列。

        Args:
            tags: 标签结果矩阵 / Tag result matrix.
            mask: 布尔掩码（True 表示该行命中） / Boolean mask.
            level: 敏感度等级 / Sensitivity level.
            category: 分类类别 / Classification category.
            rule_id: 规则 ID / Rule ID.
        """
        # 创建单个标签实例（所有命中行共享）
        tag = SecurityTag(
            level=level,
            category=category,
            source_engine="RULE",
            rule_id=rule_id,
        )
        # 遍历掩码，仅对 True 的行追加标签
        hit_count = 0  # 命中行计数
        for i, hit in enumerate(mask):
            if hit:
                tags[i].append(tag)  # 将标签追加到命中行
                hit_count += 1
        # 如果有命中行，递增 Prometheus 指标（按命中行数递增）
        if hit_count > 0:
            CLASSIFICATION_RULE_HITS_TOTAL.labels(rule_id=rule_id).inc(hit_count)


# 模块公开接口声明
__all__ = ["VectorizedRuleEngine"]
