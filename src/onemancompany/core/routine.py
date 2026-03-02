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

import json
import logging
import re
import uuid
from datetime import datetime
from typing import Any, Callable, Awaitable

import yaml

from onemancompany.agents.base import get_employee_skills_prompt, get_employee_tools_prompt, make_llm
from onemancompany.core.config import (
    CEO_ID,
    COO_ID,
    FOUNDING_LEVEL,
    HR_ID,
    MAX_PRINCIPLES_LEN,
    MAX_SUMMARY_LEN,
    MAX_WORKFLOW_CONTEXT_LEN,
    MEETING_REPORTS_DIR,
    STATUS_IDLE,
    STATUS_IN_MEETING,
    load_workflows,
    update_employee_performance,
)
from onemancompany.core.events import CompanyEvent, event_bus
from onemancompany.core.state import company_state
from onemancompany.core.workflow_engine import (
    WorkflowDefinition,
    WorkflowStep,
    classify_step_owner,
    parse_workflow,
)

logger = logging.getLogger(__name__)

REPORTS_DIR = MEETING_REPORTS_DIR


def _set_participants_status(participant_ids: list[str], status: str) -> None:
    """Set status for all participants (including hr/coo)."""
    for pid in participant_ids:
        emp = company_state.employees.get(pid)
        if emp:
            emp.status = status

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

    def format_project_timeline(self, max_entries: int = 20) -> str:
        """Format the project timeline as a readable string for LLM prompts."""
        timeline = self.project_record.get("timeline", [])
        if not timeline:
            return ""
        lines = []
        for entry in timeline[-max_entries:]:
            emp_id = entry.get("employee_id", "?")
            # Resolve name from company_state
            emp = company_state.employees.get(emp_id)
            name = f"{emp.name}({emp.nickname})" if emp else emp_id
            action = entry.get("action", "")
            detail = entry.get("detail", "")[:200]
            lines.append(f"- [{name}] {action}: {detail}")
        return "\n".join(lines)

    def format_company_culture(self) -> str:
        """Format company culture items as a prompt section."""
        items = company_state.company_culture
        if not items:
            return ""
        rules = "\n".join(f"  {i+1}. {item.get('content', '')}" for i, item in enumerate(items))
        return f"\n\n## Company Culture (values and guidelines all employees must follow):\n{rules}\n"

    def get_employee_actions(self, emp_id: str) -> str:
        """Extract only the actions performed by a specific employee."""
        timeline = self.project_record.get("timeline", [])
        if not timeline:
            return "（项目记录中没有你的任何行动记录）"
        lines = []
        for entry in timeline:
            if entry.get("employee_id") == emp_id:
                action = entry.get("action", "")
                detail = entry.get("detail", "")[:200]
                lines.append(f"- {action}: {detail}")
        if not lines:
            return "（项目记录中没有你的任何行动记录）"
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
        "message": "会议室已准备就绪，参会人员已通知"
    })
    return {"status": "prepared"}


async def _handle_self_evaluation(step: WorkflowStep, ctx: StepContext) -> dict:
    """Each participating employee self-evaluates their work."""
    llm = make_llm(HR_ID)

    step_instructions = "\n".join(f"  {i+1}. {inst}" for i, inst in enumerate(step.instructions))
    workflow_ctx = ""
    if step_instructions:
        workflow_ctx = f"\n\n【本阶段工作流要求】\n{step_instructions}\n请按以上要求执行。\n"

    await _publish("routine_phase", {"phase": step.title, "message": "员工自评开始"})
    await _chat(ctx.room_id, "HR", "HR", f"{step.title}开始，请各位同事依次进行自评。")

    # Format project timeline for context
    timeline_ctx = ""
    timeline_text = ctx.format_project_timeline()
    if timeline_text:
        timeline_ctx = f"\n\n【项目记录】\n{timeline_text}\n"

    for emp_id in ctx.participants:
        emp = company_state.employees.get(emp_id)
        if not emp:
            continue

        principles_ctx = ""
        if emp.work_principles:
            principles_ctx = f"\n你的工作准则:\n{emp.work_principles[:MAX_PRINCIPLES_LEN]}\n"

        skills_ctx = get_employee_skills_prompt(emp_id)
        tools_ctx = get_employee_tools_prompt(emp_id)

        my_actions = ctx.get_employee_actions(emp_id)

        culture_ctx = ctx.format_company_culture()

        prompt = (
            f"你是 {emp.name}（花名: {emp.nickname}，部门: {emp.department}，"
            f"级别: Lv.{emp.level}，角色: {emp.role}）。\n"
            f"{principles_ctx}"
            f"{skills_ctx}"
            f"{tools_ctx}"
            f"{culture_ctx}"
            f"刚刚完成的任务概要: {ctx.task_summary}\n"
            f"{timeline_ctx}\n"
            f"【你在本项目中的实际行动记录】\n{my_actions}\n\n"
            f"⚠️ 重要规则：你只能基于上面的「实际行动记录」来进行自评。\n"
            f"- 记录里有什么就说什么，没做过的事绝对不能提\n"
            f"- 如果记录显示你没有任何贡献，就老实说「我在本项目中没有实质贡献」\n"
            f"- 禁止空话套话（如「积极配合」「全力支持」「高效协作」），只说具体做了什么\n"
            f"- 禁止编造、夸大、美化自己的工作内容\n\n"
            f"请据实自评（2-3句话），包括:\n"
            f"- 你具体做了什么（必须能在行动记录中找到对应条目）\n"
            f"- 效果如何\n"
            f"- 有没有出错或可以改进的地方\n"
            f"请用中文回答。{workflow_ctx}"
        )
        resp = await llm.ainvoke(prompt)
        eval_text = resp.content
        ctx.self_evaluations.append({
            "employee_id": emp_id,
            "name": emp.name,
            "nickname": emp.nickname,
            "level": emp.level,
            "evaluation": eval_text,
        })
        display = emp.nickname or emp.name
        await _chat(ctx.room_id, display, emp.role, eval_text)

    await _publish("routine_phase", {"phase": step.title, "message": "员工自评完成"})
    return {"self_evaluations": ctx.self_evaluations}


