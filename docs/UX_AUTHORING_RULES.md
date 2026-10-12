# UX/UI Authoring Rules

This doc is **advisory and prescriptive**. It complements — and does not
supersede — `docs/DESIGN_SYSTEM.md` (the descriptive, ratified record of
today's visual language) and `alan/web/src/ui-spec.ts` (the measurable
floors: tap-target size, contrast ratios, viewport width). Nothing here is
wired into a CI gate, a drift guard, or the mobile-audit ratchet.

> Prescriptive layer. `DESIGN_SYSTEM.md` answers "does this screen conform?"; `ui-spec.ts`
> owns the measurable floors. This file answers "is this screen good?" It is **advisory**
> — no gate, no ratchet, no verification-team wiring. Planning-prompt grounding only.
>
> Every rule is falsifiable (point at a screen and say "this violates rule 21"),
> precedented (names a shipped product or system), and subtractive (states what it
> forbids). Constraints assumed: Tailwind, dark-only, the nine existing `--color-*`
> tokens, the ratified six-step type scale, 44px tap floor, 390px viewport floor,
> WCAG 2.1 AA. Anything requiring a token, type-step, or ratified-recipe change is
> quarantined in §Requires Migration and is **not** part of the 50.

## The Ten (paste-alone block)

**1. Comparable rows are a list, not a card grid.** Any data where more than one
row is being compared at a glance renders as a table or dense list. *Precedent:*
Polaris confines cards to low-density focused-editing contexts and reserves index/table
format for compare-at-a-glance data. *Forbids:* rendering the Inbox or workspace
list as a card stack.

**4. Causal evidence occupies the top of fold; metadata and lineage go beside or
below.** *Precedent:* Sentry ranks the stack trace "the most important piece of
information" and promotes Event Highlights above the fold, with first/last-seen
and activity pushed to a sidebar. *Forbids:* a detail view that leads with configuration
or identity metadata while the blocking artifact (failing gate, escalation reason,
diff) sits three sections down; forbids a single long scroll that interleaves evidence
and lineage.

**7. One primary action per screen — exactly one saturated fill in the viewport.**
Everything else is outline, ghost, or text-only. *Precedent:* Carbon, Polaris, and
Primer all ship a primary/secondary/tertiary button hierarchy; none defines a two-primaries
state. *Forbids:* two solid-fill buttons of equal visual weight competing on one
view.

**18. A lifecycle/workflow state enum caps at five distinct hues.** States beyond
five share a tier's hue and differentiate by icon and label. *Precedent:* Polaris
ships 5 Badge tones and deliberately reuses warning's yellow for its 5th ("attention")
rather than minting a hue; Primer ships 5; Datadog caps categorical color at 6 and
repeats rather than extending. *Forbids:* minting a 7th hue for a new workspace
status.

**20. Color is never the only channel.** Every status marker ships with a text label
or accessible name. *Precedent:* Vercel's StatusDot composes color + sentence-cased
label + a composed accessible title; Datadog documents that its "Semantic" auto-color
palette has **no** accessibility support. *Forbids:* a bare colored dot or a color-only
progress rail with no legend, label, or accessible name.

**21. The danger hue is reserved for states that require action now.** *Precedent:*
ANSI Z535.1 / ISO 3864-4 — red means "will result in death or serious injury if
not addressed," never informational; GitHub deliberately uses color to mark "the
item that needs attention" and is explicitly "mindful" not to overuse it. *Forbids:*
red on a non-actionable info badge for visibility; forbids red as the default rendering
of an unknown or pending state.

**22. Routine, expected state changes are styled calmer than states needing intervention.**
*Precedent:* 85–99% of clinical alarm signals do not require intervention, and treating
routine events with alert-level urgency trains viewers to ignore alerts. *Forbids:*
styling "PR opened," "check running," or "workspace queued" with the same saturated
treatment reserved for "blocked" or "escalated."

**35. A streaming or in-progress view loads with trailing history immediately —
never an empty pane waiting for the next event.** *Precedent:* GitHub Actions backfills
the last 1,000 emitted log lines when you open a running job, explicitly to give
"immediate context into the run's progress and status." *Forbids:* opening a live
workspace's output onto a blank region that fills only going forward.

**43. Mobile is a re-scoped task flow — triage, then drill-down — not a responsively
shrunk desktop screen.** *Precedent:* GitHub built a separate mobile IA (PR → Checks
summary → per-check detail → single-tap log) rather than reflowing the desktop log
viewer; Linear ships a separately authored, customizable mobile navigation rather
than collapsing the sidebar. *Forbids:* making the DAG canvas or the full workspace
table the primary phone surface.

**45. A phone-answered checkpoint is a low-density focused surface with at most
two thumb-reachable primary choices above the fold.** Supporting evidence is disclosed
below, never required to reach a decision. *Precedent:* Polaris's low-density/focused-editing
pattern (single task, no grid, larger targets); alan's own two-tap target. *Forbids:*
a multi-column status table as the primary interaction surface for a phone decision.

---

## Full catalog

