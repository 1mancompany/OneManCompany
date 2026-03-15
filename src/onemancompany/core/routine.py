"""Company Routine — workflow-driven post-task meeting system.

After a task completes, this routine orchestrates meetings by dynamically
loading and executing workflow documents from business/workflows/.  Each workflow
.md file defines a sequence of stages that the engine parses and runs.

If no workflow document is found for a given routine, the system falls back
to the original hardcoded two-phase meeting behavior for backward compatibility.

The workflow engine reads markdown stage definitions and dispatches each step
to the appropriate handler based on the step owner (HR, COO, employees, etc.).
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import datetime
from typing import Any, Callable, Awaitable

import yaml

from onemancompany.agents.base import get_employee_skills_prompt, get_employee_tools_prompt, make_llm, tracked_ainvoke
from onemancompany.core.config import (
    CEO_ID,
    COO_ID,
    EA_ID,
    FOUNDING_LEVEL,
    HR_ID,
    MAX_PRINCIPLES_LEN,
    MAX_SUMMARY_LEN,
    MAX_WORKFLOW_CONTEXT_LEN,
    MEETING_REPORTS_DIR,
    STATUS_IDLE,
    STATUS_IN_MEETING,
    TASKS_PER_QUARTER,
    load_workflows,
)
from onemancompany.core.events import CompanyEvent, event_bus
from onemancompany.core.state import company_state
from onemancompany.core import store as _store
from onemancompany.core.store import load_employee, load_all_employees
from onemancompany.core.workflow_engine import (
    WorkflowDefinition,
    WorkflowStep,
    classify_step_owner,
    parse_workflow,
)

from loguru import logger

REPORTS_DIR = MEETING_REPORTS_DIR


# ---------------------------------------------------------------------------
# Shared helpers to reduce repetition across routine handlers
# ---------------------------------------------------------------------------

def _format_workflow_context(step: WorkflowStep) -> str:
    """Format step instructions into a workflow context block."""
    if not step.instructions:
        return ""
    lines = "\n".join(f"  {i+1}. {inst}" for i, inst in enumerate(step.instructions))
    return f"\n\n[Workflow Requirements for This Phase]\n{lines}\nPlease execute according to the above requirements.\n"


def _parse_json_array(text: str, fallback: list | None = None) -> list:
    """Extract a JSON array from LLM response text.

    Searches for [...] in the text, parses it, and returns the array.
    Falls back to the provided default if parsing fails.
    """
    if fallback is None:
        fallback = []
    try:
        match = re.search(r'\[.*\]', text, re.DOTALL)
        if match:
            return json.loads(match.group())
    except json.JSONDecodeError:
        logger.debug("Failed to parse JSON array from LLM response, using fallback")
    return fallback


async def _set_participants_status(participant_ids: list[str], status: str) -> None:
    """Set status for all participants (including hr/coo)."""
    for pid in participant_ids:
        await _store.save_employee_runtime(pid, status=status)

# Store pending reports that are waiting for CEO approval
pending_reports: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Event helpers (public — used by other modules)
# ---------------------------------------------------------------------------

async def _publish(event_type: str, payload: dict) -> None:
    await event_bus.publish(CompanyEvent(type=event_type, payload=payload, agent="ROUTINE"))


async def _chat(room_id: str, speaker: str, role: str, message: str) -> None:
    """Publish a meeting_chat event for real-time meeting chat display."""
    await _publish("meeting_chat", {
        "room_id": room_id,
        "speaker": speaker,
        "role": role,
        "message": message,
    })


# ---------------------------------------------------------------------------
# Step execution context — passed into every step handler
# ---------------------------------------------------------------------------

class StepContext:
    """Mutable context bag shared across all step handlers during a workflow run."""

    def __init__(
        self,
        task_summary: str,
        participants: list[str],
        room_id: str,
        workflow: WorkflowDefinition,
        meeting_doc: dict,
        project_record: dict | None = None,
    ) -> None:
        self.task_summary = task_summary
        self.participants = participants
        self.room_id = room_id
        self.workflow = workflow
        self.meeting_doc = meeting_doc
        self.project_record = project_record or {}  # Project audit trail for retrospective reference
        # Accumulate results from each step so later steps can reference earlier ones
        self.results: dict[str, Any] = {}
        # Accumulated data buckets (matching old structure)
        self.self_evaluations: list[dict] = []
        self.senior_reviews: list[dict] = []
        self.hr_summary: list[dict] = []
        self.coo_report: str = ""
        self.employee_feedback: list[dict] = []
        self.action_items: list[dict] = []
        self.asset_suggestions: list[dict] = []

    def format_project_timeline(self, max_entries: int = 20) -> str:
        """Format the project timeline as a readable string for LLM prompts."""
        timeline = self.project_record.get("timeline", [])
        if not timeline:
            return ""
        lines = []
        for entry in timeline[-max_entries:]:
            emp_id = entry.get("employee_id", "?")
            # Resolve name from store
            emp_data = load_employee(emp_id)
            name = f"{emp_data.get('name', emp_id)}({emp_data.get('nickname', '')})" if emp_data else emp_id
            action = entry.get("action", "")
            detail = entry.get("detail", "")[:200]
            lines.append(f"- [{name}] {action}: {detail}")
        return "\n".join(lines)

    def format_company_culture(self) -> str:
        """Format company culture items as a prompt section."""
        items = _store.load_culture()
        if not items:
            return ""
        rules = "\n".join(f"  {i+1}. {item.get('content', '')}" for i, item in enumerate(items))
        return f"\n\n## Company Culture (values and guidelines all employees must follow):\n{rules}\n"

    def get_employee_actions(self, emp_id: str) -> str:
        """Extract only the actions performed by a specific employee."""
        timeline = self.project_record.get("timeline", [])
        if not timeline:
            return "(No action records found for you in the project log)"
        lines = []
        for entry in timeline:
            if entry.get("employee_id") == emp_id:
                action = entry.get("action", "")
                detail = entry.get("detail", "")[:200]
                lines.append(f"- {action}: {detail}")
        if not lines:
            return "(No action records found for you in the project log)"
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Step handler type and registry
# ---------------------------------------------------------------------------

StepHandler = Callable[[WorkflowStep, StepContext], Awaitable[dict]]

# Maps a step keyword (from the title) to a handler function.
# The engine tries title-based matching first, then falls back to owner-based.
_STEP_HANDLERS_BY_TITLE: dict[str, StepHandler] = {}
_STEP_HANDLERS_BY_OWNER: dict[str, StepHandler] = {}


def _register_title_handler(keyword: str, handler: StepHandler) -> None:
    _STEP_HANDLERS_BY_TITLE[keyword] = handler


def _register_owner_handler(owner_type: str, handler: StepHandler) -> None:
    _STEP_HANDLERS_BY_OWNER[owner_type] = handler


# ---------------------------------------------------------------------------
# Individual step handler implementations
# ---------------------------------------------------------------------------

async def _handle_meeting_prep(step: WorkflowStep, ctx: StepContext) -> dict:
    """Handle the meeting preparation step (booking, notification)."""
    # This step is handled before the workflow loop in run_post_task_routine,
    # so if we reach here during dynamic execution, just acknowledge it.
    await _publish("routine_phase", {
        "phase": step.title,
        "message": "Meeting room is ready, participants have been notified"
    })
    return {"status": "prepared"}


async def _handle_self_evaluation(step: WorkflowStep, ctx: StepContext) -> dict:
    """Each participating employee self-evaluates their work."""
    llm = make_llm(HR_ID)

    workflow_ctx = _format_workflow_context(step)

    await _publish("routine_phase", {"phase": step.title, "message": "Employee self-evaluation started"})
    await _chat(ctx.room_id, "HR", "HR", f"{step.title} has begun. Please proceed with self-evaluations in turn.")

    # Format project timeline for context
    timeline_ctx = ""
    timeline_text = ctx.format_project_timeline()
    if timeline_text:
        timeline_ctx = f"\n\n[Project Log]\n{timeline_text}\n"

    for emp_id in ctx.participants:
        emp_data = load_employee(emp_id)
        if not emp_data:
            continue

        work_principles = emp_data.get("work_principles", "")
        principles_ctx = ""
        if work_principles:
            principles_ctx = f"\nYour work principles:\n{work_principles[:MAX_PRINCIPLES_LEN]}\n"

        skills_ctx = get_employee_skills_prompt(emp_id)
        tools_ctx = get_employee_tools_prompt(emp_id)

        my_actions = ctx.get_employee_actions(emp_id)

        culture_ctx = ctx.format_company_culture()

        emp_name = emp_data.get("name", "")
        emp_nickname = emp_data.get("nickname", "")
        emp_dept = emp_data.get("department", "")
        emp_level = emp_data.get("level", 1)
        emp_role = emp_data.get("role", "")

        prompt = (
            f"You are {emp_name} (nickname: {emp_nickname}, department: {emp_dept}, "
            f"level: Lv.{emp_level}, role: {emp_role}).\n"
            f"{principles_ctx}"
            f"{skills_ctx}"
            f"{tools_ctx}"
            f"{culture_ctx}"
            f"Recently completed task summary: {ctx.task_summary}\n"
            f"{timeline_ctx}\n"
            f"[Your Actual Action Records in This Project]\n{my_actions}\n\n"
            f"Important rules: You must only self-evaluate based on the 'Actual Action Records' above.\n"
            f"- Only mention what is in the records; never mention things you did not do\n"
            f"- If the records show you made no contributions, honestly say 'I made no substantial contribution to this project'\n"
            f"- No empty platitudes (e.g., 'actively cooperated', 'fully supported', 'efficiently collaborated'); only state specifics\n"
            f"- Do not fabricate, exaggerate, or embellish your work\n\n"
            f"Please provide an honest self-evaluation (2-3 sentences), including:\n"
            f"- What you specifically did (must correspond to entries in the action records)\n"
            f"- What the results were\n"
            f"- Whether there were any mistakes or areas for improvement\n"
            f"{workflow_ctx}"
        )
        resp = await tracked_ainvoke(llm, prompt, category="routine", employee_id=emp_id)
        eval_text = resp.content
        ctx.self_evaluations.append({
            "employee_id": emp_id,
            "name": emp_name,
            "nickname": emp_nickname,
            "level": emp_level,
            "evaluation": eval_text,
        })
        display = emp_nickname or emp_name
        await _chat(ctx.room_id, display, emp_role, eval_text)

    await _publish("routine_phase", {"phase": step.title, "message": "Employee self-evaluation completed"})
    return {"self_evaluations": ctx.self_evaluations}


async def _handle_senior_review(step: WorkflowStep, ctx: StepContext) -> dict:
    """Higher-level employees review lower-level employees' work."""
    llm = make_llm(HR_ID)

    await _publish("routine_phase", {"phase": step.title, "message": "Senior employees begin peer review"})

    # Load participant data from store and sort by level
    participant_data: list[tuple[str, dict]] = []
    for eid in ctx.participants:
        edata = load_employee(eid)
        if edata:
            participant_data.append((eid, edata))
    participant_data.sort(key=lambda x: x[1].get("level", 1), reverse=True)

    for senior_id, senior_data in participant_data:
        senior_level = senior_data.get("level", 1)
        juniors = [(jid, jd) for jid, jd in participant_data if jd.get("level", 1) < senior_level and jid != senior_id]
        if not juniors:
            continue

        junior_info = "\n".join(
            f"- {jd.get('name', '')}（{jd.get('nickname', '')}，Lv.{jd.get('level', 1)}）: "
            + next(
                (se["evaluation"] for se in ctx.self_evaluations if se["employee_id"] == jid),
                "No self-evaluation",
            )
            for jid, jd in juniors
        )

        workflow_ctx = _format_workflow_context(step)

        timeline_ctx = ""
        timeline_text = ctx.format_project_timeline()
        if timeline_text:
            timeline_ctx = f"\n\n[Project Log]\n{timeline_text}\n"

        culture_ctx = ctx.format_company_culture()

        prompt = (
            f"You are {senior_data.get('name', '')} (nickname: {senior_data.get('nickname', '')}, Lv.{senior_level}, {senior_data.get('role', '')}).\n"
            f"{culture_ctx}"
            f"Task summary: {ctx.task_summary}\n"
            f"{timeline_ctx}\n"
            f"Below are the self-evaluations from junior colleagues:\n{junior_info}\n\n"
            f"Important rules: Your review must be strictly based on facts from the project log.\n"
            f"- Only evaluate the specific performance of employees who have actual actions in the project log\n"
            f"- If someone has no substantial contribution in the log, directly state 'This colleague made no substantial contribution to this project'\n"
            f"- If someone's self-evaluation does not match the project log (exaggerated, fabricated), you must point it out\n"
            f"- No empty platitudes (e.g., 'performed actively', 'commendable'); only state specific facts\n\n"
            f"Please provide a brief review for each junior colleague (1-2 sentences each), focusing on:\n"
            f"- What they actually did (cross-reference with project log)\n- Work effectiveness\n- Whether their self-evaluation is accurate\n"
            f"Please respond in JSON array format: [{{'name': '...', 'review': '...'}}]"
            f"{workflow_ctx}"
        )
        resp = await tracked_ainvoke(llm, prompt, category="routine", employee_id=senior_id)
        review_text = resp.content

        reviews = _parse_json_array(review_text, [{"name": "all", "review": review_text}])

        ctx.senior_reviews.append({
            "reviewer": senior_data.get("name", ""),
            "reviewer_level": senior_level,
            "reviews": reviews,
        })
        display = senior_data.get("nickname", "") or senior_data.get("name", "")
        review_summary = "; ".join(
            f"{r.get('name','')}: {r.get('review','')[:60]}" for r in reviews
        )
        await _chat(ctx.room_id, display, senior_data.get("role", ""), f"[Peer Review] {review_summary}")

    await _publish("routine_phase", {"phase": step.title, "message": "Peer review completed"})
    return {"senior_reviews": ctx.senior_reviews}


