"""Project Manager tools — query project archive for tracking and supervision.

Provides LangChain @tool functions that give the PM agent read access to
all projects, iterations, dispatches, costs, and timelines.
"""

from __future__ import annotations

from datetime import datetime

from langchain_core.tools import tool


@tool
def pm_list_projects(status_filter: str = "") -> dict:
    """List all projects with summary status. Gives a portfolio overview.

    Args:
        status_filter: Optional filter — "active", "completed", "archived", or "" for all.

    Returns:
        A dict with a list of project summaries.
    """
    from onemancompany.core.project_archive import list_projects

    projects = list_projects()
    if status_filter:
        projects = [p for p in projects if p.get("status") == status_filter]

    active = sum(1 for p in projects if p.get("status") in ("active", "in_progress"))
    completed = sum(1 for p in projects if p.get("status") == "completed")
    total_cost = sum(p.get("cost_usd", 0.0) for p in projects)

    return {
        "status": "ok",
        "total_projects": len(projects),
        "active": active,
        "completed": completed,
        "total_cost_usd": round(total_cost, 4),
        "projects": projects,
    }


@tool
def pm_get_project_status(project_id: str) -> dict:
    """Get detailed status of a specific project including iterations, dispatches, cost, and timeline.

    Works with both v1 project IDs and v2 named project slugs.

    Args:
        project_id: The project ID or slug.

    Returns:
        Detailed project status with iterations, dispatches, cost breakdown, and timeline.
    """
    from onemancompany.core.project_archive import (
        list_project_files,
        load_iteration,
        load_named_project,
        load_project,
    )

    # Try as named project first
    named = load_named_project(project_id)
    if named:
        iterations_detail = []
        total_cost = 0.0
        all_dispatches = []
        for iter_id in named.get("iterations", []):
            iter_doc = load_iteration(project_id, iter_id)
            if not iter_doc:
                continue
            iter_cost = iter_doc.get("cost", {}).get("actual_cost_usd", 0.0)
            total_cost += iter_cost
            dispatches = iter_doc.get("dispatches", [])
            all_dispatches.extend(dispatches)
            iterations_detail.append({
                "iteration_id": iter_id,
                "task": iter_doc.get("task", ""),
                "status": iter_doc.get("status", ""),
                "current_owner": iter_doc.get("current_owner", ""),
                "created_at": iter_doc.get("created_at", ""),
                "completed_at": iter_doc.get("completed_at"),
                "cost_usd": round(iter_cost, 4),
                "budget_usd": iter_doc.get("cost", {}).get("budget_estimate_usd", 0.0),
                "dispatch_count": len(dispatches),
                "dispatches_pending": sum(1 for d in dispatches if d.get("status") == "in_progress"),
                "acceptance_result": iter_doc.get("acceptance_result"),
                "ea_review_result": iter_doc.get("ea_review_result"),
                "files": list_project_files(iter_id) if iter_doc.get("project_dir") else [],
            })

        pending_dispatches = [d for d in all_dispatches if d.get("status") == "in_progress"]
        return {
            "status": "ok",
            "type": "named_project",
            "project_id": project_id,
            "name": named.get("name", ""),
            "project_status": named.get("status", ""),
            "created_at": named.get("created_at", ""),
            "iteration_count": len(named.get("iterations", [])),
            "iterations": iterations_detail,
            "total_cost_usd": round(total_cost, 4),
            "total_dispatches": len(all_dispatches),
            "pending_dispatches": len(pending_dispatches),
            "pending_dispatch_details": pending_dispatches,
        }

    # Try as v1 or iteration
    doc = load_project(project_id)
    if not doc:
        return {"status": "error", "message": f"Project '{project_id}' not found."}

    dispatches = doc.get("dispatches", [])
    cost = doc.get("cost", {})
    return {
        "status": "ok",
        "type": "v1_project",
        "project_id": project_id,
        "task": doc.get("task", ""),
        "project_status": doc.get("status", ""),
        "current_owner": doc.get("current_owner", ""),
        "created_at": doc.get("created_at", ""),
        "completed_at": doc.get("completed_at"),
        "cost_usd": round(cost.get("actual_cost_usd", 0.0), 4),
        "budget_usd": cost.get("budget_estimate_usd", 0.0),
        "dispatches": dispatches,
        "pending_dispatches": sum(1 for d in dispatches if d.get("status") == "in_progress"),
        "timeline_count": len(doc.get("timeline", [])),
        "timeline_last_5": doc.get("timeline", [])[-5:],
        "acceptance_result": doc.get("acceptance_result"),
        "ea_review_result": doc.get("ea_review_result"),
        "files": list_project_files(project_id),
    }


