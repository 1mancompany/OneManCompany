"""Product management — product records, key results, issues, and sprints.

Products stored at: PRODUCTS_DIR/{slug}/product.yaml
Issues stored at:   PRODUCTS_DIR/{slug}/issues/{issue_id}.yaml
Sprints stored at:  PRODUCTS_DIR/{slug}/sprints/{sprint_id}.yaml

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
    ACTIVITY_LOG_DIR_NAME,
    ISSUES_DIR_NAME,
    PRODUCT_YAML_FILENAME,
    PRODUCTS_DIR,
    REVIEWS_DIR_NAME,
    SPRINTS_DIR_NAME,
    VERSIONS_DIR_NAME,
    DirtyCategory,
)
from onemancompany.core.models import (
    IssueRelation,
    IssueResolution,
    IssuePriority,
    IssueStatus,
    ProductStatus,
    SprintStatus,
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
    product = load_product(slug)
    if not product:
        raise ValueError(f"Product '{slug}' not found")
    issue_id = _gen_id("issue_")
    product_id = product["id"]
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
        "issue_links": [],
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
    """Load a single issue by ID. Returns None if not found.

    Auto-migrates old ``linked_issue_ids`` format to ``issue_links``.
    """
    path = _issues_dir(slug) / f"{issue_id}.yaml"
    data = _read_yaml(path)
    if not data:
        return None

    # Auto-migrate: linked_issue_ids → issue_links
    if "linked_issue_ids" in data and "issue_links" not in data:
        old_ids = data.pop("linked_issue_ids", [])
        data["issue_links"] = [
            {"issue_id": iid, "relation": IssueRelation.RELATES_TO.value}
            for iid in old_ids
        ]
        _write_yaml(path, data)
        logger.debug("Migrated linked_issue_ids → issue_links for {}", issue_id)

    return data


def list_issues(
    slug: str,
    *,
    status: IssueStatus | None = None,
    priority: IssuePriority | None = None,
    labels: list[str] | None = None,
    sprint: str | None = None,
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
        if sprint is not None and data.get("sprint") != sprint:
            continue
        results.append(data)
    return results


def update_issue(slug: str, issue_id: str, **fields) -> dict:
    """Update issue fields. Returns updated dict. Raises ValueError if not found."""
    with _get_slug_lock(slug):
        path = _issues_dir(slug) / f"{issue_id}.yaml"
        data = _read_yaml(path)
        if not data:
            raise ValueError(f"Issue '{issue_id}' not found in product '{slug}'")
        # Validate status transition if status is being changed
        new_status = fields.get("status")
        if new_status is not None:
            current_status = data.get("status", IssueStatus.BACKLOG.value)
            if new_status != current_status:
                _validate_status_transition(current_status, new_status)
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
) -> dict:
    """Close an issue with a resolution. Returns updated dict. Raises ValueError if not found."""
    with _get_slug_lock(slug):
        path = _issues_dir(slug) / f"{issue_id}.yaml"
        data = _read_yaml(path)
        if not data:
            raise ValueError(f"Issue '{issue_id}' not found in product '{slug}'")
        old_status = data.get("status")
        _append_history(data, "status", old_status, IssueStatus.DONE.value, changed_by="system")
        data["status"] = IssueStatus.DONE.value
        data["resolution"] = resolution.value
        data["closed_at"] = datetime.now().isoformat()
        _write_yaml(path, data)
    mark_dirty(DirtyCategory.PRODUCTS)
    logger.debug("Closed issue {} with resolution {}", issue_id, resolution.value)
    return data


def reopen_issue(slug: str, issue_id: str) -> dict:
    """Reopen a closed issue. Increments reopened_count. Returns updated dict. Raises ValueError if not found."""
    with _get_slug_lock(slug):
        path = _issues_dir(slug) / f"{issue_id}.yaml"
        data = _read_yaml(path)
        if not data:
            raise ValueError(f"Issue '{issue_id}' not found in product '{slug}'")
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


def delete_issue(slug: str, issue_id: str) -> None:
    """Delete an issue and clean up all links referencing it. Raises ValueError if not found."""
    issue = load_issue(slug, issue_id)
    if not issue:
        raise ValueError(f"Issue '{issue_id}' not found in product '{slug}'")

    # Clean up links from other issues pointing to this one
    all_issues = list_issues(slug)
    for other in all_issues:
        if other["id"] == issue_id:
            continue
        links = other.get("issue_links", [])
        if any(l["issue_id"] == issue_id for l in links):
            _remove_link_entry(slug, other["id"], issue_id)

    # Remove the issue file
    with _get_slug_lock(slug):
        path = _issues_dir(slug) / f"{issue_id}.yaml"
        path.unlink(missing_ok=True)
    mark_dirty(DirtyCategory.PRODUCTS)
    logger.debug("Deleted issue {} from {}", issue_id, slug)


# ---------------------------------------------------------------------------
# Issue Status Transitions
# ---------------------------------------------------------------------------

_VALID_TRANSITIONS: dict[str, set[str]] = {
    IssueStatus.BACKLOG.value: {IssueStatus.PLANNED.value, IssueStatus.IN_PROGRESS.value},
    IssueStatus.PLANNED.value: {IssueStatus.IN_PROGRESS.value, IssueStatus.BACKLOG.value},
    IssueStatus.IN_PROGRESS.value: {IssueStatus.IN_REVIEW.value, IssueStatus.DONE.value, IssueStatus.BACKLOG.value},
    IssueStatus.IN_REVIEW.value: {IssueStatus.DONE.value, IssueStatus.IN_PROGRESS.value, IssueStatus.BACKLOG.value},
    IssueStatus.DONE.value: {IssueStatus.RELEASED.value, IssueStatus.BACKLOG.value},
    IssueStatus.RELEASED.value: {IssueStatus.BACKLOG.value},
}


def _validate_status_transition(current: str, target: str) -> None:
    """Raise ValueError if the status transition is not allowed."""
    allowed = _VALID_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise ValueError(
            f"Invalid transition: '{current}' → '{target}'. "
            f"Allowed: {sorted(allowed)}"
        )


# ---------------------------------------------------------------------------
# Issue Links
# ---------------------------------------------------------------------------

_REVERSE_RELATION = {
    IssueRelation.BLOCKS.value: IssueRelation.BLOCKED_BY.value,
    IssueRelation.BLOCKED_BY.value: IssueRelation.BLOCKS.value,
    IssueRelation.RELATES_TO.value: IssueRelation.RELATES_TO.value,
}


def add_issue_link(
    slug: str,
    issue_id: str,
    target_id: str,
    relation: IssueRelation,
) -> None:
    """Add a bidirectional link between two issues.

    Raises ValueError on self-reference or if either issue is not found.
    Idempotent — re-adding the same link is a no-op.
    """
    if issue_id == target_id:
        raise ValueError("Cannot link an issue to itself (self-reference)")

    issue = load_issue(slug, issue_id)
    if not issue:
        raise ValueError(f"Issue '{issue_id}' not found in '{slug}'")
    target = load_issue(slug, target_id)
    if not target:
        raise ValueError(f"Issue '{target_id}' not found in '{slug}'")

    rel_value = relation.value if hasattr(relation, "value") else relation
    reverse_rel = _REVERSE_RELATION[rel_value]

    # Circular dependency check for blocking relations
    if rel_value == IssueRelation.BLOCKS.value:
        _check_block_cycle(slug, issue_id, target_id)
    elif rel_value == IssueRelation.BLOCKED_BY.value:
        # blocked_by is the reverse: target blocks issue
        _check_block_cycle(slug, target_id, issue_id)

    # Add forward link (idempotent)
    _add_link_entry(slug, issue_id, target_id, rel_value)
    # Add reverse link
    _add_link_entry(slug, target_id, issue_id, reverse_rel)

    mark_dirty(DirtyCategory.PRODUCTS)
    logger.debug("Linked {} —{}→ {}", issue_id, rel_value, target_id)


def _check_block_cycle(slug: str, blocker_id: str, blocked_id: str) -> None:
    """Raise ValueError if adding 'blocker_id blocks blocked_id' would create a cycle.

    Walks the existing 'blocks' graph starting from blocked_id to see if
    blocker_id is reachable (meaning blocked_id already transitively blocks blocker_id).
    """
    visited: set[str] = set()
    queue = [blocked_id]
    while queue:
        current = queue.pop()
        if current in visited:
            continue
        visited.add(current)
        issue = load_issue(slug, current)
        if not issue:
            continue
        for link in issue.get("issue_links", []):
            if link["relation"] != IssueRelation.BLOCKS.value:
                continue
            downstream = link["issue_id"]
            if downstream == blocker_id:
                raise ValueError(
                    f"Circular dependency: {blocked_id} already transitively "
                    f"blocks {blocker_id}"
                )
            queue.append(downstream)


def _add_link_entry(slug: str, issue_id: str, target_id: str, relation: str) -> None:
    """Add a single link entry to an issue (idempotent)."""
    with _get_slug_lock(slug):
        path = _issues_dir(slug) / f"{issue_id}.yaml"
        data = _read_yaml(path)
        if not data:
            return
        links = data.setdefault("issue_links", [])
        # Idempotent check
        if any(l["issue_id"] == target_id and l["relation"] == relation for l in links):
            return
        links.append({
            "issue_id": target_id,
            "relation": relation,
            "created_at": datetime.now().isoformat(),
        })
        _write_yaml(path, data)


def remove_issue_link(slug: str, issue_id: str, target_id: str) -> None:
    """Remove all links between two issues (both directions). Silently ignores missing links."""
    _remove_link_entry(slug, issue_id, target_id)
    _remove_link_entry(slug, target_id, issue_id)
    mark_dirty(DirtyCategory.PRODUCTS)
    logger.debug("Unlinked {} ↔ {}", issue_id, target_id)


def _remove_link_entry(slug: str, issue_id: str, target_id: str) -> None:
    """Remove all link entries from issue_id to target_id."""
    with _get_slug_lock(slug):
        path = _issues_dir(slug) / f"{issue_id}.yaml"
        data = _read_yaml(path)
        if not data:
            return
        links = data.get("issue_links", [])
        data["issue_links"] = [l for l in links if l["issue_id"] != target_id]
        _write_yaml(path, data)


def get_issue_links(slug: str, issue_id: str) -> list[dict]:
    """Return the issue_links list for an issue."""
    issue = load_issue(slug, issue_id)
    if not issue:
        return []
    return issue.get("issue_links", [])


def is_blocked(slug: str, issue_id: str) -> bool:
    """Check if an issue is blocked by any unfinished blocker."""
    issue = load_issue(slug, issue_id)
    if not issue:
        return False
    links = issue.get("issue_links", [])
    for link in links:
        if link["relation"] != IssueRelation.BLOCKED_BY.value:
            continue
        blocker = load_issue(slug, link["issue_id"])
        if blocker and blocker.get("status") not in _DONE_STATUSES:
            return True
    return False


# ---------------------------------------------------------------------------
# Review Checklist
# ---------------------------------------------------------------------------

_DEFAULT_REVIEW_ITEMS = [
    {"key": "update_kr", "label": "更新 KR 进度", "checked": False},
    {"key": "review_issues", "label": "Review open issues", "checked": False},
    {"key": "assign_backlog", "label": "安排 backlog 优先级", "checked": False},
    {"key": "create_issues", "label": "创建新 issues", "checked": False},
]


def _reviews_dir(slug: str) -> Path:
    return _product_dir(slug) / REVIEWS_DIR_NAME


def create_review(
    slug: str,
    *,
    trigger: str,
    trigger_ref: str = "",
    owner: str,
    items: list[dict] | None = None,
) -> dict:
    """Create a review checklist for a product. Returns the review dict."""
    review_id = _gen_id("rev_")
    now = datetime.now().isoformat()

    data = {
        "id": review_id,
        "product_slug": slug,
        "trigger": trigger,
        "trigger_ref": trigger_ref,
        "created_at": now,
        "owner": owner,
        "status": "open",
        "items": items if items is not None else [dict(i) for i in _DEFAULT_REVIEW_ITEMS],
        "completed_at": None,
    }

    rdir = _reviews_dir(slug)
    rdir.mkdir(parents=True, exist_ok=True)
    with _get_slug_lock(slug):
        _write_yaml(rdir / f"{review_id}.yaml", data)
    mark_dirty(DirtyCategory.PRODUCTS)
    logger.debug("[PRODUCT] Review created: {} in {}", review_id, slug)
    return data


def load_review(slug: str, review_id: str) -> dict | None:
    """Load a single review by ID."""
    path = _reviews_dir(slug) / f"{review_id}.yaml"
    if not path.exists():
        return None
    return _read_yaml(path)


def list_reviews(slug: str, status: str | None = None) -> list[dict]:
    """List all reviews for a product, optionally filtered by status."""
    rdir = _reviews_dir(slug)
    if not rdir.exists():
        return []
    reviews = []
    for f in sorted(rdir.iterdir()):
        if f.suffix == ".yaml":
            data = _read_yaml(f)
            if data and (status is None or data.get("status") == status):
                reviews.append(data)
    return reviews


def update_review_item(slug: str, review_id: str, item_key: str, *, checked: bool) -> dict:
    """Check or uncheck a review item. Returns updated review dict.

    Raises ValueError if review or item key not found.
    """
    with _get_slug_lock(slug):
        path = _reviews_dir(slug) / f"{review_id}.yaml"
        data = _read_yaml(path)
        if not data:
            raise ValueError(f"Review '{review_id}' not found in '{slug}'")
        for item in data.get("items", []):
            if item["key"] == item_key:
                item["checked"] = checked
                _write_yaml(path, data)
                mark_dirty(DirtyCategory.PRODUCTS)
                return data
    raise ValueError(f"Item key '{item_key}' not found in review '{review_id}'")


def complete_review(slug: str, review_id: str) -> dict:
    """Mark a review as completed. All items must be checked.

    Raises ValueError if review not found, already completed, or has unchecked items.
    """
    with _get_slug_lock(slug):
        path = _reviews_dir(slug) / f"{review_id}.yaml"
        data = _read_yaml(path)
        if not data:
            raise ValueError(f"Review '{review_id}' not found in '{slug}'")
        if data.get("status") == "completed":
            raise ValueError(f"Review '{review_id}' is already completed")
        unchecked = [i for i in data.get("items", []) if not i.get("checked")]
        if unchecked:
            keys = ", ".join(i["key"] for i in unchecked)
            raise ValueError(f"Cannot complete review: unchecked items: {keys}")
        data["status"] = "completed"
        data["completed_at"] = datetime.now().isoformat()
        _write_yaml(path, data)
    mark_dirty(DirtyCategory.PRODUCTS)
    logger.debug("[PRODUCT] Review completed: {}", review_id)
    return data


# ---------------------------------------------------------------------------
# Kanban Board
# ---------------------------------------------------------------------------


def kanban_board(slug: str) -> dict:
    """Return issues grouped by IssueStatus columns + blocked IDs.

    Raises ValueError if product not found.
    """
    product = load_product(slug)
    if not product:
        raise ValueError(f"Product '{slug}' not found")

    all_issues = list_issues(slug)
    columns: dict[str, list[dict]] = {s.value: [] for s in IssueStatus}
    blocked_ids: list[str] = []

    for issue in all_issues:
        status = issue.get("status", IssueStatus.BACKLOG.value)
        if status in columns:
            columns[status].append(issue)
        if is_blocked(slug, issue["id"]):
            blocked_ids.append(issue["id"])

    return {"columns": columns, "blocked_ids": blocked_ids}


# ---------------------------------------------------------------------------
# Roadmap Timeline
# ---------------------------------------------------------------------------


def roadmap_timeline(slug: str) -> dict:
    """Return sprints, versions, and milestoned issues for timeline display.

    Raises ValueError if product not found.
    """
    product = load_product(slug)
    if not product:
        raise ValueError(f"Product '{slug}' not found")

    sprints = list_sprints(slug)
    sprint_summaries = []
    for s in sprints:
        issue_count = len(list_issues(slug, sprint=s["id"]))
        sprint_summaries.append({
            "id": s["id"],
            "name": s["name"],
            "start_date": s["start_date"],
            "end_date": s["end_date"],
            "status": s["status"],
            "goal": s.get("goal", ""),
            "issue_count": issue_count,
        })

    versions = list_versions(slug)
    version_summaries = []
    for v in versions:
        version_summaries.append({
            "version": v["version"],
            "released_at": v["released_at"],
            "resolved_count": len(v.get("resolved_issue_ids", [])),
            "changelog": v.get("changelog", ""),
        })

    all_issues = list_issues(slug)
    milestoned = [
        {
            "issue_id": i["id"],
            "title": i["title"],
            "priority": i.get("priority", "P2"),
            "milestone_version": i["milestone_version"],
            "status": i.get("status", "backlog"),
        }
        for i in all_issues
        if i.get("milestone_version")
    ]

    return {
        "sprints": sprint_summaries,
        "versions": version_summaries,
        "milestoned_issues": milestoned,
    }


# ---------------------------------------------------------------------------
# Product Activity Log
# ---------------------------------------------------------------------------

_MAX_ACTIVITY_ENTRIES = 500


def _activity_dir(slug: str) -> Path:
    return _product_dir(slug) / ACTIVITY_LOG_DIR_NAME


def append_product_activity(
    slug: str,
    *,
    event_type: str,
    actor: str,
    detail: str,
) -> None:
    """Append an activity entry to the product's activity log."""
    adir = _activity_dir(slug)
    adir.mkdir(parents=True, exist_ok=True)
    log_path = adir / "log.yaml"

    entry = {
        "ts": datetime.now().isoformat(),
        "event_type": event_type,
        "actor": actor,
        "detail": detail,
    }

    with _get_slug_lock(slug):
        log = _read_yaml(log_path) or []
        if not isinstance(log, list):
            log = []
        log.append(entry)
        if len(log) > _MAX_ACTIVITY_ENTRIES:
            log = log[-_MAX_ACTIVITY_ENTRIES:]
        _write_yaml(log_path, log)


