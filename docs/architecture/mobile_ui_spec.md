# Mobile UI design spec

> **Non-normative.** This document is prose explanation for humans only. It is
> never read by an automated check, a test, or a gate. The single source of
> truth for every numeric value below is
> [`alan/web/src/ui-spec.json`](../../alan/web/src/ui-spec.json), accessed
> through the typed exports in
> [`alan/web/src/ui-spec.ts`](../../alan/web/src/ui-spec.ts). If this document
> and the JSON ever disagree, the JSON wins — treat that as a bug in this
> document and fix the prose, not the constant.

## Purpose

This document describes the *target* for what "correct at mobile viewports"
means for this app's UI. The normative source of that target is
`ui-spec.json` / `ui-spec.ts`, not this prose — see the non-normative notice
above. This phase is a documentation-and-data deliverable: it defines the
numbers that later capture/measurement and gating work will check against. As
of this writing there is no headless-browser capture, no accessibility scan,
and no rendered-UI measurement gate wired to these values — only the drift
guard described below, which keeps the JSON constant and the CSS token
internally consistent with each other.

## Tap targets

Interactive elements (buttons, links, form controls) should have a minimum
touch target of **44px** on a side. This is `tapTarget.minPx` in
`ui-spec.json`, exposed as `tapTargetMinPx` from `ui-spec.ts`.

Source: WCAG 2.5.5 (Target Size) and the Apple Human Interface Guidelines,
both of which converge on 44pt/44px as the minimum tappable region for
touch input.

The design-system styling layer mirrors this value as the
`--spacing-tap-target` CSS custom property in `alan/web/src/index.css`
(currently `44px`). A dedicated drift test,
`alan/web/src/ui-spec.drift.test.ts`, parses that token out of `index.css`
and asserts it equals `tapTargetMinPx` — so the CSS token and the JSON
constant cannot silently diverge. `--spacing-tap-target` is a plain spacing
token; it does not introduce any new responsive-breakpoint utility prefix
into the styling system.

## Colour contrast

- Normal text: minimum contrast ratio **4.5:1** —
  `contrast.normalTextMinRatio` / `contrastNormalTextMinRatio`.
- Large text: minimum contrast ratio **3:1** —
  `contrast.largeTextMinRatio` / `contrastLargeTextMinRatio`.

Source: WCAG 2.1 AA (Success Criterion 1.4.3). These ratios apply to
foreground-text-against-background-colour pairs; "large text" follows the
WCAG definition (18pt/24px regular, or 14pt/18.66px bold, and larger).

## Smallest supported viewport

The floor viewport width is **390px**, labelled "iPhone 16e" —
`viewport.floorWidthPx` / `smallestSupportedViewportWidthPx`, with
`viewport.floorLabel` recording the device name. This is the width against
which a future "no horizontal overflow at the smallest breakpoint" rule is
defined: nothing in the UI should force horizontal scrolling or clip content
at 390px.

A second reference width, **402px** ("iPhone 17"), is also recorded in
`viewport.referenceWidths` (exposed as `viewportReferenceWidths`) for
context, but it is explicitly a non-gating reference — only the 390px entry
has `isFloor: true`. A future capture/measurement phase may choose to check
additional widths, but this document and the underlying spec do not require
402px to independently gate anything.

These are measurement constants describing which viewport widths this app's
UI must remain usable at. They are not — and must not become — a new
responsive-breakpoint utility namespace in the styling system (e.g. no
`sm:`/`md:`-style prefix keyed off 390 or 402 is introduced by this spec).

## Safe-area / notch handling — advisory only

Safe-area guidance (respecting `env(safe-area-inset-*)` for notches, home
indicators, and rounded corners) is captured in `safeArea.guidance`
(`safeAreaGuidance`), with `safeArea.advisory` (`safeAreaAdvisory`) set to
`true` and `safeArea.verifiable` (`safeAreaVerifiable`) set to `false`.

This is deliberate: safe-area correctness cannot be deterministically
measured in a headless browser, so unlike the tap-target, contrast, and
viewport-floor values above, it is **not** expressed as a checkable
assertion anywhere, now or in any planned future gate. Treat it as guidance
to follow when building UI, not as something a test can fail on.

## What this document is not

- It is not the source of truth — `ui-spec.json` / `ui-spec.ts` are.
- It does not measure, capture, or gate anything against rendered UI. There
  is no headless browser and no accessibility scanner in this phase. CI does
  run the drift-guard test (`ui-spec.drift.test.ts`) that keeps the CSS
  token and the JSON constant from silently diverging, but that is internal
  consistency, not a measurement or accessibility gate.
- It does not restyle or make any existing component responsive. No app UI
  changed as part of establishing this spec.

## Desktop-audit — blocking geometry gate at 1440×900

