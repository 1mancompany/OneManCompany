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


async def _call_boss_online(job_description: str, count: int = 10) -> dict:
    """Call the persistent Boss Online MCP session. Returns role-grouped result."""
    if _boss_session is None:
        raise RuntimeError("Boss Online MCP server is not running")

    result = await _boss_session.call_tool(
        "search_candidates",
        arguments={"job_description": job_description, "count": count},
    )
    # MCP now returns a single dict with role-grouped structure
    for item in result.content:
        try:
            parsed = json.loads(item.text)
        except (json.JSONDecodeError, AttributeError) as _e:
            logger.debug("Skipping unparseable content block: {}", _e)
            continue
        if isinstance(parsed, dict) and "roles" in parsed:
            return parsed
    # Fallback if no role-grouped result found
    logger.warning("Boss Online did not return role-grouped result, returning empty")
    return {"type": "individual", "summary": "", "roles": []}


@tool
async def search_candidates(job_description: str) -> dict:
    """Search the Boss Online recruitment platform for candidates matching a job description.

    Connects to the Boss Online MCP server, which generates role-grouped candidate
    profiles based on the job description. Returns a dict with type, summary, and
    roles (each containing ranked candidates).

    Args:
        job_description: The job requirements / description text.

    Returns:
        A role-grouped dict: {type, summary, roles: [{role, description, candidates}]}.
    """
    try:
        grouped = await _call_boss_online(job_description)
        total = sum(len(r.get("candidates", [])) for r in grouped.get("roles", []))
        logger.info("Boss Online returned %d candidates in %d roles for JD: %s",
                     total, len(grouped.get("roles", [])), job_description[:80])
    except Exception as e:
        logger.error("Boss Online MCP call failed: %s", e)
        # Fallback to local talent packages, wrapped in role-grouped format
        from onemancompany.core.config import list_available_talents, load_talent_profile
        talents = list_available_talents()
        candidates = []
        for t in talents:
            profile = load_talent_profile(t["id"])
            if profile:
                candidates.append(_talent_to_candidate(profile))
        grouped = {
            "type": "individual",
            "summary": "Fallback: local talent packages",
            "roles": [{"role": "Available Talents", "description": job_description, "candidates": candidates}],
        }

    # Stash ALL candidates from ALL roles so shortlist can look up by ID
    _last_search_results.clear()
    for role_group in grouped.get("roles", []):
        for c in role_group.get("candidates", []):
            cid = c.get("id") or c.get("talent_id", "")
            if cid:
                _last_search_results[cid] = c
    return grouped


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


@tool
async def submit_shortlist(jd: str, candidate_ids: list[str], roles: list[dict] | None = None) -> str:
    """Submit a shortlist of candidates to CEO for selection and interview.

    After calling search_candidates(), pick the top 5 candidates and submit
    their IDs here.  This sends the shortlist to the CEO's frontend for
    visual selection — do NOT hire directly.

    Args:
        jd: The job description used for the search.
        candidate_ids: List of candidate IDs (from search results) to include
            in the shortlist.  Maximum 5.
        roles: Optional role-grouped structure from search_candidates(). Each
            entry has {role, description, candidates}. If provided, candidates
            are re-hydrated with full data from _last_search_results.

    Returns:
        Confirmation message with batch_id.
    """
    import uuid as _uuid

    from onemancompany.core.events import CompanyEvent, event_bus

    # Build flat candidate list from IDs (always needed for backward compat)
    all_candidates = []
    for cid in candidate_ids[:5]:
        full = _last_search_results.get(cid)
        if full:
            all_candidates.append(full)
        else:
            logger.warning("submit_shortlist: candidate %s not found in search results", cid)

    if not all_candidates:
        return "ERROR: No valid candidates found. Call search_candidates() first."

    # Build hydrated role groups
    if roles:
        # Re-hydrate each role group with full candidate data
        hydrated_roles = []
        for role_group in roles:
            hydrated_candidates = []
            for c in role_group.get("candidates", []):
                cid = c.get("id") or c.get("talent_id", "")
                full = _last_search_results.get(cid)
                if full:
                    hydrated_candidates.append(full)
            hydrated_roles.append({
                "role": role_group.get("role", ""),
                "description": role_group.get("description", ""),
                "candidates": hydrated_candidates,
            })
    else:
        # Backward compat: wrap flat list in a single role group
        hydrated_roles = [{"role": "Candidates", "description": jd, "candidates": all_candidates}]

    batch_id = str(_uuid.uuid4())[:8]
    pending_candidates[batch_id] = all_candidates

    await event_bus.publish(CompanyEvent(
        type="candidates_ready",
        payload={
            "batch_id": batch_id,
            "jd": jd,
            "roles": hydrated_roles,
            "candidates": all_candidates,  # flat list for backward compat
        },
        agent="HR",
    ))
    logger.info("Shortlist submitted: batch=%s, %d candidates in %d roles",
                batch_id, len(all_candidates), len(hydrated_roles))
    return (
        f"Shortlist submitted (batch_id={batch_id}). "
        f"{len(all_candidates)} candidates sent to CEO for selection. "
        "Wait for CEO to choose — do NOT hire directly."
    )


# ---------------------------------------------------------------------------
# Snapshot provider — hiring pipeline state
# ---------------------------------------------------------------------------

from onemancompany.core.snapshot import snapshot_provider  # noqa: E402


@snapshot_provider("recruitment")
class _RecruitmentSnapshot:
    @staticmethod
    def save() -> dict:
        result = {}
        if pending_candidates:
            result["pending_candidates"] = pending_candidates
        if _pending_project_ctx:
            result["pending_project_ctx"] = _pending_project_ctx
        return result

    @staticmethod
    def restore(data: dict) -> None:
        restored_candidates = data.get("pending_candidates", {})
        if restored_candidates:
            pending_candidates.update(restored_candidates)
        restored_ctx = data.get("pending_project_ctx", {})
        if restored_ctx:
            _pending_project_ctx.update(restored_ctx)
