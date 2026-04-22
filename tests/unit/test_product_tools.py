"""Tests for product_tools — LangChain @tool wrappers over product.py CRUD."""

import pytest
import re
from onemancompany.core import product as prod


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(prod, "PRODUCTS_DIR", tmp_path)
    emp_dir = tmp_path / "employees"
    emp_dir.mkdir()
    monkeypatch.setattr(prod, "EMPLOYEES_DIR", emp_dir)
    for eid in ("00004", "00010", "00011", "emp-1", "agent", "emp001"):
        (emp_dir / eid).mkdir()
    prod.create_product(name="ToolTest", owner_id="00004", description="test product")
    yield


@pytest.fixture
def product_slug():
    return prod.list_products()[0]["slug"]


class TestProductTools:
    @pytest.mark.asyncio
    async def test_create_issue_tool(self, product_slug):
        from onemancompany.agents.product_tools import create_product_issue

        result = await create_product_issue.ainvoke(
            {
                "product_slug": product_slug,
                "title": "Performance bug",
                "description": "Page loads slowly",
                "priority": "P1",
            }
        )
        assert "issue_" in result
        assert "Performance bug" in result

    @pytest.mark.asyncio
    async def test_create_issue_with_labels(self, product_slug):
        from onemancompany.agents.product_tools import create_product_issue

        result = await create_product_issue.ainvoke(
            {
                "product_slug": product_slug,
                "title": "CSS bug",
                "description": "broken layout",
                "priority": "P2",
                "labels": "frontend,css",
            }
        )
        assert "issue_" in result
        # Verify labels persisted
        issues = prod.list_issues(product_slug)
        css_issue = [i for i in issues if i["title"] == "CSS bug"][0]
        assert "frontend" in css_issue["labels"]

    @pytest.mark.asyncio
    async def test_update_issue_tool(self, product_slug):
        from onemancompany.agents.product_tools import (
            create_product_issue,
            update_product_issue,
        )

        create_result = await create_product_issue.ainvoke(
            {
                "product_slug": product_slug,
                "title": "Bug",
                "description": "desc",
                "priority": "P2",
            }
        )
        issue_id = re.search(r"(issue_\w+)", create_result).group(1)
        result = await update_product_issue.ainvoke(
            {
                "product_slug": product_slug,
                "issue_id": issue_id,
                "status": "in_progress",
            }
        )
        assert "in_progress" in result

    @pytest.mark.asyncio
    async def test_close_issue_tool(self, product_slug):
        from onemancompany.agents.product_tools import (
            create_product_issue,
            close_product_issue,
        )

        create_result = await create_product_issue.ainvoke(
            {
                "product_slug": product_slug,
                "title": "Fix me",
                "description": "broken",
                "priority": "P1",
            }
        )
        issue_id = re.search(r"(issue_\w+)", create_result).group(1)
        result = await close_product_issue.ainvoke(
            {
                "product_slug": product_slug,
                "issue_id": issue_id,
                "resolution": "fixed",
            }
        )
        assert "closed" in result.lower()

    @pytest.mark.asyncio
    async def test_close_issue_invalid_resolution(self, product_slug):
        from onemancompany.agents.product_tools import (
            create_product_issue,
            close_product_issue,
        )

        create_result = await create_product_issue.ainvoke(
            {
                "product_slug": product_slug,
                "title": "Bug2",
                "description": "d",
                "priority": "P1",
            }
        )
        issue_id = re.search(r"(issue_\w+)", create_result).group(1)
        result = await close_product_issue.ainvoke(
            {
                "product_slug": product_slug,
                "issue_id": issue_id,
                "resolution": "invalid_resolution",
            }
        )
        assert "error" in result.lower()

    @pytest.mark.asyncio
    async def test_get_product_context_tool(self, product_slug):
        from onemancompany.agents.product_tools import get_product_context_tool

        result = await get_product_context_tool.ainvoke(
            {"product_slug": product_slug}
        )
        assert "ToolTest" in result

    @pytest.mark.asyncio
    async def test_get_product_context_not_found(self):
        from onemancompany.agents.product_tools import get_product_context_tool

        result = await get_product_context_tool.ainvoke(
            {"product_slug": "nonexistent-slug"}
        )
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_list_issues_tool(self, product_slug):
        from onemancompany.agents.product_tools import (
            create_product_issue,
            list_product_issues_tool,
        )

        await create_product_issue.ainvoke(
            {
                "product_slug": product_slug,
                "title": "Issue A",
                "description": "a",
                "priority": "P0",
            }
        )
        result = await list_product_issues_tool.ainvoke(
            {"product_slug": product_slug}
        )
        assert "Issue A" in result

    @pytest.mark.asyncio
    async def test_list_issues_empty(self, product_slug):
        from onemancompany.agents.product_tools import list_product_issues_tool

        result = await list_product_issues_tool.ainvoke(
            {"product_slug": product_slug, "status": "closed"}
        )
        assert "no issues" in result.lower()

    @pytest.mark.asyncio
    async def test_update_kr_tool(self, product_slug):
        from onemancompany.agents.product_tools import update_kr_progress_tool

        kr = prod.add_key_result(product_slug, title="DAU", target=1000)
        result = await update_kr_progress_tool.ainvoke(
            {
                "product_slug": product_slug,
                "kr_id": kr["id"],
                "current_value": 500,
            }
        )
        assert "500" in result

    @pytest.mark.asyncio
    async def test_update_kr_not_found(self, product_slug):
        from onemancompany.agents.product_tools import update_kr_progress_tool

        result = await update_kr_progress_tool.ainvoke(
            {
                "product_slug": product_slug,
                "kr_id": "kr_nonexistent",
                "current_value": 100,
            }
        )
        assert "error" in result.lower()

    @pytest.mark.asyncio
    async def test_product_tools_list(self):
        from onemancompany.agents.product_tools import PRODUCT_TOOLS

        assert len(PRODUCT_TOOLS) == 24
        names = {t.name for t in PRODUCT_TOOLS}
        assert "create_product_tool" in names
        assert "create_product_issue" in names
        assert "update_product_issue" in names
        assert "close_product_issue" in names
        assert "get_product_context_tool" in names
        assert "list_product_issues_tool" in names
        assert "update_kr_progress_tool" in names

    # ------------------------------------------------------------------
    # _resolve_caller_id fallback (lines 33-34)
    # ------------------------------------------------------------------
    def test_resolve_caller_id_exception_path(self):
        """When vessel import raises, _resolve_caller_id returns 'agent' (lines 33-34)."""
        from unittest.mock import patch

        # _resolve_caller_id imports _current_vessel inside the function.
        # We patch the import mechanism so that the import itself raises.
        import builtins
        real_import = builtins.__import__

        def _fail_vessel(name, *args, **kwargs):
            if "vessel" in name:
                raise ImportError("mocked vessel import failure")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_fail_vessel):
            from onemancompany.agents.product_tools import _resolve_caller_id
            result = _resolve_caller_id()
            assert result == "agent"

    # ------------------------------------------------------------------
    # create_product_tool (lines 57-85)
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_create_product_tool_basic(self):
        from onemancompany.agents.product_tools import create_product_tool

        result = await create_product_tool.ainvoke(
            {
                "name": "NewProduct",
                "description": "A new product",
            }
        )
        assert "Created product" in result
        assert "NewProduct" in result

    @pytest.mark.asyncio
    async def test_create_product_tool_with_krs(self):
        from onemancompany.agents.product_tools import create_product_tool

        result = await create_product_tool.ainvoke(
            {
                "name": "KRProduct",
                "description": "Product with KRs",
                "key_results": "DAU达到1000|1000|users;页面加载<2s|2.0|seconds",
            }
        )
        assert "Created product" in result
        assert "2 key results" in result

    @pytest.mark.asyncio
    async def test_create_product_tool_with_owner(self):
        from onemancompany.agents.product_tools import create_product_tool

        result = await create_product_tool.ainvoke(
            {
                "name": "OwnedProduct",
                "description": "Has owner",
                "owner_id": "emp001",
            }
        )
        assert "Created product" in result

    @pytest.mark.asyncio
    async def test_create_product_tool_kr_no_unit(self):
        """KR with only title|target (no unit)."""
        from onemancompany.agents.product_tools import create_product_tool

        result = await create_product_tool.ainvoke(
            {
                "name": "KRNoUnit",
                "description": "test",
                "key_results": "Users|500",
            }
        )
        assert "1 key results" in result

    @pytest.mark.asyncio
    async def test_create_product_tool_kr_invalid_target(self):
        """KR with non-numeric target is skipped."""
        from onemancompany.agents.product_tools import create_product_tool

        result = await create_product_tool.ainvoke(
            {
                "name": "KRBadTarget",
                "description": "test",
                "key_results": "Bad|notanumber|units",
            }
        )
        assert "Created product" in result
        # No KRs should be added
        assert "key results" not in result

    @pytest.mark.asyncio
    async def test_create_product_tool_kr_single_part_skipped(self):
        """KR with only one part (no pipe separator) is skipped."""
        from onemancompany.agents.product_tools import create_product_tool

        result = await create_product_tool.ainvoke(
            {
                "name": "KRSinglePart",
                "description": "test",
                "key_results": "nodelimiter",
            }
        )
        assert "Created product" in result
        assert "key results" not in result

    @pytest.mark.asyncio
    async def test_create_product_tool_error_path(self):
        """create_product raising ValueError returns error (lines 84-85)."""
        from unittest.mock import patch
        from onemancompany.agents.product_tools import create_product_tool

        with patch(
            "onemancompany.agents.product_tools.prod.create_product",
            side_effect=ValueError("bad input"),
        ):
            result = await create_product_tool.ainvoke(
                {"name": "Fail", "description": "d"}
            )
        assert "Error" in result
        assert "bad input" in result

    # ------------------------------------------------------------------
    # create_product_issue error paths (lines 111, 124-125)
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_create_issue_invalid_priority(self, product_slug):
        """Invalid priority returns an error message (line 111)."""
        from onemancompany.agents.product_tools import create_product_issue

        result = await create_product_issue.ainvoke(
            {
                "product_slug": product_slug,
                "title": "Bug",
                "description": "desc",
                "priority": "INVALID",
            }
        )
        assert "Error" in result
        assert "invalid priority" in result

    @pytest.mark.asyncio
    async def test_create_issue_error_path(self):
        """create_issue raising ValueError returns error (lines 124-125)."""
        from unittest.mock import patch
        from onemancompany.agents.product_tools import create_product_issue

        with patch(
            "onemancompany.agents.product_tools.prod.create_issue",
            side_effect=FileNotFoundError("product not found"),
        ):
            result = await create_product_issue.ainvoke(
                {
                    "product_slug": "nonexistent",
                    "title": "Bug",
                    "description": "desc",
                    "priority": "P1",
                }
            )
        assert "Error" in result
        assert "product not found" in result

    # ------------------------------------------------------------------
    # update_product_issue edge cases (lines 151, 153, 155, 158, 163, 166-167)
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_update_issue_no_fields(self, product_slug):
        """No fields to update returns error (line 158)."""
        from onemancompany.agents.product_tools import update_product_issue

        result = await update_product_issue.ainvoke(
            {
                "product_slug": product_slug,
                "issue_id": "issue_fake",
            }
        )
        assert "no fields to update" in result.lower()

    @pytest.mark.asyncio
    async def test_update_issue_with_priority(self, product_slug):
        """Update with priority field (line 151)."""
        from onemancompany.agents.product_tools import (
            create_product_issue,
            update_product_issue,
        )

        cr = await create_product_issue.ainvoke(
            {
                "product_slug": product_slug,
                "title": "PriBug",
                "description": "d",
                "priority": "P2",
            }
        )
        issue_id = re.search(r"(issue_\w+)", cr).group(1)
        result = await update_product_issue.ainvoke(
            {
                "product_slug": product_slug,
                "issue_id": issue_id,
                "priority": "P0",
            }
        )
        assert "P0" in result

    @pytest.mark.asyncio
    async def test_update_issue_with_assignee(self, product_slug):
        """Update with assignee_id field (line 153)."""
        from onemancompany.agents.product_tools import (
            create_product_issue,
            update_product_issue,
        )

        cr = await create_product_issue.ainvoke(
            {
                "product_slug": product_slug,
                "title": "AssigneeBug",
                "description": "d",
                "priority": "P2",
            }
        )
        issue_id = re.search(r"(issue_\w+)", cr).group(1)
        result = await update_product_issue.ainvoke(
            {
                "product_slug": product_slug,
                "issue_id": issue_id,
                "assignee_id": "emp001",
            }
        )
        assert "assignee_id" in result

    @pytest.mark.asyncio
    async def test_update_issue_with_labels(self, product_slug):
        """Update with labels field (line 155)."""
        from onemancompany.agents.product_tools import (
            create_product_issue,
            update_product_issue,
        )

        cr = await create_product_issue.ainvoke(
            {
                "product_slug": product_slug,
                "title": "LabelBug",
                "description": "d",
                "priority": "P2",
            }
        )
        issue_id = re.search(r"(issue_\w+)", cr).group(1)
        result = await update_product_issue.ainvoke(
            {
                "product_slug": product_slug,
                "issue_id": issue_id,
                "labels": "bug,frontend",
            }
        )
        assert "labels" in result

    @pytest.mark.asyncio
    async def test_update_issue_not_found(self, product_slug):
        """Issue not found returns error (line 163)."""
        from onemancompany.agents.product_tools import update_product_issue

        result = await update_product_issue.ainvoke(
            {
                "product_slug": product_slug,
                "issue_id": "issue_nonexistent",
                "status": "in_progress",
            }
        )
        assert "error" in result.lower()

    @pytest.mark.asyncio
    async def test_update_issue_error_path(self):
        """update_issue raising ValueError returns error (lines 166-167)."""
        from unittest.mock import patch
        from onemancompany.agents.product_tools import update_product_issue

        with patch(
            "onemancompany.agents.product_tools.prod.update_issue",
            side_effect=FileNotFoundError("product not found"),
        ):
            result = await update_product_issue.ainvoke(
                {
                    "product_slug": "nonexistent-product",
                    "issue_id": "issue_fake",
                    "status": "in_progress",
                }
            )
        assert "Error" in result
        assert "product not found" in result

    # ------------------------------------------------------------------
    # close_product_issue edge cases (lines 190, 193-194)
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_close_issue_not_found(self, product_slug):
        """Closing nonexistent issue returns error (line 190)."""
        from onemancompany.agents.product_tools import close_product_issue

        result = await close_product_issue.ainvoke(
            {
                "product_slug": product_slug,
                "issue_id": "issue_nonexistent",
                "resolution": "fixed",
            }
        )
        assert "error" in result.lower()

    @pytest.mark.asyncio
    async def test_close_issue_error_path(self):
        """close_issue raising ValueError returns error (lines 193-194)."""
        from unittest.mock import patch
        from onemancompany.agents.product_tools import close_product_issue

        with patch(
            "onemancompany.agents.product_tools.prod.close_issue",
            side_effect=FileNotFoundError("product not found"),
        ):
            result = await close_product_issue.ainvoke(
                {
                    "product_slug": "nonexistent-product",
                    "issue_id": "issue_fake",
                    "resolution": "fixed",
                }
            )
        assert "Error" in result
        assert "product not found" in result

    # ------------------------------------------------------------------
    # get_product_context_tool with KRs and issues (lines 220-225, 232-234)
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_get_product_context_with_krs_and_issues(self, product_slug):
        """Context includes KRs and open issues (lines 220-225, 232-234)."""
        from onemancompany.agents.product_tools import (
            create_product_issue,
            get_product_context_tool,
        )

        # Add KRs
        prod.add_key_result(product_slug, title="DAU", target=1000)
        prod.update_kr_progress(
            product_slug,
            prod.load_product(product_slug)["key_results"][0]["id"],
            current=500,
        )

        # Add an open issue
        await create_product_issue.ainvoke(
            {
                "product_slug": product_slug,
                "title": "OpenBug",
                "description": "still open",
                "priority": "P1",
            }
        )

        result = await get_product_context_tool.ainvoke(
            {"product_slug": product_slug}
        )
        assert "Key Results" in result
        assert "DAU" in result
        assert "500/1000" in result
        assert "50%" in result
        assert "Open Issues" in result
        assert "OpenBug" in result

    @pytest.mark.asyncio
    async def test_get_product_context_kr_zero_target(self, product_slug):
        """KR with target=0 shows 0% (edge case in line 224)."""
        from onemancompany.agents.product_tools import get_product_context_tool

        prod.add_key_result(product_slug, title="ZeroTarget", target=0)

        result = await get_product_context_tool.ainvoke(
            {"product_slug": product_slug}
        )
        assert "ZeroTarget" in result
        assert "0%" in result

    # ------------------------------------------------------------------
    # list_product_issues_tool filters (lines 259, 261-263)
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_list_issues_with_status_filter(self, product_slug):
        """Filter by status (line 259)."""
        from onemancompany.agents.product_tools import (
            create_product_issue,
            list_product_issues_tool,
        )

        await create_product_issue.ainvoke(
            {
                "product_slug": product_slug,
                "title": "Backlog issue",
                "description": "d",
                "priority": "P2",
            }
        )
        result = await list_product_issues_tool.ainvoke(
            {"product_slug": product_slug, "status": "backlog"}
        )
        assert "Backlog issue" in result

    @pytest.mark.asyncio
    async def test_list_issues_with_priority_filter(self, product_slug):
        """Filter by priority (lines 261-263)."""
        from onemancompany.agents.product_tools import (
            create_product_issue,
            list_product_issues_tool,
        )

        await create_product_issue.ainvoke(
            {
                "product_slug": product_slug,
                "title": "Critical issue",
                "description": "d",
                "priority": "P0",
            }
        )
        await create_product_issue.ainvoke(
            {
                "product_slug": product_slug,
                "title": "Low issue",
                "description": "d",
                "priority": "P3",
            }
        )
        result = await list_product_issues_tool.ainvoke(
            {"product_slug": product_slug, "priority": "P0"}
        )
        assert "Critical issue" in result

    # ------------------------------------------------------------------
    # PRODUCT_TOOLS export
    # ------------------------------------------------------------------
    def test_product_tools_export_is_list(self):
        from onemancompany.agents.product_tools import PRODUCT_TOOLS

        assert isinstance(PRODUCT_TOOLS, list)
        assert len(PRODUCT_TOOLS) == 24  # 7 original + 3 sprint tools
        for t in PRODUCT_TOOLS:
            assert hasattr(t, "ainvoke")


