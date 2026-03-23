# SWE-bench Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an external runner script that drives SWE-bench evaluation through the OMC CEO task API.

**Architecture:** A single Python script (`scripts/swe_bench_runner.py`) loads SWE-bench instances from HuggingFace, clones each repo, submits a fix-this-issue task via `POST /api/ceo/task`, polls for completion, collects `git diff` as patch, and outputs `predictions.json` for SWE-bench harness evaluation.

**Tech Stack:** Python 3.12, `datasets` (HuggingFace), `httpx`, `subprocess` (git), `argparse`

---

## File Structure

```
scripts/
└── swe_bench_runner.py          ← Main runner script (single file)
tests/unit/scripts/
└── test_swe_bench_runner.py     ← Unit tests
```

This is a standalone script with no modifications to OMC core code.

---

### Task 1: Core Data Structures and CLI Parsing

**Files:**
- Create: `scripts/swe_bench_runner.py`
- Create: `tests/unit/scripts/__init__.py`
- Create: `tests/unit/scripts/test_swe_bench_runner.py`

- [ ] **Step 1: Write the failing test for CLI argument parsing**

```python
# tests/unit/scripts/test_swe_bench_runner.py
import json
import subprocess

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "scripts"))


class TestParseArgs:
    def test_defaults(self):
        from swe_bench_runner import parse_args

        args = parse_args([])
        assert args.dataset == "princeton-nlp/SWE-bench_Verified"
        assert args.split == "test"
        assert args.workdir == "swe_bench_workdir"
        assert args.server_url == "http://localhost:8000"
        assert args.timeout == 1800
        assert args.max_tasks is None

    def test_custom_args(self):
        from swe_bench_runner import parse_args

        args = parse_args([
            "--dataset", "princeton-nlp/SWE-bench_Verified",
            "--workdir", "/tmp/bench",
            "--timeout", "600",
            "--max-tasks", "5",
        ])
        assert args.dataset == "princeton-nlp/SWE-bench_Verified"
        assert args.workdir == "/tmp/bench"
        assert args.timeout == 600
        assert args.max_tasks == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/scripts/test_swe_bench_runner.py::TestParseArgs -v`
Expected: FAIL — `ModuleNotFoundError` or `ImportError`

- [ ] **Step 3: Implement parse_args and Prediction dataclass**

```python
#!/usr/bin/env python3
"""SWE-bench evaluation runner for OneManCompany.

Drives SWE-bench tasks through the OMC CEO task API, collects patches,
and outputs predictions.json for SWE-bench harness evaluation.

Limitations:
- If the EA asks the CEO for clarification, the runner cannot auto-dismiss
  the prompt. The task will timeout and collect whatever partial diff exists.
- Tasks run sequentially (one at a time) to avoid collisions.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx


@dataclass
class Prediction:
    instance_id: str
    model_name_or_path: str = "OneManCompany"
    model_patch: str = ""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run SWE-bench evaluation via OMC")
    p.add_argument("--dataset", default="princeton-nlp/SWE-bench_Verified",
                   help="HuggingFace dataset name")
    p.add_argument("--split", default="test", help="Dataset split")
    p.add_argument("--workdir", default="swe_bench_workdir",
                   help="Working directory for cloned repos and outputs")
    p.add_argument("--server-url", default="http://localhost:8000",
                   help="OMC server URL")
    p.add_argument("--timeout", type=int, default=1800,
                   help="Per-task timeout in seconds (default: 1800 = 30min)")
    p.add_argument("--max-tasks", type=int, default=None,
                   help="Max number of tasks to run (for testing)")
    return p.parse_args(argv)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/scripts/test_swe_bench_runner.py::TestParseArgs -v`
Expected: PASS

- [ ] **Step 5: Create test package init file**

```bash
touch tests/unit/scripts/__init__.py
```

- [ ] **Step 6: Commit**

```bash
git add scripts/swe_bench_runner.py tests/unit/scripts/
git commit -m "feat(swe-bench): add CLI parsing and Prediction dataclass"
```

---

### Task 2: Predictions File I/O (Resume Support)

**Files:**
- Modify: `scripts/swe_bench_runner.py`
- Modify: `tests/unit/scripts/test_swe_bench_runner.py`

- [ ] **Step 1: Write failing tests for predictions load/save**

