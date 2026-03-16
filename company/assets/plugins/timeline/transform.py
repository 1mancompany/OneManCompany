"""Timeline view data transformer."""

from __future__ import annotations


def transform(dispatches: list[dict], context: dict) -> dict:
    """Transform dispatches into timeline data.

    Returns:
        {"timeline": [{dispatch_id, employee_name, description, phase, start, end}]}
    """
    employees = context.get("employees", {})

    timeline = []
    for d in dispatches:
        if d.get("dispatched_at"):
            emp_id = d.get("employee_id", "")
            emp = employees.get(emp_id, {})
            emp_name = emp.get("nickname") or emp.get("name") or emp_id
            timeline.append({
                "dispatch_id": d.get("dispatch_id", ""),
                "employee_id": emp_id,
                "employee_name": emp_name,
                "description": d.get("description", "")[:60],
                "phase": d.get("phase", 1),
                "start": d.get("dispatched_at"),
                "end": d.get("completed_at"),
                "task_type": d.get("task_type", "execution"),
                "status": d.get("status", "completed"),
                "depends_on": d.get("depends_on", []),
                "estimated_duration_min": d.get("estimated_duration_min", 0),
                "scheduled_start": d.get("scheduled_start"),
            })

    return {"timeline": timeline}
