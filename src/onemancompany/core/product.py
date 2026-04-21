"""Product management — product records, key results, and issues.

Products stored at: PRODUCTS_DIR/{slug}/product.yaml
Issues stored at:   PRODUCTS_DIR/{slug}/issues/{issue_id}.yaml

All YAML I/O through store._read_yaml / _write_yaml.
Disk is the single source of truth — no in-memory caching.
"""
from __future__ import annotations

import re
import threading
import uuid
from datetime import datetime
from pathlib import Path

from loguru import logger

from onemancompany.core.config import (
    ISSUES_DIR_NAME,
    PRODUCT_YAML_FILENAME,
    PRODUCTS_DIR,
    VERSIONS_DIR_NAME,
    DirtyCategory,
)
from onemancompany.core.models import (
    IssueResolution,
    IssuePriority,
    IssueStatus,
    ProductStatus,
)
from onemancompany.core.store import _read_yaml, _write_yaml, mark_dirty

# ---------------------------------------------------------------------------
# Per-slug threading locks (same pattern as project_archive.py)
# ---------------------------------------------------------------------------

_slug_locks: dict[str, threading.Lock] = {}
_locks_lock = threading.Lock()


def _get_slug_lock(slug: str) -> threading.Lock:
    with _locks_lock:
        if slug not in _slug_locks:
            _slug_locks[slug] = threading.Lock()
        return _slug_locks[slug]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slugify(name: str, max_len: int = 60) -> str:
    """Convert a product name to a filesystem-safe slug."""
    slug = re.sub(r"[^\w\s-]", "", name.lower().strip())
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = slug.strip("-")
    if len(slug) > max_len:
        slug = slug[:max_len].rstrip("-")
    return slug or f"product-{uuid.uuid4().hex[:6]}"


def _dedup_slug(base_slug: str) -> str:
    """Ensure slug uniqueness by appending -2, -3, etc. if needed."""
    if not (PRODUCTS_DIR / base_slug).exists():
        return base_slug
    counter = 2
    while (PRODUCTS_DIR / f"{base_slug}-{counter}").exists():
        counter += 1
    return f"{base_slug}-{counter}"


def _product_dir(slug: str) -> Path:
    return PRODUCTS_DIR / slug


def _product_yaml_path(slug: str) -> Path:
    return _product_dir(slug) / PRODUCT_YAML_FILENAME


def _issues_dir(slug: str) -> Path:
    return _product_dir(slug) / ISSUES_DIR_NAME


def _gen_id(prefix: str) -> str:
    """Generate an ID: prefix + 8 hex chars."""
    return f"{prefix}{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Product CRUD
# ---------------------------------------------------------------------------

def create_product(
    *,
    name: str,
    owner_id: str,
    description: str = "",
    status: ProductStatus = ProductStatus.PLANNING,
    current_version: str = "0.1.0",
) -> dict:
    """Create a new product. Returns the product dict."""
    slug = _dedup_slug(_slugify(name))
    product_id = _gen_id("prod_")
    now = datetime.now().isoformat()

    data = {
        "id": product_id,
        "name": name,
        "slug": slug,
        "owner_id": owner_id,
        "description": description,
        "status": status,
        "current_version": current_version,
        "key_results": [],
        "workspace_initialized": False,
        "created_at": now,
        "updated_at": now,
    }

    with _get_slug_lock(slug):
        path = _product_yaml_path(slug)
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_yaml(path, data)

    mark_dirty(DirtyCategory.PRODUCTS)
    logger.debug("Created product {} (slug={})", product_id, slug)
    return data


def load_product(slug: str) -> dict | None:
    """Load a product by slug. Returns None if not found."""
    path = _product_yaml_path(slug)
    data = _read_yaml(path)
    return data if data else None


def list_products() -> list[dict]:
    """List all products."""
    if not PRODUCTS_DIR.exists():
        return []
    results = []
    for d in sorted(PRODUCTS_DIR.iterdir()):
        if not d.is_dir():
            continue
        yaml_path = d / PRODUCT_YAML_FILENAME
        if yaml_path.exists():
            data = _read_yaml(yaml_path)
            if data:
                results.append(data)
    return results