```python
class TestPredictionsIO:
    def test_load_empty(self, tmp_path):
        from swe_bench_runner import load_predictions

        path = tmp_path / "predictions.json"
        result = load_predictions(path)
        assert result == []

    def test_load_existing(self, tmp_path):
        from swe_bench_runner import load_predictions

        path = tmp_path / "predictions.json"
        path.write_text(json.dumps([
            {"instance_id": "foo__bar-123", "model_name_or_path": "OneManCompany", "model_patch": "diff..."}
        ]))
        result = load_predictions(path)
        assert len(result) == 1
        assert result[0]["instance_id"] == "foo__bar-123"

    def test_save_prediction(self, tmp_path):
        from swe_bench_runner import Prediction, save_prediction

        path = tmp_path / "predictions.json"
        pred = Prediction(instance_id="foo__bar-123", model_patch="diff --git ...")
        save_prediction(path, pred)
        data = json.loads(path.read_text())
        assert len(data) == 1
        assert data[0]["instance_id"] == "foo__bar-123"

    def test_save_appends(self, tmp_path):
        from swe_bench_runner import Prediction, save_prediction

        path = tmp_path / "predictions.json"
        save_prediction(path, Prediction(instance_id="a", model_patch="diff1"))
        save_prediction(path, Prediction(instance_id="b", model_patch="diff2"))
        data = json.loads(path.read_text())
        assert len(data) == 2

    def test_completed_ids(self, tmp_path):
        from swe_bench_runner import load_predictions, get_completed_ids

        path = tmp_path / "predictions.json"
        path.write_text(json.dumps([
            {"instance_id": "a", "model_name_or_path": "x", "model_patch": ""},
            {"instance_id": "b", "model_name_or_path": "x", "model_patch": "diff"},
        ]))
        preds = load_predictions(path)
        assert get_completed_ids(preds) == {"a", "b"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/scripts/test_swe_bench_runner.py::TestPredictionsIO -v`
Expected: FAIL

- [ ] **Step 3: Implement predictions I/O**

Add to `scripts/swe_bench_runner.py`:

```python
def load_predictions(path: Path) -> list[dict]:
    """Load existing predictions from JSON file."""
    if not path.exists():
        return []
    return json.loads(path.read_text())


def save_prediction(path: Path, pred: Prediction) -> None:
    """Append a prediction to the JSON file."""
    existing = load_predictions(path)
    existing.append(asdict(pred))
    path.write_text(json.dumps(existing, indent=2))


def get_completed_ids(predictions: list[dict]) -> set[str]:
    """Extract instance_ids already in predictions."""
    return {p["instance_id"] for p in predictions}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/scripts/test_swe_bench_runner.py::TestPredictionsIO -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/swe_bench_runner.py tests/unit/scripts/test_swe_bench_runner.py
git commit -m "feat(swe-bench): add predictions file I/O with resume support"
```

---

### Task 3: Git Clone and Patch Collection

**Files:**
- Modify: `scripts/swe_bench_runner.py`
- Modify: `tests/unit/scripts/test_swe_bench_runner.py`

- [ ] **Step 1: Write failing tests for clone_repo and collect_patch**

```python
class TestGitOps:
    def test_clone_repo(self, tmp_path):
        """Test clone + checkout using a local bare repo as source."""
        from swe_bench_runner import clone_repo

        # Create a local repo to clone from
        src = tmp_path / "source"
        src.mkdir()
        subprocess.run(["git", "init", str(src)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(src), "config", "user.email", "test@test.com"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(src), "config", "user.name", "Test"], check=True, capture_output=True)
        (src / "file.py").write_text("v1")
        subprocess.run(["git", "-C", str(src), "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(src), "commit", "-m", "init"], check=True, capture_output=True)
        commit = subprocess.run(
            ["git", "-C", str(src), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()

        dest = tmp_path / "dest"
        result = clone_repo(str(src), commit, dest)
        assert result is True
        assert (dest / "file.py").read_text() == "v1"

    def test_collect_patch_no_changes(self, tmp_path):
        from swe_bench_runner import collect_patch

        # Init a repo with no changes
        subprocess.run(["git", "init", str(tmp_path / "repo")], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(tmp_path / "repo"), "config", "user.email", "t@t.com"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(tmp_path / "repo"), "config", "user.name", "T"], check=True, capture_output=True)
        (tmp_path / "repo" / "f.py").write_text("orig")
        subprocess.run(["git", "-C", str(tmp_path / "repo"), "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(tmp_path / "repo"), "commit", "-m", "init"], check=True, capture_output=True)

        patch = collect_patch(tmp_path / "repo")
        assert patch == ""

    def test_collect_patch_with_modifications(self, tmp_path):
        from swe_bench_runner import collect_patch

        repo = tmp_path / "repo"
        subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t.com"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "T"], check=True, capture_output=True)
        (repo / "f.py").write_text("orig")
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], check=True, capture_output=True)

        # Modify a file
        (repo / "f.py").write_text("fixed")
        patch = collect_patch(repo)
        assert "diff --git" in patch
        assert "fixed" in patch

    def test_collect_patch_with_new_file(self, tmp_path):
        from swe_bench_runner import collect_patch

        repo = tmp_path / "repo"
        subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t.com"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "T"], check=True, capture_output=True)
        (repo / "f.py").write_text("orig")
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], check=True, capture_output=True)

        # Add a new file
        (repo / "new_file.py").write_text("new content")
        patch = collect_patch(repo)
        assert "new_file.py" in patch
        assert "new content" in patch
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/scripts/test_swe_bench_runner.py::TestGitOps -v`
Expected: FAIL

