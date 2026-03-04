#!/usr/bin/env bash
# launch.sh — Start a Claude Code project manager on-site worker process.
#
# Convention (all self-hosted talents):
#   $1 = employee_dir (contains profile.yaml, connection.json)
#   Writes PID to {employee_dir}/worker.pid
#   Logs to {employee_dir}/worker.log
#   Runs in background (nohup)
#
# Skills installed from skills.sh:
#   - internal-comms (anthropics/skills) — status reports & internal communications
#   - task-planning (supercent-io/skills-template) — task decomposition & planning
#   - task-estimation (supercent-io/skills-template) — story points & effort estimation
#   - standup-meeting (supercent-io/skills-template) — daily standup support
#   - sprint-retrospective (supercent-io/skills-template) — sprint review & retrospective

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

# Install skills from skills.sh if not already present
if ! command -v npx &>/dev/null; then
    echo "WARNING: npx not found, skipping skill installation"
else
    # internal-comms — status reports & internal communications (Anthropic official)
    if [ ! -d "$EMPLOYEE_DIR/.claude/skills/internal-comms" ] && \
       [ ! -d "$HOME/.claude/skills/internal-comms" ]; then
        echo "Installing internal-comms skill..."
        npx skills add anthropics/skills --skill internal-comms 2>&1 || \
            echo "WARNING: internal-comms install failed, continuing without it"
    fi
    # task-planning — task decomposition & planning
    if [ ! -d "$EMPLOYEE_DIR/.claude/skills/task-planning" ] && \
       [ ! -d "$HOME/.claude/skills/task-planning" ]; then
        echo "Installing task-planning skill..."
        npx skills add supercent-io/skills-template --skill task-planning 2>&1 || \
            echo "WARNING: task-planning install failed, continuing without it"
    fi
    # task-estimation — story points & effort estimation
    if [ ! -d "$EMPLOYEE_DIR/.claude/skills/task-estimation" ] && \
       [ ! -d "$HOME/.claude/skills/task-estimation" ]; then
        echo "Installing task-estimation skill..."
        npx skills add supercent-io/skills-template --skill task-estimation 2>&1 || \
            echo "WARNING: task-estimation install failed, continuing without it"
    fi
    # standup-meeting — daily standup support
    if [ ! -d "$EMPLOYEE_DIR/.claude/skills/standup-meeting" ] && \
       [ ! -d "$HOME/.claude/skills/standup-meeting" ]; then
        echo "Installing standup-meeting skill..."
        npx skills add supercent-io/skills-template --skill standup-meeting 2>&1 || \
            echo "WARNING: standup-meeting install failed, continuing without it"
    fi
    # sprint-retrospective — sprint review & retrospective
    if [ ! -d "$EMPLOYEE_DIR/.claude/skills/sprint-retrospective" ] && \
       [ ! -d "$HOME/.claude/skills/sprint-retrospective" ]; then
        echo "Installing sprint-retrospective skill..."
        npx skills add supercent-io/skills-template --skill sprint-retrospective 2>&1 || \
            echo "WARNING: sprint-retrospective install failed, continuing without it"
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

echo "Starting Claude Project Manager on-site worker..."
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
