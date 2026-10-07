"""Offline tests for the shared campaign report (used by run_campaign.py and Streamlit)."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from campaign_report import CampaignRun, build_html, concept_name

URI_A = "gs://bkt/campaign-images/caption1_concept_a-1a2b3c4d.png"
URI_B = "gs://bkt/campaign-images/caption2_concept_b-5e6f7a8b.png"
URI_B2 = "gs://bkt/campaign-images/caption2_concept_b_v2-9c0d1e2f.png"


def call(name, **args):
    return {"content": {"parts": [{"function_call": {"name": name, "args": args}}]}}


def text(t):
    return {"content": {"parts": [{"text": t}]}}


def sample_run():
    run = CampaignRun()
    for event in [
        text("Starting research... "),
        call("brand_strategist", request="brief"),
        call("critic", request=f"Review {URI_A} and {URI_B}"),
        call("designer", request="REVISION"),
        call("critic", request=f"RE-REVIEW {URI_A} and {URI_B2}"),
        call("get_image_links", gcs_uris=[URI_A, URI_B2]),
        call("project_manager", request="plan"),
        text("**Project Timeline:** done"),
    ]:
        run.add_event(event)
    return run


def test_collects_text_steps_and_revisions():
    run = sample_run()
    assert "Project Timeline" in run.text
    assert run.steps.count("critic") == 2
    assert run.revision_rounds == 1


def test_final_images_come_from_get_image_links():
    run = sample_run()
    assert run.image_uris == [URI_A, URI_B2]  # superseded URI_B is excluded
    assert URI_B in run.all_uris


def test_concept_name_strips_suffix():
    assert concept_name(URI_A) == "caption1_concept_a"


def test_html_embeds_images_and_escapes_brief():
    run = sample_run()
    images = [{"uri": URI_A, "name": "caption1_concept_a", "mime": "image/png", "data": b"\x89PNG"},
              {"uri": URI_B2, "name": "caption2_concept_b_v2", "error": "404"}]
    page = build_html("<script>x</script> EcoFlow", run, images)
    assert "data:image/png;base64," in page
    assert "&lt;script&gt;" in page and "<script>x" not in page
    assert "Revision rounds: 1" in page
    assert "Could not load image" in page
