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
from onemancompany.core.system_cron import system_cron

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

    # Gate: skip auto-project during planning phase
    product = prod.load_product(slug)
    if product and product.get("status") == "planning":
        logger.debug("[PRODUCT_TRIGGER] Product '{}' is in planning — skipping auto-project", slug)
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

    # After version release, schedule a product review so owner can plan next steps
    await dispatch_product_review(slug)


def sync_issue_statuses(product_slug: str) -> list[dict]:
    """Sync all issue statuses by deriving from linked TaskNode states.

    Delegates to prod.sync_issue_statuses() which derives status from
    linked project/task states.

    Returns list of dicts with issue_id, old, and new status.
    """
    return prod.sync_issue_statuses(product_slug)


async def check_kr_progress(product_slug: str) -> list[dict]:
    """Check KR progress and create P2 issues for any lagging behind (<50%).

    Returns list of newly created issue dicts.
    """
    product = prod.load_product(product_slug)
    if not product:
        logger.warning("[PRODUCT_TRIGGER] check_kr_progress: product '{}' not found", product_slug)
        return []

    created_issues: list[dict] = []
    # Check all non-terminal issues for dedup (KR tracking issues could be in any active status)
    all_issues = prod.list_issues(product_slug)
    existing_issues = [i for i in all_issues if i.get("status") not in (IssueStatus.DONE.value, IssueStatus.RELEASED.value)]

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


async def dispatch_product_review(product_slug: str) -> str | None:
    """Dispatch a product review task to the product owner.

    The owner reviews OKR progress, issue backlog, active projects,
    and takes autonomous action to advance the product.

    Returns the project_id if a task was dispatched, None otherwise.
    """
    product = prod.load_product(product_slug)
    if not product:
        logger.warning("[PRODUCT_TRIGGER] dispatch_product_review: product '{}' not found", product_slug)
        return None

    if product.get("status") != "active":
        logger.debug("[PRODUCT_TRIGGER] Product '{}' not active, skip review", product_slug)
        return None

    owner_id = product.get("owner_id", "")
    if not owner_id:
        logger.warning("[PRODUCT_TRIGGER] Product '{}' has no owner", product_slug)
        return None

    # Check if owner already has too many active projects for this product (avoid stacking)
    from onemancompany.core.project_archive import list_projects
    all_projects = list_projects()
    existing = [p for p in all_projects
                if p.get("product_id") == product["id"]
                and p.get("status") == "active"]
    if len(existing) >= 3:
        logger.debug("[PRODUCT_TRIGGER] Product '{}' already has {} active projects, skip review dispatch",
                     product_slug, len(existing))
        return None

    # Build the review task description
    context = prod.build_product_context(product_slug)

    # Gather current project status (reuse cached list)
    linked_projects = [p for p in all_projects if p.get("product_id") == product["id"]]
    active_projects = [p for p in linked_projects if p.get("status") == "active"]
    completed_projects = [p for p in linked_projects if p.get("status") == "archived"]

    # Gather issue stats
    all_issues = prod.list_issues(product_slug)
    backlog = [i for i in all_issues if i.get("status") == IssueStatus.BACKLOG.value]
    in_progress = [i for i in all_issues if i.get("status") == IssueStatus.IN_PROGRESS.value]
    done = [i for i in all_issues if i.get("status") == IssueStatus.DONE.value]

    task_description = f"""## Product Review — {product['name']}

You are the owner of this product. Review its current state and take action to advance it toward its objectives.

{context}

### Current Status
- Active projects: {len(active_projects)}
- Completed projects: {len(completed_projects)}
- Issues: {len(backlog)} backlog, {len(in_progress)} in progress, {len(done)} done

### Review Checklist (follow in order)

**Step 1: Check existing Issues**
- Are there open issues? Who is assigned? Are they being worked on (linked projects active)?
- If an issue has no assignee or no active project → assign someone and dispatch work.

**Step 2: Check KR progress**
- For each KR: is progress on track? Is anyone currently working toward this KR?
- If a KR has no associated issues → create issues to advance it.
- If a KR has issues but nobody is working on them → assign and dispatch.

**Step 3: Fill gaps**
- Are there KRs with no path to completion? Create the missing issues.
- Are there stalled projects? Check what's blocking and take action.

**Step 4: Dispatch work**
- For unassigned high-priority issues, find the right employee (use `list_colleagues`) and assign them.
- Use `create_product_issue` to create new issues when needed.
- Use `update_product_issue` to assign and reprioritize.
- Use `update_kr_progress_tool` to update KR metrics based on completed work.

### Key Principles
- **Skip if already handled** — If an issue already has an assignee AND an active project, do nothing for that issue. Avoid duplicate work.
- **Only act on gaps** — Only create issues or dispatch work for things nobody is currently handling.
- **Take action, don't just report** — If something needs doing and nobody is on it, assign it. You are the product owner — own it.
- **Be concise** — Brief summary of what you checked and what action you took (or why no action needed).
"""

    # Dispatch directly to product owner (not CEO/EA pipeline)
    from onemancompany.core.project_archive import async_create_project_from_task
    try:
        project_id, _iter_id = await async_create_project_from_task(
            task_description,
            routed_to=owner_id,
            product_id=product["id"],
        )
        logger.info("[PRODUCT_TRIGGER] Dispatched product review for '{}' → project {}",
                    product_slug, project_id)
        return project_id
    except Exception:
        logger.exception("[PRODUCT_TRIGGER] Failed to dispatch product review for '{}'", product_slug)
        return None


