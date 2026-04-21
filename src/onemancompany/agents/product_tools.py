"""Product management tools for LangChain agents.

Six @tool functions wrapping product.py CRUD operations.
Agents use these to create/update issues, track KR progress,
and inspect product context.
"""

from __future__ import annotations

from langchain_core.tools import tool
from loguru import logger

from onemancompany.core import product as prod
from onemancompany.core.models import IssueResolution, IssuePriority, IssueStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_RESOLUTION_MAP = {r.value: r for r in IssueResolution}
_PRIORITY_MAP = {p.value: p for p in IssuePriority}
_STATUS_MAP = {s.value: s for s in IssueStatus}


def _resolve_caller_id() -> str:
    """Best-effort extraction of current employee ID from vessel context."""
    try:
        from onemancompany.core.vessel import _current_vessel

        vessel = _current_vessel.get()
        return vessel.employee_id if vessel else "agent"
    except Exception:
        return "agent"


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@tool
async def create_product_tool(
    name: str,
    description: str,
    key_results: str = "",
    owner_id: str = "",
) -> str:
    """Create a new product with optional key results.

    Args:
        name: Product name (e.g. "OneManCompany官网")
        description: Product objective/description
        key_results: Semicolon-separated KRs, each as "title|target|unit" (e.g. "DAU达到1000|1000|users;页面加载<2s|2.0|seconds")
        owner_id: Employee ID of the product owner (optional, defaults to caller)
    """
    caller = _resolve_caller_id()
    oid = owner_id or caller

    try:
        product = prod.create_product(name=name, owner_id=oid, description=description)
        slug = product["slug"]

        # Parse and add key results
        kr_count = 0
        if key_results:
            for kr_str in key_results.split(";"):
                parts = kr_str.strip().split("|")
                if len(parts) >= 2:
                    title = parts[0].strip()
                    try:
                        target = float(parts[1].strip())
                    except ValueError:
                        logger.debug("Skipping KR with non-numeric target: {}", parts[1])
                        continue
                    unit = parts[2].strip() if len(parts) >= 3 else ""
                    prod.add_key_result(slug, title=title, target=target, unit=unit)
                    kr_count += 1

        result = f"Created product '{name}' (slug: {slug})"
        if kr_count:
            result += f" with {kr_count} key results"
        return result
    except (ValueError, FileNotFoundError) as e:
        return f"Error: {e}"


@tool
async def create_product_issue(
    product_slug: str,
    title: str,
    description: str,
    priority: str,
    labels: str = "",
) -> str:
    """Create a new issue for a product.

    Args:
        product_slug: The product slug (e.g. "omc-website")
        title: Issue title
        description: Detailed description
        priority: P0 (critical), P1 (high), P2 (medium), P3 (low)
        labels: Comma-separated labels (e.g. "performance,frontend")
    """
    created_by = _resolve_caller_id()
    label_list = [l.strip() for l in labels.split(",") if l.strip()] if labels else []

    # Validate priority
    pri = _PRIORITY_MAP.get(priority)
    if pri is None:
        return f"Error: invalid priority '{priority}'. Must be one of: {', '.join(_PRIORITY_MAP)}"

    try:
        issue = prod.create_issue(
            slug=product_slug,
            title=title,
            description=description,
            priority=pri,
            labels=label_list,
            created_by=created_by,
        )
        logger.debug("create_product_issue: created {} for {}", issue["id"], product_slug)
        return f"Created issue {issue['id']}: {title} [{priority}]"
    except (ValueError, FileNotFoundError) as e:
        return f"Error: {e}"


@tool
async def update_product_issue(
    product_slug: str,
    issue_id: str,
    status: str = "",
    priority: str = "",
    assignee_id: str = "",
    labels: str = "",
) -> str:
    """Update an existing issue's fields.

    Args:
        product_slug: The product slug
        issue_id: The issue ID (e.g. "issue_abc12345")
        status: New status (backlog, planned, in_progress, in_review, done, released)
        priority: New priority (P0, P1, P2, P3)
        assignee_id: Employee ID to assign
        labels: Comma-separated labels (replaces existing)
    """
    updates: dict = {}
    if status:
        updates["status"] = status
    if priority:
        updates["priority"] = priority
    if assignee_id:
        updates["assignee_id"] = assignee_id
    if labels:
        updates["labels"] = [l.strip() for l in labels.split(",") if l.strip()]

    if not updates:
        return "Error: no fields to update"

    try:
        issue = prod.update_issue(product_slug, issue_id, **updates)
        if issue is None:
            return f"Error: issue {issue_id} not found in product {product_slug}"
        logger.debug("update_product_issue: updated {} — {}", issue_id, updates)
        return f"Updated {issue_id}: {updates}"
    except (ValueError, FileNotFoundError) as e:
        return f"Error: {e}"


