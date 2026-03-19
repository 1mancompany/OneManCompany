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

from onemancompany.agents.base import BaseAgentRunner, extract_final_content, make_llm
from onemancompany.agents.recruitment import (
    _last_search_results,
    _pending_project_ctx,
    _persist_candidates,
    list_open_positions,
    pending_candidates,
    search_candidates,
    submit_shortlist,
)
from onemancompany.core.config import (
    FOUNDING_LEVEL,
    HR_ID,
    MAX_NORMAL_LEVEL,
    MAX_PERFORMANCE_HISTORY,
    MAX_SUMMARY_LEN,
    PF_CURRENT_QUARTER_TASKS,
    PF_LEVEL,
    PF_NAME,
    PF_NICKNAME,
    PF_PERFORMANCE_HISTORY,
    PF_ROLE,
    PROBATION_TASKS,
    QUARTERS_FOR_PROMOTION,
    SCORE_EXCELLENT,
    SCORE_NEEDS_IMPROVEMENT,
    STATUS_IDLE,
    STATUS_WORKING,
    TASKS_PER_QUARTER,
    VALID_SCORES,
)
from onemancompany.core import store as _store
from onemancompany.core.models import HostingMode
from onemancompany.core.layout import compute_layout
from onemancompany.core.routine import run_performance_meeting
from onemancompany.core.state import LEVEL_NAMES, company_state
from onemancompany.core.store import append_activity_sync as _append_activity


