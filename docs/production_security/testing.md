# 生产安全加固测试文档

## 1. 概述

本文档定义 `privacy_local_agent/security/` 的测试策略、测试范围与可执行示例。安全模块测试需覆盖 TLS/mTLS 握手、API Key 认证、接口级权限鉴权、速率限制以及健康检查豁免。

## 2. 测试目标

- 验证动态生成的自签名证书链可被 REST/gRPC 服务端与客户端正确加载。
- 验证仅服务端 TLS 与 mTLS 模式下的握手行为（信任 CA / 不信任 CA / 缺失客户端证书）。
- 验证内部 API Key 通配权限、外部 API Key 最小权限、缺失/无效凭证、越权场景。
- 验证按身份 + 接口的滑动窗口限速生效，超速返回 429 / `RESOURCE_EXHAUSTED`。
- 验证 `/health` 与 `Health` RPC 默认免认证、不限速。

## 3. 单元测试策略

### 3.1 证书生成

复用 `tests/security_certs.py` 中的 `generate_test_certs`，避免在仓库中提交真实证书。

```python
from pathlib import Path
from tests.security_certs import generate_test_certs

def test_generate_certs(tmp_path: Path):
    certs = generate_test_certs(tmp_path)
    for name in ("ca_cert", "server_cert", "server_key", "client_cert", "client_key"):
        assert certs[name].exists()
```

### 3.2 TLS 配置校验

```python
import pytest
from privacy_local_agent.security.config import SecuritySettings


def test_tls_requires_cert_and_key():
    with pytest.raises(ValueError, match="PRIVACY_TLS_CERT_FILE and PRIVACY_TLS_KEY_FILE"):
        SecuritySettings(tls_enabled=True)


def test_mtls_requires_ca():
    with pytest.raises(ValueError, match="PRIVACY_TLS_CA_FILE is required"):
        SecuritySettings(
            tls_enabled=True,
            tls_cert_file="server.crt",
            tls_key_file="server.key",
            tls_client_auth="require",
        )
```

### 3.3 认证与鉴权

```python
import pytest
from fastapi.testclient import TestClient

from privacy_local_agent.main import app
from privacy_local_agent.security.config import get_security_settings
from privacy_local_agent.security.identity import Identity

client = TestClient(app)


@pytest.fixture
def auth_enabled(monkeypatch):
    monkeypatch.setenv("PRIVACY_AUTH_ENABLED", "true")
    monkeypatch.setenv(
        "PRIVACY_AUTH_INTERNAL_KEYS_JSON",
        '{"sk-internal":{"name":"secretpad","scopes":["*"]}}',
    )
    monkeypatch.setenv(
        "PRIVACY_AUTH_EXTERNAL_KEYS_JSON",
        '{"sk-external":{"name":"portal","scopes":["privacy:mask"]}}',
    )
    yield


def test_internal_token_full_access(auth_enabled):
    headers = {"Authorization": "Bearer sk-internal"}
    resp = client.post(
        "/v1/privacy/dp/count",
        headers=headers,
        json={"values": [1, 0, 1], "params": {"epsilon": 1.0}},
    )
    assert resp.status_code == 200


def test_external_token_forbidden(auth_enabled):
    headers = {"Authorization": "Bearer sk-external"}
    resp = client.post(
        "/v1/privacy/dp/count",
        headers=headers,
        json={"values": [1, 0, 1], "params": {"epsilon": 1.0}},
    )
    assert resp.status_code == 403


def test_missing_token_returns_401(auth_enabled):
    resp = client.post("/v1/privacy/mask", json={"field_name": "mobile", "value": "13812345678"})
    assert resp.status_code == 401


def test_health_exempt_by_default(auth_enabled):
    resp = client.get("/health")
    assert resp.status_code == 200
```

### 3.4 速率限制

