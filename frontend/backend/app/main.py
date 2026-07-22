"""FastAPI backend for the privacy test console.

Serves the built React SPA and proxies requests to a running privacy-local-agent
instance via REST. Endpoint samples are provided in `fixtures/samples.py`.
"""

from __future__ import annotations

import base64
import time
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .client import agent_client
from .config import settings
from .fixtures.samples import get_samples


class ProxyRequest(BaseModel):
    """Request body for the generic proxy endpoint."""

    method: str = Field(..., examples=["POST"])
    path: str = Field(..., examples=["/v1/privacy/mask"])
    body: Optional[Dict[str, Any]] = Field(default=None)
    raw_payload_b64: Optional[str] = Field(default=None)
    content_type: Optional[str] = Field(default=None)


class ProxyResponse(BaseModel):
    """Response wrapper returned by the generic proxy endpoint."""

    status: int
    duration_ms: float
    data: Any


class BatchRequestItem(BaseModel):
    """One request inside a batch."""

    method: str = Field(default="POST")
    path: str
    body: Optional[Dict[str, Any]] = Field(default=None)


class BatchRequest(BaseModel):
    """Request body for the batch proxy endpoint."""

    requests: List[BatchRequestItem] = Field(default_factory=list)


class BatchResultItem(BaseModel):
    """Outcome of a single request within a batch."""

    method: str
    path: str
    status: int
    duration_ms: float
    data: Any = None
    error: Optional[str] = None


class BatchResponse(BaseModel):
    """Aggregated result of a batch run."""

    total: int
    passed: int
    failed: int
    results: List[BatchResultItem]


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: ensure HTTP client is created at startup."""
    _ = await agent_client._get_client()
    yield
    if agent_client._client is not None:
        await agent_client._client.aclose()


app = FastAPI(title="Privacy Test Console", lifespan=lifespan)

# Allow the Vite dev server to call the backend during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health():
    """Check that the backend is up and that the agent is reachable."""
    start = time.perf_counter()
    try:
        agent_health = await agent_client.request("GET", "/health")
        duration_ms = (time.perf_counter() - start) * 1000
        return {
            "backend": "ok",
            "agent": agent_health,
            "agent_url": settings.privacy_agent_url,
            "latency_ms": round(duration_ms, 2),
        }
    except HTTPException as exc:
        return JSONResponse(
            status_code=200,
            content={
                "backend": "ok",
                "agent": "unreachable",
                "agent_url": settings.privacy_agent_url,
                "error": exc.detail,
            },
        )


@app.get("/api/samples")
async def samples():
    """Return all endpoint samples grouped by category."""
    return {"samples": get_samples()}


@app.post("/api/proxy")
async def proxy(req: ProxyRequest):
    """Forward a request to the privacy-local-agent REST server.

    The proxy transparently handles JSON and binary (Arrow IPC) payloads.
    """
    method = req.method.upper()
    path = req.path

    raw_content: Optional[bytes] = None
    if req.raw_payload_b64:
        try:
            raw_content = base64.b64decode(req.raw_payload_b64)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid base64 payload: {exc}") from exc

    start = time.perf_counter()
    try:
        result = await agent_client.request(
            method=method,
            path=path,
            body=req.body,
            raw_content=raw_content,
            content_type=req.content_type,
        )
    except HTTPException as exc:
        # Re-raise as HTTPException so FastAPI returns the right status/detail.
        raise
    duration_ms = (time.perf_counter() - start) * 1000

    return ProxyResponse(status=200, duration_ms=round(duration_ms, 2), data=result)


@app.post("/api/batch")
async def batch(req: BatchRequest):
    """Run a list of requests against the agent sequentially.

    用于前端“一键批量测试”：逐个转发请求并汇总成功 / 失败统计，
    单个请求失败不会中断整个批次。
    """
    results: List[BatchResultItem] = []
    for item in req.requests:
        method = item.method.upper()
        start = time.perf_counter()
        try:
            data = await agent_client.request(method=method, path=item.path, body=item.body)
            duration_ms = (time.perf_counter() - start) * 1000
            results.append(
                BatchResultItem(
                    method=method,
                    path=item.path,
                    status=200,
                    duration_ms=round(duration_ms, 2),
                    data=data,
                )
            )
        except HTTPException as exc:
            duration_ms = (time.perf_counter() - start) * 1000
            results.append(
                BatchResultItem(
                    method=method,
                    path=item.path,
                    status=exc.status_code,
                    duration_ms=round(duration_ms, 2),
                    error=str(exc.detail),
                )
            )
        except Exception as exc:  # noqa: BLE001 - 批量执行需吸收单个请求的任何异常
            duration_ms = (time.perf_counter() - start) * 1000
            results.append(
                BatchResultItem(
                    method=method,
                    path=item.path,
                    status=500,
                    duration_ms=round(duration_ms, 2),
                    error=str(exc),
                )
            )

    passed = sum(1 for r in results if 200 <= r.status < 300)
    return BatchResponse(total=len(results), passed=passed, failed=len(results) - passed, results=results)


# Static SPA serving: mount the built frontend at root. If the directory does not
# exist (e.g. during backend-only development), the app still works.
static_dir = settings.static_dist_dir.resolve()
if static_dir.exists() and static_dir.is_dir():
    app.mount("/assets", StaticFiles(directory=str(static_dir / "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(request: Request, full_path: str):
        """Serve the SPA index.html for every non-API route."""
        index_file = static_dir / "index.html"
        if index_file.exists():
            return FileResponse(str(index_file))
        raise HTTPException(status_code=404, detail="Frontend not built")


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Return structured errors so the frontend can display them nicely."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "status": exc.status_code},
    )
