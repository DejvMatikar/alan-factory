#!/bin/bash
# Idempotent render of deploy/ai.alan.plist (the tracked template) into a real,
# machine-specific launchd configuration at ~/Library/LaunchAgents/ai.alan.plist.
#
# Takes no arguments. The target is derived purely from $HOME; this script
# refuses to run when $HOME is unset so an unsandboxed render is structurally
# impossible rather than conventionally avoided.
#
# The third token, __ALAN_SLOT_REPO_URL__, is derived from the origin remote of
# the checkout this script lives in, normalized to a GitHub https URL. An origin
# that cannot yield one makes this script exit 78 before it creates or writes
# anything, so the existing launch configuration stays exactly as it is. The
# origin may carry a credential and is never printed.
#
# Never writes through the target: that path may currently be a symlink
# pointing back into this tracked template (the retired bootstrap mechanism).
# Writing through it would dereference into the live checkout and corrupt the
# tracked template with a rendered, machine-specific copy. Instead this script
# renders to a temp file created via mktemp inside the target's own directory,
# then mv -f's that file over whatever currently sits at the target — file or
# symlink — which is atomic within a single filesystem.
set -euo pipefail

ORIGIN_REFUSED_EXIT=78
ALNUM_CHARS='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'
NAME_CHARS="[$ALNUM_CHARS._-]+"
OWNER_RE="^[$ALNUM_CHARS][$ALNUM_CHARS-]{0,38}\$"
REPO_RE="^[$ALNUM_CHARS._-]{1,100}\$"
HTTPS_ORIGIN_RE="^https://([^/]*@)?github\\.com/($NAME_CHARS)/($NAME_CHARS)\$"
SSH_ORIGIN_RE="^ssh://([^/]*@)?github\\.com/($NAME_CHARS)/($NAME_CHARS)\$"
SCP_ORIGIN_RE="^([^/:@]*@)?github\\.com:($NAME_CHARS)/($NAME_CHARS)\$"

if [ -z "${HOME:-}" ]; then
  echo "render_plist.sh: HOME is unset; refusing to render (target derives from HOME)" >&2
  exit 1
fi

reject_unsafe_path() {
  local kind="$1" value="$2"
  case "$value" in
    *'#'* | *'&'* | *'\'*)
      echo "render_plist.sh: $kind contains '#', '&' or a backslash; refusing to render" >&2
      exit 1
      ;;
  esac
}

read_origin() {
  ( unset GIT_DIR GIT_WORK_TREE; git -C "$REPO_DIR" remote get-url origin 2>/dev/null ) || true
}

parse_origin() {
  local origin="$1" owner repo
  ORIGIN_OWNER=""
  ORIGIN_REPO=""
  if [[ $origin =~ $HTTPS_ORIGIN_RE ]] || [[ $origin =~ $SSH_ORIGIN_RE ]] || [[ $origin =~ $SCP_ORIGIN_RE ]]; then
    owner="${BASH_REMATCH[2]}"
    repo="${BASH_REMATCH[3]%.git}"
  else
    return 1
  fi
  if ! [[ $owner =~ $OWNER_RE ]] || ! [[ $repo =~ $REPO_RE ]] || [[ $repo == . || $repo == .. ]]; then
    return 1
  fi
  ORIGIN_OWNER="$owner"
  ORIGIN_REPO="$repo"
}

refuse_origin() {
  echo "render_plist.sh: origin is not a GitHub repository, so worker slots have no repository to clone from; nothing was written. Point it at yours with: git -C \"$REPO_DIR\" remote set-url origin https://github.com/<owner>/<repo>.git" >&2
  exit "$ORIGIN_REFUSED_EXIT"
}

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
reject_unsafe_path "repository directory" "$REPO_DIR"
reject_unsafe_path "home directory" "$HOME"
if ! parse_origin "$(read_origin)"; then
  refuse_origin
fi
SLOT_REPO_URL="https://github.com/$ORIGIN_OWNER/$ORIGIN_REPO.git"
TEMPLATE="$REPO_DIR/deploy/ai.alan.plist"
TARGET_DIR="$HOME/Library/LaunchAgents"
TARGET="$TARGET_DIR/ai.alan.plist"

mkdir -p "$TARGET_DIR"

TMP_FILE="$(mktemp "$TARGET_DIR/.ai.alan.plist.XXXXXX")"
trap 'rm -f "$TMP_FILE"' EXIT

sed -e "s#__ALAN_REPO_DIR__#$REPO_DIR#g" -e "s#__ALAN_HOME__#$HOME#g" -e "s#__ALAN_SLOT_REPO_URL__#$SLOT_REPO_URL#g" "$TEMPLATE" > "$TMP_FILE"

mv -f "$TMP_FILE" "$TARGET"
trap - EXIT

echo "Rendered $TEMPLATE -> $TARGET"
