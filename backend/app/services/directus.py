"""Directus integration helpers for editorial data and uploaded assets."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app.core.config import settings


class DirectusError(RuntimeError):
    """Raised when the Directus API cannot be reached or configured."""


@dataclass(slots=True)
class DirectusFile:
    """Minimal file metadata returned by Directus uploads."""

    id: str
    filename_download: str | None = None
    url: str | None = None


class DirectusClient:
    """Small helper around the Directus REST API.

    The client supports either a pre-issued bearer token or login via
    Directus email/password credentials.
    """

    def __init__(self) -> None:
        self.base_url = settings.directus_url.rstrip("/")
        self._token = settings.directus_access_token.strip() or None

    @property
    def configured(self) -> bool:
        """Return ``True`` when enough Directus credentials are present."""
        if not self.base_url:
            return False
        return bool(self._token or (settings.directus_email and settings.directus_password))

    @property
    def _has_login_credentials(self) -> bool:
        return bool(settings.directus_email and settings.directus_password)

    async def login(self) -> None:
        """Log into Directus and cache an access token."""
        if not self._has_login_credentials:
            raise DirectusError("Directus email/password are not configured")

        async with httpx.AsyncClient(base_url=self.base_url, timeout=60) as client:
            response = await client.post(
                "/auth/login",
                json={
                    "email": settings.directus_email,
                    "password": settings.directus_password,
                    "mode": "json",
                },
            )
            response.raise_for_status()

        data = response.json().get("data", {})
        token = data.get("access_token")
        if not token:
            raise DirectusError("Directus login did not return an access token")
        self._token = token

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        retry_login: bool = True,
    ) -> Any:
        """Send a request to Directus and unwrap the ``data`` payload."""
        if not self.configured:
            raise DirectusError("Directus integration is not configured")

        if self._token is None and self._has_login_credentials:
            await self.login()

        headers: dict[str, str] = {}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        async with httpx.AsyncClient(base_url=self.base_url, timeout=60) as client:
            response = await client.request(
                method,
                path,
                params=params,
                json=json_body,
                data=data,
                files=files,
                headers=headers,
            )

        if response.status_code == 401 and retry_login and self._has_login_credentials:
            self._token = None
            await self.login()
            return await self.request(
                method,
                path,
                params=params,
                json_body=json_body,
                data=data,
                files=files,
                retry_login=False,
            )

        response.raise_for_status()
        if not response.content:
            return {}

        payload = response.json()
        if isinstance(payload, dict) and "data" in payload:
            return payload["data"]
        return payload

    async def find_item_by_legacy_id(
        self,
        collection: str,
        legacy_id: str,
    ) -> dict[str, Any] | None:
        """Return the first item matching ``legacy_id`` or ``None``."""
        result = await self.request(
            "GET",
            f"/items/{collection}",
            params={"filter[legacy_id][_eq]": legacy_id, "limit": 1},
        )

        if isinstance(result, list):
            return result[0] if result else None
        if isinstance(result, dict):
            return result
        return None

    async def upsert_by_legacy_id(
        self,
        collection: str,
        legacy_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Create or update an item using ``legacy_id`` as the natural key."""
        existing = await self.find_item_by_legacy_id(collection, legacy_id)
        body = {"legacy_id": legacy_id, **payload}

        if existing and existing.get("id") is not None:
            item_id = str(existing["id"])
            updated = await self.request("PATCH", f"/items/{collection}/{item_id}", json_body=body)
            if isinstance(updated, dict):
                return updated
            return {"id": item_id, **body}

        created = await self.request("POST", f"/items/{collection}", json_body=body)
        if isinstance(created, dict):
            return created
        return body

    async def upload_file(
        self,
        *,
        filename: str,
        content: bytes,
        title: str | None = None,
    ) -> DirectusFile | None:
        """Upload a file to Directus and return the resulting file id."""
        if not self.configured:
            return None

        data: dict[str, Any] = {}
        if title:
            data["title"] = title

        uploaded = await self.request(
            "POST",
            "/files",
            data=data,
            files={"file": (filename, content)},
        )
        if not isinstance(uploaded, dict):
            return None

        file_id = str(uploaded.get("id") or "")
        if not file_id:
            return None

        return DirectusFile(
            id=file_id,
            filename_download=uploaded.get("filename_download"),
            url=f"{self.base_url}/assets/{file_id}",
        )


