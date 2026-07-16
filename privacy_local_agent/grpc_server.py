"""gRPC 服务入口模块。

基于 grpcio 与自动生成的 protobuf stub 实现 PrivacyService 的 gRPC 接口，
暴露与 REST 模块相对应的处理原语能力：脱敏、哈希、差分隐私、K-匿名、查询混淆与健康检查。
数据分类 gRPC 方法已在 classification_grpc.py 中实现，通过多重继承组合到 PrivacyServicer。

gRPC service entrypoint. Implements the protobuf-defined PrivacyService interface
using generated stubs and the shared PrivacyService business layer for processing
primitives. Data classification RPCs are implemented in classification_grpc.py and
composed into PrivacyServicer via multiple inheritance.
"""

import os
from concurrent import futures
from typing import Dict

import grpc

from . import privacy_pb2
from . import privacy_pb2_grpc
from .classification_grpc import ClassificationGrpcServicer
from .observability.logging_config import configure_logging
from .observability.middleware import GrpcObservabilityInterceptor
from .observability.tracing import init_tracing
from .security.auth import AuthInterceptor
from .security.config import get_security_settings
from .security.ratelimit import RateLimitInterceptor
from .security.tls import grpc_server_credentials
from .service import PrivacyService

# 与 REST 模块共享环境变量配置，确保两种协议使用同一 profile 与命名空间
PROFILE_PATH = os.environ.get("PRIVACY_PROFILE", "privacy-profile.yaml")
NAMESPACE = os.environ.get("PRIVACY_NAMESPACE", "default")


class PrivacyServicer(
    ClassificationGrpcServicer, privacy_pb2_grpc.PrivacyServiceServicer
):
    """PrivacyService gRPC 服务实现。

    将 protobuf 请求转换为 PrivacyService 业务方法调用，
    并将结果封装为 protobuf 响应返回给客户端。
    分类相关方法通过继承 ClassificationGrpcServicer 提供。

    Attributes:
        service: 共享的 PrivacyService 业务实例。
    """

    def __init__(self):
        """初始化 gRPC servicer，创建 PrivacyService 实例并复用它。"""
        self.service = PrivacyService(profile_path=PROFILE_PATH, namespace=NAMESPACE)
        ClassificationGrpcServicer.__init__(self, classification_service=self.service)

    def Mask(self, request, context):
        """单字段脱敏 gRPC 方法。"""
        return privacy_pb2.MaskResponse(result=self.service.mask(request.field_name, request.value, request.context))

    def MaskRecord(self, request, context):
        """整记录脱敏 gRPC 方法。"""
        result = self.service.mask_record(dict(request.record), request.context)
        return privacy_pb2.MaskRecordResponse(result=result)

    def Hash(self, request, context):
        """HMAC 哈希 gRPC 方法。"""
        return privacy_pb2.HashResponse(result=self.service.hash(request.value, request.salt))

    def _dp_params_from_request(self, request) -> Dict[str, object]:
        """从 DPRequest 构建参数字典。"""
        params: Dict[str, object] = {
            "epsilon": request.epsilon,
            "mechanism": request.mechanism,
        }
        # proto3 默认值：delta=0.0, clip_lower/upper=0.0。仅当非零或显式设置时透传。
        if request.delta != 0.0:
            params["delta"] = request.delta
        if request.clip_lower != 0.0 or request.clip_upper != 0.0:
            params["clip_lower"] = request.clip_lower
            params["clip_upper"] = request.clip_upper
        return params

    def DPCount(self, request, context):
        """差分隐私计数 gRPC 方法。"""
        result = self.service.dp_count(list(request.values), self._dp_params_from_request(request))
        return privacy_pb2.DPResponse(result=result)

    def DPSum(self, request, context):
        """差分隐私求和 gRPC 方法。"""
        result = self.service.dp_sum(list(request.values), self._dp_params_from_request(request))
        return privacy_pb2.DPResponse(result=result)

    def DPMean(self, request, context):
        """差分隐私均值 gRPC 方法。"""
        result = self.service.dp_mean(list(request.values), self._dp_params_from_request(request))
        return privacy_pb2.DPResponse(result=result)

    def KAnonymizeRecord(self, request, context):
        """单条记录 K-匿名泛化 gRPC 方法。"""
        result = self.service.k_anonymize_record(dict(request.record), list(request.qi_cols), request.k)
        return privacy_pb2.KAnonymizeResponse(result=result)

    def KAnonymizeTable(self, request, context):
        """整张表 K-匿名泛化 gRPC 方法。"""
        rows = [dict(r.fields) for r in request.rows]
        result = self.service.k_anonymize_table(rows, list(request.qi_cols), request.k, request.max_depth)
        return privacy_pb2.KAnonymizeTableResponse(
            rows=[privacy_pb2.RecordEntry(fields=r) for r in result]
        )

    def ObfuscateQuery(self, request, context):
        """查询混淆 gRPC 方法。"""
        result = self.service.obfuscate_query(request.query, request.num_dummies, request.domain)
        return privacy_pb2.ObfuscateQueryResponse(result=result)

    def Health(self, request, context):
        """健康检查 gRPC 方法。"""
        return privacy_pb2.HealthResponse(status="ok", namespace=NAMESPACE)

    def RecommendParams(self, request, context):
        """隐私参数推荐 gRPC 方法。"""
        rows = None
        if request.rows:
            rows = [dict(r.fields) for r in request.rows]
        values = list(request.values) if request.values else None
        qi_cols = list(request.qi_cols) if request.qi_cols else None

        rec_service = PrivacyService(profile_path=PROFILE_PATH, namespace=request.namespace)
        recommended = rec_service.recommend_and_save_params(values, rows, qi_cols)

        import json
        return privacy_pb2.RecommendResponse(
            status="success",
            namespace=request.namespace,
            recommended_params_json=json.dumps(recommended)
        )


