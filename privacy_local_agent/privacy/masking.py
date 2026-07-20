"""数据脱敏与哈希工具模块。

根据字段名自动识别敏感字段类型（手机号、身份证、姓名、银行卡等），
提供对应的掩码、截断、HMAC 哈希等数据保护措施。

Data masking and hashing utilities. Recognizes common sensitive field types
by name and applies format-preserving masking or HMAC hashing.
"""

import base64
import hmac
import hashlib
import re
from typing import Any, Dict, List, Optional

from ..observability.metrics import MASKING_OPERATIONS_TOTAL


def guess_field_type(field_name: str) -> str:
    """根据字段名猜测敏感字段类型。

    通过关键字匹配判断字段属于手机号、身份证、姓名、银行卡还是默认类型。

    Args:
        field_name: 字段名（大小写不敏感）。

    Returns:
        字段类型标识，可选 "mobile"、"id_card"、"name"、"bank_card"、"default"。
    """
    lower = field_name.lower()
    if "mobile" in lower or "phone" in lower:
        return "mobile"
    if "id_card" in lower or "idcard" in lower or "身份证" in lower:
        return "id_card"
    if "name" in lower or "姓名" in lower:
        return "name"
    if "bank" in lower or "card_no" in lower:
        return "bank_card"
    return "default"


def mask_mobile(value: str) -> str:
    """中国大陆手机号脱敏。

    保留前 3 位与后 4 位，中间 4 位替换为 ****。非 11 位号码原样返回。

    Args:
        value: 原始手机号。

    Returns:
        脱敏后的手机号。
    """
    if len(value) != 11:
        return value
    return f"{value[:3]}****{value[7:]}"


def mask_id_card(value: str) -> str:
    """中国大陆 18 位身份证号脱敏。

    保留前 6 位与后 4 位，中间 8 位替换为 ********。非 18 位原样返回。

    Args:
        value: 原始身份证号。

    Returns:
        脱敏后的身份证号。
    """
    if len(value) != 18:
        return value
    return f"{value[:6]}********{value[14:]}"


def mask_name(value: str) -> str:
    """中文姓名脱敏。

    - 空字符串：原样返回
    - 2 字姓名：保留首字，后接 *
    - 其他：保留首尾字，中间替换为 **（长度不足时灵活处理）

    Args:
        value: 原始姓名。

    Returns:
        脱敏后的姓名。
    """
    if len(value) == 0:
        return value
    if len(value) == 2:
        return f"{value[0]}*"
    return f"{value[0]}**{value[-1]}"


def mask_bank_card(value: str) -> str:
    """银行卡号脱敏。

    保留前 4 位与后 4 位，中间替换为空格分隔的 ****。长度小于 8 位原样返回。

    Args:
        value: 原始银行卡号。

    Returns:
        脱敏后的银行卡号。
    """
    if len(value) < 8:
        return value
    return f"{value[:4]} **** **** {value[-4:]}"


def mask_default(value: str, prefix: int = 3, suffix: int = 3) -> str:
    """默认脱敏策略。

    保留前后指定位数，中间用 * 填充。若字符串长度不足 prefix + suffix，
    则原样返回。

    Args:
        value: 原始字符串。
        prefix: 保留前缀字符数，默认 3。
        suffix: 保留后缀字符数，默认 3。

    Returns:
        脱敏后的字符串。
    """
    if len(value) <= prefix + suffix:
        return value
    stars = "*" * (len(value) - prefix - suffix)
    return f"{value[:prefix]}{stars}{value[-suffix:]}"


def mask_value(field_name: str, value: str, context: str = "") -> str:
    """根据字段类型对单个值进行脱敏。

    先调用 guess_field_type 识别字段类型，再路由到具体脱敏函数。

    Args:
        field_name: 字段名，用于推断敏感类型。
        value: 原始值。
        context: 上下文信息，当前未使用，保留给未来扩展。

    Returns:
        脱敏后的字符串。
    """
    MASKING_OPERATIONS_TOTAL.labels(operation="mask_value").inc()
    ft = guess_field_type(field_name)
    if ft == "mobile":
        return mask_mobile(value)
    if ft == "id_card":
        return mask_id_card(value)
    if ft == "name":
        return mask_name(value)
    if ft == "bank_card":
        return mask_bank_card(value)
    return mask_default(value)


