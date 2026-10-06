"""Offline tests for the Project Manager: both branches, the instruction, and error recovery."""

import datetime
import importlib
import json
import pathlib
import sys
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agents"))

import project_manager.agent as pm

NOTION_VARS = ["NOTION_TOKEN", "NOTION_PROJECT_DATABASE_ID", "NOTION_TASKS_DATABASE_ID"]


def build(monkeypatch, notion: bool):
    for var in NOTION_VARS:
        if notion:
            monkeypatch.setenv(var, f"test-{var.lower()}")
        else:
            monkeypatch.delenv(var, raising=False)
    return pm.create_project_manager_agent()


def notion_error(status, code, message):
    body = json.dumps({"object": "error", "status": status, "code": code, "message": message})
    return {"content": [{"type": "text", "text": body}], "isError": True}


def call_callback(tool_name, response):
    return pm.handle_notion_error(SimpleNamespace(name=tool_name), {}, None, response)


# --- Branching ---------------------------------------------------------------

def test_no_notion_branch_has_no_tools(monkeypatch):
    agent = build(monkeypatch, notion=False)
    assert agent.tools == []
    text = agent.instruction
    assert isinstance(text, str)  # A2A agent cards need a plain-text instruction
    assert "Notion not configured - text timeline only" in text
    assert "API-" not in text  # no tool guidance for tools it doesn't have


def test_notion_branch_has_mcp_toolset_and_callback(monkeypatch):
    agent = build(monkeypatch, notion=True)
    assert type(agent.tools[0]).__name__ == "McpToolset"
    assert agent.after_tool_callback is pm.handle_notion_error
    text = agent.instruction
    assert "test-notion_project_database_id" in text
    assert '{"relation": [{"id": "<project-page-id>"}]}' in text  # braces rendered correctly


def test_secrets_are_not_hardcoded():
    source = (ROOT / "agents" / "project_manager" / "agent.py").read_text()
    assert "secret_" not in source and "ntn_" not in source
    for var in NOTION_VARS:
        assert f'os.getenv("{var}")' in source


# --- Instruction -------------------------------------------------------------

def test_instruction_has_all_sections_and_today():
    text = pm.get_system_instruction()
    for section in ["**Project Timeline:**", "**Task List:**", "**Budget Breakdown:**",
                    "**Milestones:**", "**Notion Status:**"]:
        assert section in text
    assert "Strategy, Creation, Review, Launch" in text
    captured = []
    pm.add_current_date(None, SimpleNamespace(append_instructions=captured.extend))
    today = datetime.datetime.now(datetime.UTC).date()
    assert today.strftime("%B %d, %Y") in captured[0]


# --- Error recovery callback -------------------------------------------------

def test_404_database_as_page_gets_hint():
    out = call_callback("API-post-page", notion_error(404, "object_not_found", "Could not find page"))
    assert "database_id" in out["content"][0]["text"]


def test_400_people_property_gets_hint():
    msg = "Assignee is expected to be people."
    out = call_callback("API-post-page", notion_error(400, "validation_error", msg))
    assert "people" in out["content"][0]["text"].lower()
    assert "Retry" in out["content"][0]["text"]


def test_401_tells_agent_to_stop_notion():
    out = call_callback("API-post-page", notion_error(401, "unauthorized", "API token is invalid."))
    assert "Notion Status" in out["content"][0]["text"]


def test_success_and_non_notion_tools_untouched():
    ok = {"content": [{"type": "text", "text": json.dumps({"object": "page", "id": "abc"})}]}
    assert call_callback("API-post-page", ok) is None
    assert call_callback("google_search", notion_error(404, "object_not_found", "x")) is None


def test_non_json_or_odd_responses_do_not_crash():
    assert call_callback("API-post-page", {"content": [{"type": "text", "text": "plain text"}]}) is None
    assert call_callback("API-post-page", {"content": []}) is None
    assert call_callback("API-post-page", "not a dict") is None


def teardown_module():
    importlib.reload(pm)  # restore module state for any later imports


def test_missing_property_hint_names_it_and_limits_retries():
    msg = "Project is not a property that exists."
    out = call_callback("API-post-page", notion_error(400, "validation_error", msg))
    text = out["content"][0]["text"]
    assert "'Project'" in text and "ONCE" in text


def test_circuit_breaker_stops_notion_after_repeated_errors():
    ctx = SimpleNamespace(state={})
    err = notion_error(400, "validation_error", "Project is not a property that exists.")
    texts = [
        pm.handle_notion_error(SimpleNamespace(name="API-post-page"), {}, ctx, err)["content"][0]["text"]
        for _ in range(pm.MAX_NOTION_ERRORS)
    ]
    assert "Retry with corrected parameters." in texts[0]
    assert "STOP calling Notion tools" in texts[-1]
    assert ctx.state["notion_error_count"] == pm.MAX_NOTION_ERRORS