@tool
def pm_check_stale_dispatches(stale_hours: float = 2.0) -> dict:
    """Find dispatches that have been in_progress for too long across all projects.

    Scans all projects and iterations for dispatches that haven't completed
    within the threshold. Useful for identifying stuck work.

    Args:
        stale_hours: Number of hours after which an in_progress dispatch is considered stale. Default 2.0.

    Returns:
        List of stale dispatches with project context.
    """
    from onemancompany.core.project_archive import load_iteration, load_named_project
    from onemancompany.core.config import PROJECTS_DIR

    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    stale = []

    for d in sorted(PROJECTS_DIR.iterdir()):
        if not d.is_dir():
            continue
        project_yaml = d / "project.yaml"
        if not project_yaml.exists():
            continue

        try:
            import yaml
            with open(project_yaml) as f:
                doc = yaml.safe_load(f) or {}
        except Exception:
            continue

        if "iterations" not in doc:
            # v1 project — check dispatches in project.yaml
            for disp in doc.get("dispatches", []):
                if disp.get("status") != "in_progress":
                    continue
                dispatched_at = disp.get("dispatched_at", "")
                if not dispatched_at:
                    continue
                try:
                    dt = datetime.fromisoformat(dispatched_at)
                    hours = (now - dt).total_seconds() / 3600
                    if hours >= stale_hours:
                        stale.append({
                            "project_id": d.name,
                            "project_name": doc.get("task", d.name)[:60],
                            "employee_id": disp.get("employee_id", ""),
                            "description": disp.get("description", "")[:100],
                            "dispatched_at": dispatched_at,
                            "hours_elapsed": round(hours, 1),
                        })
                except (ValueError, TypeError):
                    continue
        else:
            # v2 named project — scan all iterations
            for iter_id in doc.get("iterations", []):
                iter_doc = load_iteration(d.name, iter_id)
                if not iter_doc:
                    continue
                for disp in iter_doc.get("dispatches", []):
                    if disp.get("status") != "in_progress":
                        continue
                    dispatched_at = disp.get("dispatched_at", "")
                    if not dispatched_at:
                        continue
                    try:
                        dt = datetime.fromisoformat(dispatched_at)
                        hours = (now - dt).total_seconds() / 3600
                        if hours >= stale_hours:
                            stale.append({
                                "project_id": d.name,
                                "project_name": doc.get("name", d.name),
                                "iteration_id": iter_id,
                                "employee_id": disp.get("employee_id", ""),
                                "description": disp.get("description", "")[:100],
                                "dispatched_at": dispatched_at,
                                "hours_elapsed": round(hours, 1),
                            })
                    except (ValueError, TypeError):
                        continue

    stale.sort(key=lambda x: x["hours_elapsed"], reverse=True)
    return {
        "status": "ok",
        "stale_threshold_hours": stale_hours,
        "stale_count": len(stale),
        "stale_dispatches": stale,
    }


@tool
def pm_get_cost_overview() -> dict:
    """Get an aggregated cost overview across all projects.

    Returns total cost, cost by department, and recent project costs.
    Useful for budget monitoring and resource allocation decisions.

    Returns:
        Cost summary with totals, department breakdown, and recent projects.
    """
    from onemancompany.core.project_archive import get_cost_summary
    summary = get_cost_summary()
    return {"status": "ok", **summary}