### 1. Layout & information architecture (1–6)

**1.** *(see The Ten)* Comparable rows are a list, not a card grid.

**2. One navigation frame, one content pane.** *Precedent:* Linear's "inverted-L"
— persistent left sidebar plus a thin top bar framing a single content pane, with
no secondary browser-style tab strip fragmenting the workspace. *Forbids:* adding
a second persistent nav rail or tab bar alongside the primary navigation.

**3. A container is entirely high-density or entirely low-density — never both.**
*Precedent:* Polaris explicitly forbids mixing densities within one card. *Forbids:*
a dense table nested inside a padded low-density card frame, or a padded card stack
inside a dense panel.

**4.** *(see The Ten)* Causal evidence occupies the top of fold.

**5. A status surface has a fixed section order: identity → aggregate health → per-item
detail → raw output last.** *Precedent:* Datadog mandates About (identity/description)
→ Overview (service checks, key metrics, monitor summary) → per-feature groups →
Logs last for every shipped integration dashboard. *Forbids:* leading with a log
or output stream; forbids burying the health rollup beneath per-item detail.

**6. Every panel belongs to a named, titled group.** *Precedent:* Datadog — widgets
are "placed within a group based on thematic organization, rather than directly
on the background." *Forbids:* an unlabeled stat block or metric card floating loose
between sections.

### 2. Visual hierarchy (7–11)

**7.** *(see The Ten)* One primary action per screen.

**8. Navigation chrome stays desaturated.** Saturated color belongs to content and
status, not the frame. *Precedent:* Linear's redesign explicitly reduced how much
brand blue was used in chrome so the frame stays neutral and content carries hierarchy;
Vercel's base palette is pure black/white with every chromatic color reserved for
workflow meaning. *Forbids:* tinting the sidebar, top bar, or nav background in
the accent hue.

