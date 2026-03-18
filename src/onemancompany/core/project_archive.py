"""Project Archive — project record and workspace system.

Named projects with multiple iterations:
  projects/{slug}/project.yaml    — project metadata
  projects/{slug}/workspace/      — shared workspace for all iterations
  projects/{slug}/iterations/     — per-iteration YAML files

Employees can save artifacts to their project workspace via save_project_file().
"""
from __future__ import annotations

import re
from loguru import logger
import shutil
import threading
import uuid
from datetime import datetime
from pathlib import Path

import yaml

from onemancompany.core.config import PROJECTS_DIR

# Per-project write locks to prevent concurrent YAML corruption
_project_locks: dict[str, threading.Lock] = {}
_locks_lock = threading.Lock()

# Regex to detect iteration IDs
_ITER_RE = re.compile(r"^iter_\d{3,}$")


def _get_project_lock(project_id: str) -> threading.Lock:
    with _locks_lock:
        if project_id not in _project_locks:
            _project_locks[project_id] = threading.Lock()
        return _project_locks[project_id]


def _rebase_project_dir(stored_path: str) -> Path:
    """Rebase a stored absolute project_dir onto the current PROJECTS_DIR.

    Iteration YAML files store absolute paths from whichever machine created
    them (e.g. /Users/yuzhengxu/projects/OneManCompany/company/business/projects/...).
    When running on a different machine, these paths don't exist.  This helper
    extracts the relative portion after 'company/business/projects/' and
    re-anchors it under the current PROJECTS_DIR.

    If the path is already under PROJECTS_DIR, it is returned as-is.
    If the marker is not found, the original path is returned as-is.
    """
    p = Path(stored_path)
    # Already local — nothing to do
    try:
        p.relative_to(PROJECTS_DIR)
        return p
    except ValueError:
        pass  # not under PROJECTS_DIR — fall through to rebase logic
    # Try to find the 'company/business/projects' marker and rebase
    parts = p.parts
    for i, part in enumerate(parts):
        if (
            part == "company"
            and i + 2 < len(parts)
            and parts[i + 1] == "business"
            and parts[i + 2] == "projects"
        ):
            relative = Path(*parts[i + 3 :])
            return PROJECTS_DIR / relative
    # No marker found — return as-is (caller should handle non-existence)
    return p



def _slugify(name: str) -> str:
    """Convert a project name to a filesystem-safe slug."""
    slug = re.sub(r"[^\w\s-]", "", name.lower().strip())
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = slug.strip("-")
    return slug or f"project-{uuid.uuid4().hex[:6]}"


def _is_iteration(pid: str) -> bool:
    """Check if pid is an iteration ID, either bare (iter_002) or qualified (slug/iter_002)."""
    if bool(_ITER_RE.match(pid)):
        return True
    # Qualified format: "slug/iter_NNN"
    if "/" in pid:
        _, _, iter_part = pid.rpartition("/")
        return bool(_ITER_RE.match(iter_part))
    return False


def _split_qualified_iter(pid: str) -> tuple[str, str]:
    """Split a possibly-qualified iteration ID into (slug, iter_id).

    "first-game/iter_002" -> ("first-game", "iter_002")
    "iter_002"            -> ("", "iter_002")
    """
    if "/" in pid:
        slug, _, iter_id = pid.rpartition("/")
        if _ITER_RE.match(iter_id):
            return slug, iter_id
    return "", pid


def _find_project_for_iteration(iter_id: str) -> str | None:
    """Find which named project owns this iteration.

    Supports qualified IDs like "first-game/iter_002" for exact matching,
    and bare IDs like "iter_002" with directory scan (legacy fallback).
    """
    # Fast path: qualified iteration ID with embedded slug
    slug, bare_id = _split_qualified_iter(iter_id)
    if slug:
        # Verify it exists
        iter_path = PROJECTS_DIR / slug / "iterations" / f"{bare_id}.yaml"
        if iter_path.exists():
            return slug
        # Slug was given but file doesn't exist — still return slug
        # so we don't accidentally match a different project
        return slug

    # Legacy: scan all projects (may be ambiguous)
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    for d in PROJECTS_DIR.iterdir():
        if not d.is_dir():
            continue
        iter_path = d / "iterations" / f"{bare_id}.yaml"
        if iter_path.exists():
            return d.name
    return None


