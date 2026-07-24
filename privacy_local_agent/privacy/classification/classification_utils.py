"""分类子系统公共工具与适配器 / Classification Subsystem Utilities and Adapters.

中文说明：
本模块合并三类能力：
- Zero-Knowledge 日志/导出安全工具（redact、hash_value、safe_log 等）
- 合规模板默认参数（JR/T 0197、GB/T 35273、GDPR）
- SecretFlow 联邦数据结构适配器

设计原则：
- 零知识日志：日志中永远不输出完整的敏感字段值，仅保留前缀或哈希
- 合规模板：预置国内/国际主流数据保护标准的默认分类参数
- 联邦适配：将 SecretFlow HDataFrame/SPU 等结构透明转换为标准记录格式

English Description:
This module combines three capabilities:
- Zero-Knowledge logging/export security utilities (redact, hash_value, safe_log, etc.)
- Compliance template default parameters (JR/T 0197, GB/T 35273, GDPR)
- SecretFlow federated data structure adapter
"""

# 启用延迟注解求值，允许在类型提示中使用尚未导入的类型
from __future__ import annotations

# 导入哈希库，用于对敏感值进行不可逆哈希（SHA-256/MD5）
import hashlib
# 导入类型注解工具：TYPE_CHECKING 用于条件导入，Any 用于通用类型声明
from typing import TYPE_CHECKING, Any

# 导入结构化日志工厂函数
from ...observability.logging_config import get_logger
# 导入数据适配器，将 SecretFlow 等联邦数据结构转换为标准 dict 记录列表
from ..data_adapters import to_records

# 仅在类型检查时导入 logging 模块（避免运行时开销）
if TYPE_CHECKING:
    import logging

# 创建模块级结构化日志器，日志自动携带模块名标识
logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Zero-Knowledge 安全工具 / Zero-Knowledge Security Utilities
# 核心原则：日志和导出中永远不暴露完整的敏感数据明文
# ---------------------------------------------------------------------------


def redact(value: Any, max_len: int = 8, placeholder: str = "***") -> str:
    """对原始值进行脱敏 / Redact Original Value.

    脱敏策略：保留前 max_len 个字符作为可辨识前缀，
    其余部分替换为固定占位符，确保日志可读但不泄露完整信息。

    示例：
        redact("13800138000")       → "13800138***"
        redact("张三丰")            → "张三丰"（长度 <= 8，不截断）
        redact(None)               → ""

    Args:
        value: 待脱敏的原始值 / Original value to redact.
        max_len: 保留的最大明文长度（默认 8） / Maximum plaintext length to keep.
        placeholder: 替换后缀的占位符（默认 "***"） / Placeholder for replaced suffix.

    Returns:
        脱敏后的字符串；value 为 None 时返回空字符串 / Redacted string.
    """
    # None 值直接返回空字符串（避免日志中出现 "None" 字样）
    if value is None:
        return ""
    # 统一转为字符串处理
    text = str(value)
    # 如果字符串长度未超过保留上限，直接返回原文（短字段名等元数据）
    if len(text) <= max_len:
        return text
    # 超过上限：保留前 max_len 个字符 + 占位符
    return text[:max_len] + placeholder


def hash_value(value: Any, algorithm: str = "sha256") -> str:
    """对原始值进行不可逆哈希 / Hash Original Value.

    用于复核导出等需要唯一标识但不需要明文的场景。
    相同输入始终产生相同哈希，可用于去重和关联，但无法反推原文。

    Args:
        value: 待哈希的原始值 / Original value to hash.
        algorithm: 哈希算法名称（支持 "sha256" 和 "md5"） / Hash algorithm.

    Returns:
        十六进制哈希字符串 / Hexadecimal hash string.

    Raises:
        ValueError: 不支持的哈希算法 / Unsupported hash algorithm.
    """
    # None 值按空字符串处理（保证哈希结果确定性）
    if value is None:
        value = ""
    # 将值转为 UTF-8 字节序列（哈希函数需要字节输入）
    data = str(value).encode("utf-8")
    # 根据指定算法计算哈希
    if algorithm == "sha256":
        return hashlib.sha256(data).hexdigest()  # SHA-256：64 字符十六进制
    if algorithm == "md5":
        return hashlib.md5(data).hexdigest()  # MD5：32 字符十六进制（仅用于非安全场景）
    # 不支持的算法抛出明确错误
    raise ValueError(f"unsupported hash algorithm: {algorithm}")


