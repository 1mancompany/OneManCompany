#!/bin/bash
# General AI Assistant — Ralph-style agent loop
# Iteratively runs the standalone agent, checking for task completion each round.
#
# Usage:
#   ./launch.sh <employee_dir>  # SubprocessExecutor mode
#   ./launch.sh [max_iterations]
#   ./launch.sh 20              # run up to 20 iterations
#   OMC_TASK_DESCRIPTION_FILE=/tmp/task.txt ./launch.sh
#   OMC_TASK_DESCRIPTION="Research this project" ./launch.sh
#   TASK="Research this project" ./launch.sh   # legacy fallback

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -n "${OMC_PYTHON_EXECUTABLE:-}" ]; then
  PYTHON_EXECUTABLE="$OMC_PYTHON_EXECUTABLE"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_EXECUTABLE="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
  PYTHON_EXECUTABLE="$(command -v python)"
else
  echo "No Python interpreter found. Set OMC_PYTHON_EXECUTABLE." >&2
  exit 1
fi
if [ -n "${OMC_EMPLOYEE_ID:-}" ]; then
  MAX_ITERATIONS="${OMC_MAX_ITERATIONS:-10}"
else
  MAX_ITERATIONS="${OMC_MAX_ITERATIONS:-${1:-10}}"
fi
PROGRESS_FILE="$SCRIPT_DIR/progress.log"

# Initialize progress log
if [ ! -f "$PROGRESS_FILE" ]; then
  echo "# Agent Progress Log" > "$PROGRESS_FILE"
  echo "Started: $(date)" >> "$PROGRESS_FILE"
  echo "---" >> "$PROGRESS_FILE"
fi

# Resolve task using the SubprocessExecutor protocol, then legacy fallbacks.
if [ -n "${OMC_TASK_DESCRIPTION_FILE:-}" ] && [ -f "$OMC_TASK_DESCRIPTION_FILE" ]; then
  TASK="$(cat "$OMC_TASK_DESCRIPTION_FILE")"
elif [ -n "${OMC_TASK_DESCRIPTION:-}" ]; then
  TASK="$OMC_TASK_DESCRIPTION"
elif [ -n "${TASK:-}" ]; then
  :
elif [ -f "$SCRIPT_DIR/task.txt" ]; then
  TASK="$(cat "$SCRIPT_DIR/task.txt")"
else
  echo "No task provided. Set OMC_TASK_DESCRIPTION_FILE, OMC_TASK_DESCRIPTION, TASK, or create task.txt"
  exit 1
fi

echo "Starting agent loop — Max iterations: $MAX_ITERATIONS"
echo "Task: $TASK"
echo ""

for i in $(seq 1 "$MAX_ITERATIONS"); do
  echo "==============================================================="
  echo "  Iteration $i of $MAX_ITERATIONS"
  echo "==============================================================="

  # Build the prompt: task + progress context
  PROMPT="$TASK"
  if [ -f "$PROGRESS_FILE" ] && [ "$(wc -l < "$PROGRESS_FILE")" -gt 3 ]; then
    PROMPT="$PROMPT

--- Previous Progress ---
$(tail -50 "$PROGRESS_FILE")
---
Continue from where you left off. When all tasks are complete, output <done>COMPLETE</done> as the last line."
  else
    PROMPT="$PROMPT

When all tasks are complete, output <done>COMPLETE</done> as the last line."
  fi

  # Run the standalone agent
  OUTPUT=$(echo "$PROMPT" | "$PYTHON_EXECUTABLE" "$SCRIPT_DIR/run.py" 2>&1 | tee /dev/stderr) || true

  # Log progress
  echo "" >> "$PROGRESS_FILE"
  echo "## Iteration $i — $(date)" >> "$PROGRESS_FILE"
  echo "$OUTPUT" | tail -20 >> "$PROGRESS_FILE"

  # Check for completion signal
  if echo "$OUTPUT" | grep -q "<done>COMPLETE</done>"; then
    echo ""
    echo "Agent completed all tasks at iteration $i."
    exit 0
  fi

  echo "Iteration $i complete. Continuing..."
  sleep 2
done

echo ""
echo "Reached max iterations ($MAX_ITERATIONS) without completion."
echo "Check $PROGRESS_FILE for status."
exit 1
