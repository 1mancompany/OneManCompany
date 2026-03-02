"""Boss Online — Simulated Recruitment Platform MCP Server

Input: Job Description (JD)
Output: N candidates, each with profile, system_prompt, skill_set, tool_set.
Runs as a subprocess via stdio transport.

All request/response data formats are defined as Pydantic models below,
serving as the formal interface protocol between our system and Boss Online.
"""

from __future__ import annotations

import random
import uuid
from typing import Literal

from pydantic import BaseModel, Field

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("BossOnline")


# ============================================================================
# Interface Protocol — Pydantic models for Boss Online <-> OneManCompany
# ============================================================================

# --- Shared enums / literals ---

RoleType = Literal["Engineer", "Designer", "Analyst", "DevOps", "QA", "Marketing"]

SpriteType = Literal[
    "employee_blue", "employee_red", "employee_green",
    "employee_purple", "employee_orange",
]


# --- Candidate data returned by Boss Online ---

class CandidateSkill(BaseModel):
    """A skill the candidate possesses, with a code sample demonstrating proficiency."""
    name: str = Field(description="Skill identifier, e.g. 'python', 'figma'")
    description: str = Field(description="Human-readable skill description")
    code: str = Field(description="Example code snippet showing proficiency")


class CandidateTool(BaseModel):
    """A tool the candidate can operate, with a code sample."""
    name: str = Field(description="Tool identifier, e.g. 'code_review', 'debugger'")
    description: str = Field(description="What the tool does")
    code: str = Field(description="Example code snippet showing tool usage")


class CandidateProfile(BaseModel):
    """Full candidate profile returned by Boss Online search.

    This is the core data structure exchanged between Boss Online and our system.
    HR receives this, filters/shortlists, and forwards to CEO for selection.
    """
    id: str = Field(description="Unique candidate ID (8-char UUID prefix)")
    name: str = Field(description="Full name, e.g. 'Alex Chen'")
    role: RoleType = Field(description="Primary role the candidate is applying for")
    experience_years: int = Field(ge=0, le=30, description="Years of work experience")
    personality_tags: list[str] = Field(description="Personality traits, e.g. ['self-motivated', 'team player']")
    system_prompt: str = Field(description="LLM persona prompt for this candidate (used in interviews)")
    skill_set: list[CandidateSkill] = Field(description="Skills with code samples (3-4 items)")
    tool_set: list[CandidateTool] = Field(description="Tools with code samples (1-2 items)")
    sprite: SpriteType = Field(description="Pixel art avatar type for office visualization")
    llm_model: str = Field(description="LLM model to use when this candidate becomes an employee")
    jd_relevance: float = Field(ge=0.0, le=1.0, description="JD match score (0.0-1.0, higher is better)")


# --- Request: what we send to Boss Online ---

class CandidateSearchRequest(BaseModel):
    """Search request sent to Boss Online recruitment platform."""
    job_description: str = Field(description="Job description / requirements text")
    count: int = Field(default=10, ge=1, le=50, description="Number of candidates to return")


# --- Response: what Boss Online returns ---

class CandidateSearchResponse(BaseModel):
    """Search response from Boss Online — a ranked list of candidates."""
    candidates: list[CandidateProfile] = Field(description="Candidates sorted by jd_relevance descending")


# --- HR -> CEO: shortlist for selection ---

class CandidateShortlist(BaseModel):
    """HR-curated shortlist sent to CEO for visual selection on the frontend."""
    batch_id: str = Field(description="Batch ID for tracking this shortlist")
    jd: str = Field(description="The original job description")
    candidates: list[CandidateProfile] = Field(max_length=5, description="Top 5 candidates selected by HR")


# --- CEO -> Backend: hire request ---

