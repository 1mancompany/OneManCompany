"""Unit tests for product_workspace — git operations for product worktrees."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from onemancompany.core import product_workspace as pw


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _git(args: list[str], cwd: Path) -> str:
    """Run a git command and return stdout.

    Strips ``GIT_*`` env vars so leaked vars from the test harness or
    pre-commit hook don't redirect commands to the wrong repo.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        env=env,
    ).stdout.strip()


def _commit_file(repo: Path, filename: str, content: str, msg: str) -> None:
    """Write a file, add, and commit in *repo*."""
    (repo / filename).write_text(content)
    _git(["add", filename], repo)
    _git(["commit", "-m", msg], repo)


# ---------------------------------------------------------------------------
# TestInitWorkspace
# ---------------------------------------------------------------------------


class TestInitWorkspace:
    def test_creates_git_repo(self, tmp_path: Path) -> None:
        ws = tmp_path / "ws"
        pw.init_workspace(ws)
        assert (ws / ".git").is_dir()

    def test_has_initial_commit(self, tmp_path: Path) -> None:
        ws = tmp_path / "ws"
        pw.init_workspace(ws)
        log = _git(["log", "--oneline"], ws)
        assert log  # at least one commit

    def test_creates_readme(self, tmp_path: Path) -> None:
        ws = tmp_path / "ws"
        pw.init_workspace(ws)
        assert (ws / "README.md").exists()

    def test_idempotent(self, tmp_path: Path) -> None:
        ws = tmp_path / "ws"
        pw.init_workspace(ws)
        pw.init_workspace(ws)  # second call should not raise
        log = _git(["log", "--oneline"], ws)
        # still exactly one commit
        assert len(log.splitlines()) == 1


# ---------------------------------------------------------------------------
# TestWorktree
# ---------------------------------------------------------------------------


class TestWorktree:
    @pytest.fixture()
    def workspace(self, tmp_path: Path) -> Path:
        ws = tmp_path / "ws"
        pw.init_workspace(ws)
        return ws

    def test_add_creates_dir_and_branch(self, workspace: Path, tmp_path: Path) -> None:
        wt = tmp_path / "wt"
        pw.add_worktree(workspace, wt, "alpha")
        assert wt.is_dir()
        branches = _git(["branch", "--list", "project/alpha"], workspace)
        assert "project/alpha" in branches

    def test_add_idempotent(self, workspace: Path, tmp_path: Path) -> None:
        wt = tmp_path / "wt"
        pw.add_worktree(workspace, wt, "alpha")
        pw.add_worktree(workspace, wt, "alpha")  # no error
        assert wt.is_dir()

    def test_remove_cleans_up(self, workspace: Path, tmp_path: Path) -> None:
        wt = tmp_path / "wt"
        pw.add_worktree(workspace, wt, "alpha")
        pw.remove_worktree(workspace, wt, "alpha")
        assert not wt.is_dir()
        branches = _git(["branch", "--list", "project/alpha"], workspace)
        assert "project/alpha" not in branches

    def test_remove_missing_is_noop(self, workspace: Path, tmp_path: Path) -> None:
        wt = tmp_path / "wt_gone"
        # removing a worktree that was never added should not raise
        pw.remove_worktree(workspace, wt, "nonexistent")


# ---------------------------------------------------------------------------
# TestPromote
# ---------------------------------------------------------------------------


