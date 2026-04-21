"""Tests for promote_to_product tool."""

import os
import subprocess
from unittest.mock import patch

import pytest

from onemancompany.core import product_workspace as pw


def _git_cmd(args: list[str], cwd) -> None:
    """Run git without inheriting GIT_* env vars (same as product_workspace._git)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=env)


@pytest.fixture
def product_workspace(tmp_path):
    ws_dir = tmp_path / "workspace"
    pw.init_workspace(ws_dir)
    wt_path = tmp_path / "product_worktree"
    pw.add_worktree(ws_dir, wt_path, "proj_test")
    return {"workspace_dir": ws_dir, "worktree_path": wt_path, "project_id": "proj_test"}


class TestPromoteToProductTool:
    @pytest.fixture(autouse=True)
    def _setup(self, product_workspace, monkeypatch):
        self.ws = product_workspace
        monkeypatch.setattr(
            "onemancompany.agents.product_workspace_tools._resolve_product_workspace",
            lambda: (self.ws["workspace_dir"], self.ws["worktree_path"], self.ws["project_id"]),
        )

    @pytest.mark.asyncio
    async def test_promote_clean(self):
        from onemancompany.agents.product_workspace_tools import promote_to_product

        wt = self.ws["worktree_path"]
        (wt / "output.md").write_text("# Output\n")
        _git_cmd(["add", "."], cwd=wt)
        _git_cmd(["commit", "-m", "add output"], cwd=wt)
        result = await promote_to_product.ainvoke({})
        assert "merged" in result.lower() or "promoted" in result.lower()

    @pytest.mark.asyncio
    async def test_promote_no_changes(self):
        from onemancompany.agents.product_workspace_tools import promote_to_product

        result = await promote_to_product.ainvoke({})
        assert "nothing" in result.lower() or "no change" in result.lower()

    @pytest.mark.asyncio
    async def test_promote_abort(self):
        from onemancompany.agents.product_workspace_tools import promote_to_product

        ws_dir = self.ws["workspace_dir"]
        wt_path = self.ws["worktree_path"]

        # Create conflicting changes on main
        (ws_dir / "README.md").write_text("# main edit\n")
        _git_cmd(["add", "."], cwd=ws_dir)
        _git_cmd(["commit", "-m", "main"], cwd=ws_dir)

        # Create conflicting changes on project branch
        (wt_path / "README.md").write_text("# project edit\n")
        _git_cmd(["add", "."], cwd=wt_path)
        _git_cmd(["commit", "-m", "project"], cwd=wt_path)

        # First call triggers conflict
        await promote_to_product.ainvoke({})

        # Abort the merge
        result = await promote_to_product.ainvoke({"abort": True})
        assert "abort" in result.lower()