def list_product_activity(slug: str, *, limit: int = 50) -> list[dict]:
    """Return product activity entries, newest first."""
    log_path = _activity_dir(slug) / "log.yaml"
    log = _read_yaml(log_path)
    if not log or not isinstance(log, list):
        return []
    # Newest first
    log.reverse()
    return log[:limit]


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

                    # Remove product worktree dir if it exists
                    from onemancompany.core.config import PRODUCT_WORKTREE_DIR_NAME
                    wt_dir = proj_dir / PRODUCT_WORKTREE_DIR_NAME
                    if wt_dir.exists():
                        _shutil.rmtree(wt_dir)
                        logger.debug("[PRODUCT] Removed product worktree for project {}", proj["project_id"])

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


# ---------------------------------------------------------------------------
# Sprint management
# ---------------------------------------------------------------------------

_DONE_STATUSES = {IssueStatus.DONE.value, IssueStatus.RELEASED.value}


def _sprints_dir(slug: str) -> Path:
    return PRODUCTS_DIR / slug / SPRINTS_DIR_NAME


def create_sprint(
    *,
    slug: str,
    name: str,
    start_date: str,
    end_date: str,
    goal: str = "",
    capacity: int | None = None,
) -> dict:
    """Create a sprint for a product. Returns the sprint dict."""
    product = load_product(slug)
    if not product:
        raise ValueError(f"Product '{slug}' not found")

    # Validate dates
    try:
        sd = datetime.strptime(start_date, "%Y-%m-%d")
        ed = datetime.strptime(end_date, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"Invalid date format: {exc}") from exc
    if ed <= sd:
        raise ValueError(
            f"End date '{end_date}' must be after start date '{start_date}'"
        )

    sprint_id = _gen_id("sprint_")
    now = datetime.now().isoformat()

    data = {
        "id": sprint_id,
        "product_id": product["id"],
        "name": name,
        "goal": goal,
        "status": SprintStatus.PLANNING.value,
        "start_date": start_date,
        "end_date": end_date,
        "capacity": capacity,
        "velocity": None,
        "carry_over_count": 0,
        "completion_rate": None,
        "retrospective": None,
        "created_at": now,
        "closed_at": None,
    }

    sdir = _sprints_dir(slug)
    sdir.mkdir(parents=True, exist_ok=True)
    with _get_slug_lock(slug):
        _write_yaml(sdir / f"{sprint_id}.yaml", data)
    mark_dirty(DirtyCategory.PRODUCTS)
    logger.debug("[PRODUCT] Sprint created: {} in {}", sprint_id, slug)
    return data


