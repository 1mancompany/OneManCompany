"""Unit tests for product management CRUD — products, key results, issues, and sprints."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from onemancompany.core import product as prod
from onemancompany.core.models import (
    IssueStatus,
    IssuePriority,
    IssueResolution,
    IssueRelation,
    ProductStatus,
    SprintStatus,
)
from onemancompany.core.task_lifecycle import TaskPhase


@pytest.fixture(autouse=True)
def _redirect_products_dir(tmp_path, monkeypatch):
    """Point PRODUCTS_DIR to a temp directory for every test."""
    monkeypatch.setattr(prod, "PRODUCTS_DIR", tmp_path)


# ---------------------------------------------------------------------------
# Product CRUD
# ---------------------------------------------------------------------------


class TestProductCRUD:
    def test_create_product(self):
        p = prod.create_product(name="Acme Widget", owner_id="00010")
        assert p["id"].startswith("prod_")
        assert len(p["id"]) == len("prod_") + 8
        assert p["name"] == "Acme Widget"
        assert p["owner_id"] == "00010"
        assert p["status"] == ProductStatus.PLANNING
        assert p["current_version"] == "0.1.0"
        assert p["slug"] == "acme-widget"

    def test_create_product_has_workspace_initialized_false(self):
        p = prod.create_product(name="WS Test", owner_id="00010")
        assert p["workspace_initialized"] is False

    def test_update_workspace_initialized(self):
        p = prod.create_product(name="WS Test2", owner_id="00010")
        prod.update_product(p["slug"], workspace_initialized=True)
        loaded = prod.load_product(p["slug"])
        assert loaded["workspace_initialized"] is True

    def test_load_product(self):
        p = prod.create_product(name="Load Me", owner_id="00010")
        loaded = prod.load_product(p["slug"])
        assert loaded is not None
        assert loaded["id"] == p["id"]
        assert loaded["name"] == "Load Me"

    def test_load_product_missing(self):
        assert prod.load_product("nonexistent") is None

    def test_list_products(self):
        prod.create_product(name="Alpha", owner_id="00010")
        prod.create_product(name="Beta", owner_id="00011")
        products = prod.list_products()
        assert len(products) == 2
        names = {p["name"] for p in products}
        assert names == {"Alpha", "Beta"}

    def test_update_product(self):
        p = prod.create_product(name="Updatable", owner_id="00010")
        prod.update_product(p["slug"], status=ProductStatus.ACTIVE, description="New desc")
        loaded = prod.load_product(p["slug"])
        assert loaded["status"] == ProductStatus.ACTIVE.value
        assert loaded["description"] == "New desc"

    def test_slug_dedup(self):
        p1 = prod.create_product(name="Dupe Name", owner_id="00010")
        p2 = prod.create_product(name="Dupe Name", owner_id="00011")
        assert p1["slug"] == "dupe-name"
        assert p2["slug"] == "dupe-name-2"
        # Third should get -3
        p3 = prod.create_product(name="Dupe Name", owner_id="00012")
        assert p3["slug"] == "dupe-name-3"


# ---------------------------------------------------------------------------
# Key Results
# ---------------------------------------------------------------------------


class TestKeyResults:
    def test_add_key_result(self):
        p = prod.create_product(name="KR Test", owner_id="00010")
        kr = prod.add_key_result(p["slug"], title="Ship v1", target=100.0)
        assert kr["id"].startswith("kr_")
        assert kr["title"] == "Ship v1"
        assert kr["target"] == 100.0
        assert kr["current"] == 0.0

    def test_update_kr_progress(self):
        p = prod.create_product(name="KR Prog", owner_id="00010")
        kr = prod.add_key_result(p["slug"], title="Revenue", target=1000.0)
        updated = prod.update_kr_progress(p["slug"], kr["id"], current=500.0)
        assert updated["current"] == 500.0
        # Verify persisted
        loaded = prod.load_product(p["slug"])
        found = [k for k in loaded["key_results"] if k["id"] == kr["id"]]
        assert found[0]["current"] == 500.0

    def test_update_kr_not_found(self):
        p = prod.create_product(name="KR Miss", owner_id="00010")
        with pytest.raises(ValueError, match="KR 'kr_nonexist' not found"):
            prod.update_kr_progress(p["slug"], "kr_nonexist", current=10.0)


# ---------------------------------------------------------------------------
# Issue CRUD
# ---------------------------------------------------------------------------


class TestIssueCRUD:
    def test_create_issue(self):
        p = prod.create_product(name="Issue Host", owner_id="00010")
        issue = prod.create_issue(
            slug=p["slug"],
            title="Button broken",
            description="Click does nothing",
            priority=IssuePriority.P1,
            created_by="00010",
        )
        assert issue["id"].startswith("issue_")
        assert issue["title"] == "Button broken"
        assert issue["status"] == IssueStatus.BACKLOG
        assert issue["priority"] == IssuePriority.P1
        assert issue["reopened_count"] == 0

    def test_load_issue(self):
        p = prod.create_product(name="Issue Load", owner_id="00010")
        issue = prod.create_issue(
            slug=p["slug"], title="Load bug", priority=IssuePriority.P2, created_by="00010",
        )
        loaded = prod.load_issue(p["slug"], issue["id"])
        assert loaded is not None
        assert loaded["title"] == "Load bug"

    def test_load_issue_missing(self):
        p = prod.create_product(name="Issue Miss", owner_id="00010")
        assert prod.load_issue(p["slug"], "issue_nope1234") is None

    def test_list_issues_no_filter(self):
        p = prod.create_product(name="Issue List", owner_id="00010")
        prod.create_issue(slug=p["slug"], title="A", priority=IssuePriority.P0, created_by="x")
        prod.create_issue(slug=p["slug"], title="B", priority=IssuePriority.P2, created_by="x")
        issues = prod.list_issues(p["slug"])
        assert len(issues) == 2

    def test_list_issues_with_filters(self):
        p = prod.create_product(name="Issue Filter", owner_id="00010")
        prod.create_issue(slug=p["slug"], title="Open P0", priority=IssuePriority.P0, created_by="x", labels=["bug"])
        prod.create_issue(slug=p["slug"], title="Open P2", priority=IssuePriority.P2, created_by="x", labels=["feature"])
        # Filter by priority
        p0s = prod.list_issues(p["slug"], priority=IssuePriority.P0)
        assert len(p0s) == 1
        assert p0s[0]["title"] == "Open P0"
        # Filter by label
        bugs = prod.list_issues(p["slug"], labels=["bug"])
        assert len(bugs) == 1
        assert bugs[0]["title"] == "Open P0"

    def test_close_issue(self):
        p = prod.create_product(name="Issue Close", owner_id="00010")
        issue = prod.create_issue(slug=p["slug"], title="Close me", priority=IssuePriority.P1, created_by="x")
        closed = prod.close_issue(p["slug"], issue["id"], resolution=IssueResolution.FIXED)
        assert closed["status"] == IssueStatus.DONE.value
        assert closed["resolution"] == IssueResolution.FIXED.value
        assert closed["closed_at"] is not None

    def test_reopen_issue(self):
        p = prod.create_product(name="Issue Reopen", owner_id="00010")
        issue = prod.create_issue(slug=p["slug"], title="Reopen me", priority=IssuePriority.P1, created_by="x")
        prod.close_issue(p["slug"], issue["id"], resolution=IssueResolution.FIXED)
        reopened = prod.reopen_issue(p["slug"], issue["id"])
        assert reopened["status"] == IssueStatus.BACKLOG.value
        assert reopened["closed_at"] is None
        assert reopened["resolution"] is None
        assert reopened["reopened_count"] == 1

    def test_update_issue(self):
        p = prod.create_product(name="Issue Update", owner_id="00010")
        issue = prod.create_issue(slug=p["slug"], title="Update me", priority=IssuePriority.P3, created_by="x")
        updated = prod.update_issue(p["slug"], issue["id"], assignee_id="00020", labels=["urgent"])
        assert updated["assignee_id"] == "00020"
        assert updated["labels"] == ["urgent"]


# ---------------------------------------------------------------------------
# Product Versioning
# ---------------------------------------------------------------------------


class TestProductVersion:
    def _make_product_with_issues(self):
        """Helper: create a product with 2 closed issues."""
        p = prod.create_product(name="Versioned App", owner_id="00010")
        i1 = prod.create_issue(slug=p["slug"], title="Fix login", priority=IssuePriority.P1, created_by="x")
        i2 = prod.create_issue(slug=p["slug"], title="Add search", priority=IssuePriority.P2, created_by="x")
        prod.close_issue(p["slug"], i1["id"], resolution=IssueResolution.FIXED)
        prod.close_issue(p["slug"], i2["id"], resolution=IssueResolution.FIXED)
        return p, [i1["id"], i2["id"]]

    def test_release_version(self):
        p, issue_ids = self._make_product_with_issues()
        ver = prod.release_version(p["slug"], issue_ids)
        assert ver["version"] == "0.1.1"
        assert "Fix login" in ver["changelog"]
        assert "Add search" in ver["changelog"]
        assert ver["resolved_issue_ids"] == issue_ids

    def test_release_version_updates_product(self):
        p, issue_ids = self._make_product_with_issues()
        prod.release_version(p["slug"], issue_ids)
        loaded = prod.load_product(p["slug"])
        assert loaded["current_version"] == "0.1.1"

    def test_release_version_file_created(self, tmp_path):
        p, issue_ids = self._make_product_with_issues()
        prod.release_version(p["slug"], issue_ids)
        ver_file = tmp_path / p["slug"] / "versions" / "0.1.1.yaml"
        assert ver_file.exists()

    def test_sequential_releases(self):
        p, issue_ids = self._make_product_with_issues()
        v1 = prod.release_version(p["slug"], issue_ids[:1])
        assert v1["version"] == "0.1.1"
        v2 = prod.release_version(p["slug"], issue_ids[1:])
        assert v2["version"] == "0.1.2"

    def test_bump_minor(self):
        p, issue_ids = self._make_product_with_issues()
        ver = prod.release_version(p["slug"], issue_ids, bump="minor")
        assert ver["version"] == "0.2.0"

    def test_bump_major(self):
        p, issue_ids = self._make_product_with_issues()
        ver = prod.release_version(p["slug"], issue_ids, bump="major")
        assert ver["version"] == "1.0.0"


# ---------------------------------------------------------------------------
# Product Context
# ---------------------------------------------------------------------------


class TestProductContext:
    def test_build_product_context(self):
        p = prod.create_product(name="CtxTest", owner_id="00004", description="Build the best product")
        prod.add_key_result(p["slug"], title="Users", target=1000)
        prod.create_issue(slug=p["slug"], title="Bug A", description="desc", priority=IssuePriority.P0, created_by="ceo")
        prod.create_issue(slug=p["slug"], title="Bug B", description="desc", priority=IssuePriority.P2, created_by="ceo")
        ctx = prod.build_product_context(p["slug"])
        assert "Build the best product" in ctx
        assert "Users" in ctx
        assert "1000" in ctx
        assert "Bug A" in ctx
        assert "0.1.0" in ctx

    def test_build_product_context_missing_product(self):
        ctx = prod.build_product_context("nonexistent")
        assert ctx == ""

    def test_find_slug_by_product_id(self):
        p = prod.create_product(name="FindTest", owner_id="00004", description="obj")
        slug = prod.find_slug_by_product_id(p["id"])
        assert slug == p["slug"]

    def test_find_slug_by_product_id_not_found(self):
        assert prod.find_slug_by_product_id("prod_nonexist") is None


# ---------------------------------------------------------------------------
# Issue History (Audit Trail)
# ---------------------------------------------------------------------------


class TestIssueHistory:
    def test_update_issue_records_history(self):
        p = prod.create_product(name="HistTest", owner_id="00004")
        issue = prod.create_issue(slug=p["slug"], title="Bug", created_by="ceo", priority=IssuePriority.P1)
        prod.update_issue(p["slug"], issue["id"], priority="P0")
        loaded = prod.load_issue(p["slug"], issue["id"])
        assert len(loaded.get("history", [])) == 1
        assert loaded["history"][-1]["field"] == "priority"

    def test_close_issue_records_history(self):
        p = prod.create_product(name="HistClose", owner_id="00004")
        issue = prod.create_issue(slug=p["slug"], title="Fix", created_by="ceo")
        prod.close_issue(p["slug"], issue["id"])
        loaded = prod.load_issue(p["slug"], issue["id"])
        assert any(h["field"] == "status" for h in loaded.get("history", []))

    def test_reopen_issue_records_history(self):
        p = prod.create_product(name="HistReopen", owner_id="00004")
        issue = prod.create_issue(slug=p["slug"], title="Fix", created_by="ceo")
        prod.close_issue(p["slug"], issue["id"])
        prod.reopen_issue(p["slug"], issue["id"])
        loaded = prod.load_issue(p["slug"], issue["id"])
        history = loaded.get("history", [])
        # Exactly 2 entries: close + reopen
        assert len(history) == 2

    def test_kr_progress_records_history(self):
        p = prod.create_product(name="KRHist", owner_id="00004")
        kr = prod.add_key_result(p["slug"], title="DAU", target=1000)
        prod.update_kr_progress(p["slug"], kr["id"], current=500)
        loaded = prod.load_product(p["slug"])
        updated_kr = [k for k in loaded["key_results"] if k["id"] == kr["id"]][0]
        assert len(updated_kr.get("history", [])) >= 1

    def test_issue_has_agile_fields(self):
        p = prod.create_product(name="AgileTest", owner_id="00004")
        issue = prod.create_issue(
            slug=p["slug"], title="Story", created_by="ceo",
            story_points=5, sprint="Sprint 1",
        )
        assert issue["story_points"] == 5
        assert issue["sprint"] == "Sprint 1"


# ---------------------------------------------------------------------------
# Issue Status Derivation
# ---------------------------------------------------------------------------


class TestIssueStatusDerivation:
    def test_no_linked_tasks_is_backlog(self):
        p = prod.create_product(name="DeriveTest", owner_id="00004")
        issue = prod.create_issue(slug=p["slug"], title="Test", created_by="ceo")
        status = prod.derive_issue_status(p["slug"], issue["id"])
        assert status == IssueStatus.BACKLOG

    def test_missing_issue_is_backlog(self):
        prod.create_product(name="DeriveTest2", owner_id="00004")
        status = prod.derive_issue_status("derivetest2", "nonexistent")
        assert status == IssueStatus.BACKLOG

    def test_sync_issue_statuses_returns_changes(self):
        p = prod.create_product(name="SyncTest", owner_id="00004")
        issue = prod.create_issue(
            slug=p["slug"], title="Sync", created_by="ceo", priority=IssuePriority.P1,
        )
        # Set status to in_progress manually but no linked tasks
        prod.update_issue(p["slug"], issue["id"], status=IssueStatus.IN_PROGRESS.value)
        changes = prod.sync_issue_statuses(p["slug"])
        # Should change back to backlog since no linked tasks
        assert len(changes) == 1
        loaded = prod.load_issue(p["slug"], issue["id"])
        assert loaded["status"] == IssueStatus.BACKLOG.value

    def test_released_status_preserved(self):
        p = prod.create_product(name="ReleasedTest", owner_id="00004")
        issue = prod.create_issue(slug=p["slug"], title="Released", created_by="ceo")
        # Walk valid transition chain: backlog → in_progress → done → released
        prod.update_issue(p["slug"], issue["id"], status=IssueStatus.IN_PROGRESS.value)
        prod.update_issue(p["slug"], issue["id"], status=IssueStatus.DONE.value)
        prod.update_issue(p["slug"], issue["id"], status=IssueStatus.RELEASED.value)
        status = prod.derive_issue_status(p["slug"], issue["id"])
        assert status == IssueStatus.RELEASED

    def test_sync_skips_released_issues(self):
        p = prod.create_product(name="SkipReleasedTest", owner_id="00004")
        issue = prod.create_issue(slug=p["slug"], title="Skip", created_by="ceo")
        # Walk valid transition chain: backlog → in_progress → done → released
        prod.update_issue(p["slug"], issue["id"], status=IssueStatus.IN_PROGRESS.value)
        prod.update_issue(p["slug"], issue["id"], status=IssueStatus.DONE.value)
        prod.update_issue(p["slug"], issue["id"], status=IssueStatus.RELEASED.value)
        changes = prod.sync_issue_statuses(p["slug"])
        assert len(changes) == 0
        loaded = prod.load_issue(p["slug"], issue["id"])
        assert loaded["status"] == IssueStatus.RELEASED.value

    def test_derive_all_tasks_processing_is_in_progress(self):
        """Linked tasks with processing status → IN_PROGRESS."""
        p = prod.create_product(name="DeriveProc", owner_id="00004")
        issue = prod.create_issue(slug=p["slug"], title="Proc", created_by="ceo")
        prod.update_issue(p["slug"], issue["id"], linked_task_ids=["proj_aaa"])
        with patch.object(prod, "_resolve_task_status", return_value=TaskPhase.PROCESSING.value):
            status = prod.derive_issue_status(p["slug"], issue["id"])
        assert status == IssueStatus.IN_PROGRESS

    def test_derive_all_tasks_holding_is_in_progress(self):
        """Linked tasks with holding status → IN_PROGRESS."""
        p = prod.create_product(name="DeriveHold", owner_id="00004")
        issue = prod.create_issue(slug=p["slug"], title="Hold", created_by="ceo")
        prod.update_issue(p["slug"], issue["id"], linked_task_ids=["proj_bbb"])
        with patch.object(prod, "_resolve_task_status", return_value=TaskPhase.HOLDING.value):
            status = prod.derive_issue_status(p["slug"], issue["id"])
        assert status == IssueStatus.IN_PROGRESS

    def test_derive_all_finished_is_done(self):
        """All tasks finished → DONE."""
        p = prod.create_product(name="DeriveDone", owner_id="00004")
        issue = prod.create_issue(slug=p["slug"], title="Done", created_by="ceo")
        prod.update_issue(p["slug"], issue["id"], linked_task_ids=["proj_c1", "proj_c2"])
        with patch.object(prod, "_resolve_task_status", return_value=TaskPhase.FINISHED.value):
            status = prod.derive_issue_status(p["slug"], issue["id"])
        assert status == IssueStatus.DONE

    def test_derive_all_accepted_is_done(self):
        """All tasks accepted → DONE."""
        p = prod.create_product(name="DeriveAccepted", owner_id="00004")
        issue = prod.create_issue(slug=p["slug"], title="Acc", created_by="ceo")
        prod.update_issue(p["slug"], issue["id"], linked_task_ids=["proj_d1"])
        with patch.object(prod, "_resolve_task_status", return_value=TaskPhase.ACCEPTED.value):
            status = prod.derive_issue_status(p["slug"], issue["id"])
        assert status == IssueStatus.DONE

    def test_derive_completed_is_in_review(self):
        """Some completed (not yet accepted) → IN_REVIEW."""
        p = prod.create_product(name="DeriveReview", owner_id="00004")
        issue = prod.create_issue(slug=p["slug"], title="Rev", created_by="ceo")
        prod.update_issue(p["slug"], issue["id"], linked_task_ids=["proj_e1", "proj_e2"])
        returns = iter([TaskPhase.COMPLETED.value, TaskPhase.FINISHED.value])
        with patch.object(prod, "_resolve_task_status", side_effect=returns):
            status = prod.derive_issue_status(p["slug"], issue["id"])
        assert status == IssueStatus.IN_REVIEW

    def test_derive_all_pending_is_planned(self):
        """All tasks pending → PLANNED."""
        p = prod.create_product(name="DerivePlan", owner_id="00004")
        issue = prod.create_issue(slug=p["slug"], title="Plan", created_by="ceo")
        prod.update_issue(p["slug"], issue["id"], linked_task_ids=["proj_f1"])
        with patch.object(prod, "_resolve_task_status", return_value=TaskPhase.PENDING.value):
            status = prod.derive_issue_status(p["slug"], issue["id"])
        assert status == IssueStatus.PLANNED

    def test_derive_all_blocked_is_planned(self):
        """All tasks blocked → PLANNED."""
        p = prod.create_product(name="DeriveBlocked", owner_id="00004")
        issue = prod.create_issue(slug=p["slug"], title="Blocked", created_by="ceo")
        prod.update_issue(p["slug"], issue["id"], linked_task_ids=["proj_g1"])
        with patch.object(prod, "_resolve_task_status", return_value=TaskPhase.BLOCKED.value):
            status = prod.derive_issue_status(p["slug"], issue["id"])
        assert status == IssueStatus.PLANNED

    def test_derive_mix_pending_and_active_is_in_progress(self):
        """Mix of pending and processing → IN_PROGRESS (fallthrough)."""
        p = prod.create_product(name="DeriveMix", owner_id="00004")
        issue = prod.create_issue(slug=p["slug"], title="Mix", created_by="ceo")
        prod.update_issue(p["slug"], issue["id"], linked_task_ids=["proj_h1", "proj_h2"])
        returns = iter([TaskPhase.PENDING.value, TaskPhase.COMPLETED.value])
        with patch.object(prod, "_resolve_task_status", side_effect=returns):
            status = prod.derive_issue_status(p["slug"], issue["id"])
        # pending + completed doesn't match any exact bucket → fallthrough IN_PROGRESS
        assert status == IssueStatus.IN_PROGRESS

    def test_derive_no_resolvable_tasks_is_planned(self):
        """Linked task IDs that all resolve to None → PLANNED."""
        p = prod.create_product(name="DeriveNoResolve", owner_id="00004")
        issue = prod.create_issue(slug=p["slug"], title="NoRes", created_by="ceo")
        prod.update_issue(p["slug"], issue["id"], linked_task_ids=["proj_z1"])
        with patch.object(prod, "_resolve_task_status", return_value=None):
            status = prod.derive_issue_status(p["slug"], issue["id"])
        assert status == IssueStatus.PLANNED


# ---------------------------------------------------------------------------
# _resolve_task_status
# ---------------------------------------------------------------------------


class TestResolveTaskStatus:
    def test_missing_project_returns_none(self):
        with patch("onemancompany.core.project_archive.load_project", return_value=None) as mock_load:
            result = prod._resolve_task_status("proj_missing")
        mock_load.assert_called_once_with("proj_missing")
        assert result is None

    def test_archived_project_returns_finished(self):
        with patch("onemancompany.core.project_archive.load_project", return_value={"status": "archived"}):
            result = prod._resolve_task_status("proj_arch")
        assert result == "finished"

    def test_active_project_no_iterations_returns_pending(self):
        with patch("onemancompany.core.project_archive.load_project", return_value={"status": "active", "iterations": []}):
            result = prod._resolve_task_status("proj_noiter")
        assert result == "pending"

    def test_active_project_with_iteration_uses_iter_status(self):
        proj = {"status": "active", "iterations": ["iter_001"]}
        iter_doc = {"status": "processing"}
        with patch("onemancompany.core.project_archive.load_project", return_value=proj), \
             patch("onemancompany.core.project_archive.load_iteration", return_value=iter_doc):
            result = prod._resolve_task_status("proj_active")
        assert result == "processing"

    def test_active_project_iteration_not_found_returns_processing(self):
        proj = {"status": "active", "iterations": ["iter_gone"]}
        with patch("onemancompany.core.project_archive.load_project", return_value=proj), \
             patch("onemancompany.core.project_archive.load_iteration", return_value=None):
            result = prod._resolve_task_status("proj_noit")
        assert result == "processing"

    def test_active_project_iteration_dict_format(self):
        """Iteration list entries can be dicts with 'id' key."""
        proj = {"status": "active", "iterations": [{"id": "iter_d01"}]}
        iter_doc = {"status": "completed"}
        with patch("onemancompany.core.project_archive.load_project", return_value=proj), \
             patch("onemancompany.core.project_archive.load_iteration", return_value=iter_doc):
            result = prod._resolve_task_status("proj_dictiter")
        assert result == "completed"

    def test_unknown_status_returns_none(self):
        """Project with unknown status (not archived, not active) → None."""
        with patch("onemancompany.core.project_archive.load_project", return_value={"status": "draft"}):
            result = prod._resolve_task_status("proj_draft")
        assert result is None


# ---------------------------------------------------------------------------
# Additional edge-case tests for full coverage
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Export / Import
# ---------------------------------------------------------------------------


class TestProductExportImport:
    def test_export_product(self):
        """Export returns portable bundle with product, KRs, issues."""
        p = prod.create_product(name="ExportTest", owner_id="00004", description="test desc")
        prod.add_key_result(p["slug"], title="KR1", target=100, unit="users")
        prod.create_issue(slug=p["slug"], title="Issue1", created_by="ceo", priority=IssuePriority.P1)

        bundle = prod.export_product(p["slug"])
        assert bundle is not None
        assert bundle["format"] == "omc-product-v1"
        assert bundle["product"]["name"] == "ExportTest"
        assert bundle["product"]["description"] == "test desc"
        assert len(bundle["product"]["key_results"]) == 1
        assert bundle["product"]["key_results"][0]["title"] == "KR1"
        assert bundle["product"]["key_results"][0]["target"] == 100
        assert bundle["product"]["key_results"][0]["unit"] == "users"
        assert len(bundle["issues"]) == 1
        assert bundle["issues"][0]["title"] == "Issue1"

    def test_export_missing_product(self):
        assert prod.export_product("nonexistent") is None

    def test_import_product(self):
        bundle = {
            "format": "omc-product-v1",
            "product": {
                "name": "Imported Product",
                "description": "imported desc",
                "key_results": [
                    {"title": "KR1", "target": 100, "unit": "users"},
                    {"title": "KR2", "target": 50},
                ],
            },
            "issues": [
                {"title": "Issue A", "priority": "P0", "labels": ["urgent"]},
                {"title": "Issue B", "description": "desc B"},
            ],
        }
        result = prod.import_product(bundle, owner_id="00004", auto_activate=True)
        assert result["issues_created"] == 2
        assert result["krs_created"] == 2
        assert result["auto_activated"] is True

        # Verify created
        product = prod.load_product(result["slug"])
        assert product["name"] == "Imported Product"
        assert product["status"] == ProductStatus.ACTIVE
        assert len(product["key_results"]) == 2
        issues = prod.list_issues(result["slug"])
        assert len(issues) == 2

    def test_import_invalid_format(self):
        with pytest.raises(ValueError, match="Invalid format"):
            prod.import_product({"format": "wrong"})

    def test_import_no_name(self):
        with pytest.raises(ValueError, match="name"):
            prod.import_product({"format": "omc-product-v1", "product": {}})

    def test_import_planning_when_no_owner(self):
        bundle = {
            "format": "omc-product-v1",
            "product": {"name": "No Owner Product", "key_results": []},
            "issues": [],
        }
        result = prod.import_product(bundle, owner_id="", auto_activate=True)
        assert result["auto_activated"] is False
        product = prod.load_product(result["slug"])
        assert product["status"] == ProductStatus.PLANNING

    def test_roundtrip_export_import(self):
        """Export a product, then import it — the imported copy should match."""
        p = prod.create_product(name="RoundTrip", owner_id="00004", description="round trip test")
        prod.add_key_result(p["slug"], title="Users", target=500, unit="DAU")
        prod.create_issue(slug=p["slug"], title="Bug X", created_by="ceo", priority=IssuePriority.P1, labels=["bug"])
        prod.create_issue(slug=p["slug"], title="Feat Y", created_by="ceo", priority=IssuePriority.P2, story_points=3)

        bundle = prod.export_product(p["slug"])
        result = prod.import_product(bundle, owner_id="00010", auto_activate=False)
        assert result["issues_created"] == 2
        assert result["krs_created"] == 1

        imported = prod.load_product(result["slug"])
        assert imported["name"] == "RoundTrip"
        assert imported["description"] == "round trip test"
        assert len(imported["key_results"]) == 1
        assert imported["key_results"][0]["title"] == "Users"

    def test_import_invalid_priority_falls_back(self):
        """Invalid priority string falls back to P2."""
        bundle = {
            "format": "omc-product-v1",
            "product": {"name": "BadPrio", "key_results": []},
            "issues": [{"title": "Oops", "priority": "INVALID"}],
        }
        result = prod.import_product(bundle, owner_id="00004")
        issues = prod.list_issues(result["slug"])
        assert len(issues) == 1
        assert issues[0]["priority"] == IssuePriority.P2


class TestSlugifyEdgeCases:
    def test_long_name_truncated(self):
        """Line 59: slug longer than max_len gets truncated."""
        long_name = "a" * 100
        slug = prod._slugify(long_name, max_len=10)
        assert len(slug) <= 10

    def test_long_name_trailing_dash_stripped(self):
        """Line 59: trailing dash after truncation is stripped."""
        # Create a name that produces dashes near the cut point
        name = "hello-world-" + "x" * 50
        slug = prod._slugify(name, max_len=12)
        assert not slug.endswith("-")


class TestListProductsEdgeCases:
    def test_list_products_no_dir(self, tmp_path, monkeypatch):
        """Line 140: PRODUCTS_DIR doesn't exist → empty list."""
        monkeypatch.setattr(prod, "PRODUCTS_DIR", tmp_path / "nonexistent")
        assert prod.list_products() == []

    def test_list_products_skips_files(self, tmp_path):
        """Line 144: non-directory entries in PRODUCTS_DIR are skipped."""
        # Create a file (not a directory) in PRODUCTS_DIR
        (tmp_path / "not-a-dir.txt").write_text("junk")
        products = prod.list_products()
        assert products == []


