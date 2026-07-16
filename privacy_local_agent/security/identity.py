"""Identity model and endpoint-permission mappings.

定义调用者身份、服务类型、权限 scope，以及 REST 路径 / gRPC 方法到权限的映射。
Defines the caller Identity, service types, scopes, and permission mapping for REST
paths and gRPC methods.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Identity:
    """Authenticated caller identity.

    Attributes:
        service_type: "internal" for high-trust services, "external" for untrusted/public clients.
        name: Service or account name, used in logs and rate-limit keys.
        scopes: Granted permission strings. ["*"] means full access.
    """

    service_type: Literal["internal", "external"]
    name: str
    scopes: list[str]

    def has_permission(self, permission: str) -> bool:
        """Check whether this identity is allowed to perform ``permission``.

        Wildcard scope "*" grants all permissions. Scope matching is exact; no
        globbing or hierarchy is supported at P0.
        """
        return "*" in self.scopes or permission in self.scopes


# Default anonymous identity used when authentication is disabled.
ANONYMOUS_IDENTITY = Identity("internal", "anonymous", ["*"])


def permission_for_rest_path(path: str) -> str:
    """Map a REST path to the required permission string.

    Paths are matched by prefix where it makes sense. Unknown paths require a
    generic wildcard permission for safety.
    """
    path = path.rstrip("/")
    if path in ("/health", "/livez", "/readyz"):
        return "health:read"
    if path in ("/v1/privacy/mask", "/v1/privacy/mask_record"):
        return "privacy:mask"
    if path == "/v1/privacy/hash":
        return "privacy:hash"
    if path.startswith("/v1/privacy/dp/"):
        return "privacy:dp"
    if path == "/v1/privacy/k_anonymize/record":
        return "privacy:kano"
    if path == "/v1/privacy/qol/obfuscate":
        return "privacy:qol"
    if path == "/v1/privacy/budget":
        return "privacy:budget"
    if path == "/v1/privacy/profile/recommend":
        return "privacy:profile"
    if path.startswith("/v1/privacy/classify/"):
        return "classification:read"
    # Conservative default for unknown routes.
    return "*"


def permission_for_grpc_method(method: str) -> str:
    """Map a gRPC full method name (e.g. '/privacy.local.PrivacyService/Mask') to a permission."""
    # Strip the package/service prefix if present.
    short = method.split("/")[-1] if "/" in method else method
    mapping = {
        "Mask": "privacy:mask",
        "MaskRecord": "privacy:mask",
        "Hash": "privacy:hash",
        "DPCount": "privacy:dp",
        "DPSum": "privacy:dp",
        "DPMean": "privacy:dp",
        "KAnonymizeRecord": "privacy:kano",
        "ObfuscateQuery": "privacy:qol",
        "ClassifyField": "classification:read",
        "ClassifyRecord": "classification:read",
        "ClassifyTable": "classification:read",
        "Health": "health:read",
        "RecommendParams": "privacy:profile",
    }
    return mapping.get(short, "*")


def is_health_path_or_method(path_or_method: str) -> bool:
    """Return True if the given REST path or gRPC method is a health probe."""
    normalized = path_or_method.rstrip("/")
    return normalized in ("/health", "/livez", "/readyz") or normalized.endswith("/Health")
