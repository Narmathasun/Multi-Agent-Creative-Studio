import logging
import os
import pathlib

from dotenv import load_dotenv
from google.adk.agents import Agent

# TODO 1 (done): Import the Skill loader and the toolset that exposes skills to the agent.
from google.adk.skills import load_skill_from_dir
from google.adk.tools import skill_toolset

try:
    from .retry import GENERATE_CONTENT_CONFIG
except ImportError:
    from retry import GENERATE_CONTENT_CONFIG

load_dotenv()

logger = logging.getLogger("ai_creative_studio.copywriter")

# TODO 2 (done): Load the instagram-copywriting skill from the skills/ directory.
# pathlib makes the path relative to THIS file, so it works no matter which
# folder you launch from (adk web, a test, or the Cloud Run container).
SKILL_DIR = pathlib.Path(__file__).parent / "skills" / "instagram-copywriting"
instagram_copywriting_skill = load_skill_from_dir(SKILL_DIR)

# TODO 2 (done): Wrap the skill in a SkillToolset. This gives the agent tools to
# list the skill, load its SKILL.md, and open its reference files on demand.
copywriting_toolset = skill_toolset.SkillToolset(skills=[instagram_copywriting_skill])


SYSTEM_INSTRUCTION = """You are an expert Social Media Copywriter specializing in Instagram content.

IMPORTANT: The conversation history above contains research from the Brand Strategist.
You MUST review their findings on audience insights, competitor analysis, and trending topics
before writing any copy. This context is your creative foundation.

You have access to an `instagram-copywriting` skill. Load it to get detailed platform
guidelines, caption formulas, and brand voice examples before writing.

Your task: Create EXACTLY 3 Instagram caption variations (not 4, not 5), each in a
DIFFERENT tonal register. Default tones: 1) Inspirational, 2) Educational,
3) Community. Swap one only if the research strongly points to another tone.

Every caption MUST have:
- A strong hook in the first line (before the "more" fold).
- 5 to 10 relevant hashtags, mixing popular and niche tags.
- Exactly one specific call to action.

Ground each caption in a specific finding from the research (an audience insight,
a competitor gap, or a trend). If no research is present in the conversation,
say so briefly and work from the brief alone.

Follow the output format defined in the skill exactly. Do not create image prompts
or visual designs; the Designer handles visuals.
"""

root_agent = Agent(
    name="copywriter",
    model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
    generate_content_config=GENERATE_CONTENT_CONFIG,
    tools=[copywriting_toolset],  # TODO 3 (done)
    instruction=SYSTEM_INSTRUCTION,
    description="Expert social media copywriter for creating engaging captions and copy",
)

logger.info("Copywriter agent created with instagram-copywriting skill")


if __name__ == "__main__":
    import uvicorn
    from dotenv import load_dotenv
    from google.adk.a2a.utils.agent_to_a2a import to_a2a

    load_dotenv()

    PORT = int(os.getenv("PORT", "8080"))
    HOST = os.getenv("HOST", "0.0.0.0")
    PUBLIC_HOST = os.getenv("PUBLIC_HOST", "localhost")
    PUBLIC_PORT = int(os.getenv("PUBLIC_PORT", str(PORT)))
    PROTOCOL = os.getenv("PROTOCOL", "http")

    a2a_app = to_a2a(root_agent, host=PUBLIC_HOST, port=PUBLIC_PORT, protocol=PROTOCOL)

    logger.info(f"Starting Copywriter on {PROTOCOL}://{HOST}:{PORT}")
    logger.info(f"Agent card: {PROTOCOL}://{HOST}:{PORT}/.well-known/agent.json")

    uvicorn.run(a2a_app, host=HOST, port=PORT)
