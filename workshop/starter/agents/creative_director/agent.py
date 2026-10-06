import logging
import os

from google.adk.agents import Agent
from google.adk.agents.remote_a2a_agent import RemoteA2aAgent
from google.adk.plugins.logging_plugin import LoggingPlugin
from google.adk.tools import FunctionTool
from google.adk.tools.agent_tool import AgentTool
try:
    from .retry import RETRY_CONFIG
    from .display_image_tool import display_image
    from .get_image_links_tool import get_image_links
except ImportError:
    from retry import RETRY_CONFIG
    from display_image_tool import display_image
    from get_image_links_tool import get_image_links

try:
    from .prompt import SYSTEM_INSTRUCTION_TEMPLATE
except ImportError:
    from prompt import SYSTEM_INSTRUCTION_TEMPLATE  # direct execution fallback

logger = logging.getLogger("ai_creative_studio.creative_director")
logger.setLevel(logging.INFO)


def create_creative_director():
    """
    Create the Creative Director orchestrator.
    Reads specialist URLs from environment variables at runtime.
    """
    # Read specialist URLs from environment
    copywriter_url = os.getenv("COPYWRITER_AGENT_URL")
    designer_url = os.getenv("DESIGNER_AGENT_URL")
    strategist_url = os.getenv("STRATEGIST_AGENT_URL")
    critic_url = os.getenv("CRITIC_AGENT_URL")
    pm_url = os.getenv("PM_AGENT_URL")

    available_agents_list = []
    agent_tools = [
        FunctionTool(func=display_image),
        FunctionTool(func=get_image_links),
    ]

    # TODO 2 (done): Wrap every configured specialist as a REMOTE tool.
    # RemoteA2aAgent only knows the specialist's address: it downloads the agent card
    # (name, skills, endpoint) and sends requests over the network. No specialist
    # code is imported here - each one runs as its own Cloud Run service.
    specialists = [
        ("brand_strategist", strategist_url,
         "Researches audience insights, 2-3 competitors and 3-5 trends. Research only."),
        ("copywriter", copywriter_url,
         ("Writes exactly 3 Instagram captions in different tones, using the research. "
          "Also revises captions when given Critic feedback.")),
        ("designer", designer_url,
         ("Generates one real image per caption, stores it in Cloud Storage and returns "
          "gcs_uri links. Also regenerates images when given Critic feedback.")),
        ("critic", critic_url,
         ("Reviews captions and the real images; returns POSTS / VISUALS / OVERALL with "
          "APPROVED or NEEDS_REVISION.")),
        ("project_manager", pm_url,
         ("Builds the dated timeline, task list, budget and milestones for an APPROVED "
          "campaign; optionally syncs to Notion.")),
    ]
    for name, url, description in specialists:
        if not url:
            logger.warning("%s URL not set - specialist unavailable", name)
            continue
        available_agents_list.append(f"- **{name}**: {description}")
        remote_agent = RemoteA2aAgent(
            name=name,
            description=description,
            agent_card=f"{url.rstrip('/')}/.well-known/agent.json",
        )
        agent_tools.append(AgentTool(agent=remote_agent))
        logger.info("Registered remote specialist %s at %s", name, url)

    available_agents_text = (
        "\n".join(available_agents_list)
        if available_agents_list
        else "No specialist agents configured. Set agent URLs in environment variables."
    )

    system_instruction = SYSTEM_INSTRUCTION_TEMPLATE.format(
        available_agents=available_agents_text
    )

    from google.genai import types
    generation_config = types.GenerateContentConfig(
        max_output_tokens=20000,
        temperature=0.2,
        http_options=types.HttpOptions(
            retry_options=RETRY_CONFIG,
            timeout=120_000,  # 120 second timeout for model calls
        ),
    )

    agent = Agent(
        name="creative_director",
        model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        description="Creative Director orchestrator that coordinates specialist agents",
        instruction=system_instruction,
        tools=agent_tools,
        generate_content_config=generation_config,
    )

    # TODO 3 (done): Wrap the agent in an App with events compaction.
    # A full run moves a lot of text between 5 specialists (research, captions,
    # reviews, revisions). Every 3 turns, older events are summarised by an LLM so
    # the conversation never exceeds the model's context window.
    from google.adk.apps import App
    from google.adk.apps.app import EventsCompactionConfig
    from google.adk.apps.llm_event_summarizer import LlmEventSummarizer
    from google.adk.models import Gemini

    summarizer_model = os.getenv("COMPACTION_MODEL") or os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    compaction_config = EventsCompactionConfig(
        summarizer=LlmEventSummarizer(llm=Gemini(model=summarizer_model)),
        compaction_interval=3,
        overlap_size=1,
    )
    app = App(
        name="creative_director",
        root_agent=agent,
        events_compaction_config=compaction_config,
        plugins=[LoggingPlugin()],
    )
    return agent, app


root_agent, root_app = create_creative_director()
