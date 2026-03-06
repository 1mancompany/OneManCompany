"""Kanban board data transformer."""

from __future__ import annotations


def transform(dispatches: list[dict], context: dict) -> dict:
    """Transform dispatches into kanban board columns + phase summary.

    Returns:
        {"columns": {status: [cards]}, "phases": [{phase, total, completed}]}
    """
    employees = context.get("employees", {})

    # Group by status
    columns: dict[str, list[dict]] = {"pending": [], "in_progress": [], "completed": []}
    for d in dispatches:
        status = d.get("status", "in_progress")
        emp_id = d.get("employee_id", "")
        emp = employees.get(emp_id, {})
        emp_name = emp.get("nickname") or emp.get("name") or emp_id

        card = {
            "dispatch_id": d.get("dispatch_id", ""),
            "employee_id": emp_id,
            "employee_name": emp_name,
            "description": d.get("description", "")[:100],
            "phase": d.get("phase", 1),
            "dispatched_at": d.get("dispatched_at"),
            "completed_at": d.get("completed_at"),
        }
        if status in columns:
            columns[status].append(card)
        else:
            columns.setdefault(status, []).append(card)

    # Phase summary
    phases: dict[int, dict] = {}
    for d in dispatches:
        p = d.get("phase", 1)
        if p not in phases:
            phases[p] = {"total": 0, "completed": 0}
        phases[p]["total"] += 1
        if d.get("status") == "completed":
            phases[p]["completed"] += 1

    return {
        "columns": columns,
        "phases": [{"phase": p, **v} for p, v in sorted(phases.items())],
    }
