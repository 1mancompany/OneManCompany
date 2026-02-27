"""Boss Online — Simulated Recruitment Platform MCP Server

Input: Job Description (JD)
Output: N candidates, each with profile, system_prompt, skill_set, tool_set.
Runs as a subprocess via stdio transport.
"""

import random
import uuid

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("BossOnline")

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

SPRITES = [
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
ROLE_PROFILES = {
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


def _generate_one_candidate(role: str, jd: str) -> dict:
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
    skills = random.sample(profile["skills"], k=n_skills)

    # Select 1-2 tools with code
    n_tools = random.randint(1, len(profile["tools"]))
    tools = random.sample(profile["tools"], k=n_tools)

    # Pick personality tags
    tags = random.sample(PERSONALITY_TAGS, k=random.randint(2, 4))

    # Experience (years)
    experience = random.randint(1, 8)

    return {
        "id": str(uuid.uuid4())[:8],
        "name": name,
        "role": role,
        "experience_years": experience,
        "personality_tags": tags,
        "system_prompt": system_prompt,
        "skill_set": skills,
        "tool_set": tools,
        "sprite": random.choice(SPRITES),
        "llm_model": random.choice(LLM_MODELS),
        "jd_relevance": round(random.uniform(0.5, 1.0), 2),  # simulated relevance
    }


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
    # Detect role from JD keywords
    jd_lower = job_description.lower()
    role_keywords = {
        "Engineer": ["engineer", "backend", "frontend", "develop", "code", "工程", "开发", "编程", "python", "java"],
        "Designer": ["design", "ui", "ux", "figma", "设计", "交互", "视觉"],
        "Analyst": ["analyst", "data", "sql", "分析", "数据", "报表"],
        "DevOps": ["devops", "infrastructure", "deploy", "docker", "运维", "部署", "容器"],
        "QA": ["qa", "test", "quality", "测试", "质量"],
        "Marketing": ["marketing", "content", "seo", "growth", "营销", "推广", "增长"],
    }

    matched_role = None
    for role, keywords in role_keywords.items():
        if any(kw in jd_lower for kw in keywords):
            matched_role = role
            break

    if not matched_role:
        matched_role = random.choice(list(ROLE_PROFILES.keys()))

    # Generate candidates — mix of target role + some random roles
    candidates = []
    for i in range(count):
        if i < count * 0.7:  # 70% target role
            r = matched_role
        else:
            r = random.choice(list(ROLE_PROFILES.keys()))
        candidates.append(_generate_one_candidate(r, job_description))

    # Sort by jd_relevance descending
    candidates.sort(key=lambda c: c["jd_relevance"], reverse=True)
    return candidates


if __name__ == "__main__":
    mcp.run()
