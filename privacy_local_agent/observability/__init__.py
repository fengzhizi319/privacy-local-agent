"""Observability layer for privacy-local-agent.

提供结构化日志、Prometheus metrics、可选 OpenTelemetry tracing 以及 REST/gRPC 中间件。
Provides structured logging, Prometheus metrics, optional OpenTelemetry tracing,
and REST/gRPC middleware.
"""

from .context import RequestContext, get_request_context, set_request_context
from .logging_config import configure_logging, get_logger
from .metrics import (
    AUTH_DENIALS_TOTAL,
    BUDGET_REMAINING,
    CLASSIFICATION_TOTAL,
    DP_QUERIES_TOTAL,
    REQUESTS_TOTAL,
    REQUEST_DURATION,
    make_asgi_app,
)
from .tracing import get_tracer, init_tracing, start_span

__all__ = [
    "RequestContext",
    "get_request_context",
    "set_request_context",
    "configure_logging",
    "get_logger",
    "AUTH_DENIALS_TOTAL",
    "BUDGET_REMAINING",
    "CLASSIFICATION_TOTAL",
    "DP_QUERIES_TOTAL",
    "REQUESTS_TOTAL",
    "REQUEST_DURATION",
    "make_asgi_app",
    "get_tracer",
    "init_tracing",
    "start_span",
]