def _resolve_and_load(pid: str) -> tuple[str, dict | None, str]:
    """Resolve a pid and load the right document.

    Returns (version, doc, resolved_key) where:
      version = "v2"
      doc = the loaded YAML dict (iteration yaml for v2)
      resolved_key = "project_slug/iter_id" as a tuple marker
    """
    if _is_iteration(pid):
        slug = _find_project_for_iteration(pid)
        _, bare_id = _split_qualified_iter(pid)
        if slug:
            doc = load_iteration(slug, bare_id)
            return ("v2", doc, f"{slug}/{bare_id}")
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
    return ("v2", None, "")


def _save_resolved(version: str, resolved_key: str, doc: dict) -> None:
    """Save doc back based on resolved version and key."""
    # resolved_key = "slug/iter_id"
    parts = resolved_key.split("/", 1)
    if len(parts) == 2:
        _save_iteration(parts[0], parts[1], doc)


# ─────────────────────────────────────────────
# v2 Named Project CRUD
# ─────────────────────────────────────────────

def _auto_project_name(task: str) -> str:
    """Fallback: derive a project name by truncating the task description."""
    first_line = task.strip().split("\n")[0].strip()
    if len(first_line) <= 50:
        return first_line or "Untitled Project"
    truncated = first_line[:50].rsplit(" ", 1)[0]
    return truncated or first_line[:50]


async def _llm_project_name(task: str) -> str:
    """Use the default LLM to generate a concise project name (2-6 words)."""
    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        from onemancompany.agents.base import make_llm, tracked_ainvoke

        llm = make_llm(temperature=0)
        result = await tracked_ainvoke(
            llm,
            [
                SystemMessage(content=(
                    "You are a project naming assistant. "
                    "Given a CEO's task description, generate a concise project name in 2-6 words. "
                    "Use the same language as the task description. "
                    "Return ONLY the project name, nothing else. No quotes, no punctuation, no explanation."
                )),
                HumanMessage(content=task[:500]),
            ],
            category="overhead",
        )
        name = result.content.strip().strip('"\'')
        if 1 < len(name) <= 60:
            return name
    except Exception as exc:
        logger.debug("LLM project naming failed, using fallback: {}", exc)
    return _auto_project_name(task)


async def async_create_project_from_task(
    task: str,
    routed_to: str = "pending",
    participants: list[str] | None = None,
) -> tuple[str, str]:
    """Create a named project + first iteration, non-blocking.

    Creates the project immediately with a truncation-based fallback name,
    then spawns a background task to generate a better LLM name and update
    the project asynchronously.

    Returns (project_id, iteration_id).
    """
    # Immediate: create project with fallback name so it appears instantly
    fallback_name = _auto_project_name(task)
    project_id = create_named_project(fallback_name)
    iter_id = create_iteration(project_id, task, routed_to)

    # Background: generate LLM name and update when ready
    from onemancompany.core.async_utils import spawn_background

    async def _rename_when_ready() -> None:
        import asyncio as _aio
        try:
            llm_name = await _aio.wait_for(_llm_project_name(task), timeout=30.0)
        except _aio.TimeoutError:
            logger.warning("LLM project naming timed out for {}, keeping fallback", project_id)
            return
        if llm_name and llm_name != fallback_name:
            _update_project_name(project_id, llm_name)
            logger.info("Project {} renamed: '{}' → '{}'", project_id, fallback_name, llm_name)
            # Notify frontend via store dirty so next sync tick picks it up
            from onemancompany.core.store import mark_dirty
            mark_dirty("task_queue")

    spawn_background(_rename_when_ready())
    return project_id, iter_id


def _update_project_name(project_id: str, new_name: str) -> None:
    """Update the display name of an existing named project."""
    path = PROJECTS_DIR / project_id / "project.yaml"
    lock = _get_project_lock(project_id)
    with lock:
        if not path.exists():
            return
        with open(path) as f:
            doc = yaml.safe_load(f) or {}
        doc["name"] = new_name
        with open(path, "w") as f:
            yaml.dump(doc, f, allow_unicode=True, default_flow_style=False)


def create_project_from_task(task: str, routed_to: str = "pending",
                             participants: list[str] | None = None) -> tuple[str, str]:
    """Sync fallback: create project with truncation-based name.

    Returns (project_id, iteration_id).
    """
    name = _auto_project_name(task)
    project_id = create_named_project(name)
    iter_id = create_iteration(project_id, task, routed_to)
    return project_id, iter_id


