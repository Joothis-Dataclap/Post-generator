"""Idea generation and content creation API endpoints (Prompt 1 & 2)."""

from fastapi import APIRouter, HTTPException

from app.core.dependencies import QdrantDep, SessionDep
from app.schemas.generation import (
    ContentGenerateRequest,
    ContentGenerateResponse,
    IdeaGenerateRequest,
    IdeaGenerateResponse,
)
from app.services.generation import generate_content_from_idea
from app.services.idea_generation import generate_ideas

router = APIRouter(tags=["ideas"])


@router.post(
    "/ideas/generate",
    response_model=IdeaGenerateResponse,
    summary="Generate content ideas (Prompt 1)",
)
async def generate_content_ideas(
    body: IdeaGenerateRequest,
    qdrant: QdrantDep,
) -> IdeaGenerateResponse:
    """Cross-reference knowledge-base chunks with trending signals to produce
    5 high-impact content ideas for DataClap Digital.

    This is **Step 1** of the two-step workflow:
    1. ``POST /ideas/generate`` — produce ideas
    2. ``POST /content/generate`` — turn an approved idea into posts
    """
    try:
        return await generate_ideas(qdrant=qdrant, request=body)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post(
    "/content/generate",
    response_model=ContentGenerateResponse,
    summary="Generate content from approved idea (Prompt 2)",
)
async def generate_content_from_approved_idea(
    body: ContentGenerateRequest,
    db: SessionDep,
    qdrant: QdrantDep,
) -> ContentGenerateResponse:
    """Take an approved idea (from Prompt 1) and generate ready-to-publish
    LinkedIn post + X thread grounded in knowledge-base chunks.

    This is **Step 2** of the two-step workflow.
    The generated content is automatically saved as a draft.
    """
    try:
        return await generate_content_from_idea(
            db=db,
            qdrant=qdrant,
            request=body,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