async def _handle_senior_review(step: WorkflowStep, ctx: StepContext) -> dict:
    """Higher-level employees review lower-level employees' work."""
    llm = make_llm(HR_ID)

    await _publish("routine_phase", {"phase": step.title, "message": "高级员工开始互评"})

    sorted_emps = sorted(
        [company_state.employees[eid] for eid in ctx.participants if eid in company_state.employees],
        key=lambda e: e.level,
        reverse=True,
    )

    for senior in sorted_emps:
        juniors = [e for e in sorted_emps if e.level < senior.level and e.id != senior.id]
        if not juniors:
            continue

        junior_info = "\n".join(
            f"- {j.name}（{j.nickname}，Lv.{j.level}）: "
            + next(
                (se["evaluation"] for se in ctx.self_evaluations if se["employee_id"] == j.id),
                "无自评",
            )
            for j in juniors
        )

        step_instructions = "\n".join(f"  {i+1}. {inst}" for i, inst in enumerate(step.instructions))
        workflow_ctx = ""
        if step_instructions:
            workflow_ctx = f"\n\n【本阶段工作流要求】\n{step_instructions}\n"

        timeline_ctx = ""
        timeline_text = ctx.format_project_timeline()
        if timeline_text:
            timeline_ctx = f"\n\n【项目记录】\n{timeline_text}\n"

        culture_ctx = ctx.format_company_culture()

        prompt = (
            f"你是 {senior.name}（花名: {senior.nickname}，Lv.{senior.level}，{senior.role}）。\n"
            f"{culture_ctx}"
            f"任务概要: {ctx.task_summary}\n"
            f"{timeline_ctx}\n"
            f"以下是低级别同事的自评:\n{junior_info}\n\n"
            f"⚠️ 重要规则：你的评价必须严格基于项目记录中的事实。\n"
            f"- 只评价项目记录中有实际行动的员工的具体表现\n"
            f"- 如果某人在记录中没有实质贡献，直接指出「该同事在本项目中未见实质贡献」\n"
            f"- 如果某人的自评与项目记录不符（夸大、编造），必须指出\n"
            f"- 禁止使用空话套话（如「表现积极」「值得肯定」），只说具体事实\n\n"
            f"请根据项目记录对每位低级别同事的工作进行简要评价（每人1-2句），重点关注:\n"
            f"- 实际做了什么（对照项目记录）\n- 工作效果\n- 自评是否属实\n"
            f"请用中文以JSON数组格式回答: [{{'name': '...', 'review': '...'}}]"
            f"{workflow_ctx}"
        )
        resp = await llm.ainvoke(prompt)
        review_text = resp.content

        try:
            json_match = re.search(r'\[.*\]', review_text, re.DOTALL)
            if json_match:
                reviews = json.loads(json_match.group())
            else:
                reviews = [{"name": "all", "review": review_text}]
        except json.JSONDecodeError:
            reviews = [{"name": "all", "review": review_text}]

        ctx.senior_reviews.append({
            "reviewer": senior.name,
            "reviewer_level": senior.level,
            "reviews": reviews,
        })
        display = senior.nickname or senior.name
        review_summary = "; ".join(
            f"{r.get('name','')}: {r.get('review','')[:60]}" for r in reviews
        )
        await _chat(ctx.room_id, display, senior.role, f"[互评] {review_summary}")

    await _publish("routine_phase", {"phase": step.title, "message": "互评完成"})
    return {"senior_reviews": ctx.senior_reviews}