@tool
def pm_generate_progress_report(project_id: str = "") -> dict:
    """Generate a structured progress report.

    If project_id is given, generates a single-project status report.
    If empty, generates a portfolio overview of all active projects.

    Args:
        project_id: Optional — specific project to report on. Empty for portfolio overview.

    Returns:
        A structured report dict with sections ready for formatting.
    """
    from onemancompany.core.project_archive import (
        list_project_files,
        list_projects,
        load_iteration,
        load_named_project,
        load_project,
    )
    from onemancompany.core.state import company_state

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    if project_id:
        # Single project report
        named = load_named_project(project_id)
        if named:
            iters = named.get("iterations", [])
            latest_iter_doc = None
            total_cost = 0.0
            all_dispatches = []
            for iter_id in iters:
                iter_doc = load_iteration(project_id, iter_id)
                if iter_doc:
                    latest_iter_doc = iter_doc
                    total_cost += iter_doc.get("cost", {}).get("actual_cost_usd", 0.0)
                    all_dispatches.extend(iter_doc.get("dispatches", []))

            pending = [d for d in all_dispatches if d.get("status") == "in_progress"]
            completed = [d for d in all_dispatches if d.get("status") == "completed"]

            # Resolve employee names
            def _emp_name(eid):
                emp = company_state.employees.get(eid)
                return (emp.nickname or emp.name) if emp else eid

            dispatch_rows = []
            for d in all_dispatches:
                dispatch_rows.append({
                    "employee": _emp_name(d.get("employee_id", "")),
                    "task": d.get("description", "")[:80],
                    "status": d.get("status", ""),
                    "dispatched_at": d.get("dispatched_at", ""),
                })

            budget = 0.0
            if latest_iter_doc:
                budget = latest_iter_doc.get("cost", {}).get("budget_estimate_usd", 0.0)
            budget_pct = (total_cost / budget * 100) if budget > 0 else 0.0

            risk_level = "green"
            risks = []
            if budget_pct > 80:
                risk_level = "red"
                risks.append(f"Cost at {budget_pct:.0f}% of budget (${total_cost:.4f} / ${budget:.4f})")
            elif budget_pct > 50:
                risk_level = "yellow"
                risks.append(f"Cost at {budget_pct:.0f}% of budget")
            if len(pending) > 0:
                risks.append(f"{len(pending)} dispatch(es) still in progress")

            return {
                "status": "ok",
                "report_type": "single_project",
                "generated_at": now_str,
                "project_id": project_id,
                "project_name": named.get("name", ""),
                "project_status": named.get("status", ""),
                "current_iteration": iters[-1] if iters else "none",
                "iteration_count": len(iters),
                "total_cost_usd": round(total_cost, 4),
                "budget_usd": budget,
                "budget_usage_pct": round(budget_pct, 1),
                "dispatches": dispatch_rows,
                "pending_count": len(pending),
                "completed_count": len(completed),
                "risk_level": risk_level,
                "risks": risks,
                "files": list_project_files(project_id),
                "latest_task": latest_iter_doc.get("task", "") if latest_iter_doc else "",
                "acceptance": latest_iter_doc.get("acceptance_result") if latest_iter_doc else None,
                "ea_review": latest_iter_doc.get("ea_review_result") if latest_iter_doc else None,
            }
        else:
            doc = load_project(project_id)
            if not doc:
                return {"status": "error", "message": f"Project '{project_id}' not found."}
            return {
                "status": "ok",
                "report_type": "v1_project",
                "generated_at": now_str,
                "project_id": project_id,
                "task": doc.get("task", ""),
                "project_status": doc.get("status", ""),
                "cost_usd": round(doc.get("cost", {}).get("actual_cost_usd", 0.0), 4),
                "dispatches": doc.get("dispatches", []),
                "timeline_count": len(doc.get("timeline", [])),
            }

    # Portfolio overview
    projects = list_projects()
    active_projects = [p for p in projects if p.get("status") in ("active", "in_progress")]
    total_cost = sum(p.get("cost_usd", 0.0) for p in projects)

    project_rows = []
    for p in projects:
        project_rows.append({
            "project_id": p.get("project_id", ""),
            "name": p.get("name", p.get("task", ""))[:40],
            "status": p.get("status", ""),
            "current_owner": p.get("current_owner", ""),
            "cost_usd": p.get("cost_usd", 0.0),
            "is_named": p.get("is_named", False),
            "iteration_count": p.get("iteration_count", 0),
        })

    return {
        "status": "ok",
        "report_type": "portfolio_overview",
        "generated_at": now_str,
        "total_projects": len(projects),
        "active_projects": len(active_projects),
        "completed_projects": sum(1 for p in projects if p.get("status") == "completed"),
        "total_cost_usd": round(total_cost, 4),
        "projects": project_rows,
    }
