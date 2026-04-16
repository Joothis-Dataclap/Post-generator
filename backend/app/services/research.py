"""
Domain intelligence research service for DataClap Digital.

Runs multi-angle web searches via the Parallel Search API, then feeds
all raw results into an LLM to produce a structured intelligence report.

Flow:
  1. Build separate Parallel Search queries for each research angle
     (Technology Updates, Research & Benchmarks, Real-World Deployments,
      Challenges & Gaps).
  2. Fire all searches concurrently via ``asyncio.gather``.
  3. Concatenate raw excerpts per angle into a context block.
  4. Call the LLM with the DataClap domain intelligence prompt.
  5. Return the parsed JSON intelligence report.

Parallel API:  POST https://api.parallel.ai/v1beta/search
Auth:          x-api-key header
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
import structlog

from app.core.config import settings

logger = structlog.get_logger()

PARALLEL_SEARCH_URL = "https://api.parallel.ai/v1beta/search"
CURRENT_YEAR = str(datetime.now(timezone.utc).year)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Angle definitions — each becomes one or more Parallel Search calls
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _build_angle_searches(service_label: str) -> list[dict[str, Any]]:
    """Return a list of search-job dicts: {angle, objective, queries, max_results}."""
    return [
        # ── Angle 1: Technology Updates (★ contextual) ───────
        {
            "angle": "technology_updates",
            "objective": (
                f"Find the newest tools, models, frameworks, or techniques "
                f"launched recently in the {service_label} space. "
                f"Prefer product announcements, release notes, and tech press."
            ),
            "queries": [
                f"{service_label} technology updates {CURRENT_YEAR}",
            ],
            "max_results": 3,
        },
        # ── Angle 3a: Research papers (★★★ primary) ──────────
        {
            "angle": "research_and_benchmarks",
            "objective": (
                f"Find the latest academic research papers, published datasets, "
                f"and benchmark results related to {service_label}. "
                f"Prefer arXiv, ACL Anthology, IEEE, NeurIPS, CVPR, Semantic Scholar."
            ),
            "queries": [
                f"{service_label} research paper {CURRENT_YEAR}",
                f"{service_label} benchmark dataset {CURRENT_YEAR}",
                f"{service_label} arxiv survey {CURRENT_YEAR}",
            ],
            "max_results": 5,
        },
        # ── Angle 4: Deployments & Case Studies (★★ supporting)
        {
            "angle": "real_world_deployments",
            "objective": (
                f"Find publicly reported production deployments, pilot programmes, "
                f"or case studies in {service_label}. "
                f"Prefer vendor case studies, conference talks, and analyst reports."
            ),
            "queries": [
                f"{service_label} deployment case study real world {CURRENT_YEAR}",
            ],
            "max_results": 4,
        },
        # ── Angle 5a: Challenges & Gaps (★★★ primary) ────────
        {
            "angle": "challenges_and_gaps",
            "objective": (
                f"Find known challenges, limitations, failure modes, and "
                f"unmet needs actively discussed in {service_label}. "
                f"Prefer survey papers, limitation sections of recent papers, "
                f"practitioner forums (Reddit ML, Hacker News, GitHub Issues), "
                f"and industry analyst reports."
            ),
            "queries": [
                f"{service_label} challenges limitations {CURRENT_YEAR}",
                f"{service_label} failure modes unsolved problems {CURRENT_YEAR}",
                f"{service_label} data quality annotation gaps {CURRENT_YEAR}",
            ],
            "max_results": 5,
        },
    ]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LLM synthesis prompt
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

RESEARCH_SYSTEM_PROMPT = """\
You are a domain intelligence researcher for DataClap Digital (www.dataclap.digital).

DataClap provides AI training data annotation services across multiple industries. \
For each research run, you are given ONE specific service domain and its description. \
Your job is to search the web exhaustively and return a structured intelligence report \
on what is happening RIGHT NOW in that domain.