```python
import pytest
from fastapi.testclient import TestClient

from privacy_local_agent.main import app

client = TestClient(app)


@pytest.fixture
def tight_rate_limit(monkeypatch):
    monkeypatch.setenv("PRIVACY_RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("PRIVACY_RATE_LIMIT_DEFAULT_RPS", "100")
    monkeypatch.setenv("PRIVACY_RATE_LIMIT_DEFAULT_BURST", "100")
    monkeypatch.setenv(
        "PRIVACY_RATE_LIMIT_PER_ENDPOINT_JSON",
        '{"/v1/privacy/mask":{"rps":1,"burst":1}}',
    )
    yield


def test_rate_limit_blocks_excess(tight_rate_limit):
    resp = client.post("/v1/privacy/mask", json={"field_name": "mobile", "value": "13812345678"})
    assert resp.status_code == 200

    resp = client.post("/v1/privacy/mask", json={"field_name": "mobile", "value": "13912345678"})
    assert resp.status_code == 429


def test_rate_limit_health_exempt(tight_rate_limit):
    for _ in range(5):
        assert client.get("/health").status_code == 200
```

### 3.5 身份模型

```python
from privacy_local_agent.security.identity import Identity


def test_identity_wildcard():
    identity = Identity("internal", "secretpad", ["*"])
    assert identity.has_permission("privacy:dp")
    assert identity.has_permission("classification:read")


def test_identity_exact_scope():
    identity = Identity("external", "portal", ["privacy:mask"])
    assert identity.has_permission("privacy:mask")
    assert not identity.has_permission("privacy:dp")
```

## 4. 集成测试策略

### 4.1 REST TLS

使用 `uvicorn.Server` 在后台线程启动应用，使用 `httpx` 访问 HTTPS。

```python
import contextlib
import os
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import uvicorn

from privacy_local_agent.main import app
from privacy_local_agent.security.config import get_security_settings
from privacy_local_agent.security.tls import uvicorn_ssl_kwargs
from tests.security_certs import generate_test_certs


class RestServer:
    def __init__(self, port: int, ssl_kwargs: dict[str, Any], ca: Path):
        self._port = port
        self._server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", **ssl_kwargs)
        )
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._ca = ca

    def start(self):
        self._thread.start()
        deadline = time.monotonic() + 10
        with httpx.Client(verify=str(self._ca)) as client:
            while time.monotonic() < deadline:
                try:
                    if client.get(f"https://127.0.0.1:{self._port}/health").status_code == 200:
                        return
                except Exception:
                    time.sleep(0.05)
        raise RuntimeError("REST server did not start")

    def stop(self):
        self._server.should_exit = True
        self._thread.join(timeout=5)


@contextlib.contextmanager
def rest_tls_server(certs: dict[str, Path]):
    os.environ["PRIVACY_TLS_ENABLED"] = "true"
    os.environ["PRIVACY_TLS_CERT_FILE"] = str(certs["server_cert"])
    os.environ["PRIVACY_TLS_KEY_FILE"] = str(certs["server_key"])
    port = 18079
    server = RestServer(port, uvicorn_ssl_kwargs(get_security_settings()), certs["ca_cert"])
    try:
        server.start()
        yield port
    finally:
        server.stop()
        os.environ.pop("PRIVACY_TLS_ENABLED", None)
        os.environ.pop("PRIVACY_TLS_CERT_FILE", None)
        os.environ.pop("PRIVACY_TLS_KEY_FILE", None)


def test_rest_tls_trusted_ca(tmp_path: Path):
    certs = generate_test_certs(tmp_path)
    with rest_tls_server(certs) as port:
        with httpx.Client(verify=str(certs["ca_cert"])) as client:
            resp = client.get(f"https://127.0.0.1:{port}/health")
            assert resp.status_code == 200
```

### 4.2 gRPC TLS/mTLS

使用 `grpc.server` + `grpc_server_credentials` 启动 gRPCs 服务端，客户端使用 `grpc.ssl_channel_credentials`。

