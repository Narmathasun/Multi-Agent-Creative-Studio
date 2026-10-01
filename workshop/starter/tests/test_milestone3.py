"""Offline tests for the Designer's image tool.

Gemini and Cloud Storage are replaced with fakes, so these run in a second
with no cost and no network. They check OUR logic: aspect-ratio injection,
byte extraction, the gs:// link, and graceful errors.
"""

import asyncio
import pathlib
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "agents"))

from designer import image_gen_tool as tool
from designer.agent import root_agent as designer

FAKE_PNG = b"\x89PNG fake image bytes"


class FakeToolContext:
    def __init__(self):
        self.saved = {}

    async def save_artifact(self, filename, artifact):
        self.saved[filename] = artifact


def make_fake_client(captured, parts):
    class FakeModels:
        async def generate_content(self, model, contents, config):
            captured["model"] = model
            captured["contents"] = contents
            captured["config"] = config
            content = SimpleNamespace(parts=parts)
            return SimpleNamespace(candidates=[SimpleNamespace(content=content)])

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs
            self.aio = SimpleNamespace(models=FakeModels())

    return FakeClient


def image_part(data=FAKE_PNG, mime="image/png"):
    return SimpleNamespace(inline_data=SimpleNamespace(data=data, mime_type=mime))


def text_part():
    return SimpleNamespace(inline_data=None, text="I can't draw that.")


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("GCS_IMAGES_BUCKET", "test-bucket")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "global")
    monkeypatch.setenv("GEMINI_IMAGE_MODEL", "test-image-model")
    uploads = []
    monkeypatch.setattr(
        tool,
        "_upload_to_gcs",
        lambda bucket, blob, data, mime: uploads.append((blob, data, mime))
        or f"gs://{bucket}/{blob}",
    )
    return uploads


def run(captured, parts, monkeypatch, aspect="1:1", name="caption1_concept_a"):
    monkeypatch.setattr(tool.genai, "Client", make_fake_client(captured, parts))
    ctx = FakeToolContext()
    result = asyncio.run(tool.generate_image(name, "A bottle on a desk", aspect, ctx))
    return result, ctx


def test_success_returns_gcs_uri_not_bytes(env, monkeypatch):
    captured = {}
    result, ctx = run(captured, [text_part(), image_part()], monkeypatch)
    assert result["status"] == "success"
    assert result["gcs_uri"].startswith("gs://test-bucket/campaign-images/caption1_concept_a-")
    assert result["gcs_uri"].endswith(".png")
    assert not any(isinstance(v, bytes) for v in result.values())  # no raw bytes
    assert env[0][1] == FAKE_PNG  # but the bytes were uploaded
    assert captured["client_kwargs"]["vertexai"] is True
    assert captured["model"] == "test-image-model"
    assert "caption1_concept_a.png" in ctx.saved


def test_aspect_ratio_injected_into_prompt(env, monkeypatch):
    square, portrait = {}, {}
    run(square, [image_part()], monkeypatch, aspect="1:1")
    run(portrait, [image_part()], monkeypatch, aspect="4:5")
    assert "square 1:1" in square["contents"]
    assert "portrait 4:5" in portrait["contents"]


def test_unknown_aspect_falls_back_to_square(env, monkeypatch):
    captured = {}
    result, _ = run(captured, [image_part()], monkeypatch, aspect="16:9")
    assert result["aspect_ratio"] == "1:1"
    assert "square 1:1" in captured["contents"]


def test_retry_is_configured(env, monkeypatch):
    captured = {}
    run(captured, [image_part()], monkeypatch)
    retry = captured["config"].http_options.retry_options
    assert retry.attempts >= 3
    assert 429 in retry.http_status_codes


def test_no_image_returns_error_not_crash(env, monkeypatch):
    result, _ = run({}, [text_part()], monkeypatch)
    assert result == {"status": "error", "error": "Gemini returned no image data"}


def test_missing_bucket_returns_error(monkeypatch):
    monkeypatch.delenv("GCS_IMAGES_BUCKET", raising=False)
    result = asyncio.run(tool.generate_image("x", "p", "1:1", FakeToolContext()))
    assert result["status"] == "error"


def test_unsafe_concept_name_is_cleaned(env, monkeypatch):
    result, _ = run({}, [image_part()], monkeypatch, name="Caption 1: Hydrate/Now!")
    assert "/campaign-images/caption-1-hydrate-now-" in result["gcs_uri"]


def test_designer_registers_generate_image_tool():
    assert [t.name for t in designer.tools] == ["generate_image"]
