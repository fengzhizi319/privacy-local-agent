"""Prometheus metrics definitions and ASGI app factory.

集中定义所有 Prometheus 指标，并提供挂载到 FastAPI 的 ASGI app。
"""

from __future__ import annotations

from typing import Any

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    make_asgi_app as _make_asgi_app,
)

# REST/gRPC request counter.
REQUESTS_TOTAL = Counter(
    "privacy_requests_total",
    "Total number of REST/gRPC requests handled.",
    ["method", "path", "status"],
)

# REST/gRPC request duration histogram.
REQUEST_DURATION = Histogram(
    "privacy_request_duration_seconds",
    "Request latency in seconds.",
    ["method", "path"],
    buckets=[
        0.005,
        0.01,
        0.025,
        0.05,
        0.1,
        0.25,
        0.5,
        1.0,
        2.5,
        5.0,
        10.0,
    ],
)

# Differential privacy query counter.
DP_QUERIES_TOTAL = Counter(
    "privacy_dp_queries_total",
    "Total number of differential privacy queries.",
    ["mechanism", "aggregation"],
)

# Remaining privacy budget per namespace.
BUDGET_REMAINING = Gauge(
    "privacy_budget_remaining",
    "Remaining privacy budget (epsilon or delta) per namespace.",
    ["namespace", "budget_type"],
)

# Classification results counter.
CLASSIFICATION_TOTAL = Counter(
    "privacy_classification_total",
    "Total number of classification results by final level and layer.",
    ["final_level", "layer"],
)

# Authentication/authorization/rate-limit denials.
AUTH_DENIALS_TOTAL = Counter(
    "privacy_auth_denials_total",
    "Total number of authentication/authorization/rate-limit denials.",
    ["reason"],
)

# Request/response traffic in bytes.
TRAFFIC_BYTES_TOTAL = Counter(
    "privacy_traffic_bytes_total",
    "Total request/response traffic in bytes.",
    ["method", "path", "direction"],
)


def make_asgi_app() -> Any:
    """Return the Prometheus metrics ASGI application to mount on FastAPI."""
    return _make_asgi_app()
