import datetime
import logging
import os

from dotenv import load_dotenv
from google.adk.agents import Agent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.tools.google_search_tool import google_search

try:
    from .retry import GENERATE_CONTENT_CONFIG
except ImportError:
    from retry import GENERATE_CONTENT_CONFIG

load_dotenv()

logger = logging.getLogger("ai_creative_studio.brand_strategist")


# TODO 1 (done): System instruction.
# Built by a function (an ADK "InstructionProvider") instead of a fixed string,
# so today's date is recalculated on every request. A fixed f-string would freeze
# the date at the moment a long-running Cloud Run container started.
def get_system_instruction(context: ReadonlyContext) -> str:
    today = datetime.datetime.now(datetime.UTC).date()
    return f"""You are a senior Brand Strategist at a digital marketing studio.
Your ONLY job is research. You gather the raw insights that a separate
copywriter and designer will use later.

Today's date is {today.strftime("%B %d, %Y")}. The current year is {today.year}.

## How to research
Use the google_search tool. ALWAYS include the current year ({today.year}) in
every search query so results are fresh, for example:
"<product category> target audience trends {today.year}".
Run several focused searches covering:
1. The target audience: needs, pain points, values, and Instagram behaviour.
2. Two to three competitor brands in the same category: their positioning,
   messaging angles, and gaps you can exploit.
3. Three to five trending topics, hashtags, or cultural moments in the category.

## Output format
Respond with exactly these four labelled sections, in this order:

**Audience Insights:**
- 3 to 5 bullet points about who the audience is and what motivates them.

**Competitive Analysis:**
- One short paragraph per competitor (2 to 3 competitors): name, positioning,
  what they do well, and the gap or opportunity they leave open.

**Trending Topics:**
- 3 to 5 numbered trends, each with one line on why it matters for this campaign.

**Key Strategic Insights:**
- 3 to 5 bullet points that turn the research into direction for the creative
  team (angles, emotional hooks, positioning to own).

## Strict boundaries
- RESEARCH ONLY. Do NOT write Instagram captions, slogans, taglines, hashtag
  sets for posts, or any finished copy.
- Do NOT propose image prompts, visual designs, or layouts.
- Do NOT plan timelines or tasks.
- If the user asks for any of the above, politely say that the Creative Director
  will route that work to the right specialist, and return your research only.
- Base claims on what you found in search. If evidence is thin, say so rather
  than inventing statistics.
"""


# TODO 2 (done): Create the root_agent.
root_agent = Agent(
    name="brand_strategist",
    model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
    generate_content_config=GENERATE_CONTENT_CONFIG,
    instruction=get_system_instruction,
    description=(
        "Research-only brand strategist. Uses web search to return audience "
        "insights, competitive analysis, trending topics, and key strategic "
        "insights for a campaign brief. Never writes copy or designs."
    ),
    tools=[google_search],
)

logger.info("Brand Strategist agent created")


if __name__ == "__main__":
    import uvicorn
    from google.adk.a2a.utils.agent_to_a2a import to_a2a

    PORT = int(os.getenv("PORT", "8082"))
    HOST = os.getenv("HOST", "0.0.0.0")
    PUBLIC_HOST = os.getenv("PUBLIC_HOST", "localhost")
    PUBLIC_PORT = int(os.getenv("PUBLIC_PORT", str(PORT)))
    PROTOCOL = os.getenv("PROTOCOL", "http")

    a2a_app = to_a2a(root_agent, host=PUBLIC_HOST, port=PUBLIC_PORT, protocol=PROTOCOL)

    logger.info(f"Starting Brand Strategist on {PROTOCOL}://{HOST}:{PORT}")
    logger.info(f"Agent card: {PROTOCOL}://{HOST}:{PORT}/.well-known/agent.json")

    uvicorn.run(a2a_app, host=HOST, port=PORT)
