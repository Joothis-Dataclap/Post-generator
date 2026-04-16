"""Central API router — mounts all sub-routers under /api/v1."""

from fastapi import APIRouter

from app.api.drafts import router as drafts_router
from app.api.generate import router as generate_router
from app.api.ideas import router as ideas_router
from app.api.search import router as search_router
from app.api.sources import router as sources_router
from app.api.webhooks import router as webhooks_router

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(sources_router)
api_router.include_router(search_router)
api_router.include_router(generate_router)
api_router.include_router(ideas_router)
api_router.include_router(drafts_router)
api_router.include_router(webhooks_router)