def create_named_project(name: str) -> str:
    """Create a persistent named project. Returns the project_id (slug-timestamp)."""
    base_slug = _slugify(name)
    # Append compact timestamp to guarantee uniqueness and prevent overwrites
    ts = datetime.now().strftime("%m%d%H%M%S")
    slug = f"{base_slug}-{ts}"
    # Extremely unlikely collision (same name + same minute) — append counter
    counter = 1
    while (PROJECTS_DIR / slug).exists():
        slug = f"{base_slug}-{ts}-{counter}"
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

    iterations_dir = PROJECTS_DIR / project_id / "iterations"
    iterations_dir.mkdir(parents=True, exist_ok=True)

    # --- per-iteration workspace ---
    # Determine previous iteration's workspace to copy from
    prev_workspace: Path | None = None
    if existing:
        prev_iter_id = existing[-1]
        prev_doc = load_iteration(project_id, prev_iter_id)
        if prev_doc and prev_doc.get("project_dir"):
            candidate = _rebase_project_dir(prev_doc["project_dir"])
            if candidate.is_dir():
                prev_workspace = candidate
    if prev_workspace is None:
        # Fallback to shared workspace/
        fallback = PROJECTS_DIR / project_id / "workspace"
        if fallback.is_dir():
            prev_workspace = fallback

    # Create the new iteration workspace directory
    new_workspace = iterations_dir / iter_id
    new_workspace.mkdir(parents=True, exist_ok=True)

    # Copy files from previous workspace
    if prev_workspace is not None and prev_workspace.is_dir():
        for item in prev_workspace.iterdir():
            dest = new_workspace / item.name
            if item.is_dir():
                shutil.copytree(item, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dest)

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
        "project_dir": str(new_workspace),
    }
    _save_iteration(project_id, iter_id, doc)

    # Update project.yaml iterations list
    proj["iterations"] = existing + [iter_id]
    path = PROJECTS_DIR / project_id / "project.yaml"
    lock = _get_project_lock(project_id)
    with lock, open(path, "w") as f:
        yaml.dump(proj, f, allow_unicode=True, default_flow_style=False)

    # Trigger 1: dispatch → in_progress — notify sync tick
    from onemancompany.core.store import mark_dirty
    mark_dirty("task_queue")

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
        except Exception as _e:
            logger.warning("Failed to load {}: {}", yaml_path, _e)
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
    """Return the workspace directory path for a v2 named project.

    Prefers the latest iteration's per-iteration workspace if available,
    otherwise falls back to the shared workspace/ directory.
    """
    proj = load_named_project(project_id)
    if proj:
        iters = proj.get("iterations", [])
        if iters:
            latest_doc = load_iteration(project_id, iters[-1])
            if latest_doc and latest_doc.get("project_dir"):
                ws = _rebase_project_dir(latest_doc["project_dir"])
                ws.mkdir(parents=True, exist_ok=True)
                return str(ws)
    # Fallback to shared workspace/
    ws = PROJECTS_DIR / project_id / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    return str(ws)


# ─────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────

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
    # Signal sync tick that task queue changed
    from onemancompany.core.store import mark_dirty
    mark_dirty("task_queue")


def load_project(project_id: str) -> dict | None:
    """Load a project or iteration record."""
    version, doc, _key = _resolve_and_load(project_id)
    return doc


def _resolve_workspace(project_id: str) -> Path:
    """Resolve the workspace directory for any project identifier.

    - v2 project slug: latest iteration's per-iteration workspace (via get_project_workspace)
    - v2 iteration ID: that iteration's project_dir from its YAML

    Supports qualified iteration IDs like "first-game/iter_002".
    """
    if _is_iteration(project_id):
        slug = _find_project_for_iteration(project_id)
        _, bare_id = _split_qualified_iter(project_id)
        if slug:
            iter_doc = load_iteration(slug, bare_id)
            if iter_doc and iter_doc.get("project_dir"):
                ws = _rebase_project_dir(iter_doc["project_dir"])
                ws.mkdir(parents=True, exist_ok=True)
                return ws
            # Fallback for old iterations without per-iter workspace
            ws = PROJECTS_DIR / slug / "workspace"
            ws.mkdir(parents=True, exist_ok=True)
            return ws
    return Path(get_project_workspace(project_id))


