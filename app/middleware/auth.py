"""Internal token auth middleware."""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.config.settings import settings

_PUBLIC_PATHS = {"/", "/health", "/docs", "/openapi.json", "/redoc"}


class InternalAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        token = settings.gateway_internal_token.strip()
        if not token:
            return await call_next(request)

        if request.url.path in _PUBLIC_PATHS:
            return await call_next(request)

        provided = request.headers.get("x-gateway-token", "")
        if not provided:
            auth = request.headers.get("authorization", "")
            if auth.lower().startswith("bearer "):
                provided = auth[7:].strip()

        if provided != token:
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or missing gateway token"},
            )

        return await call_next(request)