def should_log_value(value: Any) -> bool:
    """判断一个值是否可以安全地完整打印到日志中 / Check if Value Can Be Safely Logged.

    安全策略：
    - None、布尔值、数字：始终安全（不含隐私信息）
    - 短字符串（<= 16 字符）：通常是字段名/枚举值，安全
    - 长字符串：可能包含敏感数据（如手机号、地址），不建议完整打印
    - 其他类型（dict、list 等）：可能嵌套敏感数据，不建议完整打印

    Args:
        value: 待判断的值 / Value to check.

    Returns:
        True 表示可以安全完整打印，False 表示应脱敏后再打印 / Whether safe to log.
    """
    # None 值安全（打印为 "None"）
    if value is None:
        return True
    # 布尔值安全（True/False）
    if isinstance(value, bool):
        return True
    # 数字类型安全（整数、浮点数不含隐私）
    if isinstance(value, (int, float)):
        return True
    # 字符串：仅当长度 <= 16 时认为安全（通常是字段名、状态码等元数据）
    if isinstance(value, str):
        return len(value) <= 16
    # 其他复杂类型（dict、list、对象等）：不安全，可能嵌套敏感数据
    return False


def safe_log(
    logger: logging.Logger,
    level: int,
    msg: str,
    **fields: Any,
) -> None:
    """安全地记录日志（自动脱敏） / Safely Log with Auto-Redaction.

    对所有字符串类型的字段值自动执行 redact() 脱敏，
    非字符串类型保持原样。确保日志中不会意外泄露敏感数据。

    使用示例：
        safe_log(logger, logging.INFO, "classified field", field_name="phone", value="13800138000")
        # 日志输出中 value 会被脱敏为 "13800138***"

    Args:
        logger: 日志记录器实例 / Logger instance.
        level: 日志级别（如 logging.INFO、logging.WARNING） / Log level.
        msg: 日志消息模板 / Log message template.
        **fields: 需要格式化的键值对字段 / Fields to format (strings auto-redacted).
    """
    # 构建安全的字段字典
    safe_fields: dict[str, Any] = {}
    for key, value in fields.items():
        # 字符串值自动脱敏（保留前缀 + 占位符）
        if isinstance(value, str):
            safe_fields[key] = redact(value)
        else:
            # 非字符串值保持原样（数字、布尔等安全类型）
            safe_fields[key] = value
    # 使用处理后的安全字段输出日志
    logger.log(level, msg, safe_fields)


def mask_record_values(record: dict[str, Any] | None) -> dict[str, str]:
    """将记录中的所有字段值脱敏 / Mask All Field Values in Record.

    用于复核导出场景：当 review_export_mask=True 时，
    导出的复核条目中所有字段值都会被脱敏处理。

    Args:
        record: 原始记录字典（字段名 → 字段值） / Original record.

    Returns:
        所有值均已脱敏的记录副本；record 为 None 时返回空字典 / Redacted record copy.
    """
    # None 记录返回空字典
    if record is None:
        return {}
    # 对每个字段值执行 redact 脱敏，生成新的字典（不修改原始数据）
    return {key: redact(value) for key, value in record.items()}


# ---------------------------------------------------------------------------
# 合规模板默认参数 / Compliance Template Default Parameters
# 预置国内/国际主流数据保护标准的分类参数覆盖值。
# 当请求指定 template 时，这些参数会覆盖 ClassificationParams 的默认值。
# ---------------------------------------------------------------------------


