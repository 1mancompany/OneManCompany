#!/bin/bash
# General AI Assistant — Ralph-style agent loop
# Iteratively runs the standalone agent, checking for task completion each round.
#
# Usage:
#   ./launch.sh [max_iterations]
#   ./launch.sh 20              # run up to 20 iterations
#   TASK="研究这个项目" ./launch.sh   # pass task via env var

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MAX_ITERATIONS="${1:-10}"
PROGRESS_FILE="$SCRIPT_DIR/progress.log"

# Initialize progress log
if [ ! -f "$PROGRESS_FILE" ]; then
  echo "# Agent Progress Log" > "$PROGRESS_FILE"
  echo "Started: $(date)" >> "$PROGRESS_FILE"
  echo "---" >> "$PROGRESS_FILE"
fi

# Resolve task: env var > first arg > interactive
if [ -z "$TASK" ]; then
  if [ -f "$SCRIPT_DIR/task.txt" ]; then
    TASK="$(cat "$SCRIPT_DIR/task.txt")"
  else
    echo "No task provided. Set TASK env var or create task.txt"
    exit 1
  fi
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
  OUTPUT=$(echo "$PROMPT" | python "$SCRIPT_DIR/run.py" 2>&1 | tee /dev/stderr) || true

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