class TestUpdateProductEdgeCases:
    def test_update_product_not_found(self):
        """Lines 159-160: updating a missing product returns None."""
        result = prod.update_product("no-such-slug", description="new")
        assert result is None


class TestKeyResultEdgeCases:
    def test_add_kr_product_not_found(self):
        """Lines 191-192: adding KR to missing product raises ValueError."""
        with pytest.raises(ValueError, match="Product no-such not found"):
            prod.add_key_result("no-such", title="KR", target=10)

    def test_update_kr_progress_product_not_found(self):
        """Line 211: updating KR progress on missing product raises ValueError."""
        with pytest.raises(ValueError, match="Product 'gone' not found"):
            prod.update_kr_progress("gone", "kr_xxx", current=5)

    def test_update_kr_fields_success(self):
        """Lines 231-249: update_kr_fields updates title, target, unit."""
        p = prod.create_product(name="KRFields", owner_id="00004")
        kr = prod.add_key_result(p["slug"], title="Old Title", target=100, unit="users")
        updated = prod.update_kr_fields(
            p["slug"], kr["id"], title="New Title", target=200, unit="DAU",
        )
        assert updated["title"] == "New Title"
        assert updated["target"] == 200
        assert updated["unit"] == "DAU"
        # history should record changes
        assert len(updated.get("history", [])) >= 3  # title, target, unit

    def test_update_kr_fields_product_not_found(self):
        """Lines 231-235: update_kr_fields on missing product raises ValueError."""
        with pytest.raises(ValueError, match="Product 'nope' not found"):
            prod.update_kr_fields("nope", "kr_xxx", title="X")

    def test_update_kr_fields_kr_not_found(self):
        """Lines 248-249: update_kr_fields with unknown kr_id raises ValueError."""
        p = prod.create_product(name="KRFieldsMiss", owner_id="00004")
        with pytest.raises(ValueError, match="KR 'kr_bad' not found"):
            prod.update_kr_fields(p["slug"], "kr_bad", title="X")


