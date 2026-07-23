"""数据分类 gRPC 接口测试。

直接调用 PrivacyServicer 方法验证 ClassifyField/ClassifyRecord/ClassifyTable
的请求参数解析与 JSON 响应序列化，避免网络启停带来的不稳定。

Tests for gRPC classification methods by invoking PrivacyServicer directly.
This avoids the flakiness of starting an actual gRPC server in unit tests.
"""

import json

from privacy_local_agent import privacy_pb2
from privacy_local_agent.grpc_server import PrivacyServicer


def test_grpc_classify_field():
    """验证 gRPC 单字段分类返回正确的 JSON 结果。"""
    servicer = PrivacyServicer()
    request = privacy_pb2.ClassifyFieldRequest(
        field_name="mobile",
        value="13800138000",
        params_json="{}",
    )
    response = servicer.ClassifyField(request, None)
    result = json.loads(response.result_json)
    assert result["finalLevel"] == "L3"
    assert any(t["category"] == "PII_MOBILE" for t in result["tags"])


def test_grpc_classify_record():
    """验证 gRPC 单条记录分类聚合为最高等级。"""
    servicer = PrivacyServicer()
    record = privacy_pb2.RecordEntry(
        fields={
            "id_card": "110101199001011237",
            "diagnosis": "B21.1",
        }
    )
    request = privacy_pb2.ClassifyRecordRequest(record=record, params_json="{}")
    response = servicer.ClassifyRecord(request, None)
    result = json.loads(response.result_json)
    assert result["finalLevel"] == "L4"


def test_grpc_classify_table():
    """验证 gRPC 整张表分类聚合为最高等级。"""
    servicer = PrivacyServicer()
    row = privacy_pb2.RecordEntry(
        fields={
            "id_card": "110101199001011237",
            "brca1_status": "positive",
        }
    )
    request = privacy_pb2.ClassifyTableRequest(
        schema=["id_card", "brca1_status"],
        rows=[row],
        params_json="{}",
    )
    response = servicer.ClassifyTable(request, None)
    result = json.loads(response.result_json)
    assert result["finalLevel"] == "L5"
    assert result["schema"] == ["id_card", "brca1_status"]
