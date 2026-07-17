"""SecretFlow data structure adapters for classification.

将 SecretFlow 联邦数据结构（DataFrame / HDataFrame / VDataFrame / FedNdarray）
转换为 records 后调用 ClassificationAPI，返回结果保留原始类型。
"""

from typing import Any, Dict, Optional

from .data_adapters import from_records, to_records


def classify_secretflow(
    api: Any,
    sf_data: Any,
    params: Optional[Dict[str, Any]] = None,
    party: Optional[str] = None,
) -> Any:
    """对 SecretFlow 数据结构进行分类。

    Args:
        api: ClassificationAPI 实例。
        sf_data: SecretFlow DataFrame / HDataFrame / VDataFrame / FedNdarray。
        params: 请求级分类参数。
        party: HDataFrame 参与方；单 partition 时可省略。

    Returns:
        ClassificationResult，与同步表分类结果结构一致。

    Raises:
        ImportError: 未安装 secretflow 或 pandas。
        TypeError: 传入不支持的 SecretFlow 类型。
    """
    records = to_records(sf_data, party=party)
    return api.classify_table(
        schema=list(records[0].keys()) if records else [],
        rows=records,
        params=params,
    )
