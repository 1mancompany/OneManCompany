"""Recruitment — candidate search and shortlist management.

Extracted from hr_agent.py. Contains:
- Talent-to-candidate conversion
- TalentMarketClient (MCP SSE connection to cloud talent market)
- search_candidates / list_open_positions LangChain tools
- Pending candidate state for CEO selection
"""

from __future__ import annotations

import asyncio
import json
import random
from contextlib import AsyncExitStack
from langchain_core.tools import tool
from mcp import ClientSession

from pydantic import BaseModel, Field
from typing import Literal
from loguru import logger

from onemancompany.core import store as _store
from onemancompany.core.models import EventType, HostingMode

# --- Pydantic models (migrated from talent_market/boss_online.py) ---

RoleType = Literal["Engineer", "Designer", "Analyst", "DevOps", "QA", "Marketing"]

SpriteType = Literal[
    "employee_blue", "employee_red", "employee_green",
    "employee_purple", "employee_orange",
]


class CandidateSkill(BaseModel):
    """A skill the candidate possesses."""
    name: str = Field(description="Skill identifier")
    description: str = Field(description="Human-readable skill description")
    code: str = Field(default="", description="Example code snippet")


class CandidateTool(BaseModel):
    """A tool the candidate can operate."""
    name: str = Field(description="Tool identifier")
    description: str = Field(description="What the tool does")
    code: str = Field(default="", description="Example code snippet")


class CandidateProfile(BaseModel):
    """Full candidate profile returned by talent market search."""
    id: str = Field(description="Talent package ID")
    name: str = Field(description="Talent name")
    role: RoleType = Field(description="Primary role")
    experience_years: int = Field(ge=0, le=30, description="Years of experience")
    personality_tags: list[str] = Field(description="Personality traits")
    system_prompt: str = Field(description="LLM persona prompt")
    skill_set: list[CandidateSkill] = Field(description="Skills")
    tool_set: list[CandidateTool] = Field(description="Tools")
    sprite: SpriteType = Field(description="Pixel art avatar type")
    llm_model: str = Field(description="LLM model")
    jd_relevance: float = Field(ge=0.0, le=1.0, description="JD match score")
    remote: bool = Field(default=False)
    talent_id: str = Field(default="")
    cost_per_1m_tokens: float = Field(default=0.0)
    hiring_fee: float = Field(default=0.0)
    api_provider: str = Field(default="openrouter")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    hosting: str = Field(default="company")
    auth_method: str = Field(default="api_key")


class HireRequest(BaseModel):
    """Request to hire a candidate from a shortlist batch."""
    batch_id: str = Field(description="Batch ID from the shortlist")
    candidate_id: str = Field(description="ID of the selected candidate")
    nickname: str = Field(default="", description="Optional nickname")


class InterviewRequest(BaseModel):
    """Request to interview a candidate."""
    question: str = Field(description="The interview question text")
    candidate: CandidateProfile = Field(description="Full candidate profile")
    images: list[str] = Field(default_factory=list, description="Optional base64 images")


class InterviewResponse(BaseModel):
    """Response from a candidate interview."""
    candidate_id: str = Field(description="ID of the interviewed candidate")
    question: str = Field(description="The original question")
    answer: str = Field(description="Candidate's answer")

# ===== Pending candidate state (disk-backed) =====

# In-memory dict kept in sync with disk for fast access.
# Writes go through _persist_candidates() which saves to store.
pending_candidates: dict[str, list[dict]] = {}

# batch_id -> {project_id, project_dir}
_pending_project_ctx: dict[str, dict] = {}


def _persist_candidates() -> None:
    """Persist pending_candidates to disk (sync, fire-and-forget async)."""
    import asyncio

    data = {
        "batches": pending_candidates,
        "project_ctx": _pending_project_ctx,
        "search_results": _last_search_results,
        "session_id": _last_session_id,
    }
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_store.save_candidates("pending", data))
    except RuntimeError:
        # No event loop — write synchronously
        from onemancompany.core.config import DirtyCategory
        from onemancompany.core.store import _write_yaml, COMPANY_DIR
        from onemancompany.core.store import mark_dirty
        path = COMPANY_DIR / "candidates" / "pending.yaml"
        _write_yaml(path, data)
        mark_dirty(DirtyCategory.CANDIDATES)


