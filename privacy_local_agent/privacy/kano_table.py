"""数据集级 K-匿名（K-Anonymity）处理模块。

使用 Mondrian 多维分区算法对整张表进行 K-匿名泛化，确保每个等价组的大小
至少为 k。对数值型准标识符输出区间泛化，对分类型准标识符输出取值集合。

Dataset-level K-anonymity using the Mondrian multidimensional partitioning
algorithm. Generalizes numeric QIs to intervals and categorical QIs to value sets.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..observability.metrics import KANO_OPERATIONS_TOTAL


def _is_numeric(value: Any) -> bool:
    """判断一个值是否为数值类型（int/float），排除布尔值。"""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _span(records: List[Dict[str, Any]], col: str) -> float:
    """计算某列的跨度，用于选择分割维度。

    数值型列返回 max - min；分类型列返回不同取值数量减 1。
    """
    values = [r.get(col) for r in records if r.get(col) is not None]
    if not values:
        return 0.0
    if all(_is_numeric(v) for v in values):
        return float(max(values) - min(values))
    return float(len(set(str(v) for v in values)) - 1)


def _choose_dimension(records: List[Dict[str, Any]], qi_cols: List[str]) -> str:
    """选择跨度最大的准标识符列作为当前分割维度。"""
    spans = {col: _span(records, col) for col in qi_cols}
    return max(spans, key=spans.get)  # type: ignore[arg-type]


def _median_split(
    records: List[Dict[str, Any]], dim: str, k: int
) -> Optional[int]:
    """按指定维度的中位数进行分割，并确保左右两部分均不少于 k 条记录。

    Args:
        records: 当前记录集合。
        dim: 分割维度列名。
        k: K-匿名阈值。

    Returns:
        合法的分割下标；若不存在则返回 None。
    """
    if len(records) < 2 * k:
        return None

    def _sort_key(record: Dict[str, Any]) -> Any:
        value = record.get(dim)
        if _is_numeric(value):
            return value
        return str(value)

    sorted_records = sorted(records, key=_sort_key)
    mid = len(sorted_records) // 2
    # 保证左侧 >= k 且右侧 >= k
    split_idx = max(k, min(mid, len(sorted_records) - k))
    if split_idx < k or len(sorted_records) - split_idx < k:
        return None
    return split_idx


def _generalize(
    records: List[Dict[str, Any]], qi_cols: List[str]
) -> List[Dict[str, Any]]:
    """对等价组内的记录进行泛化。

    数值型 QI 泛化为 "[min-max]"，分类型 QI 泛化为 "{v1,v2,...}"，
    非 QI 字段保持不变。
    """
    if not records:
        return []

    generalized: List[Dict[str, Any]] = []
    for col in qi_cols:
        values = [r.get(col) for r in records if r.get(col) is not None]
        if all(_is_numeric(v) for v in values):
            low, high = min(values), max(values)
            if low == high:
                generalized.append({col: low})
            else:
                generalized.append({col: f"[{low}-{high}]"})
        else:
            unique = sorted(set(str(v) for v in values))
            if len(unique) == 1:
                # 保持原值
                generalized.append({col: unique[0]})
            else:
                generalized.append({col: "{" + ",".join(unique) + "}"})

    result: List[Dict[str, Any]] = []
    for record in records:
        new_record = dict(record)
        for item in generalized:
            new_record.update(item)
        result.append(new_record)
    return result


def k_anonymize_table(
    rows: List[Dict[str, Any]],
    qi_cols: List[str],
    k: int = 5,
    max_depth: int = 10,
) -> List[Dict[str, Any]]:
    """对整张表执行 Mondrian K-匿名泛化。

    Args:
        rows: 原始记录列表。
        qi_cols: 准标识符列名列表。
        k: K-匿名阈值，每个等价组至少包含 k 条记录。
        max_depth: 最大递归深度，防止过泛化。

    Returns:
        泛化后的记录列表（顺序可能与输入不同）。

    Raises:
        ValueError: 输入记录数不足 k，或 qi_cols 包含输入中不存在的列。
    """
    KANO_OPERATIONS_TOTAL.labels(operation="table").inc()
    if not rows:
        return []
    if len(rows) < k:
        raise ValueError(
            f"Input table has {len(rows)} rows, but k-anonymity requires at least {k}"
        )
    if not qi_cols:
        raise ValueError("qi_cols must not be empty")
    missing_cols = [col for col in qi_cols if col not in rows[0]]
    if missing_cols:
        raise ValueError(f"qi_cols not found in rows: {missing_cols}")

    def _mondrian(
        records: List[Dict[str, Any]], depth: int
    ) -> List[Dict[str, Any]]:
        if len(records) < 2 * k or depth <= 0:
            return _generalize(records, qi_cols)

        dim = _choose_dimension(records, qi_cols)
        split_idx = _median_split(records, dim, k)
        if split_idx is None:
            return _generalize(records, qi_cols)

        sorted_records = sorted(
            records,
            key=lambda r: r.get(dim) if _is_numeric(r.get(dim)) else str(r.get(dim)),
        )
        left = _mondrian(sorted_records[:split_idx], depth - 1)
        right = _mondrian(sorted_records[split_idx:], depth - 1)
        return left + right

    return _mondrian(rows, max_depth)


def k_anonymize_dataframe(
    df: Any,
    qi_cols: List[str],
    k: int = 5,
    max_depth: int = 10,
) -> Any:
    """对 DataFrame 执行 Mondrian K-匿名泛化。

    支持 pandas DataFrame 与 SecretFlow DataFrame（H/V）。
    内部转换为 records 后调用 k_anonymize_table，再按原类型返回。

    Args:
        df: 输入 DataFrame。
        qi_cols: 准标识符列名列表。
        k: K-匿名阈值。
        max_depth: 最大递归深度。

    Returns:
        泛化后的 DataFrame（pandas DataFrame）。
    """
    from .data_adapters import from_records, to_records

    KANO_OPERATIONS_TOTAL.labels(operation="dataframe").inc()
    records = to_records(df)
    anonymized = k_anonymize_table(records, qi_cols, k=k, max_depth=max_depth)
    return from_records(anonymized, df)
