"""AI post generation — supports Groq (free) and Anthropic (paid) providers.

Flow:
1. Retrieve top-5 relevant chunks via vector similarity.
2. Build a structured prompt.
3. Call the configured LLM provider (Groq or Claude).
4. Parse the structured JSON response.
5. Persist the draft to SQLite.
"""

import json
import re
import uuid
from typing import Any

import structlog
from qdrant_client import QdrantClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.draft import Draft
from app.models.idea_bundle import IdeaBundle
from app.models.source import Source
from app.schemas.generation import (
    ContentGenerateRequest,
    ContentGenerateResponse,
    ContentIdea,
    GenerateRequest,
    GenerateResponse,
    LinkedInArticle,
    LinkedInCarouselPost,
    LinkedInPostType,
    LinkedInSinglePost,
    SearchResult,
    XCarousel,
    XPostType,
    XThread,
    XTweet,
)
from app.services.directus import record_workflow_event, sync_draft_to_directus
from app.services.retrieval import semantic_search

logger = structlog.get_logger()

DEFAULT_SYSTEM_PROMPT = (
    "You are a social media content strategist. "
    "Always respond with valid JSON only — no markdown fences, "
    "no commentary, no extra text."
)

EXACT_LINKEDIN_CAROUSEL_SLIDES = 5
EXACT_X_THREAD_MIDDLE_TWEETS = 5
EXACT_X_CAROUSEL_SLIDES = 4

_LINKEDIN_MODELS: dict[str, type] = {
    "single": LinkedInSinglePost,
    "carousel": LinkedInCarouselPost,
    "article": LinkedInArticle,
}

_X_MODELS: dict[str, type] = {
    "tweet": XTweet,
    "thread": XThread,
    "carousel": XCarousel,
}