async def _handle_hr_summary(step: WorkflowStep, ctx: StepContext) -> dict:
    """HR summarizes improvement points per employee."""
    llm = make_llm(HR_ID)

    await _publish("routine_phase", {"phase": step.title, "message": "HR is summarizing improvement points"})

    workflow_ctx = _format_workflow_context(step)

    all_evals = "\n".join(
        f"[{se['name']}(Lv.{se['level']})] Self-eval: {se['evaluation']}"
        for se in ctx.self_evaluations
    )
    all_reviews = "\n".join(
        f"[{sr['reviewer']} review] " + "; ".join(
            f"{r.get('name','')}: {r.get('review','')}" for r in sr["reviews"]
        )
        for sr in ctx.senior_reviews
    )

    timeline_ctx = ""
    timeline_text = ctx.format_project_timeline()
    if timeline_text:
        timeline_ctx = f"\n\n[Project Log]\n{timeline_text}\n"

    culture_ctx = ctx.format_company_culture()

    hr_prompt = (
        f"You are the HR manager, responsible for summarizing this review meeting.\n"
        f"{culture_ctx}"
        f"Task summary: {ctx.task_summary}\n"
        f"{timeline_ctx}\n"
        f"Employee self-evaluations:\n{all_evals}\n\n"
        f"Senior employee peer reviews:\n{all_reviews}\n\n"
        f"Important rules: The summary must be based on objective facts from the project log.\n"
        f"- Cross-check each employee's self-evaluation against the project log\n"
        f"- If someone's self-evaluation does not match the log (exaggerated, fabricated), clearly note 'inaccurate self-evaluation' in the improvement points\n"
        f"- Improvement suggestions must be specific and actionable; no empty platitudes\n\n"
        f"Based on the project log and review content, summarize specific improvement points for each employee (1-3 items per person), "
        f"and respond in JSON array format:\n"
        f'[{{"employee": "...", "improvements": ["improvement 1", "improvement 2"]}}]'
        f"{workflow_ctx}"
    )
    resp = await tracked_ainvoke(llm, hr_prompt, category="routine", employee_id=HR_ID)
    hr_text = resp.content

    improvements = _parse_json_array(hr_text, [{"employee": "all", "improvements": [hr_text]}])

    ctx.hr_summary = improvements

    # Broadcast HR summary as chat
    hr_msg = "; ".join(
        f"{it.get('employee','')}: {', '.join(it.get('improvements',[]))[:60]}"
        for it in improvements
    )
    await _chat(ctx.room_id, "HR", "HR", f"[Summary] {hr_msg}")

    await _publish("routine_phase", {
        "phase": step.title,
        "message": "HR review meeting summary completed"
    })
    return {"hr_summary": improvements}


async def _handle_coo_report(step: WorkflowStep, ctx: StepContext) -> dict:
    """COO produces a company operations report."""
    llm = make_llm(COO_ID)

    await _publish("routine_phase", {"phase": step.title, "message": "COO is producing operations report"})

    workflow_ctx = _format_workflow_context(step)

    emp_count = len(load_all_employees())
    tool_count = len(company_state.tools)
    room_count = len(_store.load_rooms())

    timeline_ctx = ""
    timeline_text = ctx.format_project_timeline()
    if timeline_text:
        timeline_ctx = f"\n\n[Project Log]\n{timeline_text}\n"

    culture_ctx = ctx.format_company_culture()

    # Build cost context from project record
    cost_ctx = ""
    if ctx.project_record:
        cost_data = ctx.project_record.get("cost", {})
        if cost_data and (cost_data.get("actual_cost_usd", 0) > 0 or cost_data.get("budget_estimate_usd", 0) > 0):
            budget = cost_data.get("budget_estimate_usd", 0)
            actual = cost_data.get("actual_cost_usd", 0)
            tokens = cost_data.get("token_usage", {})
            breakdown = cost_data.get("breakdown", [])
            cost_lines = [f"Budget: ${budget:.4f}, Actual: ${actual:.4f}"]
            cost_lines.append(f"Token usage: input={tokens.get('input', 0)}, output={tokens.get('output', 0)}")
            for entry in breakdown:
                emp_data = load_employee(entry.get("employee_id", ""))
                name = emp_data.get("name", entry.get("employee_id", "?")) if emp_data else entry.get("employee_id", "?")
                cost_lines.append(f"  - {name}: {entry.get('model', '?')}, {entry.get('total_tokens', 0)} tokens, ${entry.get('cost_usd', 0):.4f}")
            cost_ctx = "\n\nProject cost data:\n" + "\n".join(cost_lines) + "\n"

    coo_prompt = (
        f"You are the COO, responsible for producing a company operations report.\n"
        f"{culture_ctx}"
        f"Recently completed task: {ctx.task_summary}\n"
        f"{timeline_ctx}"
        f"{cost_ctx}"
        f"The company currently has {emp_count} employees, {tool_count} pieces of equipment, and {room_count} meeting rooms.\n\n"
        f"Important rules: The report must be strictly based on objective facts from the project log.\n"
        f"- Only state situations that are verifiable in the project log; do not fabricate or embellish\n"
        f"- No empty platitudes; use specific data and facts\n\n"
        f"Based on the project log, briefly summarize the current company operations (3-5 sentences), including:\n"
        f"- Project completion status (who did what, how effective)\n- Resource utilization\n- Potential risks\n"
        f"- Project cost analysis (if data available), assess whether budget was exceeded\n"
        f"{workflow_ctx}"
    )
    resp = await tracked_ainvoke(llm, coo_prompt, category="routine", employee_id=COO_ID)
    ctx.coo_report = resp.content
    await _chat(ctx.room_id, "COO", "COO", ctx.coo_report)

    await _publish("routine_phase", {"phase": step.title, "message": "COO report completed"})
    return {"coo_report": ctx.coo_report}


async def _handle_asset_consolidation(step: WorkflowStep, ctx: StepContext) -> dict:
    """COO reviews project workspace files and suggests assets worth preserving."""
    from onemancompany.core.project_archive import list_project_files, get_project_dir

    project_id = ctx.project_record.get("id", "") or ctx.project_record.get("project_id", "")
    if not project_id:
        await _chat(ctx.room_id, "COO", "COO", "[Asset Consolidation] No project ID, skipping asset consolidation.")
        return {"asset_suggestions": []}

    await _publish("routine_phase", {"phase": step.title, "message": "COO is reviewing project deliverables"})

    files = list_project_files(project_id)
    if not files:
        await _chat(ctx.room_id, "COO", "COO", "[Asset Consolidation] No files in project workspace, skipping.")
        return {"asset_suggestions": []}

    project_dir = get_project_dir(project_id)
    file_list_text = "\n".join(f"- {f}" for f in files)

    workflow_ctx = _format_workflow_context(step)

    llm = make_llm(COO_ID)
    prompt = (
        f"You are the COO, responsible for reviewing project deliverables and determining which are worth preserving as company assets.\n\n"
        f"Project summary: {ctx.task_summary}\n"
        f"Project workspace: {project_dir}\n\n"
        f"Project file list:\n{file_list_text}\n\n"
        f"Please review the above files and determine which are worth registering as company assets (tools, templates, reference code, etc.).\n"
        f"Evaluation criteria:\n"
        f"- Has reuse value (can be used by other projects)\n"
        f"- Is a standalone tool, script, or template\n"
        f"- Is not a temporary file, log, or configuration file\n\n"
        f"If no files are worth preserving, return an empty array [].\n"
        f"Otherwise, return suggestions in JSON array format:\n"
        f'[{{"name": "asset name", "description": "brief description of purpose", "files": ["file1.py", "file2.md"]}}]\n'
        f"Only return the JSON array, no other content.{workflow_ctx}"
    )
    resp = await tracked_ainvoke(llm, prompt, category="routine", employee_id=COO_ID)
    raw = resp.content

    suggestions = _parse_json_array(raw)

    ctx.asset_suggestions = suggestions

    if suggestions:
        names = ", ".join(s.get("name", "?") for s in suggestions)
        await _chat(ctx.room_id, "COO", "COO", f"[Asset Consolidation Suggestions] {names}")
    else:
        await _chat(ctx.room_id, "COO", "COO", "[Asset Consolidation] No assets to preserve from this project.")

    await _publish("routine_phase", {"phase": step.title, "message": "Asset consolidation review completed"})
    return {"asset_suggestions": suggestions}


async def _handle_employee_open_floor(step: WorkflowStep, ctx: StepContext) -> dict:
    """Employee open discussion — everyone speaks freely."""
    llm = make_llm(HR_ID)

    await _publish("routine_phase", {"phase": step.title, "message": "Employee open floor started"})

    workflow_ctx = _format_workflow_context(step)

    for emp_id in ctx.participants:
        emp_data = load_employee(emp_id)
        if not emp_data:
            continue

        work_principles = emp_data.get("work_principles", "")
        principles_ctx = ""
        if work_principles:
            principles_ctx = f"\nYour work principles:\n{work_principles[:MAX_PRINCIPLES_LEN]}\n"

        skills_ctx = get_employee_skills_prompt(emp_id)
        tools_ctx = get_employee_tools_prompt(emp_id)

        timeline_ctx = ""
        timeline_text = ctx.format_project_timeline()
        if timeline_text:
            timeline_ctx = f"\n\n[Project Log]\n{timeline_text}\n"

        my_actions = ctx.get_employee_actions(emp_id)

        culture_ctx = ctx.format_company_culture()

        emp_name = emp_data.get("name", "")
        emp_nickname = emp_data.get("nickname", "")
        emp_dept = emp_data.get("department", "")
        emp_role = emp_data.get("role", "")
        emp_level = emp_data.get("level", 1)

        prompt = (
            f"You are {emp_name} ({emp_nickname}, department: {emp_dept}, "
            f"{emp_role}, Lv.{emp_level}).\n"
            f"{principles_ctx}"
            f"{skills_ctx}"
            f"{tools_ctx}"
            f"{culture_ctx}"
            f"Task summary: {ctx.task_summary}\n"
            f"{timeline_ctx}"
            f"[Your Actual Action Records in This Project]\n{my_actions}\n\n"
            f"Important rules: Your remarks must be based on your actual action records; do not make up stories.\n"
            f"- Only discuss things you actually experienced and that are verifiable in the records\n"
            f"- No empty platitudes; do not fabricate difficulties or exaggerate contributions\n\n"
            f"This is the open floor session of the meeting. Based on your actual experience, you may raise:\n"
            f"- Actual difficulties encountered during work\n"
            f"- Missing tools or equipment\n"
            f"- What kind of talent is needed\n"
            f"- Any other suggestions\n"
            f"Please speak briefly (2-3 sentences).{workflow_ctx}"
        )
        resp = await tracked_ainvoke(llm, prompt, category="routine", employee_id=emp_id)
        feedback_content = resp.content
        ctx.employee_feedback.append({
            "employee_id": emp_id,
            "name": emp_name,
            "feedback": feedback_content,
        })
        display = emp_nickname or emp_name
        await _chat(ctx.room_id, display, emp_role, feedback_content)

    await _publish("routine_phase", {"phase": step.title, "message": "Open floor concluded"})
    return {"employee_feedback": ctx.employee_feedback}


