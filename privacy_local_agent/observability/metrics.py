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

# Masking operations counter.
MASKING_OPERATIONS_TOTAL = Counter(
    "privacy_masking_operations_total",
    "Total number of masking operations.",
    ["operation"],
)

# K-anonymity operations counter.
KANO_OPERATIONS_TOTAL = Counter(
    "privacy_kano_operations_total",
    "Total number of K-anonymity operations.",
    ["operation"],
)

# Query obfuscation operations counter.
QOL_OPERATIONS_TOTAL = Counter(
    "privacy_qol_operations_total",
    "Total number of query obfuscation operations.",
    ["domain"],
)

# Classification async jobs counter.
CLASSIFICATION_JOBS_TOTAL = Counter(
    "privacy_classification_jobs_total",
    "Total number of classification async jobs by status.",
    ["status"],
)

# Classification async job duration histogram.
CLASSIFICATION_JOBS_DURATION = Histogram(
    "privacy_classification_jobs_duration_seconds",
    "Classification async job execution latency in seconds.",
    ["status"],
    buckets=[0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0],
)

# Classification human-review queue size.
CLASSIFICATION_REVIEW_QUEUE_SIZE = Gauge(
    "privacy_classification_review_queue_size",
    "Current number of pending classification review entries.",
)

# Classification shadow mode diff counter.
CLASSIFICATION_SHADOW_DIFF_TOTAL = Counter(
    "privacy_classification_shadow_diff_total",
    "Total number of shadow mode classification differences.",
)

# Classification compliance template usage counter.
CLASSIFICATION_TEMPLATES_TOTAL = Counter(
    "privacy_classification_templates_total",
    "Total number of classification requests using a compliance template.",
    ["template"],
)


def make_asgi_app() -> Any:
    """Return the Prometheus metrics ASGI application to mount on FastAPI."""
    return _make_asgi_app()
