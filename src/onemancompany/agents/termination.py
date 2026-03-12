"""Employee termination — code-driven fire flow.

Standalone function for dismissing employees. Extracted from hr_agent.py
so it can be called both by the HR agent and directly via the API.
"""

from __future__ import annotations

import shutil

import yaml

from onemancompany.core.config import (
    EMPLOYEES_DIR,
    FOUNDING_LEVEL,
    TOOLS_DIR,
    move_employee_to_ex,
)
from onemancompany.core.events import CompanyEvent, event_bus
from onemancompany.core.layout import compute_layout, persist_all_desk_positions
from onemancompany.core.state import company_state
from onemancompany.core import store as _store

from loguru import logger


async def execute_fire(employee_id: str, reason: str = "CEO decision") -> dict:
    """Execute employee termination.

    1. Validate: employee exists and is not founding (Lv.4+)
    2. Stop running agent tasks (cancel via EmployeeManager)
    3. Move to ex-employees (in-memory + disk)
    4. Recompute office layout and persist desk positions
    5. Write activity log entry
    6. Publish employee_fired event + state_snapshot

    Returns:
        {"status": "fired", "name": ..., "nickname": ...} on success,
        {"error": "..."} on failure.
    """
    emp = company_state.employees.get(employee_id)
    if not emp:
        return {"error": f"Employee '{employee_id}' not found"}

    if emp.level >= FOUNDING_LEVEL:
        return {"error": f"Cannot fire founding employee (Lv.{emp.level})"}

    # Stop any running agent tasks for this employee
    try:
        from onemancompany.core.agent_loop import employee_manager
        running = employee_manager._running_tasks.get(employee_id)
        if running:
            running.cancel()
            employee_manager._running_tasks.pop(employee_id, None)
        employee_manager.unregister(employee_id)
    except Exception:
        logger.debug("No agent loop to stop for %s", employee_id)

    # Unregister employee from any central custom tools
    try:
        from onemancompany.agents.onboarding import unregister_tool_user

        manifest_path = EMPLOYEES_DIR / employee_id / "tools" / "manifest.yaml"
        if manifest_path.exists():
            with open(manifest_path) as f:
                mdata = yaml.safe_load(f) or {}
            for tool_name in mdata.get("custom_tools", []):
                unregister_tool_user(tool_name, employee_id)
                # Clean up orphaned personal tools (no remaining users)
                tool_yaml_path = TOOLS_DIR / tool_name / "tool.yaml"
                if tool_yaml_path.exists():
                    with open(tool_yaml_path) as f:
                        tool_data = yaml.safe_load(f) or {}
                    if (tool_data.get("source_talent") and
                            "allowed_users" in tool_data and
                            len(tool_data.get("allowed_users", [])) == 0):
                        shutil.rmtree(TOOLS_DIR / tool_name, ignore_errors=True)
    except Exception:
        logger.debug("Failed to unregister tools for %s", employee_id)

    # Move to ex-employees (state + folder + store)
    name, nickname, role = emp.name, emp.nickname, emp.role
    # Persist ex-employee profile via store before moving folder
    await _store.save_ex_employee(employee_id, emp.to_dict())
    # Keep in-memory dicts in sync until Task 10 removes them
    company_state.ex_employees[employee_id] = emp
    del company_state.employees[employee_id]
    move_employee_to_ex(employee_id)

    # Recompute layout (zones may shrink) and persist all positions
    compute_layout(company_state)
    persist_all_desk_positions(company_state)

    company_state.activity_log.append({
        "type": "employee_fired",
        "name": name,
        "nickname": nickname,
        "role": role,
        "reason": reason,
    })

    await event_bus.publish(CompanyEvent(
        type="employee_fired",
        payload={
            "id": employee_id,
            "name": name,
            "nickname": nickname,
            "role": role,
            "reason": reason,
        },
        agent="HR",
    ))

    await event_bus.publish(
        CompanyEvent(type="state_snapshot", payload={}, agent="SYSTEM")
    )

    logger.info("Fired employee %s (%s) — reason: %s", employee_id, name, reason)

    return {
        "status": "fired",
        "id": employee_id,
        "name": name,
        "nickname": nickname,
        "role": role,
        "reason": reason,
    }