class TestIssueEdgeCases:
    def test_create_issue_no_product(self):
        """create_issue with missing product raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            prod.create_issue(slug="ghost", title="Orphan", created_by="ceo")

    def test_list_issues_skips_non_yaml(self, tmp_path):
        """Line 340: non-yaml files in issues dir are skipped."""
        p = prod.create_product(name="NonYaml", owner_id="00004")
        issues_dir = tmp_path / p["slug"] / "issues"
        issues_dir.mkdir(parents=True, exist_ok=True)
        (issues_dir / "readme.txt").write_text("not yaml")
        issues = prod.list_issues(p["slug"])
        assert issues == []

    def test_list_issues_skips_empty_yaml(self, tmp_path):
        """Line 343: empty yaml files are skipped."""
        p = prod.create_product(name="EmptyYaml", owner_id="00004")
        issues_dir = tmp_path / p["slug"] / "issues"
        issues_dir.mkdir(parents=True, exist_ok=True)
        (issues_dir / "empty.yaml").write_text("")
        issues = prod.list_issues(p["slug"])
        assert issues == []

    def test_list_issues_filter_by_status(self):
        """Line 346: status filter excludes non-matching issues."""
        p = prod.create_product(name="StatusFilter", owner_id="00004")
        prod.create_issue(slug=p["slug"], title="Open", created_by="ceo")
        i2 = prod.create_issue(slug=p["slug"], title="Closed", created_by="ceo")
        prod.close_issue(p["slug"], i2["id"])
        backlog = prod.list_issues(p["slug"], status=IssueStatus.BACKLOG)
        assert len(backlog) == 1
        assert backlog[0]["title"] == "Open"

    def test_update_issue_not_found(self):
        """updating missing issue raises ValueError."""
        p = prod.create_product(name="UpdateMiss", owner_id="00004")
        with pytest.raises(ValueError, match="not found"):
            prod.update_issue(p["slug"], "issue_nope", title="x")

    def test_close_issue_not_found(self):
        """closing missing issue raises ValueError."""
        p = prod.create_product(name="CloseMiss", owner_id="00004")
        with pytest.raises(ValueError, match="not found"):
            prod.close_issue(p["slug"], "issue_gone")

    def test_reopen_issue_not_found(self):
        """reopening missing issue raises ValueError."""
        p = prod.create_product(name="ReopenMiss", owner_id="00004")
        with pytest.raises(ValueError, match="not found"):
            prod.reopen_issue(p["slug"], "issue_vanish")


class TestAppendHistory:
    def test_history_capped_at_100(self):
        """Line 266: history list is capped at 100 entries."""
        data = {"history": [{"field": f"f{i}"} for i in range(105)]}
        prod._append_history(data, "new_field", "old", "new")
        assert len(data["history"]) == 100
        # The last entry should be our new one
        assert data["history"][-1]["field"] == "new_field"


class TestVersionEdgeCases:
    def test_list_versions_empty(self):
        """Lines 430-432: no versions dir → empty list."""
        p = prod.create_product(name="NoVer", owner_id="00004")
        versions = prod.list_versions(p["slug"])
        assert versions == []

    def test_list_versions_returns_versions(self):
        """Lines 430-437: list_versions returns version records."""
        p = prod.create_product(name="HasVer", owner_id="00004")
        i1 = prod.create_issue(slug=p["slug"], title="Fix", created_by="ceo")
        prod.close_issue(p["slug"], i1["id"])
        prod.release_version(p["slug"], [i1["id"]])
        versions = prod.list_versions(p["slug"])
        assert len(versions) == 1
        assert versions[0]["version"] == "0.1.1"

    def test_release_version_product_not_found(self):
        """Line 472: releasing version on missing product raises ValueError."""
        with pytest.raises(ValueError, match="Product 'phantom' not found"):
            prod.release_version("phantom", [])

    def test_release_version_marks_issues_as_released(self):
        """Line 534: release_version marks resolved issues as RELEASED."""
        p = prod.create_product(name="RelMark", owner_id="00004")
        i1 = prod.create_issue(slug=p["slug"], title="Done Bug", created_by="ceo")
        prod.close_issue(p["slug"], i1["id"])
        prod.release_version(p["slug"], [i1["id"]])
        loaded = prod.load_issue(p["slug"], i1["id"])
        assert loaded["status"] == IssueStatus.RELEASED.value


class TestBuildProductContextEdgeCases:
    def test_context_with_unit_field(self):
        """Line 524-526: KR with unit field renders correctly."""
        p = prod.create_product(name="UnitCtx", owner_id="00004")
        prod.add_key_result(p["slug"], title="Revenue", target=1000, unit="USD")
        ctx = prod.build_product_context(p["slug"])
        assert "USD" in ctx
        assert "0/1000 USD" in ctx

    def test_context_with_empty_krs(self):
        """No KRs: context should not contain 'Key Results' section."""
        p = prod.create_product(name="NoKR", owner_id="00004")
        ctx = prod.build_product_context(p["slug"])
        assert "Key Results" not in ctx

    def test_context_more_than_10_issues(self):
        """Line 534: >10 issues shows '... and N more'."""
        p = prod.create_product(name="ManyIssues", owner_id="00004")
        for i in range(12):
            prod.create_issue(slug=p["slug"], title=f"Issue {i}", created_by="ceo")
        ctx = prod.build_product_context(p["slug"])
        assert "and 2 more" in ctx


# ---------------------------------------------------------------------------
# Delete Product
# ---------------------------------------------------------------------------


class TestDeleteProduct:
    def test_delete_product(self):
        p = prod.create_product(name="ToDelete", owner_id="00004")
        prod.create_issue(slug=p["slug"], title="Issue1", created_by="ceo")
        assert prod.load_product(p["slug"]) is not None

        result = prod.delete_product(p["slug"])
        assert result["deleted"] is True
        assert result["issues_deleted"] == 1
        assert prod.load_product(p["slug"]) is None
        assert prod.list_issues(p["slug"]) == []

    def test_delete_nonexistent(self):
        with pytest.raises(ValueError, match="not found"):
            prod.delete_product("nonexistent")

    def test_delete_cleans_linked_projects(self, tmp_path, monkeypatch):
        """Deleting a product also removes linked projects."""
        from unittest.mock import patch, MagicMock
        p = prod.create_product(name="WithProjects", owner_id="00004")
        product_id = p["id"]

        # Create a fake project dir linked to this product
        from onemancompany.core.config import PROJECTS_DIR
        fake_proj_dir = PROJECTS_DIR / "fake-proj-123"
        fake_proj_dir.mkdir(parents=True, exist_ok=True)
        (fake_proj_dir / "project.yaml").write_text("test: true")

        with patch("onemancompany.core.project_archive.list_projects", return_value=[
            {"project_id": "fake-proj-123", "product_id": product_id, "status": "active"},
        ]):
            with patch("onemancompany.core.agent_loop.employee_manager") as mock_em:
                mock_em.abort_project = MagicMock()
                result = prod.delete_product(p["slug"])

        assert result["projects_deleted"] == 1
        assert not fake_proj_dir.exists()


# ---------------------------------------------------------------------------
# Sprint CRUD
# ---------------------------------------------------------------------------


class TestSprintCRUD:
    def test_create_sprint(self):
        p = prod.create_product(name="SprintProd", owner_id="00010")
        s = prod.create_sprint(
            slug=p["slug"],
            name="Sprint 1",
            goal="Build MVP",
            start_date="2026-04-21",
            end_date="2026-05-05",
        )
        assert s["id"].startswith("sprint_")
        assert s["name"] == "Sprint 1"
        assert s["goal"] == "Build MVP"
        assert s["status"] == SprintStatus.PLANNING.value
        assert s["start_date"] == "2026-04-21"
        assert s["end_date"] == "2026-05-05"
        assert s["velocity"] is None
        assert s["capacity"] is None

    def test_load_sprint(self):
        p = prod.create_product(name="LoadSprint", owner_id="00010")
        s = prod.create_sprint(slug=p["slug"], name="S1", start_date="2026-04-21", end_date="2026-05-05")
        loaded = prod.load_sprint(p["slug"], s["id"])
        assert loaded["id"] == s["id"]
        assert loaded["name"] == "S1"

    def test_list_sprints(self):
        p = prod.create_product(name="ListSprint", owner_id="00010")
        prod.create_sprint(slug=p["slug"], name="S1", start_date="2026-04-01", end_date="2026-04-15")
        prod.create_sprint(slug=p["slug"], name="S2", start_date="2026-04-16", end_date="2026-04-30")
        sprints = prod.list_sprints(p["slug"])
        assert len(sprints) == 2

    def test_list_sprints_filter_by_status(self):
        p = prod.create_product(name="FilterSprint", owner_id="00010")
        s1 = prod.create_sprint(slug=p["slug"], name="S1", start_date="2026-04-01", end_date="2026-04-15")
        prod.update_sprint(p["slug"], s1["id"], status=SprintStatus.ACTIVE.value)
        prod.create_sprint(slug=p["slug"], name="S2", start_date="2026-04-16", end_date="2026-04-30")
        active = prod.list_sprints(p["slug"], status=SprintStatus.ACTIVE.value)
        assert len(active) == 1
        assert active[0]["name"] == "S1"

    def test_update_sprint(self):
        p = prod.create_product(name="UpdateSprint", owner_id="00010")
        s = prod.create_sprint(slug=p["slug"], name="S1", start_date="2026-04-01", end_date="2026-04-15")
        updated = prod.update_sprint(p["slug"], s["id"], name="Sprint Alpha", capacity=20)
        assert updated["name"] == "Sprint Alpha"
        assert updated["capacity"] == 20

    def test_load_sprint_not_found(self):
        p = prod.create_product(name="NoSprint", owner_id="00010")
        assert prod.load_sprint(p["slug"], "sprint_nonexist") is None


class TestActiveSprint:
    def test_get_active_sprint_none(self):
        p = prod.create_product(name="NoActive", owner_id="00010")
        assert prod.get_active_sprint(p["slug"]) is None

    def test_get_active_sprint(self):
        p = prod.create_product(name="HasActive", owner_id="00010")
        s = prod.create_sprint(slug=p["slug"], name="S1", start_date="2026-04-01", end_date="2026-04-15")
        prod.update_sprint(p["slug"], s["id"], status=SprintStatus.ACTIVE.value)
        active = prod.get_active_sprint(p["slug"])
        assert active["id"] == s["id"]

    def test_only_one_active_sprint(self):
        """Activating a sprint when one is already active raises."""
        p = prod.create_product(name="OneActive", owner_id="00010")
        s1 = prod.create_sprint(slug=p["slug"], name="S1", start_date="2026-04-01", end_date="2026-04-15")
        prod.update_sprint(p["slug"], s1["id"], status=SprintStatus.ACTIVE.value)
        s2 = prod.create_sprint(slug=p["slug"], name="S2", start_date="2026-04-16", end_date="2026-04-30")
        with pytest.raises(ValueError, match="already has an active sprint"):
            prod.update_sprint(p["slug"], s2["id"], status=SprintStatus.ACTIVE.value)


class TestCloseSprint:
    def _setup_sprint_with_issues(self):
        """Helper: product with active sprint and issues."""
        p = prod.create_product(name="CloseProd", owner_id="00010")
        slug = p["slug"]
        s = prod.create_sprint(slug=slug, name="S1", start_date="2026-04-01", end_date="2026-04-15")
        prod.update_sprint(slug, s["id"], status=SprintStatus.ACTIVE.value)
        # Create issues in this sprint
        i1 = prod.create_issue(slug=slug, title="Done task", created_by="00010", story_points=5, sprint=s["id"])
        i2 = prod.create_issue(slug=slug, title="Also done", created_by="00010", story_points=3, sprint=s["id"])
        i3 = prod.create_issue(slug=slug, title="Not done", created_by="00010", story_points=8, sprint=s["id"])
        # Close first two
        prod.close_issue(slug, i1["id"], resolution=IssueResolution.FIXED)
        prod.close_issue(slug, i2["id"], resolution=IssueResolution.FIXED)
        return slug, s["id"], i1["id"], i2["id"], i3["id"]

    def test_close_sprint_velocity(self):
        slug, sprint_id, _, _, _ = self._setup_sprint_with_issues()
        result = prod.close_sprint(slug, sprint_id)
        assert result["velocity"] == 8  # 5 + 3
        assert result["status"] == SprintStatus.CLOSED.value

    def test_close_sprint_completion_rate(self):
        slug, sprint_id, _, _, _ = self._setup_sprint_with_issues()
        result = prod.close_sprint(slug, sprint_id)
        # 2 done out of 3
        assert abs(result["completion_rate"] - 66.67) < 1

    def test_close_sprint_carry_over_to_backlog(self):
        """Unfinished issues go to backlog when no next sprint exists."""
        slug, sprint_id, _, _, i3_id = self._setup_sprint_with_issues()
        prod.close_sprint(slug, sprint_id)
        issue3 = prod.load_issue(slug, i3_id)
        assert issue3["sprint"] == ""
        assert issue3["status"] == IssueStatus.BACKLOG.value
        assert issue3.get("carried_over") is True

    def test_close_sprint_carry_over_to_next(self):
        """Unfinished issues move to next planning sprint."""
        slug, sprint_id, _, _, i3_id = self._setup_sprint_with_issues()
        # Create next sprint in planning
        next_s = prod.create_sprint(slug=slug, name="S2", start_date="2026-04-16", end_date="2026-04-30")
        prod.close_sprint(slug, sprint_id)
        issue3 = prod.load_issue(slug, i3_id)
        assert issue3["sprint"] == next_s["id"]
        assert issue3.get("carried_over") is True

    def test_close_sprint_carry_over_count(self):
        slug, sprint_id, _, _, _ = self._setup_sprint_with_issues()
        result = prod.close_sprint(slug, sprint_id)
        assert result["carry_over_count"] == 1

    def test_close_sprint_retrospective_generated(self):
        slug, sprint_id, _, _, _ = self._setup_sprint_with_issues()
        result = prod.close_sprint(slug, sprint_id)
        assert result["retrospective"] is not None
        assert "velocity" in result["retrospective"].lower() or "完成" in result["retrospective"]

    def test_close_sprint_sets_closed_at(self):
        slug, sprint_id, _, _, _ = self._setup_sprint_with_issues()
        result = prod.close_sprint(slug, sprint_id)
        assert result["closed_at"] is not None

    def test_close_already_closed_raises(self):
        slug, sprint_id, _, _, _ = self._setup_sprint_with_issues()
        prod.close_sprint(slug, sprint_id)
        with pytest.raises(ValueError, match="not active"):
            prod.close_sprint(slug, sprint_id)


class TestSprintVelocity:
    def test_velocity_only_counts_done_issues(self):
        p = prod.create_product(name="VelProd", owner_id="00010")
        slug = p["slug"]
        s = prod.create_sprint(slug=slug, name="S1", start_date="2026-04-01", end_date="2026-04-15")
        prod.update_sprint(slug, s["id"], status=SprintStatus.ACTIVE.value)
        i1 = prod.create_issue(slug=slug, title="Done", created_by="00010", story_points=5, sprint=s["id"])
        prod.create_issue(slug=slug, title="Open", created_by="00010", story_points=10, sprint=s["id"])
        prod.close_issue(slug, i1["id"], resolution=IssueResolution.FIXED)
        vel = prod.get_sprint_velocity(slug, s["id"])
        assert vel == 5

    def test_velocity_zero_when_no_story_points(self):
        p = prod.create_product(name="VelZero", owner_id="00010")
        slug = p["slug"]
        s = prod.create_sprint(slug=slug, name="S1", start_date="2026-04-01", end_date="2026-04-15")
        prod.update_sprint(slug, s["id"], status=SprintStatus.ACTIVE.value)
        i1 = prod.create_issue(slug=slug, title="Done no pts", created_by="00010", sprint=s["id"])
        prod.close_issue(slug, i1["id"], resolution=IssueResolution.FIXED)
        vel = prod.get_sprint_velocity(slug, s["id"])
        assert vel == 0


class TestSuggestCapacity:
    def test_no_history_returns_none(self):
        p = prod.create_product(name="NoHist", owner_id="00010")
        assert prod.suggest_capacity(p["slug"]) is None

    def test_fewer_than_3_returns_none(self):
        p = prod.create_product(name="TwoHist", owner_id="00010")
        slug = p["slug"]
        for i in range(2):
            s = prod.create_sprint(slug=slug, name=f"S{i}", start_date="2026-04-01", end_date="2026-04-15")
            prod.update_sprint(slug, s["id"], status=SprintStatus.ACTIVE.value)
            prod.close_sprint(slug, s["id"])
        assert prod.suggest_capacity(slug) is None

    def test_sliding_average_with_3_sprints(self):
        p = prod.create_product(name="ThreeHist", owner_id="00010")
        slug = p["slug"]
        velocities = [10, 20, 30]
        for i, v in enumerate(velocities):
            s = prod.create_sprint(slug=slug, name=f"S{i}", start_date="2026-04-01", end_date="2026-04-15")
            prod.update_sprint(slug, s["id"], status=SprintStatus.ACTIVE.value)
            # Create an issue with story_points = v, close it
            issue = prod.create_issue(slug=slug, title=f"T{i}", created_by="00010", story_points=v, sprint=s["id"])
            prod.close_issue(slug, issue["id"], resolution=IssueResolution.FIXED)
            prod.close_sprint(slug, s["id"])
        suggested = prod.suggest_capacity(slug)
        assert suggested == 20  # average of 10, 20, 30


class TestSprintRetrospective:
    def test_retrospective_content(self):
        p = prod.create_product(name="RetroProd", owner_id="00010")
        slug = p["slug"]
        s = prod.create_sprint(slug=slug, name="S1", goal="Build MVP", start_date="2026-04-01", end_date="2026-04-15")
        prod.update_sprint(slug, s["id"], status=SprintStatus.ACTIVE.value)
        i1 = prod.create_issue(slug=slug, title="Task A", created_by="00010", story_points=5, sprint=s["id"])
        prod.close_issue(slug, i1["id"], resolution=IssueResolution.FIXED)
        retro = prod.build_sprint_retrospective(slug, s["id"])
        assert "Sprint 1" in retro or "S1" in retro
        assert "velocity" in retro.lower() or "5" in retro

    def test_retrospective_with_carry_over(self):
        """Retrospective includes carried-over issues section."""
        p = prod.create_product(name="RetroCarry", owner_id="00010")
        slug = p["slug"]
        s = prod.create_sprint(slug=slug, name="S1", start_date="2026-04-01", end_date="2026-04-15")
        prod.update_sprint(slug, s["id"], status=SprintStatus.ACTIVE.value)
        prod.create_issue(slug=slug, title="Unfinished work", created_by="00010", story_points=3, sprint=s["id"])
        retro = prod.build_sprint_retrospective(slug, s["id"])
        assert "Carried Over" in retro
        assert "Unfinished work" in retro

    def test_retrospective_nonexistent_sprint(self):
        """build_sprint_retrospective returns empty string for missing sprint."""
        p = prod.create_product(name="RetroNone", owner_id="00010")
        assert prod.build_sprint_retrospective(p["slug"], "sprint_nonexist") == ""


class TestSprintErrorPaths:
    def test_create_sprint_product_not_found(self):
        with pytest.raises(ValueError, match="not found"):
            prod.create_sprint(slug="nonexist", name="S1", start_date="2026-04-01", end_date="2026-04-15")

    def test_update_sprint_not_found(self):
        p = prod.create_product(name="UpdErr", owner_id="00010")
        with pytest.raises(ValueError, match="not found"):
            prod.update_sprint(p["slug"], "sprint_nonexist", name="X")

    def test_close_sprint_not_found(self):
        p = prod.create_product(name="CloseErr", owner_id="00010")
        with pytest.raises(ValueError, match="not found"):
            prod.close_sprint(p["slug"], "sprint_nonexist")


# ---------------------------------------------------------------------------
# Issue Links
# ---------------------------------------------------------------------------


class TestAddIssueLink:
    def test_add_blocks_link(self):
        """Adding a 'blocks' link creates bidirectional entries."""
        p = prod.create_product(name="LinkProd", owner_id="00010")
        slug = p["slug"]
        i1 = prod.create_issue(slug=slug, title="Blocker", created_by="ceo")
        i2 = prod.create_issue(slug=slug, title="Blocked", created_by="ceo")

        prod.add_issue_link(slug, i1["id"], i2["id"], IssueRelation.BLOCKS)

        # i1 should have blocks→i2
        links1 = prod.get_issue_links(slug, i1["id"])
        assert len(links1) == 1
        assert links1[0]["issue_id"] == i2["id"]
        assert links1[0]["relation"] == IssueRelation.BLOCKS.value
        assert "created_at" in links1[0]

        # i2 should have blocked_by→i1
        links2 = prod.get_issue_links(slug, i2["id"])
        assert len(links2) == 1
        assert links2[0]["issue_id"] == i1["id"]
        assert links2[0]["relation"] == IssueRelation.BLOCKED_BY.value

    def test_add_relates_to_link(self):
        """relates_to is symmetric — both sides get relates_to."""
        p = prod.create_product(name="RelateProd", owner_id="00010")
        slug = p["slug"]
        i1 = prod.create_issue(slug=slug, title="A", created_by="ceo")
        i2 = prod.create_issue(slug=slug, title="B", created_by="ceo")

        prod.add_issue_link(slug, i1["id"], i2["id"], IssueRelation.RELATES_TO)

        links1 = prod.get_issue_links(slug, i1["id"])
        links2 = prod.get_issue_links(slug, i2["id"])
        assert links1[0]["relation"] == IssueRelation.RELATES_TO.value
        assert links2[0]["relation"] == IssueRelation.RELATES_TO.value

    def test_add_blocked_by_creates_reverse_blocks(self):
        """Adding blocked_by on A→B creates blocks on B→A."""
        p = prod.create_product(name="RevLink", owner_id="00010")
        slug = p["slug"]
        i1 = prod.create_issue(slug=slug, title="A", created_by="ceo")
        i2 = prod.create_issue(slug=slug, title="B", created_by="ceo")

        prod.add_issue_link(slug, i1["id"], i2["id"], IssueRelation.BLOCKED_BY)

        links1 = prod.get_issue_links(slug, i1["id"])
        assert links1[0]["relation"] == IssueRelation.BLOCKED_BY.value
        links2 = prod.get_issue_links(slug, i2["id"])
        assert links2[0]["relation"] == IssueRelation.BLOCKS.value

    def test_idempotent_add(self):
        """Adding the same link twice doesn't duplicate."""
        p = prod.create_product(name="IdempotLink", owner_id="00010")
        slug = p["slug"]
        i1 = prod.create_issue(slug=slug, title="A", created_by="ceo")
        i2 = prod.create_issue(slug=slug, title="B", created_by="ceo")

        prod.add_issue_link(slug, i1["id"], i2["id"], IssueRelation.BLOCKS)
        prod.add_issue_link(slug, i1["id"], i2["id"], IssueRelation.BLOCKS)

        links = prod.get_issue_links(slug, i1["id"])
        assert len(links) == 1

    def test_self_reference_rejected(self):
        """Cannot link an issue to itself."""
        p = prod.create_product(name="SelfRef", owner_id="00010")
        slug = p["slug"]
        i1 = prod.create_issue(slug=slug, title="A", created_by="ceo")

        with pytest.raises(ValueError, match="self"):
            prod.add_issue_link(slug, i1["id"], i1["id"], IssueRelation.BLOCKS)

    def test_add_link_source_not_found(self):
        """Linking from a nonexistent source issue raises."""
        p = prod.create_product(name="MissLink", owner_id="00010")
        slug = p["slug"]
        i1 = prod.create_issue(slug=slug, title="A", created_by="ceo")

        with pytest.raises(ValueError, match="not found"):
            prod.add_issue_link(slug, "issue_nonexist", i1["id"], IssueRelation.BLOCKS)

    def test_add_link_target_not_found(self):
        """Linking to a nonexistent target issue raises."""
        p = prod.create_product(name="MissLink2", owner_id="00010")
        slug = p["slug"]
        i1 = prod.create_issue(slug=slug, title="A", created_by="ceo")

        with pytest.raises(ValueError, match="not found"):
            prod.add_issue_link(slug, i1["id"], "issue_nonexist", IssueRelation.BLOCKS)


