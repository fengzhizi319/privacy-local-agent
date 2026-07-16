"""Deployment artifact validation tests.

验证 Helm chart 与原生 K8s manifests 的语法正确性，不依赖实际集群。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
HELM_DIR = PROJECT_ROOT / "deploy" / "helm" / "privacy-local-agent"
K8S_DIR = PROJECT_ROOT / "deploy" / "k8s"

HELM = shutil.which("helm")


@pytest.mark.skipif(HELM is None, reason="helm not found in PATH")
def test_helm_lint() -> None:
    """helm lint must pass for the chart."""
    result = subprocess.run(
        [HELM, "lint", str(HELM_DIR)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(HELM is None, reason="helm not found in PATH")
def test_helm_template_default_values() -> None:
    """helm template must render valid YAML documents."""
    result = subprocess.run(
        [HELM, "template", "test", str(HELM_DIR)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    # Verify at least one YAML document parses.
    docs = list(yaml.safe_load_all(result.stdout))
    assert any(doc for doc in docs)


@pytest.mark.skipif(HELM is None, reason="helm not found in PATH")
def test_helm_template_production_values() -> None:
    """Production values with TLS/auth must render valid YAML."""
    values_file = HELM_DIR / "values-production.yaml"
    result = subprocess.run(
        [
            HELM,
            "template",
            "prod",
            str(HELM_DIR),
            "-f",
            str(values_file),
            "--set",
            "security.tls.existingSecret=tls-secret",
            "--set",
            "security.auth.apiKeysSecret=keys-secret",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    docs = list(yaml.safe_load_all(result.stdout))
    # Find Deployment and assert TLS/auth env vars are present.
    deployment = next(
        (d for d in docs if isinstance(d, dict) and d.get("kind") == "Deployment"),
        None,
    )
    assert deployment is not None
    containers = deployment["spec"]["template"]["spec"]["containers"]
    env_names = {
        env["name"]
        for c in containers
        for env in c.get("env", [])
    }
    assert "PRIVACY_TLS_ENABLED" in env_names
    assert "PRIVACY_AUTH_ENABLED" in env_names


def test_k8s_manifests_are_valid_yaml() -> None:
    """All files in deploy/k8s must be valid YAML."""
    for path in K8S_DIR.glob("*.yaml"):
        # Skip example secrets and kustomization; they are still valid YAML though.
        with path.open("r", encoding="utf-8") as f:
            content = f.read()
        # kustomization.yaml may be empty-ish; safe_load_all handles it.
        docs = list(yaml.safe_load_all(content))
        # Ensure at least one non-None document.
        assert any(d is not None for d in docs), f"{path} contains no documents"