async def _handle_action_plan(step: WorkflowStep, ctx: StepContext) -> dict:
    """COO + HR summarize action items from the meeting."""
    llm = make_llm(COO_ID)

    await _publish("routine_phase", {"phase": step.title, "message": "COO and HR are compiling the action plan"})

    workflow_ctx = _format_workflow_context(step)

    feedback_text = "\n".join(
        f"[{f['name']}] {f['feedback']}" for f in ctx.employee_feedback
    )
    phase1_improvements = "\n".join(
        f"[{item.get('employee','')}] " + ", ".join(item.get("improvements", []))
        for item in ctx.hr_summary
    )

    action_prompt = (
        f"You represent both COO and HR in compiling the meeting action plan.\n\n"
        f"COO operations report: {ctx.coo_report}\n\n"
        f"Employee remarks:\n{feedback_text}\n\n"
        f"Review improvement suggestions:\n{phase1_improvements}\n\n"
        f"Please compile into specific action items, each indicating who is responsible (HR/COO), "
        f"and respond in JSON array format:\n"
        f'[{{"source": "HR/COO", "description": "specific action", "priority": "high/medium/low"}}]'
        f"{workflow_ctx}"
    )
    resp = await tracked_ainvoke(llm, action_prompt, category="routine", employee_id=COO_ID)
    action_text = resp.content

    action_items = _parse_json_array(
        action_text, [{"source": "COO", "description": action_text, "priority": "medium"}]
    )

    # Merge asset consolidation suggestions as action items
    project_id = ctx.project_record.get("id", "") or ctx.project_record.get("project_id", "")
    if ctx.asset_suggestions and project_id:
        from onemancompany.core.project_archive import get_project_dir
        project_dir = get_project_dir(project_id)
        for suggestion in ctx.asset_suggestions:
            action_items.append({
                "type": "asset_consolidation",
                "source": "COO",
                "description": f"Consolidate project asset: {suggestion.get('name', '')} — {suggestion.get('description', '')}",
                "priority": "medium",
                "name": suggestion.get("name", ""),
                "asset_description": suggestion.get("description", ""),
                "project_dir": project_dir,
                "files": suggestion.get("files", []),
            })

    ctx.action_items = action_items

    actions_msg = "; ".join(
        f"[{a.get('source','')}] {a.get('description','')[:50]}"
        for a in action_items[:5]
    )
    await _chat(ctx.room_id, "COO+HR", "COO", f"[Action Plan] {actions_msg}")

    return {"action_items": action_items}


async def _handle_ea_approval(step: WorkflowStep, ctx: StepContext) -> dict:
    """EA reviews and approves meeting action items on behalf of CEO."""
    from onemancompany.core.agent_loop import get_agent_loop

    if not ctx.action_items:
        await _publish("routine_phase", {
            "phase": step.title,
            "message": "No action items pending approval, skipping EA approval"
        })
        await _chat(ctx.room_id, "EA", "EA", "No action items requiring approval in this meeting.")
        return {"status": "no_actions", "approved": [], "rejected": [], "skipped_duplicates": []}

    # Dedup: filter out items already proposed in past meetings
    unique_items, dup_items, recurring_items = _dedup_action_items(ctx.action_items)

    if dup_items:
        dup_descs = "; ".join(d.get("description", "")[:40] for d in dup_items)
        await _chat(ctx.room_id, "EA", "EA",
                    f"[Dedup] Skipping {len(dup_items)} previously proposed improvements: {dup_descs}")
        await _publish("routine_phase", {
            "phase": step.title,
            "message": f"Dedup skipped {len(dup_items)} duplicate improvements"
        })

    # Recurring items (proposed 2+ times before) — escalate to CEO
    if recurring_items:
        recurring_descs = "\n".join(f"  - {r.get('description', '')[:80]}" for r in recurring_items)
        await _chat(ctx.room_id, "EA", "EA",
                    f"[Warning] The following {len(recurring_items)} improvements have been proposed multiple times without resolution, requiring CEO attention:\n{recurring_descs}")
        await _publish("recurring_action_items", {
            "items": [r.get("description", "") for r in recurring_items],
            "message": f"{len(recurring_items)} improvements keep recurring and may not be resolvable through normal means; CEO decision needed",
        })

    if not unique_items:
        await _chat(ctx.room_id, "EA", "EA", "All improvement items have been proposed in previous meetings; no new action plans.")
        return {
            "status": "all_duplicates",
            "approved": [],
            "rejected": [],
            "skipped_duplicates": [d.get("description", "") for d in dup_items],
            "recurring_escalated": [r.get("description", "") for r in recurring_items],
        }

    # Update action_items to only contain unique items for EA review
    ctx.action_items = unique_items

    llm = make_llm(EA_ID)

    items_text = "\n".join(
        f"  {i+1}. [{a.get('source', '')}] {a.get('description', '')} (priority: {a.get('priority', '')})"
        for i, a in enumerate(unique_items)
    )

    workflow_ctx = _format_workflow_context(step)

    prompt = (
        "You are the EA (Executive Assistant), strictly reviewing meeting action plans on behalf of the CEO.\n\n"
        "Core approval principle: Fewer, more precise and critical improvements are better than many.\n"
        "The CEO's top priority is improving organizational efficiency; any action that does not directly serve this goal should be rejected.\n\n"
        f"Meeting summary: {ctx.task_summary}\n\n"
        f"COO operations report: {ctx.coo_report}\n\n"
        f"Action plans pending review:\n{items_text}\n\n"
        "Strict review criteria (ALL must be met for approval):\n"
        "1. Specifically actionable: Has clear execution steps, not vague platitudes (e.g., 'strengthen management', 'improve efficiency', 'optimize processes' — reject all such vague statements)\n"
        "2. Directly relevant: Must be directly related to actual issues in this project, not generic advice\n"
        "3. Measurable results: Can clearly see results after execution, with criteria for judging success/failure\n"
        "4. Reasonable ROI: Benefits from improvement must outweigh execution costs\n"
        "5. No duplication or contradiction: Must not duplicate or contradict other action items\n\n"
        "Typical examples that should be rejected:\n"
        "- 'Strengthen code review process' — too vague, how to strengthen? What specifically to do?\n"
        "- 'Improve team collaboration capability' — empty rhetoric, no concrete action\n"
        "- 'Optimize project management mechanism' — bureaucratic, doesn't solve specific problems\n"
        "- Generic improvement suggestions unrelated to this project\n\n"
        "Review strictly; better to approve fewer than too many. Return your decision in JSON format:\n"
        '{"approved_indices": [0, 1, ...], "rejected_indices": [2, ...], "reason": "review notes"}\n'
        "approved_indices are the action numbers you approve (0-based), rejected_indices are the ones you reject.\n"
        f"Only return JSON, no other content.{workflow_ctx}"
    )

    await _publish("routine_phase", {
        "phase": step.title,
        "message": "EA is reviewing the action plan"
    })

    resp = await tracked_ainvoke(llm, prompt, category="routine", employee_id=EA_ID)
    raw = resp.content

    # Parse EA decision
    approved_indices: list[int] = []
    rejected_indices: list[int] = []
    ea_reason = ""
    try:
        json_match = re.search(r'\{.*\}', raw, re.DOTALL)
        if json_match:
            decision = json.loads(json_match.group())
            approved_indices = decision.get("approved_indices", [])
            rejected_indices = decision.get("rejected_indices", [])
            ea_reason = decision.get("reason", "")
        else:
            # If EA can't produce JSON, approve all by default
            approved_indices = list(range(len(ctx.action_items)))
            ea_reason = "EA did not return valid JSON, defaulting to approve all"
    except json.JSONDecodeError:
        approved_indices = list(range(len(ctx.action_items)))
        ea_reason = "EA returned invalid format, defaulting to approve all"

    approved = [ctx.action_items[i] for i in approved_indices if i < len(ctx.action_items)]
    rejected = [ctx.action_items[i] for i in rejected_indices if i < len(ctx.action_items)]

    # Chat announcement
    await _chat(ctx.room_id, "EA", "EA",
                f"[Approval Result] Approved {len(approved)} items, rejected {len(rejected)} items. {ea_reason}")

    if not approved:
        await _publish("routine_phase", {
            "phase": step.title,
            "message": "EA did not approve any action plans"
        })
        return {"status": "none_approved", "approved": [], "rejected": rejected, "reason": ea_reason}

    # Execute approved actions directly

    # 1. Handle asset consolidation actions
    from onemancompany.agents.coo_agent import register_asset
    asset_results = []
    remaining_actions = []
    for a in approved:
        if a.get("type") == "asset_consolidation":
            result = register_asset.invoke({
                "name": a.get("name", ""),
                "description": a.get("asset_description", a.get("description", "")),
                "source_project_dir": a.get("project_dir", ""),
                "source_files": a.get("files", []),
            })
            asset_results.append(result)
            logger.info("Asset registered: %s -> %s", a.get("name"), result)
        else:
            remaining_actions.append(a)

    if asset_results:
        await _publish("routine_phase", {
            "phase": "Asset Consolidation",
            "message": f"Registered {len(asset_results)} company assets"
        })

    # 2. Push remaining actions to COO for dispatch
    if remaining_actions:
        action_lines = []
        for a in remaining_actions:
            source = a.get("source", "COO")
            action_lines.append(f"- [{source}] {a['description']}")

        coo_task = (
            "EA has approved the following action plan. Please assign execution based on the source field:\n"
            "- source=HR actions: Use dispatch_child() to assign to HR (employee_id='00002')\n"
            "- source=COO actions: Execute yourself\n\n"
            "Action plan:\n" + "\n".join(action_lines)
        )

        coo_loop = get_agent_loop(COO_ID)
        if coo_loop:
            coo_loop.push_task(coo_task)
            await _chat(ctx.room_id, "EA", "EA",
                        f"Pushed {len(remaining_actions)} approved actions to COO task board")

    await _publish("routine_phase", {
        "phase": step.title,
        "message": f"EA approval completed: approved {len(approved)} items, rejected {len(rejected)} items"
    })

    return {
        "status": "ea_approved",
        "approved": [a.get("description", "") for a in approved],
        "rejected": [a.get("description", "") for a in rejected],
        "skipped_duplicates": [d.get("description", "") for d in dup_items],
        "recurring_escalated": [r.get("description", "") for r in recurring_items],
        "asset_results": asset_results,
        "reason": ea_reason,
    }


async def _handle_generic_step(step: WorkflowStep, ctx: StepContext) -> dict:
    """Fallback handler for steps that do not match any specific handler.

    Uses LLM to produce a summary based on the step definition.
    """
    llm = make_llm(HR_ID)

    step_instructions = "\n".join(f"  {i+1}. {inst}" for i, inst in enumerate(step.instructions))

    prompt = (
        f"You are the company meeting facilitator. Currently executing a workflow step:\n\n"
        f"Step: {step.title}\n"
        f"Responsible: {step.owner}\n"
        f"Specific requirements:\n{step_instructions}\n"
        f"Expected output: {step.output_description}\n\n"
        f"Task background: {ctx.task_summary}\n\n"
        f"Please briefly summarize the key execution points for this step (2-3 sentences)."
    )
    resp = await tracked_ainvoke(llm, prompt, category="routine", employee_id=HR_ID)

    await _publish("routine_phase", {"phase": step.title, "message": resp.content[:200]})
    await _chat(ctx.room_id, step.owner or "Facilitator", "HR", resp.content)

    return {"generic_output": resp.content}


# ---------------------------------------------------------------------------
# Register handlers — title-keyword matching (checked first, more specific)
# ---------------------------------------------------------------------------

# Title keywords map to specific handlers for project retrospective workflow steps
_register_title_handler("Review Preparation", _handle_meeting_prep)
_register_title_handler("Self-Evaluation", _handle_self_evaluation)
_register_title_handler("Senior Peer Review", _handle_senior_review)
_register_title_handler("Peer Review", _handle_senior_review)
_register_title_handler("HR Summary", _handle_hr_summary)
_register_title_handler("Summary", _handle_hr_summary)
_register_title_handler("COO Operations Report", _handle_coo_report)
_register_title_handler("Operations Report", _handle_coo_report)
_register_title_handler("Asset Consolidation", _handle_asset_consolidation)
_register_title_handler("Employee Open Floor", _handle_employee_open_floor)
_register_title_handler("Open Floor", _handle_employee_open_floor)
_register_title_handler("Action Plan", _handle_action_plan)
_register_title_handler("CEO Approval", _handle_ea_approval)
_register_title_handler("EA Approval", _handle_ea_approval)
_register_title_handler("Approval", _handle_ea_approval)

