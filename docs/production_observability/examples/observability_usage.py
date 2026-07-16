"""可观测性模块使用示例。

本脚本不依赖外部 collector，可直接运行，演示：
1. 配置结构化日志（JSON / 文本）。
2. 生成并读取 Prometheus 指标。
3. 设置请求上下文并在日志中透传。
4. 初始化 OpenTelemetry（NoOp 降级）。

运行方式：
    source .venv/bin/activate
    PYTHONPATH=. python docs/production_observability/examples/observability_usage.py
"""

from __future__ import annotations

import asyncio
import io
import sys

from privacy_local_agent.observability import (
    AUTH_DENIALS_TOTAL,
    BUDGET_REMAINING,
    CLASSIFICATION_TOTAL,
    DP_QUERIES_TOTAL,
    REQUESTS_TOTAL,
    REQUEST_DURATION,
    RequestContext,
    configure_logging,
    get_logger,
    get_request_context,
    init_tracing,
    make_asgi_app,
    set_request_context,
    start_span,
)


def demo_logging() -> None:
    """演示文本与 JSON 结构化日志。"""
    print("\n=== 1. 结构化日志示例 ===")

    # 文本格式
    configure_logging(log_level="INFO", json_format=False)
    logger = get_logger("observability_usage")
    logger.info("这是一条文本日志")

    # 请求上下文中打印日志
    set_request_context(
        RequestContext(
            request_id="demo-req-001",
            method="POST",
            path="/v1/privacy/mask",
            identity_name="portal",
        )
    )
    logger.info("请求上下文已注入")

    # 读取当前上下文并打印
    ctx = get_request_context()
    print(
        f"当前上下文: request_id={ctx.request_id}, "
        f"path={ctx.path}, identity={ctx.identity_name}"
    )


async def _capture_metrics() -> bytes:
    """调用 Prometheus ASGI app 并返回指标文本。"""
    app = make_asgi_app()
    stdout = io.BytesIO()
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/metrics",
        "headers": [],
    }

    async def receive() -> dict:
        return {"type": "http.request", "body": b""}

    async def send(message: dict) -> None:
        if message.get("type") == "http.response.body":
            stdout.write(message.get("body", b""))

    await app(scope, receive, send)
    return stdout.getvalue()


def demo_metrics() -> None:
    """演示 Prometheus 指标生成与读取。"""
    print("\n=== 2. Prometheus 指标示例 ===")

    # 模拟 REST 请求指标
    REQUESTS_TOTAL.labels(method="POST", path="/v1/privacy/mask", status="200").inc()
    REQUEST_DURATION.labels(method="POST", path="/v1/privacy/mask").observe(0.012)

    # 模拟 DP 查询指标
    DP_QUERIES_TOTAL.labels(mechanism="laplace", aggregation="count").inc()
    DP_QUERIES_TOTAL.labels(mechanism="gaussian", aggregation="sum").inc()

    # 模拟预算指标
    BUDGET_REMAINING.labels(namespace="default", budget_type="epsilon").set(8.5)
    BUDGET_REMAINING.labels(namespace="default", budget_type="delta").set(0.0009)

    # 模拟分类结果指标
    CLASSIFICATION_TOTAL.labels(final_level="sensitive", layer="rule").inc()
    CLASSIFICATION_TOTAL.labels(final_level="internal", layer="llm").inc()

    # 模拟认证拒绝指标
    AUTH_DENIALS_TOTAL.labels(reason="unauthenticated").inc()
    AUTH_DENIALS_TOTAL.labels(reason="rate_limited").inc()

    if sys.version_info < (3, 7):
        raise RuntimeError("Python 3.7+ is required")

    metrics_bytes: bytes = asyncio.run(_capture_metrics())
    metrics_text = metrics_bytes.decode("utf-8")

    print("\n--- /metrics 输出片段 ---")
    for line in metrics_text.splitlines():
        if line.startswith("privacy_"):
            print(line)

    # 断言关键指标存在
    assert (
        'privacy_requests_total{method="POST",path="/v1/privacy/mask",status="200"} 1.0'
        in metrics_text
    )
    assert (
        'privacy_dp_queries_total{aggregation="count",mechanism="laplace"} 1.0'
        in metrics_text
    )
    assert (
        'privacy_budget_remaining{budget_type="epsilon",namespace="default"} 8.5'
        in metrics_text
    )


def demo_tracing() -> None:
    """演示 OpenTelemetry 可选初始化（NoOp 降级）。"""
    print("\n=== 3. OpenTelemetry Tracing 示例 ===")

    # 不设置 endpoint：返回 NoOp tracer
    tracer = init_tracing(endpoint=None, service_name="observability_usage")
    print(f"tracer 类型: {type(tracer).__name__}")

    with start_span("demo_span", attributes={"path": "/v1/privacy/mask"}) as span:
        print(f"span: {span}")
        print("NoOp tracer 不依赖外部 collector，运行结束")


def main() -> None:
    demo_logging()
    demo_metrics()
    demo_tracing()
    print("\n所有示例运行完成。")


if __name__ == "__main__":
    main()
