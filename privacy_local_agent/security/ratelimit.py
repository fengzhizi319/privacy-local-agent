"""Rate limiting for REST and gRPC.

基于 ``limits`` 库实现按调用者身份 + 接口的滑动窗口限流。默认使用进程内存存储，
多副本场景可配置 Redis 后端。

Implements per-identity, per-endpoint sliding-window rate limiting using the
``limits`` library. Uses in-memory storage by default; Redis can be configured for
multi-replica deployments.
"""

from __future__ import annotations

import math
from typing import Any, Callable

import grpc
from fastapi import Depends, HTTPException, Request
from limits import RateLimitItemPerSecond, storage, strategies

from ..observability.middleware import record_auth_denial
from .config import SecuritySettings, get_security_settings
from .identity import Identity, is_health_path_or_method


class Limiter:
    """Shared rate limiter backed by memory or Redis storage."""

    def __init__(self, settings: SecuritySettings):
        self._settings = settings
        if settings.rate_limit_redis_url:
            self._storage: storage.Storage = storage.RedisStorage(settings.rate_limit_redis_url)
        else:
            self._storage = storage.MemoryStorage()
        self._limiter = strategies.MovingWindowRateLimiter(self._storage)

    def _limit_for_endpoint(self, endpoint: str) -> RateLimitItemPerSecond:
        """Build a ``RateLimitItemPerSecond`` for the given endpoint.

        If a per-endpoint override exists, use it; otherwise fall back to defaults.
        The burst value is treated as the maximum hits allowed within a window whose
        length (in seconds) is ``burst / rps`` (clamped to at least 1 second).
        """
        cfg = self._settings.rate_limit_per_endpoint.get(endpoint)
        if cfg:
            rps = cfg.rps
            burst = cfg.burst
        else:
            rps = self._settings.rate_limit_default_rps
            burst = self._settings.rate_limit_default_burst

        window_seconds = max(1.0, burst / rps) if rps > 0 else 1.0
        # limits uses integer multiples; round up to be slightly more conservative.
        return RateLimitItemPerSecond(int(burst), math.ceil(window_seconds))

    def is_allowed(self, identity: Identity, endpoint: str) -> bool:
        """Return True if the call is within the rate limit for identity + endpoint."""
        key = f"{identity.service_type}:{identity.name}:{endpoint}"
        item = self._limit_for_endpoint(endpoint)
        return self._limiter.hit(item, key)


# Module-level singleton so REST and gRPC share the same limiter state.
# We recreate it when the parsed settings change (e.g. in tests or on reload).
_limiter_instance: Limiter | None = None
_limiter_settings: SecuritySettings | None = None


def get_limiter() -> Limiter:
    """Return the shared Limiter instance, recreating it if settings changed."""
    global _limiter_instance, _limiter_settings
    settings = get_security_settings()
    if _limiter_instance is None or _limiter_settings != settings:
        _limiter_settings = settings
        _limiter_instance = Limiter(settings)
    return _limiter_instance


def reset_limiter() -> None:
    """Reset the cached limiter. Useful for tests that change rate-limit settings."""
    global _limiter_instance, _limiter_settings
    _limiter_instance = None
    _limiter_settings = None


# ---------------------------------------------------------------------------
# REST dependency
# ---------------------------------------------------------------------------

async def rate_limit_dependency(request: Request) -> None:
    """FastAPI dependency that enforces rate limiting.

    Expects ``request.state.identity`` to have been set by ``get_current_identity``.
    When rate limiting is disabled this is a no-op.
    """
    settings = get_security_settings()
    if not settings.rate_limit_enabled:
        return

    path = request.url.path
    if is_health_path_or_method(path) and settings.health_no_rate_limit:
        return

    identity: Identity | None = getattr(request.state, "identity", None)
    if identity is None:
        # Auth is disabled and no identity was stashed; rate-limit by anonymous.
        identity = Identity("external", "anonymous", [])

    if not get_limiter().is_allowed(identity, path):
        record_auth_denial("rate_limited")
        raise HTTPException(status_code=429, detail="Rate limit exceeded")


def rate_limit_for_path(path: str) -> Depends:
    """Return a FastAPI dependency enforcing rate limits for a specific path."""

    async def _checker(request: Request) -> None:
        if not _settings.rate_limit_enabled:
            return
        if is_health_path_or_method(path) and _settings.health_no_rate_limit:
            return
        identity: Identity | None = getattr(request.state, "identity", None)
        if identity is None:
            identity = Identity("external", "anonymous", [])
        if not get_limiter().is_allowed(identity, path):
            raise HTTPException(status_code=429, detail="Rate limit exceeded")

    return Depends(_checker)


# ---------------------------------------------------------------------------
# gRPC interceptor
# ---------------------------------------------------------------------------

class RateLimitInterceptor(grpc.ServerInterceptor):
    """gRPC server interceptor enforcing per-identity rate limits."""

    def __init__(self, settings: SecuritySettings | None = None):
        self._settings = settings or get_security_settings()

    def _check(self, context: grpc.ServicerContext, method: str) -> None:
        settings = self._settings
        if not settings.rate_limit_enabled:
            return
        if is_health_path_or_method(method) and settings.health_no_rate_limit:
            return

        # Import here to avoid circular imports at module load time.
        from .auth import get_identity_from_grpc_context

        identity = get_identity_from_grpc_context(context, method)
        if not get_limiter().is_allowed(identity, method):
            record_auth_denial("rate_limited")
            context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED, "Rate limit exceeded")

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
