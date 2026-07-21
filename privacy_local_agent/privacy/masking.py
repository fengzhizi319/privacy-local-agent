"""数据脱敏与哈希工具模块 / Data Masking & Hashing Primitive API Implementation.

中文说明：
根据字段名自动识别敏感字段类型（手机号、身份证、姓名、银行卡等），
提供对应的掩码、截断、HMAC 哈希等数据保护措施。
支持单值、批量、DataFrame、流式分块等多种处理模式，
内置输入校验、结构化日志与 Prometheus 指标埋点。

English Description:
Field-name-aware data masking and hashing utilities. Recognizes common sensitive
field types (mobile, ID card, name, bank card, email, address) by name and applies
format-preserving masking, truncation, or HMAC hashing.
Supports scalar, batch, DataFrame, and streaming chunked processing modes with
built-in input validation, structured logging, and Prometheus metrics instrumentation.

扩展能力 / Key Features:
- 向量化批处理：pandas DataFrame 列级 apply 加速，减少 Python 循环开销。
- 结构化日志：每次操作记录操作类型、字段数、记录数等上下文信息。
- 输入校验：统一的参数合法性检查，快速失败并给出清晰错误信息。
- 枚举类型安全：FieldType / MaskingOperation 枚举避免裸字符串拼写错误。
- 流式分块：生成器接口支持超大规模记录集的惰性脱敏。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Union

from ..observability.logging_config import get_logger
from ..observability.metrics import MASKING_OPERATIONS_TOTAL

# Module-level structured logger for masking operations
logger = get_logger(__name__)


class FieldType(str, Enum):
    """敏感字段类型枚举 / Sensitive Field Type Enum.

    继承 str 保证与字符串的向后兼容性：FieldType.MOBILE == "mobile" 为 True。
    IDE 自动补全 + 静态类型检查，避免裸字符串拼写错误。
    """

    MOBILE = "mobile"
    ID_CARD = "id_card"
    NAME = "name"
    BANK_CARD = "bank_card"
    EMAIL = "email"
    ADDRESS = "address"
    DEFAULT = "default"


class MaskingOperation(str, Enum):
    """脱敏操作类型枚举 / Masking Operation Type Enum.

    继承 str 保证与字符串的向后兼容性。
    用于 MaskingResult.operation 字段和 Prometheus 指标标签。
    """

    MASK_VALUE = "mask_value"
    MASK_VALUE_BATCH = "mask_value_batch"
    MASK_DATAFRAME = "mask_dataframe"
    MASK_RECORD = "mask_record"
    HASH_VALUE = "hash_value"
    TRUNCATE = "truncate"
    CHUNKED_MASK_RECORDS = "chunked_mask_records"


def _validate_field_name(field_name: str) -> None:
    """校验字段名参数有效性 / Validate field_name parameter.

    Args:
        field_name: 待校验的字段名。

    Raises:
        ValueError: 当字段名为空或不是字符串时抛出。
    """
    if not isinstance(field_name, str):
        raise ValueError(f"field_name must be a string, got {type(field_name).__name__}")
    if not field_name.strip():
        raise ValueError("field_name must not be empty or whitespace-only")


def _validate_value(value: str) -> None:
    """校验待脱敏值参数有效性 / Validate value parameter.

    Args:
        value: 待校验的值。

    Raises:
        ValueError: 当值不是字符串时抛出。
    """
    if not isinstance(value, str):
        raise ValueError(f"value must be a string, got {type(value).__name__}")


def _validate_salt(salt: str) -> None:
    """校验 HMAC 盐值参数有效性 / Validate HMAC salt parameter.

    Args:
        salt: 待校验的盐值。

    Raises:
        ValueError: 当盐值为空或不是字符串时抛出。
    """
    if not isinstance(salt, str):
        raise ValueError(f"salt must be a string, got {type(salt).__name__}")
    if not salt:
        raise ValueError("salt must not be empty for HMAC hashing")


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
    """根据字段名猜测敏感字段类型 / Infer Sensitive Field Type from Field Name.

    执行步骤 / Execution Steps:
    1. 将输入字段名转为小写，进行大小写不敏感匹配。
       (Lowercase the field name for case-insensitive matching)
    2. 使用关键字规则链判断字段属于 mobile, id_card, name, bank_card, email, address 还是 default。
       (Apply keyword rule chain to classify field type)
    3. 返回匹配的 FieldType 枚举值字符串。
       (Return matched FieldType enum value string)

    Args:
        field_name: 字段名（大小写不敏感）/ Field name (case-insensitive).

    Returns:
        字段类型标识 / Field type identifier，可选值参见 FieldType 枚举。
    """
    lower = field_name.lower()
    if "mobile" in lower or "phone" in lower or "tel" in lower:
        return FieldType.MOBILE.value
    if "id_card" in lower or "idcard" in lower or "身份证" in lower or "identity" in lower:
        return FieldType.ID_CARD.value
    if "email" in lower or "mail" in lower or "邮箱" in lower:
        return FieldType.EMAIL.value
    if "addr" in lower or "address" in lower or "地址" in lower:
        return FieldType.ADDRESS.value
    if "name" in lower or "姓名" in lower:
        return FieldType.NAME.value
    if "bank" in lower or "card_no" in lower:
        return FieldType.BANK_CARD.value
    return FieldType.DEFAULT.value


def mask_mobile(value: str) -> str:
    """中国大陆手机号脱敏 / China Mainland Mobile Number Masking.

    保留前 3 位与后 4 位，中间 4 位替换为 ****。非 11 位号码原样返回。
    """
    if len(value) != 11:
        return value
    return f"{value[:3]}****{value[7:]}"


def mask_id_card(value: str) -> str:
    """中国大陆 18 位身份证号脱敏 / China 18-digit ID Card Masking.

    保留前 6 位与后 4 位，中间 8 位替换为 ********。非 18 位原样返回。
    """
    if len(value) != 18:
        return value
    return f"{value[:6]}********{value[14:]}"


def mask_name(value: str) -> str:
    """中文姓名脱敏 / Chinese Name Masking.

    保留首尾字，中间替换为 *。
    """
    if len(value) == 0:
        return value
    if len(value) == 2:
        return f"{value[0]}*"
    return f"{value[0]}**{value[-1]}"


def mask_bank_card(value: str) -> str:
    """银行卡号脱敏 / Bank Card Number Masking.

    保留前 4 位与后 4 位，中间替换为空格分隔的 ****。长度小于 8 位原样返回。
    """
    if len(value) < 8:
        return value
    return f"{value[:4]} **** **** {value[-4:]}"


def mask_email(value: str) -> str:
    """电子邮箱地址脱敏 / Email Address Masking.

    执行步骤 / Execution Steps:
    1. 以 @ 分割用户名与域名。
       (Split username and domain by @)
    2. 用户名保留首尾字符，中间替换为 ***。
       (Keep first and last char of username, mask middle with ***)
    3. 域名完整保留以维持可路由性。
       (Preserve domain for routability)

    无 @ 符号的字符串使用默认脱敏策略。
    """
    if "@" not in value:
        return mask_default(value)
    local, domain = value.rsplit("@", 1)
    if len(local) <= 2:
        masked_local = local[0] + "***" if local else "***"
    else:
        masked_local = f"{local[0]}***{local[-1]}"
    return f"{masked_local}@{domain}"


def mask_address(value: str) -> str:
    """地址信息脱敏 / Address Masking.

    执行步骤 / Execution Steps:
    1. 保留前 6 个字符（通常包含省/市/区信息）。
       (Keep first 6 characters, typically province/city/district)
    2. 剩余部分替换为 ****。
       (Replace remainder with ****)

    长度不超过 6 的字符串原样返回。
    """
    if len(value) <= 6:
        return value
    return f"{value[:6]}****"


def mask_default(value: str, prefix: int = 3, suffix: int = 3) -> str:
    """默认脱敏策略 / Default Masking Strategy.

    保留前后指定位数，中间用 * 填充。
    """
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
    """根据字段类型对单个值进行脱敏 / Mask a Single Value by Field Type.

    执行步骤 / Execution Steps:
    1. 校验 field_name 和 value 参数有效性。
       (Validate field_name and value parameters)
    2. 调用 `guess_field_type` 识别字段敏感类型。
       (Invoke guess_field_type to classify sensitive field type)
    3. 根据分类路由调用对应的脱敏函数。
       (Route to corresponding masking function by type)
    4. 累加脱敏指标 `MASKING_OPERATIONS_TOTAL` 并记录结构化日志。
       (Increment metrics counter and emit structured log)
    5. 若 `return_details=True` 则封装导出 `MaskingResult` 结构。
       (If return_details=True, wrap in MaskingResult)

    Args:
        field_name: 字段名 / Field name for type inference.
        value: 待脱敏值 / Value to mask.
        context: 脱敏上下文标识 / Masking context identifier.
        return_details: 是否返回 MaskingResult / Whether to return MaskingResult.

    Returns:
        脱敏后的字符串或 MaskingResult / Masked string or MaskingResult.
    """
    _validate_field_name(field_name)
    _validate_value(value)
    MASKING_OPERATIONS_TOTAL.labels(operation=MaskingOperation.MASK_VALUE.value).inc()
    ft = guess_field_type(field_name)
    if ft == FieldType.MOBILE:
        masked_val = mask_mobile(value)
    elif ft == FieldType.ID_CARD:
        masked_val = mask_id_card(value)
    elif ft == FieldType.NAME:
        masked_val = mask_name(value)
    elif ft == FieldType.BANK_CARD:
        masked_val = mask_bank_card(value)
    elif ft == FieldType.EMAIL:
        masked_val = mask_email(value)
    elif ft == FieldType.ADDRESS:
        masked_val = mask_address(value)
    else:
        masked_val = mask_default(value)

    logger.debug(
        "mask_value_completed",
        extra={"field_name": field_name, "field_type": ft, "context": context},
    )

    if return_details:
        return MaskingResult(
            value=masked_val,
            operation=f"{MaskingOperation.MASK_VALUE.value}:{ft}",
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
    """批量对字段值进行脱敏 / Batch Mask Field Values.

    执行步骤 / Execution Steps:
    1. 校验 `field_names` 与 `values` 长度等长且非空。
       (Validate field_names and values have equal non-zero length)
    2. 顺序遍历调用 `mask_value` 完成元素级脱敏。
       (Iterate and apply mask_value per element)
    3. 记录结构化日志并累加指标。
       (Emit structured log and increment metrics)
    4. 若 `return_details=True` 封装导出 `MaskingResult`。
       (If return_details=True, wrap in MaskingResult)

    Args:
        field_names: 字段名列表 / List of field names.
        values: 待脱敏值列表 / List of values to mask.
        context: 脱敏上下文标识 / Masking context identifier.
        return_details: 是否返回 MaskingResult / Whether to return MaskingResult.

    Returns:
        脱敏后的字符串列表或 MaskingResult / List of masked strings or MaskingResult.

    Raises:
        ValueError: 当 field_names 与 values 长度不一致时 / When lengths mismatch.
    """
    if len(field_names) != len(values):
        raise ValueError(
            f"field_names and values must have the same length, "
            f"got {len(field_names)} and {len(values)}"
        )
    if not field_names:
        raise ValueError("field_names and values must not be empty")
    MASKING_OPERATIONS_TOTAL.labels(operation=MaskingOperation.MASK_VALUE_BATCH.value).inc()
    masked_list = [mask_value(fn, val, context) for fn, val in zip(field_names, values)]

    logger.info(
        "mask_value_batch_completed",
        extra={"num_fields": len(field_names), "context": context},
    )

    if return_details:
        return MaskingResult(
            value=masked_list,
            operation=MaskingOperation.MASK_VALUE_BATCH.value,
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
    """对 DataFrame 中的指定列进行脱敏 / Mask Specified Columns in DataFrame.

    支持 pandas DataFrame 与 SecretFlow DataFrame。

    执行步骤 / Execution Steps:
    1. 识别或指定输入 DataFrame 中需要脱敏的目标敏感列。
       (Identify or specify target sensitive columns to mask)
    2. 使用 pandas 向量化 apply 逐列应用 `mask_value` 方法。
       (Apply mask_value per column using pandas vectorized apply)
    3. 若未能导入 pandas 则回退使用 `data_adapters.to_records` 进行记录级脱敏。
       (Fallback to record-level masking via data_adapters if pandas unavailable)
    4. 记录结构化日志并根据 `return_details` 返回结果。
       (Emit structured log and return result based on return_details)

    Args:
        df: 输入 DataFrame / Input DataFrame.
        columns: 可选，限定需要脱敏的列名列表 / Optional column name list.
        context: 脱敏上下文标识 / Masking context identifier.
        return_details: 是否返回 MaskingResult / Whether to return MaskingResult.

    Returns:
        脱敏后的 DataFrame 或 MaskingResult / Masked DataFrame or MaskingResult.
    """
    MASKING_OPERATIONS_TOTAL.labels(operation=MaskingOperation.MASK_DATAFRAME.value).inc()
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
            logger.info(
                "mask_dataframe_completed",
                extra={
                    "num_rows": len(result_df),
                    "num_cols": len(target_cols),
                    "columns": target_cols,
                    "context": context,
                },
            )
            if return_details:
                return MaskingResult(
                    value=result_df,
                    operation=MaskingOperation.MASK_DATAFRAME.value,
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
            return MaskingResult(value=res, operation=MaskingOperation.MASK_DATAFRAME.value, masked_fields=[], total_masked=0)
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

    logger.info(
        "mask_dataframe_completed",
        extra={"num_rows": len(records), "num_cols": len(target_cols), "context": context},
    )

    res_df = from_records(masked_records, df)
    if return_details:
        return MaskingResult(
            value=res_df,
            operation=MaskingOperation.MASK_DATAFRAME.value,
            masked_fields=target_cols,
            total_masked=len(records),
        )
    return res_df


def hash_value(value: str, salt: str, return_details: bool = False) -> Union[str, MaskingResult]:
    """对字符串进行 HMAC-SHA256 哈希 / HMAC-SHA256 Hash with Salt.

    执行步骤 / Execution Steps:
    1. 校验 salt 参数非空且为字符串。
       (Validate salt parameter is non-empty string)
    2. 使用传入盐值与原始字符串构建 HMAC-SHA256。
       (Construct HMAC-SHA256 with salt and value)
    3. 计算摘要并进行 base64 编码，截取前 16 字符导出。
       (Compute digest, base64 encode, truncate to 16 chars)
    4. 记录结构化日志并累加指标。
       (Emit structured log and increment metrics)

    Args:
        value: 待哈希的原始字符串 / Original string to hash.
        salt: HMAC 盐值 / HMAC salt (must not be empty).
        return_details: 是否返回 MaskingResult / Whether to return MaskingResult.

    Returns:
        16 位 base64 编码的哈希字符串或 MaskingResult / 16-char base64 hash or MaskingResult.

    Raises:
        ValueError: 当 salt 为空时 / When salt is empty.
    """
    _validate_salt(salt)
    MASKING_OPERATIONS_TOTAL.labels(operation=MaskingOperation.HASH_VALUE.value).inc()
    mac = hmac.new(salt.encode(), value.encode(), hashlib.sha256).digest()
    hashed = base64.b64encode(mac).decode()[:16]
    logger.debug("hash_value_completed", extra={"value_length": len(value)})
    if return_details:
        return MaskingResult(value=hashed, operation=MaskingOperation.HASH_VALUE.value, masked_fields=[], total_masked=1)
    return hashed


def truncate(value: str, keep_prefix: int, return_details: bool = False) -> Union[str, MaskingResult]:
    """截断字符串 / Truncate String with Masking Suffix.

    执行步骤 / Execution Steps:
    1. 校验 keep_prefix 必须为非负整数。
       (Validate keep_prefix is non-negative integer)
    2. 判定输入字符串长度，若不超过 keep_prefix 则原样返回。
       (If value length <= keep_prefix, return as-is)
    3. 截取前缀字符并追加 *** 导出。
       (Truncate and append *** suffix)

    Args:
        value: 待截断字符串 / String to truncate.
        keep_prefix: 保留的前缀位数 / Number of prefix characters to keep.
        return_details: 是否返回 MaskingResult / Whether to return MaskingResult.

    Returns:
        截断后的字符串或 MaskingResult / Truncated string or MaskingResult.

    Raises:
        ValueError: 当 keep_prefix 为负数时 / When keep_prefix is negative.
    """
    if keep_prefix < 0:
        raise ValueError(f"keep_prefix must be non-negative, got {keep_prefix}")
    MASKING_OPERATIONS_TOTAL.labels(operation=MaskingOperation.TRUNCATE.value).inc()
    if len(value) <= keep_prefix:
        res = value
    else:
        res = value[:keep_prefix] + "***"
    if return_details:
        return MaskingResult(value=res, operation=MaskingOperation.TRUNCATE.value, masked_fields=[], total_masked=1)
    return res


def mask_record(
    record: Dict[str, Any], context: str = "", return_details: bool = False
) -> Union[Dict[str, Any], MaskingResult]:
    """对整条记录字典中的每个字符串值进行脱敏 / Mask All String Fields in Record.

    执行步骤 / Execution Steps:
    1. 校验 record 参数为非空字典。
       (Validate record is a non-empty dict)
    2. 遍历记录字典各项 Key-Value。
       (Iterate over record key-value pairs)
    3. 对字符串类型数据按 Key 名推断敏感类型并调用 `mask_value` 替换。
       (Infer field type by key name and apply mask_value for string values)
    4. 记录结构化日志并返回脱敏后记录或 MaskingResult。
       (Emit structured log and return masked record or MaskingResult)

    Args:
        record: 待脱敏记录字典 / Record dict to mask.
        context: 脱敏上下文标识 / Masking context identifier.
        return_details: 是否返回 MaskingResult / Whether to return MaskingResult.

    Returns:
        脱敏后的记录字典或 MaskingResult / Masked record dict or MaskingResult.

    Raises:
        ValueError: 当 record 不是字典或为空时 / When record is not a dict or empty.
    """
    if not isinstance(record, dict):
        raise ValueError(f"record must be a dict, got {type(record).__name__}")
    if not record:
        raise ValueError("record must not be empty")
    MASKING_OPERATIONS_TOTAL.labels(operation=MaskingOperation.MASK_RECORD.value).inc()
    masked_rec = {
        k: mask_value(k, v, context) if isinstance(v, str) else v
        for k, v in record.items()
    }
    masked_fields = [k for k, v in record.items() if isinstance(v, str)]
    logger.info(
        "mask_record_completed",
        extra={"num_fields": len(masked_fields), "context": context},
    )
    if return_details:
        return MaskingResult(
            value=masked_rec,
            operation=MaskingOperation.MASK_RECORD.value,
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
    """分块流式对记录进行脱敏 / Streaming Chunked Record Masking (Generator Interface).

    允许调用方以多个 chunk（生成器/迭代器）分批传入记录，
    每个 chunk 惰性处理并 yield 结果，避免一次性加载全部数据到内存。
    适用于超大规模记录集的脱敏场景。

    执行步骤 / Execution Steps:
    1. 选代每个 chunk，对其中每条记录执行字段级脱敏。
       (Iterate each chunk, apply field-level masking per record)
    2. 累计每个 chunk 的脱敏字段数与记录数。
       (Accumulate masked field count and record count per chunk)
    3. 记录结构化日志并 yield 脱敏结果。
       (Emit structured log and yield masked result)

    Args:
        chunks: 记录块的可迭代对象 / Iterable of record chunks.
        columns: 可选，限定需要脱敏的列名列表 / Optional column filter.
        context: 脱敏上下文标识 / Masking context identifier.
        return_details: 是否对每个 chunk 返回 MaskingResult / Whether to yield MaskingResult per chunk.

    Yields:
        每个 chunk 的脱敏后记录列表，或当 return_details=True 时 yield MaskingResult。
    """
    chunk_idx = 0
    for chunk in chunks:
        MASKING_OPERATIONS_TOTAL.labels(operation=MaskingOperation.CHUNKED_MASK_RECORDS.value).inc()
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
        logger.debug(
            "chunked_mask_records_chunk_completed",
            extra={"chunk_idx": chunk_idx, "num_records": len(masked_chunk), "total_masked": total_masked},
        )
        chunk_idx += 1
        if return_details:
            yield MaskingResult(
                value=masked_chunk,
                operation=MaskingOperation.CHUNKED_MASK_RECORDS.value,
                masked_fields=list(set(all_masked_fields)),
                total_masked=total_masked,
            )
        else:
            yield masked_chunk

