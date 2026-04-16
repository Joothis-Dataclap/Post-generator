"""Post generation API endpoint."""

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.core.dependencies import QdrantDep, SessionDep
from app.models.source import Source
from app.schemas.generation import GenerateRequest, GenerateResponse
from app.services.generation import generate_posts

router = APIRouter(tags=["generate"])


@router.post("/generate", response_model=GenerateResponse)
async def generate(
    body: GenerateRequest,
    db: SessionDep,
    qdrant: QdrantDep,
) -> GenerateResponse:
    """Generate platform-specific social media posts from a source.

    Retrieves relevant chunks via RAG, builds a structured prompt,
    calls Claude for content generation, generates a cover image via
    Gemini, and saves the result as a draft.
    """
    result = await db.execute(select(Source).where(Source.id == body.source_id))
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    try:
        response = await generate_posts(
            db=db,
            qdrant=qdrant,
            source=source,
            request=body,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    return response
