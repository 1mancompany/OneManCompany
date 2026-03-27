#!/usr/bin/env bash
# launch_template.sh — Template for company-hosted employee task execution.
#
# SubprocessExecutor calling convention:
#   $1 = employee_dir (contains profile.yaml, skills/, progress.log, etc.)
#
# Environment variables (auto-injected by SubprocessExecutor):
#   OMC_EMPLOYEE_ID           — Employee ID (e.g., "00010")
#   OMC_TASK_ID               — Current task ID
#   OMC_PROJECT_ID            — Project ID
#   OMC_PROJECT_DIR           — Project working directory
#   OMC_TASK_DESCRIPTION_FILE — Path to temp file containing task prompt
#   OMC_SERVER_URL            — Company backend URL (e.g., "http://localhost:8000")
#   OMC_MAX_ITERATIONS        — Max iterations (default 20)
#
# Output convention:
#   stdout → JSON: {"output":"...", "model":"...", "input_tokens":N, "output_tokens":N}
#   stderr → Logs (does not affect result parsing)
#   exit 0 → Success, non-zero → Failure
#
# Timeout and cancellation:
#   SubprocessExecutor manages timeout (default 3600s, configured by TaskNode.timeout_seconds).
#   On timeout or cancellation, process receives SIGTERM → SIGKILL after 30s if not exited.
#   Script should handle SIGTERM for cleanup (trap EXIT is sufficient).

set -euo pipefail

EMPLOYEE_DIR="${1:?Usage: launch.sh <employee_dir>}"
EMPLOYEE_DIR="$(cd "$EMPLOYEE_DIR" && pwd)"

MAX_ITERATIONS="${OMC_MAX_ITERATIONS:-20}"

# ---------------------------------------------------------------------------
# Cleanup hook: runs when process receives SIGTERM
# ---------------------------------------------------------------------------
cleanup() {
    # If MCP server or other child processes were started, clean up here
    if [ -n "${MCP_PID:-}" ] && kill -0 "$MCP_PID" 2>/dev/null; then
        kill "$MCP_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT

# ── Read task description from file ──────────────────────────────────────────
TASK_DESC_FILE="${OMC_TASK_DESCRIPTION_FILE:-}"
if [ -z "$TASK_DESC_FILE" ] || [ ! -f "$TASK_DESC_FILE" ]; then
    >&2 echo "ERROR: OMC_TASK_DESCRIPTION_FILE not set or file not found"
    exit 1
fi
OMC_TASK_DESCRIPTION="$(cat "$TASK_DESC_FILE")"

>&2 echo "[launch.sh] Employee=${OMC_EMPLOYEE_ID} Task=${OMC_TASK_ID} PID=$$"
>&2 echo "[launch.sh] Project=${OMC_PROJECT_ID} Dir=${OMC_PROJECT_DIR}"
>&2 echo "[launch.sh] Description: ${OMC_TASK_DESCRIPTION:0:200}"

# ---------------------------------------------------------------------------
# Optional: Start MCP server to provide company tools
# ---------------------------------------------------------------------------
# If your talent needs to call company tools via MCP, uncomment below:
#
# PROJECT_ROOT="$(cd "$EMPLOYEE_DIR/../../../.." && pwd)"
# PYTHON="${PROJECT_ROOT}/.venv/bin/python"
#
# coproc MCP_PROC {
#     exec "$PYTHON" -m onemancompany.tools.mcp.server 2>/dev/null
# }
# MCP_PID=$MCP_PROC_PID
# >&2 echo "[launch.sh] MCP server PID=${MCP_PID}"

# ---------------------------------------------------------------------------
# Main logic: Call LLM to complete the task
# ---------------------------------------------------------------------------
# Implement your agent loop here. Below is a skeleton using curl + OpenRouter:
#
# RESULT=$(curl -s https://openrouter.ai/api/v1/chat/completions \
#     -H "Authorization: Bearer ${OPENROUTER_API_KEY}" \
#     -H "Content-Type: application/json" \
#     -d "{
#       \"model\": \"${LLM_MODEL:-google/gemini-3.1-pro-preview}\",
#       \"messages\": [{\"role\": \"user\", \"content\": $(echo "$OMC_TASK_DESCRIPTION" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')}]
#     }")
#
# OUTPUT=$(echo "$RESULT" | jq -r '.choices[0].message.content // "No output"')
# MODEL=$(echo "$RESULT" | jq -r '.model // ""')
# IN_TOKENS=$(echo "$RESULT" | jq -r '.usage.prompt_tokens // 0')
# OUT_TOKENS=$(echo "$RESULT" | jq -r '.usage.completion_tokens // 0')

# ---------------------------------------------------------------------------
# Output JSON to stdout (SubprocessExecutor parses this format)
# ---------------------------------------------------------------------------
# Replace with your actual agent output:
OUTPUT="Task completed"
MODEL=""
IN_TOKENS=0
OUT_TOKENS=0

cat <<EOF
{"output": $(echo "$OUTPUT" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))'), "model": "$MODEL", "input_tokens": $IN_TOKENS, "output_tokens": $OUT_TOKENS}
EOF
