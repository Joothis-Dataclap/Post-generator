"""Webhook endpoints for external systems such as Postiz."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from sqlalchemy import select

from app.core.config import settings
from app.core.dependencies import SessionDep
from app.models.draft import Draft
from app.services.directus import (
    record_workflow_event,
    sync_draft_to_directus,
    sync_publish_target_to_directus,
)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _load_targets(raw_targets: str | None) -> list[dict[str, Any]]:
    if not raw_targets:
        return []
    try:
        parsed = json.loads(raw_targets)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _target_matches(target: dict[str, Any], post_id: str | None, integration_id: str | None) -> bool:
    if not post_id and not integration_id:
        return False
    if post_id and str(target.get("post_id")) == post_id:
        return True
    if integration_id and str(target.get("integration_id")) == integration_id:
        return True
    return False


def _resolve_event_type(payload: dict[str, Any]) -> str:
    for key in ("event", "type", "status", "state"):
        value = payload.get(key)
        if value:
            return str(value)
    return "postiz.webhook"


@router.post("/postiz")
async def receive_postiz_webhook(
    payload: dict[str, Any],
    db: SessionDep,
    x_postiz_secret: str | None = Header(default=None),
) -> dict[str, Any]:
    """Handle Postiz state updates and mirror them into the local DB + Directus."""
    if settings.postiz_webhook_secret:
        header_value = x_postiz_secret or payload.get("secret")
        if header_value != settings.postiz_webhook_secret:
            raise HTTPException(status_code=401, detail="Invalid Postiz webhook secret")

    post_id = payload.get("postId") or payload.get("post_id") or payload.get("id")
    integration_id = payload.get("integration") or payload.get("integrationId") or payload.get("integration_id")
    event_type = _resolve_event_type(payload)
    status_value = str(payload.get("status") or payload.get("state") or "").lower()

    await record_workflow_event(
        entity_type="postiz_webhook",
        entity_legacy_id=str(post_id or integration_id or "unknown"),
        event_type=event_type,
        payload=payload,
        source="postiz",
        occurred_at=datetime.now(timezone.utc),
    )

    result = await db.execute(select(Draft).order_by(Draft.created_at.desc()))
    drafts = result.scalars().all()

    matched: list[str] = []
    for draft in drafts:
        targets = _load_targets(draft.postiz_targets)
        if not any(_target_matches(target, str(post_id) if post_id else None, str(integration_id) if integration_id else None) for target in targets):
            continue

        matched.append(draft.id)
        for target in targets:
            if _target_matches(target, str(post_id) if post_id else None, str(integration_id) if integration_id else None):
                if status_value in {"published", "success", "completed", "done"}:
                    target["status"] = "published"
                    target["published_at"] = datetime.now(timezone.utc).isoformat()
                elif status_value in {"scheduled", "queue", "queued"}:
                    target["status"] = "scheduled"
                elif status_value in {"failed", "error"}:
                    target["status"] = "failed"
                    target["error_message"] = payload.get("error") or payload.get("message")

                await sync_publish_target_to_directus(
                    draft_legacy_id=draft.id,
                    platform=str(target.get("platform") or ""),
                    post_id=str(target.get("post_id") or post_id or ""),
                    integration_id=str(target.get("integration_id") or integration_id or ""),
                    scheduled_at=draft.scheduled_at,
                    payload=target.get("payload"),
                    status=str(target.get("status") or "scheduled"),
                    published_at=datetime.now(timezone.utc) if target.get("status") == "published" else None,
                    error_message=target.get("error_message"),
                )

        draft.postiz_targets = json.dumps(targets)
        if any(target.get("status") == "published" for target in targets):
            draft.status = "published"
            draft.published_at = datetime.now(timezone.utc)
        elif any(target.get("status") == "scheduled" for target in targets):
            draft.status = "scheduled"
        elif any(target.get("status") == "failed" for target in targets):
            draft.status = "approved"

        await sync_draft_to_directus(
            draft,
            cover_image_path=draft.cover_image_path,
            postiz_targets=targets,
            scheduled_at=draft.scheduled_at,
        )

    await db.commit()
    return {"ok": True, "matched_drafts": matched}
