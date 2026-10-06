import datetime
import json
import logging
import os

from dotenv import load_dotenv
from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.models import LlmRequest
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext

try:
    from .retry import GENERATE_CONTENT_CONFIG
except ImportError:
    from retry import GENERATE_CONTENT_CONFIG

load_dotenv()

logger = logging.getLogger("ai_creative_studio.project_manager")

DESCRIPTION = (
    "Project manager that turns an approved campaign (captions and visuals) into a "
    "dated timeline, task list with owners, budget breakdown and milestones. "
    "Optionally syncs the project and tasks to Notion."
)


# After this many Notion errors in one session the agent is told to stop using Notion.
MAX_NOTION_ERRORS = 4


def _recovery_hint(status: int, code: str, message: str) -> str:
    """Turn a raw Notion error into an instruction the agent can act on."""
    lower = message.lower()
    if status == 404 and code == "object_not_found":
        # The Notion message blames sharing/permissions, but the usual cause is
        # passing a database ID as page_id in the parent object.
        return (
            "object_not_found: you passed a database ID as page_id. "
            'Use {"parent": {"database_id": "<id>"}} not {"parent": {"page_id": "<id>"}}. '
            "If the parent is already database_id, the database is not shared with the "
            "integration: stop Notion work and report it in Notion Status."
        )
    if status == 400 and ("people" in lower or "person" in lower):
        return "Do not set people/person properties - integration tokens cannot assign users. Retry without them."
    if status == 400 and "relation" in lower:
        return "Relation property rejected. Retry creating this page WITHOUT the relation property."
    if status == 400 and "is not a property that exists" in lower:
        missing = message.split(" is not a property")[0].strip()
        return (
            f"The property '{missing}' does not exist in this database. Retry ONCE with that "
            "property removed entirely. Do not add it back or guess another name for it."
        )
    if status == 400 and "property" in lower:
        return (
            "A property value does not match the database schema. Retry ONCE using only the "
            "properties listed in the schema you already retrieved; drop any you are unsure of."
        )
    if status == 401:
        return "The Notion token is invalid. Stop Notion work and report 'Notion authentication failed' in Notion Status."
    if status == 403:
        return "The integration lacks access to this database. Stop Notion work and report it in Notion Status."
    if status == 429:
        return "Notion rate limit hit. Retry this call once; if it fails again, skip it and continue."
    return message or "Unknown Notion error."


# Callback: runs after EVERY tool call. Returning None keeps the original result;
# returning a dict replaces it with what the model sees.
def handle_notion_error(
    tool: BaseTool,
    args: dict,
    tool_context: ToolContext,
    tool_response: dict,
) -> dict | None:
    """Intercept Notion API errors and replace the raw error with an actionable recovery hint."""
    if not tool.name.startswith("API-") or not isinstance(tool_response, dict):
        return None

    content = (tool_response.get("content") or [{}])[0].get("text", "")
    try:
        data = json.loads(content)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None

    status = data.get("status")
    if status not in (400, 401, 403, 404, 429):
        return None

    code = data.get("code", "")
    hint = _recovery_hint(status, code, data.get("message", ""))

    # Circuit breaker: count Notion errors in this session so a bad schema can never
    # trap the agent in an endless retry loop.
    errors = 1
    if tool_context is not None:
        errors = tool_context.state.get("notion_error_count", 0) + 1
        tool_context.state["notion_error_count"] = errors
    logger.warning(
        "Notion %s (%s) on %s - error %d/%d, injecting recovery hint",
        status, code, tool.name, errors, MAX_NOTION_ERRORS,
    )

    if errors >= MAX_NOTION_ERRORS:
        next_step = (
            f"This is Notion error {errors}. STOP calling Notion tools now. Write the full "
            "text plan and list in Notion Status what was created and what failed."
        )
    else:
        next_step = "Retry with corrected parameters."

    return {
        "content": [{
            "type": "text",
            "text": f"Notion {status} ({code}) on {tool.name}: {hint}\n\n{next_step}",
        }]
    }


