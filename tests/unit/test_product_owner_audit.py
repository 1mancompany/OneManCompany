"""Tests for product owner chain audit fixes.

Covers:
- Batch 1: owner_id/assignee_id validation, release_version DONE-only, narrow exceptions
- Batch 2: sprint carry-over sort, DONE status event, KR dedup by kr_id, race guard,
           notify_owner review project, sprint date overlap
- Batch 3: per-product config thresholds, assign_issue + transfer_ownership tools
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from onemancompany.core import product as prod
from onemancompany.core.models import (
    IssueStatus,
    IssuePriority,
    IssueRelation,
    IssueResolution,
    ProductStatus,
    SprintStatus,
)


@pytest.fixture(autouse=True)
def _redirect_products_dir(tmp_path, monkeypatch):
    """Point PRODUCTS_DIR and EMPLOYEES_DIR to temp directories for every test."""
    monkeypatch.setattr(prod, "PRODUCTS_DIR", tmp_path)
    emp_dir = tmp_path / "employees"
    emp_dir.mkdir()
    monkeypatch.setattr(prod, "EMPLOYEES_DIR", emp_dir)
    # Create standard test employee directories
    for eid in ("00010", "00011", "00004"):
        (emp_dir / eid).mkdir()


def _make_product(status="planning", **kw):
    return prod.create_product(
        name=kw.pop("name", "AuditProd"),
        owner_id=kw.pop("owner_id", "00010"),
        description=kw.pop("description", "test"),
        status=ProductStatus(status),
        **kw,
    )


# ---------------------------------------------------------------------------
# Batch 1: Validate owner_id / assignee_id
# ---------------------------------------------------------------------------


class TestOwnerIdValidation:
    """create_product and update_product must validate owner_id."""

    def test_create_product_invalid_owner_raises(self):
        with pytest.raises(ValueError, match="not found in employee registry"):
            prod.create_product(name="Bad Owner", owner_id="NONEXISTENT_999")

    def test_create_product_valid_owner_succeeds(self, tmp_path):
        """Valid owner_id (directory exists under EMPLOYEES_DIR) should succeed."""
        # create_product should work normally with valid owner_id
        # We need to ensure the validation function recognizes valid IDs
        p = prod.create_product(name="Good Owner", owner_id="00010")
        assert p["owner_id"] == "00010"

    def test_update_product_invalid_owner_raises(self):
        p = _make_product(owner_id="00010")
        with pytest.raises(ValueError, match="not found in employee registry"):
            prod.update_product(p["slug"], owner_id="NONEXISTENT_999")

    def test_update_product_valid_owner_succeeds(self):
        p = _make_product(owner_id="00010")
        result = prod.update_product(p["slug"], owner_id="00011")
        assert result["owner_id"] == "00011"


class TestAssigneeIdValidation:
    """create_issue and update_issue must validate assignee_id."""

    def test_create_issue_invalid_assignee_raises(self):
        p = _make_product()
        with pytest.raises(ValueError, match="not found in employee registry"):
            prod.create_issue(
                slug=p["slug"], title="Bad", created_by="ceo",
                assignee_id="NONEXISTENT_999",
            )

    def test_create_issue_no_assignee_ok(self):
        p = _make_product()
        issue = prod.create_issue(slug=p["slug"], title="No Assignee", created_by="ceo")
        assert issue["assignee_id"] is None

    def test_update_issue_invalid_assignee_raises(self):
        p = _make_product()
        issue = prod.create_issue(slug=p["slug"], title="Update Test", created_by="ceo")
        with pytest.raises(ValueError, match="not found in employee registry"):
            prod.update_issue(p["slug"], issue["id"], assignee_id="NONEXISTENT_999")


# ---------------------------------------------------------------------------
# Batch 1: release_version rejects non-DONE issues
# ---------------------------------------------------------------------------


class TestReleaseVersionValidation:
    """release_version should only transition DONE issues to RELEASED."""

    def test_release_skips_non_done_issues(self):
        p = _make_product()
        slug = p["slug"]
        # Create two issues: one DONE, one still BACKLOG
        done_issue = prod.create_issue(slug=slug, title="Done Issue", created_by="ceo")
        prod.update_issue(slug, done_issue["id"], status=IssueStatus.IN_PROGRESS.value)
        prod.update_issue(slug, done_issue["id"], status=IssueStatus.DONE.value)

        backlog_issue = prod.create_issue(slug=slug, title="Backlog Issue", created_by="ceo")

        result = prod.release_version(slug, [done_issue["id"], backlog_issue["id"]])
        # Done issue should be RELEASED
        done_after = prod.load_issue(slug, done_issue["id"])
        assert done_after["status"] == IssueStatus.RELEASED.value
        # Backlog issue should remain BACKLOG (NOT forced to RELEASED)
        backlog_after = prod.load_issue(slug, backlog_issue["id"])
        assert backlog_after["status"] == IssueStatus.BACKLOG.value
        # Result should report skipped issues
        assert "skipped_issues" in result

    def test_release_done_issues_all_transition(self):
        p = _make_product()
        slug = p["slug"]
        i1 = prod.create_issue(slug=slug, title="I1", created_by="ceo")
        i2 = prod.create_issue(slug=slug, title="I2", created_by="ceo")
        for i in [i1, i2]:
            prod.update_issue(slug, i["id"], status=IssueStatus.IN_PROGRESS.value)
            prod.update_issue(slug, i["id"], status=IssueStatus.DONE.value)
        result = prod.release_version(slug, [i1["id"], i2["id"]])
        assert prod.load_issue(slug, i1["id"])["status"] == IssueStatus.RELEASED.value
        assert prod.load_issue(slug, i2["id"])["status"] == IssueStatus.RELEASED.value
        assert len(result.get("skipped_issues", [])) == 0


# ---------------------------------------------------------------------------
# Batch 1: Narrow exception in notify_owner (tested in test_product_triggers)
# ---------------------------------------------------------------------------

class TestNotifyOwnerExceptionHandling:
    """notify_owner should catch specific exceptions, not bare Exception."""

    @pytest.mark.asyncio
    async def test_unexpected_error_propagates(self):
        """TypeError or KeyError should NOT be silently swallowed."""
        from onemancompany.core.product_triggers import notify_owner

        p = prod.create_product(name="PropagateErr", owner_id="00010",
                                status=ProductStatus.ACTIVE)
        prod.create_issue(slug=p["slug"], title="I1", created_by="ceo")

        # Simulate an unexpected TypeError inside the function
        with patch("onemancompany.core.project_archive.list_projects", side_effect=TypeError("unexpected")):
            with pytest.raises(TypeError, match="unexpected"):
                await notify_owner(p["slug"], reason="test")


# ---------------------------------------------------------------------------
# Batch 2: Sprint carry-over sorts by start_date
# ---------------------------------------------------------------------------


class TestSprintCarryOverSort:
    """close_sprint should carry unfinished issues to the chronologically next sprint."""

    def test_carry_over_picks_earliest_planning_sprint(self):
        p = _make_product()
        slug = p["slug"]
        # Create active sprint
        active = prod.create_sprint(
            slug=slug, name="Sprint 1", start_date="2026-01-01", end_date="2026-01-14"
        )
        prod.start_sprint(slug, active["id"])

        # Create two planning sprints — later one first (alphabetical order would pick wrong)
        later = prod.create_sprint(
            slug=slug, name="Sprint 3", start_date="2026-02-01", end_date="2026-02-14"
        )
        earlier = prod.create_sprint(
            slug=slug, name="Sprint 2", start_date="2026-01-15", end_date="2026-01-28"
        )

        # Add issue to active sprint
        issue = prod.create_issue(slug=slug, title="Unfinished", created_by="ceo", sprint=active["id"])

        # Close active sprint
        prod.close_sprint(slug, active["id"])

        # Issue should be in the EARLIER planning sprint, not the later one
        updated_issue = prod.load_issue(slug, issue["id"])
        assert updated_issue["sprint"] == earlier["id"]


# ---------------------------------------------------------------------------
# Batch 2: update_issue with status=DONE sets closed_at
# ---------------------------------------------------------------------------


class TestUpdateIssueDoneAutoClose:
    """Setting status to DONE via update_issue should set closed_at and resolution."""

    def test_status_done_sets_closed_at(self):
        p = _make_product()
        slug = p["slug"]
        issue = prod.create_issue(slug=slug, title="Auto Close", created_by="ceo")
        prod.update_issue(slug, issue["id"], status=IssueStatus.IN_PROGRESS.value)
        prod.update_issue(slug, issue["id"], status=IssueStatus.DONE.value)
        loaded = prod.load_issue(slug, issue["id"])
        assert loaded["closed_at"] is not None
        assert loaded["resolution"] == IssueResolution.FIXED.value


# ---------------------------------------------------------------------------
# Batch 2: KR-to-issue dedup uses kr_id label
# ---------------------------------------------------------------------------


class TestKrIssueDedupByKrId:
    """Auto-created KR issues should use kr_id label for dedup, not title match."""

    @pytest.mark.asyncio
    async def test_auto_kr_issue_has_kr_id_label(self):
        from onemancompany.core.product_triggers import run_product_check

        p = prod.create_product(
            name="KrDedup", owner_id="00010", status=ProductStatus.ACTIVE
        )
        slug = p["slug"]
        kr = prod.add_key_result(slug, title="Revenue", target=100, unit="$")

        with patch("onemancompany.core.project_archive.list_projects", return_value=[]):
            result = await run_product_check(slug)

        # Check that the auto-created issue has a label with kr_id
        issues = prod.list_issues(slug)
        kr_issues = [i for i in issues if f"kr:{kr['id']}" in i.get("labels", [])]
        assert len(kr_issues) == 1

    @pytest.mark.asyncio
    async def test_kr_dedup_prevents_duplicate_on_second_run(self):
        from onemancompany.core.product_triggers import run_product_check

        p = prod.create_product(
            name="KrNoDup", owner_id="00010", status=ProductStatus.ACTIVE
        )
        slug = p["slug"]
        prod.add_key_result(slug, title="Revenue", target=100, unit="$")

        with patch("onemancompany.core.project_archive.list_projects", return_value=[]):
            await run_product_check(slug)
            await run_product_check(slug)

        # Should still only have 1 issue
        issues = prod.list_issues(slug)
        kr_issues = [i for i in issues if "kr-tracking" in i.get("labels", [])]
        assert len(kr_issues) == 1


# ---------------------------------------------------------------------------
# Batch 2: Race guard in handle_issue_assigned
# ---------------------------------------------------------------------------


class TestIssueAssignedRaceGuard:
    """handle_issue_assigned re-reads linked_task_ids before creating project."""

    @pytest.mark.asyncio
    async def test_skips_if_linked_task_ids_appeared_during_race(self):
        from onemancompany.core.product_triggers import handle_issue_assigned
        from onemancompany.core.events import CompanyEvent
        from onemancompany.core.models import EventType

        p = prod.create_product(
            name="RaceGuard", owner_id="00010", status=ProductStatus.ACTIVE
        )
        issue = prod.create_issue(
            slug=p["slug"], title="Race Issue", created_by="ceo"
        )

        # Simulate: between event fire and handler, linked_task_ids got populated
        prod.update_issue(p["slug"], issue["id"], linked_task_ids=["proj_already"])

        event = CompanyEvent(
            type=EventType.ISSUE_ASSIGNED,
            payload={
                "product_slug": p["slug"],
                "issue_id": issue["id"],
                "assignee_id": "00010",
            },
        )
        # Should NOT create a project (already has linked_task_ids)
        with patch("onemancompany.core.product_triggers._create_project_for_issue") as mock_create:
            await handle_issue_assigned(event)
            mock_create.assert_not_called()


# ---------------------------------------------------------------------------
# Batch 2: notify_owner uses dedicated _create_review_project
# ---------------------------------------------------------------------------


class TestNotifyOwnerReviewProject:
    """notify_owner should create a properly structured review project, not fake an issue dict."""

    @pytest.mark.asyncio
    async def test_creates_review_project_with_proper_structure(self):
        from onemancompany.core.product_triggers import notify_owner

        p = prod.create_product(
            name="ReviewProj", owner_id="00010", status=ProductStatus.ACTIVE
        )
        prod.create_issue(slug=p["slug"], title="I1", created_by="ceo")

        # No active projects → should create one via _create_review_project
        with patch("onemancompany.core.project_archive.list_projects", return_value=[]), \
             patch("onemancompany.core.product_triggers._create_review_project",
                   new_callable=AsyncMock, return_value="proj_review_1") as mock_create:
            result = await notify_owner(p["slug"], reason="quarterly review")
            mock_create.assert_called_once()
            # Verify it was called with slug and reason, NOT a fake issue dict
            call_args = mock_create.call_args
            assert call_args[0][0] == p["slug"]  # first arg is slug


# ---------------------------------------------------------------------------
# Batch 2: Sprint date overlap validation
# ---------------------------------------------------------------------------


class TestSprintDateOverlap:
    """create_sprint should reject overlapping dates with non-closed sprints."""

    def test_overlapping_sprint_raises(self):
        p = _make_product()
        slug = p["slug"]
        prod.create_sprint(
            slug=slug, name="Sprint A", start_date="2026-01-01", end_date="2026-01-14"
        )
        # Overlapping: starts during Sprint A
        with pytest.raises(ValueError, match="[Oo]verlap"):
            prod.create_sprint(
                slug=slug, name="Sprint B", start_date="2026-01-10", end_date="2026-01-20"
            )

    def test_adjacent_sprint_ok(self):
        p = _make_product()
        slug = p["slug"]
        prod.create_sprint(
            slug=slug, name="Sprint A", start_date="2026-01-01", end_date="2026-01-14"
        )
        # Adjacent: starts after Sprint A ends
        s2 = prod.create_sprint(
            slug=slug, name="Sprint B", start_date="2026-01-15", end_date="2026-01-28"
        )
        assert s2["name"] == "Sprint B"

    def test_closed_sprint_no_overlap_check(self):
        p = _make_product()
        slug = p["slug"]
        s1 = prod.create_sprint(
            slug=slug, name="Sprint A", start_date="2026-01-01", end_date="2026-01-14"
        )
        # Start and close the sprint
        prod.start_sprint(slug, s1["id"])
        # Add an issue so close_sprint has something to work with
        prod.close_sprint(slug, s1["id"])
        # Now create overlapping sprint — should be OK since s1 is closed
        s2 = prod.create_sprint(
            slug=slug, name="Sprint B", start_date="2026-01-01", end_date="2026-01-14"
        )
        assert s2["name"] == "Sprint B"


# ---------------------------------------------------------------------------
# Batch 3: Per-product config thresholds
# ---------------------------------------------------------------------------


class TestPerProductThresholds:
    """Products can override global thresholds via config field."""

    def test_default_thresholds_used(self):
        p = _make_product()
        loaded = prod.load_product(p["slug"])
        # No config field → should use defaults
        assert loaded.get("config") is None or loaded.get("config", {}).get("max_active_projects") is None

    def test_per_product_threshold_stored(self):
        p = _make_product()
        prod.update_product(p["slug"], config={"max_active_projects": 5})
        loaded = prod.load_product(p["slug"])
        assert loaded["config"]["max_active_projects"] == 5

    @pytest.mark.asyncio
    async def test_product_check_uses_per_product_threshold(self):
        from onemancompany.core.product_triggers import run_product_check

        p = prod.create_product(
            name="CustomThresh", owner_id="00010", status=ProductStatus.ACTIVE
        )
        slug = p["slug"]
        prod.update_product(slug, config={"max_active_projects": 1})

        # Create 1 active project mock
        mock_proj = {"project_id": "proj_1", "product_id": p["id"], "status": "active"}
        # Create a P0 issue that would normally trigger project creation
        prod.create_issue(
            slug=slug, title="P0 Issue", created_by="ceo", priority=IssuePriority.P0
        )

        with patch("onemancompany.core.project_archive.list_projects", return_value=[mock_proj]):
            result = await run_product_check(slug)

        # Should skip because max_active_projects=1 and we already have 1
        actions = result.get("actions", [])
        project_actions = [a for a in actions if "Created project for P0" in a]
        assert len(project_actions) == 0


# ---------------------------------------------------------------------------
# Batch 3: assign_issue_tool and transfer_product_ownership_tool
# ---------------------------------------------------------------------------


class TestAssignIssueTool:
    """Dedicated assign_issue_tool should validate and emit event."""

    @pytest.mark.asyncio
    async def test_assign_issue_tool_basic(self):
        from onemancompany.agents.product_tools import assign_issue_tool
        p = _make_product()
        slug = p["slug"]
        issue = prod.create_issue(slug=slug, title="Assign Me", created_by="ceo")

        result = await assign_issue_tool.ainvoke({
            "product_slug": slug,
            "issue_id": issue["id"],
            "assignee_id": "00010",
        })
        assert "assigned" in result.lower() or "00010" in result
        loaded = prod.load_issue(slug, issue["id"])
        assert loaded["assignee_id"] == "00010"


class TestTransferOwnershipTool:
    """Dedicated transfer_product_ownership_tool."""

    @pytest.mark.asyncio
    async def test_transfer_ownership_tool_basic(self):
        from onemancompany.agents.product_tools import transfer_product_ownership_tool
        p = _make_product(owner_id="00010")
        slug = p["slug"]

        result = await transfer_product_ownership_tool.ainvoke({
            "product_slug": slug,
            "new_owner_id": "00011",
        })
        assert "00011" in result or "transfer" in result.lower()
        loaded = prod.load_product(slug)
        assert loaded["owner_id"] == "00011"


# ---------------------------------------------------------------------------
# Coverage: _create_review_project full function (product_triggers.py:157-224)
# ---------------------------------------------------------------------------


class TestCreateReviewProjectFull:
    """Exercise _create_review_project body to cover lines 164-224."""

    @pytest.mark.asyncio
    async def test_happy_path_creates_tree_and_schedules(self):
        """Full flow: product exists, project created, tree built, owner scheduled."""
        from onemancompany.core.product_triggers import _create_review_project

        p = _make_product(name="ReviewFull", owner_id="00010")

        mock_async_create = AsyncMock(return_value=("proj-rev-1", "iter-1"))
        mock_get_dir = MagicMock(return_value="/tmp/proj-rev-1")
        mock_tree_inst = MagicMock()
        mock_root = MagicMock()
        mock_root.id = "root-1"
        mock_owner_node = MagicMock()
        mock_owner_node.id = "owner-1"
        mock_tree_inst.create_root.return_value = mock_root
        mock_tree_inst.add_child.return_value = mock_owner_node
        mock_tree_cls = MagicMock(return_value=mock_tree_inst)
        mock_save = MagicMock()
        mock_em = MagicMock()

        with patch("onemancompany.core.project_archive.async_create_project_from_task", mock_async_create), \
             patch("onemancompany.core.project_archive.get_project_dir", mock_get_dir), \
             patch("onemancompany.core.task_tree.TaskTree", mock_tree_cls), \
             patch("onemancompany.core.vessel._save_project_tree", mock_save), \
             patch("onemancompany.core.agent_loop.employee_manager", mock_em):
            result = await _create_review_project(p["slug"], "quarterly review")

        assert result == "proj-rev-1"
        mock_async_create.assert_called_once()
        mock_tree_cls.assert_called_once_with(project_id="proj-rev-1/iter-1", mode="standard")
        mock_tree_inst.create_root.assert_called_once()
        mock_tree_inst.add_child.assert_called_once()
        # Verify owner_id (00010) is used as employee_id for the child node
        add_child_kwargs = mock_tree_inst.add_child.call_args
        assert add_child_kwargs[1]["employee_id"] == "00010"
        assert "quarterly review" in add_child_kwargs[1]["title"]
        mock_save.assert_called_once()
        mock_em.schedule_node.assert_called_once_with("00010", "owner-1", mock_em.schedule_node.call_args[0][2])
        mock_em._schedule_next.assert_called_once_with("00010")

    @pytest.mark.asyncio
    async def test_product_not_found_returns_empty(self):
        """Line 170-171: nonexistent product slug returns empty string."""
        from onemancompany.core.product_triggers import _create_review_project

        result = await _create_review_project("nonexistent-slug", "some reason")
        assert result == ""

    @pytest.mark.asyncio
    async def test_no_owner_falls_back_to_ea(self):
        """Line 201: if owner_id is empty, falls back to EA_ID."""
        from onemancompany.core.product_triggers import _create_review_project

        p = _make_product(name="NoOwnerReview", owner_id="00010")
        # Clear owner_id after creation
        prod.update_product(p["slug"], owner_id="")

        mock_async_create = AsyncMock(return_value=("proj-rev-2", ""))
        mock_get_dir = MagicMock(return_value="/tmp/proj-rev-2")
        mock_tree_inst = MagicMock()
        mock_root = MagicMock()
        mock_root.id = "root-1"
        mock_child = MagicMock()
        mock_child.id = "child-1"
        mock_tree_inst.create_root.return_value = mock_root
        mock_tree_inst.add_child.return_value = mock_child
        mock_tree_cls = MagicMock(return_value=mock_tree_inst)
        mock_em = MagicMock()

        with patch("onemancompany.core.project_archive.async_create_project_from_task", mock_async_create), \
             patch("onemancompany.core.project_archive.get_project_dir", mock_get_dir), \
             patch("onemancompany.core.task_tree.TaskTree", mock_tree_cls), \
             patch("onemancompany.core.vessel._save_project_tree", MagicMock()), \
             patch("onemancompany.core.agent_loop.employee_manager", mock_em):
            result = await _create_review_project(p["slug"], "no owner check")

        assert result == "proj-rev-2"
        # Should use EA_ID as fallback
        from onemancompany.core.config import EA_ID
        add_child_kwargs = mock_tree_inst.add_child.call_args
        assert add_child_kwargs[1]["employee_id"] == EA_ID
        mock_em.schedule_node.assert_called_once()
        assert mock_em.schedule_node.call_args[0][0] == EA_ID

    @pytest.mark.asyncio
    async def test_no_iter_id_uses_project_id_only(self):
        """Line 182: when iter_id is empty, ctx_id = project_id."""
        from onemancompany.core.product_triggers import _create_review_project

        p = _make_product(name="NoIterReview", owner_id="00010")

        mock_async_create = AsyncMock(return_value=("proj-rev-3", ""))
        mock_get_dir = MagicMock(return_value="/tmp/proj-rev-3")
        mock_tree_inst = MagicMock()
        mock_root = MagicMock()
        mock_root.id = "root-1"
        mock_child = MagicMock()
        mock_child.id = "child-1"
        mock_tree_inst.create_root.return_value = mock_root
        mock_tree_inst.add_child.return_value = mock_child
        mock_tree_cls = MagicMock(return_value=mock_tree_inst)
        mock_em = MagicMock()

        with patch("onemancompany.core.project_archive.async_create_project_from_task", mock_async_create), \
             patch("onemancompany.core.project_archive.get_project_dir", mock_get_dir), \
             patch("onemancompany.core.task_tree.TaskTree", mock_tree_cls), \
             patch("onemancompany.core.vessel._save_project_tree", MagicMock()), \
             patch("onemancompany.core.agent_loop.employee_manager", mock_em):
            result = await _create_review_project(p["slug"], "no iter")

        assert result == "proj-rev-3"
        mock_tree_cls.assert_called_once_with(project_id="proj-rev-3", mode="standard")

    @pytest.mark.asyncio
    async def test_exception_returns_empty_string(self):
        """Lines 219-224: exception during project creation returns empty string."""
        from onemancompany.core.product_triggers import _create_review_project

        p = _make_product(name="ExcReview", owner_id="00010")

        mock_async_create = AsyncMock(side_effect=RuntimeError("project creation failed"))
        with patch("onemancompany.core.project_archive.async_create_project_from_task", mock_async_create):
            result = await _create_review_project(p["slug"], "exc test")

        assert result == ""
