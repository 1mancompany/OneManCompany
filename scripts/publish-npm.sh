#!/usr/bin/env bash
# OneManCompany — Publish npm package
#
# Usage:
#   bash scripts/publish-npm.sh           # Publish (auto-sync version from pyproject.toml)
#   bash scripts/publish-npm.sh --dry-run # Dry run (no actual publish)
#
# Prerequisites:
#   npm adduser  (one-time login)

set -euo pipefail
cd "$(dirname "$0")/.."

info()  { printf '\033[1;36m▸ %s\033[0m\n' "$*"; }
warn()  { printf '\033[1;33m⚠ %s\033[0m\n' "$*"; }
error() { printf '\033[1;31m✖ %s\033[0m\n' "$*" >&2; exit 1; }

DRY_RUN=""
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN="--dry-run"

# ── Sync version from pyproject.toml ──────────────────────────────────────
PY_VERSION=$(python3 -c "
import re
with open('pyproject.toml') as f:
    m = re.search(r'version\s*=\s*\"([^\"]+)\"', f.read())
    print(m.group(1) if m else '')
")

if [ -z "$PY_VERSION" ]; then
  error "Could not extract version from pyproject.toml"
fi

# Update package.json version
node -e "
const fs = require('fs');
const pkg = JSON.parse(fs.readFileSync('package.json', 'utf8'));
pkg.version = '${PY_VERSION}';
fs.writeFileSync('package.json', JSON.stringify(pkg, null, 2) + '\n');
"

info "Version synced: ${PY_VERSION}"

# ── Verify package contents ───────────────────────────────────────────────
info "Package contents:"
npm pack --dry-run 2>&1 | head -30

echo ""

# ── Publish ───────────────────────────────────────────────────────────────
if [ -n "$DRY_RUN" ]; then
  warn "Dry run — skipping publish"
  npm publish --dry-run --access public
else
  info "Publishing @1mancompany/onemancompany@${PY_VERSION} to npm..."
  npm publish --access public
  info "Published! Users can now run: npx @1mancompany/onemancompany"
fi