# ---------------------------------------------------------------------------
# Sprint tools
# ---------------------------------------------------------------------------


class TestSprintTools:
    @pytest.mark.asyncio
    async def test_create_sprint_tool(self, product_slug):
        from onemancompany.agents.product_tools import create_sprint_tool

        result = await create_sprint_tool.ainvoke({
            "product_slug": product_slug,
            "name": "Sprint 1",
            "start_date": "2026-04-21",
            "end_date": "2026-05-05",
            "goal": "MVP",
        })
        assert "Created sprint" in result
        assert "Sprint 1" in result

    @pytest.mark.asyncio
    async def test_create_sprint_tool_with_capacity(self, product_slug):
        from onemancompany.agents.product_tools import create_sprint_tool

        result = await create_sprint_tool.ainvoke({
            "product_slug": product_slug,
            "name": "Sprint 2",
            "start_date": "2026-05-06",
            "end_date": "2026-05-20",
            "capacity": "21",
        })
        assert "Created sprint" in result
        sprints = prod.list_sprints(product_slug)
        s2 = [s for s in sprints if s["name"] == "Sprint 2"][0]
        assert s2["capacity"] == 21

    @pytest.mark.asyncio
    async def test_close_sprint_tool(self, product_slug):
        from onemancompany.agents.product_tools import close_sprint_tool

        s = prod.create_sprint(slug=product_slug, name="S1", start_date="2026-04-01", end_date="2026-04-15")
        prod.update_sprint(product_slug, s["id"], status="active")
        result = await close_sprint_tool.ainvoke({"product_slug": product_slug})
        assert "Sprint closed" in result
        assert "velocity=" in result

    @pytest.mark.asyncio
    async def test_close_sprint_tool_no_active(self, product_slug):
        from onemancompany.agents.product_tools import close_sprint_tool

        result = await close_sprint_tool.ainvoke({"product_slug": product_slug})
        assert "No active sprint" in result

    @pytest.mark.asyncio
    async def test_get_sprint_info_active(self, product_slug):
        from onemancompany.agents.product_tools import get_sprint_info_tool

        s = prod.create_sprint(slug=product_slug, name="S1", start_date="2026-04-01", end_date="2026-04-15", goal="Build it")
        prod.update_sprint(product_slug, s["id"], status="active")
        result = await get_sprint_info_tool.ainvoke({"product_slug": product_slug})
        assert "S1" in result
        assert "Build it" in result

    @pytest.mark.asyncio
    async def test_get_sprint_info_no_sprints(self, product_slug):
        from onemancompany.agents.product_tools import get_sprint_info_tool

        result = await get_sprint_info_tool.ainvoke({"product_slug": product_slug})
        assert "No sprints found" in result

    @pytest.mark.asyncio
    async def test_create_sprint_tool_error(self):
        from onemancompany.agents.product_tools import create_sprint_tool

        result = await create_sprint_tool.ainvoke({
            "product_slug": "nonexistent",
            "name": "S1",
            "start_date": "2026-04-01",
            "end_date": "2026-04-15",
        })
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_close_sprint_tool_error(self, product_slug):
        from onemancompany.agents.product_tools import close_sprint_tool

        s = prod.create_sprint(slug=product_slug, name="S1", start_date="2026-04-01", end_date="2026-04-15")
        # Try closing a non-active sprint
        result = await close_sprint_tool.ainvoke({
            "product_slug": product_slug,
            "sprint_id": s["id"],
        })
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_get_sprint_info_by_id(self, product_slug):
        from onemancompany.agents.product_tools import get_sprint_info_tool

        s = prod.create_sprint(slug=product_slug, name="ById", start_date="2026-04-01", end_date="2026-04-15")
        result = await get_sprint_info_tool.ainvoke({
            "product_slug": product_slug,
            "sprint_id": s["id"],
        })
        assert "ById" in result

    @pytest.mark.asyncio
    async def test_get_sprint_info_fallback_listing(self, product_slug):
        """No active sprint but sprints exist → lists all sprints."""
        from onemancompany.agents.product_tools import get_sprint_info_tool

        prod.create_sprint(slug=product_slug, name="Closed1", start_date="2026-04-01", end_date="2026-04-15")
        result = await get_sprint_info_tool.ainvoke({"product_slug": product_slug})
        assert "No active sprint" in result
        assert "Closed1" in result

    @pytest.mark.asyncio
    async def test_get_sprint_info_with_capacity_and_suggestion(self, product_slug):
        """Active sprint with capacity set and enough history for suggestion."""
        from onemancompany.agents.product_tools import get_sprint_info_tool
        from onemancompany.core.models import IssueResolution

        slug = product_slug
        # Create 3 closed sprints for velocity history
        for i in range(3):
            s = prod.create_sprint(slug=slug, name=f"H{i}", start_date="2026-01-01", end_date="2026-01-15")
            prod.update_sprint(slug, s["id"], status="active")
            issue = prod.create_issue(slug=slug, title=f"T{i}", created_by="x", story_points=10, sprint=s["id"])
            prod.close_issue(slug, issue["id"], resolution=IssueResolution.FIXED)
            prod.close_sprint(slug, s["id"])

        # Create active sprint with capacity
        active = prod.create_sprint(slug=slug, name="Current", start_date="2026-04-01", end_date="2026-04-15")
        prod.update_sprint(slug, active["id"], status="active", capacity=15)

        result = await get_sprint_info_tool.ainvoke({"product_slug": slug})
        assert "Capacity: 15 pts" in result
        assert "Suggested capacity" in result

    @pytest.mark.asyncio
    async def test_get_sprint_info_error(self):
        from onemancompany.agents.product_tools import get_sprint_info_tool

        # Use a product slug that doesn't exist but won't raise — just returns empty
        # Force an error by patching
        from unittest.mock import patch
        with patch("onemancompany.agents.product_tools.prod.get_active_sprint", side_effect=ValueError("boom")):
            result = await get_sprint_info_tool.ainvoke({"product_slug": "x"})
        assert "Error" in result


