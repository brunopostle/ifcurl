#!/bin/bash
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

# Install Python project and dev dependencies
pip install -e "." -q
pip install pytest ruff -q

# Install bd (beads issue tracker) if not present
GOPATH=$(go env GOPATH)
BD="$GOPATH/bin/bd"
if [ ! -x "$BD" ]; then
  CGO_ENABLED=1 GOFLAGS=-tags=gms_pure_go \
    go install github.com/steveyegge/beads/cmd/bd@latest
fi

# Make bd available as 'bd' in the session
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  echo "export PATH=\"\$PATH:$GOPATH/bin\"" >> "$CLAUDE_ENV_FILE"
fi

# Suppress beads.role warning for agent sessions
git config beads.role contributor 2>/dev/null || true

# Bootstrap beads database from git-tracked issues.jsonl if not already present.
# In fresh web sessions the embedded Dolt directory is absent (not tracked by git).
# The bootstrap sequence:
#   1. Point sync.remote to the HTTP git origin so bootstrap can create the db schema
#      (the Dolt wire-protocol clone will fail over HTTP/1.1 but that is expected)
#   2. Run bootstrap — creates an initialised but empty embedded Dolt database
#   3. Restore git-tracked files before any bd auto-export can overwrite them
#   4. Trigger auto-import: bd detects missing issue_prefix and loads issues.jsonl
if ! "$BD" ready > /dev/null 2>&1; then
  rm -rf .beads/embeddeddolt
  ORIGIN_URL=$(git remote get-url origin)
  "$BD" config set sync.remote "$ORIGIN_URL" 2>/dev/null || true
  BD_NON_INTERACTIVE=1 "$BD" bootstrap 2>/dev/null || true
  git checkout HEAD -- .beads/config.yaml .beads/issues.jsonl 2>/dev/null || true
  "$BD" config set issue_prefix ifcurl 2>/dev/null || true
fi
