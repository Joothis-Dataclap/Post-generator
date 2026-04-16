"""Pydantic schemas for AI generation, search, and platform-specific content models."""

from typing import Any, Literal

from pydantic import BaseModel, Field


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Request schemas
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class GenerateRequest(BaseModel):
    """Request body for the ``POST /api/v1/generate`` endpoint."""

    source_id: str
    query_context: str | None = Field(
        None,
        description="Extra instruction or angle for generation",
    )
    linkedin_type: Literal["single", "carousel", "article"] | None = "single"
    x_type: Literal["tweet", "thread", "carousel"] | None = "tweet"
    brand_voice: str = "professional yet approachable"
    target_audience: str = "industry professionals"
    image_style: str = "modern, clean, corporate illustration"


class SearchRequest(BaseModel):
    """Request body for the ``POST /api/v1/search`` endpoint."""

    query: str
    top_k: int = Field(5, ge=1, le=50)
    category_filter: str | None = None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LinkedIn content models
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class LinkedInSinglePost(BaseModel):
    """A single LinkedIn feed post."""

    hook: str = Field(..., max_length=80)
    body: str
    hashtags: list[str] = Field(default_factory=list)
    image_description: str = ""


class CarouselSlide(BaseModel):
    """One slide in a LinkedIn or X carousel."""

    headline: str
    body: str
    image_description: str = ""


class LinkedInCarouselPost(BaseModel):
    """A LinkedIn carousel (multi-image) post."""

    intro_caption: str
    slides: list[CarouselSlide] = Field(..., min_length=1, max_length=10)
    hashtags: list[str] = Field(default_factory=list)


class LinkedInArticle(BaseModel):
    """A native LinkedIn article."""

    title: str
    subtitle: str
    body: str
    hashtags: list[str] = Field(default_factory=list)
    image_description: str = ""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# X/Twitter content models
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class XTweet(BaseModel):
    """A single tweet."""

    text: str = Field(..., max_length=260)
    hashtags: list[str] = Field(default_factory=list)
    image_description: str = ""


class XThread(BaseModel):
    """A Twitter/X thread (hook → body tweets → CTA)."""

    hook_tweet: str
    tweets: list[str] = Field(..., min_length=1, max_length=15)
    cta_tweet: str
    hashtags: list[str] = Field(default_factory=list)


class XCarouselSlide(BaseModel):
    """One slide in an X multi-image carousel."""

    headline: str
    image_description: str = ""


class XCarousel(BaseModel):
    """An X carousel post (tweet + up to 4 images)."""

    caption: str = Field(..., max_length=240)
    slides: list[XCarouselSlide] = Field(..., min_length=1, max_length=4)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Response schemas
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class GenerateResponse(BaseModel):
    """Response returned after post generation completes."""

    draft_id: str
    source_id: str
    linkedin_type: str | None = None
    x_type: str | None = None
    linkedin_content: dict[str, Any] | None = None
    x_content: dict[str, Any] | None = None
    cover_image_url: str | None = None


class SearchResult(BaseModel):
    """A single chunk returned from semantic search."""

    chunk_id: str
    source_id: str
    source_title: str
    text: str
    score: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchResponse(BaseModel):
    """Response wrapper for semantic search results."""

    query: str
    results: list[SearchResult]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Idea generation (Prompt 1)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class IdeaGenerateRequest(BaseModel):
    """Request body for ``POST /api/v1/ideas/generate``."""

    query: str = Field(
        ...,
        description="Search query used to retrieve relevant knowledge-base chunks",
    )
    trending_topics: list[str] = Field(
        ...,
        min_length=1,
        description="List of currently trending topics / signals to cross-reference",
    )
    top_k: int = Field(8, ge=1, le=20, description="Number of KB chunks to retrieve")
    category_filter: str | None = None


class ContentIdea(BaseModel):
    """A single content idea produced by the idea-generation prompt."""

    id: str
    title: str
    angle: Literal["opinion", "how-to", "case-study", "data-story", "myth-busting"]
    core_hook: str
    knowledge_source: str
    trend_source: str
    target_audience: str
    engagement_potential: Literal["High", "Medium"]
    engagement_reasoning: str
    suggested_formats: list[str] = Field(default_factory=list)
    research_data: dict | None = Field(
        None,
        description="Research findings with online sources and expert insights"
    )


class IdeaGenerateResponse(BaseModel):
    """Response returned by the idea-generation endpoint."""

    generated_at: str
    context_summary: str
    ideas: list[ContentIdea]
    research_sources: list[dict] | None = Field(
        None,
        description="Online sources and research data from Parallel API",
    )
    research_insights: str | None = Field(
        None,
        description="Key research insights and expert opinions",
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Content generation from idea (Prompt 2)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class ContentGenerateRequest(BaseModel):
    """Request body for ``POST /api/v1/content/generate``."""

    idea: ContentIdea = Field(..., description="The approved idea from Prompt 1")
    query: str = Field(
        ...,
        description="Search query for retrieving supporting chunks",
    )
    source_id: str | None = Field(
        None,
        description="Optional source ID to restrict chunks to a single source",
    )
    top_k: int = Field(8, ge=1, le=20)
    image_style: str = "modern, clean, corporate illustration"


class LinkedInContent(BaseModel):
    """LinkedIn post generated by Prompt 2."""

    post: str
    hashtags: list[str] = Field(default_factory=list)
    char_count: int = 0
    cta_type: Literal["question", "link", "comment-prompt", "poll-suggestion"] = "question"


class XTweetItem(BaseModel):
    """A single tweet in an X thread."""

    tweet_number: int
    text: str
    char_count: int = 0


class XTwitterContent(BaseModel):
    """X/Twitter thread generated by Prompt 2."""

    thread: list[XTweetItem] = Field(default_factory=list)
    hashtags: list[str] = Field(default_factory=list)
    thread_length: int = 0


class ContentGenerateResponse(BaseModel):
    """Response returned by the content-generation endpoint."""

    idea_id: str
    idea_title: str
    linkedin: LinkedInContent
    x_twitter: XTwitterContent
    content_notes: str | None = None
    draft_id: str | None = None
    cover_image_url: str | None = None