class TestRemoveIssueLink:
    def test_remove_link(self):
        """Removing a link deletes both sides."""
        p = prod.create_product(name="RmLink", owner_id="00010")
        slug = p["slug"]
        i1 = prod.create_issue(slug=slug, title="A", created_by="ceo")
        i2 = prod.create_issue(slug=slug, title="B", created_by="ceo")

        prod.add_issue_link(slug, i1["id"], i2["id"], IssueRelation.BLOCKS)
        prod.remove_issue_link(slug, i1["id"], i2["id"])

        assert prod.get_issue_links(slug, i1["id"]) == []
        assert prod.get_issue_links(slug, i2["id"]) == []

    def test_remove_nonexistent_silently_ignored(self):
        """Removing a link that doesn't exist does nothing."""
        p = prod.create_product(name="RmNone", owner_id="00010")
        slug = p["slug"]
        i1 = prod.create_issue(slug=slug, title="A", created_by="ceo")
        i2 = prod.create_issue(slug=slug, title="B", created_by="ceo")

        # Should not raise
        prod.remove_issue_link(slug, i1["id"], i2["id"])
        assert prod.get_issue_links(slug, i1["id"]) == []

    def test_remove_link_missing_issue_file(self):
        """Removing a link when issue file is missing doesn't crash."""
        p = prod.create_product(name="RmMiss", owner_id="00010")
        slug = p["slug"]
        # Remove link on nonexistent issue IDs — should silently skip
        prod.remove_issue_link(slug, "issue_ghost1", "issue_ghost2")

    def test_get_issue_links_missing_issue(self):
        """get_issue_links returns [] for nonexistent issue."""
        p = prod.create_product(name="LinkMiss", owner_id="00010")
        assert prod.get_issue_links(p["slug"], "issue_nope") == []

    def test_add_link_entry_missing_issue_file(self):
        """_add_link_entry silently skips when issue yaml doesn't exist."""
        p = prod.create_product(name="AddMiss", owner_id="00010")
        slug = p["slug"]
        # Calling internal _add_link_entry on nonexistent issue — should not crash
        prod._add_link_entry(slug, "issue_ghost", "issue_target", IssueRelation.BLOCKS.value)


