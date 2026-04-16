"""Idea generation service — Prompt 1.

Cross-references retrieved knowledge-base chunks with trending signals
to produce high-impact content ideas for DataClap Digital.
Enriches ideas with deep research from online sources via Parallel API.
"""

import asyncio
import json
import re
from datetime import datetime, timezone

import structlog
from qdrant_client import QdrantClient

from app.core.config import settings
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
You are a senior content strategist for DataClap Digital (www.dataclap.digital), \
a company operating in the data engineering, analytics, cloud infrastructure, \
and digital transformation space.

Your job is to synthesize retrieved knowledge base content with real-time \
trending signals to generate high-impact content ideas that position DataClap \
as a thought leader.

Rules:
- Ground every idea in at least one retrieved chunk OR one trending signal.
- Prioritize ideas that are timely (trending NOW), original, and have strong \
engagement potential on LinkedIn and X (Twitter).
- Do NOT invent technical claims not supported by context.
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
        chunk_xml += (
            f'  [{i}] (source: "{chunk.source_title}", '
            f"score: {chunk.score:.3f})\n"
            f"  {chunk.text}\n\n"
        )

    trending_xml = "\n".join(f"  - {t}" for t in trending_topics)

    # Add research data if available
    research_section = ""
    if research_data:
        research_section = "\n\n<research_insights>\n"
        if research_data.get("findings"):
            research_section += "Key Findings:\n"
            for finding in research_data.get("findings", []):
                research_section += f"  • {finding}\n"
        
        if research_data.get("expert_insights"):
            research_section += "\nExpert Insights:\n"
            for insight in research_data.get("expert_insights", []):
                research_section += f"  • {insight}\n"
        
        if research_data.get("data_points"):
            research_section += "\nData Points:\n"
            for point in research_data.get("data_points", []):
                research_section += f"  • {point}\n"
        
        if research_data.get("sources"):
            research_section += "\nOnline Sources:\n"
            for source in research_data.get("sources", [])[:3]:
                if isinstance(source, dict):
                    url = source.get("url", "")
                    takeaway = source.get("takeaway", "")
                    research_section += f"  • {url} - {takeaway}\n"
        
        research_section += "</research_insights>"

    return f"""\
You have access to the following retrieved knowledge chunks from DataClap's \
internal knowledge base:

<retrieved_chunks>
{chunk_xml}
</retrieved_chunks>

You also have access to the following trending topics and signals relevant to \
DataClap's domain (data engineering, analytics, AI/ML, cloud, digital \
transformation):

<trending_signals>
{trending_xml}
</trending_signals>{research_section}

Today's date: {current_date}

Task:
Generate exactly 5 content ideas by cross-referencing the retrieved chunks \
with the trending signals and research insights. For each idea:
1. Identify the core intersection (what from the chunks + what from trends \
makes this timely).
2. Define the content angle (opinion, how-to, case study, data story, \
myth-busting).
3. Estimate engagement potential (High / Medium) with one-line reasoning.

Return ONLY a JSON object in this exact schema:

{{
  "generated_at": "ISO8601 timestamp",
  "context_summary": "2-sentence summary of the key themes found in chunks + trends",
  "ideas": [
    {{
      "id": "idea_1",
      "title": "Short punchy working title",
      "angle": "opinion | how-to | case-study | data-story | myth-busting",
      "core_hook": "The single most compelling sentence about why this idea matters RIGHT NOW",
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
    qdrant: QdrantClient,
    request: IdeaGenerateRequest,
) -> IdeaGenerateResponse:
    """Retrieve chunks, cross-reference with trends, run deep research, and generate 5 ideas."""

    # 1. Retrieve relevant chunks
    search_results = semantic_search(
        qdrant,
        query=request.query,
        top_k=request.top_k,
        category_filter=request.category_filter,
    )

    # 2. Run deep research on trending topics (if Parallel API key is available)
    # Use asyncio.wait_for to timeout research at 10 seconds to prevent blocking
    research_data = None
    research_insights = None
    research_sources = []
    
    if settings.parallel_api_key:
        try:
            # Run research on the combined trending topics
            trending_combined = ", ".join(request.trending_topics[:3])
            
            # Wrap research call with timeout to prevent blocking
            research_data = await asyncio.wait_for(
                run_deep_research(
                    topic=trending_combined,
                    trending_angle=request.query,
                    category=request.category_filter or "Data & Analytics",
                    max_resources=5,
                ),
                timeout=10.0  # 10 second timeout for research
            )
            logger.info("Deep research completed", sources=len(research_data.get("sources", [])))
            
            # Prepare research insights for the response
            if research_data:
                research_insights = "\n".join(research_data.get("findings", [])[:2])
                research_sources = research_data.get("sources", [])
        except asyncio.TimeoutError:
            logger.warning("Research service timed out, continuing without research")
            research_data = None
        except Exception as e:
            logger.warning("Research service failed, continuing without research", error=str(e))
            research_data = None

    # 3. Build prompt (now with research data)
    current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    user_prompt = _build_idea_prompt(
        chunks=search_results,
        trending_topics=request.trending_topics,
        current_date=current_date,
        research_data=research_data,
    )

    # 4. Call LLM
    raw_text = await _call_llm_ideas(user_prompt)

    # 5. Parse response
    cleaned = raw_text.strip()
    cleaned = re.sub(r"^```\w*\s*\n?", "", cleaned)
    cleaned = re.sub(r"\n?```\s*$", "", cleaned)
    cleaned = cleaned.strip()

    try:
        parsed: dict = json.loads(cleaned, strict=False)
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse idea generation response", raw=raw_text[:300])
        raise RuntimeError(f"LLM returned invalid JSON: {exc}") from exc

    # 6. Validate into schema and attach research data to each idea
    ideas = []
    for idea_data in parsed.get("ideas", []):
        idea_data["research_data"] = research_data  # Attach research to each idea
        ideas.append(ContentIdea(**idea_data))

    response = IdeaGenerateResponse(
        generated_at=parsed.get(
            "generated_at",
            datetime.now(timezone.utc).isoformat(),
        ),
        context_summary=parsed.get("context_summary", ""),
        ideas=ideas,
        research_sources=research_sources if research_sources else None,
        research_insights=research_insights,
    )

    logger.info("Generated content ideas", count=len(ideas), with_research=research_data is not None)
    return response


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LLM caller (reuses same provider dispatch)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


async def _call_llm_ideas(user_prompt: str) -> str:
    """Call the configured LLM with the idea-generation system prompt."""
    if settings.generation_provider == "groq":
        return await _call_groq_ideas(user_prompt)
    return await _call_anthropic_ideas(user_prompt)


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
