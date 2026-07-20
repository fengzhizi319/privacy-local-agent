"""数据脱敏与哈希工具模块。

根据字段名自动识别敏感字段类型（手机号、身份证、姓名、银行卡等），
提供对应的掩码、截断、HMAC 哈希等数据保护措施。

Data masking and hashing utilities. Recognizes common sensitive field types
by name and applies format-preserving masking or HMAC hashing.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Union

from ..observability.metrics import MASKING_OPERATIONS_TOTAL


@dataclass
class MaskingResult:
    """数据脱敏计算结果及结构化元数据包装。

    Attributes:
        value: 脱敏后的数据结果（标量字符串、字符串列表、字典记录或 DataFrame）。
        operation: 执行的脱敏操作标识（"mask_value" / "mask_dataframe" / "hash" 等）。
        masked_fields: 被脱敏处理的字段名称列表。
        total_masked: 被脱敏记录或字符的计数量。
    """

    value: Any
    operation: str
    masked_fields: List[str] = field(default_factory=list)
    total_masked: int = 1

    def to_arrow(self):
        """将 MaskingResult 包装转换为附带 脱敏 Metadata 的 PyArrow Table。

        执行步骤：
        1. 提取 MaskingResult 的脱敏元数据（操作类型、涉及字段列、脱敏总数）构造 JSON 结构。
        2. 将元数据存入 Schema 字典 Key `b"masking_metadata"`。
        3. 根据 `value` 的类型（字符串/列表/字典/DataFrame）构造对应的 PyArrow Table。
        4. 替换 Table Schema Metadata 后导出。
        """
        import json
        import pyarrow as pa

        meta = {
            "operation": str(self.operation),
            "masked_fields": str(self.masked_fields),
            "total_masked": str(self.total_masked),
        }
        custom_metadata = {b"masking_metadata": json.dumps(meta).encode("utf-8")}

        if isinstance(self.value, str):
            arr = pa.array([self.value])
            table = pa.Table.from_arrays([arr], names=["masked_value"])
        elif isinstance(self.value, list):
            if self.value and isinstance(self.value[0], dict):
                table = pa.Table.from_pylist(self.value)
            else:
                arr = pa.array([str(x) for x in self.value])
                table = pa.Table.from_arrays([arr], names=["masked_value"])
        elif isinstance(self.value, dict):
            keys = pa.array(list(self.value.keys()))
            vals = pa.array([str(v) for v in self.value.values()])
            table = pa.Table.from_arrays([keys, vals], names=["field", "masked_value"])
        else:
            try:
                import pandas as pd
                if isinstance(self.value, pd.DataFrame):
                    table = pa.Table.from_pandas(self.value)
                else:
                    arr = pa.array([str(self.value)])
                    table = pa.Table.from_arrays([arr], names=["masked_value"])
            except ImportError:
                arr = pa.array([str(self.value)])
                table = pa.Table.from_arrays([arr], names=["masked_value"])

        existing_meta = table.schema.metadata or {}
        merged_meta = {**existing_meta, **custom_metadata}
        return table.replace_schema_metadata(merged_meta)


def guess_field_type(field_name: str) -> str:
    """根据字段名猜测敏感字段类型。

    执行步骤：
    1. 将输入字段名转为小写。
    2. 使用关键字匹配判断字段属于 mobile, id_card, name, bank_card 还是 default。
    3. 返回匹配的字段类型字符串。

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
    """
    if len(value) != 11:
        return value
    return f"{value[:3]}****{value[7:]}"


def mask_id_card(value: str) -> str:
    """中国大陆 18 位身份证号脱敏。

    保留前 6 位与后 4 位，中间 8 位替换为 ********。非 18 位原样返回。
    """
    if len(value) != 18:
        return value
    return f"{value[:6]}********{value[14:]}"


def mask_name(value: str) -> str:
    """中文姓名脱敏。

    保留首尾字，中间替换为 *。
    """
    if len(value) == 0:
        return value
    if len(value) == 2:
        return f"{value[0]}*"
    return f"{value[0]}**{value[-1]}"


def mask_bank_card(value: str) -> str:
    """银行卡号脱敏。

    保留前 4 位与后 4 位，中间替换为空格分隔的 ****。长度小于 8 位原样返回。
    """
    if len(value) < 8:
        return value
    return f"{value[:4]} **** **** {value[-4:]}"


def mask_default(value: str, prefix: int = 3, suffix: int = 3) -> str:
    """默认脱敏策略：保留前后指定位数，中间用 * 填充。"""
    if len(value) <= prefix + suffix:
        return value
    stars = "*" * (len(value) - prefix - suffix)
    return f"{value[:prefix]}{stars}{value[-suffix:]}"


def mask_value(
    field_name: str,
    value: str,
    context: str = "",
    return_details: bool = False,
) -> Union[str, MaskingResult]:
    """根据字段类型对单个值进行脱敏。

    执行步骤：
    1. 调用 `guess_field_type` 识别字段敏态类型。
    2. 根据分类路由调用 `mask_mobile` / `mask_id_card` / `mask_name` / `mask_bank_card` / `mask_default`。
    3. 累加脱敏指标 `MASKING_OPERATIONS_TOTAL`。
    4. 若 `return_details=True` 则封装导出 `MaskingResult` 结构。
    """
    MASKING_OPERATIONS_TOTAL.labels(operation="mask_value").inc()
    ft = guess_field_type(field_name)
    if ft == "mobile":
        masked_val = mask_mobile(value)
    elif ft == "id_card":
        masked_val = mask_id_card(value)
    elif ft == "name":
        masked_val = mask_name(value)
    elif ft == "bank_card":
        masked_val = mask_bank_card(value)
    else:
        masked_val = mask_default(value)

    if return_details:
        return MaskingResult(
            value=masked_val,
            operation=f"mask_value:{ft}",
            masked_fields=[field_name],
            total_masked=1,
        )
    return masked_val


def mask_value_batch(
    field_names: List[str],
    values: List[str],
    context: str = "",
    return_details: bool = False,
) -> Union[List[str], MaskingResult]:
    """批量对字段值进行脱敏。

    执行步骤：
    1. 校验 `field_names` 与 `values` 长度等长。
    2. 顺序遍历调用 `mask_value` 完成元素级脱敏。
    3. 若 `return_details=True` 封装导出 `MaskingResult`。
    """
    if len(field_names) != len(values):
        raise ValueError("field_names and values must have the same length")
    MASKING_OPERATIONS_TOTAL.labels(operation="mask_value_batch").inc()
    masked_list = [mask_value(fn, val, context) for fn, val in zip(field_names, values)]
    
    if return_details:
        return MaskingResult(
            value=masked_list,
            operation="mask_value_batch",
            masked_fields=list(set(field_names)),
            total_masked=len(masked_list),
        )
    return masked_list


def mask_dataframe(
    df: Any,
    columns: Optional[List[str]] = None,
    context: str = "",
    return_details: bool = False,
) -> Union[Any, MaskingResult]:
    """对 DataFrame 中的指定列进行脱敏（支持 pandas 与 SecretFlow DataFrame）。

    执行步骤：
    1. 识别或指定输入 DataFrame 中需要脱敏的目标敏感列。
    2. 使用 pandas 向量化 apply 逐列应用 `mask_value` 方法。
    3. 若未能导入 pandas 则回退使用 `data_adapters.to_records` 进行记录级脱敏。
    4. 根据 `return_details` 返回脱敏后的 DataFrame 或 `MaskingResult` 结构。
    """
    MASKING_OPERATIONS_TOTAL.labels(operation="mask_dataframe").inc()
    target_cols = columns or []
    try:
        import pandas as pd
        if isinstance(df, pd.DataFrame):
            if columns is None:
                target_cols = [
                    col for col in df.columns 
                    if df[col].dtype == object or pd.api.types.is_string_dtype(df[col])
                ]
            result_df = df.copy()
            for col in target_cols:
                if col in result_df.columns:
                    result_df[col] = result_df[col].apply(
                        lambda val: mask_value(col, str(val), context) if pd.notna(val) else val
                    )
            if return_details:
                return MaskingResult(
                    value=result_df,
                    operation="mask_dataframe",
                    masked_fields=target_cols,
                    total_masked=len(result_df),
                )
            return result_df
    except ImportError:
        pass

    from .data_adapters import from_records, to_records
    records = to_records(df)
    if not records:
        res = from_records(records, df)
        if return_details:
            return MaskingResult(value=res, operation="mask_dataframe", masked_fields=[], total_masked=0)
        return res

    if columns is None:
        target_cols = [k for k in records[0].keys() if isinstance(records[0].get(k), str)]

    masked_records = []
    for record in records:
        new_record = dict(record)
        for col in target_cols:
            val = new_record.get(col)
            if isinstance(val, str):
                new_record[col] = mask_value(col, val, context)
        masked_records.append(new_record)

    res_df = from_records(masked_records, df)
    if return_details:
        return MaskingResult(
            value=res_df,
            operation="mask_dataframe",
            masked_fields=target_cols,
            total_masked=len(records),
        )
    return res_df


def hash_value(value: str, salt: str, return_details: bool = False) -> Union[str, MaskingResult]:
    """对字符串进行 HMAC-SHA256 哈希，并取前 16 位 base64。

    执行步骤：
    1. 使用传入盐值与原始字符串构建 HMAC-SHA256。
    2. 计算摘要并进行 base64 编码，截取前 16 字符导出。
    """
    MASKING_OPERATIONS_TOTAL.labels(operation="hash").inc()
    mac = hmac.new(salt.encode(), value.encode(), hashlib.sha256).digest()
    hashed = base64.b64encode(mac).decode()[:16]
    if return_details:
        return MaskingResult(value=hashed, operation="hash_value", masked_fields=[], total_masked=1)
    return hashed


def truncate(value: str, keep_prefix: int, return_details: bool = False) -> Union[str, MaskingResult]:
    """截断字符串，仅保留前 keep_prefix 位并追加 ***。

    执行步骤：
    1. 判定输入字符串长度，若不超过 keep_prefix 则原样返回。
    2. 截取前缀字符并追加 *** 导出。
    """
    MASKING_OPERATIONS_TOTAL.labels(operation="truncate").inc()
    if len(value) <= keep_prefix:
        res = value
    else:
        res = value[:keep_prefix] + "***"
    if return_details:
        return MaskingResult(value=res, operation="truncate", masked_fields=[], total_masked=1)
    return res


def mask_record(
    record: Dict[str, Any], context: str = "", return_details: bool = False
) -> Union[Dict[str, Any], MaskingResult]:
    """对整条记录字典中的每个字符串值进行脱敏。

    执行步骤：
    1. 遍历记录字典各项 Key-Value。
    2. 对字符串类型数据按 Key 名字推断敏态类型并调用 `mask_value` 替换。
    3. 返回脱敏后记录或 MaskingResult。
    """
    MASKING_OPERATIONS_TOTAL.labels(operation="mask_record").inc()
    masked_rec = {
        k: mask_value(k, v, context) if isinstance(v, str) else v
        for k, v in record.items()
    }
    masked_fields = [k for k, v in record.items() if isinstance(v, str)]
    if return_details:
        return MaskingResult(
            value=masked_rec,
            operation="mask_record",
            masked_fields=masked_fields,
            total_masked=len(masked_fields),
        )
    return masked_rec


def chunked_mask_records(
    chunks: Iterable[Iterable[Dict[str, Any]]],
    columns: Optional[List[str]] = None,
    context: str = "",
    return_details: bool = False,
) -> Union[Iterator[List[Dict[str, Any]]], Iterator[MaskingResult]]:
    """分块流式对记录进行脱敏（生成器接口）。

    允许调用方以多个 chunk（生成器/迭代器）分批传入记录，
    每个 chunk 惰性处理并 yield 结果，避免一次性加载全部数据到内存。
    适用于超大规模记录集的脱敏场景。

    Args:
        chunks: 记录块的可迭代对象，每块为 `Iterable[Dict[str, Any]]`。
        columns: 可选，限定需要脱敏的列名列表；若为 None 则对所有字符串列脱敏。
        context: 脱敏上下文标识。
        return_details: 是否对每个 chunk 返回 MaskingResult 结构。

    Yields:
        每个 chunk 的脱敏后记录列表，或当 return_details=True 时 yield MaskingResult。
    """
    for chunk in chunks:
        MASKING_OPERATIONS_TOTAL.labels(operation="chunked_mask_records").inc()
        masked_chunk: List[Dict[str, Any]] = []
        all_masked_fields: List[str] = []
        total_masked = 0
        for record in chunk:
            target_cols = columns or [k for k, v in record.items() if isinstance(v, str)]
            masked_rec = dict(record)
            chunk_fields: List[str] = []
            for col in target_cols:
                val = masked_rec.get(col)
                if isinstance(val, str):
                    masked_rec[col] = mask_value(col, val, context)
                    chunk_fields.append(col)
            masked_chunk.append(masked_rec)
            all_masked_fields.extend(chunk_fields)
            total_masked += len(chunk_fields)
        if return_details:
            yield MaskingResult(
                value=masked_chunk,
                operation="chunked_mask_records",
                masked_fields=list(set(all_masked_fields)),
                total_masked=total_masked,
            )
        else:
            yield masked_chunk

