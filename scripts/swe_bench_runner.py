#!/usr/bin/env python3
"""SWE-bench evaluation runner for OneManCompany.

Drives SWE-bench tasks through the OMC CEO task API, collects patches,
and outputs predictions.json for SWE-bench harness evaluation.

Supports batch submission: tasks are submitted in batches (--batch-size),
then polled concurrently until all complete or timeout.

Limitations:
- If the EA asks the CEO for clarification, the runner cannot auto-dismiss
  the prompt. The task will timeout and collect whatever partial diff exists.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from dataclasses import asdict, dataclass
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
    p.add_argument("--batch-size", type=int, default=1,
                   help="Number of tasks to submit before polling (default: 1 = sequential)")
    return p.parse_args(argv)


# ---------------------------------------------------------------------------
# Predictions I/O (resume support)
# ---------------------------------------------------------------------------

def load_predictions(path: Path) -> list[dict]:
    """Load existing predictions from JSON file."""
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        backup = path.with_suffix(".json.bak")
        print(f"  [WARN] Corrupt predictions file, backing up to {backup}: {e}")
        path.rename(backup)
        return []


def save_prediction(path: Path, pred: Prediction) -> None:
    """Save a prediction to the JSON file (atomic write, deduplicates by instance_id)."""
    existing = load_predictions(path)
    # Remove any previous entry for this instance_id (e.g. replacing an empty-patch retry)
    existing = [p for p in existing if p["instance_id"] != pred.instance_id]
    existing.append(asdict(pred))
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(existing, indent=2))
    tmp.rename(path)


def get_completed_ids(predictions: list[dict]) -> set[str]:
    """Extract instance_ids that are truly done (have a non-empty patch).

    Predictions with empty model_patch are considered retriable — they will
    be re-submitted on the next run.
    """
    return {p["instance_id"] for p in predictions if p.get("model_patch")}


# ---------------------------------------------------------------------------
# Git operations
# ---------------------------------------------------------------------------

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


def _reset_repo(repo_dir: Path, base_commit: str) -> bool:
    """Reset a repo to clean state at base_commit. Returns True on success."""
    try:
        subprocess.run(
            ["git", "-C", str(repo_dir), "checkout", "--quiet", base_commit],
            check=True, capture_output=True, timeout=30,
        )
        subprocess.run(
            ["git", "-C", str(repo_dir), "reset", "--hard", "HEAD"],
            check=True, capture_output=True, timeout=30,
        )
        subprocess.run(
            ["git", "-C", str(repo_dir), "clean", "-fd", "--quiet"],
            check=True, capture_output=True, timeout=30,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        print(f"  [ERROR] _reset_repo failed: {e}")
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


# ---------------------------------------------------------------------------
# Task description
# ---------------------------------------------------------------------------

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
        f"- Do NOT modify test files\n"
        f"- Run existing relevant unit tests to verify your fix does not break anything\n"
        f"- Ensure all tests pass before considering the task complete\n"
        f"- Do NOT hack or manipulate test results - the final evaluation uses a separate test suite that you cannot see"
    )


# ---------------------------------------------------------------------------
# OMC API client
# ---------------------------------------------------------------------------

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
            except httpx.HTTPError as e:
                print(f"  [WARN] Poll error (will retry): {e}")
            time.sleep(interval)

    return "timeout"


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

@dataclass
class SubmittedTask:
    """Tracks a submitted task awaiting completion."""
    instance_id: str
    repo_dir: Path
    project_id: str
    iteration_id: str


def prepare_and_submit(
    instance: dict,
    instances_dir: Path,
    server_url: str,
) -> SubmittedTask | Prediction | None:
    """Clone repo and submit task. Returns SubmittedTask on success,
    Prediction (empty patch) on submit failure, or None on clone failure."""
    iid = instance["instance_id"]
    repo_slug = instance["repo"]  # e.g. "astropy/astropy"
    base_commit = instance["base_commit"]
    problem = instance["problem_statement"]

    repo_dir = instances_dir / iid / "repo"
    clone_url = f"https://github.com/{repo_slug}.git"

    print(f"\n  [{iid}] Repo: {repo_slug} @ {base_commit[:8]}")

    # 1. Clone repo (skip if already exists from a previous partial run)
    if not repo_dir.exists():
        print(f"  [{iid}] Cloning {clone_url}...")
        if not clone_repo(clone_url, base_commit, repo_dir):
            print(f"  [{iid}] [SKIP] Clone failed")
            return None
    else:
        print(f"  [{iid}] Repo exists, resetting to clean state")
        if not _reset_repo(repo_dir, base_commit):
            print(f"  [{iid}] [SKIP] Reset failed")
            return None

    # 2. Build task description and submit
    task_desc = build_task_description(repo_slug, str(repo_dir.resolve()), problem)

    print(f"  [{iid}] Submitting task to OMC...")
    try:
        project_id, iteration_id = submit_task(server_url, task_desc)
        print(f"  [{iid}] Project: {project_id}/{iteration_id}")
        return SubmittedTask(
            instance_id=iid,
            repo_dir=repo_dir,
            project_id=project_id,
            iteration_id=iteration_id,
        )
    except Exception as e:
        print(f"  [{iid}] [ERROR] Submit failed: {e}")
        return Prediction(instance_id=iid, model_patch="")


def poll_and_collect_batch(
    tasks: list[SubmittedTask],
    server_url: str,
    timeout: int,
    interval: float = 30.0,
) -> list[Prediction]:
    """Poll all submitted tasks concurrently until all finish or timeout."""
    deadline = time.time() + timeout
    terminal = {"completed", "failed", "cancelled"}
    pending = {t.instance_id: t for t in tasks}
    results: list[Prediction] = []

    print(f"\n  Polling {len(pending)} tasks (timeout={timeout}s)...")

    with httpx.Client(timeout=30) as client:
        while pending and time.time() < deadline:
            finished_ids = []
            for iid, task in pending.items():
                try:
                    resp = client.get(
                        f"{server_url}/api/projects/{task.project_id}/{task.iteration_id}"
                    )
                    if resp.status_code == 200:
                        status = resp.json().get("status", "")
                        if status in terminal:
                            print(f"  [{iid}] Status: {status}")
                            patch = collect_patch(task.repo_dir)
                            patch_lines = len(patch.splitlines()) if patch else 0
                            print(f"  [{iid}] Patch: {patch_lines} lines")
                            results.append(Prediction(instance_id=iid, model_patch=patch))
                            finished_ids.append(iid)
                except httpx.HTTPError as e:
                    print(f"  [{iid}] [WARN] Poll error (will retry): {e}")

            for iid in finished_ids:
                del pending[iid]

            if pending:
                time.sleep(interval)

    # Timeout: collect whatever diff exists for remaining tasks
    for iid, task in pending.items():
        print(f"  [{iid}] Status: timeout")
        patch = collect_patch(task.repo_dir)
        patch_lines = len(patch.splitlines()) if patch else 0
        print(f"  [{iid}] Patch: {patch_lines} lines")
        results.append(Prediction(instance_id=iid, model_patch=patch))

    return results


def run_instance(
    instance: dict,
    instances_dir: Path,
    server_url: str,
    timeout: int,
) -> Prediction | None:
    """Run a single SWE-bench instance (sequential mode). Returns Prediction or None."""
    result = prepare_and_submit(instance, instances_dir, server_url)

    if result is None:
        return None
    if isinstance(result, Prediction):
        return result

    # It's a SubmittedTask — poll and collect
    preds = poll_and_collect_batch([result], server_url, timeout)
    return preds[0] if preds else None


def _poll_check_finished(
    inflight: dict[str, SubmittedTask],
    server_url: str,
    client: httpx.Client,
) -> list[Prediction]:
    """One poll pass: check all inflight tasks, return finished ones."""
    terminal = {"completed", "failed", "cancelled"}
    finished: list[Prediction] = []
    finished_ids: list[str] = []

    for iid, task in inflight.items():
        try:
            resp = client.get(
                f"{server_url}/api/projects/{task.project_id}/{task.iteration_id}"
            )
            if resp.status_code == 200:
                status = resp.json().get("status", "")
                if status in terminal:
                    print(f"  [{iid}] Status: {status}")
                    patch = collect_patch(task.repo_dir)
                    patch_lines = len(patch.splitlines()) if patch else 0
                    print(f"  [{iid}] Patch: {patch_lines} lines")
                    finished.append(Prediction(instance_id=iid, model_patch=patch))
                    finished_ids.append(iid)
        except httpx.HTTPError as e:
            print(f"  [{iid}] [WARN] Poll error (will retry): {e}")

    for iid in finished_ids:
        del inflight[iid]

    return finished


def _collect_timed_out(
    inflight: dict[str, SubmittedTask],
    deadlines: dict[str, float],
    now: float,
) -> list[str]:
    """Return instance IDs that have exceeded their deadline."""
    return [iid for iid in inflight if now >= deadlines.get(iid, float("inf"))]


def main() -> None:
    args = parse_args()
    workdir = Path(args.workdir)
    instances_dir = workdir / "instances"
    predictions_path = workdir / "predictions.json"
    window_size = max(1, args.batch_size)
    poll_interval = 30.0

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
    print(f"Running: {len(instances)} instances (window={window_size})")

    # Sliding window: keep up to window_size tasks inflight at all times
    queue = list(instances)  # remaining instances to submit
    inflight: dict[str, SubmittedTask] = {}  # instance_id → SubmittedTask
    deadlines: dict[str, float] = {}  # instance_id → deadline timestamp
    total = len(instances)
    done_count = 0

    with httpx.Client(timeout=30) as client:
        while queue or inflight:
            # Fill window: submit tasks until we reach window_size
            while queue and len(inflight) < window_size:
                instance = queue.pop(0)
                result = prepare_and_submit(instance, instances_dir, args.server_url)
                if result is None:
                    done_count += 1
                    continue  # clone failed, skip
                if isinstance(result, Prediction):
                    save_prediction(predictions_path, result)
                    print(f"  Saved prediction for {result.instance_id} (submit failed)")
                    done_count += 1
                    continue
                inflight[result.instance_id] = result
                deadlines[result.instance_id] = time.time() + args.timeout

            if not inflight:
                break

            # Poll all inflight tasks
            finished = _poll_check_finished(inflight, args.server_url, client)
            for pred in finished:
                save_prediction(predictions_path, pred)
                deadlines.pop(pred.instance_id, None)
                done_count += 1
                print(f"  Saved prediction for {pred.instance_id} [{done_count}/{total}]")

            # Check for timeouts
            now = time.time()
            timed_out_ids = _collect_timed_out(inflight, deadlines, now)
            for iid in timed_out_ids:
                task = inflight.pop(iid)
                deadlines.pop(iid, None)
                print(f"  [{iid}] Status: timeout")
                patch = collect_patch(task.repo_dir)
                patch_lines = len(patch.splitlines()) if patch else 0
                print(f"  [{iid}] Patch: {patch_lines} lines")
                pred = Prediction(instance_id=iid, model_patch=patch)
                save_prediction(predictions_path, pred)
                done_count += 1
                print(f"  Saved prediction for {iid} [{done_count}/{total}]")

            if inflight:
                time.sleep(poll_interval)

    # Summary
    final = load_predictions(predictions_path)
    non_empty = sum(1 for p in final if p["model_patch"])
    print(f"\n{'='*60}")
    print(f"  Done! {len(final)} predictions total, {non_empty} with patches")
    print(f"  Output: {predictions_path}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
