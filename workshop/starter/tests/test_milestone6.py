"""Offline tests for the Creative Director: A2A wiring, compaction, and the quality gate."""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agents"))

import creative_director.agent as cd
from creative_director.prompt import SYSTEM_INSTRUCTION_TEMPLATE

URLS = {
    "STRATEGIST_AGENT_URL": "https://strategist.example.run.app",
    "COPYWRITER_AGENT_URL": "https://copywriter.example.run.app",
    "DESIGNER_AGENT_URL": "https://designer.example.run.app",
    "CRITIC_AGENT_URL": "https://critic.example.run.app",
    "PM_AGENT_URL": "https://pm.example.run.app/",
}
SPECIALISTS = ["brand_strategist", "copywriter", "designer", "critic", "project_manager"]


def build(monkeypatch, urls):
    for key in URLS:
        monkeypatch.delenv(key, raising=False)
    for key, value in urls.items():
        monkeypatch.setenv(key, value)
    return cd.create_creative_director()


def remote_tools(agent):
    return {t.name: t for t in agent.tools if type(t).__name__ == "AgentTool"}


def test_all_five_specialists_are_remote_a2a_tools(monkeypatch):
    agent, _ = build(monkeypatch, URLS)
    tools = remote_tools(agent)
    assert sorted(tools) == sorted(SPECIALISTS)
    for tool in tools.values():
        assert type(tool.agent).__name__ == "RemoteA2aAgent"


def test_agent_cards_point_at_well_known_path(monkeypatch):
    agent, _ = build(monkeypatch, URLS)
    card = vars(remote_tools(agent)["project_manager"].agent)["_agent_card_source"]
    assert card == "https://pm.example.run.app/.well-known/agent.json"  # no double slash


def test_missing_urls_are_skipped(monkeypatch):
    agent, _ = build(monkeypatch, {"CRITIC_AGENT_URL": URLS["CRITIC_AGENT_URL"]})
    assert list(remote_tools(agent)) == ["critic"]


def test_app_has_events_compaction(monkeypatch):
    _, app = build(monkeypatch, URLS)
    config = app.events_compaction_config
    assert config is not None and config.compaction_interval == 3
    assert config.summarizer is not None


def test_no_specialist_code_is_imported():
    source = (ROOT / "agents" / "creative_director" / "agent.py").read_text()
    for module in SPECIALISTS + ["image_gen_tool", "image_review_tool"]:
        assert f"import {module}" not in source and f"from {module}" not in source


def test_prompt_enforces_quality_gate_and_rereview():
    text = SYSTEM_INSTRUCTION_TEMPLATE.format(available_agents="x")
    assert "All Approved: YES" in text and "QUALITY GATE" in text
    assert "Send the Revised Work Back to the Critic" in text
    assert "Maximum **2 revision rounds**" in text
    assert "do NOT call the project manager" in text
