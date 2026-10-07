"""
Run a campaign through the deployed Creative Director on Agent Engine.
Usage:
    uv run run_campaign.py                       # built-in EcoFlow brief
    uv run run_campaign.py "One-line brief ..."  # your own brief

Prints the run live and saves a self-contained HTML report in reports/.
"""

import datetime
import os
import pathlib
import sys

import vertexai
from dotenv import load_dotenv
from vertexai import Client

from campaign_report import CampaignRun, build_html, load_images

load_dotenv()

PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT_ID") or os.getenv("PROJECT_ID")
LOCATION = os.getenv("CLOUD_RUN_REGION") or os.getenv("GCP_REGION") or os.getenv("LOCATION", "us-central1")

vertexai.init(project=PROJECT_ID, location=LOCATION)
client = Client(project=PROJECT_ID, location=LOCATION)

resource_name = (
    f"projects/{PROJECT_ID}/locations/{LOCATION}/"
    f"reasoningEngines/{os.getenv('AGENT_ENGINE_ID')}"
)

agent_engine = client.agent_engines.get(name=resource_name)
session = agent_engine.create_session(user_id="workshop-user")
print(f"Session: {session['id']}\n")

DEFAULT_BRIEF = """
Create a complete Instagram campaign for:
- Product: EcoFlow Smart Water Bottle (tracks hydration, keeps drinks cold 24h)
- Target Audience: Health-conscious millennials, 25-35 years old
- Platform: Instagram
- Goal: Brand awareness + drive website traffic
- Brand Voice: Motivational, clean, science-backed
- Budget: $3,000
- Timeline: Launch in 2 weeks
"""

# A brief passed on the command line overrides the default.
campaign_brief = " ".join(sys.argv[1:]).strip() or DEFAULT_BRIEF
print(f"Brief: {campaign_brief.strip()}\n")

run = CampaignRun()
for event in agent_engine.stream_query(
    user_id="workshop-user",
    session_id=session["id"],
    message=campaign_brief,
):
    update = run.add_event(event)
    if update["step"]:
        print(f"\n[→ calling {update['step']}]", flush=True)
    if update["text"]:
        print(update["text"], end="", flush=True)

# Save the finished campaign as one HTML file with the images embedded.
images = load_images(run.image_uris, project=PROJECT_ID)
reports_dir = pathlib.Path(__file__).parent / "reports"
reports_dir.mkdir(exist_ok=True)
report_path = reports_dir / f"campaign-{datetime.datetime.now():%Y%m%d-%H%M%S}.html"
report_path.write_text(build_html(campaign_brief, run, images), encoding="utf-8")
print(f"\n\n📄 Report saved: {report_path}  ({len(images)} images, {run.revision_rounds} revision round(s))")
