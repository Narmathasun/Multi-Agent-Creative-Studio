"""Multimodal image review tool. Loads image from GCS via Part.from_uri()."""
import logging
import os
from typing import Literal, Optional

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

logger = logging.getLogger("ai_creative_studio.critic.image_review")

# Rubric threshold shared with the Critic's system instruction: 7+ is APPROVED.
APPROVAL_THRESHOLD = 7


class _GeminiReview(BaseModel):
    """Internal schema used to force structured JSON output from Gemini."""
    score: int = Field(ge=1, le=10, description="Quality score from 1 to 10")
    approval_status: Literal["APPROVED", "NEEDS_REVISION"]
    what_works: str = Field(description="Specific visual strengths observed in the image")
    issues: str = Field(description="Specific problems identified, or 'None' if approved")
    suggestions: str = Field(description="Concrete improvements if NEEDS_REVISION, or 'None' if approved")


class ImageReviewResult(BaseModel):
    """Structured result returned to the Critic agent by the review_image tool."""
    status: Literal["success", "error"]
    concept_name: str
    score: Optional[int] = None
    approval_status: Optional[Literal["APPROVED", "NEEDS_REVISION"]] = None
    what_works: Optional[str] = None
    issues: Optional[str] = None
    suggestions: Optional[str] = None
    error: Optional[str] = None


def _status_for(score: int) -> Literal["APPROVED", "NEEDS_REVISION"]:
    """Derive the status from the score so the two can never contradict each other."""
    return "APPROVED" if score >= APPROVAL_THRESHOLD else "NEEDS_REVISION"


async def review_image(gcs_uri: str, concept_name: str, campaign_context: str) -> dict:
    """
    Review an image stored in GCS using Gemini multimodal.

    Vertex AI fetches the image from GCS server-side - no GCS credentials needed
    on the Critic container.

    Args:
        gcs_uri: GCS URI of the image (gs://bucket/path.png)
        concept_name: Name/label for this image concept
        campaign_context: Brief describing the campaign, brand voice, target audience

    Returns:
        ImageReviewResult (as a dict) with score (1-10), approval_status
        (APPROVED/NEEDS_REVISION), and structured feedback fields. On error,
        status="error" and error contains the message.
    """
    if not gcs_uri or not gcs_uri.startswith("gs://"):
        return ImageReviewResult(
            status="error",
            concept_name=concept_name,
            error=f"Invalid gcs_uri {gcs_uri!r}: must start with gs://",
        ).model_dump(exclude_none=True)

    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")
    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

    try:
        client = genai.Client(vertexai=True, project=project_id, location=location)

        # Infer mime type from the GCS URI extension
        ext = gcs_uri.rsplit(".", 1)[-1].lower() if "." in gcs_uri else "png"
        mime_map = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}
        mime_type = mime_map.get(ext, "image/png")

        # TODO 1 (done): A *reference* to the image. Vertex AI downloads it from GCS
        # on Google's side, so we never pull megabytes into this container.
        image_part = types.Part.from_uri(file_uri=gcs_uri, mime_type=mime_type)

        prompt = f"""You are reviewing an AI-generated image for an Instagram campaign.

Campaign context: {campaign_context}
Concept name: {concept_name}

Evaluate this image on:
- Visual quality and composition
- Brand alignment and audience fit
- Instagram platform suitability
- Visual-copy alignment potential

Also check for common AI-image defects: distorted hands or faces, garbled or
misspelled text, warped products, and unrealistic physics. Any of these should
lower the score.

Scoring guide:
- 9-10: APPROVED (exceptional)
- 7-8:  APPROVED (good, minor polish only)
- 5-6:  NEEDS_REVISION (has potential but needs improvement)
- 1-4:  NEEDS_REVISION (significant issues)

Be specific: name what you actually see in the image.
"""

        # TODO 2 (done): Ask Gemini to look at the image and answer ONLY in our JSON schema.
        response = await client.aio.models.generate_content(
            model=model,
            contents=[image_part, prompt],
            config=types.GenerateContentConfig(
                response_schema=_GeminiReview,
                response_mime_type="application/json",
                temperature=0.2,  # low = consistent scores for the same image
                http_options=types.HttpOptions(
                    retry_options=types.HttpRetryOptions(
                        attempts=4,
                        exp_base=2,
                        initial_delay=5,
                        http_status_codes=[429, 500, 503, 504],
                    ),
                    timeout=120_000,
                ),
            ),
        )

        # TODO 3 (done): Parse and validate the JSON into our Pydantic model.
        review = _GeminiReview.model_validate_json(response.text)

        # Keep the status consistent with the rubric. The score itself is used as-is.
        consistent_status = _status_for(review.score)
        if consistent_status != review.approval_status:
            logger.warning(
                "%s: model said %s for score %d - using %s per rubric",
                concept_name, review.approval_status, review.score, consistent_status,
            )
        data = review.model_dump()
        data["approval_status"] = consistent_status

        return ImageReviewResult(
            status="success", concept_name=concept_name, **data
        ).model_dump(exclude_none=True)

    except Exception as e:
        logger.exception("Image review failed for %s", concept_name)
        return ImageReviewResult(
            status="error",
            concept_name=concept_name,
            error=f"{type(e).__name__}: {e}",
        ).model_dump(exclude_none=True)
