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
    IssueRelation,
    IssuePriority,
    IssueResolution,
    IssueStatus,
)
from onemancompany.core import product as prod
from onemancompany.core.system_cron import system_cron

# Priorities that auto-trigger project creation
_AUTO_PROJECT_PRIORITIES = {IssuePriority.P0.value, IssuePriority.P1.value}

# ---------------------------------------------------------------------------
# Configurable thresholds (B4 audit: extracted from inline magic numbers)
# ---------------------------------------------------------------------------

KR_LAGGING_THRESHOLD: int = 50          # KR progress % below which it's "lagging"
MAX_ACTIVE_PROJECTS: int = 3            # Max concurrent active projects per product
BACKLOG_GROOMING_THRESHOLD: int = 5     # P2/P3 unscheduled issues before grooming nudge
STALE_REVIEW_HOURS: int = 24            # Hours before an open review is considered stale
BLOCKED_DAYS_THRESHOLD: int = 7         # Days before a blocked issue is flagged
UNHANDLED_BACKLOG_THRESHOLD: int = 2    # Unhandled backlog issues before alert

def _get_threshold(product: dict, key: str, default: int) -> int:
    """Read per-product config threshold, falling back to module-level default."""
    config = product.get("config") or {}
    return config.get(key, default)


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


async def _create_review_project(product_slug: str, reason: str) -> str:
    """Create a standalone review project for the product owner.

    Unlike _create_project_for_issue, this doesn't take an issue dict —
    it constructs a proper review-scoped project.
    Returns project_id or empty string.
    """
    from pathlib import Path
    from onemancompany.core.config import CEO_ID, EA_ID, TASK_TREE_FILENAME
    from onemancompany.core.project_archive import async_create_project_from_task, get_project_dir
    from onemancompany.core.task_lifecycle import NodeType, TaskPhase

    product = prod.load_product(product_slug)
    if not product:
        return ""
    product_id = product["id"]
    owner_id = product.get("owner_id", "")
    task_description = f"Product review for '{product['name']}': {reason}"

    try:
        project_id, iter_id = await async_create_project_from_task(
            task_description,
            product_id=product_id,
        )
        pdir = get_project_dir(project_id)
        ctx_id = f"{project_id}/{iter_id}" if iter_id else project_id

        product_ctx = prod.build_product_context(product_slug)
        review_task = (
            f"Product review needed: {reason}\n\n"
            f"{product_ctx}\n\n"
            f"[Project ID: {ctx_id}] [Project workspace: {pdir}]"
        )

        from onemancompany.core.task_tree import TaskTree
        from onemancompany.core.vessel import _save_project_tree

        tree = TaskTree(project_id=ctx_id, mode="standard")
        ceo_root = tree.create_root(employee_id=CEO_ID, description=task_description)
        ceo_root.node_type = NodeType.CEO_PROMPT.value
        ceo_root.set_status(TaskPhase.PROCESSING)

        owner_node = tree.add_child(
            parent_id=ceo_root.id,
            employee_id=owner_id or EA_ID,
            description=review_task,
            acceptance_criteria=[],
            title=f"Product review: {reason[:50]}",
        )
        _save_project_tree(pdir, tree)

        from onemancompany.core.agent_loop import employee_manager
        target_id = owner_id or EA_ID
        tree_path = str(Path(pdir) / TASK_TREE_FILENAME)
        employee_manager.schedule_node(target_id, owner_node.id, tree_path)
        employee_manager._schedule_next(target_id)

        logger.info(
            "[PRODUCT_TRIGGER] Created review project {} for product '{}' (reason: {})",
            project_id, product_slug, reason,
        )
        return project_id
    except Exception:
        logger.exception(
            "[PRODUCT_TRIGGER] Failed to create review project for '{}'",
            product_slug,
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
        await notify_owner(slug, reason=f"Project {project_id} completed, please review and update KR progress")
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

    # After version release, run product check + notify owner
    await run_product_check(slug)
    await notify_owner(slug, reason=f"Project completed, version {version_record['version']} released")


async def notify_owner(product_slug: str, reason: str = "") -> bool:
    """Push a review task to the product owner on an existing product project.

    If the product has an active project, adds a child task to its tree.
    If no active project exists, creates one.
    Skips if owner already has a pending review task (no duplicates).

    Returns True if task was pushed, False if skipped.
    """
    product = prod.load_product(product_slug)
    if not product or product.get("status") != "active":
        return False

    owner_id = product.get("owner_id", "")
    if not owner_id:
        return False

    # Build task description
    context = prod.build_product_context(product_slug)
    all_issues = prod.list_issues(product_slug)
    backlog = [i for i in all_issues if i.get("status") == IssueStatus.BACKLOG.value]
    in_progress = [i for i in all_issues if i.get("status") == IssueStatus.IN_PROGRESS.value]
    done = [i for i in all_issues if i.get("status") == IssueStatus.DONE.value]

    task_desc = (
        f"Product review needed: {reason}\n\n"
        f"{context}\n\n"
        f"Status: {len(backlog)} backlog, {len(in_progress)} in progress, {len(done)} done\n\n"
        f"Follow the product-review skill checklist strictly:\n"
        f"1. Update KR progress using update_kr_progress_tool\n"
        f"2. Review and close/reprioritize issues\n"
        f"3. Assign unhandled backlog to the right people\n"
        f"4. Create issues for gaps — do NOT create projects directly\n\n"
        f"[skill: product-review]"
    )

    from pathlib import Path
    from onemancompany.core.config import CEO_ID, TASK_TREE_FILENAME
    from onemancompany.core.project_archive import list_projects, get_project_dir
    from onemancompany.core.task_tree import get_tree
    from onemancompany.core.vessel import _save_project_tree

    # Find existing active project for this product
    all_projects = list_projects()
    active_product_projects = [
        p for p in all_projects
        if p.get("product_id") == product["id"] and p.get("status") == "active"
    ]

    if active_product_projects:
        # Add task to existing project's tree
        proj = active_product_projects[0]
        pdir = get_project_dir(proj["project_id"])
        tree_path = Path(pdir) / TASK_TREE_FILENAME
        if not tree_path.exists():
            logger.debug("[PRODUCT_TRIGGER] Tree not found for project {}", proj["project_id"])
            return False

        tree = get_tree(str(tree_path))

        # Check if owner already has a pending/processing review task — skip if so
        from onemancompany.core.task_lifecycle import TaskPhase
        for node in tree.all_nodes():
            if (node.employee_id == owner_id
                    and node.status in (TaskPhase.PENDING.value, TaskPhase.PROCESSING.value)
                    and "review" in (node.title or node.description or "").lower()):
                logger.debug("[PRODUCT_TRIGGER] Owner {} already has pending review task {}, skip",
                             owner_id, node.id)
                return False

        # Find a suitable parent (EA node or root)
        ea_node = tree.get_ea_node()
        parent_id = ea_node.id if ea_node else tree.root_id

        child = tree.add_child(
            parent_id=parent_id,
            employee_id=owner_id,
            description=task_desc,
            acceptance_criteria=[],
            title=f"Product review: {reason[:50]}",
        )
        _save_project_tree(pdir, tree)

        # Schedule owner to execute
        from onemancompany.core.agent_loop import employee_manager
        employee_manager.schedule_node(owner_id, child.id, str(tree_path))
        employee_manager._schedule_next(owner_id)

        logger.info("[PRODUCT_TRIGGER] Pushed review task to owner {} on project {} (reason: {})",
                    owner_id, proj["project_id"], reason)
    else:
        # No active project — create a dedicated review project
        project_id = await _create_review_project(product_slug, reason)
        if not project_id:
            return False
        logger.info("[PRODUCT_TRIGGER] Created review project {} for owner {} (reason: {})",
                    project_id, owner_id, reason)

    return True


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
        if progress_pct >= KR_LAGGING_THRESHOLD:
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

    max_active = _get_threshold(product, "max_active_projects", MAX_ACTIVE_PROJECTS)

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
            if len(active_for_product) >= max_active:
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
            if len(active_for_product) >= max_active:
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

        kr_id = kr.get("id", "")
        kr_title = kr.get("title", "")
        kr_label = f"kr:{kr_id}"
        # Check if any open issue is already tracking this KR (by kr_id label)
        has_issue = any(
            kr_label in i.get("labels", [])
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
                labels=["kr-tracking", "auto-created", kr_label],
            )
            actions_taken.append(f"Created issue for KR: {kr_title}")
            all_issues.append(issue)  # prevent duplicate creation in same cycle

    # --- Step 3: Sprint expiry check ---
    from datetime import date as _date

    active_sprint = prod.get_active_sprint(product_slug)
    if active_sprint:
        end_date_str = active_sprint.get("end_date", "")
        try:
            end_date = _date.fromisoformat(end_date_str)
            if _date.today() > end_date:
                actions_taken.append(f"Sprint '{active_sprint['name']}' expired on {end_date_str}")
        except (ValueError, TypeError):
            logger.debug("[PRODUCT_CHECK] Invalid end_date '{}' on sprint {}", end_date_str, active_sprint.get("id"))

    # --- Step 4: Backlog grooming reminder ---
    unscheduled_low = [
        i for i in all_issues
        if i.get("priority") in (IssuePriority.P2.value, IssuePriority.P3.value)
        and not i.get("sprint")
        and i.get("status") not in (IssueStatus.DONE.value, IssueStatus.RELEASED.value)
    ]
    if len(unscheduled_low) >= BACKLOG_GROOMING_THRESHOLD:
        actions_taken.append(f"{len(unscheduled_low)} P2/P3 issues unscheduled — backlog grooming needed")

    # --- Step 5: Stale review check ---
    from datetime import datetime as _datetime, timedelta as _timedelta

    open_reviews = prod.list_reviews(product_slug, status="open")
    stale_reviews = []
    for rev in open_reviews:
        try:
            created = _datetime.fromisoformat(rev.get("created_at", ""))
            if _datetime.now() - created > _timedelta(hours=STALE_REVIEW_HOURS):
                stale_reviews.append(rev)
        except (ValueError, TypeError):
            logger.debug("[PRODUCT_CHECK] Invalid created_at on review {}", rev.get("id"))
    if stale_reviews:
        actions_taken.append(f"{len(stale_reviews)} stale review(s) open > {STALE_REVIEW_HOURS}h")

    # --- Step 6: Blocked issue check ---
    for issue in all_issues:
        if issue.get("status") in (IssueStatus.DONE.value, IssueStatus.RELEASED.value):
            continue
        links = issue.get("issue_links", [])
        blocked_links = [
            link for link in links
            if link["relation"] == IssueRelation.BLOCKED_BY.value
            and _is_blocker_unresolved(product_slug, link["issue_id"])
        ]
        if not blocked_links:
            continue
        # Use the oldest blocked_by link's created_at to determine how long blocked
        oldest_blocked_at = None
        for link in blocked_links:
            try:
                link_created = _datetime.fromisoformat(link.get("created_at", ""))
                if oldest_blocked_at is None or link_created < oldest_blocked_at:
                    oldest_blocked_at = link_created
            except (ValueError, TypeError):
                logger.debug("[PRODUCT_CHECK] Invalid created_at on link in issue {}", issue.get("id"))
        if oldest_blocked_at and _datetime.now() - oldest_blocked_at > _timedelta(days=BLOCKED_DAYS_THRESHOLD):
            actions_taken.append(
                f"Issue '{issue['title']}' blocked for >{BLOCKED_DAYS_THRESHOLD} days"
            )

    # --- Step 7: Check if owner review is needed ---
    # Conditions: backlog issues with no one working, or KRs at 0% with completed projects
    needs_review = False
    review_reasons = []

    unhandled_backlog = [
        i for i in all_issues
        if i.get("status") == IssueStatus.BACKLOG.value and not i.get("linked_task_ids")
    ]
    if len(unhandled_backlog) > UNHANDLED_BACKLOG_THRESHOLD:
        needs_review = True
        review_reasons.append(f"{len(unhandled_backlog)} unhandled backlog issues")

    stale_krs = [
        kr for kr in krs
        if kr.get("target", 0) > 0 and kr.get("current", 0) == 0
    ]
    completed_projects = [p for p in all_projects if p.get("product_id") == product["id"] and p.get("status") == "archived"]
    if stale_krs and completed_projects:
        needs_review = True
        review_reasons.append(f"{len(stale_krs)} KRs at 0% despite {len(completed_projects)} completed projects")

    # Sprint expired → needs owner review
    if active_sprint:
        try:
            end_date = _date.fromisoformat(active_sprint.get("end_date", ""))
            if _date.today() > end_date:
                needs_review = True
                review_reasons.append(f"Sprint '{active_sprint['name']}' expired")
        except (ValueError, TypeError):
            logger.debug("[PRODUCT_CHECK] Invalid end_date on sprint {} for review check", active_sprint.get("id"))

    # Backlog grooming threshold → needs owner review
    if len(unscheduled_low) >= BACKLOG_GROOMING_THRESHOLD:
        needs_review = True
        review_reasons.append(f"{len(unscheduled_low)} P2/P3 issues need sprint assignment")

    # Stale reviews → needs owner review
    if stale_reviews:
        needs_review = True
        review_reasons.append(f"{len(stale_reviews)} stale review(s) pending")

    if needs_review:
        reason = "; ".join(review_reasons)
        notified = await notify_owner(product_slug, reason=reason)
        if notified:
            actions_taken.append(f"Owner notified: {reason}")

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

    # Re-read to guard against race with handle_issue_created
    fresh_issue = prod.load_issue(slug, issue_id)
    if fresh_issue and fresh_issue.get("linked_task_ids"):
        logger.debug("[PRODUCT_TRIGGER] Race guard: issue {} got linked_task_ids before project creation", issue_id)
        return

    logger.info("[PRODUCT_TRIGGER] Issue {} assigned to {} — creating project", issue_id, assignee_id)
    project_id = await _create_project_for_issue(slug, issue)

    if project_id:
        prod.update_issue(
            slug, issue_id,
            status=IssueStatus.IN_PROGRESS.value,
            linked_task_ids=[project_id],
        )


def _is_blocker_unresolved(slug: str, issue_id: str) -> bool:
    """Check if a blocker issue is still unresolved (not done/released)."""
    blocker = prod.load_issue(slug, issue_id)
    if not blocker:
        return False
    return blocker.get("status") not in (IssueStatus.DONE.value, IssueStatus.RELEASED.value)


async def handle_sprint_closed(event: CompanyEvent) -> None:
    """When a sprint is closed, auto-create a review checklist for the product owner."""
    slug = event.payload.get("product_slug", "")
    sprint_id = event.payload.get("sprint_id", "")

    if not slug:
        logger.debug("[PRODUCT_TRIGGER] handle_sprint_closed: no product_slug, skip")
        return

    product = prod.load_product(slug)
    if not product:
        logger.warning("[PRODUCT_TRIGGER] handle_sprint_closed: product '{}' not found", slug)
        return

    owner_id = product.get("owner_id", "")
    prod.create_review(
        slug=slug,
        trigger="sprint_closed",
        trigger_ref=sprint_id,
        owner=owner_id,
    )
    logger.info("[PRODUCT_TRIGGER] Auto-created review for sprint {} in {}", sprint_id, slug)


def _log_product_activity(event: CompanyEvent) -> None:
    """Log a product event to the product-scoped activity feed."""
    slug = event.payload.get("product_slug", "")
    if not slug:
        return
    detail = event.payload.get("detail", "")
    if not detail:
        # Build a default detail string from event type + payload
        etype = event.type.value
        title = event.payload.get("title", "")
        issue_id = event.payload.get("issue_id", "")
        sprint_id = event.payload.get("sprint_id", "")
        if title:
            detail = f"{etype}: {title}"
        elif issue_id:
            detail = f"{etype}: {issue_id}"
        elif sprint_id:
            detail = f"{etype}: {sprint_id}"
        else:
            detail = etype
    try:
        prod.append_product_activity(
            slug,
            event_type=event.type.value,
            actor=event.agent,
            detail=detail,
        )
    except Exception:
        logger.debug("[PRODUCT_TRIGGER] Failed to log activity for {}", slug)


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

    # Event types that should be auto-logged to product activity feed
    _ACTIVITY_EVENT_TYPES = {
        EventType.ISSUE_CREATED,
        EventType.ISSUE_CLOSED,
        EventType.ISSUE_ASSIGNED,
        EventType.SPRINT_CREATED,
        EventType.SPRINT_STARTED,
        EventType.SPRINT_CLOSED,
        EventType.VERSION_RELEASED,
        EventType.REVIEW_CREATED,
        EventType.REVIEW_COMPLETED,
        EventType.KR_UPDATED,
    }

    async def _dispatch_loop() -> None:
        while True:
            event = await queue.get()
            try:
                # Auto-log product events to activity feed
                if event.type in _ACTIVITY_EVENT_TYPES:
                    _log_product_activity(event)

                if event.type == EventType.ISSUE_CREATED:
                    await handle_issue_created(event)
                elif event.type == EventType.ISSUE_ASSIGNED:
                    await handle_issue_assigned(event)
                elif event.type == EventType.AGENT_DONE:
                    # Only handle if it has product context
                    if event.payload.get("product_slug"):
                        await handle_project_complete(event)
                elif event.type == EventType.SPRINT_CLOSED:
                    await handle_sprint_closed(event)
            except Exception:
                logger.exception(
                    "[PRODUCT_TRIGGER] Error handling event {}", event.type
                )

    task = asyncio.ensure_future(_dispatch_loop())
    logger.info("[PRODUCT_TRIGGER] Product triggers registered")
    return task
