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
    """Create a project from an issue AND schedule EA to execute it.

    Full flow: create project → create TaskTree → schedule EA node.
    Same as CEO task submission in routes.py, but triggered by product system.
    Returns the project_id or empty string.
    """
    from pathlib import Path
    from onemancompany.core.config import CEO_ID, EA_ID, TASK_TREE_FILENAME
    from onemancompany.core.project_archive import async_create_project_from_task, get_project_dir
    from onemancompany.core.task_lifecycle import NodeType, TaskPhase

    product = prod.load_product(slug)
    product_id = product["id"] if product else ""
    task_description = f"[{issue.get('priority', '')}] {issue['title']}: {issue.get('description', '')}"

    try:
        project_id, iter_id = await async_create_project_from_task(
            task_description,
            product_id=product_id,
        )
        pdir = get_project_dir(project_id)
        ctx_id = f"{project_id}/{iter_id}" if iter_id else project_id

        # Build EA task with product context
        product_ctx = prod.build_product_context(slug)
        ea_task = (
            f"A product issue needs to be resolved. Analyze and dispatch to the appropriate employee:\n\n"
            f"Issue: {task_description}\n\n"
            f"{product_ctx}\n\n"
            f"[Project ID: {ctx_id}] [Project workspace: {pdir}]"
        )

        # Create TaskTree with CEO root + EA child (same as ceo_submit_task)
        from onemancompany.core.task_tree import TaskTree
        from onemancompany.core.vessel import _save_project_tree

        tree = TaskTree(project_id=ctx_id, mode="standard")
        ceo_root = tree.create_root(employee_id=CEO_ID, description=task_description)
        ceo_root.node_type = NodeType.CEO_PROMPT.value
        ceo_root.set_status(TaskPhase.PROCESSING)
        ea_node = tree.add_child(
            parent_id=ceo_root.id,
            employee_id=EA_ID,
            description=ea_task,
            acceptance_criteria=[],
        )
        _save_project_tree(pdir, tree)

        # Schedule EA to execute
        from onemancompany.core.agent_loop import employee_manager
        tree_path = str(Path(pdir) / TASK_TREE_FILENAME)
        employee_manager.schedule_node(EA_ID, ea_node.id, tree_path)
        employee_manager._schedule_next(EA_ID)

        logger.info(
            "[PRODUCT_TRIGGER] Created project {} with TaskTree for issue {} → EA scheduled",
            project_id, issue["id"],
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

    # Skip version release if no issues were resolved
    if not resolved_issue_ids:
        logger.debug("[PRODUCT_TRIGGER] No resolved issues for project {}, skip version release", project_id)
        await run_product_check(slug)
        return

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

    # After version release, run product check to detect next gaps
    await run_product_check(slug)


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


async def run_product_check(product_slug: str) -> dict:
    """Code-level product health check. No LLM calls — pure logic.

    Checks for gaps and only dispatches work when needed:
    1. Unassigned high-priority issues → auto-create project for them
    2. KRs with no issues → auto-create issues
    3. Issues with assignee but no active project → create project
    All actions are logged. Returns summary dict.
    """
    product = prod.load_product(product_slug)
    if not product:
        return {"skipped": True, "reason": "not found"}

    if product.get("status") != "active":
        return {"skipped": True, "reason": f"status={product.get('status')}"}

    owner_id = product.get("owner_id", "")
    if not owner_id:
        return {"skipped": True, "reason": "no owner"}

    from onemancompany.core.project_archive import list_projects
    all_projects = list_projects()
    active_for_product = [
        p for p in all_projects
        if p.get("product_id") == product["id"] and p.get("status") == "active"
    ]

    all_issues = prod.list_issues(product_slug)
    actions_taken: list[str] = []

    # --- Step 1: Unassigned backlog/planned issues with P0/P1 → create project ---
    for issue in all_issues:
        status = issue.get("status", "")
        if status in (IssueStatus.DONE.value, IssueStatus.RELEASED.value):
            continue
        linked = issue.get("linked_task_ids", [])
        has_active_project = any(
            pid in [p.get("project_id") for p in active_for_product]
            for pid in linked
        )
        if has_active_project:
            continue  # someone is already working on it

        priority = issue.get("priority", "")

        # High priority + no active project → create project
        if priority in _AUTO_PROJECT_PRIORITIES and not linked:
            if len(active_for_product) >= 3:
                logger.debug("[PRODUCT_CHECK] Skipping project for issue {} — 3+ active projects", issue["id"])
                continue
            project_id = await _create_project_for_issue(product_slug, issue)
            if project_id:
                prod.update_issue(
                    product_slug, issue["id"],
                    status=IssueStatus.IN_PROGRESS.value,
                    linked_task_ids=list(linked) + [project_id],
                )
                active_for_product.append({"project_id": project_id, "status": "active"})
                actions_taken.append(f"Created project for P0/P1 issue: {issue['title']}")

        # Has assignee but no project → create project
        elif issue.get("assignee_id") and not linked:
            if len(active_for_product) >= 3:
                continue
            project_id = await _create_project_for_issue(product_slug, issue)
            if project_id:
                prod.update_issue(
                    product_slug, issue["id"],
                    status=IssueStatus.IN_PROGRESS.value,
                    linked_task_ids=[project_id],
                )
                active_for_product.append({"project_id": project_id, "status": "active"})
                actions_taken.append(f"Created project for assigned issue: {issue['title']}")

    # --- Step 2: KRs with no issues → auto-create issues ---
    krs = product.get("key_results", [])
    for kr in krs:
        target = kr.get("target", 0)
        current = kr.get("current", 0)
        if target <= 0 or current >= target:
            continue  # met or invalid

        kr_title = kr.get("title", "")
        # Check if any open issue is related to this KR (by title match)
        has_issue = any(
            kr_title in i.get("title", "") or kr.get("id", "") in i.get("title", "")
            for i in all_issues
            if i.get("status") not in (IssueStatus.DONE.value, IssueStatus.RELEASED.value)
        )
        if not has_issue:
            progress_pct = (current / target * 100) if target else 0
            issue = prod.create_issue(
                slug=product_slug,
                title=f"Advance KR: {kr_title} (currently {progress_pct:.0f}%)",
                description=f"Key result '{kr_title}' is at {current}/{target}. Create and execute work to advance this metric.",
                priority=IssuePriority.P2,
                created_by="system",
                labels=["kr-tracking", "auto-created"],
            )
            actions_taken.append(f"Created issue for KR: {kr_title}")
            all_issues.append(issue)  # prevent duplicate creation in same cycle

    if actions_taken:
        logger.info("[PRODUCT_CHECK] Product '{}': {}", product_slug, "; ".join(actions_taken))
    else:
        logger.debug("[PRODUCT_CHECK] Product '{}': no action needed", product_slug)

    return {
        "skipped": False,
        "actions": actions_taken,
        "active_projects": len(active_for_product),
        "total_issues": len(all_issues),
    }


@system_cron("product_health_check", interval="10m", description="Periodic product status sync + gap detection")
async def product_health_check() -> list | None:
    """Lightweight code-level product check. No LLM calls.

    For each active product:
    1. Sync issue statuses from TaskNode states
    2. Detect gaps (unassigned issues, missing KR issues) and auto-dispatch
    """
    products = prod.list_products()
    events = []
    for p in products:
        slug = p.get("slug", "")
        if not slug:
            continue
        # Sync issue statuses from linked TaskNode states
        status_changes = sync_issue_statuses(slug)
        # Code-level gap detection and auto-dispatch
        check_result = await run_product_check(slug)
        actions = check_result.get("actions", [])
        if status_changes or actions:
            msg_parts = []
            if status_changes:
                msg_parts.append(f"{len(status_changes)} status changes")
            if actions:
                msg_parts.append(f"{len(actions)} actions: {'; '.join(actions)}")
            events.append(CompanyEvent(
                type=EventType.ACTIVITY,
                payload={"message": f"Product '{p['name']}': {', '.join(msg_parts)}"},
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