def get_project_dir(project_id: str) -> str:
    """Return the absolute path of a project's workspace directory.

    iteration: returns that iteration's workspace
    slug: returns latest iteration's workspace (or shared workspace/)
    """
    ws = _resolve_workspace(project_id)
    ws.mkdir(parents=True, exist_ok=True)
    return str(ws)


def save_project_file(project_id: str, filename: str, content: str | bytes) -> dict:
    """Save a file into the project workspace directory."""
    project_dir = _resolve_workspace(project_id)
    project_dir.mkdir(parents=True, exist_ok=True)

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


# Internal infrastructure files excluded from user-facing document listing
_INTERNAL_FILE_NAMES = frozenset({"project.yaml", "task_tree.yaml"})
_INTERNAL_DIR_NAMES = frozenset({"nodes"})


def _is_internal_file(name: str) -> bool:
    """Check if a filename is internal infrastructure (task tree archive etc.)."""
    if name in _INTERNAL_FILE_NAMES:
        return True
    # Archived task trees: task_tree_iter_NNN.yaml
    if name.startswith("task_tree_iter_") and name.endswith(".yaml"):
        return True
    return False


def list_project_files(project_id: str) -> list[str]:
    """List user-facing files in a project workspace.

    Excludes internal infrastructure files (project.yaml, task trees, node content).
    """
    project_dir = _resolve_workspace(project_id)
    logger.debug("[list_project_files] project_id={} → workspace={}", project_id, project_dir)

    if not project_dir.exists():
        logger.debug("[list_project_files] workspace does not exist")
        return []

    files = []
    for p in sorted(project_dir.rglob("*")):
        if not p.is_file():
            continue
        if _is_internal_file(p.name):
            continue
        rel = p.relative_to(project_dir)
        if rel.parts and rel.parts[0] in _INTERNAL_DIR_NAMES:
            continue
        files.append(str(rel))
    logger.debug("[list_project_files] found {} files", len(files))
    return files


def _safe_file_count(project_id: str) -> int:
    """Return file count for a project, returning 0 on any error."""
    try:
        return len(list_project_files(project_id))
    except Exception as e:
        logger.debug("[_safe_file_count] failed for {}: {}", project_id, e)
        return 0


def list_projects() -> list[dict]:
    """List all projects (v2 summary)."""
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
        except Exception as _e:
            logger.warning("Failed to load {}: {}", yaml_path, _e)
            continue

        if "iterations" not in doc:
            continue

        iterations = doc.get("iterations", [])
        latest_task = ""
        project_status = doc.get("status", "active")
        latest_iter_status = ""
        latest_owner = ""
        total_cost = 0.0
        if iterations:
            latest_iter = load_iteration(d.name, iterations[-1])
            if latest_iter:
                latest_task = latest_iter.get("task", "")
                latest_iter_status = latest_iter.get("status", "")
                latest_owner = latest_iter.get("current_owner", "")
            # Aggregate cost across all iterations
            for iter_id in iterations:
                iter_doc = load_iteration(d.name, iter_id)
                if iter_doc:
                    total_cost += iter_doc.get("cost", {}).get("actual_cost_usd", 0.0)
        projects.append({
            "project_id": doc.get("project_id", d.name),
            "task": latest_task or doc.get("name", ""),
            "status": project_status,
            "latest_iter_status": latest_iter_status,
            "routed_to": "",
            "current_owner": latest_owner,
            "created_at": doc.get("created_at", ""),
            "completed_at": doc.get("archived_at"),
            "participant_count": 0,
            "action_count": 0,
            "file_count": _safe_file_count(d.name),
            "is_named": True,
            "name": doc.get("name", d.name),
            "iteration_count": len(iterations),
            "cost_usd": round(total_cost, 4),
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
        except Exception as _e:
            logger.warning("Failed to load {}: {}", yaml_path, _e)
            continue

        if "iterations" not in doc:
            continue

        # Aggregate cost from iterations
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
                from onemancompany.core.store import load_employee as _load_emp, load_ex_employees as _load_ex
                _emp_d = _load_emp(eid)
                if not _emp_d:
                    _ex = _load_ex()
                    _emp_d = _ex.get(eid, {})
                dept = _emp_d.get("department", "Unknown")
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


