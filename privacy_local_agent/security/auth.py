"""Authentication and authorization for REST and gRPC.

提供 FastAPI dependency 与 gRPC server interceptor，支持：
- 静态 API Key（内部 / 外部服务）
- gRPC mTLS 客户端证书身份提取
- 接口级权限校验

Provides FastAPI dependencies and a gRPC server interceptor supporting static API
keys (internal/external), gRPC mTLS client-certificate identity extraction, and
per-method permission checks.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import grpc
from fastapi import Depends, HTTPException, Request

from ..observability.middleware import record_auth_denial
from .config import SecuritySettings, get_security_settings
from .identity import (
    ANONYMOUS_IDENTITY,
    Identity,
    is_health_path_or_method,
    permission_for_grpc_method,
    permission_for_rest_path,
)

if TYPE_CHECKING:
    from collections.abc import Callable


def _extract_bearer_token(header_value: str | None) -> str | None:
    """Extract the bearer token from an Authorization header value."""
    if not header_value:
        return None
    parts = header_value.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return None


def _authenticate_api_key(settings: SecuritySettings, token: str) -> Identity | None:
    """Look up an API key in internal and external key stores.

    Internal keys are checked first so an internal token can never be shadowed by an
    external one.
    """
    internal = settings.internal_keys.get(token)
    if internal:
        return Identity("internal", internal.name, internal.scopes)
    external = settings.external_keys.get(token)
    if external:
        return Identity("external", external.name, external.scopes)
    return None


def _authenticate_mtls(
    settings: SecuritySettings, auth_context: dict[str, Any]
) -> Identity | None:
    """Derive an internal Identity from a verified mTLS client certificate.

    The gRPC auth_context is populated only when the connection uses TLS and the
    client presented a certificate. We treat any verified mTLS peer as an internal
    service when ``auth_internal_mtls_enabled`` is true.
    """
    if not settings.auth_internal_mtls_enabled:
        return None
    transport = auth_context.get("transport_security_type", [b""])[0]
    if transport != b"ssl":
        return None
    cn_bytes = auth_context.get("x509_common_name", [b""])[0]
    if not cn_bytes:
        return None
    cn = cn_bytes.decode("utf-8", errors="replace")
    return Identity("internal", cn, ["*"])


def _extract_identity_from_grpc_context(
    settings: SecuritySettings,
    context: grpc.ServicerContext,
    method: str,
) -> Identity | None:
    """Extract identity from gRPC metadata and/or mTLS auth context."""
    # First try mTLS because a verified certificate is stronger than a bearer token.
    auth_context = context.auth_context()
    if auth_context:
        identity = _authenticate_mtls(settings, auth_context)
        if identity:
            return identity

    # Health endpoints may be exempt from authentication.
    if is_health_path_or_method(method) and settings.health_no_auth:
        return Identity("internal", "health-probe", ["*"])

    metadata = dict(context.invocation_metadata() or [])
    auth_header = metadata.get("authorization", "")
    token = _extract_bearer_token(auth_header)
    if token:
        return _authenticate_api_key(settings, token)
    return None


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------

async def get_current_identity(request: Request) -> Identity:
    """FastAPI dependency that resolves the caller identity.

    When auth is disabled this returns an anonymous admin identity so downstream
    code can treat every request uniformly. Health endpoints are exempt when
    configured.
    """
    settings = get_security_settings()
    if not settings.auth_enabled:
        return ANONYMOUS_IDENTITY

    path = request.url.path
    if is_health_path_or_method(path) and settings.health_no_auth:
        return Identity("internal", "health-probe", ["*"])

    token = _extract_bearer_token(request.headers.get("authorization"))
    if not token:
        record_auth_denial("unauthenticated")
        raise HTTPException(status_code=401, detail="Unauthorized: missing credentials")

    identity = _authenticate_api_key(settings, token)
    if identity is None:
        record_auth_denial("unauthenticated")
        raise HTTPException(status_code=401, detail="Unauthorized: invalid credentials")

    # Stash identity on request.state so rate limiting can reuse it without
    # re-authenticating.
    request.state.identity = identity
    return identity


def require_permission(permission: str) -> Any:
    """Return a FastAPI dependency that enforces a specific permission.

    Usage:
        @app.post("/v1/privacy/mask", dependencies=[require_permission("privacy:mask")])
    """

    async def _checker(identity: Identity = Depends(get_current_identity)) -> None:
        if not identity.has_permission(permission):
            record_auth_denial("forbidden")
            raise HTTPException(status_code=403, detail="Forbidden: insufficient scope")

    return Depends(_checker)


def require_rest_path_permission(path: str) -> Any:
    """Convenience wrapper that enforces the permission for a REST path."""
    return require_permission(permission_for_rest_path(path))


# ---------------------------------------------------------------------------
# gRPC interceptor
# ---------------------------------------------------------------------------

class AuthInterceptor(grpc.ServerInterceptor):
    """gRPC server interceptor enforcing authentication and authorization."""

    def __init__(self, settings: SecuritySettings | None = None):
        self._settings = settings or get_security_settings()

    def _check(self, context: grpc.ServicerContext, method: str) -> Identity:
        """Authenticate and authorize the current gRPC call."""
        if not self._settings.auth_enabled:
            return ANONYMOUS_IDENTITY
        identity = _extract_identity_from_grpc_context(self._settings, context, method)
        if identity is None:
            record_auth_denial("unauthenticated")
            context.abort(grpc.StatusCode.UNAUTHENTICATED, "Missing or invalid credentials")
        assert identity is not None
        permission = permission_for_grpc_method(method)
        if not identity.has_permission(permission):
            record_auth_denial("forbidden")
            context.abort(grpc.StatusCode.PERMISSION_DENIED, "Insufficient scope")
        return identity

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

        def _wrap(handler_fn: Callable[..., Any]) -> Callable[..., Any]:
            def _wrapper(request_or_iterator: Any, context: grpc.ServicerContext) -> Any:
                self._check(context, method)
                return handler_fn(request_or_iterator, context)

            return _wrapper

        if not handler.request_streaming and not handler.response_streaming:
            return grpc.unary_unary_rpc_method_handler(
                _wrap(handler.unary_unary), **kwargs
            )
        if not handler.request_streaming and handler.response_streaming:
            return grpc.unary_stream_rpc_method_handler(
                _wrap(handler.unary_stream), **kwargs
            )
        if handler.request_streaming and not handler.response_streaming:
            return grpc.stream_unary_rpc_method_handler(
                _wrap(handler.stream_unary), **kwargs
            )
        return grpc.stream_stream_rpc_method_handler(
            _wrap(handler.stream_stream), **kwargs
        )


# Helper used by the rate-limit interceptor to avoid duplicating auth extraction.
def get_identity_from_grpc_context(
    context: grpc.ServicerContext, method: str
) -> Identity:
    """Extract identity from a gRPC context, falling back to anonymous if auth off."""
    settings = get_security_settings()
    if not settings.auth_enabled:
        return ANONYMOUS_IDENTITY
    identity = _extract_identity_from_grpc_context(settings, context, method)
    if identity is None:
        # The auth interceptor would have already rejected; this fallback avoids
        # leaking anonymous rate-limit budget if called in isolation.
        context.abort(grpc.StatusCode.UNAUTHENTICATED, "Missing or invalid credentials")
    assert identity is not None
    return identity
