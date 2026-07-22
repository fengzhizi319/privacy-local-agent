"""烟雾测试：通过后端代理逐个调用所有示例端点。

运行前提：后端（8080）与 privacy-local-agent 均已启动：

    cd frontend/backend
    source .venv/bin/activate
    python smoke_test.py

设计说明：
    - 逐个遍历 :func:`get_samples` 返回的示例，经 ``/api/proxy`` 转发；
    - 非 200 响应只打印不中断，避免局部环境问题（如缺少 LLM 模型）
      掩盖其他端点的真实结果；
    - 需要预存运行时资源的端点（异步任务查询、复核确认）会被跳过，
      因为它们依赖真实 ID，需在 UI 中手动验证。
    - 全部通过时退出码为 0，有失败时为 1（便于 CI 集成）。
"""

from __future__ import annotations

import sys
from typing import Any, Dict, List, Tuple

import httpx

from app.fixtures.samples import get_samples

BASE = "http://127.0.0.1:8080"


def send(method: str, path: str, body: Any = None, raw_b64: str = None, content_type: str = None) -> Tuple[int, Any]:
    """构造代理请求并发送到 ``/api/proxy``，返回（状态码，响应体）。"""
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
    """主流程：遍历示例逐个调用，统计并打印通过 / 失败数，返回退出码。"""
    samples = get_samples()
    passed = 0
    failed = 0

    for sample in samples:
        label = f"{sample['method']} {sample['path']} ({sample['label']})"
        body = sample.get("body")
        raw_b64 = sample.get("rawPayloadB64")
        content_type = sample.get("contentType")

        # 需要预存运行时资源的示例在自动化烟雾测试中跳过；
        # 它们依赖真实 ID，需在 UI 中手动验证。
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