def update_product(slug: str, **fields) -> dict | None:
    """Update product fields. Returns updated dict or None if not found."""
    with _get_slug_lock(slug):
        path = _product_yaml_path(slug)
        data = _read_yaml(path)
        if not data:
            logger.warning("update_product: slug={} not found", slug)
            return None
        for key, value in fields.items():
            if value is not None:
                data[key] = value if not isinstance(value, ProductStatus) else value.value
        data["updated_at"] = datetime.now().isoformat()
        _write_yaml(path, data)

    mark_dirty(DirtyCategory.PRODUCTS)
    return data


# ---------------------------------------------------------------------------
# Key Results
# ---------------------------------------------------------------------------

def add_key_result(slug: str, *, title: str, target: float, unit: str = "") -> dict:
    """Add a key result to a product. Returns the KR dict."""
    kr_id = _gen_id("kr_")
    kr = {
        "id": kr_id,
        "title": title,
        "target": target,
        "current": 0.0,
        "unit": unit,
        "created_at": datetime.now().isoformat(),
    }

    with _get_slug_lock(slug):
        path = _product_yaml_path(slug)
        data = _read_yaml(path)
        if not data:
            logger.error("add_key_result: product slug={} not found", slug)
            raise ValueError(f"Product {slug} not found")
        data.setdefault("key_results", []).append(kr)
        data["updated_at"] = datetime.now().isoformat()
        _write_yaml(path, data)

    mark_dirty(DirtyCategory.PRODUCTS)
    logger.debug("Added KR {} to product {}", kr_id, slug)
    return kr


def update_kr_progress(slug: str, kr_id: str, *, current: float) -> dict:
    """Update a key result's current progress. Returns updated KR dict.

    Raises ValueError if product or KR not found.
    """
    with _get_slug_lock(slug):
        path = _product_yaml_path(slug)
        data = _read_yaml(path)
        if not data:
            raise ValueError(f"Product '{slug}' not found")
        for kr in data.get("key_results", []):
            if kr["id"] == kr_id:
                old_current = kr.get("current")
                if old_current != current:
                    _append_history(kr, "current", old_current, current)
                kr["current"] = current
                data["updated_at"] = datetime.now().isoformat()
                _write_yaml(path, data)
                mark_dirty(DirtyCategory.PRODUCTS)
                return kr

    raise ValueError(f"KR '{kr_id}' not found in product '{slug}'")


def update_kr_fields(slug: str, kr_id: str, **fields) -> dict:
    """Update arbitrary fields on a key result. Returns updated KR dict.

    Raises ValueError if product or KR not found.
    """
    with _get_slug_lock(slug):
        path = _product_yaml_path(slug)
        data = _read_yaml(path)
        if not data:
            raise ValueError(f"Product '{slug}' not found")
        for kr in data.get("key_results", []):
            if kr["id"] == kr_id:
                for k, v in fields.items():
                    if v is not None:
                        old_v = kr.get(k)
                        if old_v != v:
                            _append_history(kr, k, old_v, v)
                        kr[k] = v
                data["updated_at"] = datetime.now().isoformat()
                _write_yaml(path, data)
                mark_dirty(DirtyCategory.PRODUCTS)
                return kr

    raise ValueError(f"KR '{kr_id}' not found in product '{slug}'")


# ---------------------------------------------------------------------------
# Issue CRUD
# ---------------------------------------------------------------------------

def _append_history(data: dict, field: str, old_value, new_value, changed_by: str = "system") -> None:
    """Append a history entry to a dict's history list. Cap at 100 entries."""
    data.setdefault("history", []).append({
        "timestamp": datetime.now().isoformat(),
        "field": field,
        "old_value": str(old_value) if old_value is not None else None,
        "new_value": str(new_value) if new_value is not None else None,
        "changed_by": changed_by,
    })
    if len(data["history"]) > 100:
        data["history"] = data["history"][-100:]