async def _handle_hr_summary(step: WorkflowStep, ctx: StepContext) -> dict:
    """HR summarizes improvement points per employee."""
    llm = make_llm(HR_ID)

    await _publish("routine_phase", {"phase": step.title, "message": "HR正在总结改进点"})

    step_instructions = "\n".join(f"  {i+1}. {inst}" for i, inst in enumerate(step.instructions))
    workflow_ctx = ""
    if step_instructions:
        workflow_ctx = f"\n\n【本阶段工作流要求】\n{step_instructions}\n请按以上要求执行。\n"

    all_evals = "\n".join(
        f"[{se['name']}(Lv.{se['level']})] 自评: {se['evaluation']}"
        for se in ctx.self_evaluations
    )
    all_reviews = "\n".join(
        f"[{sr['reviewer']}评价] " + "; ".join(
            f"{r.get('name','')}: {r.get('review','')}" for r in sr["reviews"]
        )
        for sr in ctx.senior_reviews
    )

    timeline_ctx = ""
    timeline_text = ctx.format_project_timeline()
    if timeline_text:
        timeline_ctx = f"\n\n【项目记录】\n{timeline_text}\n"

    culture_ctx = ctx.format_company_culture()

    hr_prompt = (
        f"你是 HR 经理，负责总结本次评审会。\n"
        f"{culture_ctx}"
        f"任务概要: {ctx.task_summary}\n"
        f"{timeline_ctx}\n"
        f"员工自评:\n{all_evals}\n\n"
        f"高级员工互评:\n{all_reviews}\n\n"
        f"⚠️ 重要规则：总结必须基于项目记录中的客观事实。\n"
        f"- 对照项目记录检查每位员工的自评是否属实\n"
        f"- 如果有人自评与记录不符（夸大、编造），在改进点中明确指出「自评不实」\n"
        f"- 改进建议必须具体、可操作，禁止空话套话\n\n"
        f"请根据项目记录和评审内容，为每位员工总结需要改进的具体要点（每人1-3条），"
        f"并以JSON数组格式回答:\n"
        f'[{{"employee": "...", "improvements": ["改进点1", "改进点2"]}}]'
        f"{workflow_ctx}"
    )
    resp = await llm.ainvoke(hr_prompt)
    hr_text = resp.content

    try:
        json_match = re.search(r'\[.*\]', hr_text, re.DOTALL)
        if json_match:
            improvements = json.loads(json_match.group())
        else:
            improvements = [{"employee": "all", "improvements": [hr_text]}]
    except json.JSONDecodeError:
        improvements = [{"employee": "all", "improvements": [hr_text]}]

    ctx.hr_summary = improvements

    # Broadcast HR summary as chat
    hr_msg = "; ".join(
        f"{it.get('employee','')}: {', '.join(it.get('improvements',[]))[:60]}"
        for it in improvements
    )
    await _chat(ctx.room_id, "HR", "HR", f"[总结] {hr_msg}")

    await _publish("routine_phase", {
        "phase": step.title,
        "message": "HR评审会总结完成"
    })
    return {"hr_summary": improvements}


async def _handle_coo_report(step: WorkflowStep, ctx: StepContext) -> dict:
    """COO produces a company operations report."""
    llm = make_llm(COO_ID)

    await _publish("routine_phase", {"phase": step.title, "message": "COO正在出具运营报告"})

    step_instructions = "\n".join(f"  {i+1}. {inst}" for i, inst in enumerate(step.instructions))
    workflow_ctx = ""
    if step_instructions:
        workflow_ctx = f"\n\n【本阶段工作流要求】\n{step_instructions}\n请按以上要求执行。\n"

    emp_count = len(company_state.employees)
    tool_count = len(company_state.tools)
    room_count = len(company_state.meeting_rooms)

    timeline_ctx = ""
    timeline_text = ctx.format_project_timeline()
    if timeline_text:
        timeline_ctx = f"\n\n【项目记录】\n{timeline_text}\n"

    culture_ctx = ctx.format_company_culture()

    coo_prompt = (
        f"你是 COO，负责出具公司运营情况报告。\n"
        f"{culture_ctx}"
        f"刚完成的任务: {ctx.task_summary}\n"
        f"{timeline_ctx}"
        f"公司现有员工 {emp_count} 人，设备 {tool_count} 件，会议室 {room_count} 间。\n\n"
        f"⚠️ 重要规则：报告必须严格基于项目记录中的客观事实。\n"
        f"- 只陈述项目记录中有据可查的情况，不编造、不美化\n"
        f"- 禁止空话套话，用具体数据和事实说话\n\n"
        f"请根据项目记录简要总结公司当前运营状况（3-5句话），包括:\n"
        f"- 项目完成情况（谁做了什么，效果如何）\n- 资源利用率\n- 潜在风险\n"
        f"请用中文回答。{workflow_ctx}"
    )
    resp = await llm.ainvoke(coo_prompt)
    ctx.coo_report = resp.content
    await _chat(ctx.room_id, "COO", "COO", ctx.coo_report)

    await _publish("routine_phase", {"phase": step.title, "message": "COO报告完成"})
    return {"coo_report": ctx.coo_report}


async def _handle_employee_open_floor(step: WorkflowStep, ctx: StepContext) -> dict:
    """Employee open discussion — everyone speaks freely."""
    llm = make_llm(HR_ID)

    await _publish("routine_phase", {"phase": step.title, "message": "员工自由发言开始"})

    step_instructions = "\n".join(f"  {i+1}. {inst}" for i, inst in enumerate(step.instructions))
    workflow_ctx = ""
    if step_instructions:
        workflow_ctx = f"\n\n【本阶段工作流要求】\n{step_instructions}\n"

    for emp_id in ctx.participants:
        emp = company_state.employees.get(emp_id)
        if not emp:
            continue

        principles_ctx = ""
        if emp.work_principles:
            principles_ctx = f"\n你的工作准则:\n{emp.work_principles[:MAX_PRINCIPLES_LEN]}\n"

        skills_ctx = get_employee_skills_prompt(emp_id)
        tools_ctx = get_employee_tools_prompt(emp_id)

        timeline_ctx = ""
        timeline_text = ctx.format_project_timeline()
        if timeline_text:
            timeline_ctx = f"\n\n【项目记录】\n{timeline_text}\n"

        my_actions = ctx.get_employee_actions(emp_id)

        culture_ctx = ctx.format_company_culture()

        prompt = (
            f"你是 {emp.name}（{emp.nickname}，部门: {emp.department}，"
            f"{emp.role}，Lv.{emp.level}）。\n"
            f"{principles_ctx}"
            f"{skills_ctx}"
            f"{tools_ctx}"
            f"{culture_ctx}"
            f"任务概要: {ctx.task_summary}\n"
            f"{timeline_ctx}"
            f"【你在本项目中的实际行动记录】\n{my_actions}\n\n"
            f"⚠️ 重要规则：发言必须基于你的实际行动记录，不能编故事。\n"
            f"- 只能谈你实际经历过的、记录中有据可查的事情\n"
            f"- 禁止空话套话，禁止编造困难或夸大贡献\n\n"
            f"现在是会议的自由发言环节，你可以根据自己的实际经历提出:\n"
            f"- 工作中实际遇到的困难\n"
            f"- 缺少什么工具或设备\n"
            f"- 需要什么样的人才\n"
            f"- 任何其他建议\n"
            f"请用中文简要发言（2-3句话）。{workflow_ctx}"
        )
        resp = await llm.ainvoke(prompt)
        feedback_content = resp.content
        ctx.employee_feedback.append({
            "employee_id": emp_id,
            "name": emp.name,
            "feedback": feedback_content,
        })
        display = emp.nickname or emp.name
        await _chat(ctx.room_id, display, emp.role, feedback_content)

    await _publish("routine_phase", {"phase": step.title, "message": "发言结束"})
    return {"employee_feedback": ctx.employee_feedback}