# GB/T 35273《信息安全技术 个人信息安全规范》核心字段模式
# 这些字段名模式在规则引擎中用于识别个人敏感信息
_GBT35273_FIELD_PATTERNS = [
    "name",      # 姓名
    "id_card",   # 身份证号
    "mobile",    # 手机号
    "phone",     # 电话号码
    "address",   # 地址
    "email",     # 电子邮箱
    "location",  # 位置信息
    "轨迹",      # 行踪轨迹（中文）
]

# GDPR（欧盟通用数据保护条例）第 9 条特殊类别数据字段模式
# 这些字段涉及最敏感的个人数据，需要最高级别保护
_GDPR_FIELD_PATTERNS = [
    "biometric",    # 生物特征数据
    "health",       # 健康数据
    "genetic",      # 遗传数据
    "race",         # 种族
    "ethnicity",    # 民族
    "political",    # 政治观点
    "religion",     # 宗教信仰
    "sexual",       # 性取向
]

# JR/T 0197《金融数据安全 数据安全分级指南》金融字段模式
# 金融行业特有的敏感字段
_JRT0197_FIELD_PATTERNS = [
    "bank_card",    # 银行卡号
    "bankcard",     # 银行卡号（无下划线变体）
    "account",      # 账户
    "card_no",      # 卡号
    "credit",       # 信用
    "transaction",  # 交易
    "asset",        # 资产
    "balance",      # 余额
]

# 合规模板参数字典：模板名 → 参数覆盖值
# 当 ClassificationParams.template 指定某个模板时，
# 系统会从此字典获取对应的参数覆盖值
TEMPLATES: dict[str, dict[str, Any]] = {
    # GB/T 35273 个人信息安全规范模板
    "gbt35273": {
        "version": "gbt35273-1.0.0",  # 模板版本号
        "default_level": "L3",         # 默认敏感度等级（无规则命中时）
        # 扩展基因关键字列表，在默认基础上增加生物识别相关词汇
        "genomic_keywords": [
            "brca1", "brca2", "tp53", "rs", "snp", "cnv", "genome", "genomic",
            "gene", "mutation", "variant", "biometric", "fingerprint", "face",
        ],
        # 扩展 ICD-10 L4 敏感区间，在默认基础上增加糖尿病区间
        "icd10_l4_intervals": [
            {"start": "B20", "end": "B24"},   # HIV 相关
            {"start": "F20", "end": "F29"},   # 精神疾病
            {"start": "C00", "end": "C97"},   # 恶性肿瘤
            {"start": "E10", "end": "E14"},   # 糖尿病（GB/T 35273 扩展）
        ],
    },
    # GDPR 欧盟通用数据保护条例模板
    "gdpr": {
        "version": "gdpr-1.0.0",  # 模板版本号
        "default_level": "L3",     # 默认敏感度等级
        # 扩展基因关键字，增加 GDPR 第 9 条特殊类别词汇
        "genomic_keywords": [
            "brca1", "brca2", "tp53", "rs", "snp", "cnv", "genome", "genomic",
            "gene", "mutation", "variant", "biometric", "health", "genetic",
            "race", "ethnicity", "political", "religion", "sexual",
        ],
        # ICD-10 L4 区间（与默认相同）
        "icd10_l4_intervals": [
            {"start": "B20", "end": "B24"},   # HIV 相关
            {"start": "F20", "end": "F29"},   # 精神疾病
            {"start": "C00", "end": "C97"},   # 恶性肿瘤
        ],
    },
    # JR/T 0197 金融数据安全分级指南模板
    "jrt0197": {
        "version": "jrt0197-1.0.0",  # 模板版本号
        "default_level": "L3",        # 默认敏感度等级
        # 扩展基因关键字，增加金融敏感字段词汇
        # 注意：这里复用 genomic_keywords 字段来传递金融关键词，
        # 规则引擎会统一在字段名匹配中使用
        "genomic_keywords": [
            "brca1", "brca2", "tp53", "rs", "snp", "cnv", "genome", "genomic",
            "gene", "mutation", "variant", "bank_card", "bankcard", "card_no",
            "account", "credit", "transaction", "asset", "balance",
        ],
        # ICD-10 L4 区间（与默认相同）
        "icd10_l4_intervals": [
            {"start": "B20", "end": "B24"},   # HIV 相关
            {"start": "F20", "end": "F29"},   # 精神疾病
            {"start": "C00", "end": "C97"},   # 恶性肿瘤
        ],
    },
}