@tool
async def close_product_issue(
    product_slug: str,
    issue_id: str,
    resolution: str,
) -> str:
    """Close an issue with a resolution.

    Args:
        product_slug: The product slug
        issue_id: The issue ID
        resolution: fixed, wontfix, duplicate, or by_design
    """
    res = _RESOLUTION_MAP.get(resolution)
    if res is None:
        return f"Error: invalid resolution '{resolution}'. Must be one of: {', '.join(_RESOLUTION_MAP)}"

    try:
        issue = prod.close_issue(product_slug, issue_id, resolution=res)
        if issue is None:
            return f"Error: issue {issue_id} not found in product {product_slug}"
        logger.debug("close_product_issue: closed {} as {}", issue_id, resolution)
        return f"Closed {issue_id} as {resolution}"
    except (ValueError, FileNotFoundError) as e:
        return f"Error: {e}"


@tool
async def get_product_context_tool(product_slug: str) -> str:
    """Get current product context: objective, KR progress, active issues.

    Args:
        product_slug: The product slug
    """
    product = prod.load_product(product_slug)
    if not product:
        return f"Product '{product_slug}' not found"

    lines = [
        f"# {product['name']}",
        f"Status: {product.get('status', 'unknown')}",
        f"Version: {product.get('current_version', '?')}",
    ]

    if product.get("description"):
        lines.append(f"Description: {product['description']}")

    # Key Results
    krs = product.get("key_results", [])
    if krs:
        lines.append("\n## Key Results")
        for kr in krs:
            target = kr.get("target", 0)
            current = kr.get("current", 0)
            pct = (current / target * 100) if target else 0
            lines.append(f"- {kr['title']}: {current}/{target} ({pct:.0f}%)")

    # Active issues
    issues = prod.list_issues(product_slug)
    terminal = {IssueStatus.DONE.value, IssueStatus.RELEASED.value}
    open_issues = [i for i in issues if i.get("status") not in terminal]
    if open_issues:
        lines.append(f"\n## Open Issues ({len(open_issues)})")
        for issue in open_issues:
            lines.append(
                f"- [{issue.get('priority', '?')}] {issue['title']} "
                f"({issue['id']}) [{issue.get('status', '?')}]"
            )

    return "\n".join(lines)


@tool
async def list_product_issues_tool(
    product_slug: str,
    status: str = "",
    priority: str = "",
) -> str:
    """List issues for a product, optionally filtered.

    Args:
        product_slug: The product slug
        status: Filter by status (backlog, planned, in_progress, in_review, done, released)
        priority: Filter by priority (P0, P1, P2, P3)
    """
    kwargs: dict = {}
    if status:
        s = _STATUS_MAP.get(status)
        if s:
            kwargs["status"] = s
    if priority:
        p = _PRIORITY_MAP.get(priority)
        if p:
            kwargs["priority"] = p

    issues = prod.list_issues(product_slug, **kwargs)
    if not issues:
        return "No issues found"

    lines = [
        f"- [{i.get('priority', '?')}] {i['title']} ({i['id']}) [{i.get('status', '?')}]"
        for i in issues
    ]
    return "\n".join(lines)


@tool
async def update_kr_progress_tool(
    product_slug: str,
    kr_id: str,
    current_value: float,
) -> str:
    """Update a Key Result's current progress value.

    Args:
        product_slug: The product slug
        kr_id: The key result ID (e.g. "kr_abc12345")
        current_value: New current value
    """
    try:
        kr = prod.update_kr_progress(product_slug, kr_id, current=current_value)
        target = kr.get("target", 0)
        current = kr.get("current", 0)
        pct = (current / target * 100) if target else 0
        logger.debug("update_kr_progress_tool: {} → {}/{}", kr_id, current, target)
        return f"Updated {kr['title']}: {current}/{target} ({pct:.0f}%)"
    except (ValueError, FileNotFoundError) as e:
        return f"Error: {e}"