# ---------------------------------------------------------------------------
# Issue link tools
# ---------------------------------------------------------------------------


class TestIssueLinkTools:
    @pytest.mark.asyncio
    async def test_link_issues_tool(self, product_slug):
        from onemancompany.agents.product_tools import link_issues_tool

        i1 = prod.create_issue(slug=product_slug, title="A", created_by="ceo")
        i2 = prod.create_issue(slug=product_slug, title="B", created_by="ceo")
        result = await link_issues_tool.ainvoke({
            "product_slug": product_slug,
            "issue_id": i1["id"],
            "target_id": i2["id"],
            "relation": "blocks",
        })
        assert "Linked" in result
        assert "blocks" in result

    @pytest.mark.asyncio
    async def test_link_issues_invalid_relation(self, product_slug):
        from onemancompany.agents.product_tools import link_issues_tool

        result = await link_issues_tool.ainvoke({
            "product_slug": product_slug,
            "issue_id": "i1",
            "target_id": "i2",
            "relation": "invalid",
        })
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_link_issues_self_reference(self, product_slug):
        from onemancompany.agents.product_tools import link_issues_tool

        i1 = prod.create_issue(slug=product_slug, title="Self", created_by="ceo")
        result = await link_issues_tool.ainvoke({
            "product_slug": product_slug,
            "issue_id": i1["id"],
            "target_id": i1["id"],
            "relation": "blocks",
        })
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_unlink_issues_tool(self, product_slug):
        from onemancompany.agents.product_tools import unlink_issues_tool

        i1 = prod.create_issue(slug=product_slug, title="A", created_by="ceo")
        i2 = prod.create_issue(slug=product_slug, title="B", created_by="ceo")
        from onemancompany.core.models import IssueRelation
        prod.add_issue_link(product_slug, i1["id"], i2["id"], IssueRelation.BLOCKS)
        result = await unlink_issues_tool.ainvoke({
            "product_slug": product_slug,
            "issue_id": i1["id"],
            "target_id": i2["id"],
        })
        assert "Unlinked" in result

    @pytest.mark.asyncio
    async def test_check_blocked_issues_none(self, product_slug):
        from onemancompany.agents.product_tools import check_blocked_issues_tool

        result = await check_blocked_issues_tool.ainvoke({"product_slug": product_slug})
        assert "No blocked issues" in result

    @pytest.mark.asyncio
    async def test_check_blocked_issues_found(self, product_slug):
        from onemancompany.agents.product_tools import check_blocked_issues_tool

        i1 = prod.create_issue(slug=product_slug, title="Blocker", created_by="ceo")
        i2 = prod.create_issue(slug=product_slug, title="Blocked", created_by="ceo")
        from onemancompany.core.models import IssueRelation
        prod.add_issue_link(product_slug, i2["id"], i1["id"], IssueRelation.BLOCKED_BY)

        result = await check_blocked_issues_tool.ainvoke({"product_slug": product_slug})
        assert "Blocked" in result
        assert i1["id"] in result


