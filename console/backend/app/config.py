"""Privacy 测试控制台后端的配置模块。

基于 ``pydantic-settings`` 从环境变量（可选 ``.env`` 文件）加载配置，
所有项均有默认值，本地开发零配置即可运行。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """从环境变量加载的配置集合。

    所有环境变量均为可选，默认值面向本地开发场景（agent 运行在
    默认的 ``127.0.0.1:8079``）。字段通过 ``alias`` 映射到环境变量名。
    """

    # 下游 privacy-local-agent 的 REST 基地址
    privacy_agent_url: str = Field(default="http://127.0.0.1:8079", alias="PRIVACY_AGENT_URL")
    # 可选的认证 API Key（agent 开启 auth 时必填）
    privacy_agent_api_key: Optional[str] = Field(default=None, alias="PRIVACY_AGENT_API_KEY")
    # 控制台后端监听地址
    console_host: str = Field(default="127.0.0.1", alias="PRIVACY_CONSOLE_HOST")
    # 控制台后端监听端口
    console_port: int = Field(default=8080, alias="PRIVACY_CONSOLE_PORT")
    # 前端构建产物目录（相对于 backend/ 工作目录），用于静态 SPA 托管
    static_dist_dir: Path = Field(default=Path("../web/dist"), alias="PRIVACY_CONSOLE_STATIC_DIR")

    # ── 可选安全加固配置（默认关闭 / 宽松，本地开发零配置即可运行）──────────────
    # 控制台 API Key：设置后 /api/*（除 /api/health）需携带 Authorization: Bearer <key>；
    # 未设置则完全放行（本地开发默认行为）。
    console_api_key: Optional[str] = Field(default=None, alias="CONSOLE_API_KEY")
    # 限流：每分钟每客户端 IP 的最大请求数（默认 600，设为 0 关闭）。
    console_rate_limit: int = Field(default=600, alias="CONSOLE_RATE_LIMIT")
    # 上传文件大小上限（字节），默认 10MB；超限返回 413。
    max_upload_bytes: int = Field(default=10 * 1024 * 1024, alias="CONSOLE_MAX_UPLOAD_BYTES")
    # 负载均衡探测目标 host 白名单（逗号分隔）；为空则不限制（本地探测默认行为）。
    lb_allowed_hosts: Optional[str] = Field(default=None, alias="LB_ALLOWED_HOSTS")

    class Config:
        # 支持从 .env 文件加载；populate_by_name 允许同时用字段名赋值（便于测试）
        env_file = ".env"
        env_file_encoding = "utf-8"
        populate_by_name = True


# 全局配置单例：模块导入时即完成环境变量解析。
settings = Settings()
