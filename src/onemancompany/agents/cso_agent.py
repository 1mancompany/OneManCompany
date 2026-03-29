"""CSO Agent — Chief Sales Officer managing sales pipeline and external tasks.

Manages sales employees, reviews contracts from external clients,
dispatches approved work to COO for production, and tracks settlement tokens.
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

from onemancompany.agents.base import BaseAgentRunner, extract_final_content, make_llm
from onemancompany.core.config import COO_ID, CSO_ID, HR_ID, MAX_SUMMARY_LEN, STATUS_IDLE, STATUS_WORKING
from onemancompany.core.models import DecisionStatus
from onemancompany.core.store import append_activity_sync as _append_activity
from onemancompany.core import store as _store

# ---------------------------------------------------------------------------
# Single-file constants — sales pipeline statuses
# ---------------------------------------------------------------------------
SALES_STATUS_PENDING = "pending"
SALES_STATUS_ACCEPTED = "accepted"
SALES_STATUS_IN_PRODUCTION = "in_production"
SALES_STATUS_DELIVERED = "delivered"
SALES_STATUS_SETTLED = "settled"


# CSO operational prompt is now in employees/00005/role_guide.md (loaded by _get_role_identity_section)


# ===== Sales task helpers (disk-backed) =====


def _get_sales_task(task_id: str) -> dict | None:
    """Load a single sales task by ID from disk."""
    tasks = _store.load_sales_tasks()
    for t in tasks:
        if t.get("id") == task_id:
            return t
    return None


def _update_sales_task_sync(task_id: str, updates: dict) -> None:
    """Update a sales task on disk (sync wrapper for use in LangChain tools)."""
    import asyncio

    tasks = _store.load_sales_tasks()
    for t in tasks:
        if t.get("id") == task_id:
            t.update(updates)
            break

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_store.save_sales_tasks(tasks))
    except RuntimeError:
        _store.save_sales_tasks_sync(tasks)


def _load_overhead_tokens() -> int:
    """Load company_tokens from overhead.yaml."""
    data = _store.load_overhead()
    return data.get("company_tokens", 0)


def _save_overhead_tokens_sync(tokens: int) -> None:
    """Save company_tokens to overhead.yaml (sync wrapper)."""
    import asyncio
    data = _store.load_overhead()
    data["company_tokens"] = tokens

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_store.save_overhead(data))
    except RuntimeError:
        _store.save_overhead_sync(data)


# ===== CSO-specific tools =====

@tool
def list_sales_tasks() -> list[dict]:
    """List all tasks in the sales queue with their current status.

    Returns:
        A list of sales task dicts with id, client, description, status, etc.
    """
    return _store.load_sales_tasks()


@tool
def review_contract(task_id: str, approved: bool, notes: str = "") -> dict:
    """Review a sales task contract. If approved, dispatch to COO for production.

    Args:
        task_id: The sales task ID to review.
        approved: True to approve, False to reject.
        notes: Review notes or rejection reason.

    Returns:
        Review result with updated task status.
    """
    task = _get_sales_task(task_id)
    if not task:
        return {"status": "error", "message": f"Sales task '{task_id}' not found."}

    if task["status"] != SALES_STATUS_PENDING and task["status"] != SALES_STATUS_ACCEPTED:
        return {"status": "error", "message": f"Task is already '{task['status']}', cannot review."}

    if approved:
        _update_sales_task_sync(task_id, {"contract_approved": True, "status": SALES_STATUS_IN_PRODUCTION})
        # Dispatch to COO for production
        from onemancompany.core.agent_loop import get_agent_loop
        coo_loop = get_agent_loop(COO_ID)
        if coo_loop:
            coo_task = (
                f"External client task approved for production.\n"
                f"Client: {task['client_name']}\n"
                f"Task: {task['description']}\n"
                f"Requirements: {task.get('requirements', '')}\n"
                f"Budget tokens: {task.get('budget_tokens', 0)}\n"
                f"Sales Task ID: {task['id']}\n"
                f"CSO notes: {notes}\n\n"
                f"Please execute this task and report results."
            )
            coo_loop.push_task(coo_task)
        _append_activity({
            "type": "contract_approved",
            "task_id": task_id,
            "client": task["client_name"],
            "notes": notes,
        })
        return {
            "status": DecisionStatus.APPROVED.value,
            "task_id": task_id,
            "message": f"Contract approved. Task dispatched to COO for production.",
        }
    else:
        _update_sales_task_sync(task_id, {"status": DecisionStatus.REJECTED.value})
        _append_activity({
            "type": "contract_rejected",
            "task_id": task_id,
            "client": task["client_name"],
            "notes": notes,
        })
        return {
            "status": DecisionStatus.REJECTED.value,
            "task_id": task_id,
            "message": f"Contract rejected. Reason: {notes}",
        }


@tool
def complete_delivery(task_id: str, delivery_summary: str) -> dict:
    """Mark a sales task as delivered with a summary of what was delivered.

    Args:
        task_id: The sales task ID.
        delivery_summary: Summary of the deliverable.

    Returns:
        Updated task status.
    """
    task = _get_sales_task(task_id)
    if not task:
        return {"status": "error", "message": f"Sales task '{task_id}' not found."}

    if task["status"] != SALES_STATUS_IN_PRODUCTION:
        return {"status": "error", "message": f"Task is '{task['status']}', expected '{SALES_STATUS_IN_PRODUCTION}'."}

    _update_sales_task_sync(task_id, {"status": SALES_STATUS_DELIVERED, "delivery": delivery_summary})
    _append_activity({
        "type": "task_delivered",
        "task_id": task_id,
        "client": task["client_name"],
    })
    return {
        "status": SALES_STATUS_DELIVERED,
        "task_id": task_id,
        "message": f"Task marked as delivered. Ready for settlement.",
    }


@tool
def settle_task(task_id: str) -> dict:
    """Collect settlement tokens for a delivered task.

    Args:
        task_id: The sales task ID to settle.

    Returns:
        Settlement result with tokens credited.
    """
    task = _get_sales_task(task_id)
    if not task:
        return {"status": "error", "message": f"Sales task '{task_id}' not found."}

    if task["status"] != SALES_STATUS_DELIVERED:
        return {"status": "error", "message": f"Task is '{task['status']}', must be '{SALES_STATUS_DELIVERED}' to settle."}

    tokens = task.get("budget_tokens", 0)
    _update_sales_task_sync(task_id, {
        "settlement_tokens": tokens,
        "status": SALES_STATUS_SETTLED,
    })
    current_tokens = _load_overhead_tokens()
    new_total = current_tokens + tokens
    _save_overhead_tokens_sync(new_total)
    _append_activity({
        "type": "task_settled",
        "task_id": task_id,
        "client": task["client_name"],
        "tokens": tokens,
        "company_total": new_total,
    })
    return {
        "status": SALES_STATUS_SETTLED,
        "task_id": task_id,
        "tokens_earned": tokens,
        "company_total_tokens": new_total,
    }


def _register_cso_tools() -> None:
    from onemancompany.core.tool_registry import ToolMeta, tool_registry

    for t in [list_sales_tasks, review_contract, complete_delivery, settle_task]:
        tool_registry.register(t, ToolMeta(name=t.name, category="role", allowed_roles=["CSO"]))


_register_cso_tools()


class CSOAgent(BaseAgentRunner):
    role = "CSO"
    employee_id = CSO_ID

    def __init__(self) -> None:
        from onemancompany.core.tool_registry import tool_registry

        self._agent_tools = tool_registry.get_proxied_tools_for(self.employee_id)
        self._agent = create_react_agent(
            model=make_llm(self.employee_id),
            tools=self._agent_tools,
        )

    def _get_role_identity_section(self) -> str:
        from onemancompany.core.config import EMPLOYEES_DIR, read_text_utf
        guide_path = EMPLOYEES_DIR / self.employee_id / "role_guide.md"
        if guide_path.exists():
            return read_text_utf(guide_path)
        return ""

    def _customize_prompt(self, pb) -> None:
        pass  # All CSO prompt content is in role_guide.md

    async def run(self, task: str) -> str:
        self._set_status(STATUS_WORKING)
        await self._publish("agent_thinking", {"message": f"CSO analyzing: {task}"})

        result = await self._agent.ainvoke(
            {"messages": [
                SystemMessage(content=self._build_full_prompt()),
                HumanMessage(content=task),
            ]}
        )

        self._extract_and_record_usage(result)
        final = extract_final_content(result)
        self._set_status(STATUS_IDLE)
        await self._publish("agent_done", {"role": "CSO", "summary": final[:MAX_SUMMARY_LEN]})
        return final