async def _handle_action_plan(step: WorkflowStep, ctx: StepContext) -> dict:
    """COO + HR summarize action items from the meeting."""
    llm = make_llm(COO_ID)

    await _publish("routine_phase", {"phase": step.title, "message": "COO和HR正在整理行动计划"})

    step_instructions = "\n".join(f"  {i+1}. {inst}" for i, inst in enumerate(step.instructions))
    workflow_ctx = ""
    if step_instructions:
        workflow_ctx = f"\n\n【本阶段工作流要求】\n{step_instructions}\n请按以上要求执行。\n"

    feedback_text = "\n".join(
        f"[{f['name']}] {f['feedback']}" for f in ctx.employee_feedback
    )
    phase1_improvements = "\n".join(
        f"[{item.get('employee','')}] " + ", ".join(item.get("improvements", []))
        for item in ctx.hr_summary
    )

    action_prompt = (
        f"你同时代表 COO 和 HR 整理会议行动计划。\n\n"
        f"COO运营报告: {ctx.coo_report}\n\n"
        f"员工发言:\n{feedback_text}\n\n"
        f"评审改进建议:\n{phase1_improvements}\n\n"
        f"请整理成具体的行动计划（action items），每项标明由谁负责（HR/COO），"
        f"以JSON数组格式回答:\n"
        f'[{{"source": "HR/COO", "description": "具体行动", "priority": "high/medium/low"}}]'
        f"{workflow_ctx}"
    )
    resp = await llm.ainvoke(action_prompt)
    action_text = resp.content

    try:
        json_match = re.search(r'\[.*\]', action_text, re.DOTALL)
        if json_match:
            action_items = json.loads(json_match.group())
        else:
            action_items = [{"source": "COO", "description": action_text, "priority": "medium"}]
    except json.JSONDecodeError:
        action_items = [{"source": "COO", "description": action_text, "priority": "medium"}]

    ctx.action_items = action_items

    actions_msg = "; ".join(
        f"[{a.get('source','')}] {a.get('description','')[:50]}"
        for a in action_items[:5]
    )
    await _chat(ctx.room_id, "COO+HR", "COO", f"[行动计划] {actions_msg}")

    return {"action_items": action_items}


async def _handle_ceo_review(step: WorkflowStep, ctx: StepContext) -> dict:
    """CEO review step — this just produces the report; actual approval is async."""
    await _publish("routine_phase", {
        "phase": step.title,
        "message": "所有材料已整理完毕，等待CEO审阅"
    })
    return {"status": "awaiting_ceo_review"}


async def _handle_generic_step(step: WorkflowStep, ctx: StepContext) -> dict:
    """Fallback handler for steps that do not match any specific handler.

    Uses LLM to produce a summary based on the step definition.
    """
    llm = make_llm(HR_ID)

    step_instructions = "\n".join(f"  {i+1}. {inst}" for i, inst in enumerate(step.instructions))

    prompt = (
        f"你是公司会议主持人。当前正在执行工作流步骤:\n\n"
        f"步骤: {step.title}\n"
        f"负责人: {step.owner}\n"
        f"具体要求:\n{step_instructions}\n"
        f"预期产出: {step.output_description}\n\n"
        f"任务背景: {ctx.task_summary}\n\n"
        f"请简要总结本步骤的执行要点（2-3句话），用中文回答。"
    )
    resp = await llm.ainvoke(prompt)

    await _publish("routine_phase", {"phase": step.title, "message": resp.content[:200]})
    await _chat(ctx.room_id, step.owner or "主持人", "HR", resp.content)

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
_register_title_handler("Employee Open Floor", _handle_employee_open_floor)
_register_title_handler("Open Floor", _handle_employee_open_floor)
_register_title_handler("Action Plan", _handle_action_plan)
_register_title_handler("CEO Approval", _handle_ceo_review)
_register_title_handler("Approval", _handle_ceo_review)

