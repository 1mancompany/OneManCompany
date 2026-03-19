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
    MANIFEST_YAML_FILENAME,
    SYSTEM_AGENT,
    TOOL_YAML_FILENAME,
    TOOLS_DIR,
    move_employee_to_ex,
)
from onemancompany.core.events import CompanyEvent, event_bus
from onemancompany.core.models import EventType
from onemancompany.core.layout import compute_layout
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
    emp_data = _store.load_employee(employee_id)
    if not emp_data:
        return {"error": f"Employee '{employee_id}' not found"}

    if emp_data.get("level", 1) >= FOUNDING_LEVEL:
        return {"error": f"Cannot fire founding employee (Lv.{emp_data.get('level', 1)})"}

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

        manifest_path = EMPLOYEES_DIR / employee_id / "tools" / MANIFEST_YAML_FILENAME
        if manifest_path.exists():
            with open(manifest_path) as f:
                mdata = yaml.safe_load(f) or {}
            for tool_name in mdata.get("custom_tools", []):
                unregister_tool_user(tool_name, employee_id)
                # Clean up orphaned personal tools (no remaining users)
                tool_yaml_path = TOOLS_DIR / tool_name / TOOL_YAML_FILENAME
                if tool_yaml_path.exists():
                    with open(tool_yaml_path) as f:
                        tool_data = yaml.safe_load(f) or {}
                    if (tool_data.get("source_talent") and
                            "allowed_users" in tool_data and
                            len(tool_data.get("allowed_users", [])) == 0):
                        shutil.rmtree(TOOLS_DIR / tool_name, ignore_errors=True)
    except Exception:
        logger.debug("Failed to unregister tools for %s", employee_id)

    # Move to ex-employees (folder + store)
    name = emp_data.get("name", "")
    nickname = emp_data.get("nickname", "")
    role = emp_data.get("role", "")
    # Persist ex-employee profile via store before moving folder
    await _store.save_ex_employee(employee_id, emp_data)
    move_employee_to_ex(employee_id)

    # Post-fire cleanup — employee is already gone, so wrap to ensure we return success
    try:
        compute_layout(company_state)
    except Exception as e:
        logger.warning("compute_layout after fire failed: {}", e)

    try:
        await _store.append_activity({
            "type": "employee_fired",
            "name": name,
            "nickname": nickname,
            "role": role,
            "reason": reason,
        })

        await event_bus.publish(CompanyEvent(
            type=EventType.EMPLOYEE_FIRED,
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
            CompanyEvent(type=EventType.STATE_SNAPSHOT, payload={}, agent=SYSTEM_AGENT)
        )
    except Exception as e:
        logger.warning("Post-fire event publishing failed for {}: {}", employee_id, e)

    logger.info("Fired employee %s (%s) — reason: %s", employee_id, name, reason)

    return {
        "status": "fired",
        "id": employee_id,
        "name": name,
        "nickname": nickname,
        "role": role,
        "reason": reason,
    }
