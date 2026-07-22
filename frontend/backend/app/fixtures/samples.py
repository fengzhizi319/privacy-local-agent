"""Sample payloads for every privacy-local-agent REST endpoint.

The samples are intentionally minimal and deterministic; they are meant to exercise
connectivity and demonstrate valid request shapes. Users can edit them in the UI
before sending.
"""

from __future__ import annotations

import base64
from typing import Any, Dict, List, Optional


def _arrow_ipc_payload() -> str:
    """Generate a tiny Arrow IPC stream and return it as a base64 string."""
    import pyarrow as pa
    import io

    table = pa.table({"value": [1.0, 2.0, 3.0, 4.0, 5.0]})
    sink = io.BytesIO()
    with pa.ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    return base64.b64encode(sink.getvalue()).decode("ascii")


class EndpointSample:
    """Metadata and sample payload for one privacy-local-agent endpoint."""

    def __init__(
        self,
        method: str,
        path: str,
        label: str,
        category: str,
        description: str,
        body: Optional[Dict[str, Any]] = None,
        content_type: Optional[str] = None,
        raw_payload_b64: Optional[str] = None,
    ):
        self.method = method
        self.path = path
        self.label = label
        self.category = category
        self.description = description
        self.body = body
        self.content_type = content_type
        self.raw_payload_b64 = raw_payload_b64

    def to_dict(self) -> Dict[str, Any]:
        return {
            "method": self.method,
            "path": self.path,
            "label": self.label,
            "category": self.category,
            "description": self.description,
            "body": self.body,
            "contentType": self.content_type,
            "rawPayloadB64": self.raw_payload_b64,
        }