def _load_candidates_from_disk() -> None:
    """Restore pending_candidates + search cache from disk on startup."""
    global _last_session_id
    data = _store.load_candidates("pending")
    if data:
        restored = data.get("batches", {})
        if restored:
            pending_candidates.update(restored)
        ctx = data.get("project_ctx", {})
        if ctx:
            _pending_project_ctx.update(ctx)
        search = data.get("search_results", {})
        if search:
            _last_search_results.update(search)
        sid = data.get("session_id", "")
        if sid:
            _last_session_id = sid

# candidate_id -> full candidate dict (stashed from last search)
_last_search_results: dict[str, dict] = {}

# session_id from the most recent Talent Market search (used by hire_talents)
_last_session_id: str = ""


def _extract_candidate_id(candidate: dict) -> str:
    """Extract candidate ID from various response formats.

    Handles both local format (flat dict with "id") and
    Talent Market API format (nested talent.profile.id).
    """
    # Flat format (local fallback / already normalized)
    cid = candidate.get("id") or candidate.get("talent_id", "")
    if cid:
        return cid
    # Talent Market nested format: {talent: {profile: {id: ...}, id: ...}}
    talent = candidate.get("talent", {})
    if isinstance(talent, dict):
        cid = talent.get("id", "")
        if not cid:
            profile = talent.get("profile", {})
            if isinstance(profile, dict):
                cid = profile.get("id", "")
    return cid


def _normalize_market_candidate(candidate: dict) -> dict:
    """Normalize a Talent Market API candidate into CandidateProfile-compatible dict.

    Talent Market returns: {talent: {profile: {...}, skills_detail, ...}, score, reasoning}
    We flatten this into the same shape as local candidates.
    """
    talent = candidate.get("talent", {})
    if not isinstance(talent, dict):
        return candidate  # already flat or unknown format

    profile = talent.get("profile", {})
    if not isinstance(profile, dict):
        return candidate

    talent_id = profile.get("id", "")
    sprites = ["employee_blue", "employee_red", "employee_green", "employee_purple", "employee_orange"]

    # Build skill_set from skills_detail
    skill_set = []
    for sd in talent.get("skills_detail", []):
        skill_set.append({
            "name": sd.get("name", ""),
            "description": sd.get("description", sd.get("content_preview", "")[:200]),
            "code": "",
        })

    # Build tool_set from tools_detail
    tool_set = []
    for td in talent.get("tools_detail", []):
        tool_set.append({
            "name": td.get("name", ""),
            "description": td.get("description", ""),
            "code": "",
        })

    llm_model = profile.get("llm_model", "")
    api_provider = profile.get("api_provider", "openrouter")
    cost_per_1m = 0.0
    if llm_model and api_provider == "openrouter":
        try:
            from onemancompany.core.model_costs import compute_salary
            cost_per_1m = compute_salary(llm_model)
        except Exception as exc:
            logger.debug("Could not compute salary for {}: {}", llm_model, exc)

    return {
        "id": talent_id,
        "name": profile.get("name", talent_id),
        "role": profile.get("role", "Engineer"),
        "experience_years": 3,
        "personality_tags": profile.get("personality_tags", []),
        "system_prompt": profile.get("system_prompt_template", ""),
        "skill_set": skill_set,
        "tool_set": tool_set,
        "sprite": random.choice(sprites),
        "llm_model": llm_model,
        "temperature": profile.get("temperature", 0.7),
        "image_model": profile.get("image_model", ""),
        "jd_relevance": candidate.get("score", 1.0),
        "remote": profile.get("remote", False),
        "talent_id": talent_id,
        "api_provider": api_provider,
        "hosting": profile.get("hosting", HostingMode.COMPANY),
        "auth_method": profile.get("auth_method", "api_key"),
        "cost_per_1m_tokens": round(cost_per_1m, 2),
        "hiring_fee": float(profile.get("hiring_fee", 0.0)),
        # Preserve raw data for hire flow
        "description_md": talent.get("description_md", ""),
        "source_repo": talent.get("source_repo", ""),
        "dir_name": talent.get("dir_name", ""),
    }


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
        "hosting": talent.get("hosting", HostingMode.COMPANY),
        "auth_method": talent.get("auth_method", "api_key"),
        "cost_per_1m_tokens": round(cost_per_1m, 2),
        "hiring_fee": float(talent.get("hiring_fee", 0.0)),
    }


