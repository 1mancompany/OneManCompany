#!/usr/bin/env bash
# OneManCompany — one-click start script
#
# Usage:
#   bash start.sh              # Start server (runs init wizard if first time)
#   bash start.sh init         # Run setup wizard only
#   bash start.sh --port 8080  # Override port
#
# Environment:
#   HOST / PORT                 # Server bind (default 0.0.0.0:8000)
#   OPENROUTER_API_KEY          # Required for LLM access

set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"

# ---------- helpers ----------
info()  { printf '\033[1;36m▸ %s\033[0m\n' "$*"; }
warn()  { printf '\033[1;33m⚠ %s\033[0m\n' "$*"; }
error() { printf '\033[1;31m✖ %s\033[0m\n' "$*" >&2; exit 1; }

ensure_venv() {
  if [ ! -d .venv ]; then
    info "Creating Python virtual environment..."
    $PYTHON -m venv .venv
  fi
  # shellcheck disable=SC1091
  source .venv/bin/activate
  info "Installing dependencies..."
  pip install -q -e . 2>/dev/null
}

run_init() {
  ensure_venv
  info "Running setup wizard..."
  .venv/bin/onemancompany-init
  info "Starting OneManCompany..."
  exec .venv/bin/onemancompany "$@"
}

_init_is_complete() {
  # Check that key files/dirs exist within .onemancompany/
  [ -d .onemancompany ] \
    && [ -f .onemancompany/.env ] \
    && [ -d .onemancompany/company/human_resource/employees ]
}

run_server() {
  ensure_venv

  if ! _init_is_complete; then
    if [ -d .onemancompany ]; then
      warn ".onemancompany/ exists but is incomplete — re-running setup wizard"
    else
      warn ".onemancompany/ not found — launching setup wizard first"
    fi
    .venv/bin/onemancompany-init
  fi

  info "Starting OneManCompany..."
  exec .venv/bin/onemancompany "$@"
}

# ---------- entry ----------
case "${1:-}" in
  init)    shift; run_init "$@" ;;
  --help|-h)
    echo "Usage: bash start.sh [init | --port PORT | --host HOST]"
    echo ""
    echo "Commands:"
    echo "  (default)   Start the server (auto-init if needed)"
    echo "  init        Run the setup wizard"
    echo ""
    echo "Options are passed through to uvicorn (--host, --port, etc.)"
    ;;
  *)       run_server "$@" ;;
esac
