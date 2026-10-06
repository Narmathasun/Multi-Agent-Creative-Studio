"""Offline structure checks for Milestone 2 (no model calls, no cost)."""

import asyncio
import datetime
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "agents"))

from brand_strategist.agent import SYSTEM_INSTRUCTION, current_date_note
from brand_strategist.agent import root_agent as strategist
from copywriter.agent import root_agent as copywriter


def test_strategist_uses_google_search():
    assert [t.name for t in strategist.tools] == ["google_search"]


def test_strategist_instruction_has_sections_and_limits():
    text = SYSTEM_INSTRUCTION
    for section in [
        "**Audience Insights:**",
        "**Competitive Analysis:**",
        "**Trending Topics:**",
        "**Key Strategic Insights:**",
    ]:
        assert section in text
    assert str(datetime.datetime.now(datetime.UTC).year) in current_date_note()
    assert strategist.before_model_callback is not None
    assert "RESEARCH ONLY" in text
    assert "Do NOT write Instagram captions" in text


def test_copywriter_loads_instagram_skill():
    toolset = copywriter.tools[0]
    tool_names = [t.name for t in asyncio.run(toolset.get_tools())]
    assert "load_skill" in tool_names
    assert "load_skill_resource" in tool_names
    assert "EXACTLY 3" in copywriter.instruction
