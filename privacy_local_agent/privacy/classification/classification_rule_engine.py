"""数据分类规则引擎实现 / Data Classification Rule Engine Implementation.

中文说明：
提供默认规则引擎 DefaultRuleEngine、向后兼容的 RuleEngine 抽象接口，
以及规则匹配所需的内部工具函数（字段名归一化、身份证号/医保卡校验、
ICD-10 区间判断、标签去重等）。

本模块是三层分类漏斗的第一层（Layer-1），负责通过字段名模式匹配和
字段值正则/校验算法快速识别敏感数据。规则引擎是无状态的，所有配置
通过 ClassificationParams 传入。

执行逻辑概览：
1. 规范化字段名（小写 + 去下划线/空格）
2. 基因组字段名规则（BRCA/TP53/SNP/CNV → L5）
3. 值规则（身份证/手机号/医保卡/ICD-10 → L3/L4）
4. 基因组文件内容检测（BAM/VCF/FASTQ 头 → L5）
5. 合规模板扩展字段名规则（JR/T 0197、GB/T 35273、GDPR）
6. 白名单/运营字段降级规则（→ L1/L2）
7. 标签去重后返回

English Description:
Provides the default rule engine DefaultRuleEngine, backward-compatible RuleEngine
abstract interface, and internal utility functions for rule matching (field name
normalization, ID card/medical card checksum validation, ICD-10 interval checking,
tag deduplication, etc.).
"""

# 启用延迟注解求值，允许在类型提示中引用尚未定义的类名
from __future__ import annotations

# 导入正则表达式模块，用于字段值模式匹配（手机号、ICD-10 编码、基因序列等）
import re
# 导入 Any 类型，用于声明字段值参数（可能是字符串、数字或 None）
from typing import Any

# 导入结构化日志工厂函数，创建模块级日志器
from ...observability.logging_config import get_logger
# 导入 Prometheus Counter 指标，用于统计每条规则的命中次数
from ...observability.metrics import CLASSIFICATION_RULE_HITS_TOTAL
# 从数据模型模块导入核心类型
from .classification_models import (
    ClassificationParams,  # 分类参数模型（含规则配置、模板、阈值等）
    RuleEngineABC,         # 规则引擎抽象基类
    SecurityTag,           # 安全标签模型（单次规则命中的描述）
    SensitivityLevel,      # 敏感度等级枚举（L1~L5）
)

# 创建模块级结构化日志器，日志中自动携带模块名（classification_rule_engine）
logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# 默认值与常量 / Defaults and Constants
# ---------------------------------------------------------------------------

# 中国大陆 18 位身份证号校验码权重因子（GB 11643-1999 标准）
# 前 17 位数字分别乘以对应权重后求和，对 11 取模得到校验码索引
_ID_CARD_WEIGHTS = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
# 校验码字符映射表：模 11 余数 0~10 对应的校验字符
# 余数 0→'1', 1→'0', 2→'X', 3→'9', ..., 10→'2'
_ID_CARD_CHARS = ["1", "0", "X", "9", "8", "7", "6", "5", "4", "3", "2"]

# 上海医保卡号 9 位数字校验权重因子（前 8 位参与计算）
_SH_MEDICAL_WEIGHTS = [7, 9, 10, 5, 8, 4, 2, 1]


# ---------------------------------------------------------------------------
# 工具函数 / Utility Functions
# ---------------------------------------------------------------------------


def _normalize_field_name(name: str) -> str:
    """规范化字段名 / Normalize Field Name.

    将字段名统一为小写并移除下划线与空格，使得不同命名风格
    （如 phone_number、phoneNumber、Phone Number）都能被同一规则匹配。

    示例：
        "Phone_Number" → "phonenumber"
        "ICD_10 Code"  → "icd10code"

    Args:
        name: 原始字段名 / Original field name.

    Returns:
        规范化后的字段名（全小写、无下划线、无空格） / Normalized field name.
    """
    # 先转字符串（防止非字符串输入），再转小写，最后移除下划线和空格
    return str(name).lower().replace("_", "").replace(" ", "")


