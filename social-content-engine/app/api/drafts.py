"""Drafts API — list, edit, approve, reject, and publish drafts."""

import json
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.core.dependencies import SessionDep
from app.models.draft import Draft
from app.schemas.draft import ApproveRequest, DraftResponse, DraftUpdate, RejectRequest
from app.services.publisher_linkedin import publish_to_linkedin
from app.services.publisher_x import publish_to_x

logger = structlog.get_logger()

router = APIRouter(prefix="/drafts", tags=["drafts"])


def _draft_to_response(draft: Draft) -> DraftResponse:
    """Convert an ORM ``Draft`` row into a ``DraftResponse`` schema.

    JSON-encoded content columns are deserialised back to dicts so the
    API always returns structured objects.
    """
    return DraftResponse(
        id=draft.id,
        source_id=draft.source_id,
        linkedin_type=draft.linkedin_type,
        x_type=draft.x_type,
        linkedin_content=json.loads(draft.linkedin_content) if draft.linkedin_content else None,
        x_content=json.loads(draft.x_content) if draft.x_content else None,
        cover_image_path=draft.cover_image_path,
        status=draft.status,
        reject_reason=draft.reject_reason,
        created_at=draft.created_at,
        published_at=draft.published_at,
        linkedin_post_id=draft.linkedin_post_id,
        x_post_id=draft.x_post_id,
    )


@router.get("", response_model=list[DraftResponse])
async def list_drafts(
    db: SessionDep,
    status: str | None = None,
) -> list[DraftResponse]:
    """List all drafts, optionally filtered by status.

    Query parameters:
        status: If provided, return only drafts matching this status
                (e.g. ``pending``, ``approved``, ``published``, ``rejected``).
    """
    query = select(Draft).order_by(Draft.created_at.desc())
    if status:
        query = query.where(Draft.status == status)
    result = await db.execute(query)
    drafts = result.scalars().all()
    return [_draft_to_response(d) for d in drafts]


@router.get("/{draft_id}", response_model=DraftResponse)
async def get_draft(draft_id: str, db: SessionDep) -> DraftResponse:
    """Retrieve a single draft by ID with full deserialised content."""
    result = await db.execute(select(Draft).where(Draft.id == draft_id))
    draft = result.scalar_one_or_none()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    return _draft_to_response(draft)


@router.put("/{draft_id}", response_model=DraftResponse)
async def update_draft(
    draft_id: str,
    body: DraftUpdate,
    db: SessionDep,
) -> DraftResponse:
    """Edit a draft's content or status before approval.

    Published drafts cannot be edited — returns 400.
    """
    result = await db.execute(select(Draft).where(Draft.id == draft_id))
    draft = result.scalar_one_or_none()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    if draft.status == "published":
        raise HTTPException(status_code=400, detail="Cannot edit a published draft")

    if body.linkedin_content is not None:
        draft.linkedin_content = json.dumps(body.linkedin_content)
    if body.x_content is not None:
        draft.x_content = json.dumps(body.x_content)
    if body.status is not None:
        draft.status = body.status

    await db.commit()
    await db.refresh(draft)
    return _draft_to_response(draft)


@router.post("/{draft_id}/approve", response_model=DraftResponse)
async def approve_draft(
    draft_id: str,
    body: ApproveRequest,
    db: SessionDep,
) -> DraftResponse:
    """Approve a draft and optionally publish to LinkedIn and/or X.

    Content overrides can be supplied in the request body.  If
    publishing fails on one platform the draft stays ``approved``
    and errors are logged; partial success is allowed.
    """
    result = await db.execute(select(Draft).where(Draft.id == draft_id))
    draft = result.scalar_one_or_none()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    if draft.status == "published":
        raise HTTPException(status_code=400, detail="Draft already published")

    # Apply overrides if provided
    if body.linkedin_content_override is not None:
        draft.linkedin_content = json.dumps(body.linkedin_content_override)
    if body.x_content_override is not None:
        draft.x_content = json.dumps(body.x_content_override)

    draft.status = "approved"
    await db.commit()

    errors: list[str] = []

    # Publish to LinkedIn
    if body.publish_linkedin and draft.linkedin_content and draft.linkedin_type:
        try:
            li_content = json.loads(draft.linkedin_content)
            post_id = await publish_to_linkedin(
                draft.linkedin_type, li_content, draft.cover_image_path
            )
            draft.linkedin_post_id = post_id
        except Exception as exc:
            logger.error("LinkedIn publish failed", error=str(exc), draft_id=draft_id)
            errors.append(f"LinkedIn: {exc}")

    # Publish to X
    if body.publish_x and draft.x_content and draft.x_type:
        try:
            x_content = json.loads(draft.x_content)
            tweet_id = await publish_to_x(
                draft.x_type, x_content, draft.cover_image_path
            )
            draft.x_post_id = tweet_id
        except Exception as exc:
            logger.error("X publish failed", error=str(exc), draft_id=draft_id)
            errors.append(f"X: {exc}")

    if draft.linkedin_post_id or draft.x_post_id:
        draft.status = "published"
        draft.published_at = datetime.now(timezone.utc)
    elif errors:
        draft.status = "approved"  # stay approved if publish failed

    await db.commit()
    await db.refresh(draft)

    if errors:
        logger.warning("Partial publish", errors=errors)

    return _draft_to_response(draft)


@router.post("/{draft_id}/reject", response_model=DraftResponse)
async def reject_draft(
    draft_id: str,
    body: RejectRequest,
    db: SessionDep,
) -> DraftResponse:
    """Reject a draft with a human-supplied reason.

    Published drafts cannot be rejected — returns 400.
    """
    result = await db.execute(select(Draft).where(Draft.id == draft_id))
    draft = result.scalar_one_or_none()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    if draft.status == "published":
        raise HTTPException(status_code=400, detail="Cannot reject a published draft")

    draft.status = "rejected"
    draft.reject_reason = body.reason
    await db.commit()
    await db.refresh(draft)
    return _draft_to_response(draft)
