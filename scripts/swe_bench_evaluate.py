#!/usr/bin/env python3
"""Evaluate SWE-bench predictions using the official harness.

Wraps swebench.harness.run_evaluation with sensible defaults and
auto-detects Docker socket on macOS.

Usage:
    # Evaluate all predictions in workdir
    python scripts/swe_bench_evaluate.py --workdir ~/swe_bench_workdir_1

    # Evaluate specific instances only
    python scripts/swe_bench_evaluate.py --workdir ~/swe_bench_workdir_1 \
        --instance-ids astropy__astropy-12907 astropy__astropy-13033

    # Re-collect patches before evaluating (if runner timed out early)
    python scripts/swe_bench_evaluate.py --workdir ~/swe_bench_workdir_1 --recollect
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Evaluate SWE-bench predictions via official harness")
    p.add_argument("--workdir", default="swe_bench_workdir",
                   help="Working directory with predictions.json and instances/")
    p.add_argument("--dataset", default="princeton-nlp/SWE-bench_Verified",
                   help="HuggingFace dataset name")
    p.add_argument("--split", default="test", help="Dataset split")
    p.add_argument("--run-id", default="omc_eval",
                   help="Run ID for evaluation results (default: omc_eval)")
    p.add_argument("--max-workers", type=int, default=1,
                   help="Max parallel evaluation workers (default: 1)")
    p.add_argument("--instance-ids", nargs="*", default=None,
                   help="Evaluate only these instance IDs (space separated)")
    p.add_argument("--recollect", action="store_true",
                   help="Re-collect patches from repos before evaluating")
    return p.parse_args(argv)


def _detect_docker_socket() -> str | None:
    """Auto-detect Docker socket path for macOS Docker Desktop."""
    candidates = [
        os.environ.get("DOCKER_HOST", ""),
        f"unix://{Path.home()}/.docker/run/docker.sock",
        "unix:///var/run/docker.sock",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        sock_path = candidate.replace("unix://", "")
        if Path(sock_path).exists():
            return f"unix://{sock_path}"
    return None


def recollect_patches(workdir: Path) -> None:
    """Re-collect git diffs from instance repos into predictions.json."""
    predictions_path = workdir / "predictions.json"
    if not predictions_path.exists():
        print("No predictions.json found, nothing to recollect.")
        return

    preds = json.loads(predictions_path.read_text())
    updated = 0

    for pred in preds:
        iid = pred["instance_id"]
        repo_dir = workdir / "instances" / iid / "repo"
        if not repo_dir.exists():
            continue

        # Stage all, diff, unstage
        subprocess.run(["git", "-C", str(repo_dir), "add", "-A"],
                       capture_output=True, timeout=30)
        result = subprocess.run(
            ["git", "-C", str(repo_dir), "diff", "--cached", "HEAD"],
            capture_output=True, text=True, timeout=30,
        )
        subprocess.run(["git", "-C", str(repo_dir), "reset", "HEAD", "--quiet"],
                       capture_output=True, timeout=30)

        new_patch = result.stdout
        if new_patch and not pred["model_patch"]:
            print(f"  [{iid}] Recollected: {len(new_patch.splitlines())} lines")
            pred["model_patch"] = new_patch
            updated += 1
        elif new_patch != pred["model_patch"] and new_patch:
            print(f"  [{iid}] Updated: {len(new_patch.splitlines())} lines")
            pred["model_patch"] = new_patch
            updated += 1

    if updated:
        tmp = predictions_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(preds, indent=2))
        tmp.rename(predictions_path)
        print(f"  Recollected {updated} patches")
    else:
        print("  All patches already up to date")


def main() -> None:
    args = parse_args()
    workdir = Path(args.workdir).expanduser()
    predictions_path = workdir / "predictions.json"

    if not predictions_path.exists():
        print(f"Error: {predictions_path} not found")
        sys.exit(1)

    # Summary of predictions
    preds = json.loads(predictions_path.read_text())
    non_empty = sum(1 for p in preds if p["model_patch"])
    print(f"Predictions: {len(preds)} total, {non_empty} with patches")

    # Re-collect patches if requested
    if args.recollect:
        print("\nRe-collecting patches from repos...")
        recollect_patches(workdir)
        # Reload
        preds = json.loads(predictions_path.read_text())
        non_empty = sum(1 for p in preds if p["model_patch"])
        print(f"After recollection: {non_empty} with patches")

    if non_empty == 0:
        print("No patches to evaluate.")
        sys.exit(0)

    # Detect Docker socket
    docker_host = _detect_docker_socket()
    if not docker_host:
        print("Error: Docker socket not found. Is Docker running?")
        sys.exit(1)
    print(f"Docker: {docker_host}")

    # Build evaluation command
    cmd = [
        sys.executable, "-m", "swebench.harness.run_evaluation",
        "--dataset_name", args.dataset,
        "--split", args.split,
        "--predictions_path", str(predictions_path),
        "--max_workers", str(args.max_workers),
        "--run_id", args.run_id,
    ]
    if args.instance_ids:
        cmd += ["--instance_ids"] + args.instance_ids

    print(f"\nRunning evaluation...")
    print(f"  Command: {' '.join(cmd)}")

    env = os.environ.copy()
    env["DOCKER_HOST"] = docker_host

    result = subprocess.run(cmd, env=env)

    if result.returncode != 0:
        print(f"\nEvaluation failed with exit code {result.returncode}")
        sys.exit(result.returncode)

    # Look for report files
    print(f"\nEvaluation complete!")
    report_dir = Path(f"./reports/{args.run_id}")
    if report_dir.exists():
        for f in sorted(report_dir.glob("*.json")):
            print(f"  Report: {f}")


if __name__ == "__main__":
    main()
