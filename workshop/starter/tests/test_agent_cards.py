"""Every specialist must be able to build its A2A agent card.

Cloud Run starts each specialist with to_a2a(), which builds the agent card at
startup. If the card cannot be built, the container crashes before listening on
port 8080. This test catches that offline, before a 10-minute deploy fails.
"""

import asyncio
import importlib
import pathlib
import sys

import pytest
from google.adk.a2a.utils.agent_card_builder import AgentCardBuilder

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "agents"))

SPECIALISTS = ["brand_strategist", "copywriter", "designer", "critic", "project_manager"]


@pytest.mark.parametrize("name", SPECIALISTS)
def test_agent_card_builds(name):
    agent = importlib.import_module(f"{name}.agent").root_agent
    card = asyncio.run(AgentCardBuilder(agent=agent, rpc_url="http://localhost:8080/").build())
    assert card.name == name
    assert card.skills
