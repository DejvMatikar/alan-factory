#!/bin/bash
# Launchd entrypoint for the scheduled self-update agent.
#
# Gates on the installed default-engine profile in pure shell, before any
# Python is invoked, so a public copy of alan with no profile installed never
# fetches or writes anything here, even if its virtual environment is broken.
set -euo pipefail

if [ -z "${HOME:-}" ]; then
  echo "update_runner.sh: HOME is unset; refusing to run" >&2
  exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE_STATE="${ALAN_ENGINES_ROOT:-$HOME/.alan/engines}/default_profile.yaml"

if [ ! -f "$PROFILE_STATE" ]; then
  exit 0
fi

exec "$REPO_DIR/.venv/bin/python" -m alan.self_update apply \
  --scheduled \
  --plist-path "$HOME/Library/LaunchAgents/ai.alan.plist" \
  --queue-db-path "$HOME/.alan/queue.db"