def create_issue(
    *,
    slug: str,
    title: str,
    created_by: str,
    description: str = "",
    priority: IssuePriority = IssuePriority.P2,
    labels: list[str] | None = None,
    assignee_id: str | None = None,
    milestone_version: str | None = None,
    story_points: int | None = None,
    sprint: str | None = None,
) -> dict:
    """Create an issue for a product. Returns the issue dict."""
    issue_id = _gen_id("issue_")
    product = load_product(slug)
    product_id = product["id"] if product else ""
    now = datetime.now().isoformat()

    data = {
        "id": issue_id,
        "product_id": product_id,
        "title": title,
        "description": description,
        "status": IssueStatus.BACKLOG,
        "priority": priority,
        "labels": labels or [],
        "assignee_id": assignee_id,
        "linked_task_ids": [],
        "linked_issue_ids": [],
        "milestone_version": milestone_version,
        "created_at": now,
        "created_by": created_by,
        "closed_at": None,
        "resolution": None,
        "reopened_count": 0,
        "story_points": story_points,
        "sprint": sprint,
    }

    issues_path = _issues_dir(slug)
    issues_path.mkdir(parents=True, exist_ok=True)
    issue_path = issues_path / f"{issue_id}.yaml"
    _write_yaml(issue_path, data)

    mark_dirty(DirtyCategory.PRODUCTS)
    logger.debug("Created issue {} for product {}", issue_id, slug)
    return data


def load_issue(slug: str, issue_id: str) -> dict | None:
    """Load a single issue by ID. Returns None if not found."""
    path = _issues_dir(slug) / f"{issue_id}.yaml"
    data = _read_yaml(path)
    return data if data else None


def list_issues(
    slug: str,
    *,
    status: IssueStatus | None = None,
    priority: IssuePriority | None = None,
    labels: list[str] | None = None,
) -> list[dict]:
    """List issues for a product, optionally filtered."""
    issues_path = _issues_dir(slug)
    if not issues_path.exists():
        return []
    results = []
    for f in sorted(issues_path.iterdir()):
        if f.suffix not in (".yaml", ".yml"):
            continue
        data = _read_yaml(f)
        if not data:
            continue
        # Apply filters
        if status is not None and data.get("status") != status.value:
            continue
        if priority is not None and data.get("priority") != priority.value:
            continue
        if labels is not None:
            issue_labels = set(data.get("labels", []))
            if not set(labels).intersection(issue_labels):
                continue
        results.append(data)
    return results


def update_issue(slug: str, issue_id: str, **fields) -> dict | None:
    """Update issue fields. Returns updated dict or None if not found."""
    with _get_slug_lock(slug):
        path = _issues_dir(slug) / f"{issue_id}.yaml"
        data = _read_yaml(path)
        if not data:
            logger.warning("update_issue: issue {} not found in {}", issue_id, slug)
            return None
        for key, value in fields.items():
            if value is not None:
                old_value = data.get(key)
                if old_value != value:
                    _append_history(data, key, old_value, value, changed_by="system")
                data[key] = value
        _write_yaml(path, data)
    mark_dirty(DirtyCategory.PRODUCTS)
    return data


def close_issue(
    slug: str,
    issue_id: str,
    *,
    resolution: IssueResolution = IssueResolution.FIXED,
) -> dict | None:
    """Close an issue with a resolution. Returns updated dict or None."""
    with _get_slug_lock(slug):
        path = _issues_dir(slug) / f"{issue_id}.yaml"
        data = _read_yaml(path)
        if not data:
            logger.warning("close_issue: issue {} not found in {}", issue_id, slug)
            return None
        old_status = data.get("status")
        _append_history(data, "status", old_status, IssueStatus.DONE.value, changed_by="system")
        data["status"] = IssueStatus.DONE.value
        data["resolution"] = resolution.value
        data["closed_at"] = datetime.now().isoformat()
        _write_yaml(path, data)
    mark_dirty(DirtyCategory.PRODUCTS)
    logger.debug("Closed issue {} with resolution {}", issue_id, resolution.value)
    return data


def reopen_issue(slug: str, issue_id: str) -> dict | None:
    """Reopen a closed issue. Increments reopened_count. Returns updated dict or None."""
    with _get_slug_lock(slug):
        path = _issues_dir(slug) / f"{issue_id}.yaml"
        data = _read_yaml(path)
        if not data:
            logger.warning("reopen_issue: issue {} not found in {}", issue_id, slug)
            return None
        old_status = data.get("status")
        _append_history(data, "status", old_status, IssueStatus.BACKLOG.value, changed_by="system")
        data["status"] = IssueStatus.BACKLOG.value
        data["closed_at"] = None
        data["resolution"] = None
        data["reopened_count"] = data.get("reopened_count", 0) + 1
        _write_yaml(path, data)
    mark_dirty(DirtyCategory.PRODUCTS)
    logger.debug("Reopened issue {} (reopened_count={})", issue_id, data["reopened_count"])
    return data