class TestIsBlocked:
    def test_blocked_by_open_issue(self):
        """Issue is blocked when it has a blocked_by link to an undone issue."""
        p = prod.create_product(name="BlockProd", owner_id="00010")
        slug = p["slug"]
        i1 = prod.create_issue(slug=slug, title="Blocker", created_by="ceo")
        i2 = prod.create_issue(slug=slug, title="Blocked", created_by="ceo")

        prod.add_issue_link(slug, i2["id"], i1["id"], IssueRelation.BLOCKED_BY)
        assert prod.is_blocked(slug, i2["id"]) is True

    def test_not_blocked_when_blocker_done(self):
        """Issue is not blocked when all blockers are done."""
        p = prod.create_product(name="UnblockProd", owner_id="00010")
        slug = p["slug"]
        i1 = prod.create_issue(slug=slug, title="Blocker", created_by="ceo")
        i2 = prod.create_issue(slug=slug, title="Blocked", created_by="ceo")

        prod.add_issue_link(slug, i2["id"], i1["id"], IssueRelation.BLOCKED_BY)
        prod.close_issue(slug, i1["id"], resolution=IssueResolution.FIXED)
        assert prod.is_blocked(slug, i2["id"]) is False

    def test_not_blocked_without_links(self):
        """Issue with no links is not blocked."""
        p = prod.create_product(name="NoLinkProd", owner_id="00010")
        slug = p["slug"]
        i1 = prod.create_issue(slug=slug, title="Free", created_by="ceo")
        assert prod.is_blocked(slug, i1["id"]) is False

    def test_is_blocked_issue_not_found(self):
        """is_blocked for nonexistent issue returns False."""
        p = prod.create_product(name="GhostBlock", owner_id="00010")
        assert prod.is_blocked(p["slug"], "issue_none") is False


