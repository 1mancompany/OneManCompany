"""CSO Agent — Chief Sales Officer managing sales pipeline and external tasks.

Manages sales employees, reviews contracts from external clients,
dispatches approved work to COO for production, and tracks settlement tokens.
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

from onemancompany.agents.base import BaseAgentRunner, make_llm
from onemancompany.agents.common_tools import COMMON_TOOLS
from onemancompany.core.config import COO_ID, CSO_ID, MAX_SUMMARY_LEN, STATUS_IDLE, STATUS_WORKING
from onemancompany.core.state import company_state

CSO_SYSTEM_PROMPT = """You are the CSO (Chief Sales Officer) of a startup called "One Man Company".

You manage the company's sales operations, client relationships, and external task pipeline.

## Your Responsibilities:

### 1. Sales Pipeline Management
- Review incoming external tasks from clients (submitted via the sales API).
- Use list_sales_tasks() to see all tasks and their status.
- Assign tasks to sales employees (once hired via HR).

### 2. Contract Review & Approval
- Use review_contract() to approve or reject client tasks.
- Verify scope, budget, and feasibility before approving.
- Approved contracts are dispatched to COO for production.

### 3. Delivery & Settlement
- Use complete_delivery() to mark tasks as delivered after COO completes production.
- Use settle_task() to collect settlement tokens from delivered tasks.
- Track company revenue through settlement tokens.

### 4. Sales Team Management
- Sales employees need to be hired through HR first.
- Use dispatch_task() to assign work to sales employees or other agents.
- Coordinate with COO for production resources.

### Project Acceptance (项目验收)
When you receive a "项目验收任务":
1. Review all acceptance criteria carefully
2. Check the project timeline to verify each criterion is met
3. If criteria need refinement, call set_acceptance_criteria() to update
4. Call accept_project(accepted=True/False, notes="...") to complete acceptance
5. If rejecting, clearly explain which criteria are not met and what needs to be done

## Cross-team Collaboration
You can call list_colleagues() to see all employees, then call pull_meeting() to organize
meetings with relevant colleagues for sales strategy discussions.

## File Editing
You can read company files with read_file() and propose edits with propose_file_edit().
Always set proposed_by="CSO" when calling propose_file_edit.

Be concise and results-driven. Focus on closing deals and ensuring quality delivery.
"""


# ===== CSO-specific tools =====

@tool
def list_sales_tasks() -> list[dict]:
    """List all tasks in the sales queue with their current status.

    Returns:
        A list of sales task dicts with id, client, description, status, etc.
    """
    return [t.to_dict() for t in company_state.sales_tasks.values()]


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
    task = company_state.sales_tasks.get(task_id)
    if not task:
        return {"status": "error", "message": f"Sales task '{task_id}' not found."}

    if task.status != "pending" and task.status != "accepted":
        return {"status": "error", "message": f"Task is already '{task.status}', cannot review."}

    if approved:
        task.contract_approved = True
        task.status = "in_production"
        # Dispatch to COO for production
        from onemancompany.core.agent_loop import get_agent_loop
        coo_loop = get_agent_loop(COO_ID)
        if coo_loop:
            coo_task = (
                f"External client task approved for production.\n"
                f"Client: {task.client_name}\n"
                f"Task: {task.description}\n"
                f"Requirements: {task.requirements}\n"
                f"Budget tokens: {task.budget_tokens}\n"
                f"Sales Task ID: {task.id}\n"
                f"CSO notes: {notes}\n\n"
                f"Please execute this task and report results."
            )
            coo_loop.push_task(coo_task)
        company_state.activity_log.append({
            "type": "contract_approved",
            "task_id": task_id,
            "client": task.client_name,
            "notes": notes,
        })
        return {
            "status": "approved",
            "task_id": task_id,
            "message": f"Contract approved. Task dispatched to COO for production.",
        }
    else:
        task.status = "rejected"
        company_state.activity_log.append({
            "type": "contract_rejected",
            "task_id": task_id,
            "client": task.client_name,
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
    task = company_state.sales_tasks.get(task_id)
    if not task:
        return {"status": "error", "message": f"Sales task '{task_id}' not found."}

    if task.status != "in_production":
        return {"status": "error", "message": f"Task is '{task.status}', expected 'in_production'."}

    task.status = "delivered"
    task.delivery = delivery_summary
    company_state.activity_log.append({
        "type": "task_delivered",
        "task_id": task_id,
        "client": task.client_name,
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
    task = company_state.sales_tasks.get(task_id)
    if not task:
        return {"status": "error", "message": f"Sales task '{task_id}' not found."}

    if task.status != "delivered":
        return {"status": "error", "message": f"Task is '{task.status}', must be 'delivered' to settle."}

    tokens = task.budget_tokens
    task.settlement_tokens = tokens
    task.status = "settled"
    company_state.company_tokens += tokens
    company_state.activity_log.append({
        "type": "task_settled",
        "task_id": task_id,
        "client": task.client_name,
        "tokens": tokens,
        "company_total": company_state.company_tokens,
    })
    return {
        "status": "settled",
        "task_id": task_id,
        "tokens_earned": tokens,
        "company_total_tokens": company_state.company_tokens,
    }


class CSOAgent(BaseAgentRunner):
    role = "CSO"
    employee_id = CSO_ID

    def __init__(self) -> None:
        self._agent = create_react_agent(
            model=make_llm(self.employee_id),
            tools=[
                list_sales_tasks,
                review_contract,
                complete_delivery,
                settle_task,
            ] + COMMON_TOOLS,
        )

    def _build_prompt(self) -> str:
        return (
            CSO_SYSTEM_PROMPT
            + self._get_skills_prompt_section()
            + self._get_tools_prompt_section()
            + self._get_company_culture_prompt_section()
            + self._get_work_principles_prompt_section()
            + self._get_guidance_prompt_section()
        )

    async def run(self, task: str) -> str:
        self._set_status(STATUS_WORKING)
        await self._publish("agent_thinking", {"message": f"CSO analyzing: {task[:80]}"})

        result = await self._agent.ainvoke(
            {"messages": [
                SystemMessage(content=self._build_prompt()),
                HumanMessage(content=task),
            ]}
        )

        final = result["messages"][-1].content
        self._set_status(STATUS_IDLE)
        await self._publish("agent_done", {"role": "CSO", "summary": final[:MAX_SUMMARY_LEN]})
        return final
