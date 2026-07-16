"""Security configuration loaded from environment variables.

安全相关配置统一由此模块解析。所有开关默认关闭，保证本地开发与既有测试
不受影响；生产环境通过环境变量显式启用。

Security settings are centralized here. All toggles default to off so local dev and
existing tests keep working; production opts in via environment variables.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class KeyConfig(BaseModel):
    """Static API key mapping entry.

    Attributes:
        name: Human-readable service/account name used in logs and rate-limit keys.
        scopes: List of permissions granted to this key. Use ["*"] for full access.
    """

    name: str
    scopes: list[str] = Field(default_factory=list)


class RateLimitConfig(BaseModel):
    """Per-endpoint rate limit override.

    Attributes:
        rps: Sustained requests per second.
        burst: Maximum burst allowed before throttling.
    """

    rps: float
    burst: float


class SecuritySettings(BaseModel):
    """Centralized security settings parsed from environment variables.

    The model uses Pydantic v2 BaseModel without introducing an extra dependency on
    pydantic-settings. Environment variables are read once at import time.
    """

    # ---------------------------- TLS ---------------------------------
    tls_enabled: bool = Field(default=False)
    tls_cert_file: Path | None = Field(default=None)
    tls_key_file: Path | None = Field(default=None)
    tls_ca_file: Path | None = Field(default=None)
    tls_client_auth: Literal["none", "optional", "require"] = Field(default="none")
    tls_key_password: str | None = Field(default=None)

    # ---------------------------- Auth --------------------------------
    auth_enabled: bool = Field(default=False)
    auth_internal_mtls_enabled: bool = Field(default=True)
    internal_keys: dict[str, KeyConfig] = Field(default_factory=dict)
    external_keys: dict[str, KeyConfig] = Field(default_factory=dict)

    # -------------------------- Rate Limit ----------------------------
    rate_limit_enabled: bool = Field(default=False)
    rate_limit_default_rps: float = Field(default=10.0)
    rate_limit_default_burst: float = Field(default=20.0)
    rate_limit_per_endpoint: dict[str, RateLimitConfig] = Field(default_factory=dict)
    rate_limit_redis_url: str | None = Field(default=None)

    # --------------------------- Health -------------------------------
    health_no_auth: bool = Field(default=True)
    health_no_rate_limit: bool = Field(default=True)

    @model_validator(mode="after")
    def _check_tls_consistency(self) -> "SecuritySettings":
        """Validate that TLS settings are mutually consistent."""
        if self.tls_enabled:
            if not self.tls_cert_file or not self.tls_key_file:
                raise ValueError(
                    "PRIVACY_TLS_CERT_FILE and PRIVACY_TLS_KEY_FILE are required when TLS is enabled."
                )
        if self.tls_client_auth in ("optional", "require") and not self.tls_ca_file:
            raise ValueError(
                "PRIVACY_TLS_CA_FILE is required when tls_client_auth is optional or require."
            )
        return self


def _load_bool_env(name: str, default: bool = False) -> bool:
    """Parse a boolean environment variable (true/1/yes)."""
    value = os.environ.get(name, "")
    return value.lower() in {"true", "1", "yes", "on"} if value else default


def _load_json_env(name: str, default: dict[str, Any]) -> dict[str, Any]:
    """Parse a JSON object from an environment variable."""
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Environment variable {name} contains invalid JSON: {exc}")
    if not isinstance(parsed, dict):
        raise ValueError(f"Environment variable {name} must be a JSON object.")
    return parsed


def get_security_settings() -> SecuritySettings:
    """Parse and return SecuritySettings from the current environment.

    This function re-parses environment variables on every call so that tests and
    runtime reconfiguration can change behaviour by updating ``os.environ``.
    """
    return SecuritySettings(
        tls_enabled=_load_bool_env("PRIVACY_TLS_ENABLED"),
        tls_cert_file=os.environ.get("PRIVACY_TLS_CERT_FILE") or None,
        tls_key_file=os.environ.get("PRIVACY_TLS_KEY_FILE") or None,
        tls_ca_file=os.environ.get("PRIVACY_TLS_CA_FILE") or None,
        tls_client_auth=(
            os.environ.get("PRIVACY_TLS_CLIENT_AUTH", "none") or "none"  # type: ignore[arg-type]
        ),
        tls_key_password=os.environ.get("PRIVACY_TLS_KEY_PASSWORD") or None,
        auth_enabled=_load_bool_env("PRIVACY_AUTH_ENABLED"),
        auth_internal_mtls_enabled=_load_bool_env(
            "PRIVACY_AUTH_INTERNAL_MTLS_ENABLED", default=True
        ),
        internal_keys=_load_json_env("PRIVACY_AUTH_INTERNAL_KEYS_JSON", {}),
        external_keys=_load_json_env("PRIVACY_AUTH_EXTERNAL_KEYS_JSON", {}),
        rate_limit_enabled=_load_bool_env("PRIVACY_RATE_LIMIT_ENABLED"),
        rate_limit_default_rps=float(
            os.environ.get("PRIVACY_RATE_LIMIT_DEFAULT_RPS", "10")
        ),
        rate_limit_default_burst=float(
            os.environ.get("PRIVACY_RATE_LIMIT_DEFAULT_BURST", "20")
        ),
        rate_limit_per_endpoint=_load_json_env(
            "PRIVACY_RATE_LIMIT_PER_ENDPOINT_JSON", {}
        ),
        rate_limit_redis_url=os.environ.get("PRIVACY_RATE_LIMIT_REDIS_URL") or None,
        health_no_auth=_load_bool_env("PRIVACY_HEALTH_NO_AUTH", default=True),
        health_no_rate_limit=_load_bool_env(
            "PRIVACY_HEALTH_NO_RATE_LIMIT", default=True
        ),
    )


# Module-level convenience alias used by the rest of the package.
settings = get_security_settings()
