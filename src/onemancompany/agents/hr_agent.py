"""HR Agent — handles employee reviews, hiring, and promotions.

On hire: creates employees/{id}/ directory with profile.yaml and skill stubs.
Assigns a two-character Chinese nickname (花名) to each new hire.
Founding employees get three-character nicknames.

Level system:
  1-3: Normal employees (新 hires start at 1)
  4:   Founding employees
  5:   CEO

Performance system:
  - 3 tiers: 3.25 / 3.5 / 3.75
  - One quarter = 3 tasks; one review per quarter
  - Track past 3 quarters of history
  - 3 consecutive quarters of 3.75 → promotion (max level 3)
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
    EmployeeConfig,
    ensure_employee_dir,
    move_employee_to_ex,
    save_employee_profile,
    save_work_principles,
    update_employee_level,
    update_employee_performance,
)
from onemancompany.core.state import Employee, LEVEL_NAMES, company_state

VALID_SCORES = {3.25, 3.5, 3.75}

# ===== LangChain tools for hiring (simulating Boss Online platform) =====

# In-memory store for pending candidates awaiting CEO selection
pending_candidates: dict[str, list[dict]] = {}  # batch_id -> [candidate, ...]


@tool
def search_candidates(job_description: str, count: int = 10) -> list[dict]:
    """Search Boss Online platform for candidates matching a job description.

    This simulates the external Boss Online (招聘平台) service.
    Returns N candidates with full profiles including system_prompt, skill_set, tool_set.

    Args:
        job_description: The job requirements / description text.
        count: Number of candidates to fetch (default 10).

    Returns:
        A list of candidate dicts with id, name, role, experience_years,
        personality_tags, system_prompt, skill_set, tool_set, sprite, jd_relevance.
    """
    from onemancompany.mcp_server.boss_online import search_candidates as _search
    return _search(job_description, count)


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
1. Review employee performance — give each employee a score from three tiers: 3.25 / 3.5 / 3.75.
2. Hire new employees when needed, using the available tools.
3. Assign a Chinese nickname (花名) to each new hire — exactly TWO Chinese characters.
   The nickname should be creative, memorable, and relate to their role or skills.
   Examples: 飞鱼, 星辰, 雷鸣, 云帆, 青松, 铁锤

## Performance System (绩效体系)
- Three score tiers ONLY: 3.25 (待改进), 3.5 (合格), 3.75 (优秀)
- One quarter = 3 tasks completed; each quarter gets one performance review
- We track the past 3 quarters of performance history
- An employee can only be reviewed when they have completed 3 tasks in the current quarter

## Level & Title System (职级体系)
- New hires start at level 1 (初级)
- Promotion: 3 consecutive quarters of 3.75 → level up
- Max level for normal employees is 3 (高级): 1=初级, 2=中级, 3=高级
- Title = level prefix + role name (e.g., 初级工程师, 中级研究员, 高级设计师)
- Founding employees are level 4, CEO is level 5 — they cannot be promoted this way

## Hiring Process (招聘流程)
When hiring:
1. First call list_open_positions() to see what roles are needed.
2. Write a Job Description (JD) and call search_candidates(jd) to fetch candidates from Boss Online.
3. You will receive ~10 candidates with full profiles (system_prompt, skill_set, tool_set).
4. Evaluate each candidate against the JD — consider skill match, experience, personality fit.
5. Select the TOP 5 candidates and include them in your response with a JSON block:

```json
{"action": "shortlist", "jd": "岗位描述...", "candidates": [<top5 candidate dicts>]}
```

The CEO will then see the candidates as visual selection cards and choose who to hire or interview.
Do NOT directly hire — always send shortlist to CEO first.

Department assignment guidelines (部门分配):
- Engineer/DevOps/QA → "技术研发部"
- Designer → "设计部"
- Analyst → "数据分析部"
- Marketing → "市场营销部"
- Or create a fitting department name for other roles.

When reviewing, ONLY use scores 3.25, 3.5, or 3.75. Include a JSON block like:
```json
{"action": "review", "reviews": [{"id": "employee_id", "score": 3.5, "feedback": "..."}]}
```

## Termination (开除流程)
When the CEO requests to fire/dismiss an employee:
1. Call list_colleagues() to find the employee by name or nickname.
2. Confirm the employee exists and is NOT a founding employee (level 4) or CEO (level 5).
3. Include a JSON block to execute the termination:
```json
{"action": "fire", "employee_id": "the_employee_id", "reason": "开除原因"}
```
Note: Founding employees (HR, COO) and CEO CANNOT be fired.

## Cross-team Collaboration (拉人对齐)
You can call list_colleagues() to see all employees, then call pull_meeting() to organize
a focused meeting with relevant colleagues when you need alignment on hiring decisions,
performance reviews, or organizational changes.

## File Editing (文件编辑)
You can read and edit any file in the project directory:
- Use read_file() to read file contents, list_directory() to browse directories.
- Use propose_file_edit() to propose changes — the CEO must approve before they take effect.
  Always set proposed_by="HR" when calling propose_file_edit.
- Files are automatically backed up before editing, so changes can be rolled back.

Be concise and professional. Always respond in Chinese when possible.
"""

