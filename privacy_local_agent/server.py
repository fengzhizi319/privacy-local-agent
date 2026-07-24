"""REST 与 gRPC 双协议统一启动与优雅关闭服务。

负责将 FastAPI REST 服务与 gRPC 服务分别运行，并捕获关闭信号（SIGTERM/SIGINT），
确保在途请求得到处理后安全干净地退出整个进程。

Unified launcher with graceful shutdown for REST and gRPC servers.
Captures termination signals, stops both servers with a grace period, and joins threads.
"""

import os
import signal
import sys
import threading
import time

import uvicorn

from .grpc_server import serve as grpc_serve
from .main import app
from .security.config import get_security_settings
from .security.tls import uvicorn_ssl_kwargs

# 从环境变量读取监听地址与端口
REST_HOST = os.environ.get("PRIVACY_REST_HOST", "0.0.0.0")
REST_PORT = int(os.environ.get("PRIVACY_REST_PORT", "8079"))
GRPC_HOST = os.environ.get("PRIVACY_GRPC_HOST", "0.0.0.0")
GRPC_PORT = int(os.environ.get("PRIVACY_GRPC_PORT", "50051"))


def main():
    """主入口函数。

    解析命令行参数，配置并启动 REST 与 gRPC 服务器，注册系统关闭信号处理器实现优雅关闭。
    命令行参数优先级高于环境变量。
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="privacy_local_agent.server",
        description="SecretFlow Local Privacy Agent REST + gRPC server.",
    )
    parser.add_argument(
        "--rest-host",
        default=os.environ.get("PRIVACY_REST_HOST", REST_HOST),
        help=f"REST server host (default: {REST_HOST} or PRIVACY_REST_HOST).",
    )
    parser.add_argument(
        "--rest-port",
        type=int,
        default=int(os.environ.get("PRIVACY_REST_PORT", str(REST_PORT))),
        help=f"REST server port (default: {REST_PORT} or PRIVACY_REST_PORT).",
    )
    parser.add_argument(
        "--grpc-host",
        default=os.environ.get("PRIVACY_GRPC_HOST", GRPC_HOST),
        help=f"gRPC server host (default: {GRPC_HOST} or PRIVACY_GRPC_HOST).",
    )
    parser.add_argument(
        "--grpc-port",
        type=int,
        default=int(os.environ.get("PRIVACY_GRPC_PORT", str(GRPC_PORT))),
        help=f"gRPC server port (default: {GRPC_PORT} or PRIVACY_GRPC_PORT).",
    )
    args = parser.parse_args()

    # 1. 配置 REST 隐式启动
    ssl_kwargs = uvicorn_ssl_kwargs(get_security_settings())
    config = uvicorn.Config(
        app,
        host=args.rest_host,
        port=args.rest_port,
        log_level="info",
        **ssl_kwargs,
    )
    rest_server = uvicorn.Server(config)

    # 2. 在非守护线程中启动 REST 服务
    rest_thread = threading.Thread(
        target=rest_server.run,
        name="uvicorn-rest-server",
        daemon=False,
    )
    rest_thread.start()

    # 3. 启动 gRPC 服务（非阻塞模式，使其返回 server 对象）
    grpc_server = grpc_serve(host=args.grpc_host, port=args.grpc_port, wait_for_termination=False)

    # 4. 信号处理逻辑
    shutdown_event = threading.Event()

    def handle_shutdown(signum, frame):
        print(f"\n[!] 捕获到信号 {signum}，正在启动优雅关闭流程...")
        shutdown_event.set()

    # 注册信号处理器
    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)

    # 5. 主线程等待终止信号
    try:
        while not shutdown_event.is_set():
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass

    # 6. 执行优雅退出
    print("[*] 正在停止 gRPC 服务 (保留 5 秒在途处理时间)...")
    grpc_server.stop(grace=5)

    print("[*] 正在停止 REST 服务...")
    rest_server.should_exit = True

    # 等待 REST 线程退出
    rest_thread.join(timeout=10)
    print("[+] 双协议服务优雅退出成功，进程安全终止。")
    sys.exit(0)


if __name__ == "__main__":
    main()