```python
import contextlib
import os
from concurrent import futures
from pathlib import Path

import grpc
import pytest

from privacy_local_agent import privacy_pb2, privacy_pb2_grpc
from privacy_local_agent.grpc_server import PrivacyServicer
from privacy_local_agent.security.config import get_security_settings
from privacy_local_agent.security.tls import grpc_server_credentials
from tests.security_certs import generate_test_certs


def _start_grpc_server(port: int, certs: dict[str, Path], client_auth: str = "none"):
    os.environ["PRIVACY_TLS_ENABLED"] = "true"
    os.environ["PRIVACY_TLS_CERT_FILE"] = str(certs["server_cert"])
    os.environ["PRIVACY_TLS_KEY_FILE"] = str(certs["server_key"])
    os.environ["PRIVACY_TLS_CLIENT_AUTH"] = client_auth
    if client_auth in ("optional", "require"):
        os.environ["PRIVACY_TLS_CA_FILE"] = str(certs["ca_cert"])

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
    privacy_pb2_grpc.add_PrivacyServiceServicer_to_server(PrivacyServicer(), server)
    creds = grpc_server_credentials(get_security_settings())
    server.add_secure_port(f"127.0.0.1:{port}", creds)
    server.start()
    return server


@contextlib.contextmanager
def grpc_tls_server(certs: dict[str, Path], port: int, client_auth: str = "none"):
    server = _start_grpc_server(port, certs, client_auth)
    try:
        yield
    finally:
        server.stop(0)
        for key in ("PRIVACY_TLS_ENABLED", "PRIVACY_TLS_CERT_FILE", "PRIVACY_TLS_KEY_FILE", "PRIVACY_TLS_CLIENT_AUTH", "PRIVACY_TLS_CA_FILE"):
            os.environ.pop(key, None)


def test_grpc_mtls_require_client_cert(tmp_path: Path):
    certs = generate_test_certs(tmp_path)
    port = 50052
    with grpc_tls_server(certs, port, client_auth="require"):
        # 无客户端证书，连接失败
        ca = certs["ca_cert"].read_bytes()
        creds = grpc.ssl_channel_credentials(root_certificates=ca)
        with pytest.raises(grpc.FutureTimeoutError):
            with grpc.secure_channel(f"127.0.0.1:{port}", creds) as channel:
                grpc.channel_ready_future(channel).result(timeout=3)

        # 携带受信客户端证书，调用成功
        creds = grpc.ssl_channel_credentials(
            root_certificates=ca,
            private_key=certs["client_key"].read_bytes(),
            certificate_chain=certs["client_cert"].read_bytes(),
        )
        with grpc.secure_channel(f"127.0.0.1:{port}", creds) as channel:
            stub = privacy_pb2_grpc.PrivacyServiceStub(channel)
            resp = stub.Health(privacy_pb2.HealthRequest())
            assert resp.status == "ok"
```

## 5. 测试执行命令

```bash
# 运行全部安全相关测试
PYTHONPATH=. pytest tests/test_security_*.py -v

# 单独运行认证测试
PYTHONPATH=. pytest tests/test_security_auth.py -v

# 单独运行 TLS 测试
PYTHONPATH=. pytest tests/test_security_tls.py -v

# 单独运行限速测试
PYTHONPATH=. pytest tests/test_security_rate_limit.py -v
```

## 6. 持续集成建议

- 每次提交前执行 `pytest tests/test_security_*.py`。
- CI 中安装 `cryptography`（已通过项目依赖引入）。
- TLS 集成测试使用动态临时端口，避免与本地服务冲突。
- 限速测试使用远高于默认值的 `burst`，减少测试运行时间抖动。

## 7. 验收检查清单

- [ ] 动态生成 CA/服务器/客户端证书并在测试中使用。
- [ ] 仅服务端 TLS 下，受信 CA 可连接，不受信 CA 失败。
- [ ] mTLS `require` 模式下，缺失客户端证书连接失败。
- [ ] 内部 API Key 可访问全部接口，外部 API Key 越权返回 403 / `PERMISSION_DENIED`。
- [ ] 缺失/无效凭证返回 401 / `UNAUTHENTICATED`。
- [ ] 超速调用返回 429 / `RESOURCE_EXHAUSTED`。
- [ ] `/health` 与 `Health` 默认免认证、不限速。
- [ ] 关闭安全开关后既有测试集无需修改即可通过。
