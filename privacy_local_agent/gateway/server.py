"""网关统一启动入口模块。

读取配置文件与环境变量，初始化负载均衡器，并在同一 Event Loop 内异步运行 REST (Uvicorn)
与 gRPC 网关服务器。

Gateway unified entrypoint module.

Reads configuration from file and environment variables, initializes the load
balancer, and runs REST (Uvicorn) + gRPC gateway servers in the same event loop.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import sys
from typing import Any

import yaml

from privacy_local_agent.observability.logging_config import configure_logging, get_logger

from .balancer import LoadBalancer, health_check_loop
from .grpc_proxy import start_grpc_gateway
from .http_proxy import create_http_gateway_app

# 配置结构化日志 / Configure structured logging
configure_logging(
    log_level=os.environ.get("PRIVACY_LOG_LEVEL", "INFO"),
    json_format=os.environ.get("PRIVACY_LOG_FORMAT", "text").lower() == "json",
)
logger = get_logger(__name__)


def load_config() -> dict[str, Any]:
    """从配置文件或环境变量加载网关配置参数 / Load gateway config from file or env.

    Returns:
        解析合并后的配置字典。
    """
    config: dict[str, Any] = {
        "gateway": {
            "rest_host": "0.0.0.0",
            "rest_port": 8000,
            "grpc_host": "0.0.0.0",
            "grpc_port": 50000,
            "strategy": "round_robin",
            "health_check_interval": 5.0,
            # TLS 终结配置 / TLS termination config
            "tls_enabled": False,
            "tls_cert_file": "",
            "tls_key_file": "",
            "tls_ca_file": "",  # 用于 mTLS 客户端证书验证
        },
        "backends": [],
    }

    # 1. 尝试从指定配置文件读取
    config_path = os.environ.get("PRIVACY_GATEWAY_CONFIG")
    if config_path and os.path.exists(config_path):
        try:
            with open(config_path, encoding="utf-8") as f:
                loaded = yaml.safe_load(f)
                if loaded:
                    if "gateway" in loaded:
                        config["gateway"].update(loaded["gateway"])
                    if "backends" in loaded:
                        config["backends"] = loaded["backends"]
            logger.info("Loaded config from yaml", extra={"config_path": config_path})
        except Exception as e:
            logger.error("Failed to load config file", extra={"error": str(e)})

    # 2. 尝试使用环境变量进行覆盖/补充
    gw = config["gateway"]
    gw["rest_host"] = os.environ.get("GATEWAY_REST_HOST", gw["rest_host"])
    gw["rest_port"] = int(os.environ.get("GATEWAY_REST_PORT", str(gw["rest_port"])))
    gw["grpc_host"] = os.environ.get("GATEWAY_GRPC_HOST", gw["grpc_host"])
    gw["grpc_port"] = int(os.environ.get("GATEWAY_GRPC_PORT", str(gw["grpc_port"])))
    gw["strategy"] = os.environ.get("GATEWAY_STRATEGY", gw["strategy"])
    gw["health_check_interval"] = float(
        os.environ.get("GATEWAY_HEALTH_INTERVAL", str(gw["health_check_interval"]))
    )
    # TLS 终结环境变量 / TLS termination env vars
    gw["tls_enabled"] = os.environ.get("GATEWAY_TLS_ENABLED", str(gw["tls_enabled"])).lower() == "true"
    gw["tls_cert_file"] = os.environ.get("GATEWAY_TLS_CERT", gw["tls_cert_file"])
    gw["tls_key_file"] = os.environ.get("GATEWAY_TLS_KEY", gw["tls_key_file"])
    gw["tls_ca_file"] = os.environ.get("GATEWAY_TLS_CA", gw["tls_ca_file"])

    env_backends = os.environ.get("GATEWAY_BACKENDS")
    if env_backends:
        backends = []
        # 格式示例：http://127.0.0.1:8079|127.0.0.1:50051,http://127.0.0.1:8080|127.0.0.1:50052
        for item in env_backends.split(","):
            item = item.strip()
            if "|" in item:
                parts = item.split("|")
                backends.append(
                    {
                        "http_url": parts[0],
                        "grpc_address": parts[1],
                        "weight": 1,
                    }
                )
        config["backends"] = backends

    return config


async def async_main(
    rest_host: str | None = None,
    rest_port: int | None = None,
    grpc_host: str | None = None,
    grpc_port: int | None = None,
):
    """异步主函数，加载配置、初始化节点、启动双协议服务及健康检查后台任务。"""
    config = load_config()
    gw = config["gateway"]

    # 命令行参数覆盖配置
    if rest_host is not None:
        gw["rest_host"] = rest_host
    if rest_port is not None:
        gw["rest_port"] = rest_port
    if grpc_host is not None:
        gw["grpc_host"] = grpc_host
    if grpc_port is not None:
        gw["grpc_port"] = grpc_port

    # 初始化负载均衡器
    balancer = LoadBalancer(strategy=gw["strategy"])
    backends = config["backends"]

    if not backends:
        logger.warning("No backend nodes configured. Gateway will reject all forwarding requests!")

    for node_cfg in backends:
        balancer.add_node(
            http_url=node_cfg["http_url"],
            grpc_address=node_cfg["grpc_address"],
            weight=node_cfg.get("weight", 1),
        )

    # 1. 启动异步 gRPC 网关服务器（支持 TLS 终结）
    grpc_server = await start_grpc_gateway(
        host=gw["grpc_host"],
        port=gw["grpc_port"],
        balancer=balancer,
        tls_enabled=gw.get("tls_enabled", False),
        tls_cert_file=gw.get("tls_cert_file", ""),
        tls_key_file=gw.get("tls_key_file", ""),
        tls_ca_file=gw.get("tls_ca_file", ""),
    )

    # 2. 启动 HTTP 网关 FastAPI + Uvicorn 服务器（支持 TLS 终结）
    http_app = create_http_gateway_app(balancer)
    import uvicorn

    uv_config = uvicorn.Config(
        app=http_app,
        host=gw["rest_host"],
        port=gw["rest_port"],
        log_level="info",
        ssl_certfile=gw["tls_cert_file"] if gw.get("tls_enabled") else None,
        ssl_keyfile=gw["tls_key_file"] if gw.get("tls_enabled") else None,
        ssl_ca_certs=gw["tls_ca_file"] if gw.get("tls_enabled") and gw.get("tls_ca_file") else None,
    )
    uv_server = uvicorn.Server(uv_config)

    # 3. 注册健康检查后台任务
    health_interval = gw["health_check_interval"]
    health_task = asyncio.create_task(health_check_loop(balancer, health_interval))

    logger.info(
        "Gateway services successfully launched",
        extra={"rest_port": gw["rest_port"], "grpc_port": gw["grpc_port"], "strategy": gw["strategy"]},
    )

    try:
        # 并发挂起运行 Uvicorn 服务器和 gRPC 服务器，保持主流程运行
        await asyncio.gather(
            uv_server.serve(),
            grpc_server.wait_for_termination(),
        )
    except (asyncio.CancelledError, KeyboardInterrupt):
        logger.info("Gateway is shutting down...")
    finally:
        # 优雅清理资源
        health_task.cancel()
        await grpc_server.stop(grace=1.0)
        await balancer.close_all()
        logger.info("Gateway services safely stopped.")


def main():
    """同步入口，解析命令行参数并启动 asyncio 事件循环。"""

    parser = argparse.ArgumentParser(
        prog="privacy_local_agent.gateway.server",
        description="Privacy Local Agent REST + gRPC gateway / load balancer.",
    )
    parser.add_argument(
        "--rest-host",
        default=os.environ.get("GATEWAY_REST_HOST", "0.0.0.0"),
        help="Gateway REST host (default: 0.0.0.0 or GATEWAY_REST_HOST).",
    )
    parser.add_argument(
        "--rest-port",
        type=int,
        default=int(os.environ.get("GATEWAY_REST_PORT", "8000")),
        help="Gateway REST port (default: 8000 or GATEWAY_REST_PORT).",
    )
    parser.add_argument(
        "--grpc-host",
        default=os.environ.get("GATEWAY_GRPC_HOST", "0.0.0.0"),
        help="Gateway gRPC host (default: 0.0.0.0 or GATEWAY_GRPC_HOST).",
    )
    parser.add_argument(
        "--grpc-port",
        type=int,
        default=int(os.environ.get("GATEWAY_GRPC_PORT", "50000")),
        help="Gateway gRPC port (default: 50000 or GATEWAY_GRPC_PORT).",
    )
    args = parser.parse_args()

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(
            async_main(
                rest_host=args.rest_host,
                rest_port=args.rest_port,
                grpc_host=args.grpc_host,
                grpc_port=args.grpc_port,
            )
        )


if __name__ == "__main__":
    main()
