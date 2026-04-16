"""
Deep research service using Parallel API for online resource gathering.
Enriches idea generation with real-time insights and trending data.
"""

import json
import re
from typing import Optional

import httpx
import structlog

from app.core.config import settings

logger = structlog.get_logger()


class ResearchService:
    """Service for deep research using Parallel API Pro model."""

    def __init__(self):
        self.api_key = settings.parallel_api_key
        self.api_endpoint = settings.parallel_api_endpoint
        self.model = "llama-3"  # Parallel API's fast model for research
        self.client = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create async HTTP client."""
        if self.client is None:
            # Use shorter timeout for research (30 sec instead of 60)
            self.client = httpx.AsyncClient(timeout=30.0)
        return self.client

    async def deep_research(
        self,
        topic: str,
        trending_angle: Optional[str] = None,
        category: Optional[str] = None,
        max_resources: int = 5,
    ) -> dict:
        """
        Run deep research on a topic using Parallel API with online access.

        Args:
            topic: Main research topic
            trending_angle: Specific trending angle to research
            category: Content category (e.g., "B2B Tech", "Marketing")
            max_resources: Max online resources to include

        Returns:
            dict with: findings, sources, trending_angles, key_insights, data_points
        """
        # If no API key, return default
        if not self.api_key:
            logger.warning("No Parallel API key configured, using default research")
            return self._get_default_research(topic)

        try:
            client = await self._get_client()

            # Build research prompt
            research_prompt = self._build_research_prompt(
                topic, trending_angle, category, max_resources
            )

            logger.info(f"Starting deep research on: {topic}")

            # Call Parallel API with streaming capability disabled for full response
            headers = {"Authorization": f"Bearer {self.api_key}"}

            response = await client.post(
                f"{self.api_endpoint}/chat/completions",
                headers=headers,
                json={
                    "model": self.model,
                    "messages": [
                        {
                            "role": "system",
                            "content": self._get_research_system_prompt(category),
                        },
                        {"role": "user", "content": research_prompt},
                    ],
                    "temperature": 0.7,
                    "max_tokens": 2000,
                },
            )

            if response.status_code != 200:
                logger.error(f"Parallel API error: {response.status_code}")
                return self._get_default_research(topic)

            data = response.json()
            raw_response = data["choices"][0]["message"]["content"]

            # Parse research response
            research_data = self._parse_research_response(raw_response)
            logger.info(f"Research completed for {topic}")

            return research_data

        except Exception as e:
            logger.error(f"Research service error: {str(e)}")
            return self._get_default_research(topic)

    def _get_research_system_prompt(self, category: Optional[str] = None) -> str:
        """Build system prompt for research agent."""
        category_context = (
            f"You specialize in {category} content research." if category else ""
        )

        return f"""You are DataClap Digital's deep research agent.
Your role is to find and analyze the latest online resources, trends, and insights.

{category_context}

For each research query, provide:
1. **Key Findings**: 2-3 main insights from current online sources
2. **Online Sources**: 3-5 specific resources (with URLs and credibility assessment)
3. **Trending Angles**: 2-3 emerging angles related to the topic
4. **Expert Insights**: Key expert opinions or data points
5. **Data Points**: Specific statistics or metrics that prove the trend

Format your response as a JSON object. Be specific with URLs and dates."""

    def _build_research_prompt(
        self,
        topic: str,
        trending_angle: Optional[str],
        category: Optional[str],
        max_resources: int,
    ) -> str:
        """Build the research query prompt."""
        angle_text = f" with focus on: {trending_angle}" if trending_angle else ""
        category_text = f"({category}) " if category else ""

        return f"""Research the following topic and find the latest online resources:

Topic: {category_text}{topic}{angle_text}

Please provide up to {max_resources} online resources with:
- URL
- Source credibility (High/Medium/Low)
- Key takeaway (1-2 sentences)
- Publication date or recency

Also include:
- What experts are saying about this
- Recent data/statistics proving this trend
- 2-3 emerging angles we could angle for content
- Why this matters now (April 2026 context)

Return response as JSON with keys: findings, sources, trending_angles, expert_insights, data_points"""

    def _parse_research_response(self, raw_response: str) -> dict:
        """Parse research response and extract structured data."""
        try:
            # Strip markdown code fences if present
            cleaned = re.sub(r"^```json\s*\n?", "", raw_response)
            cleaned = re.sub(r"\n?```\s*$", "", cleaned)

            # Parse JSON with lenient control character handling
            research_data = json.loads(cleaned, strict=False)

            # Ensure required keys exist
            defaults = {
                "findings": ["Current trend analysis in progress"],
                "sources": [],
                "trending_angles": ["Main trend", "Secondary angle"],
                "expert_insights": ["Industry experts discussing this trend"],
                "data_points": [
                    "Latest statistics supporting this trend"
                ],
            }

            for key, default_value in defaults.items():
                if key not in research_data:
                    research_data[key] = default_value

            return research_data

        except json.JSONDecodeError:
            logger.warning("Failed to parse research JSON, using defaults")
            return self._get_default_research("")

    def _get_default_research(self, topic: str) -> dict:
        """Return default research structure when API fails."""
        return {
            "findings": [
                f"Emerging trend in {topic if topic else 'digital landscape'}"
            ],
            "sources": [
                {
                    "url": "https://www.techcrunch.com",
                    "credibility": "High",
                    "takeaway": "Latest insights from tech industry",
                }
            ],
            "trending_angles": [f"The rise of {topic}", "Industry transformation"],
            "expert_insights": [
                "Industry leaders emphasize the importance of staying ahead of trends"
            ],
            "data_points": ["Market growth continues in this sector"],
        }


# Singleton instance
_research_service: Optional[ResearchService] = None


def get_research_service() -> ResearchService:
    """Get or create research service singleton."""
    global _research_service
    if _research_service is None:
        _research_service = ResearchService()
    return _research_service


async def run_deep_research(
    topic: str,
    trending_angle: Optional[str] = None,
    category: Optional[str] = None,
    max_resources: int = 5,
) -> dict:
    """Convenience function to run deep research."""
    service = get_research_service()
    return await service.deep_research(topic, trending_angle, category, max_resources)
