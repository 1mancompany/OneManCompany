#!/usr/bin/env bash
# launch.sh — Start an OpenClaw on-site worker process.
#
# Convention (all self-hosted talents):
#   $1 = employee_dir (contains profile.yaml, connection.json)
#   Writes PID to {employee_dir}/worker.pid
#   Logs to {employee_dir}/worker.log
#   Runs in background (nohup)
#
# This script handles the full lifecycle:
#   1. Install openclaw if not found
#   2. Start the gateway if not already running
#   3. Start the worker (polls company API, delegates to openclaw agent)

set -euo pipefail

EMPLOYEE_DIR="${1:?Usage: launch.sh <employee_dir>}"
EMPLOYEE_DIR="$(cd "$EMPLOYEE_DIR" && pwd)"

# Resolve project root from employee_dir (company/human_resource/employees/XXXXX/)
PROJECT_ROOT="$(cd "$EMPLOYEE_DIR/../../../.." && pwd)"
WORKER_SCRIPT="$PROJECT_ROOT/src/onemancompany/talent_market/talents/openclaw/run_worker.py"
PYTHON="$PROJECT_ROOT/.venv/bin/python"

PID_FILE="$EMPLOYEE_DIR/worker.pid"
LOG_FILE="$EMPLOYEE_DIR/worker.log"
GATEWAY_PID_FILE="$EMPLOYEE_DIR/gateway.pid"
GATEWAY_LOG_FILE="$EMPLOYEE_DIR/gateway.log"

# Ensure connection.json exists
if [ ! -f "$EMPLOYEE_DIR/connection.json" ]; then
    echo "ERROR: $EMPLOYEE_DIR/connection.json not found" >&2
    exit 1
fi

# Read openclaw_bin from connection.json (if set)
OPENCLAW_BIN="$($PYTHON -c "import json; d=json.load(open('$EMPLOYEE_DIR/connection.json')); print(d.get('openclaw_bin','openclaw'))" 2>/dev/null || echo "openclaw")"

# ── Step 1: Ensure openclaw is installed ──────────────────────────────────────
if ! command -v "$OPENCLAW_BIN" &>/dev/null; then
    echo "openclaw not found, installing..."
    if command -v npm &>/dev/null; then
        npm install -g openclaw@latest
    else
        echo "ERROR: npm not found. Install Node.js >= 22.12.0 first." >&2
        exit 1
    fi
    # Verify installation
    if ! command -v openclaw &>/dev/null; then
        echo "ERROR: openclaw installation failed" >&2
        exit 1
    fi
    OPENCLAW_BIN="openclaw"
    echo "openclaw installed: $($OPENCLAW_BIN --version)"
fi

# ── Step 2: Ensure gateway is running ─────────────────────────────────────────
GATEWAY_PORT=18789
GATEWAY_RUNNING=false

# Check by PID file first
if [ -f "$GATEWAY_PID_FILE" ]; then
    GW_PID=$(cat "$GATEWAY_PID_FILE")
    if kill -0 "$GW_PID" 2>/dev/null; then
        GATEWAY_RUNNING=true
        echo "Gateway already running (PID $GW_PID)"
    else
        rm -f "$GATEWAY_PID_FILE"
    fi
fi

# Also check if something is already listening on the gateway port
if [ "$GATEWAY_RUNNING" = false ] && lsof -i ":$GATEWAY_PORT" &>/dev/null; then
    GATEWAY_RUNNING=true
    echo "Gateway already running on port $GATEWAY_PORT (external)"
fi

if [ "$GATEWAY_RUNNING" = false ]; then
    echo "Starting openclaw gateway..."
    nohup "$OPENCLAW_BIN" gateway > "$GATEWAY_LOG_FILE" 2>&1 &
    GW_PID=$!
    echo "$GW_PID" > "$GATEWAY_PID_FILE"
    echo "  Gateway PID: $GW_PID"

    # Wait for gateway to be ready (up to 15s)
    for i in $(seq 1 30); do
        if lsof -i ":$GATEWAY_PORT" &>/dev/null; then
            echo "  Gateway ready on port $GATEWAY_PORT"
            break
        fi
        sleep 0.5
    done

    if ! lsof -i ":$GATEWAY_PORT" &>/dev/null; then
        echo "WARNING: Gateway may not be ready yet, continuing anyway..." >&2
    fi
fi

# ── Step 3: Kill existing worker if PID file exists ───────────────────────────
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "Stopping existing worker (PID $OLD_PID)..."
        kill "$OLD_PID" 2>/dev/null || true
        sleep 1
    fi
    rm -f "$PID_FILE"
fi

# ── Step 4: Start the worker ──────────────────────────────────────────────────
echo "Starting OpenClaw on-site worker..."
echo "  Employee dir:  $EMPLOYEE_DIR"
echo "  OpenClaw bin:  $OPENCLAW_BIN"
echo "  Log file:      $LOG_FILE"

nohup "$PYTHON" "$WORKER_SCRIPT" "$EMPLOYEE_DIR" \
    --openclaw-bin "$OPENCLAW_BIN" \
    --poll-interval 3.0 \
    > "$LOG_FILE" 2>&1 &

WORKER_PID=$!
echo "$WORKER_PID" > "$PID_FILE"
echo "  Worker PID:    $WORKER_PID"
echo "Worker started successfully."
