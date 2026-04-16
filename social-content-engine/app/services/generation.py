"""AI post generation — supports Groq (free) and Anthropic (paid) providers.

Flow:
1. Retrieve top-5 relevant chunks via vector similarity.
2. Build a structured prompt.
3. Call the configured LLM provider (Groq or Claude).
4. Parse the structured JSON response.
5. Generate a cover image via Gemini (optional).
6. Persist the draft to SQLite.
"""

import json
import re
import uuid

import structlog
from qdrant_client import QdrantClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.draft import Draft
from app.models.source import Source
from app.schemas.generation import (
    ContentGenerateRequest,
    ContentGenerateResponse,
    ContentIdea,
    GenerateRequest,
    GenerateResponse,
    LinkedInContent,
    SearchResult,
    XTweetItem,
    XTwitterContent,
)
from app.services.image_gen import generate_image
from app.services.retrieval import semantic_search

logger = structlog.get_logger()


def _strip_code_fences(raw: str) -> str:
    """Remove markdown code fences (```json ... ```) from LLM output."""
    text = raw.strip()
    text = re.sub(r"^```\w*\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()


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
            "hashtags": ["relevant", "hashtags"],
            "image_description": "description of an ideal cover image for this post"
        }"""
    if linkedin_type == "carousel":
        return """Generate a LinkedIn carousel post as JSON:
        {
            "intro_caption": "caption text to accompany the carousel",
            "slides": [
                {"headline": "slide headline", "body": "slide body text", "image_description": "visual for this slide"}
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
            "hashtags": ["relevant", "hashtags"],
            "image_description": "description of an ideal cover image"
        }"""
    return "No LinkedIn content requested."


def _x_format_spec(x_type: str | None) -> str:
    """Return the X/Twitter output-format instruction block."""
    if x_type == "tweet":
        return """Generate a single tweet as JSON:
        {
            "text": "tweet text (max 260 characters)",
            "hashtags": ["relevant", "hashtags"],
            "image_description": "description of an ideal image for this tweet"
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
                {"headline": "slide headline", "image_description": "visual description"}
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
    LLM API call → image generation → draft persistence.

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

    # ── 1. Retrieve relevant chunks ──────────────────────────
    query = request.query_context or source.title
    search_results = semantic_search(
        qdrant,
        query=query,
        top_k=5,
        source_id_filter=request.source_id,
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

    # ── 3. Call LLM provider ───────────────────────────────
    raw_text = await _call_llm(prompt)

    # ── 4. Parse response ────────────────────────────────────
    cleaned = _strip_code_fences(raw_text)

    try:
        generated: dict = json.loads(cleaned, strict=False)
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse LLM response as JSON", raw=raw_text[:200])
        raise RuntimeError(f"LLM returned invalid JSON: {exc}") from exc

    linkedin_content: dict | None = generated.get("linkedin")
    x_content: dict | None = generated.get("x")

    # ── 5. Generate cover image ──────────────────────────────
    cover_image_path: str | None = None
    image_desc = _extract_image_description(linkedin_content, x_content)

    if image_desc:
        try:
            cover_image_path = await generate_image(
                prompt=f"{image_desc}. Style: {request.image_style}",
                filename_prefix=f"draft_{source.id[:8]}",
            )
        except Exception as exc:
            logger.warning("Image generation failed, continuing without image", error=str(exc))

    # ── 6. Save draft ────────────────────────────────────────
    draft = Draft(
        id=str(uuid.uuid4()),
        source_id=source.id,
        linkedin_type=request.linkedin_type,
        x_type=request.x_type,
        linkedin_content=json.dumps(linkedin_content) if linkedin_content else None,
        x_content=json.dumps(x_content) if x_content else None,
        cover_image_path=cover_image_path,
        status="pending",
    )
    db.add(draft)
    await db.commit()
    await db.refresh(draft)

    logger.info("Generated posts", draft_id=draft.id, source_id=source.id)

    return GenerateResponse(
        draft_id=draft.id,
        source_id=source.id,
        linkedin_type=request.linkedin_type,
        x_type=request.x_type,
        linkedin_content=linkedin_content,
        x_content=x_content,
        cover_image_url=f"/storage/images/{cover_image_path}" if cover_image_path else None,
    )


def _extract_image_description(
    linkedin_content: dict | None,
    x_content: dict | None,
) -> str | None:
    """Pull the best image description from generated content."""
    if linkedin_content:
        desc = linkedin_content.get("image_description")
        if desc:
            return desc
        slides = linkedin_content.get("slides")
        if slides and isinstance(slides, list) and slides:
            return slides[0].get("image_description")
    if x_content:
        desc = x_content.get("image_description")
        if desc:
            return desc
    return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LLM provider dispatch
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


async def _call_llm(prompt: str) -> str:
    """Dispatch a prompt to the configured LLM provider and return raw text.

    Returns:
        The raw text response from the model.

    Raises:
        RuntimeError: On any API or network error.
    """
    if settings.generation_provider == "groq":
        return await _call_groq(prompt)
    return await _call_anthropic(prompt)


async def _call_groq(prompt: str) -> str:
    """Call Groq's OpenAI-compatible chat API using the ``openai`` SDK.

    Groq offers free-tier access to Llama 3.3 70B and other models.
    """
    try:
        import openai

        client = openai.AsyncOpenAI(
            api_key=settings.groq_api_key,
            base_url="https://api.groq.com/openai/v1",
        )
        response = await client.chat.completions.create(
            model=settings.groq_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a social media content strategist. "
                        "Always respond with valid JSON only — no markdown fences, "
                        "no commentary, no extra text."
                    ),
                },
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


async def _call_anthropic(prompt: str) -> str:
    """Call the Anthropic Claude API for content generation."""
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        message = client.messages.create(
            model=settings.claude_model,
            max_tokens=4096,
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
(www.dataclap.digital). You write high-performing content for LinkedIn and \
X (Twitter) that builds thought leadership in data engineering, cloud, \
analytics, and AI.

Voice & Tone:
- LinkedIn: authoritative, insight-driven, professional but human. Use short \
paragraphs. Strategic use of line breaks. Ends with a discussion-provoking \
question or CTA.
- X / Twitter: punchy, opinionated, scroll-stopping. Thread format when depth \
is needed. First tweet must hook in under 12 words.

Rules:
- Never use corporate jargon or fluff (e.g. "synergize", "leverage our robust \
solutions").
- Ground claims in the provided idea brief and retrieved chunks only.
- Include relevant hashtags (3-5 max per platform).
- LinkedIn posts: 150-300 words. X threads: 5-8 tweets, each under 280 \
characters.
- Return ONLY valid JSON. No commentary outside the JSON block."""


def _build_content_prompt(
    idea: ContentIdea,
    chunks: list[SearchResult],
) -> str:
    """Build the user prompt for content generation (Prompt 2)."""
    idea_json = json.dumps(idea.model_dump(), indent=2)

    chunk_xml = ""
    for i, chunk in enumerate(chunks):
        chunk_xml += (
            f'  [{i}] (source: "{chunk.source_title}", '
            f"score: {chunk.score:.3f})\n"
            f"  {chunk.text}\n\n"
        )

    return f"""\
You are generating social media content for DataClap Digital based on an \
approved content idea.

Approved idea brief:
<idea_brief>
{idea_json}
</idea_brief>

Supporting knowledge chunks for factual grounding:
<retrieved_chunks>
{chunk_xml}
</retrieved_chunks>

Brand context:
- Company: DataClap Digital
- Website: www.dataclap.digital
- Domains: Data Engineering, Cloud Infrastructure, Analytics, AI/ML, Digital \
Transformation
- Tone: Expert but approachable. Confident, not arrogant.

Task:
Generate ready-to-publish content for BOTH platforms based on the idea brief \
above.

Return ONLY a JSON object in this exact schema:

{{
  "idea_id": "matching id from idea brief",
  "idea_title": "matching title from idea brief",
  "linkedin": {{
    "post": "Full LinkedIn post text (150-300 words). Use line breaks between \
paragraphs. Include a CTA or discussion question at the end.",
    "hashtags": ["#Tag1", "#Tag2", "#Tag3"],
    "char_count": 0,
    "cta_type": "question | link | comment-prompt | poll-suggestion"
  }},
  "x_twitter": {{
    "thread": [
      {{
        "tweet_number": 1,
        "text": "Hook tweet — under 280 chars, stops the scroll",
        "char_count": 0
      }},
      {{
        "tweet_number": 2,
        "text": "Context / setup tweet",
        "char_count": 0
      }},
      {{
        "tweet_number": 3,
        "text": "Core insight or data point",
        "char_count": 0
      }},
      {{
        "tweet_number": 4,
        "text": "Practical takeaway or example",
        "char_count": 0
      }},
      {{
        "tweet_number": 5,
        "text": "Closing opinion or provocative question + hashtags",
        "char_count": 0
      }}
    ],
    "hashtags": ["#Tag1", "#Tag2", "#Tag3"],
    "thread_length": 5
  }},
  "content_notes": "Optional: 1-2 sentences on tone choices or anything the \
human reviewer should know before publishing"
}}"""


async def generate_content_from_idea(
    *,
    db: AsyncSession,
    qdrant: QdrantClient,
    request: ContentGenerateRequest,
) -> ContentGenerateResponse:
    """Generate platform-ready content from an approved idea (Prompt 2 flow).

    1. Retrieve supporting chunks from the vector DB.
    2. Build Prompt 2 using the idea brief + chunks.
    3. Call the LLM.
    4. Parse the structured response.
    5. Optionally generate a cover image.
    6. Persist as a draft.
    """

    # 1. Retrieve supporting chunks
    search_results = semantic_search(
        qdrant,
        query=request.query,
        top_k=request.top_k,
        source_id_filter=request.source_id,
    )

    # 2. Build prompt
    user_prompt = _build_content_prompt(
        idea=request.idea,
        chunks=search_results,
    )

    # 3. Call LLM with content system prompt
    raw_text = await _call_llm_content(user_prompt)

    # 4. Parse response
    cleaned = _strip_code_fences(raw_text)

    try:
        parsed: dict = json.loads(cleaned, strict=False)
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse content generation response", raw=raw_text[:300])
        raise RuntimeError(f"LLM returned invalid JSON: {exc}") from exc

    # 5. Build typed response
    li_data = parsed.get("linkedin", {})
    x_data = parsed.get("x_twitter", {})

    linkedin = LinkedInContent(
        post=li_data.get("post", ""),
        hashtags=li_data.get("hashtags", []),
        char_count=li_data.get("char_count", len(li_data.get("post", ""))),
        cta_type=li_data.get("cta_type", "question"),
    )

    x_thread_items = [
        XTweetItem(
            tweet_number=t.get("tweet_number", idx + 1),
            text=t.get("text", ""),
            char_count=t.get("char_count", len(t.get("text", ""))),
        )
        for idx, t in enumerate(x_data.get("thread", []))
    ]
    x_twitter = XTwitterContent(
        thread=x_thread_items,
        hashtags=x_data.get("hashtags", []),
        thread_length=x_data.get("thread_length", len(x_thread_items)),
    )

    # 6. Generate cover image (optional)
    cover_image_path: str | None = None
    try:
        cover_image_path = await generate_image(
            prompt=(
                f"Social media cover image for: {request.idea.title}. "
                f"Style: {request.image_style}"
            ),
            filename_prefix=f"idea_{request.idea.id}",
        )
    except Exception as exc:
        logger.warning("Image generation failed, continuing without image", error=str(exc))

    # 7. Save as draft
    draft = Draft(
        id=str(uuid.uuid4()),
        source_id=request.source_id or "ideas",
        linkedin_type="single",
        x_type="thread",
        linkedin_content=json.dumps(linkedin.model_dump()),
        x_content=json.dumps(x_twitter.model_dump()),
        cover_image_path=cover_image_path,
        status="pending",
    )
    db.add(draft)
    await db.commit()
    await db.refresh(draft)

    logger.info(
        "Generated content from idea",
        idea_id=request.idea.id,
        draft_id=draft.id,
    )

    return ContentGenerateResponse(
        idea_id=parsed.get("idea_id", request.idea.id),
        idea_title=parsed.get("idea_title", request.idea.title),
        linkedin=linkedin,
        x_twitter=x_twitter,
        content_notes=parsed.get("content_notes"),
        draft_id=draft.id,
        cover_image_url=f"/storage/images/{cover_image_path}" if cover_image_path else None,
    )


async def _call_llm_content(user_prompt: str) -> str:
    """Call the configured LLM with the content-generation system prompt."""
    if settings.generation_provider == "groq":
        return await _call_groq_content(user_prompt)
    return await _call_anthropic_content(user_prompt)


async def _call_groq_content(user_prompt: str) -> str:
    import openai

    client = openai.AsyncOpenAI(
        api_key=settings.groq_api_key,
        base_url="https://api.groq.com/openai/v1",
    )
    try:
        response = await client.chat.completions.create(
            model=settings.groq_model,
            messages=[
                {"role": "system", "content": CONTENT_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
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


async def _call_anthropic_content(user_prompt: str) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    try:
        message = client.messages.create(
            model=settings.claude_model,
            max_tokens=4096,
            system=CONTENT_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return message.content[0].text
    except Exception as exc:
        logger.error("Anthropic API error", error=str(exc))
        raise RuntimeError(f"Anthropic API error: {exc}") from exc
