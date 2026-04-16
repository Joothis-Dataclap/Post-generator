"""Image generation via Google Gemini 2.0 Flash.

Generates a cover image from a text prompt, saves as PNG to the
local ``storage/images/`` directory, and returns the filename.
"""

import base64
import uuid
from pathlib import Path

import structlog

from app.core.config import settings

logger = structlog.get_logger()


async def generate_image(
    prompt: str,
    filename_prefix: str = "image",
) -> str | None:
    """Generate an image using Gemini 2.0 Flash and save it locally.

    Args:
        prompt: Text description of the desired image.
        filename_prefix: Prefix for the saved file name.

    Returns:
        The filename (relative to *images_dir*) on success, or ``None``
        if no image was produced.

    Raises:
        RuntimeError: On Gemini API errors.
    """
    if not settings.gemini_api_key:
        logger.info("No Gemini API key configured — skipping image generation")
        return None

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=settings.gemini_api_key)

        response = client.models.generate_content(
            model="gemini-2.0-flash-exp",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE", "TEXT"],
            ),
        )
    except Exception as exc:
        logger.error("Gemini API call failed", error=str(exc))
        raise RuntimeError(f"Gemini image generation error: {exc}") from exc

    # Walk response parts looking for the first inline image
    try:
        for part in response.candidates[0].content.parts:
            if part.inline_data is not None:
                image_data = part.inline_data.data
                mime_type = part.inline_data.mime_type or "image/png"

                ext = "png"
                if "jpeg" in mime_type or "jpg" in mime_type:
                    ext = "jpg"
                elif "webp" in mime_type:
                    ext = "webp"

                filename = f"{filename_prefix}_{uuid.uuid4().hex[:8]}.{ext}"
                save_path = Path(settings.images_dir) / filename
                save_path.parent.mkdir(parents=True, exist_ok=True)

                with open(save_path, "wb") as f:
                    if isinstance(image_data, str):
                        f.write(base64.b64decode(image_data))
                    else:
                        f.write(image_data)

                logger.info("Image generated and saved", filename=filename)
                return filename
    except (IndexError, AttributeError) as exc:
        logger.warning("Unexpected Gemini response structure", error=str(exc))

    logger.warning("No image part found in Gemini response")
    return None
