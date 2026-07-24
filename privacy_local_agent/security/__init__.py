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
    "Identity",
    "Limiter",
    "RateLimitInterceptor",
    "SecuritySettings",
    "get_current_identity",
    "get_identity_from_grpc_context",
    "get_limiter",
    "get_security_settings",
    "grpc_server_credentials",
    "permission_for_grpc_method",
    "permission_for_rest_path",
    "rate_limit_dependency",
    "rate_limit_for_path",
    "require_permission",
    "require_rest_path_permission",
    "settings",
    "uvicorn_ssl_kwargs",
]