You are NOT writing content. You are NOT summarizing what DataClap does.
You ARE finding what the world is talking about in this space — with the deepest \
focus on recent academic research, published benchmarks, and known challenges or gaps.

PRIORITY HIERARCHY (follow strictly):
★★★ PRIMARY (maximum depth, 3 sources each, extended fields):
  Angle 3 — Research & Benchmarks
  Angle 5 — Challenges & Gaps

★★ SUPPORTING (standard depth, 2 sources each):
  Angle 4 — Real-World Deployments & Case Studies

★ CONTEXTUAL (lightweight, 1–2 sources, brief):
  Angle 1 — Technology Updates

RULES:
- Every source needs: real URL, real title, real published date.
- Extract at least one hard fact, number, stat, or direct quote per source.
- DO NOT hallucinate URLs, titles, or statistics. If a search returned nothing \
strong, explicitly state "no strong source found" for that slot.
- Prefer sources from the last 90 days. Flag anything older with its actual date.
- Cover at least 3 different source types across the full report."""


def _build_research_user_prompt(
    service_label: str,
    service_description: str,
    current_date: str,
    raw_results_by_angle: dict[str, list[dict[str, Any]]],
) -> str:
    """Build the user prompt that feeds raw Parallel Search results into the LLM."""

    # Format raw results per angle for the LLM
    raw_section = ""
    for angle_key, results in raw_results_by_angle.items():
        raw_section += f"\n── RAW SEARCH RESULTS: {angle_key.upper()} ──\n"
        if not results:
            raw_section += "  (no results returned by search)\n"
            continue
        for i, r in enumerate(results):
            raw_section += f"\n  [{i+1}] Title: {r.get('title', 'N/A')}\n"
            raw_section += f"      URL: {r.get('url', 'N/A')}\n"
            raw_section += f"      Published: {r.get('publish_date', 'N/A')}\n"
            excerpts = r.get("excerpts", [])
            excerpt_text = "\n".join(
                e[:3000] if isinstance(e, str) else "" for e in excerpts[:3]
            ).strip()
            if excerpt_text:
                raw_section += f"      Excerpt:\n{excerpt_text[:4000]}\n"

    return f"""\
<service_description>
{service_description}
</service_description>

Service label: {service_label}
Research date: {current_date}

Below are the raw web search results gathered for each research angle. \
Use ONLY these results to populate your intelligence report. Do not invent \
URLs, titles, dates, or statistics that do not appear below.

<raw_search_results>
{raw_section}
</raw_search_results>

Now produce the structured intelligence report as JSON.

RESEARCH ANGLES:

── ANGLE 1 (Contextual) ─────────────────────────────
Technology Updates — What new tools, models, frameworks, or techniques have launched recently?
Depth: 1–2 sources, brief summary only.

── ANGLE 3 ★★★ PRIMARY (Deep Research) ──────────────
Research & Benchmarks — What recent papers, datasets, or benchmarks have been published?
Depth: Minimum 3 sources. For each: title, URL, date, publisher, 3–4 sentence summary, \
key finding or metric, methodology note, contribution_type (new_dataset | new_model | new_benchmark | survey | other).
Identify top_research_finding across all R sources.

── ANGLE 4 ★★ Supporting ────────────────────────────
Real-World Deployments & Case Studies — What production deployments or pilots have been publicly reported?
Depth: 2 sources, standard fields.

── ANGLE 5 ★★★ PRIMARY (Deep Gap Analysis) ──────────
Challenges & Gaps — What problems, limitations, failure modes, or unmet needs are actively discussed?
Depth: Minimum 3 sources. For each: title, URL, date, publisher, 3–4 sentence summary, \
key quote or stat, gap_type (Data Quality | Model Limitation | Scalability | Regulatory/Ethics | Cost | Tooling | Domain Coverage | Other).
Synthesize gap_cluster_summary (2–3 named themes). Identify top_open_problem.

