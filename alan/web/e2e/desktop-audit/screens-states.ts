import type { DesktopStateRegistry } from "../../src/desktop-audit/registry";
import { SCREENS } from "../mobile-audit/screens";

function seeded(screen: string, uncovered: readonly string[] = []): DesktopStateRegistry[number] {
  return { screen, maxState: { kind: "seeded", uncovered } };
}

function none(screen: string, reason: string): DesktopStateRegistry[number] {
  return { screen, maxState: { kind: "none", noMaxStateReason: reason, uncovered: [] } };
}

export const DESKTOP_STATE_REGISTRY: DesktopStateRegistry = [
  seeded("home"),
  seeded("projects"),
  seeded("build"),
  none(
    "connect-wizard-step1",
    "Step 1 is the initial repo-identify form: its only conditional element (a " +
      "post-lookup validation result) needs a live GitHub round-trip to reach, " +
      "which the constraints forbid driving synchronously — there is no local " +
      "seed path to a richer state than what standard already renders.",
  ),
  seeded("connect-wizard-step2"),
  seeded("connect-wizard-step3"),
  seeded("connect-wizard-step4"),
  seeded("health"),
  seeded("explore-architecture", [
    "the assistant's streamed reply and in-flight typing indicator (both require " +
      "a live async chat turn, which the constraints forbid driving synchronously)",
  ]),
  seeded("workspace-decision"),
  none(
    "prep-chat",
    "The archived transcript is a single, already-terminal static rendering with " +
      "no conditional badges, notices, or outcome states — standard already " +
      "renders its full content, so there is no orthogonal max variant to seed.",
  ),
  none(
    "repo-engine-config",
    "The read-only body renders every structural section (agent catalogue tile, " +
      "forge, CI, code quality, tool allowlists, roster, flow policy) unconditionally from " +
      "a single registered engine's config — there is no conditional badge, " +
      "notice, or outcome state orthogonal to what standard already renders.",
  ),
  none(
    "agents",
    "The catalogue lists every agent collapsed, resolved from a single registered " +
      "engine, with the search bar, filters, notes and flags always rendered — there " +
      "is no conditional badge, notice, or outcome state orthogonal to what " +
      "standard already renders, and the expanded variant is audited as its own screen.",
  ),
  none(
    "agents-expanded",
    "One agent expanded through the agent URL key is the richest state of the " +
      "catalogue — the full prompt, user-message template and every launch field — " +
      "and the route itself carries that state, so there is no further orthogonal " +
      "max variant to seed beyond what standard already renders.",
  ),
  none(
    "repo-rail-collapsed",
    "Collapsing the rail is a layout-mode toggle, not a content state — it " +
      "replaces the rail with a fixed compact indicator regardless of how much " +
      "content is behind it, and the dashboard content underneath is already " +
      "exercised by home@max — there is no orthogonal richer variant to seed.",
  ),
  none(
    "repo-drawer-open",
    "The drawer's own content is the repository switcher list plus the same " +
      "conversations rail rendered at @standard — both are already content-" +
      "audited via the top-bar switcher and the rail's own screens, so there " +
      "is no orthogonal richer variant to seed beyond what @standard renders.",
  ),
  none(
    "operator",
    "The page renders one state at a time — a notice, the scope picker, or the " +
      "terminal with its controls bar — and the terminal itself is a live pty " +
      "attachment that the audit's seeded server cannot start (detached mode is " +
      "pinned off there). @standard renders the unavailable notice; the terminal " +
      "and its bar are covered by the operator-terminal-session e2e spec.",
  ),
];

const registeredScreens = new Set(DESKTOP_STATE_REGISTRY.map((entry) => entry.screen));
const knownScreens = new Set(SCREENS.map((screen) => screen.name));
if (registeredScreens.size !== knownScreens.size || [...knownScreens].some((s) => !registeredScreens.has(s))) {
  throw new Error(
    "desktop-audit screens-states.ts registry does not exactly match mobile-audit/screens.ts's SCREENS",
  );
}
