"""Idea generation service — Prompt 1.

Takes an industry from the user, retrieves relevant PDF chunks,
runs deep online research via Parallel API, combines both, and
generates 5 high-impact content ideas.  Everything is persisted
to the ``idea_bundles`` table for later retrieval.
"""

import asyncio
import json
import re
import uuid
from datetime import datetime, timezone

import structlog
from qdrant_client import QdrantClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.idea_bundle import IdeaBundle
from app.schemas.generation import (
    ContentIdea,
    IdeaGenerateRequest,
    IdeaGenerateResponse,
    SearchResult,
)
from app.services.retrieval import semantic_search
from app.services.research import run_deep_research

logger = structlog.get_logger()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Prompt builder
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

IDEA_SYSTEM_PROMPT = """\
You are a senior content strategist for DataClap Digital (www.dataclap.digital).

About DataClap Digital:
DataClap Digital is a specialist AI training data annotation company. It provides:
  • Data labelling and annotation (text, image, audio, video)
  • NLP annotation — NER, intent classification, sentiment, coreference, QA pairs
  • Computer vision data — bounding boxes, segmentation, keypoints, 3D annotation
  • Model evaluation and quality assurance for ML pipelines
  • Industry-specific annotation across fintech, healthtech, autonomous vehicles,
    e-commerce, legal tech, and more

Target audience for DataClap's content:
  • ML engineers and data scientists building or scaling models
  • AI product managers and heads of AI
  • Data science leads and ML platform teams at enterprises
  • Founders and CTOs of AI-first startups
  • Anyone responsible for training data quality

Your job:
Generate 5 high-impact content ideas that position DataClap Digital as the
annotation and training-data authority. Every idea must:
  1. Be grounded in at least one retrieved knowledge chunk OR one web research finding.
  2. Carry a DataClap angle — connect the industry trend to the need for
     high-quality annotated training data, labelling pipelines, or data quality.
  3. Be timely (trending RIGHT NOW), original, and have strong engagement potential.

Rules:
  - Do NOT invent technical claims not supported by context.
  - Do NOT generate generic AI hype content. Be specific and data-driven.
  - Be concise. No preamble. No filler. Return only valid JSON."""


