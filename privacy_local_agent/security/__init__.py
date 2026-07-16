"""Security layer for privacy-local-agent.

Provides TLS, authentication/authorization, and rate limiting shared between the
REST (FastAPI) and gRPC servers.
"""

from .auth import (
    AuthInterceptor,
    get_current_identity,
    get_identity_from_grpc_context,
    require_permission,
    require_rest_path_permission,
)
from .config import SecuritySettings, get_security_settings, settings
from .identity import Identity, permission_for_grpc_method, permission_for_rest_path
from .ratelimit import (
    Limiter,
    RateLimitInterceptor,
    get_limiter,
    rate_limit_dependency,
    rate_limit_for_path,
)
from .tls import grpc_server_credentials, uvicorn_ssl_kwargs

__all__ = [
    "AuthInterceptor",
    "get_current_identity",
    "get_identity_from_grpc_context",
    "require_permission",
    "require_rest_path_permission",
    "SecuritySettings",
    "get_security_settings",
    "settings",
    "Identity",
    "permission_for_grpc_method",
    "permission_for_rest_path",
    "Limiter",
    "RateLimitInterceptor",
    "get_limiter",
    "rate_limit_dependency",
    "rate_limit_for_path",
    "grpc_server_credentials",
    "uvicorn_ssl_kwargs",
]