def get_system_instruction(project_database_id=None, tasks_database_id=None):
    # notion_section is empty when Notion is not configured, so the agent
    # receives no tool instructions for capabilities it doesn't have.
    notion_section = (
        f"""
## Notion sync (secondary - only after the text plan is complete in your head)
Projects database ID: {project_database_id}
Tasks database ID: {tasks_database_id}

Also persist the project and tasks to these Notion databases using the available Notion tools.
Notion tools follow the pattern `API-<operation>` — use their exact names as listed in the tool
manifest. Use them directly — never wrap in `print()` or prefix with `default_api.`

Before creating anything, use the available tools to discover the schema of each database.
Only use property names and types that actually exist in the schema you discover.

Property rules:
- Always set the database parent using `database_id` — never `page_id`
- Never set "people" or "person" type properties — integration tokens cannot assign users; skip them
- For "relation" type properties linking tasks to the project: set ONLY {{"relation": [{{"id": "<project-page-id>"}}]}}.
  Never set sub-fields like name, state, start, lat on the relation - those are read-only rollups.
  If a task creation fails with a validation_error on a relation property, immediately retry
  creating that task WITHOUT the relation property entirely.
- Only set properties whose type you can identify from the schema response; if a property type
  is unclear after reading the schema, skip it and note it in the Notion Status

If any Notion call fails, continue — the text timeline is always the primary deliverable.
Write your complete response AFTER all Notion operations are done (or have failed).
In Notion Status, report exactly what was created (project name and number of tasks)
and anything that failed.

If image HTTPS links are provided in the input (under "Generated Images" from the Creative
Director), add them to the Notion project page body as a bulleted list under a
"Generated Images" heading after creating the project page.
"""
        if project_database_id
        else ""
    )

    notion_default = (
        "Report what was created in Notion, or what failed."
        if project_database_id
        else 'Write exactly: "Notion not configured - text timeline only"'
    )

    # TODO 1 (done): Project Manager system instruction.
    return f"""You are an experienced Marketing Project Manager for a digital studio.
Your job: turn an APPROVED Instagram campaign into an actionable launch plan.

Today's date is given at the end of these instructions. Day 1 of the plan is today.
Every date you write must be a real calendar date on or after today.

## Use the campaign you were given
The conversation contains the brief, the approved captions and the approved visuals
(with gcs_uri or https image links). Plan around THOSE exact deliverables:
- Create one publishing task per approved caption, named by its caption theme.
- Reference the matching visual concept for each post.
- If no captions or visuals are present, say so in one line and plan generically.
Do NOT rewrite captions, invent new visuals, or re-review quality - that work is done.

## Planning rules
- Four phases in this order: Strategy, Creation, Review, Launch.
- Default length 14 days unless the brief specifies otherwise.
- Owners must be roles, not people: Brand Strategist, Copywriter, Designer, Critic,
  Project Manager, Social Media Manager.
- Space the Instagram posts at least 2 days apart in the Launch phase.
- Status for every task starts as "Not Started", except Strategy, copy, visuals and review,
  which are "Done" because the AI team already completed them.
- Budget: if the brief gives a budget, split it; otherwise use an illustrative total of
  $5,000 and label it "illustrative".

## Required output (ALWAYS produce all five sections, in this order)

**Project Timeline:**
| Phase | Start | End | Key activities |

**Task List:**
| Task | Owner | Deadline | Status |

**Budget Breakdown:**
| Category | Amount | Notes |

**Milestones:**
- [date] - [checkpoint]

**Notion Status:**
- {notion_default}

The text plan above is the PRIMARY deliverable. Never skip it, even if a tool fails.
{notion_section}
"""


def add_current_date(callback_context: CallbackContext, llm_request: LlmRequest):
    """Runs before every model call: appends today's date to the instructions.

    The instruction itself stays plain text because ADK builds the A2A agent
    card from it, and the card builder only accepts a string.
    """
    today = datetime.datetime.now(datetime.UTC).date()
    llm_request.append_instructions([f"Today's date is {today.strftime('%A, %B %d, %Y')}."])


def create_project_manager_agent():
    """Create the Project Manager agent, with Notion MCP if credentials are set."""
    notion_token = os.getenv("NOTION_TOKEN")
    notion_project_db_id = os.getenv("NOTION_PROJECT_DATABASE_ID")
    notion_tasks_db_id = os.getenv("NOTION_TASKS_DATABASE_ID")

    if not notion_token or not notion_project_db_id or not notion_tasks_db_id:
        logger.warning("Notion credentials not set — running without Notion integration")

        # TODO 2 (done): Agent without tools - text plan only.
        return Agent(
            name="project_manager",
            model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
            generate_content_config=GENERATE_CONTENT_CONFIG,
            instruction=get_system_instruction(),
            before_model_callback=add_current_date,
            description=DESCRIPTION,
        )

    logger.info(
        "Notion configured — projects database: %s, tasks database: %s",
        notion_project_db_id, notion_tasks_db_id,
    )

    # TODO 3 (done): Create the MCP toolset for Notion.
    # Imported here so the no-Notion branch never needs the MCP packages.
    from google.adk.tools.mcp_tool import McpToolset, StdioConnectionParams
    from mcp import StdioServerParameters

    # ADK launches the Notion MCP server as a child process and talks to it over
    # stdin/stdout. The token is passed only to that process's environment.
    server_params = StdioServerParameters(
        command="notion-mcp-server",
        env={"NOTION_TOKEN": notion_token, "PATH": os.environ.get("PATH", "")},
    )
    notion_toolset = McpToolset(
        connection_params=StdioConnectionParams(server_params=server_params, timeout=30.0)
    )

    # TODO 3 (done): Agent WITH the Notion toolset and the error-recovery callback.
    return Agent(
        name="project_manager",
        model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        generate_content_config=GENERATE_CONTENT_CONFIG,
        before_model_callback=add_current_date,
        after_tool_callback=handle_notion_error,
        instruction=get_system_instruction(
            project_database_id=notion_project_db_id,
            tasks_database_id=notion_tasks_db_id,
        ),
        description=DESCRIPTION,
        tools=[notion_toolset],
    )


root_agent = create_project_manager_agent()
logger.info("Project Manager agent created")


if __name__ == "__main__":
    import uvicorn
    from google.adk.a2a.utils.agent_to_a2a import to_a2a

    PORT = int(os.getenv("PORT", "8080"))
    HOST = os.getenv("HOST", "0.0.0.0")
    PUBLIC_HOST = os.getenv("PUBLIC_HOST", "localhost")
    PUBLIC_PORT = int(os.getenv("PUBLIC_PORT", str(PORT)))
    PROTOCOL = os.getenv("PROTOCOL", "http")

    a2a_app = to_a2a(root_agent, host=PUBLIC_HOST, port=PUBLIC_PORT, protocol=PROTOCOL)

    logger.info(f"Starting Project Manager on {PROTOCOL}://{HOST}:{PORT}")
    logger.info(f"Agent card: {PROTOCOL}://{HOST}:{PORT}/.well-known/agent.json")

    uvicorn.run(a2a_app, host=HOST, port=PORT)