# fmt: off
SAMPLES: List[EndpointSample] = [
    # Health
    EndpointSample("GET", "/health", "Health", "Health", "服务健康检查"),
    EndpointSample("GET", "/livez", "Livez", "Health", "存活探针"),
    EndpointSample("GET", "/readyz", "Readyz", "Health", "就绪探针"),
    EndpointSample("GET", "/readyz/llm", "LLM Ready", "Health", "LLM 分类器就绪探针"),

    # Masking
    EndpointSample(
        "POST", "/v1/privacy/mask", "Mask", "Masking",
        "单字段脱敏",
        body={"field_name": "email", "value": "alice@example.com", "context": ""},
    ),
    EndpointSample(
        "POST", "/v1/privacy/mask_record", "Mask Record", "Masking",
        "整条记录脱敏",
        body={
            "record": {
                "email": "alice@example.com",
                "phone": "13800138000",
                "name": "Alice",
                "id_card": "11010119900101XXXX",
            },
            "context": "",
        },
    ),
    EndpointSample(
        "POST", "/v1/privacy/mask/batch", "Mask Batch", "Masking",
        "批量字段脱敏",
        body={
            "field_names": ["email", "phone", "name"],
            "values": ["bob@example.com", "13900139000", "Bob"],
            "context": "",
        },
    ),
    EndpointSample(
        "POST", "/v1/privacy/mask/dataframe", "Mask DataFrame", "Masking",
        "DataFrame 脱敏",
        body={
            "data": [
                {"email": "alice@example.com", "phone": "13800138000"},
                {"email": "bob@example.com", "phone": "13900139000"},
            ],
            "columns": ["email", "phone"],
            "context": "",
        },
    ),
    EndpointSample(
        "POST", "/v1/privacy/hash", "Hash", "Hash",
        "HMAC 哈希",
        body={"value": "sensitive-value", "salt": "demo-salt"},
    ),

    # DP
    EndpointSample(
        "POST", "/v1/privacy/dp/count", "DP Count", "DP",
        "差分隐私计数",
        body={"values": [1.0, 2.0, 3.0, 4.0, 5.0], "params": {"epsilon": 0.1, "mechanism": "laplace"}},
    ),
    EndpointSample(
        "POST", "/v1/privacy/dp/sum", "DP Sum", "DP",
        "差分隐私求和",
        body={
            "values": [1000.0, 2000.0, 3000.0, 4000.0, 5000.0],
            "params": {"epsilon": 0.1, "mechanism": "laplace", "clip_lower": 0.0, "clip_upper": 10000.0},
        },
    ),
    EndpointSample(
        "POST", "/v1/privacy/dp/mean", "DP Mean", "DP",
        "差分隐私均值",
        body={
            "values": [20.0, 30.0, 40.0, 50.0, 60.0],
            "params": {"epsilon": 0.1, "mechanism": "laplace", "clip_lower": 0.0, "clip_upper": 100.0},
        },
    ),
    EndpointSample(
        "POST", "/v1/privacy/dp/histogram", "DP Histogram", "DP",
        "差分隐私直方图",
        body={
            "values": ["eng", "hr", "eng", "sales", "eng"],
            "categories": ["eng", "hr", "sales", "marketing"],
            "params": {"epsilon": 0.1, "mechanism": "laplace"},
        },
    ),
    EndpointSample(
        "POST", "/v1/privacy/dp/noisy_count", "Noisy Count", "DP",
        "对已聚合计数加噪",
        body={"true_count": 100.0, "params": {"epsilon": 0.1, "mechanism": "laplace"}},
    ),
    EndpointSample(
        "POST", "/v1/privacy/dp/noisy_sum", "Noisy Sum", "DP",
        "对已聚合求和加噪",
        body={
            "true_sum": 10000.0,
            "params": {"epsilon": 0.1, "mechanism": "laplace", "clip_lower": 0.0, "clip_upper": 10000.0},
        },
    ),
    EndpointSample(
        "POST", "/v1/privacy/dp/noisy_mean", "Noisy Mean", "DP",
        "对已聚合均值加噪",
        body={
            "true_sum": 10000.0,
            "true_count": 100.0,
            "params": {"epsilon": 0.1, "mechanism": "laplace", "clip_lower": 0.0, "clip_upper": 10000.0},
        },
    ),
    EndpointSample(
        "POST", "/v1/privacy/dp/noisy_histogram", "Noisy Histogram", "DP",
        "对已聚合直方图加噪",
        body={
            "true_counts": {"eng": 50.0, "hr": 20.0, "sales": 30.0},
            "params": {"epsilon": 0.1, "mechanism": "laplace"},
        },
    ),
    EndpointSample(
        "POST", "/v1/privacy/dp/aggregate", "DP Aggregate", "DP",
        "表格级原位 DP 聚合",
        body={
            "rows": [
                {"age": 20, "salary": 1000.0, "dept": "eng"},
                {"age": 30, "salary": 2000.0, "dept": "hr"},
                {"age": 40, "salary": 3000.0, "dept": "eng"},
                {"age": 50, "salary": 4000.0, "dept": "sales"},
            ],
            "specs": {
                "age": ["mean", {"clip_lower": 0, "clip_upper": 100}],
                "salary": ["sum", {"clip_lower": 0, "clip_upper": 10000}],
                "dept": ["histogram", {"categories": ["eng", "hr", "sales"]}],
            },
            "params": {"epsilon": 0.5, "mechanism": "laplace"},
        },
    ),
    EndpointSample(
        "POST", "/v1/privacy/dp/vector_sum", "DP Vector Sum", "DP",
        "高维向量 DP 求和",
        body={
            "vectors": [[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]],
            "params": {"epsilon": 0.1, "delta": 1e-5, "mechanism": "gaussian", "max_norm": 10.0},
        },
    ),
    EndpointSample(
        "POST", "/v1/privacy/dp/vector_mean", "DP Vector Mean", "DP",
        "高维向量 DP 均值",
        body={
            "vectors": [[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]],
            "params": {"epsilon": 0.1, "delta": 1e-5, "mechanism": "gaussian", "max_norm": 10.0},
        },
    ),
    EndpointSample(
        "POST", "/v1/privacy/dp/adaptive_clip", "Adaptive Clip", "DP",
        "自适应二分搜索估计截断上下界",
        body={
            "values": [1.0, 5.0, 10.0, 15.0, 20.0],
            "params": {"epsilon": 0.1, "target_quantile": 0.95, "num_iterations": 15, "initial_clip": 10.0},
        },
    ),
    EndpointSample(
        "POST", "/v1/privacy/dp/groupby", "DP GroupBy", "DP",
        "Tau-Thresholding 差分隐私 Group-By",
        body={
            "rows": [
                {"dept": "eng", "salary": 1000.0},
                {"dept": "hr", "salary": 2000.0},
                {"dept": "eng", "salary": 3000.0},
                {"dept": "sales", "salary": 4000.0},
            ],
            "group_col": "dept",
            "target_col": "salary",
            "agg": "sum",
            "params": {"epsilon": 0.1, "mechanism": "laplace", "clip_lower": 0.0, "clip_upper": 10000.0},
        },
    ),
    EndpointSample(
        "POST", "/v1/privacy/dp/chunked_count", "Chunked Count", "DP",
        "分块流式 DP 计数",
        body={
            "chunks": [[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]],
            "params": {"epsilon": 0.1, "mechanism": "laplace"},
        },
    ),
    EndpointSample(
        "POST", "/v1/privacy/dp/chunked_sum", "Chunked Sum", "DP",
        "分块流式 DP 求和",
        body={
            "chunks": [[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]],
            "params": {"epsilon": 0.1, "mechanism": "laplace", "clip_lower": 0.0, "clip_upper": 10.0},
        },
    ),
    EndpointSample(
        "POST", "/v1/privacy/dp/chunked_mean", "Chunked Mean", "DP",
        "分块流式 DP 均值",
        body={
            "chunks": [[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]],
            "params": {"epsilon": 0.1, "mechanism": "laplace", "clip_lower": 0.0, "clip_upper": 10.0},
        },
    ),
    EndpointSample(
        "POST", "/v1/privacy/dp/chunked_histogram", "Chunked Histogram", "DP",
        "分块流式 DP 直方图",
        body={
            "chunks": [["eng", "hr"], ["eng", "sales"], ["eng", "marketing"]],
            "categories": ["eng", "hr", "sales", "marketing"],
            "params": {"epsilon": 0.1, "mechanism": "laplace"},
        },
    ),
    EndpointSample(
        "POST", "/v1/privacy/dp/arrow_ipc", "Arrow IPC", "DP",
        "Arrow IPC 二进制 DP 聚合",
        body={"column": "value", "aggregation": "count", "epsilon": 0.1, "delta": 0.0, "mechanism": "laplace"},
        content_type="application/vnd.apache.arrow.stream",
        raw_payload_b64=_arrow_ipc_payload(),
    ),

    # LDP
    EndpointSample(
        "POST", "/v1/privacy/ldp/perturb/binary", "Perturb Binary", "LDP",
        "二值本地 DP 扰动",
        body={"values": [0, 1, 1, 0, 1], "epsilon": 1.0},
    ),
    EndpointSample(
        "POST", "/v1/privacy/ldp/perturb/categorical", "Perturb Categorical", "LDP",
        "类别型本地 DP 扰动",
        body={"values": ["eng", "hr", "eng", "sales"], "categories": ["eng", "hr", "sales"], "epsilon": 1.0},
    ),
    EndpointSample(
        "POST", "/v1/privacy/ldp/estimate/binary", "Estimate Binary", "LDP",
        "二值本地 DP 估计",
        body={"reported_values": [0, 1, 1, 0, 1], "epsilon": 1.0},
    ),
    EndpointSample(
        "POST", "/v1/privacy/ldp/estimate/categorical", "Estimate Categorical", "LDP",
        "类别型本地 DP 估计",
        body={
            "reported_values": ["eng", "hr", "eng", "sales"],
            "categories": ["eng", "hr", "sales"],
            "epsilon": 1.0,
        },
    ),

    # K-Anonymity
    EndpointSample(
        "POST", "/v1/privacy/k_anonymize/record", "K-Anonymize Record", "K-Anonymity",
        "单条记录 K-匿名泛化",
        body={
            "record": {"age": "30", "zip": "100000", "gender": "F"},
            "qi_cols": ["age", "zip", "gender"],
            "k": 2,
        },
    ),
    EndpointSample(
        "POST", "/v1/privacy/k_anonymize/table", "K-Anonymize Table", "K-Anonymity",
        "整张表 K-匿名泛化",
        body={
            "rows": [
                {"age": "30", "zip": "100000", "gender": "F"},
                {"age": "31", "zip": "100001", "gender": "F"},
                {"age": "32", "zip": "100002", "gender": "M"},
                {"age": "33", "zip": "100003", "gender": "M"},
            ],
            "qi_cols": ["age", "zip", "gender"],
            "k": 2,
            "max_depth": 10,
        },
    ),
    EndpointSample(
        "POST", "/v1/privacy/k_anonymize/dataframe", "K-Anonymize DataFrame", "K-Anonymity",
        "DataFrame K-匿名泛化",
        body={
            "data": [
                {"age": "30", "zip": "100000", "gender": "F"},
                {"age": "31", "zip": "100001", "gender": "F"},
                {"age": "32", "zip": "100002", "gender": "M"},
                {"age": "33", "zip": "100003", "gender": "M"},
            ],
            "qi_cols": ["age", "zip", "gender"],
            "k": 2,
            "max_depth": 10,
        },
    ),

    # Query Obfuscation
    EndpointSample(
        "POST", "/v1/privacy/qol/obfuscate", "Obfuscate Query", "Query Obfuscation",
        "查询混淆",
        body={"query": "糖尿病患者用药推荐", "num_dummies": 3, "domain": "medical"},
    ),
    EndpointSample(
        "POST", "/v1/privacy/qol/obfuscate/batch", "Obfuscate Batch", "Query Obfuscation",
        "批量查询混淆",
        body={
            "queries": ["糖尿病患者用药推荐", "高血压患者饮食建议"],
            "num_dummies": 3,
            "domain": "medical",
        },
    ),

    # Classification
    EndpointSample(
        "POST", "/v1/privacy/classify/field", "Classify Field", "Classification",
        "单字段分类",
        body={"field_name": "email", "value": "alice@example.com", "params": {}},
    ),
    EndpointSample(
        "POST", "/v1/privacy/classify/record", "Classify Record", "Classification",
        "单条记录分类",
        body={
            "record": {
                "email": "alice@example.com",
                "phone": "13800138000",
                "name": "Alice",
            },
            "params": {},
        },
    ),
    EndpointSample(
        "POST", "/v1/privacy/classify/table", "Classify Table", "Classification",
        "整张表分类",
        body={
            "schema": ["email", "phone", "salary"],
            "rows": [
                {"email": "alice@example.com", "phone": "13800138000", "salary": "1000"},
                {"email": "bob@example.com", "phone": "13900139000", "salary": "2000"},
            ],
            "params": {},
        },
    ),
    EndpointSample(
        "POST", "/v1/privacy/classify/table/async", "Classify Table Async", "Classification",
        "异步表分类",
        body={
            "schema": ["email", "phone"],
            "rows": [
                {"email": "alice@example.com", "phone": "13800138000"},
                {"email": "bob@example.com", "phone": "13900139000"},
            ],
            "params": {},
        },
    ),
    EndpointSample(
        "GET", "/v1/privacy/classify/jobs/demo-job-id", "Get Job", "Classification",
        "查询异步分类任务（示例 job_id 可能不存在）",
    ),
    EndpointSample(
        "POST", "/v1/privacy/classify/secretflow", "Classify SecretFlow", "Classification",
        "SecretFlow 数据结构分类",
        body={
            "party": "alice",
            "params_json": "{}",
            "data_json": '{"schema": ["email", "phone"], "rows": [{"email": "alice@example.com", "phone": "13800138000"}]}',
        },
    ),
    EndpointSample(
        "POST", "/v1/privacy/classify/review/confirm", "Confirm Review", "Classification",
        "确认复核结果",
        body={
            "review_id": "demo-review-id",
            "corrected_level": "2",
            "reviewer": "tester",
            "comment": "confirmed",
        },
    ),
    EndpointSample(
        "POST", "/v1/privacy/classify/review/export", "Export Reviews", "Classification",
        "导出复核样本",
        body={"format": "jsonl", "mask_input": False},
    ),

    # Budget & Profile
    EndpointSample("GET", "/v1/privacy/budget", "Budget", "Budget", "查询剩余隐私预算"),
    EndpointSample(
        "POST", "/v1/privacy/profile/recommend", "Recommend Params", "Profile",
        "自动推荐隐私参数",
        body={
            "namespace": "demo-recommend",
            "values": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0],
            "rows": [
                {"age": "30", "zip": "100000", "gender": "F"},
                {"age": "31", "zip": "100001", "gender": "M"},
            ],
            "qi_cols": ["age", "zip", "gender"],
        },
    ),
]
# fmt: on


def get_samples() -> List[Dict[str, Any]]:
    """Return all endpoint samples as plain dictionaries."""
    return [s.to_dict() for s in SAMPLES]


def find_sample(path: str) -> Optional[Dict[str, Any]]:
    """Find a sample by endpoint path."""
    for s in SAMPLES:
        if s.path == path:
            return s.to_dict()
    return None
