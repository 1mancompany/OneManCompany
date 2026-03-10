#!/usr/bin/env bash
# heartbeat.sh — Check if the OpenClaw worker + gateway are alive.
# Returns 0 if alive, 1 if dead.
# If gateway died but worker is alive, restart the gateway.

EMPLOYEE_DIR="${1:?Usage: heartbeat.sh <employee_dir>}"
PID_FILE="$EMPLOYEE_DIR/worker.pid"
GATEWAY_PID_FILE="$EMPLOYEE_DIR/gateway.pid"

# Check worker
if [ ! -f "$PID_FILE" ]; then
    exit 1
fi

PID=$(cat "$PID_FILE")
if ! kill -0 "$PID" 2>/dev/null; then
    rm -f "$PID_FILE"
    exit 1
fi

# Check gateway — restart if needed
if [ -f "$GATEWAY_PID_FILE" ]; then
    GW_PID=$(cat "$GATEWAY_PID_FILE")
    if ! kill -0 "$GW_PID" 2>/dev/null; then
        # Gateway died, restart it
        OPENCLAW_BIN="openclaw"
        GATEWAY_LOG_FILE="$EMPLOYEE_DIR/gateway.log"
        if command -v "$OPENCLAW_BIN" &>/dev/null; then
            nohup "$OPENCLAW_BIN" gateway >> "$GATEWAY_LOG_FILE" 2>&1 &
            echo "$!" > "$GATEWAY_PID_FILE"
        fi
    fi
fi

exit 0
