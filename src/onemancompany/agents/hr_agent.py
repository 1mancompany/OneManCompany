"""HR Agent -- handles employee reviews, hiring, and promotions.

On hire: creates employees/{id}/ directory with profile.yaml and skill stubs.
Assigns a two-character Chinese nickname (hua ming) to each new hire.
Founding employees get three-character nicknames.

Level system:
  1-3: Normal employees (new hires start at 1)
  4:   Founding employees
  5:   CEO

Performance system:
  - 3 tiers: 3.25 / 3.5 / 3.75
  - One quarter = 3 tasks; one review per quarter
  - Track past 3 quarters of history
  - 3 consecutive quarters of 3.75 -> promotion (max level 3)
"""

from __future__ import annotations

import json
import random
import re
import uuid

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

from onemancompany.agents.base import BaseAgentRunner, make_llm
from onemancompany.agents.common_tools import COMMON_TOOLS
from onemancompany.core.config import (
    DEFAULT_DEPARTMENT,
    FOUNDING_LEVEL,
    HR_ID,
    MAX_NORMAL_LEVEL,
    MAX_PERFORMANCE_HISTORY,
    MAX_SUMMARY_LEN,
    QUARTERS_FOR_PROMOTION,
    ROLE_DEPARTMENT_MAP,
    SCORE_EXCELLENT,
    STATUS_IDLE,
    STATUS_WORKING,
    TASKS_PER_QUARTER,
    VALID_SCORES,
    EmployeeConfig,
    ensure_employee_dir,
    move_employee_to_ex,
    save_employee_profile,
    save_work_principles,
    update_employee_level,
    update_employee_performance,
)
from onemancompany.core.layout import compute_layout, get_next_desk_for_department, persist_all_desk_positions
from onemancompany.core.state import Employee, LEVEL_NAMES, company_state

# ===== LangChain tools for hiring (from talent_market/talents/) =====

# In-memory store for pending candidates awaiting CEO selection
pending_candidates: dict[str, list[dict]] = {}  # batch_id -> [candidate, ...]


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
        "llm_model": talent.get("llm_model", ""),
        "jd_relevance": 1.0,
        "remote": talent.get("remote", False),
        "talent_id": talent_id,
    }


@tool
def search_candidates(job_description: str) -> list[dict]:
    """Search the talent market for available candidates.

    Scans all talent packages under talent_market/talents/ and returns them
    as candidate profiles that the CEO can review and hire.

    Args:
        job_description: The job requirements / description text (used for context).

    Returns:
        A list of candidate dicts from available talent packages.
    """
    from onemancompany.core.config import list_available_talents, load_talent_profile

    talents = list_available_talents()
    candidates = []
    for t in talents:
        profile = load_talent_profile(t["id"])
        if profile:
            candidates.append(_talent_to_candidate(profile))
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


HR_SYSTEM_PROMPT = """You are the HR manager of a startup company called "One Man Company".

Your responsibilities:
1. Review employee performance -- give each employee a score from three tiers: 3.25 / 3.5 / 3.75.
2. Hire new employees when needed, using the available tools.
3. Assign a Chinese nickname (花名) to each new hire -- exactly TWO Chinese characters.
   The nickname should be creative, memorable, and relate to their role or skills.
   Examples: 飞鱼, 星辰, 雷鸣, 云帆, 青松, 铁锤

## Performance System
- Three score tiers ONLY: 3.25 (needs improvement), 3.5 (meets expectations), 3.75 (excellent)
- One quarter = 3 tasks completed; each quarter gets one performance review
- We track the past 3 quarters of performance history
- An employee can only be reviewed when they have completed 3 tasks in the current quarter

## Level & Title System
- New hires start at level 1 (Junior)
- Promotion: 3 consecutive quarters of 3.75 -> level up
- Max level for normal employees is 3 (Senior): 1=Junior, 2=Mid-level, 3=Senior
- Title = level prefix + role name (e.g., Junior Engineer, Mid-level Researcher, Senior Designer)
- Founding employees are level 4, CEO is level 5 -- they cannot be promoted this way

## Hiring Process
When hiring:
1. First call list_open_positions() to see what roles are needed.
2. Call search_candidates(jd) to browse available talents from the talent market.
   Each talent is a pre-packaged profile with skills, tools, and LLM config.
3. Review the returned candidates — each comes from a talent package.
4. Select candidates that best fit the need and include them in your response with a JSON block:

```json
{"action": "shortlist", "jd": "Job description...", "candidates": [<top5 candidate dicts>]}
```

The CEO will then see the candidates as visual selection cards and choose who to hire or interview.
Do NOT directly hire -- always send shortlist to CEO first.

Department assignment guidelines:
- Engineer/DevOps/QA -> "Engineering"
- Designer -> "Design"
- Analyst -> "Data Analytics"
- Marketing -> "Marketing"
- Or create a fitting department name for other roles.

When reviewing, ONLY use scores 3.25, 3.5, or 3.75. Include a JSON block like:
```json
{"action": "review", "reviews": [{"id": "employee_id", "score": 3.5, "feedback": "..."}]}
```

## Termination
When the CEO requests to fire/dismiss an employee:
1. Call list_colleagues() to find the employee by name or nickname.
2. Confirm the employee exists and is NOT a founding employee (level 4) or CEO (level 5).
3. Include a JSON block to execute the termination:
```json
{"action": "fire", "employee_id": "the_employee_id", "reason": "Reason for termination"}
```
Note: Founding employees (HR, COO) and CEO CANNOT be fired.

## Cross-team Collaboration
You can call list_colleagues() to see all employees, then call pull_meeting() to organize
a focused meeting with relevant colleagues when you need alignment on hiring decisions,
performance reviews, or organizational changes.

## File Editing
You can read and edit any file in the project directory:
- Use read_file() to read file contents, list_directory() to browse directories.
- Use propose_file_edit() to propose changes -- the CEO must approve before they take effect.
  Always set proposed_by="HR" when calling propose_file_edit.
- Files are automatically backed up before editing, so changes can be rolled back.

Be concise and professional.
"""

