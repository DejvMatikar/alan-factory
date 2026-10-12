from __future__ import annotations

from alan import default_profile
from alan.engine_config import EngineConfig

_IOS_PRESET = "ios"
_RECIPE_TEAMS = frozenset({"implementation", "implementation_fix"})
_UI_REVIEW_TEAM = "ui_review"

_DEGRADE_LINE = "native capture unavailable — continuing from source"

_RECIPE_HEAD = (
    "NATIVE SIMULATOR CAPTURE (advisory, best-effort, capture-only — NEVER "
    "build) — when your diff touches native application UI, ground visual "
    "claims in a real simulator screenshot of your OWN build by following "
    "this chain exactly. At any absence or failure below, write exactly "
    f"`{_DEGRADE_LINE}` and continue reviewing from source — a missing "
    "capture must never block, escalate, delay, change a verdict or "
    "severity, or enter decisions.yaml, files_to_edit, or test_scenarios.\n"
    "1. LOCATE — resolve the already-built app: "
    '`APP=$(ls -d "$DERIVED_DATA_PATH"/Build/Products/Debug-iphonesimulator/'
    '*.app 2>/dev/null | head -1)`. If this is empty OR the command itself '
    "is denied by your tool sandbox (a known, tracked gap), the app is not "
    f"reachable — this is NOT yours to fix: write `{_DEGRADE_LINE}` and "
    "stop. NEVER build it yourself — a build writes into the pool-shared "
    "derived-data tree every concurrent worker reads from, and this "
    "recipe is capture-only.\n"
    "2. FRESHNESS REFUSAL (the most important step) — a build newer than "
    "your worktree's own history is not proof it is YOUR build; derived "
    "data is shared across every concurrent worker on this host. Compare "
    "the app's modification time against your own worktree's HEAD commit: "
    '`HEAD_TS=$(git log -1 --format=%ct)` then `APP_TS=$(stat -f %m '
    '"$APP")` (BSD `stat -f`, NOT GNU `stat -c` — this host is macOS). If '
    "`APP_TS` is older than `HEAD_TS`, the build predates your own work "
    f"and belongs to a sibling worker: write `{_DEGRADE_LINE}` and stop — "
    "do NOT capture a sibling's screen and report it as your own. If "
    "either command in this step is denied by your tool sandbox rather "
    f"than exiting cleanly, treat it identically: write `{_DEGRADE_LINE}` "
    "and stop.\n"
    "3. ISOLATION TOKEN — derive one token from your own worktree, chosen "
    "once and reused for BOTH the device set and the output directory: "
    '`TOKEN="$(basename "$PWD")-$(git rev-parse --short HEAD)"`. NEVER a '
    "process id — a pid is not stable across a sequence of independent "
    "shell invocations, and this token is what keeps your capture out of "
    "another worker's directory.\n"
    "4. ISOLATE — create your own throwaway device set and output "
    "directory, both keyed to $TOKEN: "
    '`SET="$TMPDIR/alan-simsets/$TOKEN"; OUT="$TMPDIR/alan-captures/'
    '$TOKEN"; mkdir -p "$SET" "$OUT"`.\n'
    "5. CREATE — create a throwaway simulator inside your own device set "
    "and capture its identifier explicitly: "
    '`UDID=$(xcrun simctl --set "$SET" create "cap-$TOKEN" "<a device '
    'type from xcrun simctl --set "$SET" list devicetypes>")`. Carry '
    "`$UDID` explicitly through every command below. NEVER the bare "
    "booted-device shorthand — always the captured identifier.\n"
    "6. BOOT — at most ONE booted simulator per worker (measured host "
    "ceiling: 2 simulators booted at once, total; advisory, not "
    'enforced by this recipe): `xcrun simctl --set "$SET" boot "$UDID"` '
    'then `xcrun simctl --set "$SET" bootstatus "$UDID" -b`.\n'
)

_RECIPE_TAIL = (
    "8. CAPTURE — address the device AND the display explicitly; the "
    "display is NOT optional, the command silently defaults it "
    'otherwise: `xcrun simctl --set "$SET" io "$UDID" screenshot '
    '--type=png --display=internal "$OUT/<screen-name>.png"`.\n'
    "9. READ BACK — Read only PNGs from `$OUT`. NEVER read any other "
    "capture directory — another worker's output directory may hold a "
    "screen you did not render.\n"
    "10. TEARDOWN — unconditionally, including on failure: "
    '`xcrun simctl --set "$SET" shutdown "$UDID"; xcrun simctl --set '
    '"$SET" delete "$UDID"; rm -rf "$SET"`.\n'
    "NEVER retry a failed or refused capture into shared space — a "
    "retry belongs in a fresh $TOKEN-scoped attempt or not at all.\n"
    "The second capture path (in-test screenshot attachment) works only "
    "in a graphical session and is out of scope for this recipe."
)

_DISCOVERED_LAUNCH_STEP = (
    "7. INSTALL + LAUNCH — "
    '`xcrun simctl --set "$SET" install "$UDID" "$APP"`, then read the '
    "application's bundle identifier from the build you just installed: "
    '`BUNDLE_ID=$(/usr/libexec/PlistBuddy -c "Print :CFBundleIdentifier" '
    '"$APP/Info.plist" 2>/dev/null)`. If `$BUNDLE_ID` is empty OR the '
    "command itself is denied by your tool sandbox, run step 10 TEARDOWN, "
    f"write `{_DEGRADE_LINE}` and stop. Otherwise launch it: "
    '`xcrun simctl --set "$SET" launch "$UDID" "$BUNDLE_ID"`.\n'
)

NATIVE_CAPTURE_UNAVAILABLE = (
    "NATIVE SIMULATOR CAPTURE — structurally unavailable to this "
    "capability on this engine. This supersedes any web screen-capture "
    "instruction appearing earlier in this prompt for this run: do not "
    "attempt it. Orchestrated worker nodes run with no working directory, "
    "so on this engine you execute inside alan's own shared production "
    "checkout — no application source, no built application, and no "
    "simulator artifact are reachable from here. Ground every finding in "
    "captures already on disk or attached to the pull request instead; "
    "never claim to have seen a rendered screen you could not have "
    "captured."
)


def _launch_step(bundle_id: str | None) -> str:
    if bundle_id is None:
        return _DISCOVERED_LAUNCH_STEP
    return (
        "7. INSTALL + LAUNCH — "
        '`xcrun simctl --set "$SET" install "$UDID" "$APP"` then `xcrun '
        f'simctl --set "$SET" launch "$UDID" {bundle_id}` '
        f"(the application's bundle identifier is `{bundle_id}`).\n"
    )


def native_capture_recipe(bundle_id: str | None) -> str:
    return _RECIPE_HEAD + _launch_step(bundle_id) + _RECIPE_TAIL


NATIVE_CAPTURE_RECIPE = native_capture_recipe(None)


def native_capture_block(cfg: EngineConfig, team: str) -> str:
    if cfg.preset != _IOS_PRESET:
        return ""
    if team in _RECIPE_TEAMS:
        return native_capture_recipe(default_profile.resolved().profile.ios_bundle_id)
    if team == _UI_REVIEW_TEAM:
        return NATIVE_CAPTURE_UNAVAILABLE
    return ""


__all__ = [
    "NATIVE_CAPTURE_RECIPE",
    "NATIVE_CAPTURE_UNAVAILABLE",
    "native_capture_block",
    "native_capture_recipe",
]