class TestPromote:
    @pytest.fixture()
    def setup(self, tmp_path: Path):
        """Create a workspace + worktree with a diverging commit."""
        ws = tmp_path / "ws"
        pw.init_workspace(ws)
        wt = tmp_path / "wt"
        pw.add_worktree(ws, wt, "beta")
        return ws, wt

    def test_clean_merge(self, setup) -> None:
        ws, wt = setup
        _commit_file(wt, "feature.txt", "hello", "add feature")
        result = pw.promote(ws, wt, "beta")
        assert result["status"] == "merged"
        # main should now have the file
        assert (ws / "feature.txt").exists()

    def test_nothing_to_merge(self, setup) -> None:
        ws, wt = setup
        result = pw.promote(ws, wt, "beta")
        assert result["status"] == "nothing"

    def test_conflict_returns_both_versions(self, setup) -> None:
        ws, wt = setup
        # Diverging edits on the same file
        _commit_file(ws, "shared.txt", "main version", "main edit")
        _commit_file(wt, "shared.txt", "branch version", "branch edit")
        result = pw.promote(ws, wt, "beta")
        assert result["status"] == "conflict"
        assert len(result["conflicts"]) > 0
        conflict = result["conflicts"][0]
        assert conflict["file"] == "shared.txt"
        assert "main version" in conflict["product_version"]
        assert "branch version" in conflict["your_version"]

    def test_conflict_resolution_then_retry(self, setup) -> None:
        ws, wt = setup
        _commit_file(ws, "shared.txt", "main version", "main edit")
        _commit_file(wt, "shared.txt", "branch version", "branch edit")
        result = pw.promote(ws, wt, "beta")
        assert result["status"] == "conflict"

        # Resolve the conflict in the workspace (main)
        (ws / "shared.txt").write_text("resolved version")
        _git(["add", "shared.txt"], ws)

        # Retry should finalize the merge
        result2 = pw.promote(ws, wt, "beta")
        assert result2["status"] == "merged"
        assert (ws / "shared.txt").read_text() == "resolved version"

    def test_abort_cleans_up(self, setup) -> None:
        ws, wt = setup
        _commit_file(ws, "shared.txt", "main version", "main edit")
        _commit_file(wt, "shared.txt", "branch version", "branch edit")
        result = pw.promote(ws, wt, "beta")
        assert result["status"] == "conflict"

        result2 = pw.promote(ws, wt, "beta", abort=True)
        assert result2["status"] == "aborted"
        # shared.txt should be back to main version
        assert (ws / "shared.txt").read_text() == "main version"


# ---------------------------------------------------------------------------
# TestLifecycleHooks
# ---------------------------------------------------------------------------

from onemancompany.core import product as prod
from onemancompany.core import project_archive as pa


class TestLifecycleHooks:
    @pytest.fixture(autouse=True)
    def _setup_dirs(self, tmp_path, monkeypatch):
        self.products_dir = tmp_path / "products"
        self.projects_dir = tmp_path / "projects"
        self.products_dir.mkdir()
        self.projects_dir.mkdir()
        monkeypatch.setattr(prod, "PRODUCTS_DIR", self.products_dir)
        monkeypatch.setattr(pa, "PRODUCTS_DIR", self.products_dir)
        monkeypatch.setattr(pa, "PROJECTS_DIR", self.projects_dir)

    def _create_product(self) -> dict:
        """Helper: create a product on disk and return its dict."""
        return prod.create_product(name="Test App", owner_id="emp001")

    def test_project_with_product_id_creates_worktree(self) -> None:
        product = self._create_product()
        project_id = pa.create_named_project("feat-one", product_id=product["id"])

        # Workspace should be initialised inside product dir
        slug = prod.find_slug_by_product_id(product["id"])
        ws = self.products_dir / slug / "workspace"
        assert (ws / ".git").is_dir(), "workspace git repo not created"

        # Worktree dir should exist in project dir
        wt = self.projects_dir / project_id / "product_worktree"
        assert wt.is_dir(), "worktree dir not created"

        # Product should be marked as workspace_initialized
        updated = prod.load_product(slug)
        assert updated["workspace_initialized"] is True

    def test_project_without_product_id_no_worktree(self) -> None:
        project_id = pa.create_named_project("standalone")
        wt = self.projects_dir / project_id / "product_worktree"
        assert not wt.exists(), "worktree should not exist for project without product_id"

    def test_second_project_reuses_workspace(self) -> None:
        product = self._create_product()

        proj1 = pa.create_named_project("feat-one", product_id=product["id"])
        proj2 = pa.create_named_project("feat-two", product_id=product["id"])

        slug = prod.find_slug_by_product_id(product["id"])
        ws = self.products_dir / slug / "workspace"

        # Both worktrees exist
        assert (self.projects_dir / proj1 / "product_worktree").is_dir()
        assert (self.projects_dir / proj2 / "product_worktree").is_dir()

        # Workspace was only initialised once (1 initial commit on main)
        log = _git(["log", "--oneline", "main"], ws)
        assert len(log.splitlines()) == 1, "workspace should have exactly 1 initial commit"

    def test_archive_project_removes_worktree(self) -> None:
        product = self._create_product()
        project_id = pa.create_named_project("feat-one", product_id=product["id"])

        wt = self.projects_dir / project_id / "product_worktree"
        assert wt.is_dir(), "precondition: worktree should exist"

        pa.archive_project(project_id)

        assert not wt.is_dir(), "worktree should be removed after archive"
