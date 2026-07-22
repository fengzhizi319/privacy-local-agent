"""Backend proxy configuration for the privacy test console."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Settings loaded from environment variables.

    All environment variables are optional; sensible defaults are provided for
    local development against the default privacy-local-agent server.
    """

    privacy_agent_url: str = Field(default="http://127.0.0.1:8079", alias="PRIVACY_AGENT_URL")
    privacy_agent_api_key: Optional[str] = Field(default=None, alias="PRIVACY_AGENT_API_KEY")
    console_host: str = Field(default="127.0.0.1", alias="PRIVACY_CONSOLE_HOST")
    console_port: int = Field(default=8080, alias="PRIVACY_CONSOLE_PORT")
    static_dist_dir: Path = Field(default=Path("../web/dist"), alias="PRIVACY_CONSOLE_STATIC_DIR")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        populate_by_name = True


settings = Settings()