def _stringify_json(value: Any | None) -> str | None:
    """Serialise JSON-like values for Directus text fields."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _source_payload(source: Any) -> dict[str, Any]:
    return {
        "title": getattr(source, "title", ""),
        "source_type": getattr(source, "source_type", "article"),
        "category": getattr(source, "category", "general"),
        "filename": getattr(source, "filename", None),
        "raw_text": getattr(source, "raw_text", None),
        "chunk_count": getattr(source, "chunk_count", 0),
        "created_at": _isoformat(getattr(source, "created_at", None)),
    }


async def sync_source_to_directus(
    source: Any,
    *,
    file_bytes: bytes | None = None,
    filename: str | None = None,
) -> dict[str, Any] | None:
    """Mirror a source document into Directus."""
    client = DirectusClient()
    if not client.configured:
        return None

    payload = _source_payload(source)
    if file_bytes and filename:
        uploaded = await client.upload_file(
            filename=filename,
            content=file_bytes,
            title=getattr(source, "title", None),
        )
        if uploaded:
            payload["file_id"] = uploaded.id
            payload["file_url"] = uploaded.url

    return await client.upsert_by_legacy_id(
        settings.directus_sources_collection,
        getattr(source, "id"),
        payload,
    )


async def sync_generated_ideas_to_directus(
    request: Any,
    response: Any,
) -> list[dict[str, Any]]:
    """Mirror a generated idea bundle into Directus."""
    client = DirectusClient()
    if not client.configured:
        return []

    payloads: list[dict[str, Any]] = []
    research_sources = getattr(response, "research_sources", None)
    research_insights = getattr(response, "research_insights", None)

    for idea in getattr(response, "ideas", []):
        payload = {
            "source_legacy_id": None,
            "query": getattr(request, "query", ""),
            "trending_topics": _stringify_json(getattr(request, "trending_topics", [])),
            "generated_at": getattr(response, "generated_at", None),
            "context_summary": getattr(response, "context_summary", ""),
            "title": getattr(idea, "title", ""),
            "angle": getattr(idea, "angle", ""),
            "core_hook": getattr(idea, "core_hook", ""),
            "knowledge_source": getattr(idea, "knowledge_source", ""),
            "trend_source": getattr(idea, "trend_source", ""),
            "target_audience": getattr(idea, "target_audience", ""),
            "engagement_potential": getattr(idea, "engagement_potential", ""),
            "engagement_reasoning": getattr(idea, "engagement_reasoning", ""),
            "suggested_formats": _stringify_json(getattr(idea, "suggested_formats", [])),
            "research_data": _stringify_json(getattr(idea, "research_data", None)),
            "research_sources": _stringify_json(research_sources),
            "research_insights": research_insights,
            "status": "generated",
        }
        record = await client.upsert_by_legacy_id(
            settings.directus_ideas_collection,
            getattr(idea, "id"),
            payload,
        )
        payloads.append(record)

    return payloads


async def sync_draft_to_directus(
    draft: Any,
    *,
    linkedin_content: dict[str, Any] | None = None,
    x_content: dict[str, Any] | None = None,
    cover_image_path: str | None = None,
    cover_image_filename: str | None = None,
    postiz_targets: list[dict[str, Any]] | None = None,
    scheduled_at: datetime | None = None,
) -> dict[str, Any] | None:
    """Mirror a draft into Directus, uploading the cover image if present."""
    client = DirectusClient()
    if not client.configured:
        return None

    payload = {
        "source_legacy_id": getattr(draft, "source_id", None),
        "linkedin_type": getattr(draft, "linkedin_type", None),
        "x_type": getattr(draft, "x_type", None),
        "linkedin_content": _stringify_json(
            linkedin_content if linkedin_content is not None else getattr(draft, "linkedin_content", None)
        ),
        "x_content": _stringify_json(
            x_content if x_content is not None else getattr(draft, "x_content", None)
        ),
        "cover_image_path": cover_image_path or getattr(draft, "cover_image_path", None),
        "scheduled_at": _isoformat(scheduled_at or getattr(draft, "scheduled_at", None)),
        "postiz_targets": _stringify_json(postiz_targets or getattr(draft, "postiz_targets", None)),
        "status": getattr(draft, "status", "pending"),
        "reject_reason": getattr(draft, "reject_reason", None),
        "created_at": _isoformat(getattr(draft, "created_at", None)),
        "published_at": _isoformat(getattr(draft, "published_at", None)),
        "linkedin_post_id": getattr(draft, "linkedin_post_id", None),
        "x_post_id": getattr(draft, "x_post_id", None),
    }

    if cover_image_path:
        full_path = Path(settings.images_dir) / cover_image_path
        if full_path.exists():
            uploaded = await client.upload_file(
                filename=cover_image_filename or full_path.name,
                content=full_path.read_bytes(),
                title=getattr(draft, "id", None),
            )
            if uploaded:
                payload["cover_image_file_id"] = uploaded.id
                payload["cover_image_url"] = uploaded.url

    return await client.upsert_by_legacy_id(
        settings.directus_drafts_collection,
        getattr(draft, "id"),
        payload,
    )


async def sync_publish_target_to_directus(
    *,
    draft_legacy_id: str,
    platform: str,
    post_id: str | None,
    integration_id: str | None,
    scheduled_at: datetime | None,
    payload: dict[str, Any] | None = None,
    status: str = "scheduled",
    published_at: datetime | None = None,
    error_message: str | None = None,
) -> dict[str, Any] | None:
    """Mirror a Postiz schedule target into Directus."""
    client = DirectusClient()
    if not client.configured:
        return None

    record_payload = {
        "draft_legacy_id": draft_legacy_id,
        "platform": platform,
        "status": status,
        "postiz_post_id": post_id,
        "postiz_integration_id": integration_id,
        "scheduled_at": _isoformat(scheduled_at),
        "published_at": _isoformat(published_at),
        "payload": _stringify_json(payload),
        "error_message": error_message,
    }

    legacy_id = f"{draft_legacy_id}:{platform}"
    return await client.upsert_by_legacy_id(
        settings.directus_publish_targets_collection,
        legacy_id,
        record_payload,
    )


async def sync_carousel_assets_to_directus(
    *,
    draft_legacy_id: str,
    platform: str,
    assets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Mirror generated carousel assets into Directus."""
    client = DirectusClient()
    if not client.configured:
        return []

    records: list[dict[str, Any]] = []
    for asset in assets:
        path = asset.get("path")
        uploaded = None
        if path:
            full_path = Path(path)
            if full_path.exists():
                uploaded = await client.upload_file(
                    filename=full_path.name,
                    content=full_path.read_bytes(),
                    title=asset.get("description") or asset.get("prompt") or full_path.stem,
                )

        payload = {
            "draft_legacy_id": draft_legacy_id,
            "platform": platform,
            "slide_index": asset.get("slide_index", 0),
            "asset_kind": asset.get("asset_kind", "slide"),
            "prompt": asset.get("prompt"),
            "description": asset.get("description"),
            "directus_file_id": uploaded.id if uploaded else asset.get("directus_file_id"),
            "directus_file_url": uploaded.url if uploaded else asset.get("directus_file_url"),
            "status": asset.get("status", "generated"),
        }
        legacy_id = asset.get("legacy_id") or f"{draft_legacy_id}:{platform}:{payload['slide_index']}"
        record = await client.upsert_by_legacy_id(
            settings.directus_carousel_assets_collection,
            legacy_id,
            payload,
        )
        records.append(record)

    return records


async def record_workflow_event(
    *,
    entity_type: str,
    entity_legacy_id: str,
    event_type: str,
    payload: dict[str, Any] | None = None,
    source: str = "social-content-engine",
    occurred_at: datetime | None = None,
) -> dict[str, Any] | None:
    """Append a workflow event into Directus."""
    client = DirectusClient()
    if not client.configured:
        return None

    event_legacy_id = uuid.uuid4().hex
    event_payload = {
        "entity_type": entity_type,
        "entity_legacy_id": entity_legacy_id,
        "event_type": event_type,
        "payload": _stringify_json(payload),
        "source": source,
        "occurred_at": _isoformat(occurred_at or datetime.now(timezone.utc)),
    }
    return await client.upsert_by_legacy_id(
        settings.directus_workflow_events_collection,
        event_legacy_id,
        event_payload,
    )