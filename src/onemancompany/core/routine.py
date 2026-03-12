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
    return f"\n\n【本阶段工作流要求】\n{lines}\n请按以上要求执行。\n"


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

    workflow_ctx = _format_workflow_context(step)

    await _publish("routine_phase", {"phase": step.title, "message": "员工自评开始"})
    await _chat(ctx.room_id, "HR", "HR", f"{step.title}开始，请各位同事依次进行自评。")

    # Format project timeline for context
    timeline_ctx = ""
    timeline_text = ctx.format_project_timeline()
    if timeline_text:
        timeline_ctx = f"\n\n【项目记录】\n{timeline_text}\n"

    for emp_id in ctx.participants:
        emp_data = load_employee(emp_id)
        if not emp_data:
            continue

        work_principles = emp_data.get("work_principles", "")
        principles_ctx = ""
        if work_principles:
            principles_ctx = f"\n你的工作准则:\n{work_principles[:MAX_PRINCIPLES_LEN]}\n"

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
            f"你是 {emp_name}（花名: {emp_nickname}，部门: {emp_dept}，"
            f"级别: Lv.{emp_level}，角色: {emp_role}）。\n"
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

    await _publish("routine_phase", {"phase": step.title, "message": "员工自评完成"})
    return {"self_evaluations": ctx.self_evaluations}


