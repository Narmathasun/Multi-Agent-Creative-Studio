"""Gemini native image generation tool. Generates image, uploads to GCS, returns URI."""
import asyncio
import io
import logging
import os
import re
import uuid

from google import genai
from google.adk.tools import ToolContext
from google.cloud import storage
from google.genai import types

logger = logging.getLogger("ai_creative_studio.designer.image_gen")

# Only the two Instagram feed formats the campaign uses.
ASPECT_HINTS = {
    "1:1": "square 1:1 aspect ratio (1080x1080)",
    "4:5": "portrait 4:5 aspect ratio (1080x1350)",
}


def _safe_name(concept_name: str) -> str:
    """Make a concept name safe to use inside a Cloud Storage object path."""
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", concept_name).strip("-").lower()
    return cleaned[:60] or "concept"


def _upload_to_gcs(bucket_name: str, blob_name: str, data: bytes, mime_type: str) -> str:
    """Blocking upload helper. Runs in a worker thread (see asyncio.to_thread below)."""
    bucket = storage.Client().bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.upload_from_file(io.BytesIO(data), content_type=mime_type)
    return f"gs://{bucket_name}/{blob_name}"


async def generate_image(
    concept_name: str,
    image_prompt: str,
    aspect_ratio: str,
    tool_context: ToolContext,
) -> dict:
    """
    Generate an image with Gemini native image generation and upload it to GCS.

    Args:
        concept_name: Short identifier for this image concept (e.g. "post1_concept_a")
        image_prompt: Full image generation prompt string
        aspect_ratio: "1:1" for square (1080x1080) or "4:5" for portrait (1080x1350)

    Returns:
        {"status": "success", "gcs_uri": "gs://...", "concept_name": "...", "aspect_ratio": "..."}
        or {"status": "error", "error": "..."}
    """
    bucket_name = os.environ.get("GCS_IMAGES_BUCKET")
    if not bucket_name:
        return {"status": "error", "error": "GCS_IMAGES_BUCKET env var not set"}

    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")
    # Use a dedicated image-capable model; separate from the text GEMINI_MODEL.
    # The image model does NOT support function calling, so it cannot be an ADK
    # agent - but calling it directly from inside this tool is fine.
    image_model = os.environ.get("GEMINI_IMAGE_MODEL", "gemini-3.1-flash-image")

    # Only accept the two supported formats; anything else falls back to square
    # so the agent can never send the image model an unusable instruction.
    if aspect_ratio not in ASPECT_HINTS:
        logger.warning("Unsupported aspect_ratio %r - defaulting to 1:1", aspect_ratio)
        aspect_ratio = "1:1"

    # Aspect ratio is not an API parameter for Gemini native generation - inject it
    # into the prompt so the model respects the desired Instagram format.
    prompt_with_aspect = (
        f"{image_prompt}\n\nGenerate this as a {ASPECT_HINTS[aspect_ratio]} image."
    )

    try:
        # TODO 1 (done): Create the client and call the image model.
        # client.aio = the async version, so a slow image call does not freeze the
        # server for other requests while we wait.
        client = genai.Client(vertexai=True, project=project_id, location=location)
        response = await client.aio.models.generate_content(
            model=image_model,
            contents=prompt_with_aspect,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE", "TEXT"],
                http_options=types.HttpOptions(
                    # Image quota recovers slowly, so wait 30s, 60s, 120s... between
                    # attempts instead of failing on the first 429.
                    retry_options=types.HttpRetryOptions(
                        attempts=5,
                        exp_base=2,
                        initial_delay=30,
                        http_status_codes=[429, 500, 503, 504],
                    ),
                    timeout=180_000,
                ),
            ),
        )

        # TODO 2 (done): Extract image bytes from the response.
        image_bytes = None
        mime_type = "image/png"
        candidates = response.candidates or []
        parts = (
            candidates[0].content.parts
            if candidates and candidates[0].content and candidates[0].content.parts
            else []
        )
        for part in parts:
            if part.inline_data is not None and part.inline_data.data:
                image_bytes = part.inline_data.data
                mime_type = part.inline_data.mime_type or "image/png"
                break

        if not image_bytes:
            # Often a safety block: the model replies with text instead of an image.
            return {"status": "error", "error": "Gemini returned no image data"}

        # TODO 3 (done): Upload to GCS and build the gs:// URI.
        ext = "jpg" if "jpeg" in mime_type else "png"
        blob_name = f"campaign-images/{_safe_name(concept_name)}-{uuid.uuid4().hex[:8]}.{ext}"
        # The storage library is synchronous, so run it in a worker thread.
        gcs_uri = await asyncio.to_thread(
            _upload_to_gcs, bucket_name, blob_name, image_bytes, mime_type
        )
        logger.info("Uploaded %s (%s) to %s", concept_name, aspect_ratio, gcs_uri)

        # Save as ADK artifact so adk web renders the image inline when testing
        # the Designer directly. Silently skipped when no artifact service is
        # configured (e.g. Cloud Run deployment).
        try:
            artifact = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
            await tool_context.save_artifact(f"{concept_name}.{ext}", artifact)
        except ValueError:
            pass

        # Return only the link - never the raw bytes.
        return {
            "status": "success",
            "gcs_uri": gcs_uri,
            "concept_name": concept_name,
            "aspect_ratio": aspect_ratio,
        }

    except Exception as e:
        logger.exception("Image generation failed for %s", concept_name)
        return {"status": "error", "error": f"{type(e).__name__}: {e}"}
