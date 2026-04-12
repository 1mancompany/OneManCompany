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

def add_key_result(slug: str, *, title: str, target: float) -> dict:
    """Add a key result to a product. Returns the KR dict."""
    kr_id = _gen_id("kr_")
    kr = {
        "id": kr_id,
        "title": title,
        "target": target,
        "current": 0.0,
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


def update_kr_progress(slug: str, kr_id: str, *, current: float) -> dict | None:
    """Update a key result's current progress. Returns updated KR or None."""
    with _get_slug_lock(slug):
        path = _product_yaml_path(slug)
        data = _read_yaml(path)
        if not data:
            logger.warning("update_kr_progress: product slug={} not found", slug)
            return None
        for kr in data.get("key_results", []):
            if kr["id"] == kr_id:
                kr["current"] = current
                data["updated_at"] = datetime.now().isoformat()
                _write_yaml(path, data)
                mark_dirty(DirtyCategory.PRODUCTS)
                return kr

    logger.warning("update_kr_progress: kr_id={} not found in {}", kr_id, slug)
    return None


# ---------------------------------------------------------------------------
# Issue CRUD
# ---------------------------------------------------------------------------

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
        "status": IssueStatus.OPEN,
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
    path = _issues_dir(slug) / f"{issue_id}.yaml"
    data = _read_yaml(path)
    if not data:
        logger.warning("update_issue: issue {} not found in {}", issue_id, slug)
        return None
    for key, value in fields.items():
        if value is not None:
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
    path = _issues_dir(slug) / f"{issue_id}.yaml"
    data = _read_yaml(path)
    if not data:
        logger.warning("close_issue: issue {} not found in {}", issue_id, slug)
        return None
    data["status"] = IssueStatus.CLOSED.value
    data["resolution"] = resolution.value
    data["closed_at"] = datetime.now().isoformat()
    _write_yaml(path, data)
    mark_dirty(DirtyCategory.PRODUCTS)
    logger.debug("Closed issue {} with resolution {}", issue_id, resolution.value)
    return data


def reopen_issue(slug: str, issue_id: str) -> dict | None:
    """Reopen a closed issue. Increments reopened_count. Returns updated dict or None."""
    path = _issues_dir(slug) / f"{issue_id}.yaml"
    data = _read_yaml(path)
    if not data:
        logger.warning("reopen_issue: issue {} not found in {}", issue_id, slug)
        return None
    data["status"] = IssueStatus.OPEN.value
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

    mark_dirty(DirtyCategory.PRODUCTS)
    logger.info("[VERSION] Released {} for product '{}'", new_version, product_slug)
    return version_record
