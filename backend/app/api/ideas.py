"""Idea generation and content creation API endpoints (Prompt 1 & 2)."""

import json

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.core.dependencies import QdrantDep, SessionDep
from app.models.idea_bundle import IdeaBundle
from app.schemas.generation import (
    ContentGenerateRequest,
    ContentGenerateResponse,
    ContentIdea,
    IdeaBundleResponse,
    IdeaGenerateRequest,
    IdeaGenerateResponse,
)
from app.services.directus import record_workflow_event, sync_generated_ideas_to_directus
from app.services.generation import generate_content_from_idea
from app.services.idea_generation import generate_ideas

router = APIRouter(tags=["ideas"])


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Helpers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _bundle_to_response(bundle: IdeaBundle) -> IdeaBundleResponse:
    """Convert an ORM IdeaBundle to its API response schema."""
    ideas_list: list[ContentIdea] = []
    if bundle.ideas:
        try:
            raw = json.loads(bundle.ideas)
            ideas_list = [ContentIdea(**i) for i in raw]
        except (json.JSONDecodeError, TypeError):
            pass

    research_data = None
    if bundle.research_data:
        try:
            research_data = json.loads(bundle.research_data)
        except json.JSONDecodeError:
            pass

    research_sources = None
    if bundle.research_sources:
        try:
            research_sources = json.loads(bundle.research_sources)
        except json.JSONDecodeError:
            pass

    return IdeaBundleResponse(
        id=bundle.id,
        industry=bundle.industry,
        context_summary=bundle.context_summary,
        ideas=ideas_list,
        research_data=research_data,
        research_sources=research_sources,
        research_insights=bundle.research_insights,
        idea_count=bundle.idea_count,
        status=bundle.status,
        created_at=bundle.created_at.isoformat(),
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Step 1 — Idea generation
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@router.post(
    "/ideas/generate",
    response_model=IdeaGenerateResponse,
    summary="Generate content ideas for an industry (Prompt 1)",
)
async def generate_content_ideas(
    body: IdeaGenerateRequest,
    db: SessionDep,
    qdrant: QdrantDep,
) -> IdeaGenerateResponse:
    """Takes an **industry** from the user, searches relevant PDF chunks
    in the knowledge base, runs deep online research via Parallel API,
    combines both, and generates 5 high-impact content ideas.

    The result is persisted as an ``IdeaBundle`` — use the returned
    ``bundle_id`` to list or select ideas later.

    This is **Step 1** of the two-step workflow:
    1. ``POST /ideas/generate`` — produce ideas (this endpoint)
    2. ``POST /content/generate`` — generate posts from a selected idea
    """
    try:
        response = await generate_ideas(db=db, qdrant=qdrant, request=body)
        try:
            await sync_generated_ideas_to_directus(body, response)
            await record_workflow_event(
                entity_type="idea_bundle",
                entity_legacy_id=response.bundle_id,
                event_type="ideas.generated",
                payload={"idea_count": len(response.ideas), "industry": body.industry},
                source="social-content-engine",
            )
        except Exception:
            pass
        return response
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Idea retrieval
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@router.get(
    "/ideas",
    response_model=list[IdeaBundleResponse],
    summary="List all idea bundles",
)
async def list_idea_bundles(
    db: SessionDep,
    industry: str | None = None,
) -> list[IdeaBundleResponse]:
    """List all stored idea bundles, newest first.

    Optionally filter by ``?industry=fintech``.
    """
    query = select(IdeaBundle).order_by(IdeaBundle.created_at.desc())
    if industry:
        query = query.where(IdeaBundle.industry == industry)
    result = await db.execute(query)
    bundles = result.scalars().all()
    return [_bundle_to_response(b) for b in bundles]


@router.get(
    "/ideas/{bundle_id}",
    response_model=IdeaBundleResponse,
    summary="Get a single idea bundle with all ideas",
)
async def get_idea_bundle(
    bundle_id: str,
    db: SessionDep,
) -> IdeaBundleResponse:
    """Retrieve a single idea bundle by ID, including all 5 ideas,
    research data, and the chunks that were used."""
    result = await db.execute(select(IdeaBundle).where(IdeaBundle.id == bundle_id))
    bundle = result.scalar_one_or_none()
    if not bundle:
        raise HTTPException(status_code=404, detail="Idea bundle not found")
    return _bundle_to_response(bundle)


@router.get(
    "/ideas/{bundle_id}/{idea_id}",
    response_model=ContentIdea,
    summary="Get a single idea from a bundle",
)
async def get_single_idea(
    bundle_id: str,
    idea_id: str,
    db: SessionDep,
) -> ContentIdea:
    """Retrieve one specific idea by ``bundle_id`` + ``idea_id``."""
    result = await db.execute(select(IdeaBundle).where(IdeaBundle.id == bundle_id))
    bundle = result.scalar_one_or_none()
    if not bundle:
        raise HTTPException(status_code=404, detail="Idea bundle not found")

    if not bundle.ideas:
        raise HTTPException(status_code=404, detail="Bundle has no ideas")

    try:
        ideas_raw = json.loads(bundle.ideas)
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="Corrupted ideas data")

    for idea_data in ideas_raw:
        if idea_data.get("id") == idea_id:
            return ContentIdea(**idea_data)

    raise HTTPException(status_code=404, detail=f"Idea '{idea_id}' not found in bundle")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Step 2 — Content generation from selected idea
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@router.post(
    "/content/generate",
    response_model=ContentGenerateResponse,
    summary="Generate content from a selected idea (Prompt 2)",
)
async def generate_content_from_approved_idea(
    body: ContentGenerateRequest,
    db: SessionDep,
    qdrant: QdrantDep,
) -> ContentGenerateResponse:
    """Takes a ``bundle_id`` + ``idea_id``, loads the stored idea and
    its research context from the database, retrieves supporting PDF
    chunks, and generates ready-to-publish posts for LinkedIn + X.

    The generated content is automatically saved as a draft.

    This is **Step 2** of the two-step workflow.
    """
    try:
        return await generate_content_from_idea(
            db=db,
            qdrant=qdrant,
            request=body,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