def _build_idea_prompt(
    chunks: list[SearchResult],
    trending_topics: list[str],
    current_date: str,
    research_data: dict | None = None,
) -> str:
    """Build the user prompt for idea generation (Prompt 1)."""
    chunk_xml = ""
    for i, chunk in enumerate(chunks):
        content_type = chunk.metadata.get("content_type", "general")
        chunk_xml += (
            f'  [{i}] (source: "{chunk.source_title}", '
            f"score: {chunk.score:.3f}, type: {content_type})\n"
            f"  {chunk.text}\n\n"
        )

    trending_xml = "\n".join(f"  - {t}" for t in trending_topics)

    # Add research data if available (structured intelligence report from Parallel + LLM)
    research_section = ""
    if research_data:
        research_section = "\n\n<domain_intelligence>\n"
        research_section += "The following structured intelligence report was generated from live web search.\n"
        research_section += "Use these real sources, data points, and insights to ground your ideas.\n\n"

        # Top-level findings
        if research_data.get("intelligence_summary"):
            research_section += f"Intelligence Summary:\n  {research_data['intelligence_summary']}\n\n"

        if research_data.get("top_research_finding"):
            research_section += f"Top Research Finding:\n  {research_data['top_research_finding']}\n\n"

        if research_data.get("top_open_problem"):
            research_section += f"Top Open Problem:\n  {research_data['top_open_problem']}\n\n"

        angles = research_data.get("angles", {})

        # Technology Updates (★ contextual)
        tech = angles.get("technology_updates", {})
        if tech.get("headline"):
            research_section += f"── Technology Updates ──\n  {tech['headline']}\n"
            for s in tech.get("sources", []):
                research_section += (
                    f"  • [{s.get('index', '?')}] {s.get('title', 'N/A')}\n"
                    f"    URL: {s.get('url', '')}\n"
                    f"    Key fact: {s.get('key_fact', '')}\n"
                )
            research_section += "\n"

        # Research & Benchmarks (★★★ primary)
        rb = angles.get("research_and_benchmarks", {})
        if rb.get("headline"):
            research_section += f"── Research & Benchmarks (PRIMARY) ──\n  {rb['headline']}\n"
            for s in rb.get("sources", []):
                research_section += (
                    f"  • [{s.get('index', '?')}] {s.get('title', 'N/A')}\n"
                    f"    URL: {s.get('url', '')}\n"
                    f"    Published: {s.get('published_date', 'N/A')}\n"
                    f"    Key fact: {s.get('key_fact', '')}\n"
                    f"    Methodology: {s.get('methodology_note', '')}\n"
                    f"    Type: {s.get('contribution_type', '')}\n"
                )
            research_section += "\n"

        # Real-World Deployments (★★ supporting)
        rw = angles.get("real_world_deployments", {})
        if rw.get("headline"):
            research_section += f"── Real-World Deployments ──\n  {rw['headline']}\n"
            for s in rw.get("sources", []):
                research_section += (
                    f"  • [{s.get('index', '?')}] {s.get('title', 'N/A')}\n"
                    f"    URL: {s.get('url', '')}\n"
                    f"    Key fact: {s.get('key_fact', '')}\n"
                )
            research_section += "\n"

        # Challenges & Gaps (★★★ primary)
        cg = angles.get("challenges_and_gaps", {})
        if cg.get("headline"):
            research_section += f"── Challenges & Gaps (PRIMARY) ──\n  {cg['headline']}\n"
            if cg.get("gap_cluster_summary"):
                research_section += "  Gap Clusters:\n"
                for g in cg["gap_cluster_summary"]:
                    research_section += f"    → {g.get('theme', '?')}: {g.get('description', '')}\n"
            for s in cg.get("sources", []):
                research_section += (
                    f"  • [{s.get('index', '?')}] {s.get('title', 'N/A')}\n"
                    f"    URL: {s.get('url', '')}\n"
                    f"    Key fact: {s.get('key_fact', '')}\n"
                    f"    Gap type: {s.get('gap_type', '')}\n"
                )
            research_section += "\n"

        # Content opportunities
        opps = research_data.get("content_opportunities", [])
        if opps:
            research_section += "── Content Opportunities Identified ──\n"
            for opp in opps:
                research_section += (
                    f"  • [{opp.get('angle', '')}] {opp.get('suggested_topic', '')}\n"
                    f"    Why now: {opp.get('why_now', '')}\n"
                    f"    Best source: {opp.get('best_source_index', '')}\n"
                )

        research_section += "</domain_intelligence>"

    return f"""\
You have access to the following retrieved knowledge chunks from DataClap's \
internal knowledge base (each chunk is tagged with its content type — \
statistic, opinion, how-to, definition, case-study, or general):

<retrieved_chunks>
{chunk_xml}
</retrieved_chunks>

You also have access to the following trending topics and signals relevant to \
the requested industry:

<trending_signals>
{trending_xml}
</trending_signals>{research_section}

Today's date: {current_date}
Publisher: DataClap Digital (www.dataclap.digital)
DataClap's domain: AI training data annotation — labelling, NLP annotation,
  computer vision data, model evaluation, QA for ML pipelines.

Task:
Generate exactly 5 content ideas by cross-referencing the retrieved chunks \
with the trending signals and research insights. For each idea:

1. Identify the core intersection (what from the chunks + what from trends \
makes this timely).
2. Define the content angle (opinion, how-to, case study, data story, myth-busting).
3. Define the DataClap angle: HOW does this idea connect to annotation quality,
   labelling pipelines, or the need for better AI training data? \
   Every idea MUST have a clear DataClap annotation angle.
4. Prioritise chunks tagged as "statistic" or "case-study" for data-driven ideas.
5. Estimate engagement potential (High / Medium) with one-line reasoning.

Return ONLY a JSON object in this exact schema:

{{
  "generated_at": "ISO8601 timestamp",
  "context_summary": "2-sentence summary of the key themes found in chunks + trends + their relevance to DataClap's annotation services",
  "ideas": [
    {{
      "id": "idea_1",
      "title": "Short punchy working title",
      "angle": "opinion | how-to | case-study | data-story | myth-busting",
      "core_hook": "The single most compelling sentence about why this idea matters RIGHT NOW",
      "dataclap_angle": "One sentence: how this connects to annotation quality, labelling, or ML training data",
      "knowledge_source": "Which chunk(s) informed this idea (reference by chunk index)",
      "trend_source": "Which trending signal this taps into",
      "target_audience": "Who on LinkedIn/X will care most",
      "engagement_potential": "High | Medium",
      "engagement_reasoning": "One sentence why",
      "suggested_formats": ["linkedin_post", "x_thread"]
    }}
  ]
}}"""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Main function
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


