# SWE-bench Evaluation Design

## Goal

Evaluate the OneManCompany (OMC) system's ability to solve real-world software engineering tasks through its full organizational collaboration flow, measured by SWE-bench benchmark scores.

## Context

SWE-bench is a benchmark of 2294 real GitHub issues from popular Python repositories. Each instance provides a repository, a base commit, and a problem statement (the GitHub issue text). The system must produce a patch that resolves the issue.

OMC is a multi-agent organizational simulator where a CEO delegates tasks to AI employees. Testing on SWE-bench validates whether the CEO → EA → Engineer → QA pipeline can solve real engineering problems end-to-end.

## Constraints

- **OMC system: zero modifications.** The benchmark runner is a pure external driver.
- **Self-hosted employees only.** Claude CLI sessions, no token cost concerns.
- **Dataset:** SWE-bench_Verified (500 tasks) as starting point.
- **Environment isolation:** git clone + checkout per task, no Docker.

## Prerequisites

Before running the benchmark:

1. **Hire two self-hosted employees** (manual, via existing HR flow):
   - **Python SWE Engineer** — reads issue, locates bug, writes patch
   - **QA/Test Engineer** — verifies patch correctness, checks for regressions
2. **OMC server running** with the two employees registered and online.
3. **Python dependencies installed:** `swe-bench`, `datasets` (HuggingFace).

## Architecture

```
swe_bench_runner.py (external script)
        │
        │  HTTP (POST /api/ceo/task, GET /api/projects/{id}/{iter_id})
        ▼
OMC Server (unchanged)
        │
        ▼
CEO task → EA → SWE Engineer (patch) + QA Engineer (verify)
```

The runner script is the only new code. It drives the benchmark by submitting tasks through the same CEO API that the frontend uses.

**Note on EA mediation:** The EA is an autonomous agent that decides how to decompose and dispatch tasks. The runner does not control which employees receive the work. The EA may assign to one or both engineers, or ask the CEO for clarification. The task description is written to be unambiguous enough that the EA should dispatch without questions, but this is best-effort. If the EA creates a CEO inbox prompt (asking for clarification), the runner should auto-dismiss it or timeout.

## Runner Script: `scripts/swe_bench_runner.py`

### Inputs

- `--dataset`: HuggingFace dataset name (default: `princeton-nlp/SWE-bench_Verified`)
- `--split`: Dataset split (default: `test`)
- `--workdir`: Directory for cloned repos and outputs (default: `swe_bench_workdir/`)
- `--server-url`: OMC server URL (default: `http://localhost:8000`)
- `--timeout`: Per-task timeout in seconds (default: `1800` = 30 min)
- `--max-tasks`: Optional cap on number of tasks to run (for testing)

### Output

```
swe_bench_workdir/
├── instances/
│   ├── {instance_id}/
│   │   └── repo/              ← cloned repo at base_commit
│   └── ...
├── predictions.json           ← SWE-bench format predictions
└── results/                   ← harness evaluation output
```

### predictions.json Schema

```json
[
  {
    "instance_id": "astropy__astropy-12907",
    "model_name_or_path": "OneManCompany",
    "model_patch": "diff --git a/astropy/... b/astropy/...\n..."
  }
]
```

### Flow per Instance

```
1. Skip if instance_id already in predictions.json (resume support)
2. Clone repo → checkout base_commit
   - git clone {repo_url} instances/{instance_id}/repo
   - cd repo && git checkout {base_commit}
3. Construct task description (natural language)
4. POST /api/ceo/task (FormData) with:
   - task: constructed description
   - mode: "standard"
   Response returns: { project_id, iteration_id }
5. Poll GET /api/projects/{project_id}/{iteration_id} every 30s until:
   - Iteration status is "completed" → collect patch
   - Timeout (30 min) → collect whatever diff exists
   - Error → record empty patch
6. Collect patch:
   - cd repo
   - git add -A                    (stage everything including new files)
   - git diff --cached HEAD        (diff staged changes vs base commit)
   - git reset HEAD                (unstage, leave working tree intact)
7. Append to predictions.json
```

### Task Description Template

```
[SWE-Bench] Fix issue in {repo_name}

Repository path: {absolute_path_to_repo}
Issue:
{problem_statement}

Requirements:
- Fix the issue described above by modifying the repository code
- Work directly in the repository directory specified above
- Do NOT commit your changes - just modify the files
- Do NOT modify test files
```

### Resume Support

`predictions.json` is written incrementally. On restart, the runner loads existing predictions and skips completed instance_ids. This allows:
- Interrupting and resuming long benchmark runs
- Re-running with `--max-tasks` for incremental testing

### Timeout & Error Handling

| Scenario | Action |
|----------|--------|
| Task completes normally | Collect `git diff --cached`, save prediction |
| Task timeout (30 min) | Collect current diff (partial), continue |
| OMC returns error | Record empty patch, log error, continue |
| Clone fails | Log error, skip instance, continue |
| Runner interrupted (Ctrl+C) | predictions.json already has partial results, resume next run |
| EA asks CEO for clarification | Runner auto-dismisses or timeout triggers |

The runner waits for the previous task to fully complete (or timeout) before submitting the next task. This avoids collisions from overlapping tasks.

## Evaluation

After all predictions collected, use the `swe-bench` package to evaluate:

```python
# Verify the correct evaluation API against installed swe-bench version
# The CLI or Python API may differ across versions
from swebench.harness.run_evaluation import main as run_evaluation
```

Or via CLI (verify exact flags against installed version):

```bash
python -m swebench.harness.run_evaluation \
    --predictions_path swe_bench_workdir/predictions.json \
    --swe_bench_tasks princeton-nlp/SWE-bench_Verified \
    --log_dir swe_bench_workdir/results/logs \
    --testbed swe_bench_workdir/results/testbed
```

**Note:** SWE-bench's test harness may require Docker for test isolation. If so, Docker must be available on the host machine for the *evaluation* step (even though the *task execution* step does not use Docker). The runner script should document this dependency clearly.

Output: per-instance pass/fail, overall resolve rate.

## What This Does NOT Include

- No changes to OMC core code
- No frontend/dashboard for benchmark results (file output only)
- No Docker for task execution (clone + checkout only; Docker may be needed for evaluation harness)
- No employee hiring automation (manual prerequisite)
- No parallel task execution (sequential, one task at a time)
- No per-instance cost/token tracking (could be added later via project archive data)