Unlike everything above, this section describes a live, blocking gate:
`mobile-audit-gate`'s desktop step (`npm run audit:desktop`,
`alan/web/e2e/desktop-audit/desktop-audit.spec.ts`). It exists because two
visible desktop layout defects — an action row structurally split across two
lines, and a repeating grid whose name column disagreed with its sibling
columns on vertical alignment — reached production despite the mobile ratchet,
the (advisory, capture-only) screenshot suite, and the (advisory,
never-a-gate) `ui_review` team. Both slipped every mechanism because none of
them measured desktop geometry with a pass/fail assertion.

**Constants.** `ui-spec.json`'s `desktopAudit` block is the single source of
truth, exposed through `ui-spec.ts`:

- `viewport.widthPx` / `viewport.heightPx` — 1440×900, one representative
  desktop width. No breakpoint sweep: a structural defect visible at 1440px
  is also visible at 2560px, so a second width buys nothing.
- `fitHeadroomPx` — how much spare width an action group's children must have
  (beyond their own summed width) before a wrap is judged "should have fit on
  one line" rather than "genuinely too narrow."
- `bandEpsilonPx` — the vertical tolerance for treating two children as being
  on the same row band (wrapping detection).
- `anchorTolerancePx` — the vertical tolerance for treating two columns
  within one repeating-group row as sharing the same cross-axis anchor.
- `minRepeatingGroupSize` — how many same-signature siblings are needed
  before a container counts as a "repeating group" at all (below this, a
  handful of similarly-styled elements is not treated as a grid).

All four were chosen from the two known defects' actual geometry (not
guessed) and validated by two consecutive full runs on this machine
producing byte-identical violation counts across every screen and state —
the load-bearing property for a zero-flake-tolerance gate. They have not
yet been through the formal margin-calibration pass described under
"Measurement procedure" below (confirming ≥2x margin from the nearest clean
group specifically); that pass is expected before the first tolerance
tightening, using this item's own baseline as the starting point.

**Two dimensions, geometry only.** `alan/web/src/desktop-audit/geometry.ts`
infers structure from the rendered page (no opt-in markup annotation — an
area that "looked fine" would never have been annotated) and
`alan/web/src/desktop-audit/audit-helpers.ts` grades it:

