"""Prometheus metrics definitions and ASGI app factory.

集中定义所有 Prometheus 指标，并提供挂载到 FastAPI 的 ASGI app。
"""

from __future__ import annotations

from typing import Any

from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
)
from prometheus_client import (
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

# Classification rule engine hit counter (Layer-1).
CLASSIFICATION_RULE_HITS_TOTAL = Counter(
    "privacy_classification_rule_hits_total",
    "Total number of Layer-1 rule engine hits by rule_id.",
    ["rule_id"],
)

# Classification NER engine invocations (Layer-2).
CLASSIFICATION_NER_TOTAL = Counter(
    "privacy_classification_ner_total",
    "Total number of Small-NER engine invocations.",
    ["status"],
)

# Classification LLM engine invocations (Layer-3).
CLASSIFICATION_LLM_TOTAL = Counter(
    "privacy_classification_llm_total",
    "Total number of LLM classifier invocations.",
    ["status"],
)

# Classification field/record/table operation duration histogram.
CLASSIFICATION_DURATION = Histogram(
    "privacy_classification_duration_seconds",
    "Classification operation latency in seconds.",
    ["operation"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

# Classification composite rule hit counter.
CLASSIFICATION_COMPOSITE_HITS_TOTAL = Counter(
    "privacy_classification_composite_hits_total",
    "Total number of composite rule hits by rule_id.",
    ["rule_id"],
)

# NER engine inference duration histogram (Layer-2).
CLASSIFICATION_NER_DURATION = Histogram(
    "privacy_classification_ner_duration_seconds",
    "Small-NER engine inference latency in seconds.",
    ["engine"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
)

# LLM engine inference duration histogram (Layer-3).
CLASSIFICATION_LLM_DURATION = Histogram(
    "privacy_classification_llm_duration_seconds",
    "LLM classifier inference latency in seconds.",
    ["engine"],
    buckets=[0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0],
)

# Vectorized rule engine batch evaluation counter.
CLASSIFICATION_VECTORIZED_BATCH_TOTAL = Counter(
    "privacy_classification_vectorized_batch_total",
    "Total number of vectorized batch evaluations.",
    ["field_name"],
)

# Vectorized rule engine batch size histogram.
CLASSIFICATION_VECTORIZED_BATCH_SIZE = Histogram(
    "privacy_classification_vectorized_batch_size",
    "Number of rows per vectorized batch evaluation.",
    buckets=[1, 10, 50, 100, 500, 1000, 5000, 10000, 50000],
)

# Profile parameter resolution counter.
PROFILE_RESOLVE_TOTAL = Counter(
    "privacy_profile_resolve_total",
    "Total number of parameter resolution operations.",
    ["primitive", "status"],
)

# Data adapter extraction counter.
DATA_EXTRACTION_TOTAL = Counter(
    "privacy_data_extraction_total",
    "Total number of data extraction operations by source format.",
    ["format", "status"],
)


def make_asgi_app() -> Any:
    """Return the Prometheus metrics ASGI application to mount on FastAPI."""
    return _make_asgi_app()
