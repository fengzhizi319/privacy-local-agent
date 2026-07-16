"""生产安全加固使用示例。

本脚本演示如何：
1. 动态生成自签名 CA、服务器证书与客户端证书（无需提前准备真实证书）。
2. 通过环境变量构造 SecuritySettings。
3. 使用 security 模块提供的 TLS/Auth/RateLimit 工具。
4. 模拟认证与鉴权逻辑。

运行方式：
    source .venv/bin/activate
    PYTHONPATH=. python docs/production_security/examples/security_usage.py
"""

from __future__ import annotations

import datetime
import ipaddress
import os
import tempfile
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from privacy_local_agent.security.config import SecuritySettings, get_security_settings
from privacy_local_agent.security.identity import Identity, permission_for_rest_path
from privacy_local_agent.security.ratelimit import Limiter
from privacy_local_agent.security.tls import uvicorn_ssl_kwargs


def _extract_bearer_token(header_value: str | None) -> str | None:
    """从 Authorization header 中提取 Bearer token。"""
    if not header_value:
        return None
    parts = header_value.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return None


def _authenticate_api_key(settings: SecuritySettings, token: str):
    """在 internal_keys 与 external_keys 中查找 token 对应的 Identity。"""
    internal = settings.internal_keys.get(token)
    if internal:
        return Identity("internal", internal.name, internal.scopes)
    external = settings.external_keys.get(token)
    if external:
        return Identity("external", external.name, external.scopes)
    return None


def _generate_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _write_pem(path: Path, data: bytes) -> None:
    path.write_bytes(data)


def _make_ca(name: str, key: rsa.RSAPrivateKey) -> x509.Certificate:
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    return (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1))
        .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_cert_sign=True,
                crl_sign=True,
                content_commitment=False,
                data_encipherment=False,
                decipher_only=False,
                encipher_only=False,
                key_agreement=False,
                key_encipherment=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )


def _make_end_entity(
    cn: str,
    subject_key: rsa.RSAPrivateKey,
    issuer_cert: x509.Certificate,
    issuer_key: rsa.RSAPrivateKey,
    *,
    san: list[x509.GeneralName] | None = None,
    extended_key_usage: list[x509.ObjectIdentifier] | None = None,
) -> x509.Certificate:
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer_cert.subject)
        .public_key(subject_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1))
        .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
    )
    if san:
        builder = builder.add_extension(x509.SubjectAlternativeName(san), critical=False)
    if extended_key_usage:
        builder = builder.add_extension(x509.ExtendedKeyUsage(extended_key_usage), critical=False)
    builder = builder.add_extension(
        x509.SubjectKeyIdentifier.from_public_key(subject_key.public_key()), critical=False
    )
    builder = builder.add_extension(
        x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()), critical=False
    )
    return builder.sign(issuer_key, hashes.SHA256())


def generate_self_signed_certs(tmp_dir: Path) -> dict[str, Path]:
    """生成 CA、服务器、客户端证书链，返回 PEM 文件路径。"""
    tmp_dir = Path(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    ca_key = _generate_key()
    ca_cert = _make_ca("privacy-local-agent-example-ca", ca_key)

    server_key = _generate_key()
    server_cert = _make_end_entity(
        "localhost",
        server_key,
        ca_cert,
        ca_key,
        san=[
            x509.DNSName("localhost"),
            x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
        ],
        extended_key_usage=[ExtendedKeyUsageOID.SERVER_AUTH],
    )

    client_key = _generate_key()
    client_cert = _make_end_entity(
        "internal-client",
        client_key,
        ca_cert,
        ca_key,
        extended_key_usage=[ExtendedKeyUsageOID.CLIENT_AUTH],
    )

    paths: dict[str, Path] = {
        "ca_cert": tmp_dir / "ca.crt",
        "server_cert": tmp_dir / "server.crt",
        "server_key": tmp_dir / "server.key",
        "client_cert": tmp_dir / "client.crt",
        "client_key": tmp_dir / "client.key",
    }

    _write_pem(paths["ca_cert"], ca_cert.public_bytes(serialization.Encoding.PEM))
    _write_pem(paths["server_cert"], server_cert.public_bytes(serialization.Encoding.PEM))
    _write_pem(
        paths["server_key"],
        server_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ),
    )
    _write_pem(paths["client_cert"], client_cert.public_bytes(serialization.Encoding.PEM))
    _write_pem(
        paths["client_key"],
        client_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ),
    )

    return paths


