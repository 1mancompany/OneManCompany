"""Unit tests for product management CRUD — products, key results, and issues."""

from __future__ import annotations

import pytest

from onemancompany.core import product as prod
from onemancompany.core.models import (
    IssueStatus,
    IssuePriority,
    IssueResolution,
    ProductStatus,
)


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
        result = prod.update_kr_progress(p["slug"], "kr_nonexist", current=10.0)
        assert result is None


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
        assert issue["status"] == IssueStatus.OPEN
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
        assert closed["status"] == IssueStatus.CLOSED.value
        assert closed["resolution"] == IssueResolution.FIXED.value
        assert closed["closed_at"] is not None

    def test_reopen_issue(self):
        p = prod.create_product(name="Issue Reopen", owner_id="00010")
        issue = prod.create_issue(slug=p["slug"], title="Reopen me", priority=IssuePriority.P1, created_by="x")
        prod.close_issue(p["slug"], issue["id"], resolution=IssueResolution.FIXED)
        reopened = prod.reopen_issue(p["slug"], issue["id"])
        assert reopened["status"] == IssueStatus.OPEN.value
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
