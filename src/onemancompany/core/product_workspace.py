"""Git operations for product workspaces — init, worktree management, and promote."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from filelock import FileLock
from loguru import logger


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _git(
    args: list[str],
    cwd: Path,
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a git command with debug logging.

    Strips ``GIT_*`` env vars so that tests running inside a git worktree
    (or after other tests that leak git env) don't interfere.
    """
    cmd = ["git", *args]
    logger.debug("git: {} (cwd={})", " ".join(cmd), cwd)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False, env=env)
    logger.debug("git rc={} stdout={!r} stderr={!r}", result.returncode, result.stdout[:200], result.stderr[:200])
    if check and result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode, cmd, result.stdout, result.stderr
        )
    return result


# ---------------------------------------------------------------------------
# init_workspace
# ---------------------------------------------------------------------------


def init_workspace(workspace_dir: Path) -> None:
    """``git init`` a directory with an initial commit. Idempotent."""
    if (workspace_dir / ".git").is_dir():
        logger.debug("init_workspace: already initialised at {}", workspace_dir)
        return

    workspace_dir.mkdir(parents=True, exist_ok=True)
    _git(["init", "-b", "main"], workspace_dir)
    _git(["config", "user.email", "workspace@localhost"], workspace_dir)
    _git(["config", "user.name", "workspace"], workspace_dir)

    readme = workspace_dir / "README.md"
    readme.write_text("# Product Workspace\n")
    _git(["add", "README.md"], workspace_dir)
    _git(["commit", "-m", "Initial commit"], workspace_dir)


# ---------------------------------------------------------------------------
# add_worktree / remove_worktree
# ---------------------------------------------------------------------------


def add_worktree(workspace_dir: Path, worktree_path: Path, project_id: str) -> None:
    """``git worktree add`` on branch ``project/{project_id}``. Idempotent."""
    branch = f"project/{project_id}"

    if worktree_path.is_dir():
        logger.debug("add_worktree: {} already exists, skipping", worktree_path)
        return

    _git(["worktree", "add", "-b", branch, str(worktree_path)], workspace_dir)


def remove_worktree(workspace_dir: Path, worktree_path: Path, project_id: str) -> None:
    """Remove a worktree and delete its branch. Idempotent / noop if missing."""
    branch = f"project/{project_id}"

    # Guard: if workspace_dir itself is gone (e.g. product already deleted),
    # there's nothing to clean up in git — just remove the directory.
    if not (workspace_dir / ".git").is_dir():
        logger.debug("remove_worktree: workspace {} gone, skipping git cleanup", workspace_dir)
        if worktree_path.is_dir():
            import shutil
            shutil.rmtree(worktree_path)
        return

    if worktree_path.is_dir():
        _git(["worktree", "remove", "--force", str(worktree_path)], workspace_dir)

    # Prune stale worktree bookkeeping
    _git(["worktree", "prune"], workspace_dir)

    # Delete branch if it exists
    result = _git(["branch", "--list", branch], workspace_dir)
    if branch in result.stdout:
        _git(["branch", "-D", branch], workspace_dir)


# ---------------------------------------------------------------------------
# promote
# ---------------------------------------------------------------------------


def _is_merging(workspace_dir: Path) -> bool:
    """Check if the workspace is in a merge state."""
    return (workspace_dir / ".git" / "MERGE_HEAD").exists()


def _has_conflict_markers(workspace_dir: Path) -> bool:
    """Check if any unmerged file still has conflict markers in its working-tree content.

    Two-step check: first find unmerged paths via the index, then read the
    actual file content for ``<<<<<<<`` markers.  This avoids false positives
    from ``git diff --check`` (which also flags trailing-whitespace) while
    correctly detecting resolved-but-unstaged files.
    """
    result = _git(["diff", "--name-only", "--diff-filter=U"], workspace_dir, check=False)
    unmerged = [f for f in result.stdout.strip().splitlines() if f]
    if not unmerged:
        return False
    for fname in unmerged:
        fpath = workspace_dir / fname
        if fpath.is_file() and "<<<<<<<" in fpath.read_text():
            return True
    return False


