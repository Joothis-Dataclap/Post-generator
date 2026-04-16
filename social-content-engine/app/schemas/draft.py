"""Pydantic schemas for draft review workflow (request / response)."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class DraftResponse(BaseModel):
    """Full public representation of a content draft."""

    id: str
    directus_item_id: str | None = None
    source_id: str
    linkedin_type: str | None = None
    x_type: str | None = None
    linkedin_content: Any | None = None
    x_content: Any | None = None
    cover_image_path: str | None = None
    status: str
    reject_reason: str | None = None
    created_at: datetime
    scheduled_at: datetime | None = None
    published_at: datetime | None = None
    linkedin_post_id: str | None = None
    x_post_id: str | None = None
    postiz_targets: Any | None = None

    model_config = {"from_attributes": True}


class DraftUpdate(BaseModel):
    """Fields allowed when editing a draft before approval."""

    linkedin_content: dict | None = None
    x_content: dict | None = None
    status: Literal["pending", "approved", "scheduled", "published", "rejected"] | None = None


class ApproveRequest(BaseModel):
    """Body sent when approving a draft for publication."""

    publish_linkedin: bool = True
    publish_x: bool = True
    scheduled_at: datetime | None = None
    linkedin_content_override: dict | None = None
    x_content_override: dict | None = None


class RejectRequest(BaseModel):
    """Body sent when rejecting a draft."""

    reason: str = Field(..., min_length=1, max_length=2000)
