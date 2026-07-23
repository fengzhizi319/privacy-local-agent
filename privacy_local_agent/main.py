"""REST API 入口模块（应用装配）。

基于 FastAPI 构建，负责应用级装配：生命周期管理、可观测性中间件、
Prometheus metrics 暴露，以及把按域拆分到 ``routers/*`` 的子路由与既有的
数据分类路由 ``classification_router`` 统一挂载。

各端点的请求模型定义在 ``schemas.py``，跨路由共享的服务单例、安全依赖与
异常映射定义在 ``deps.py``；本模块仅做组装，不再内联具体端点实现。

REST API entrypoint built with FastAPI. Endpoint implementations are split into
``routers/*``; request models live in ``schemas.py`` and shared dependencies
(service singleton, security deps, exception mapping) live in ``deps.py``.
"""

import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .classification_routes import classification_router
from .deps import service  # noqa: F401  # 重新导出，保持 ``from privacy_local_agent.main import service`` 可用
from .observability.logging_config import configure_logging
from .observability.middleware import ObservabilityMiddleware
from .observability.metrics import make_asgi_app
from .observability.tracing import init_tracing
from .routers import budget, dp, file, health, kano, ldp, mask, profile, qol


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理器。

    启动时初始化结构化日志、可选的 OpenTelemetry tracing 以及 LLM 异步预热。
    关闭时执行清理逻辑。

    Args:
        app: FastAPI 应用实例。
    """
    configure_logging(
        log_level=os.environ.get("PRIVACY_LOG_LEVEL", "INFO"),
        json_format=os.environ.get("PRIVACY_LOG_FORMAT", "text").lower() == "json",
        service_name=os.environ.get("PRIVACY_SERVICE_NAME", "privacy-local-agent"),
    )
    init_tracing(
        endpoint=os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"),
        service_name=os.environ.get(
            "OTEL_SERVICE_NAME",
            os.environ.get("PRIVACY_SERVICE_NAME", "privacy-local-agent"),
        ),
    )

    # 在后台异步预热本地大模型（若启用），避免第一个请求阻塞
    warmup_task = None
    if os.environ.get("PRIVACY_WARMUP_LLM", "false").lower() == "true":
        warmup_task = asyncio.create_task(service.classification_api.warmup_async())
        app.state.warmup_task = warmup_task

    try:
        yield
    finally:
        if warmup_task is not None:
            warmup_task.cancel()


# FastAPI 应用实例；title 用于 OpenAPI 文档，lifespan 用于生命周期钩子
app = FastAPI(title="SecretFlow Local Privacy Agent", lifespan=lifespan)

# 注册可观测性中间件：request_id 透传、访问日志、Prometheus metrics。
# 注意：/metrics 本身会被中间件排除，避免自引用。
app.add_middleware(ObservabilityMiddleware)

# 暴露 Prometheus metrics。
app.mount("/metrics", make_asgi_app())

# 挂载数据分类路由；分类路由自身已声明认证、限速与权限依赖
app.include_router(classification_router)

# 挂载按域拆分的子路由（健康检查 / 脱敏 / DP / LDP / K-匿名 / QoL / 预算 / 推荐 / 文件处理）。
app.include_router(health.router)
app.include_router(mask.router)
app.include_router(dp.router)
app.include_router(ldp.router)
app.include_router(kano.router)
app.include_router(qol.router)
app.include_router(budget.router)
app.include_router(profile.router)
app.include_router(file.router)


if __name__ == "__main__":
    import uvicorn

    # 直接运行本模块时使用 uvicorn 启动开发服务器，监听 127.0.0.1:8079
    uvicorn.run(app, host="127.0.0.1", port=8079)
