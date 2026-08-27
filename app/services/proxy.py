"""HTTP transparent proxy to upstream AI APIs."""

from __future__ import annotations

import json
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
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
    "host", "content-length", "content-encoding",
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


def _is_openai_stream_request(body: bytes) -> bool:
    if not body:
        return False
    try:
        return bool(json.loads(body).get("stream"))
    except (json.JSONDecodeError, ValueError):
        return False


class ProxyService:
    def __init__(self) -> None:
        limits = httpx.Limits(max_connections=200, max_keepalive_connections=50)
        self._client = httpx.AsyncClient(follow_redirects=False, limits=limits)

    async def close(self) -> None:
        await self._client.aclose()

    async def forward_gemini(self, request: Request, path: str) -> Response:
        # OpenRouter 번역 모드: generateContent 계열 요청을 OpenRouter로 전환
        if (settings.openrouter_translate_gemini
                and settings.openrouter_api_key.strip()
                and ("generatecontent" in path.lower())):
            return await self._forward_gemini_translated(request, path)

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
                backend="gemini", service_id=service_id, method=request.method,
                path=f"/openrouter/{settings.openrouter_default_model}", url=url, headers=headers, body=body,
            )

        return await self._buffered(
            backend="gemini", service_id=service_id, method=request.method,
            path=f"/openrouter/{settings.openrouter_default_model}", url=url, headers=headers, body=body,
            timeout=settings.proxy_timeout_seconds,
        )

    async def _forward_gemini_translated(self, request: Request, path: str) -> Response:
        """Gemini generateContent 요청을 OpenRouter(OpenAI 포맷)로 번역해서 전달."""
        from app.services.gemini_compat import (
            gemini_body_to_openai,
            openai_response_to_gemini,
            openai_sse_chunk_to_gemini,
        )

        body = await request.body()
        service_id = request.headers.get("x-service-id", "")
        is_stream = "streamgeneratecontent" in path.lower()

        try:
            gemini_body = json.loads(body) if body else {}
        except json.JSONDecodeError:
            gemini_body = {}

        model = settings.openrouter_default_model
        openai_body = gemini_body_to_openai(gemini_body, model, stream=is_stream)
        openai_bytes = json.dumps(openai_body, ensure_ascii=False).encode()

        upstream_url = f"{settings.openrouter_base_url.rstrip('/')}/chat/completions"
        headers = {
            "content-type": "application/json",
            "authorization": f"Bearer {settings.openrouter_api_key}",
        }

        if is_stream:
            started = time.perf_counter()
            req = self._client.build_request("POST", upstream_url, headers=headers, content=openai_bytes)
            upstream = await self._client.send(req, stream=True)

            async def sse_iterator() -> AsyncIterator[bytes]:
                buffer = b""
                raw_chunks: list[bytes] = []
                try:
                    async for raw_chunk in upstream.aiter_bytes():
                        buffer += raw_chunk
                        raw_chunks.append(raw_chunk)
                        while b"\n" in buffer:
                            line, buffer = buffer.split(b"\n", 1)
                            decoded = line.decode("utf-8", errors="replace")
                            if decoded.startswith("data: "):
                                gemini_json = openai_sse_chunk_to_gemini(decoded[6:])
                                if gemini_json is not None:
                                    yield (f"data: {gemini_json}\n\n").encode()
                finally:
                    await upstream.aclose()
                    elapsed_ms = (time.perf_counter() - started) * 1000
                    log_request(
                        backend="openrouter", service_id=service_id, method="POST",
                        path=f"/openrouter/{settings.openrouter_default_model}", status_code=upstream.status_code,
                        elapsed_ms=elapsed_ms,
                        response_body=b"".join(raw_chunks) if upstream.status_code == 200 else None,
                    )

            return StreamingResponse(sse_iterator(), status_code=upstream.status_code,
                                     media_type="text/event-stream")

        else:
            started = time.perf_counter()
            try:
                upstream = await self._client.request(
                    "POST", upstream_url, headers=headers, content=openai_bytes,
                    timeout=settings.proxy_timeout_seconds,
                )
            except httpx.TimeoutException as exc:
                raise HTTPException(status_code=504, detail="Upstream timeout") from exc
            except httpx.HTTPError as exc:
                raise HTTPException(status_code=502, detail=f"Upstream error: {exc}") from exc

            elapsed_ms = (time.perf_counter() - started) * 1000

            if upstream.status_code == 200:
                openai_resp = json.loads(upstream.content)
                gemini_resp = openai_response_to_gemini(openai_resp)
                content = json.dumps(gemini_resp, ensure_ascii=False).encode()
                log_body = upstream.content
            else:
                content = upstream.content
                log_body = None

            return Response(
                content=content, status_code=upstream.status_code,
                media_type="application/json",
                background=BackgroundTask(
                    log_request,
                    backend="openrouter", service_id=service_id, method="POST",
                    path=f"/openrouter/{settings.openrouter_default_model}", status_code=upstream.status_code,
                    elapsed_ms=elapsed_ms, response_body=log_body,
                ),
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
                backend="vertex", service_id=service_id, method=request.method,
                path=f"/vertex/{path}", url=url, headers=headers, body=body,
            )

        return await self._buffered(
            backend="vertex", service_id=service_id, method=request.method,
            path=f"/vertex/{path}", url=url, headers=headers, body=body,
            timeout=settings.proxy_timeout_seconds,
        )

    async def forward_openrouter(self, request: Request, path: str) -> Response:
        if not settings.openrouter_api_key.strip():
            raise HTTPException(status_code=503, detail="OPENROUTER_API_KEY is not configured")

        upstream_base = settings.openrouter_base_url.rstrip("/")
        query = request.url.query
        url = f"{upstream_base}/{path.lstrip('/')}"
        if query:
            url = f"{url}?{query}"

        headers = _filtered_headers(request.headers)
        headers.pop("x-goog-api-key", None)
        headers["authorization"] = f"Bearer {settings.openrouter_api_key}"

        body = await request.body()
        service_id = request.headers.get("x-service-id", "")

        if _is_openai_stream_request(body):
            return await self._stream(
                backend="openrouter", service_id=service_id, method=request.method,
                path=f"/openrouter/{path}", url=url, headers=headers, body=body,
            )

        return await self._buffered(
            backend="openrouter", service_id=service_id, method=request.method,
            path=f"/openrouter/{path}", url=url, headers=headers, body=body,
            timeout=settings.proxy_timeout_seconds,
        )

    async def _buffered(self, *, backend, service_id, method, path, url, headers, body, timeout) -> Response:
        started = time.perf_counter()
        try:
            upstream = await self._client.request(
                method=method, url=url, headers=headers,
                content=body if body else None, timeout=timeout,
            )
        except httpx.TimeoutException as exc:
            raise HTTPException(status_code=504, detail="Upstream timeout") from exc
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"Upstream error: {exc}") from exc

        elapsed_ms = (time.perf_counter() - started) * 1000
        content = upstream.content
        log_body = content if upstream.status_code == 200 else None

        response_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in HOP_BY_HOP}
        return Response(
            content=content, status_code=upstream.status_code,
            headers=response_headers, media_type=upstream.headers.get("content-type"),
            background=BackgroundTask(
                log_request, backend=backend, service_id=service_id, method=method,
                path=path, status_code=upstream.status_code,
                elapsed_ms=elapsed_ms, response_body=log_body,
            ),
        )

    async def _stream(self, *, backend, service_id, method, path, url, headers, body) -> StreamingResponse:
        started = time.perf_counter()
        request = self._client.build_request(method=method, url=url, headers=headers,
                                              content=body if body else None)
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
                    backend=backend, service_id=service_id, method=method, path=path,
                    status_code=upstream.status_code, elapsed_ms=elapsed_ms,
                    response_body=combined if upstream.status_code == 200 else None,
                )

        response_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in HOP_BY_HOP}
        return StreamingResponse(
            iterator(), status_code=upstream.status_code,
            headers=response_headers, media_type=upstream.headers.get("content-type"),
        )


proxy_service = ProxyService()
