"""K-匿名（K-Anonymity）记录级处理模块。

提供针对准标识符（Quasi-Identifiers, QI）的泛化函数与内置泛化层次结构，
支持对单条记录按指定 k 值进行泛化，降低重识别风险。

K-anonymity primitives for record-level generalization. Provides hierarchy
generalization functions for common QIs such as age, zipcode and gender.
"""

from typing import Any, Callable, Dict, List

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
) -> Dict[str, Any]:
    """对单条记录按 K-匿名要求进行泛化。

    仅处理 qi_cols 中列出的准标识符字段；对于存在内置泛化层次且值为字符串的字段，
    调用对应层次函数进行泛化。其他字段保持原值不变。

    Args:
        record: 原始记录字典。
        qi_cols: 准标识符列名列表。
        hierarchies: 列名到泛化层次函数的映射。
        k: K-匿名参数，用于决定泛化层级。

    Returns:
        泛化后的新记录字典（不修改原始字典）。

    Note:
        当前 MVP 版本主要依赖 BUILTIN_HIERARCHIES；自定义层次结构参数已预留，
        但尚未实际合并到内置层次中。
    """
    result = dict(record)
    for col in qi_cols:
        h = hierarchies.get(col)
        val = result.get(col)
        if h is not None and isinstance(val, str):
            # 内置字段中，gender 最大泛化层级为 1，其余默认为 4
            max_level = 4 if col != "gender" else 1
            result[col] = h(val, choose_level(k, max_level))
    return result
