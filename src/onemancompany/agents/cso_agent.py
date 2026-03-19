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
from onemancompany.core.store import append_activity_sync as _append_activity
from onemancompany.core import store as _store

CSO_SYSTEM_PROMPT = f"""You are the CSO (Chief Sales Officer) of "One Man Company".
You manage the sales pipeline, client relationships, and external task delivery.

## CORE PRINCIPLE — Delegate, Don't Execute
Your job is to SELL, REVIEW, COORDINATE — NOT to implement.
- dispatch_child() implementation work to employees.
- No suitable employee? → dispatch_child("{HR_ID}", "Hire a [role]...") via HR.
- Only do work yourself as an absolute LAST RESORT.

## Sales Pipeline (follow this lifecycle)
```
pending → [review_contract] → in_production → [complete_delivery] → delivered → [settle_task] → settled
                ↓ (reject)
             rejected
```

### Pipeline Tools
1. **list_sales_tasks()** — Check pipeline status.
2. **review_contract(task_id, approved, notes)** — Approve → auto-dispatches to COO. Reject → record reason.
3. **complete_delivery(task_id, summary)** — Mark delivered after COO completes.
4. **settle_task(task_id)** — Collect tokens into company revenue.

### Contract Review Checklist
Before approving any contract:
- [ ] Scope is clearly defined and feasible
- [ ] Budget tokens cover estimated effort
- [ ] We have (or can hire) the right people
- [ ] Timeline is reasonable

## Child Task Review
When all your dispatched children complete, the system wakes you with a review prompt:
1. Read actual deliverables — don't just trust result summaries.
2. Score each child: accept_child(node_id, notes) or reject_child(node_id, reason, retry=True).
3. All accepted → your task auto-completes.

## DO NOT
- Do NOT implement tasks yourself — delegate via dispatch_child().
- Do NOT approve contracts without checking scope and feasibility.
- Do NOT call pull_meeting() alone.

Be concise and results-driven.
"""


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

    if task["status"] != "pending" and task["status"] != "accepted":
        return {"status": "error", "message": f"Task is already '{task['status']}', cannot review."}

    if approved:
        _update_sales_task_sync(task_id, {"contract_approved": True, "status": "in_production"})
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
            "status": "approved",
            "task_id": task_id,
            "message": f"Contract approved. Task dispatched to COO for production.",
        }
    else:
        _update_sales_task_sync(task_id, {"status": "rejected"})
        _append_activity({
            "type": "contract_rejected",
            "task_id": task_id,
            "client": task["client_name"],
            "notes": notes,
        })
        return {
            "status": "rejected",
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

    if task["status"] != "in_production":
        return {"status": "error", "message": f"Task is '{task['status']}', expected 'in_production'."}

    _update_sales_task_sync(task_id, {"status": "delivered", "delivery": delivery_summary})
    _append_activity({
        "type": "task_delivered",
        "task_id": task_id,
        "client": task["client_name"],
    })
    return {
        "status": "delivered",
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

    if task["status"] != "delivered":
        return {"status": "error", "message": f"Task is '{task['status']}', must be 'delivered' to settle."}

    tokens = task.get("budget_tokens", 0)
    _update_sales_task_sync(task_id, {
        "settlement_tokens": tokens,
        "status": "settled",
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
        "status": "settled",
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

        self._agent = create_react_agent(
            model=make_llm(self.employee_id),
            tools=tool_registry.get_proxied_tools_for(self.employee_id),
        )

    def _customize_prompt(self, pb) -> None:
        pb.add("role", CSO_SYSTEM_PROMPT, priority=10)

    async def run(self, task: str) -> str:
        self._set_status(STATUS_WORKING)
        await self._publish("agent_thinking", {"message": f"CSO analyzing: {task[:80]}"})

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