class HireRequest(BaseModel):
    """CEO's decision to hire a specific candidate from the shortlist."""
    batch_id: str = Field(description="Batch ID from the shortlist")
    candidate_id: str = Field(description="ID of the selected candidate")
    nickname: str = Field(default="", description="Optional 花名 (2 Chinese chars); auto-generated if empty")


class HireResponse(BaseModel):
    """Response after successfully hiring a candidate."""
    status: str = Field(default="hired")
    employee_id: str = Field(description="Assigned 5-digit employee number")
    name: str = Field(description="Employee's full name")
    nickname: str = Field(description="Assigned 花名")


# --- CEO -> Backend: interview request ---

class InterviewRequest(BaseModel):
    """CEO's interview question for a candidate."""
    question: str = Field(description="The interview question text")
    candidate: CandidateProfile = Field(description="Full candidate profile for context")
    images: list[str] = Field(default_factory=list, description="Optional base64-encoded images (max 3)")


class InterviewResponse(BaseModel):
    """Candidate's answer to the interview question."""
    candidate_id: str = Field(description="ID of the interviewed candidate")
    question: str = Field(description="The original question")
    answer: str = Field(description="Candidate's LLM-generated answer")


# ============================================================================
# Candidate generation data
# ============================================================================

FIRST_NAMES = [
    "Alex", "Jordan", "Taylor", "Morgan", "Casey", "Riley", "Drew",
    "Avery", "Quinn", "Sage", "Blake", "Skyler", "Reese", "Parker",
    "Jamie", "Rowan", "Kai", "Nova", "River", "Phoenix",
]
LAST_NAMES = [
    "Smith", "Chen", "Patel", "Johnson", "Kim", "Garcia", "Lee",
    "Wang", "Nakamura", "Müller", "Santos", "Ivanov", "Osei",
    "Zhao", "Singh", "Tanaka", "Park", "Fernandez",
]

SPRITES: list[SpriteType] = [
    "employee_blue", "employee_red", "employee_green",
    "employee_purple", "employee_orange",
]

LLM_MODELS = [
    "openai/gpt-4o-mini",
    "openai/gpt-4o",
    "anthropic/claude-3.5-sonnet",
    "anthropic/claude-3-haiku",
    "google/gemini-2.0-flash-001",
    "google/gemini-2.5-pro-preview",
    "meta-llama/llama-3.1-70b-instruct",
    "deepseek/deepseek-chat-v3-0324:free",
    "qwen/qwen-2.5-72b-instruct",
]