def load_sprint(slug: str, sprint_id: str) -> dict | None:
    """Load a single sprint by ID."""
    path = _sprints_dir(slug) / f"{sprint_id}.yaml"
    if not path.exists():
        return None
    return _read_yaml(path)


def list_sprints(slug: str, status: str | None = None) -> list[dict]:
    """List all sprints for a product, optionally filtered by status."""
    sdir = _sprints_dir(slug)
    if not sdir.exists():
        return []
    sprints = []
    for f in sorted(sdir.iterdir()):
        if f.suffix == ".yaml":
            data = _read_yaml(f)
            if data and (status is None or data.get("status") == status):
                sprints.append(data)
    return sprints


def update_sprint(slug: str, sprint_id: str, **fields) -> dict:
    """Update sprint fields. Enforces single-active-sprint constraint."""
    sprint = load_sprint(slug, sprint_id)
    if not sprint:
        raise ValueError(f"Sprint '{sprint_id}' not found in '{slug}'")

    # Enforce: only one active sprint per product
    new_status = fields.get("status")
    if new_status == SprintStatus.ACTIVE.value:
        existing_active = get_active_sprint(slug)
        if existing_active and existing_active["id"] != sprint_id:
            raise ValueError(f"Product '{slug}' already has an active sprint: {existing_active['id']}")

    sprint.update(fields)
    with _get_slug_lock(slug):
        _write_yaml(_sprints_dir(slug) / f"{sprint_id}.yaml", sprint)
    mark_dirty(DirtyCategory.PRODUCTS)
    return sprint