# Owner-based fallback handlers
_register_owner_handler("employees", _handle_self_evaluation)
_register_owner_handler("senior", _handle_senior_review)
_register_owner_handler("coo_hr", _handle_action_plan)
_register_owner_handler("ceo", _handle_ea_approval)


# ---------------------------------------------------------------------------
# Step dispatcher
# ---------------------------------------------------------------------------

def _resolve_handler(step: WorkflowStep) -> StepHandler:
    """Find the best handler for a workflow step.

    1. Try matching title keywords (most specific).
    2. Try matching owner type (less specific).
    3. Fall back to generic handler.
    """
    # Title keyword matching
    for keyword, handler in _STEP_HANDLERS_BY_TITLE.items():
        if keyword in step.title:
            return handler

    # Owner-based matching
    owner_type = classify_step_owner(step.owner)
    if owner_type in _STEP_HANDLERS_BY_OWNER:
        return _STEP_HANDLERS_BY_OWNER[owner_type]

    return _handle_generic_step


# ---------------------------------------------------------------------------
# Main workflow execution
# ---------------------------------------------------------------------------

async def _run_workflow(workflow: WorkflowDefinition, ctx: StepContext) -> dict:
    """Execute all steps in a parsed workflow definition sequentially.

    Each step is dispatched to the appropriate handler. Results are accumulated
    in the StepContext.
    """
    all_step_results: dict[str, dict] = {}

    for step in workflow.steps:
        handler = _resolve_handler(step)
        logger.info("Workflow [%s] executing step %d: %s (handler=%s)",
                     workflow.name, step.index, step.title, handler.__name__)

        await _publish("routine_phase", {
            "phase": step.title,
            "message": f"Starting execution: {step.title}"
        })

        result = await handler(step, ctx)
        all_step_results[step.title] = result
        ctx.results[step.title] = result

    return all_step_results


# ---------------------------------------------------------------------------
# Auto-trigger HR review when employee hits TASKS_PER_QUARTER
# ---------------------------------------------------------------------------

def _auto_trigger_hr_review(employee_id: str) -> None:
    """Push an HR review task when an employee reaches the quarterly task threshold."""
    try:
        from onemancompany.api.routes import _push_adhoc_task
        emp_data = load_employee(employee_id)
        if not emp_data:
            return
        from onemancompany.core.state import LEVEL_NAMES, make_title
        perf = emp_data.get("performance_history", [])
        hist_str = ", ".join(
            f"Q{i+1}={h['score']}" for i, h in enumerate(perf)
        ) or "no history"
        level = emp_data.get("level", 1)
        info = (
            f"- {emp_data.get('name', '')} (nickname: {emp_data.get('nickname', '')}, ID: {employee_id}, "
            f"Title: {make_title(level, emp_data.get('role', ''))}, Lv.{level} {LEVEL_NAMES.get(level, '')}, "
            f"Q tasks: {emp_data.get('current_quarter_tasks', 0)}/3, "
            f"Performance history: [{hist_str}])"
        )
        review_task = (
            f"Run a performance review for employee {employee_id} who has completed {TASKS_PER_QUARTER} tasks this quarter.\n\n"
            f"Employee ready for review:\n{info}\n\n"
            f"Give a score of 3.25, 3.5, or 3.75 based on their work quality."
        )
        _push_adhoc_task(HR_ID, review_task)
        logger.info("Auto-triggered HR review for employee {}", employee_id)
    except Exception as e:
        logger.warning("Failed to auto-trigger HR review for {}: {}", employee_id, e)


# ---------------------------------------------------------------------------
# Public API — run_post_task_routine (refactored to be workflow-driven)
# ---------------------------------------------------------------------------

async def run_post_task_routine(
    task_summary: str,
    participants: list[str] | None = None,
    project_id: str = "",
) -> None:
    """Run the full post-task routine. Called after a task completes.

    Dynamically loads and executes the project_retrospective_workflow from business/workflows/.
    Falls back to the hardcoded two-phase meeting if no workflow document exists.
    """
    all_emps = load_all_employees()
    if not all_emps:
        return

    if participants is None:
        participants = list(all_emps.keys())

    # Load project record for retrospective reference
    project_record: dict = {}
    if project_id:
        from onemancompany.core.project_archive import load_project
        project_record = load_project(project_id) or {}

        # Filter participants to only actual contributors (those with timeline entries)
        actual_contributors = {
            entry["employee_id"]
            for entry in project_record.get("timeline", [])
            if entry.get("employee_id")
        }
        if actual_contributors:
            # EA always attends (dispatched the task, needs full context).
            # Everyone else only joins if they actually contributed.
            actual_contributors.add(EA_ID)
            participants = [pid for pid in participants if pid in actual_contributors]

    # Increment current_quarter_tasks for participating normal employees
    for pid in participants:
        emp_data = load_employee(pid)
        if emp_data and emp_data.get("level", 1) < FOUNDING_LEVEL:  # only track for normal employees
            new_count = emp_data.get("current_quarter_tasks", 0) + 1
            perf_history = emp_data.get("performance_history", [])
            await _store.save_employee(pid, {
                "current_quarter_tasks": new_count,
                "performance_history": perf_history,
            })
            # Auto-trigger HR review when employee hits the quarterly threshold
            if new_count >= TASKS_PER_QUARTER:
                _auto_trigger_hr_review(pid)

    # Retrospective meeting requires 2+ people — solo tasks skip the meeting
    if len(participants) < 2:
        return

    # Load workflow documents
    workflows = load_workflows()
    workflow_doc = workflows.get("project_retrospective_workflow", "")

    # If no workflow document, fall back to hardcoded behavior
    if not workflow_doc:
        await _run_post_task_routine_fallback(task_summary, participants)
        return

    # Parse the workflow into structured steps
    workflow = parse_workflow("project_retrospective_workflow", workflow_doc)
    if not workflow.steps:
        # Malformed document — fall back
        await _run_post_task_routine_fallback(task_summary, participants)
        return

    report_id = str(uuid.uuid4())[:8]
    meeting_doc: dict = {
        "id": report_id,
        "timestamp": datetime.now().isoformat(),
        "task_summary": task_summary,
        "participants": participants,
        "workflow": workflow.name,
        "workflow_flow_id": workflow.flow_id,
        "steps": {},
        "phase1": {},
        "phase2": {},
        "action_items": [],
    }

    # ===== Book a meeting room (always the first operational step) =====
    await _publish("routine_phase", {"phase": "Preparation", "message": "HR is requesting a meeting room from COO..."})

    room = None
    for r in company_state.meeting_rooms.values():
        if not r.is_booked:
            room_participants = list(dict.fromkeys(participants + [EA_ID]))
            r.is_booked = True
            r.booked_by = HR_ID
            r.participants = room_participants
            await _store.save_room(r.id, {
                "is_booked": True,
                "booked_by": HR_ID,
                "participants": room_participants,
            })
            room = r
            break

    if not room:
        await _publish("routine_phase", {
            "phase": "Preparation",
            "message": "No available meeting rooms. Meeting postponed. Employees continue with current work."
        })
        return

    await _publish("meeting_booked", {
        "room_id": room.id,
        "room_name": room.name,
        "participants": room.participants,
    })
    await _set_participants_status(room.participants, STATUS_IN_MEETING)

    try:
        # Create the execution context (with project record for retrospective)
        ctx = StepContext(
            task_summary=task_summary,
            participants=participants,
            room_id=room.id,
            workflow=workflow,
            meeting_doc=meeting_doc,
            project_record=project_record,
        )

        # Execute workflow steps dynamically (skip the first "preparation" step since
        # room booking was already handled above)
        steps_to_run = workflow.steps
        if steps_to_run and ("Preparation" in steps_to_run[0].title or "Prep" in steps_to_run[0].title):
            steps_to_run = steps_to_run[1:]

        # Build a workflow with the remaining steps and execute
        for step in steps_to_run:
            handler = _resolve_handler(step)
            logger.info("Workflow [%s] executing step %d: %s (handler=%s)",
                         workflow.name, step.index, step.title, handler.__name__)

            await _publish("routine_phase", {
                "phase": step.title,
                "message": f"Starting execution: {step.title}"
            })

            result = await handler(step, ctx)
            meeting_doc["steps"][step.title] = result
            ctx.results[step.title] = result

        # Populate backward-compatible phase1/phase2 structure
        meeting_doc["phase1"] = {
            "self_evaluations": ctx.self_evaluations,
            "senior_reviews": ctx.senior_reviews,
            "hr_summary": ctx.hr_summary,
        }
        meeting_doc["phase2"] = {
            "coo_report": ctx.coo_report,
            "employee_feedback": ctx.employee_feedback,
            "action_items": ctx.action_items,
            "asset_suggestions": ctx.asset_suggestions,
        }
        meeting_doc["action_items"] = ctx.action_items
        meeting_doc["asset_suggestions"] = ctx.asset_suggestions

        # Save report to disk
        _save_report(report_id, meeting_doc)

        # Publish informational event (EA already handled approval in workflow)
        summary_text = _build_summary(meeting_doc)

        await _publish("meeting_report_complete", {
            "report_id": report_id,
            "summary": summary_text,
        })

        # Record routine results in project archive
        if project_id:
            from onemancompany.core.project_archive import append_action
            # Record each participant's self-evaluation
            for ev in ctx.self_evaluations:
                append_action(project_id, ev.get("id", ""), "self-evaluation", ev.get("evaluation", "")[:MAX_SUMMARY_LEN])
            for rv in ctx.senior_reviews:
                append_action(project_id, rv.get("reviewer_id", ""), "senior review", rv.get("review", "")[:MAX_SUMMARY_LEN])
            if ctx.coo_report:
                append_action(project_id, COO_ID, "operations report", ctx.coo_report[:MAX_SUMMARY_LEN])
            for ai in ctx.action_items:
                append_action(project_id, ai.get("source", ""), "improvement item", ai.get("description", "")[:MAX_SUMMARY_LEN])

    finally:
        # Release meeting room
        await _set_participants_status(room.participants, STATUS_IDLE)
        room.is_booked = False
        room.booked_by = ""
        room.participants = []
        await _store.save_room(room.id, {
            "is_booked": False,
            "booked_by": "",
            "participants": [],
        })
        await _publish("meeting_released", {"room_id": room.id, "room_name": room.name})


# ---------------------------------------------------------------------------
# Action item dedup — check historical meeting reports
# ---------------------------------------------------------------------------

def _load_past_action_items() -> list[dict]:
    """Load action items from all past meeting reports.

    Returns a list of dicts with keys: description, approved (bool), report_id.
    """
    items: list[dict] = []
    if not REPORTS_DIR.exists():
        return items
    for report_path in REPORTS_DIR.glob("*.yaml"):
        try:
            with open(report_path) as f:
                doc = yaml.safe_load(f)
            if not doc or not isinstance(doc, dict):
                continue
            report_id = doc.get("id", report_path.stem)
            # Collect all action items
            for ai in doc.get("action_items", []):
                if isinstance(ai, dict):
                    desc = ai.get("description", "")
                    items.append({"description": desc, "report_id": report_id})
        except Exception:
            logger.debug("Failed to load report %s for dedup check", report_path)
    return items


