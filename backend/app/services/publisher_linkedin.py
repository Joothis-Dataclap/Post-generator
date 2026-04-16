"""LinkedIn publishing service — async with OAuth2 and retry logic.

Supports three post types:
- **single**: standard feed post (text + optional image)
- **carousel**: multi-image post
- **article**: long-form article body via UGC API
"""

from pathlib import Path

import httpx
import structlog
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import settings

logger = structlog.get_logger()

_BASE_URL = "https://api.linkedin.com"


def _is_rate_limit(exc: BaseException) -> bool:
    """Return ``True`` if the exception is an HTTP 429."""
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429


def _headers() -> dict[str, str]:
    """Build common LinkedIn API headers with OAuth2 Bearer token."""
    return {
        "Authorization": f"Bearer {settings.linkedin_access_token}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0",
        "LinkedIn-Version": "202401",
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Image upload helper
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@retry(
    retry=retry_if_exception(_is_rate_limit),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=4, max=30),
    reraise=True,
)
async def _upload_image(client: httpx.AsyncClient, image_path: str) -> str | None:
    """Upload an image to LinkedIn via registerUpload. Returns the asset URN."""
    register_body = {
        "registerUploadRequest": {
            "recipes": ["urn:li:digitalmediaRecipe:feedshare-image"],
            "owner": settings.linkedin_person_urn,
            "serviceRelationships": [
                {
                    "relationshipType": "OWNER",
                    "identifier": "urn:li:userGeneratedContent",
                }
            ],
        }
    }

    try:
        resp = await client.post(
            f"{_BASE_URL}/v2/assets?action=registerUpload",
            headers=_headers(),
            json=register_body,
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        logger.error("LinkedIn registerUpload failed", status=exc.response.status_code)
        raise

    data = resp.json()
    upload_url: str = data["value"]["uploadMechanism"][
        "com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest"
    ]["uploadUrl"]
    asset: str = data["value"]["asset"]

    # Read the image file
    full_path = Path(settings.images_dir) / image_path
    if not full_path.exists():
        logger.warning("Image file not found for upload", path=str(full_path))
        return None

    image_bytes = full_path.read_bytes()

    upload_headers = {
        "Authorization": f"Bearer {settings.linkedin_access_token}",
        "Content-Type": "application/octet-stream",
    }
    try:
        resp = await client.put(upload_url, headers=upload_headers, content=image_bytes)
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        logger.error("LinkedIn image binary upload failed", status=exc.response.status_code)
        raise

    return asset


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Single post
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@retry(
    retry=retry_if_exception(_is_rate_limit),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=4, max=30),
    reraise=True,
)
async def publish_single_post(
    content: dict,
    image_path: str | None = None,
) -> str:
    """Publish a single LinkedIn post. Returns the post URN.

    Args:
        content: Dict with keys *hook*, *body*, *hashtags*, *image_description*.
        image_path: Relative path inside the images dir.
    """
    async with httpx.AsyncClient(timeout=60) as client:
        media: list[dict] = []
        if image_path:
            try:
                asset = await _upload_image(client, image_path)
                if asset:
                    media.append(
                        {
                            "status": "READY",
                            "description": {"text": content.get("image_description", "")},
                            "media": asset,
                            "title": {"text": "Post image"},
                        }
                    )
            except Exception as exc:
                logger.warning("Image upload failed, posting without image", error=str(exc))

        text = content.get("hook", "") + "\n\n" + content.get("body", "")
        hashtags = content.get("hashtags", [])
        if hashtags:
            text += "\n\n" + " ".join(f"#{h}" for h in hashtags)

        post_body = {
            "author": settings.linkedin_person_urn,
            "lifecycleState": "PUBLISHED",
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "shareCommentary": {"text": text},
                    "shareMediaCategory": "IMAGE" if media else "NONE",
                    "media": media,
                }
            },
            "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
        }

        try:
            resp = await client.post(
                f"{_BASE_URL}/v2/ugcPosts",
                headers=_headers(),
                json=post_body,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.error("LinkedIn single post failed", status=exc.response.status_code)
            raise

        post_id = resp.headers.get("x-restli-id", resp.json().get("id", ""))
        logger.info("LinkedIn single post published", post_id=post_id)
        return post_id


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Carousel post
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@retry(
    retry=retry_if_exception(_is_rate_limit),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=4, max=30),
    reraise=True,
)
async def publish_carousel_post(
    content: dict,
    image_path: str | None = None,
) -> str:
    """Publish a LinkedIn carousel (multi-image) post. Returns the post URN.

    Each slide re-uses the cover image (individual slide images require
    separate generation, which can be added later).
    """
    async with httpx.AsyncClient(timeout=60) as client:
        media: list[dict] = []
        slides = content.get("slides", [])
        if image_path:
            for slide in slides:
                try:
                    asset = await _upload_image(client, image_path)
                    if asset:
                        media.append(
                            {
                                "status": "READY",
                                "description": {"text": slide.get("body", "")[:200]},
                                "media": asset,
                                "title": {"text": slide.get("headline", "")},
                            }
                        )
                except Exception as exc:
                    logger.warning("Slide image upload failed", error=str(exc))

        caption = content.get("intro_caption", "")
        hashtags = content.get("hashtags", [])
        if hashtags:
            caption += "\n\n" + " ".join(f"#{h}" for h in hashtags)

        post_body = {
            "author": settings.linkedin_person_urn,
            "lifecycleState": "PUBLISHED",
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "shareCommentary": {"text": caption},
                    "shareMediaCategory": "IMAGE" if media else "NONE",
                    "media": media,
                }
            },
            "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
        }

        try:
            resp = await client.post(
                f"{_BASE_URL}/v2/ugcPosts",
                headers=_headers(),
                json=post_body,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.error("LinkedIn carousel post failed", status=exc.response.status_code)
            raise

        post_id = resp.headers.get("x-restli-id", resp.json().get("id", ""))
        logger.info("LinkedIn carousel published", post_id=post_id)
        return post_id


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Article
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@retry(
    retry=retry_if_exception(_is_rate_limit),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=4, max=30),
    reraise=True,
)
async def publish_article(
    content: dict,
    image_path: str | None = None,
) -> str:
    """Publish a LinkedIn article via UGC API. Returns the post URN."""
    async with httpx.AsyncClient(timeout=60) as client:
        thumbnail = None
        if image_path:
            try:
                thumbnail = await _upload_image(client, image_path)
            except Exception as exc:
                logger.warning("Article thumbnail upload failed", error=str(exc))

        article_body = {
            "author": settings.linkedin_person_urn,
            "lifecycleState": "PUBLISHED",
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "shareCommentary": {
                        "text": (
                            content.get("title", "")
                            + "\n\n"
                            + content.get("subtitle", "")
                        )
                    },
                    "shareMediaCategory": "ARTICLE",
                    "media": [
                        {
                            "status": "READY",
                            "originalUrl": "",
                            "title": {"text": content.get("title", "")},
                            "description": {"text": content.get("body", "")[:300]},
                            **({"thumbnails": [{"resolvedUrl": thumbnail}]} if thumbnail else {}),
                        }
                    ],
                }
            },
            "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
        }

        try:
            resp = await client.post(
                f"{_BASE_URL}/v2/ugcPosts",
                headers=_headers(),
                json=article_body,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.error("LinkedIn article post failed", status=exc.response.status_code)
            raise

        post_id = resp.headers.get("x-restli-id", resp.json().get("id", ""))
        logger.info("LinkedIn article published", post_id=post_id)
        return post_id


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Dispatcher
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


async def publish_to_linkedin(
    linkedin_type: str,
    content: dict,
    image_path: str | None = None,
) -> str:
    """Route to the correct LinkedIn publisher based on type.

    Args:
        linkedin_type: One of ``single``, ``carousel``, ``article``.
        content: Platform-specific content dict.
        image_path: Optional cover image filename.

    Returns:
        The LinkedIn post URN / ID.

    Raises:
        ValueError: If *linkedin_type* is not recognised.
    """
    if linkedin_type == "single":
        return await publish_single_post(content, image_path)
    if linkedin_type == "carousel":
        return await publish_carousel_post(content, image_path)
    if linkedin_type == "article":
        return await publish_article(content, image_path)
    raise ValueError(f"Unknown LinkedIn type: {linkedin_type}")