def start_sprint(slug: str, sprint_id: str) -> dict:
    """Start a sprint (set status to active). Raises ValueError if already active elsewhere."""
    return update_sprint(slug, sprint_id, status=SprintStatus.ACTIVE.value)


def delete_sprint(slug: str, sprint_id: str) -> None:
    """Delete a sprint. Cannot delete an active sprint. Raises ValueError if not found."""
    sprint = load_sprint(slug, sprint_id)
    if not sprint:
        raise ValueError(f"Sprint '{sprint_id}' not found in '{slug}'")
    if sprint.get("status") == SprintStatus.ACTIVE.value:
        raise ValueError(f"Cannot delete active sprint '{sprint_id}'. Close it first.")
    with _get_slug_lock(slug):
        path = _sprints_dir(slug) / f"{sprint_id}.yaml"
        path.unlink(missing_ok=True)
    mark_dirty(DirtyCategory.PRODUCTS)
    logger.debug("Deleted sprint {} from {}", sprint_id, slug)


def get_active_sprint(slug: str) -> dict | None:
    """Return the current active sprint for a product, or None."""
    active = list_sprints(slug, status=SprintStatus.ACTIVE.value)
    return active[0] if active else None


def get_sprint_velocity(slug: str, sprint_id: str) -> int:
    """Calculate velocity: sum of story_points for done/released issues in this sprint."""
    issues = list_issues(slug, sprint=sprint_id)
    total = 0
    for issue in issues:
        if issue.get("status") in _DONE_STATUSES:
            total += issue.get("story_points") or 0
    return total