OUTPUT FORMAT — Return ONLY valid JSON, no preamble, no markdown fences:

{{
  "service_label": "{service_label}",
  "research_date": "{current_date}",
  "total_sources_found": 0,
  "priority_focus": "research_and_benchmarks + challenges_and_gaps",
  "intelligence_summary": "3–4 sentence synthesis",
  "top_research_finding": "single most compelling finding — one sentence with source index",
  "top_open_problem": "single most urgent gap — one sentence with source index",
  "angles": {{
    "technology_updates": {{
      "headline": "",
      "sources": [
        {{"index": "T1", "title": "", "url": "", "published_date": "", "publisher": "", "summary": "", "key_fact": ""}}
      ]
    }},
    "research_and_benchmarks": {{
      "headline": "",
      "top_research_finding": "",
      "sources": [
        {{"index": "R1", "title": "", "url": "", "published_date": "", "publisher": "", "summary": "", "key_fact": "", "methodology_note": "", "contribution_type": ""}},
        {{"index": "R2", "title": "", "url": "", "published_date": "", "publisher": "", "summary": "", "key_fact": "", "methodology_note": "", "contribution_type": ""}},
        {{"index": "R3", "title": "", "url": "", "published_date": "", "publisher": "", "summary": "", "key_fact": "", "methodology_note": "", "contribution_type": ""}}
      ]
    }},
    "real_world_deployments": {{
      "headline": "",
      "sources": [
        {{"index": "D1", "title": "", "url": "", "published_date": "", "publisher": "", "summary": "", "key_fact": ""}},
        {{"index": "D2", "title": "", "url": "", "published_date": "", "publisher": "", "summary": "", "key_fact": ""}}
      ]
    }},
    "challenges_and_gaps": {{
      "headline": "",
      "top_open_problem": "",
      "gap_cluster_summary": [
        {{"theme": "", "description": ""}},
        {{"theme": "", "description": ""}},
        {{"theme": "", "description": ""}}
      ],
      "sources": [
        {{"index": "C1", "title": "", "url": "", "published_date": "", "publisher": "", "summary": "", "key_fact": "", "gap_type": ""}},
        {{"index": "C2", "title": "", "url": "", "published_date": "", "publisher": "", "summary": "", "key_fact": "", "gap_type": ""}},
        {{"index": "C3", "title": "", "url": "", "published_date": "", "publisher": "", "summary": "", "key_fact": "", "gap_type": ""}}
      ]
    }}
  }},
  "content_opportunities": [
    {{"angle": "research_and_benchmarks", "suggested_topic": "", "why_now": "", "best_source_index": ""}},
    {{"angle": "challenges_and_gaps", "suggested_topic": "", "why_now": "", "best_source_index": ""}},
    {{"angle": "real_world_deployments | technology_updates", "suggested_topic": "", "why_now": "", "best_source_index": ""}}
  ]
}}"""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Research service
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class ResearchService:
    """Multi-angle domain intelligence via Parallel Search + LLM synthesis."""

    def __init__(self) -> None:
        self.api_key: str = settings.parallel_api_key
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=60.0)
        return self._client

    # ── Parallel Search helpers ──────────────────────────────

    async def _search_parallel(
        self,
        objective: str,
        queries: list[str],
        max_results: int = 5,
    ) -> list[dict[str, Any]]:
        """Execute a single Parallel Search API call and return the results list."""
        client = await self._get_client()

        payload = {
            "objective": objective,
            "search_queries": queries,
            "mode": "one-shot",
            "max_results": max_results,
            "excerpts": {"max_chars_per_result": 8000},
        }

        try:
            resp = await client.post(
                PARALLEL_SEARCH_URL,
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": self.api_key,
                },
                json=payload,
            )

            if resp.status_code != 200:
                logger.warning(
                    "Parallel Search returned non-200",
                    status=resp.status_code,
                    body=resp.text[:200],
                )
                return []

            data = resp.json()
            return data.get("results", [])

        except Exception as exc:
            logger.warning("Parallel Search call failed", error=str(exc))
            return []

    async def _run_all_searches(
        self, service_label: str,
    ) -> dict[str, list[dict[str, Any]]]:
        """Fire all angle searches concurrently and return results keyed by angle."""
        angle_jobs = _build_angle_searches(service_label)

        async def _run_one(job: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
            results = await self._search_parallel(
                objective=job["objective"],
                queries=job["queries"],
                max_results=job["max_results"],
            )
            return job["angle"], results

        tasks = [_run_one(job) for job in angle_jobs]
        completed = await asyncio.gather(*tasks, return_exceptions=True)

        results_by_angle: dict[str, list[dict[str, Any]]] = {}
        for item in completed:
            if isinstance(item, Exception):
                logger.warning("Search job failed", error=str(item))
                continue
            angle_key, results = item
            results_by_angle[angle_key] = results

        return results_by_angle

    # ── LLM synthesis ────────────────────────────────────────

    async def _synthesise_with_llm(
        self,
        service_label: str,
        service_description: str,
        current_date: str,
        raw_results_by_angle: dict[str, list[dict[str, Any]]],
    ) -> dict[str, Any]:
        """Feed all raw search results into the LLM and parse the intelligence JSON."""
        user_prompt = _build_research_user_prompt(
            service_label=service_label,
            service_description=service_description,
            current_date=current_date,
            raw_results_by_angle=raw_results_by_angle,
        )

        raw_text = await _call_llm_research(user_prompt)

        # Strip markdown fences
        cleaned = raw_text.strip()
        cleaned = re.sub(r"^```\w*\s*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```\s*$", "", cleaned)
        cleaned = cleaned.strip()

        try:
            return json.loads(cleaned, strict=False)
        except json.JSONDecodeError:
            logger.error("LLM returned invalid intelligence JSON", raw=raw_text[:300])
            return _fallback_intelligence(service_label, current_date)

    # ── Public entry point ───────────────────────────────────

    async def deep_research(
        self,
        topic: str,
        service_description: str = "",
        trending_angle: Optional[str] = None,
        category: Optional[str] = None,
        max_resources: int = 5,
    ) -> dict[str, Any]:
        """Run full domain intelligence research on *topic*.

        1. Fires 4 concurrent Parallel Search batches (one per angle).
        2. Feeds all raw results into the LLM with the DataClap
           domain-intelligence prompt.
        3. Returns the structured intelligence report as a dict.
        """
        if not self.api_key:
            logger.warning("No Parallel API key — returning fallback intelligence")
            current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            return _fallback_intelligence(topic, current_date)

        service_label = topic
        if not service_description:
            service_description = (
                f"AI training data annotation and intelligence services "
                f"for the {topic} domain, including data labelling, model "
                f"evaluation, and quality assurance."
            )

        current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        logger.info("Starting multi-angle domain research", service_label=service_label)

        # Step 1: Run all Parallel searches concurrently
        raw_results = await self._run_all_searches(service_label)

        total_results = sum(len(v) for v in raw_results.values())
        logger.info(
            "Parallel searches completed",
            service_label=service_label,
            total_results=total_results,
            angles=list(raw_results.keys()),
        )

        if total_results == 0:
            logger.warning("All searches returned empty, using fallback")
            return _fallback_intelligence(service_label, current_date)

        # Step 2: Synthesise with LLM
        intelligence = await self._synthesise_with_llm(
            service_label=service_label,
            service_description=service_description,
            current_date=current_date,
            raw_results_by_angle=raw_results,
        )

        logger.info(
            "Intelligence report generated",
            service_label=service_label,
            sources_found=intelligence.get("total_sources_found", 0),
        )

        return intelligence


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LLM callers (reuses same provider dispatch as idea_generation)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


async def _call_llm_research(user_prompt: str) -> str:
    """Call the configured LLM with the research system prompt."""
    if settings.generation_provider == "openrouter":
        return await _call_openrouter_research(user_prompt)
    if settings.generation_provider == "groq":
        return await _call_groq_research(user_prompt)
    return await _call_anthropic_research(user_prompt)


async def _call_openrouter_research(user_prompt: str) -> str:
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
                {"role": "system", "content": RESEARCH_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=6000,
            temperature=0.4,
        )
        return response.choices[0].message.content or ""
    except openai.APIConnectionError as exc:
        logger.error("OpenRouter connection error (research)", error=str(exc))
        raise RuntimeError(f"OpenRouter connection error: {exc}") from exc
    except openai.RateLimitError as exc:
        logger.error("OpenRouter rate limit (research)", error=str(exc))
        raise RuntimeError(f"OpenRouter rate limited: {exc}") from exc
    except openai.APIStatusError as exc:
        logger.error("OpenRouter API error (research)", status=exc.status_code, error=str(exc))
        raise RuntimeError(f"OpenRouter API error ({exc.status_code}): {exc}") from exc


async def _call_groq_research(user_prompt: str) -> str:
    import openai

    client = openai.AsyncOpenAI(
        api_key=settings.groq_api_key,
        base_url="https://api.groq.com/openai/v1",
    )
    try:
        response = await client.chat.completions.create(
            model=settings.groq_model,
            messages=[
                {"role": "system", "content": RESEARCH_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=6000,
            temperature=0.4,
        )
        return response.choices[0].message.content or ""
    except Exception as exc:
        logger.error("Groq research LLM error", error=str(exc))
        raise RuntimeError(f"Groq research LLM error: {exc}") from exc


async def _call_anthropic_research(user_prompt: str) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    try:
        message = client.messages.create(
            model=settings.claude_model,
            max_tokens=6000,
            system=RESEARCH_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return message.content[0].text
    except Exception as exc:
        logger.error("Anthropic research LLM error", error=str(exc))
        raise RuntimeError(f"Anthropic research LLM error: {exc}") from exc


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Fallback
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _fallback_intelligence(service_label: str, current_date: str) -> dict[str, Any]:
    """Minimal intelligence structure when searches or LLM fail."""
    return {
        "service_label": service_label,
        "research_date": current_date,
        "total_sources_found": 0,
        "priority_focus": "research_and_benchmarks + challenges_and_gaps",
        "intelligence_summary": (
            f"Unable to retrieve live intelligence for {service_label}. "
            f"Parallel API key may be missing or searches returned no results."
        ),
        "top_research_finding": "No research data available",
        "top_open_problem": "No gap data available",
        "angles": {
            "technology_updates": {"headline": "No data", "sources": []},
            "research_and_benchmarks": {
                "headline": "No data",
                "top_research_finding": "N/A",
                "sources": [],
            },
            "real_world_deployments": {"headline": "No data", "sources": []},
            "challenges_and_gaps": {
                "headline": "No data",
                "top_open_problem": "N/A",
                "gap_cluster_summary": [],
                "sources": [],
            },
        },
        "content_opportunities": [],
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Singleton + convenience function
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_research_service: ResearchService | None = None


def get_research_service() -> ResearchService:
    global _research_service
    if _research_service is None:
        _research_service = ResearchService()
    return _research_service


async def run_deep_research(
    topic: str,
    service_description: str = "",
    trending_angle: Optional[str] = None,
    category: Optional[str] = None,
    max_resources: int = 5,
) -> dict[str, Any]:
    """Convenience wrapper used by the idea-generation service."""
    service = get_research_service()
    return await service.deep_research(
        topic,
        service_description=service_description,
        trending_angle=trending_angle,
        category=category,
        max_resources=max_resources,
    )
