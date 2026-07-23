"""数据文件隐私处理路由（process_file）。"""

import json
from typing import Any, Dict, List

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from ..classification_routes import classification_service
from ..deps import MAX_UPLOAD_BYTES, SECURITY_DEPS, handle_request_exception, service
from ..security.auth import require_permission

router = APIRouter()

# 文件处理支持的操作类型：DataFrame 脱敏 / K-匿名 / 整表分类。
_FILE_OPERATIONS = {"mask_dataframe", "k_anonymize", "classify_table"}


def _parse_upload_to_records(content: bytes, filename: str) -> List[Dict[str, Any]]:
    """把上传的 CSV/JSON 文件字节解析为 records 列表。

    Args:
        content: 文件原始字节。
        filename: 原始文件名（用于按扩展名判定格式）。

    Returns:
        记录列表，每条记录为“列名 -> 值”字典（缺失值统一为空字符串）。

    Raises:
        HTTPException(400): 文件格式不受支持或内容无法解析。
    """
    import io

    import pandas as pd

    name = (filename or "").lower()
    try:
        if name.endswith(".csv"):
            df = pd.read_csv(io.BytesIO(content), dtype=str)
        elif name.endswith(".json"):
            # JSON 文件需为记录数组（list of objects）
            data = json.loads(content.decode("utf-8"))
            if not isinstance(data, list):
                raise ValueError("JSON 文件需为记录数组（list of objects）")
            df = pd.DataFrame(data)
        else:
            raise HTTPException(status_code=400, detail="仅支持 .csv 与 .json 文件")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"文件解析失败: {exc}") from exc

    # 缺失值统一为空字符串，与下游字符串语义保持一致
    df = df.fillna("")
    return df.to_dict(orient="records")


@router.post(
    "/v1/privacy/process_file",
    dependencies=[*SECURITY_DEPS, require_permission("privacy:mask")],
)
async def process_file(
    file: UploadFile = File(...),
    operation: str = Form(...),
    params: str = Form("{}"),
):
    """数据文件隐私处理接口。

    接收上传的 CSV/JSON 数据文件，按 ``operation`` 对其内容执行 DataFrame 脱敏、
    K-匿名或整表分类，返回处理后的记录。

    表单字段：
        - ``file``：CSV/JSON 数据文件；
        - ``operation``：操作类型，``mask_dataframe`` / ``k_anonymize`` / ``classify_table``；
        - ``params``：操作参数 JSON 字符串，例如
          ``{"columns": ["email"], "context": ""}``（脱敏）、
          ``{"qi_cols": ["age", "zip"], "k": 2, "max_depth": 10}``（K-匿名）、
          ``{"params": {}}``（分类）。

    Returns:
        ``{"operation", "rows_in", "rows_out", "result"}``；分类时 ``result`` 为表分类结果字典。
    """
    if operation not in _FILE_OPERATIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的操作 '{operation}'，可选: {sorted(_FILE_OPERATIONS)}",
        )

    try:
        options = json.loads(params or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"params 需为合法 JSON: {exc}") from exc
    if not isinstance(options, dict):
        raise HTTPException(status_code=400, detail="params 需为 JSON 对象")

    content = await file.read()
    # 上传大小限制：超限返回 413，避免大文件耗尽内存（DoS 防护）。
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"文件过大（{len(content)} 字节），上限 {MAX_UPLOAD_BYTES} 字节",
        )
    records = _parse_upload_to_records(content, file.filename or "")
    rows_in = len(records)

    try:
        import pandas as pd

        if operation == "mask_dataframe":
            df = pd.DataFrame(records)
            result_df = service.mask_dataframe(
                df, columns=options.get("columns"), context=options.get("context", "")
            )
            result: Any = result_df.to_dict(orient="records")
        elif operation == "k_anonymize":
            qi_cols = options.get("qi_cols")
            if not qi_cols:
                raise ValueError("k_anonymize 操作需提供 qi_cols 参数")
            df = pd.DataFrame(records)
            result_df = service.k_anonymize_dataframe(
                df,
                qi_cols,
                k=int(options.get("k", 5)),
                max_depth=int(options.get("max_depth", 10)),
            )
            result = result_df.to_dict(orient="records")
        else:  # classify_table
            schema = options.get("schema")
            if not schema:
                schema = list(records[0].keys()) if records else []
            result = classification_service.classify_table(
                schema, records, options.get("params", {})
            )
    except HTTPException:
        raise
    except Exception as exc:
        handle_request_exception(exc)

    rows_out = len(result) if isinstance(result, list) else rows_in
    return {
        "operation": operation,
        "rows_in": rows_in,
        "rows_out": rows_out,
        "result": result,
    }
