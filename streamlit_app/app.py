"""AI Creative Studio - web front end for the deployed Creative Director (Agent Engine).

Local:   uv run --project workshop/starter --with streamlit --with markdown \
             streamlit run streamlit_app/app.py
Cloud:   Streamlit Community Cloud, main file = streamlit_app/app.py
"""

import datetime
import pathlib
import sys

import streamlit as st

# Reuse the same report logic as run_campaign.py.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "workshop" / "starter"))
from campaign_report import CampaignRun, build_html, load_images

st.set_page_config(page_title="AI Creative Studio", page_icon="🎨", layout="wide")

MAX_RUNS_PER_SESSION = 3  # each run calls Gemini + image generation, so cap the cost
STEP_LABELS = {
    "brand_strategist": "🔎 Brand Strategist is researching the market...",
    "copywriter": "✍️ Copywriter is writing captions...",
    "designer": "🎨 Designer is generating images...",
    "critic": "🧐 Critic is reviewing copy and images...",
    "project_manager": "🗂️ Project Manager is building the timeline...",
    "get_image_links": "🔗 Preparing image links...",
}
DEFAULT_BRIEF = (
    "Launch campaign for EcoFlow, a smart water bottle that tracks hydration. "
    "Target health-conscious millennials."
)


def secret(key: str, default=None):
    """Read a Streamlit secret without crashing when no secrets file exists (local runs)."""
    try:
        return st.secrets.get(key, default)
    except Exception:
        return default


def require_password() -> None:
    password = secret("APP_PASSWORD")
    if not password or st.session_state.get("authenticated"):
        return
    entered = st.text_input("Password", type="password")
    if entered and entered == password:
        st.session_state["authenticated"] = True
        st.rerun()
    if entered:
        st.error("Wrong password")
    st.stop()


@st.cache_resource(show_spinner=False)
def connect():
    """Connect to the deployed Creative Director. Service-account key on Streamlit Cloud,
    your own gcloud login (ADC) when running locally."""
    import os

    from vertexai import Client

    credentials = None
    sa_info = secret("gcp_service_account")
    if sa_info:
        from google.oauth2 import service_account

        credentials = service_account.Credentials.from_service_account_info(
            dict(sa_info), scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
    project = secret("GOOGLE_CLOUD_PROJECT") or os.getenv("GOOGLE_CLOUD_PROJECT")
    location = secret("LOCATION") or os.getenv("CLOUD_RUN_REGION", "us-central1")
    engine_id = secret("AGENT_ENGINE_ID") or os.getenv("AGENT_ENGINE_ID")
    if not (project and engine_id):
        st.error("Missing GOOGLE_CLOUD_PROJECT or AGENT_ENGINE_ID configuration.")
        st.stop()
    client = Client(project=project, location=location, credentials=credentials)
    name = f"projects/{project}/locations/{location}/reasoningEngines/{engine_id}"
    return client.agent_engines.get(name=name), credentials, project


# ---------------------------------------------------------------- page
st.title("🎨 AI Creative Studio")
st.caption("One brief in → research, captions, real images, a quality review and a launch plan out.")
require_password()

if "local_env" not in st.session_state:
    try:
        from dotenv import load_dotenv

        load_dotenv(pathlib.Path(__file__).resolve().parents[1] / "workshop" / "starter" / ".env")
    except ImportError:
        pass
    st.session_state["local_env"] = True

brief = st.text_area("Campaign brief", DEFAULT_BRIEF, height=110)
runs_used = st.session_state.get("runs", 0)
start = st.button(
    "🚀 Generate campaign",
    type="primary",
    disabled=runs_used >= MAX_RUNS_PER_SESSION or not brief.strip(),
)
st.caption(f"Runs this session: {runs_used}/{MAX_RUNS_PER_SESSION}. A full run takes about 5-10 minutes.")

if start:
    st.session_state["runs"] = runs_used + 1
    agent_engine, credentials, project = connect()
    session = agent_engine.create_session(user_id="streamlit-user")
    run = CampaignRun()

    with st.status("Creative Director is coordinating the team...", expanded=True) as status:
        live_text = st.empty()
        for event in agent_engine.stream_query(
            user_id="streamlit-user", session_id=session["id"], message=brief
        ):
            update = run.add_event(event)
            if update["step"] in STEP_LABELS:
                label = STEP_LABELS[update["step"]]
                if update["step"] == "critic" and run.steps.count("critic") > 1:
                    label = "🔄 Critic is re-reviewing the revised work..."
                st.write(label)
            if update["text"]:
                live_text.markdown(run.text[-1500:])  # show the latest part while streaming
        live_text.empty()
        status.update(label="✅ Campaign complete", state="complete", expanded=False)

    images = load_images(run.image_uris, credentials=credentials, project=project)
    st.session_state["result"] = {"brief": brief, "run": run, "images": images}

result = st.session_state.get("result")
if result:
    run, images = result["run"], result["images"]
    col1, col2, col3 = st.columns(3)
    col1.metric("Specialist calls", len([s for s in run.steps if s in STEP_LABELS]))
    col2.metric("Revision rounds", run.revision_rounds)
    col3.metric("Images", len([i for i in images if "data" in i]))

    st.subheader("📸 Generated images")
    cols = st.columns(max(len(images), 1))
    for col, img in zip(cols, images):
        if "data" in img:
            col.image(img["data"], caption=img["name"])
        else:
            col.warning(f"{img['name']}: {img['error']}")

    st.subheader("📋 Full campaign")
    st.markdown(run.text)

    st.download_button(
        "⬇️ Download HTML report",
        data=build_html(result["brief"], run, images),
        file_name=f"campaign-{datetime.datetime.now():%Y%m%d-%H%M}.html",
        mime="text/html",
    )
