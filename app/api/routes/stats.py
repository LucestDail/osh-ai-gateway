"""Stats dashboard routes."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.services import stats as stats_mod
from app.services.usage_store import open_readonly

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[2] / "templates"))


@router.get("/stats", response_class=HTMLResponse)
async def stats_page(request: Request) -> HTMLResponse:
    conn = open_readonly()
    if conn is None:
        ctx = {"request": request, "empty": True, "no_db": True}
        return templates.TemplateResponse(request, "stats.html", ctx)

    try:
        data = stats_mod.build_dashboard(conn)
        ctx = {"request": request, **data}
        return templates.TemplateResponse(request, "stats.html", ctx)
    finally:
        conn.close()


@router.get("/api/stats")
async def stats_api() -> JSONResponse:
    conn = open_readonly()
    if conn is None:
        return JSONResponse({"empty": True, "no_db": True})
    try:
        return JSONResponse(stats_mod.build_dashboard(conn))
    finally:
        conn.close()
