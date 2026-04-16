"""Drafts API — list, edit, approve, reject, and publish drafts."""

import json
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.core.config import settings
from app.core.dependencies import SessionDep
from app.models.draft import Draft
from app.schemas.draft import ApproveRequest, DraftResponse, DraftUpdate, RejectRequest
from app.services.directus import record_workflow_event, sync_draft_to_directus
from app.services.postiz import PostizError, schedule_draft_via_postiz
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
        idea_bundle_id=draft.idea_bundle_id,
        idea_id=draft.idea_id,
        linkedin_type=draft.linkedin_type,
        x_type=draft.x_type,
        linkedin_content=json.loads(draft.linkedin_content) if draft.linkedin_content else None,
        x_content=json.loads(draft.x_content) if draft.x_content else None,
        cover_image_path=draft.cover_image_path,
        status=draft.status,
        reject_reason=draft.reject_reason,
        created_at=draft.created_at,
        scheduled_at=draft.scheduled_at,
        published_at=draft.published_at,
        linkedin_post_id=draft.linkedin_post_id,
        x_post_id=draft.x_post_id,
        postiz_targets=_load_targets(draft.postiz_targets),
    )


def _load_content(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _load_targets(raw: str | None) -> list[dict]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []


def _blocked_text_only_targets(
    draft: Draft,
    *,
    publish_linkedin: bool,
    publish_x: bool,
) -> list[str]:
    """Return publish blockers for carousel drafts that still need media assets."""
    errors: list[str] = []
    if publish_linkedin and draft.linkedin_type == "carousel":
        errors.append(
            "LinkedIn carousel publishing is disabled for text-only drafts until slide media handoff is implemented"
        )
    if publish_x and draft.x_type == "carousel":
        errors.append(
            "X carousel publishing is disabled for text-only drafts until slide media handoff is implemented"
        )
    return errors


async def _mirror_draft_to_directus(db, draft: Draft) -> None:
    """Best-effort Directus sync for draft state changes."""
    try:
        record = await sync_draft_to_directus(
            draft,
            linkedin_content=_load_content(draft.linkedin_content),
            x_content=_load_content(draft.x_content),
            cover_image_path=draft.cover_image_path,
            postiz_targets=_load_targets(draft.postiz_targets),
            scheduled_at=draft.scheduled_at,
        )
    except Exception as exc:
        logger.warning("Directus draft sync failed", draft_id=draft.id, error=str(exc))
        return

    if record and record.get("id") is not None:
        directus_item_id = str(record["id"])
        if draft.directus_item_id != directus_item_id:
            draft.directus_item_id = directus_item_id
            db.add(draft)
            await db.commit()
            await db.refresh(draft)


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
    await _mirror_draft_to_directus(db, draft)
    await record_workflow_event(
        entity_type="draft",
        entity_legacy_id=draft.id,
        event_type="draft.updated",
        payload={"status": draft.status},
        source="social-content-engine",
        occurred_at=datetime.now(timezone.utc),
    )
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
    draft.scheduled_at = body.scheduled_at
    await db.commit()

    errors: list[str] = []
    scheduled_targets: list[dict] = []
    linkedin_content = _load_content(draft.linkedin_content)
    x_content = _load_content(draft.x_content)
    blocked_targets = _blocked_text_only_targets(
        draft,
        publish_linkedin=body.publish_linkedin,
        publish_x=body.publish_x,
    )
    errors.extend(blocked_targets)
    publish_linkedin = body.publish_linkedin and draft.linkedin_type != "carousel"
    publish_x = body.publish_x and draft.x_type != "carousel"

    if settings.postiz_api_key:
        try:
            scheduled_targets = [
                target.to_record()
                for target in await schedule_draft_via_postiz(
                    draft_id=draft.id,
                    linkedin_type=draft.linkedin_type,
                    linkedin_content=linkedin_content,
                    x_type=draft.x_type,
                    x_content=x_content,
                    publish_linkedin=publish_linkedin,
                    publish_x=publish_x,
                    scheduled_at=body.scheduled_at,
                )
            ]
        except PostizError as exc:
            logger.error("Postiz scheduling failed", error=str(exc), draft_id=draft_id)
            errors.append(f"Postiz: {exc}")
        except Exception as exc:
            logger.error("Postiz scheduling failed", error=str(exc), draft_id=draft_id)
            errors.append(f"Postiz: {exc}")

        if scheduled_targets:
            draft.postiz_targets = json.dumps(scheduled_targets)
            draft.scheduled_at = datetime.fromisoformat(scheduled_targets[0]["scheduled_at"].replace("Z", "+00:00"))
            draft.status = "scheduled"
        elif not errors:
            errors.append("Postiz: no configured integrations matched the requested platforms")
            draft.status = "approved"
    else:
        # Legacy fallback: publish directly to the provider APIs.
        if publish_linkedin and linkedin_content and draft.linkedin_type:
            try:
                post_id = await publish_to_linkedin(
                    draft.linkedin_type, linkedin_content, draft.cover_image_path
                )
                draft.linkedin_post_id = post_id
            except Exception as exc:
                logger.error("LinkedIn publish failed", error=str(exc), draft_id=draft_id)
                errors.append(f"LinkedIn: {exc}")

        if publish_x and x_content and draft.x_type:
            try:
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
    await _mirror_draft_to_directus(db, draft)
    if draft.status == "scheduled":
        event_type = "draft.scheduled"
    elif draft.status == "published":
        event_type = "draft.published"
    elif draft.status == "rejected":
        event_type = "draft.rejected"
    else:
        event_type = "draft.approved"
    await record_workflow_event(
        entity_type="draft",
        entity_legacy_id=draft.id,
        event_type=event_type,
        payload={
            "status": draft.status,
            "scheduled_targets": scheduled_targets,
            "linkedin_post_id": draft.linkedin_post_id,
            "x_post_id": draft.x_post_id,
        },
        source="social-content-engine",
        occurred_at=datetime.now(timezone.utc),
    )

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
    await _mirror_draft_to_directus(db, draft)
    await record_workflow_event(
        entity_type="draft",
        entity_legacy_id=draft.id,
        event_type="draft.rejected",
        payload={"reason": body.reason},
        source="social-content-engine",
        occurred_at=datetime.now(timezone.utc),
    )
    return _draft_to_response(draft)
