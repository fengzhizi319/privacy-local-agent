"""数据格式适配器。

为 DP 等隐私原语提供统一的数据输入适配，支持从多种数据格式中提取目标列：

- Python list/tuple
- numpy ndarray
- pandas Series/DataFrame
- SecretFlow DataFrame / HDataFrame / VDataFrame / MixDataFrame / FedNdarray

SecretFlow 相关依赖为可选依赖，未安装时不会影响其他格式的支持。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Union
import numpy as np


def _is_secretflow_available() -> bool:
    try:
        import secretflow  # noqa: F401
        return True
    except ImportError:
        return False


def _extract_from_secretflow(
    data: Any,
    column: Optional[str] = None,
    party: Optional[str] = None,
) -> np.ndarray:
    """从 SecretFlow 数据结构中抽取目标列并返回 np.ndarray。"""
    import secretflow as sf

    # FedNdarray: 联邦 ndarray，底层是 numpy
    if hasattr(data, "partitions") and hasattr(data, "partition_way"):
        # HDataFrame / VDataFrame / MixDataFrame
        return _extract_from_sf_dataframe(data, column=column, party=party)

    # 本地 DataFrame（sf.data.DataFrame 底层通常是 pandas）
    if isinstance(data, sf.data.DataFrame):
        if column is None:
            raise ValueError(
                "column must be specified when input is a SecretFlow DataFrame"
            )
        return _to_numpy_array(data[column].to_numpy())

    raise TypeError(f"Unsupported SecretFlow data type: {type(data)}")


def _extract_from_sf_dataframe(
    data: Any,
    column: Optional[str] = None,
    party: Optional[str] = None,
) -> np.ndarray:
    """从 SecretFlow HDataFrame / VDataFrame / MixDataFrame 中提取数据。"""
    from secretflow.data.horizontal import HDataFrame
    from secretflow.data.mix import MixDataFrame
    from secretflow.data.vertical import VDataFrame

    if column is None:
        raise ValueError(
            "column must be specified when input is a SecretFlow federated DataFrame"
        )

    if isinstance(data, HDataFrame):
        return _extract_from_hdataframe(data, column=column, party=party)

    if isinstance(data, VDataFrame):
        return _extract_from_vdataframe(data, column=column)

    if isinstance(data, MixDataFrame):
        raise TypeError(
            "MixDataFrame is not directly supported; please convert to "
            "HDataFrame/VDataFrame or extract values manually"
        )

    raise TypeError(f"Unsupported SecretFlow federated DataFrame type: {type(data)}")


def _extract_from_hdataframe(
    data: Any,
    column: str,
    party: Optional[str] = None,
) -> np.ndarray:
    """从 HDataFrame 中提取指定列。

    HDataFrame 是水平分割：各参与方拥有相同样本空间的不同子集。
    若未指定 party 且只有一个 partition，则自动提取；否则必须指定 party。
    """
    partitions = dict(data.partitions)
    if party is None:
        if len(partitions) == 1:
            partition = next(iter(partitions.values()))
        else:
            raise ValueError(
                "party must be specified when HDataFrame has multiple partitions"
            )
    else:
        # party 可以是 str(device name) 或 PYU 对象
        key = None
        for k in partitions:
            if str(k) == party or getattr(k, "device_id", None) == party:
                key = k
                break
        if key is None:
            raise ValueError(f"party '{party}' not found in HDataFrame partitions")
        partition = partitions[key]

    pdf = partition.data
    if column not in pdf.columns:
        raise ValueError(f"column '{column}' not found in HDataFrame partition")
    return _to_numpy_array(pdf[column].to_numpy())


def _extract_from_vdataframe(data: Any, column: str) -> np.ndarray:
    """从 VDataFrame 中提取指定列。

    VDataFrame 是垂直分割：列分布在不同参与方。系统会自动找到包含该列的 partition。
    """
    for partition in data.partitions.values():
        pdf = partition.data
        if column in pdf.columns:
            return _to_numpy_array(pdf[column].to_numpy())
    raise ValueError(f"column '{column}' not found in any VDataFrame partition")


def _is_sparse_matrix(data: Any) -> bool:
    """判断是否为 scipy.sparse 稀疏矩阵。"""
    try:
        import scipy.sparse as sp

        return sp.issparse(data)
    except ImportError:
        return False


def _to_numpy_array(arr: Any) -> np.ndarray:
    """辅助函数：尝试将输入数组转换为 np.float64 ndarray，无法转数值时保留 object。"""
    np_arr = np.asarray(arr)
    if np_arr.ndim != 1:
        np_arr = np_arr.ravel()
    if np.issubdtype(np_arr.dtype, np.number) or np.issubdtype(np_arr.dtype, np.bool_):
        return np_arr.astype(np.float64, copy=False)
    try:
        return np_arr.astype(np.float64)
    except (ValueError, TypeError):
        return np_arr


def _to_2d_numpy_array(data: Any) -> Any:
    """将多种表格或 2D 数据源转换为二维 np.ndarray (float64) 或保留 scipy.sparse 矩阵。"""
    if _is_sparse_matrix(data):
        return data

    try:
        import pandas as pd

        if isinstance(data, pd.DataFrame):
            return data.to_numpy(dtype=np.float64)
        if isinstance(data, pd.Series):
            return data.to_numpy(dtype=np.float64).reshape(-1, 1)
    except ImportError:
        pass

    arr = np.asarray(data)
    if arr.ndim == 1:
        arr = arr.reshape(-1, 1)
    if np.issubdtype(arr.dtype, np.number) or np.issubdtype(arr.dtype, np.bool_):
        return arr.astype(np.float64, copy=False)
    try:
        return arr.astype(np.float64)
    except (ValueError, TypeError):
        return arr


def extract_values(
    data: Any,
    column: Optional[str] = None,
    party: Optional[str] = None,
) -> Union[np.ndarray, Any]:
    """从多种数据格式中提取数值或类别数组并统一返回一维 np.ndarray 或 scipy.sparse 矩阵。

    Args:
        data: 输入数据。支持 list/tuple/ndarray/Series/DataFrame/SecretFlow/sparse/arrow/polars 格式。
        column: 当 data 为表格类型且需要指定列时使用。
        party: 当 data 为 HDataFrame 且需要指定参与方时使用。

    Returns:
        一维 np.ndarray 数组或 scipy.sparse 矩阵。
    """
    # 0. scipy.sparse 稀疏矩阵
    if _is_sparse_matrix(data):
        return data

    # 1. numpy ndarray
    if isinstance(data, np.ndarray):
        return _to_numpy_array(data)

    # 2. Python 原生序列
    if isinstance(data, (list, tuple)):
        return _to_numpy_array(data)

    # 3. pandas Series / DataFrame
    try:
        import pandas as pd

        if isinstance(data, pd.Series):
            return _to_numpy_array(data.to_numpy())
        if isinstance(data, pd.DataFrame):
            if column is None:
                raise ValueError(
                    "column must be specified when input is a pandas DataFrame"
                )
            if column not in data.columns:
                raise ValueError(f"column '{column}' not found in DataFrame")
            return _to_numpy_array(data[column].to_numpy())
    except ImportError:
        pass

    # 4. SecretFlow 数据结构（可选依赖）
    if _is_secretflow_available():
        return _extract_from_secretflow(data, column=column, party=party)

    # 5. 通用 duck-typing 回退 (Polars / PyArrow / Zero-copy)
    if hasattr(data, "to_numpy"):
        try:
            return _to_numpy_array(data.to_numpy(zero_copy_only=False))
        except TypeError:
            return _to_numpy_array(data.to_numpy())
    if hasattr(data, "tolist"):
        return _to_numpy_array(data.tolist())

    raise TypeError(f"Unsupported data type for DP values: {type(data)}")


def extract_chunks(
    chunks: Iterable[Any],
    column: Optional[str] = None,
    party: Optional[str] = None,
) -> List[np.ndarray]:
    """对分块输入逐块调用 extract_values，返回 List[np.ndarray]。"""
    return [extract_values(chunk, column=column, party=party) for chunk in chunks]


def _extract_dataframe_partition(data: Any) -> Any:
    """从 SecretFlow HDataFrame / VDataFrame 中提取单个 pandas DataFrame。

    - HDataFrame 多 partition 时需要指定 party；单 partition 自动选择。
    - VDataFrame 所有列应在同一个 partition 中（目前只取第一个 partition）。
    """
    import secretflow as sf
    from secretflow.data.horizontal import HDataFrame
    from secretflow.data.vertical import VDataFrame

    if isinstance(data, HDataFrame):
        partitions = dict(data.partitions)
        if len(partitions) == 1:
            return next(iter(partitions.values())).data
        raise ValueError(
            "party must be specified when input HDataFrame has multiple partitions"
        )

    if isinstance(data, VDataFrame):
        # VDataFrame 中所有列都在同一个参与方，取第一个 partition
        return next(iter(data.partitions.values())).data

    if isinstance(data, sf.data.DataFrame):
        return data

    raise TypeError(f"Unsupported SecretFlow data type: {type(data)}")


def to_records(
    data: Any,
    party: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """将多种表格型输入统一转换为 List[Dict[str, Any]]。

    支持：
    - list/tuple of dict
    - pandas DataFrame
    - SecretFlow DataFrame / HDataFrame / VDataFrame（可选依赖）

    Args:
        data: 输入数据。
        party: SecretFlow HDataFrame 多 partition 时指定参与方。

    Returns:
        记录列表，每条记录为字段名到值的字典。
    """
    # 1. 原生 dict 列表
    if isinstance(data, (list, tuple)):
        if not data:
            return []
        if all(isinstance(r, dict) for r in data):
            return list(data)
        raise TypeError("list input must contain dict records")

    # 2. pandas DataFrame
    try:
        import pandas as pd

        if isinstance(data, pd.DataFrame):
            return data.to_dict(orient="records")
    except ImportError:
        pass

    # 3. SecretFlow 数据结构（可选依赖）
    if _is_secretflow_available():
        pdf = _extract_dataframe_partition(data)
        if isinstance(pdf, pd.DataFrame):
            return pdf.to_dict(orient="records")
        # SecretFlow 本地 DataFrame 可能直接是 pandas-like
        return pdf.to_dict(orient="records")

    raise TypeError(f"Unsupported table input type: {type(data)}")


def from_records(
    records: List[Dict[str, Any]], original: Any
) -> Any:
    """根据原始输入类型将 records 转换回对应格式。

    当前支持：
    - pandas DataFrame -> pandas DataFrame
    - SecretFlow DataFrame -> pandas DataFrame（返回本地副本）
    - list/tuple of dict -> list of dict
    """
    # pandas / SecretFlow DataFrame 统一返回 pandas DataFrame
    try:
        import pandas as pd

        if isinstance(original, pd.DataFrame):
            return pd.DataFrame(records)
    except ImportError:
        pass

    if _is_secretflow_available():
        import pandas as pd
        from secretflow.data.horizontal import HDataFrame
        from secretflow.data.vertical import VDataFrame

        if isinstance(original, (HDataFrame, VDataFrame)) or hasattr(original, "partitions"):
            return pd.DataFrame(records)

    if isinstance(original, (list, tuple)):
        return records

    return records