- [ ] **Step 3: Implement git operations**

Add to `scripts/swe_bench_runner.py`:

```python
def clone_repo(repo_url: str, base_commit: str, dest: Path) -> bool:
    """Clone a repo and checkout a specific commit. Returns True on success."""
    try:
        subprocess.run(
            ["git", "clone", "--quiet", repo_url, str(dest)],
            check=True, capture_output=True, text=True, timeout=300,
        )
        subprocess.run(
            ["git", "-C", str(dest), "checkout", "--quiet", base_commit],
            check=True, capture_output=True, text=True, timeout=60,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        print(f"  [ERROR] Clone/checkout failed: {e}")
        return False


def collect_patch(repo_dir: Path) -> str:
    """Collect git diff including new files. Returns unified diff string."""
    try:
        # Stage everything (including new files)
        subprocess.run(
            ["git", "-C", str(repo_dir), "add", "-A"],
            check=True, capture_output=True, timeout=30,
        )
        # Diff staged changes vs HEAD
        result = subprocess.run(
            ["git", "-C", str(repo_dir), "diff", "--cached", "HEAD"],
            capture_output=True, text=True, check=True, timeout=30,
        )
        patch = result.stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        print(f"  [ERROR] collect_patch failed: {e}")
        patch = ""
    finally:
        # Always unstage to leave working tree intact
        subprocess.run(
            ["git", "-C", str(repo_dir), "reset", "HEAD", "--quiet"],
            capture_output=True, timeout=30,
        )
    return patch
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/scripts/test_swe_bench_runner.py::TestGitOps -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/swe_bench_runner.py tests/unit/scripts/test_swe_bench_runner.py
git commit -m "feat(swe-bench): add git clone and patch collection"
```

---

### Task 4: Task Description Builder

**Files:**
- Modify: `scripts/swe_bench_runner.py`
- Modify: `tests/unit/scripts/test_swe_bench_runner.py`

- [ ] **Step 1: Write failing test**

```python
class TestTaskDescription:
    def test_build_task_description(self):
        from swe_bench_runner import build_task_description

        desc = build_task_description(
            repo_name="astropy/astropy",
            repo_path="/tmp/bench/instances/astropy__astropy-12907/repo",
            problem_statement="When I run `astropy.table.Table()` it crashes with...",
        )
        assert "[SWE-Bench]" in desc
        assert "astropy/astropy" in desc
        assert "/tmp/bench/instances/astropy__astropy-12907/repo" in desc
        assert "When I run" in desc
        assert "Do NOT commit" in desc
        assert "Do NOT modify test files" in desc
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/scripts/test_swe_bench_runner.py::TestTaskDescription -v`
Expected: FAIL

- [ ] **Step 3: Implement**

```python
def build_task_description(repo_name: str, repo_path: str, problem_statement: str) -> str:
    """Build the natural-language task description for the CEO API."""
    return (
        f"[SWE-Bench] Fix issue in {repo_name}\n\n"
        f"Repository path: {repo_path}\n\n"
        f"Issue:\n{problem_statement}\n\n"
        f"Requirements:\n"
        f"- Fix the issue described above by modifying the repository code\n"
        f"- Work directly in the repository directory specified above\n"
        f"- Do NOT commit your changes - just modify the files\n"
        f"- Do NOT modify test files"
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/scripts/test_swe_bench_runner.py::TestTaskDescription -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/swe_bench_runner.py tests/unit/scripts/test_swe_bench_runner.py
git commit -m "feat(swe-bench): add task description builder"
```

