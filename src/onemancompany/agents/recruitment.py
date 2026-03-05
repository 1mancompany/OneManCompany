"""Recruitment — candidate search and shortlist management.

Extracted from hr_agent.py. Contains:
- Talent-to-candidate conversion
- Boss Online MCP client management
- search_candidates / list_open_positions LangChain tools
- Pending candidate state for CEO selection
"""

from __future__ import annotations

import asyncio
import json
import random
import sys

from langchain_core.tools import tool
from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters

from loguru import logger

# ===== In-memory state for pending candidates =====

# batch_id -> [candidate, ...]
pending_candidates: dict[str, list[dict]] = {}

# batch_id -> {project_id, project_dir}
_pending_project_ctx: dict[str, dict] = {}

# candidate_id -> full candidate dict (stashed from last search)
_last_search_results: dict[str, dict] = {}


def _talent_to_candidate(talent: dict) -> dict:
    """Convert a talent profile.yaml dict into a CandidateProfile-compatible dict."""
    from onemancompany.core.config import load_talent_skills, load_talent_tools

    talent_id = talent.get("id", "unknown")
    skill_names = talent.get("skills", [])
    tool_names = load_talent_tools(talent_id)
    skill_contents = load_talent_skills(talent_id)

    # Build skill_set with content from markdown files
    skill_set = []
    for i, name in enumerate(skill_names):
        content = skill_contents[i] if i < len(skill_contents) else ""
        skill_set.append({
            "name": name,
            "description": content[:200] if content else f"{name} skill",
            "code": "",
        })

    # Build tool_set from manifest
    tool_set = [{"name": t, "description": f"{t} tool", "code": ""} for t in tool_names]

    sprites = ["employee_blue", "employee_red", "employee_green", "employee_purple", "employee_orange"]

    # Compute cost per 1M tokens
    llm_model = talent.get("llm_model", "")
    api_provider = talent.get("api_provider", "openrouter")
    cost_per_1m = 0.0
    if llm_model and api_provider == "openrouter":
        from onemancompany.core.model_costs import compute_salary
        cost_per_1m = compute_salary(llm_model)

    return {
        "id": talent_id,
        "name": talent.get("name", talent_id),
        "role": talent.get("role", "Engineer"),
        "experience_years": 3,
        "personality_tags": talent.get("personality_tags", []),
        "system_prompt": talent.get("system_prompt_template", ""),
        "skill_set": skill_set,
        "tool_set": tool_set,
        "sprite": random.choice(sprites),
        "llm_model": llm_model,
        "temperature": talent.get("temperature", 0.7),
        "image_model": talent.get("image_model", ""),
        "jd_relevance": 1.0,
        "remote": talent.get("remote", False),
        "talent_id": talent_id,
        "api_provider": api_provider,
        "hosting": talent.get("hosting", "company"),
        "auth_method": talent.get("auth_method", "api_key"),
        "cost_per_1m_tokens": round(cost_per_1m, 2),
        "hiring_fee": float(talent.get("hiring_fee", 0.0)),
    }


# ---------------------------------------------------------------------------
# Persistent Boss Online MCP client
# ---------------------------------------------------------------------------

_boss_session: ClientSession | None = None
_boss_cleanup: asyncio.Task | None = None


async def start_boss_online() -> None:
    """Start the Boss Online MCP server as a persistent subprocess.

    Called once during app lifespan startup.  The session is stored in
    module-level ``_boss_session`` so ``search_candidates`` can reuse it.
    """
    global _boss_session, _boss_cleanup

    from pathlib import Path

    boss_online_path = str(
        Path(__file__).resolve().parent.parent / "talent_market" / "boss_online.py"
    )
    server_params = StdioServerParameters(
        command=sys.executable,
        args=[boss_online_path],
    )

    # stdio_client is an async context manager that starts the subprocess.
    # We enter it manually and store the exit stack so we can clean up later.
    from contextlib import AsyncExitStack
    stack = AsyncExitStack()
    read, write = await stack.enter_async_context(stdio_client(server_params))
    session = await stack.enter_async_context(ClientSession(read, write))
    await session.initialize()

    _boss_session = session
    # Store the stack so stop_boss_online can tear it down
    _boss_session._exit_stack = stack  # type: ignore[attr-defined]
    logger.info("Boss Online MCP server started (persistent)")


async def stop_boss_online() -> None:
    """Shut down the persistent Boss Online MCP server."""
    global _boss_session
    if _boss_session is not None:
        stack = getattr(_boss_session, "_exit_stack", None)
        _boss_session = None
        if stack:
            await stack.aclose()
        logger.info("Boss Online MCP server stopped")


async def _call_boss_online(job_description: str, count: int = 10) -> list[dict]:
    """Call the persistent Boss Online MCP session."""
    if _boss_session is None:
        raise RuntimeError("Boss Online MCP server is not running")

    result = await _boss_session.call_tool(
        "search_candidates",
        arguments={"job_description": job_description, "count": count},
    )
    candidates = []
    for item in result.content:
        try:
            candidates.append(json.loads(item.text))
        except (json.JSONDecodeError, AttributeError) as _e:
            logger.debug("Skipping unparseable candidate block: {}", _e)
            continue
    return candidates


@tool
async def search_candidates(job_description: str) -> list[dict]:
    """Search the Boss Online recruitment platform for candidates matching a job description.

    Connects to the Boss Online MCP server, which generates candidate profiles
    based on the job description. Returns ranked candidates with full profiles
    including skills, tools, system prompts, and JD relevance scores.

    Args:
        job_description: The job requirements / description text.

    Returns:
        A list of candidate dicts sorted by JD relevance (highest first).
    """
    try:
        candidates = await _call_boss_online(job_description)
        logger.info("Boss Online returned %d candidates for JD: %s", len(candidates), job_description[:80])
    except Exception as e:
        logger.error("Boss Online MCP call failed: %s", e)
        # Fallback to local talent packages
        from onemancompany.core.config import list_available_talents, load_talent_profile
        talents = list_available_talents()
        candidates = []
        for t in talents:
            profile = load_talent_profile(t["id"])
            if profile:
                candidates.append(_talent_to_candidate(profile))
    # Stash full results so shortlist can look up by ID (LLM may drop fields)
    _last_search_results.clear()
    for c in candidates:
        cid = c.get("id") or c.get("talent_id", "")
        if cid:
            _last_search_results[cid] = c
    return candidates


@tool
def list_open_positions() -> list[dict]:
    """Return a list of open positions the company might want to fill.

    Returns:
        A list of dicts, each with role and priority fields.
    """
    positions = [
        {"role": "Engineer", "priority": "high", "reason": "Need more development capacity"},
        {"role": "Designer", "priority": "medium", "reason": "UI/UX improvements needed"},
        {"role": "Analyst", "priority": "medium", "reason": "Data-driven decisions"},
        {"role": "DevOps", "priority": "low", "reason": "Infrastructure automation"},
        {"role": "QA", "priority": "high", "reason": "Quality assurance gaps"},
        {"role": "Marketing", "priority": "low", "reason": "Growth and outreach"},
    ]
    return random.sample(positions, k=random.randint(2, 4))
