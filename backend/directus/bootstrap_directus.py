"""Bootstrap the Directus editorial collections used by the content engine.

The collections are intentionally simple at this stage: they use string
references instead of full relation fields so the schema can be created
quickly against a fresh Directus SQLite instance. The Python app treats
Directus as the source of truth and stores the local UUIDs in ``legacy_id``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(slots=True)
class CollectionSpec:
    collection: str
    fields: list[dict[str, Any]]
    note: str


COLLECTIONS: list[CollectionSpec] = [
    CollectionSpec(
        collection="content_sources",
        note="Ingested knowledge-base sources mirrored from the Python app.",
        fields=[
            {"field": "legacy_id", "type": "string", "interface": "input", "meta": {"required": True, "note": "Local Source UUID"}, "schema": {"is_unique": True}},
            {"field": "title", "type": "string", "interface": "input", "meta": {"required": True}},
            {"field": "source_type", "type": "string", "interface": "select-dropdown", "meta": {"options": {"choices": [{"text": "article", "value": "article"}, {"text": "doc", "value": "doc"}, {"text": "blog", "value": "blog"}, {"text": "product", "value": "product"}]}}},
            {"field": "category", "type": "string", "interface": "input"},
            {"field": "filename", "type": "string", "interface": "input"},
            {"field": "raw_text", "type": "text", "interface": "textarea"},
            {"field": "chunk_count", "type": "integer", "interface": "input"},
            {"field": "file_id", "type": "string", "interface": "input"},
            {"field": "file_url", "type": "string", "interface": "input"},
            {"field": "created_at", "type": "string", "interface": "input"},
        ],
    ),
    CollectionSpec(
        collection="content_ideas",
        note="Prompt 1 idea outputs and manually curated ideas.",
        fields=[
            {"field": "legacy_id", "type": "string", "interface": "input", "meta": {"required": True, "note": "Idea UUID"}, "schema": {"is_unique": True}},
            {"field": "source_legacy_id", "type": "string", "interface": "input"},
            {"field": "query", "type": "string", "interface": "input"},
            {"field": "trending_topics", "type": "text", "interface": "textarea"},
            {"field": "generated_at", "type": "string", "interface": "input"},
            {"field": "context_summary", "type": "text", "interface": "textarea"},
            {"field": "title", "type": "string", "interface": "input"},
            {"field": "angle", "type": "string", "interface": "select-dropdown", "meta": {"options": {"choices": [{"text": "opinion", "value": "opinion"}, {"text": "how-to", "value": "how-to"}, {"text": "case-study", "value": "case-study"}, {"text": "data-story", "value": "data-story"}, {"text": "myth-busting", "value": "myth-busting"}]}}},
            {"field": "core_hook", "type": "text", "interface": "textarea"},
            {"field": "knowledge_source", "type": "text", "interface": "textarea"},
            {"field": "trend_source", "type": "text", "interface": "textarea"},
            {"field": "target_audience", "type": "text", "interface": "textarea"},
            {"field": "engagement_potential", "type": "string", "interface": "select-dropdown", "meta": {"options": {"choices": [{"text": "High", "value": "High"}, {"text": "Medium", "value": "Medium"}]}}},
            {"field": "engagement_reasoning", "type": "text", "interface": "textarea"},
            {"field": "suggested_formats", "type": "text", "interface": "textarea"},
            {"field": "research_data", "type": "text", "interface": "textarea"},
            {"field": "research_sources", "type": "text", "interface": "textarea"},
            {"field": "research_insights", "type": "text", "interface": "textarea"},
            {"field": "status", "type": "string", "interface": "select-dropdown", "meta": {"options": {"choices": [{"text": "generated", "value": "generated"}, {"text": "finalized", "value": "finalized"}, {"text": "published", "value": "published"}]}}},
        ],
    ),
    CollectionSpec(
        collection="content_drafts",
        note="Generated drafts awaiting review, scheduling, or publication.",
        fields=[
            {"field": "legacy_id", "type": "string", "interface": "input", "meta": {"required": True, "note": "Local Draft UUID"}, "schema": {"is_unique": True}},
            {"field": "source_legacy_id", "type": "string", "interface": "input"},
            {"field": "linkedin_type", "type": "string", "interface": "select-dropdown", "meta": {"options": {"choices": [{"text": "single", "value": "single"}, {"text": "carousel", "value": "carousel"}, {"text": "article", "value": "article"}]}}},
            {"field": "x_type", "type": "string", "interface": "select-dropdown", "meta": {"options": {"choices": [{"text": "tweet", "value": "tweet"}, {"text": "thread", "value": "thread"}, {"text": "carousel", "value": "carousel"}]}}},
            {"field": "linkedin_content", "type": "text", "interface": "textarea"},
            {"field": "x_content", "type": "text", "interface": "textarea"},
            {"field": "cover_image_path", "type": "string", "interface": "input"},
            {"field": "cover_image_file_id", "type": "string", "interface": "input"},
            {"field": "cover_image_url", "type": "string", "interface": "input"},
            {"field": "scheduled_at", "type": "string", "interface": "input"},
            {"field": "postiz_targets", "type": "text", "interface": "textarea"},
            {"field": "status", "type": "string", "interface": "select-dropdown", "meta": {"options": {"choices": [{"text": "pending", "value": "pending"}, {"text": "approved", "value": "approved"}, {"text": "scheduled", "value": "scheduled"}, {"text": "published", "value": "published"}, {"text": "rejected", "value": "rejected"}]}}},
            {"field": "reject_reason", "type": "text", "interface": "textarea"},
            {"field": "created_at", "type": "string", "interface": "input"},
            {"field": "published_at", "type": "string", "interface": "input"},
            {"field": "linkedin_post_id", "type": "string", "interface": "input"},
            {"field": "x_post_id", "type": "string", "interface": "input"},
        ],
    ),
    CollectionSpec(
        collection="publish_targets",
        note="Per-platform Postiz schedule records for each draft.",
        fields=[
            {"field": "legacy_id", "type": "string", "interface": "input", "meta": {"required": True, "note": "Draft/platform/post composite key"}, "schema": {"is_unique": True}},
            {"field": "draft_legacy_id", "type": "string", "interface": "input"},
            {"field": "platform", "type": "string", "interface": "select-dropdown", "meta": {"options": {"choices": [{"text": "linkedin", "value": "linkedin"}, {"text": "x", "value": "x"}]}}},
            {"field": "status", "type": "string", "interface": "select-dropdown", "meta": {"options": {"choices": [{"text": "scheduled", "value": "scheduled"}, {"text": "published", "value": "published"}, {"text": "failed", "value": "failed"}]}}},
            {"field": "postiz_post_id", "type": "string", "interface": "input"},
            {"field": "postiz_integration_id", "type": "string", "interface": "input"},
            {"field": "scheduled_at", "type": "string", "interface": "input"},
            {"field": "published_at", "type": "string", "interface": "input"},
            {"field": "payload", "type": "text", "interface": "textarea"},
            {"field": "release_url", "type": "string", "interface": "input"},
            {"field": "error_message", "type": "text", "interface": "textarea"},
        ],
    ),
    CollectionSpec(
        collection="carousel_assets",
        note="Slide-level assets for carousel posts.",
        fields=[
            {"field": "legacy_id", "type": "string", "interface": "input", "meta": {"required": True, "note": "Draft/platform/slide composite key"}, "schema": {"is_unique": True}},
            {"field": "draft_legacy_id", "type": "string", "interface": "input"},
            {"field": "platform", "type": "string", "interface": "select-dropdown", "meta": {"options": {"choices": [{"text": "linkedin", "value": "linkedin"}, {"text": "x", "value": "x"}]}}},
            {"field": "slide_index", "type": "integer", "interface": "input"},
            {"field": "asset_kind", "type": "string", "interface": "select-dropdown", "meta": {"options": {"choices": [{"text": "cover", "value": "cover"}, {"text": "slide", "value": "slide"}]}}},
            {"field": "prompt", "type": "text", "interface": "textarea"},
            {"field": "description", "type": "text", "interface": "textarea"},
            {"field": "directus_file_id", "type": "string", "interface": "input"},
            {"field": "directus_file_url", "type": "string", "interface": "input"},
            {"field": "status", "type": "string", "interface": "select-dropdown", "meta": {"options": {"choices": [{"text": "generated", "value": "generated"}, {"text": "uploaded", "value": "uploaded"}, {"text": "failed", "value": "failed"}]}}},
        ],
    ),
    CollectionSpec(
        collection="workflow_events",
        note="Append-only audit trail for generation, approval, scheduling, and webhook events.",
        fields=[
            {"field": "legacy_id", "type": "string", "interface": "input", "meta": {"required": True, "note": "Event UUID"}, "schema": {"is_unique": True}},
            {"field": "entity_type", "type": "string", "interface": "input"},
            {"field": "entity_legacy_id", "type": "string", "interface": "input"},
            {"field": "event_type", "type": "string", "interface": "input"},
            {"field": "payload", "type": "text", "interface": "textarea"},
            {"field": "source", "type": "string", "interface": "input"},
            {"field": "occurred_at", "type": "string", "interface": "input"},
        ],
    ),
]


async def _login(base_url: str, email: str, password: str) -> str:
    status, body = _request_json(
        base_url,
        "POST",
        "/auth/login",
        json_body={"email": email, "password": password, "mode": "json"},
    )
    if status >= 400:
        raise RuntimeError(f"Directus login failed with HTTP {status}: {body}")
    token = json.loads(body).get("data", {}).get("access_token") if body else None
    if not token:
        raise RuntimeError("Directus login failed: missing access token")
    return token


def _request_json(
    base_url: str,
    method: str,
    path: str,
    *,
    token: str | None = None,
    json_body: dict[str, Any] | None = None,
) -> tuple[int, str]:
    url = base_url.rstrip("/") + path
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if json_body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(json_body).encode("utf-8")

    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=60) as response:
            return response.status, response.read().decode("utf-8")
    except HTTPError as exc:
        body = exc.read().decode("utf-8") if exc.fp else ""
        return exc.code, body
    except URLError as exc:
        raise RuntimeError(f"Directus request failed: {exc}") from exc


async def _ensure_field(base_url: str, token: str, collection: str, field_def: dict[str, Any]) -> None:
    create_status, create_body = _request_json(
        base_url,
        "POST",
        f"/fields/{collection}",
        token=token,
        json_body=_field_request_body(field_def),
    )
    if create_status >= 400 and not _looks_like_exists(create_body):
        raise RuntimeError(f"Directus field create failed for {collection}.{field_def['field']} with HTTP {create_status}: {create_body}")


async def _ensure_collection(base_url: str, token: str, spec: CollectionSpec) -> None:
    create_status, create_body = _request_json(
        base_url,
        "POST",
        "/collections",
        token=token,
        json_body={
            "collection": spec.collection,
            "schema": {"name": spec.collection},
            "meta": {"note": spec.note, "hidden": False, "singleton": False},
            "fields": [_field_request_body(field) for field in spec.fields],
        },
    )
    if create_status >= 400 and not _looks_like_exists(create_body):
        raise RuntimeError(f"Directus collection create failed for {spec.collection} with HTTP {create_status}: {create_body}")

    for field_def in spec.fields:
        await _ensure_field(base_url, token, spec.collection, field_def)


def _looks_like_exists(body: str) -> bool:
    lowered = body.lower()
    return "already exists" in lowered or "duplicate" in lowered or "exists" in lowered


def _field_request_body(field_def: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {
        "type": field_def["type"],
        "field": field_def["field"],
    }

    meta = dict(field_def.get("meta", {}))
    interface = field_def.get("interface")
    if interface:
        meta.setdefault("interface", interface)
    if meta:
        body["meta"] = meta

    schema = dict(field_def.get("schema", {}))
    if schema:
        body["schema"] = schema

    return body


async def bootstrap_directus(
    *,
    base_url: str,
    email: str,
    password: str,
) -> None:
    token = await _login(base_url, email, password)
    for spec in COLLECTIONS:
        await _ensure_collection(base_url, token, spec)


if __name__ == "__main__":
    import asyncio

    asyncio.run(
        bootstrap_directus(
            base_url=os.environ.get("DIRECTUS_URL", "http://localhost:8055"),
            email=os.environ.get("DIRECTUS_EMAIL", "admin@gmail.com"),
            password=os.environ.get("DIRECTUS_PASSWORD", "Admin@123"),
        )
    )
