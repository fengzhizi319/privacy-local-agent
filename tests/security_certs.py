"""Helpers to generate ephemeral CA/server/client certificates for security tests.

测试专用的自签名证书生成工具，避免在仓库中提交真实证书。
"""

from __future__ import annotations

import datetime
import ipaddress
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def _generate_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _write_pem(path: Path, data: bytes) -> None:
    path.write_bytes(data)


def _make_ca(name: str, key: rsa.RSAPrivateKey) -> x509.Certificate:
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1))
        .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1))
        .add_extension(
            x509.BasicConstraints(ca=True, path_length=None),
            critical=True,
        )
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
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    return cert


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
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None),
            critical=True,
        )
    )
    if san:
        builder = builder.add_extension(x509.SubjectAlternativeName(san), critical=False)
    if extended_key_usage:
        builder = builder.add_extension(
            x509.ExtendedKeyUsage(extended_key_usage), critical=False
        )
    builder = builder.add_extension(
        x509.SubjectKeyIdentifier.from_public_key(subject_key.public_key()),
        critical=False,
    )
    builder = builder.add_extension(
        x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()),
        critical=False,
    )
    return builder.sign(issuer_key, hashes.SHA256())


def generate_test_certs(tmp_dir: Path) -> dict[str, Path]:
    """Generate a trusted CA, server cert, client cert and an untrusted CA/server cert.

    Returns a dictionary of PEM file paths.
    """
    tmp_dir = Path(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    # Trusted CA
    ca_key = _generate_key()
    ca_cert = _make_ca("privacy-local-agent-test-ca", ca_key)

    # Server cert for localhost / 127.0.0.1
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

    # Client cert
    client_key = _generate_key()
    client_cert = _make_end_entity(
        "internal-client",
        client_key,
        ca_cert,
        ca_key,
        extended_key_usage=[ExtendedKeyUsageOID.CLIENT_AUTH],
    )

    # Untrusted CA + server cert (negative tests)
    bad_ca_key = _generate_key()
    bad_ca_cert = _make_ca("untrusted-test-ca", bad_ca_key)
    bad_server_key = _generate_key()
    bad_server_cert = _make_end_entity(
        "localhost",
        bad_server_key,
        bad_ca_cert,
        bad_ca_key,
        san=[x509.DNSName("localhost")],
        extended_key_usage=[ExtendedKeyUsageOID.SERVER_AUTH],
    )

    paths: dict[str, Path] = {
        "ca_cert": tmp_dir / "ca.crt",
        "server_cert": tmp_dir / "server.crt",
        "server_key": tmp_dir / "server.key",
        "client_cert": tmp_dir / "client.crt",
        "client_key": tmp_dir / "client.key",
        "bad_ca_cert": tmp_dir / "bad_ca.crt",
        "bad_server_cert": tmp_dir / "bad_server.crt",
        "bad_server_key": tmp_dir / "bad_server.key",
    }

    _write_pem(paths["ca_cert"], ca_cert.public_bytes(serialization.Encoding.PEM))
    _write_pem(
        paths["server_cert"], server_cert.public_bytes(serialization.Encoding.PEM)
    )
    _write_pem(
        paths["server_key"],
        server_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ),
    )
    _write_pem(
        paths["client_cert"], client_cert.public_bytes(serialization.Encoding.PEM)
    )
    _write_pem(
        paths["client_key"],
        client_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ),
    )
    _write_pem(
        paths["bad_ca_cert"], bad_ca_cert.public_bytes(serialization.Encoding.PEM)
    )
    _write_pem(
        paths["bad_server_cert"],
        bad_server_cert.public_bytes(serialization.Encoding.PEM),
    )
    _write_pem(
        paths["bad_server_key"],
        bad_server_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ),
    )

    return paths
