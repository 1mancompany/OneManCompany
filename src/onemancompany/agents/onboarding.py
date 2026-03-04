"""Employee onboarding — code-driven hire flow.

Standalone functions for creating employees, setting up profiles,
copying talent assets, generating nicknames, and registering agent loops.
Called by routes.py (talent market hire) and hr_agent.py (_apply_results).
"""

from __future__ import annotations

import json as _json
import re
import shutil

from langchain_core.messages import HumanMessage, SystemMessage

from onemancompany.core.config import (
    DEFAULT_TOOL_PERMISSIONS,
    DEFAULT_TOOL_PERMISSIONS_FALLBACK,
    DEFAULT_DEPARTMENT,
    HR_ID,
    ROLE_DEPARTMENT_MAP,
    TALENTS_DIR,
    EmployeeConfig,
    ensure_employee_dir,
    save_employee_profile,
    save_work_principles,
    settings,
)
from onemancompany.core.events import CompanyEvent, event_bus
from onemancompany.core.layout import (
    compute_layout,
    get_next_desk_for_department,
    persist_all_desk_positions,
)
from onemancompany.core.state import Employee, company_state, make_title


# ---------------------------------------------------------------------------
# Nickname generation
# ---------------------------------------------------------------------------

def _get_existing_nicknames() -> set[str]:
    """Collect all nicknames in use by current and ex-employees."""
    nicknames: set[str] = set()
    for emp in company_state.employees.values():
        if emp.nickname:
            nicknames.add(emp.nickname)
    for emp in company_state.ex_employees.values():
        if emp.nickname:
            nicknames.add(emp.nickname)
    return nicknames


async def generate_nickname(name: str, role: str, is_founding: bool = False) -> str:
    """Generate a wuxia-themed Chinese nickname (花名) for an employee.

    Founding employees (level 4) get 3-character nicknames.
    Normal employees (level 1-3) get 2-character nicknames.
    All nicknames must be unique across all current and ex-employees.
    """
    from onemancompany.agents.base import make_llm, tracked_ainvoke

    char_count = 3 if is_founding else 2
    existing = _get_existing_nicknames()
    gen_llm = make_llm(HR_ID)

    for attempt in range(5):
        avoid_clause = ""
        if existing:
            sample = list(existing)[:20]
            avoid_clause = f"- MUST NOT be any of these existing nicknames: {', '.join(sample)}\n"

        gen_prompt = (
            f"You are a wuxia novelist naming a character.\n"
            f"Give a 花名 (nickname) for: {name}, role: {role}.\n\n"
            f"Requirements:\n"
            f"- Exactly {char_count} Chinese characters\n"
            f"- Must have a wuxia/martial arts/jianghu flavor — think swordsmen, heroes, legendary figures\n"
            f"- Should sound like a person's name or title in the jianghu, not an object\n"
            f"- Creative, memorable, and fitting for their role\n"
            f"- Reference style: 独孤求败, 风清扬, 令狐冲, 段誉, 黄蓉, 小龙女, 逍遥子, 天山童姥\n"
            f"- For {char_count}-char names: 铁面侠, 暖心侠, 玲珑阁, 金算盘, 逍遥子, 追风客\n"
            f"{avoid_clause}\n"
            f"Reply with ONLY the {char_count}-character 花名, nothing else."
        )
        result = await tracked_ainvoke(gen_llm, [
            SystemMessage(content="You are a wuxia novelist. Reply with ONLY the nickname."),
            HumanMessage(content=gen_prompt),
        ], category="nickname_gen", employee_id=HR_ID)
        nickname = result.content.strip()
        chinese_chars = re.findall(r'[\u4e00-\u9fff]', nickname)
        if len(chinese_chars) >= char_count:
            candidate = ''.join(chinese_chars[:char_count])
        elif chinese_chars:
            candidate = ''.join(chinese_chars)
        else:
            continue

        if candidate not in existing:
            return candidate

    return ""


# ---------------------------------------------------------------------------
# Talent asset copying
# ---------------------------------------------------------------------------

def copy_talent_assets(talent_id: str, emp_dir) -> None:
    """Copy skills/ and tools/ from a talent package into an employee folder."""
    talent_dir = TALENTS_DIR / talent_id
    if not talent_dir.exists():
        return

    talent_skills = talent_dir / "skills"
    if talent_skills.exists():
        emp_skills = emp_dir / "skills"
        emp_skills.mkdir(exist_ok=True)
        for src_file in talent_skills.iterdir():
            if src_file.suffix == ".md" and src_file.is_file():
                dst_file = emp_skills / src_file.name
                if not dst_file.exists():
                    shutil.copy2(str(src_file), str(dst_file))

    talent_tools = talent_dir / "tools"
    if talent_tools.exists():
        emp_tools = emp_dir / "tools"
        emp_tools.mkdir(exist_ok=True)
        for src_file in talent_tools.iterdir():
            if src_file.is_file():
                dst_file = emp_tools / src_file.name
                if not dst_file.exists():
                    shutil.copy2(str(src_file), str(dst_file))


# ---------------------------------------------------------------------------
# Core hire execution
# ---------------------------------------------------------------------------

