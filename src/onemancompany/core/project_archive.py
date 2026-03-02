"""Project Archive — project record and workspace system.

Supports two project formats:
  v1 (legacy): One-shot projects with timestamp-based IDs under projects/{timestamp_id}/
  v2 (named):  Persistent named projects with multiple iterations:
    projects/{slug}/project.yaml    — project metadata
    projects/{slug}/workspace/      — shared workspace for all iterations
    projects/{slug}/iterations/     — per-iteration YAML files

Employees can save artifacts to their project workspace via save_project_file().
"""
from __future__ import annotations

import re
import threading
import uuid
from datetime import datetime
from pathlib import Path

import yaml

from onemancompany.core.config import PROJECTS_DIR

# Per-project write locks to prevent concurrent YAML corruption
_project_locks: dict[str, threading.Lock] = {}
_locks_lock = threading.Lock()

# Regex to detect v1 timestamp-based project IDs
_V1_RE = re.compile(r"^\d{8}_\d{6}_[0-9a-f]+$")
# Regex to detect iteration IDs
_ITER_RE = re.compile(r"^iter_\d{3,}$")


def _get_project_lock(project_id: str) -> threading.Lock:
    with _locks_lock:
        if project_id not in _project_locks:
            _project_locks[project_id] = threading.Lock()
        return _project_locks[project_id]


def _project_dir(project_id: str) -> Path:
    """Return the directory path for a given project."""
    return PROJECTS_DIR / project_id


def _project_yaml(project_id: str) -> Path:
    """Return the project.yaml path for a given project."""
    return _project_dir(project_id) / "project.yaml"