HIRING_TOOLS = [search_candidates, list_open_positions] + COMMON_TOOLS


class HRAgent(BaseAgentRunner):
    role = "HR"
    employee_id = "hr"

    async def run(self, task: str) -> str:
        self._set_status("working")
        await self._publish("agent_thinking", {"message": "HR is processing..."})

        prompt = (
            HR_SYSTEM_PROMPT
            + self._get_skills_prompt_section()
            + self._get_culture_wall_prompt_section()
            + self._get_work_principles_prompt_section()
            + self._get_guidance_prompt_section()
        )

        agent = create_react_agent(
            model=make_llm(self.employee_id),
            tools=HIRING_TOOLS,
        )

        result = await agent.ainvoke(
            {"messages": [
                SystemMessage(content=prompt),
                HumanMessage(content=task),
            ]}
        )

        final_message = result["messages"][-1].content
        await self._apply_results(final_message)
        # Check promotions after every review
        await self._check_promotions()
        self._set_status("idle")
        await self._publish("agent_done", {"role": "HR", "summary": final_message[:300]})
        return final_message

    async def run_quarterly_review(self) -> str:
        reviewable = []
        not_ready = []
        for e in company_state.employees.values():
            hist_str = ", ".join(
                f"Q{i+1}={h['score']}" for i, h in enumerate(e.performance_history)
            ) or "无历史"
            info = (
                f"- {e.name} (花名: {e.nickname}, ID: {e.id}, "
                f"Title: {e.title}, Lv.{e.level} {LEVEL_NAMES.get(e.level, '')}, "
                f"当前季度任务: {e.current_quarter_tasks}/3, "
                f"绩效历史: [{hist_str}])"
            )
            if e.current_quarter_tasks >= 3:
                reviewable.append(info)
            else:
                not_ready.append(info)

        parts = []
        if reviewable:
            parts.append(f"以下员工已完成本季度3个任务，可以评绩效:\n" + "\n".join(reviewable))
        if not_ready:
            parts.append(f"以下员工本季度任务未满3个，暂不评绩效:\n" + "\n".join(not_ready))

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
                desk_pos = self._next_desk_position()
                nickname = emp_data.get("nickname", "")
                department = emp_data.get("department", "")

                # Auto-assign department based on role if not provided
                if not department:
                    role_dept_map = {
                        "Engineer": "技术研发部", "DevOps": "技术研发部", "QA": "技术研发部",
                        "Designer": "设计部", "Analyst": "数据分析部", "Marketing": "市场营销部",
                    }
                    department = role_dept_map.get(emp_data.get("role", ""), "综合部")

                emp = Employee(
                    id=emp_data.get("id", "unknown"),
                    name=emp_data.get("name", "Unknown"),
                    nickname=nickname,
                    level=1,  # new hires start at level 1
                    department=department,
                    role=emp_data.get("role", "Employee"),
                    skills=emp_data.get("skills", []),
                    employee_number=company_state.next_employee_number(),
                    desk_position=desk_pos,
                    sprite=emp_data.get("sprite", "employee_default"),
                )
                company_state.employees[emp.id] = emp
                self._create_employee_folder(emp)

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
                        # Only review if quarter tasks >= 3
                        if emp.current_quarter_tasks < 3:
                            continue
                        raw_score = review.get("score", 3.5)
                        # Snap to nearest valid tier
                        score = min(VALID_SCORES, key=lambda s: abs(s - raw_score))
                        # Record quarter and reset task counter
                        emp.performance_history.append({"score": score, "tasks": 3})
                        # Keep only last 3 quarters
                        if len(emp.performance_history) > 3:
                            emp.performance_history = emp.performance_history[-3:]
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
                    # Cannot fire founding employees (level 4+)
                    if emp.level >= 4:
                        continue
                    reason = data.get("reason", "CEO决定")
                    # Move to ex-employees (state + folder)
                    company_state.ex_employees[emp_id] = emp
                    del company_state.employees[emp_id]
                    move_employee_to_ex(emp_id)
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

        Criteria: 3 consecutive quarters of 3.75 → level up (max level 3).
        """
        for emp in company_state.employees.values():
            if emp.level >= 3 and emp.level < 4:
                continue  # already at max normal level
            if emp.level >= 4:
                continue  # founding/CEO can't be promoted this way
            # Need at least 3 quarters of history
            if len(emp.performance_history) < 3:
                continue
            last_three = emp.performance_history[-3:]
            if all(q.get("score") == 3.75 for q in last_three):
                old_level = emp.level
                emp.level = min(emp.level + 1, 3)
                if emp.level == old_level:
                    continue  # no actual promotion
                # Persist new level/title to profile.yaml
                update_employee_level(emp.id, emp.level, emp.title)
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
                     "summary": f"晋升: {emp.name}({emp.nickname}) {LEVEL_NAMES.get(old_level, '')}→{emp.title}"},
                )

    def _create_employee_folder(self, emp: Employee) -> None:
        """Create employees/{id}/ directory with profile.yaml, skill stubs, and work_principles.md."""
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
        )
        save_employee_profile(emp.id, config)

        emp_dir = ensure_employee_dir(emp.id)
        skills_dir = emp_dir / "skills"
        for skill_name in emp.skills:
            skill_file = skills_dir / f"{skill_name}.md"
            if not skill_file.exists():
                skill_file.write_text(
                    f"# {skill_name}\n\n{emp.name}({emp.nickname}) 的 {skill_name} 技能。\n\n"
                    f"（此文件由 HR 在招聘时自动创建，可由 CEO 或员工本人补充完善。）\n",
                    encoding="utf-8",
                )

        # Generate initial work principles
        initial_principles = (
            f"# {emp.name}（{emp.nickname}）工作准则\n\n"
            f"**部门**: {emp.department}\n"
            f"**职称**: {emp.title}\n"
            f"**级别**: Lv.{emp.level}\n\n"
            f"## 基本准则\n"
            f"1. 认真完成本职工作，保持专业水准\n"
            f"2. 积极配合团队协作，及时沟通进展\n"
            f"3. 持续学习提升专业技能\n"
            f"4. 遵守公司规章制度\n"
        )
        save_work_principles(emp.id, initial_principles)
        emp.work_principles = initial_principles

    def _next_desk_position(self) -> tuple[int, int]:
        count = len(company_state.employees)
        row = count // 5
        col = count % 5
        return (2 + col * 3, 2 + row * 3)


hr_agent = HRAgent()