async def generate_ideas(
    *,
    db: AsyncSession,
    qdrant: QdrantClient,
    request: IdeaGenerateRequest,
) -> IdeaGenerateResponse:
    """Retrieve chunks by industry, run deep research, generate 5 ideas, persist to DB."""

    industry = request.industry
    service_description = request.service_description or ""

    # 1. Derive a search query from the industry
    search_query = f"{industry} trends insights strategy"

    # 2. Retrieve relevant PDF chunks from the knowledge base
    search_results = semantic_search(
        qdrant,
        query=search_query,
        top_k=request.top_k,
        category_filter=request.category_filter,
    )

    # Serialise chunks for DB storage
    chunks_for_db = [
        {
            "chunk_id": r.chunk_id,
            "source_id": r.source_id,
            "source_title": r.source_title,
            "text": r.text,
            "score": r.score,
        }
        for r in search_results
    ]

    # 3. Run deep research on the industry (Parallel API)
    research_data = None
    research_insights = None
    research_sources: list[dict] = []

    if settings.parallel_api_key:
        try:
            research_data = await asyncio.wait_for(
                run_deep_research(
                    topic=industry,
                    service_description=service_description,
                    trending_angle=f"latest {industry} trends and developments",
                    category=industry,
                    max_resources=5,
                ),
                timeout=60.0,
            )
            logger.info("Deep research completed", industry=industry, sources=research_data.get("total_sources_found", 0))

            if research_data:
                # Extract insights from the structured intelligence report
                research_insights = research_data.get("intelligence_summary", "")
                # Collect all sources from all angles
                research_sources = []
                for angle_data in research_data.get("angles", {}).values():
                    if isinstance(angle_data, dict):
                        for src in angle_data.get("sources", []):
                            if isinstance(src, dict):
                                research_sources.append(src)
        except asyncio.TimeoutError:
            logger.warning("Research service timed out", industry=industry)
            research_data = None
        except Exception as e:
            logger.warning("Research service failed", industry=industry, error=str(e))
            research_data = None

    # 4. Derive trending topics from the intelligence report
    trending_topics: list[str] = []
    if research_data:
        # Pull content opportunity topics as trending signals
        for opp in research_data.get("content_opportunities", []):
            if isinstance(opp, dict) and opp.get("suggested_topic"):
                trending_topics.append(opp["suggested_topic"])
        # Pull angle headlines as additional signals
        for angle_data in research_data.get("angles", {}).values():
            if isinstance(angle_data, dict) and angle_data.get("headline"):
                trending_topics.append(angle_data["headline"])

    if not trending_topics:
        trending_topics = [
            f"latest {industry} innovations",
            f"{industry} market shifts 2025-2026",
            f"AI and automation in {industry}",
            f"{industry} digital transformation",
        ]

    # 5. Build prompt
    current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    user_prompt = _build_idea_prompt(
        chunks=search_results,
        trending_topics=trending_topics,
        current_date=current_date,
        research_data=research_data,
    )

    # 6. Call LLM
    raw_text = await _call_llm_ideas(user_prompt)

    # 7. Parse response
    cleaned = raw_text.strip()
    cleaned = re.sub(r"^```\w*\s*\n?", "", cleaned)
    cleaned = re.sub(r"\n?```\s*$", "", cleaned)
    cleaned = cleaned.strip()

    try:
        parsed: dict = json.loads(cleaned, strict=False)
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse idea generation response", raw=raw_text[:300])
        raise RuntimeError(f"LLM returned invalid JSON: {exc}") from exc

    # 8. Validate into schema and attach research data to each idea
    ideas: list[ContentIdea] = []
    for idea_data in parsed.get("ideas", []):
        idea_data["research_data"] = research_data
        ideas.append(ContentIdea(**idea_data))

    # 9. Persist IdeaBundle to the database
    bundle_id = str(uuid.uuid4())
    generated_at = parsed.get("generated_at", datetime.now(timezone.utc).isoformat())
    context_summary = parsed.get("context_summary", "")

    bundle = IdeaBundle(
        id=bundle_id,
        industry=industry,
        retrieved_chunks=json.dumps(chunks_for_db),
        research_data=json.dumps(research_data) if research_data else None,
        research_insights=research_insights,
        research_sources=json.dumps(research_sources) if research_sources else None,
        llm_prompt=user_prompt,
        llm_raw_response=raw_text,
        ideas=json.dumps([idea.model_dump() for idea in ideas]),
        idea_count=len(ideas),
        context_summary=context_summary,
    )
    db.add(bundle)
    await db.commit()
    await db.refresh(bundle)

    logger.info(
        "Generated and persisted idea bundle",
        bundle_id=bundle_id,
        industry=industry,
        count=len(ideas),
        with_research=research_data is not None,
    )

    response = IdeaGenerateResponse(
        bundle_id=bundle_id,
        industry=industry,
        generated_at=generated_at,
        context_summary=context_summary,
        ideas=ideas,
        research_sources=research_sources if research_sources else None,
        research_insights=research_insights,
    )

    return response


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LLM caller (reuses same provider dispatch)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