async def _handle_senior_review(step: WorkflowStep, ctx: StepContext) -> dict:
    """Higher-level employees review lower-level employees' work."""
    llm = make_llm(HR_ID)

    await _publish("routine_phase", {"phase": step.title, "message": "高级员工开始互评"})

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
                "无自评",
            )
            for jid, jd in juniors
        )

        workflow_ctx = _format_workflow_context(step)

        timeline_ctx = ""
        timeline_text = ctx.format_project_timeline()
        if timeline_text:
            timeline_ctx = f"\n\n【项目记录】\n{timeline_text}\n"

        culture_ctx = ctx.format_company_culture()

        prompt = (
            f"你是 {senior_data.get('name', '')}（花名: {senior_data.get('nickname', '')}，Lv.{senior_level}，{senior_data.get('role', '')}）。\n"
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
        await _chat(ctx.room_id, display, senior_data.get("role", ""), f"[互评] {review_summary}")

    await _publish("routine_phase", {"phase": step.title, "message": "互评完成"})
    return {"senior_reviews": ctx.senior_reviews}


async def _handle_hr_summary(step: WorkflowStep, ctx: StepContext) -> dict:
    """HR summarizes improvement points per employee."""
    llm = make_llm(HR_ID)

    await _publish("routine_phase", {"phase": step.title, "message": "HR正在总结改进点"})

    workflow_ctx = _format_workflow_context(step)

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
    resp = await tracked_ainvoke(llm, hr_prompt, category="routine", employee_id=HR_ID)
    hr_text = resp.content

    improvements = _parse_json_array(hr_text, [{"employee": "all", "improvements": [hr_text]}])

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

    workflow_ctx = _format_workflow_context(step)

    emp_count = len(load_all_employees())
    tool_count = len(company_state.tools)
    room_count = len(_store.load_rooms())

    timeline_ctx = ""
    timeline_text = ctx.format_project_timeline()
    if timeline_text:
        timeline_ctx = f"\n\n【项目记录】\n{timeline_text}\n"

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
            cost_lines = [f"预算: ${budget:.4f}, 实际: ${actual:.4f}"]
            cost_lines.append(f"Token用量: input={tokens.get('input', 0)}, output={tokens.get('output', 0)}")
            for entry in breakdown:
                emp_data = load_employee(entry.get("employee_id", ""))
                name = emp_data.get("name", entry.get("employee_id", "?")) if emp_data else entry.get("employee_id", "?")
                cost_lines.append(f"  - {name}: {entry.get('model', '?')}, {entry.get('total_tokens', 0)} tokens, ${entry.get('cost_usd', 0):.4f}")
            cost_ctx = "\n\n项目开销数据:\n" + "\n".join(cost_lines) + "\n"

    coo_prompt = (
        f"你是 COO，负责出具公司运营情况报告。\n"
        f"{culture_ctx}"
        f"刚完成的任务: {ctx.task_summary}\n"
        f"{timeline_ctx}"
        f"{cost_ctx}"
        f"公司现有员工 {emp_count} 人，设备 {tool_count} 件，会议室 {room_count} 间。\n\n"
        f"⚠️ 重要规则：报告必须严格基于项目记录中的客观事实。\n"
        f"- 只陈述项目记录中有据可查的情况，不编造、不美化\n"
        f"- 禁止空话套话，用具体数据和事实说话\n\n"
        f"请根据项目记录简要总结公司当前运营状况（3-5句话），包括:\n"
        f"- 项目完成情况（谁做了什么，效果如何）\n- 资源利用率\n- 潜在风险\n"
        f"- 项目开销分析（如有数据），评估是否超出预算\n"
        f"请用中文回答。{workflow_ctx}"
    )
    resp = await tracked_ainvoke(llm, coo_prompt, category="routine", employee_id=COO_ID)
    ctx.coo_report = resp.content
    await _chat(ctx.room_id, "COO", "COO", ctx.coo_report)

    await _publish("routine_phase", {"phase": step.title, "message": "COO报告完成"})
    return {"coo_report": ctx.coo_report}


async def _handle_asset_consolidation(step: WorkflowStep, ctx: StepContext) -> dict:
    """COO reviews project workspace files and suggests assets worth preserving."""
    from onemancompany.core.project_archive import list_project_files, get_project_dir

    project_id = ctx.project_record.get("id", "") or ctx.project_record.get("project_id", "")
    if not project_id:
        await _chat(ctx.room_id, "COO", "COO", "[资产沉淀] 无项目ID，跳过资产沉淀环节。")
        return {"asset_suggestions": []}

    await _publish("routine_phase", {"phase": step.title, "message": "COO正在审视项目产物"})

    files = list_project_files(project_id)
    if not files:
        await _chat(ctx.room_id, "COO", "COO", "[资产沉淀] 项目工作区无文件，跳过。")
        return {"asset_suggestions": []}

    project_dir = get_project_dir(project_id)
    file_list_text = "\n".join(f"- {f}" for f in files)

    workflow_ctx = _format_workflow_context(step)

    llm = make_llm(COO_ID)
    prompt = (
        f"你是 COO，负责审视项目产物并判断哪些值得沉淀为公司资产。\n\n"
        f"项目概要: {ctx.task_summary}\n"
        f"项目工作区: {project_dir}\n\n"
        f"项目文件列表:\n{file_list_text}\n\n"
        f"请审视以上文件，判断哪些值得注册为公司资产（工具、模板、参考代码等）。\n"
        f"评判标准:\n"
        f"- 具有复用价值（其他项目可以用到）\n"
        f"- 是可独立运行的工具、脚本、模板\n"
        f"- 不是临时文件、日志、配置文件\n\n"
        f"如果没有值得沉淀的文件，返回空数组 []。\n"
        f"否则以JSON数组格式返回建议:\n"
        f'[{{"name": "资产名称", "description": "简要描述用途", "files": ["file1.py", "file2.md"]}}]\n'
        f"只返回JSON数组，不要其他内容。{workflow_ctx}"
    )
    resp = await tracked_ainvoke(llm, prompt, category="routine", employee_id=COO_ID)
    raw = resp.content

    suggestions = _parse_json_array(raw)

    ctx.asset_suggestions = suggestions

    if suggestions:
        names = ", ".join(s.get("name", "?") for s in suggestions)
        await _chat(ctx.room_id, "COO", "COO", f"[资产沉淀建议] {names}")
    else:
        await _chat(ctx.room_id, "COO", "COO", "[资产沉淀] 本次项目无需沉淀资产。")

    await _publish("routine_phase", {"phase": step.title, "message": "资产沉淀审视完成"})
    return {"asset_suggestions": suggestions}


async def _handle_employee_open_floor(step: WorkflowStep, ctx: StepContext) -> dict:
    """Employee open discussion — everyone speaks freely."""
    llm = make_llm(HR_ID)

    await _publish("routine_phase", {"phase": step.title, "message": "员工自由发言开始"})

    workflow_ctx = _format_workflow_context(step)

    for emp_id in ctx.participants:
        emp_data = load_employee(emp_id)
        if not emp_data:
            continue

        work_principles = emp_data.get("work_principles", "")
        principles_ctx = ""
        if work_principles:
            principles_ctx = f"\n你的工作准则:\n{work_principles[:MAX_PRINCIPLES_LEN]}\n"

        skills_ctx = get_employee_skills_prompt(emp_id)
        tools_ctx = get_employee_tools_prompt(emp_id)

        timeline_ctx = ""
        timeline_text = ctx.format_project_timeline()
        if timeline_text:
            timeline_ctx = f"\n\n【项目记录】\n{timeline_text}\n"

        my_actions = ctx.get_employee_actions(emp_id)

        culture_ctx = ctx.format_company_culture()

        emp_name = emp_data.get("name", "")
        emp_nickname = emp_data.get("nickname", "")
        emp_dept = emp_data.get("department", "")
        emp_role = emp_data.get("role", "")
        emp_level = emp_data.get("level", 1)

        prompt = (
            f"你是 {emp_name}（{emp_nickname}，部门: {emp_dept}，"
            f"{emp_role}，Lv.{emp_level}）。\n"
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
        resp = await tracked_ainvoke(llm, prompt, category="routine", employee_id=emp_id)
        feedback_content = resp.content
        ctx.employee_feedback.append({
            "employee_id": emp_id,
            "name": emp_name,
            "feedback": feedback_content,
        })
        display = emp_nickname or emp_name
        await _chat(ctx.room_id, display, emp_role, feedback_content)

    await _publish("routine_phase", {"phase": step.title, "message": "发言结束"})
    return {"employee_feedback": ctx.employee_feedback}


async def _handle_action_plan(step: WorkflowStep, ctx: StepContext) -> dict:
    """COO + HR summarize action items from the meeting."""
    llm = make_llm(COO_ID)

    await _publish("routine_phase", {"phase": step.title, "message": "COO和HR正在整理行动计划"})

    workflow_ctx = _format_workflow_context(step)

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
                "description": f"沉淀项目资产: {suggestion.get('name', '')} — {suggestion.get('description', '')}",
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
    await _chat(ctx.room_id, "COO+HR", "COO", f"[行动计划] {actions_msg}")

    return {"action_items": action_items}


async def _handle_ea_approval(step: WorkflowStep, ctx: StepContext) -> dict:
    """EA reviews and approves meeting action items on behalf of CEO."""
    from onemancompany.core.agent_loop import get_agent_loop

    if not ctx.action_items:
        await _publish("routine_phase", {
            "phase": step.title,
            "message": "无待审批行动计划，跳过EA审批环节"
        })
        await _chat(ctx.room_id, "EA", "EA", "本次会议无需审批的行动计划。")
        return {"status": "no_actions", "approved": [], "rejected": [], "skipped_duplicates": []}

    # Dedup: filter out items already proposed in past meetings
    unique_items, dup_items, recurring_items = _dedup_action_items(ctx.action_items)

    if dup_items:
        dup_descs = "; ".join(d.get("description", "")[:40] for d in dup_items)
        await _chat(ctx.room_id, "EA", "EA",
                    f"[去重] 跳过 {len(dup_items)} 项已提出过的改进: {dup_descs}")
        await _publish("routine_phase", {
            "phase": step.title,
            "message": f"去重跳过 {len(dup_items)} 项重复改进"
        })

    # Recurring items (proposed 2+ times before) — escalate to CEO
    if recurring_items:
        recurring_descs = "\n".join(f"  - {r.get('description', '')[:80]}" for r in recurring_items)
        await _chat(ctx.room_id, "EA", "EA",
                    f"[警告] 以下 {len(recurring_items)} 项改进已多次提出但未能解决，需CEO关注:\n{recurring_descs}")
        await _publish("recurring_action_items", {
            "items": [r.get("description", "") for r in recurring_items],
            "message": f"{len(recurring_items)} 项改进反复出现，可能无法通过常规方式解决，请CEO决策",
        })

    if not unique_items:
        await _chat(ctx.room_id, "EA", "EA", "所有改进项均已在之前会议中提出过，无新行动计划。")
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
        f"  {i+1}. [{a.get('source', '')}] {a.get('description', '')} (优先级: {a.get('priority', '')})"
        for i, a in enumerate(unique_items)
    )

    workflow_ctx = _format_workflow_context(step)

    prompt = (
        "你是EA（行政助理），代表CEO严格审核会议行动计划。\n\n"
        "⚠️ 审批核心原则：改进项不是越多越好，而是越精越关键越好。\n"
        "CEO最关注的是提高组织效率，一切不直接服务于此目标的行动都应被否决。\n\n"
        f"会议概要: {ctx.task_summary}\n\n"
        f"COO运营报告: {ctx.coo_report}\n\n"
        f"待审核行动计划:\n{items_text}\n\n"
        "严格审核标准（必须全部满足才能批准）:\n"
        "1. 具体可执行：有明确的执行步骤，不是空话套话（如「加强管理」「提高效率」「优化流程」等空泛表述一律否决）\n"
        "2. 直接相关：必须与本次项目的实际问题直接相关，不是泛泛而谈的通用建议\n"
        "3. 可衡量效果：执行后能明确看到效果，有判断成功/失败的标准\n"
        "4. 投入产出合理：改进带来的收益必须大于执行成本\n"
        "5. 无重复矛盾：不与其他行动项重复或矛盾\n\n"
        "应该否决的典型例子:\n"
        "- 「加强代码审查流程」→ 太空泛，怎么加强？具体做什么？\n"
        "- 「提升团队协作能力」→ 假大空，没有实际行动\n"
        "- 「优化项目管理机制」→ 形式主义，不解决具体问题\n"
        "- 与本项目无关的通用改进建议\n\n"
        "请严格审核，宁可少批不可多批。以JSON格式返回你的决定:\n"
        '{"approved_indices": [0, 1, ...], "rejected_indices": [2, ...], "reason": "审核说明"}\n'
        "approved_indices 是你批准的行动编号（0-based），rejected_indices 是你否决的。\n"
        f"只返回JSON，不要其他内容。{workflow_ctx}"
    )

    await _publish("routine_phase", {
        "phase": step.title,
        "message": "EA正在审核行动计划"
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
            ea_reason = "EA未返回有效JSON，默认全部批准"
    except json.JSONDecodeError:
        approved_indices = list(range(len(ctx.action_items)))
        ea_reason = "EA返回格式错误，默认全部批准"

    approved = [ctx.action_items[i] for i in approved_indices if i < len(ctx.action_items)]
    rejected = [ctx.action_items[i] for i in rejected_indices if i < len(ctx.action_items)]

    # Chat announcement
    await _chat(ctx.room_id, "EA", "EA",
                f"[审批结果] 批准 {len(approved)} 项，否决 {len(rejected)} 项。{ea_reason}")

    if not approved:
        await _publish("routine_phase", {
            "phase": step.title,
            "message": "EA未批准任何行动计划"
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
            "phase": "资产沉淀",
            "message": f"已注册 {len(asset_results)} 项公司资产"
        })

    # 2. Push remaining actions to COO for dispatch
    if remaining_actions:
        action_lines = []
        for a in remaining_actions:
            source = a.get("source", "COO")
            action_lines.append(f"- [{source}] {a['description']}")

        coo_task = (
            "EA已批准以下行动计划。请按source字段分配执行：\n"
            "- source=HR的行动：使用dispatch_child()分派给HR（employee_id='00002'）\n"
            "- source=COO的行动：由你自己执行\n\n"
            "行动计划:\n" + "\n".join(action_lines)
        )

        coo_loop = get_agent_loop(COO_ID)
        if coo_loop:
            coo_loop.push_task(coo_task)
            await _chat(ctx.room_id, "EA", "EA",
                        f"已推送 {len(remaining_actions)} 项批准行动到COO任务板")

    await _publish("routine_phase", {
        "phase": step.title,
        "message": f"EA审批完成：批准 {len(approved)} 项，否决 {len(rejected)} 项"
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
        f"你是公司会议主持人。当前正在执行工作流步骤:\n\n"
        f"步骤: {step.title}\n"
        f"负责人: {step.owner}\n"
        f"具体要求:\n{step_instructions}\n"
        f"预期产出: {step.output_description}\n\n"
        f"任务背景: {ctx.task_summary}\n\n"
        f"请简要总结本步骤的执行要点（2-3句话），用中文回答。"
    )
    resp = await tracked_ainvoke(llm, prompt, category="routine", employee_id=HR_ID)

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
    await _publish("routine_phase", {"phase": "准备", "message": "HR 正在向 COO 申请会议室..."})

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
            "phase": "准备",
            "message": "没有空闲会议室，会议延期。员工们继续完善当前工作。"
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
                append_action(project_id, ev.get("id", ""), "自评", ev.get("evaluation", "")[:MAX_SUMMARY_LEN])
            for rv in ctx.senior_reviews:
                append_action(project_id, rv.get("reviewer_id", ""), "高级评审", rv.get("review", "")[:MAX_SUMMARY_LEN])
            if ctx.coo_report:
                append_action(project_id, COO_ID, "运营报告", ctx.coo_report[:MAX_SUMMARY_LEN])
            for ai in ctx.action_items:
                append_action(project_id, ai.get("source", ""), "改进项", ai.get("description", "")[:MAX_SUMMARY_LEN])

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
                    f"[去重] 跳过 {len(dup_items)} 项已提出过的改进: {dup_descs}")

    if recurring_items:
        recurring_descs = "\n".join(f"  - {r.get('description', '')[:80]}" for r in recurring_items)
        await _chat(room_id, "EA", "EA",
                    f"[警告] 以下 {len(recurring_items)} 项改进已多次提出但未能解决，需CEO关注:\n{recurring_descs}")
        await _publish("recurring_action_items", {
            "items": [r.get("description", "") for r in recurring_items],
            "message": f"{len(recurring_items)} 项改进反复出现，可能无法通过常规方式解决，请CEO决策",
        })

    if not unique_items:
        await _chat(room_id, "EA", "EA", "所有改进项均已在之前会议中提出过，无新行动计划。")
        return {"approved": [], "rejected_count": 0, "skipped_duplicates": len(dup_items), "reason": "全部为重复项"}

    action_items = unique_items

    llm = make_llm(EA_ID)

    items_text = "\n".join(
        f"  {i+1}. [{a.get('source', '')}] {a.get('description', '')} (优先级: {a.get('priority', '')})"
        for i, a in enumerate(action_items)
    )

    prompt = (
        "你是EA（行政助理），代表CEO严格审核会议行动计划。\n\n"
        "⚠️ 审批核心原则：改进项不是越多越好，而是越精越关键越好。\n"
        "CEO最关注的是提高组织效率，一切不直接服务于此目标的行动都应被否决。\n\n"
        f"会议概要: {task_summary}\n\n"
        f"COO运营报告: {coo_report}\n\n"
        f"待审核行动计划:\n{items_text}\n\n"
        "严格审核标准（必须全部满足才能批准）:\n"
        "1. 具体可执行：有明确的执行步骤，不是空话套话（如「加强管理」「提高效率」「优化流程」等空泛表述一律否决）\n"
        "2. 直接相关：必须与本次项目的实际问题直接相关，不是泛泛而谈的通用建议\n"
        "3. 可衡量效果：执行后能明确看到效果，有判断成功/失败的标准\n"
        "4. 投入产出合理：改进带来的收益必须大于执行成本\n"
        "5. 无重复矛盾：不与其他行动项重复或矛盾\n\n"
        "应该否决的典型例子:\n"
        "- 「加强代码审查流程」→ 太空泛，怎么加强？具体做什么？\n"
        "- 「提升团队协作能力」→ 假大空，没有实际行动\n"
        "- 「优化项目管理机制」→ 形式主义，不解决具体问题\n"
        "- 与本项目无关的通用改进建议\n\n"
        "请严格审核，宁可少批不可多批。以JSON格式返回你的决定:\n"
        '{"approved_indices": [0, 1, ...], "rejected_indices": [2, ...], "reason": "审核说明"}\n'
        "approved_indices 是你批准的行动编号（0-based），rejected_indices 是你否决的。\n"
        "只返回JSON，不要其他内容。"
    )

    await _publish("routine_phase", {"phase": "EA审批", "message": "EA正在审核行动计划"})

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
            ea_reason = "EA未返回有效JSON，默认全部批准"
    except json.JSONDecodeError:
        approved_indices = list(range(len(action_items)))
        ea_reason = "EA返回格式错误，默认全部批准"

    approved = [action_items[i] for i in approved_indices if i < len(action_items)]

    await _chat(room_id, "EA", "EA",
                f"[审批结果] 批准 {len(approved)} 项，否决 {len(rejected_indices)} 项。{ea_reason}")

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
                "EA已批准以下行动计划。请按source字段分配执行：\n"
                "- source=HR的行动：使用dispatch_child()分派给HR（employee_id='00002'）\n"
                "- source=COO的行动：由你自己执行\n\n"
                "行动计划:\n" + "\n".join(action_lines)
            )
            coo_loop = get_agent_loop(COO_ID)
            if coo_loop:
                coo_loop.push_task(coo_task)

    await _publish("routine_phase", {
        "phase": "EA审批",
        "message": f"EA审批完成：批准 {len(approved)} 项，否决 {len(rejected_indices)} 项"
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
    await _publish("routine_phase", {"phase": "准备", "message": "HR 正在向 COO 申请会议室..."})

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
            "phase": "准备",
            "message": "没有空闲会议室，会议延期。员工们继续完善当前工作。"
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
        workflow_ctx = f"\n\n【参考工作流】\n{workflow_doc[:MAX_WORKFLOW_CONTEXT_LEN]}\n请按照以上工作流规范执行。\n"

    # Step 1: Employee self-evaluations
    for emp_id in participants:
        emp_data = load_employee(emp_id)
        if not emp_data:
            continue

        work_principles = emp_data.get("work_principles", "")
        principles_ctx = ""
        if work_principles:
            principles_ctx = f"\n你的工作准则:\n{work_principles[:MAX_PRINCIPLES_LEN]}\n"

        skills_ctx = get_employee_skills_prompt(emp_id)
        tools_ctx = get_employee_tools_prompt(emp_id)

        emp_name = emp_data.get("name", "")
        emp_nickname = emp_data.get("nickname", "")
        emp_dept = emp_data.get("department", "")
        emp_level = emp_data.get("level", 1)
        emp_role = emp_data.get("role", "")

        prompt = (
            f"你是 {emp_name}（花名: {emp_nickname}，部门: {emp_dept}，"
            f"级别: Lv.{emp_level}，角色: {emp_role}）。\n"
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

    await _publish("routine_phase", {"phase": "第一阶段", "message": "员工自评完成，高级员工开始互评"})

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
                "无自评",
            )
            for jid, jd in juniors
        )

        prompt = (
            f"你是 {senior_data.get('name', '')}（花名: {senior_data.get('nickname', '')}，Lv.{senior_level}，{senior_data.get('role', '')}）。\n"
            f"任务概要: {task_summary}\n\n"
            f"以下是低级别同事的自评:\n{junior_info}\n\n"
            f"请对每位低级别同事的工作进行简要评价（每人1-2句），重点关注:\n"
            f"- 工作效率\n- 工作效果\n- 是否有失误\n"
            f"请用中文以JSON数组格式回答: [{{'name': '...', 'review': '...'}}]"
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
        await _chat(room_id, display, senior_data.get("role", ""), f"[互评] {review_summary}")

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
    resp = await tracked_ainvoke(llm, hr_prompt, category="routine", employee_id=HR_ID)
    hr_text = resp.content

    improvements = _parse_json_array(hr_text, [{"employee": "all", "improvements": [hr_text]}])

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
    emp_count = len(load_all_employees())
    tool_count = len(company_state.tools)
    room_count = len(_store.load_rooms())

    coo_prompt = (
        f"你是 COO，负责出具公司运营情况报告。\n"
        f"刚完成的任务: {task_summary}\n"
        f"公司现有员工 {emp_count} 人，设备 {tool_count} 件，会议室 {room_count} 间。\n\n"
        f"请简要总结公司当前运营状况（3-5句话），包括:\n"
        f"- 项目完成情况\n- 资源利用率\n- 潜在风险\n"
        f"请用中文回答。{workflow_ctx}"
    )
    resp = await tracked_ainvoke(llm, coo_prompt, category="routine", employee_id=COO_ID)
    result["coo_report"] = resp.content
    await _chat(room_id, "COO", "COO", result["coo_report"])

    await _publish("routine_phase", {"phase": "第二阶段", "message": "COO报告完成，员工自由发言"})

    # Step 2: Employee open floor
    for emp_id in participants:
        emp_data = load_employee(emp_id)
        if not emp_data:
            continue

        work_principles = emp_data.get("work_principles", "")
        principles_ctx = ""
        if work_principles:
            principles_ctx = f"\n你的工作准则:\n{work_principles[:MAX_PRINCIPLES_LEN]}\n"

        skills_ctx = get_employee_skills_prompt(emp_id)
        tools_ctx = get_employee_tools_prompt(emp_id)

        emp_name = emp_data.get("name", "")
        emp_nickname = emp_data.get("nickname", "")
        emp_dept = emp_data.get("department", "")
        emp_role = emp_data.get("role", "")
        emp_level = emp_data.get("level", 1)

        prompt = (
            f"你是 {emp_name}（{emp_nickname}，部门: {emp_dept}，"
            f"{emp_role}，Lv.{emp_level}）。\n"
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
        resp = await tracked_ainvoke(llm, prompt, category="routine", employee_id=emp_id)
        feedback_content = resp.content
        result["employee_feedback"].append({
            "employee_id": emp_id,
            "name": emp_name,
            "feedback": feedback_content,
        })
        display = emp_nickname or emp_name
        await _chat(room_id, display, emp_role, feedback_content)

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

    # Asset consolidation suggestions
    asset_suggestions = doc.get("asset_suggestions") or doc.get("phase2", {}).get("asset_suggestions", [])
    if asset_suggestions:
        lines.append("【资产沉淀建议】")
        for s in asset_suggestions:
            files = ", ".join(s.get("files", []))
            lines.append(f"  {s.get('name', '?')}: {s.get('description', '')} (文件: {files})")
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
        "phase": "执行",
        "message": f"CEO批准了 {len(approved)} 项改进，HR和COO开始执行"
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
            "phase": "资产沉淀",
            "message": f"已注册 {len(asset_results)} 项公司资产"
        })

    if not remaining_actions:
        summary = f"已执行 {len(asset_results)} 项资产沉淀，无其他行动计划"
        await _publish("routine_phase", {"phase": "执行完毕", "message": summary[:MAX_SUMMARY_LEN]})
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
        "CEO已批准以下行动计划。请按source字段分配执行：\n"
        "- source=HR的行动：使用dispatch_child()分派给HR（employee_id='00002'）\n"
        "- source=COO的行动：由你自己执行\n\n"
        "行动计划:\n" + "\n".join(action_lines)
    )

    coo_loop = get_agent_loop(COO_ID)
    if coo_loop:
        coo_loop.push_task(coo_task)
        summary = f"已推送 {len(remaining_actions)} 项批准行动到COO任务板"
    else:
        summary = "COO agent loop not found"

    if asset_results:
        summary += f"，已注册 {len(asset_results)} 项公司资产"

    await _publish("routine_phase", {"phase": "执行完毕", "message": summary[:MAX_SUMMARY_LEN]})

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
            "phase": "全员大会",
            "message": "没有可用的大会议厅，全员大会延期。"
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
            "phase": "全员大会",
            "message": f"CEO在{room.name}召集全员大会"
        })

        await _publish("routine_phase", {
            "phase": "全员大会",
            "message": f"CEO发布指示: {ceo_message[:100]}"
        })
        await _chat(room.id, "CEO", "CEO", ceo_message)

        llm = make_llm(HR_ID)

        for emp_id, emp_data in all_emps.items():
            work_principles = emp_data.get("work_principles", "")
            principles_ctx = ""
            if work_principles:
                principles_ctx = f"\n你的工作准则:\n{work_principles[:MAX_PRINCIPLES_LEN]}\n"

            skills_ctx = get_employee_skills_prompt(emp_id)
            tools_ctx = get_employee_tools_prompt(emp_id)

            emp_name = emp_data.get("name", "")
            emp_nickname = emp_data.get("nickname", "")

            prompt = (
                f"你是 {emp_name}（花名: {emp_nickname}，部门: {emp_data.get('department', '')}，"
                f"Lv.{emp_data.get('level', 1)}，{emp_data.get('role', '')}）。\n"
                f"{principles_ctx}"
                f"{skills_ctx}"
                f"{tools_ctx}"
                f"CEO刚在全员大会上发表了以下指示:\n\n"
                f'"{ceo_message}"\n\n'
                f"请用1-2句中文总结你从这次大会中领悟到的会议精神，"
                f"以及你打算如何在今后的工作中落实。"
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
            "phase": "全员大会",
            "message": f"全员大会结束，{len(all_emps)}名员工已吸收会议精神"
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