def serve(port: int = 50051, max_workers: int = 10, wait_for_termination: bool = True):
    """启动 gRPC 服务器。

    使用 ThreadPoolExecutor 作为工作线程池，注册 PrivacyServicer，
    监听指定端口，并阻塞或非阻塞等待连接。

    根据环境变量可启用 TLS/mTLS、认证鉴权、速率限制与可观测性拦截器。

    Args:
        port: gRPC 服务监听端口，默认 50051。
        max_workers: 线程池最大工作线程数，默认 10。
        wait_for_termination: 是否阻塞等待服务器终止，默认 True。
    """
    # gRPC-only entrypoint: ensure logging/tracing are initialized.
    configure_logging(
        log_level=os.environ.get("PRIVACY_LOG_LEVEL", "INFO"),
        json_format=os.environ.get("PRIVACY_LOG_FORMAT", "text").lower() == "json",
        service_name=os.environ.get("PRIVACY_SERVICE_NAME", "privacy-local-agent"),
    )
    init_tracing(
        endpoint=os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"),
        service_name=os.environ.get(
            "OTEL_SERVICE_NAME",
            os.environ.get("PRIVACY_SERVICE_NAME", "privacy-local-agent"),
        ),
    )

    settings = get_security_settings()
    interceptors: list[grpc.ServerInterceptor] = [
        GrpcObservabilityInterceptor(),
    ]
    if settings.auth_enabled:
        interceptors.append(AuthInterceptor(settings))
    if settings.rate_limit_enabled:
        interceptors.append(RateLimitInterceptor(settings))

    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=max_workers),
        interceptors=tuple(interceptors) if interceptors else None,
    )
    privacy_pb2_grpc.add_PrivacyServiceServicer_to_server(PrivacyServicer(), server)

    if settings.tls_enabled:
        creds = grpc_server_credentials(settings)
        server.add_secure_port(f"[::]:{port}", creds)
        print(f"gRPC server started on port {port} (TLS/mTLS)")
    else:
        # 本地开发模式，使用非安全端口；生产环境建议启用 TLS/mTLS
        server.add_insecure_port(f"[::]:{port}")
        print(f"gRPC server started on port {port}")

    server.start()
    if wait_for_termination:
        server.wait_for_termination()
    return server


if __name__ == "__main__":
    serve()