def _ensure_project_dir(project_id: str) -> Path:
    """Ensure the project directory exists and return it."""
    d = _project_dir(project_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _slugify(name: str) -> str:
    """Convert a project name to a filesystem-safe slug."""
    slug = re.sub(r"[^\w\s-]", "", name.lower().strip())
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = slug.strip("-")
    return slug or f"project-{uuid.uuid4().hex[:6]}"


# ─────────────────────────────────────────────
# v1 / v2 detection and bridge
# ─────────────────────────────────────────────

def _is_v1(pid: str) -> bool:
    return bool(_V1_RE.match(pid))


def _is_iteration(pid: str) -> bool:
    return bool(_ITER_RE.match(pid))


def _find_project_for_iteration(iter_id: str) -> str | None:
    """Scan projects to find which named project owns this iteration."""
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    for d in PROJECTS_DIR.iterdir():
        if not d.is_dir():
            continue
        iter_path = d / "iterations" / f"{iter_id}.yaml"
        if iter_path.exists():
            return d.name
    return None


def _resolve_and_load(pid: str) -> tuple[str, dict | None, str]:
    """Resolve a pid and load the right document.

    Returns (version, doc, resolved_key) where:
      version = "v1" | "v2"
      doc = the loaded YAML dict (project.yaml for v1, iteration yaml for v2)
      resolved_key = the key to use for saving:
        v1: project_id (timestamp)
        v2: "project_slug/iter_id" as a tuple marker
    """
    if _is_v1(pid) or pid.startswith("_auto_"):
        doc = _load_v1_project(pid)
        return ("v1", doc, pid)

    if _is_iteration(pid):
        slug = _find_project_for_iteration(pid)
        if slug:
            doc = load_iteration(slug, pid)
            return ("v2", doc, f"{slug}/{pid}")
        return ("v2", None, "")

    # Assume it's a project slug — load latest iteration or project itself
    proj = load_named_project(pid)
    if proj:
        iters = proj.get("iterations", [])
        if iters:
            latest = iters[-1]
            doc = load_iteration(pid, latest)
            return ("v2", doc, f"{pid}/{latest}")
        return ("v2", proj, pid)
    return ("v1", None, "")


def _save_resolved(version: str, resolved_key: str, doc: dict) -> None:
    """Save doc back based on resolved version and key."""
    if version == "v1":
        _save_project(resolved_key, doc)
    else:
        # resolved_key = "slug/iter_id"
        parts = resolved_key.split("/", 1)
        if len(parts) == 2:
            _save_iteration(parts[0], parts[1], doc)


# ─────────────────────────────────────────────
# v1 legacy functions (internal)
# ─────────────────────────────────────────────

def _load_v1_project(project_id: str) -> dict | None:
    path = _project_yaml(project_id)
    if not path.exists():
        return None
    lock = _get_project_lock(project_id)
    with lock, open(path) as f:
        return yaml.safe_load(f) or {}


# ─────────────────────────────────────────────
# v2 Named Project CRUD
# ─────────────────────────────────────────────

def create_named_project(name: str) -> str:
    """Create a persistent named project. Returns the project_id (slug)."""
    slug = _slugify(name)
    # Ensure unique slug
    base_slug = slug
    counter = 1
    while (PROJECTS_DIR / slug).exists():
        slug = f"{base_slug}-{counter}"
        counter += 1

    proj_dir = PROJECTS_DIR / slug
    proj_dir.mkdir(parents=True, exist_ok=True)
    (proj_dir / "workspace").mkdir(exist_ok=True)
    (proj_dir / "iterations").mkdir(exist_ok=True)

    doc = {
        "project_id": slug,
        "name": name,
        "status": "active",
        "created_at": datetime.now().isoformat(),
        "archived_at": None,
        "iterations": [],
    }
    path = proj_dir / "project.yaml"
    lock = _get_project_lock(slug)
    with lock, open(path, "w") as f:
        yaml.dump(doc, f, allow_unicode=True, default_flow_style=False)
    return slug


def create_iteration(project_id: str, task: str, routed_to: str) -> str:
    """Create a new iteration under an existing named project. Returns iteration_id."""
    proj = load_named_project(project_id)
    if not proj:
        raise ValueError(f"Project '{project_id}' not found")

    existing = proj.get("iterations", [])
    iter_num = len(existing) + 1
    iter_id = f"iter_{iter_num:03d}"

    iter_dir = PROJECTS_DIR / project_id / "iterations"
    iter_dir.mkdir(parents=True, exist_ok=True)

    workspace = str(PROJECTS_DIR / project_id / "workspace")
    doc = {
        "iteration_id": iter_id,
        "project_id": project_id,
        "task": task,
        "status": "in_progress",
        "routed_to": routed_to,
        "current_owner": routed_to.lower() if routed_to else "",
        "created_at": datetime.now().isoformat(),
        "completed_at": None,
        "timeline": [],
        "output": None,
        "acceptance_criteria": [],
        "responsible_officer": "",
        "dispatches": [],
        "acceptance_result": None,
        "ea_review_result": None,
        "cost": {
            "budget_estimate_usd": 0.0,
            "actual_cost_usd": 0.0,
            "token_usage": {"input": 0, "output": 0, "total": 0},
            "breakdown": [],
        },
        "project_dir": workspace,
    }
    _save_iteration(project_id, iter_id, doc)

    # Update project.yaml iterations list
    proj["iterations"] = existing + [iter_id]
    path = PROJECTS_DIR / project_id / "project.yaml"
    lock = _get_project_lock(project_id)
    with lock, open(path, "w") as f:
        yaml.dump(proj, f, allow_unicode=True, default_flow_style=False)

    return iter_id


def load_iteration(project_id: str, iteration_id: str) -> dict | None:
    """Load an iteration YAML."""
    path = PROJECTS_DIR / project_id / "iterations" / f"{iteration_id}.yaml"
    if not path.exists():
        return None
    lock_key = f"{project_id}/{iteration_id}"
    lock = _get_project_lock(lock_key)
    with lock, open(path) as f:
        return yaml.safe_load(f) or {}


def _save_iteration(project_id: str, iteration_id: str, doc: dict) -> None:
    """Save an iteration YAML."""
    iter_dir = PROJECTS_DIR / project_id / "iterations"
    iter_dir.mkdir(parents=True, exist_ok=True)
    path = iter_dir / f"{iteration_id}.yaml"
    lock_key = f"{project_id}/{iteration_id}"
    lock = _get_project_lock(lock_key)
    with lock, open(path, "w") as f:
        yaml.dump(doc, f, allow_unicode=True, default_flow_style=False)


def load_named_project(project_id: str) -> dict | None:
    """Load a named project's project.yaml."""
    path = PROJECTS_DIR / project_id / "project.yaml"
    if not path.exists():
        return None
    lock = _get_project_lock(project_id)
    with lock, open(path) as f:
        doc = yaml.safe_load(f) or {}
    # Distinguish v2 by checking for 'iterations' key
    if "iterations" not in doc:
        return None
    return doc


def list_named_projects() -> list[dict]:
    """List all v2 named projects (summary)."""
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    projects = []
    for d in sorted(PROJECTS_DIR.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        yaml_path = d / "project.yaml"
        if not yaml_path.exists():
            continue
        try:
            with open(yaml_path) as fh:
                doc = yaml.safe_load(fh) or {}
        except Exception:
            continue
        # Only v2 projects have 'iterations' key
        if "iterations" not in doc:
            continue
        iterations = doc.get("iterations", [])
        projects.append({
            "project_id": doc.get("project_id", d.name),
            "name": doc.get("name", d.name),
            "status": doc.get("status", "active"),
            "created_at": doc.get("created_at", ""),
            "archived_at": doc.get("archived_at"),
            "iteration_count": len(iterations),
            "iterations": iterations,
        })
    return projects


def archive_project(project_id: str) -> None:
    """Mark a named project as archived."""
    proj = load_named_project(project_id)
    if not proj:
        return
    proj["status"] = "archived"
    proj["archived_at"] = datetime.now().isoformat()
    path = PROJECTS_DIR / project_id / "project.yaml"
    lock = _get_project_lock(project_id)
    with lock, open(path, "w") as f:
        yaml.dump(proj, f, allow_unicode=True, default_flow_style=False)


def get_project_workspace(project_id: str) -> str:
    """Return the workspace directory path for a v2 named project."""
    ws = PROJECTS_DIR / project_id / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    return str(ws)


# ─────────────────────────────────────────────
# Public API (v1-compatible, bridged for v2)
# ─────────────────────────────────────────────

def create_project(task: str, routed_to: str, participants: list[str] | None = None) -> str:
    """Create a new v1 project record. Returns the project_id."""
    project_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    project_dir = _ensure_project_dir(project_id)
    doc = {
        "project_id": project_id,
        "project_dir": str(project_dir),
        "task": task,
        "routed_to": routed_to,
        "participants": participants or [],
        "current_owner": routed_to.lower(),
        "status": "in_progress",
        "created_at": datetime.now().isoformat(),
        "completed_at": None,
        "timeline": [],
        "output": None,
        "acceptance_criteria": [],
        "responsible_officer": "",
        "dispatches": [],
        "acceptance_result": None,
        "ea_review_result": None,
        "cost": {
            "budget_estimate_usd": 0.0,
            "actual_cost_usd": 0.0,
            "token_usage": {"input": 0, "output": 0, "total": 0},
            "breakdown": [],
        },
    }
    _save_project(project_id, doc)
    return project_id


def append_action(project_id: str, employee_id: str, action: str, detail: str = "") -> None:
    """Append an action entry to the project timeline and update current_owner."""
    version, doc, key = _resolve_and_load(project_id)
    if not doc:
        return
    doc.setdefault("timeline", []).append({
        "time": datetime.now().isoformat(),
        "employee_id": employee_id,
        "action": action,
        "detail": detail[:500],
    })
    if employee_id:
        doc["current_owner"] = employee_id
    _save_resolved(version, key, doc)


def complete_project(project_id: str, output: str = "") -> None:
    """Mark a project/iteration as completed."""
    version, doc, key = _resolve_and_load(project_id)
    if not doc:
        return
    doc["status"] = "completed"
    doc["completed_at"] = datetime.now().isoformat()
    doc["output"] = output
    doc["current_owner"] = ""

    actual_contributors = {
        entry["employee_id"]
        for entry in doc.get("timeline", [])
        if entry.get("employee_id")
    }
    if actual_contributors:
        doc["participants"] = [
            pid for pid in doc.get("participants", [])
            if pid in actual_contributors
        ]

    _save_resolved(version, key, doc)


def load_project(project_id: str) -> dict | None:
    """Load a project or iteration record."""
    version, doc, _key = _resolve_and_load(project_id)
    return doc


def get_project_dir(project_id: str) -> str:
    """Return the absolute path of a project's workspace directory.

    v1: returns projects/{timestamp_id}
    v2 iteration: returns projects/{slug}/workspace
    """
    if _is_iteration(project_id):
        slug = _find_project_for_iteration(project_id)
        if slug:
            ws = PROJECTS_DIR / slug / "workspace"
            ws.mkdir(parents=True, exist_ok=True)
            return str(ws)
    if not _is_v1(project_id) and not project_id.startswith("_auto_"):
        # Could be a named project slug
        ws = PROJECTS_DIR / project_id / "workspace"
        if ws.parent.exists():
            ws.mkdir(parents=True, exist_ok=True)
            return str(ws)
    return str(_project_dir(project_id))


def save_project_file(project_id: str, filename: str, content: str | bytes) -> dict:
    """Save a file into the project workspace directory."""
    # Determine the actual workspace dir
    if _is_iteration(project_id):
        slug = _find_project_for_iteration(project_id)
        if slug:
            project_dir = PROJECTS_DIR / slug / "workspace"
            project_dir.mkdir(parents=True, exist_ok=True)
        else:
            project_dir = _ensure_project_dir(project_id)
    elif not _is_v1(project_id) and not project_id.startswith("_auto_"):
        project_dir = PROJECTS_DIR / project_id / "workspace"
        project_dir.mkdir(parents=True, exist_ok=True)
    else:
        project_dir = _ensure_project_dir(project_id)

    file_path = project_dir / filename

    # Security: ensure the resolved path stays within the project directory
    resolved = file_path.resolve()
    if not str(resolved).startswith(str(project_dir.resolve())):
        return {"status": "error", "message": f"Path escapes project directory: {filename}"}

    file_path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        file_path.write_bytes(content)
    else:
        file_path.write_text(content, encoding="utf-8")

    return {"status": "ok", "path": str(file_path), "relative": filename}


def list_project_files(project_id: str) -> list[str]:
    """List all files in a project workspace (excluding project.yaml and iterations/)."""
    if _is_iteration(project_id):
        slug = _find_project_for_iteration(project_id)
        if slug:
            project_dir = PROJECTS_DIR / slug / "workspace"
        else:
            project_dir = _project_dir(project_id)
    elif not _is_v1(project_id) and not project_id.startswith("_auto_"):
        project_dir = PROJECTS_DIR / project_id / "workspace"
    else:
        project_dir = _project_dir(project_id)

    if not project_dir.exists():
        return []
    files = []
    for p in sorted(project_dir.rglob("*")):
        if p.is_file() and p.name != "project.yaml":
            files.append(str(p.relative_to(project_dir)))
    return files


def list_projects() -> list[dict]:
    """List all projects (v1 + v2 summary)."""
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    projects = []
    for d in sorted(PROJECTS_DIR.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        yaml_path = d / "project.yaml"
        if not yaml_path.exists():
            continue
        try:
            with open(yaml_path) as fh:
                doc = yaml.safe_load(fh) or {}
        except Exception:
            continue

        if "iterations" in doc:
            # v2 named project — summarize from latest iteration
            iterations = doc.get("iterations", [])
            latest_task = ""
            latest_status = doc.get("status", "active")
            latest_owner = ""
            if iterations:
                latest_iter = load_iteration(d.name, iterations[-1])
                if latest_iter:
                    latest_task = latest_iter.get("task", "")
                    latest_status = latest_iter.get("status", latest_status)
                    latest_owner = latest_iter.get("current_owner", "")
            projects.append({
                "project_id": doc.get("project_id", d.name),
                "task": latest_task or doc.get("name", ""),
                "status": latest_status,
                "routed_to": "",
                "current_owner": latest_owner,
                "created_at": doc.get("created_at", ""),
                "completed_at": doc.get("archived_at"),
                "participant_count": 0,
                "action_count": 0,
                "file_count": len(list_project_files(d.name)),
                "is_named": True,
                "name": doc.get("name", d.name),
                "iteration_count": len(iterations),
            })
        else:
            # v1 legacy project
            projects.append({
                "project_id": doc.get("project_id", d.name),
                "task": doc.get("task", ""),
                "status": doc.get("status", "unknown"),
                "routed_to": doc.get("routed_to", ""),
                "current_owner": doc.get("current_owner", ""),
                "created_at": doc.get("created_at", ""),
                "completed_at": doc.get("completed_at"),
                "participant_count": len(doc.get("participants", [])),
                "action_count": len(doc.get("timeline", [])),
                "file_count": len(list_project_files(d.name)),
                "is_named": False,
            })
    return projects


def set_acceptance_criteria(project_id: str, criteria: list[str], responsible_officer: str) -> None:
    """Set or update acceptance criteria and responsible officer."""
    version, doc, key = _resolve_and_load(project_id)
    if not doc:
        return
    doc["acceptance_criteria"] = criteria
    doc["responsible_officer"] = responsible_officer
    _save_resolved(version, key, doc)


def record_dispatch(project_id: str, employee_id: str, description: str) -> None:
    """Record that a task was dispatched to an agent for this project."""
    version, doc, key = _resolve_and_load(project_id)
    if not doc:
        return
    dispatches = doc.get("dispatches", [])
    dispatches.append({
        "employee_id": employee_id,
        "description": description[:200],
        "status": "in_progress",
        "dispatched_at": datetime.now().isoformat(),
    })
    doc["dispatches"] = dispatches
    _save_resolved(version, key, doc)


def record_dispatch_completion(project_id: str, employee_id: str) -> None:
    """Mark a dispatch as completed."""
    version, doc, key = _resolve_and_load(project_id)
    if not doc:
        return
    for d in doc.get("dispatches", []):
        if d["employee_id"] == employee_id and d["status"] == "in_progress":
            d["status"] = "completed"
            d["completed_at"] = datetime.now().isoformat()
            break
    _save_resolved(version, key, doc)


def all_dispatches_complete(project_id: str) -> bool:
    """Check if all dispatches for a project are completed."""
    _version, doc, _key = _resolve_and_load(project_id)
    if not doc:
        return True
    dispatches = doc.get("dispatches", [])
    if not dispatches:
        return True
    return all(d["status"] == "completed" for d in dispatches)


def set_acceptance_result(project_id: str, accepted: bool, officer_id: str, notes: str = "") -> None:
    """Record the acceptance result."""
    version, doc, key = _resolve_and_load(project_id)
    if not doc:
        return
    doc["acceptance_result"] = {
        "accepted": accepted,
        "officer_id": officer_id,
        "notes": notes,
        "timestamp": datetime.now().isoformat(),
    }
    _save_resolved(version, key, doc)


def set_ea_review_result(project_id: str, approved: bool, notes: str = "") -> None:
    """Record the EA quality review result (CEO's quality gate)."""
    version, doc, key = _resolve_and_load(project_id)
    if not doc:
        return
    doc["ea_review_result"] = {
        "approved": approved,
        "notes": notes,
        "timestamp": datetime.now().isoformat(),
    }
    _save_resolved(version, key, doc)


def set_project_budget(project_id: str, budget_usd: float) -> None:
    """Set estimated budget for a project."""
    version, doc, key = _resolve_and_load(project_id)
    if not doc:
        return
    cost = doc.setdefault("cost", {})
    cost["budget_estimate_usd"] = budget_usd
    _save_resolved(version, key, doc)


def record_project_cost(
    project_id: str,
    employee_id: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
) -> None:
    """Append a cost entry to the project breakdown and update totals."""
    version, doc, key = _resolve_and_load(project_id)
    if not doc:
        return
    cost = doc.setdefault("cost", {
        "budget_estimate_usd": 0.0,
        "actual_cost_usd": 0.0,
        "token_usage": {"input": 0, "output": 0, "total": 0},
        "breakdown": [],
    })
    cost["actual_cost_usd"] = cost.get("actual_cost_usd", 0.0) + cost_usd
    tokens = cost.setdefault("token_usage", {"input": 0, "output": 0, "total": 0})
    tokens["input"] = tokens.get("input", 0) + input_tokens
    tokens["output"] = tokens.get("output", 0) + output_tokens
    tokens["total"] = tokens.get("total", 0) + input_tokens + output_tokens
    breakdown = cost.setdefault("breakdown", [])
    breakdown.append({
        "employee_id": employee_id,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "cost_usd": cost_usd,
    })
    _save_resolved(version, key, doc)


def get_cost_summary() -> dict:
    """Aggregate cost data across all projects."""
    from onemancompany.core.state import company_state

    total_cost = 0.0
    total_input = 0
    total_output = 0
    dept_costs: dict[str, dict] = {}  # dept -> {cost_usd, input, output}
    recent_projects = []  # [{project_id, task, cost_usd, tokens, status}]

    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    all_dirs = sorted(PROJECTS_DIR.iterdir(), reverse=True)

    for d in all_dirs:
        if not d.is_dir():
            continue
        yaml_path = d / "project.yaml"
        if not yaml_path.exists():
            continue
        try:
            with open(yaml_path) as fh:
                doc = yaml.safe_load(fh) or {}
        except Exception:
            continue

        # For v2 projects, aggregate cost from iterations
        if "iterations" in doc:
            for iter_id in doc.get("iterations", []):
                iter_doc = load_iteration(d.name, iter_id)
                if not iter_doc:
                    continue
                cost = iter_doc.get("cost", {})
                proj_cost = cost.get("actual_cost_usd", 0.0)
                tokens = cost.get("token_usage", {})
                proj_input = tokens.get("input", 0)
                proj_output = tokens.get("output", 0)
                total_cost += proj_cost
                total_input += proj_input
                total_output += proj_output
                for entry in cost.get("breakdown", []):
                    eid = entry.get("employee_id", "")
                    emp = company_state.employees.get(eid) or company_state.ex_employees.get(eid)
                    dept = emp.department if emp else "Unknown"
                    if dept not in dept_costs:
                        dept_costs[dept] = {"cost_usd": 0.0, "input": 0, "output": 0}
                    dept_costs[dept]["cost_usd"] += entry.get("cost_usd", 0.0)
                    dept_costs[dept]["input"] += entry.get("input_tokens", 0)
                    dept_costs[dept]["output"] += entry.get("output_tokens", 0)
            if len(recent_projects) < 10:
                recent_projects.append({
                    "project_id": doc.get("project_id", d.name),
                    "task": doc.get("name", "")[:60],
                    "cost_usd": total_cost,
                    "input_tokens": total_input,
                    "output_tokens": total_output,
                    "total_tokens": total_input + total_output,
                    "status": doc.get("status", "active"),
                })
            continue

        # v1 project
        cost = doc.get("cost", {})
        proj_cost = cost.get("actual_cost_usd", 0.0)
        tokens = cost.get("token_usage", {})
        proj_input = tokens.get("input", 0)
        proj_output = tokens.get("output", 0)

        total_cost += proj_cost
        total_input += proj_input
        total_output += proj_output

        # Per-department breakdown from cost.breakdown[]
        for entry in cost.get("breakdown", []):
            eid = entry.get("employee_id", "")
            emp = company_state.employees.get(eid) or company_state.ex_employees.get(eid)
            dept = emp.department if emp else "Unknown"
            if dept not in dept_costs:
                dept_costs[dept] = {"cost_usd": 0.0, "input": 0, "output": 0}
            dept_costs[dept]["cost_usd"] += entry.get("cost_usd", 0.0)
            dept_costs[dept]["input"] += entry.get("input_tokens", 0)
            dept_costs[dept]["output"] += entry.get("output_tokens", 0)

        if len(recent_projects) < 10:
            recent_projects.append({
                "project_id": doc.get("project_id", d.name),
                "task": (doc.get("task", ""))[:60],
                "cost_usd": proj_cost,
                "input_tokens": proj_input,
                "output_tokens": proj_output,
                "total_tokens": proj_input + proj_output,
                "status": doc.get("status", "unknown"),
            })

    return {
        "total": {
            "cost_usd": round(total_cost, 4),
            "input_tokens": total_input,
            "output_tokens": total_output,
            "total_tokens": total_input + total_output,
        },
        "by_department": {
            dept: {
                "cost_usd": round(v["cost_usd"], 4),
                "input_tokens": v["input"],
                "output_tokens": v["output"],
                "total_tokens": v["input"] + v["output"],
            }
            for dept, v in sorted(dept_costs.items())
        },
        "recent_projects": recent_projects,
    }


def _save_project(project_id: str, doc: dict) -> None:
    _ensure_project_dir(project_id)
    path = _project_yaml(project_id)
    lock = _get_project_lock(project_id)
    with lock, open(path, "w") as f:
        yaml.dump(doc, f, allow_unicode=True, default_flow_style=False)