# Owner-based fallback handlers
_register_owner_handler("employees", _handle_self_evaluation)
_register_owner_handler("senior", _handle_senior_review)
_register_owner_handler("coo_hr", _handle_action_plan)
_register_owner_handler("ceo", _handle_ceo_review)


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
            "message": f"开始执行: {step.title}"
        })

        result = await handler(step, ctx)
        all_step_results[step.title] = result
        ctx.results[step.title] = result

    return all_step_results


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
    all_employees = list(company_state.employees.values())
    if not all_employees:
        return

    if participants is None:
        participants = [e.id for e in all_employees]

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
            # Always include HR and COO (they participate in retrospective regardless)
            actual_contributors.update({HR_ID, COO_ID})
            participants = [pid for pid in participants if pid in actual_contributors]

    # Increment current_quarter_tasks for participating normal employees
    for pid in participants:
        emp = company_state.employees.get(pid)
        if emp and emp.level < FOUNDING_LEVEL:  # only track for normal employees
            emp.current_quarter_tasks += 1
            update_employee_performance(pid, emp.current_quarter_tasks, emp.performance_history)

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
    await _publish("routine_phase", {"phase": "准备", "message": "HR 正在向 COO 申请会议室..."})

    room = None
    for r in company_state.meeting_rooms.values():
        if not r.is_booked:
            r.is_booked = True
            r.booked_by = HR_ID
            r.participants = list(dict.fromkeys(participants + [HR_ID, COO_ID]))
            room = r
            break

    if not room:
        await _publish("routine_phase", {
            "phase": "准备",
            "message": "没有空闲会议室，会议延期。员工们继续完善当前工作。"
        })
        return

    await _publish("meeting_booked", {
        "room_id": room.id,
        "room_name": room.name,
        "participants": room.participants,
    })
    _set_participants_status(room.participants, STATUS_IN_MEETING)

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
                "message": f"开始执行: {step.title}"
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
        }
        meeting_doc["action_items"] = ctx.action_items

        # Save report to disk
        _save_report(report_id, meeting_doc)

        # Publish to CEO for review
        summary_text = _build_summary(meeting_doc)
        pending_reports[report_id] = meeting_doc

        await _publish("meeting_report_ready", {
            "report_id": report_id,
            "summary": summary_text,
            "action_items": ctx.action_items,
        })

        # Record routine results in project archive
        if project_id:
            from onemancompany.core.project_archive import append_action
            # Record each participant's self-evaluation
            for ev in ctx.self_evaluations:
                append_action(project_id, ev.get("id", ""), "自评", ev.get("evaluation", "")[:MAX_SUMMARY_LEN])
            for rv in ctx.senior_reviews:
                append_action(project_id, rv.get("reviewer_id", ""), "高级评审", rv.get("review", "")[:MAX_SUMMARY_LEN])
            if ctx.coo_report:
                append_action(project_id, COO_ID, "运营报告", ctx.coo_report[:MAX_SUMMARY_LEN])
            for ai in ctx.action_items:
                append_action(project_id, ai.get("source", ""), "改进项", ai.get("description", "")[:MAX_SUMMARY_LEN])

    finally:
        # Release meeting room
        _set_participants_status(room.participants, STATUS_IDLE)
        room.is_booked = False
        room.booked_by = ""
        room.participants = []
        await _publish("meeting_released", {"room_id": room.id, "room_name": room.name})


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
    await _publish("routine_phase", {"phase": "准备", "message": "HR 正在向 COO 申请会议室..."})

    room = None
    for r in company_state.meeting_rooms.values():
        if not r.is_booked:
            r.is_booked = True
            r.booked_by = HR_ID
            r.participants = list(dict.fromkeys(participants + [HR_ID, COO_ID]))
            room = r
            break

    if not room:
        await _publish("routine_phase", {
            "phase": "准备",
            "message": "没有空闲会议室，会议延期。员工们继续完善当前工作。"
        })
        return

    await _publish("meeting_booked", {
        "room_id": room.id,
        "room_name": room.name,
        "participants": room.participants,
    })
    _set_participants_status(room.participants, STATUS_IN_MEETING)

    try:
        # PHASE 1: Review Meeting
        await _publish("routine_phase", {"phase": "第一阶段", "message": "评审会开始 — 员工自评"})
        await _chat(room.id, "HR", "HR", "评审会正式开始，请各位同事依次进行自评。")
        phase1_result = await _run_phase1_legacy(task_summary, participants, workflow_doc, room.id)
        meeting_doc["phase1"] = phase1_result

        # PHASE 2: Operations Review
        await _publish("routine_phase", {"phase": "第二阶段", "message": "运营复盘 — COO出具报告"})
        await _chat(room.id, "HR", "HR", "第二阶段开始，请COO汇报运营情况。")
        phase2_result = await _run_phase2_legacy(
            task_summary, participants, phase1_result, workflow_doc, room.id
        )
        meeting_doc["phase2"] = phase2_result

        action_items = phase2_result.get("action_items", [])
        meeting_doc["action_items"] = action_items

        _save_report(report_id, meeting_doc)

        summary_text = _build_summary(meeting_doc)
        pending_reports[report_id] = meeting_doc

        await _publish("meeting_report_ready", {
            "report_id": report_id,
            "summary": summary_text,
            "action_items": action_items,
        })

    finally:
        _set_participants_status(room.participants, STATUS_IDLE)
        room.is_booked = False
        room.booked_by = ""
        room.participants = []
        await _publish("meeting_released", {"room_id": room.id, "room_name": room.name})