---

### Task 5: OMC API Client (Submit + Poll)

**Files:**
- Modify: `scripts/swe_bench_runner.py`
- Modify: `tests/unit/scripts/test_swe_bench_runner.py`

- [ ] **Step 1: Write failing tests for submit and poll**

```python
class TestOMCClient:
    def test_submit_task(self, httpx_mock):
        """Test submitting a task via the CEO API."""
        from swe_bench_runner import submit_task

        # httpx_mock is from pytest-httpx; if not available, use unittest.mock
        # Fallback: mock httpx.Client directly
        import unittest.mock as mock

        mock_response = mock.MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "project_id": "swe_astropy_12907",
            "iteration_id": "iter_001",
        }

        with mock.patch("swe_bench_runner.httpx") as mock_httpx:
            client = mock.MagicMock()
            mock_httpx.Client.return_value.__enter__ = mock.MagicMock(return_value=client)
            mock_httpx.Client.return_value.__exit__ = mock.MagicMock(return_value=False)
            client.post.return_value = mock_response

            pid, iid = submit_task("http://localhost:8000", "Fix the bug")
            assert pid == "swe_astropy_12907"
            assert iid == "iter_001"

            # Verify it sent FormData with 'task' field
            call_args = client.post.call_args
            assert "/api/ceo/task" in call_args[0][0]

    def test_poll_status_completed(self):
        from swe_bench_runner import poll_until_done
        import unittest.mock as mock

        mock_response = mock.MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "completed"}

        with mock.patch("swe_bench_runner.httpx") as mock_httpx:
            client = mock.MagicMock()
            mock_httpx.Client.return_value.__enter__ = mock.MagicMock(return_value=client)
            mock_httpx.Client.return_value.__exit__ = mock.MagicMock(return_value=False)
            client.get.return_value = mock_response

            status = poll_until_done("http://localhost:8000", "proj1", "iter_001", timeout=10, interval=0.1)
            assert status == "completed"

    def test_poll_status_timeout(self):
        from swe_bench_runner import poll_until_done
        import unittest.mock as mock

        mock_response = mock.MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "in_progress"}

        with mock.patch("swe_bench_runner.httpx") as mock_httpx:
            client = mock.MagicMock()
            mock_httpx.Client.return_value.__enter__ = mock.MagicMock(return_value=client)
            mock_httpx.Client.return_value.__exit__ = mock.MagicMock(return_value=False)
            client.get.return_value = mock_response

            status = poll_until_done("http://localhost:8000", "proj1", "iter_001", timeout=1, interval=0.2)
            assert status == "timeout"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/scripts/test_swe_bench_runner.py::TestOMCClient -v`
Expected: FAIL

- [ ] **Step 3: Implement submit and poll**

```python
def submit_task(server_url: str, task_description: str) -> tuple[str, str]:
    """Submit a task via the CEO API. Returns (project_id, iteration_id)."""
    with httpx.Client(timeout=60) as client:
        resp = client.post(
            f"{server_url}/api/ceo/task",
            data={"task": task_description, "mode": "standard"},
        )
        resp.raise_for_status()
        body = resp.json()
        if "error" in body:
            raise RuntimeError(f"Task submission failed: {body['error']}")
        return body["project_id"], body["iteration_id"]


def poll_until_done(
    server_url: str,
    project_id: str,
    iteration_id: str,
    timeout: int = 1800,
    interval: float = 30.0,
) -> str:
    """Poll iteration status until completed, failed, or timeout.

    Returns final status string: "completed", "failed", "cancelled", or "timeout".
    """
    deadline = time.time() + timeout
    terminal = {"completed", "failed", "cancelled"}

    with httpx.Client(timeout=30) as client:
        while time.time() < deadline:
            try:
                resp = client.get(f"{server_url}/api/projects/{project_id}/{iteration_id}")
                if resp.status_code == 200:
                    status = resp.json().get("status", "")
                    if status in terminal:
                        return status
            except httpx.HTTPError:
                pass  # Transient error, keep polling
            time.sleep(interval)

    return "timeout"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/scripts/test_swe_bench_runner.py::TestOMCClient -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/swe_bench_runner.py tests/unit/scripts/test_swe_bench_runner.py
git commit -m "feat(swe-bench): add OMC API client (submit + poll)"
```

