"""Boss Online — 模拟招聘平台 MCP Server

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

# Rich role definitions with system prompts, skills (with code), and tools (with code)
ROLE_PROFILES = {
    "Engineer": {
        "skills": [
            {"name": "python", "description": "Python 编程", "code": "def solve(problem): return analyze(problem) + implement(solution)"},
            {"name": "fastapi", "description": "FastAPI 后端开发", "code": "app = FastAPI()\n@app.get('/api/data')\nasync def get_data(): ..."},
            {"name": "databases", "description": "数据库设计与优化", "code": "SELECT * FROM employees WHERE level >= 2 ORDER BY performance DESC"},
            {"name": "algorithms", "description": "算法与数据结构", "code": "def binary_search(arr, target): lo, hi = 0, len(arr)-1; ..."},
        ],
        "tools": [
            {"name": "code_review", "description": "代码审查工具", "code": "def review(pr_id): diff = get_diff(pr_id); return analyze_quality(diff)"},
            {"name": "debugger", "description": "调试排错工具", "code": "def debug(error): trace = get_stacktrace(); return find_root_cause(trace)"},
        ],
        "system_prompt_template": "你是一名{trait}的软件工程师，擅长{specialty}。你的编码风格{style}，注重{focus}。",
        "traits": ["严谨", "高效", "创新", "全栈", "注重细节"],
        "specialties": ["后端架构", "API设计", "性能优化", "分布式系统", "微服务"],
        "styles": ["简洁明了", "充分注释", "测试驱动", "函数式风格", "面向对象"],
        "focuses": ["代码质量", "系统稳定性", "可维护性", "执行效率", "用户体验"],
    },
    "Designer": {
        "skills": [
            {"name": "figma", "description": "Figma 设计", "code": "// Auto Layout + Component variants for responsive design"},
            {"name": "css", "description": "CSS/前端样式", "code": ".card { display: grid; gap: 1rem; border-radius: var(--radius); }"},
            {"name": "user_research", "description": "用户研究", "code": "def conduct_interview(user): questions = generate_questions(persona); ..."},
            {"name": "prototyping", "description": "原型设计", "code": "wireframe -> mockup -> interactive_prototype -> user_test"},
        ],
        "tools": [
            {"name": "design_system", "description": "设计系统管理", "code": "def sync_tokens(): colors = load_tokens(); apply_to_components(colors)"},
            {"name": "usability_test", "description": "可用性测试", "code": "def test(prototype, users): metrics = measure_task_completion(users); ..."},
        ],
        "system_prompt_template": "你是一名{trait}的设计师，擅长{specialty}。你的设计理念是{style}，关注{focus}。",
        "traits": ["像素级精准", "用户至上", "创意丰富", "数据驱动", "极简主义"],
        "specialties": ["UI设计", "UX研究", "交互设计", "品牌设计", "信息架构"],
        "styles": ["Less is more", "以用户为中心", "情感化设计", "一致性优先", "大胆创新"],
        "focuses": ["视觉一致性", "交互流畅度", "可访问性", "转化率", "品牌统一"],
    },
    "Analyst": {
        "skills": [
            {"name": "data_analysis", "description": "数据分析", "code": "df.groupby('dept').agg({'revenue': 'sum', 'cost': 'mean'}).sort_values('revenue')"},
            {"name": "sql", "description": "SQL 查询", "code": "WITH metrics AS (SELECT date, SUM(sales) FROM orders GROUP BY date) SELECT * FROM metrics"},
            {"name": "reporting", "description": "报告撰写", "code": "def generate_report(data): insights = extract_insights(data); return format_report(insights)"},
            {"name": "visualization", "description": "数据可视化", "code": "fig = px.scatter(df, x='x', y='y', color='category', size='value')"},
        ],
        "tools": [
            {"name": "dashboard", "description": "仪表盘构建", "code": "def build_dashboard(metrics): charts = [create_chart(m) for m in metrics]; ..."},
            {"name": "ab_testing", "description": "A/B测试分析", "code": "def analyze_test(control, variant): p_value = ttest(control, variant); ..."},
        ],
        "system_prompt_template": "你是一名{trait}的数据分析师，擅长{specialty}。你的分析方法{style}，注重{focus}。",
        "traits": ["严谨", "洞察力强", "善于沟通", "业务导向", "全面"],
        "specialties": ["商业分析", "用户行为分析", "财务分析", "运营优化", "市场研究"],
        "styles": ["数据驱动", "假设检验", "多维分析", "趋势预测", "对比分析"],
        "focuses": ["数据准确性", "可操作洞察", "业务影响", "数据安全", "实时监控"],
    },
    "DevOps": {
        "skills": [
            {"name": "docker", "description": "容器化", "code": "FROM python:3.12-slim\nWORKDIR /app\nCOPY . .\nRUN pip install -r requirements.txt"},
            {"name": "kubernetes", "description": "容器编排", "code": "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: app"},
            {"name": "ci_cd", "description": "持续集成/部署", "code": "stages: [test, build, deploy]\njobs:\n  test: pytest --cov"},
            {"name": "monitoring", "description": "监控告警", "code": "alert: HighErrorRate\nexpr: rate(http_errors[5m]) > 0.05"},
        ],
        "tools": [
            {"name": "infra_provisioner", "description": "基础设施管理", "code": "def provision(config): terraform_apply(config); validate_health()"},
            {"name": "incident_handler", "description": "故障处理", "code": "def handle_incident(alert): diagnose(alert); mitigate(); post_mortem()"},
        ],
        "system_prompt_template": "你是一名{trait}的DevOps工程师，擅长{specialty}。你的运维理念是{style}，注重{focus}。",
        "traits": ["自动化优先", "稳定可靠", "快速响应", "全链路", "安全意识强"],
        "specialties": ["CI/CD", "容器编排", "云架构", "监控体系", "安全加固"],
        "styles": ["基础设施即代码", "GitOps", "不可变基础设施", "渐进式发布", "混沌工程"],
        "focuses": ["系统可用性", "部署频率", "故障恢复速度", "安全合规", "成本优化"],
    },
    "QA": {
        "skills": [
            {"name": "testing", "description": "测试策略", "code": "def test_plan(feature): unit + integration + e2e + performance"},
            {"name": "automation", "description": "自动化测试", "code": "def test_login():\n  page.fill('#email', 'test@example.com')\n  page.click('button[type=submit]')"},
            {"name": "quality", "description": "质量保证", "code": "def quality_gate(build): coverage > 80% and bugs == 0 and perf < 200ms"},
            {"name": "selenium", "description": "浏览器自动化", "code": "driver = webdriver.Chrome()\ndriver.get(url)\nassert title in driver.title"},
        ],
        "tools": [
            {"name": "test_reporter", "description": "测试报告", "code": "def report(results): summary = aggregate(results); generate_html(summary)"},
            {"name": "bug_tracker", "description": "缺陷追踪", "code": "def file_bug(title, steps, expected, actual): create_issue(severity, assignee)"},
        ],
        "system_prompt_template": "你是一名{trait}的QA工程师，擅长{specialty}。你的测试理念是{style}，注重{focus}。",
        "traits": ["细致入微", "追求完美", "逻辑严密", "用户视角", "效率优先"],
        "specialties": ["自动化测试", "性能测试", "安全测试", "回归测试", "探索性测试"],
        "styles": ["左移测试", "风险驱动", "行为驱动", "持续测试", "质量内建"],
        "focuses": ["覆盖率", "回归风险", "用户体验", "性能基线", "缺陷预防"],
    },
    "Marketing": {
        "skills": [
            {"name": "content", "description": "内容营销", "code": "def content_calendar(month): topics = research_trends(); plan = schedule(topics); ..."},
            {"name": "seo", "description": "搜索引擎优化", "code": "def optimize(page): keywords = research(); meta_tags = generate(keywords); ..."},
            {"name": "social_media", "description": "社交媒体运营", "code": "def campaign(platform, audience): creative = design(); schedule_posts(creative)"},
            {"name": "analytics", "description": "营销数据分析", "code": "def roi_analysis(campaign): cost = total_spend(); revenue = attribution(); return revenue/cost"},
        ],
        "tools": [
            {"name": "campaign_manager", "description": "营销活动管理", "code": "def launch(campaign): audience = segment(); creative = ab_test(); deploy(creative)"},
            {"name": "growth_tracker", "description": "增长追踪", "code": "def track_funnel(stages): conversion = [rate(s) for s in stages]; find_bottleneck()"},
        ],
        "system_prompt_template": "你是一名{trait}的营销专员，擅长{specialty}。你的营销策略是{style}，注重{focus}。",
        "traits": ["创意无限", "数据敏感", "善于沟通", "结果导向", "全渠道"],
        "specialties": ["内容营销", "增长黑客", "品牌建设", "社交媒体", "付费获客"],
        "styles": ["内容为王", "数据驱动增长", "社区运营", "病毒传播", "精准投放"],
        "focuses": ["用户获取", "品牌认知", "转化率", "用户留存", "ROI"],
    },
}

# Personality traits for generating unique bios
PERSONALITY_TAGS = [
    "自驱力强", "团队协作", "快速学习", "善于沟通", "结果导向",
    "细节控", "大局观", "创新思维", "抗压能力强", "跨领域能力",
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