def _parse_conflicts(workspace_dir: Path) -> list[dict]:
    """Parse unmerged files and extract ours/theirs content."""
    result = _git(["diff", "--name-only", "--diff-filter=U"], workspace_dir)
    conflicts = []
    for fname in result.stdout.strip().splitlines():
        if not fname:
            continue
        raw = (workspace_dir / fname).read_text()
        ours = ""
        theirs = ""
        for match in re.finditer(
            r"<<<<<<<[^\n]*\n(.*?)=======\n(.*?)>>>>>>>[^\n]*\n",
            raw,
            re.DOTALL,
        ):
            ours += match.group(1)
            theirs += match.group(2)
        conflicts.append({"file": fname, "your_version": theirs, "product_version": ours})
    return conflicts


def _workspace_lock(workspace_dir: Path) -> FileLock:
    """Return a per-workspace file lock to serialise promote operations."""
    return FileLock(workspace_dir / ".git" / "promote.lock", timeout=120)


def promote(
    workspace_dir: Path,
    worktree_path: Path,
    project_id: str,
    *,
    abort: bool = False,
) -> dict:
    """Stateful merge of project branch into main.

    Acquires a per-workspace file lock so concurrent promote calls on the
    same product are serialised.  Returns dict with keys: status, conflicts,
    message.
    """
    branch = f"project/{project_id}"

    with _workspace_lock(workspace_dir):
        # --- Abort mode ---
        if abort:
            if _is_merging(workspace_dir):
                _git(["merge", "--abort"], workspace_dir)
                return {"status": "aborted", "conflicts": [], "message": "Merge aborted."}
            return {"status": "aborted", "conflicts": [], "message": "No merge in progress."}

        # --- Resume after conflict resolution ---
        if _is_merging(workspace_dir):
            if _has_conflict_markers(workspace_dir):
                conflicts = _parse_conflicts(workspace_dir)
                return {
                    "status": "conflict",
                    "conflicts": conflicts,
                    "message": "Conflicts still present.",
                }
            # All resolved — stage and finalize
            _git(["add", "-A"], workspace_dir)
            _git(["commit", "--no-edit"], workspace_dir)
            return {"status": "merged", "conflicts": [], "message": "Merge completed after conflict resolution."}

        # --- Normal flow: sync main into branch, then merge branch into main ---

        # Step 1: merge main into project branch (sync — best effort)
        # If the sync conflicts, abort it to avoid leaving the worktree dirty.
        # The promote still works because Step 3 merges the branch HEAD (not
        # the worktree state) into main.
        sync = _git(["merge", "main", "--no-edit"], worktree_path, check=False)
        if sync.returncode != 0:
            _git(["merge", "--abort"], worktree_path, check=False)
            logger.debug("promote: sync merge into branch failed for {}, proceeding", branch)

        # Step 2: check if branch has anything beyond main
        result = _git(["log", f"main..{branch}", "--oneline"], workspace_dir)
        if not result.stdout.strip():
            return {"status": "nothing", "conflicts": [], "message": "Nothing to merge."}

        # Step 3: merge project branch into main
        merge = _git(["merge", branch, "--no-edit"], workspace_dir, check=False)

        if merge.returncode == 0:
            return {"status": "merged", "conflicts": [], "message": "Branch merged into main."}

        # Conflict
        conflicts = _parse_conflicts(workspace_dir)
        return {
            "status": "conflict",
            "conflicts": conflicts,
            "message": "Merge conflicts detected.",
        }


# ---------------------------------------------------------------------------
# Context injection helpers
# ---------------------------------------------------------------------------


def format_workspace_context(worktree_path: str, product_name: str, file_count: int) -> str:
    """Build the context string injected into task descriptions."""
    return (
        f'[Product "{product_name}" workspace: {worktree_path} ({file_count} files)\n'
        f" Read and write files here using your normal tools.\n"
        f" When changes are ready, call promote_to_product() to merge into the product.]"
    )


def count_worktree_files(worktree_path: Path) -> int:
    """Count user-facing files in a worktree (excluding .git, README.md)."""
    count = 0
    for f in worktree_path.rglob("*"):
        if f.is_file() and ".git" not in f.parts and f.name != "README.md":
            count += 1
    return count
