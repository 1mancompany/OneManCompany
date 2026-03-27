#!/usr/bin/env bash
# launch.sh — Execute a single task via OpenClaw agent (foreground, company-hosted).
#
# Called by SubprocessExecutor per task:
#   bash launch.sh <employee_dir>
#
# Environment variables (injected by platform):
#   OMC_EMPLOYEE_ID           — Employee ID
#   OMC_TASK_ID               — Task ID
#   OMC_PROJECT_ID            — Project ID
#   OMC_PROJECT_DIR           — Project workspace (cwd)
#   OMC_TASK_DESCRIPTION_FILE — Path to file containing task/prompt text
#   OMC_SERVER_URL            — Backend URL
#
# Output: single JSON line to stdout, logs to stderr.

set -euo pipefail

EMPLOYEE_DIR="${1:?Usage: launch.sh <employee_dir>}"
EMPLOYEE_DIR="$(cd "$EMPLOYEE_DIR" && pwd)"
PROJECT_ROOT="$(cd "$EMPLOYEE_DIR/../../../.." && pwd)"

# ── Load company .env for API keys ────────────────────────────────────────────
ENV_FILE="$PROJECT_ROOT/.env"
if [ -f "$ENV_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
fi

OPENCLAW_BIN="openclaw"

# ── Ensure openclaw is installed ──────────────────────────────────────────────
if ! command -v "$OPENCLAW_BIN" &>/dev/null; then
    >&2 echo "[launch.sh] openclaw not found, installing..."
    if command -v npm &>/dev/null; then
        npm install -g openclaw@latest >&2
    else
        >&2 echo "ERROR: npm not found. Install Node.js >= 22.12.0 first."
        exit 1
    fi
fi

# ── Configure openclaw on first run ───────────────────────────────────────────
OPENCLAW_CONFIG="$HOME/.openclaw/openclaw.json"
if [ ! -f "$OPENCLAW_CONFIG" ] && [ -n "${OPENROUTER_API_KEY:-}" ]; then
    >&2 echo "[launch.sh] First run — configuring openclaw with OpenRouter..."
    "$OPENCLAW_BIN" onboard --non-interactive --accept-risk \
        --auth-choice openrouter-api-key \
        --openrouter-api-key "$OPENROUTER_API_KEY" \
        --flow quickstart >&2 2>&1 || true
fi

# ── Ensure gateway is running ─────────────────────────────────────────────────
GATEWAY_PORT=18789
GATEWAY_PID_FILE="$EMPLOYEE_DIR/gateway.pid"
GATEWAY_LOG_FILE="$EMPLOYEE_DIR/gateway.log"
GATEWAY_RUNNING=false

if [ -f "$GATEWAY_PID_FILE" ]; then
    GW_PID=$(cat "$GATEWAY_PID_FILE")
    if kill -0 "$GW_PID" 2>/dev/null; then
        GATEWAY_RUNNING=true
    else
        rm -f "$GATEWAY_PID_FILE"
    fi
fi

if [ "$GATEWAY_RUNNING" = false ] && lsof -i ":$GATEWAY_PORT" &>/dev/null; then
    GATEWAY_RUNNING=true
fi

if [ "$GATEWAY_RUNNING" = false ]; then
    >&2 echo "[launch.sh] Starting openclaw gateway..."
    nohup "$OPENCLAW_BIN" gateway > "$GATEWAY_LOG_FILE" 2>&1 &
    GW_PID=$!
    echo "$GW_PID" > "$GATEWAY_PID_FILE"
    # Wait for gateway ready (up to 15s)
    for _ in $(seq 1 30); do
        if lsof -i ":$GATEWAY_PORT" &>/dev/null; then
            >&2 echo "[launch.sh] Gateway ready (PID $GW_PID)"
            break
        fi
        sleep 0.5
    done
fi

# ── Read task description from file ──────────────────────────────────────────
TASK_DESC_FILE="${OMC_TASK_DESCRIPTION_FILE:-}"
if [ -z "$TASK_DESC_FILE" ] || [ ! -f "$TASK_DESC_FILE" ]; then
    >&2 echo "ERROR: OMC_TASK_DESCRIPTION_FILE not set or file not found"
    exit 1
fi
OMC_TASK_DESCRIPTION="$(cat "$TASK_DESC_FILE")"

# ── Run task via openclaw agent ───────────────────────────────────────────────
>&2 echo "[launch.sh] Employee=${OMC_EMPLOYEE_ID} Task=${OMC_TASK_ID}"

OUTPUT=$("$OPENCLAW_BIN" agent "$OMC_TASK_DESCRIPTION" 2>/dev/null || echo "")

if [ -z "$OUTPUT" ]; then
    OUTPUT="[openclaw] No output returned"
fi

# ── Emit result JSON to stdout ────────────────────────────────────────────────
python3 -c "
import json, sys
print(json.dumps({
    'output': sys.argv[1],
    'model': 'openclaw/openrouter',
    'input_tokens': 0,
    'output_tokens': 0,
}))
" "$OUTPUT"