# ---------------------------------------------------------------------------
# Review tools
# ---------------------------------------------------------------------------


class TestReviewTools:
    @pytest.mark.asyncio
    async def test_manage_review_list_empty(self, product_slug):
        from onemancompany.agents.product_tools import manage_review_tool

        result = await manage_review_tool.ainvoke({
            "product_slug": product_slug,
            "action": "list",
        })
        assert "No reviews" in result

    @pytest.mark.asyncio
    async def test_manage_review_create_and_list(self, product_slug):
        from onemancompany.agents.product_tools import manage_review_tool

        prod.create_review(slug=product_slug, trigger="test", owner="00010")
        result = await manage_review_tool.ainvoke({
            "product_slug": product_slug,
            "action": "list",
        })
        assert "open" in result.lower()
        assert "rev_" in result

    @pytest.mark.asyncio
    async def test_manage_review_view(self, product_slug):
        from onemancompany.agents.product_tools import manage_review_tool

        review = prod.create_review(slug=product_slug, trigger="sprint_closed", owner="00010")
        result = await manage_review_tool.ainvoke({
            "product_slug": product_slug,
            "action": "view",
            "review_id": review["id"],
        })
        assert "Checklist" in result
        assert "sprint_closed" in result

    @pytest.mark.asyncio
    async def test_manage_review_view_not_found(self, product_slug):
        from onemancompany.agents.product_tools import manage_review_tool

        result = await manage_review_tool.ainvoke({
            "product_slug": product_slug,
            "action": "view",
            "review_id": "rev_nonexist",
        })
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_manage_review_check_item(self, product_slug):
        from onemancompany.agents.product_tools import manage_review_tool

        review = prod.create_review(slug=product_slug, trigger="test", owner="00010")
        first_key = review["items"][0]["key"]
        result = await manage_review_tool.ainvoke({
            "product_slug": product_slug,
            "action": "check",
            "review_id": review["id"],
            "item_key": first_key,
        })
        assert "Checked" in result

    @pytest.mark.asyncio
    async def test_manage_review_uncheck_item(self, product_slug):
        from onemancompany.agents.product_tools import manage_review_tool

        review = prod.create_review(slug=product_slug, trigger="test", owner="00010")
        first_key = review["items"][0]["key"]
        prod.update_review_item(product_slug, review["id"], first_key, checked=True)
        result = await manage_review_tool.ainvoke({
            "product_slug": product_slug,
            "action": "uncheck",
            "review_id": review["id"],
            "item_key": first_key,
        })
        assert "Unchecked" in result

    @pytest.mark.asyncio
    async def test_manage_review_complete(self, product_slug):
        from onemancompany.agents.product_tools import manage_review_tool

        review = prod.create_review(slug=product_slug, trigger="test", owner="00010")
        for item in review["items"]:
            prod.update_review_item(product_slug, review["id"], item["key"], checked=True)
        result = await manage_review_tool.ainvoke({
            "product_slug": product_slug,
            "action": "complete",
            "review_id": review["id"],
        })
        assert "completed" in result.lower()

    @pytest.mark.asyncio
    async def test_manage_review_complete_unchecked_error(self, product_slug):
        from onemancompany.agents.product_tools import manage_review_tool

        review = prod.create_review(slug=product_slug, trigger="test", owner="00010")
        result = await manage_review_tool.ainvoke({
            "product_slug": product_slug,
            "action": "complete",
            "review_id": review["id"],
        })
        assert "Error" in result
        assert "unchecked" in result.lower()

    @pytest.mark.asyncio
    async def test_manage_review_no_review_id(self, product_slug):
        from onemancompany.agents.product_tools import manage_review_tool

        result = await manage_review_tool.ainvoke({
            "product_slug": product_slug,
            "action": "view",
        })
        assert "Error" in result
        assert "review_id" in result.lower()

    @pytest.mark.asyncio
    async def test_manage_review_check_no_item_key(self, product_slug):
        from onemancompany.agents.product_tools import manage_review_tool

        review = prod.create_review(slug=product_slug, trigger="test", owner="00010")
        result = await manage_review_tool.ainvoke({
            "product_slug": product_slug,
            "action": "check",
            "review_id": review["id"],
        })
        assert "Error" in result
        assert "item_key" in result.lower()

    @pytest.mark.asyncio
    async def test_manage_review_unknown_action(self, product_slug):
        from onemancompany.agents.product_tools import manage_review_tool

        result = await manage_review_tool.ainvoke({
            "product_slug": product_slug,
            "action": "delete",
            "review_id": "rev_fake",
        })
        assert "Error" in result
        assert "unknown action" in result.lower()


