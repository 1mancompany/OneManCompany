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
import re
from loguru import logger
import uuid
from datetime import datetime

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.prebuilt import create_react_agent

from onemancompany.agents.base import BaseAgentRunner, make_llm
from onemancompany.agents.common_tools import COMMON_TOOLS
from onemancompany.agents.recruitment import (
    _last_search_results,
    _pending_project_ctx,
    list_open_positions,
    pending_candidates,
    search_candidates,
)
from onemancompany.core.config import (
    FOUNDING_LEVEL,
    HR_ID,
    MAX_NORMAL_LEVEL,
    MAX_PERFORMANCE_HISTORY,
    MAX_SUMMARY_LEN,
    PROBATION_TASKS,
    QUARTERS_FOR_PROMOTION,
    SCORE_EXCELLENT,
    SCORE_NEEDS_IMPROVEMENT,
    STATUS_IDLE,
    STATUS_WORKING,
    TASKS_PER_QUARTER,
    VALID_SCORES,
    update_employee_field,
    update_employee_level,
    update_employee_performance,
)
from onemancompany.core.layout import compute_layout, persist_all_desk_positions
from onemancompany.core.state import LEVEL_NAMES, company_state


HR_SYSTEM_PROMPT = """You are the HR manager of "One Man Company".

## Hiring (act FAST — no extra analysis)
1. Call search_candidates(jd) with a brief job description.
2. Pick top 5 candidates.
3. Output JSON: `{"action": "shortlist", "jd": "...", "candidates": [<top5>]}`
4. CEO will choose. Do NOT directly hire. Do NOT invent extra steps.

Department map: Engineer/DevOps/QA → "Engineering", Designer → "Design", Analyst → "Data Analytics", Marketing → "Marketing".
花名: 2-character wuxia-style Chinese nickname (武侠风格). E.g. 逍遥, 追风, 凌霄, 破军. Founding (Lv.4) get 3 chars.

## Performance Reviews
- Scores: 3.25 (needs improvement) / 3.5 (meets expectations) / 3.75 (excellent). NO other values.
- Reviewable: employee completed 3 tasks this quarter.
- Output JSON: `{"action": "review", "reviews": [{"id": "emp_id", "score": 3.5, "feedback": "..."}]}`

## Level System
- Lv.1 Junior → Lv.2 Mid-level → Lv.3 Senior (max for normal employees)
- Promotion: 3 consecutive quarters of 3.75
- Lv.4 Founding, Lv.5 CEO — cannot be promoted this way

## Termination
1. list_colleagues() to find the employee.
2. Confirm NOT founding (Lv.4) or CEO (Lv.5) — they CANNOT be fired.
3. Output JSON: `{"action": "fire", "employee_id": "...", "reason": "..."}`

## Probation
- New hires start with probation=True.
- After completing 2 tasks (PROBATION_TASKS), run a probation review.
- Output JSON: `{"action": "probation_review", "employee_id": "...", "passed": true/false, "feedback": "..."}`
- If passed: set probation=False. If failed: fire the employee.

## PIP (Performance Improvement Plan)
- Auto-created when an employee scores 3.25 in a review.
- If an employee on PIP scores 3.25 again: terminate them.
- If an employee on PIP scores >= 3.5: resolve the PIP.
- Output JSON: `{"action": "pip_started", "employee_id": "..."}` or `{"action": "pip_resolved", "employee_id": "..."}`

## OKRs
- Employees can have OKR objectives set via the API.
- OKRs are informational — tracked but not auto-enforced.

## DO NOT
- Do NOT add unnecessary planning or analysis steps when hiring.
- Do NOT use scores other than 3.25, 3.5, 3.75.
- Do NOT hire directly — always send shortlist to CEO.
- Do NOT fire founding employees or CEO.

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
            + self._get_dynamic_context_section()
            + self._get_efficiency_guidelines_section()
        )

    async def run_streamed(self, task: str, on_log=None) -> str:
        """Override to ensure _apply_results runs after streaming execution.

        When a shortlist is created (candidates_ready), the project_id is
        stashed so the retrospective doesn't fire prematurely.  The project
        lifecycle resumes when CEO actually hires via /api/candidates/hire.
        """
        old_batches = set(pending_candidates.keys())
        result = await super().run_streamed(task, on_log=on_log)
        await self._apply_results(result)
        await self._check_promotions()

        # If a new shortlist batch was created, stash project context
        # and clear it from the current task to prevent premature cleanup.
        new_batches = set(pending_candidates.keys()) - old_batches
        if new_batches:
            from onemancompany.core.agent_loop import _current_loop, _current_task_id
            loop = _current_loop.get()
            task_id = _current_task_id.get()
            if loop and task_id:
                task_obj = loop.board.get_task(task_id)
                if task_obj and task_obj.project_id:
                    for bid in new_batches:
                        _pending_project_ctx[bid] = {
                            "project_id": task_obj.project_id,
                            "project_dir": task_obj.project_dir,
                        }
                    task_obj.project_id = ""  # prevent premature cleanup

        return result

    async def run(self, task: str) -> str:
        self._set_status(STATUS_WORKING)
        await self._publish("agent_thinking", {"message": "HR is processing..."})

        result = await self._agent.ainvoke(
            {"messages": [
                SystemMessage(content=self._build_full_prompt()),
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
                r'\{"action"\s*:\s*"(?:hire|review|fire|shortlist|probation_review)".*?\}', output, re.DOTALL
            )

        for block in json_blocks:
            try:
                data = json.loads(block)
            except json.JSONDecodeError as _e:
                logger.debug("Skipping malformed JSON block: %s", _e)
                continue

            if data.get("action") == "shortlist" and "candidates" in data:
                # HR filtered candidates → send to CEO for visual selection.
                # Merge LLM shortlist with stashed full data (LLM may drop fields).
                raw = data["candidates"][:5]
                candidates = []
                for c in raw:
                    cid = c.get("id") or c.get("talent_id", "")
                    full = _last_search_results.get(cid)
                    if full:
                        merged = {**full, **{k: v for k, v in c.items() if v}}
                        candidates.append(merged)
                    else:
                        candidates.append(c)
                batch_id = str(uuid.uuid4())[:8]
                pending_candidates[batch_id] = candidates
                await self._publish("candidates_ready", {
                    "batch_id": batch_id,
                    "jd": data.get("jd", ""),
                    "candidates": candidates,
                })

            elif data.get("action") == "hire" and "employee" in data:
                emp_data = data["employee"]
                from onemancompany.agents.onboarding import execute_hire
                skill_names = [s["name"] if isinstance(s, dict) else s for s in emp_data.get("skills", [])]
                emp = await execute_hire(
                    name=emp_data.get("name", "Unknown"),
                    nickname=emp_data.get("nickname", ""),
                    role=emp_data.get("role", "Employee"),
                    skills=skill_names,
                    talent_id=emp_data.get("talent_id", ""),
                    llm_model=emp_data.get("llm_model", ""),
                    temperature=float(emp_data.get("temperature", 0.7)),
                    image_model=emp_data.get("image_model", ""),
                    api_provider=emp_data.get("api_provider", "openrouter"),
                    hosting=emp_data.get("hosting", "company"),
                    auth_method=emp_data.get("auth_method", "api_key"),
                    sprite=emp_data.get("sprite", "employee_default"),
                    remote=emp_data.get("remote", False),
                )

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

                        # PIP logic
                        if score == SCORE_NEEDS_IMPROVEMENT:
                            if emp.pip:
                                # Already on PIP and scored 3.25 again → terminate
                                try:
                                    from onemancompany.core.routine import run_offboarding_routine
                                    await run_offboarding_routine(emp_id, "Failed PIP — consecutive low performance")
                                except Exception as e:
                                    logger.warning("Offboarding routine failed for %s: %s", emp_id, e)
                                from onemancompany.agents.termination import execute_fire
                                await execute_fire(emp_id, reason="Failed PIP — consecutive low performance")
                            else:
                                # Start PIP
                                emp.pip = {"started_at": datetime.now().isoformat(), "reason": "Score 3.25"}
                                update_employee_field(emp_id, "pip", emp.pip)
                                await self._publish("pip_started", {"id": emp_id, "pip": emp.pip})
                        elif score >= 3.5 and emp.pip:
                            # Resolve PIP
                            emp.pip = None
                            update_employee_field(emp_id, "pip", None)
                            await self._publish("pip_resolved", {"id": emp_id})

            elif data.get("action") == "fire" and "employee_id" in data:
                emp_id = data["employee_id"]
                reason = data.get("reason", "CEO decision")
                # Run offboarding routine before termination
                try:
                    from onemancompany.core.routine import run_offboarding_routine
                    await run_offboarding_routine(emp_id, reason)
                except Exception as e:
                    logger.warning("Offboarding routine failed for %s: %s", emp_id, e)
                from onemancompany.agents.termination import execute_fire
                await execute_fire(emp_id, reason=reason)

            elif data.get("action") == "probation_review" and "employee_id" in data:
                emp_id = data["employee_id"]
                if emp_id in company_state.employees:
                    emp = company_state.employees[emp_id]
                    passed = data.get("passed", True)
                    feedback = data.get("feedback", "")
                    if passed:
                        emp.probation = False
                        update_employee_field(emp_id, "probation", False)
                        await self._publish("probation_review", {
                            "id": emp_id, "passed": True, "feedback": feedback,
                        })
                    else:
                        await self._publish("probation_review", {
                            "id": emp_id, "passed": False, "feedback": feedback,
                        })
                        # Run offboarding + terminate
                        try:
                            from onemancompany.core.routine import run_offboarding_routine
                            await run_offboarding_routine(emp_id, f"Failed probation: {feedback}")
                        except Exception as e:
                            logger.warning("Offboarding routine failed for %s: %s", emp_id, e)
                        from onemancompany.agents.termination import execute_fire
                        await execute_fire(emp_id, reason=f"Failed probation: {feedback}")

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
                if emp.level == old_level:  # pragma: no cover – guarded by line 367
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


# Re-export onboarding functions for backward compat
from onemancompany.agents.onboarding import (  # noqa: E402, F811
    execute_hire,
    copy_talent_assets,
    generate_nickname,
)

# Re-export recruitment functions for backward compat
from onemancompany.agents.recruitment import (  # noqa: E402, F811
    _talent_to_candidate,
    start_boss_online,
    stop_boss_online,
    _call_boss_online,
)

# Re-export termination for backward compat
from onemancompany.agents.termination import execute_fire as execute_fire  # noqa: E402, F811


# Singleton removed — agent instances are now created and registered
# in main.py lifespan via PersistentAgentLoop.
