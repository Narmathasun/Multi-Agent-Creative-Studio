"""Collect a Creative Director run and turn it into a self-contained HTML report.

Shared by run_campaign.py (command line) and streamlit_app/app.py (web UI).
"""

import base64
import datetime
import html
import json
import re

GCS_URI_RE = re.compile(r"gs://[a-z0-9._-]+/[^\s\"'()\[\]]+?\.(?:png|jpe?g|webp)", re.IGNORECASE)
STEP_NAMES = ["brand_strategist", "copywriter", "designer", "critic", "project_manager"]


class CampaignRun:
    """Accumulates text, specialist calls and image URIs from Agent Engine stream events."""

    def __init__(self):
        self.text_parts: list[str] = []
        self.steps: list[str] = []
        self.all_uris: list[str] = []
        self.final_uris: list[str] | None = None

    def add_event(self, event: dict) -> dict:
        """Record one streamed event. Returns {"text": new text, "step": tool called or None}."""
        new_text, step = "", None
        parts = (event.get("content") or {}).get("parts") or []
        for part in parts:
            if part.get("text"):
                self.text_parts.append(part["text"])
                new_text += part["text"]
            call = part.get("function_call")
            if call and call.get("name"):
                step = call["name"]
                self.steps.append(step)
                if step == "get_image_links":
                    # The orchestrator passes the FINAL (post-revision) images here.
                    uris = (call.get("args") or {}).get("gcs_uris") or []
                    if uris:
                        self.final_uris = list(uris)
        for uri in GCS_URI_RE.findall(json.dumps(event, default=str)):
            if uri not in self.all_uris:
                self.all_uris.append(uri)
        return {"text": new_text, "step": step}

    @property
    def text(self) -> str:
        return "".join(self.text_parts)

    @property
    def image_uris(self) -> list[str]:
        return self.final_uris or self.all_uris

    @property
    def revision_rounds(self) -> int:
        """Each Critic call after the first one is a re-review, i.e. one revision round."""
        return max(self.steps.count("critic") - 1, 0)


def concept_name(gcs_uri: str) -> str:
    filename = gcs_uri.rsplit("/", 1)[-1]
    return re.sub(r"-[0-9a-f]{8}\.[^.]+$", "", filename)


def load_images(gcs_uris: list[str], credentials=None, project: str | None = None) -> list[dict]:
    """Download images from Cloud Storage. Failures are skipped, never fatal."""
    from google.cloud import storage

    client = storage.Client(project=project, credentials=credentials)
    images = []
    for uri in gcs_uris:
        try:
            bucket_name, blob_path = uri[len("gs://"):].split("/", 1)
            blob = client.bucket(bucket_name).blob(blob_path)
            data = blob.download_as_bytes()
            mime = "image/jpeg" if blob_path.lower().endswith((".jpg", ".jpeg")) else "image/png"
            images.append({"uri": uri, "name": concept_name(uri), "mime": mime, "data": data})
        except Exception as err:  # keep going: a missing image must not lose the report
            images.append({"uri": uri, "name": concept_name(uri), "error": str(err)})
    return images


def markdown_to_html(text: str) -> str:
    try:
        import markdown

        return markdown.markdown(text, extensions=["tables", "fenced_code", "sane_lists"])
    except ImportError:
        return f"<pre>{html.escape(text)}</pre>"


def build_html(brief: str, run: CampaignRun, images: list[dict]) -> str:
    """A single HTML file with images embedded, so it keeps working after links expire."""
    created = datetime.datetime.now().strftime("%B %d, %Y %H:%M")
    cards = []
    for img in images:
        if "data" in img:
            b64 = base64.b64encode(img["data"]).decode()
            body = f'<img src="data:{img["mime"]};base64,{b64}" alt="{html.escape(img["name"])}">'
        else:
            body = f'<p class="err">Could not load image: {html.escape(img.get("error", ""))}</p>'
        cards.append(f'<figure>{body}<figcaption>{html.escape(img["name"])}</figcaption></figure>')
    steps = " → ".join(s for s in run.steps if s in STEP_NAMES) or "n/a"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Campaign Report</title>
<style>
 body {{ font-family: system-ui, Arial, sans-serif; max-width: 1000px; margin: 2rem auto;
        padding: 0 1rem; line-height: 1.55; color: #1f2328; }}
 h1 {{ margin-bottom: .2rem; }} .meta {{ color: #57606a; font-size: .9rem; }}
 .brief {{ background: #f6f8fa; border-left: 4px solid #6e40c9; padding: .8rem 1rem; }}
 .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 1rem; }}
 figure {{ margin: 0; background: #f6f8fa; padding: .5rem; border-radius: 8px; }}
 figure img {{ width: 100%; border-radius: 6px; }} figcaption {{ font-size: .85rem; color: #57606a; }}
 table {{ border-collapse: collapse; width: 100%; }} td, th {{ border: 1px solid #d0d7de; padding: .35rem .5rem; }}
 .err {{ color: #cf222e; }}
</style></head><body>
<h1>🎨 Instagram Campaign Report</h1>
<p class="meta">Generated {created} · Pipeline: {html.escape(steps)} · Revision rounds: {run.revision_rounds}</p>
<h2>Brief</h2><div class="brief">{html.escape(brief.strip())}</div>
<h2>Generated Images</h2><div class="grid">{"".join(cards) or "<p>No images found.</p>"}</div>
<h2>Full Campaign</h2>{markdown_to_html(run.text)}
</body></html>"""
