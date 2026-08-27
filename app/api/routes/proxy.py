"""Transparent passthrough routes."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from app.config.settings import settings
from app.services.proxy import proxy_service

router = APIRouter()


@router.api_route(
    "/gemini/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    response_model=None,
)
async def gemini_passthrough(path: str, request: Request) -> Response:
    return await proxy_service.forward_gemini(request, path)


@router.api_route(
    "/vertex/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    response_model=None,
)
async def vertex_passthrough(path: str, request: Request) -> Response:
    return await proxy_service.forward_vertex(request, path)


@router.api_route(
    "/openrouter/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    response_model=None,
)
async def openrouter_passthrough(path: str, request: Request) -> Response:
    return await proxy_service.forward_openrouter(request, path)


@router.get("/status")
async def gateway_status() -> dict:
    return {
        "gemini_configured": bool(settings.gemini_api_key.strip()),
        "vertex_enabled": settings.vertex_enabled,
        "vertex_project_id": settings.vertex_project_id or None,
        "vertex_location": settings.vertex_location,
        "openrouter_configured": bool(settings.openrouter_api_key.strip()),
        "auth_enabled": bool(settings.gateway_internal_token.strip()),
    }
