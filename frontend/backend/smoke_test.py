"""Smoke test: exercise every sample endpoint via the backend proxy.

Run with the backend and privacy-local-agent already started:

    cd frontend/backend
    source .venv/bin/activate
    python smoke_test.py

Non-200 responses are printed but do not abort the script so that partial
environment issues (e.g. missing LLM models) do not mask every other result.
"""

from __future__ import annotations

import sys
from typing import Any, Dict, List, Tuple

import httpx

from app.fixtures.samples import get_samples

BASE = "http://127.0.0.1:8080"


def send(method: str, path: str, body: Any = None, raw_b64: str = None, content_type: str = None) -> Tuple[int, Any]:
    payload: Dict[str, Any] = {
        "method": method,
        "path": path,
        "body": body,
        "raw_payload_b64": raw_b64,
        "content_type": content_type,
    }
    r = httpx.post(f"{BASE}/api/proxy", json=payload, timeout=60.0)
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, r.text


def main() -> int:
    samples = get_samples()
    passed = 0
    failed = 0

    for sample in samples:
        label = f"{sample['method']} {sample['path']} ({sample['label']})"
        body = sample.get("body")
        raw_b64 = sample.get("rawPayloadB64")
        content_type = sample.get("contentType")

        # Samples that require pre-existing runtime resources are skipped in the
        # automated smoke test; exercise them in the UI with real IDs.
        if (
            sample["method"] == "GET" and sample["path"].startswith("/v1/privacy/classify/jobs/")
        ) or sample["path"].startswith("/v1/privacy/classify/review/confirm"):
            print(f"SKIP  {label}")
            continue

        status, data = send(sample["method"], sample["path"], body, raw_b64, content_type)
        if status == 200:
            print(f"OK    {label}")
            passed += 1
        else:
            detail = data.get("detail", data) if isinstance(data, dict) else data
            print(f"FAIL  {label} -> {status}: {detail}")
            failed += 1

    print(f"\nTotal: {passed} passed, {failed} failed out of {passed + failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
