#!/usr/bin/env bash
set -euo pipefail

# Session logger hook for self-improving-agent.
# Reads JSON from stdin, logs session event to episodic memory.

EVENT="${OMC_HOOK_EVENT:-unknown}"
EMPLOYEE="${OMC_EMPLOYEE_ID:-unknown}"
TASK="${OMC_TASK_ID:-unknown}"
SKILLS_DIR="${OMC_SKILLS_DIR:-}"
TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)

# Memory directory
MEMORY_DIR="${SKILLS_DIR}/self-improving-agent/memory/working"
mkdir -p "$MEMORY_DIR"

# Read stdin (JSON hook input)
INPUT=$(cat)

# Log session event
cat > "$MEMORY_DIR/session_${EVENT}.json" << EOF
{
  "event": "$EVENT",
  "employee_id": "$EMPLOYEE",
  "task_id": "$TASK",
  "timestamp": "$TIMESTAMP",
  "input": $INPUT
}
EOF

echo "[self-improving-agent] Session logged: $EVENT for $EMPLOYEE" >&2
