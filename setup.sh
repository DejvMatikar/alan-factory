#!/bin/bash
# Bootstrap entry for a copy of alan: everything scripts/setup_macos.sh does,
# plus the copy step between install and build. It creates your private copy
# of the template on GitHub, clones it (retrying while GitHub is still filling
# the new repository), and hands over to the new clone's own setup script.
#
# The template comes from --template <owner/repo>, else from a one-line
# setup.template file next to this script, else setup refuses. The copy is
# owned by --owner <org>, else by the signed-in GitHub user, and is private.
# See docs/architecture/setup.md.
set -euo pipefail

BOOTSTRAP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
. "$BOOTSTRAP_DIR/scripts/setup_macos.sh"

SETUP_STEPS="preflight install copy build profile start credential harden protect confirm sonar verifier trigger claude updater latch report"
ACCEPTS_TEMPLATE=1
ACCEPTS_CHECK_CLAUDE=0
TEMPLATE_FILE="$BOOTSTRAP_DIR/setup.template"
TEMPLATE_SLUG_RE='^[A-Za-z0-9]+(-[A-Za-z0-9]+)*/[A-Za-z0-9_][A-Za-z0-9._-]*$'
TEMPLATE_SLUG=""
COPY_OWNER=""
COPY_SLUG=""
COPY_DEST=""
CLONE_FAILURE=""

raw_template_value() {
  if [ -n "$TEMPLATE_ARG" ]; then
    printf '%s' "$TEMPLATE_ARG"
  elif [ -f "$TEMPLATE_FILE" ]; then
    head -n 1 "$TEMPLATE_FILE" | tr -d '[:space:]'
  fi
}

extra_guard_roots() {
  local raw
  raw="$(raw_template_value)"
  if [[ $raw =~ $TEMPLATE_SLUG_RE ]]; then
    canonical_dir "$HOME/${raw#*/}"
  fi
}

resolve_template() {
  local raw
  if [ -z "$TEMPLATE_ARG" ] && [ ! -f "$TEMPLATE_FILE" ]; then
    refuse template "no template named: pass --template <owner/repo>, or add a one-line setup.template file next to setup.sh"
  fi
  raw="$(raw_template_value)"
  if ! [[ $raw =~ $TEMPLATE_SLUG_RE ]]; then
    refuse template "the template must be written <owner>/<repo>"
  fi
  TEMPLATE_SLUG="$raw"
}

before_steps() {
  if step_selected copy; then
    CURRENT_STEP="copy"
    resolve_template
  fi
}

resolve_copy_owner() {
  if [ -n "$OWNER_ARG" ]; then
    COPY_OWNER="$OWNER_ARG"
    return 0
  fi
  if ! COPY_OWNER="$(gh api user --jq .login 2>/dev/null)" || [ -z "$COPY_OWNER" ]; then
    refuse copy-owner "cannot read the signed-in GitHub user; pass --owner <org> or sign in with gh"
  fi
}

dest_origin_is_copy() {
  local origin
  if ! origin="$(git -C "$COPY_DEST" remote get-url origin 2>/dev/null)"; then
    return 1
  fi
  parse_origin "$origin" || return 1
  [ "$(lower "$ORIGIN_OWNER/$ORIGIN_REPO")" = "$(lower "$COPY_SLUG")" ]
}

dest_has_head() {
  git -C "$COPY_DEST" rev-parse --verify HEAD >/dev/null 2>&1
}

copy_is_present() {
  [ -d "$COPY_DEST/.git" ] && dest_origin_is_copy && dest_has_head
}

remove_headless_clone() {
  if [ -d "$COPY_DEST/.git" ] && dest_origin_is_copy && ! dest_has_head; then
    rm -rf "$COPY_DEST"
  fi
}

require_usable_destination() {
  if [ ! -e "$COPY_DEST" ]; then
    return 0
  fi
  if [ -d "$COPY_DEST/.git" ] && dest_origin_is_copy && ! dest_has_head; then
    return 0
  fi
  refuse copy-destination "$COPY_DEST already exists and is not a clone of $COPY_SLUG; move it away and re-run"
}

create_copy_repository() {
  if gh repo view "$COPY_SLUG" >/dev/null 2>&1; then
    note already "GitHub repository $COPY_SLUG exists"
  else
    gh repo create "$COPY_SLUG" --template "$TEMPLATE_SLUG" --private
    note done "created the private GitHub repository $COPY_SLUG from $TEMPLATE_SLUG"
  fi
}

remove_partial_clone() {
  if [ -d "$COPY_DEST" ] && [ ! -d "$COPY_DEST/.git" ]; then
    rm -rf "$COPY_DEST"
  fi
}

clone_once() {
  CLONE_FAILURE=""
  if ! gh repo clone "$COPY_SLUG" "$COPY_DEST"; then
    CLONE_FAILURE="gh repo clone $COPY_SLUG failed"
    remove_partial_clone
  elif ! dest_has_head; then
    CLONE_FAILURE="the new repository has no commits yet"
  fi
  [ -z "$CLONE_FAILURE" ]
}

clone_with_retry() {
  local attempt=1
  remove_headless_clone
  while [ "$attempt" -le "$CLONE_ATTEMPTS" ]; do
    if clone_once; then
      return 0
    fi
    say "  $CLONE_FAILURE (attempt $attempt of $CLONE_ATTEMPTS); waiting for GitHub"
    remove_headless_clone
    attempt=$((attempt + 1))
    sleep "$WAIT_SECONDS"
  done
  case "$CLONE_FAILURE" in
    gh*) refuse clone "gh repo clone $COPY_SLUG failed on every one of $CLONE_ATTEMPTS attempts; its message is above, check your access to the repository and re-run" ;;
  esac
  refuse clone "the clone of $COPY_SLUG still has no HEAD after $CLONE_ATTEMPTS attempts; GitHub may still be filling the new repository, re-run in a few minutes"
}

step_copy() {
  resolve_copy_owner
  COPY_SLUG="$COPY_OWNER/${TEMPLATE_SLUG#*/}"
  COPY_DEST="$HOME/${TEMPLATE_SLUG#*/}"
  if copy_is_present; then
    note already "your copy $COPY_SLUG is cloned"
    return 0
  fi
  require_usable_destination
  create_copy_repository
  clone_with_retry
  note done "cloned your copy $COPY_SLUG"
}

hand_over() {
  local next="$1" entry="$COPY_DEST/scripts/setup_macos.sh" session_flag="" sonar_flag="" no_sonar_flag=""
  if [ ! -f "$entry" ]; then
    refuse handover "the new clone has no scripts/setup_macos.sh to hand over to"
  fi
  if [ "$USE_GH_SESSION" = 1 ]; then
    session_flag="--use-gh-token"
  fi
  if [ "$ACCEPT_SONAR_USER" = 1 ]; then
    sonar_flag="--accept-sonar-user-token"
  fi
  if [ "$NO_SONAR" = 1 ]; then
    no_sonar_flag="--no-sonar"
  fi
  say ""
  say "handing over to the setup script inside your new clone, from step $next onward"
  exec /bin/bash "$entry" --from "$next" ${OWNER_ARG:+--owner "$OWNER_ARG"} $session_flag $sonar_flag $no_sonar_flag
}

after_step() {
  local next
  if [ "$1" != copy ]; then
    return 0
  fi
  next="$(selected_after copy)"
  if [ -n "$next" ]; then
    hand_over "$next"
  fi
}

main "$@"