def _strip_code_fences(raw: str) -> str:
    """Remove markdown code fences (```json ... ```) from LLM output."""
    text = raw.strip()
    text = re.sub(r"^```\w*\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()


def _parse_llm_json(raw_text: str, *, log_context: str) -> dict[str, Any]:
    """Parse the raw LLM response into JSON."""
    cleaned = _strip_code_fences(raw_text)

    try:
        parsed = json.loads(cleaned, strict=False)
    except json.JSONDecodeError as exc:
        logger.error(log_context, raw=raw_text[:300])
        raise RuntimeError(f"LLM returned invalid JSON: {exc}") from exc

    if not isinstance(parsed, dict):
        raise RuntimeError("LLM returned an unexpected JSON shape")

    return parsed


def _validate_linkedin_content(
    linkedin_type: LinkedInPostType | None,
    content: Any,
) -> dict[str, Any] | None:
    """Validate LinkedIn content against the selected output type."""
    if linkedin_type is None:
        return None
    if not isinstance(content, dict):
        raise RuntimeError(f"LinkedIn content must be a JSON object for type '{linkedin_type}'")

    model = _LINKEDIN_MODELS[linkedin_type]
    validated = model.model_validate(content)

    if linkedin_type == "carousel" and len(validated.slides) != EXACT_LINKEDIN_CAROUSEL_SLIDES:
        raise RuntimeError(
            f"LinkedIn carousel must contain exactly {EXACT_LINKEDIN_CAROUSEL_SLIDES} slides"
        )

    return validated.model_dump()


def _validate_x_content(
    x_type: XPostType | None,
    content: Any,
) -> dict[str, Any] | None:
    """Validate X content against the selected output type."""
    if x_type is None:
        return None
    if not isinstance(content, dict):
        raise RuntimeError(f"X content must be a JSON object for type '{x_type}'")

    model = _X_MODELS[x_type]
    validated = model.model_validate(content)

    if x_type == "thread" and len(validated.tweets) != EXACT_X_THREAD_MIDDLE_TWEETS:
        raise RuntimeError(
            f"X thread must contain exactly {EXACT_X_THREAD_MIDDLE_TWEETS} middle tweets"
        )
    if x_type == "carousel" and len(validated.slides) != EXACT_X_CAROUSEL_SLIDES:
        raise RuntimeError(
            f"X carousel must contain exactly {EXACT_X_CAROUSEL_SLIDES} slides"
        )

    return validated.model_dump()


def _validate_generated_payload(
    *,
    generated: dict[str, Any],
    linkedin_type: LinkedInPostType | None,
    x_type: XPostType | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Validate both platform payloads from the model response."""
    linkedin_content = _validate_linkedin_content(linkedin_type, generated.get("linkedin"))
    x_content = _validate_x_content(x_type, generated.get("x"))
    return linkedin_content, x_content


def _serialize_chunks(chunks: list[SearchResult]) -> str:
    """Serialise retrieved chunks into the prompt format used by Prompt 2."""
    chunk_text = ""
    for i, chunk in enumerate(chunks):
        chunk_text += (
            f'  [{i}] (source: "{chunk.source_title}", '
            f"score: {chunk.score:.3f})\n"
            f"  {chunk.text}\n\n"
        )
    return chunk_text


def _retrieve_chunks(
    qdrant: QdrantClient,
    *,
    query: str,
    top_k: int,
    source_id_filter: str | None,
) -> list[SearchResult]:
    """Run semantic search and return the retrieved chunks."""
    return semantic_search(
        qdrant,
        query=query,
        top_k=top_k,
        source_id_filter=source_id_filter,
    )


async def _persist_generated_draft(
    *,
    db: AsyncSession,
    source_id: str,
    linkedin_type: LinkedInPostType | None,
    x_type: XPostType | None,
    linkedin_content: dict[str, Any] | None,
    x_content: dict[str, Any] | None,
    idea_bundle_id: str | None = None,
    idea_id: str | None = None,
) -> Draft:
    """Persist a generated draft and mirror it to Directus."""
    draft = Draft(
        id=str(uuid.uuid4()),
        source_id=source_id,
        idea_bundle_id=idea_bundle_id,
        idea_id=idea_id,
        linkedin_type=linkedin_type,
        x_type=x_type,
        linkedin_content=json.dumps(linkedin_content) if linkedin_content else None,
        x_content=json.dumps(x_content) if x_content else None,
        cover_image_path=None,
        status="pending",
    )
    db.add(draft)
    await db.commit()
    await db.refresh(draft)

    await _mirror_draft_to_directus(
        db=db,
        draft=draft,
        linkedin_content=linkedin_content,
        x_content=x_content,
        cover_image_path=None,
    )
    return draft


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Prompt builder
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _build_prompt(
    request: GenerateRequest,
    source: Source,
    chunks: list[dict],
) -> str:
    """Build a structured XML prompt for Claude with RAG grounding context.

    The prompt includes:
    - Brand voice and target audience directives.
    - Retrieved knowledge-base chunks (with metadata).
    - Platform-specific output format instructions.

    Returns:
        A fully-formed prompt string.
    """

    # Serialise chunks into XML
    chunk_xml = ""
    for i, chunk in enumerate(chunks):
        meta = chunk.get("metadata", {})
        chunk_xml += (
            f'\n    <chunk index="{i}"'
            f' source_title="{chunk.get("source_title", "")}"'
            f' word_count="{meta.get("word_count", "")}">'
            f'\n{chunk.get("text", "")}'
            f"\n    </chunk>"
        )

    # LinkedIn format spec
    linkedin_format = _linkedin_format_spec(request.linkedin_type)

    # X format spec
    x_format = _x_format_spec(request.x_type)

    extra = (
        f"\n<extra_instructions>{request.query_context}</extra_instructions>"
        if request.query_context
        else ""
    )

    return f"""<task>
You are a world-class social media content strategist. Your job is to create engaging,
platform-specific social media posts based on the provided knowledge base content.
</task>

<brand_voice>{request.brand_voice}</brand_voice>
<target_audience>{request.target_audience}</target_audience>
<source_title>{source.title}</source_title>

<knowledge_base>
{chunk_xml}
</knowledge_base>
{extra}

<output_requirements>
You MUST respond with valid JSON only — no markdown fences, no extra text.
Return a JSON object with two top-level keys: "linkedin" and "x".

<linkedin_format>
{linkedin_format}
</linkedin_format>

<x_format>
{x_format}
</x_format>

Full response structure:
{{
    "linkedin": {{ ... linkedin content as specified above ... }},
    "x": {{ ... x/twitter content as specified above ... }}
}}

Rules:
- Ground ALL content in the provided knowledge base chunks. Do not hallucinate facts.
- Match the brand voice described above.
- Tailor language and format to each platform's best practices.
- Use emojis sparingly and appropriately for the platform.
- Make content actionable and valuable to the target audience.
- Each piece of content should be self-contained and compelling.
</output_requirements>"""


def _linkedin_format_spec(linkedin_type: str | None) -> str:
    """Return the LinkedIn output-format instruction block."""
    if linkedin_type == "single":
        return """Generate a LinkedIn single post as JSON:
        {
            "hook": "attention-grabbing opening line (max 80 characters)",
            "body": "main post body (200-300 words, use line breaks for readability)",
            "hashtags": ["relevant", "hashtags"]
        }"""
    if linkedin_type == "carousel":
        return """Generate a LinkedIn carousel post as JSON:
        {
            "intro_caption": "caption text to accompany the carousel",
            "slides": [
                {"headline": "slide headline", "body": "slide body text"}
            ],
            "hashtags": ["relevant", "hashtags"]
        }
        Generate exactly 5 slides."""
    if linkedin_type == "article":
        return """Generate a LinkedIn article as JSON:
        {
            "title": "article title",
            "subtitle": "article subtitle",
            "body": "full article body (600-800 words, well-structured with headers)",
            "hashtags": ["relevant", "hashtags"]
        }"""
    return "No LinkedIn content requested."


def _x_format_spec(x_type: str | None) -> str:
    """Return the X/Twitter output-format instruction block."""
    if x_type == "tweet":
        return """Generate a single tweet as JSON:
        {
            "text": "tweet text (max 260 characters)",
            "hashtags": ["relevant", "hashtags"]
        }"""
    if x_type == "thread":
        return """Generate a Twitter/X thread as JSON:
        {
            "hook_tweet": "attention-grabbing first tweet",
            "tweets": ["tweet 2", "tweet 3", "tweet 4", "tweet 5", "tweet 6"],
            "cta_tweet": "final tweet with call to action",
            "hashtags": ["relevant", "hashtags"]
        }
        Generate exactly 5 middle tweets."""
    if x_type == "carousel":
        return """Generate a Twitter/X carousel as JSON:
        {
            "caption": "carousel caption (max 240 characters)",
            "slides": [
                {"headline": "slide headline"}
            ]
        }
        Generate exactly 4 slides."""
    return "No X/Twitter content requested."


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Main generation function
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


async def generate_posts(
    *,
    db: AsyncSession,
    qdrant: QdrantClient,
    source: Source,
    request: GenerateRequest,
) -> GenerateResponse:
    """Generate platform-specific social media posts for a source.

    Orchestrates the full pipeline: RAG retrieval → prompt building →
    LLM API call → payload validation → draft persistence.

    Args:
        db: Async database session.
        qdrant: Qdrant client.
        source: The source ORM object to generate posts from.
        request: Generation parameters (types, voice, audience, etc.).

    Returns:
        A ``GenerateResponse`` containing the draft ID and generated content.

    Raises:
        RuntimeError: If the LLM API call fails.
    """

    query = request.query_context or source.title
    search_results = _retrieve_chunks(
        qdrant,
        query=query,
        top_k=5,
        source_id_filter=source.id,
    )
    chunks = [
        {
            "text": r.text,
            "source_title": r.source_title,
            "metadata": r.metadata,
        }
        for r in search_results
    ]

    # ── 2. Build prompt ──────────────────────────────────────
    prompt = _build_prompt(request, source, chunks)

    raw_text = await _call_llm(prompt)
    generated = _parse_llm_json(raw_text, log_context="Failed to parse LLM response as JSON")
    linkedin_content, x_content = _validate_generated_payload(
        generated=generated,
        linkedin_type=request.linkedin_type,
        x_type=request.x_type,
    )

    draft = await _persist_generated_draft(
        db=db,
        source_id=source.id,
        linkedin_type=request.linkedin_type,
        x_type=request.x_type,
        linkedin_content=linkedin_content,
        x_content=x_content,
    )

    logger.info("Generated posts", draft_id=draft.id, source_id=source.id)

    return GenerateResponse(
        draft_id=draft.id,
        source_id=source.id,
        linkedin_type=request.linkedin_type,
        x_type=request.x_type,
        linkedin_content=linkedin_content,
        x_content=x_content,
        cover_image_url=None,
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LLM provider dispatch
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


async def _call_llm(
    prompt: str,
    *,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
) -> str:
    """Dispatch a prompt to the configured LLM provider and return raw text.

    Provider priority: openrouter → groq → anthropic
    Set GENERATION_PROVIDER in .env to choose.
    """
    if settings.generation_provider == "openrouter":
        return await _call_openrouter(prompt, system_prompt=system_prompt)
    if settings.generation_provider == "groq":
        return await _call_groq(prompt, system_prompt=system_prompt)
    return await _call_anthropic(prompt, system_prompt=system_prompt)


async def _call_openrouter(
    prompt: str,
    *,
    system_prompt: str,
) -> str:
    """Call OpenRouter using the OpenAI-compatible SDK.

    Default model: meta-llama/llama-3.3-70b-instruct
    OpenRouter routes to the best available provider for the model.
    Docs: https://openrouter.ai/docs
    """
    try:
        import openai

        client = openai.AsyncOpenAI(
            api_key=settings.openrouter_api_key,
            base_url="https://openrouter.ai/api/v1",
            default_headers={
                "HTTP-Referer": settings.openrouter_site_url,
                "X-Title": settings.openrouter_site_name,
            },
        )
        response = await client.chat.completions.create(
            model=settings.openrouter_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            max_tokens=4096,
            temperature=0.7,
        )
        return response.choices[0].message.content or ""
    except openai.APIConnectionError as exc:
        logger.error("OpenRouter connection error", error=str(exc))
        raise RuntimeError(f"OpenRouter connection error: {exc}") from exc
    except openai.RateLimitError as exc:
        logger.error("OpenRouter rate limit", error=str(exc))
        raise RuntimeError(f"OpenRouter rate limited: {exc}") from exc
    except openai.APIStatusError as exc:
        logger.error("OpenRouter API error", status=exc.status_code, error=str(exc))
        raise RuntimeError(f"OpenRouter API error ({exc.status_code}): {exc}") from exc


async def _call_groq(
    prompt: str,
    *,
    system_prompt: str,
) -> str:
    """Call Groq's OpenAI-compatible chat API (fallback provider)."""
    try:
        import openai

        client = openai.AsyncOpenAI(
            api_key=settings.groq_api_key,
            base_url="https://api.groq.com/openai/v1",
        )
        response = await client.chat.completions.create(
            model=settings.groq_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            max_tokens=4096,
            temperature=0.7,
        )
        return response.choices[0].message.content or ""
    except openai.APIConnectionError as exc:
        logger.error("Groq API connection error", error=str(exc))
        raise RuntimeError(f"Groq API connection error: {exc}") from exc
    except openai.RateLimitError as exc:
        logger.error("Groq API rate limit", error=str(exc))
        raise RuntimeError(f"Groq API rate limited: {exc}") from exc
    except openai.APIStatusError as exc:
        logger.error("Groq API error", status=exc.status_code, error=str(exc))
        raise RuntimeError(f"Groq API error ({exc.status_code}): {exc}") from exc


async def _call_anthropic(
    prompt: str,
    *,
    system_prompt: str,
) -> str:
    """Call the Anthropic Claude API for content generation."""
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        message = client.messages.create(
            model=settings.claude_model,
            max_tokens=4096,
            system=system_prompt,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text
    except Exception as exc:
        logger.error("Anthropic API error", error=str(exc))
        raise RuntimeError(f"Anthropic API error: {exc}") from exc


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Prompt 2 — content generation from approved idea
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

CONTENT_SYSTEM_PROMPT = """\
You are a professional B2B social media copywriter for DataClap Digital \
(www.dataclap.digital).

About DataClap Digital:
DataClap Digital provides AI training data annotation services across multiple \
industries — including data labelling, model evaluation, NLP annotation, \
computer vision data, and quality assurance for machine learning pipelines. \
DataClap helps enterprises and AI teams build better-trained, more reliable models \
by delivering high-quality human-annotated datasets at scale.

Your job:
Write high-performing LinkedIn and X (Twitter) posts that position DataClap Digital \
as a thought leader in AI data annotation, ML data quality, and the industries it serves. \
Every post must be grounded exclusively in the retrieved knowledge base chunks and the \
approved idea brief provided. Do NOT invent claims, statistics, or quotes not found in \
the provided context.

Voice & Tone:
- LinkedIn: authoritative, insight-driven, professional but human. \
Short paragraphs. Strategic line breaks. Ends with a discussion-provoking question or CTA. \
Mention DataClap Digital or link back to the domain where it feels natural.
- X / Twitter: punchy, opinionated, scroll-stopping. \
Each tweet must stand alone and be clear without context from surrounding tweets.

Rules:
- BASE every factual claim on the retrieved chunks. If the chunks don't support a claim, don't make it.
- DataClap Digital is the narrator/publisher — write as if DataClap is sharing insight, not just reporting news.
- Never use corporate jargon or fluff (e.g. 'synergize', 'leverage our robust solutions').
- Respect the requested LinkedIn and X post types exactly — structure, slide counts, character limits.
- Include 3-5 relevant hashtags per platform only when the format calls for hashtags.
- Return ONLY valid JSON. No preamble, no markdown fences, no commentary outside the JSON."""


def _build_content_prompt(
    request: ContentGenerateRequest,
    idea: ContentIdea,
    chunks: list[SearchResult],
) -> str:
    """Build the RAG-grounded user prompt for content generation (Prompt 2).

    The prompt explicitly instructs the model to:
    - Use the retrieved knowledge chunks as the ONLY factual source
    - Position DataClap Digital as the narrator/publisher
    - Match the requested post types exactly
    """
    # Serialise only the fields relevant to the copywriter (drop raw research JSON)
    idea_brief = {
        "id": idea.id,
        "title": idea.title,
        "angle": idea.angle,
        "core_hook": idea.core_hook,
        "knowledge_source": idea.knowledge_source,
        "trend_source": idea.trend_source,
        "target_audience": idea.target_audience,
        "engagement_potential": idea.engagement_potential,
        "engagement_reasoning": idea.engagement_reasoning,
        "suggested_formats": idea.suggested_formats,
    }
    idea_json = json.dumps(idea_brief, indent=2)
    chunk_text = _serialize_chunks(chunks)
    linkedin_format = _linkedin_format_spec(request.linkedin_type)
    x_format = _x_format_spec(request.x_type)

    no_chunks_warning = (
        "\n⚠️  No chunks retrieved — use the idea brief alone and note "
        "this in content_notes."
        if not chunks
        else ""
    )

    return f"""\
You are writing social media posts for DataClap Digital (www.dataclap.digital).

STEP 1 — Read the approved idea brief:
<idea_brief>
{idea_json}
</idea_brief>

STEP 2 — Read the retrieved knowledge base chunks. \
These are the ONLY facts you are allowed to use in the post. \
Do not introduce any statistics, quotes, or claims not present in these chunks.{no_chunks_warning}
<retrieved_chunks>
{chunk_text}
</retrieved_chunks>

STEP 3 — Generate the posts.

Publisher: DataClap Digital (write from DataClap's perspective as the expert sharing insight)
Brand voice: {request.brand_voice}
Target audience: {request.target_audience}

Platform types requested:
- LinkedIn: {request.linkedin_type}
- X: {request.x_type}

Instructions:
1. The core_hook in the idea brief is your opening — use it or a close adaptation.
2. Build the body using facts from the retrieved chunks only.
3. The post should feel like DataClap Digital is sharing expert insight, \
not just summarising a news article.
4. End LinkedIn posts with a CTA or discussion question.
5. Keep X posts punchy — every tweet must work standalone.

Return ONLY a JSON object in this exact schema (no preamble, no markdown fences):

{{
  "idea_id": "{idea.id}",
  "idea_title": "{idea.title}",
  "linkedin": {{ {linkedin_format.strip()} }},
  "x": {{ {x_format.strip()} }},
  "content_notes": "1-2 sentences: which chunk(s) most informed this content, \
and anything the reviewer should know before publishing"
}}

Full LinkedIn format spec:
{linkedin_format}

Full X format spec:
{x_format}
"""


async def generate_content_from_idea(
    *,
    db: AsyncSession,
    qdrant: QdrantClient,
    request: ContentGenerateRequest,
) -> ContentGenerateResponse:
    """Generate platform-ready content from a selected idea (Prompt 2 flow).

    1. Load the IdeaBundle and selected idea from the database.
    2. Retrieve supporting chunks from the vector DB.
    3. Build Prompt 2 using the idea brief + chunks.
    4. Call the LLM.
    5. Parse and validate the structured response.
    6. Persist as a draft linked to the idea bundle.
    """

    from sqlalchemy import select

    # 1. Load the idea from the stored bundle
    result = await db.execute(
        select(IdeaBundle).where(IdeaBundle.id == request.bundle_id)
    )
    bundle = result.scalar_one_or_none()
    if not bundle:
        raise RuntimeError(f"Idea bundle '{request.bundle_id}' not found")

    if not bundle.ideas:
        raise RuntimeError("Bundle has no ideas")

    try:
        ideas_raw = json.loads(bundle.ideas)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Corrupted ideas data in bundle") from exc

    idea_data = None
    for item in ideas_raw:
        if item.get("id") == request.idea_id:
            idea_data = item
            break
    if idea_data is None:
        raise RuntimeError(
            f"Idea '{request.idea_id}' not found in bundle '{request.bundle_id}'"
        )

    idea = ContentIdea(**idea_data)

    # 2. Retrieve supporting chunks
    # Enrich the query with industry + DataClap-specific vocabulary so BGE
    # retrieves chunks relevant to annotation and training data, not just the title.
    industry = getattr(bundle, "industry", "") or ""
    search_query = (
        f"{idea.title} {industry} annotation training data labelling"
        if industry
        else f"{idea.title} annotation training data labelling"
    ).strip()
    search_results = _retrieve_chunks(
        qdrant,
        query=search_query,
        top_k=request.top_k,
        source_id_filter=request.source_id,
    )

    # 3. Build prompt
    user_prompt = _build_content_prompt(
        request=request,
        idea=idea,
        chunks=search_results,
    )

    # 4. Call LLM
    raw_text = await _call_llm(user_prompt, system_prompt=CONTENT_SYSTEM_PROMPT)

    # 5. Parse and validate
    parsed = _parse_llm_json(
        raw_text,
        log_context="Failed to parse content generation response",
    )
    linkedin_content, x_content = _validate_generated_payload(
        generated=parsed,
        linkedin_type=request.linkedin_type,
        x_type=request.x_type,
    )

    # 6. Persist draft with idea bundle link
    draft = await _persist_generated_draft(
        db=db,
        source_id=request.source_id or "ideas",
        linkedin_type=request.linkedin_type,
        x_type=request.x_type,
        linkedin_content=linkedin_content,
        x_content=x_content,
        idea_bundle_id=request.bundle_id,
        idea_id=request.idea_id,
    )

    logger.info(
        "Generated content from idea",
        idea_id=idea.id,
        bundle_id=request.bundle_id,
        draft_id=draft.id,
    )

    return ContentGenerateResponse(
        bundle_id=request.bundle_id,
        idea_id=parsed.get("idea_id", idea.id),
        idea_title=parsed.get("idea_title", idea.title),
        linkedin_type=request.linkedin_type,
        x_type=request.x_type,
        linkedin_content=linkedin_content,
        x_content=x_content,
        content_notes=parsed.get("content_notes"),
        draft_id=draft.id,
        cover_image_url=None,
    )


async def _mirror_draft_to_directus(
    *,
    db: AsyncSession,
    draft: Draft,
    linkedin_content: dict | None,
    x_content: dict | None,
    cover_image_path: str | None,
) -> None:
    """Best-effort mirror of a generated draft into Directus."""
    try:
        record = await sync_draft_to_directus(
            draft,
            linkedin_content=linkedin_content,
            x_content=x_content,
            cover_image_path=cover_image_path,
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

    try:
        await record_workflow_event(
            entity_type="draft",
            entity_legacy_id=draft.id,
            event_type="draft.generated",
            payload={"source_id": draft.source_id, "linkedin_type": draft.linkedin_type, "x_type": draft.x_type},
            source="social-content-engine",
            occurred_at=draft.created_at,
        )
    except Exception as exc:
        logger.warning("Directus workflow event failed", draft_id=draft.id, error=str(exc))
