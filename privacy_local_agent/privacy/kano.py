"""K-匿名（K-Anonymity）记录级处理模块。

提供针对准标识符（Quasi-Identifiers, QI）的泛化函数与内置泛化层次结构，
支持对单条记录按指定 k 值进行泛化，降低重识别风险。

K-anonymity primitives for record-level generalization. Provides hierarchy
generalization functions for common QIs such as age, zipcode and gender.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Union

from ..observability.metrics import KANO_OPERATIONS_TOTAL


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
    """性别泛化层次函数。

    - level 0: 原始值
    - level >= 1: "*"

    Args:
        value: 原始性别字符串。
        level: 泛化层级。

    Returns:
        泛化后的性别表示。
    """
    return "*" if level >= 1 else value


# 内置准标识符泛化层次结构映射表
BUILTIN_HIERARCHIES = {
    "age": age_hierarchy,
    "zipcode": zipcode_hierarchy,
    "gender": gender_hierarchy,
}


def choose_level(k: int, max_level: int) -> int:
    """根据 k 值选择泛化层级。

    采用启发式策略：level 与 k // 5 成正比，但不超过 max_level 且至少为 1。

    Args:
        k: K-匿名参数，目标匿名集大小。
        max_level: 该字段支持的最大泛化层级。

    Returns:
        选定的泛化层级（正整数）。
    """
    level = k // 5
    return max(1, min(level, max_level))


def anonymize_record(
    record: Dict[str, Any],
    qi_cols: List[str],
    hierarchies: Dict[str, GeneralizationHierarchy],
    k: int,
    return_details: bool = False,
) -> Union[Dict[str, Any], KAnonymityRecordResult]:
    """对单条记录按 K-匿名要求进行泛化。

    执行步骤：
    1. 增加 `KANO_OPERATIONS_TOTAL` 操作指标计数。
    2. 拷贝输入记录字典，仅处理 `qi_cols` 中指定的准标识符列。
    3. 寻找匹配的泛化层次函数（若未显式传参则查询内置 `BUILTIN_HIERARCHIES`）。
    4. 根据 `k` 智选粒度层级 `choose_level`，应用泛化替换准标识符属性。
    5. 返回泛化后的记录或封装结果。
    """
    KANO_OPERATIONS_TOTAL.labels(operation="record").inc()
    result = dict(record)
    effective_hierarchies = {**BUILTIN_HIERARCHIES, **(hierarchies or {})}
    applied_levels: Dict[str, int] = {}
    hierarchies_used: Dict[str, str] = {}
    for col in qi_cols:
        h = effective_hierarchies.get(col)
        val = result.get(col)
        if h is not None and isinstance(val, str):
            max_level = 4 if col != "gender" else 1
            level = choose_level(k, max_level)
            result[col] = h(val, level)
            applied_levels[col] = level
            hierarchies_used[col] = h.__name__
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
