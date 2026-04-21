"""Unit tests for product management CRUD — products, key results, and issues."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from onemancompany.core import product as prod
from onemancompany.core.models import (
    IssueStatus,
    IssuePriority,
    IssueResolution,
    ProductStatus,
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
        assert len(loaded.get("history", [])) >= 1
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
        # Should have at least 2 entries: close + reopen
        assert len(history) >= 2

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
        assert len(changes) >= 1
        loaded = prod.load_issue(p["slug"], issue["id"])
        assert loaded["status"] == IssueStatus.BACKLOG.value

    def test_released_status_preserved(self):
        p = prod.create_product(name="ReleasedTest", owner_id="00004")
        issue = prod.create_issue(slug=p["slug"], title="Released", created_by="ceo")
        prod.update_issue(p["slug"], issue["id"], status=IssueStatus.RELEASED.value)
        status = prod.derive_issue_status(p["slug"], issue["id"])
        assert status == IssueStatus.RELEASED

    def test_sync_skips_released_issues(self):
        p = prod.create_product(name="SkipReleasedTest", owner_id="00004")
        issue = prod.create_issue(slug=p["slug"], title="Skip", created_by="ceo")
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
        """Line 266 (implicit): create_issue with missing product still works (empty product_id)."""
        issue = prod.create_issue(slug="ghost", title="Orphan", created_by="ceo")
        assert issue["product_id"] == ""

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
        """Lines 363-364: updating missing issue returns None."""
        p = prod.create_product(name="UpdateMiss", owner_id="00004")
        result = prod.update_issue(p["slug"], "issue_nope", title="x")
        assert result is None

    def test_close_issue_not_found(self):
        """Lines 387-388: closing missing issue returns None."""
        p = prod.create_product(name="CloseMiss", owner_id="00004")
        result = prod.close_issue(p["slug"], "issue_gone")
        assert result is None

    def test_reopen_issue_not_found(self):
        """Lines 406-407: reopening missing issue returns None."""
        p = prod.create_product(name="ReopenMiss", owner_id="00004")
        result = prod.reopen_issue(p["slug"], "issue_vanish")
        assert result is None


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
