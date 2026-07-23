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
from typing import Dict, List

import grpc

from . import privacy_pb2
from . import privacy_pb2_grpc
from .classification_grpc import ClassificationGrpcServicer
from .observability.logging_config import configure_logging, get_logger
from .observability.middleware import GrpcObservabilityInterceptor
from .observability.tracing import init_tracing
from .privacy.budget import PrivacyBudgetExhausted
from .security.auth import AuthInterceptor
from .security.config import get_security_settings
from .security.ratelimit import RateLimitInterceptor
from .security.tls import grpc_server_credentials
from .service import PrivacyService

# 与 REST 模块共享环境变量配置，确保两种协议使用同一 profile 与命名空间
PROFILE_PATH = os.environ.get("PRIVACY_PROFILE", "privacy-profile.yaml")
NAMESPACE = os.environ.get("PRIVACY_NAMESPACE", "default")

logger = get_logger(__name__)


def _grpc_error_mapper(fn):
    """将 gRPC 方法异常映射到语义化 gRPC 状态码，避免全部返回 UNKNOWN。"""
    def wrapper(self, request, context):
        try:
            return fn(self, request, context)
        except PrivacyBudgetExhausted as e:
            context.set_code(grpc.StatusCode.RESOURCE_EXHAUSTED)
            context.set_details(str(e))
        except ValueError as e:
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details(str(e))
        except Exception:
            logger.exception("grpc_request_error")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Internal server error")
    return wrapper


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
        self._service_cache = {NAMESPACE: self.service}
        ClassificationGrpcServicer.__init__(self)

    def Mask(self, request, context):
        """单字段脱敏 gRPC 方法。"""
        return privacy_pb2.MaskResponse(result=self.service.mask(request.field_name, request.value, request.context))

    def MaskRecord(self, request, context):
        """整记录脱敏 gRPC 方法。"""
        result = self.service.mask_record(dict(request.record), request.context)
        return privacy_pb2.MaskRecordResponse(result=result)

    def MaskBatch(self, request, context):
        """批量字段脱敏 gRPC 方法。"""
        results = self.service.mask_batch(
            list(request.field_names), list(request.values), request.context
        )
        return privacy_pb2.MaskBatchResponse(results=results)

    def MaskDataFrame(self, request, context):
        """DataFrame 脱敏 gRPC 方法。"""
        import pandas as pd

        df = pd.DataFrame([dict(r.fields) for r in request.data])
        columns = list(request.columns) if request.columns else None
        result_df = self.service.mask_dataframe(df, columns=columns, context=request.context)
        rows = [privacy_pb2.RecordEntry(fields=r) for r in result_df.to_dict(orient="records")]
        return privacy_pb2.MaskDataFrameResponse(data=rows)

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

    def DPHistogram(self, request, context):
        """差分隐私直方图 gRPC 方法。"""
        params = {
            "epsilon": request.epsilon,
            "mechanism": request.mechanism,
        }
        if request.delta != 0.0:
            params["delta"] = request.delta
        res_dict = self.service.dp_histogram(
            list(request.values), list(request.categories), params
        )
        result = {str(k): float(v) for k, v in res_dict.items()}
        return privacy_pb2.DPHistogramResponse(result=result)

    def _dp_params_from_noisy_request(self, request) -> Dict[str, object]:
        """从 noisify 请求构建参数字典。"""
        params: Dict[str, object] = {
            "epsilon": request.epsilon,
            "mechanism": request.mechanism,
        }
        if request.delta != 0.0:
            params["delta"] = request.delta
        # proto3 默认值问题：sensitivity=0 视为未提供，依赖 clip 边界推导
        if getattr(request, "sensitivity", 0.0) != 0.0:
            params["sensitivity"] = request.sensitivity
        if getattr(request, "clip_lower", 0.0) != 0.0 or getattr(request, "clip_upper", 0.0) != 0.0:
            params["clip_lower"] = request.clip_lower
            params["clip_upper"] = request.clip_upper
        if getattr(request, "min_count", 0.0) != 0.0:
            params["min_count"] = request.min_count
        return params

    def DPNoisyCount(self, request, context):
        """对已聚合计数加噪的 gRPC 方法。"""
        params = self._dp_params_from_noisy_request(request)
        result = self.service.dp_noisy_count(request.true_count, params)
        return privacy_pb2.DPResponse(result=result)

    def DPNoisySum(self, request, context):
        """对已聚合求和加噪的 gRPC 方法。"""
        params = self._dp_params_from_noisy_request(request)
        result = self.service.dp_noisy_sum(request.true_sum, params)
        return privacy_pb2.DPResponse(result=result)

    def DPNoisyMean(self, request, context):
        """对已聚合 sum/count 加噪得到均值的 gRPC 方法。"""
        params = self._dp_params_from_noisy_request(request)
        result = self.service.dp_noisy_mean(
            request.true_sum, request.true_count, params
        )
        return privacy_pb2.DPResponse(result=result)

    def DPNoisyHistogram(self, request, context):
        """对已聚合直方图加噪的 gRPC 方法。"""
        params = self._dp_params_from_noisy_request(request)
        res_dict = self.service.dp_noisy_histogram(dict(request.true_counts), params)
        result = {str(k): float(v) for k, v in res_dict.items()}
        return privacy_pb2.DPHistogramResponse(result=result)

    def _chunks_from_request(self, request) -> List[List[float]]:
        """从 chunked 请求中提取数据块列表。"""
        return [list(chunk.values) for chunk in request.chunks]

    def DPChunkedCount(self, request, context):
        """分块流式 DP 计数 gRPC 方法。"""
        params = self._dp_params_from_noisy_request(request)
        result = self.service.dp_chunked_count(self._chunks_from_request(request), params)
        return privacy_pb2.DPResponse(result=result)

    def DPChunkedSum(self, request, context):
        """分块流式 DP 求和 gRPC 方法。"""
        params = self._dp_params_from_noisy_request(request)
        result = self.service.dp_chunked_sum(self._chunks_from_request(request), params)
        return privacy_pb2.DPResponse(result=result)

    def DPChunkedMean(self, request, context):
        """分块流式 DP 均值 gRPC 方法。"""
        params = self._dp_params_from_noisy_request(request)
        result = self.service.dp_chunked_mean(
            self._chunks_from_request(request), params
        )
        return privacy_pb2.DPResponse(result=result)

    def DPChunkedHistogram(self, request, context):
        """分块流式 DP 直方图 gRPC 方法。"""
        params = self._dp_params_from_noisy_request(request)
        chunks = [list(chunk.values) for chunk in request.chunks]
        res_dict = self.service.dp_chunked_histogram(
            chunks, list(request.categories), params
        )
        result = {str(k): float(v) for k, v in res_dict.items()}
        return privacy_pb2.DPHistogramResponse(result=result)


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

    def KAnonymizeDataFrame(self, request, context):
        """DataFrame K-匿名泛化 gRPC 方法。"""
        import pandas as pd

        df = pd.DataFrame([dict(r.fields) for r in request.data])
        result_df = self.service.k_anonymize_dataframe(
            df, list(request.qi_cols), request.k, request.max_depth
        )
        rows = [privacy_pb2.RecordEntry(fields=r) for r in result_df.to_dict(orient="records")]
        return privacy_pb2.KAnonymizeDataFrameResponse(data=rows)

    def ObfuscateQuery(self, request, context):
        """查询混淆 gRPC 方法。"""
        result = self.service.obfuscate_query(
            request.query,
            request.num_dummies,
            request.domain,
            medical_pool=list(request.medical_pool) if request.medical_pool else None,
            generic_pool=list(request.generic_pool) if request.generic_pool else None,
            seed=request.seed if request.seed != 0 else None,
        )
        return privacy_pb2.ObfuscateQueryResponse(result=result)

    def ObfuscateQueryBatch(self, request, context):
        """批量查询混淆 gRPC 方法。"""
        results = self.service.obfuscate_query_batch(
            list(request.queries),
            request.num_dummies,
            request.domain,
            medical_pool=list(request.medical_pool) if request.medical_pool else None,
            generic_pool=list(request.generic_pool) if request.generic_pool else None,
            seed=request.seed if request.seed != 0 else None,
        )
        return privacy_pb2.ObfuscateQueryBatchResponse(
            results=[privacy_pb2.ObfuscateQueryResponse(result=r) for r in results]
        )

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

        rec_service = self._service_cache.get(request.namespace)
        if rec_service is None:
            rec_service = PrivacyService(profile_path=PROFILE_PATH, namespace=request.namespace)
            self._service_cache[request.namespace] = rec_service
        recommended = rec_service.recommend_and_save_params(values, rows, qi_cols)

        import json
        return privacy_pb2.RecommendResponse(
            status="success",
            namespace=request.namespace,
            recommended_params_json=json.dumps(recommended)
        )

    def PerturbBinaryBatch(self, request, context):
        """二值本地 DP 扰动 gRPC 方法。"""
        results = self.service.perturb_binary_batch(list(request.values), request.epsilon)
        return privacy_pb2.PerturbBinaryBatchResponse(results=results)

    def PerturbCategoricalBatch(self, request, context):
        """类别型本地 DP 扰动 gRPC 方法。"""
        results = self.service.perturb_categorical_batch(
            list(request.values), list(request.categories), request.epsilon
        )
        return privacy_pb2.PerturbCategoricalBatchResponse(results=results)

    def EstimateBinaryFrequency(self, request, context):
        """二值估计 gRPC 方法。"""
        estimated = self.service.estimate_binary_frequency(
            list(request.reported_values), request.epsilon
        )
        return privacy_pb2.EstimateBinaryFrequencyResponse(estimated_frequency=estimated)

    def EstimateCategoricalHistogram(self, request, context):
        """类别直方图估计 gRPC 方法。"""
        est_dict = self.service.estimate_categorical_histogram(
            list(request.reported_values), list(request.categories), request.epsilon
        )
        # 将 key 转换为 str，value 转换为 float，以契合 map<string, double>
        estimated_histogram = {str(k): float(v) for k, v in est_dict.items()}
        return privacy_pb2.EstimateCategoricalHistogramResponse(
            estimated_histogram=estimated_histogram
        )

    def DPAggregate(self, request, context):
        import json
        import pandas as pd
        rows = [dict(r.fields) for r in request.rows]
        df = pd.DataFrame(rows)
        specs = json.loads(request.specs_json)
        params = {"epsilon": request.epsilon, "delta": request.delta, "mechanism": request.mechanism, "return_details": request.return_details}
        res = self.service.dp_aggregate(df, specs, params)
        res_json = json.dumps(res, default=str)
        return privacy_pb2.DPAggregateResponse(results_json=res_json)

    def DPVectorSum(self, request, context):
        import numpy as np
        vec_list = [list(chunk.values) for chunk in request.vectors]
        vectors = np.array(vec_list)
        params = {"max_norm": request.max_norm, "epsilon": request.epsilon, "delta": request.delta, "mechanism": request.mechanism, "return_details": request.return_details}
        res = self.service.dp_vector_sum(vectors, params)

        if hasattr(res, "value"):
            noisy_vec = list(res.value)
            dp_proto = privacy_pb2.DPResultProto(
                value_vector=noisy_vec,
                noise_mechanism=res.noise_mechanism,
                noise_scale=float(np.mean(res.noise_scale)) if isinstance(res.noise_scale, (list, np.ndarray)) else float(res.noise_scale),
                epsilon_spent=res.epsilon_spent,
                delta_spent=res.delta_spent,
            )
            return privacy_pb2.DPVectorSumResponse(noisy_vector=noisy_vec, result_details=dp_proto)
        else:
            noisy_vec = list(res)
            return privacy_pb2.DPVectorSumResponse(noisy_vector=noisy_vec)

    def DPAdaptiveClip(self, request, context):
        params = {"epsilon": request.epsilon, "target_quantile": request.target_quantile, "num_iterations": request.num_iterations, "initial_clip": request.initial_clip}
        lower, upper = self.service.dp_adaptive_clip(list(request.values), params)
        return privacy_pb2.DPAdaptiveClipResponse(clip_lower=lower, clip_upper=upper)

    def DPGroupBy(self, request, context):
        import json
        import pandas as pd
        rows = [dict(r.fields) for r in request.rows]
        df = pd.DataFrame(rows)
        params = {"epsilon": request.epsilon, "delta": request.delta, "mechanism": request.mechanism, "clip_lower": request.clip_lower, "clip_upper": request.clip_upper}
        res = self.service.dp_groupby(df, request.group_col, request.target_col, request.agg, params)
        res_json = json.dumps(res, default=str)
        return privacy_pb2.DPGroupByResponse(result_json=res_json)


# 为所有公共 RPC 方法统一包装异常映射，避免直接返回 UNKNOWN 状态码
for _name in dir(PrivacyServicer):
    if _name.startswith("_"):
        continue
    _attr = getattr(PrivacyServicer, _name)
    if callable(_attr):
        setattr(PrivacyServicer, _name, _grpc_error_mapper(_attr))


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

    # 设置 gRPC 消息大小限制：默认仅 4 MiB，base64 编码的图片或大表分类
    # 场景极易超限导致服务端重置 HTTP/2 连接（表现为 connection reset by peer）。
    # 将收发上限均提升至 64 MiB，与 Go 客户端保持一致。
    _MAX_MSG_SIZE = 64 * 1024 * 1024  # 64 MiB
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=max_workers),
        interceptors=tuple(interceptors) if interceptors else None,
        options=[
            ("grpc.max_receive_message_length", _MAX_MSG_SIZE),
            ("grpc.max_send_message_length", _MAX_MSG_SIZE),
        ],
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
    # 独立 gRPC 入口：与 server.py 保持一致，从环境变量读取端口（默认 50051）
    serve(port=int(os.environ.get("PRIVACY_GRPC_PORT", "50051")))
