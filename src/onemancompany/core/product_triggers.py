"""Product triggers — event-driven automation for the product management module.

Subscribes to the company event bus and reacts to:
- ISSUE_CREATED  → auto-create a project for P0/P1 issues
- AGENT_DONE     → close linked issues + release a new version
- Periodic       → check KR progress, create issues for lagging KRs
"""
from __future__ import annotations

from loguru import logger

from onemancompany.core.events import CompanyEvent, event_bus
from onemancompany.core.models import (
    EventType,
    IssuePriority,
    IssueResolution,
    IssueStatus,
)
from onemancompany.core import product as prod

# Priorities that auto-trigger project creation
_AUTO_PROJECT_PRIORITIES = {IssuePriority.P0.value, IssuePriority.P1.value}


# ---------------------------------------------------------------------------
# Trigger handlers
# ---------------------------------------------------------------------------


async def handle_issue_created(event: CompanyEvent) -> None:
    """When a P0/P1 issue is created, auto-create a project to address it."""
    slug = event.payload.get("product_slug", "")
    issue_id = event.payload.get("issue_id", "")

    issue = prod.load_issue(slug, issue_id)
    if not issue:
        logger.warning("[PRODUCT_TRIGGER] issue {} not found in {}", issue_id, slug)
        return

    priority = issue.get("priority", "")
    # Normalise — could be an enum value or a raw string
    if hasattr(priority, "value"):
        priority = priority.value

    if priority not in _AUTO_PROJECT_PRIORITIES:
        logger.debug(
            "[PRODUCT_TRIGGER] Skipping project creation for {} issue {}",
            priority,
            issue_id,
        )
        return

    logger.info(
        "[PRODUCT_TRIGGER] {} issue {} — creating project", priority, issue_id
    )
    project_id = await _create_project_for_issue(slug, issue)

    # Link the project back to the issue
    if project_id:
        linked = list(issue.get("linked_task_ids", []))
        linked.append(project_id)
        prod.update_issue(slug, issue_id, status=IssueStatus.IN_PROGRESS.value, linked_task_ids=linked)


async def _create_project_for_issue(slug: str, issue: dict) -> str:
    """Create a project from an issue. Returns the project_id or empty string."""
    from onemancompany.core.project_archive import async_create_project_from_task

    product = prod.load_product(slug)
    product_id = product["id"] if product else ""
    task_description = f"[{issue.get('priority', '')}] {issue['title']}: {issue.get('description', '')}"

    try:
        project_id, _iter_id = await async_create_project_from_task(
            task_description,
            product_id=product_id,
        )
        logger.info(
            "[PRODUCT_TRIGGER] Created project {} for issue {}",
            project_id,
            issue["id"],
        )
        return project_id
    except Exception:
        logger.exception(
            "[PRODUCT_TRIGGER] Failed to create project for issue {}",
            issue["id"],
        )
        return ""


async def handle_project_complete(event: CompanyEvent) -> None:
    """When a project with product context completes, close issues + release version."""
    slug = event.payload.get("product_slug", "")
    project_id = event.payload.get("project_id", "")
    resolved_issue_ids: list[str] = event.payload.get("resolved_issue_ids", [])

    if not slug:
        logger.debug("[PRODUCT_TRIGGER] handle_project_complete: no product_slug, skip")
        return

    # Close all resolved issues
    for issue_id in resolved_issue_ids:
        prod.close_issue(slug, issue_id, resolution=IssueResolution.FIXED)
        logger.info("[PRODUCT_TRIGGER] Closed issue {} as fixed", issue_id)

    # Release a new version
    version_record = prod.release_version(
        slug,
        resolved_issue_ids,
        project_ids=[project_id] if project_id else None,
    )
    logger.info(
        "[PRODUCT_TRIGGER] Released version {} for product '{}'",
        version_record["version"],
        slug,
    )

    # Publish VERSION_RELEASED event
    await event_bus.publish(
        CompanyEvent(
            type=EventType.VERSION_RELEASED,
            payload={
                "product_slug": slug,
                "version": version_record["version"],
                "changelog": version_record["changelog"],
                "resolved_issue_ids": resolved_issue_ids,
            },
        )
    )


async def check_kr_progress(product_slug: str) -> list[dict]:
    """Check KR progress and create P2 issues for any lagging behind (<50%).

    Returns list of newly created issue dicts.
    """
    product = prod.load_product(product_slug)
    if not product:
        logger.warning("[PRODUCT_TRIGGER] check_kr_progress: product '{}' not found", product_slug)
        return []

    created_issues: list[dict] = []
    existing_issues = prod.list_issues(product_slug, status=IssueStatus.OPEN)

    for kr in product.get("key_results", []):
        target = kr.get("target", 0)
        current = kr.get("current", 0)
        if target <= 0:
            continue
        progress_pct = current / target * 100
        if progress_pct >= 50:
            continue

        # Check if an open issue already exists for this KR
        kr_title = kr.get("title", "")
        already_tracked = any(
            kr_title in iss.get("title", "") for iss in existing_issues
        )
        if already_tracked:
            logger.debug(
                "[PRODUCT_TRIGGER] KR '{}' already has an open issue, skip",
                kr_title,
            )
            continue

        issue = prod.create_issue(
            slug=product_slug,
            title=f"KR behind target: {kr_title} ({progress_pct:.0f}%)",
            description=(
                f"Key result '{kr_title}' is at {current}/{target} ({progress_pct:.0f}%). "
                f"Target progress threshold: 50%."
            ),
            priority=IssuePriority.P2,
            created_by="system",
            labels=["kr-tracking", "auto-created"],
        )
        created_issues.append(issue)
        logger.info(
            "[PRODUCT_TRIGGER] Created P2 issue for lagging KR '{}' ({}%)",
            kr_title,
            f"{progress_pct:.0f}",
        )

    return created_issues


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_product_triggers() -> "asyncio.Task":
    """Subscribe product trigger handlers to the event bus.

    This is a convenience registration that dispatches events from a
    single subscriber queue to the appropriate handler based on EventType.

    Returns the asyncio.Task so the caller can cancel it on shutdown.
    """
    import asyncio

    queue = event_bus.subscribe()

    async def _dispatch_loop() -> None:
        while True:
            event = await queue.get()
            try:
                if event.type == EventType.ISSUE_CREATED:
                    await handle_issue_created(event)
                elif event.type == EventType.AGENT_DONE:
                    # Only handle if it has product context
                    if event.payload.get("product_slug"):
                        await handle_project_complete(event)
            except Exception:
                logger.exception(
                    "[PRODUCT_TRIGGER] Error handling event {}", event.type
                )

    task = asyncio.ensure_future(_dispatch_loop())
    logger.info("[PRODUCT_TRIGGER] Product triggers registered")
    return task
