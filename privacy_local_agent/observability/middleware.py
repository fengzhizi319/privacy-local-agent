"""REST middleware and gRPC interceptor for observability.

提供统一的访问日志、metrics 与 request_id 透传。
Provides unified access logs, metrics, and request-id propagation for REST and gRPC.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Callable

import grpc
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from .context import RequestContext, get_request_context, set_request_context
from .logging_config import get_logger
from .metrics import REQUESTS_TOTAL, REQUEST_DURATION, TRAFFIC_BYTES_TOTAL

logger = get_logger(__name__)

_REQUEST_ID_HEADER = "x-request-id"


def _generate_request_id() -> str:
    """Generate a short unique request id."""
    return uuid.uuid4().hex[:16]


def _get_request_id_from_metadata(context: grpc.ServicerContext) -> str:
    """Extract x-request-id from gRPC invocation metadata."""
    metadata = dict(context.invocation_metadata() or [])
    value = metadata.get(_REQUEST_ID_HEADER, "")
    return value or _generate_request_id()


class ObservabilityMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware that sets request context, logs, and records metrics.

    The ``/metrics`` endpoint is excluded to avoid self-referential noise.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        path = request.url.path
        if path == "/metrics":
            return await call_next(request)

        request_id = request.headers.get(_REQUEST_ID_HEADER) or _generate_request_id()
        method = request.method

        # Set initial context (identity will be filled after auth dependency runs).
        set_request_context(
            RequestContext(
                request_id=request_id,
                method=method,
                path=path,
                identity_name="",
            )
        )

        start = time.perf_counter()
        status_code = 500
        request_size = 0
        try:
            # 读取请求体以统计流量；FastAPI Request 会缓存 body，后续仍可读取。
            try:
                request_size = len(await request.body())
            except Exception:
                request_size = 0

            response = await call_next(request)
            status_code = response.status_code

            # 从响应头读取内容长度；若不存在则尝试读取响应体。
            response_size = int(response.headers.get("content-length", 0))
            if response_size == 0:
                try:
                    response_size = len(getattr(response, "body", b""))
                except Exception:
                    response_size = 0

            # Try to enrich the log with the authenticated identity.
            identity: Any = getattr(request.state, "identity", None)
            identity_name = identity.name if identity else ""
            set_request_context(
                RequestContext(
                    request_id=request_id,
                    method=method,
                    path=path,
                    identity_name=identity_name,
                )
            )

            duration = time.perf_counter() - start
            REQUESTS_TOTAL.labels(method=method, path=path, status=str(status_code)).inc()
            REQUEST_DURATION.labels(method=method, path=path).observe(duration)
            TRAFFIC_BYTES_TOTAL.labels(method=method, path=path, direction="request").inc(request_size)
            TRAFFIC_BYTES_TOTAL.labels(method=method, path=path, direction="response").inc(response_size)

            logger.info(
                "%s %s %s %.3fms request=%dB response=%dB identity=%s",
                method,
                path,
                status_code,
                duration * 1000,
                request_size,
                response_size,
                identity_name or "anonymous",
            )

            response.headers[_REQUEST_ID_HEADER] = request_id
            return response
        except Exception as exc:  # noqa: BLE001
            duration = time.perf_counter() - start
            REQUESTS_TOTAL.labels(method=method, path=path, status="500").inc()
            REQUEST_DURATION.labels(method=method, path=path).observe(duration)
            TRAFFIC_BYTES_TOTAL.labels(method=method, path=path, direction="request").inc(request_size)
            logger.exception(
                "%s %s 500 %.3fms request=%dB error=%s",
                method,
                path,
                duration * 1000,
                request_size,
                exc,
            )
            raise


def _grpc_status(context: grpc.ServicerContext) -> str:
    """Return a status label for a gRPC call."""
    code = context.code()
    if code is None:
        return "OK"
    return code.name


def _message_size(message: Any) -> int:
    """尝试获取 protobuf 消息的字节大小；不支持时返回 0。"""
    if message is None:
        return 0
    if hasattr(message, "ByteSize"):
        try:
            return int(message.ByteSize())
        except Exception:
            return 0
    return 0


def _wrap_unary_unary(
    handler: Callable[[Any, grpc.ServicerContext], Any],
    method: str,
    request_deserializer: Callable[[bytes], Any] | None,
    response_serializer: Callable[[Any], bytes] | None,
) -> grpc.RpcMethodHandler:
    """Wrap a unary-unary handler with observability."""

    def _wrapper(request: Any, context: grpc.ServicerContext) -> Any:
        request_id = _get_request_id_from_metadata(context)
        set_request_context(
            RequestContext(request_id=request_id, method="gRPC", path=method)
        )
        start = time.perf_counter()
        request_size = _message_size(request)
        response = None
        try:
            response = handler(request, context)
            return response
        finally:
            response_size = _message_size(response)
            _observe_grpc(context, method, start, request_size, response_size)

    return grpc.unary_unary_rpc_method_handler(
        _wrapper,
        request_deserializer=request_deserializer,
        response_serializer=response_serializer,
    )


def _wrap_unary_stream(
    handler: Callable[[Any, grpc.ServicerContext], Any],
    method: str,
    request_deserializer: Callable[[bytes], Any] | None,
    response_serializer: Callable[[Any], bytes] | None,
) -> grpc.RpcMethodHandler:
    """Wrap a unary-stream handler with observability."""

    def _wrapper(request: Any, context: grpc.ServicerContext) -> Any:
        request_id = _get_request_id_from_metadata(context)
        set_request_context(
            RequestContext(request_id=request_id, method="gRPC", path=method)
        )
        start = time.perf_counter()
        request_size = _message_size(request)
        try:
            return handler(request, context)
        finally:
            _observe_grpc(context, method, start, request_size, 0)

    return grpc.unary_stream_rpc_method_handler(
        _wrapper,
        request_deserializer=request_deserializer,
        response_serializer=response_serializer,
    )


def _wrap_stream_unary(
    handler: Callable[[Any, grpc.ServicerContext], Any],
    method: str,
    request_deserializer: Callable[[bytes], Any] | None,
    response_serializer: Callable[[Any], bytes] | None,
) -> grpc.RpcMethodHandler:
    """Wrap a stream-unary handler with observability."""

    def _wrapper(request_iterator: Any, context: grpc.ServicerContext) -> Any:
        request_id = _get_request_id_from_metadata(context)
        set_request_context(
            RequestContext(request_id=request_id, method="gRPC", path=method)
        )
        start = time.perf_counter()
        response = None
        try:
            response = handler(request_iterator, context)
            return response
        finally:
            response_size = _message_size(response)
            _observe_grpc(context, method, start, 0, response_size)

    return grpc.stream_unary_rpc_method_handler(
        _wrapper,
        request_deserializer=request_deserializer,
        response_serializer=response_serializer,
    )


def _wrap_stream_stream(
    handler: Callable[[Any, grpc.ServicerContext], Any],
    method: str,
    request_deserializer: Callable[[bytes], Any] | None,
    response_serializer: Callable[[Any], bytes] | None,
) -> grpc.RpcMethodHandler:
    """Wrap a stream-stream handler with observability."""

    def _wrapper(request_iterator: Any, context: grpc.ServicerContext) -> Any:
        request_id = _get_request_id_from_metadata(context)
        set_request_context(
            RequestContext(request_id=request_id, method="gRPC", path=method)
        )
        start = time.perf_counter()
        try:
            return handler(request_iterator, context)
        finally:
            _observe_grpc(context, method, start, 0, 0)

    return grpc.stream_stream_rpc_method_handler(
        _wrapper,
        request_deserializer=request_deserializer,
        response_serializer=response_serializer,
    )


def _observe_grpc(
    context: grpc.ServicerContext,
    method: str,
    start: float,
    request_size: int = 0,
    response_size: int = 0,
) -> None:
    """Record gRPC access log and metrics."""
    duration = time.perf_counter() - start
    status = _grpc_status(context)
    REQUESTS_TOTAL.labels(method="gRPC", path=method, status=status).inc()
    REQUEST_DURATION.labels(method="gRPC", path=method).observe(duration)
    TRAFFIC_BYTES_TOTAL.labels(method="gRPC", path=method, direction="request").inc(request_size)
    TRAFFIC_BYTES_TOTAL.labels(method="gRPC", path=method, direction="response").inc(response_size)
    logger.info(
        "gRPC %s %s %.3fms request=%dB response=%dB",
        method,
        status,
        duration * 1000,
        request_size,
        response_size,
    )


class GrpcObservabilityInterceptor(grpc.ServerInterceptor):
    """gRPC server interceptor for request context, logs, and metrics."""

    def intercept_service(
        self,
        continuation: Callable[[grpc.HandlerCallDetails], grpc.RpcMethodHandler],
        handler_call_details: grpc.HandlerCallDetails,
    ) -> grpc.RpcMethodHandler:
        handler = continuation(handler_call_details)
        if handler is None:
            return handler  # type: ignore[return-value]

        method = handler_call_details.method
        kwargs = {
            "request_deserializer": handler.request_deserializer,
            "response_serializer": handler.response_serializer,
        }
        if not handler.request_streaming and not handler.response_streaming:
            return _wrap_unary_unary(handler.unary_unary, method, **kwargs)
        if not handler.request_streaming and handler.response_streaming:
            return _wrap_unary_stream(handler.unary_stream, method, **kwargs)
        if handler.request_streaming and not handler.response_streaming:
            return _wrap_stream_unary(handler.stream_unary, method, **kwargs)
        return _wrap_stream_stream(handler.stream_stream, method, **kwargs)


def record_auth_denial(reason: str) -> None:
    """Record an authentication/authorization/rate-limit denial.

    This helper is called from security/auth.py and security/ratelimit.py so that
    denial events are both logged and counted in Prometheus.
    """
    from .metrics import AUTH_DENIALS_TOTAL

    AUTH_DENIALS_TOTAL.labels(reason=reason).inc()
    logger.warning("Auth denial: reason=%s", reason)
