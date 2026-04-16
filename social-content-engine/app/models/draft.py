"""Draft ORM model — represents a generated social-media content draft."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _utcnow() -> datetime:
    """Return the current UTC datetime (timezone-aware)."""
    return datetime.now(timezone.utc)


class Draft(Base):
    """A generated content draft awaiting human review / publishing.

    Fields ``linkedin_content`` and ``x_content`` store serialised JSON strings.
    """

    __tablename__ = "drafts"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    directus_item_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    source_id: Mapped[str] = mapped_column(
        String(36), nullable=False, index=True,
    )

    # Content type selectors
    linkedin_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    x_type: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Serialised JSON content
    linkedin_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    x_content: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Image
    cover_image_path: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # Scheduling / external workflow metadata
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    postiz_targets: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Workflow status: pending → approved → published | rejected
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending",
    )
    reject_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow,
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    # Platform post IDs (set after successful publish)
    linkedin_post_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    x_post_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