class TestLoadIssueMigration:
    def test_migrate_linked_issue_ids_to_issue_links(self):
        """Old format linked_issue_ids auto-migrates to issue_links on load."""
        p = prod.create_product(name="MigProd", owner_id="00010")
        slug = p["slug"]
        i1 = prod.create_issue(slug=slug, title="A", created_by="ceo")
        # Manually write old format
        from onemancompany.core.store import _read_yaml, _write_yaml
        path = prod._issues_dir(slug) / f"{i1['id']}.yaml"
        data = _read_yaml(path)
        data["linked_issue_ids"] = ["issue_other1", "issue_other2"]
        if "issue_links" in data:
            del data["issue_links"]
        _write_yaml(path, data)

        # Now load — should auto-migrate
        loaded = prod.load_issue(slug, i1["id"])
        assert "issue_links" in loaded
        assert len(loaded["issue_links"]) == 2
        assert loaded["issue_links"][0]["relation"] == IssueRelation.RELATES_TO.value
        # Old field should be removed
        assert "linked_issue_ids" not in loaded


# ---------------------------------------------------------------------------
# Review Checklist
# ---------------------------------------------------------------------------


class TestReviewCRUD:
    def test_create_review(self):
        p = prod.create_product(name="ReviewProd", owner_id="00010")
        slug = p["slug"]
        review = prod.create_review(
            slug=slug,
            trigger="sprint_closed",
            trigger_ref="sprint_1",
            owner="00010",
        )
        assert review["id"].startswith("rev_")
        assert review["status"] == "open"
        assert review["trigger"] == "sprint_closed"
        assert review["trigger_ref"] == "sprint_1"
        assert review["owner"] == "00010"
        assert len(review["items"]) > 0  # default checklist items
        assert all(not item["checked"] for item in review["items"])

    def test_load_review(self):
        p = prod.create_product(name="LoadRev", owner_id="00010")
        slug = p["slug"]
        review = prod.create_review(slug=slug, trigger="manual", owner="00010")
        loaded = prod.load_review(slug, review["id"])
        assert loaded["id"] == review["id"]

    def test_load_review_not_found(self):
        p = prod.create_product(name="NoRev", owner_id="00010")
        assert prod.load_review(p["slug"], "rev_nonexist") is None

    def test_list_reviews(self):
        p = prod.create_product(name="ListRev", owner_id="00010")
        slug = p["slug"]
        prod.create_review(slug=slug, trigger="a", owner="00010")
        prod.create_review(slug=slug, trigger="b", owner="00010")
        reviews = prod.list_reviews(slug)
        assert len(reviews) == 2

    def test_list_reviews_filter_by_status(self):
        p = prod.create_product(name="FilterRev", owner_id="00010")
        slug = p["slug"]
        r1 = prod.create_review(slug=slug, trigger="a", owner="00010")
        prod.create_review(slug=slug, trigger="b", owner="00010")
        # Complete first review
        for item in r1["items"]:
            prod.update_review_item(slug, r1["id"], item["key"], checked=True)
        prod.complete_review(slug, r1["id"])
        open_reviews = prod.list_reviews(slug, status="open")
        assert len(open_reviews) == 1

    def test_list_reviews_empty(self):
        p = prod.create_product(name="EmptyRev", owner_id="00010")
        assert prod.list_reviews(p["slug"]) == []


class TestReviewItemUpdate:
    def test_check_item(self):
        p = prod.create_product(name="CheckItem", owner_id="00010")
        slug = p["slug"]
        review = prod.create_review(slug=slug, trigger="test", owner="00010")
        first_key = review["items"][0]["key"]

        updated = prod.update_review_item(slug, review["id"], first_key, checked=True)
        checked_item = next(i for i in updated["items"] if i["key"] == first_key)
        assert checked_item["checked"] is True

    def test_uncheck_item(self):
        p = prod.create_product(name="UncheckItem", owner_id="00010")
        slug = p["slug"]
        review = prod.create_review(slug=slug, trigger="test", owner="00010")
        first_key = review["items"][0]["key"]

        prod.update_review_item(slug, review["id"], first_key, checked=True)
        updated = prod.update_review_item(slug, review["id"], first_key, checked=False)
        checked_item = next(i for i in updated["items"] if i["key"] == first_key)
        assert checked_item["checked"] is False

    def test_update_item_review_not_found(self):
        p = prod.create_product(name="NoRevItem", owner_id="00010")
        with pytest.raises(ValueError, match="not found"):
            prod.update_review_item(p["slug"], "rev_none", "key", checked=True)

    def test_update_item_key_not_found(self):
        p = prod.create_product(name="BadKeyRev", owner_id="00010")
        slug = p["slug"]
        review = prod.create_review(slug=slug, trigger="test", owner="00010")
        with pytest.raises(ValueError, match="not found"):
            prod.update_review_item(slug, review["id"], "bad_key", checked=True)


class TestCompleteReview:
    def test_complete_all_checked(self):
        p = prod.create_product(name="CompRev", owner_id="00010")
        slug = p["slug"]
        review = prod.create_review(slug=slug, trigger="test", owner="00010")
        for item in review["items"]:
            prod.update_review_item(slug, review["id"], item["key"], checked=True)
        completed = prod.complete_review(slug, review["id"])
        assert completed["status"] == "completed"
        assert completed["completed_at"] is not None

    def test_complete_with_unchecked_raises(self):
        p = prod.create_product(name="IncomplRev", owner_id="00010")
        slug = p["slug"]
        review = prod.create_review(slug=slug, trigger="test", owner="00010")
        with pytest.raises(ValueError, match="unchecked"):
            prod.complete_review(slug, review["id"])

    def test_complete_review_not_found(self):
        p = prod.create_product(name="NoCompRev", owner_id="00010")
        with pytest.raises(ValueError, match="not found"):
            prod.complete_review(p["slug"], "rev_none")

    def test_complete_already_completed_raises(self):
        p = prod.create_product(name="DupComp", owner_id="00010")
        slug = p["slug"]
        review = prod.create_review(slug=slug, trigger="test", owner="00010")
        for item in review["items"]:
            prod.update_review_item(slug, review["id"], item["key"], checked=True)
        prod.complete_review(slug, review["id"])
        with pytest.raises(ValueError, match="already completed"):
            prod.complete_review(slug, review["id"])


# ---------------------------------------------------------------------------
# Kanban Board
# ---------------------------------------------------------------------------


class TestKanbanBoard:
    def test_kanban_groups_by_status(self):
        """kanban_board returns issues grouped by IssueStatus columns."""
        p = prod.create_product(name="KanbanProd", owner_id="00010")
        slug = p["slug"]
        prod.create_issue(slug=slug, title="Backlog1", created_by="ceo")
        i2 = prod.create_issue(slug=slug, title="InProg1", created_by="ceo")
        prod.update_issue(slug, i2["id"], status=IssueStatus.IN_PROGRESS.value)
        i3 = prod.create_issue(slug=slug, title="Done1", created_by="ceo")
        prod.update_issue(slug, i3["id"], status=IssueStatus.IN_PROGRESS.value)
        prod.update_issue(slug, i3["id"], status=IssueStatus.DONE.value)

        board = prod.kanban_board(slug)
        assert "backlog" in board["columns"]
        assert "in_progress" in board["columns"]
        assert "done" in board["columns"]
        assert len(board["columns"]["backlog"]) == 1
        assert len(board["columns"]["in_progress"]) == 1
        assert len(board["columns"]["done"]) == 1
        assert board["columns"]["backlog"][0]["title"] == "Backlog1"

    def test_kanban_includes_blocked_ids(self):
        """kanban_board marks blocked issue IDs."""
        p = prod.create_product(name="KanbanBlocked", owner_id="00010")
        slug = p["slug"]
        blocker = prod.create_issue(slug=slug, title="Blocker", created_by="ceo")
        blocked = prod.create_issue(slug=slug, title="Blocked", created_by="ceo")
        prod.add_issue_link(slug, blocked["id"], blocker["id"], IssueRelation.BLOCKED_BY)

        board = prod.kanban_board(slug)
        assert blocked["id"] in board["blocked_ids"]
        assert blocker["id"] not in board["blocked_ids"]

    def test_kanban_empty_product(self):
        """kanban_board on product with no issues returns empty columns."""
        p = prod.create_product(name="KanbanEmpty", owner_id="00010")
        board = prod.kanban_board(p["slug"])
        for col in board["columns"].values():
            assert col == []

    def test_kanban_product_not_found(self):
        """kanban_board raises ValueError for missing product."""
        with pytest.raises(ValueError, match="not found"):
            prod.kanban_board("nonexistent")


# ---------------------------------------------------------------------------
# Roadmap Timeline
# ---------------------------------------------------------------------------


class TestRoadmapTimeline:
    def test_roadmap_includes_sprints(self):
        """roadmap_timeline returns sprint date ranges."""
        p = prod.create_product(name="RoadmapProd", owner_id="00010")
        slug = p["slug"]
        s = prod.create_sprint(slug=slug, name="S1", start_date="2026-04-01", end_date="2026-04-14")

        roadmap = prod.roadmap_timeline(slug)
        assert len(roadmap["sprints"]) == 1
        assert roadmap["sprints"][0]["name"] == "S1"
        assert roadmap["sprints"][0]["start_date"] == "2026-04-01"

    def test_roadmap_includes_versions(self):
        """roadmap_timeline returns released versions."""
        p = prod.create_product(name="RoadmapVer", owner_id="00010")
        slug = p["slug"]
        i1 = prod.create_issue(slug=slug, title="Fix", created_by="ceo")
        prod.update_issue(slug, i1["id"], status=IssueStatus.IN_PROGRESS.value)
        prod.update_issue(slug, i1["id"], status=IssueStatus.DONE.value)
        prod.release_version(slug, resolved_issue_ids=[i1["id"]])

        roadmap = prod.roadmap_timeline(slug)
        assert len(roadmap["versions"]) == 1
        assert "released_at" in roadmap["versions"][0]

    def test_roadmap_includes_milestoned_issues(self):
        """roadmap_timeline returns issues with milestone_version set."""
        p = prod.create_product(name="RoadmapMile", owner_id="00010")
        slug = p["slug"]
        i1 = prod.create_issue(slug=slug, title="Milestone Issue", created_by="ceo")
        prod.update_issue(slug, i1["id"], milestone_version="1.1.0")
        i2 = prod.create_issue(slug=slug, title="No milestone", created_by="ceo")

        roadmap = prod.roadmap_timeline(slug)
        assert len(roadmap["milestoned_issues"]) == 1
        assert roadmap["milestoned_issues"][0]["milestone_version"] == "1.1.0"

    def test_roadmap_empty(self):
        """roadmap_timeline on fresh product returns empty lists."""
        p = prod.create_product(name="RoadmapEmpty", owner_id="00010")
        roadmap = prod.roadmap_timeline(p["slug"])
        assert roadmap["sprints"] == []
        assert roadmap["versions"] == []
        assert roadmap["milestoned_issues"] == []

    def test_roadmap_product_not_found(self):
        with pytest.raises(ValueError, match="not found"):
            prod.roadmap_timeline("nonexistent")


# ---------------------------------------------------------------------------
# Product Activity
# ---------------------------------------------------------------------------


class TestProductActivity:
    def test_append_and_list(self):
        """append_product_activity adds entries, list returns them."""
        p = prod.create_product(name="ActivityProd", owner_id="00010")
        slug = p["slug"]
        prod.append_product_activity(slug, event_type="issue_created", actor="ceo", detail="Created issue X")
        prod.append_product_activity(slug, event_type="sprint_closed", actor="00010", detail="Sprint S1 closed")

        log = prod.list_product_activity(slug)
        assert len(log) == 2
        assert log[0]["event_type"] == "sprint_closed"  # newest first
        assert log[1]["event_type"] == "issue_created"

    def test_list_with_limit(self):
        """list_product_activity respects limit parameter."""
        p = prod.create_product(name="ActivityLimit", owner_id="00010")
        slug = p["slug"]
        for i in range(5):
            prod.append_product_activity(slug, event_type=f"event_{i}", actor="ceo", detail=f"Event {i}")

        log = prod.list_product_activity(slug, limit=3)
        assert len(log) == 3

    def test_list_empty(self):
        """list_product_activity on fresh product returns empty list."""
        p = prod.create_product(name="ActivityEmpty", owner_id="00010")
        assert prod.list_product_activity(p["slug"]) == []

    def test_activity_has_timestamp(self):
        """Each activity entry has a ts field."""
        p = prod.create_product(name="ActivityTS", owner_id="00010")
        slug = p["slug"]
        prod.append_product_activity(slug, event_type="test", actor="ceo", detail="test")
        log = prod.list_product_activity(slug)
        assert "ts" in log[0]

    def test_activity_max_entries(self):
        """Activity log is capped at _MAX_ACTIVITY_ENTRIES."""
        p = prod.create_product(name="ActivityCap", owner_id="00010")
        slug = p["slug"]
        # Directly write a large log, then append one more to trigger cap
        from onemancompany.core.store import _write_yaml
        log_path = prod._activity_dir(slug) / "log.yaml"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        big_log = [{"ts": "2026-01-01", "event_type": f"e{i}", "actor": "x", "detail": "d"} for i in range(500)]
        _write_yaml(log_path, big_log)
        # Append one more — should trigger cap
        prod.append_product_activity(slug, event_type="overflow", actor="ceo", detail="over")
        log = prod.list_product_activity(slug, limit=1000)
        assert len(log) <= 500
        assert log[0]["event_type"] == "overflow"  # newest first