# Rich role definitions with system prompts, skills (with code), and tools (with code)
ROLE_PROFILES: dict[str, dict] = {
    "Engineer": {
        "skills": [
            {"name": "python", "description": "Python programming", "code": "def solve(problem): return analyze(problem) + implement(solution)"},
            {"name": "fastapi", "description": "FastAPI backend development", "code": "app = FastAPI()\n@app.get('/api/data')\nasync def get_data(): ..."},
            {"name": "databases", "description": "Database design & optimization", "code": "SELECT * FROM employees WHERE level >= 2 ORDER BY performance DESC"},
            {"name": "algorithms", "description": "Algorithms & data structures", "code": "def binary_search(arr, target): lo, hi = 0, len(arr)-1; ..."},
        ],
        "tools": [
            {"name": "code_review", "description": "Code review tool", "code": "def review(pr_id): diff = get_diff(pr_id); return analyze_quality(diff)"},
            {"name": "debugger", "description": "Debugging tool", "code": "def debug(error): trace = get_stacktrace(); return find_root_cause(trace)"},
        ],
        "system_prompt_template": "You are a {trait} software engineer skilled in {specialty}. Your coding style is {style}, focusing on {focus}.",
        "traits": ["rigorous", "efficient", "innovative", "full-stack", "detail-oriented"],
        "specialties": ["backend architecture", "API design", "performance optimization", "distributed systems", "microservices"],
        "styles": ["clean & concise", "well-commented", "test-driven", "functional", "object-oriented"],
        "focuses": ["code quality", "system stability", "maintainability", "execution efficiency", "user experience"],
    },
    "Designer": {
        "skills": [
            {"name": "figma", "description": "Figma design", "code": "// Auto Layout + Component variants for responsive design"},
            {"name": "css", "description": "CSS/frontend styling", "code": ".card { display: grid; gap: 1rem; border-radius: var(--radius); }"},
            {"name": "user_research", "description": "User research", "code": "def conduct_interview(user): questions = generate_questions(persona); ..."},
            {"name": "prototyping", "description": "Prototyping", "code": "wireframe -> mockup -> interactive_prototype -> user_test"},
        ],
        "tools": [
            {"name": "design_system", "description": "Design system management", "code": "def sync_tokens(): colors = load_tokens(); apply_to_components(colors)"},
            {"name": "usability_test", "description": "Usability testing", "code": "def test(prototype, users): metrics = measure_task_completion(users); ..."},
        ],
        "system_prompt_template": "You are a {trait} designer skilled in {specialty}. Your design philosophy is {style}, focusing on {focus}.",
        "traits": ["pixel-perfect", "user-first", "highly creative", "data-driven", "minimalist"],
        "specialties": ["UI design", "UX research", "interaction design", "brand design", "information architecture"],
        "styles": ["less is more", "user-centered", "emotional design", "consistency-first", "bold & innovative"],
        "focuses": ["visual consistency", "interaction fluidity", "accessibility", "conversion rate", "brand coherence"],
    },
    "Analyst": {
        "skills": [
            {"name": "data_analysis", "description": "Data analysis", "code": "df.groupby('dept').agg({'revenue': 'sum', 'cost': 'mean'}).sort_values('revenue')"},
            {"name": "sql", "description": "SQL querying", "code": "WITH metrics AS (SELECT date, SUM(sales) FROM orders GROUP BY date) SELECT * FROM metrics"},
            {"name": "reporting", "description": "Report writing", "code": "def generate_report(data): insights = extract_insights(data); return format_report(insights)"},
            {"name": "visualization", "description": "Data visualization", "code": "fig = px.scatter(df, x='x', y='y', color='category', size='value')"},
        ],
        "tools": [
            {"name": "dashboard", "description": "Dashboard building", "code": "def build_dashboard(metrics): charts = [create_chart(m) for m in metrics]; ..."},
            {"name": "ab_testing", "description": "A/B test analysis", "code": "def analyze_test(control, variant): p_value = ttest(control, variant); ..."},
        ],
        "system_prompt_template": "You are a {trait} data analyst skilled in {specialty}. Your analytical approach is {style}, focusing on {focus}.",
        "traits": ["rigorous", "insightful", "communicative", "business-oriented", "thorough"],
        "specialties": ["business analytics", "user behavior analysis", "financial analysis", "operations optimization", "market research"],
        "styles": ["data-driven", "hypothesis-testing", "multi-dimensional analysis", "trend forecasting", "comparative analysis"],
        "focuses": ["data accuracy", "actionable insights", "business impact", "data security", "real-time monitoring"],
    },
    "DevOps": {
        "skills": [
            {"name": "docker", "description": "Containerization", "code": "FROM python:3.12-slim\nWORKDIR /app\nCOPY . .\nRUN pip install -r requirements.txt"},
            {"name": "kubernetes", "description": "Container orchestration", "code": "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: app"},
            {"name": "ci_cd", "description": "CI/CD pipelines", "code": "stages: [test, build, deploy]\njobs:\n  test: pytest --cov"},
            {"name": "monitoring", "description": "Monitoring & alerting", "code": "alert: HighErrorRate\nexpr: rate(http_errors[5m]) > 0.05"},
        ],
        "tools": [
            {"name": "infra_provisioner", "description": "Infrastructure management", "code": "def provision(config): terraform_apply(config); validate_health()"},
            {"name": "incident_handler", "description": "Incident handling", "code": "def handle_incident(alert): diagnose(alert); mitigate(); post_mortem()"},
        ],
        "system_prompt_template": "You are a {trait} DevOps engineer skilled in {specialty}. Your operational philosophy is {style}, focusing on {focus}.",
        "traits": ["automation-first", "reliable", "fast-responding", "full-stack", "security-conscious"],
        "specialties": ["CI/CD", "container orchestration", "cloud architecture", "monitoring systems", "security hardening"],
        "styles": ["infrastructure as code", "GitOps", "immutable infrastructure", "progressive delivery", "chaos engineering"],
        "focuses": ["system availability", "deployment frequency", "incident recovery speed", "security compliance", "cost optimization"],
    },
    "QA": {
        "skills": [
            {"name": "testing", "description": "Test strategy", "code": "def test_plan(feature): unit + integration + e2e + performance"},
            {"name": "automation", "description": "Test automation", "code": "def test_login():\n  page.fill('#email', 'test@example.com')\n  page.click('button[type=submit]')"},
            {"name": "quality", "description": "Quality assurance", "code": "def quality_gate(build): coverage > 80% and bugs == 0 and perf < 200ms"},
            {"name": "selenium", "description": "Browser automation", "code": "driver = webdriver.Chrome()\ndriver.get(url)\nassert title in driver.title"},
        ],
        "tools": [
            {"name": "test_reporter", "description": "Test reporting", "code": "def report(results): summary = aggregate(results); generate_html(summary)"},
            {"name": "bug_tracker", "description": "Bug tracking", "code": "def file_bug(title, steps, expected, actual): create_issue(severity, assignee)"},
        ],
        "system_prompt_template": "You are a {trait} QA engineer skilled in {specialty}. Your testing philosophy is {style}, focusing on {focus}.",
        "traits": ["meticulous", "perfectionist", "logically rigorous", "user-perspective", "efficiency-first"],
        "specialties": ["test automation", "performance testing", "security testing", "regression testing", "exploratory testing"],
        "styles": ["shift-left testing", "risk-driven", "behavior-driven", "continuous testing", "built-in quality"],
        "focuses": ["coverage", "regression risk", "user experience", "performance baselines", "defect prevention"],
    },
    "Marketing": {
        "skills": [
            {"name": "content", "description": "Content marketing", "code": "def content_calendar(month): topics = research_trends(); plan = schedule(topics); ..."},
            {"name": "seo", "description": "Search engine optimization", "code": "def optimize(page): keywords = research(); meta_tags = generate(keywords); ..."},
            {"name": "social_media", "description": "Social media management", "code": "def campaign(platform, audience): creative = design(); schedule_posts(creative)"},
            {"name": "analytics", "description": "Marketing analytics", "code": "def roi_analysis(campaign): cost = total_spend(); revenue = attribution(); return revenue/cost"},
        ],
        "tools": [
            {"name": "campaign_manager", "description": "Campaign management", "code": "def launch(campaign): audience = segment(); creative = ab_test(); deploy(creative)"},
            {"name": "growth_tracker", "description": "Growth tracking", "code": "def track_funnel(stages): conversion = [rate(s) for s in stages]; find_bottleneck()"},
        ],
        "system_prompt_template": "You are a {trait} marketing specialist skilled in {specialty}. Your marketing strategy is {style}, focusing on {focus}.",
        "traits": ["endlessly creative", "data-sensitive", "communicative", "results-driven", "omni-channel"],
        "specialties": ["content marketing", "growth hacking", "brand building", "social media", "paid acquisition"],
        "styles": ["content is king", "data-driven growth", "community building", "viral marketing", "precision targeting"],
        "focuses": ["user acquisition", "brand awareness", "conversion rate", "user retention", "ROI"],
    },
}

