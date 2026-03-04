#!/usr/bin/env bash
# launch.sh — Start a Claude Code game dev on-site worker process.
#
# Convention (all self-hosted talents):
#   $1 = employee_dir (contains profile.yaml, connection.json)
#   Writes PID to {employee_dir}/worker.pid
#   Logs to {employee_dir}/worker.log
#   Runs in background (nohup)
#
# This talent includes roblox-game-development skill installed via:
#   npx skills add https://github.com/greedychipmunk/agent-skills --skill roblox-game-development

set -euo pipefail

EMPLOYEE_DIR="${1:?Usage: launch.sh <employee_dir>}"
EMPLOYEE_DIR="$(cd "$EMPLOYEE_DIR" && pwd)"

# Resolve project root from employee_dir (company/human_resource/employees/XXXXX/)
PROJECT_ROOT="$(cd "$EMPLOYEE_DIR/../../../.." && pwd)"
WORKER_SCRIPT="$PROJECT_ROOT/src/onemancompany/talent_market/talents/claude_code/run_worker.py"
PYTHON="$PROJECT_ROOT/.venv/bin/python"

PID_FILE="$EMPLOYEE_DIR/worker.pid"
LOG_FILE="$EMPLOYEE_DIR/worker.log"

# Ensure connection.json exists
if [ ! -f "$EMPLOYEE_DIR/connection.json" ]; then
    echo "ERROR: $EMPLOYEE_DIR/connection.json not found" >&2
    exit 1
fi

# Read work_dir from connection.json (if set)
WORK_DIR="$($PYTHON -c "import json; d=json.load(open('$EMPLOYEE_DIR/connection.json')); print(d.get('work_dir',''))" 2>/dev/null || echo "")"

# Install skills if not already present
if ! command -v npx &>/dev/null; then
    echo "WARNING: npx not found, skipping skill installation"
else
    # Roblox game development skill
    if [ ! -d "$EMPLOYEE_DIR/.claude/skills/roblox-game-development" ] && \
       [ ! -d "$HOME/.claude/skills/roblox-game-development" ]; then
        echo "Installing roblox-game-development skill..."
        npx skills add https://github.com/greedychipmunk/agent-skills --skill roblox-game-development 2>&1 || \
            echo "WARNING: roblox-game-development install failed, continuing without it"
    fi
    # Algorithmic art skill (procedural generation for game visuals)
    if [ ! -d "$EMPLOYEE_DIR/.claude/skills/algorithmic-art" ] && \
       [ ! -d "$HOME/.claude/skills/algorithmic-art" ]; then
        echo "Installing algorithmic-art skill..."
        npx skills add algorithmic-art 2>&1 || \
            echo "WARNING: algorithmic-art install failed, continuing without it"
    fi
    # Canvas design skill (graphic design for game UI/assets)
    if [ ! -d "$EMPLOYEE_DIR/.claude/skills/canvas-design" ] && \
       [ ! -d "$HOME/.claude/skills/canvas-design" ]; then
        echo "Installing canvas-design skill..."
        npx skills add canvas-design 2>&1 || \
            echo "WARNING: canvas-design install failed, continuing without it"
    fi
fi

# Kill existing worker if PID file exists
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "Stopping existing worker (PID $OLD_PID)..."
        kill "$OLD_PID" 2>/dev/null || true
        sleep 1
    fi
    rm -f "$PID_FILE"
fi

echo "Starting Claude Game Dev on-site worker..."
echo "  Employee dir: $EMPLOYEE_DIR"
echo "  Log file:     $LOG_FILE"

WORK_DIR_ARGS=()
if [ -n "$WORK_DIR" ] && [ -d "$WORK_DIR" ]; then
    WORK_DIR_ARGS=(--work-dir "$WORK_DIR")
    echo "  Work dir:     $WORK_DIR"
else
    echo "  Work dir:     (per-task project_dir)"
fi

nohup "$PYTHON" "$WORKER_SCRIPT" "$EMPLOYEE_DIR" \
    --poll-interval 3.0 \
    --max-turns 50 \
    "${WORK_DIR_ARGS[@]}" \
    > "$LOG_FILE" 2>&1 &

WORKER_PID=$!
echo "$WORKER_PID" > "$PID_FILE"
echo "  Worker PID:   $WORKER_PID"
echo "Worker started successfully."