# ---------------------------------------------------------------------------
# Batch 1 Critical Fixes — Audit Findings
# ---------------------------------------------------------------------------


class TestCreateIssueValidatesProduct:
    """Fix #1: create_issue() must raise ValueError when product doesn't exist."""

    def test_create_issue_nonexistent_product_raises(self):
        with pytest.raises(ValueError, match="not found"):
            prod.create_issue(
                slug="no-such-product",
                title="Ghost issue",
                created_by="ceo",
            )

    def test_create_issue_existing_product_works(self):
        p = prod.create_product(name="Valid Prod", owner_id="00010")
        issue = prod.create_issue(slug=p["slug"], title="Real issue", created_by="ceo")
        assert issue["product_id"] == p["id"]


class TestCircularDependencyDetection:
    """Fix #2: add_issue_link() must detect circular block dependencies."""

    def _make_issues(self, slug, count=3):
        p = prod.create_product(name="CycleProd", owner_id="00010")
        issues = []
        for i in range(count):
            issues.append(
                prod.create_issue(slug=p["slug"], title=f"Issue {i}", created_by="ceo")
            )
        return p["slug"], issues

    def test_direct_circular_blocks_raises(self):
        """A blocks B, then B blocks A → circular dependency error."""
        slug, issues = self._make_issues("cycle", 2)
        a_id, b_id = issues[0]["id"], issues[1]["id"]
        prod.add_issue_link(slug, a_id, b_id, IssueRelation.BLOCKS)
        with pytest.raises(ValueError, match="[Cc]ircular"):
            prod.add_issue_link(slug, b_id, a_id, IssueRelation.BLOCKS)

    def test_transitive_circular_blocks_raises(self):
        """A blocks B, B blocks C, C blocks A → circular dependency error."""
        slug, issues = self._make_issues("cycle", 3)
        a, b, c = issues[0]["id"], issues[1]["id"], issues[2]["id"]
        prod.add_issue_link(slug, a, b, IssueRelation.BLOCKS)
        prod.add_issue_link(slug, b, c, IssueRelation.BLOCKS)
        with pytest.raises(ValueError, match="[Cc]ircular"):
            prod.add_issue_link(slug, c, a, IssueRelation.BLOCKS)

    def test_relates_to_allows_cycles(self):
        """relates_to links don't have directionality — cycles are fine."""
        slug, issues = self._make_issues("cycle", 2)
        a_id, b_id = issues[0]["id"], issues[1]["id"]
        prod.add_issue_link(slug, a_id, b_id, IssueRelation.RELATES_TO)
        # Should NOT raise
        prod.add_issue_link(slug, b_id, a_id, IssueRelation.RELATES_TO)

    def test_blocked_by_circular_raises(self):
        """A blocks B exists. Then A blocked_by B means B blocks A → circular."""
        slug, issues = self._make_issues("cycle", 2)
        a_id, b_id = issues[0]["id"], issues[1]["id"]
        prod.add_issue_link(slug, a_id, b_id, IssueRelation.BLOCKS)
        # A blocked_by B → target(B) blocks issue(A) → B blocks A → circular with existing A blocks B
        with pytest.raises(ValueError, match="[Cc]ircular"):
            prod.add_issue_link(slug, a_id, b_id, IssueRelation.BLOCKED_BY)

    def test_cycle_check_skips_missing_issues(self):
        """Cycle check handles links pointing to deleted/missing issues gracefully."""
        slug, issues = self._make_issues("cycle", 4)
        a, b, c, d = [i["id"] for i in issues]
        # A blocks B, B blocks C, C blocks D
        prod.add_issue_link(slug, a, b, IssueRelation.BLOCKS)
        prod.add_issue_link(slug, b, c, IssueRelation.BLOCKS)
        prod.add_issue_link(slug, c, d, IssueRelation.BLOCKS)
        # Delete C's yaml so the cycle checker encounters a missing issue mid-walk
        import os
        c_path = prod._issues_dir(slug) / f"{c}.yaml"
        os.remove(c_path)
        # D blocks A would be circular IF C existed. But C is gone, so walk stops.
        # Still, add_issue_link validates both issues exist, so call _check_block_cycle directly.
        prod._check_block_cycle(slug, a, b)  # walks B→C(missing)→stops, no cycle found

    def test_cycle_check_handles_diamond_graph(self):
        """Diamond: A→B, A→C, B→D, C→D. Adding D→A is circular."""
        slug, issues = self._make_issues("cycle", 4)
        a, b, c, d = [i["id"] for i in issues]
        prod.add_issue_link(slug, a, b, IssueRelation.BLOCKS)
        prod.add_issue_link(slug, a, c, IssueRelation.BLOCKS)
        prod.add_issue_link(slug, b, d, IssueRelation.BLOCKS)
        prod.add_issue_link(slug, c, d, IssueRelation.BLOCKS)
        with pytest.raises(ValueError, match="[Cc]ircular"):
            prod.add_issue_link(slug, d, a, IssueRelation.BLOCKS)

    def test_cycle_check_visited_dedup(self):
        """Diamond graph without cycle — BFS deduplicates via visited set."""
        slug, issues = self._make_issues("cycle", 5)
        a, b, c, d, e = [i["id"] for i in issues]
        # A→C, A→D, C→E, D→E (diamond converging on E)
        prod.add_issue_link(slug, a, c, IssueRelation.BLOCKS)
        prod.add_issue_link(slug, a, d, IssueRelation.BLOCKS)
        prod.add_issue_link(slug, c, e, IssueRelation.BLOCKS)
        prod.add_issue_link(slug, d, e, IssueRelation.BLOCKS)
        # B blocks A — not circular, but BFS from A will visit E twice (via C and D)
        # The second visit hits the 'visited' skip (line 507)
        prod.add_issue_link(slug, b, a, IssueRelation.BLOCKS)  # should NOT raise


class TestEnumValidationInRoutes:
    """Fix #3: Invalid enum values should raise ValueError (routes wrap in 400)."""

    def test_invalid_issue_status_raises(self):
        with pytest.raises(ValueError):
            IssueStatus("not_a_real_status")

    def test_invalid_issue_resolution_raises(self):
        with pytest.raises(ValueError):
            IssueResolution("not_a_real_resolution")

    def test_invalid_issue_priority_raises(self):
        with pytest.raises(ValueError):
            IssuePriority("not_a_real_priority")


class TestMissingIssueRaisesValueError:
    """Fix #4: update_issue, close_issue, reopen_issue should raise ValueError on missing."""

    def test_update_issue_missing_raises(self):
        prod.create_product(name="ErrProd", owner_id="00010")
        with pytest.raises(ValueError, match="not found"):
            prod.update_issue("err-prod", "nonexistent_id", title="Nope")

    def test_close_issue_missing_raises(self):
        prod.create_product(name="ErrProd2", owner_id="00010")
        with pytest.raises(ValueError, match="not found"):
            prod.close_issue("err-prod2", "nonexistent_id")

    def test_reopen_issue_missing_raises(self):
        prod.create_product(name="ErrProd3", owner_id="00010")
        with pytest.raises(ValueError, match="not found"):
            prod.reopen_issue("err-prod3", "nonexistent_id")


# ---------------------------------------------------------------------------
# Batch 2 — CRUD + Validation
# ---------------------------------------------------------------------------


class TestDeleteIssue:
    """B2 Fix #1: delete_issue() removes issue yaml and cleans up links."""

    def test_delete_issue_removes_file(self):
        p = prod.create_product(name="DelProd", owner_id="00010")
        issue = prod.create_issue(slug=p["slug"], title="To delete", created_by="ceo")
        prod.delete_issue(p["slug"], issue["id"])
        assert prod.load_issue(p["slug"], issue["id"]) is None

    def test_delete_issue_cleans_links(self):
        p = prod.create_product(name="DelLink", owner_id="00010")
        i1 = prod.create_issue(slug=p["slug"], title="A", created_by="ceo")
        i2 = prod.create_issue(slug=p["slug"], title="B", created_by="ceo")
        prod.add_issue_link(p["slug"], i1["id"], i2["id"], IssueRelation.BLOCKS)
        prod.delete_issue(p["slug"], i1["id"])
        # i2 should no longer have links to i1
        links = prod.get_issue_links(p["slug"], i2["id"])
        assert not any(l["issue_id"] == i1["id"] for l in links)

    def test_delete_issue_not_found_raises(self):
        p = prod.create_product(name="DelMiss", owner_id="00010")
        with pytest.raises(ValueError, match="not found"):
            prod.delete_issue(p["slug"], "issue_nope")

    def test_delete_issue_from_nonexistent_product(self):
        with pytest.raises(ValueError, match="not found"):
            prod.delete_issue("no-product", "issue_nope")


class TestDeleteSprint:
    """B2 Fix #2: delete_sprint() removes sprint yaml."""

    def test_delete_sprint_removes_file(self):
        p = prod.create_product(name="DelSprint", owner_id="00010")
        sprint = prod.create_sprint(slug=p["slug"], name="S1",
                                     start_date="2026-01-01", end_date="2026-01-14")
        prod.delete_sprint(p["slug"], sprint["id"])
        assert prod.load_sprint(p["slug"], sprint["id"]) is None

    def test_delete_sprint_not_found_raises(self):
        p = prod.create_product(name="DelSMiss", owner_id="00010")
        with pytest.raises(ValueError, match="not found"):
            prod.delete_sprint(p["slug"], "sprint_nope")

    def test_delete_active_sprint_raises(self):
        """Cannot delete an active sprint — must close first."""
        p = prod.create_product(name="DelActive", owner_id="00010")
        sprint = prod.create_sprint(slug=p["slug"], name="S1",
                                     start_date="2026-01-01", end_date="2026-01-14")
        prod.start_sprint(p["slug"], sprint["id"])
        with pytest.raises(ValueError, match="[Aa]ctive|[Cc]annot"):
            prod.delete_sprint(p["slug"], sprint["id"])


class TestIssueStatusTransition:
    """B2 Fix #3: validate issue status transitions."""

    def test_valid_forward_transitions(self):
        p = prod.create_product(name="Trans", owner_id="00010")
        issue = prod.create_issue(slug=p["slug"], title="Flow", created_by="ceo")
        # backlog → planned → in_progress → in_review → done
        prod.update_issue(p["slug"], issue["id"], status=IssueStatus.PLANNED.value)
        prod.update_issue(p["slug"], issue["id"], status=IssueStatus.IN_PROGRESS.value)
        prod.update_issue(p["slug"], issue["id"], status=IssueStatus.IN_REVIEW.value)
        prod.update_issue(p["slug"], issue["id"], status=IssueStatus.DONE.value)
        loaded = prod.load_issue(p["slug"], issue["id"])
        assert loaded["status"] == IssueStatus.DONE.value

    def test_backward_to_backlog_always_allowed(self):
        """Moving back to backlog is always valid (requeue)."""
        p = prod.create_product(name="BackQ", owner_id="00010")
        issue = prod.create_issue(slug=p["slug"], title="Requeue", created_by="ceo")
        prod.update_issue(p["slug"], issue["id"], status=IssueStatus.IN_PROGRESS.value)
        prod.update_issue(p["slug"], issue["id"], status=IssueStatus.BACKLOG.value)
        loaded = prod.load_issue(p["slug"], issue["id"])
        assert loaded["status"] == IssueStatus.BACKLOG.value

    def test_invalid_transition_raises(self):
        """Cannot jump from backlog directly to done."""
        p = prod.create_product(name="BadTrans", owner_id="00010")
        issue = prod.create_issue(slug=p["slug"], title="Skip", created_by="ceo")
        with pytest.raises(ValueError, match="[Tt]ransition|[Ii]nvalid"):
            prod.update_issue(p["slug"], issue["id"], status=IssueStatus.DONE.value)