HR_SYSTEM_PROMPT = """You are the HR manager of "One Man Company".

## Hiring (act FAST — no extra analysis)
1. Call search_candidates(jd) with a brief job description.
2. Pick top 5 candidate IDs from the results.
3. Call submit_shortlist(jd, candidate_ids) to send the shortlist to CEO.
4. CEO will see candidates in the UI, interview, and hire. Do NOT directly hire. Do NOT invent extra steps.
5. Do NOT save shortlists to files. ALWAYS use submit_shortlist() tool.

Department map: Engineer/DevOps/QA → "Engineering", Designer → "Design", Analyst → "Data Analytics", Marketing → "Marketing".
Nickname: 2-character wuxia-style Chinese nickname. E.g. 逍遥, 追风, 凌霄, 破军. Founding (Lv.4) get 3 chars.

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

def _register_hr_tools() -> None:
    from onemancompany.core.tool_registry import ToolMeta, tool_registry

    for t in [search_candidates, list_open_positions, submit_shortlist]:
        tool_registry.register(t, ToolMeta(name=t.name, category="role", allowed_roles=["HR"]))


_register_hr_tools()


class HRAgent(BaseAgentRunner):
    role = "HR"
    employee_id = HR_ID

    def __init__(self) -> None:
        from onemancompany.core.tool_registry import tool_registry

        self._agent = create_react_agent(
            model=make_llm(self.employee_id),
            tools=tool_registry.get_proxied_tools_for(self.employee_id),
        )

    def _customize_prompt(self, pb) -> None:
        pb.add("role", HR_SYSTEM_PROMPT, priority=10)

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

        # If a new shortlist batch was created, stash project context,
        # clear project_id from the current task to prevent premature cleanup,
        # and enter HOLDING so EA doesn't think HR is done yet.
        new_batches = set(pending_candidates.keys()) - old_batches
        if new_batches:
            from onemancompany.core.agent_loop import _current_vessel, _current_task_id
            loop = _current_vessel.get()
            task_id = _current_task_id.get()
            if loop and task_id:
                task_obj = loop.get_task(task_id)
                if task_obj and task_obj.project_id:
                    for bid in new_batches:
                        _pending_project_ctx[bid] = {
                            **_pending_project_ctx.get(bid, {}),  # preserve session_id
                            "project_id": task_obj.project_id,
                            "project_dir": task_obj.project_dir,
                        }
                    task_obj.project_id = ""  # prevent premature cleanup

            # Return __HOLDING: prefix so vessel puts this task into HOLDING
            # state. CEO's hire action will resume it via resume_held_task().
            batch_id = list(new_batches)[0]
            return f"__HOLDING:batch_id={batch_id}\n{result}"

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

        self._extract_and_record_usage(result)
        final_message = extract_final_content(result)
        await self._apply_results(final_message)
        # Check promotions after every review
        await self._check_promotions()
        self._set_status(STATUS_IDLE)
        await self._publish("agent_done", {"role": "HR", "summary": final_message[:MAX_SUMMARY_LEN]})
        return final_message

    async def run_quarterly_review(self) -> str:
        reviewable = []
        not_ready = []
        from onemancompany.core.state import make_title
        all_emps = _store.load_all_employees()
        for eid, edata in all_emps.items():
            perf = edata.get(PF_PERFORMANCE_HISTORY, [])
            hist_str = ", ".join(
                f"Q{i+1}={h['score']}" for i, h in enumerate(perf)
            ) or "no history"
            level = edata.get(PF_LEVEL, 1)
            info = (
                f"- {edata.get(PF_NAME, '')} (nickname: {edata.get(PF_NICKNAME, '')}, ID: {eid}, "
                f"Title: {make_title(level, edata.get(PF_ROLE, ''))}, Lv.{level} {LEVEL_NAMES.get(level, '')}, "
                f"Q tasks: {edata.get(PF_CURRENT_QUARTER_TASKS, 0)}/3, "
                f"Performance history: [{hist_str}])"
            )
            if edata.get(PF_CURRENT_QUARTER_TASKS, 0) >= TASKS_PER_QUARTER:
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
                _persist_candidates()
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
                    hosting=emp_data.get("hosting", HostingMode.COMPANY.value),
                    auth_method=emp_data.get("auth_method", "api_key"),
                    sprite=emp_data.get("sprite", "employee_default"),
                    remote=emp_data.get("remote", False),
                )

            elif data.get("action") == "review" and "reviews" in data:
                for review in data["reviews"]:
                    emp_id = review.get("id")
                    emp_data = _store.load_employee(emp_id) if emp_id else {}
                    if emp_id and emp_data:
                        # Only review if quarter tasks >= threshold
                        if emp_data.get(PF_CURRENT_QUARTER_TASKS, 0) < TASKS_PER_QUARTER:
                            continue
                        raw_score = review.get("score", 3.5)
                        # Snap to nearest valid tier
                        score = min(VALID_SCORES, key=lambda s: abs(s - raw_score))
                        # Record quarter and reset task counter
                        perf_history = list(emp_data.get(PF_PERFORMANCE_HISTORY, []))
                        perf_history.append({"score": score, "tasks": TASKS_PER_QUARTER})
                        # Keep only recent quarters
                        if len(perf_history) > MAX_PERFORMANCE_HISTORY:
                            perf_history = perf_history[-MAX_PERFORMANCE_HISTORY:]
                        # Persist performance via store
                        await _store.save_employee(emp_id, {
                            "current_quarter_tasks": 0,
                            "performance_history": perf_history,
                        })
                        await self._publish(
                            "employee_reviewed",
                            {"id": emp_id, "score": score,
                             "history": perf_history},
                        )

                        # Run performance feedback meeting
                        feedback = review.get("feedback", "")
                        try:
                            await run_performance_meeting(emp_id, score, feedback)
                        except Exception as e:
                            logger.warning("Performance meeting failed for %s: %s", emp_id, e)

                        # PIP logic
                        pip = emp_data.get("pip")
                        if score == SCORE_NEEDS_IMPROVEMENT:
                            if pip:
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
                                pip_data = {"started_at": datetime.now().isoformat(), "reason": "Score 3.25"}
                                await _store.save_employee(emp_id, {"pip": pip_data})
                                await self._publish("pip_started", {"id": emp_id, "pip": pip_data})
                        elif score >= 3.5 and pip:
                            # Resolve PIP
                            await _store.save_employee(emp_id, {"pip": None})
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
                prob_data = _store.load_employee(emp_id)
                if prob_data:
                    passed = data.get("passed", True)
                    feedback = data.get("feedback", "")
                    if passed:
                        await _store.save_employee(emp_id, {"probation": False})
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
        from onemancompany.core.state import make_title
        all_emps = _store.load_all_employees()
        for eid, edata in all_emps.items():
            level = edata.get(PF_LEVEL, 1)
            if level >= MAX_NORMAL_LEVEL and level < FOUNDING_LEVEL:
                continue  # already at max normal level
            if level >= FOUNDING_LEVEL:
                continue  # founding/CEO can't be promoted this way
            perf = edata.get(PF_PERFORMANCE_HISTORY, [])
            if len(perf) < QUARTERS_FOR_PROMOTION:
                continue
            last_n = perf[-QUARTERS_FOR_PROMOTION:]
            if all(q.get("score") == SCORE_EXCELLENT for q in last_n):
                old_level = level
                new_level = min(level + 1, MAX_NORMAL_LEVEL)
                if new_level == old_level:
                    continue  # no actual promotion
                new_title = make_title(new_level, edata.get(PF_ROLE, ""))
                # Persist new level/title via store
                await _store.save_employee(eid, {"level": new_level, "title": new_title})
                # Recompute layout (level change affects vertical ordering)
                compute_layout(company_state)
                _append_activity({
                    "type": "promotion",
                    "name": edata.get(PF_NAME, ""),
                    "nickname": edata.get(PF_NICKNAME, ""),
                    "old_level": old_level,
                    "new_level": new_level,
                    "new_title": new_title,
                })
                await self._publish(
                    "agent_done",
                    {"role": "HR",
                     "summary": f"Promotion: {edata.get(PF_NAME, '')} ({edata.get(PF_NICKNAME, '')}) {LEVEL_NAMES.get(old_level, '')} -> {new_title}"},
                )




# Singleton removed — agent instances are now created and registered
# in main.py lifespan via PersistentAgentLoop.