def mask_value_batch(
    field_names: List[str], values: List[str], context: str = ""
) -> List[str]:
    """批量对字段值进行脱敏。

    Args:
        field_names: 字段名列表，与 values 一一对应。
        values: 原始值列表。
        context: 上下文信息。

    Returns:
        脱敏后的值列表。
    """
    if len(field_names) != len(values):
        raise ValueError("field_names and values must have the same length")
    MASKING_OPERATIONS_TOTAL.labels(operation="mask_value_batch").inc()
    return [mask_value(fn, val, context) for fn, val in zip(field_names, values)]


def mask_dataframe(
    df: Any,
    columns: Optional[List[str]] = None,
    context: str = "",
) -> Any:
    """对 DataFrame 中的指定列进行脱敏。

    支持 pandas DataFrame 与 SecretFlow DataFrame（H/V）。

    Args:
        df: 输入 DataFrame。
        columns: 需要脱敏的列名列表；未指定时对所有字符串/object 列脱敏。
        context: 上下文信息。

    Returns:
        脱敏后的 DataFrame（pandas DataFrame）。
    """
    MASKING_OPERATIONS_TOTAL.labels(operation="mask_dataframe").inc()
    # 优先采用 Pandas 向量化机制进行高性能脱敏，避免 to_records 内存爆炸
    try:
        import pandas as pd
        if isinstance(df, pd.DataFrame):
            if columns is None:
                # 识别所有 object 或者是 string 类型的列
                columns = [
                    col for col in df.columns 
                    if df[col].dtype == object or pd.api.types.is_string_dtype(df[col])
                ]
            result_df = df.copy()
            for col in columns:
                if col in result_df.columns:
                    # 使用 pandas 向量化 apply，规避 Python 行级别 dict 循环
                    result_df[col] = result_df[col].apply(
                        lambda val: mask_value(col, str(val), context) if pd.notna(val) else val
                    )
            return result_df
    except ImportError:
        pass

    # 降级方案：对于不支持直接 pandas 操作的复杂类型/未安装 pandas 环境，采用 to_records/from_records 兜底
    from .data_adapters import to_records, from_records
    records = to_records(df)
    if not records:
        return from_records(records, df)

    if columns is None:
        # 默认对所有值为字符串的列脱敏
        columns = [k for k in records[0].keys() if isinstance(records[0].get(k), str)]

    masked_records = []
    for record in records:
        new_record = dict(record)
        for col in columns:
            val = new_record.get(col)
            if isinstance(val, str):
                new_record[col] = mask_value(col, val, context)
        masked_records.append(new_record)

    return from_records(masked_records, df)


def hash_value(value: str, salt: str) -> str:
    """对字符串进行 HMAC-SHA256 哈希，并取前 16 位 base64。

    使用盐值增强抗彩虹表能力，输出固定长度 16 字符的摘要字符串。

    Args:
        value: 待哈希的原始值。
        salt: 哈希盐值。

    Returns:
        16 字符长的 base64 编码摘要。
    """
    MASKING_OPERATIONS_TOTAL.labels(operation="hash").inc()
    mac = hmac.new(salt.encode(), value.encode(), hashlib.sha256).digest()
    return base64.b64encode(mac).decode()[:16]


def truncate(value: str, keep_prefix: int) -> str:
    """截断字符串，仅保留前 keep_prefix 位并追加 ***。

    Args:
        value: 原始字符串。
        keep_prefix: 保留的前缀长度。

    Returns:
        截断后的字符串；若原始长度不超过保留长度则原样返回。
    """
    MASKING_OPERATIONS_TOTAL.labels(operation="truncate").inc()
    if len(value) <= keep_prefix:
        return value
    return value[:keep_prefix] + "***"


def mask_record(record: Dict[str, str], context: str = "") -> Dict[str, str]:
    """对整条记录中的每个字符串值进行脱敏。

    遍历记录字典，对每个字符串类型的值调用 mask_value；非字符串值保持不变。

    Args:
        record: 原始记录字典。
        context: 上下文信息，透传给 mask_value，当前未使用。

    Returns:
        脱敏后的新记录字典。
    """
    MASKING_OPERATIONS_TOTAL.labels(operation="mask_record").inc()
    return {k: mask_value(k, v, context) if isinstance(v, str) else v for k, v in record.items()}