---

### Task 6: Main Runner Loop

**Files:**
- Modify: `scripts/swe_bench_runner.py`
- Modify: `tests/unit/scripts/test_swe_bench_runner.py`

- [ ] **Step 1: Write failing test for run_instance**

```python
class TestRunInstance:
    def test_run_instance_success(self, tmp_path):
        from swe_bench_runner import run_instance, Prediction
        import unittest.mock as mock

        instance = {
            "instance_id": "test__repo-1",
            "repo": "test/repo",
            "base_commit": "abc123",
            "problem_statement": "Something is broken",
        }
        repo_dir = tmp_path / "instances" / "test__repo-1" / "repo"

        with mock.patch("swe_bench_runner.clone_repo", return_value=True), \
             mock.patch("swe_bench_runner.submit_task", return_value=("proj1", "iter_001")), \
             mock.patch("swe_bench_runner.poll_until_done", return_value="completed"), \
             mock.patch("swe_bench_runner.collect_patch", return_value="diff --git a/f.py"):

            pred = run_instance(instance, tmp_path / "instances", "http://localhost:8000", 1800)
            assert pred is not None
            assert pred.instance_id == "test__repo-1"
            assert pred.model_patch == "diff --git a/f.py"

    def test_run_instance_clone_fails(self, tmp_path):
        from swe_bench_runner import run_instance
        import unittest.mock as mock

        instance = {
            "instance_id": "test__repo-2",
            "repo": "test/repo",
            "base_commit": "abc123",
            "problem_statement": "Bug",
        }

        with mock.patch("swe_bench_runner.clone_repo", return_value=False):
            pred = run_instance(instance, tmp_path / "instances", "http://localhost:8000", 1800)
            assert pred is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/scripts/test_swe_bench_runner.py::TestRunInstance -v`
Expected: FAIL

- [ ] **Step 3: Implement run_instance and main**

```python
def _reset_repo(repo_dir: Path, base_commit: str) -> None:
    """Reset a repo to clean state at base_commit (for reuse after failed runs)."""
    subprocess.run(
        ["git", "-C", str(repo_dir), "checkout", "--quiet", base_commit],
        capture_output=True, timeout=30,
    )
    subprocess.run(
        ["git", "-C", str(repo_dir), "reset", "--hard", "HEAD"],
        capture_output=True, timeout=30,
    )
    subprocess.run(
        ["git", "-C", str(repo_dir), "clean", "-fd", "--quiet"],
        capture_output=True, timeout=30,
    )


def run_instance(
    instance: dict,
    instances_dir: Path,
    server_url: str,
    timeout: int,
) -> Prediction | None:
    """Run a single SWE-bench instance. Returns Prediction or None on clone failure."""
    iid = instance["instance_id"]
    repo_slug = instance["repo"]  # e.g. "astropy/astropy"
    base_commit = instance["base_commit"]
    problem = instance["problem_statement"]

    repo_dir = instances_dir / iid / "repo"
    clone_url = f"https://github.com/{repo_slug}.git"

    print(f"\n{'='*60}")
    print(f"  Instance: {iid}")
    print(f"  Repo: {repo_slug} @ {base_commit[:8]}")
    print(f"{'='*60}")

    # 1. Clone repo (skip if already exists from a previous partial run)
    if not repo_dir.exists():
        print(f"  Cloning {clone_url}...")
        if not clone_repo(clone_url, base_commit, repo_dir):
            print(f"  [SKIP] Clone failed for {iid}")
            return None
    else:
        print(f"  Repo already exists, resetting to clean state")
        _reset_repo(repo_dir, base_commit)

    # 2. Build task description
    task_desc = build_task_description(repo_slug, str(repo_dir.resolve()), problem)

    # 3. Submit to OMC
    print(f"  Submitting task to OMC...")
    try:
        project_id, iteration_id = submit_task(server_url, task_desc)
        print(f"  Project: {project_id}/{iteration_id}")
    except Exception as e:
        print(f"  [ERROR] Submit failed: {e}")
        return Prediction(instance_id=iid, model_patch="")

    # 4. Poll for completion
    print(f"  Polling (timeout={timeout}s)...")
    status = poll_until_done(server_url, project_id, iteration_id, timeout=timeout)
    print(f"  Status: {status}")

    # 5. Collect patch
    patch = collect_patch(repo_dir)
    patch_lines = len(patch.splitlines()) if patch else 0
    print(f"  Patch: {patch_lines} lines")

    return Prediction(instance_id=iid, model_patch=patch)


def main() -> None:
    args = parse_args()
    workdir = Path(args.workdir)
    instances_dir = workdir / "instances"
    predictions_path = workdir / "predictions.json"

    workdir.mkdir(parents=True, exist_ok=True)
    instances_dir.mkdir(parents=True, exist_ok=True)

    # Load dataset
    print(f"Loading dataset: {args.dataset} (split={args.split})...")
    from datasets import load_dataset
    ds = load_dataset(args.dataset, split=args.split)
    print(f"Loaded {len(ds)} instances")

    # Load existing predictions for resume
    existing = load_predictions(predictions_path)
    completed = get_completed_ids(existing)
    print(f"Already completed: {len(completed)} instances")

    # Filter and limit
    instances = [inst for inst in ds if inst["instance_id"] not in completed]
    if args.max_tasks is not None:
        instances = instances[:args.max_tasks]
    print(f"Running: {len(instances)} instances")

    # Run each instance
    for i, instance in enumerate(instances, 1):
        print(f"\n[{i}/{len(instances)}]", end="")
        pred = run_instance(instance, instances_dir, args.server_url, args.timeout)
        if pred is not None:
            save_prediction(predictions_path, pred)
            print(f"  Saved prediction for {pred.instance_id}")

    # Summary
    final = load_predictions(predictions_path)
    non_empty = sum(1 for p in final if p["model_patch"])
    print(f"\n{'='*60}")
    print(f"  Done! {len(final)} predictions total, {non_empty} with patches")
    print(f"  Output: {predictions_path}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/scripts/test_swe_bench_runner.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/swe_bench_runner.py tests/unit/scripts/test_swe_bench_runner.py
git commit -m "feat(swe-bench): add main runner loop with run_instance"
```

