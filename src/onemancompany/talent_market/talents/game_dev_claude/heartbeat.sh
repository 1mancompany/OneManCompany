#!/usr/bin/env bash
# heartbeat.sh — exit 0 if worker alive, exit 1 if not
# $1 = employee_dir
EMPLOYEE_DIR="${1:?}"
PID_FILE="$EMPLOYEE_DIR/worker.pid"
[ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null
