"""Offline tests for the Critic: image-review tool (mocked Gemini) and verdict format."""

import asyncio
import json
import pathlib
import sys
from types import SimpleNamespace

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agents"))
sys.path.insert(0, str(ROOT / "tests"))

from critic import image_review_tool as tool
from critic.agent import root_agent as critic
from critic_format import parse_verdict


def fake_client(captured, payload):
    class FakeModels:
        async def generate_content(self, model, contents, config):
            captured.update(model=model, contents=contents, config=config)
            return SimpleNamespace(text=json.dumps(payload))

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs
            self.aio = SimpleNamespace(models=FakeModels())

    return FakeClient


def review(monkeypatch, payload, uri="gs://bucket/campaign-images/a.png"):
    captured = {}
    monkeypatch.setattr(tool.genai, "Client", fake_client(captured, payload))
    result = asyncio.run(tool.review_image(uri, "caption1_concept_a", "EcoFlow bottle"))
    return result, captured


GOOD = {"score": 8, "approval_status": "APPROVED", "what_works": "Clean light",
        "issues": "None", "suggestions": "None"}


def test_review_returns_structured_fields(monkeypatch):
    result, captured = review(monkeypatch, GOOD)
    assert result["status"] == "success"
    assert result["score"] == 8
    assert result["approval_status"] == "APPROVED"
    assert set(result) >= {"what_works", "issues", "suggestions", "concept_name"}
    assert captured["config"].response_mime_type == "application/json"
    assert captured["client_kwargs"]["vertexai"] is True


def test_image_is_sent_as_gcs_reference(monkeypatch):
    _, captured = review(monkeypatch, GOOD, uri="gs://bucket/x.jpg")
    image_part = captured["contents"][0]
    assert image_part.file_data.file_uri == "gs://bucket/x.jpg"
    assert image_part.file_data.mime_type == "image/jpeg"


def test_status_follows_score_rubric(monkeypatch):
    contradictory = {**GOOD, "score": 5, "approval_status": "APPROVED"}
    result, _ = review(monkeypatch, contradictory)
    assert result["score"] == 5  # score used as-is
    assert result["approval_status"] == "NEEDS_REVISION"  # status made consistent


def test_invalid_uri_returns_error(monkeypatch):
    result, _ = review(monkeypatch, GOOD, uri="https://example.com/a.png")
    assert result["status"] == "error"


def test_bad_json_returns_error_not_crash(monkeypatch):
    result, _ = review(monkeypatch, {"score": 99})
    assert result["status"] == "error"


def test_critic_registers_review_image():
    assert [t.name for t in critic.tools] == ["review_image"]
    assert "LOWEST" in critic.instruction
    assert "NOT_REVIEWED" in critic.instruction


APPROVED_SAMPLE = """**POSTS REVIEW:**
- Score: 8/10
- Status: APPROVED
- What Works: Strong hooks
- Issues: None
- Suggestions: None

**VISUALS REVIEW:**
- Score: 7/10
- Status: APPROVED
- What Works: Bright, on-brand
- Issues: None
- Suggestions: None

**OVERALL ASSESSMENT:**
- All Approved: YES
- Priority Revisions: None
- Overall Score: 7/10
"""

REVISION_SAMPLE = APPROVED_SAMPLE.replace(
    "- Score: 7/10\n- Status: APPROVED", "- Score: 4/10\n- Status: NEEDS_REVISION"
).replace("All Approved: YES", "All Approved: NO").replace(
    "Priority Revisions: None", "Priority Revisions: Redo caption2_concept_b - warped bottle"
).replace("Overall Score: 7/10", "Overall Score: 4/10")

NO_IMAGES_SAMPLE = APPROVED_SAMPLE.replace(
    "- Score: 7/10\n- Status: APPROVED\n- What Works: Bright, on-brand",
    "- Score: N/A\n- Status: NOT_REVIEWED\n- What Works: No images provided for review.",
).replace("Overall Score: 7/10", "Overall Score: 8/10")


def test_parser_accepts_approved():
    assert parse_verdict(APPROVED_SAMPLE)["needs_revision"] is False


def test_parser_accepts_needs_revision():
    verdict = parse_verdict(REVISION_SAMPLE)
    assert verdict["needs_revision"] is True
    assert "caption2" in verdict["priority"]


def test_parser_treats_not_reviewed_as_approved():
    assert parse_verdict(NO_IMAGES_SAMPLE)["needs_revision"] is False


def test_parser_rejects_wrong_overall_score():
    with pytest.raises(ValueError, match="lowest"):
        parse_verdict(APPROVED_SAMPLE.replace("Overall Score: 7/10", "Overall Score: 8/10"))


def test_parser_rejects_missing_section():
    with pytest.raises(ValueError, match="VISUALS"):
        parse_verdict(APPROVED_SAMPLE.replace("**VISUALS REVIEW:**", "Visuals:"))
