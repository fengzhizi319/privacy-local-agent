"""Optional OpenTelemetry tracing.

OpenTelemetry 作为可选依赖；未安装或环境变量未设置时使用 NoOp tracer，零开销。
OpenTelemetry is an optional dependency. When not installed or not configured,
a NoOp tracer is used so there is no runtime overhead.
"""

from __future__ import annotations

import contextlib
import os
from typing import Any, Iterator

# Optional import: if opentelemetry is not installed, tracing is a no-op.
try:  # pragma: no cover
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    _HAS_OTEL = True
except ImportError:  # pragma: no cover
    _HAS_OTEL = False
    trace = None  # type: ignore[assignment]

# Module-level tracer instance. Initialized lazily by init_tracing().
_tracer: Any = None


def init_tracing(
    endpoint: str | None = None,
    service_name: str = "privacy-local-agent",
) -> Any:
    """Initialize OpenTelemetry tracing if endpoint is provided and library exists.

    Args:
        endpoint: OTLP HTTP endpoint, e.g. ``http://jaeger:4318/v1/traces``.
                  Falls back to ``OTEL_EXPORTER_OTLP_ENDPOINT`` env var.
        service_name: Service name attached to every span.

    Returns:
        The configured tracer, or a no-op tracer.
    """
    global _tracer

    if not _HAS_OTEL:
        _tracer = _noop_tracer()
        return _tracer

    endpoint = endpoint or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        _tracer = trace.get_tracer(__name__)
        return _tracer

    provider = TracerProvider(
        resource=Resource({"service.name": service_name or "privacy-local-agent"})
    )
    exporter = OTLPSpanExporter(endpoint=endpoint)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    _tracer = provider.get_tracer(service_name)
    return _tracer


def _noop_tracer() -> Any:
    """Return a no-op tracer when opentelemetry is unavailable."""
    if _HAS_OTEL:
        return trace.get_tracer(__name__)
    # Minimal duck-typing fallback for environments without opentelemetry.
    class _NoOpTracer:
        @contextlib.contextmanager
        def start_as_current_span(self, *args: Any, **kwargs: Any) -> Iterator[Any]:
            yield None

        def start_span(self, *args: Any, **kwargs: Any) -> Any:
            return None

    return _NoOpTracer()


def get_tracer() -> Any:
    """Return the configured tracer, initializing a no-op tracer if necessary."""
    global _tracer
    if _tracer is None:
        _tracer = _noop_tracer()
    return _tracer


@contextlib.contextmanager
def start_span(name: str, attributes: dict[str, Any] | None = None) -> Iterator[Any]:
    """Context manager to start a span with optional attributes."""
    tracer = get_tracer()
    with tracer.start_as_current_span(name) as span:
        if span and attributes:
            for key, value in attributes.items():
                span.set_attribute(key, value)
        yield span
