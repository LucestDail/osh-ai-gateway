"""OSH AI Gateway — transparent Gemini / Vertex passthrough."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes import proxy, stats
from app.config.settings import settings
from app.middleware.auth import InternalAuthMiddleware
from app.services.proxy import proxy_service
from pathlib import Path

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await proxy_service.close()


app = FastAPI(
    title="OSH AI Gateway",
    description="Transparent passthrough proxy for Gemini AI Studio and Vertex AI",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(InternalAuthMiddleware)
app.include_router(proxy.router, tags=["proxy"])
app.include_router(stats.router, tags=["stats"])
app.mount("/static", StaticFiles(directory=str(Path(__file__).resolve().parent / "static")), name="static")


@app.get("/")
async def root() -> dict[str, str]:
    return {"message": "OSH AI Gateway is running", "stats": "/stats"}


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.server_host,
        port=settings.server_port,
        reload=settings.debug,
    )