# Personality traits for generating unique bios
PERSONALITY_TAGS = [
    "self-motivated", "team player", "fast learner", "strong communicator", "results-driven",
    "detail-oriented", "big-picture thinker", "creative", "resilient", "cross-disciplinary",
]

# JD keyword -> role mapping for auto-detection
ROLE_KEYWORDS: dict[str, list[str]] = {
    "Engineer": ["engineer", "backend", "frontend", "develop", "code", "工程", "开发", "编程", "python", "java"],
    "Designer": ["design", "ui", "ux", "figma", "设计", "交互", "视觉"],
    "Analyst": ["analyst", "data", "sql", "分析", "数据", "报表"],
    "DevOps": ["devops", "infrastructure", "deploy", "docker", "运维", "部署", "容器"],
    "QA": ["qa", "test", "quality", "测试", "质量"],
    "Marketing": ["marketing", "content", "seo", "growth", "营销", "推广", "增长"],
}


# ============================================================================
# Candidate generation
# ============================================================================

def _generate_one_candidate(role: str) -> CandidateProfile:
    """Generate a single rich candidate profile."""
    profile = ROLE_PROFILES.get(role, ROLE_PROFILES["Engineer"])

    first = random.choice(FIRST_NAMES)
    last = random.choice(LAST_NAMES)
    name = f"{first} {last}"

    # Build unique system prompt
    system_prompt = profile["system_prompt_template"].format(
        trait=random.choice(profile["traits"]),
        specialty=random.choice(profile["specialties"]),
        style=random.choice(profile["styles"]),
        focus=random.choice(profile["focuses"]),
    )

    # Select 3-4 skills with code
    n_skills = random.randint(3, len(profile["skills"]))
    skills = [CandidateSkill(**s) for s in random.sample(profile["skills"], k=n_skills)]

    # Select 1-2 tools with code
    n_tools = random.randint(1, len(profile["tools"]))
    tools = [CandidateTool(**t) for t in random.sample(profile["tools"], k=n_tools)]

    # Pick personality tags
    tags = random.sample(PERSONALITY_TAGS, k=random.randint(2, 4))

    return CandidateProfile(
        id=str(uuid.uuid4())[:8],
        name=name,
        role=role,
        experience_years=random.randint(1, 8),
        personality_tags=tags,
        system_prompt=system_prompt,
        skill_set=skills,
        tool_set=tools,
        sprite=random.choice(SPRITES),
        llm_model=random.choice(LLM_MODELS),
        jd_relevance=round(random.uniform(0.5, 1.0), 2),
    )