async def _call_llm_ideas(user_prompt: str) -> str:
    """Call the configured LLM with the idea-generation system prompt."""
    if settings.generation_provider == "openrouter":
        return await _call_openrouter_ideas(user_prompt)
    if settings.generation_provider == "groq":
        return await _call_groq_ideas(user_prompt)
    return await _call_anthropic_ideas(user_prompt)


async def _call_openrouter_ideas(user_prompt: str) -> str:
    import openai

    client = openai.AsyncOpenAI(
        api_key=settings.openrouter_api_key,
        base_url="https://openrouter.ai/api/v1",
        default_headers={
            "HTTP-Referer": settings.openrouter_site_url,
            "X-Title": settings.openrouter_site_name,
        },
    )
    try:
        response = await client.chat.completions.create(
            model=settings.openrouter_model,
            messages=[
                {"role": "system", "content": IDEA_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=4096,
            temperature=0.7,
        )
        return response.choices[0].message.content or ""
    except openai.APIConnectionError as exc:
        logger.error("OpenRouter connection error (ideas)", error=str(exc))
        raise RuntimeError(f"OpenRouter connection error: {exc}") from exc
    except openai.RateLimitError as exc:
        logger.error("OpenRouter rate limit (ideas)", error=str(exc))
        raise RuntimeError(f"OpenRouter rate limited: {exc}") from exc
    except openai.APIStatusError as exc:
        logger.error("OpenRouter API error (ideas)", status=exc.status_code, error=str(exc))
        raise RuntimeError(f"OpenRouter API error ({exc.status_code}): {exc}") from exc


async def _call_groq_ideas(user_prompt: str) -> str:
    import openai

    client = openai.AsyncOpenAI(
        api_key=settings.groq_api_key,
        base_url="https://api.groq.com/openai/v1",
    )
    try:
        response = await client.chat.completions.create(
            model=settings.groq_model,
            messages=[
                {"role": "system", "content": IDEA_SYSTEM_PROMPT},
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


async def _call_anthropic_ideas(user_prompt: str) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    try:
        message = client.messages.create(
            model=settings.claude_model,
            max_tokens=4096,
            system=IDEA_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return message.content[0].text
    except Exception as exc:
        logger.error("Anthropic API error", error=str(exc))
        raise RuntimeError(f"Anthropic API error: {exc}") from exc
