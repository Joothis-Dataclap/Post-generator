"""Postiz scheduling helpers for social-media publication."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import structlog

from app.core.config import settings
from app.services.directus import sync_publish_target_to_directus

logger = structlog.get_logger()


@dataclass(slots=True)
class ScheduledPostTarget:
    """One scheduled post returned from Postiz."""

    platform: str
    integration_id: str
    post_id: str
    scheduled_at: datetime
    payload: dict[str, Any]
    release_url: str | None = None

    def to_record(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "integration_id": self.integration_id,
            "post_id": self.post_id,
            "scheduled_at": self.scheduled_at.astimezone(timezone.utc).isoformat(),
            "payload": self.payload,
            "release_url": self.release_url,
            "status": "scheduled",
        }


class PostizError(RuntimeError):
    """Raised when the Postiz public API cannot be reached."""


class PostizClient:
    """Small wrapper around the Postiz public API."""

    def __init__(self) -> None:
        self.base_url = settings.postiz_api_url.rstrip("/")
        self.api_key = settings.postiz_api_key.strip()

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": self.api_key,
            "Content-Type": "application/json",
        }

    async def create_post(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Submit a schedule request to Postiz."""
        if not self.configured:
            raise PostizError("Postiz is not configured")

        async with httpx.AsyncClient(base_url=self.base_url, timeout=60) as client:
            response = await client.post("/posts", headers=self._headers(), json=payload)
        response.raise_for_status()

        data = response.json()
        if isinstance(data, dict) and "data" in data:
            return data["data"]
        return data

    async def schedule_platform_post(
        self,
        *,
        platform: str,
        integration_id: str,
        text: str,
        scheduled_at: datetime,
        image_urls: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> ScheduledPostTarget:
        """Schedule a single platform post through Postiz."""
        payload = {
            "type": "schedule",
            "date": scheduled_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "shortLink": False,
            "tags": tags or [],
            "posts": [
                {
                    "integration": {"id": integration_id},
                    "value": [
                        {
                            "content": text,
                            "image": image_urls or [],
                        }
                    ],
                    "settings": {"__type": platform},
                }
            ],
        }

        response = await self.create_post(payload)
        post_id = str(response.get("postId") or response.get("id") or "")
        if not post_id:
            raise PostizError("Postiz did not return a post id")

        release_url = response.get("releaseURL") or response.get("releaseUrl")
        return ScheduledPostTarget(
            platform=platform,
            integration_id=integration_id,
            post_id=post_id,
            scheduled_at=scheduled_at,
            payload=payload,
            release_url=release_url,
        )


def _normalize_hashtags(values: list[str] | None) -> str:
    hashtags = values or []
    normalized = []
    for value in hashtags:
        tag = value.lstrip("#").strip()
        if tag:
            normalized.append(f"#{tag}")
    return " ".join(normalized)


def _linkedin_text(linkedin_type: str | None, content: dict[str, Any]) -> str:
    if linkedin_type == "article":
        parts = [content.get("title", ""), content.get("subtitle", ""), content.get("body", "")]
    elif linkedin_type == "carousel":
        parts = [content.get("intro_caption", "")]
        for slide in content.get("slides", []):
            if isinstance(slide, dict):
                parts.append(f"{slide.get('headline', '')}\n{slide.get('body', '')}".strip())
    else:
        parts = [content.get("hook", ""), content.get("body", "")]

    text = "\n\n".join(part for part in parts if part)
    hashtags = _normalize_hashtags(content.get("hashtags", []))
    if hashtags:
        text = f"{text}\n\n{hashtags}" if text else hashtags
    return text.strip()


def _x_text(x_type: str | None, content: dict[str, Any]) -> str:
    if x_type == "thread":
        parts = [content.get("hook_tweet", "")]
        parts.extend(content.get("tweets", []))
        cta = content.get("cta_tweet", "")
        if cta:
            parts.append(cta)
        text = "\n\n".join(part for part in parts if part)
    elif x_type == "carousel":
        parts = [content.get("caption", "")]
        for slide in content.get("slides", []):
            if isinstance(slide, dict):
                parts.append(f"{slide.get('headline', '')}".strip())
        text = "\n\n".join(part for part in parts if part)
    else:
        text = content.get("text", "")

    hashtags = _normalize_hashtags(content.get("hashtags", []))
    if hashtags:
        text = f"{text}\n\n{hashtags}" if text else hashtags
    return text.strip()


def _resolve_schedule_time(scheduled_at: datetime | None) -> datetime:
    if scheduled_at is not None:
        if scheduled_at.tzinfo is None:
            return scheduled_at.replace(tzinfo=timezone.utc)
        return scheduled_at.astimezone(timezone.utc)
    return datetime.now(timezone.utc) + timedelta(minutes=settings.postiz_default_delay_minutes)


async def schedule_draft_via_postiz(
    *,
    draft_id: str,
    linkedin_type: str | None,
    linkedin_content: dict[str, Any] | None,
    x_type: str | None,
    x_content: dict[str, Any] | None,
    cover_image_urls: list[str] | None = None,
    publish_linkedin: bool = True,
    publish_x: bool = True,
    scheduled_at: datetime | None = None,
) -> list[ScheduledPostTarget]:
    """Schedule the draft with Postiz and mirror the targets into Directus."""
    client = PostizClient()
    if not client.configured:
        raise PostizError("Postiz is not configured")

    publish_at = _resolve_schedule_time(scheduled_at)
    scheduled_targets: list[ScheduledPostTarget] = []

    if publish_linkedin and linkedin_content and settings.postiz_linkedin_integration_id:
        target = await client.schedule_platform_post(
            platform="linkedin",
            integration_id=settings.postiz_linkedin_integration_id,
            text=_linkedin_text(linkedin_type, linkedin_content),
            scheduled_at=publish_at,
            image_urls=cover_image_urls,
        )
        scheduled_targets.append(target)
        await sync_publish_target_to_directus(
            draft_legacy_id=draft_id,
            platform="linkedin",
            post_id=target.post_id,
            integration_id=target.integration_id,
            scheduled_at=target.scheduled_at,
            payload=target.payload,
            status="scheduled",
        )
    elif publish_linkedin and linkedin_content:
        logger.warning(
            "LinkedIn Postiz integration id is missing",
            draft_id=draft_id,
        )

    if publish_x and x_content and settings.postiz_x_integration_id:
        target = await client.schedule_platform_post(
            platform="x",
            integration_id=settings.postiz_x_integration_id,
            text=_x_text(x_type, x_content),
            scheduled_at=publish_at,
            image_urls=cover_image_urls,
        )
        scheduled_targets.append(target)
        await sync_publish_target_to_directus(
            draft_legacy_id=draft_id,
            platform="x",
            post_id=target.post_id,
            integration_id=target.integration_id,
            scheduled_at=target.scheduled_at,
            payload=target.payload,
            status="scheduled",
        )
    elif publish_x and x_content:
        logger.warning(
            "X Postiz integration id is missing",
            draft_id=draft_id,
        )

    return scheduled_targets
