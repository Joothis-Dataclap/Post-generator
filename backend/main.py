"""Social Content Engine — FastAPI entry point.

Configures structured logging, initialises the database on startup,
serves generated images as static files, and mounts all API routes
under ``/api/v1``.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router
from app.core.config import settings
from app.core.database import init_db

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan handler — runs startup and shutdown logic.

    Startup: creates the images directory and initialises the SQLite
    database (runs ``CREATE TABLE IF NOT EXISTS`` for all models).

    Shutdown: logs a clean exit message.
    """
    logger.info("Starting Social Content Engine", debug=settings.debug)
    Path(settings.images_dir).mkdir(parents=True, exist_ok=True)
    await init_db()
    yield
    logger.info("Shutting down")


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description="AI-powered social media content generation engine with RAG",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve generated images as static files
images_path = Path(settings.images_dir)
images_path.mkdir(parents=True, exist_ok=True)
app.mount("/storage/images", StaticFiles(directory=str(images_path)), name="images")

# Mount API router
app.include_router(api_router)


@app.get("/health")
async def health():
    return {"status": "ok", "app": settings.app_name}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=settings.debug)