# ---------------------------------------------------------------------------
# Product Versioning
# ---------------------------------------------------------------------------

def _versions_dir(slug: str) -> Path:
    return _product_dir(slug) / VERSIONS_DIR_NAME


def list_versions(slug: str) -> list[dict]:
    """List all versions for a product, newest first."""
    vdir = _versions_dir(slug)
    if not vdir.exists():
        return []
    versions = []
    for f in sorted(vdir.iterdir(), reverse=True):
        if f.name.endswith(".yaml"):
            versions.append(_read_yaml(f))
    return versions


def _bump_version(current: str, bump: str = "patch") -> str:
    """Bump a semver string. bump = 'patch' | 'minor' | 'major'."""
    parts = current.split(".")
    major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])
    if bump == "major":
        return f"{major + 1}.0.0"
    elif bump == "minor":
        return f"{major}.{minor + 1}.0"
    else:
        return f"{major}.{minor}.{patch + 1}"


def _generate_changelog(product_slug: str, resolved_issue_ids: list[str]) -> str:
    """Generate changelog text from resolved issue titles."""
    lines = []
    for issue_id in resolved_issue_ids:
        issue = load_issue(product_slug, issue_id)
        if issue:
            lines.append(f"- {issue['title']} (#{issue_id})")
    return "\n".join(lines) if lines else "- No issues resolved"


def release_version(
    product_slug: str,
    resolved_issue_ids: list[str],
    project_ids: list[str] | None = None,
    bump: str = "patch",
) -> dict:
    """Release a new product version. Returns the version dict."""
    with _get_slug_lock(product_slug):
        product = _read_yaml(_product_yaml_path(product_slug))
        if not product:
            raise ValueError(f"Product '{product_slug}' not found")

        new_version = _bump_version(product["current_version"], bump)
        changelog = _generate_changelog(product_slug, resolved_issue_ids)

        version_record = {
            "version": new_version,
            "released_at": datetime.now().isoformat(),
            "changelog": changelog,
            "resolved_issue_ids": resolved_issue_ids,
            "project_ids": project_ids or [],
        }

        versions_dir = _versions_dir(product_slug)
        versions_dir.mkdir(parents=True, exist_ok=True)
        _write_yaml(versions_dir / f"{new_version}.yaml", version_record)

        product["current_version"] = new_version
        _write_yaml(_product_yaml_path(product_slug), product)

    # Mark resolved issues as released
    for issue_id in resolved_issue_ids:
        issue = load_issue(product_slug, issue_id)
        if issue and issue.get("status") != IssueStatus.RELEASED.value:
            update_issue(product_slug, issue_id, status=IssueStatus.RELEASED.value)

    mark_dirty(DirtyCategory.PRODUCTS)
    logger.info("[VERSION] Released {} for product '{}'", new_version, product_slug)
    return version_record


# ---------------------------------------------------------------------------
# Product Context Injection
# ---------------------------------------------------------------------------

def build_product_context(product_slug: str) -> str:
    """Build product context string for agent prompt injection."""
    product = load_product(product_slug)
    if not product:
        return ""
    parts: list[str] = []
    parts.append(f"=== Product: {product['name']} (v{product['current_version']}) ===")
    desc = product.get("description") or product.get("objective", "")
    if desc:
        parts.append(f"Objective: {desc}")
    krs = product.get("key_results", [])
    if krs:
        parts.append("\nKey Results:")
        for kr in krs:
            target = kr.get("target", 0)
            current = kr.get("current", 0)
            pct = (current / target * 100) if target else 0
            unit = kr.get("unit", "")
            suffix = f" {unit}" if unit else ""
            parts.append(f"  - {kr['title']}: {current}/{target}{suffix} ({pct:.0f}%)")
    issues = list_issues(product_slug, status=IssueStatus.BACKLOG)
    issues.sort(key=lambda i: i.get("priority", "P3"))
    if issues:
        parts.append(f"\nActive Issues ({len(issues)}):")
        for issue in issues[:10]:
            parts.append(f"  - [{issue['priority']}] {issue['title']} ({issue['id']})")
        if len(issues) > 10:
            parts.append(f"  ... and {len(issues) - 10} more")
    parts.append("=== End Product Context ===")
    return "\n".join(parts)