def main():
    with tempfile.TemporaryDirectory() as tmp_dir:
        certs = generate_self_signed_certs(Path(tmp_dir))
        print("已生成测试证书：")
        for name, path in certs.items():
            print(f"  {name}: {path}")

        # 1. 构造 SecuritySettings（模拟生产环境变量）
        os.environ.update(
            {
                "PRIVACY_TLS_ENABLED": "true",
                "PRIVACY_TLS_CERT_FILE": str(certs["server_cert"]),
                "PRIVACY_TLS_KEY_FILE": str(certs["server_key"]),
                "PRIVACY_TLS_CA_FILE": str(certs["ca_cert"]),
                "PRIVACY_TLS_CLIENT_AUTH": "require",
                "PRIVACY_AUTH_ENABLED": "true",
                "PRIVACY_AUTH_INTERNAL_KEYS_JSON": '{"sk-internal":{"name":"secretpad","scopes":["*"]}}',
                "PRIVACY_AUTH_EXTERNAL_KEYS_JSON": '{"sk-external":{"name":"portal","scopes":["privacy:mask","classification:read"]}}',
                "PRIVACY_RATE_LIMIT_ENABLED": "true",
                "PRIVACY_RATE_LIMIT_DEFAULT_RPS": "10",
                "PRIVACY_RATE_LIMIT_DEFAULT_BURST": "20",
                'PRIVACY_RATE_LIMIT_PER_ENDPOINT_JSON': '{"/v1/privacy/dp/count":{"rps":2,"burst":5}}',
            }
        )

        settings = get_security_settings()
        print("\nSecuritySettings 解析结果：")
        print(f"  tls_enabled={settings.tls_enabled}")
        print(f"  tls_client_auth={settings.tls_client_auth}")
        print(f"  auth_enabled={settings.auth_enabled}")
        print(f"  internal_keys={list(settings.internal_keys.keys())}")
        print(f"  external_keys={list(settings.external_keys.keys())}")
        print(f"  rate_limit_enabled={settings.rate_limit_enabled}")

        # 2. 构造 Uvicorn SSL 参数
        ssl_kwargs = uvicorn_ssl_kwargs(settings)
        print("\nUvicorn SSL 参数：")
        for key, value in ssl_kwargs.items():
            print(f"  {key}: {value}")

        # 3. 模拟 API Key 认证
        print("\n模拟 API Key 认证：")
        internal_identity = _authenticate_api_key(settings, "sk-internal")
        print(f"  sk-internal -> {internal_identity}")

        external_identity = _authenticate_api_key(settings, "sk-external")
        print(f"  sk-external -> {external_identity}")

        invalid_identity = _authenticate_api_key(settings, "invalid-token")
        print(f"  invalid-token -> {invalid_identity}")

        # 4. 模拟 Authorization header 解析
        print("\n模拟 Authorization 解析：")
        token = _extract_bearer_token("Bearer sk-internal")
        print(f"  'Bearer sk-internal' -> {token}")
        token = _extract_bearer_token("Basic sk-internal")
        print(f"  'Basic sk-internal' -> {token}")

        # 5. 模拟权限校验
        print("\n模拟接口权限校验：")
        permission = permission_for_rest_path("/v1/privacy/dp/count")
        print(f"  /v1/privacy/dp/count 需要权限: {permission}")
        print(f"  内部服务是否允许: {internal_identity.has_permission(permission)}")
        print(f"  外部服务是否允许: {external_identity.has_permission(permission)}")

        permission = permission_for_rest_path("/v1/privacy/mask")
        print(f"  /v1/privacy/mask 需要权限: {permission}")
        print(f"  外部服务是否允许: {external_identity.has_permission(permission)}")

        # 6. 模拟速率限制
        print("\n模拟速率限制（默认 10 rps / 20 burst）：")
        limiter = Limiter(settings)
        identity = Identity("external", "portal", ["privacy:mask"])
        allowed = 0
        for _ in range(25):
            if limiter.is_allowed(identity, "/v1/privacy/mask"):
                allowed += 1
        print(f"  25 次请求中允许 {allowed} 次")

        # 清理环境变量
        for key in list(os.environ.keys()):
            if key.startswith("PRIVACY_"):
                del os.environ[key]


if __name__ == "__main__":
    main()
