"""K-匿名（K-Anonymity）记录级处理模块 / K-Anonymity Record-Level Primitive API Implementation.

中文说明：
提供针对准标识符（Quasi-Identifiers, QI）的泛化函数与内置泛化层次结构，
支持对单条记录按指定 k 值进行泛化，降低重识别风险。
内置输入校验、结构化日志与 Prometheus 指标埋点。

English Description:
K-anonymity primitives for record-level generalization. Provides hierarchy
generalization functions for common QIs such as age, zipcode, gender, and salary.
Supports single-record and batch generalization with configurable hierarchies.
Built-in input validation, structured logging, and Prometheus metrics instrumentation.

扩展能力 / Key Features:
- 枚举类型安全：QIType / GeneralizationStrategy 枚举避免裸字符串拼写错误。
- 结构化日志：每次操作记录 k 值、准标识符列、泛化层级等上下文信息。
- 输入校验：统一的参数合法性检查，快速失败并给出清晰错误信息。
- 可扩展泛化层次：支持自定义泛化函数与内置层次结构映射表。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Union

from ..observability.logging_config import get_logger
from ..observability.metrics import KANO_OPERATIONS_TOTAL

# Module-level structured logger for K-anonymity operations
logger = get_logger(__name__)


class QIType(str, Enum):
    """准标识符类型枚举 / Quasi-Identifier Type Enum.

    继承 str 保证与字符串的向后兼容性：QIType.AGE == "age" 为 True。
    IDE 自动补全 + 静态类型检查，避免裸字符串拼写错误。
    """

    AGE = "age"
    ZIPCODE = "zipcode"
    GENDER = "gender"
    SALARY = "salary"
    EDUCATION = "education"


class GeneralizationStrategy(str, Enum):
    """泛化策略枚举 / Generalization Strategy Enum.

    继承 str 保证与字符串的向后兼容性。
    用于标识不同的泛化方法（区间泛化、集合泛化、抑制等）。
    """

    INTERVAL = "interval"      # 数值区间泛化 / Numeric interval generalization
    SET = "set"                # 分类集合泛化 / Categorical set generalization
    SUPPRESSION = "suppression"  # 完全抑制 / Full suppression (*)
    PREFIX = "prefix"          # 前缀保留泛化 / Prefix-preserving generalization


def _validate_k(k: int) -> None:
    """校验 K-匿名参数有效性 / Validate K-anonymity parameter.

    Args:
        k: K-匿名参数。

    Raises:
        ValueError: 当 k 不是正整数或小于 2 时抛出。
    """
    if not isinstance(k, int) or isinstance(k, bool):
        raise ValueError(f"k must be an integer, got {type(k).__name__}")
    if k < 2:
        raise ValueError(f"k must be at least 2 for meaningful anonymity, got {k}")


def _validate_qi_cols(qi_cols: List[str]) -> None:
    """校验准标识符列名列表有效性 / Validate QI column list.

    Args:
        qi_cols: 准标识符列名列表。

    Raises:
        ValueError: 当列表为空或包含非字符串元素时抛出。
    """
    if not qi_cols:
        raise ValueError("qi_cols must not be empty")
    for col in qi_cols:
        if not isinstance(col, str) or not col.strip():
            raise ValueError(f"each qi_col must be a non-empty string, got {col!r}")


@dataclass
class KAnonymityRecordResult:
    """K-匿名单条记录泛化结果及结构化元数据包装。

    Attributes:
        value: 泛化后的记录字典。
        k: K-匿名阈值参数。
        qi_cols: 准标识符列名列表。
        applied_level: 实际应用的泛化层级（由 k 值启发式决定）。
        hierarchies_used: 本次使用的泛化层次函数名称映射。
    """

    value: Dict[str, Any]
    k: int
    qi_cols: List[str]
    applied_level: int = 1
    hierarchies_used: Dict[str, str] = field(default_factory=dict)

    def to_arrow(self):
        """将 KAnonymityRecordResult 包装转换为附带 K-匿名 Metadata 的 PyArrow Table。

        执行步骤：
        1. 提取 K-匿名记录级元数据（k 值、准标识符列表、应用层级）构造 JSON。
        2. 将元数据编码存储为 Schema Metadata Key `b"k_anonymity_record_metadata"`。
        3. 根据 `value` 字典构造 PyArrow Table。
        4. 替换 Table Schema Metadata 后导出。
        """
        import json
        import pyarrow as pa

        meta = {
            "k": str(self.k),
            "qi_cols": str(self.qi_cols),
            "applied_level": str(self.applied_level),
            "hierarchies_used": str(self.hierarchies_used),
        }
        custom_metadata = {b"k_anonymity_record_metadata": json.dumps(meta).encode("utf-8")}

        keys = pa.array(list(self.value.keys()))
        vals = pa.array([str(v) for v in self.value.values()])
        table = pa.Table.from_arrays([keys, vals], names=["field", "generalized_value"])

        existing_meta = table.schema.metadata or {}
        merged_meta = {**existing_meta, **custom_metadata}
        return table.replace_schema_metadata(merged_meta)

# 泛化层次函数类型：输入原始值与泛化层级，输出泛化后的字符串
GeneralizationHierarchy = Callable[[str, int], str]


def age_hierarchy(value: str, level: int) -> str:
    """年龄泛化层次函数。

    根据 level 将具体年龄泛化为区间：
    - level 0: 原始值
    - level 1: 5 岁区间，如 "[25-30]"
    - level 2: 10 岁区间
    - level 3: 20 岁区间
    - level >= 4: "*"

    Args:
        value: 原始年龄字符串，需可转换为整数。
        level: 泛化层级，数值越大粒度越粗。

    Returns:
        泛化后的年龄表示。
    """
    age = int(value)
    if level == 0:
        return value
    if level == 1:
        start = (age // 5) * 5
        return f"[{start}-{start + 5}]"
    if level == 2:
        start = (age // 10) * 10
        return f"[{start}-{start + 10}]"
    if level == 3:
        start = (age // 20) * 20
        return f"[{start}-{start + 20}]"
    return "*"


def zipcode_hierarchy(value: str, level: int) -> str:
    """邮编泛化层次函数。

    根据 level 逐步隐藏邮编后几位：
    - level 0: 原始值
    - level 1: 保留前 3 位
    - level 2: 保留前 2 位
    - level 3: 保留前 1 位
    - level >= 4 或长度不足: "*"

    Args:
        value: 原始邮编字符串。
        level: 泛化层级。

    Returns:
        泛化后的邮编表示。
    """
    if level == 0:
        return value
    if level == 1 and len(value) >= 3:
        return value[:3] + "***"
    if level == 2 and len(value) >= 2:
        return value[:2] + "****"
    if level == 3 and len(value) >= 1:
        return value[0] + "*****"
    return "*"


def gender_hierarchy(value: str, level: int) -> str:
    """性别泛化层次函数 / Gender Generalization Hierarchy.

    - level 0: 原始值 / Original value
    - level >= 1: "*" (完全抑制 / Full suppression)

    Args:
        value: 原始性别字符串 / Original gender string.
        level: 泛化层级 / Generalization level.

    Returns:
        泛化后的性别表示 / Generalized gender representation.
    """
    return "*" if level >= 1 else value


def salary_hierarchy(value: str, level: int) -> str:
    """薪资泛化层次函数 / Salary Generalization Hierarchy.

    根据 level 将具体薪资泛化为区间：
    - level 0: 原始值 / Original value
    - level 1: 5K 区间 / 5K interval, e.g. "[10K-15K]"
    - level 2: 10K 区间 / 10K interval
    - level 3: 50K 区间 / 50K interval
    - level >= 4: "*" (完全抑制 / Full suppression)

    Args:
        value: 原始薪资字符串（单位：K）/ Original salary string (unit: K).
        level: 泛化层级 / Generalization level.

    Returns:
        泛化后的薪资表示 / Generalized salary representation.
    """
    try:
        salary = int(float(value))
    except (ValueError, TypeError):
        return "*" if level >= 1 else value
    if level == 0:
        return value
    if level == 1:
        start = (salary // 5) * 5
        return f"[{start}K-{start + 5}K]"
    if level == 2:
        start = (salary // 10) * 10
        return f"[{start}K-{start + 10}K]"
    if level == 3:
        start = (salary // 50) * 50
        return f"[{start}K-{start + 50}K]"
    return "*"


def education_hierarchy(value: str, level: int) -> str:
    """学历泛化层次函数 / Education Generalization Hierarchy.

    根据 level 将具体学历泛化为更宽泛的类别：
    - level 0: 原始值 / Original value
    - level 1: 合并为 "高等教育" / "基础教育" / Merge into "Higher" / "Basic"
    - level >= 2: "*" (完全抑制 / Full suppression)

    Args:
        value: 原始学历字符串 / Original education string.
        level: 泛化层级 / Generalization level.

    Returns:
        泛化后的学历表示 / Generalized education representation.
    """
    if level == 0:
        return value
    higher_edu = {"本科", "硕士", "博士", "博士后", "MBA", "EMBA", "bachelor", "master", "phd", "doctorate"}
    if level == 1:
        if value.lower() in higher_edu or value in higher_edu:
            return "高等教育"
        return "基础教育"
    return "*"


# 内置准标识符泛化层次结构映射表 / Built-in QI Generalization Hierarchy Registry
BUILTIN_HIERARCHIES: Dict[str, GeneralizationHierarchy] = {
    QIType.AGE.value: age_hierarchy,
    QIType.ZIPCODE.value: zipcode_hierarchy,
    QIType.GENDER.value: gender_hierarchy,
    QIType.SALARY.value: salary_hierarchy,
    QIType.EDUCATION.value: education_hierarchy,
}


def choose_level(k: int, max_level: int) -> int:
    """根据 k 值选择泛化层级 / Select Generalization Level by K Value.

    采用启发式策略：level 与 k // 5 成正比，但不超过 max_level 且至少为 1。

    执行步骤 / Execution Steps:
    1. 校验 k 和 max_level 参数有效性。
       (Validate k and max_level parameters)
    2. 计算启发式层级 level = k // 5。
       (Compute heuristic level = k // 5)
    3. 将层级限制在 [1, max_level] 区间内。
       (Clamp level to [1, max_level] range)

    Args:
        k: K-匿名参数，目标匿名集大小 / K-anonymity parameter.
        max_level: 该字段支持的最大泛化层级 / Maximum supported generalization level.

    Returns:
        选定的泛化层级（正整数）/ Selected generalization level (positive integer).

    Raises:
        ValueError: 当 k < 2 或 max_level < 1 时 / When k < 2 or max_level < 1.
    """
    _validate_k(k)
    if max_level < 1:
        raise ValueError(f"max_level must be at least 1, got {max_level}")
    level = k // 5
    return max(1, min(level, max_level))


def anonymize_record(
    record: Dict[str, Any],
    qi_cols: List[str],
    hierarchies: Dict[str, GeneralizationHierarchy],
    k: int,
    return_details: bool = False,
) -> Union[Dict[str, Any], KAnonymityRecordResult]:
    """对单条记录按 K-匿名要求进行泛化 / Generalize Single Record for K-Anonymity.

    执行步骤 / Execution Steps:
    1. 校验 k 值和 qi_cols 参数有效性。
       (Validate k value and qi_cols parameters)
    2. 增加 `KANO_OPERATIONS_TOTAL` 操作指标计数。
       (Increment KANO_OPERATIONS_TOTAL metrics counter)
    3. 拷贝输入记录字典，仅处理 `qi_cols` 中指定的准标识符列。
       (Copy input record, process only specified QI columns)
    4. 寻找匹配的泛化层次函数（若未显式传参则查询内置 `BUILTIN_HIERARCHIES`）。
       (Resolve hierarchy function from explicit param or built-in registry)
    5. 根据 `k` 智选粒度层级 `choose_level`，应用泛化替换准标识符属性。
       (Select level via choose_level and apply generalization)
    6. 记录结构化日志并返回泛化后的记录或封装结果。
       (Emit structured log and return generalized record or result)

    Args:
        record: 待泛化的记录字典 / Record dict to generalize.
        qi_cols: 准标识符列名列表 / Quasi-Identifier column names.
        hierarchies: 自定义泛化层次函数映射 / Custom hierarchy function mapping.
        k: K-匿名阈值参数 / K-anonymity threshold parameter.
        return_details: 是否返回 KAnonymityRecordResult / Whether to return result struct.

    Returns:
        泛化后的记录字典或 KAnonymityRecordResult / Generalized record or result struct.

    Raises:
        ValueError: 当 k < 2 或 qi_cols 为空时 / When k < 2 or qi_cols is empty.
    """
    _validate_k(k)
    _validate_qi_cols(qi_cols)
    if not isinstance(record, dict):
        raise ValueError(f"record must be a dict, got {type(record).__name__}")

    KANO_OPERATIONS_TOTAL.labels(operation="record").inc()
    result = dict(record)
    effective_hierarchies = {**BUILTIN_HIERARCHIES, **(hierarchies or {})}
    applied_levels: Dict[str, int] = {}
    hierarchies_used: Dict[str, str] = {}
    for col in qi_cols:
        h = effective_hierarchies.get(col)
        val = result.get(col)
        if h is not None and isinstance(val, str):
            max_level = 4 if col not in (QIType.GENDER.value, QIType.EDUCATION.value) else (
                1 if col == QIType.GENDER.value else 2
            )
            level = choose_level(k, max_level)
            result[col] = h(val, level)
            applied_levels[col] = level
            hierarchies_used[col] = h.__name__

    logger.info(
        "kano_anonymize_record_completed",
        extra={
            "k": k,
            "qi_cols": qi_cols,
            "applied_levels": applied_levels,
            "num_qi_processed": len(applied_levels),
        },
    )

    if return_details:
        avg_level = (
            sum(applied_levels.values()) // len(applied_levels)
            if applied_levels
            else 1
        )
        return KAnonymityRecordResult(
            value=result,
            k=k,
            qi_cols=qi_cols,
            applied_level=avg_level,
            hierarchies_used=hierarchies_used,
        )
    return result


def anonymize_records_batch(
    records: List[Dict[str, Any]],
    qi_cols: List[str],
    hierarchies: Optional[Dict[str, GeneralizationHierarchy]] = None,
    k: int = 5,
    return_details: bool = False,
) -> Union[List[Dict[str, Any]], KAnonymityRecordResult]:
    """批量对多条记录按 K-匿名要求进行泛化 / Batch Generalize Records for K-Anonymity.

    执行步骤 / Execution Steps:
    1. 校验 k 值、qi_cols 和 records 参数有效性。
       (Validate k, qi_cols, and records parameters)
    2. 增加 `KANO_OPERATIONS_TOTAL` 操作指标计数。
       (Increment KANO_OPERATIONS_TOTAL metrics counter)
    3. 遍历每条记录调用 `anonymize_record` 执行泛化。
       (Iterate records and apply anonymize_record per record)
    4. 记录结构化日志并返回泛化后的记录列表或封装结果。
       (Emit structured log and return generalized records or result)

    Args:
        records: 待泛化的记录字典列表 / List of record dicts to generalize.
        qi_cols: 准标识符列名列表 / Quasi-Identifier column names.
        hierarchies: 可选自定义泛化层次函数映射 / Optional custom hierarchy mapping.
        k: K-匿名阈值参数 / K-anonymity threshold parameter.
        return_details: 是否返回 KAnonymityRecordResult / Whether to return result struct.

    Returns:
        泛化后的记录列表或 KAnonymityRecordResult / Generalized records or result struct.

    Raises:
        ValueError: 当 records 为空或 k < 2 时 / When records is empty or k < 2.
    """
    _validate_k(k)
    _validate_qi_cols(qi_cols)
    if not records:
        raise ValueError("records must not be empty")
    if not isinstance(records, list):
        raise ValueError(f"records must be a list, got {type(records).__name__}")

    KANO_OPERATIONS_TOTAL.labels(operation="record_batch").inc()
    effective_hierarchies = hierarchies or {}
    generalized: List[Dict[str, Any]] = []
    total_levels: List[int] = []
    all_hierarchies_used: Dict[str, str] = {}

    for record in records:
        result = anonymize_record(
            record, qi_cols, effective_hierarchies, k, return_details=True
        )
        if isinstance(result, KAnonymityRecordResult):
            generalized.append(result.value)
            total_levels.append(result.applied_level)
            all_hierarchies_used.update(result.hierarchies_used)
        else:
            generalized.append(result)

    logger.info(
        "kano_anonymize_records_batch_completed",
        extra={
            "k": k,
            "qi_cols": qi_cols,
            "num_records": len(records),
            "avg_level": sum(total_levels) // max(1, len(total_levels)),
        },
    )

    if return_details:
        avg_level = sum(total_levels) // max(1, len(total_levels)) if total_levels else 1
        return KAnonymityRecordResult(
            value={"records": generalized, "count": len(generalized)},
            k=k,
            qi_cols=qi_cols,
            applied_level=avg_level,
            hierarchies_used=all_hierarchies_used,
        )
    return generalized