def close_sprint(slug: str, sprint_id: str) -> dict:
    """Close a sprint: calculate velocity, carry-over, generate retrospective."""
    sprint = load_sprint(slug, sprint_id)
    if not sprint:
        raise ValueError(f"Sprint '{sprint_id}' not found in '{slug}'")
    if sprint.get("status") != SprintStatus.ACTIVE.value:
        raise ValueError(f"Sprint '{sprint_id}' is not active")

    # 1. Calculate velocity
    velocity = get_sprint_velocity(slug, sprint_id)

    # 2. Identify unfinished issues
    all_issues = list_issues(slug, sprint=sprint_id)
    done_count = sum(1 for i in all_issues if i.get("status") in _DONE_STATUSES)
    total_count = len(all_issues)
    unfinished = [i for i in all_issues if i.get("status") not in _DONE_STATUSES]

    # 3. Carry-over: find next planning sprint
    planning_sprints = list_sprints(slug, status=SprintStatus.PLANNING.value)
    next_sprint = planning_sprints[0] if planning_sprints else None

    for issue in unfinished:
        if next_sprint:
            update_issue(slug, issue["id"], sprint=next_sprint["id"], carried_over=True)
        else:
            update_issue(slug, issue["id"], sprint="", status=IssueStatus.BACKLOG.value, carried_over=True)

    # 4. Completion rate
    completion_rate = round((done_count / total_count) * 100, 2) if total_count > 0 else 0.0

    # 5. Retrospective
    retrospective = build_sprint_retrospective(slug, sprint_id)

    # 6. Update sprint record
    now = datetime.now().isoformat()
    updated = update_sprint(
        slug, sprint_id,
        status=SprintStatus.CLOSED.value,
        velocity=velocity,
        carry_over_count=len(unfinished),
        completion_rate=completion_rate,
        retrospective=retrospective,
        closed_at=now,
    )

    logger.debug(
        "[PRODUCT] Sprint {} closed — velocity={}, completion={}%, carry_over={}",
        sprint_id, velocity, completion_rate, len(unfinished),
    )
    return updated


