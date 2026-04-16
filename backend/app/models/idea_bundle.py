"""IdeaBundle ORM model — stores generated idea bundles with research data."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class IdeaBundle(Base):
    """A bundle of 5 generated content ideas for a given industry.

    Stores the industry input, retrieved PDF chunks, deep-research JSON,
    the generated ideas, and the raw LLM prompt/response for auditability.
    """

    __tablename__ = "idea_bundles"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4()),
    )

    # ── User input ───────────────────────────────────────────
    industry: Mapped[str] = mapped_column(String(256), nullable=False, index=True)

    # ── Retrieved context ────────────────────────────────────
    # Serialised JSON list of chunk dicts used for grounding
    retrieved_chunks: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Deep research ────────────────────────────────────────
    # Raw JSON returned by the Parallel API research service
    research_data: Mapped[str | None] = mapped_column(Text, nullable=True)
    research_insights: Mapped[str | None] = mapped_column(Text, nullable=True)
    research_sources: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── LLM generation ───────────────────────────────────────
    llm_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    llm_raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Generated output ─────────────────────────────────────
    # Serialised JSON list of ContentIdea dicts
    ideas: Mapped[str | None] = mapped_column(Text, nullable=True)
    idea_count: Mapped[int] = mapped_column(Integer, default=0)
    context_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Metadata ─────────────────────────────────────────────
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="generated",
    )  # generated | archived
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow,
    )