**9. Hierarchy in a dense view is carried by contrast, size, and position — never
by introducing a new hue.** *Precedent:* Linear restored hierarchy by making neutral
text and icons lighter in dark mode (a contrast adjustment), not by adding accent
colors. *Forbids:* adding a color to make a section "pop"; forbids leaving a heading's
color implicit when every sibling heading states it (`HealthView.tsx:180`'s `text-xl
font-semibold` omits the `text-text-primary` every other Shell heading carries).

**10. Secondary text on a colored surface is a desaturated shade of that surface's
hue, never neutral grey.** *Precedent:* Refactoring UI's grey-on-color rule. *Forbids:*
a neutral-grey timestamp or caption sitting directly on a status-colored chip or
banner fill.

**11. Text case is a hierarchy channel independent of size:** group/section titles
Title Case, widget and sub-headings sentence case, data and status values lowercase.
*Precedent:* Datadog specifies group titles Title Case, widget titles sentence case,
and graph titles leading with the most important word; alan's ratified shell-screen
casing convention already says the same thing. *Forbids:* Title-Casing a status
value, or sentence-casing a nav control, in the name of consistency.

### 3. Density & spacing (12–16)

**12. Row height is chosen by content shape.** A taller row is justified only when
its content is expected to wrap to two or more lines. *Precedent:* Carbon restricts
its "tall" row variant to exactly the case where "data is expected to have 2 lines
of content." *Forbids:* increasing row height for breathing room when every cell
is single-line.

**13. Intra-row spacing draws from the bottom of the spacing scale; section-level
gaps draw from the top. No token serves both jobs.** *Precedent:* Atlassian's split
between `space.025`–`space.100` (2–8px, inline/intra-component) and `space.400`–`space.1000`
(32–80px, layout). *Forbids:* using the same `space-y-*` value as both a row's
internal rhythm and the gap between page sections — `Project.tsx:1202–1251` alternates
`space-y-3` and `space-y-2` across six sibling `<section>` blocks with no stated
rule distinguishing them.

**14. In dense views, separation comes from surface-color zoning, not from added
whitespace.** *Precedent:* Polaris's explicit high-density alternative to dividers
and gaps. *Forbids:* separating groups inside a table by widening vertical padding,
which fights the density the table exists for.

**15. Dividers belong to high-density table contexts only.** Card stacks separate
by spacing and surface color. *Precedent:* Polaris calls dividers in card contexts
"confusing and hard to understand." *Forbids:* table-style vertical rules inside
a checkpoint or decision card stack.

**16. A toolbar or bulk-action bar is sized to the density of the rows it governs.**
*Precedent:* Carbon pairs its small toolbar exclusively with short/compact rows
and its large toolbar exclusively with normal/tall rows. *Forbids:* a 48px+ bulk
bar atop a compact list, or an undersized bar atop tall rows.

### 4. Color & status encoding (17–25)

> **Scope note — the hue-count question.** Three different caps apply to three different
> things, and conflating them produced the specialists' apparent contradiction. Rule
> 17 governs a *health rollup* (one thing, is it fine). Rule 18 governs a *lifecycle
> enum* (many mutually exclusive workflow states). Rule 25's companion constraint
> governs a *single scannable column* at one glance. Sentry's six-level severity
> taxonomy (fatal/error/warning/info/debug/sample) is neither a health rollup nor
> a lifecycle enum and is not a counterexample to either.

**17. Health is tri-state: healthy / transitional / blocked map to exactly three
hues.** *Precedent:* Grafana (ok / pending / firing), GitHub Actions (success /
in-progress-and-queued sharing yellow / failure), Datadog's alert guidance. *Forbids:*
a fourth "warning-adjacent" hue in a health context that is neither clearly transitional
nor clearly blocked.

**18.** *(see The Ten)* A lifecycle enum caps at five hues.

**19. Two states rendered in the same hue must call for the same response.** If
they call for different responses, they differ by icon or shape — not only by label
text read at close range. *Precedent:* Carbon's color+shape+symbol triad; ANSI
Z535's tier semantics, where a tier *is* a required response. *Forbids:* `escalated`
(needs a human decision) and `failed` (needs a re-dispatch) both rendering as `bg-red-500/15
text-red-300` with no distinguishing glyph — the current state of `StatusBadge.tsx:19,21`.

**20.** *(see The Ten)* Color is never the only channel.

**21.** *(see The Ten)* The danger hue is reserved for act-now.

**22.** *(see The Ten)* Routine changes are styled calmer than interventions.

**23. Status color renders as a token-sized dot, chip, or icon — never a full-row
or full-card background wash.** *Precedent:* Vercel physically constrains status
color to indicators of ~10px or less and never a background fill. *Forbids:* tinting
an entire Inbox row or workspace card by its severity. *(Scoped: this rule governs
status encoding on scannable rows. It does **not** target the ratified block-level
error banner — see §Conflicts with ratified recipes.)*

**24. A state escalates to the danger hue only after a debounce; a single failing
sample renders transitional.** *Precedent:* Grafana's alert state machine mandates
Pending → Firing, and its docs are explicit that "it will take at least two evaluation
cycles before an alert is fired." *Forbids:* a health finding or workspace status
flipping straight to red on one poll.

**25. Color-to-meaning binding is global — the same hue means the same thing on
every screen.** *Precedent:* Grafana — "using the same colors for the same type
of information shown on different panels makes it easier to understand"; the
per-glance ceiling is Datadog's 6-colour categorical cap (rule 18) plus Healey
et al.'s finding that colour-coded task performance stays reliably high only to
~7 categories and degrades beyond. *Forbids:* amber meaning "reset/caution" on
a button and "queued" on a badge with no stated shared meaning; forbids a status
column using six or more hues to mark fine-grained sub-states — sub-states
differentiate by icon or label inside a shared hue.

### 5. Typography (26–30)

**26. Six steps is enough.** Hierarchy comes from step, weight, and case — not from
new sizes. *Precedent:* Primer ships 8 steps (`f00`–`f6`); Material 3's 15 type
tokens resolve to 5 functional roles × 3 sizes, not 15 independent jobs. **This
confirms alan's ratified six-step scale — no change needed.** *Forbids:* an arbitrary
`text-[13px]` to fit content into a layout.

**27. Below the smallest step, reduce weight or line-height — never introduce a
smaller raw size.** *Precedent:* Primer stops its scale at `f3` (20px) below normal
weight, citing legibility. *Forbids:* `text-[10px]`/`text-[11px]` spreading beyond
the documented DAG/graph-canvas exception into list, table, or badge UI; fix density
with row height (rule 12) instead.

**28. Heading steps carry a single weight; size alone separates heading levels.**
*Precedent:* Atlassian ships all seven heading steps at Bold, varying weight only
in body. *Forbids:* a semibold `h2` beside a bold `h1` as an extra hierarchy signal
layered on top of size.

**29. Semibold is the weight ceiling.** *Precedent:* Vercel's Geist ships no 700
weight anywhere. *(Third-party sourced — see §Low-Confidence Findings in the evidence
appendix; also flagged below in §Confidence and known gaps.)* *Forbids:*
`font-bold` as an emphasis device inside a dense dark table or list.

**30. Numeric and identifier content — IDs, SHAs, durations, costs, token counts
— is tabular or monospace; prose is not.** *Precedent:* Vercel reserves Geist Mono
for code and metrics specifically; GitHub renders Actions logs monospace with ANSI/24-bit
color pass-through as first-class content. *Forbids:* proportional digits in a column
of costs or durations meant to be compared vertically.

### 6. Component & state patterns (31–42)

**31. Loading uses one shared component, and skeletons mirror the layout they replace.**
*Precedent:* Polaris requires a skeleton "mimicking layout similarly to the state
that will be loaded"; Carbon specifies skeletons appear "only a few seconds" with
motion signalling the page is not stuck. *Forbids:* three different bare loading
strings standing in for structured content — `WorkspaceList.tsx:700` and `Project.tsx:1147`
say `"Loading…"`, `Project.tsx:316` says `"Loading conversations…"`, `Project.tsx:585`
says `"Loading doc…"`, and no shared component exists.

**32. Skeletons never stand in for interactive controls — buttons stay hidden until
data resolves.** *Precedent:* Primer explicitly instructs avoiding skeletons for
interactive elements, "instead hiding them until loading is complete"; Carbon's
exclusion list bars skeletons for toasts, overflow menus, dropdown items, modals,
and loaders. *Forbids:* a skeleton-shaped action row that "loads in."

**33. Empty states are typed by cause,** and error-state graphics are never playful:
never-used invites the first action, cleared confirms completion, failed states
what broke. *Precedent:* Atlassian distinguishes blank slate (never tried) from
empty state (completed) from error message, with a different copy register for each;
Primer's Blankslate rule drops the delight treatment for error states specifically.
*Forbids:* one "Nothing here" template reused across first-use, drained-inbox, and
fetch-failure; forbids reusing the friendly empty illustration on a failure screen.

**34. A full empty-state block is page- or panel-level only.** Inside a cell, node,
or row, a one-line inline message substitutes. *Precedent:* Polaris scopes EmptyState
to "a full page in the admin," not individual list or table cells. *Forbids:* an
illustrated empty-state block inside a DAG node or a table cell.

**35.** *(see The Ten)* Streaming loads with trailing history.

**36. Animation means in-progress; a static indicator means resolved — and there
is only one in-progress channel.** *Precedent:* Vercel's StatusDot pulses only
in `BUILDING`/`QUEUED` and goes static at `READY`/`ERROR`/`CANCELED`, with an explicit
anti-pattern: "Don't add separate spinners alongside the dot." *Forbids:* animating
a terminal-state indicator; forbids a spinner beside an already-animating status
indicator.

**37. Status re-renders on state transitions, not on poll ticks.** *Precedent:*
Vercel documents this as an explicit anti-pattern — "Don't flash the color through
every transitional state on a polling tick; only update when the readyState changes."
*Forbids:* a status chip that flashes or re-animates on every refetch even when
the underlying state is unchanged.

**38. Failures are addressable at their own granularity.** A failing check, finding,
or log line is linkable individually. *Precedent:* GitHub Actions ships per-line
anchors so it is "easy to copy a link that highlights a specific line number to
share a CI/CD failure." *Forbids:* a failure reachable only by scrolling an un-anchored
blob of synthesis or log output.

**39. In a time-backed view, state transitions are annotated on the timeline — not
only reported as a current-state badge.** *Precedent:* Grafana renders pending/alerting/resolving
as colored annotations directly on the time-series panel. *Forbids:* a workspace
showing only `escalated` with no visible trace of when it left `running`.

**40. Streaming output regions use `aria-live="polite"`; at most one polite region
per page, present in the DOM at load.** *Precedent:* WAI-ARIA APG — `assertive`
"could potentially clear the speech queue of previous updates" and is for time-critical
interruption only; several screen readers announce only into regions that existed
at load. *Forbids:* `aria-live="assertive"` on a token stream (one disruptive interruption
per chunk); forbids wiring `aria-live` onto every status chip.

**41. Disclosure controls are real buttons with `aria-expanded`; every tab carries
`aria-selected` and `aria-controls`, not just the active one.** *Precedent:* APG's
disclosure pattern makes `role="button"` + `aria-expanded` its only two non-optional
requirements; APG's tabs pattern requires `aria-selected` unconditionally across
the tablist. *Forbids:* a chevron `div` with an onClick and no button semantics
(breaks Enter/Space); forbids omitting `aria-selected="false"` on inactive tabs.

**42. A dialog traps focus, restores it to the invoking element on close, closes
on Escape, and — when the action is destructive — places initial focus on the least-destructive
option.** *Precedent:* APG's dialog pattern specifies all four, including the high-risk-dialog
focus exception. *Forbids:* a force-ship or delete confirmation auto-focusing Confirm
as the Enter-key default; forbids a mobile sheet whose background content remains
tabbable.

### 7. Mobile decision surfaces (43–47)

**43.** *(see The Ten)* Mobile is a re-scoped triage flow.

**44. Mobile carries the mutating actions that matter, inline.** *Precedent:* GitHub
Mobile exposes cancel and re-run "throughout these new screens" rather than gating
them behind a desktop settings page. *Forbids:* a read-only mobile mirror where
answering a checkpoint, or unblocking a workspace, requires switching to desktop.

**45.** *(see The Ten)* A phone checkpoint is two choices plus disclosure.

**46. Every tappable control clears 44px; density optimizations never shrink a hit
area.** *Precedent:* Apple HIG's 44×44pt convention, which is alan's ratified
floor. **Note:** WCAG 2.1 AA has no target-size criterion at all — 2.5.5 (44px)
is AAA under 2.1 and 2.5.8 (24px) only reached AA in WCAG 2.2. The 44px floor is
HIG-sourced and exceeds both. *Forbids:* applying a compact-row treatment to a mobile
answer control and letting its hit area drop below 44px to save vertical space.

**47. A decision surface never requires horizontal scroll at 390px.** Wide tabular
content is wrapped in a horizontal-scroll container or restructured into stacked
rows. *Precedent:* Datadog sets minimum column-span floors per widget *type* (timeseries
≥4 columns, stream widgets ≥6) rather than one global minimum, on the grounds that
below the floor a widget is "squashed"; alan's own 390px floor. *Forbids:* a raw
`<table>` with no `overflow-x-auto` wrapper on a screen a decision is made from
— `HealthView.tsx:234–244` is exactly this, against a ratchet that currently tolerates
`health.overflow: 0`.

### 8. Motion (48–50)

**48. Motion signals state, never delight.** *Precedent:* Carbon — "motion is used
in a skeleton state to convey that the page is not stuck loading." *Forbids:* spring
or bounce easing on skeletons, list entry, or card mount; a shimmer or pulse only.

**49. At most one animating element per viewport.** *Precedent:* Vercel's single-channel
discipline (the dot animates; no additional spinner is permitted alongside it).
*Forbids:* a pulsing status chip inside a shimmering skeleton row beside a spinner
— three simultaneous claims on the same attention.

**50. Every animation respects `prefers-reduced-motion`, degrading to an instant
state change rather than a slower one.** *Precedent:* universal platform convention
(Apple HIG, Material 3) and WCAG's motion-related criteria. *Forbids:* an in-progress
pulse that keeps pulsing under reduced-motion; forbids merely lengthening a duration
in response to the preference.

---

## Requires migration (quarantined — NOT part of the 50)

Each item below would break a drift guard, the ratchet, or a ratified recipe. None
is directly adoptable; each needs its own PR with the stated cost paid in full.
This list merges `design_system_comparator`'s M1–M5 (rule-driven) with `feasibility_analyst`'s
migration-cost table (drift-mechanics-driven), which were produced independently
and overlapped.

| # | Change | Guard that fires | Full cost in one PR | Verdict |
|---|---|---|---|---|
| **M1** | **Status-hue tokens** (`--color-status-*`). Today 7 raw Tailwind hues (`blue/amber/green/zinc/red/purple/orange`-500 at `/15`) carry state semantics with zero token backing across `StatusBadge.tsx`, `ProjectStatusBadge` (`Projects.tsx:19-24`), and `HealthView.tsx`. | `design-system.drift.test.ts` colour test parses every `--color-*` from `index.css` and requires an exact name+hex row in DESIGN_SYSTEM.md's table — a new token without a row fails. | Add tokens to `@theme`; add matching backtick-quoted doc rows; migrate every consuming call site; re-run the mobile audit (contrast counts on `build`/`explore-architecture`/`workspace-decision` each tolerate 1). | **Do it.** Prerequisite for rules 18, 19, 25 being enforceable rather than aspirational. |
| **M2** | **Separate the primary-action accent from the status palette.** `--color-btn-create` (#10b981 green) and a green "healthy/complete" status chip share a meaning-space: one hue says both "click this" and "this is fine." *Precedent:* Carbon, Polaris, and Primer all keep accent tokens structurally separate from success/warning/danger. | Same colour drift test as M1, plus every `Button` call site using the create variant. | Token split + doc rows + all create/action call sites. | **Highest cost on the list.** Do after M1; needs the owner's call on which side keeps green. |
| **M3** | **`--color-banner-error` token** for the block-level error banner (DESIGN_SYSTEM.md deferred follow-up #5). The recipe `rounded-md bg-red-500/15 px-3 py-2` + `text-red-300` runs to 15 sites in 11 files with no token backing, unlike its field-level counterpart (`text-btn-destroy`). | Colour drift test + `RATIFIED_RECIPES` fragment existence check. | Token + doc row + all 15 sites + update the quoted recipe fragment in the doc atomically. | **Do it with M1** — same class of work, and rule 25 (global colour binding) requires the banner's red to be the danger red. |
| **M4** | **Any type-step change** — adding a step, removing one, or snapping the scale to a named ratio (Polaris's major-third 1.2, Atlassian's minor-third, Material 3's 15-token/5-role structure). | `design-system.drift.test.ts` typography test fails **immediately and bidirectionally**: an undocumented-but-used step fails; a documented-but-unused step fails. No partial rollout is possible — the guard scans the whole non-test `src/` tree at once. | Every non-test call site migrated in one PR + doc table edit. | **Don't.** Rule 26 confirms six steps sits inside Primer's and Material's precedented range. |
| **M5** | **Carbon's literal row-height pixels** (24 / 32 / 40–48 / 64). | None directly — but the values must coincide with alan's ratified spacing scale or they introduce arbitrary values. | Spacing narrative is unguarded prose, so nothing mechanically catches non-conformance either way. | **Don't.** Rule 12's *principle* (content-driven row height) is adoptable now against existing tokens; the px values are not. |
| **M6** | **Raise the tap-target floor** (44 → 48). | `ui-spec.drift.test.ts` asserts `--spacing-tap-target` in `index.css` equals `tapTargetMinPx` in `ui-spec.json` — both must change together or CI blocks. | The real cost is the ratchet: every element between the old and new floor becomes a fresh `tapTarget` violation across all 8 screens simultaneously, and `baseline.json` must be **raised** in the same PR — the one legitimate case where `assertBaselineNotRaised`'s tightening-only direction is inverted. Plus `mobile_ui_spec.md`'s prose citation. | **Don't.** 44 already exceeds WCAG 2.2 AA (24px) and 2.1 AAA (44px). |
| **M7** | **Converge the 5 divergent badge shapes** onto the ratified `inline-block rounded px-2 py-0.5 text-xs` recipe (deferred follow-up #1). | `RATIFIED_RECIPES` is an `it.each` per-fragment existence check in **both** doc and source. Changing the fragment string keeps the old assertion green while one straggler call site remains — the guard cannot detect "recipe changed but doc still quotes the old one" until the doc edit lands, at which point every unmigrated site fails loudly. | Doc fragment + every call site atomically; remove follow-up #1 from §Deferred. | **Separate PR.** Rules 20 and 23 constrain the badge's *channels* and *size*, not its shape — this stays a visual-convergence decision. |
| **M8** | **A 6th severity hue** to match Datadog's 6-category ceiling (e.g. a dedicated "outlier/undefined" purple tier). | Colour drift test (as M1). | — | **Reject.** Rules 17 and 18 must be satisfied with the existing hue count; a 6th tier differentiates by icon and label inside an existing tier. |
| **M9** | **Remap severity hues to ANSI/ISO's literal set** (red/orange/yellow/green). | Colour drift test (as M1). | — | **Don't.** Rules 17 and 21 adopt the *tier count and reservation logic* now; remapping hue values is a token edit with no user-visible gain. |

**Not on this list, and cheap:** the Force-ship variant question (deferred follow-up #6) has **no drift test at all** — `Project.tsx`'s `variant="destroy"` is prose-flagged as an open question, not fragment-guarded. Resolving it costs one `Project.tsx` edit and one deleted paragraph.

---

## Conflicts with ratified recipes — argued, not silently overridden

**The two-tier error convention stands.** Rule 23 ("status color is a dot or chip, never a row wash") appears to contradict the ratified block-level error banner, which *is* a tinted background (`rounded-md bg-red-500/15 px-3 py-2`). It does not. Rule 23 is scoped to status encoding on scannable rows in a list, where a full-row wash destroys the ability to scan by position. The block-level banner is a page-level message container, not a per-row status indicator — a different job with a different failure mode. **I explicitly do not collapse the block/field two-tier split**, which is a ratified convention, not debt.

**The casing convention is ratified and extended, not replaced.** Rule 11 restates alan's existing Title-Case-controls / lowercase-data-and-status convention and adds Datadog's sentence-case-for-sub-headings rung. Compatible; nothing flattens.

**Force-ship: `destroy`, with a Cancel-focused confirm dialog.** Deferred follow-up #6 asks destroy vs reset. Rules 21 (danger hue reserved for act-now, irreversible consequence) and 42 (destructive dialogs focus the least-destructive option) resolve it together: force-ship bypasses gates and is not reversible, so it is destructive, and the guard against a misfire moves from *colour hesitation* to *focus placement*. This is a recommendation — DESIGN_SYSTEM.md records it as the owner's call, and these rules are advisory, so it stays open until ratified.

**Tab pill vs underline stays open.** Rule 41 governs tab *semantics* only (`aria-selected`, `aria-controls`). It has no opinion on `WorkspaceList.tsx`'s pill vs `components/UnderlineTab.tsx`'s underline. Directional, not binding: underline reads as in-content view-switching (Linear, GitHub), pill reads as a filter toggle (Polaris, Primer) — the two components arguably do different jobs and may not need converging at all. Deferred follow-up #2 remains open.

**The "shipped" vs "completed" domain split is out of scope.** No rule here proposes unifying workspace and project completion vocabulary; it is a ratified domain distinction, not a consistency defect.

---

## Application (a) — the existing front door

Sources: `feasibility_analyst`'s repo grounding, with `StatusBadge.tsx:19,21`, `screens.ts`, and `baseline.json` independently spot-verified by the `verifier`.

| Surface | Route | Component | Ratchet (overflow / tapTarget / contrast) |
|---|---|---|---|
| Home | `/` | `pages/WorkspaceList.tsx` (719 ln) | 1 / 1 / 0 |
| Projects | `/projects` | `Projects.tsx` (342 ln) | 0 / 1 / 0 |
| **Project detail** | `/projects/:projectId` | `pages/Project.tsx` (1262 ln) | **not registered** |
| Inbox | `/inbox` | `pages/Inbox.tsx` (1166 ln) | 0 / 2 / 0 |
| Health | `/health` | `pages/HealthView.tsx` (248 ln) | 0 / 3 / 0 |

**Named current violations:**

1. **Rule 19 — `StatusBadge.tsx:19,21`.** `escalated` and `failed` map to the identical `bg-red-500/15 text-red-300` pair. They demand different responses (a human decision vs a re-dispatch) but are distinguishable only by label text read at close range. Fix without migration: add a distinguishing glyph. Fix with migration: M1.
2. **Rules 18 + 25 — status hues repo-wide.** Seven-plus raw Tailwind hues carry state semantics across `StatusBadge.tsx`, `ProjectStatusBadge`, and `HealthView.tsx`'s severity/category chips, with no `--color-*` backing and no documented hue→meaning binding. DESIGN_SYSTEM.md's colour table covers 4 button colours + 5 neutrals and is silent here. This is the largest single conflict in the codebase; M1 is its fix.
3. **Rule 47 — `HealthView.tsx:234–244`.** A raw `<table>` with no `overflow-x-auto` wrapper, unlike `MarkdownBody.tsx`'s GFM-table convention which explicitly wraps wide tables. At 390px a severity + description + links table risks the exact `overflow` metric the ratchet measures, and `health.overflow` is currently tolerated at **0** — a regression fails CI outright, not advisorily. **Cheapest high-value fix on this list.**
4. **Rule 13 — `Project.tsx:1202–1251`.** Six sibling `<section>` blocks alternate `space-y-3` (1202, 1212, 1220) and `space-y-2` (1236, 1241, 1246, 1251) with no visible rule. Spacing is unguarded convention prose, so nothing catches it.
5. **Rules 31 + 32 — loading everywhere.** Three near-identical, non-identical loading strings (`"Loading…"` ×2, `"Loading conversations…"`, `"Loading doc…"`), no shared component, no skeleton anywhere. Greenfield: no migration cost, no drift guard, pure convention addition.
6. **Rule 9 — `HealthView.tsx:180`.** `<h2 className="text-xl font-semibold">System Health</h2>` omits the `text-text-primary` every other Shell heading carries. Harmless today only because no ancestor overrides text colour — a latent, not active, defect.
7. **Measurement gap, not a rule violation — Project detail is absent from `screens.ts`.** The largest front-door file (1262 lines: autonomy editor, burn, PR list, conversations, doc, propose-amendment, archive/delete, roadmap, complete/ship) has *zero* tap-target, overflow, or contrast measurement — not even a tolerated baseline. Registering it plus a captured baseline is the single cheapest structural improvement available and requires no rule adoption at all.

**Motion (rules 48–50) is entirely greenfield.** A repo-wide grep for `transition|animate-|duration-|ease-` outside tests finds only `Button.tsx:12`'s bare `transition-colors` and a few engine-config-editor components. Home, Projects, Project detail, Inbox, and Health have **no motion whatsoever**, and there is no `prefers-reduced-motion` handling anywhere. DESIGN_SYSTEM.md has no Motion section. Nothing to migrate; the rules are pre-emptive.

**Not established:** whether Home (`WorkspaceList.tsx`) currently renders as a list or a card stack was not determined — `feasibility_analyst` sampled by targeted grep rather than full read. Rule 1 should be checked against it before being cited as a violation.

## Application (b) — unbuilt lifecycle surfaces (parked)

**Conversational project creation (L1.1).** Today: a plain modal (`Projects.tsx:42–144` `NewProjectModal`, title + doc-ref, no conversation). The shared chat core exists as three `ChatCoreConfig` instances, but `ChatSession` has **no mode field**, and `_validate_new_workspace_shape` in `workspace_chat.py` hardcodes `title ⟹ team == "implementation_plan"` — that must be relaxed to a per-mode team allowlist before a planning conversation can mint a `roadmap_plan` workspace. Governing rules: **33** (a blank planning conversation is a never-used blank slate inviting the first action, not an error, and not the same template as a drained list), **35** (reopening a planning session shows prior turns immediately, never a blank pane), **40** (one polite live region for the streaming reply), **45** (the moment the conversation proposes a workspace *is* a decision surface — two choices, evidence below).

**Roadmap review + adversarial-pass output (L1.2, L1.3).** Delivered: `alan/project_roadmap.py` (`parse_roadmap_markdown`, `import_roadmap`, `RoadmapItem`, the `roadmap.json` execution store) — the ingestion contract is real. The architecture team's structure_mapper → devil_advocate → judge panel is the pattern precedent. Unbuilt: no `roadmap_plan` team, no challenge pass, and **nothing in the UI renders a verdict today**. Governing rules: **4** (the challenge and its verdict are the causal evidence and go top-of-fold; roadmap-item metadata goes to the sidebar), **5** (identity → verdict rollup → per-item challenges → raw synthesis last), **17** (a verdict is tri-state — accepted / contested / blocked; resist inventing a six-level confidence taxonomy for it), **22** (a challenge pass that finds nothing is a *routine* outcome, styled calm — not a green celebration, not a red alarm), **38** (adversarial output is long; each challenge must be individually linkable or it cannot be cited in a follow-up), **34** (a per-item "no challenges" result is an inline line, not an illustrated block).

**Phone-answered typed checkpoints (L0.3 retired, L1.3 pending).** The interactive rendering shell described here at L0.3's writing — the answering form, its submitting/answered/stale states, the inbox `answer_accept` action kind — is retired (remove-in-app-blocking-decision-answering-ui): the client no longer offers any way to submit an answer, and `answer_accept` is gone from the client's `InboxActionKind` type. `POST /api/workspaces/{ws}/tasks/{task}/answer` behind the control-token guard, and gold-label capture, are unchanged and remain the sole answering path — reachable only from outside the browser. The workspace page still shows a parked workspace's blocking question, but strictly read-only (question text, blocking flag, resolver role, each option as a discrete copyable row, and any recorded answer), via `features/workspace/components/OpenQuestionItem.tsx`. Any future phone-answered checkpoint work (L1.3 and beyond) starts from the endpoint plus a new surface, not from this retired shell — a typed checkpoint store (amendment-pattern-cloned, revision-fingerprint-bound) and the roadmap-phase decision *content* (per-phase approval, budget approval) are both still unbuilt. Governing rules: **43**, **44**, **45**, **46**, **47**. Rule 45 is the specific guard against L1.3's likely failure mode — a typed checkpoint carrying per-phase *and* budget fields will grow past what a two-tap surface tolerates unless the primary choices stay at two above the fold with everything else behind disclosure.

## DESIGN_SYSTEM.md's six deferred follow-ups — resolved vs left open

| # | Follow-up | Verdict | Reasoning |
|---|---|---|---|
| 1 | Converge the 5 divergent filled-badge shapes | **Left open, now bounded** | No rule decides badge *shape*. Rules 20 (label always present) and 23 (chip-sized, not a wash) constrain the badge's channels and scale; convergence itself is M7. |
| 2 | Converge tab-toggle visuals (pill vs underline) | **Left open** | Rule 41 governs semantics only. Directional note recorded above; the two components may legitimately do different jobs. |
| 3 | Add `min-w-11 gap-1.5` to the `Projects` nav link in `App.tsx` | **RESOLVED** | Rule 46 (44px floor, no density exception) makes it a straight defect, not a preference. Cheapest close on the list — no drift test, no ratchet movement. |
| 4 | Name an icon-button primitive if a second hand-rolled instance appears | **Left open** | No rule here mints primitives; the trigger condition (a second instance beyond `ChatDraftRows.tsx`) is unchanged. |
| 5 | Evaluate a `--color-banner-error`-style token | **RESOLVED IN DIRECTION — requires migration (M3)** | Rule 25 (global colour-to-meaning binding) requires the banner's red to be *the* danger red, which means it must be a token. The direction is decided; the token work is quarantined. |
| 6 | Force-ship variant colour (destroy vs reset) | **RESOLVED IN RECOMMENDATION — pending the owner** | Rules 21 + 42: irreversible gate bypass is destructive, so `destroy`, with the misfire guard moved to Cancel-focused confirmation. Recorded as the owner's call; these rules are advisory and cannot ratify it. |

**Score: 1 resolved outright (3), 1 resolved in direction (5), 1 resolved as recommendation (6), 3 left open (1, 2, 4).**

---

## Confidence and known gaps

These rules are not uniformly evidenced, and they are advisory only. Read them
with the following caveats in mind:

- **Motion is the least-evidenced section.** Rules 48–50 rest on essentially one
  fetched source (Carbon's skeleton-motion note) across six exemplars, not on
  convergent precedent the way rules 17–25 or 31–34 do. They cost nothing to
  adopt — alan ships zero motion today — but they should not be cited as
  well-precedented doctrine.
- **Rules are not tiered by confidence; some are more weakly precedented than
  others and are flagged inline where the source flagged them.** Rule 7 ("one
  primary action per screen") has no fetched supporting quote — it is
  convergence-by-absence across Carbon, Polaris, and Primer rather than an
  independently sourced finding (`prior_art_finder` row 8). Rule 29 ("semibold
  is the weight ceiling") is third-party sourced: Vercel's "no weight 700"
  claim comes from a Geist breakdown, not Geist's own docs, and the
  corroborating Linear weight band comes from a reverse-engineered dump of
  Linear's marketing site, not the product (`prior_art_finder` row 13/14,
  echoed in `design_system_comparator` R6). Treat both as directionally safe,
  weakly cited — not documented doctrine.
- **WCAG 2.1 AA contains no target-size criterion.** alan's 44px tap floor
  (rule 46) derives from Apple HIG's 44×44pt convention, not from WCAG. Under
  WCAG 2.1, target size (2.5.5) is an AAA criterion at 44px; the AA-level
  target-size criterion (2.5.8, 24px) was only introduced in WCAG 2.2. alan's
  44px floor exceeds both, but it is HIG-sourced, and every place in this doc
  that touches the tap floor states that provenance rather than crediting
  WCAG.
- **These rules are advisory and authoring-side only.** They are not wired
  into the `implementation` or `verification` team prompts, and they are not
  a review gate. They exist to ground planning-stage prompts and human
  judgement calls about whether a screen is good — not to fail an existing
  screen for non-conformance.

See `docs/UX_AUTHORING_EVIDENCE.md`'s own "Low-Confidence Findings" section for
the full per-source breakdown behind these caveats.
