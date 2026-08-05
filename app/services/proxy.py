"""HTTP transparent proxy to upstream AI APIs."""

from __future__ import annotations

import logging
import time
from typing import AsyncIterator, Mapping
import httpx
from fastapi import HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from app.config.settings import settings
from app.services.usage_logger import log_request

logger = logging.getLogger(__name__)

HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
    "content-encoding",
}


def _filtered_headers(headers: Mapping[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in headers.items():
        if key.lower() in HOP_BY_HOP:
            continue
        if key.lower().startswith("x-gateway-"):
            continue
        if key.lower() == "x-goog-api-key":
            continue
        out[key] = value
    return out


def _is_stream_request(path: str, query: str) -> bool:
    combined = f"{path}?{query}".lower()
    return "streamgeneratecontent" in combined or "alt=sse" in combined


class ProxyService:
    def __init__(self) -> None:
        limits = httpx.Limits(max_connections=200, max_keepalive_connections=50)
        self._client = httpx.AsyncClient(follow_redirects=False, limits=limits)

    async def close(self) -> None:
        await self._client.aclose()

    async def forward_gemini(self, request: Request, path: str) -> Response:
        if not settings.gemini_api_key.strip():
            raise HTTPException(status_code=503, detail="GEMINI_API_KEY is not configured")

        upstream_base = settings.gemini_base_url.rstrip("/")
        query = request.url.query
        url = f"{upstream_base}/{path.lstrip('/')}"
        if query:
            url = f"{url}?{query}"

        headers = _filtered_headers(request.headers)
        headers.pop("authorization", None)
        headers["x-goog-api-key"] = settings.gemini_api_key

        body = await request.body()
        service_id = request.headers.get("x-service-id", "")

        if _is_stream_request(path, query):
            return await self._stream(
                backend="gemini",
                service_id=service_id,
                method=request.method,
                path=f"/gemini/{path}",
                url=url,
                headers=headers,
                body=body,
            )

        return await self._buffered(
            backend="gemini",
            service_id=service_id,
            method=request.method,
            path=f"/gemini/{path}",
            url=url,
            headers=headers,
            body=body,
            timeout=settings.proxy_timeout_seconds,
        )

    async def forward_vertex(self, request: Request, path: str) -> Response:
        if not settings.vertex_enabled:
            raise HTTPException(status_code=503, detail="Vertex passthrough is disabled")
        if not settings.vertex_project_id.strip():
            raise HTTPException(status_code=503, detail="VERTEX_PROJECT_ID is not configured")

        location = settings.vertex_location.strip()
        upstream_base = f"https://{location}-aiplatform.googleapis.com"
        query = request.url.query
        url = f"{upstream_base}/{path.lstrip('/')}"
        if query:
            url = f"{url}?{query}"

        try:
            from app.services.vertex_auth import get_vertex_access_token

            token = get_vertex_access_token()
        except Exception as exc:
            logger.exception("Vertex token refresh failed")
            raise HTTPException(status_code=503, detail=f"Vertex auth failed: {exc}") from exc

        headers = _filtered_headers(request.headers)
        headers.pop("x-goog-api-key", None)
        headers["authorization"] = f"Bearer {token}"

        body = await request.body()
        service_id = request.headers.get("x-service-id", "")

        if _is_stream_request(path, query):
            return await self._stream(
                backend="vertex",
                service_id=service_id,
                method=request.method,
                path=f"/vertex/{path}",
                url=url,
                headers=headers,
                body=body,
            )

        return await self._buffered(
            backend="vertex",
            service_id=service_id,
            method=request.method,
            path=f"/vertex/{path}",
            url=url,
            headers=headers,
            body=body,
            timeout=settings.proxy_timeout_seconds,
        )

    async def _buffered(
        self,
        *,
        backend: str,
        service_id: str,
        method: str,
        path: str,
        url: str,
        headers: dict[str, str],
        body: bytes,
        timeout: float,
    ) -> Response:
        started = time.perf_counter()
        try:
            upstream = await self._client.request(
                method=method,
                url=url,
                headers=headers,
                content=body if body else None,
                timeout=timeout,
            )
        except httpx.TimeoutException as exc:
            raise HTTPException(status_code=504, detail="Upstream timeout") from exc
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"Upstream error: {exc}") from exc

        elapsed_ms = (time.perf_counter() - started) * 1000
        content = upstream.content
        log_body = content if upstream.status_code == 200 else None

        response_headers = {
            k: v
            for k, v in upstream.headers.items()
            if k.lower() not in HOP_BY_HOP
        }
        return Response(
            content=content,
            status_code=upstream.status_code,
            headers=response_headers,
            media_type=upstream.headers.get("content-type"),
            background=BackgroundTask(
                log_request,
                backend=backend,
                service_id=service_id,
                method=method,
                path=path,
                status_code=upstream.status_code,
                elapsed_ms=elapsed_ms,
                response_body=log_body,
            ),
        )

    async def _stream(
        self,
        *,
        backend: str,
        service_id: str,
        method: str,
        path: str,
        url: str,
        headers: dict[str, str],
        body: bytes,
    ) -> StreamingResponse:
        started = time.perf_counter()
        request = self._client.build_request(
            method=method,
            url=url,
            headers=headers,
            content=body if body else None,
        )
        upstream = await self._client.send(request, stream=True)

        async def iterator() -> AsyncIterator[bytes]:
            chunks: list[bytes] = []
            try:
                async for chunk in upstream.aiter_bytes():
                    chunks.append(chunk)
                    yield chunk
            finally:
                await upstream.aclose()
                elapsed_ms = (time.perf_counter() - started) * 1000
                combined = b"".join(chunks)
                log_request(
                    backend=backend,
                    service_id=service_id,
                    method=method,
                    path=path,
                    status_code=upstream.status_code,
                    elapsed_ms=elapsed_ms,
                    response_body=combined if upstream.status_code == 200 else None,
                )

        response_headers = {
            k: v
            for k, v in upstream.headers.items()
            if k.lower() not in HOP_BY_HOP
        }
        return StreamingResponse(
            iterator(),
            status_code=upstream.status_code,
            headers=response_headers,
            media_type=upstream.headers.get("content-type"),
        )


proxy_service = ProxyService()