def _normalize_icd10(code: str) -> tuple[str, int] | None:
    """解析并归一化 ICD-10 编码 / Parse and Normalize ICD-10 Code.

    ICD-10 编码格式：字母 + 2位数字 + 可选的小数点和亚目（如 "B20"、"F20.1"）。
    本函数提取首字母和两位数字部分，用于后续的区间比较。

    示例：
        "B20"   → ("B", 20)
        "F20.1" → ("F", 20)
        "C97"   → ("C", 97)
        "xyz"   → None（无效）

    Args:
        code: ICD-10 编码字符串 / ICD-10 code string.

    Returns:
        (letter, two-digit number) 元组，无效时返回 None / Tuple or None if invalid.
    """
    # 正则匹配：1个大写字母 + 2位数字 + 可选的 .亚目（0~2位数字）
    match = re.match(r"^([A-Z])(\d{2})(?:\.\d{0,2})?$", code.upper())
    # 不匹配 ICD-10 格式则返回 None
    if not match:
        return None
    # 返回（首字母, 两位数字的整数值）元组
    return match.group(1), int(match.group(2))


def _in_icd10_interval(code: tuple[str, int], start: str, end: str) -> bool:
    """判断 ICD-10 编码是否落在闭区间 / Check if ICD-10 Code Falls Within Closed Interval.

    使用元组比较实现字典序区间判断：先比较字母，字母相同再比较数字。
    例如 ("B", 22) 在 [("B", 20), ("B", 24)] 区间内。

    Args:
        code: 归一化后的 (letter, number) 元组 / Normalized (letter, number) tuple.
        start: 区间起点字符串 / Interval start string (e.g. "B20").
        end: 区间终点字符串 / Interval end string (e.g. "B24").

    Returns:
        是否在区间内 / Whether code is within the interval.
    """
    # 解析区间起止点的 ICD-10 编码
    start_norm = _normalize_icd10(start)
    end_norm = _normalize_icd10(end)
    # 如果起止点格式无效，返回 False（安全降级）
    if not start_norm or not end_norm:
        return False
    # 利用 Python 元组的字典序比较：(letter, number) 先比字母再比数字
    return start_norm <= code <= end_norm


def _id_card_checksum(value: str) -> bool:
    """校验中国大陆 18 位身份证号校验码 / Validate Chinese ID Card Checksum.

    校验流程：
    1. 检查长度是否为 18 位
    2. 正则验证格式：6位地区码 + 8位出生日期 + 3位顺序码 + 1位校验码
    3. 计算加权校验和：前17位 × 权重因子 求和
    4. 对 11 取模，查表得到期望校验码
    5. 比较第18位与期望校验码

    Args:
        value: 身份证号字符串 / ID card number string.

    Returns:
        校验是否通过 / Whether checksum validation passes.
    """
    # 长度必须恰好为 18 个字符
    if len(value) != 18:
        return False
    # 正则验证身份证号格式：
    # [1-9]\d{5}     - 6位地区码（首位非零）
    # (18|19|20)\d{2} - 4位年份（1800~2099）
    # (0[1-9]|1[0-2]) - 2位月份（01~12）
    # (0[1-9]|[12]\d|3[01]) - 2位日期（01~31）
    # \d{3}          - 3位顺序码
    # [\dXx]         - 1位校验码（数字或X）
    if not re.match(r"^[1-9]\d{5}(18|19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\d{3}[\dXx]$", value):
        return False
    try:
        # 计算前 17 位的加权和：每位数字 × 对应权重因子
        total = sum(int(value[i]) * _ID_CARD_WEIGHTS[i] for i in range(17))
        # 对 11 取模后查表得到期望的校验字符
        expected = _ID_CARD_CHARS[total % 11]
        # 比较第 18 位（校验码）与期望值（统一转大写比较，兼容小写 x）
        return value[17].upper() == expected
    except (ValueError, IndexError):
        # 转换失败（非数字字符等）时返回 False
        return False