def export_product(slug: str) -> dict | None:
    """Export product as a portable bundle."""
    product = load_product(slug)
    if not product:
        return None
    issues = list_issues(slug)
    return {
        "format": "omc-product-v1",
        "product": {
            "name": product.get("name", ""),
            "description": product.get("description", ""),
            "key_results": [
                {"title": kr["title"], "target": kr["target"], "current": kr.get("current", 0), "unit": kr.get("unit", "")}
                for kr in product.get("key_results", [])
            ],
        },
        "issues": [
            {
                "title": issue["title"],
                "description": issue.get("description", ""),
                "priority": issue.get("priority", "P2"),
                "labels": issue.get("labels", []),
                "story_points": issue.get("story_points"),
                "sprint": issue.get("sprint"),
                "status": issue.get("status", "backlog"),
            }
            for issue in issues
        ],
    }


def import_product(bundle: dict, owner_id: str = "", auto_activate: bool = True) -> dict:
    """Import product from a portable bundle. Returns result dict."""
    if bundle.get("format") != "omc-product-v1":
        raise ValueError("Invalid format. Expected 'omc-product-v1'")

    product_data = bundle.get("product", {})
    name = product_data.get("name")
    if not name:
        raise ValueError("Product name is required")

    status = ProductStatus.ACTIVE if auto_activate and owner_id else ProductStatus.PLANNING
    product = create_product(
        name=name,
        owner_id=owner_id,
        description=product_data.get("description", ""),
        status=status,
    )
    slug = product["slug"]

    for kr_data in product_data.get("key_results", []):
        add_key_result(slug, title=kr_data["title"], target=kr_data.get("target", 1), unit=kr_data.get("unit", ""))

    issue_ids = []
    for issue_data in bundle.get("issues", []):
        try:
            priority = IssuePriority(issue_data.get("priority", "P2"))
        except ValueError:
            priority = IssuePriority.P2
        issue = create_issue(
            slug=slug,
            title=issue_data["title"],
            description=issue_data.get("description", ""),
            priority=priority,
            labels=issue_data.get("labels", []),
            story_points=issue_data.get("story_points"),
            sprint=issue_data.get("sprint"),
            created_by="import",
        )
        issue_ids.append(issue["id"])

    return {
        "slug": slug,
        "product_id": product["id"],
        "issues_created": len(issue_ids),
        "krs_created": len(product_data.get("key_results", [])),
        "auto_activated": status == ProductStatus.ACTIVE,
    }


def delete_product(slug: str) -> dict:
    """Delete a product, its issues/versions, and all linked projects.

    Returns summary dict with counts of deleted items.
    Raises ValueError if product not found.
    """
    product = load_product(slug)
    if not product:
        raise ValueError(f"Product '{slug}' not found")

    product_id = product.get("id", "")

    # Delete linked projects
    deleted_projects = 0
    if product_id:
        from onemancompany.core.project_archive import list_projects
        from onemancompany.core.config import PROJECTS_DIR
        import shutil as _shutil

        for proj in list_projects():
            if proj.get("product_id") == product_id:
                proj_dir = PROJECTS_DIR / proj["project_id"]
                if proj_dir.exists():
                    # Cancel running tasks for this project
                    try:
                        from onemancompany.core.agent_loop import employee_manager
                        employee_manager.abort_project(proj["project_id"])
                    except Exception as e:
                        logger.debug("[PRODUCT] Could not abort project {}: {}", proj["project_id"], e)
                    _shutil.rmtree(proj_dir)
                    deleted_projects += 1
                    logger.debug("[PRODUCT] Deleted linked project {}", proj["project_id"])

    # Count issues before deletion
    issues = list_issues(slug)
    deleted_issues = len(issues)

    # Delete product directory (product.yaml, issues/, versions/)
    import shutil
    product_dir = _product_dir(slug)
    with _get_slug_lock(slug):
        shutil.rmtree(product_dir)

    mark_dirty(DirtyCategory.PRODUCTS)
    logger.info("[PRODUCT] Deleted product '{}': {} issues, {} projects removed", slug, deleted_issues, deleted_projects)
    return {
        "deleted": True,
        "slug": slug,
        "issues_deleted": deleted_issues,
        "projects_deleted": deleted_projects,
    }