class TestSprintDateValidation:
    """B2 Fix #4: sprint end_date must be after start_date."""

    def test_valid_dates(self):
        p = prod.create_product(name="DateOK", owner_id="00010")
        sprint = prod.create_sprint(slug=p["slug"], name="Good",
                                     start_date="2026-01-01", end_date="2026-01-14")
        assert sprint["start_date"] == "2026-01-01"

    def test_end_before_start_raises(self):
        p = prod.create_product(name="DateBad", owner_id="00010")
        with pytest.raises(ValueError, match="[Ee]nd.*before|[Ss]tart.*after|[Dd]ate"):
            prod.create_sprint(slug=p["slug"], name="Bad",
                               start_date="2026-01-14", end_date="2026-01-01")

    def test_same_date_raises(self):
        p = prod.create_product(name="DateSame", owner_id="00010")
        with pytest.raises(ValueError, match="[Ee]nd.*before|[Ss]tart.*after|[Dd]ate"):
            prod.create_sprint(slug=p["slug"], name="Same",
                               start_date="2026-01-01", end_date="2026-01-01")

    def test_invalid_date_format_raises(self):
        p = prod.create_product(name="DateFmt", owner_id="00010")
        with pytest.raises(ValueError):
            prod.create_sprint(slug=p["slug"], name="Fmt",
                               start_date="not-a-date", end_date="2026-01-14")


# ---------------------------------------------------------------------------
# Batch 4 — Config + Query Filters + Assertion Fixes
# ---------------------------------------------------------------------------


class TestListIssuesAssigneeFilter:
    """B4: list_issues should support assignee_id filter."""

    def test_filter_by_assignee(self):
        p = prod.create_product(name="AssignFilter", owner_id="00010")
        i1 = prod.create_issue(slug=p["slug"], title="A", created_by="ceo")
        i2 = prod.create_issue(slug=p["slug"], title="B", created_by="ceo")
        prod.update_issue(p["slug"], i1["id"], assignee_id="emp001")
        prod.update_issue(p["slug"], i2["id"], assignee_id="emp002")
        result = prod.list_issues(p["slug"], assignee_id="emp001")
        assert len(result) == 1
        assert result[0]["id"] == i1["id"]

    def test_filter_unassigned(self):
        p = prod.create_product(name="Unassigned", owner_id="00010")
        i1 = prod.create_issue(slug=p["slug"], title="A", created_by="ceo")
        prod.create_issue(slug=p["slug"], title="B", created_by="ceo")
        prod.update_issue(p["slug"], i1["id"], assignee_id="emp001")
        result = prod.list_issues(p["slug"], assignee_id="")
        # Empty string means unassigned — should return only i2
        assert len(result) == 1
        assert result[0]["assignee_id"] in (None, "")

    def test_filter_no_match(self):
        p = prod.create_product(name="NoMatch", owner_id="00010")
        prod.create_issue(slug=p["slug"], title="A", created_by="ceo")
        result = prod.list_issues(p["slug"], assignee_id="nobody")
        assert result == []


class TestConfigurableThresholds:
    """B4: product_triggers thresholds should be module-level constants."""

    def test_thresholds_are_module_constants(self):
        from onemancompany.core import product_triggers as pt
        # All thresholds should be accessible as module attributes
        assert hasattr(pt, "KR_LAGGING_THRESHOLD")
        assert hasattr(pt, "MAX_ACTIVE_PROJECTS")
        assert hasattr(pt, "BACKLOG_GROOMING_THRESHOLD")
        assert hasattr(pt, "STALE_REVIEW_HOURS")
        assert hasattr(pt, "BLOCKED_DAYS_THRESHOLD")
        assert hasattr(pt, "UNHANDLED_BACKLOG_THRESHOLD")

    def test_history_cap_is_constant(self):
        assert hasattr(prod, "HISTORY_MAX_ENTRIES")
        assert isinstance(prod.HISTORY_MAX_ENTRIES, int)
        assert prod.HISTORY_MAX_ENTRIES > 0


# ---------------------------------------------------------------------------
# Batch 2 (Audit) — Status Transition Consistency
# ---------------------------------------------------------------------------


class TestDeleteKeyResult:
    """B4 audit: KR deletion was impossible — no backend function existed."""

    def test_delete_kr(self):
        p = prod.create_product(name="KRDel", owner_id="00010")
        kr = prod.add_key_result(p["slug"], title="Users", target=100)
        prod.delete_key_result(p["slug"], kr["id"])
        loaded = prod.load_product(p["slug"])
        assert len(loaded["key_results"]) == 0

    def test_delete_kr_not_found(self):
        p = prod.create_product(name="KRDelNF", owner_id="00010")
        with pytest.raises(ValueError, match="not found"):
            prod.delete_key_result(p["slug"], "kr_nonexist")

    def test_delete_kr_product_not_found(self):
        with pytest.raises(ValueError, match="not found"):
            prod.delete_key_result("nonexist", "kr_1")

    def test_delete_kr_preserves_others(self):
        p = prod.create_product(name="KRKeep", owner_id="00010")
        kr1 = prod.add_key_result(p["slug"], title="A", target=10)
        kr2 = prod.add_key_result(p["slug"], title="B", target=20)
        prod.delete_key_result(p["slug"], kr1["id"])
        loaded = prod.load_product(p["slug"])
        assert len(loaded["key_results"]) == 1
        assert loaded["key_results"][0]["id"] == kr2["id"]


class TestCloseIssueFromAnyStatus:
    """close_issue should work from any status — it's a special operation."""

    def test_close_from_backlog(self):
        p = prod.create_product(name="CloseAny", owner_id="00010")
        issue = prod.create_issue(slug=p["slug"], title="FromBacklog", created_by="ceo")
        result = prod.close_issue(p["slug"], issue["id"])
        assert result["status"] == IssueStatus.DONE.value

    def test_close_from_in_progress(self):
        p = prod.create_product(name="CloseIP", owner_id="00010")
        issue = prod.create_issue(slug=p["slug"], title="FromIP", created_by="ceo")
        prod.update_issue(p["slug"], issue["id"], status=IssueStatus.IN_PROGRESS.value)
        result = prod.close_issue(p["slug"], issue["id"])
        assert result["status"] == IssueStatus.DONE.value

    def test_close_from_planned(self):
        p = prod.create_product(name="ClosePl", owner_id="00010")
        issue = prod.create_issue(slug=p["slug"], title="FromPlanned", created_by="ceo")
        prod.update_issue(p["slug"], issue["id"], status=IssueStatus.PLANNED.value)
        result = prod.close_issue(p["slug"], issue["id"])
        assert result["status"] == IssueStatus.DONE.value

    def test_close_from_released_is_noop(self):
        """Closing an already-released issue should still set DONE (downgrade)."""
        p = prod.create_product(name="CloseRel", owner_id="00010")
        issue = prod.create_issue(slug=p["slug"], title="AlreadyReleased", created_by="ceo")
        prod.update_issue(p["slug"], issue["id"], status=IssueStatus.IN_PROGRESS.value)
        prod.update_issue(p["slug"], issue["id"], status=IssueStatus.DONE.value)
        prod.update_issue(p["slug"], issue["id"], status=IssueStatus.RELEASED.value)
        result = prod.close_issue(p["slug"], issue["id"])
        assert result["status"] == IssueStatus.DONE.value


class TestReopenIssueFromAnyStatus:
    """reopen_issue should work from any closed/done/released status."""

    def test_reopen_from_done(self):
        p = prod.create_product(name="ReopenD", owner_id="00010")
        issue = prod.create_issue(slug=p["slug"], title="ClosedDone", created_by="ceo")
        prod.close_issue(p["slug"], issue["id"])
        result = prod.reopen_issue(p["slug"], issue["id"])
        assert result["status"] == IssueStatus.BACKLOG.value
        assert result["reopened_count"] == 1

    def test_reopen_from_released(self):
        p = prod.create_product(name="ReopenR", owner_id="00010")
        issue = prod.create_issue(slug=p["slug"], title="Released", created_by="ceo")
        prod.update_issue(p["slug"], issue["id"], status=IssueStatus.IN_PROGRESS.value)
        prod.update_issue(p["slug"], issue["id"], status=IssueStatus.DONE.value)
        prod.update_issue(p["slug"], issue["id"], status=IssueStatus.RELEASED.value)
        result = prod.reopen_issue(p["slug"], issue["id"])
        assert result["status"] == IssueStatus.BACKLOG.value


class TestSyncIssueStatusesBypassesValidation:
    """sync_issue_statuses should set derived status without transition validation."""

    def test_sync_can_jump_to_non_adjacent_status(self):
        """If a task goes from pending straight to finished, issue should go BACKLOG → DONE."""
        p = prod.create_product(name="SyncJump", owner_id="00010")
        issue = prod.create_issue(slug=p["slug"], title="FastTrack", created_by="ceo")
        # Mock derive_issue_status to return DONE (skipping intermediate states)
        with patch("onemancompany.core.product.derive_issue_status", return_value=IssueStatus.DONE):
            changed = prod.sync_issue_statuses(p["slug"])
        assert len(changed) == 1
        assert changed[0]["old"] == IssueStatus.BACKLOG.value
        assert changed[0]["new"] == IssueStatus.DONE.value
        loaded = prod.load_issue(p["slug"], issue["id"])
        assert loaded["status"] == IssueStatus.DONE.value

    def test_sync_can_go_backward(self):
        """If tasks revert, issue can go from IN_PROGRESS back to PLANNED."""
        p = prod.create_product(name="SyncBack", owner_id="00010")
        issue = prod.create_issue(slug=p["slug"], title="Reverted", created_by="ceo")
        prod.update_issue(p["slug"], issue["id"], status=IssueStatus.IN_PROGRESS.value)
        with patch("onemancompany.core.product.derive_issue_status", return_value=IssueStatus.PLANNED):
            changed = prod.sync_issue_statuses(p["slug"])
        assert len(changed) == 1
        loaded = prod.load_issue(p["slug"], issue["id"])
        assert loaded["status"] == IssueStatus.PLANNED.value


class TestReleaseVersionClosesFirstIfNeeded:
    """release_version should handle issues not yet in DONE status."""

    def test_release_version_with_done_issues(self):
        """Standard path: issues already DONE → RELEASED."""
        p = prod.create_product(name="RelDone", owner_id="00010")
        issue = prod.create_issue(slug=p["slug"], title="Ready", created_by="ceo")
        prod.update_issue(p["slug"], issue["id"], status=IssueStatus.IN_PROGRESS.value)
        prod.update_issue(p["slug"], issue["id"], status=IssueStatus.DONE.value)
        v = prod.release_version(p["slug"], [issue["id"]])
        loaded = prod.load_issue(p["slug"], issue["id"])
        assert loaded["status"] == IssueStatus.RELEASED.value
        assert v["version"] == "0.1.1"

    def test_release_version_with_non_done_issue(self):
        """Edge case: issue is BACKLOG but included in release. Should not crash."""
        p = prod.create_product(name="RelBack", owner_id="00010")
        issue = prod.create_issue(slug=p["slug"], title="Surprise", created_by="ceo")
        # Issue is still in BACKLOG — release should handle gracefully
        v = prod.release_version(p["slug"], [issue["id"]])
        loaded = prod.load_issue(p["slug"], issue["id"])
        assert loaded["status"] == IssueStatus.RELEASED.value
        assert v["version"] == "0.1.1"