---

### Task 7: End-to-End Smoke Test

**Files:**
- Modify: `tests/unit/scripts/test_swe_bench_runner.py`

- [ ] **Step 1: Write integration-style test that mocks only external I/O**

```python
class TestMainFlow:
    def test_main_end_to_end(self, tmp_path, monkeypatch):
        """Smoke test: full flow with mocked dataset and API."""
        from swe_bench_runner import main
        import unittest.mock as mock

        # Mock dataset
        fake_ds = [
            {
                "instance_id": "test__repo-1",
                "repo": "test/repo",
                "base_commit": "abc123",
                "problem_statement": "Bug report",
            },
        ]

        workdir = str(tmp_path / "work")

        monkeypatch.setattr("sys.argv", [
            "swe_bench_runner.py",
            "--workdir", workdir,
            "--max-tasks", "1",
            "--timeout", "5",
        ])

        with mock.patch("swe_bench_runner.clone_repo", return_value=True) as m_clone, \
             mock.patch("swe_bench_runner.submit_task", return_value=("p1", "iter_001")) as m_submit, \
             mock.patch("swe_bench_runner.poll_until_done", return_value="completed") as m_poll, \
             mock.patch("swe_bench_runner.collect_patch", return_value="diff --git fixed") as m_patch, \
             mock.patch("datasets.load_dataset", return_value=fake_ds):

            main()

        # Verify predictions file was created
        preds = json.loads((tmp_path / "work" / "predictions.json").read_text())
        assert len(preds) == 1
        assert preds[0]["instance_id"] == "test__repo-1"
        assert preds[0]["model_patch"] == "diff --git fixed"
```

- [ ] **Step 2: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/scripts/test_swe_bench_runner.py::TestMainFlow -v`
Expected: PASS (this test mocks everything — it validates wiring, not external systems)

- [ ] **Step 3: Commit**

```bash
git add tests/unit/scripts/test_swe_bench_runner.py
git commit -m "test(swe-bench): add end-to-end smoke test"
```

---

### Task 8: Make Script Executable and Add README

**Files:**
- Modify: `scripts/swe_bench_runner.py` (add shebang, chmod)

- [ ] **Step 1: Ensure script is executable**

```bash
chmod +x scripts/swe_bench_runner.py
```

- [ ] **Step 2: Run full test suite**

Run: `.venv/bin/python -m pytest tests/unit/scripts/test_swe_bench_runner.py -v`
Expected: ALL PASS

- [ ] **Step 3: Final commit**

```bash
git add scripts/swe_bench_runner.py
git commit -m "chore(swe-bench): make runner script executable"
```
