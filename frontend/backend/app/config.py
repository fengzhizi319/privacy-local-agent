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

    class Config:
        # 支持从 .env 文件加载；populate_by_name 允许同时用字段名赋值（便于测试）
        env_file = ".env"
        env_file_encoding = "utf-8"
        populate_by_name = True


# 全局配置单例：模块导入时即完成环境变量解析。
settings = Settings()