HIRING_TOOLS = [search_candidates, list_open_positions] + COMMON_TOOLS


class HRAgent(BaseAgentRunner):
    role = "HR"
    employee_id = HR_ID

    def __init__(self) -> None:
        self._agent = create_react_agent(
            model=make_llm(self.employee_id),
            tools=HIRING_TOOLS,
        )

    def _build_prompt(self) -> str:
        return (
            HR_SYSTEM_PROMPT
            + self._get_skills_prompt_section()
            + self._get_tools_prompt_section()
            + self._get_company_culture_prompt_section()
            + self._get_work_principles_prompt_section()
            + self._get_guidance_prompt_section()
        )

    async def run(self, task: str) -> str:
        self._set_status(STATUS_WORKING)
        await self._publish("agent_thinking", {"message": "HR is processing..."})

        result = await self._agent.ainvoke(
            {"messages": [
                SystemMessage(content=self._build_prompt()),
                HumanMessage(content=task),
            ]}
        )

        final_message = result["messages"][-1].content
        await self._apply_results(final_message)
        # Check promotions after every review
        await self._check_promotions()
        self._set_status(STATUS_IDLE)
        await self._publish("agent_done", {"role": "HR", "summary": final_message[:MAX_SUMMARY_LEN]})
        return final_message

    async def run_quarterly_review(self) -> str:
        reviewable = []
        not_ready = []
        for e in company_state.employees.values():
            hist_str = ", ".join(
                f"Q{i+1}={h['score']}" for i, h in enumerate(e.performance_history)
            ) or "no history"
            info = (
                f"- {e.name} (花名: {e.nickname}, ID: {e.id}, "
                f"Title: {e.title}, Lv.{e.level} {LEVEL_NAMES.get(e.level, '')}, "
                f"Q tasks: {e.current_quarter_tasks}/3, "
                f"Performance history: [{hist_str}])"
            )
            if e.current_quarter_tasks >= TASKS_PER_QUARTER:
                reviewable.append(info)
            else:
                not_ready.append(info)

        parts = []
        if reviewable:
            parts.append(f"The following employees completed 3 tasks this quarter and are ready for review:\n" + "\n".join(reviewable))
        if not_ready:
            parts.append(f"The following employees have not completed 3 tasks yet:\n" + "\n".join(not_ready))

        task = (
            "Run a quarterly performance review.\n\n"
            + "\n\n".join(parts)
            + "\n\nFor each reviewable employee, give a score of 3.25, 3.5, or 3.75.\n"
            "After the review, check for open positions and hire one new candidate."
        )
        return await self.run(task)

    async def _apply_results(self, output: str) -> None:
        json_blocks = re.findall(r"```json\s*(\{.*?\})\s*```", output, re.DOTALL)
        if not json_blocks:
            json_blocks = re.findall(
                r'\{"action"\s*:\s*"(?:hire|review|fire|shortlist)".*?\}', output, re.DOTALL
            )

        for block in json_blocks:
            try:
                data = json.loads(block)
            except json.JSONDecodeError:
                continue

            if data.get("action") == "shortlist" and "candidates" in data:
                # HR filtered candidates → send to CEO for visual selection
                candidates = data["candidates"][:5]  # max 5
                batch_id = str(uuid.uuid4())[:8]
                pending_candidates[batch_id] = candidates
                await self._publish("candidates_ready", {
                    "batch_id": batch_id,
                    "jd": data.get("jd", ""),
                    "candidates": candidates,
                })

            elif data.get("action") == "hire" and "employee" in data:
                emp_data = data["employee"]
                nickname = emp_data.get("nickname", "")
                department = emp_data.get("department", "")

                # Auto-assign department based on role if not provided
                if not department:
                    department = ROLE_DEPARTMENT_MAP.get(emp_data.get("role", ""), DEFAULT_DEPARTMENT)

                desk_pos = get_next_desk_for_department(company_state, department)

                emp_num = company_state.next_employee_number()
                is_remote = emp_data.get("remote", False)
                emp = Employee(
                    id=emp_num,
                    name=emp_data.get("name", "Unknown"),
                    nickname=nickname,
                    level=1,  # new hires start at level 1
                    department=department,
                    role=emp_data.get("role", "Employee"),
                    skills=emp_data.get("skills", []),
                    employee_number=emp_num,
                    desk_position=desk_pos,
                    sprite=emp_data.get("sprite", "employee_default"),
                    remote=is_remote,
                )
                company_state.employees[emp_num] = emp
                talent_id = emp_data.get("talent_id", "")
                self._create_employee_folder(emp, talent_id=talent_id)

                # Recompute layout (zones may resize) and persist all positions
                compute_layout(company_state)
                persist_all_desk_positions(company_state)

                company_state.activity_log.append(
                    {"type": "employee_hired", "name": emp.name,
                     "nickname": nickname, "role": emp.role}
                )
                await self._publish("employee_hired", emp.to_dict())

            elif data.get("action") == "review" and "reviews" in data:
                for review in data["reviews"]:
                    emp_id = review.get("id")
                    if emp_id and emp_id in company_state.employees:
                        emp = company_state.employees[emp_id]
                        # Only review if quarter tasks >= threshold
                        if emp.current_quarter_tasks < TASKS_PER_QUARTER:
                            continue
                        raw_score = review.get("score", 3.5)
                        # Snap to nearest valid tier
                        score = min(VALID_SCORES, key=lambda s: abs(s - raw_score))
                        # Record quarter and reset task counter
                        emp.performance_history.append({"score": score, "tasks": TASKS_PER_QUARTER})
                        # Keep only recent quarters
                        if len(emp.performance_history) > MAX_PERFORMANCE_HISTORY:
                            emp.performance_history = emp.performance_history[-MAX_PERFORMANCE_HISTORY:]
                        emp.current_quarter_tasks = 0
                        # Persist performance to profile.yaml
                        update_employee_performance(
                            emp_id, emp.current_quarter_tasks, emp.performance_history
                        )
                        await self._publish(
                            "employee_reviewed",
                            {"id": emp_id, "score": score,
                             "history": emp.performance_history},
                        )

            elif data.get("action") == "fire" and "employee_id" in data:
                emp_id = data["employee_id"]
                if emp_id in company_state.employees:
                    emp = company_state.employees[emp_id]
                    # Cannot fire founding employees
                    if emp.level >= FOUNDING_LEVEL:
                        continue
                    reason = data.get("reason", "CEO decision")
                    # Move to ex-employees (state + folder)
                    company_state.ex_employees[emp_id] = emp
                    del company_state.employees[emp_id]
                    move_employee_to_ex(emp_id)

                    # Recompute layout (zones may shrink) and persist all positions
                    compute_layout(company_state)
                    persist_all_desk_positions(company_state)

                    company_state.activity_log.append({
                        "type": "employee_fired",
                        "name": emp.name,
                        "nickname": emp.nickname,
                        "role": emp.role,
                        "reason": reason,
                    })
                    await self._publish("employee_fired", {
                        "id": emp_id,
                        "name": emp.name,
                        "nickname": emp.nickname,
                        "role": emp.role,
                        "reason": reason,
                    })

    async def _check_promotions(self) -> None:
        """Check if any employees qualify for promotion.

        Criteria: QUARTERS_FOR_PROMOTION consecutive quarters of SCORE_EXCELLENT → level up.
        """
        for emp in company_state.employees.values():
            if emp.level >= MAX_NORMAL_LEVEL and emp.level < FOUNDING_LEVEL:
                continue  # already at max normal level
            if emp.level >= FOUNDING_LEVEL:
                continue  # founding/CEO can't be promoted this way
            if len(emp.performance_history) < QUARTERS_FOR_PROMOTION:
                continue
            last_n = emp.performance_history[-QUARTERS_FOR_PROMOTION:]
            if all(q.get("score") == SCORE_EXCELLENT for q in last_n):
                old_level = emp.level
                emp.level = min(emp.level + 1, MAX_NORMAL_LEVEL)
                if emp.level == old_level:
                    continue  # no actual promotion
                # Persist new level/title to profile.yaml
                update_employee_level(emp.id, emp.level, emp.title)
                # Recompute layout (level change affects vertical ordering)
                compute_layout(company_state)
                persist_all_desk_positions(company_state)
                company_state.activity_log.append({
                    "type": "promotion",
                    "name": emp.name,
                    "nickname": emp.nickname,
                    "old_level": old_level,
                    "new_level": emp.level,
                    "new_title": emp.title,
                })
                await self._publish(
                    "agent_done",
                    {"role": "HR",
                     "summary": f"Promotion: {emp.name} ({emp.nickname}) {LEVEL_NAMES.get(old_level, '')} -> {emp.title}"},
                )

    def _create_employee_folder(self, emp: Employee, talent_id: str = "") -> None:
        """Create employees/{id}/ directory with profile.yaml, skill stubs, and work_principles.md.

        If *talent_id* is provided and the employee is on-site (not remote),
        copies the talent's skill markdown files and tools into the employee folder.
        """
        config = EmployeeConfig(
            name=emp.name,
            nickname=emp.nickname,
            level=emp.level,
            department=emp.department,
            role=emp.role,
            skills=emp.skills,
            employee_number=emp.employee_number,
            desk_position=list(emp.desk_position),
            sprite=emp.sprite,
            remote=emp.remote,
        )
        save_employee_profile(emp.id, config)

        emp_dir = ensure_employee_dir(emp.id)
        skills_dir = emp_dir / "skills"

        # If sourced from a talent and on-site, copy talent skills + tools
        if talent_id and not emp.remote:
            self._copy_talent_assets(talent_id, emp_dir)

        for skill_name in emp.skills:
            skill_file = skills_dir / f"{skill_name}.md"
            if not skill_file.exists():
                skill_file.write_text(
                    f"# {skill_name}\n\n{emp.name} ({emp.nickname})'s {skill_name} skill.\n\n"
                    f"(This file was auto-created by HR during hiring. It can be updated by the CEO or the employee.)\n",
                    encoding="utf-8",
                )

        # Generate initial work principles
        initial_principles = (
            f"# {emp.name} ({emp.nickname}) Work Principles\n\n"
            f"**Department**: {emp.department}\n"
            f"**Title**: {emp.title}\n"
            f"**Level**: Lv.{emp.level}\n\n"
            f"## Core Principles\n"
            f"1. Complete assigned work diligently and maintain professional standards\n"
            f"2. Actively collaborate with the team and communicate progress promptly\n"
            f"3. Continuously learn and improve professional skills\n"
            f"4. Follow company rules and guidelines\n"
        )
        save_work_principles(emp.id, initial_principles)
        emp.work_principles = initial_principles

    @staticmethod
    def _copy_talent_assets(talent_id: str, emp_dir) -> None:
        """Copy skills/ and tools/ from a talent package into an employee folder."""
        import shutil

        from onemancompany.core.config import TALENTS_DIR

        talent_dir = TALENTS_DIR / talent_id
        if not talent_dir.exists():
            return

        # Copy skill markdown files
        talent_skills = talent_dir / "skills"
        if talent_skills.exists():
            emp_skills = emp_dir / "skills"
            emp_skills.mkdir(exist_ok=True)
            for src_file in talent_skills.iterdir():
                if src_file.suffix == ".md" and src_file.is_file():
                    dst_file = emp_skills / src_file.name
                    if not dst_file.exists():
                        shutil.copy2(str(src_file), str(dst_file))

        # Copy tools directory (manifest + custom .py files)
        talent_tools = talent_dir / "tools"
        if talent_tools.exists():
            emp_tools = emp_dir / "tools"
            emp_tools.mkdir(exist_ok=True)
            for src_file in talent_tools.iterdir():
                if src_file.is_file():
                    dst_file = emp_tools / src_file.name
                    if not dst_file.exists():
                        shutil.copy2(str(src_file), str(dst_file))

    def _next_desk_position(self, department: str = "") -> tuple[int, int]:
        """Get next desk position using department-based layout."""
        return get_next_desk_for_department(company_state, department or "General")


# Singleton removed — agent instances are now created and registered
# in main.py lifespan via PersistentAgentLoop.
