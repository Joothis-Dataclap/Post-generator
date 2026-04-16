"""Twitter/X publishing service — async with OAuth and retry logic.

Supports three post types:
- **tweet**: single tweet (≤280 chars + optional image)
- **thread**: hook → body tweets → CTA (chained via ``in_reply_to_tweet_id``)
- **carousel**: single tweet with up to 4 attached images

Uses:
- Twitter v2 API for tweet creation (``POST /2/tweets``).
- Twitter v1.1 media upload endpoint for images.
"""

import base64
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

_V2_BASE = "https://api.twitter.com/2"
_V1_UPLOAD = "https://upload.twitter.com/1.1/media/upload.json"


def _is_rate_limit(exc: BaseException) -> bool:
    """Return ``True`` if the exception is an HTTP 429."""
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429


def _bearer_headers() -> dict[str, str]:
    """Return Bearer-token headers for Twitter v2 API."""
    return {
        "Authorization": f"Bearer {settings.x_bearer_token}",
        "Content-Type": "application/json",
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Media upload (v1.1)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


async def _upload_media(client: httpx.AsyncClient, image_path: str) -> str | None:
    """Upload an image to the Twitter v1.1 media endpoint.

    Returns:
        The ``media_id_string`` on success, or ``None`` if the file is missing.
    """
    full_path = Path(settings.images_dir) / image_path
    if not full_path.exists():
        logger.warning("Image not found for X upload", path=str(full_path))
        return None

    image_bytes = full_path.read_bytes()
    b64_data = base64.b64encode(image_bytes).decode()

    form_data = {"media_data": b64_data}

    try:
        resp = await client.post(
            _V1_UPLOAD,
            headers={"Authorization": f"Bearer {settings.x_bearer_token}"},
            data=form_data,
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        logger.error("X media upload failed", status=exc.response.status_code)
        raise

    media_id: str = resp.json().get("media_id_string", "")
    logger.info("X media uploaded", media_id=media_id)
    return media_id


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Core tweet posting
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@retry(
    retry=retry_if_exception(_is_rate_limit),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=4, max=30),
    reraise=True,
)
async def _post_tweet(
    client: httpx.AsyncClient,
    text: str,
    media_ids: list[str] | None = None,
    reply_to: str | None = None,
) -> str:
    """Post a single tweet via v2 API. Returns the tweet ID.

    Args:
        client: Reusable async HTTP client.
        text: Tweet text.
        media_ids: Optional list of media IDs to attach.
        reply_to: Optional tweet ID to reply to (for threads).
    """
    body: dict = {"text": text}
    if media_ids:
        body["media"] = {"media_ids": media_ids}
    if reply_to:
        body["reply"] = {"in_reply_to_tweet_id": reply_to}

    try:
        resp = await client.post(
            f"{_V2_BASE}/tweets",
            headers=_bearer_headers(),
            json=body,
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        logger.error("Tweet post failed", status=exc.response.status_code, text=text[:60])
        raise

    tweet_id: str = resp.json()["data"]["id"]
    return tweet_id


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Public publisher functions
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


async def publish_tweet(
    content: dict,
    image_path: str | None = None,
) -> str:
    """Publish a single tweet, optionally with an image.

    Returns:
        The published tweet's ID.
    """
    async with httpx.AsyncClient(timeout=60) as client:
        media_ids: list[str] = []
        if image_path:
            try:
                mid = await _upload_media(client, image_path)
                if mid:
                    media_ids.append(mid)
            except Exception as exc:
                logger.warning("Media upload failed, tweeting without image", error=str(exc))

        text = content.get("text", "")
        hashtags = content.get("hashtags", [])
        if hashtags:
            tag_str = " ".join(f"#{h}" for h in hashtags)
            if len(text) + len(tag_str) + 1 <= 280:
                text += "\n" + tag_str

        tweet_id = await _post_tweet(client, text, media_ids or None)
        logger.info("Tweet published", tweet_id=tweet_id)
        return tweet_id


async def publish_thread(
    content: dict,
    image_path: str | None = None,
) -> str:
    """Publish a tweet thread. Returns the first tweet's ID.

    Thread structure: hook_tweet → tweets[] → cta_tweet, chained via replies.
    """
    async with httpx.AsyncClient(timeout=60) as client:
        media_ids: list[str] = []
        if image_path:
            try:
                mid = await _upload_media(client, image_path)
                if mid:
                    media_ids.append(mid)
            except Exception as exc:
                logger.warning("Media upload failed for thread", error=str(exc))

        # Post hook tweet (with image if available)
        hook = content.get("hook_tweet", "")
        first_id = await _post_tweet(client, hook, media_ids or None)

        # Post middle tweets
        prev_id = first_id
        for tweet_text in content.get("tweets", []):
            try:
                prev_id = await _post_tweet(client, tweet_text, reply_to=prev_id)
            except Exception as exc:
                logger.error("Thread tweet failed, stopping chain", error=str(exc))
                break

        # Post CTA tweet
        cta = content.get("cta_tweet", "")
        if cta:
            hashtags = content.get("hashtags", [])
            if hashtags:
                tag_str = " ".join(f"#{h}" for h in hashtags)
                if len(cta) + len(tag_str) + 1 <= 280:
                    cta += "\n" + tag_str
            try:
                await _post_tweet(client, cta, reply_to=prev_id)
            except Exception as exc:
                logger.error("CTA tweet failed", error=str(exc))

        logger.info("Thread published", first_tweet_id=first_id)
        return first_id


async def publish_x_carousel(
    content: dict,
    image_path: str | None = None,
) -> str:
    """Publish an X carousel (tweet with up to 4 images).

    Returns:
        The published tweet's ID.
    """
    async with httpx.AsyncClient(timeout=60) as client:
        media_ids: list[str] = []
        slides = content.get("slides", [])
        if image_path:
            for _ in slides[:4]:
                try:
                    mid = await _upload_media(client, image_path)
                    if mid:
                        media_ids.append(mid)
                except Exception as exc:
                    logger.warning("Carousel slide upload failed", error=str(exc))

        caption = content.get("caption", "")
        tweet_id = await _post_tweet(client, caption, media_ids or None)
        logger.info("X carousel published", tweet_id=tweet_id)
        return tweet_id


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Dispatcher
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


async def publish_to_x(
    x_type: str,
    content: dict,
    image_path: str | None = None,
) -> str:
    """Route to the correct X publisher based on type.

    Args:
        x_type: One of ``tweet``, ``thread``, ``carousel``.
        content: Platform-specific content dict.
        image_path: Optional cover image filename.

    Returns:
        The tweet ID of the published post (first tweet for threads).

    Raises:
        ValueError: If *x_type* is not recognised.
    """
    if x_type == "tweet":
        return await publish_tweet(content, image_path)
    if x_type == "thread":
        return await publish_thread(content, image_path)
    if x_type == "carousel":
        return await publish_x_carousel(content, image_path)
    raise ValueError(f"Unknown X type: {x_type}")
