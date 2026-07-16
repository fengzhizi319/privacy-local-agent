"""网关统一启动入口模块。

读取配置文件与环境变量，初始化负载均衡器，并在同一 Event Loop 内异步运行 REST (Uvicorn)
与 gRPC 网关服务器。
"""

import asyncio
import logging
import os
import sys
import yaml

from .balancer import LoadBalancer, health_check_loop
from .grpc_proxy import start_grpc_gateway
from .http_proxy import create_http_gateway_app

# 配置基础日志格式与级别
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("gateway.server")


def load_config() -> dict:
    """从配置文件或环境变量加载网关配置参数。

    Returns:
        解析合并后的配置字典。
    """
    config = {
        "gateway": {
            "rest_host": "0.0.0.0",
            "rest_port": 8000,
            "grpc_host": "0.0.0.0",
            "grpc_port": 50000,
            "strategy": "round_robin",
            "health_check_interval": 5.0,
        },
        "backends": [],
    }

    # 1. 尝试从指定配置文件读取
    config_path = os.environ.get("PRIVACY_GATEWAY_CONFIG")
    if config_path and os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                loaded = yaml.safe_load(f)
                if loaded:
                    if "gateway" in loaded:
                        config["gateway"].update(loaded["gateway"])
                    if "backends" in loaded:
                        config["backends"] = loaded["backends"]
            logger.info(f"Loaded config from yaml: {config_path}")
        except Exception as e:
            logger.error(f"Failed to load config file: {e}")

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


async def async_main():
    """异步主函数，加载配置、初始化节点、启动双协议服务及健康检查后台任务。"""
    config = load_config()
    gw = config["gateway"]

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

    # 1. 启动异步 gRPC 网关服务器
    grpc_server = await start_grpc_gateway(
        host=gw["grpc_host"],
        port=gw["grpc_port"],
        balancer=balancer,
    )

    # 2. 启动 HTTP 网关 FastAPI + Uvicorn 服务器
    http_app = create_http_gateway_app(balancer)
    import uvicorn

    uv_config = uvicorn.Config(
        app=http_app,
        host=gw["rest_host"],
        port=gw["rest_port"],
        log_level="info",
    )
    uv_server = uvicorn.Server(uv_config)

    # 3. 注册健康检查后台任务
    health_interval = gw["health_check_interval"]
    health_task = asyncio.create_task(health_check_loop(balancer, health_interval))

    logger.info(
        f"Gateway services successfully launched: REST port={gw['rest_port']}, gRPC port={gw['grpc_port']}, strategy={gw['strategy']}"
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
    """同步入口，启动 asyncio 事件循环。"""
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
