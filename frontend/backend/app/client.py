"""HTTP client proxy for privacy-local-agent REST endpoints."""

from __future__ import annotations

import base64
import io
from typing import Any, Dict, Optional

import httpx
from fastapi import HTTPException

from .config import settings


class PrivacyAgentClient:
    """Thin async client that forwards requests to privacy-local-agent."""

    def __init__(self) -> None:
        self.base_url = settings.privacy_agent_url.rstrip("/")
        self.api_key = settings.privacy_agent_api_key
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=60.0,
                follow_redirects=True,
                # 不读取环境变量 / macOS 系统代理配置：
                # 控制台与本地 agent 通信必须直连，否则系统代理
                # （如 Clash 等工具设置的 127.0.0.1:7897）会导致
                # "All connection attempts failed" 连接失败。
                trust_env=False,
            )
        return self._client

    def _headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    @staticmethod
    def _parse_arrow_response(response: httpx.Response) -> Dict[str, Any]:
        """Parse an Arrow IPC stream and return records + metadata."""
        import pyarrow as pa

        table = pa.ipc.open_stream(io.BytesIO(response.content)).read_all()
        metadata = {}
        if table.schema.metadata:
            metadata = {k.decode() if isinstance(k, bytes) else k: v.decode() if isinstance(v, bytes) else v for k, v in table.schema.metadata.items()}

        return {
            "_content_type": "application/vnd.apache.arrow.stream",
            "metadata": metadata,
            "records": table.to_pandas().replace({float("nan"): None}).to_dict(orient="records"),
        }

    async def request(
        self,
        method: str,
        path: str,
        body: Optional[Any] = None,
        raw_content: Optional[bytes] = None,
        content_type: Optional[str] = None,
    ) -> Any:
        """Forward a request to the privacy agent and return its response.

        JSON responses are parsed to Python objects. Arrow IPC responses are parsed
        into records + metadata. Other binary responses are returned as base64.
        """
        client = await self._get_client()
        url = f"{self.base_url}{path}"
        headers = self._headers()

        try:
            if raw_content is not None:
                headers["Content-Type"] = content_type or "application/octet-stream"
                response = await client.request(method, url, content=raw_content, headers=headers)
            elif body is not None:
                response = await client.request(method, url, json=body, headers=headers)
            else:
                response = await client.request(method, url, headers=headers)
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=502, detail=f"Unable to reach privacy agent: {exc}"
            ) from exc

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = self._extract_detail(response)
            raise HTTPException(status_code=response.status_code, detail=detail) from exc

        ct = response.headers.get("content-type", "application/json")
        if "application/vnd.apache.arrow.stream" in ct:
            return self._parse_arrow_response(response)
        if "application/json" in ct:
            return response.json()

        # Fallback for other binary content: base64 encode so the UI can display it.
        return {
            "_content_type": ct,
            "_base64": base64.b64encode(response.content).decode("ascii"),
        }

    @staticmethod
    def _extract_detail(response: httpx.Response) -> str:
        try:
            data = response.json()
            if isinstance(data, dict) and "detail" in data:
                return str(data["detail"])
            return str(data)
        except Exception:  # noqa: BLE001
            return response.text or response.reason_phrase


agent_client = PrivacyAgentClient()