# ---------------------------------------------------------------------------
# Talent Market MCP client
# ---------------------------------------------------------------------------


class TalentMarketClient:
    """SSE-based MCP client for the cloud Talent Market service."""

    def __init__(self) -> None:
        self._session: ClientSession | None = None
        self._stack: AsyncExitStack | None = None
        self._api_key: str = ""

    async def connect(self, url: str, api_key: str) -> None:
        """Establish an SSE connection to the Talent Market MCP server."""
        if self._session is not None:
            return
        from mcp.client.sse import sse_client

        stack = AsyncExitStack()
        headers = {"Authorization": f"Bearer {api_key}"}
        read, write = await stack.enter_async_context(sse_client(url=url, headers=headers))
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        self._session = session
        self._stack = stack
        self._api_key = api_key
        logger.info("Connected to Talent Market at {}", url)

    async def disconnect(self) -> None:
        """Tear down the MCP connection."""
        if self._session is None:
            return
        stack = self._stack
        self._session = None
        self._stack = None
        self._api_key = ""
        if stack:
            await stack.aclose()
        logger.info("Talent Market disconnected")

    @property
    def connected(self) -> bool:
        """Return True if an active session exists."""
        return self._session is not None

    async def _call(self, tool_name: str, **kwargs) -> dict:
        """Invoke an MCP tool, auto-injecting the API key."""
        if not self._session:
            raise RuntimeError("Not connected to Talent Market")
        kwargs["api_key"] = self._api_key
        logger.debug("[TalentMarket] calling tool={} args={}", tool_name,
                     {k: v[:30] + "..." if isinstance(v, str) and len(v) > 30 else v for k, v in kwargs.items() if k != "api_key"})
        result = await self._session.call_tool(tool_name, arguments=kwargs)
        logger.debug("[TalentMarket] result content blocks: {}", len(result.content))
        for item in result.content:
            try:
                parsed = json.loads(item.text)
            except (json.JSONDecodeError, AttributeError):
                logger.debug("Skipping unparseable MCP content block: {}", getattr(item, 'text', '')[:200])
                continue
            if isinstance(parsed, dict):
                return parsed
        logger.warning("[TalentMarket] No dict found in response, raw content: {}",
                      [getattr(item, 'text', '')[:200] for item in result.content])
        return {}

    async def search(self, job_description: str) -> dict:
        """Search for candidates matching a job description."""
        return await self._call("search_candidates", job_description=job_description)

    async def list_available(self, role: str = "", skills: str = "", page: int = 1, page_size: int = 20) -> dict:
        """List available talents with optional filters."""
        return await self._call("list_available_talents", role=role, skills=skills, page=page, page_size=page_size)

    async def list_my_talents(self) -> dict:
        """List talents owned by the current account."""
        return await self._call("list_my_talents")

    async def get_info(self, talent_id: str) -> dict:
        """Get detailed info for a talent."""
        return await self._call("get_talent_info", talent_id=talent_id)

    async def get_cv(self, talent_id: str) -> dict:
        """Get the CV/resume for a talent."""
        return await self._call("get_talent_cv", talent_id=talent_id)

    async def hire(self, talent_ids: list[str], session_id: str = "") -> dict:
        """Hire one or more talents."""
        args: dict = {"talent_ids": talent_ids}
        if session_id:
            args["session_id"] = session_id
        return await self._call("hire_talents", **args)

    async def onboard(self, talent_id: str) -> dict:
        """Onboard a hired talent."""
        return await self._call("onboard_talent", talent_id=talent_id)


