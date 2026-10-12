#!/bin/bash
# Launchd entrypoint for the ai.alan service.
#
# Sources machine-local secrets (e.g. ALAN_VERIFIER_GH_TOKEN) from a file OUTSIDE the
# git-tracked deploy checkout, then execs the server. Keeping secrets out of the tracked
# deploy/ai.alan.plist means CI Deploy's `git reset --hard origin/main` + `git clean -fdx`
# never touch them and can never wedge on a locally-edited, skip-worktree'd plist (the prior
# in-plist approach broke every deploy once a commit changed the plist).
#
# Secrets file format: plain KEY=value lines, one per line; values are taken literally
# (not shell-evaluated). Blank lines and lines starting with # are ignored. Default
# ~/.alan/secrets.env, override with ALAN_SECRETS_FILE.
set -euo pipefail

SECRETS_FILE="${ALAN_SECRETS_FILE:-$HOME/.alan/secrets.env}"
if [ -f "$SECRETS_FILE" ]; then
  while IFS='=' read -r key value || [ -n "$key" ]; do
    case "$key" in
      ''|'#'*) continue ;;
    esac
    export "$key=$value"
  done < "$SECRETS_FILE"
fi

# Raise the open-file-descriptor soft limit above the macOS default of 256.
# On service restart the dispatcher's startup reconciler resumes every active
# workspace at once; each resume + the inbox aggregation (`_build_inbox_response`
# reads a handover file per workspace) opens many fds concurrently. Under the
# default 256, that reconciler herd exhausted the limit (`OSError: [Errno 24] Too
# many open files`), GIL-starving the event loop and wedging the HTTP API (100%
# CPU, http 000) — observed 3× on 2026-07-05→07, each time re-wedging within
# minutes of a bare restart because the herd re-hit 256. 4096 gives the herd headroom.
ulimit -S -n 4096

BIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.venv/bin"
exec "$BIN_DIR/python" -m alan
