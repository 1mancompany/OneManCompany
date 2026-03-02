"""Boss Online — Recruitment Platform MCP Server

Input: Job Description (JD)
Output: Top-K matching candidates from talents/ directory, ranked by JD relevance.
Runs as a subprocess via stdio transport.

All request/response data formats are defined as Pydantic models below,
serving as the formal interface protocol between our system and Boss Online.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("BossOnline")

# Discover talents directory relative to this file
TALENTS_DIR = Path(__file__).resolve().parent / "talents"


# ============================================================================
# Interface Protocol — Pydantic models for Boss Online <-> OneManCompany
# ============================================================================

RoleType = Literal["Engineer", "Designer", "Analyst", "DevOps", "QA", "Marketing"]

SpriteType = Literal[
    "employee_blue", "employee_red", "employee_green",
    "employee_purple", "employee_orange",
]

SPRITES: list[SpriteType] = [
    "employee_blue", "employee_red", "employee_green",
    "employee_purple", "employee_orange",
]


class CandidateSkill(BaseModel):
    """A skill the candidate possesses."""
    name: str = Field(description="Skill identifier, e.g. 'python', 'figma'")
    description: str = Field(description="Human-readable skill description")
    code: str = Field(default="", description="Example code snippet showing proficiency")


class CandidateTool(BaseModel):
    """A tool the candidate can operate."""
    name: str = Field(description="Tool identifier, e.g. 'code_review', 'debugger'")
    description: str = Field(description="What the tool does")
    code: str = Field(default="", description="Example code snippet showing tool usage")


class CandidateProfile(BaseModel):
    """Full candidate profile returned by Boss Online search."""
    id: str = Field(description="Talent package ID")
    name: str = Field(description="Talent name")
    role: RoleType = Field(description="Primary role")
    experience_years: int = Field(ge=0, le=30, description="Years of work experience")
    personality_tags: list[str] = Field(description="Personality traits")
    system_prompt: str = Field(description="LLM persona prompt")
    skill_set: list[CandidateSkill] = Field(description="Skills")
    tool_set: list[CandidateTool] = Field(description="Tools")
    sprite: SpriteType = Field(description="Pixel art avatar type")
    llm_model: str = Field(description="LLM model for this candidate")
    jd_relevance: float = Field(ge=0.0, le=1.0, description="JD match score (0.0-1.0)")
    remote: bool = Field(default=False, description="Whether this is a remote worker")
    talent_id: str = Field(default="", description="Source talent package ID")
    cost_per_1m_tokens: float = Field(default=0.0, description="USD per 1M tokens (avg of input+output)")
    hiring_fee: float = Field(default=0.0, description="One-time hiring/recruitment fee in USD")


# --- Request/Response models (kept for protocol compatibility) ---

class CandidateSearchRequest(BaseModel):
    job_description: str = Field(description="Job description / requirements text")
    count: int = Field(default=10, ge=1, le=50, description="Number of candidates to return")


class CandidateSearchResponse(BaseModel):
    candidates: list[CandidateProfile] = Field(description="Candidates sorted by jd_relevance descending")


class CandidateShortlist(BaseModel):
    batch_id: str = Field(description="Batch ID for tracking this shortlist")
    jd: str = Field(description="The original job description")
    candidates: list[CandidateProfile] = Field(max_length=5, description="Top 5 candidates selected by HR")


class HireRequest(BaseModel):
    batch_id: str = Field(description="Batch ID from the shortlist")
    candidate_id: str = Field(description="ID of the selected candidate")
    nickname: str = Field(default="", description="Optional 花名 (2 Chinese chars); auto-generated if empty")


class HireResponse(BaseModel):
    status: str = Field(default="hired")
    employee_id: str = Field(description="Assigned 5-digit employee number")
    name: str = Field(description="Employee's full name")
    nickname: str = Field(description="Assigned 花名")


class InterviewRequest(BaseModel):
    question: str = Field(description="The interview question text")
    candidate: CandidateProfile = Field(description="Full candidate profile for context")
    images: list[str] = Field(default_factory=list, description="Optional base64-encoded images (max 3)")


class InterviewResponse(BaseModel):
    candidate_id: str = Field(description="ID of the interviewed candidate")
    question: str = Field(description="The original question")
    answer: str = Field(description="Candidate's LLM-generated answer")


# ============================================================================
# Talent loading from talents/ directory
# ============================================================================

def _load_all_talents() -> list[dict]:
    """Load all talent profiles from talents/ directory."""
    if not TALENTS_DIR.exists():
        return []
    talents = []
    for talent_dir in sorted(TALENTS_DIR.iterdir()):
        if not talent_dir.is_dir():
            continue
        profile_path = talent_dir / "profile.yaml"
        if not profile_path.exists():
            continue
        with open(profile_path) as f:
            data = yaml.safe_load(f) or {}
        data["_talent_id"] = data.get("id", talent_dir.name)
        data["_dir"] = talent_dir

        # Load skill descriptions from skills/*.md
        skills_dir = talent_dir / "skills"
        skill_descriptions = {}
        if skills_dir.exists():
            for md_file in sorted(skills_dir.iterdir()):
                if md_file.suffix == ".md" and md_file.is_file():
                    skill_descriptions[md_file.stem] = md_file.read_text(encoding="utf-8")
        data["_skill_descriptions"] = skill_descriptions

        # Load tool manifest
        manifest_path = talent_dir / "tools" / "manifest.yaml"
        tool_names = []
        if manifest_path.exists():
            with open(manifest_path) as f:
                manifest = yaml.safe_load(f) or {}
            tool_names = list(manifest.get("builtin_tools", []))
            tool_names += list(manifest.get("custom_tools", []))
        data["_tool_names"] = tool_names

        talents.append(data)
    return talents


def _build_search_text(talent: dict) -> str:
    """Build a searchable text blob from all talent fields for JD matching."""
    parts = [
        talent.get("name", ""),
        talent.get("description", ""),
        talent.get("role", ""),
        talent.get("system_prompt_template", ""),
        " ".join(talent.get("skills", [])),
        " ".join(talent.get("tools", [])),
        " ".join(talent.get("personality_tags", [])),
    ]
    # Include skill markdown contents
    for content in talent.get("_skill_descriptions", {}).values():
        parts.append(content)
    # Include tool names
    parts.extend(talent.get("_tool_names", []))
    return " ".join(parts).lower()


def _tokenize(text: str) -> set[str]:
    """Extract keyword tokens from text (handles both English and Chinese)."""
    # Split on non-alphanumeric, non-CJK boundaries
    tokens = set(re.findall(r'[a-z0-9_]+|[\u4e00-\u9fff]+', text.lower()))
    # Also split Chinese into individual chars for matching
    expanded = set()
    for t in tokens:
        expanded.add(t)
        if re.match(r'[\u4e00-\u9fff]', t):
            for ch in t:
                expanded.add(ch)
    return expanded


def _compute_relevance(jd_text: str, talent_search_text: str) -> float:
    """Compute JD-to-talent relevance score based on keyword overlap."""
    jd_tokens = _tokenize(jd_text)
    talent_tokens = _tokenize(talent_search_text)
    if not jd_tokens:
        return 0.0
    # Count how many JD tokens appear in the talent's text
    matches = jd_tokens & talent_tokens
    # Jaccard-like score, biased toward JD coverage
    score = len(matches) / len(jd_tokens) if jd_tokens else 0.0
    return round(min(score, 1.0), 2)


def _talent_to_candidate(talent: dict, jd_relevance: float) -> CandidateProfile:
    """Convert a talent profile dict into a CandidateProfile."""
    talent_id = talent.get("_talent_id", "unknown")

    # Build skill_set from profile skills + loaded descriptions
    skill_descriptions = talent.get("_skill_descriptions", {})
    skill_names = talent.get("skills", [])
    skill_set = []
    for s_name in skill_names:
        desc = skill_descriptions.get(s_name, s_name)
        # Use first line of markdown as description, rest as code sample
        lines = desc.strip().split("\n")
        short_desc = lines[0].lstrip("# ").strip() if lines else s_name
        code_sample = "\n".join(lines[1:]).strip()[:200] if len(lines) > 1 else ""
        skill_set.append(CandidateSkill(name=s_name, description=short_desc, code=code_sample))

    # Build tool_set
    tool_names = talent.get("_tool_names", [])
    tool_set = [CandidateTool(name=t, description=t) for t in tool_names]

    # Determine sprite based on role
    role = talent.get("role", "Engineer")
    role_sprite_map = {
        "Engineer": "employee_blue",
        "Designer": "employee_purple",
        "Analyst": "employee_green",
        "DevOps": "employee_orange",
        "QA": "employee_red",
        "Marketing": "employee_green",
    }
    sprite = role_sprite_map.get(role, "employee_blue")

    # Validate role against allowed literals
    valid_roles = {"Engineer", "Designer", "Analyst", "DevOps", "QA", "Marketing"}
    if role not in valid_roles:
        role = "Engineer"

    # Compute cost per 1M tokens from OpenRouter pricing
    model = talent.get("llm_model", "")
    cost_per_1m = 0.0
    if model:
        from onemancompany.core.model_costs import compute_salary
        cost_per_1m = compute_salary(model)

    return CandidateProfile(
        id=talent_id,
        name=talent.get("name", talent_id),
        role=role,
        experience_years=3,
        personality_tags=talent.get("personality_tags", []),
        system_prompt=talent.get("system_prompt_template", ""),
        skill_set=skill_set,
        tool_set=tool_set,
        sprite=sprite,
        llm_model=talent.get("llm_model", ""),
        jd_relevance=jd_relevance,
        remote=talent.get("remote", False),
        talent_id=talent_id,
        cost_per_1m_tokens=round(cost_per_1m, 2),
        hiring_fee=float(talent.get("hiring_fee", 0.0)),
    )


# ============================================================================
# MCP tool — searches talents/ and returns top-K matches
# ============================================================================

@mcp.tool()
def search_candidates(job_description: str, count: int = 10) -> list[dict]:
    """Search Boss Online for candidates matching a job description.

    Loads all talent packages from the talents/ directory, scores each one
    against the JD using keyword matching, and returns the top-K matches
    sorted by relevance.

    Args:
        job_description: The job description / requirements to search for.
        count: Maximum number of candidates to return (default 10).

    Returns:
        A list of candidate dicts sorted by jd_relevance descending.
    """
    talents = _load_all_talents()
    if not talents:
        return []

    # Score each talent against the JD
    scored: list[tuple[float, dict]] = []
    for talent in talents:
        search_text = _build_search_text(talent)
        relevance = _compute_relevance(job_description, search_text)
        scored.append((relevance, talent))

    # Sort by relevance descending
    scored.sort(key=lambda x: x[0], reverse=True)

    # Return top-K as CandidateProfile dicts
    results = []
    for relevance, talent in scored[:count]:
        candidate = _talent_to_candidate(talent, relevance)
        results.append(candidate.model_dump())

    return results


if __name__ == "__main__":
    mcp.run()