def _tokenize(text: str) -> set[str]:
    """Simple tokenizer for similarity comparison."""
    return set(re.findall(r'[\w\u4e00-\u9fff]+', text.lower()))


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _dedup_action_items(
    new_items: list[dict],
    threshold: float = 0.6,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Filter out action items that are similar to previously proposed ones.

    Returns (unique_items, duplicate_items, recurring_items).
    - duplicate_items: appeared once before → skip silently
    - recurring_items: appeared 2+ times before → likely unresolvable, escalate to CEO
    """
    past_items = _load_past_action_items()
    if not past_items:
        return new_items, [], []

    # Count how many times each past description appeared
    past_tokens_with_count: list[tuple[set[str], int]] = []
    desc_counts: dict[str, int] = {}
    for p in past_items:
        desc = p["description"]
        tokens = _tokenize(desc)
        frozen = frozenset(tokens)
        key = str(sorted(frozen))
        desc_counts[key] = desc_counts.get(key, 0) + 1

    # Build lookup: tokens → count
    seen_keys: dict[str, tuple[set[str], int]] = {}
    for p in past_items:
        tokens = _tokenize(p["description"])
        frozen = frozenset(tokens)
        key = str(sorted(frozen))
        if key not in seen_keys:
            seen_keys[key] = (tokens, desc_counts.get(key, 1))

    unique: list[dict] = []
    duplicates: list[dict] = []
    recurring: list[dict] = []

    for item in new_items:
        desc = item.get("description", "")
        item_tokens = _tokenize(desc)
        match_count = 0
        for key, (pt, count) in seen_keys.items():
            if _jaccard(item_tokens, pt) > threshold:
                match_count = count
                break
        if match_count >= 2:
            recurring.append(item)
        elif match_count == 1:
            duplicates.append(item)
        else:
            unique.append(item)

    return unique, duplicates, recurring


# ---------------------------------------------------------------------------
# EA auto-approval helper (shared by workflow and fallback paths)
# ---------------------------------------------------------------------------

async def _ea_auto_approve_actions(
    action_items: list[dict],
    task_summary: str,
    coo_report: str,
    room_id: str,
) -> dict:
    """EA reviews action items via LLM and executes approved ones.

    Used by the fallback path (the workflow path uses _handle_ea_approval).
    """
    from onemancompany.core.agent_loop import get_agent_loop

    # Dedup: filter out items already proposed in past meetings
    unique_items, dup_items, recurring_items = _dedup_action_items(action_items)

    if dup_items:
        dup_descs = "; ".join(d.get("description", "")[:40] for d in dup_items)
        await _chat(room_id, "EA", "EA",
                    f"[Dedup] Skipping {len(dup_items)} previously proposed improvements: {dup_descs}")

    if recurring_items:
        recurring_descs = "\n".join(f"  - {r.get('description', '')[:80]}" for r in recurring_items)
        await _chat(room_id, "EA", "EA",
                    f"[Warning] The following {len(recurring_items)} improvements have been proposed multiple times without resolution, requiring CEO attention:\n{recurring_descs}")
        await _publish("recurring_action_items", {
            "items": [r.get("description", "") for r in recurring_items],
            "message": f"{len(recurring_items)} improvements keep recurring and may not be resolvable through normal means; CEO decision needed",
        })

    if not unique_items:
        await _chat(room_id, "EA", "EA", "All improvement items have been proposed in previous meetings; no new action plans.")
        return {"approved": [], "rejected_count": 0, "skipped_duplicates": len(dup_items), "reason": "All are duplicates"}

    action_items = unique_items

    llm = make_llm(EA_ID)

    items_text = "\n".join(
        f"  {i+1}. [{a.get('source', '')}] {a.get('description', '')} (priority: {a.get('priority', '')})"
        for i, a in enumerate(action_items)
    )

    prompt = (
        "You are the EA (Executive Assistant), strictly reviewing meeting action plans on behalf of the CEO.\n\n"
        "Core approval principle: Fewer, more precise and critical improvements are better than many.\n"
        "The CEO's top priority is improving organizational efficiency; any action that does not directly serve this goal should be rejected.\n\n"
        f"Meeting summary: {task_summary}\n\n"
        f"COO operations report: {coo_report}\n\n"
        f"Action plans pending review:\n{items_text}\n\n"
        "Strict review criteria (ALL must be met for approval):\n"
        "1. Specifically actionable: Has clear execution steps, not vague platitudes (e.g., 'strengthen management', 'improve efficiency', 'optimize processes' — reject all such vague statements)\n"
        "2. Directly relevant: Must be directly related to actual issues in this project, not generic advice\n"
        "3. Measurable results: Can clearly see results after execution, with criteria for judging success/failure\n"
        "4. Reasonable ROI: Benefits from improvement must outweigh execution costs\n"
        "5. No duplication or contradiction: Must not duplicate or contradict other action items\n\n"
        "Typical examples that should be rejected:\n"
        "- 'Strengthen code review process' — too vague, how to strengthen? What specifically to do?\n"
        "- 'Improve team collaboration capability' — empty rhetoric, no concrete action\n"
        "- 'Optimize project management mechanism' — bureaucratic, doesn't solve specific problems\n"
        "- Generic improvement suggestions unrelated to this project\n\n"
        "Review strictly; better to approve fewer than too many. Return your decision in JSON format:\n"
        '{"approved_indices": [0, 1, ...], "rejected_indices": [2, ...], "reason": "review notes"}\n'
        "approved_indices are the action numbers you approve (0-based), rejected_indices are the ones you reject.\n"
        "Only return JSON, no other content."
    )

    await _publish("routine_phase", {"phase": "EA Approval", "message": "EA is reviewing the action plan"})

    resp = await tracked_ainvoke(llm, prompt, category="routine", employee_id=EA_ID)
    raw = resp.content

    approved_indices: list[int] = []
    rejected_indices: list[int] = []
    ea_reason = ""
    try:
        json_match = re.search(r'\{.*\}', raw, re.DOTALL)
        if json_match:
            decision = json.loads(json_match.group())
            approved_indices = decision.get("approved_indices", [])
            rejected_indices = decision.get("rejected_indices", [])
            ea_reason = decision.get("reason", "")
        else:
            approved_indices = list(range(len(action_items)))
            ea_reason = "EA did not return valid JSON, defaulting to approve all"
    except json.JSONDecodeError:
        approved_indices = list(range(len(action_items)))
        ea_reason = "EA returned invalid format, defaulting to approve all"

    approved = [action_items[i] for i in approved_indices if i < len(action_items)]

    await _chat(room_id, "EA", "EA",
                f"[Approval Result] Approved {len(approved)} items, rejected {len(rejected_indices)} items. {ea_reason}")

    if approved:
        # Execute: push remaining (non-asset) actions to COO
        remaining = []
        for a in approved:
            if a.get("type") == "asset_consolidation":
                from onemancompany.agents.coo_agent import register_asset
                register_asset.invoke({
                    "name": a.get("name", ""),
                    "description": a.get("asset_description", a.get("description", "")),
                    "source_project_dir": a.get("project_dir", ""),
                    "source_files": a.get("files", []),
                })
            else:
                remaining.append(a)

        if remaining:
            action_lines = [f"- [{a.get('source', 'COO')}] {a['description']}" for a in remaining]
            coo_task = (
                "EA has approved the following action plan. Please assign execution based on the source field:\n"
                "- source=HR actions: Use dispatch_child() to assign to HR (employee_id='00002')\n"
                "- source=COO actions: Execute yourself\n\n"
                "Action plan:\n" + "\n".join(action_lines)
            )
            coo_loop = get_agent_loop(COO_ID)
            if coo_loop:
                coo_loop.push_task(coo_task)

    await _publish("routine_phase", {
        "phase": "EA Approval",
        "message": f"EA approval completed: approved {len(approved)} items, rejected {len(rejected_indices)} items"
    })

    return {
        "approved": [a.get("description", "") for a in approved],
        "rejected_count": len(rejected_indices),
        "reason": ea_reason,
    }


# ---------------------------------------------------------------------------
# Fallback — original hardcoded two-phase routine
# ---------------------------------------------------------------------------

async def _run_post_task_routine_fallback(task_summary: str, participants: list[str]) -> None:
    """Original hardcoded two-phase meeting, used when no workflow doc is available."""
    workflows = load_workflows()
    workflow_doc = workflows.get("project_retrospective_workflow", "")

    report_id = str(uuid.uuid4())[:8]
    meeting_doc: dict = {
        "id": report_id,
        "timestamp": datetime.now().isoformat(),
        "task_summary": task_summary,
        "participants": participants,
        "phase1": {},
        "phase2": {},
        "action_items": [],
    }

    # Book a meeting room
    await _publish("routine_phase", {"phase": "Preparation", "message": "HR is requesting a meeting room from COO..."})

    room = None
    for r in company_state.meeting_rooms.values():
        if not r.is_booked:
            room_participants = list(dict.fromkeys(participants + [EA_ID]))
            r.is_booked = True
            r.booked_by = HR_ID
            r.participants = room_participants
            await _store.save_room(r.id, {
                "is_booked": True,
                "booked_by": HR_ID,
                "participants": room_participants,
            })
            room = r
            break

    if not room:
        await _publish("routine_phase", {
            "phase": "Preparation",
            "message": "No available meeting rooms. Meeting postponed. Employees continue with current work."
        })
        return

    await _publish("meeting_booked", {
        "room_id": room.id,
        "room_name": room.name,
        "participants": room.participants,
    })
    await _set_participants_status(room.participants, STATUS_IN_MEETING)

    try:
        # PHASE 1: Review Meeting
        await _publish("routine_phase", {"phase": "Phase 1", "message": "Review meeting begins — employee self-evaluation"})
        await _chat(room.id, "HR", "HR", "The review meeting has officially begun. Please proceed with self-evaluations in turn.")
        phase1_result = await _run_phase1_legacy(task_summary, participants, workflow_doc, room.id)
        meeting_doc["phase1"] = phase1_result

        # PHASE 2: Operations Review
        await _publish("routine_phase", {"phase": "Phase 2", "message": "Operations review — COO producing report"})
        await _chat(room.id, "HR", "HR", "Phase 2 begins. COO, please report on operations.")
        phase2_result = await _run_phase2_legacy(
            task_summary, participants, phase1_result, workflow_doc, room.id
        )
        meeting_doc["phase2"] = phase2_result

        action_items = phase2_result.get("action_items", [])
        meeting_doc["action_items"] = action_items

        # EA auto-approval for fallback path
        if action_items:
            ea_approved = await _ea_auto_approve_actions(
                action_items, task_summary, phase2_result.get("coo_report", ""), room.id,
            )
            meeting_doc["ea_approval"] = ea_approved

        _save_report(report_id, meeting_doc)

        summary_text = _build_summary(meeting_doc)

        await _publish("meeting_report_complete", {
            "report_id": report_id,
            "summary": summary_text,
        })

    finally:
        await _set_participants_status(room.participants, STATUS_IDLE)
        room.is_booked = False
        room.booked_by = ""
        room.participants = []
        await _store.save_room(room.id, {
            "is_booked": False,
            "booked_by": "",
            "participants": [],
        })
        await _publish("meeting_released", {"room_id": room.id, "room_name": room.name})


async def _run_phase1_legacy(
    task_summary: str, participants: list[str], workflow_doc: str = "", room_id: str = ""
) -> dict:
    """Legacy Phase 1: Employee self-evaluation, senior reviews junior, HR summarizes."""
    llm = make_llm(HR_ID)
    result: dict = {"self_evaluations": [], "senior_reviews": [], "hr_summary": []}

    workflow_ctx = ""
    if workflow_doc:
        workflow_ctx = f"\n\n[Reference Workflow]\n{workflow_doc[:MAX_WORKFLOW_CONTEXT_LEN]}\nPlease execute according to the above workflow specification.\n"

    # Step 1: Employee self-evaluations
    for emp_id in participants:
        emp_data = load_employee(emp_id)
        if not emp_data:
            continue

        work_principles = emp_data.get("work_principles", "")
        principles_ctx = ""
        if work_principles:
            principles_ctx = f"\nYour work principles:\n{work_principles[:MAX_PRINCIPLES_LEN]}\n"

        skills_ctx = get_employee_skills_prompt(emp_id)
        tools_ctx = get_employee_tools_prompt(emp_id)

        emp_name = emp_data.get("name", "")
        emp_nickname = emp_data.get("nickname", "")
        emp_dept = emp_data.get("department", "")
        emp_level = emp_data.get("level", 1)
        emp_role = emp_data.get("role", "")

        prompt = (
            f"You are {emp_name} (nickname: {emp_nickname}, department: {emp_dept}, "
            f"level: Lv.{emp_level}, role: {emp_role}).\n"
            f"{principles_ctx}"
            f"{skills_ctx}"
            f"{tools_ctx}"
            f"Recently completed task summary: {task_summary}\n\n"
            f"Please briefly self-evaluate your performance on this task (2-3 sentences), including:\n"
            f"- What your contribution was\n"
            f"- How efficient you were\n"
            f"- Whether there were any mistakes or areas for improvement\n"
            f"{workflow_ctx}"
        )
        resp = await tracked_ainvoke(llm, prompt, category="routine", employee_id=emp_id)
        eval_text = resp.content
        result["self_evaluations"].append({
            "employee_id": emp_id,
            "name": emp_name,
            "nickname": emp_nickname,
            "level": emp_level,
            "evaluation": eval_text,
        })
        display = emp_nickname or emp_name
        await _chat(room_id, display, emp_role, eval_text)

    await _publish("routine_phase", {"phase": "Phase 1", "message": "Employee self-evaluation complete, senior employees begin peer review"})

    # Step 2: Senior employees review junior employees
    participant_data: list[tuple[str, dict]] = []
    for eid in participants:
        edata = load_employee(eid)
        if edata:
            participant_data.append((eid, edata))
    participant_data.sort(key=lambda x: x[1].get("level", 1), reverse=True)

    for senior_id, senior_data in participant_data:
        senior_level = senior_data.get("level", 1)
        juniors = [(jid, jd) for jid, jd in participant_data if jd.get("level", 1) < senior_level and jid != senior_id]
        if not juniors:
            continue

        junior_info = "\n".join(
            f"- {jd.get('name', '')}（{jd.get('nickname', '')}，Lv.{jd.get('level', 1)}）: "
            + next(
                (se["evaluation"] for se in result["self_evaluations"] if se["employee_id"] == jid),
                "No self-evaluation",
            )
            for jid, jd in juniors
        )

        prompt = (
            f"You are {senior_data.get('name', '')} (nickname: {senior_data.get('nickname', '')}, Lv.{senior_level}, {senior_data.get('role', '')}).\n"
            f"Task summary: {task_summary}\n\n"
            f"Below are the self-evaluations from junior colleagues:\n{junior_info}\n\n"
            f"Please provide a brief review for each junior colleague (1-2 sentences each), focusing on:\n"
            f"- Work efficiency\n- Work effectiveness\n- Whether there were any mistakes\n"
            f"Please respond in JSON array format: [{{'name': '...', 'review': '...'}}]"
        )
        resp = await tracked_ainvoke(llm, prompt, category="routine", employee_id=senior_id)
        review_text = resp.content

        reviews = _parse_json_array(review_text, [{"name": "all", "review": review_text}])

        result["senior_reviews"].append({
            "reviewer": senior_data.get("name", ""),
            "reviewer_level": senior_level,
            "reviews": reviews,
        })
        display = senior_data.get("nickname", "") or senior_data.get("name", "")
        review_summary = "; ".join(
            f"{r.get('name','')}: {r.get('review','')[:60]}" for r in reviews
        )
        await _chat(room_id, display, senior_data.get("role", ""), f"[Peer Review] {review_summary}")

    await _publish("routine_phase", {"phase": "Phase 1", "message": "Peer review complete, HR summarizing improvement points"})

    # Step 3: HR summarizes improvement points
    all_evals = "\n".join(
        f"[{se['name']}(Lv.{se['level']})] Self-eval: {se['evaluation']}"
        for se in result["self_evaluations"]
    )
    all_reviews = "\n".join(
        f"[{sr['reviewer']} review] " + "; ".join(
            f"{r.get('name','')}: {r.get('review','')}" for r in sr["reviews"]
        )
        for sr in result["senior_reviews"]
    )

    hr_prompt = (
        f"You are the HR manager, responsible for summarizing this review meeting.\n"
        f"Task summary: {task_summary}\n\n"
        f"Employee self-evaluations:\n{all_evals}\n\n"
        f"Senior employee peer reviews:\n{all_reviews}\n\n"
        f"Please summarize specific improvement points for each employee (1-3 items per person), "
        f"and respond in JSON array format:\n"
        f'[{{"employee": "...", "improvements": ["improvement 1", "improvement 2"]}}]'
        f"{workflow_ctx}"
    )
    resp = await tracked_ainvoke(llm, hr_prompt, category="routine", employee_id=HR_ID)
    hr_text = resp.content

    improvements = _parse_json_array(hr_text, [{"employee": "all", "improvements": [hr_text]}])

    result["hr_summary"] = improvements

    hr_msg = "; ".join(
        f"{it.get('employee','')}: {', '.join(it.get('improvements',[]))[:60]}"
        for it in improvements
    )
    await _chat(room_id, "HR", "HR", f"[Summary] {hr_msg}")

    await _publish("routine_phase", {
        "phase": "Phase 1",
        "message": "HR review meeting summary completed, Phase 1 ends"
    })
    return result


async def _run_phase2_legacy(
    task_summary: str,
    participants: list[str],
    phase1: dict,
    workflow_doc: str = "",
    room_id: str = "",
) -> dict:
    """Legacy Phase 2: COO report, employee feedback, action items for CEO."""
    llm = make_llm(COO_ID)
    result: dict = {"coo_report": "", "employee_feedback": [], "action_items": []}

    workflow_ctx = ""
    if workflow_doc:
        workflow_ctx = f"\n\n[Reference Workflow]\n{workflow_doc[:MAX_WORKFLOW_CONTEXT_LEN]}\nPlease execute according to the above workflow specification.\n"

    # Step 1: COO operations report
    emp_count = len(load_all_employees())
    tool_count = len(company_state.tools)
    room_count = len(_store.load_rooms())

    coo_prompt = (
        f"You are the COO, responsible for producing a company operations report.\n"
        f"Recently completed task: {task_summary}\n"
        f"The company currently has {emp_count} employees, {tool_count} pieces of equipment, and {room_count} meeting rooms.\n\n"
        f"Please briefly summarize the current company operations (3-5 sentences), including:\n"
        f"- Project completion status\n- Resource utilization\n- Potential risks\n"
        f"{workflow_ctx}"
    )
    resp = await tracked_ainvoke(llm, coo_prompt, category="routine", employee_id=COO_ID)
    result["coo_report"] = resp.content
    await _chat(room_id, "COO", "COO", result["coo_report"])

    await _publish("routine_phase", {"phase": "Phase 2", "message": "COO report complete, employee open floor"})

    # Step 2: Employee open floor
    for emp_id in participants:
        emp_data = load_employee(emp_id)
        if not emp_data:
            continue

        work_principles = emp_data.get("work_principles", "")
        principles_ctx = ""
        if work_principles:
            principles_ctx = f"\nYour work principles:\n{work_principles[:MAX_PRINCIPLES_LEN]}\n"

        skills_ctx = get_employee_skills_prompt(emp_id)
        tools_ctx = get_employee_tools_prompt(emp_id)

        emp_name = emp_data.get("name", "")
        emp_nickname = emp_data.get("nickname", "")
        emp_dept = emp_data.get("department", "")
        emp_role = emp_data.get("role", "")
        emp_level = emp_data.get("level", 1)

        prompt = (
            f"You are {emp_name} ({emp_nickname}, department: {emp_dept}, "
            f"{emp_role}, Lv.{emp_level}).\n"
            f"{principles_ctx}"
            f"{skills_ctx}"
            f"{tools_ctx}"
            f"Task summary: {task_summary}\n"
            f"This is the open floor session of the meeting. You may raise:\n"
            f"- Difficulties encountered during work\n"
            f"- Missing tools or equipment\n"
            f"- What kind of talent is needed\n"
            f"- Any other suggestions\n"
            f"Please speak briefly (2-3 sentences)."
        )
        resp = await tracked_ainvoke(llm, prompt, category="routine", employee_id=emp_id)
        feedback_content = resp.content
        result["employee_feedback"].append({
            "employee_id": emp_id,
            "name": emp_name,
            "feedback": feedback_content,
        })
        display = emp_nickname or emp_name
        await _chat(room_id, display, emp_role, feedback_content)

    await _publish("routine_phase", {"phase": "Phase 2", "message": "Open floor concluded, COO and HR compiling action plan"})

    # Step 3: COO + HR summarize action items
    feedback_text = "\n".join(
        f"[{f['name']}] {f['feedback']}" for f in result["employee_feedback"]
    )
    phase1_improvements = "\n".join(
        f"[{item.get('employee','')}] " + ", ".join(item.get("improvements", []))
        for item in phase1.get("hr_summary", [])
    )

    action_prompt = (
        f"You represent both COO and HR in compiling the meeting action plan.\n\n"
        f"COO operations report: {result['coo_report']}\n\n"
        f"Employee remarks:\n{feedback_text}\n\n"
        f"Phase 1 improvement suggestions:\n{phase1_improvements}\n\n"
        f"Please compile into specific action items, each indicating who is responsible (HR/COO), "
        f"and respond in JSON array format:\n"
        f'[{{"source": "HR/COO", "description": "specific action", "priority": "high/medium/low"}}]'
        f"{workflow_ctx}"
    )
    resp = await tracked_ainvoke(llm, action_prompt, category="routine", employee_id=COO_ID)
    action_text = resp.content

    action_items = _parse_json_array(
        action_text, [{"source": "COO", "description": action_text, "priority": "medium"}]
    )

    result["action_items"] = action_items

    actions_msg = "; ".join(
        f"[{a.get('source','')}] {a.get('description','')[:50]}"
        for a in action_items[:5]
    )
    await _chat(room_id, "COO+HR", "COO", f"[Action Plan] {actions_msg}")

    return result


# ---------------------------------------------------------------------------
# Summary & persistence (unchanged)
# ---------------------------------------------------------------------------

def _build_summary(doc: dict) -> str:
    """Build a human-readable summary of the meeting report."""
    lines = [f"Meeting Report — {doc['timestamp'][:10]}"]
    lines.append(f"Task: {doc['task_summary'][:100]}")
    lines.append("")

    # If workflow-driven, include step names
    if doc.get("workflow"):
        lines.append(f"Workflow: {doc['workflow']}")
        lines.append("")

    # Phase 1 summary
    if doc.get("phase1", {}).get("hr_summary"):
        lines.append("[Review Meeting]")
        for item in doc["phase1"]["hr_summary"]:
            emp = item.get("employee", "?")
            imps = ", ".join(item.get("improvements", []))
            lines.append(f"  {emp}: {imps}")
        lines.append("")

    # Phase 2 summary
    if doc.get("phase2", {}).get("coo_report"):
        lines.append("[Operations Review]")
        lines.append(f"  COO Report: {doc['phase2']['coo_report'][:200]}")
        lines.append("")

    if doc.get("phase2", {}).get("employee_feedback"):
        lines.append("  Employee Remarks:")
        for f in doc["phase2"]["employee_feedback"]:
            lines.append(f"    {f['name']}: {f['feedback'][:80]}")
        lines.append("")

    # Asset consolidation suggestions
    asset_suggestions = doc.get("asset_suggestions") or doc.get("phase2", {}).get("asset_suggestions", [])
    if asset_suggestions:
        lines.append("[Asset Consolidation Suggestions]")
        for s in asset_suggestions:
            files = ", ".join(s.get("files", []))
            lines.append(f"  {s.get('name', '?')}: {s.get('description', '')} (files: {files})")
        lines.append("")

    return "\n".join(lines)


def _save_report(report_id: str, doc: dict) -> None:
    """Save meeting report to meeting_reports/ directory."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS_DIR / f"{report_id}.yaml"
    with open(report_path, "w") as f:
        yaml.dump(doc, f, allow_unicode=True, default_flow_style=False)


# ---------------------------------------------------------------------------
# Public API — execute_approved_actions
# ---------------------------------------------------------------------------

async def execute_approved_actions(report_id: str, approved_indices: list[int]) -> str:
    """CEO approved certain action items. HR and COO execute them."""
    doc = pending_reports.pop(report_id, None)
    if not doc:
        # Fallback: try loading from disk (survives server restart)
        report_path = REPORTS_DIR / f"{report_id}.yaml"
        if report_path.exists():
            with open(report_path) as f:
                doc = yaml.safe_load(f)
    if not doc:
        return "Report not found."

    action_items = doc.get("action_items", [])
    approved = [action_items[i] for i in approved_indices if i < len(action_items)]

    if not approved:
        return "No actions to execute."

    await _publish("routine_phase", {
        "phase": "Execution",
        "message": f"CEO approved {len(approved)} improvements, HR and COO begin execution"
    })

    # Execute asset consolidation actions directly (no LLM needed)
    from onemancompany.agents.coo_agent import register_asset

    remaining_actions = []
    asset_results = []
    for a in approved:
        if a.get("type") == "asset_consolidation":
            result = register_asset.invoke({
                "name": a.get("name", ""),
                "description": a.get("asset_description", a.get("description", "")),
                "source_project_dir": a.get("project_dir", ""),
                "source_files": a.get("files", []),
            })
            asset_results.append(result)
            logger.info("Asset registered: %s -> %s", a.get("name"), result)
        else:
            remaining_actions.append(a)

    if asset_results:
        await _publish("routine_phase", {
            "phase": "Asset Consolidation",
            "message": f"Registered {len(asset_results)} company assets"
        })

    if not remaining_actions:
        summary = f"Executed {len(asset_results)} asset consolidations, no other action plans"
        await _publish("routine_phase", {"phase": "Execution Complete", "message": summary[:MAX_SUMMARY_LEN]})
        doc["execution"] = {"approved": approved, "results": [summary], "asset_results": asset_results}
        _save_report(doc["id"], doc)
        return summary

    # Group remaining by source (HR vs COO); unmatched actions default to COO
    hr_actions = [a for a in remaining_actions if "HR" in a.get("source", "").upper()]
    coo_actions = [a for a in remaining_actions if "COO" in a.get("source", "").upper()]
    routed = set(id(a) for a in hr_actions) | set(id(a) for a in coo_actions)
    unrouted = [a for a in remaining_actions if id(a) not in routed]
    coo_actions.extend(unrouted)

    # COO is responsible for receiving all approved actions and dispatching them.
    # Build a task description with all actions and their sources, then push to COO.
    from onemancompany.core.agent_loop import get_agent_loop
    from onemancompany.core.config import COO_ID

    action_lines = []
    for a in remaining_actions:
        source = a.get("source", "COO")
        action_lines.append(f"- [{source}] {a['description']}")

    coo_task = (
        "CEO has approved the following action plan. Please assign execution based on the source field:\n"
        "- source=HR actions: Use dispatch_child() to assign to HR (employee_id='00002')\n"
        "- source=COO actions: Execute yourself\n\n"
        "Action plan:\n" + "\n".join(action_lines)
    )

    coo_loop = get_agent_loop(COO_ID)
    if coo_loop:
        coo_loop.push_task(coo_task)
        summary = f"Pushed {len(remaining_actions)} approved actions to COO task board"
    else:
        summary = "COO agent loop not found"

    if asset_results:
        summary += f", registered {len(asset_results)} company assets"

    await _publish("routine_phase", {"phase": "Execution Complete", "message": summary[:MAX_SUMMARY_LEN]})

    doc["execution"] = {"approved": approved, "results": [summary], "asset_results": asset_results}
    _save_report(doc["id"], doc)

    return summary


# ---------------------------------------------------------------------------
# Public API — run_all_hands_meeting (unchanged)
# ---------------------------------------------------------------------------

async def run_all_hands_meeting(ceo_message: str) -> None:
    """CEO convenes an all-hands meeting in the large meeting hall.

    All employees attend. CEO delivers a company-wide directive.
    Afterwards, each employee absorbs and summarizes the meeting spirit,
    which gets recorded into their guidance notes.
    """
    all_emps = load_all_employees()
    if not all_emps:
        return

    all_emp_ids = list(all_emps.keys())

    room = None
    room_participants = [CEO_ID] + all_emp_ids
    for r in sorted(company_state.meeting_rooms.values(), key=lambda x: x.capacity, reverse=True):
        if not r.is_booked:
            r.is_booked = True
            r.booked_by = CEO_ID
            r.participants = room_participants
            await _store.save_room(r.id, {
                "is_booked": True,
                "booked_by": CEO_ID,
                "participants": room_participants,
            })
            room = r
            break

    if not room:
        await _publish("routine_phase", {
            "phase": "All-Hands Meeting",
            "message": "No large meeting hall available. All-hands meeting postponed."
        })
        return

    await _publish("meeting_booked", {
        "room_id": room.id,
        "room_name": room.name,
        "participants": room.participants,
    })
    await _set_participants_status(room.participants, STATUS_IN_MEETING)

    try:
        await _publish("routine_phase", {
            "phase": "All-Hands Meeting",
            "message": f"CEO convened an all-hands meeting in {room.name}"
        })

        await _publish("routine_phase", {
            "phase": "All-Hands Meeting",
            "message": f"CEO issued directive: {ceo_message[:100]}"
        })
        await _chat(room.id, "CEO", "CEO", ceo_message)

        llm = make_llm(HR_ID)

        for emp_id, emp_data in all_emps.items():
            work_principles = emp_data.get("work_principles", "")
            principles_ctx = ""
            if work_principles:
                principles_ctx = f"\nYour work principles:\n{work_principles[:MAX_PRINCIPLES_LEN]}\n"

            skills_ctx = get_employee_skills_prompt(emp_id)
            tools_ctx = get_employee_tools_prompt(emp_id)

            emp_name = emp_data.get("name", "")
            emp_nickname = emp_data.get("nickname", "")

            prompt = (
                f"You are {emp_name} (nickname: {emp_nickname}, department: {emp_data.get('department', '')}, "
                f"Lv.{emp_data.get('level', 1)}, {emp_data.get('role', '')}).\n"
                f"{principles_ctx}"
                f"{skills_ctx}"
                f"{tools_ctx}"
                f"The CEO just delivered the following directive at the all-hands meeting:\n\n"
                f'"{ceo_message}"\n\n'
                f"Please summarize in 1-2 sentences what you took away from this meeting "
                f"and how you plan to implement it in your future work."
            )
            resp = await tracked_ainvoke(llm, prompt, category="routine", employee_id=emp_id)
            summary_text = resp.content

            display = emp_nickname or emp_name
            await _chat(room.id, display, emp_data.get("role", ""), summary_text)

            await _publish("guidance_noted", {
                "employee_id": emp_id,
                "name": emp_name,
                "guidance": ceo_message[:80],
                "acknowledgment": summary_text,
            })

        await _publish("routine_phase", {
            "phase": "All-Hands Meeting",
            "message": f"All-hands meeting concluded, {len(all_emps)} employees have absorbed the meeting directives"
        })

        report_id = str(uuid.uuid4())[:8]
        doc = {
            "id": report_id,
            "type": "all_hands",
            "timestamp": datetime.now().isoformat(),
            "ceo_message": ceo_message,
            "room": room.name,
            "attendees": all_emp_ids,
        }
        _save_report(report_id, doc)

    finally:
        await _set_participants_status(room.participants, STATUS_IDLE)
        room.is_booked = False
        room.booked_by = ""
        room.participants = []
        await _store.save_room(room.id, {
            "is_booked": False,
            "booked_by": "",
            "participants": [],
        })
        await _publish("meeting_released", {"room_id": room.id, "room_name": room.name})


# ---------------------------------------------------------------------------
# CEO Meeting System — All-Hands & Discussion
# ---------------------------------------------------------------------------

_active_ceo_meeting: dict | None = None


async def start_ceo_meeting(meeting_type: str) -> dict:
    """Start a CEO meeting (all_hands or discussion). Books a room, sets state."""
    global _active_ceo_meeting
    if _active_ceo_meeting:
        return {"error": "A CEO meeting is already in progress"}

    all_emps = load_all_employees()
    if not all_emps:
        return {"error": "No employees available"}

    all_emp_ids = list(all_emps.keys())
    room_participants = [CEO_ID] + all_emp_ids

    # Book largest available room
    room = None
    for r in sorted(company_state.meeting_rooms.values(), key=lambda x: x.capacity, reverse=True):
        if not r.is_booked:
            r.is_booked = True
            r.booked_by = CEO_ID
            r.participants = room_participants
            await _store.save_room(r.id, {
                "is_booked": True,
                "booked_by": CEO_ID,
                "participants": room_participants,
            })
            room = r
            break

    if not room:
        return {"error": "No meeting room available"}

    await _publish("meeting_booked", {
        "room_id": room.id,
        "room_name": room.name,
        "participants": room.participants,
    })
    await _set_participants_status(room_participants, STATUS_IN_MEETING)

    _active_ceo_meeting = {
        "type": meeting_type,
        "room_id": room.id,
        "room_name": room.name,
        "participants": all_emp_ids,
        "chat_history": [],
    }

    await _publish("routine_phase", {
        "phase": "CEO Meeting",
        "message": f"CEO convened a {'all-hands' if meeting_type == 'all_hands' else 'discussion'} meeting in {room.name}",
    })

    return {
        "status": "started",
        "type": meeting_type,
        "room_id": room.id,
        "room_name": room.name,
        "participants": [
            {"id": eid, "name": edata.get("name", ""), "nickname": edata.get("nickname", "")}
            for eid, edata in all_emps.items()
        ],
    }


async def ceo_meeting_chat(message: str) -> dict:
    """CEO sends a message in the active meeting. Returns employee responses."""
    global _active_ceo_meeting
    if not _active_ceo_meeting:
        return {"error": "No active CEO meeting"}

    meeting = _active_ceo_meeting
    room_id = meeting["room_id"]

    # Post CEO message to room chat
    await _chat(room_id, "CEO", "CEO", message)
    meeting["chat_history"].append({"speaker": "CEO", "message": message})

    await _publish("routine_phase", {
        "phase": "CEO Meeting",
        "message": f"CEO: {message[:100]}",
    })

    responses: list[dict] = []
    all_emps = load_all_employees()

    if meeting["type"] == "all_hands":
        # All-hands: each employee absorbs silently (no discussion)
        llm = make_llm(HR_ID)
        for emp_id in meeting["participants"]:
            emp_data = all_emps.get(emp_id)
            if not emp_data:
                continue

            emp_name = emp_data.get("name", "")
            emp_nickname = emp_data.get("nickname", "")
            work_principles = emp_data.get("work_principles", "")
            principles_ctx = f"\nYour work principles:\n{work_principles[:MAX_PRINCIPLES_LEN]}\n" if work_principles else ""

            prompt = (
                f"You are {emp_name} (nickname: {emp_nickname}, department: {emp_data.get('department', '')}, "
                f"Lv.{emp_data.get('level', 1)}, {emp_data.get('role', '')}).\n"
                f"{principles_ctx}"
                f"The CEO just delivered the following at the all-hands meeting:\n\n"
                f'"{message}"\n\n'
                f"Summarize in 1-2 sentences what you took away and how it affects your work."
            )
            resp = await tracked_ainvoke(llm, prompt, category="routine", employee_id=emp_id)
            summary_text = resp.content

            display = emp_nickname or emp_name
            await _chat(room_id, display, emp_data.get("role", ""), summary_text)
            meeting["chat_history"].append({"speaker": display, "message": summary_text})

            responses.append({
                "employee_id": emp_id,
                "name": emp_name,
                "nickname": emp_nickname,
                "message": summary_text,
            })

            await _publish("guidance_noted", {
                "employee_id": emp_id,
                "name": emp_name,
                "guidance": message[:80],
                "acknowledgment": summary_text,
            })

    elif meeting["type"] == "discussion":
        # Discussion: token-grab rounds until no one wants to speak
        from onemancompany.agents.common_tools import _build_evaluate_prompt, _build_speech_prompt

        speakers = [
            (eid, all_emps[eid])
            for eid in meeting["participants"]
            if eid in all_emps
        ]

        loop = asyncio.get_running_loop()
        last_speaker_id = ""
        max_rounds = 10

        for _round in range(max_rounds):
            async def _evaluate(eid_and_data: tuple[str, dict]):
                eid, edata = eid_and_data
                prompt = _build_evaluate_prompt(edata, eid, "CEO Meeting", "", meeting["chat_history"])
                llm = make_llm(eid)
                t0 = loop.time()
                resp = await tracked_ainvoke(llm, prompt, category="meeting", employee_id=eid)
                t1 = loop.time()
                first_line = resp.content.strip().split("\n")[0].upper()[:20]
                wants = "YES" in first_line
                return (eid, edata, wants, t1)

            results = await asyncio.gather(
                *[_evaluate(e) for e in speakers],
                return_exceptions=True,
            )

            willing = [
                (eid, edata, ts)
                for r in results
                if not isinstance(r, Exception)
                for eid, edata, wants, ts in [r]
                if wants
            ]

            if not willing:
                break

            # Token grab — fastest wins, no consecutive same speaker
            willing.sort(key=lambda x: x[2])
            winner_id, winner_data, _ = willing[0]
            if winner_id == last_speaker_id and len(willing) > 1:
                winner_id, winner_data, _ = willing[1]

            speech_prompt = _build_speech_prompt(winner_data, winner_id, "CEO Meeting", "", meeting["chat_history"])
            resp = await tracked_ainvoke(make_llm(winner_id), speech_prompt, category="meeting", employee_id=winner_id)
            last_speaker_id = winner_id

            display = winner_data.get("nickname", "") or winner_data.get("name", "")
            await _chat(room_id, display, winner_data.get("role", ""), resp.content)
            meeting["chat_history"].append({"speaker": display, "message": resp.content})

            responses.append({
                "employee_id": winner_id,
                "name": winner_data.get("name", ""),
                "nickname": winner_data.get("nickname", ""),
                "message": resp.content,
            })

    return {"responses": responses}


async def end_ceo_meeting() -> dict:
    """End CEO meeting. EA summarizes action points, saves guidance, creates project if needed."""
    global _active_ceo_meeting
    if not _active_ceo_meeting:
        return {"error": "No active CEO meeting"}

    meeting = _active_ceo_meeting
    room_id = meeting["room_id"]
    meeting_type = meeting["type"]
    chat_history = meeting["chat_history"]

    await _publish("routine_phase", {
        "phase": "CEO Meeting",
        "message": "Meeting ending — summarizing action points...",
    })

    # --- Save guidance notes for each employee ---
    all_emps = load_all_employees()
    full_transcript = "\n".join(f"[{e['speaker']}] {e['message']}" for e in chat_history)
    date_str = datetime.now().strftime("%Y-%m-%d")
    meeting_label = "All-Hands" if meeting_type == "all_hands" else "Discussion"

    for emp_id in meeting["participants"]:
        emp_data = all_emps.get(emp_id)
        if not emp_data:
            continue

        # Save guidance note
        note = f"**{date_str} {meeting_label} Meeting**\nMeeting transcript summary:\n{full_transcript[:500]}"
        try:
            existing_notes = _store.load_employee_guidance(emp_id)
            existing_notes.append(note)
            await _store.save_guidance(emp_id, existing_notes)
        except Exception as e:
            logger.warning("Failed to save guidance for {}: {}", emp_id, e)

        # Reflect on work principles update
        work_principles = emp_data.get("work_principles", "") or "(No work principles yet)"
        emp_name = emp_data.get("name", "")
        emp_nickname = emp_data.get("nickname", "")

        try:
            reflection_prompt = (
                f"You are {emp_name} ({emp_nickname}, {emp_data.get('role', '')}).\n\n"
                f"You just attended a {meeting_label} meeting. Here is the transcript:\n\n"
                f"{full_transcript[:2000]}\n\n"
                f"Your current work principles:\n{work_principles}\n\n"
                f"Did the CEO convey any actionable guidance that should update your work principles?\n"
                f"If YES — output UPDATED: followed by complete updated work principles in Markdown.\n"
                f"If NO — output NO_UPDATE"
            )
            llm = make_llm(emp_id)
            result = await tracked_ainvoke(llm, reflection_prompt, category="meeting", employee_id=emp_id)
            resp_text = result.content.strip()

            if "UPDATED:" in resp_text and "NO_UPDATE" not in resp_text:
                new_principles = resp_text[resp_text.index("UPDATED:") + len("UPDATED:"):].strip()
                if new_principles:
                    await _store.save_work_principles(emp_id, new_principles)
        except Exception as e:
            logger.warning("Principles reflection failed for {}: {}", emp_id, e)

    # --- EA summarizes action points ---
    action_points: list[str] = []
    try:
        ea_summary_prompt = (
            f"You are the Executive Assistant reviewing a CEO {meeting_label} meeting.\n\n"
            f"Full meeting transcript:\n{full_transcript[:3000]}\n\n"
            f"Extract concrete, actionable items from this meeting.\n"
            f"For each action point, write a clear, measurable acceptance criterion.\n\n"
            f"Output format — JSON array of strings, each being one action point:\n"
            f'["Action point 1: description", "Action point 2: description"]\n\n'
            f"If there are NO action points (purely informational meeting), output: []"
        )
        ea_llm = make_llm(EA_ID)
        ea_result = await tracked_ainvoke(ea_llm, ea_summary_prompt, category="meeting", employee_id=EA_ID)
        ea_text = ea_result.content.strip()

        import json as _json
        json_match = re.search(r'\[.*\]', ea_text, re.DOTALL)
        if json_match:
            parsed = _json.loads(json_match.group())
            if isinstance(parsed, list):
                action_points = [str(ap) for ap in parsed if ap]
    except Exception as e:
        logger.warning("EA action point extraction failed: {}", e)

    summary_msg = f"Meeting concluded. {len(action_points)} action point(s) identified."
    if action_points:
        summary_msg += "\n" + "\n".join(f"• {ap}" for ap in action_points)
    await _chat(room_id, "EA", "EA", summary_msg)

    await _publish("routine_phase", {
        "phase": "CEO Meeting",
        "message": summary_msg[:200],
    })

    # --- Auto-create project if action points exist ---
    project_id = ""
    if action_points:
        try:
            project_id = await _create_project_from_action_points(
                action_points, meeting_type, full_transcript[:1000],
            )
        except Exception as e:
            logger.warning("Failed to create project from action points: {}", e)

    # Save meeting report
    report_id = str(uuid.uuid4())[:8]
    doc = {
        "id": report_id,
        "type": f"ceo_{meeting_type}",
        "timestamp": datetime.now().isoformat(),
        "room": meeting["room_name"],
        "attendees": meeting["participants"],
        "action_points": action_points,
        "project_id": project_id,
    }
    _save_report(report_id, doc)

    # Release room
    room = company_state.meeting_rooms.get(room_id)
    if room:
        await _set_participants_status(room.participants, STATUS_IDLE)
        room.is_booked = False
        room.booked_by = ""
        room.participants = []
        await _store.save_room(room.id, {
            "is_booked": False, "booked_by": "", "participants": [],
        })
        await _publish("meeting_released", {"room_id": room.id, "room_name": room.name})

    _active_ceo_meeting = None

    return {
        "status": "ended",
        "action_points": action_points,
        "project_id": project_id,
    }


async def _create_project_from_action_points(
    action_points: list[str], meeting_type: str, transcript_excerpt: str,
) -> str:
    """Create a new project from meeting action points, dispatched to EA."""
    from onemancompany.core.project_archive import create_project, get_project_dir
    from onemancompany.core.task_tree import TaskTree
    from onemancompany.core.vessel import _save_project_tree
    from onemancompany.core.agent_loop import employee_manager
    from onemancompany.core.task_lifecycle import TaskPhase
    from pathlib import Path

    meeting_label = "All-Hands" if meeting_type == "all_hands" else "Discussion"
    task_desc = (
        f"Action points from CEO {meeting_label} meeting:\n\n"
        + "\n".join(f"- {ap}" for ap in action_points)
        + f"\n\nMeeting context:\n{transcript_excerpt}"
    )

    pid = create_project(task_desc, "pending", list(load_all_employees().keys()))
    pdir = get_project_dir(pid)

    tree = TaskTree(project_id=pid)
    ceo_root = tree.create_root(employee_id=CEO_ID, description=task_desc)
    ceo_root.node_type = "ceo_prompt"
    ceo_root.set_status(TaskPhase.PROCESSING)

    ea_task = (
        f"CEO has assigned action points from a {meeting_label} meeting. "
        f"Please analyze and dispatch to the appropriate owner:\n\n"
        f"Task: {task_desc}\n\n"
        f"[Project ID: {pid}] [Project workspace: {pdir}]"
    )
    ea_node = tree.add_child(
        parent_id=ceo_root.id,
        employee_id=EA_ID,
        description=ea_task,
        acceptance_criteria=action_points,
    )
    _save_project_tree(pdir, tree)

    tree_path = str(Path(pdir) / "task_tree.yaml")
    employee_manager.schedule_node(EA_ID, ea_node.id, tree_path)
    employee_manager._schedule_next(EA_ID)

    await _publish("routine_phase", {
        "phase": "CEO Meeting",
        "message": f"Created project {pid} with {len(action_points)} action points",
    })
    # Broadcast so frontend sees project immediately
    await event_bus.publish(
        CompanyEvent(type="state_snapshot", payload={}, agent="SYSTEM")
    )

    logger.info("Created project {} from meeting action points", pid)
    return pid


# ---------------------------------------------------------------------------
# Onboarding routine
# ---------------------------------------------------------------------------

# Need PROBATION_TASKS at module level for the onboarding routine
from onemancompany.core.config import PROBATION_TASKS  # noqa: E402


async def run_onboarding_routine(employee_id: str) -> None:
    """Run onboarding for a new employee: welcome, team intro, probation brief."""
    emp_data = load_employee(employee_id)
    if not emp_data:
        return

    emp_name = emp_data.get("name", "")
    emp_nickname = emp_data.get("nickname", "")

    await _publish("onboarding_started", {"id": employee_id, "name": emp_name})
    await _publish("routine_phase", {
        "phase": "onboarding",
        "message": f"Welcome {emp_name} ({emp_nickname}) to the team! Starting onboarding...",
    })

    # Brief the new hire on probation
    await _publish("routine_phase", {
        "phase": "onboarding",
        "message": f"{emp_name} has been briefed on the probation period (complete {PROBATION_TASKS} tasks to pass).",
    })

    # Generate work principles if empty — persist via store
    if not emp_data.get("work_principles", ""):
        from onemancompany.core.state import make_title
        principles = (
            f"# {emp_name} ({emp_nickname}) Work Principles\n\n"
            f"**Department**: {emp_data.get('department', '')}\n"
            f"**Title**: {make_title(emp_data.get('level', 1), emp_data.get('role', ''))}\n\n"
            f"## Core Principles\n"
            f"1. Complete assigned work diligently\n"
            f"2. Collaborate with the team\n"
            f"3. Continuously learn and improve\n"
            f"4. Follow company rules and guidelines\n"
        )
        await _store.save_work_principles(employee_id, principles)

    # Mark onboarding complete
    await _store.save_employee(employee_id, {"onboarding_completed": True})

    await _publish("onboarding_completed", {"id": employee_id, "name": emp_name})


# ---------------------------------------------------------------------------
# Offboarding routine
# ---------------------------------------------------------------------------

async def run_offboarding_routine(employee_id: str, reason: str) -> None:
    """Run offboarding for a departing employee: exit interview, feedback."""
    emp_data = load_employee(employee_id)
    if not emp_data:
        return

    emp_name = emp_data.get("name", "")
    emp_nickname = emp_data.get("nickname", "")

    await _publish("exit_interview_started", {
        "id": employee_id, "name": emp_name, "reason": reason,
    })

    await _publish("routine_phase", {
        "phase": "offboarding",
        "message": f"Exit interview with {emp_name} ({emp_nickname}). Reason: {reason}",
    })

    # Generate exit report
    report_id = str(uuid.uuid4())[:8]
    doc = {
        "id": report_id,
        "type": "exit_interview",
        "timestamp": datetime.now().isoformat(),
        "employee_id": employee_id,
        "employee_name": emp_name,
        "reason": reason,
    }
    _save_report(report_id, doc)

    await _publish("exit_interview_completed", {
        "id": employee_id, "name": emp_name, "report_id": report_id,
    })


# ---------------------------------------------------------------------------
# Performance meeting routine
# ---------------------------------------------------------------------------

async def run_performance_meeting(employee_id: str, score: float, feedback: str) -> None:
    """Run a 1-on-1 performance feedback meeting."""
    emp_data = load_employee(employee_id)
    if not emp_data:
        return

    emp_name = emp_data.get("name", "")
    emp_nickname = emp_data.get("nickname", "")

    await _publish("routine_phase", {
        "phase": "performance_meeting",
        "message": f"Performance meeting with {emp_name} ({emp_nickname}): score {score}",
    })

    await _publish("routine_phase", {
        "phase": "performance_meeting",
        "message": f"Feedback for {emp_name}: {feedback}",
    })


# ---------------------------------------------------------------------------
# Snapshot provider — pending reports
# ---------------------------------------------------------------------------

from onemancompany.core.snapshot import snapshot_provider  # noqa: E402


@snapshot_provider("routine")
class _RoutineSnapshot:
    @staticmethod
    def save() -> dict:
        if not pending_reports:
            return {}
        return {"pending_reports": {k: v for k, v in pending_reports.items()}}

    @staticmethod
    def restore(data: dict) -> None:
        restored = data.get("pending_reports", {})
        if restored:
            pending_reports.update(restored)
