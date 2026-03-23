#!/usr/bin/env bash
# SWE-bench: run OMC on tasks then evaluate with official harness.
#
# Usage:
#   ./scripts/swe_bench_run_and_eval.sh                          # 全部 500 题
#   ./scripts/swe_bench_run_and_eval.sh --max-tasks 10           # 跑 10 题
#   ./scripts/swe_bench_run_and_eval.sh --max-tasks 3 --batch-size 3  # 3 题并发提交
#   ./scripts/swe_bench_run_and_eval.sh --workdir ~/my_bench --timeout 600
#
# 支持断点续跑：中断后重新执行同样的命令即可。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PYTHON="${PROJECT_DIR}/.venv/bin/python"

# ── Defaults (can be overridden via flags) ──
WORKDIR="${HOME}/swe_bench_workdir"
SERVER_URL="http://localhost:8000"
TIMEOUT=1800
MAX_TASKS=""
BATCH_SIZE=5
DATASET="princeton-nlp/SWE-bench_Verified"
SPLIT="test"
RUN_ID="omc_eval"
MAX_WORKERS=1
RECOLLECT=true

# ── Parse arguments ──
while [[ $# -gt 0 ]]; do
    case "$1" in
        --workdir)      WORKDIR="$2";      shift 2 ;;
        --server-url)   SERVER_URL="$2";   shift 2 ;;
        --timeout)      TIMEOUT="$2";      shift 2 ;;
        --max-tasks)    MAX_TASKS="$2";    shift 2 ;;
        --batch-size)   BATCH_SIZE="$2";   shift 2 ;;
        --dataset)      DATASET="$2";      shift 2 ;;
        --split)        SPLIT="$2";        shift 2 ;;
        --run-id)       RUN_ID="$2";       shift 2 ;;
        --max-workers)  MAX_WORKERS="$2";  shift 2 ;;
        --no-recollect) RECOLLECT=false;   shift ;;
        -h|--help)
            echo "Usage: $0 [--workdir DIR] [--max-tasks N] [--batch-size N] [--timeout S]"
            echo "          [--server-url URL] [--run-id ID] [--max-workers N] [--no-recollect]"
            exit 0 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

echo "========================================"
echo "  SWE-bench: OMC Run + Evaluate"
echo "========================================"
echo "  Workdir:    ${WORKDIR}"
echo "  Server:     ${SERVER_URL}"
echo "  Timeout:    ${TIMEOUT}s per task"
echo "  Batch size: ${BATCH_SIZE}"
echo "  Dataset:    ${DATASET}"
[ -n "$MAX_TASKS" ] && echo "  Max tasks:  ${MAX_TASKS}"
echo "========================================"

# ── Phase 1: Run OMC on SWE-bench tasks ──
echo ""
echo "▶ Phase 1: Running OMC on SWE-bench tasks..."

RUNNER_CMD=(
    "$PYTHON" "${SCRIPT_DIR}/swe_bench_runner.py"
    --workdir "$WORKDIR"
    --server-url "$SERVER_URL"
    --timeout "$TIMEOUT"
    --batch-size "$BATCH_SIZE"
    --dataset "$DATASET"
    --split "$SPLIT"
)
[ -n "$MAX_TASKS" ] && RUNNER_CMD+=(--max-tasks "$MAX_TASKS")

"${RUNNER_CMD[@]}"

# ── Phase 2: Evaluate predictions ──
echo ""
echo "▶ Phase 2: Evaluating predictions..."

EVAL_CMD=(
    "$PYTHON" "${SCRIPT_DIR}/swe_bench_evaluate.py"
    --workdir "$WORKDIR"
    --dataset "$DATASET"
    --split "$SPLIT"
    --run-id "$RUN_ID"
    --max-workers "$MAX_WORKERS"
)
[ "$RECOLLECT" = true ] && EVAL_CMD+=(--recollect)

"${EVAL_CMD[@]}"

echo ""
echo "========================================"
echo "  All done!"
echo "  Predictions: ${WORKDIR}/predictions.json"
echo "  Reports:     ./reports/${RUN_ID}/"
echo "========================================"
