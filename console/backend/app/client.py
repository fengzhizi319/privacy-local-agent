"""privacy-local-agent REST 接口的 HTTP 代理客户端。

本模块是控制台后端与 agent 通信的唯一出口：
    - 维护一个应用级单例的 ``httpx.AsyncClient`` 连接池，复用 TCP 连接；
    - 统一处理 JSON / Arrow IPC / 其他二进制三类响应的解析；
    - 把下游的网络异常、HTTP 错误状态码转换为 :class:`HTTPException`，
      交由 FastAPI 统一返回给前端。

全局单例 :data:`agent_client` 在 :mod:`app.main` 的 lifespan 中预热与释放。
"""

from __future__ import annotations

import base64
import io
from typing import Any

import httpx
from fastapi import HTTPException

from .config import settings


class PrivacyAgentClient:
    """转发请求到 privacy-local-agent 的轻量异步客户端。

    设计为应用级单例（见模块底部 :data:`agent_client`），内部懒初始化
    ``httpx.AsyncClient`` 以复用连接池，避免每次请求重建连接的开销。
    """

    def __init__(self) -> None:
        # agent REST 基地址（去掉尾部斜杠，便于拼接 path）
        self.base_url = settings.privacy_agent_url.rstrip("/")
        # 可选的认证 API Key（agent 开启 auth 时才需要）
        self.api_key = settings.privacy_agent_api_key
        # 懒初始化的异步 HTTP 客户端（连接池）
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """获取（必要时创建）底层 ``httpx.AsyncClient``。

        客户端未创建或已关闭时重建，保证连接池有效。
        """
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=60.0,
                # 不跟随重定向：避免重定向被用于绕过限制 / 放大 SSRF。
                follow_redirects=False,
                # 不读取环境变量 / macOS 系统代理配置：
                # 控制台与本地 agent 通信必须直连，否则系统代理
                # （如 Clash 等工具设置的 127.0.0.1:7897）会导致
                # "All connection attempts failed" 连接失败。
                trust_env=False,
            )
        return self._client

    def _headers(self) -> dict[str, str]:
        """构造请求头：配置了 API Key 时附加 ``Authorization: Bearer``。"""
        headers: dict[str, str] = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    @staticmethod
    def _parse_arrow_response(response: httpx.Response) -> dict[str, Any]:
        """解析 Arrow IPC 流响应，返回记录列表 + schema 元数据。

        agent 的 ``/v1/privacy/dp/arrow_ipc`` 等端点返回二进制 Arrow 流，
        前端无法直接展示，这里将其转换为 JSON 友好的结构：
            - ``metadata``：schema 级元数据（bytes 键值解码为 str）；
            - ``records``：表格数据转为记录列表，NaN 替换为 None。
        """
        # 延迟导入 pyarrow：避免在未用到 Arrow 的场景下引入重量级依赖。
        import pyarrow as pa

        # 把响应体（二进制 Arrow 流）包装为 BytesIO 并读取为 Table。
        table = pa.ipc.open_stream(io.BytesIO(response.content)).read_all()
        # 提取 schema 级元数据（可能为空）。
        metadata = {}
        if table.schema.metadata:
            # Arrow 元数据的键值均为 bytes，统一解码为 str 以便 JSON 序列化。
            metadata = {
                k.decode() if isinstance(k, bytes) else k: v.decode() if isinstance(v, bytes) else v
                for k, v in table.schema.metadata.items()
            }

        # 返回 JSON 友好结构：内容类型标记 + 元数据 + 记录列表。
        return {
            "_content_type": "application/vnd.apache.arrow.stream",
            "metadata": metadata,
            # 表格转 pandas 再转记录列表；NaN 替换为 None（JSON 无 NaN）。
            "records": table.to_pandas().replace({float("nan"): None}).to_dict(orient="records"),
        }

    async def request(
        self,
        method: str,
        path: str,
        body: Any | None = None,
        raw_content: bytes | None = None,
        content_type: str | None = None,
    ) -> Any:
        """转发一个请求到 privacy agent 并返回其响应。

        参数优先级：``raw_content``（二进制）> ``body``（JSON）> 无请求体。

        响应解析策略（按 Content-Type 区分）：
            - Arrow IPC 流 → 解析为记录 + 元数据（见 :meth:`_parse_arrow_response`）；
            - JSON → 解析为 Python 对象；
            - 其他二进制 → base64 编码后返回，便于前端展示。

        异常处理：
            - 网络层错误（连不上、超时等）→ 502 Bad Gateway；
            - agent 返回非 2xx → 透传原状态码与 ``detail``。
        """
        # 获取（必要时重建）底层连接池客户端。
        client = await self._get_client()
        # 拼接完整目标 URL（base_url + path）。
        url = f"{self.base_url}{path}"
        # 构造请求头（可能携带 Bearer 认证）。
        headers = self._headers()

        try:
            if raw_content is not None:
                # 二进制载荷：显式设置 Content-Type（兑底 octet-stream），
                # 以 content= 传递原始字节。
                headers["Content-Type"] = content_type or "application/octet-stream"
                response = await client.request(method, url, content=raw_content, headers=headers)
            elif body is not None:
                # JSON 载荷：httpx 自动序列化并设置 application/json。
                response = await client.request(method, url, json=body, headers=headers)
            else:
                # 无请求体（如 GET 请求）。
                response = await client.request(method, url, headers=headers)
        except httpx.RequestError as exc:
            # 网络层错误（连不上 / 超时等）：包装为 502 Bad Gateway。
            raise HTTPException(
                status_code=502, detail=f"Unable to reach privacy agent: {exc}"
            ) from exc

        try:
            # 状态码检查：非 2xx 抛出 HTTPStatusError。
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # 非 2xx：提取 agent 返回的 detail 并透传状态码，
            # 让前端能看到与直连 agent 一致的错误信息。
            detail = self._extract_detail(response)
            raise HTTPException(status_code=response.status_code, detail=detail) from exc

        # 根据响应 Content-Type 选择解析方式。
        ct = response.headers.get("content-type", "application/json")
        if "application/vnd.apache.arrow.stream" in ct:
            # Arrow IPC 流：解析为记录 + 元数据。
            return self._parse_arrow_response(response)
        if "application/json" in ct:
            # JSON：直接反序列化为 Python 对象。
            return response.json()

        # 其他二进制内容的兑底处理：base64 编码，让前端能安全展示。
        return {
            "_content_type": ct,
            "_base64": base64.b64encode(response.content).decode("ascii"),
        }

    async def request_multipart(
        self,
        path: str,
        files: dict[str, Any],
        data: dict[str, Any] | None = None,
    ) -> Any:
        """以 multipart/form-data 转发请求到 privacy agent 并返回其响应。

        用于文件上传类请求的转发（如 ``/v1/privacy/process_file``）：
        前端把文件上传到控制台后端，后端再以 multipart 透传给 agent。

        Args:
            path: 目标 agent 路径。
            files: httpx files 映射（如 ``{"file": (filename, content, content_type)}``）。
            data: 随附的表单字段（如 ``operation`` / ``params``）。

        异常处理与 :meth:`request` 一致：网络错误 → 502，agent 非 2xx → 透传。
        """
        # 获取（必要时重建）底层连接池客户端。
        client = await self._get_client()
        # 拼接完整目标 URL。
        url = f"{self.base_url}{path}"
        # 构造请求头（可能携带 Bearer 认证）。
        headers = self._headers()

        try:
            # 以 multipart/form-data 发送：files 为文件字段，
            # data 为随附表单字段（httpx 自动构造 multipart 边界）。
            response = await client.post(url, files=files, data=data or {}, headers=headers)
        except httpx.RequestError as exc:
            # 网络层错误：包装为 502 Bad Gateway。
            raise HTTPException(
                status_code=502, detail=f"Unable to reach privacy agent: {exc}"
            ) from exc

        try:
            # 状态码检查：非 2xx 抛出 HTTPStatusError。
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # 非 2xx：提取 detail 并透传状态码。
            detail = self._extract_detail(response)
            raise HTTPException(status_code=response.status_code, detail=detail) from exc

        # 根据响应 Content-Type 选择解析方式。
        ct = response.headers.get("content-type", "application/json")
        if "application/json" in ct:
            # JSON：直接反序列化。
            return response.json()
        # 非 JSON 响应的兑底：base64 编码后返回。
        return {
            "_content_type": ct,
            "_base64": base64.b64encode(response.content).decode("ascii"),
        }

    @staticmethod
    def _extract_detail(response: httpx.Response) -> str:
        """从错误响应中提取可读的错误描述。

        优先取 JSON 体中的 ``detail`` 字段（FastAPI 规范）；解析失败时
        降级为原始文本或 HTTP reason phrase，保证始终有可读信息。
        """
        try:
            # 尝试按 JSON 解析响应体。
            data = response.json()
            if isinstance(data, dict) and "detail" in data:
                # FastAPI 规范：错误信息放在 detail 字段。
                return str(data["detail"])
            # 是 JSON 但无 detail 字段：直接字符串化整个体。
            return str(data)
        except Exception:
            # 解析失败（非 JSON 响应）：降级为原始文本或 HTTP reason phrase。
            return response.text or response.reason_phrase


# 应用级单例：整个后端共享同一个客户端（连接池），
# 由 :mod:`app.main` 的 lifespan 负责预热与优雅关闭。
agent_client = PrivacyAgentClient()