def get_template_params(template: str | None) -> dict[str, Any]:
    """获取指定合规模板的默认参数 / Get Compliance Template Default Parameters.

    根据模板名称从 TEMPLATES 字典中查找对应的参数覆盖值。
    返回的是副本（dict()），避免调用方意外修改全局模板配置。

    Args:
        template: 模板名称（如 "gbt35273"、"gdpr"、"jrt0197"） / Template name.

    Returns:
        模板参数字典的副本；模板不存在或为 None 时返回空字典 / Template params copy.
    """
    # 未指定模板时返回空字典（使用 ClassificationParams 的内置默认值）
    if template is None:
        return {}
    # 从全局模板字典中查找，未找到时返回空字典；找到时返回浅拷贝
    return dict(TEMPLATES.get(template, {}))


# ---------------------------------------------------------------------------
# SecretFlow 联邦数据结构适配器 / SecretFlow Federated Data Adapter
# 将 SecretFlow 的 HDataFrame、SPU 对象等联邦计算数据结构
# 透明转换为标准的 dict 记录列表，供 ClassificationAPI 统一处理。
# ---------------------------------------------------------------------------


def classify_secretflow(
    api: Any,
    sf_data: Any,
    params: dict[str, Any] | None = None,
    party: str | None = None,
) -> Any:
    """对 SecretFlow 数据结构进行分类 / Classify SecretFlow Data Structure.

    执行流程：
    1. 调用 to_records() 将 SecretFlow 数据结构转换为标准 dict 记录列表
    2. 从第一条记录中提取字段名列表作为 schema
    3. 调用 ClassificationAPI.classify_table() 执行表级分类

    Args:
        api: ClassificationAPI 实例（三层分类漏斗入口） / ClassificationAPI instance.
        sf_data: SecretFlow 数据结构（HDataFrame/SPU 等） / SecretFlow data structure.
        params: 请求级分类参数（可选，覆盖默认配置） / Request-level classification parameters.
        party: HDataFrame 参与方标识（多方数据时指定查看哪一方） / HDataFrame party identifier.

    Returns:
        ClassificationResult 分类结果 / Classification result.

    Raises:
        ImportError: 未安装 secretflow 或 pandas / secretflow or pandas not installed.
        TypeError: 传入不支持的 SecretFlow 类型 / Unsupported SecretFlow type.
    """
    # 第一步：通过数据适配器将 SecretFlow 结构转为标准记录列表
    # to_records 内部处理 HDataFrame、SPU、PYU 等多种类型
    records = to_records(sf_data, party=party)
    # 第二步：调用表级分类接口
    # schema 从第一条记录的键名提取；空数据集时 schema 为空列表
    return api.classify_table(
        schema=list(records[0].keys()) if records else [],  # 字段名列表
        rows=records,    # 数据记录列表
        params=params,   # 分类参数
    )


# 模块公开接口声明
__all__ = [
    "TEMPLATES",              # 合规模板参数字典
    "classify_secretflow",    # SecretFlow 适配器
    "get_template_params",    # 模板参数获取函数
    "hash_value",             # 值哈希函数
    "mask_record_values",     # 记录脱敏函数
    "redact",                 # 值脱敏函数
    "safe_log",               # 安全日志函数
    "should_log_value",       # 日志安全判断函数
]