talent_market = TalentMarketClient()


async def start_talent_market() -> None:
    """Connect to the Talent Market MCP server using config.yaml settings."""
    from onemancompany.core.config import load_app_config

    tm_config = load_app_config().get("talent_market", {})
    logger.debug("[recruitment] talent_market config: {}", {k: v[:8] + "..." if k == "api_key" and v else v for k, v in tm_config.items()})
    url = tm_config.get("url", "https://api.one-man-company.com/mcp/sse")
    api_key = tm_config.get("api_key", "")
    if not api_key:
        logger.warning("Talent Market API key not configured — skipping connection")
        return
    logger.info("[recruitment] Connecting to Talent Market at {} ...", url)
    await talent_market.connect(url, api_key)


async def stop_talent_market() -> None:
    """Disconnect from the Talent Market MCP server."""
    await talent_market.disconnect()


def _local_fallback_search(job_description: str) -> dict:
    """Search local talent packages as a fallback when Talent Market is unavailable."""
    from onemancompany.core.config import list_available_talents, load_talent_profile

    talents = list_available_talents()
    candidates = []
    for t in talents:
        profile = load_talent_profile(t["id"])
        if profile:
            candidates.append(_talent_to_candidate(profile))
    return {
        "type": "individual",
        "summary": "Local talent packages",
        "roles": [{"role": "Available Talents", "description": job_description, "candidates": candidates}],
    }


@tool
async def search_candidates(job_description: str) -> dict:
    """Search for candidates matching a job description.
    Uses Talent Market API when connected, falls back to local talent packages.

    Args:
        job_description: The job requirements / description text.

    Returns:
        A role-grouped dict: {type, summary, roles: [{role, description, candidates}]}.
    """
    global _last_session_id

    logger.debug("[recruitment] search_candidates called, talent_market.connected={}", talent_market.connected)
    from_market = False
    if talent_market.connected:
        try:
            logger.debug("[recruitment] Calling Talent Market API for JD: {}", job_description[:80])
            grouped = await talent_market.search(job_description)
            total = sum(len(r.get("candidates", [])) for r in grouped.get("roles", []))
            logger.info("Talent Market returned {} candidates in {} roles for JD: {}",
                        total, len(grouped.get("roles", [])), job_description[:80])
            # Enumerate all returned talents for debugging
            for role_group in grouped.get("roles", []):
                for idx, candidate in enumerate(role_group.get("candidates", [])):
                    talent_data = candidate.get("talent", {}) if isinstance(candidate.get("talent"), dict) else {}
                    profile = talent_data.get("profile", {}) if isinstance(talent_data, dict) else {}
                    talent_id = profile.get("id", "") or candidate.get("id", "") or candidate.get("talent_id", "")
                    talent_name = profile.get("name", "") or candidate.get("name", "")
                    talent_role = role_group.get("role", "")
                    logger.debug("[recruitment] Talent Market candidate #{}: id={}, name={}, role={}",
                                 idx + 1, talent_id, talent_name, talent_role)
            from_market = True
        except Exception as e:
            logger.opt(exception=e).error("Talent Market search failed, falling back to local: {!r}", e)
            grouped = _local_fallback_search(job_description)
    else:
        logger.info("[recruitment] Talent Market not connected, using local talent pool")
        grouped = _local_fallback_search(job_description)

    _last_session_id = grouped.get("session_id", "")

    # Normalize Talent Market candidates into flat CandidateProfile dicts
    # and stash ALL candidates from ALL roles for shortlist lookup by ID
    _last_search_results.clear()
    for role_group in grouped.get("roles", []):
        normalized = []
        for c in role_group.get("candidates", []):
            # Check if this is a nested Talent Market response
            if "talent" in c and isinstance(c.get("talent"), dict):
                c = _normalize_market_candidate(c)
            cid = _extract_candidate_id(c)
            if cid:
                _last_search_results[cid] = c
            normalized.append(c)
        role_group["candidates"] = normalized
    _persist_candidates()  # persist search cache to survive restarts

    # Talent Market results are pre-screened — auto-submit all as shortlist,
    # grouped by role, skipping HR's LLM filtering step.
    if from_market and _last_search_results:
        all_ids = list(_last_search_results.keys())
        logger.info("[recruitment] Auto-submitting {} Talent Market candidates as shortlist", len(all_ids))
        result = await _auto_submit_shortlist(job_description, all_ids, grouped.get("roles", []))
        return {
            "type": grouped.get("type", "market"),
            "summary": result,
            "roles": grouped.get("roles", []),
            "auto_shortlisted": True,
        }

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


