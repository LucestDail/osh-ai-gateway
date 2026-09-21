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


def _provider_pins() -> dict[str, list[str]]:
    """설정 문자열 → {service_id: [사업자…]}. 형식이 깨진 항목은 버리고 warn 한다."""
    pins: dict[str, list[str]] = {}
    raw = (settings.openrouter_provider_pins or "").strip()
    for entry in filter(None, (e.strip() for e in raw.split(";"))):
        svc, _, provs = entry.partition(":")
        names = [p.strip() for p in provs.split(",") if p.strip()]
        if not svc.strip() or not names:
            # 🔴 조용히 넘기지 않는다 — 오타 하나로 고정이 통째로 안 걸리면
            #    "걸린 줄 알았는데 안 걸린" 상태가 되고 그건 초록불로 보인다.
            logger.warning(
                "[provider-pin] 형식이 잘못된 항목을 버린다: %r "
                "(형식: '서비스ID:사업자[,사업자...]' 를 ';' 로 구분)", entry,
            )
            continue
        pins[svc.strip()] = names
    return pins


def _apply_provider_policy(openai_body: dict, service_id: str) -> None:
    """지정된 서비스에만 사업자 고정을 붙인다(제자리 수정).

    🔴 이 분기에 **들어가지 않는 서비스는 요청이 한 글자도 안 바뀐다.**
       그래서 다른 서비스의 회귀를 따로 잴 필요가 없다 — 조건이 구조적으로 가른다.

    ⚠️ `allow_fallbacks=False` 라 **목록 밖으로 나가지 않는다.** 적은 사업자가
       전부 죽으면 그 서비스만 실패한다. 이것이 "한 곳으로 고정" 의 대가이고
       사용자가 그 대가를 알고 고른 것이다(2026-09-21).
    """
    pins = _provider_pins()
    order = pins.get(service_id)
    if not order:
        return
    policy: dict[str, object] = {"order": order, "allow_fallbacks": False}
    if settings.openrouter_provider_deny_training:
        policy["data_collection"] = "deny"
    openai_body["provider"] = policy
    logger.info(
        "[provider-pin] service_id=%s → order=%s allow_fallbacks=False deny_training=%s",
        service_id, order, settings.openrouter_provider_deny_training,
    )


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
        _apply_provider_policy(openai_body, service_id)
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

            # 🔎 실제로 답한 모델·사업자를 **응답 헤더**로 알려 준다 (2026-09-21)
            #
            # 왜: 호출하는 쪽은 자기가 **요청한** 모델 이름밖에 모른다. 게이트웨이가
            #     `openrouter_default_model` 로 갈아끼우고 OpenRouter 가 다시 사업자를
            #     고르므로, 화면에 "gemini-3.5-flash" 를 띄우면 **거짓말이 된다**
            #     (실측: simpleStock 이 그 상태였고 마지막 gemini 호출은 2026-06-24 였다).
            # 왜 헤더인가: 본문 스키마를 안 건드려 **다른 서비스에 영향이 0** 이다.
            #     모르는 헤더는 무시되므로 읽지 않는 쪽은 아무 일도 일어나지 않는다.
            extra_headers: dict[str, str] = {}
            if upstream.status_code == 200:
                openai_resp = json.loads(upstream.content)
                # ⚠️ 응답이 말해 주는 값을 쓴다 — 우리가 **요청한** 값이 아니다.
                #    둘이 다를 수 있고, 다를 때가 바로 알고 싶은 순간이다.
                # 🔴 HTTP 헤더 값은 **latin-1 만** 담을 수 있다. 업스트림이 비-latin
                #    문자가 든 모델명을 주면 헤더를 만드는 순간 **응답 전체가 깨진다**
                #    (내 테스트가 이걸 먼저 잡았다 — UnicodeEncodeError).
                #    ⇒ 담을 수 없으면 **그 헤더만 조용히 뺀다.** 부가 정보 하나 때문에
                #       본 응답을 잃는 것이 훨씬 나쁘다.
                for key, val in (("X-Llm-Model", openai_resp.get("model")),
                                 ("X-Llm-Provider", openai_resp.get("provider"))):
                    if not isinstance(val, str) or not val:
                        continue
                    try:
                        val.encode("latin-1")
                    except UnicodeEncodeError:
                        logger.warning("[llm-header] %s 를 헤더에 담을 수 없다(비-latin): %r", key, val[:40])
                        continue
                    extra_headers[key] = val
                gemini_resp = openai_response_to_gemini(openai_resp)
                content = json.dumps(gemini_resp, ensure_ascii=False).encode()
                log_body = upstream.content
            else:
                content = upstream.content
                log_body = None

            return Response(
                content=content, status_code=upstream.status_code,
                media_type="application/json",
                headers=extra_headers or None,
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