# ---------------------------------------------------------------------------
# Sprint tools
# ---------------------------------------------------------------------------


@tool
async def create_sprint_tool(
    product_slug: str,
    name: str,
    start_date: str,
    end_date: str,
    goal: str = "",
    capacity: str = "",
) -> str:
    """Create a new sprint for a product.

    Args:
        product_slug: The product slug
        name: Sprint name (e.g. "Sprint 3")
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format
        goal: Sprint goal description
        capacity: Optional capacity in story points
    """
    try:
        cap = int(capacity) if capacity else None
        sprint = prod.create_sprint(
            slug=product_slug,
            name=name,
            start_date=start_date,
            end_date=end_date,
            goal=goal,
            capacity=cap,
        )
        logger.debug("create_sprint_tool: {} in {}", sprint["id"], product_slug)
        return f"Created sprint '{name}' ({sprint['id']}) for {product_slug}: {start_date} → {end_date}"
    except (ValueError, FileNotFoundError) as e:
        return f"Error: {e}"


@tool
async def close_sprint_tool(
    product_slug: str,
    sprint_id: str = "",
) -> str:
    """Close the active sprint for a product. Calculates velocity, carries over unfinished issues, generates retrospective.

    Args:
        product_slug: The product slug
        sprint_id: Sprint ID to close. If empty, closes the active sprint.
    """
    try:
        if not sprint_id:
            active = prod.get_active_sprint(product_slug)
            if not active:
                return f"No active sprint found for {product_slug}"
            sprint_id = active["id"]
        result = prod.close_sprint(product_slug, sprint_id)
        vel = result.get("velocity", 0)
        rate = result.get("completion_rate", 0)
        carry = result.get("carry_over_count", 0)
        logger.debug("close_sprint_tool: {} closed — vel={}", sprint_id, vel)
        return (
            f"Sprint closed: velocity={vel} pts, completion={rate}%, "
            f"carry_over={carry} issues\n\n{result.get('retrospective', '')}"
        )
    except (ValueError, FileNotFoundError) as e:
        return f"Error: {e}"


@tool
async def get_sprint_info_tool(
    product_slug: str,
    sprint_id: str = "",
) -> str:
    """Get sprint information. Defaults to the active sprint if no ID given.

    Args:
        product_slug: The product slug
        sprint_id: Sprint ID. If empty, returns the active sprint.
    """
    try:
        if sprint_id:
            sprint = prod.load_sprint(product_slug, sprint_id)
        else:
            sprint = prod.get_active_sprint(product_slug)

        if not sprint:
            # List all sprints as fallback
            all_sprints = prod.list_sprints(product_slug)
            if not all_sprints:
                return f"No sprints found for {product_slug}"
            lines = [f"No active sprint. All sprints for {product_slug}:"]
            for s in all_sprints:
                lines.append(f"- [{s['status']}] {s['name']} ({s['id']}) {s['start_date']}→{s['end_date']}")
            return "\n".join(lines)

        # Show sprint details
        issues = prod.list_issues(product_slug, sprint=sprint["id"])
        done = [i for i in issues if i.get("status") in ("done", "released")]
        vel = sum(i.get("story_points") or 0 for i in done)
        total_pts = sum(i.get("story_points") or 0 for i in issues)

        lines = [
            f"**{sprint['name']}** ({sprint['id']})",
            f"Status: {sprint['status']}",
            f"Goal: {sprint.get('goal') or 'N/A'}",
            f"Period: {sprint['start_date']} → {sprint['end_date']}",
            f"Issues: {len(done)}/{len(issues)} done",
            f"Points: {vel}/{total_pts}",
        ]
        if sprint.get("capacity"):
            lines.append(f"Capacity: {sprint['capacity']} pts")

        suggestion = prod.suggest_capacity(product_slug)
        if suggestion is not None:
            lines.append(f"Suggested capacity (avg last 3): {suggestion} pts")

        return "\n".join(lines)
    except (ValueError, FileNotFoundError) as e:
        return f"Error: {e}"


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

PRODUCT_TOOLS = [
    create_product_tool,
    create_product_issue,
    update_product_issue,
    close_product_issue,
    get_product_context_tool,
    list_product_issues_tool,
    update_kr_progress_tool,
    create_sprint_tool,
    close_sprint_tool,
    get_sprint_info_tool,
]
