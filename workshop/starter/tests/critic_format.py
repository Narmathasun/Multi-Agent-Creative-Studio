"""Parse and validate the Critic's verdict exactly the way the orchestrator must.

Use it in tests, or check a real Critic answer from the terminal:
    uv run python tests/critic_format.py < critic_output.txt
"""

import re
import sys

SECTIONS = ["POSTS REVIEW", "VISUALS REVIEW", "OVERALL ASSESSMENT"]
STATUSES = {"APPROVED", "NEEDS_REVISION", "NOT_REVIEWED"}


def _field(block: str, name: str) -> str | None:
    match = re.search(rf"^\s*-\s*{re.escape(name)}:\s*(.+?)\s*$", block, re.MULTILINE)
    return match.group(1).strip().strip("[]") if match else None


def _score(value: str | None) -> int | None:
    match = re.match(r"(\d+)\s*/\s*10", value or "")
    return int(match.group(1)) if match else None


def parse_verdict(text: str) -> dict:
    """Return the parsed verdict, or raise ValueError describing what is wrong."""
    positions = []
    for name in SECTIONS:
        match = re.search(rf"\*\*{name}:\*\*", text)
        if not match:
            raise ValueError(f"Missing section header **{name}:**")
        positions.append(match.start())
    if positions != sorted(positions):
        raise ValueError("Sections are out of order")

    blocks = dict(zip(SECTIONS, [text[a:b] for a, b in zip(positions, positions[1:] + [len(text)])]))
    posts, visuals, overall = (blocks[s] for s in SECTIONS)

    result = {
        "posts_score": _score(_field(posts, "Score")),
        "posts_status": _field(posts, "Status"),
        "visuals_score": _score(_field(visuals, "Score")),
        "visuals_status": _field(visuals, "Status"),
        "all_approved": _field(overall, "All Approved"),
        "priority": _field(overall, "Priority Revisions"),
        "overall_score": _score(_field(overall, "Overall Score")),
    }

    if result["posts_score"] is None:
        raise ValueError("POSTS Score must look like 'N/10'")
    if result["posts_status"] not in {"APPROVED", "NEEDS_REVISION"}:
        raise ValueError(f"Bad POSTS Status: {result['posts_status']!r}")
    if result["visuals_status"] not in STATUSES:
        raise ValueError(f"Bad VISUALS Status: {result['visuals_status']!r}")
    if result["visuals_status"] == "NOT_REVIEWED":
        if _field(visuals, "Score") != "N/A":
            raise ValueError("NOT_REVIEWED visuals must have Score: N/A")
    elif result["visuals_score"] is None:
        raise ValueError("VISUALS Score must look like 'N/10'")
    if result["all_approved"] not in {"YES", "NO"}:
        raise ValueError(f"All Approved must be YES or NO, got {result['all_approved']!r}")
    if result["overall_score"] is None:
        raise ValueError("Overall Score must look like 'N/10'")

    # Consistency rules from the Critic's instruction.
    approved = result["posts_status"] == "APPROVED" and result["visuals_status"] in {
        "APPROVED",
        "NOT_REVIEWED",
    }
    if (result["all_approved"] == "YES") != approved:
        raise ValueError("All Approved does not match the POSTS/VISUALS statuses")
    expected_overall = min(
        s for s in (result["posts_score"], result["visuals_score"]) if s is not None
    )
    if result["overall_score"] != expected_overall:
        raise ValueError(
            f"Overall Score {result['overall_score']} should be the lowest score ({expected_overall})"
        )
    if result["all_approved"] == "NO" and (not result["priority"] or result["priority"] == "None"):
        raise ValueError("NEEDS_REVISION verdict must name a Priority Revision")

    result["needs_revision"] = result["all_approved"] == "NO"
    return result


if __name__ == "__main__":
    try:
        verdict = parse_verdict(sys.stdin.read())
    except ValueError as err:
        print(f"❌ INVALID FORMAT: {err}")
        sys.exit(1)
    print("✅ Valid Critic verdict")
    for key, value in verdict.items():
        print(f"  {key}: {value}")