def _shanghai_medical_card_checksum(value: str) -> bool:
    """校验上海医保卡号 9 位数字校验码 / Validate Shanghai Medical Card Checksum.

    校验流程：
    1. 检查是否为 9 位纯数字
    2. 前 8 位 × 权重因子求和
    3. 校验码 = (10 - 加权和 % 10) % 10
    4. 比较第 9 位与计算的校验码

    Args:
        value: 医保卡号字符串 / Medical card number string.

    Returns:
        校验是否通过 / Whether checksum validation passes.
    """
    # 必须恰好为 9 位纯数字
    if not re.match(r"^\d{9}$", value):
        return False
    # 将每个字符转为整数，得到 9 位数字列表
    digits = [int(c) for c in value]
    # 计算前 8 位的加权和
    total = sum(digits[i] * _SH_MEDICAL_WEIGHTS[i] for i in range(8))
    # 计算期望的第 9 位校验码：(10 - 加权和个位) 的个位
    expected = (10 - total % 10) % 10
    # 比较实际第 9 位与期望校验码
    return digits[8] == expected


def _unique_tags(tags: list[SecurityTag]) -> list[SecurityTag]:
    """去重安全标签 / Deduplicate Security Tags.

    以 (level, category) 组合为去重键，保留首次出现的标签，
    维持原始顺序不变。避免同一字段被同一规则重复标记。

    Args:
        tags: 原始标签列表 / Original tag list.

    Returns:
        去重后的标签列表 / Deduplicated tag list.
    """
    seen: set = set()  # 已见过的 (level, category) 组合集合
    result: list[SecurityTag] = []  # 去重后的结果列表
    for tag in tags:
        # 构造去重键：等级值 + 类别名
        key = (tag.level.value, tag.category)
        # 如果该组合已出现过，跳过
        if key in seen:
            continue
        # 首次出现：加入已见集合和结果列表
        seen.add(key)
        result.append(tag)
    return result


# ---------------------------------------------------------------------------
# 规则引擎 / Rule Engine
# ---------------------------------------------------------------------------


class RuleEngine(RuleEngineABC):
    """向后兼容的 RuleEngine 抽象接口 / Backward-Compatible RuleEngine Abstract Interface.

    这是 RuleEngineABC 的空子类，存在的唯一目的是保持旧代码中
    `from ... import RuleEngine` 的导入路径不被破坏。
    新代码应直接使用 RuleEngineABC 或 DefaultRuleEngine。
    """


