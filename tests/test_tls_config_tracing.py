"""安全 TLS/配置校验与 tracing noop 路径补充测试 / TLS, Config & Tracing Unit Tests.

中文说明：
补齐覆盖率门禁所需的少量分支：
- security/tls.py：uvicorn_ssl_kwargs 关闭/完整分支、grpc_server_credentials 关闭时报错
- security/config.py：TLS 一致性校验失败、_load_json_env 非法 JSON / 非对象
- observability/tracing.py：opentelemetry 缺失时的 noop tracer 与 start_span 路径

English Description:
Covers remaining branches in tls.py, config.py validators, and the no-op tracing
path used when opentelemetry is not installed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from privacy_local_agent.observability import tracing
from privacy_local_agent.security import tls as tls_mod
from privacy_local_agent.security.config import SecuritySettings, _load_json_env


class TestUvicornSslKwargs:
    def test_disabled_returns_empty(self):
        assert tls_mod.uvicorn_ssl_kwargs(SecuritySettings()) == {}

    def test_enabled_full(self):
        settings = SecuritySettings(
            tls_enabled=True,
            tls_cert_file=Path("server.crt"),
            tls_key_file=Path("server.key"),
            tls_ca_file=Path("ca.crt"),
            tls_key_password="secret",
            tls_client_auth="require",
        )
        kwargs = tls_mod.uvicorn_ssl_kwargs(settings)
        assert kwargs["ssl_certfile"] == "server.crt"
        assert kwargs["ssl_keyfile"] == "server.key"
        assert kwargs["ssl_ca_certs"] == "ca.crt"
        assert kwargs["ssl_keyfile_password"] == "secret"


class TestGrpcServerCredentials:
    def test_disabled_raises(self):
        with pytest.raises(RuntimeError, match="TLS disabled"):
            tls_mod.grpc_server_credentials(SecuritySettings())


class TestConfigValidators:
    def test_tls_enabled_requires_cert_key(self):
        with pytest.raises(ValueError, match="CERT_FILE"):
            SecuritySettings(tls_enabled=True)

    def test_client_auth_requires_ca(self):
        with pytest.raises(ValueError, match="CA_FILE"):
            SecuritySettings(tls_client_auth="require")


class TestLoadJsonEnv:
    def test_invalid_json_raises(self, monkeypatch):
        monkeypatch.setenv("X_JSON", "{not-valid")
        with pytest.raises(ValueError, match="invalid JSON"):
            _load_json_env("X_JSON", {})

    def test_non_object_raises(self, monkeypatch):
        monkeypatch.setenv("X_JSON", "[1, 2]")
        with pytest.raises(ValueError, match="must be a JSON object"):
            _load_json_env("X_JSON", {})


class TestTracingNoOp:
    def test_get_tracer_initializes_noop(self):
        old = tracing._tracer
        tracing._tracer = None
        try:
            tracer = tracing.get_tracer()
            assert tracer is not None
        finally:
            tracing._tracer = old

    def test_start_span_noop(self):
        old = tracing._tracer
        tracing._tracer = None
        try:
            with tracing.start_span("op", attributes={"k": "v"}) as span:
                assert span is None
        finally:
            tracing._tracer = old

    def test_noop_tracer_start_span_returns_none(self):
        tracer = tracing._noop_tracer()
        assert tracer.start_span("x") is None

    def test_init_tracing_noop_without_otel(self):
        tracer = tracing.init_tracing()
        assert tracer is not None