# ---------------------------------------------------------------------------
# B5: update_product_tool and delete_product_tool
# ---------------------------------------------------------------------------


class TestUpdateProductTool:
    """Agent tool: update_product_tool wraps prod.update_product."""

    @pytest.mark.asyncio
    async def test_update_name_and_description(self):
        from onemancompany.agents.product_tools import update_product_tool

        p = prod.create_product(name="OrigName", owner_id="00010", description="Old desc")
        result = await update_product_tool.ainvoke(
            {"product_slug": p["slug"], "name": "NewName", "description": "New desc"}
        )
        assert "Updated" in result
        loaded = prod.load_product(p["slug"])
        assert loaded["name"] == "NewName"
        assert loaded["description"] == "New desc"

    @pytest.mark.asyncio
    async def test_update_nonexistent_product(self):
        from onemancompany.agents.product_tools import update_product_tool

        result = await update_product_tool.ainvoke({"product_slug": "nope", "name": "X"})
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_update_partial_fields(self):
        from onemancompany.agents.product_tools import update_product_tool

        p = prod.create_product(name="Partial", owner_id="00010", description="Keep me")
        result = await update_product_tool.ainvoke(
            {"product_slug": p["slug"], "name": "Changed"}
        )
        assert "Updated" in result
        loaded = prod.load_product(p["slug"])
        assert loaded["name"] == "Changed"
        assert loaded["description"] == "Keep me"


class TestDeleteProductTool:
    """Agent tool: delete_product_tool wraps prod.delete_product."""

    @pytest.mark.asyncio
    async def test_delete_existing_product(self):
        from onemancompany.agents.product_tools import delete_product_tool

        p = prod.create_product(name="ToDelete", owner_id="00010")
        result = await delete_product_tool.ainvoke({"product_slug": p["slug"]})
        assert "Deleted" in result
        assert prod.load_product(p["slug"]) is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_product(self):
        from onemancompany.agents.product_tools import delete_product_tool

        result = await delete_product_tool.ainvoke({"product_slug": "ghost"})
        assert "error" in result.lower() or "not found" in result.lower()