def suggest_capacity(slug: str) -> int | None:
    """Suggest sprint capacity based on sliding average of last 3 closed sprints.

    Returns None if fewer than 3 closed sprints exist.
    """
    closed = list_sprints(slug, status=SprintStatus.CLOSED.value)
    if len(closed) < 3:
        return None
    # Take last 3 by closed_at
    recent = sorted(closed, key=lambda s: s.get("closed_at") or "")[-3:]
    velocities = [s.get("velocity") or 0 for s in recent]
    return round(sum(velocities) / len(velocities))


def build_sprint_retrospective(slug: str, sprint_id: str) -> str:
    """Generate a sprint retrospective report string."""
    sprint = load_sprint(slug, sprint_id)
    if not sprint:
        return ""

    issues = list_issues(slug, sprint=sprint_id)
    done = [i for i in issues if i.get("status") in _DONE_STATUSES]
    unfinished = [i for i in issues if i.get("status") not in _DONE_STATUSES]
    velocity = sum(i.get("story_points") or 0 for i in done)
    total_points = sum(i.get("story_points") or 0 for i in issues)
    total_count = len(issues)
    done_count = len(done)

    # Compare with previous sprint velocity
    closed = list_sprints(slug, status=SprintStatus.CLOSED.value)
    closed_sorted = sorted(closed, key=lambda s: s.get("closed_at") or "")
    prev_velocity = None
    for cs in closed_sorted:
        if cs["id"] != sprint_id and cs.get("velocity") is not None:
            prev_velocity = cs["velocity"]

    lines = [
        f"## Sprint Retrospective: {sprint['name']}",
        f"**Goal**: {sprint.get('goal') or 'N/A'}",
        f"**Period**: {sprint.get('start_date')} → {sprint.get('end_date')}",
        "",
        f"### Metrics",
        f"- **Velocity**: {velocity} story points",
        f"- **Completion**: {done_count}/{total_count} issues ({round(done_count / total_count * 100, 1) if total_count else 0}%)",
        f"- **Story points completed**: {velocity}/{total_points}",
        f"- **Carry-over**: {len(unfinished)} issues",
    ]

    if prev_velocity is not None:
        delta = velocity - prev_velocity
        direction = "↑" if delta > 0 else ("↓" if delta < 0 else "→")
        lines.append(f"- **vs Previous Sprint**: {direction} {abs(delta)} points ({prev_velocity} → {velocity})")

    if done:
        lines.append("")
        lines.append("### Completed")
        for i in done:
            pts = f" ({i.get('story_points') or 0}pts)" if i.get("story_points") else ""
            lines.append(f"- ✓ {i['title']}{pts}")

    if unfinished:
        lines.append("")
        lines.append("### Carried Over")
        for i in unfinished:
            pts = f" ({i.get('story_points') or 0}pts)" if i.get("story_points") else ""
            lines.append(f"- ○ {i['title']}{pts}")

    return "\n".join(lines)
