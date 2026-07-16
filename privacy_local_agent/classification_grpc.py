"""数据分类 gRPC 服务实现模块。

将 ClassifyField/ClassifyRecord/ClassifyTable 三个 gRPC 方法从 grpc_server.py
中拆分出来，与处理原语（masking/dp/k-anonymity/qol）在代码层面完全分离。

gRPC service implementation for data classification. Splits the
ClassifyField/ClassifyRecord/ClassifyTable RPCs out of grpc_server.py so that
classification is physically separated from the processing primitives.
"""

import json
import os

from . import privacy_pb2
from .classification_service import ClassificationService

# 与 REST 模块共享环境变量配置，确保两种协议使用同一 profile
PROFILE_PATH = os.environ.get("PRIVACY_PROFILE", "privacy-profile.yaml")


class ClassificationGrpcServicer:
    """数据分类 gRPC 服务辅助类。

    可被 PrivacyServicer 继承或实例化后委托，用于实现分类相关的 gRPC 方法。

    Attributes:
        classification_service: 数据分类业务服务实例。
    """

    def __init__(self, classification_service=None):
        """初始化分类 gRPC servicer。"""
        self.classification_service = classification_service or ClassificationService(profile_path=PROFILE_PATH)

    def ClassifyField(self, request, context):
        """单字段分类 gRPC 方法。"""
        params = json.loads(request.params_json) if request.params_json else {}
        result = self.classification_service.classify_field(
            request.field_name, request.value, params
        )
        return privacy_pb2.ClassifyFieldResponse(result_json=json.dumps(result, ensure_ascii=False))

    def ClassifyRecord(self, request, context):
        """单条记录分类 gRPC 方法。"""
        params = json.loads(request.params_json) if request.params_json else {}
        record = dict(request.record.fields)
        result = self.classification_service.classify_record(record, params)
        return privacy_pb2.ClassifyRecordResponse(result_json=json.dumps(result, ensure_ascii=False))

    def ClassifyTable(self, request, context):
        """整张表分类 gRPC 方法。"""
        params = json.loads(request.params_json) if request.params_json else {}
        schema = list(request.schema)
        rows = [dict(row.fields) for row in request.rows]
        result = self.classification_service.classify_table(schema, rows, params)
        return privacy_pb2.ClassifyTableResponse(result_json=json.dumps(result, ensure_ascii=False))