@system_cron("product_health_check", interval="10m", description="Periodic product review + health check")
async def product_health_check() -> list | None:
    """Check all products for stale issues, lagging KRs, and dispatch owner reviews."""
    products = prod.list_products()
    events = []
    for p in products:
        slug = p.get("slug", "")
        if not slug:
            continue
        # Sync issue statuses from linked TaskNode states
        status_changes = sync_issue_statuses(slug)
        kr_issues = await check_kr_progress(slug)
        # Dispatch product review to owner (only for active products)
        review_project = await dispatch_product_review(slug)
        if status_changes or kr_issues or review_project:
            events.append(CompanyEvent(
                type=EventType.ACTIVITY,
                payload={"message": f"Product '{p['name']}': {len(status_changes)} status changes, {len(kr_issues)} KR alerts" + (f", review dispatched" if review_project else "")},
            ))
    return events if events else None


async def handle_issue_assigned(event: CompanyEvent) -> None:
    """When an issue is (re)assigned, create a project so the assignee starts working."""
    slug = event.payload.get("product_slug", "")
    issue_id = event.payload.get("issue_id", "")
    assignee_id = event.payload.get("assignee_id", "")

    issue = prod.load_issue(slug, issue_id)
    if not issue:
        logger.warning("[PRODUCT_TRIGGER] handle_issue_assigned: issue {} not found", issue_id)
        return

    # Gate: skip auto-project during planning phase
    product = prod.load_product(slug)
    if product and product.get("status") == "planning":
        logger.debug("[PRODUCT_TRIGGER] Product '{}' is in planning — skipping auto-project on assign", slug)
        return

    # Only act on open/in_progress issues
    if issue.get("status") == IssueStatus.DONE.value:
        logger.debug("[PRODUCT_TRIGGER] Skipping assignment for closed issue {}", issue_id)
        return

    # Check if a project already exists for this issue (avoid duplicates)
    linked = issue.get("linked_task_ids", [])
    if linked:
        logger.debug("[PRODUCT_TRIGGER] Issue {} already has linked tasks {}, skip", issue_id, linked)
        return

    logger.info("[PRODUCT_TRIGGER] Issue {} assigned to {} — creating project", issue_id, assignee_id)
    project_id = await _create_project_for_issue(slug, issue)

    if project_id:
        prod.update_issue(
            slug, issue_id,
            status=IssueStatus.IN_PROGRESS.value,
            linked_task_ids=[project_id],
        )


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
                elif event.type == EventType.ISSUE_ASSIGNED:
                    await handle_issue_assigned(event)
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