async def _create_and_publish_batch(jd: str, candidates: list[dict], roles: list[dict]) -> str:
    """Create a pending batch and publish candidates_ready event.

    Shared by both auto-submit (Talent Market) and manual submit (local) paths.
    Returns confirmation message or error string.
    """
    import uuid as _uuid
    from onemancompany.core.events import CompanyEvent, event_bus

    if pending_candidates:
        existing_ids = list(pending_candidates.keys())
        return f"A shortlist is already pending (batch_id={existing_ids[0]})."

    if not candidates:
        return "ERROR: No valid candidates found."

    batch_id = str(_uuid.uuid4())[:8]
    pending_candidates[batch_id] = candidates
    _pending_project_ctx[batch_id] = {"session_id": _last_session_id}
    _persist_candidates()

    await event_bus.publish(CompanyEvent(
        type=EventType.CANDIDATES_READY,
        payload={
            "batch_id": batch_id,
            "jd": jd,
            "roles": roles,
            "candidates": candidates,
        },
        agent="HR",
    ))
    logger.info("Shortlist submitted: batch={}, {} candidates in {} roles",
                batch_id, len(candidates), len(roles))
    return f"Shortlist submitted (batch_id={batch_id}). {len(candidates)} candidates sent to CEO."


async def _auto_submit_shortlist(jd: str, candidate_ids: list[str], roles: list[dict]) -> str:
    """Auto-submit all Talent Market candidates as shortlist (no cap)."""
    all_candidates = [_last_search_results[cid] for cid in candidate_ids if cid in _last_search_results]
    return await _create_and_publish_batch(jd, all_candidates, roles)


@tool
async def submit_shortlist(jd: str, candidate_ids: list[str], roles: list[dict] | None = None) -> str:
    """Submit a shortlist of candidates to CEO for selection and interview.

    After calling search_candidates(), pick the top 12 candidates and submit
    their IDs here.  This sends the shortlist to the CEO's frontend for
    visual selection — do NOT hire directly.

    Args:
        jd: The job description used for the search.
        candidate_ids: List of candidate IDs (from search results) to include
            in the shortlist.  Maximum 12.
        roles: Optional role-grouped structure from search_candidates(). Each
            entry has {role, description, candidates}. If provided, candidates
            are re-hydrated with full data from _last_search_results.

    Returns:
        Confirmation message with batch_id.
    """
    logger.debug("[shortlist] submit_shortlist called: {} candidate_ids, pending_candidates keys={}",
                 len(candidate_ids), list(pending_candidates.keys()))

    # Build flat candidate list from IDs
    all_candidates = []
    for cid in candidate_ids[:12]:
        full = _last_search_results.get(cid)
        if full:
            all_candidates.append(full)
        else:
            logger.warning("submit_shortlist: candidate {} not found in search results", cid)

    if not all_candidates:
        return "ERROR: No valid candidates found. Call search_candidates() first."

    # Build hydrated role groups
    if roles:
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
        hydrated_roles = [{"role": "Candidates", "description": jd, "candidates": all_candidates}]

    return await _create_and_publish_batch(jd, all_candidates, hydrated_roles)


# ---------------------------------------------------------------------------
# Restore candidates from disk on module load
# ---------------------------------------------------------------------------

_load_candidates_from_disk()