async def execute_hire(
    name: str,
    nickname: str,
    role: str,
    skills: list[str],
    *,
    talent_id: str = "",
    llm_model: str = "",
    temperature: float = 0.7,
    image_model: str = "",
    api_provider: str = "openrouter",
    hosting: str = "company",
    auth_method: str = "api_key",
    sprite: str = "employee_default",
    remote: bool = False,
) -> Employee:
    """Execute the full hire flow in code — no LLM involved.

    Assigns employee number, department, desk position, permissions,
    creates profile, copies talent assets, generates work principles,
    and registers the agent loop.

    Returns the newly created Employee.
    """
    from onemancompany.core.model_costs import compute_salary

    # Auto-assign department based on role
    department = ROLE_DEPARTMENT_MAP.get(role, DEFAULT_DEPARTMENT)

    # Desk position
    if remote:
        desk_pos = (-1, -1)
    else:
        desk_pos = get_next_desk_for_department(company_state, department)

    emp_num = company_state.next_employee_number()

    # Default permissions
    default_perms = ["company_file_access", "web_search"]
    default_tool_perms = list(DEFAULT_TOOL_PERMISSIONS.get(
        department, DEFAULT_TOOL_PERMISSIONS_FALLBACK
    ))

    # Salary
    if api_provider == "openrouter":
        salary = compute_salary(llm_model) if llm_model else 0.0
    else:
        salary = 0.0

    # Auto-generate nickname if not provided
    if not nickname:
        nickname = await generate_nickname(name, role, is_founding=False)

    emp = Employee(
        id=emp_num,
        name=name,
        nickname=nickname,
        level=1,
        department=department,
        role=role,
        skills=skills,
        employee_number=emp_num,
        desk_position=desk_pos,
        sprite=sprite,
        remote=remote,
        permissions=default_perms,
        tool_permissions=default_tool_perms,
        salary_per_1m_tokens=salary,
        probation=True,
        onboarding_completed=False,
    )
    company_state.employees[emp_num] = emp

    # Save profile
    config = EmployeeConfig(
        name=name,
        nickname=nickname,
        level=1,
        department=department,
        role=role,
        skills=skills,
        employee_number=emp_num,
        desk_position=list(desk_pos),
        sprite=sprite,
        remote=remote,
        llm_model=llm_model,
        temperature=temperature,
        image_model=image_model,
        permissions=default_perms,
        tool_permissions=default_tool_perms,
        salary_per_1m_tokens=salary,
        api_provider=api_provider,
        hosting=hosting,
        auth_method=auth_method,
    )
    save_employee_profile(emp_num, config)

    emp_dir = ensure_employee_dir(emp_num)
    skills_dir = emp_dir / "skills"

    # Connection config for remote and self-hosted employees
    if remote or hosting == "self":
        connection = {
            "employee_id": emp_num,
            "company_url": f"http://{settings.host}:{settings.port}",
            "talent_id": talent_id,
        }
        (emp_dir / "connection.json").write_text(
            _json.dumps(connection, indent=2, ensure_ascii=False), encoding="utf-8",
        )

    # Copy talent skills + tools
    if talent_id and not remote:
        copy_talent_assets(talent_id, emp_dir)

    # Copy launch.sh for self-hosted employees
    if hosting == "self" and talent_id:
        talent_launch = TALENTS_DIR / talent_id / "launch.sh"
        if talent_launch.exists():
            dst_launch = emp_dir / "launch.sh"
            if not dst_launch.exists():
                shutil.copy2(str(talent_launch), str(dst_launch))
                dst_launch.chmod(dst_launch.stat().st_mode | 0o111)  # ensure executable

    # Create skill stubs
    for skill_name in skills:
        skill_file = skills_dir / f"{skill_name}.md"
        if not skill_file.exists():
            skill_file.write_text(
                f"# {skill_name}\n\n{name} ({nickname})'s {skill_name} skill.\n\n"
                f"(Auto-created by HR during hiring.)\n",
                encoding="utf-8",
            )

    # Generate initial work principles
    initial_principles = (
        f"# {name} ({nickname}) Work Principles\n\n"
        f"**Department**: {department}\n"
        f"**Title**: {make_title(1, role)}\n"
        f"**Level**: Lv.1\n\n"
        f"## Core Principles\n"
        f"1. Complete assigned work diligently and maintain professional standards\n"
        f"2. Actively collaborate with the team and communicate progress promptly\n"
        f"3. Continuously learn and improve professional skills\n"
        f"4. Follow company rules and guidelines\n"
    )
    save_work_principles(emp_num, initial_principles)
    emp.work_principles = initial_principles

    # Recompute layout
    compute_layout(company_state)
    persist_all_desk_positions(company_state)

    company_state.activity_log.append(
        {"type": "employee_hired", "name": name, "nickname": nickname, "role": role}
    )
    await event_bus.publish(CompanyEvent(type="employee_hired", payload=emp.to_dict(), agent="HR"))

    # Register in EmployeeManager (skip remote — they use remote task queue)
    if not remote:
        from onemancompany.core.agent_loop import get_agent_loop, register_and_start_agent, register_self_hosted
        if not get_agent_loop(emp_num):
            if hosting == "self":
                register_self_hosted(emp_num)
            else:
                from onemancompany.agents.base import EmployeeAgent
                await register_and_start_agent(emp_num, EmployeeAgent(emp_num))

    # Trigger onboarding routine as background task
    import asyncio
    from onemancompany.core.routine import run_onboarding_routine
    asyncio.create_task(run_onboarding_routine(emp_num))

    return emp
