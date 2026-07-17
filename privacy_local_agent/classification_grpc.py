"""数据分类 gRPC 服务实现模块。

将 ClassifyField/ClassifyRecord/ClassifyTable 等 gRPC 方法从 grpc_server.py
中拆分出来，与处理原语（masking/dp/k-anonymity/qol）在代码层面完全分离。
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

    def _params(self, params_json: str) -> dict:
        return json.loads(params_json) if params_json else {}

    def ClassifyField(self, request, context):
        """单字段分类 gRPC 方法。"""
        params = self._params(request.params_json)
        result = self.classification_service.classify_field(
            request.field_name, request.value, params
        )
        return privacy_pb2.ClassifyFieldResponse(result_json=json.dumps(result, ensure_ascii=False))

    def ClassifyRecord(self, request, context):
        """单条记录分类 gRPC 方法。"""
        params = self._params(request.params_json)
        record = dict(request.record.fields)
        result = self.classification_service.classify_record(record, params)
        return privacy_pb2.ClassifyRecordResponse(result_json=json.dumps(result, ensure_ascii=False))

    def ClassifyTable(self, request, context):
        """整张表分类 gRPC 方法。"""
        params = self._params(request.params_json)
        schema = list(request.schema)
        rows = [dict(row.fields) for row in request.rows]
        result = self.classification_service.classify_table(schema, rows, params)
        return privacy_pb2.ClassifyTableResponse(result_json=json.dumps(result, ensure_ascii=False))

    def ClassifyTableAsync(self, request, context):
        """提交异步表分类任务。"""
        params = self._params(request.params_json)
        schema = list(request.schema)
        rows = [dict(row.fields) for row in request.rows]
        job_id = self.classification_service.submit_classify_table_async(schema, rows, params)
        return privacy_pb2.ClassifyTableAsyncResponse(
            job_id=job_id,
            status="PENDING",
        )

    def GetClassificationJob(self, request, context):
        """查询异步分类任务结果。"""
        job = self.classification_service.get_job_result(request.job_id)
        result_json = ""
        if job.get("result") and job["result"].get("result"):
            result_json = json.dumps(job["result"]["result"], ensure_ascii=False)
        return privacy_pb2.GetClassificationJobResponse(
            job_id=job.get("jobId", request.job_id),
            status=job.get("status", "UNKNOWN"),
            result_json=result_json,
            error=job.get("error") or "",
            created_at=job.get("createdAt") or "",
            finished_at=job.get("finishedAt") or "",
        )

    def ClassifySecretFlow(self, request, context):
        """SecretFlow 数据结构分类 gRPC 方法。"""
        params = self._params(request.params_json)
        data = json.loads(request.data_json) if request.data_json else {}
        result = self.classification_service.classify_table(
            schema=list(data.get("schema", [])),
            rows=data.get("rows", []),
            params=params,
        )
        return privacy_pb2.ClassifySecretFlowResponse(
            result_json=json.dumps(result, ensure_ascii=False)
        )

    def ConfirmReview(self, request, context):
        """确认复核 gRPC 方法。"""
        result = self.classification_service.confirm_review(
            request.review_id,
            request.corrected_level,
            request.reviewer,
            request.comment,
        )
        return privacy_pb2.ConfirmReviewResponse(
            result_json=json.dumps(result, ensure_ascii=False)
        )

    def ExportReviews(self, request, context):
        """导出复核样本 gRPC 方法。"""
        data = self.classification_service.export_reviews(
            format=request.format,
            mask_input=request.mask_input,
        )
        return privacy_pb2.ExportReviewsResponse(data=data)
