"""Pydantic schemas for source documents (request / response)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class SourceCreate(BaseModel):
    """Schema accepted when creating a new source via the API."""

    title: str = Field(..., max_length=512)
    source_type: Literal["article", "doc", "blog", "product"] = "article"
    category: str = Field("general", max_length=128)
    text_content: str | None = Field(
        None,
        description="Direct text content to ingest (alternative to file upload)",
    )


class SourceResponse(BaseModel):
    """Public representation of a source document."""

    id: str
    directus_item_id: str | None = None
    title: str
    source_type: str
    category: str
    filename: str | None = None
    chunk_count: int
    created_at: datetime

    model_config = {"from_attributes": True}


class SourceDetailResponse(SourceResponse):
    """Source document with its raw text and all associated chunks."""

    raw_text: str | None = None
    chunks: list[dict] = Field(default_factory=list)
