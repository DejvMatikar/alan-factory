#!/bin/bash
# Idempotent render of deploy/ai.alan.update.plist (the tracked template) into
# a real, machine-specific launchd configuration at
# ~/Library/LaunchAgents/ai.alan.update.plist.
#
# Takes no arguments. The target is derived purely from $HOME; this script
# refuses to run when $HOME is unset so an unsandboxed render is structurally
# impossible rather than conventionally avoided. It never loads or unloads
# the rendered agent: installing it is a separate, deliberate step.
set -euo pipefail

if [ -z "${HOME:-}" ]; then
  echo "render_update_plist.sh: HOME is unset; refusing to render (target derives from HOME)" >&2
  exit 1
fi

reject_unsafe_path() {
  local kind="$1" value="$2"
  case "$value" in
    *'#'* | *'&'* | *'\'*)
      echo "render_update_plist.sh: $kind contains '#', '&' or a backslash; refusing to render" >&2
      exit 1
      ;;
  esac
}

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
reject_unsafe_path "repository directory" "$REPO_DIR"
reject_unsafe_path "home directory" "$HOME"
TEMPLATE="$REPO_DIR/deploy/ai.alan.update.plist"
TARGET_DIR="$HOME/Library/LaunchAgents"
TARGET="$TARGET_DIR/ai.alan.update.plist"

mkdir -p "$TARGET_DIR"

TMP_FILE="$(mktemp "$TARGET_DIR/.ai.alan.update.plist.XXXXXX")"
trap 'rm -f "$TMP_FILE"' EXIT

sed -e "s#__ALAN_REPO_DIR__#$REPO_DIR#g" -e "s#__ALAN_HOME__#$HOME#g" "$TEMPLATE" > "$TMP_FILE"

mv -f "$TMP_FILE" "$TARGET"
trap - EXIT

echo "Rendered $TEMPLATE -> $TARGET"