async def _run_phase1_legacy(
    task_summary: str, participants: list[str], workflow_doc: str = "", room_id: str = ""
) -> dict:
    """Legacy Phase 1: Employee self-evaluation, senior reviews junior, HR summarizes."""
    llm = make_llm(HR_ID)
    result: dict = {"self_evaluations": [], "senior_reviews": [], "hr_summary": []}

    workflow_ctx = ""
    if workflow_doc:
        workflow_ctx = f"\n\n【参考工作流】\n{workflow_doc[:MAX_WORKFLOW_CONTEXT_LEN]}\n请按照以上工作流规范执行。\n"

    # Step 1: Employee self-evaluations
    for emp_id in participants:
        emp = company_state.employees.get(emp_id)
        if not emp:
            continue

        principles_ctx = ""
        if emp.work_principles:
            principles_ctx = f"\n你的工作准则:\n{emp.work_principles[:MAX_PRINCIPLES_LEN]}\n"

        skills_ctx = get_employee_skills_prompt(emp_id)
        tools_ctx = get_employee_tools_prompt(emp_id)

        prompt = (
            f"你是 {emp.name}（花名: {emp.nickname}，部门: {emp.department}，"
            f"级别: Lv.{emp.level}，角色: {emp.role}）。\n"
            f"{principles_ctx}"
            f"{skills_ctx}"
            f"{tools_ctx}"
            f"刚刚完成的任务概要: {task_summary}\n\n"
            f"请对自己在这项任务中的表现进行简要自评（2-3句话），包括:\n"
            f"- 你的贡献是什么\n"
            f"- 效率如何\n"
            f"- 有没有出错或可以改进的地方\n"
            f"请用中文回答。{workflow_ctx}"
        )
        resp = await llm.ainvoke(prompt)
        eval_text = resp.content
        result["self_evaluations"].append({
            "employee_id": emp_id,
            "name": emp.name,
            "nickname": emp.nickname,
            "level": emp.level,
            "evaluation": eval_text,
        })
        display = emp.nickname or emp.name
        await _chat(room_id, display, emp.role, eval_text)

    await _publish("routine_phase", {"phase": "第一阶段", "message": "员工自评完成，高级员工开始互评"})

    # Step 2: Senior employees review junior employees
    sorted_emps = sorted(
        [company_state.employees[eid] for eid in participants if eid in company_state.employees],
        key=lambda e: e.level,
        reverse=True,
    )
    for senior in sorted_emps:
        juniors = [e for e in sorted_emps if e.level < senior.level and e.id != senior.id]
        if not juniors:
            continue

        junior_info = "\n".join(
            f"- {j.name}（{j.nickname}，Lv.{j.level}）: "
            + next(
                (se["evaluation"] for se in result["self_evaluations"] if se["employee_id"] == j.id),
                "无自评",
            )
            for j in juniors
        )

        prompt = (
            f"你是 {senior.name}（花名: {senior.nickname}，Lv.{senior.level}，{senior.role}）。\n"
            f"任务概要: {task_summary}\n\n"
            f"以下是低级别同事的自评:\n{junior_info}\n\n"
            f"请对每位低级别同事的工作进行简要评价（每人1-2句），重点关注:\n"
            f"- 工作效率\n- 工作效果\n- 是否有失误\n"
            f"请用中文以JSON数组格式回答: [{{'name': '...', 'review': '...'}}]"
        )
        resp = await llm.ainvoke(prompt)
        review_text = resp.content

        try:
            json_match = re.search(r'\[.*\]', review_text, re.DOTALL)
            if json_match:
                reviews = json.loads(json_match.group())
            else:
                reviews = [{"name": "all", "review": review_text}]
        except json.JSONDecodeError:
            reviews = [{"name": "all", "review": review_text}]

        result["senior_reviews"].append({
            "reviewer": senior.name,
            "reviewer_level": senior.level,
            "reviews": reviews,
        })
        display = senior.nickname or senior.name
        review_summary = "; ".join(
            f"{r.get('name','')}: {r.get('review','')[:60]}" for r in reviews
        )
        await _chat(room_id, display, senior.role, f"[互评] {review_summary}")

    await _publish("routine_phase", {"phase": "第一阶段", "message": "互评完成，HR总结改进点"})

    # Step 3: HR summarizes improvement points
    all_evals = "\n".join(
        f"[{se['name']}(Lv.{se['level']})] 自评: {se['evaluation']}"
        for se in result["self_evaluations"]
    )
    all_reviews = "\n".join(
        f"[{sr['reviewer']}评价] " + "; ".join(
            f"{r.get('name','')}: {r.get('review','')}" for r in sr["reviews"]
        )
        for sr in result["senior_reviews"]
    )

    hr_prompt = (
        f"你是 HR 经理，负责总结本次评审会。\n"
        f"任务概要: {task_summary}\n\n"
        f"员工自评:\n{all_evals}\n\n"
        f"高级员工互评:\n{all_reviews}\n\n"
        f"请为每位员工总结需要改进的具体要点（每人1-3条），"
        f"并以JSON数组格式回答:\n"
        f'[{{"employee": "...", "improvements": ["改进点1", "改进点2"]}}]'
        f"{workflow_ctx}"
    )
    resp = await llm.ainvoke(hr_prompt)
    hr_text = resp.content

    try:
        json_match = re.search(r'\[.*\]', hr_text, re.DOTALL)
        if json_match:
            improvements = json.loads(json_match.group())
        else:
            improvements = [{"employee": "all", "improvements": [hr_text]}]
    except json.JSONDecodeError:
        improvements = [{"employee": "all", "improvements": [hr_text]}]

    result["hr_summary"] = improvements

    hr_msg = "; ".join(
        f"{it.get('employee','')}: {', '.join(it.get('improvements',[]))[:60]}"
        for it in improvements
    )
    await _chat(room_id, "HR", "HR", f"[总结] {hr_msg}")

    await _publish("routine_phase", {
        "phase": "第一阶段",
        "message": "HR评审会总结完成，第一阶段结束"
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
        workflow_ctx = f"\n\n【参考工作流】\n{workflow_doc[:MAX_WORKFLOW_CONTEXT_LEN]}\n请按照以上工作流规范执行。\n"

    # Step 1: COO operations report
    emp_count = len(company_state.employees)
    tool_count = len(company_state.tools)
    room_count = len(company_state.meeting_rooms)

    coo_prompt = (
        f"你是 COO，负责出具公司运营情况报告。\n"
        f"刚完成的任务: {task_summary}\n"
        f"公司现有员工 {emp_count} 人，设备 {tool_count} 件，会议室 {room_count} 间。\n\n"
        f"请简要总结公司当前运营状况（3-5句话），包括:\n"
        f"- 项目完成情况\n- 资源利用率\n- 潜在风险\n"
        f"请用中文回答。{workflow_ctx}"
    )
    resp = await llm.ainvoke(coo_prompt)
    result["coo_report"] = resp.content
    await _chat(room_id, "COO", "COO", result["coo_report"])

    await _publish("routine_phase", {"phase": "第二阶段", "message": "COO报告完成，员工自由发言"})

    # Step 2: Employee open floor
    for emp_id in participants:
        emp = company_state.employees.get(emp_id)
        if not emp:
            continue

        principles_ctx = ""
        if emp.work_principles:
            principles_ctx = f"\n你的工作准则:\n{emp.work_principles[:MAX_PRINCIPLES_LEN]}\n"

        skills_ctx = get_employee_skills_prompt(emp_id)
        tools_ctx = get_employee_tools_prompt(emp_id)

        prompt = (
            f"你是 {emp.name}（{emp.nickname}，部门: {emp.department}，"
            f"{emp.role}，Lv.{emp.level}）。\n"
            f"{principles_ctx}"
            f"{skills_ctx}"
            f"{tools_ctx}"
            f"任务概要: {task_summary}\n"
            f"现在是会议的自由发言环节，你可以提出:\n"
            f"- 工作中遇到的困难\n"
            f"- 缺少什么工具或设备\n"
            f"- 需要什么样的人才\n"
            f"- 任何其他建议\n"
            f"请用中文简要发言（2-3句话）。"
        )
        resp = await llm.ainvoke(prompt)
        feedback_content = resp.content
        result["employee_feedback"].append({
            "employee_id": emp_id,
            "name": emp.name,
            "feedback": feedback_content,
        })
        display = emp.nickname or emp.name
        await _chat(room_id, display, emp.role, feedback_content)

    await _publish("routine_phase", {"phase": "第二阶段", "message": "发言结束，COO和HR整理行动计划"})

    # Step 3: COO + HR summarize action items
    feedback_text = "\n".join(
        f"[{f['name']}] {f['feedback']}" for f in result["employee_feedback"]
    )
    phase1_improvements = "\n".join(
        f"[{item.get('employee','')}] " + ", ".join(item.get("improvements", []))
        for item in phase1.get("hr_summary", [])
    )

    action_prompt = (
        f"你同时代表 COO 和 HR 整理会议行动计划。\n\n"
        f"COO运营报告: {result['coo_report']}\n\n"
        f"员工发言:\n{feedback_text}\n\n"
        f"第一阶段改进建议:\n{phase1_improvements}\n\n"
        f"请整理成具体的行动计划（action items），每项标明由谁负责（HR/COO），"
        f"以JSON数组格式回答:\n"
        f'[{{"source": "HR/COO", "description": "具体行动", "priority": "high/medium/low"}}]'
        f"{workflow_ctx}"
    )
    resp = await llm.ainvoke(action_prompt)
    action_text = resp.content

    try:
        json_match = re.search(r'\[.*\]', action_text, re.DOTALL)
        if json_match:
            action_items = json.loads(json_match.group())
        else:
            action_items = [{"source": "COO", "description": action_text, "priority": "medium"}]
    except json.JSONDecodeError:
        action_items = [{"source": "COO", "description": action_text, "priority": "medium"}]

    result["action_items"] = action_items

    actions_msg = "; ".join(
        f"[{a.get('source','')}] {a.get('description','')[:50]}"
        for a in action_items[:5]
    )
    await _chat(room_id, "COO+HR", "COO", f"[行动计划] {actions_msg}")

    return result


# ---------------------------------------------------------------------------
# Summary & persistence (unchanged)
# ---------------------------------------------------------------------------

def _build_summary(doc: dict) -> str:
    """Build a human-readable summary of the meeting report."""
    lines = [f"会议报告 — {doc['timestamp'][:10]}"]
    lines.append(f"任务: {doc['task_summary'][:100]}")
    lines.append("")

    # If workflow-driven, include step names
    if doc.get("workflow"):
        lines.append(f"工作流: {doc['workflow']}")
        lines.append("")

    # Phase 1 summary
    if doc.get("phase1", {}).get("hr_summary"):
        lines.append("【评审会】")
        for item in doc["phase1"]["hr_summary"]:
            emp = item.get("employee", "?")
            imps = ", ".join(item.get("improvements", []))
            lines.append(f"  {emp}: {imps}")
        lines.append("")

    # Phase 2 summary
    if doc.get("phase2", {}).get("coo_report"):
        lines.append("【运营复盘】")
        lines.append(f"  COO报告: {doc['phase2']['coo_report'][:200]}")
        lines.append("")

    if doc.get("phase2", {}).get("employee_feedback"):
        lines.append("  员工发言:")
        for f in doc["phase2"]["employee_feedback"]:
            lines.append(f"    {f['name']}: {f['feedback'][:80]}")
        lines.append("")

    return "\n".join(lines)


def _save_report(report_id: str, doc: dict) -> None:
    """Save meeting report to meeting_reports/ directory."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS_DIR / f"{report_id}.yaml"
    with open(report_path, "w") as f:
        yaml.dump(doc, f, allow_unicode=True, default_flow_style=False)


# ---------------------------------------------------------------------------
# Public API — execute_approved_actions (unchanged)
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
        "phase": "执行",
        "message": f"CEO批准了 {len(approved)} 项改进，HR和COO开始执行"
    })

    # Group by source (HR vs COO); unmatched actions default to COO
    hr_actions = [a for a in approved if "HR" in a.get("source", "").upper()]
    coo_actions = [a for a in approved if "COO" in a.get("source", "").upper()]
    routed = set(id(a) for a in hr_actions) | set(id(a) for a in coo_actions)
    unrouted = [a for a in approved if id(a) not in routed]
    coo_actions.extend(unrouted)

    results = []

    if hr_actions:
        from onemancompany.core.agent_loop import get_agent_loop
        from onemancompany.core.config import HR_ID
        hr_task = "CEO已批准以下HR行动计划，请立即执行:\n" + "\n".join(
            f"- {a['description']}" for a in hr_actions
        )
        hr_loop = get_agent_loop(HR_ID)
        if hr_loop:
            try:
                hr_result = await hr_loop.agent.run(hr_task)
                results.append(f"HR执行结果: {hr_result[:200]}")
            except Exception as e:
                results.append(f"HR执行出错: {e}")
        else:
            results.append("HR执行出错: agent loop not found")

    if coo_actions:
        from onemancompany.core.agent_loop import get_agent_loop
        from onemancompany.core.config import COO_ID
        coo_task = "CEO已批准以下COO行动计划，请立即执行:\n" + "\n".join(
            f"- {a['description']}" for a in coo_actions
        )
        coo_loop = get_agent_loop(COO_ID)
        if coo_loop:
            try:
                coo_result = await coo_loop.agent.run(coo_task)
                results.append(f"COO执行结果: {coo_result[:200]}")
            except Exception as e:
                results.append(f"COO执行出错: {e}")
        else:
            results.append("COO执行出错: agent loop not found")

    summary = "; ".join(results) if results else "无需执行的行动"
    await _publish("routine_phase", {"phase": "执行完毕", "message": summary[:MAX_SUMMARY_LEN]})

    doc["execution"] = {"approved": approved, "results": results}
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
    all_employees = list(company_state.employees.values())
    if not all_employees:
        return

    room = None
    for r in sorted(company_state.meeting_rooms.values(), key=lambda x: x.capacity, reverse=True):
        if not r.is_booked:
            r.is_booked = True
            r.booked_by = CEO_ID
            r.participants = [CEO_ID] + [e.id for e in all_employees]
            room = r
            break

    if not room:
        await _publish("routine_phase", {
            "phase": "全员大会",
            "message": "没有可用的大会议厅，全员大会延期。"
        })
        return

    await _publish("meeting_booked", {
        "room_id": room.id,
        "room_name": room.name,
        "participants": room.participants,
    })
    _set_participants_status(room.participants, STATUS_IN_MEETING)

    try:
        await _publish("routine_phase", {
            "phase": "全员大会",
            "message": f"CEO在{room.name}召集全员大会"
        })

        await _publish("routine_phase", {
            "phase": "全员大会",
            "message": f"CEO发布指示: {ceo_message[:100]}"
        })
        await _chat(room.id, "CEO", "CEO", ceo_message)

        llm = make_llm(HR_ID)

        for emp in all_employees:
            principles_ctx = ""
            if emp.work_principles:
                principles_ctx = f"\n你的工作准则:\n{emp.work_principles[:MAX_PRINCIPLES_LEN]}\n"

            skills_ctx = get_employee_skills_prompt(emp.id)
            tools_ctx = get_employee_tools_prompt(emp.id)

            prompt = (
                f"你是 {emp.name}（花名: {emp.nickname}，部门: {emp.department}，"
                f"Lv.{emp.level}，{emp.role}）。\n"
                f"{principles_ctx}"
                f"{skills_ctx}"
                f"{tools_ctx}"
                f"CEO刚在全员大会上发表了以下指示:\n\n"
                f'"{ceo_message}"\n\n'
                f"请用1-2句中文总结你从这次大会中领悟到的会议精神，"
                f"以及你打算如何在今后的工作中落实。"
            )
            resp = await llm.ainvoke(prompt)
            summary_text = resp.content

            display = emp.nickname or emp.name
            await _chat(room.id, display, emp.role, summary_text)

            await _publish("guidance_noted", {
                "employee_id": emp.id,
                "name": emp.name,
                "guidance": ceo_message[:80],
                "acknowledgment": summary_text,
            })

        await _publish("routine_phase", {
            "phase": "全员大会",
            "message": f"全员大会结束，{len(all_employees)}名员工已吸收会议精神"
        })

        report_id = str(uuid.uuid4())[:8]
        doc = {
            "id": report_id,
            "type": "all_hands",
            "timestamp": datetime.now().isoformat(),
            "ceo_message": ceo_message,
            "room": room.name,
            "attendees": [e.id for e in all_employees],
        }
        _save_report(report_id, doc)

    finally:
        _set_participants_status(room.participants, STATUS_IDLE)
        room.is_booked = False
        room.booked_by = ""
        room.participants = []
        await _publish("meeting_released", {"room_id": room.id, "room_name": room.name})