class DefaultRuleEngine(RuleEngine):
    """默认规则引擎 / Default Rule Engine.

    实现规范中全部 Layer-1 规则，是三层分类漏斗的第一层。
    规则类型包括：
    - 字段名规则：通过字段名关键词匹配（基因组、金融、PII 等）
    - 值规则：通过字段值的正则/校验算法匹配（身份证、手机号、ICD-10 等）
    - 文件内容规则：通过文件头特征检测（BAM/VCF/FASTQ）
    - 合规模板扩展规则：根据激活的模板添加额外字段名规则
    - 降级规则：白名单字段降为 L1，运营字段降为 L2

    本引擎是无状态的，所有配置通过 params 参数传入。
    每次 evaluate() 调用都会递增对应的 Prometheus Counter 指标。
    """

    def evaluate(
        self, field_name: str, value: Any, params: ClassificationParams
    ) -> list[SecurityTag]:
        """按字段名与字段值评估规则并收集标签 / Evaluate Rules by Field Name and Value.

        这是规则引擎的核心方法，按以下顺序执行所有 Layer-1 规则：

        执行步骤 / Execution Steps:
        1. 规范化字段名和字段值（小写 + 去分隔符）。
        2. 执行字段名规则匹配（基因组 BRCA/TP53/SNP/CNV/文件格式 → L5）。
        3. 执行值规则匹配（身份证/手机号/医保卡 → L3，ICD-10 → L3/L4）。
        4. 执行基因组文件内容检测（BAM/VCF/FASTQ 文件头/长序列 → L5）。
        5. 执行合规模板扩展字段名规则（JR/T 0197、GB/T 35273、GDPR）。
        6. 执行白名单与运营统计字段降级规则（→ L1/L2）。
        7. 去重并返回标签列表。

        Args:
            field_name: 字段名 / Field name.
            value: 字段值 / Field value (will be converted to string).
            params: 分类参数 / Classification parameters.

        Returns:
            命中的 SecurityTag 列表（已去重） / List of matched SecurityTags (deduplicated).
        """
        # 初始化标签收集列表，存放本次评估中所有规则命中的标签
        tags: list[SecurityTag] = []
        # 规范化字段名：转小写 + 去下划线/空格（如 "Phone_Number" → "phonenumber"）
        norm_name = _normalize_field_name(field_name)
        # 将字段值转为字符串（None 转为空字符串），用于后续值规则匹配
        str_value = str(value) if value is not None else ""

        # ===== Step 1: 基于字段名的规则（基因组指标） =====
        # 同时对字段值也做规范化，用于检测值中包含的基因组关键词
        norm_value = _normalize_field_name(str_value)

        # 规则 RULE_ID_G_001：基因组 BRCA/TP53 基因指标（最高敏感度 L5）
        # 匹配字段名中包含 brca1、brca2、tp53 的情况
        if any(kw in norm_name for kw in ("brca1", "brca2", "tp53")):
            tags.append(
                SecurityTag(
                    level=SensitivityLevel.L5,       # 极敏感：基因信息
                    category="GENOMIC_BRCA_TP53",   # 类别：BRCA/TP53 基因
                    source_engine="RULE",           # 来源：规则引擎
                    rule_id="RULE_ID_G_001",        # 规则唯一标识
                )
            )
            # 递增 Prometheus 指标：记录该规则被命中的次数
            CLASSIFICATION_RULE_HITS_TOTAL.labels(rule_id="RULE_ID_G_001").inc()

        # 规则 RULE_ID_G_002：基因组变异指标（rs 编号、SNP、CNV 等）
        # 匹配条件：字段名或字段值中包含 rs+数字（如 rs12345），
        # 或字段名包含 snp/cnv/genome/genomic 关键词
        if re.search(r"rs\d+", norm_name) or re.search(r"rs\d+", norm_value) or any(
            kw in norm_name for kw in ("snp", "cnv", "genome", "genomic")
        ):
            tags.append(
                SecurityTag(
                    level=SensitivityLevel.L5,       # 极敏感：基因变异信息
                    category="GENOMIC_VARIANT",     # 类别：基因组变异
                    source_engine="RULE",
                    rule_id="RULE_ID_G_002",
                )
            )
            CLASSIFICATION_RULE_HITS_TOTAL.labels(rule_id="RULE_ID_G_002").inc()

        # 规则 RULE_ID_G_003：基因组提示指标（gene、mutation、variant）
        # 匹配字段名中包含基因相关通用关键词
        if any(kw in norm_name for kw in ("gene", "mutation", "variant")):
            tags.append(
                SecurityTag(
                    level=SensitivityLevel.L5,       # 极敏感：基因相关
                    category="GENOMIC_HINT",        # 类别：基因组提示
                    source_engine="RULE",
                    rule_id="RULE_ID_G_003",
                )
            )
            CLASSIFICATION_RULE_HITS_TOTAL.labels(rule_id="RULE_ID_G_003").inc()

        # 规则 RULE_ID_G_004：基因组文件格式指标（BAM、VCF、FASTQ）
        # 匹配字段名中包含基因组测序文件格式名称
        if any(kw in norm_name for kw in ("bam", "vcf", "fastq")):
            tags.append(
                SecurityTag(
                    level=SensitivityLevel.L5,       # 极敏感：基因组文件
                    category="GENOMIC_FILE",        # 类别：基因组文件格式
                    source_engine="RULE",
                    rule_id="RULE_ID_G_004",
                )
            )
            CLASSIFICATION_RULE_HITS_TOTAL.labels(rule_id="RULE_ID_G_004").inc()

        # ===== Step 2: 基于值的规则（PII 标识符） =====

        # 规则 RULE_ID_001：中国大陆 18 位身份证号（含校验码验证）
        # 通过 _id_card_checksum 函数验证格式和校验码
        if _id_card_checksum(str_value):
            tags.append(
                SecurityTag(
                    level=SensitivityLevel.L3,       # 敏感：个人身份信息
                    category="PII_ID_CARD",         # 类别：身份证号
                    source_engine="RULE",
                    rule_id="RULE_ID_001",
                )
            )
            CLASSIFICATION_RULE_HITS_TOTAL.labels(rule_id="RULE_ID_001").inc()

        # 规则 RULE_ID_002：中国大陆手机号（11位，1开头，第二位3-9）
        # 正则匹配：^1[3-9]\d{9}$
        if re.match(r"^1[3-9]\d{9}$", str_value):
            tags.append(
                SecurityTag(
                    level=SensitivityLevel.L3,       # 敏感：个人联系方式
                    category="PII_MOBILE",          # 类别：手机号
                    source_engine="RULE",
                    rule_id="RULE_ID_002",
                )
            )
            CLASSIFICATION_RULE_HITS_TOTAL.labels(rule_id="RULE_ID_002").inc()

        # 规则 RULE_ID_003：上海医保卡号（9位数字 + 校验码）
        # 通过 _shanghai_medical_card_checksum 函数验证
        if _shanghai_medical_card_checksum(str_value):
            tags.append(
                SecurityTag(
                    level=SensitivityLevel.L3,       # 敏感：医疗卡号
                    category="PII_MEDICAL_CARD",    # 类别：医保卡号
                    source_engine="RULE",
                    rule_id="RULE_ID_003",
                )
            )
            CLASSIFICATION_RULE_HITS_TOTAL.labels(rule_id="RULE_ID_003").inc()

        # 规则 RULE_ID_004：ICD-10 医疗编码（含 L4 敏感疾病升级）
        # 先解析 ICD-10 编码格式，再判断是否落在敏感区间内
        icd = _normalize_icd10(str_value)
        if icd:
            # 默认 ICD-10 编码为 L3（一般医疗信息）
            level = SensitivityLevel.L3
            category = "MEDICAL_ICD10_GENERAL"
            # 遍历配置的 L4 敏感区间（HIV/精神疾病/恶性肿瘤）
            for interval in params.icd10_l4_intervals:
                start = interval.get("start", "")  # 区间起始编码
                end = interval.get("end", "")      # 区间结束编码
                # 判断当前编码是否落在该敏感区间内
                if _in_icd10_interval(icd, start, end):
                    # 命中敏感区间：升级为 L4（高敏感）
                    level = SensitivityLevel.L4
                    # 根据区间首字母确定具体疾病类别
                    if start.upper().startswith("B"):
                        category = "MEDICAL_ICD10_HIV"          # B20-B24: HIV 相关
                    elif start.upper().startswith("F"):
                        category = "MEDICAL_ICD10_PSYCHIATRIC"  # F20-F29: 精神疾病
                    elif start.upper().startswith("C"):
                        category = "MEDICAL_ICD10_CANCER"       # C00-C97: 恶性肿瘤
                    break  # 命中第一个匹配区间即停止
            # 添加 ICD-10 标签（可能是 L3 一般或 L4 敏感）
            tags.append(
                SecurityTag(
                    level=level,
                    category=category,
                    source_engine="RULE",
                    rule_id="RULE_ID_004",
                )
            )
            CLASSIFICATION_RULE_HITS_TOTAL.labels(rule_id="RULE_ID_004").inc()

        # ===== Step 3: 基因组文件内容检测（BAM/VCF/FASTQ 文件头） =====

        # 规则 RULE_ID_G_010：BAM 文件格式检测
        # BAM 文件以 "BAM\x01" 魔数开头，或以 "@SQ" 序列头开头
        if str_value.startswith("BAM\x01") or str_value.startswith("@SQ"):
            tags.append(
                SecurityTag(
                    level=SensitivityLevel.L5,       # 极敏感：基因组测序数据
                    category="GENOMIC_BAM",         # 类别：BAM 文件
                    source_engine="RULE",
                    rule_id="RULE_ID_G_010",
                )
            )
            CLASSIFICATION_RULE_HITS_TOTAL.labels(rule_id="RULE_ID_G_010").inc()

        # 规则 RULE_ID_G_011：VCF 文件格式检测
        # VCF 文件以 "##fileformat=VCF" 元信息行开头
        if str_value.startswith("##fileformat=VCF"):
            tags.append(
                SecurityTag(
                    level=SensitivityLevel.L5,       # 极敏感：基因组变异数据
                    category="GENOMIC_VCF",         # 类别：VCF 文件
                    source_engine="RULE",
                    rule_id="RULE_ID_G_011",
                )
            )
            CLASSIFICATION_RULE_HITS_TOTAL.labels(rule_id="RULE_ID_G_011").inc()

        # 规则 RULE_ID_G_012：FASTQ 文件格式检测
        # FASTQ 文件特征：以 @ 开头 + 包含 SRA 编号（SRR/ERR/DRR）
        # 或者第 3 行为 "+"（FASTQ 四行格式的分隔符）
        lines = str_value.splitlines()  # 按行分割用于检测第 3 行
        if str_value.startswith("@") and (
            any(token in str_value for token in ("SRR", "ERR", "DRR"))  # SRA 登录号
            or (len(lines) >= 3 and lines[2].strip() == "+")            # FASTQ 格式第3行
        ):
            tags.append(
                SecurityTag(
                    level=SensitivityLevel.L5,       # 极敏感：基因组测序原始数据
                    category="GENOMIC_FASTQ",       # 类别：FASTQ 文件
                    source_engine="RULE",
                    rule_id="RULE_ID_G_012",
                )
            )
            CLASSIFICATION_RULE_HITS_TOTAL.labels(rule_id="RULE_ID_G_012").inc()

        # 规则 RULE_ID_G_013：基因组序列检测（长 ATCGN 序列）
        # 如果字段值中包含连续 50 个以上的碱基字符（A/T/C/G/N），
        # 则判定为基因组序列数据
        if re.search(r"[ATCGNatcgn]{50,}", str_value):
            tags.append(
                SecurityTag(
                    level=SensitivityLevel.L5,       # 极敏感：基因组序列
                    category="GENOMIC_SEQUENCE",    # 类别：碱基序列
                    source_engine="RULE",
                    rule_id="RULE_ID_G_013",
                )
            )
            CLASSIFICATION_RULE_HITS_TOTAL.labels(rule_id="RULE_ID_G_013").inc()

        # ===== Step 4: 合规模板扩展字段名规则 =====
        # 根据激活的合规模板（JR/T 0197、GB/T 35273、GDPR）
        # 添加额外的字段名匹配规则
        tags.extend(self._apply_template_field_rules(norm_name, params))

        # ===== Step 5: 白名单与运营统计字段降级规则 =====

        # 规则 RULE_ID_L1_001：公开字段白名单降级
        # 如果字段名匹配 public_field_whitelist 中的任何模式，
        # 则标记为 L1（公开），如 "public_report"、"annual_summary"、"科普"
        norm_public_whitelist = [_normalize_field_name(kw) for kw in params.public_field_whitelist]
        if any(kw in norm_name for kw in norm_public_whitelist):
            tags.append(
                SecurityTag(
                    level=SensitivityLevel.L1,       # 公开：无隐私风险
                    category="PUBLIC_REPORT",       # 类别：公开报告
                    source_engine="RULE",
                    rule_id="RULE_ID_L1_001",
                )
            )
            CLASSIFICATION_RULE_HITS_TOTAL.labels(rule_id="RULE_ID_L1_001").inc()

        # 规则 RULE_ID_L2_001：运营统计字段降级
        # 如果字段名匹配 operational_field_patterns 中的任何模式，
        # 则标记为 L2（内部），如 "turnover_rate"、"device_usage"、"inventory"
        norm_operational = [_normalize_field_name(kw) for kw in params.operational_field_patterns]
        if any(kw in norm_name for kw in norm_operational):
            tags.append(
                SecurityTag(
                    level=SensitivityLevel.L2,       # 内部：低敏感度
                    category="OPERATIONAL_STAT",    # 类别：运营统计
                    source_engine="RULE",
                    rule_id="RULE_ID_L2_001",
                )
            )
            CLASSIFICATION_RULE_HITS_TOTAL.labels(rule_id="RULE_ID_L2_001").inc()

        # ===== Step 6: 去重并返回 =====
        # 以 (level, category) 为键去重，避免同一规则重复标记
        return _unique_tags(tags)

    def _apply_template_field_rules(
        self, norm_name: str, params: ClassificationParams
    ) -> list[SecurityTag]:
        """根据合规模板扩展字段名规则 / Apply Compliance Template Extension Field Rules.

        根据激活的合规模板添加额外的字段名匹配规则：
        - JR/T 0197（金融行业标准）：银行卡号、交易、资产等字段 → L4
        - GB/T 35273（个人信息安全规范）：邮箱、地址、轨迹等字段 → L3
        - GDPR（欧盟通用数据保护条例）：生物特征、健康、种族等特殊类别 → L4

        Args:
            norm_name: 规范化后的字段名 / Normalized field name.
            params: 分类参数（含 template 字段指定激活的模板） / Classification parameters.

        Returns:
            命中的 SecurityTag 列表 / List of matched SecurityTags.
        """
        # 初始化本方法的标签收集列表
        tags: list[SecurityTag] = []
        # 获取当前激活的合规模板名称
        template = params.template
        # 如果没有激活任何模板，直接返回空列表
        if not template:
            return tags

        # 模板名统一转小写，便于比较
        template = str(template).lower()

        # JR/T 0197 金融行业标准模板
        # 匹配金融账户相关字段名：银行卡号、卡号、信用、交易、资产、余额、账户
        if template == "jrt0197" and any(
            kw in norm_name
            for kw in ("bankcard", "cardno", "credit", "transaction", "asset", "balance", "account")
        ):
            tags.append(
                SecurityTag(
                    level=SensitivityLevel.L4,       # 高敏感：金融账户信息
                    category="FINANCE_ACCOUNT",     # 类别：金融账户
                    source_engine="RULE",
                    rule_id="RULE_ID_JRT_001",      # JR/T 0197 规则
                )
            )

        # GB/T 35273 个人信息安全规范 或 GDPR 模板
        # 匹配个人联系方式和位置相关字段名：邮箱、地址、位置、轨迹
        if template in ("gbt35273", "gdpr") and any(
            kw in norm_name for kw in ("email", "address", "location", "轨迹")
        ):
            tags.append(
                SecurityTag(
                    level=SensitivityLevel.L3,           # 敏感：个人联系/位置信息
                    category="PII_CONTACT_LOCATION",    # 类别：联系方式与位置
                    source_engine="RULE",
                    rule_id="RULE_ID_GBT_001",          # GB/T 35273 规则
                )
            )

        # GDPR 特殊类别数据（第 9 条）
        # 匹配生物特征、健康、遗传、种族、政治、宗教、性取向等极敏感字段
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
                "political",    # 政治观点
                "religion",     # 宗教信仰
                "sexual",       # 性取向
            )
        ):
            tags.append(
                SecurityTag(
                    level=SensitivityLevel.L4,               # 高敏感：GDPR 特殊类别
                    category="GDPR_SPECIAL_CATEGORY",       # 类别：GDPR 第9条特殊数据
                    source_engine="RULE",
                    rule_id="RULE_ID_GDPR_001",             # GDPR 规则
                )
            )

        # 返回本模板规则命中的所有标签
        return tags


# 模块公开接口声明：仅导出规则引擎类和去重工具函数
__all__ = ["DefaultRuleEngine", "RuleEngine", "_unique_tags"]