def find_slug_by_product_id(product_id: str) -> str | None:
    """Find product slug by product ID."""
    for p in list_products():
        if p.get("id") == product_id:
            return p.get("slug")
    return None


# ---------------------------------------------------------------------------
# Issue Status Derivation
# ---------------------------------------------------------------------------

def derive_issue_status(slug: str, issue_id: str) -> IssueStatus:
    """Derive issue status from linked TaskNode states.

    Rules:
    - No linked tasks → BACKLOG
    - All tasks pending → PLANNED
    - Any task processing/holding → IN_PROGRESS
    - All tasks completed (not yet accepted) → IN_REVIEW
    - All tasks accepted/finished → DONE
    - Issue already released (in a version) → RELEASED
    """
    issue = load_issue(slug, issue_id)
    if not issue:
        return IssueStatus.BACKLOG

    # Already released? Keep it.
    if issue.get("status") == IssueStatus.RELEASED.value:
        return IssueStatus.RELEASED

    linked_ids = issue.get("linked_task_ids", [])
    if not linked_ids:
        return IssueStatus.BACKLOG

    # Load task node statuses from project archives
    from onemancompany.core.task_lifecycle import TaskPhase

    statuses = []
    for task_ref in linked_ids:
        status = _resolve_task_status(task_ref)
        if status:
            statuses.append(status)

    if not statuses:
        return IssueStatus.PLANNED

    # Derive from statuses
    status_set = set(statuses)

    # Any processing/holding → in_progress
    active = {TaskPhase.PROCESSING.value, TaskPhase.HOLDING.value}
    if status_set & active:
        return IssueStatus.IN_PROGRESS

    # All finished/accepted → done
    done = {TaskPhase.FINISHED.value, TaskPhase.ACCEPTED.value}
    if status_set <= done:
        return IssueStatus.DONE

    # All completed (but not accepted yet) → in_review
    completed_plus = {TaskPhase.COMPLETED.value} | done
    if status_set <= completed_plus and TaskPhase.COMPLETED.value in status_set:
        return IssueStatus.IN_REVIEW

    # All pending → planned
    pending = {TaskPhase.PENDING.value, TaskPhase.BLOCKED.value}
    if status_set <= pending:
        return IssueStatus.PLANNED

    # Mix of pending and active → in_progress
    return IssueStatus.IN_PROGRESS


def _resolve_task_status(task_ref: str) -> str | None:
    """Resolve a task reference to its status.

    task_ref can be a project_id. Look up the project's task tree
    and find the overall status.
    """
    from onemancompany.core.project_archive import load_project as _load_proj

    proj = _load_proj(task_ref)
    if not proj:
        return None

    status = proj.get("status", "")
    # Map project status to TaskPhase equivalent
    if status == "archived":
        return "finished"
    elif status == "active":
        # Check if the project has an active iteration
        iters = proj.get("iterations", [])
        if not iters:
            return "pending"
        # Use the latest iteration's status
        from onemancompany.core.project_archive import load_iteration

        latest_iter_id = iters[-1] if isinstance(iters[-1], str) else iters[-1].get("id", "")
        if latest_iter_id:
            iter_doc = load_iteration(task_ref, latest_iter_id)
            if iter_doc:
                return iter_doc.get("status", "pending")
        return "processing"
    return None


def sync_issue_statuses(slug: str) -> list[dict]:
    """Sync all issue statuses by deriving from linked TaskNode states.

    Returns list of issues whose status changed.
    """
    issues = list_issues(slug)
    changed = []
    for issue in issues:
        # Skip released issues
        if issue.get("status") == IssueStatus.RELEASED.value:
            continue

        derived = derive_issue_status(slug, issue["id"])
        current = issue.get("status", IssueStatus.BACKLOG.value)

        if derived.value != current:
            update_issue(slug, issue["id"], status=derived.value)
            changed.append({"issue_id": issue["id"], "old": current, "new": derived.value})
            logger.debug("[PRODUCT] Issue {} status derived: {} → {}", issue["id"], current, derived.value)

    return changed