- `actionGroupFit` — for each flex-row container (column/column-reverse
  excluded) with ≥2 visible children, whether those children land on a single
  row band; if they wrap onto ≥2 bands **and** their summed width plus gaps
  would have fit within the container's content width minus `fitHeadroomPx`,
  that is a violation. Only the innermost violating container in a nested
  pair is counted, so one visible defect counts once. A child's own summed
  width is its `firstTextRect` width (the same leading text-bearing
  descendant `crossAxisAlignment` anchors on) when one was found, not its
  full border-box width: a control sitting above a long caption (e.g.
  `RepoActionRow.tsx`'s Maintenance/Hide buttons) renders a box as wide as
  the caption, which would otherwise mask the control row's own wrap. The
  caption is free to keep wrapping on its own line without counting against
  the row's fit. Home's per-repository Maintenance/Hide/Settings row used to
  be exactly this shape; a 2026-08-20 item gave the group `sm:flex-nowrap`
  and each control's wrapper span `max-w-40` so the caption wraps within a
  fixed column instead of forcing the row wide, discharging it from this
  count entirely. What remained — `RecurringProcessesPanel`'s per-row
  label/chip group (main's #1130, merged after this gate's baseline was
  first measured) and one `RepoSection` table primary-cell wrap newly
  visible at `@max` only (a badge-bearing cell wrapping against its sibling
  text; out of the 2026-08-20 item's file scope) — was `actionGroupFit`'s
  tolerated-on-arrival entry (`home@standard: 5`, `home@max: 6`, re-measured
  2026-08-20). **Discharged by conversations-rail-and-chat-in-shell:** the
  `RecurringProcessesPanel` row shared this same shape (a short status-chip
  sibling vertically centered, via `items-center`, against a taller label
  sibling — `rect.y`-based band detection sees mismatched tops even though
  nothing genuinely wraps), and swapping it to `items-start` cleared it;
  `home@standard`/`home@max` both measure `actionGroupFit: 0` as of this
  item.
  `Start a project` is never graded together with `Build`/`Explore` as one
  candidate: `RepoActionToolbar.tsx` wraps it in a plain (non-flex) `<div>`,
  a sibling of `RepoActionRow` inside a `flex-col` toolbar — column-direction
  containers are excluded from `actionGroupFit` by `isRowFlexContainer`, so
  the two were already structurally distinct action groups before this fix,
  not a byproduct of it. The editable `engine-config-editor` screen this
  paragraph used to cite here was replaced by `repo-engine-config`, a
  read-only, repo-scoped view (main's #1128); its current
  `repo-engine-config@standard` entry measures `actionGroupFit: 0` — the
  checkbox-grid violations the old screen had are gone along with the
  editable checkbox rows they were measuring.
- `crossAxisAlignment` — for each repeating group of ≥`minRepeatingGroupSize`
  same-signature siblings, whether every row's columns agree on their
  cross-axis (vertical) text anchor within `anchorTolerancePx`; a row whose
  columns disagree beyond tolerance flags its group once.

Both are geometry measurements, never aesthetic or taste judgment — that
question is deliberately deferred to the (advisory, never-blocking) AI-assisted
`ui_review` track, which files issues rather than blocking.

**Two states per screen.** Each of the 15 screens in
`alan/web/e2e/mobile-audit/screens.ts` (reused, not forked) is evaluated at
desktop in a `standard` state (the state already seeded today) and, for 10 of
the 15, a `max` state that seeds every compatible conditional element at once
— outcome notices, error/warning messages, badges, many rows, realistically
long text. `connect-wizard-step1`, `prep-chat`, `repo-engine-config`,
`repo-rail-collapsed`, and `repo-drawer-open` are `maxState: "none"` (each
with a `noMaxStateReason` in `screens-states.ts`): `connect-wizard-step1`'s
only conditional element needs a live GitHub round-trip the constraints
forbid driving synchronously, `prep-chat`'s archived transcript is already a
single, terminal static rendering with no conditional elements,
`repo-engine-config`'s read-only body renders every structural section
unconditionally from one registered engine, and the conversations-rail
shell's two layout-mode screens (`?rail=collapsed`, `?drawer=open` on the
repo dashboard route — see `DESIGN_SYSTEM.md`'s "Conversation rail,
collapse, and drawer" entry) are orthogonal to content richness, not
proportional to it: collapsing the rail always renders the same fixed
compact indicator regardless of how much dashboard content sits behind it,
and the drawer's own content (the repository switcher list plus the
standard-state rail) is already covered by the switcher's and rail's own
audits — none of these five has a conditional element orthogonal to what
standard already renders.
Where a screen's variants are mutually exclusive, the max state picks the most
content-bearing one and names what remains uncovered in the registry's
`uncovered` field (`explore-architecture`'s live-turn streaming reply, for
example) rather than silently dropping it. Every state is reached by seeding
or forcing state directly (API creates, on-disk fixture writes, localStorage
seeding) — never by driving a live asynchronous flow, since the flake
tolerance is empty and a gate that flakes is worse than no gate. The baseline
keys each `(screen, state)` pair as `"<screen>@<state>"` — screen names are
constrained to `/^[a-z0-9-]+$/` so `@` can never collide with a real screen
name.

**Ratchet, shared with mobile.** `alan/web/src/ui-audit/baseline-ratchet.ts`
is the generic comparison/diff-formatting core both the mobile and desktop
baselines wrap (`alan/web/src/mobile-audit/baseline-compare.ts`,
`alan/web/src/desktop-audit/baseline-compare.ts`) — one implementation,
two dimension lists, so the two ratchets can never silently diverge in
behavior. `alan/web/e2e/desktop-audit/baseline.json` and its pin
`baseline-high-water.ts` are tightening-only, hand-edit-only, with no
regeneration mechanism, exactly like the mobile baseline's pair.

**Measurement procedure (for a future tolerance or baseline change).** Push
the branch; the desktop step uploads its raw per-group margin report as a
`desktop-audit-results` artifact. Read the measured deviation for the
nearest clean group and for any known defect; a tolerance is valid only when
the nearest clean group's deviation is ≤ half the tolerance and every known
defect exceeds it by ≥2x. Then hand-write `baseline.json` and
`baseline-high-water.ts` from the measured counts — never predicted, and
never from a local machine.

The mobile baseline is measured the same way. The same `mobile-audit-gate`
job uploads `alan/web/mobile-audit-results/` as a `mobile-audit-results`
artifact whether the audit passed or failed — the upload step sits directly
after the mobile audit and ahead of the desktop gate, so a desktop step that
hangs into the job timeout cannot take the mobile report with it. It holds
whatever the audit wrote before it stopped: a run that died mid-screen
carries the per-screen files up to that point and no `report.json`.
`report.json` carries one entry per screen under `screens`, and each
`<screen>.audit.json` the per-element detail behind it. The three baseline
dimensions are not stored as numbers — `src/mobile-audit/baseline-compare.ts`
derives them, and a hand edit must derive them the same way: `overflow` is
`0` when `overflow.pass` is true and `1` otherwise, `tapTarget` is the length
of `tapTargets.violations`, and `contrast` is `accessibility.violationCount`
— the number of violated axe rules out of the audit's rule set
(`AXE_RULE_IDS` in `mobile-audit.spec.ts`: `color-contrast` plus seven ARIA
rules), so the dimension's name is narrower than what it counts. A
tightening edit to `alan/web/e2e/mobile-audit/baseline.json` and its pin
reads its counts from that artifact on the pushed branch. Until this upload existed the rule above
could not be followed for the mobile pair at all — the report was written on
the runner and discarded, and the job log prints no per-screen counts — so a
change that shrank a screen had no sanctioned way to learn its new count
(alan#1640). No fleet role can read a CI artifact: tightening either baseline
remains a hand edit by whoever holds forge access.