def _detect_role(jd: str) -> str:
    """Detect the target role from JD text using keyword matching."""
    jd_lower = jd.lower()
    for role, keywords in ROLE_KEYWORDS.items():
        if any(kw in jd_lower for kw in keywords):
            return role
    return random.choice(list(ROLE_PROFILES.keys()))


# ============================================================================
# MCP tool (the actual Boss Online API endpoint)
# ============================================================================

@mcp.tool()
def search_candidates(job_description: str, count: int = 10) -> list[dict]:
    """Search Boss Online for candidates matching a job description.

    Args:
        job_description: The job description / requirements to search for.
        count: Number of candidates to return (default 10).

    Returns:
        A list of candidate dicts, each with id, name, role, experience_years,
        personality_tags, system_prompt, skill_set, tool_set, sprite, jd_relevance.
    """
    matched_role = _detect_role(job_description)

    # Generate candidates — mix of target role + some random roles
    candidates: list[CandidateProfile] = []
    for i in range(count):
        if i < count * 0.7:  # 70% target role
            r = matched_role
        else:
            r = random.choice(list(ROLE_PROFILES.keys()))
        candidates.append(_generate_one_candidate(r))

    # Sort by jd_relevance descending
    candidates.sort(key=lambda c: c.jd_relevance, reverse=True)

    # Return as dicts for MCP JSON serialization
    return [c.model_dump() for c in candidates]


if __name__ == "__main__":
    mcp.run()
