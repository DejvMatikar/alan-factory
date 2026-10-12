# Design System — Source of Truth

## Purpose & status

This document is the human-readable, narrative half of the web UI's design
system. It records today's visual language as the baseline — colours,
spacing/tap-target sizing, typography conventions, and a WCAG 2.1 AA
accessibility baseline — so implicit conventions become explicit and
normative. It invents no new colours, spacing, or conventions; where
current usage is genuinely divergent across call sites (e.g. two badge
shapes, two tab-toggle visual treatments), that divergence is recorded
honestly as known debt with a named follow-up — never silently resolved by
picking a winner and implying the other call sites are wrong.

For the prescriptive, advisory question this doc doesn't answer — "is this
screen good?", not "does this screen conform?" — see
`docs/UX_AUTHORING_RULES.md`; this doc remains the descriptive, ratified
source of truth.

The numeric single sources of truth already exist and are not duplicated
here:

- **Colours + tap spacing** — the `@theme` block in `alan/web/src/index.css`.
- **Measurement numbers** (tap-target size, contrast ratios, smallest
  supported viewport width, safe-area guidance) — `alan/web/src/ui-spec.ts`
  (backed by `alan/web/src/ui-spec.json`).

This doc explains and points at those sources; it does not restate their
numbers as free-standing prose, except where an automated drift guard
protects the restatement (see "How it's enforced" for exactly which
sections that covers).

## Colour tokens

Source of truth: the `--color-*` custom properties in the `@theme` block of
`alan/web/src/index.css`.

| Token | Value | Semantic role |
|---|---|---|
| `--color-surface` | `#0f172a` | Page/app background |
| `--color-surface-elevated` | `#1e293b` | Cards, panels, raised surfaces |
| `--color-border` | `#334155` | Dividers, outlines, ghost-button borders |
| `--color-text-primary` | `#f1f5f9` | Primary text, headings |
| `--color-text-muted` | `#94a3b8` | Secondary/muted text |
| `--color-btn-create` | `#10b981` | Creation / positive actions (green) |
| `--color-btn-action` | `#0ea5e9` | Neutral/primary actions (blue) |
| `--color-btn-destroy` | `#ef4444` | Destructive actions (red) |
| `--color-btn-reset` | `#f59e0b` | Reset/caution actions (amber) |
| `--color-status-active-tint` | `oklch(62.3% 0.214 259.815)` | Workspace `active` background tint |
| `--color-status-active-fg` | `oklch(80.9% 0.105 251.813)` | Workspace `active` foreground |
| `--color-status-attention-tint` | `oklch(76.9% 0.188 70.08)` | Workspace `awaiting_input`, project `provisioning` background tint |
| `--color-status-attention-fg` | `oklch(87.9% 0.169 91.605)` | Workspace `awaiting_input`, project `provisioning` foreground |
| `--color-status-success-tint` | `oklch(72.3% 0.219 149.579)` | Workspace/project `completed` background tint |
| `--color-status-success-fg` | `oklch(87.1% 0.15 154.449)` | Workspace/project `completed` foreground |
| `--color-status-neutral-tint` | `oklch(55.2% 0.016 285.938)` | Workspace/project `archived`, unknown health severity background tint |
| `--color-status-neutral-fg` | `oklch(87.1% 0.006 286.286)` | Workspace/project `archived`, unknown health severity foreground |
| `--color-status-danger-tint` | `oklch(63.7% 0.237 25.331)` | Workspace `escalated`/`failed`, severity `critical` background tint |
| `--color-status-danger-fg` | `oklch(80.8% 0.114 19.571)` | Workspace `escalated`/`failed`, severity `critical` foreground |
| `--color-status-paused-tint` | `oklch(62.7% 0.265 303.9)` | Workspace `paused` background tint |
| `--color-status-paused-fg` | `oklch(82.7% 0.119 306.383)` | Workspace `paused` foreground |
| `--color-status-warning-tint` | `oklch(70.5% 0.213 47.604)` | Workspace `partial`, severity `high` background tint |
| `--color-status-warning-fg` | `oklch(83.7% 0.128 66.29)` | Workspace `partial`, severity `high` foreground |
| `--color-status-moderate-tint` | `oklch(79.5% 0.184 86.047)` | Severity `medium` background tint |
| `--color-status-moderate-fg` | `oklch(90.5% 0.182 98.111)` | Severity `medium` foreground |
| `--color-banner-error-tint` | `oklch(63.7% 0.237 25.331)` | Block-level error banner background tint (identical value to `--color-status-danger-tint`) |
| `--color-banner-error-fg` | `oklch(80.8% 0.114 19.571)` | Block-level error banner foreground (identical value to `--color-status-danger-fg`) |
| `--color-scope-repo-tint` | `oklch(70.4% 0.14 182.503)` | Repository-scope accent — card edge background tint (never a state fill) |
| `--color-scope-repo-fg` | `oklch(85.5% 0.138 181.07)` | Repository-scope accent — card edge/name foreground (never a state fill) |

React Flow's `--xy-*` variables (also declared in `index.css`) are **derived**
from the tokens above via `var(--color-*)` — they carry no independent
values, so there is nothing for them to drift from.

**Scope-marking rule (rework-repo-dashboard-chrome-and-tab-stability, revised
by user-chosen-repo-accent-palette-and-frame):** scope — global chrome (the
top bar) versus repository-scoped chrome (a repository's page) — is carried
primarily by *structure*, not colour: the repository's name is visible in
the card header (`features/home/components/RepoSection.tsx`'s `h1`, no longer `sr-only`),
repository-scoped controls stay inside the card's boundary, and global
controls stay in the top bar. The per-repository accent (below) reinforces
that structural read as a single accent, applied only as structure — never
as a filled state pill akin to the `--color-status-*` tokens below. **The
card itself no longer carries an edge of its own** — `border-l-2
border-scope-repo-tint` was the pre-accent treatment; today the accent edge
is painted once, as a frame around the whole repository page
(`shell/RepoShell.tsx`, see "Per-repository accent" below), and the card
keeps only the repository name's own text colour. `--color-scope-repo-tint`/
`-fg` remain in use, but only for the categorical `RepoBadge` chip on the
health page (`features/health/pages/HealthView.tsx`), not for this card. The token marks
*scope*, never *state*: it must not be reused to imply health, urgency, or
any other state meaning, and a repository-scoped control's state (health
count, CI colour) is still carried entirely by the existing
`--color-status-*` vocabulary, untouched by this accent.

**Scope note (W1.1):** the `--color-status-*` tokens name *meaning*, not
hue — same meaning maps to the same token everywhere it's used, but the
converse doesn't hold (`--color-status-attention-*` backs both the
workspace `awaiting_input` state and the project `provisioning` state, since
both mean "waiting on something" at the same amber value). This item
migrates exactly three surfaces onto these tokens: the workspace status
badge (`components/StatusBadge.tsx`), the project status badge (`Projects.tsx`'s
`ProjectStatusBadge`), and the health severity chip
(`HealthView.tsx`'s `SeverityBadge`) — plus the block-level error banner
recipe onto `--color-banner-error-*`. Left deliberately raw, staged for a
later item rather than migrated here: `HealthView.tsx`'s `CategoryBadge`
(categorical, not state-semantic) and `BlockerChips` (amber, not this
item's surface); `features/project/components/StatusBadge.tsx` (a different,
roadmap-delivery-status component); `HealthStatusBadge.tsx` and
`healthDisplay.ts`; `TerminalOutcomeBanner.tsx`; `StreamPanel.tsx`'s
`px-2 py-1` near-miss banner; hover states; and the other ~70 state-tint
sites across ~25 files this roadmap's next item rebuilds directly onto
these tokens. **`RepoBadge` is that later item, resolved by
rework-repo-dashboard-chrome-and-tab-stability:** its raw `bg-teal-500/15
text-teal-300` pair is now `bg-scope-repo-tint/15 text-scope-repo-fg` — the
promotion of that existing teal hue into the documented, categorical scope
token above, not a new invention. `EngineBadge`/`EngineOpsPanel`'s purple
remains raw and is explicitly **not** the scope colour: it is
value-identical to `--color-status-paused-*`, so reusing it as a second
scope accent would collide with an existing state meaning — exactly what
the scope-marking rule above forbids.

**Named exception — unblocker ownership widening
(surface-the-autonomous-unblocker-in-the-ui):** `--color-status-active-*`
and `--color-status-attention-*` are reused a second time each, beyond the
workspace-status meanings in the table above, to mark who currently owns a
parked workspace rather than the workspace's own status. `UnblockNotice.tsx`
(mounted on the workspace page) and the workspace-list host-owned badge
(`RepoWorkspaceTable.tsx`'s `HostOwnedParkBadge`) paint `active-*` while the
autonomous unblocker is engaged with a park — waiting-and-re-probing or
having just acted — and `attention-*` once the park needs a human, whether
because the host reported it or because it has gone silent past
`unblockView.ts`'s `SILENCE_THRESHOLD_MS` (90 minutes). This is a deliberate
widening, not a collision: both readings still mean the same thing the
scope note above ratifies (`active-*` = something is in motion, `attention-*`
= something is waiting on a person), just applied to unblocker ownership
instead of workspace status. `--color-status-neutral-*` was considered and
rejected for the host-owned badge, since neutral already means "no signal at
all" (`archived`, unknown severity) and a host actively working a park is
the opposite of that.

**Per-repository accent (repo-chrome-colour-semantics-and-accent, revised by
user-chosen-repo-accent-palette-and-frame, widened by
widen-accent-palette-glow-and-frame-spacing):** a connected repository's
accent is a user-chosen, server-persisted setting, not a pure hash —
`AccentChoice` (`alan.repo_accent`, mirrored as `AccentChoice` in
`alan/web/src/api.ts`) is
`{ color: AccentColor | null; intensity: "soft" | "standard" | "bold" }`,
read from `EngineConfig.accent` and written only through
`PUT /api/engines/{owner}/{repo}/accent`. Sixteen curated colours — the
original eight (iris, violet, plum, orchid, magenta, rose, ruby, crimson, all
in the violet-to-rose band, roughly 258°–342°) plus eight added to cover the
rest of the wheel (orange, amber, tan, green, teal, blue, slate, bone) — each
carry four roles — three border intensities and one intensity-invariant
text colour. The accent is edge and border only, never a filled state,
and the text colour is fixed across all three intensities. The hex
values live in `alan/web/src/lib/repoAccent.ts` — a distinct set from the
`--color-*` custom properties tabulated above, whose source is
`index.css`. `repoAccent.test.ts` pins two representative anchors
(`iris` and `crimson`, both standard intensity) byte for byte and
otherwise verifies derived properties — valid hex format, a distinct
edge hex per intensity, a distinct `-fg` hex for each of the sixteen
colours, and strictly increasing glow alpha from soft to standard to
bold — not a byte-for-byte pin of all sixty-four values. **Revised
hue-collision claim:** the original eight were
chosen to sit clear of every `--color-btn-*`/`--color-status-*`/
`--color-scope-repo-*` hue in use; that claim no longer holds once the
palette spans the full wheel, and is not restated as true. Instead, each of
the five new colours with a same-hue-family status counterpart (blue vs.
`--color-status-active-*`, amber vs. both `--color-status-attention-*` and
`--color-status-moderate-*`, orange vs. `--color-status-warning-*`, green vs.
`--color-status-success-*`, slate vs. `--color-status-neutral-*`) is held at
an OKLab distance of at least 0.075 from that status colour's `-fg` token —
verified for all six pairs by `repoAccentContrast.test.ts`'s "hue-collision
separation" suite, mechanically, not by eyeballing hue values. That distance
is bought with lightness and chroma, not hue alone — the new blue accent's
hue (243°) sits close to `--color-status-active-fg`'s (251.8°) on purpose,
relying on the accent's markedly lower OKLab lightness for separation. One
near-miss is named rather than gated: **teal** (198°) sits close to
`--color-scope-repo-tint`/`-fg` (181–182.5°). It is deliberately not added to
the gated pair table, because `--color-scope-repo-*` marks repository
*scope*, not health or urgency state — the collision rule this section
enforces is about state colours, which scope is not. What keeps every one of
the sixteen colours from reading as a health/urgency signal regardless of
hue proximity is the same structural constraint the boundary section above
states: an accent is painted only as a border edge, a glow on that edge, or
a repository name's text colour — it never becomes a filled background or a
pill, which is the shape every `--color-status-*` use case actually takes.
Two new colours needed a lightness constraint beyond the plain contrast
minimums: brown and gray, as saturated colours, cannot pass the large/normal
text contrast minimums against the dark surfaces at all without going
light — brown renders as the curated **tan**, gray as the curated **slate**;
neither a dark brown nor a mid gray ships. Slate and bone (the off-white
addition) carry one further constraint the other fourteen colours don't:
each `-fg` token is held at a contrast ratio of at least 1.35 against both
`--color-text-primary` and `--color-text-muted`, so a repository name painted
in either colour stays visually distinguishable from ordinary primary or
muted text rather than reading as unstyled text with no accent applied at
all — verified by the same test file's "near-neutral accent text separation"
suite. This 1.35 floor is not asked of the other fourteen colours' `-fg`
tokens (several sit below it against `--color-text-muted`, unchanged from
before this item), because a saturated hue is already visually distinct from
neutral text without a luminance floor — only a near-neutral colour risks
being mistaken for plain text. `alan/web/src/lib/repoAccent.ts`'s `resolveAccents(engines)` is the pure
resolution function every consumer calls: a repository with an explicit
`accent.color` uses it as-is; a repository with an explicit `accent.color:
null` resolves to `null` (no accent); a repository with no explicit choice
(`accent` absent or `null`) is assigned automatically, deterministically
preferring a hash of its `owner/repo` key but walking to the next free colour
whenever that preferred colour is already taken by another connected
repository's resolved colour — so two repositories no longer collide by
default as long as any of the sixteen colours remains free. `accentVars(
resolved)` returns literal `--accent-edge`/`--accent-fg`/`--accent-glow` hex
strings for a resolved, non-null accent (never a further
`var()`/`color-mix()`/alpha reference — Chromium fails to resolve that
double indirection on first paint) or an empty style object for `null`;
`accentEdgeClass(resolved)`/`accentTextClass(resolved)` return the shared
`ACCENT_EDGE_CLASS` (`border-[var(--accent-edge)]`)/`ACCENT_TEXT_CLASS`
(`text-[var(--accent-fg)]`) classes for a resolved accent, or swap to the
existing neutral `border-border`/`text-text-primary` tokens for an explicit
no-accent choice — never a new neutral hex. Every consumer performs the same
`resolveAccents(engines).get(key)` lookup and supplies the result via
`style={accentVars(resolved)}` on whichever ancestor element carries the
colour, relying on ordinary CSS custom-property inheritance rather than a
per-colour class table. Intensity governs the border treatment only — the
`-fg` token is fixed per colour across all three intensities, so the
repository name's text colour never changes when the intensity dial moves.
The accent is painted as a page **frame**, not repository-card decoration:
`shell/RepoShell.tsx` hoists `style={accentVars(resolved)}` onto its rail
slot (`border-l-2`, the outer edge of the conversation list) and onto the
`<Outlet/>` wrapper (`border-r-2`, the outer edge of the content column) —
two parallel lines that persist across every nested route, including when
the rail is collapsed to `CollapsedRailIndicator` (the border lives on the
slot, not on the rail content) and, below the `sm` breakpoint where the rail
slot is hidden outright, the content-column line alone carries the accent.
Both frame-line slots additionally carry inset padding — four pads total
(`ACCENT_FRAME_PAD_LEFT_CLASS`/`ACCENT_FRAME_PAD_RIGHT_CLASS`/
`ACCENT_FRAME_PAD_TOP_CLASS`/`ACCENT_FRAME_PAD_BOTTOM_CLASS` in
`repoAccent.ts`, all four unit-asserted equal to each other) so the border
line no longer sits flush against the content it frames — the padding lives
on the same bordered slot element as the line itself, at every breakpoint
the line is visible in, including collapsed-rail and rail-hidden states,
since it never depends on which child currently renders inside the slot.
The rail slot carries left + top + right pad (`ACCENT_FRAME_PAD_LEFT_CLASS
ACCENT_FRAME_PAD_RIGHT_CLASS ACCENT_FRAME_PAD_TOP_CLASS`) so the
conversation list is inset from its column's right edge by the same amount
as its left edge, rather than sitting flush against it; the content slot
carries left + top + right + bottom (`ACCENT_FRAME_PAD_LEFT_CLASS
ACCENT_FRAME_PAD_TOP_CLASS ACCENT_FRAME_PAD_RIGHT_CLASS
ACCENT_FRAME_PAD_BOTTOM_CLASS`) so its heading, tab row, transcript, and
composer all share the same left inset the rail already had instead of
sitting flush against the column's own left edge, and so a long
conversation's composer gets breathing room below it instead of landing
flush against the end of the page frame. The rail's own height budget
(`shell/ConversationRail.tsx`'s `RAIL_MAX_H_CLASS`,
`max-h-[calc(100dvh-7rem)]`, sourced from `shell/stickyOffsets.ts`) is
derived from three named terms, not two: the top bar's own height (4rem)
**plus `main`'s vertical padding** (`App.tsx`'s `Shell` renders `<main
className="... py-8">`, 2rem) gives the rail slot's true resting position
(6rem, `STICKY_SLOT_TOP_CLASS`, `sm:top-24`) — this is what `RepoShell.tsx`'s
rail slot now pins to, replacing the previous `sm:top-16` (4rem), which
omitted `main`'s padding and caused the rail to drift upward by 2rem before
catching instead of pinning from the first pixel of scroll. Adding one frame
pad unit (1rem) on top of that resting position gives the height budget
(7rem) the rail's max-height subtracts, since that pad is consumed inside
the slot's own border box and would otherwise push the rail's collapse
control below the viewport.

**Sticky page regions (flatten-shell-chrome-and-per-row-actions):**
`shell/stickyOffsets.ts` is the single source of truth for every pinned
region on a repository page — the numeric terms above
(`TOP_BAR_REM`/`MAIN_PAD_TOP_REM`/`FRAME_PAD_REM`), the two derived offset
classes (`STICKY_SLOT_TOP_CLASS`, `sm:top-24`, consumed by the rail slot
itself; `STICKY_FRAME_CHILD_TOP_CLASS`, `sm:top-28`, folded into the shared
`STICKY_REGION_CLASS`), the rail's `RAIL_MAX_H_CLASS`, and the composed
`STICKY_REGION_CLASS` (`sm:sticky sm:top-28 bg-surface sm:z-20`) that two
regions share verbatim rather than each hand-rolling its own sticky
recipe: the conversations rail's heading plus its Build/Explore controls
(pinned by the rail slot's own stickiness in `RepoShell.tsx`, not this
class), and the repository dashboard's header row
(`features/home/components/RepoSection.tsx`'s `RepoHeader` plus `RepoActionRow`). The
Build/Explore chat page's title/cost-line/tab row (`components/
ChatScaffold.tsx`) no longer uses this class — see "Two chat scroll modes"
below. Every
sticky class is `sm:`-prefixed, matching the top bar's own `sm:sticky`, so
nothing pins below the `sm` breakpoint the mobile-audit ratchet measures at.
Each region pins from the first pixel of scroll, with no initial drift,
because its offset is derived from the same three terms that already
describe its true resting position in the page's normal flow — the bug the
old `sm:top-16` rail offset had (drifting before catching) is what forced
these terms to be named explicitly rather than guessed. Layering follows a
four-level ladder, each rung its own named constant in
`shell/stickyOffsets.ts`: the workspace table's sticky column headers
(`TABLE_HEADER_Z_CLASS`, `sm:z-10`) sit below the sticky page region itself
(`STICKY_REGION_Z_CLASS`, `sm:z-20`, folded into `STICKY_REGION_CLASS`),
which sits below a hover/focus-revealed description (`REVEAL_Z_CLASS`,
`z-30`), which sits below the global top bar (`TOP_BAR_Z_CLASS`, `sm:z-40`,
`App.tsx`'s `Shell` header). The sticky region is deliberately raised above
the table header rather than left at its prior `sm:z-0`: an explicit
`z-index` on a sticky element establishes its own stacking context, which
clamps any reveal nested inside it to that context's own stacking order —
so a tooltip revealed from inside a region sitting below the table header
could never climb above it no matter how high the reveal's own z-index
went, unless the region itself first cleared the header. This is what makes
the Active/Archived tab tooltips, which live inside the sticky region,
render above the workspace table's sticky column headers instead of behind
them. `shell/stickyOffsets.test.ts` asserts the full ladder mechanically —
both the numeric ordering of the four constants and that each consumer
module (`App.tsx`, `components/HoverReveal.tsx`,
`features/home/components/RepoWorkspaceTable.tsx`) references its constant by name rather
than a raw literal — instead of trusting the ordering by inspection.

**Two chat scroll modes, and why the dashboard's own viewport-box mode is
untouched (calm-the-chat-reading-experience):** `shell/stickyOffsets.ts`
now carries two mutually exclusive pairs of scroll-column/scroll-body
constants rather than one. `SCROLL_COLUMN_CLASS`/`SCROLL_BODY_SIZE_CLASS`/
`SCROLL_BODY_CLASS` are the pre-existing viewport-box constants — a bounded
`max-h-[calc(100dvh-7rem)]` column with its own internal
`sm:overflow-y-auto` scroll body — and remain byte-identical, still
consumed only by the repository dashboard's workspace table
(`features/home/components/RepoWorkspaceTable.tsx`) and its own pinned header. The new
`SCROLL_COLUMN_FLOW_CLASS` (`sm:flex sm:flex-col`) and
`SCROLL_BODY_FLOW_CLASS` (the empty string) carry no `max-h`, `overflow-y`,
or `min-h-0` token at all — `shell/stickyOffsets.test.ts` asserts this
mechanically, both as a property of the two flow constants and as the pin
that keeps the three viewport-box constants from drifting. `ChatScaffold.tsx`
and `ChatPanel.tsx` (the Build/Explore chat surfaces) use the flow pair: the
tab panel and the transcript wrapper carry no scroll-region classes of their
own, so the chat renders at its natural length and the page itself scrolls,
exactly as it already does below the `sm` breakpoint — this item just makes
that the desktop behaviour too, rather than a mobile-only fallback. The
Chat and Summary tabs now share one class (`SCROLL_BODY_FLOW_CLASS`) instead
of a chat/summary ternary, since neither tab owns internal scroll chrome
any more; the tab panel's `tabIndex={0}` — added only to satisfy axe's
`scrollable-region-focusable` rule for the region that used to scroll
internally — is dropped along with it, since a region that doesn't scroll
needs no such stop. **Revised by calm-the-chat-reading-experience: the chat page pins nothing
at any width.** `ChatScaffold.tsx`'s title/cost-line/tab-row wrapper no
longer applies `STICKY_REGION_CLASS` at all — it carries only its own
layout classes (`space-y-2`). The page title, the conversation/collision
cost cards, and the Chat/Summary tab row now scroll away with the
transcript exactly like everything else on the page, at every breakpoint,
not only below `sm`. Reaching the metrics or switching tabs on a long
conversation requires scrolling back up first — an explicitly accepted
trade-off, not a regression: the two conflicting scroll models (a
page-length transcript underneath a pinned header block) were clipping
messages under the opaque bar, and unpinning is what makes the chat page a
single, calmly scrolling surface instead of two. `STICKY_REGION_CLASS`
itself is untouched and remains live for its other two consumers (the rail
heading and the dashboard header row, see "Sticky page regions" above) —
only this one consumer stopped applying it.

**Jump to latest (calm-the-chat-reading-experience):** while a chat turn is
running, the page does not move on its own unless the reader is already
parked at the bottom (`useStickToBottom.ts`, `src/hooks/useAutoScroll.ts`'s
sibling for the two page-flow chat surfaces). When new content arrives while the reader has scrolled up,
`ChatPanel.tsx` renders a `Button` `variant="ghost"` labelled "Jump to
latest" (`data-testid="chat-jump-to-latest"`), and hides it again once the
reader is back at the bottom. This is the app's first non-dialog `fixed`
control, so its placement is a named composed constant rather than raw
literals on the component: `shell/stickyOffsets.ts`'s
`JUMP_TO_LATEST_CLASS` is `fixed`, anchored bottom-centre of the viewport
(`bottom-4 left-1/2 -translate-x-1/2`, not the document), painted on the
opaque `bg-surface-elevated` surface (AA contrast, since it overlays
message content) with a `border-border` edge, padded for the safe area
(`pb-[env(safe-area-inset-bottom)]`), and placed on the `REVEAL_Z_CLASS`
rung — below the global top bar (`TOP_BAR_Z_CLASS`) only from the `sm`
breakpoint up, since below `sm` the header is `position: static` and
scrolls away entirely, leaving no overlap to arbitrate. It reuses the
existing `ghost` button recipe rather than inventing a new one, mounts and
unmounts with the `!pinned` condition (never merely hidden, so it is out of
the tab order while not shown and keyboard-reachable while shown, per the
existing conditional-mount pattern this doc already ratifies elsewhere),
and steals no focus and shifts no layout on either transition since it is
`fixed`, not part of document flow.

**Billing chip row (calm-the-chat-reading-experience):** `RunPricing.tsx`'s
per-run tool-usage chip row (`run-summary-tools`) now reuses the same
`lg:`-scoped nowrap-and-scroll recipe `features/health/components/RepoHealthTable.tsx`
already established for its own horizontally-scrolling table
(`tabIndex={0}` + `aria-label` on the scroll container, an
`eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex` comment
justifying it) — the row wraps by default (`flex flex-wrap`) and switches to
a single non-wrapping, horizontally-scrolling line only from the `lg`
breakpoint up (`lg:flex-nowrap lg:overflow-x-auto`), rendered as a
chrome-reset `<fieldset>` (`m-0 border-0 p-0`, the same reset
`features/home/components/RepoStatusStrip.tsx` and `features/connect/pages/ConnectStep2Configure.tsx`
already use for a non-form `<fieldset>`) rather than a plain `<div>`, purely
because a `<fieldset>` was already this doc's precedent for a grouped,
labelled control cluster. This exists to keep the billing summary's own
height stable during a run: a chip row that never wraps past its container
cannot trip the desktop-audit ratchet's action-group line-fit measurement,
which is what let this run without needing a baseline change.

**Frame glow (widen-accent-palette-glow-and-frame-spacing):** both frame
lines additionally carry a soft, accent-coloured glow — this app's first and
still only authored `box-shadow` (a deliberate, singular exception, not a
precedent for future shadow use). `accentVars(resolved)` emits a third
variable, `--accent-glow`, an `#RRGGBBAA` string from
`accentGlowHex(color, intensity)` — the same edge hex `accentEdgeHex`
already resolves, with a per-intensity alpha suffix appended (soft `0x66`,
standard `0x99`, bold `0xcc`; the floor is pinned as
`ACCENT_GLOW_MIN_ALPHA = 0x66` and asserted in both the unit and E2E
suites) — never a `color-mix()` composition, for the same first-paint
double-indirection reason `--accent-edge`/`--accent-fg` already avoid it.
`ACCENT_GLOW_LEFT_CLASS`/`ACCENT_GLOW_RIGHT_CLASS` are two literal Tailwind
arbitrary-value one-sided `box-shadow` classes —
`shadow-[-6px_0_18px_var(--accent-glow)]` on the rail slot's left edge and
`shadow-[6px_0_18px_var(--accent-glow)]` on the content slot's right edge —
each offsetting the shadow only along the axis the line already runs, away
from the two-column layout's centre, so the glow bleeds outward past the
line rather than back over the content it frames. A one-sided directional
`box-shadow` was chosen over a border-weight increase (which cannot express
a soft, spreading bleed) or `filter: drop-shadow()` (which shadows an
element's rendered alpha shape, not a specific edge, and is markedly more
expensive to composite for a persistent, always-mounted page frame).
`accentGlowClass(resolved, side)` returns the empty string for an explicit
no-accent resolution, so a repository with no accent keeps a neutral,
glow-free line, matching `accentEdgeClass`'s existing null handling.
`features/home/components/RepoSection.tsx`'s dashboard card carries no edge of its own —
only the repository name's own text colour
(`accentTextClass(resolved)`). The accent additionally paints the repository
name in the top bar (`TopBarRepoSwitcher.tsx`, selected entries only —
unselected entries keep the muted `text-text-muted` treatment) and the
selected entry in `shell/RepoDrawer.tsx`'s repository list; both reuse the
selection-edge convention below rather than the page-frame convention.
**Stated assumption:** `features/home/components/RepoSection.tsx` drops its own edge
unconditionally, with no `frameOwnsAccent` flag, because its only production
caller (`features/home/pages/Repo.tsx`, via `RepoPageBody`) already renders inside
`RepoShell`'s frame, and `features/home/pages/Home.tsx` is a pure redirect with no
multi-repository listing route today — a flag would ship an unreachable
production branch. Should a listing route be added outside the frame,
`RepoSection`'s leading-edge treatment is the reinstatement point, not a new
mechanism. It never appears as a filled/background state indicator — only as
a border edge or as text. **Flat card, no panel chrome
(flatten-shell-chrome-and-per-row-actions):** the dashboard card
(`features/home/components/RepoSection.tsx`'s `<section>`) no longer sits on its own
rounded, tinted panel either — the prior `rounded-lg bg-surface-elevated/30
p-3` is gone, leaving only the layout utility `space-y-2`. The section
renders flat on the shared page background, the same way the Build and
Explore chat surfaces (`features/chat/components/ChatScaffold.tsx`'s root) already do,
with no nested second border and no panel padding of its own; the accent
frame around the whole repository page (above) remains the only border a
repository page shows.
`repoAccentContrast.test.ts` mechanically verifies every `-fg` token against
both `--color-surface` and `--color-surface-elevated` at
`contrastNormalTextMinRatio`, and every `-soft`/`-standard`/`-bold` edge
token at `contrastLargeTextMinRatio` (`ui-spec.ts`), plus (added by
widen-accent-palette-glow-and-frame-spacing) the slate/bone text-separation
floor and the six-pair OKLab hue-collision table described above, so
"pre-verified for accessible contrast" is a falsifiable, CI-gated claim
rather than a design intention. `repoAccentDrift.test.ts` separately asserts
the sixteen-name colour set is identical across `alan/repo_accent.py` and
`api.ts`, that every name matches `^[a-z]+$`, and that `lavender` never
re-enters the palette (it remains the
`test_set_accent_unknown_color_returns_422` negative fixture).

**Removals and width (repo-chrome-colour-semantics-and-accent):** the
cross-repository "Recent conversations" panel (`pages/ResumeDraftsPanel.tsx`,
formerly rendered in the `home-resume-drafts-slot` on the global Inbox page —
itself later deleted by remove-inbox-from-web-ui, which re-homed the
needs-you slot onto each repository's own dashboard) is deleted outright —
each repository's own conversation rail
(`shell/ConversationRail.tsx`) already covers it, and a fleet-wide list of
drafts across every connected repository duplicated that without adding
anything. `components/ChatDraftRows.tsx`'s `DraftRowItem`/`useDraftRows`
survive unchanged — `features/project/pages/Project.tsx`'s own conversations list still
consumes them. `shell/RepoShell.tsx`'s conversation-rail column widened
`w-72` → `w-96`, and the app shell's overall content clamp widened
`max-w-7xl` → `max-w-[96rem]` (`App.tsx`'s header and `<main>`) so the wider
rail doesn't shrink the detail region beside it.

## Spacing & tap targets

The tap-target minimum is defined by the `--spacing-tap-target` token in
`index.css`'s `@theme` block. Its authoritative value is `tapTargetMinPx` in
`alan/web/src/ui-spec.ts` (WCAG 2.5.5 / Apple HIG) — see that module for the
number; it is not restated here (`alan/web/src/ui-spec.drift.test.ts` asserts
the CSS token equals `tapTargetMinPx`, so the two can never silently
diverge).

**Deliberate exception — controls on graph-canvas node cards.** A control
placed inside a graph-canvas node card (`NodeCardIcons.tsx`'s step-card
Output/Prompt/Config icons and `InvocationGraph.tsx`'s run-node "Open full
view" link) uses `Button density="compact"`: a 24px minimum target
(`min-h-6 min-w-6`, WCAG 2.5.8 AA) instead of the 44px default. The card's
fixed 220px width cannot hold three 44px controls beside the step name and
its status badge, so this is an intentional exception to the density rules,
not a precedent. `density="compact"` is used nowhere else, and the 44px
default is unchanged for every other control.

## Typography

Ratified type scale: the app uses exactly six Tailwind `text-*` steps for
standard UI text, and no others. Each step's canonical role below is
derived from a full scan of non-test `src/**/*.{ts,tsx}` on current main:

| Step | Class | Canonical role |
|---|---|---|
| xs | `text-xs` | Dense metadata & micro-copy — status pills/badges, timestamps, field labels, compact buttons. The most-used step by a wide margin. |
| sm | `text-sm` | Default running text — body copy, form inputs, most button labels. Second most-used; `xs` + `sm` together cover the large majority of call sites. |
| base | `text-base` | Sub-heading weight — modal/panel section titles (`h1`–`h3` inside dialogs and cards, e.g. `Projects.tsx`'s "New project" heading, `ConsultantModal.tsx`'s "Choose a consultant"). |
| lg | `text-lg` | Brand wordmark — `App.tsx`'s "alan" header logo; also one rung of `MarkdownBody.tsx`'s rendered-Markdown heading ladder. |
| xl | `text-xl` | Page-title heading, owned by each screen's own content — `App.tsx`'s `Shell` renders no page-title heading in its chrome at any route. `Projects.tsx` renders it as the page's `h1`; `Project.tsx` (detail) and `HealthView.tsx` render theirs as `h2` alongside other page content, not as the page's sole top-level heading. On repository screens (every route nested under `shell/RepoShell.tsx`, including the Home dashboard) the sole `h1` is `features/home/components/RepoSection.tsx`'s heading on the dashboard. **Revised by redesign-workspace-detail-page-layout:** the workspace detail page (`/workspaces/:taskId`) gained its own sole `h1`, `features/workspace/components/WorkspaceHeader.tsx`'s title — the page previously had no `h1` at all, only an unlabelled `h2`. |
| 2xl | `text-2xl` | Large stat/metric number display — `TriageSweepCard.tsx`'s headline figures only. |

Source of truth: there is no bespoke `font-size` scale in `index.css`'s
`@theme` block — the table above IS the Tailwind default scale, narrowed to
the six steps the app actually reaches for. `design-system.drift.test.ts`
scans every non-test `.ts`/`.tsx` file under `src/` and asserts the set of
`text-*` classes in use is exactly this six-step set: a step used in code
but missing from this table, or a documented step no longer used anywhere,
fails the guard.

**Deliberate exception, not part of the scale:** DAG/graph-canvas node and
edge micro-labels (`WorkflowDAG.tsx`, `InvocationGraph.tsx`,
`NodeDetailModal.tsx`, `HandoverDetailPanel.tsx`, `handoverSections.tsx`,
`InvocationCard.tsx`, `StreamPanel.tsx`, `RunPricing.tsx`) use arbitrary
sub-`xs` pixel values (`text-[10px]`, `text-[11px]`) for text rendered
inside constrained graph-node cards — a distinct density regime from
standard UI text. This mirrors the boundary the 2026-07-15 front-door
review itself excluded ("the Workspace detail page, `WorkflowView`, and the
DAG/invocation execution internals"). It is recorded here for honesty, not
ratified as a seventh scale step, and the drift guard does not police it.

## Interaction states

- **Disabled** — every disabled interactive control in the app shares the
  identical pair `disabled:opacity-50 disabled:cursor-not-allowed`.
  `Button.tsx`'s `BASE` constant is one of 28 non-test call sites of
  `disabled:opacity-50` under `src/**/*.{ts,tsx}`; no other opacity value is
  used anywhere in the codebase. Ratified as the canonical disabled
  treatment.
- **Focus ring** — every authored focus indicator uses the identical ring
  value `ring-2 ring-text-primary/60`; only the pseudo-class prefix varies,
  by element type:
  - `focus-visible:` on native interactive controls, where the app wants to
    distinguish keyboard focus from pointer focus (`Button.tsx`'s `BASE`,
    so every `Button` variant inherits it).
  - `focus:` on elements that receive a *programmatic* focus the app wants
    visible regardless of input modality (`ChatComposer.tsx`'s autofocused
    textarea). The composer's autofocus is still programmatic and
    modality-independent, but it is taken only when nothing else currently
    holds focus — so returning to the Chat tab by click or arrow key does not
    re-take focus from the tab control.
  - `focus-within:` on a wrapping `<label>` whose focusable child is a
    native checkbox, so the ring highlights the whole tappable row
    (`SettingsModal.tsx`'s row-selection checkboxes).

  All three are ratified; new call sites should reuse `ring-2
  ring-text-primary/60` and pick the prefix matching one of these three
  shapes rather than inventing a fourth.
- **Hover** — stays convention-shape prose, not a token. Hover treatments
  follow "darken/lighten the element's own colour or surface"
  (`hover:bg-btn-action/90` and its sibling variants on `Button`,
  `hover:bg-surface-elevated` on nav-link chrome) rather than one recurring
  numeric value — there is no single hover value to ratify as a token the
  way the focus ring's exact `ring-2 ring-text-primary/60` is. **The app
  header (`Shell`'s `<header>` in `App.tsx`) sits on `bg-surface`, not
  `bg-surface-elevated`.** Every top-bar control already hovers to
  `hover:bg-surface-elevated` — while the header itself sat on that same
  `bg-surface-elevated` tone, that hover was a no-op with no visible change
  at all. The header surface is the one token that moved; the hover value
  on its controls did not. **The top bar carries no bottom border
  (flatten-shell-chrome-and-per-row-actions):** the header's own `bg-surface`
  already matches the page background beneath it, so the `border-b
  border-border` rule that used to separate them is gone — the two now read
  as one continuous surface rather than two stacked panels.
- **Step card (graph canvas)** — `WorkflowDAG.tsx`'s `StepNodeCard` signals
  that it is clickable while idle, not only on hover: a bordered card, a
  trailing chevron (`step-card-chevron`), `cursor-pointer` on the main
  `<button>` (accessible name "Show activity of {step}"), and a hover border
  brightening (`hover:border-text-muted`). Keyboard focus draws the standard
  `ring-2 ring-text-primary/60` (`SELECTION_FOCUS_CLASS`) on that main
  button. Selection is a different cue — a left-edge `border-l-4
  border-text-primary` plus `aria-current="true"`, never a ring — so
  keyboard focus and selection stay visually distinct. There are no
  hover-only tooltips on cards: no `title` attribute, and a failed step's
  full error lives in the selection panel (`StreamPanel.tsx`'s
  `NodeErrorFooter`), which scrolls into view on select through
  `useAutoScroll(…, "nearest")`; the card keeps only a two-line clamped
  preview of it.

## Accessibility baseline (WCAG 2.1 AA)

All figures below are owned by `alan/web/src/ui-spec.ts` (backed by
`alan/web/src/ui-spec.json`) — see that module for the exact numbers, which
are not restated here:

- Contrast ratios (`contrastNormalTextMinRatio`, `contrastLargeTextMinRatio`).
- Minimum tap target size (`tapTargetMinPx`).
- Smallest supported viewport width (`smallestSupportedViewportWidthPx`).
- Safe-area guidance (`safeAreaAdvisory`, `safeAreaGuidance`) — advisory only,
  not mechanically verifiable in a headless browser.

## Component conventions

This section records load-bearing, shared conventions and primitives that
teams are expected to reuse — not an exhaustive component inventory (which
would go stale immediately and has no automated guard).

- **`Button.tsx`** — the shared button primitive. All button colour variants
  (`create`, `action`, `destroy`, `reset`, `ghost`) are driven by the
  `--color-btn-*` tokens above; new call sites should use a `Button` variant
  rather than hand-rolling colour classes. The button-element branch takes an
  optional `busy` prop (`workspace-header-actions-on-shared-controls`): while
  `true` it sets `aria-disabled` and `aria-busy`, appends
  `aria-disabled:opacity-50 aria-disabled:cursor-not-allowed` to the class
  string, and drops `onClick` to a no-op — the control stays natively
  focusable throughout, unlike the native `disabled` attribute every other
  call site still uses. `busy` is omitted (not `false`) on every call site
  that doesn't pass it, so its class string stays byte-identical to before
  the prop existed.
- **Interactive/edit-in-place data table chrome** — the connect wizard's step
  2 diff table (`EngineConfigProposalDiffTable.tsx`) is this app's first data
  table where every body cell is a live editable control rather than static
  text. It matches the existing static-table chrome exactly —
  `w-full text-left text-sm` on the `<table>`, `border-b border-border text-xs
  uppercase text-text-muted` on the header row, `scope="col"` on column
  headers and `scope="row"` on the row header — the same recipe
  `RoleConfigTable.tsx` and Home's `RepoSection.tsx` already use, rather than
  inventing a third table convention for the "editable" case. No new
  `--color-*` token was minted for this — every hue used (border, muted text,
  banner-error tint/fg for the 422 fallback) already exists above.
- **Shared chat primitives** — `ChatComposer.tsx` (send/stop input group),
  `ChatScaffold.tsx` (tab-based chat page layout), and `EditableBrief.tsx`
  (editable summary/brief textarea) are the reusable building blocks for
  every chat-style surface (build-chat, explore-chat, workspace chat). New
  chat surfaces should compose these rather than re-implementing chat layout.
- **`alan/web/src/ui/`** — the shared UI primitives location, holding
  `DialogFrame.tsx` (the dialog frame primitive: owns the `<dialog>` element,
  the frame `className`, the required `ariaLabel`, `busy`-vs-idle dismissal,
  and native-cancel handling), `useModalLifecycle.ts` (the single modal
  open/close/focus-restoration lifecycle, moved here from `components/`), and
  `src/ui/Input.tsx` (the field-chrome primitive: the single owner of the repeated
  `rounded-md border border-border bg-surface px-2 py-1.5 text-sm
  text-text-primary` class string, rendered as an `<input>`, `<textarea>`, or
  `<select>` via a required `as` prop). Variation is expressed through
  properties, not stored alternative strings: `tapTarget` (adds `min-h-11`,
  default off — a caller must opt in, so no site gains or loses tap-target
  sizing silently), `focusRing`, `disabledStyle` (default on), and
  `placeholderStyle`, plus a `className` layout prefix prepended before the
  chrome. `placeholderStyle` is a compile-time error on the `select` variant,
  which instead takes `children` (its `<option>`s) — a combination that makes
  no sense for a `<select>` is unreachable, not silently ignored.
  Eight dialogs (`ConfirmDestructiveDialog.tsx`, `NewProjectModal.tsx`,
  `ImportRoadmapModal.tsx`, `PostureConfirmModal.tsx`,
  `ProposeRoadmapChangesModal.tsx`, two
  in `features/project/pages/Project.tsx`, and
  `features/workspace/components/DeleteWorkspaceDialog.tsx`) render through
  `DialogFrame`; one more (`NodeDetailModal.tsx`) keeps its own distinct
  frame shape and adopts `useModalLifecycle` only. New
  primitives that generalise across more than one existing component belong
  here, not in `components/`. `src/ui/ErrorBanner.tsx` is the block-level
  error banner primitive: the single owner of the repeated `rounded-md
  bg-banner-error-tint/15 px-3 py-2 text-xs text-banner-error-fg` class
  string, taking `children`, an optional trailing `action` slot (present
  whenever `action !== undefined`, wrapping `children` in a `<span>` and
  adding flex-row chrome so the action sits opposite the message), and a
  `data-testid`, with no `className` escape hatch.
  `src/ui/SectionHeading.tsx` is the small uppercase section-heading
  primitive: the single owner of the repeated `text-xs font-semibold
  uppercase tracking-wide text-text-muted` class string, taking `children`, a required
  `level` (`h2`–`h5`, since the sites it replaces span four heading levels
  and the document outline is an accessibility concern, not a cosmetic one),
  an optional `truncate` option (`min-w-0 truncate`), and a `data-testid`,
  again with no `className` escape hatch.
  `src/ui/useAction.ts` is the shared mutation busy-lifecycle hook:
  `useAction(clearOn?)` returns `{ busy, run, begin, end }`, holding `busy`
  in a reducer rather than a literal `useState` boolean setter (a setter
  there would itself be an instance of the shape this hook exists to
  remove). `run(thunk)` is the normal path — it flags busy, awaits the
  thunk, and returns the thunk's settled promise unchanged so a call site
  still attaches its own two-arg `.then(onSuccess, onError)`. `clearOn` is
  the hook's one timing property: `"settle"` (the default) clears busy on
  both resolve and reject; `"rejection"` clears only on reject, leaving
  busy true after a resolve for sites whose success path removes or
  navigates away from the control instead of re-enabling it. `begin()`/
  `end()` are the escape hatch for a site that must decide for itself
  whether a given settle should clear busy — e.g. a stale-request guard
  that must leave busy true when a response arrives for a superseded
  request. The hook deliberately does not own disablement: call sites keep
  applying a real `disabled` attribute, `aria-disabled`, or whatever
  mechanism they used before adopting the hook, exactly as they did before.
- **Modal-key convention** — `components/modalKeys.ts`'s `handleModalKeyDown`
  is the shared keyboard-handling convention for the non-frame dialog family
  (`HiddenRepoDialog.tsx`, `ListChooserModal.tsx`, `PresetSelectDialog.tsx`,
  `SettingsModal.tsx`): Escape closes the modal, Ctrl+Enter submits it.
  `handleModalKeyDown`'s Ctrl+Enter branch is matched through
  `lib/shortcuts.ts`'s `modal-confirm` row
  (`workspace-header-actions-on-shared-controls`) rather than a literal
  `e.ctrlKey && e.key === "Enter"` check, so the modal convention's bound key
  and any advertised hint for it share the one shortcuts table instead of
  drifting apart.
  `ListChooserModal.tsx` also takes an optional `notice` slot: when supplied,
  it renders in place of the item list (an `ErrorBanner` for a failed load,
  or any other block), so a chooser degrades to a single failure message
  instead of a silently empty list. New
  modals in that family should wire their container's `onKeyDown` through
  this helper rather than re-implementing the key handling ad hoc. The nine
  `DialogFrame` adopters above get the same Escape/Ctrl+Enter behaviour, plus
  busy-vs-idle dismissal and native-cancel handling, from `src/ui/
  DialogFrame.tsx` instead — `handleModalKeyDown` is what `DialogFrame`
  itself is built on, not a second implementation of it.
  **Deliberate exception: non-modal proposal surfaces.**
  `features/chat/components/ProposalPanel.tsx` (the Build/Explore Summary-tab inline
  dispatch-proposal panel, see `docs/architecture/build_chat.md`'s
  "Frontend" section) does NOT wire `handleModalKeyDown` and has no Ctrl+Enter
  affordance — on the Summary tab the focused textarea is the brief editor
  itself, so binding Ctrl+Enter to Dispatch would let a keystroke typed
  inside that text field commit a workspace. This is a considered exception
  to the convention above, not an oversight; a future non-modal surface
  reaching for a submit shortcut should make the same call, not silently
  wire the modal convention onto a text-editing surface.
  `ProposalPanel`'s **stale** state (the live brief has diverged from the
  brief the settled proposal was proposed against) dims the panel's own
  chrome and its primary button to the existing `opacity-70` control idiom
  (see "Direct repo action row" below) — the proposal's own prose stays at
  full contrast, since dimming body text below `opacity-70` risks dropping
  under the WCAG-AA contrast minimum this document requires. The
  programmatic signal a screen reader gets is a rewritten sentence inside
  the panel's persistent `<output>` status region ("Brief changed since this
  proposal…"), not a colour change — don't reach for a new colour token to
  represent staleness; the existing dimmed-chrome-plus-live-region pair is
  the pattern.
- **One-click "Summarize & Dispatch" — the `create`/`action` button pairing
  and the auto-run status channel (add-one-click-summarize-and-dispatch)** —
  both conversational dispatch doors (build and explore) gained a "Summarize & Dispatch" `Button`
  `variant="create"` immediately beside the door's existing primary trigger — `Summarize`
  (`variant="action"`) on the composer's right action group
  (`features/chat/components/ChatComposer.tsx`'s `RightActions`). Pairing a `create`-variant
  control beside an `action`-variant one (rather than two `action` buttons
  side by side) is deliberate: the differing fill colour is what keeps the
  one-click shortcut visually distinguishable from the manual step it
  collapses, rather than reading as an arbitrary second choice with no
  hierarchy. The run's own progress/stop reporting reuses
  `ProposalPanel`'s persistent-live-region idiom (rewritten in place, never
  mounted/unmounted) via the shared `features/chat/components/AutoDispatchStatus.tsx` —
  with one deliberate divergence: it renders a `<div aria-live="polite">`
  rather than an `<output>`. An `<output>` element carries an *implicit*
  ARIA `status` role; `AutoDispatchStatus` is hoisted to a page-persistent
  slot (`ChatScaffold`'s new `autoDispatchStatus` prop, mounted regardless of
  which tab — chat or summary — is active, since a run transitions the
  person from one tab to the other mid-flight and the status must survive
  that switch) rather than scoped to one tab's content the way
  `ProposalPanel`'s own status region is, so on a screen that already has an
  unrelated `role="status"` region mounted at the same time (e.g. an
  early-return notice), a second implicit-`status`-role `<output>` would
  make `getByRole("status")`-shaped queries ambiguous. `aria-live="polite"`
  alone still delivers the same assistive-technology announcement without
  claiming the role. `features/chat/components/AutoDispatchStatusRow.tsx` pairs that
  status output with a conditionally-rendered `ghost` `Button` labelled
  "Stop", shared byte-for-byte by both doors so jscpd's threshold-1
  duplication gate has one implementation to check, not two. **Considered
  exception, extending `ProposalPanel`'s rationale above:** the new button
  binds no `⌃⇧`-style keyboard shortcut on either door, and
  carries no `ShortcutHint` glyph. The same reasoning applies one level up
  from Ctrl+Enter: on the chat door a focused message textarea sits directly
  beside it, so a bound shortcut would leak into ordinary typing exactly the way
  `ProposalPanel`'s own Ctrl+Enter omission already documents. A future
  control reaching for a shortcut beside a focused text field should make
  the same call, not silently bind one.
- **Direct repo action row, retiring the menu-button pattern (W3)** — the
  per-repo "New" popover (the
  [WAI-ARIA APG menu-button pattern](https://www.w3.org/WAI/ARIA/apg/patterns/menu-button/))
  is retired: Maintenance, Hide, and Settings render as directly visible
  `Button` `ghost` instances in every repo section, no trigger or panel to
  open first. **Superseded by the conversations rail:** Build and Explore no
  longer live on this row — they moved to `shell/ConversationRail.tsx` (see
  "Conversation rail, collapse, and drawer" below), which is now the only
  click-path for starting either kind of conversation. Maintenance carries
  `opacity-70 hover:opacity-100` — the same dimmed-chip idiom
  `CollisionBox.tsx` already uses — so hierarchy comes from grouping,
  ordering, and this existing opacity value, never a saturated fill or alarm
  colour. Maintenance dispatches immediately on click from the per-repo page;
  success is reported by a `role=status` `<output>` notice
  (`repo-maintenance-notice-*`) carrying a workspace link and a Stop control,
  and Stop rewrites that same notice's text rather than unmounting it, so
  the live region still announces the change. New repo-scoped starting
  actions should extend the rail rather than reintroducing a menu-button
  panel or a dashboard control. **Hide control and the hidden-repositories
  disclosure** extend the same row: Hide is a fourth `ghost` `Button`
  carrying the same `opacity-70 hover:opacity-100` dimmed-chip weight as
  Maintenance, `aria-disabled` (never natively `disabled`, so the control
  stays in the tab order and its `HoverReveal` description stays keyboard-
  reachable) for the default repository, with the click handler itself
  guarded to a no-op while `aria-disabled`. Unlike
  Maintenance's Stop, a successful hide unmounts the whole section —
  `useEngines`' visible-only `engines` filter drops the repo as soon as a
  racing engines refetch lands (the hide route persists the hidden flag as
  its first statement, before doing any of its slow work, so that refetch
  can land before the browser's own hide request even resolves) — which
  would carry a row-owned `role=status` notice away with it before it is
  ever rendered, not just before assistive tech can announce it.
  `features/home/components/RepoActionRow.tsx` therefore holds only `MaintenanceNotice`
  now; the row-owned `repo-hide-notice-*` notice is deleted outright, not
  kept as a second surface. The hide (and restore) outcome is reported
  solely through `components/HiddenRepoDialog.tsx`'s always-mounted
  `HiddenRepoAnnouncementRegion` — a `<div role="status" aria-live="polite"
  aria-atomic="true" data-testid="hidden-repo-announcement">` (a plain `div`
  with explicit live-region ARIA, not an `<output>`, since the region nests
  an interactive dismiss `Button`) that toggles between `sr-only`
  and the `QueuePausedBanner` container recipe (`border-b border-border
  bg-surface-elevated px-6 py-2 text-sm text-text-primary`), carrying an
  `ok` | `warn` | `error` tone (an `ok` report renders in the plain
  `text-text-primary` token; `warn` — a hide that left residue — renders in
  the `font-medium text-status-warning-fg` amber warning-text token, so a
  partial success reads distinctly from an outright failure; `error` —
  including the request itself failing, previously silent — renders in the
  `font-medium text-btn-destroy` destructive-text token)
  plus a `ghost` `Button` dismiss, cleared only by that explicit dismiss,
  never by a timer or a route change. `announceHiddenRepoChange()` is the
  cross-subtree channel a row hands its outcome to;
  `HIDDEN_REPO_DISCLOSURE_SUMMARY_ID` is the stable id a row's Hide control
  focuses once its own section is about to disappear — the same
  rewrite-in-place principle as Maintenance's Stop, applied across a
  component boundary instead of within one, and now the *only* hide/restore
  outcome surface rather than one of two. New Home-level actions that need
  to announce past their own row's lifetime should reuse this channel
  rather than inventing a page-level live region. **Accepted limitation:**
  while the hidden-repositories `<dialog>` (`components/HiddenRepoDialog.tsx`,
  a native `showModal()` dialog opened from an always-mounted trigger button
  in `App.tsx`'s top bar, superseding the retired per-Home `<details>`
  disclosure this section used to describe) is open, this banner sits below
  the dialog's browser top layer and is only actually seen once the dialog
  closes; the dialog's own
  inline per-row restore state (`hidden-repo-restore-*`, its own inline
  error text) covers a restore outcome while the dialog stays open, so this
  is considered covered rather than a gap. **Shared cache (previously
  known-and-unfixed):** `useEngines()` now reads one shared `["engines","list"]`
  query cache entry (`docs/architecture/frontend.md`'s Engine list section)
  instead of `App`, `RepoShell`, `Repo` and the rest of its call sites each
  holding an independent uncached `GET /api/engines` with its own `loaded`
  transition, so the per-mount widening this row used to describe is gone:
  every consumer now observes the same single request and the same single
  `loaded` transition at the same instant. The fetch-settling interval itself
  still exists on a cold load — that is the accepted **third state,
  unresolved** window described just below, not a gap — but it is no longer
  N independently-timed copies racing each other. `App.tsx`'s always-mounted
  `Shell` keeps the engine-list observer permanently active, so a `Repo`
  mount reached after the app shell's first fetch has already settled finds
  the cache warm and reads `loaded: true` immediately.
  **Relocated into the section header, hover/focus-revealed descriptions
  (compact-repo-dashboard-and-shell-density):** Maintenance, Hide, and
  Settings moved out of their own row and into `features/home/components/RepoSection.tsx`'s
  header row, at its right-hand end, immediately before the `AggregateChip`
  — the visible repository name that used to occupy that row's left side is
  gone, replaced by an `sr-only` `h1` that preserves the page's one
  top-level heading without spending screen space on it.
  `features/home/components/RepoActionRow.tsx` shrank to just `MaintenanceNotice` — the
  maintenance post-action outcome message — still rendered directly below
  the header, unchanged in behaviour; the hide outcome moved to the
  app-level `hidden-repo-announcement` channel described above, not a
  row-owned notice. Each control is now
  `features/home/components/RepoActionControl.tsx`, a thin wrapper around `Button` and the
  new `components/HoverReveal.tsx` primitive (see "Hover/focus-revealed
  descriptions" below): the one-line consequence sentence that used to be a
  permanently visible sibling `<span>` is now that sentence rendered inside
  `HoverReveal`, `sr-only` until hovered or focused. The `aria-describedby`
  wiring is unchanged — only whether the description is visually painted
  changed, never whether it's programmatically associated. Once posture has
  resolved and gates the control below the dispatch floor, Maintenance
  renders as a `Button` `ghost` `linkTo` the repository's posture setting
  (`posturePathFor()`), never hidden, `disabled`, or `aria-disabled` — that
  path is a real link, not a button, so there is no click to guard. The
  busy-button path (Hide for the default repository, or Maintenance while
  `maintenanceDispatching`) instead renders `aria-disabled` with a guarded
  `onClick`, per the `RepoActionControl` note above.
  **A third state, unresolved (never-render-an-unresolved-posture-as-a-real-one):**
  posture is not known at first paint — it arrives with the engines fetch —
  and until that fetch settles (`postureResolved`, threaded from
  `useEngines().loaded` through `Repo.tsx` → `RepoPageBody.tsx` →
  `RepoSection.tsx`, never confused with the `ready` workspace/project
  readiness signal already on that surface), `features/home/components/postureGate.ts`'s
  `maintenanceGate` returns `{ kind: "unresolved" }` rather than treating the
  not-yet-loaded posture as a known-insufficient one. Maintenance then
  renders through the **same** `Button` `ghost` `<button>` host node the
  permitted path uses — deliberately, not the gated path's `<a>` — carrying
  `aria-disabled="true"` and `aria-busy="true"` with a guarded `onClick` (so
  a click during this window neither dispatches nor navigates) and the
  `HoverReveal` description "Checking this repository's posture…". Sharing
  the permitted path's host type is what lets React reuse the DOM node
  rather than unmount/remount it, so keyboard focus survives an
  `unresolved → permitted` transition once the fetch resolves autonomous; a
  type change happens only on the `unresolved → gated` transition, where the
  not-permitted form's real-link semantics (middle-click, status-bar
  preview) still require an `<a>`. `aria-disabled="true"` is also what makes
  Playwright treat the control as non-actionable, so an end-to-end click
  during this window waits on product state rather than on any test-side
  polling. The engines fetch failing outright (posture staying `null` after
  it settles) is not a fourth state — it resolves straight to the gated-link
  rendering, with its own reason ("Maintenance posture couldn't be read for
  this repository.") distinguishing it from a merely-low posture.
- **Per-row workspace action, retiring bulk selection
  (flatten-shell-chrome-and-per-row-actions)** — the workspace list's
  per-row checkbox column, `Clear selection`, and the whole bulk action bar
  (`useWorkspaceBulk`/`workspaceBulkOps`/the `BulkActionBar` instance in
  `features/home/components/RepoPageBody.tsx`) are gone; a feature row now shows exactly
  one action control, never zero and never two — Stop while running,
  Archive while not running, Unarchive while archived
  (`features/home/components/rowAction.ts`'s `rowActionFor`). This is an accepted
  capability loss, not a deferral: archiving many workspaces in one gesture
  is no longer possible anywhere, and Resume is deliberately not offered
  from the list — it survives only on the workspace detail page. The
  control is `components/SubduedActionControl.tsx`, wrapped in the same
  `HoverReveal` primitive so its consequence sentence reveals on hover and
  on keyboard focus. **Icon treatment
  (icon-row-actions-rail-align-tooltip-layer):** the control renders as an
  icon-only `Button` in the blue `action` variant
  (`ICON_ONLY_VARIANT_CLASSES.action`, `text-btn-action
  hover:bg-btn-action/15`) — the same quiet-glyph-plus-tinted-hover
  principle as the rail delete-bin's `destroy` variant, reused rather than
  reinvented, never a saturated fill. Each of the three actions carries its
  own glyph from `Button.tsx`: `StopIcon` (a filled square, a visibly
  different *kind* of shape) for Stop, and `ArchiveIcon`/`UnarchiveIcon` —
  rendering one shared, module-private tray-arrow path, the second with a
  plain `rotate-180` — so the two filing actions read as a literal mirror
  pair. `features/home/components/rowAction.ts`'s `RowAction` carries the icon alongside
  its existing `label`/`busyLabel`/`description`, and
  `rowActionAriaLabel(action, busy, workspaceTitle)` composes the accessible
  name (e.g. `"Stop — my-task"`) so a screen reader can tell same-action
  rows apart. `SubduedActionControl.tsx` gained optional `icon`/`variant`
  props precisely so this stayed the one action-control primitive rather
  than a second one-off icon button: given an `icon`, it renders `Button`
  `iconOnly` with a module-private `ICON_BUSY_CLASS`
  (`aria-disabled:opacity-50 aria-disabled:cursor-not-allowed`, no resting
  dim) instead of `SUBDUED_WEIGHT_CLASS`, since `SUBDUED_WEIGHT_CLASS`'s
  `opacity-70` resting dim exists for a *text* label and would permanently
  mute a glyph; `RepoActionControl.tsx`'s Maintenance/Hide/Settings
  controls pass no `icon` and keep rendering the pre-existing
  `SUBDUED_WEIGHT_CLASS` dimmed-label branch unchanged. `HomeRow.tsx`'s
  action `<td>` is a constant `w-11` regardless of which action, or
  whether it is busy or carries an error, so the column never widens; a
  per-row error message wraps (`break-words`) inside that fixed width
  rather than pushing it out. Stop fires immediately with no confirmation
  step, matching Maintenance's own immediate-dispatch precedent above,
  since a run in progress is cheap to restart and a confirmation dialog
  would only slow down the common case. `HomeRow.tsx`'s trailing action
  `<td>` replaces the former leading selection `<td>`, keeping the table at
  the same column count so header and body cells stay in parity by
  construction (asserted directly, not left to eyeballing).
  `BulkActionBar.tsx`/`bulkDrain.ts` are deleted (remove-inbox-from-web-ui) —
  Inbox was their only consumer.
- **Hover/focus-revealed descriptions (`components/HoverReveal.tsx`)** — the
  one sanctioned primitive for a description that shouldn't cost permanent
  vertical space: it renders its `children` plus a description `<span>` that
  toggles between `sr-only` and an absolutely positioned visible layer,
  opening on `onPointerEnter`/`onFocusCapture` and closing on
  `onPointerLeave`/`onBlurCapture`, with Escape dismissing it via a
  capture-phase `document` keydown listener attached only while open, which
  calls `preventDefault()` before `stopPropagation()` — the latter keeps a
  nested reveal from also closing a surrounding modal on the same keypress,
  and the former keeps that same keypress from also firing a surrounding
  native `<dialog>`'s own `cancel` event. `HoverReveal` also reconciles its
  `focused` flag against `document.activeElement` in a no-dependency-array
  `useLayoutEffect` that runs after every commit, comparing by reference
  identity against the element captured from `onFocusCapture`'s
  `event.target` — never `wrapperRef.current.contains(document.activeElement)`.
  This reconciler is **monotone-downward: it may only clear `focused`, never
  set it**, which is what keeps an explicit Escape dismissal from being
  reopened by an unrelated later re-render. It closes a real defect: when the
  element underneath a focused reveal is replaced by a different element (for
  example, a repository dashboard's gated maintenance link swapping for a
  live button once posture resolves) while the reveal's wrapper stays
  mounted, the browser delivers no `blur` for the removed element, so the
  pre-change component pinned its description open for the remaining life of
  the page. `hovered` is deliberately not reconciled the same way — jsdom has
  no `:hover` to check against, and the pointer handlers live on the
  surviving wrapper, so hover has no equivalent hole. Losing keyboard focus
  entirely when the underlying element is swapped out is a separate,
  unfixed accessibility defect; this reconciliation only stops the
  description from latching open, it does not restore the user's focus. The
  description node is never conditionally rendered and the primitive never
  uses a `title` attribute — `title` fails keyboard-reach, Escape-dismiss,
  and reliable assistive-technology announcement simultaneously. The
  revealed description's z-index comes from a single named constant,
  `shell/stickyOffsets.ts`'s `REVEAL_Z_CLASS` (`z-30`) — see "Sticky page
  regions" above for the full layering ladder this sits in — so every
  `HoverReveal` consumer, not just one call site, renders its open
  description above sticky page chrome including the workspace table's
  sticky column headers. This is the
  compliant answer to the constraint the conversation rail's collapse/expand
  controls already state under "Conversation rail, collapse, and drawer"
  below ("neither control is reachable only by pointer, and neither is a
  hover-only reveal"): `HoverReveal` opens on focus exactly as readily as on
  hover, so a description behind it is never pointer-only. New consequence
  text, tooltip-shaped help, or any other disclosure that would otherwise
  cost permanent layout space should compose `HoverReveal` rather than
  inventing a second hover primitive or falling back to `title`.
  `features/home/components/RepoActionControl.tsx` is its first consumer. **Normative rule
  (repo-chrome-colour-semantics-and-accent):** every navigation and action
  control in the shell — Build, Explore, connect-repository,
  health, hidden-repositories, project features, settings, and the Active/Archived toggle —
  MUST carry a short, plain-language description through `HoverReveal`
  (directly, or through `components/DescribedNavLink.tsx` for a nav-link-
  shaped control). A control's description states what it does, not how to
  invoke it — `components/ShortcutHint.tsx` already owns keyboard-shortcut
  disclosure, so a `HoverReveal` description must never repeat a shortcut a
  sibling `ShortcutHint` already states. `components/DescribedNavLink.tsx`
  is the shared wrapper `HealthStatusBadge.tsx` and
  `features/home/components/RepoScopedCountLink.tsx` route through so the nav-link chrome,
  the count-pill wiring, and the `HoverReveal` description plumbing are
  authored once rather than three times; a control whose shape isn't a
  nav-link (a plain button, an icon-only control, a tab) composes
  `HoverReveal` directly instead. `HoverReveal`'s `align="end"` prop keeps a
  right-edge header control's description from clipping off the right side
  of the viewport; its `wrapperRole="presentation"` prop keeps a reveal that
  wraps one item of a `role="tablist"`-shaped group ARIA-transparent, so the
  group's required-owned-elements contract still holds with the reveal's
  wrapper `<span>` sitting between the group and its item.
- **`components/ShortcutHint.tsx` — the shortcut-in-the-button paradigm,
  universalized (compact-repo-dashboard-and-shell-density)** — every
  keyboard shortcut previously taught by nearby prose (the composer's
  send/clear/summarize sentence, the conversation rail's build/explore
  caption) is now taught by this shared primitive instead: an `aria-hidden`
  glyph span byte-identical to the literal the app already used, plus a
  sibling `sr-only` spoken form ("`, shortcut Control Shift X`") inside the
  same button. Removing the prose without the spoken sibling would have
  dropped the shortcut from the accessibility tree entirely, since the
  glyph alone is `aria-hidden`. `features/chat/components/ChatComposer.tsx`'s Clear/
  Summarize, `features/chat/components/SendOrStopGroup.tsx`'s Send/Queue/Stop, and
  `shell/ConversationRail.tsx`'s Build/Explore controls are its consumers.
  The primitive renders its two spans inside one non-flex wrapper `<span>`
  rather than as two flattened siblings — a `Button`'s own `inline-flex`
  box only ever gains one extra child element this way, which matters
  because the desktop-audit gate's `actionGroupFit` check flags any
  `flex`/`inline-flex` container with 2+ visible children whose rows don't
  share a `y` position; the `sr-only` span's `position: absolute` computes a
  wildly different rect than its visible sibling, and two flattened
  children directly inside a `Button` would have tripped that check as a
  false-positive "wrap" on every consuming button.
  **The command-palette trigger (add-visible-command-palette-trigger-and-hints)**
  is the paradigm's top-bar consumer: `components/CommandPaletteTrigger.tsx`
  is the always-visible way into the palette, a text-labelled `ghost`
  `Button` reading "Commands" with the palette's `ShortcutHint` inside it, so
  the spoken form ("`, shortcut Control K`") lands in the button's accessible
  name and the shortcut is taught where the pointer already is. It is a
  desktop control — its wrapper is `hidden sm:inline-flex`, the same
  `sm`-switch the rest of the right group uses, and the mobile layout is
  unchanged. Its `HoverReveal` description states purpose only ("Search and
  run commands, such as starting a build or exploring a repository.") and
  never the shortcut its own `ShortcutHint` already carries. Because a click
  does not focus a `<button>` in Safari, the trigger focuses itself before it
  opens the palette, so `ui/useModalLifecycle.ts` captures it as the element
  to restore focus to when the palette closes. The palette's own rows use the
  same primitive through `ChooserRow`'s optional `hint`: the root Build and
  Explore rows carry their direct shortcuts, and the repository-scoped
  "Build in {repository}" / "Explore {repository}" rows — offered only while a
  repository is current — carry `⌃⇧X`/`⌃⇧E`.
  **Platform-aware glyphs — the rule for new hints.** A new `ShortcutHint`
  takes its `glyph`/`spoken` pair from `lib/shortcuts.ts`'s `shortcutHint(id)`,
  never from a literal at the call site: the one table there holds both the
  matcher each global keydown listener uses and the Apple and non-Apple label
  of the same chord, so the advertised shortcut and the bound one cannot
  drift apart, and `lib/platform.ts`'s `isApplePlatform()` (read per call from
  `navigator.userAgentData?.platform ?? navigator.userAgent`) picks the
  variant. Apple shows `⌘K`, `⌥⌘B`, `⌥⌘C` (spoken "Command K", "Option Command
  B", "Option Command C"); every other platform shows `Ctrl K`, `Ctrl Alt B`,
  `Ctrl Alt C` (spoken "Control K", "Control Alt B", "Control Alt C").
  The conversation rail's Build/Explore hints and the chat composer's
  send/clear/summarize hints are knowingly left on their literal `⌃` glyphs:
  until the harmonization follow-up moves them onto the table, the same
  Ctrl+Shift+X chord reads `⌃⇧X` on the rail and `Ctrl Shift X` in the
  palette's scoped row on a non-Apple platform. The delete dialog's Escape
  hint stays out of scope for the same reason; its Ctrl+Enter hint moved onto
  the table (`workspace-header-actions-on-shared-controls`) as
  `lib/shortcuts.ts`'s `modal-confirm` row: `⌃↵` on Apple, `Ctrl Enter`
  elsewhere, both spoken "Control Enter" — Ctrl-only, with no `⌘` alternate,
  since the modal convention binds `ctrlKey` specifically rather than either
  modifier. The delete-workspace confirm button is this row's first
  consumer.
- **Empty-state copy** — one-line list/section empty states follow the
  "No {noun} yet." sentence shape (`WorkspaceList.tsx`'s tab and
  project-filter empty states, `Projects.tsx`, `features/project/pages/Project.tsx`).
  **`features/home/components/ConnectBlankSlate.tsx` (W2.5)** is the sanctioned instance of
  a richer, multi-line welcome-style card, not a one-line list empty state —
  Home's full-page "no repositories connected" state, rendered only when the
  engine list has settled without failing and either no engine is visible or the
  only visible engine is the listed default with no forge target (an
  unconfigured install). A configured lone default is a connected repository:
  Home redirects to its dashboard, and the top-bar switcher and the drawer list
  it as one entry. Copy and chrome are unchanged. Its chrome is
  `border-dashed` plus `bg-surface-elevated/30` (translucent): the dashed,
  translucent treatment reads as an empty canvas inviting the one action
  that fills it, appropriate for a full-page state. Home's needs-you lines
  (`features/home/components/NeedsYouLines.tsx`) reuse the ratified body-link recipe below
  rather than a third empty-state shape. **Historical note:** the deleted
  Inbox page's own `EmptyState` used to be a second instance of this richer
  card shape (solid `border-border` plus full-opacity `bg-surface-elevated`,
  appropriate for a compact, in-page card rather than a full-page one) —
  removed by remove-inbox-from-web-ui, not converged onto anything.
  **`DriverLoopsPanel.tsx`'s configured-empty copy (retire-dead-driver-loop-
  registrations)** carries a trailing explanatory clause in the same single
  muted `<p>` rather than a second element — deliberately *not* a third
  instance of the richer multi-line card shape above: no chrome, no border,
  no second element, so a reader does not mistake the extra sentence for a
  silent violation of the one-line convention.
- **Shell-screen casing convention** — control labels (tab toggles, buttons)
  render Title Case; data/status values (status badges, enum-backed text)
  render lowercase. `WorkspaceList.tsx`'s Active/Archived tab toggle and the
  adjacent `StatusBadge` are the reference example — the two conventions
  coexist deliberately on the same screen rather than being unified, since
  lowercasing the tabs would clash with the Title Case action buttons beside
  them.
- **"shipped" vs "completed" is an intentional domain split** — project
  surfaces render the word "shipped" for a like-named workspace "completed"
  status; they are different domains (projects *ship*, workspaces
  *complete*), not a copy inconsistency to fix. Spans `ProjectStatusBadge`
  (`Projects.tsx`'s `STATUS_LABELS.completed`), the "This project has
  shipped." prose and "Mark shipped"/"Force ship" buttons in
  `features/project/pages/Project.tsx`, versus `StatusBadge.tsx`'s `STATUS_LABELS.completed`
  ("completed") for workspaces.
- **Badge / status-pill recipe** — the ratified shape is
  `inline-block rounded px-2 py-0.5 text-xs` plus a per-status colour pair.
  A fresh repo-wide grep (2026-07-20, superseding both the "3 of 4" count
  that the 2026-07-15 review itself flagged as an undercount, and this
  same section's own first re-count, which a later review round found
  still missed several implementations) finds **8** conforming
  implementations: `StatusBadge.tsx:30` (workspace status),
  `NodeStatusBadge.tsx:16` (DAG node status), `Projects.tsx:27`'s
  `ProjectStatusBadge` (project status), `HealthView.tsx`'s `SeverityBadge`,
  `CategoryBadge`, `BlockerChips`, and `RepoBadge` (the health page's
  finding-severity, finding-category, blocker-flag, and per-engine-repo
  chips; previously a one-off light-mode `bg-*-100 text-*-800` treatment
  with `uppercase font-semibold`, now conforming), and — newly converged
  by this item, no longer debt — `handoverSections.tsx`'s scenario-status
  pill (`TestScenarioItem`'s per-scenario runner-outcome badge: passed,
  failed, skipped, docs only, or not run — previously a bespoke
  `text-[10px]` executable/docs-only pair that said nothing about whether
  a scenario actually ran). **9th implementation, added by the
  dispatch-instructions read-back item:** `InstructionBadge.tsx` — the
  per-invocation dispatch-instructions status pill rendered on
  `InvocationCard` (with a collapsed `<details>` disclosure showing the
  full recorded text) and, in compact `showDetails={false}` form, on
  `InvocationGraph.tsx`'s `InvGroupNode`. It deliberately reuses this
  recipe's exact geometry rather than copying `NodeStatusBadge.tsx`'s
  hardcoded slate literals, and maps its four dispatch-instructions
  statuses onto the shared `--color-status-*` token pairs: `attached` →
  `status-active`, `not_delivered` → `status-attention`, `none` and
  `unknown` → `status-neutral` (an intentionally-empty run and an
  unresolvable one read as the same neutral colour, distinguished only by
  label text — "no instructions" vs "instructions unknown" — since neither
  is an alarm state). **Known divergence, not
  endorsed — five more shapes exist:**
  (1) `rounded-full px-2 py-0.5 text-xs font-medium` plus a per-status
  colour pair, shared by `ChatDraftRows.tsx`'s two badges (draft family,
  session status) and `HealthView.tsx`'s `InProgressChip` — the in-progress
  chip now imports `ChatDraftRows.tsx`'s exported `IN_PROGRESS_BADGE_CLASS`
  constant so the two screens' in-progress indicators can't silently drift
  apart, but the shape itself is still the un-ratified `rounded-full`
  variant, not this section's `rounded` recipe. (2) `features/home/components/RepoSection.tsx:54`'s
  dependency-count `dep-badge`
  (`inline-block rounded-full bg-blue-500/20 px-2 py-0.5 text-xs
  text-blue-300` — mixes `inline-block` with `rounded-full` and omits any
  font-weight utility; previously uncounted). (3) The nav count-pill,
  `rounded-full bg-status-neutral-tint/15 px-1.5 py-0.5 text-xs font-semibold
  text-status-neutral-fg` (the shared `NAV_COUNT_PILL_CLASS` constant in
  `components/navCountPill.ts`), used by
  `HealthStatusBadge.tsx`'s count span (`px-1.5` not `px-2`,
  `font-semibold` not `font-medium`; also previously uncounted). **W1.4
  desaturated this pill** — it previously rendered on `--color-btn-destroy`
  (saturated danger red) regardless of the count's meaning, a rule-22 defect
  ("counts are data, not alarm") rather than a rename; the geometry is
  otherwise unchanged, so it remains one of the five un-ratified filled-pill
  shapes below, just recoloured onto the neutral status tokens. (4) `rounded` (not
  `rounded-full`, no `inline-block`) `px-1.5 py-0.5 text-xs font-medium`
  plus a filled colour pair — `TriageSweepCard.tsx:37`'s "Stale" chip
  (`bg-amber-500/15 text-amber-300`), `TerminalOutcomeBanner.tsx:31`'s
  merge-block-class span (`bg-white/10`, inherits surrounding text colour),
  and `Workspace.tsx:120`'s blocker-kind span (`bg-white/10`, inherits
  `text-xs` from its parent `<li>`) — three previously-uncounted
  implementations. (5) The same `rounded px-1.5 py-0.5 text-xs
  font-medium` geometry but with no background fill at all — colour
  carried by text alone: `handoverSections.tsx`'s
  `ConfidenceBadge`/`confidenceBadgeClass` (previously uncounted; fits
  neither this section's filled-pill recipe nor the unfilled-bordered-chip
  exclusion below, since it has no border either — recorded here for
  completeness). **Explicitly excluded from this recipe's count:** the
  outline-style chips at `CollisionBox.tsx:65` (`shrink-0 rounded border
  border-current px-2 py-0.5 text-xs opacity-70 hover:opacity-100`),
  `RunPricing.tsx:121` (`rounded-full border border-border px-2 py-0.5`),
  and `handoverSections.tsx`'s `ReversibilityChip`/`reversibilityChipClass`
  (`rounded border border-{colour}-800 px-1.5 py-0.5 text-xs font-medium
  text-{colour}-400`; previously uncounted) — all three render an
  unfilled, bordered chip rather than a filled colour pill, a materially
  different visual pattern this recipe does not cover. Recorded as debt;
  converging the five filled shapes onto one is a follow-up brief (see
  "Deferred follow-ups"), not fixed here.
- **Tab/toggle presentation** — both toggles on the front door now share
  proper tab semantics: `role="tablist"`/`role="tab"`/`aria-selected` plus
  arrow-key navigation (`WorkspaceList.tsx`'s Active/Archived toggle,
  `components/UnderlineTab.tsx`'s Chat/Summary toggle, reused on Build and
  Explore and by the step-detail pop-up's Output/Prompt/Config tabs).
  That accessibility gap from the 2026-07-15 review is closed. **Known
  divergence, not endorsed:** the two *visual* treatments still differ —
  `WorkspaceList.tsx` renders a pill toggle (`bg-surface-elevated` behind
  the active tab), `components/UnderlineTab.tsx` renders an underline toggle
  (`border-b-2 border-text-primary`). Neither is ratified as canonical;
  converging on one visual treatment is a follow-up brief.
  `components/UnderlineTab.tsx` and `components/tablistKeys.ts` (the
  clamped ArrowLeft/ArrowRight `handleTablistKeyDown`) are shared
  primitives that any tablist may reuse, but the underline is not ratified
  as the app-wide standard — the pill-vs-underline decision stays open.
- **Nav-link chrome** — the ratified recipe is
  `inline-flex min-h-11 min-w-11 items-center gap-1.5 rounded-md px-2 text-sm font-medium text-text-primary hover:bg-surface-elevated`,
  used by `App.tsx`'s `Shell` header's `HealthStatusBadge.tsx` and by
  `features/home/components/RepoScopedCountLink.tsx`'s per-repo Health control. **Resolved
  by W1.4:** the header's third link, a bare `Projects` nav-link that
  diverged from this recipe (it omitted `min-w-11`/`gap-1.5`), is retired —
  and `/projects` survives as a destination reachable from each repo
  section's footer link instead. **Removed by remove-inbox-from-web-ui:**
  `InboxBadge.tsx`, the header's other nav-link-chrome consumer, is deleted
  along with the needs-you nav destination it pointed to — the top bar's
  right cluster is now hidden-repositories trigger · health · settings
  exhaustively.
  **Superseded by the repository switcher:** the top bar's left side is no
  longer the brand wordmark — it's `TopBarRepoSwitcher.tsx`, one entry per
  visible connected repository. The right side keeps the badges above in
  the same order, now preceded by the hidden-repositories dialog trigger
  and the relocated Connect-repository action.
  **Superseded by move-project-features-toggle-to-top-bar:** the right group
  now reads, exhaustively, `components/ProjectFeaturesToggle.tsx`'s
  "Projects" · hidden-repositories trigger · health · settings — on screen,
  `Projects · Hidden (N) · Health · Settings`. Projects is an `aria-pressed`
  toggle, not a nav-link: it does not revive the `Projects` nav-link W1.4
  retired, it navigates nowhere, and `/projects` stays reachable from each
  repo section's footer link. Its pressed-state recipe, which later top-bar
  toggles reuse, is a ghost `Button` + `aria-pressed` + an `aria-hidden`
  check glyph (`CheckIcon`, present or absent — a shape cue, not colour
  alone) + `bg-surface-elevated`; the visible label stays fixed as
  "Projects" in both states. Its `HoverReveal` uses the default start
  alignment rather than `align="end"`, because it is not at the right edge.
  The preference no longer appears in the Settings dialog.
  **Amended by add-visible-command-palette-trigger-and-hints:** the right group
  now reads, exhaustively, `components/CommandPaletteTrigger.tsx`'s "Commands"
  · Projects · hidden-repositories trigger · health · settings — on screen,
  `Commands · Projects · Hidden (N) · Health · Settings`. Commands is first, a
  ghost button rather than a toggle or a nav-link, and it appears at `sm` and
  up only. It opens the command palette at its root list, like the keyboard
  shortcut, and closing the palette returns focus to it (see the
  `ShortcutHint` bullet above for its shape, its description and the
  platform-aware glyph rule).
  **Amended by add-operator-page:** the right group now reads, exhaustively,
  `Commands · Projects · Hidden (N) · Health · Operator · Settings` —
  `features/operator/components/OperatorNavLink.tsx` sits between Health and
  Settings and is the third consumer of `DescribedNavLink` (see the Operator
  page bullet below for its dot).
- **Operator page (`features/operator/`, add-operator-page)** — `/operator`
  is a full page and never a modal, because Escape belongs to Claude Code
  inside the terminal and a modal would take it away. The page holds one
  interactive Claude Code session, so everything around the terminal follows
  the conventions already ratified here rather than adding new ones; no new
  `--color-*` token, text size or hand-rolled button was minted for it.
  - **Top-bar entry.** `OperatorNavLink.tsx` is a `DescribedNavLink` with
    the nav-link chrome recipe and a leading dot built like
    `HealthStatusBadge.tsx`'s: `h-2 w-2 rounded-full` inside an `h-5 w-2`
    wrapper, `aria-hidden`. The dot is *lit* — filled `bg-green-500` — while
    a session is live and *unlit* — a hollow `border border-text-muted` ring
    — when none is, so the state is a shape difference and not colour alone;
    the accessible name carries it too ("Operator terminal: session live"),
    and `data-live` exposes it to tests. It reads the same `["operator",
    "status"]` query the page does and refetches through
    `useChangeRefetch("health")`, because the server files
    `operator_session_changed` under the health change kind. It never asks
    for an attach ticket, so nothing reattaches until the page itself is
    opened.
  - **States.** `operatorPhase.ts` derives one phase from the status
    query, the viewport and the socket, and `OperatorNotice.tsx` renders it:
    checking the status (`loading`), no session (`choose`, which also opens
    the scope dialog), `connecting`, `attached`, `reattaching`, `ended`,
    `superseded`, and `unavailable(reason)`. An unavailable notice is a
    `role="alert"` panel naming the cause in plain words
    (`operatorCopy.ts`'s label for `detached_mode_off`, `tmux_missing`,
    `claude_missing`, `git_missing`, `checkout_clone_failed`, or the two the
    client itself raises, an unreachable status and a refused ticket) above
    the server's own sentence; a reason this build does not know is shown
    verbatim, never hidden. Every other notice, and the update warning, the
    phone message and the terminal-loading message, is a native `<output>`
    element (implicit `role="status"`) rather than a `role="status"` on a
    `div` or `p`. While connecting, attached or reattaching the
    terminal stays mounted; in every other state no terminal is rendered at
    all. Below the `sm` breakpoint the page shows a plain
    "needs a larger screen" message instead of a terminal, and opens no
    session connection.
  - **Controls.** `OperatorBar.tsx` carries **Close**, **End session** and
    **Replace session** as `Button`s in a native `<fieldset>` named "Session
    controls" (with `min-w-0`, so the fieldset's intrinsic minimum width
    cannot keep it wide) *outside* the terminal area, in the tab order ahead
    of it. Close leaves the page and
    leaves the session running; End session goes through
    `ConfirmDestructiveDialog` (the `destroy` variant) and only then stops
    the session; Replace session opens `ScopePickerDialog.tsx`, which asks
    for an explicit "Replace the running session" confirmation before it
    will send `replace: true`.
  - **The scope dialog and its list pickers.** `ScopePickerDialog.tsx` is a
    `DialogFrame` with a native radio group — **Global**, one repository,
    one workspace — and, for the last two, one native dropdown. Global keeps
    the wire value `fleet` and the test id `operator-scope-fleet`; only its
    label changed. There is no free-text target field and no new combobox
    component: `TargetSelect.tsx` renders the existing `Input` primitive as
    `as="select"` with `tapTarget` and `focusRing`, under a `<label>` linked
    by `htmlFor`, and its first `<option>` is a disabled "Choose a …" prompt.
    Each scope option carries a one-line hint saying what the scope does
    (`operatorCopy.ts`'s `SCOPE_OPTIONS`): Global opens in the folder holding
    a checkout of every connected repository with the fleet's workspaces,
    logs and state folders available, a repository opens in that repository's
    operator checkout and waits for the operator to type, and a workspace
    starts analysing that workspace straight away from its own repository's
    checkout. The dialog no longer carries the line saying a scope never
    changes permissions, because it is no longer true.
    - **Repository options** come from `useEngines().engines` — the visible
      list, so hidden repositories are never offered — deduplicated by
      `owner/repo`, sorted with `compareRepoEntries(defaultEngineKey)` so the
      default comes first, and labelled by the slug itself. They deliberately
      bypass `switcherEntries` and `registeredRepoEntries`, which add the
      default key when the list omits it and hide a lone repository.
      A single connected repository is preselected, and so is the one a
      `?scope=repo&target=owner/name` link names when it is connected; a
      repository the list does not carry is never offered.
    - **Workspace options** are the rows of `/api/workspaces?include=triage`
      whose server-side `operator_link` is true — the same definition that
      decides whether a workspace page shows its operator link, never a status
      list re-derived in the client — plus the workspace the link named, kept
      even when it stopped being stuck in the meantime (a bare-id option when
      the list no longer carries it at all, and only when the id fits
      `WORKSPACE_TARGET_PATTERN`). Each option reads
      `title · status · repository`, the repository part left out when the
      workspace has none. The workspace list is requested only while the
      workspace scope is chosen.
    - **States.** *Loading* disables the dropdown, sets `aria-busy` and shows
      "Loading repositories…" / "Loading workspaces…" as its only option.
      *Load failure* shows an inline `ErrorBanner` (`role="alert"`) under the
      disabled dropdown, linked to it by `aria-describedby`. *Empty* shows a
      muted `text-sm text-text-muted` message — "Nothing needs the operator
      right now." for workspaces, "No repository is connected yet." for
      repositories — linked the same way. In all three Start stays disabled.
      Start is enabled only when the choice is one of the listed options, plus
      the replace confirmation when a session is running; a Global start needs
      no choice.
    - **Keyboard and focus.** The order is the scope radios, then the
      dropdown, then the replace confirmation, then Cancel and Start.
      Ctrl+Enter submits from anywhere in the dialog but does nothing while
      Start would be disabled, and Escape cancels. The dropdown takes the
      ratified `focus-visible:ring-2 focus-visible:ring-text-primary/60` ring
      through `focusRing`, and its text, the muted messages and the banner use
      the existing `--color-*` tokens, so contrast is the dialog's own
      WCAG-AA contrast.
  - **Terminal.** `OperatorTerminal.tsx` runs xterm with
    `screenReaderMode` on, inside a native `<fieldset>` named "Operator
    terminal", and fits it to its container on mount and on every
    window resize, sending the new size to the session each time. Its theme
    (`operatorTheme.ts`) is read from the existing `--color-surface`,
    `--color-text-primary` and `--color-border` tokens at mount, and a token
    that cannot be read is left out so xterm keeps its own default. The
    container takes the ratified `focus-within:ring-2
    focus-within:ring-text-primary/60` ring, so keyboard focus is visible.
  - **Leaving the terminal by keyboard.** Claude Code and tmux own almost
    every key inside the terminal, so one chord is reserved to hand focus
    back: `lib/shortcuts.ts`'s `operator-escape` row, Ctrl+Shift+F (`⌃⇧F` on
    Apple platforms, spoken "Control Shift F"). It is handled only inside
    xterm's `attachCustomKeyEventHandler` — which calls `preventDefault`,
    keeps xterm from forwarding the chord to the session, and moves focus to
    the first button of the bar — and never by a document-level listener, so
    it does nothing outside the terminal. `OperatorBar.tsx` shows it on
    screen through `ShortcutHint`. `lib/shortcuts.test.ts` pins the row
    against a denylist of browser-reserved combinations and of Claude Code
    and tmux keys; adding a shortcut that lands on one of them is a failing
    test, not a review comment.
  - **The stuck-workspace link.** `WorkspaceHeader.tsx` shows an "Open in
    operator terminal" link (the body-link recipe, `min-h-11`) exactly when
    the workspace detail's `operator_link` is true — the server's own
    definition of stuck, never a status list re-derived in the client. It
    opens `/operator?scope=workspace&target=<task id>`, which preselects the
    workspace scope and that workspace in the dialog's dropdown — even when it
    has stopped being stuck since the link was rendered — in replace mode when
    a session is already live.
  - **Audits.** `operator` is a screen in `e2e/mobile-audit/screens.ts` with
    a zero baseline and a zero pin, and a `none` entry in the desktop-audit
    registry because the terminal is a live pty attachment the audit's seeded
    server cannot start (detached mode is pinned off there, so both audits see
    the unavailable notice). The terminal, its bar and its keyboard path are
    covered by `e2e/operator-terminal-session.spec.ts` against stubbed
    endpoints and a stubbed socket.
- **Repository-switcher selection recipe (ratified, revised by
  repo-chrome-colour-semantics-and-accent)** — the one selection language
  later work (the repository drawer, and originally the conversations rail —
  see "Strong tier" below for why the rail row has since moved off it) reuses:
  an accent edge (`ACCENT_EDGE_CLASS`, `border-[var(--accent-edge)]`, as a
  `border-b-2`/`border-l-4` shape cue — present or absent, not colour alone)
  driven by the selected repository's own `accentVars(key)` rather than one
  shared blue, a text-contrast step (the selected entry's name renders in
  `ACCENT_TEXT_CLASS` — its own accent foreground colour — while an
  unselected entry stays `text-text-muted`), and `aria-current="true"` —
  never `aria-current="page"`, since the scoped repository stays marked
  while the user is on a cross-repository page (Health, Connect),
  where `"page"` would misdescribe those routes. `aria-current` is what
  carries the non-colour selection signal: colour alone never distinguishes
  selected from unselected. The scoped repository is the route's own on
  `/repos/:owner/:repo/*`; on `/workspaces/:taskId/*` — the detail page, the
  preparation-chat page and the run full-view page — it is the workspace's own
  repository (a workspace with no engine key belongs to the default
  repository), resolved through `hooks/useRepoScope.ts` even when the page was
  opened directly in a fresh tab, so the switcher marks the same repository
  the Ctrl+Shift+X/E shortcuts and the palette's scoped rows will act on. It
  marks nothing while that repository is unresolved or is not among the
  visible entries, and the session mirror is neither read nor written on a
  workspace page. Every entry carries `hover:bg-surface-elevated`
  (see the Hover bullet above for why that's now a visible change) and
  `focus-visible:ring-2 ring-text-primary/60`; no entry is ever `disabled`.
  Below the small breakpoint, the desktop entry list (`hidden sm:flex`) is
  not shown at all — the small-width equivalent is `shell/RepoDrawer.tsx`'s
  repository list (see "Conversation rail, collapse, and drawer" below), not
  a native `<select>`; the `<select>` this bullet used to describe is
  retired along with it. The connect-repository action
  (`TopBarRepoSwitcher.tsx`'s `connect-repo-cta`) renders once, immediately
  after the repository list and outside its `nav` landmark, at every
  viewport width — the narrower `connect-repo-cta-compact` instance
  `App.tsx`'s `Shell` used to render separately below the `sm` breakpoint is
  retired, since one instance placed after the list already satisfies both
  widths structurally.
- **Conversation rail, collapse, and drawer** — every repository page is a
  persistent master–detail shell, `shell/RepoShell.tsx`: the shell renders no
  title of its own — it opens directly with a two-column row —
  `shell/ConversationRail.tsx` on the left, an `<Outlet/>` detail region on
  the right that shows the repository dashboard by default and swaps to a
  build or explore conversation without remounting the shell. Below the `sm`
  breakpoint the rail column is hidden (`hidden sm:block` on its slot) and a
  single `sm:hidden` drawer-trigger button carrying the repository name takes
  its place above the still-visible, now full-width `<Outlet/>` — that button
  is the only shell-chrome rendering of the repository name at that width;
  tapping it opens `shell/RepoDrawer.tsx` (below) to reach the rail. The rail
  is the **only**
  click-path for starting a conversation — `features/home/components/RepoActionRow.tsx`
  no longer offers Build or Explore (see "Direct repo action row" above) —
  and its Build/Explore controls (`variant="action"`, blue — resolved by
  repo-chrome-colour-semantics-and-accent from the earlier neutral `ghost`,
  matching the rule that navigational/primary actions read blue), each
  carrying its `⌃⇧X`/`⌃⇧E` shortcut via `components/ShortcutHint.tsx` (see
  "Component conventions" above) and its own `HoverReveal` description
  stating purpose only — never the shortcut a sibling `ShortcutHint` already
  states — sit directly beneath the rail's own heading, in
  `shell/ConversationRail.tsx`'s `StartControls`. Each row
  (`shell/RailRow.tsx`) is two lines — date plus kind/state labels, then a
  `line-clamp-2` description — with an always-visible delete control
  (`min-h-11 min-w-11`, no hover-only reveal), reusing the same
  confirm-then-delete flow `ChatDraftRows.tsx`'s `DraftRowItem` already
  established. The height budget (`max-h-[calc(100dvh-5rem)]`) is carried by
  the rail's own outer flex column, not the scroll region alone — the
  heading, `StartControls`, and the collapse control all take their natural
  height first, and the conversation list (`min-h-0 flex-1 overflow-y-auto`)
  absorbs whatever's left, so a long list scrolls in place rather than
  pushing the collapse control off-screen, and an empty rail shows "No
  conversations yet." — the same one-line empty-state sentence shape as
  elsewhere in the app.
  **Selection** is extracted into `shell/selection.ts` and consumed
  byte-for-byte by the top-bar switcher, the rail's active row, and the
  drawer's repository list — no second selection idiom outside this module,
  but the treatment it hands out is a two-tier vocabulary, not one identical
  recipe across every consumer. Every consumer shares the same interactive-
  state fragment,
  `hover:bg-surface-elevated focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-text-primary/60`
  (a `focus-visible` ring, so the indicator paints for a keyboard-driven
  focus move but not for one a mouse click triggers programmatically), and
  the same `aria-current="true"` — never `"page"`, for the same reason the
  switcher's own recipe above avoids it — carried unchanged regardless of
  tier. **Base tier** (the top-bar switcher, the drawer's repository list):
  the shared interactive fragment above, paired with the accent edge
  (`ACCENT_EDGE_CLASS`, `border-[var(--accent-edge)]` — present or absent,
  not colour alone, resolved per repository rather than one shared blue
  since repo-chrome-colour-semantics-and-accent; `selection.ts`'s own
  `SELECTION_ACCENT_CLASS` literal is retired along with it — the in-app
  blocking-decision answering surface, the literal's last consumer, is
  itself retired (remove-in-app-blocking-decision-answering-ui), so
  `border-btn-action` has no remaining consumer and is no longer a ratified
  recipe; the workspace blocking-decision surface now renders its question
  read-only, via `features/workspace/components/OpenQuestionItem.tsx`), and the row's own
  `text-text-primary`/`text-text-muted` contrast step. **Strong tier** (the
  rail row only, `shell/RailRow.tsx`): the selected `<li>` additionally
  carries a persistent `SELECTION_ROW_SELECTED_CLASS`
  (`bg-surface-elevated`) raised background — never a hover-only effect, and
  distinct from the base tier's hover treatment, which only ever paints the
  interactive `<Link>` inside the row, not the row itself. **Revised by
  calm-the-chat-reading-experience: the rail row's accent inset edge and its
  `--accent-fg` preview text are removed outright, not merely dimmed.**
  `shell/selection.ts` no longer exports `selectionAccentShadowClass` or
  `selectedPreviewTextClass`, and the module carries no
  `ACCENT_EDGE_INSET_SHADOW_CLASS`/`NEUTRAL_EDGE_INSET_SHADOW_CLASS`
  constants — the selected row's preview text now renders in
  `UNSELECTED_PREVIEW_TEXT_CLASS` (`text-text-primary`) unconditionally,
  the same class an unselected row already used, so selection carries no
  colour-only cue anywhere in the row. What remains, and is now the whole
  strong-tier vocabulary: the raised `bg-surface-elevated` background, the
  link's own `aria-current="true"`, and the shared interactive fragment's
  focus ring — the identical non-colour cues the base tier (the top-bar
  switcher, the drawer's repository list) already relies on. This was a
  deliberate readability call, not an accessibility regression: the accent
  edge and accent-tinted preview text were legible but competed with the
  transcript text itself for attention in the one place (the rail) dense
  enough for that competition to matter; `ConversationRail.tsx` still
  receives and applies the repository's own `resolvedAccent` to its own
  container via `accentVars` — only the per-row treatment inside it changed.
  Superseded history, kept for context: **icon-row-actions-rail-align-
  tooltip-layer** had reworked the row's accent edge from a `border-l-4` into
  an inset `box-shadow` (`shadow-[inset_4px_0_0_var(--accent-edge)]`, or the
  neutral `shadow-[inset_4px_0_0_var(--color-border)]` for an explicit
  no-accent repository) specifically so the edge would paint without
  consuming row layout width, since a real border had needed a compensating
  negative margin that grew the row's border box past the rail's true left
  edge — the box-shadow approach worked as intended for its lifetime, but
  calm-the-chat-reading-experience found the whole edge (in either shadow
  form) still an unwanted second signal once the rail dropped it. Opening a
  conversation moves focus
  to its `ChatScaffold` heading; deleting the open conversation returns the
  detail region to the dashboard and announces the outcome through the
  rail's own `role=status` live region, while deleting any other row
  leaves the detail region untouched. The in-chat "back to workspaces" link
  `ChatScaffold.tsx` used to render is removed — the rail replaces it
  everywhere the shell is present.
  **Collapse** (`shell/useRailCollapse.ts`) is addressed via `?rail=`,
  mirrored to `sessionStorage` the same way the repository-switcher's own
  scope mirroring works, so a collapse chosen on one screen re-applies
  itself on the next navigation that doesn't already carry the param.
  While collapsed, a compact repository-initials indicator
  (`shell/RepoShell.tsx`'s `CollapsedRailIndicator`) both reopens the rail
  and offers a compact way to start a build conversation, so the app's
  primary action is never entirely invisible.
  **The collapse control's position (revised by
  compact-repo-dashboard-and-shell-density):** it no longer sits inline with
  the rail's own `Conversations` label above the conversation list.
  `shell/ConversationRail.tsx`'s optional `onCollapse` prop now renders the
  `rail-collapse` button in its own row *after* `conversation-rail-scroll`,
  so DOM order is label → `StartControls` → the scrollable conversation list
  → the collapse control — trading "always visible without scrolling" for
  more of the list itself being visible without scrolling, which is the
  point of the density pass this control's relocation belongs to. It is the
  mirror image of the collapsed state's `rail-expand` button, which sits in
  `shell/RepoShell.tsx`'s `CollapsedRailIndicator` in place of the rail
  entirely. `RepoDrawer.tsx` renders the rail with no `onCollapse` at all —
  collapsing is meaningless once the rail is already inside a dismissible
  dialog. The two controls are mutually exclusive (only one is ever mounted
  at a time, since collapsing swaps the rail for the indicator outright) and
  each carries the `aria-expanded` state of the region it discloses:
  `rail-collapse` (rail expanded) is `aria-expanded="true"`, `rail-expand`
  (rail collapsed) is `aria-expanded="false"`. Neither carries
  `aria-controls`: the region each one toggles is unmounted, not merely
  hidden, on the other side of the toggle, so a collapsed-state
  `aria-controls` would reference an element that doesn't exist in the DOM —
  a dangling IDREF is worse than an omitted optional attribute. Neither
  control is reachable only by pointer, and neither is a hover-only reveal —
  the rail's own delete control (`RailRow.tsx`) already forbids that pattern,
  and this convention doesn't reopen it for a second control.
  **Heading order below the page's `h1`, added by review:** the
  `Conversations` label above is deliberately not a heading element — it
  never competes with the dashboard's own outline, which matters because the
  rail column precedes the `<Outlet/>` in `shell/RepoShell.tsx`'s DOM order,
  so an `h2` there would have sat ahead of `features/home/components/RepoSection.tsx`'s
  `h1` in reading order. `features/home/components/RepoStatusStrip.tsx`'s `Repository
  Status` and `features/health/components/RecurringProcessesPanel.tsx`'s `Recurring
  Processes` are `h2` (promoted from `h3`), so the repository dashboard's
  heading order is `h1` → `h2` → `h2`, sequential and with nothing
  preceding the `h1`. **Reconciled by compact-repo-dashboard-and-shell-
  density — no change needed, checked:** `features/home/components/RepoSection.tsx`'s
  `h1` is now `sr-only` (its text no longer paints on screen, since item 5
  removed the visible repository name from the card), but it is still a
  real, present `h1` ahead of the same two `h2`s in the same order — the
  ladder this passage describes holds exactly as stated.
  **The drawer** (`shell/RepoDrawer.tsx`, addressed via `?drawer=open`) is
  the small-width replacement for both the switcher and the rail at once: a
  `<dialog>` built on the same `src/ui/useModalLifecycle.ts` primitive
  `ListChooserModal.tsx`/`ConsultantModal.tsx` already use — native focus
  trap, Escape-to-close, and focus returned to the trigger that opened it —
  holding the repository list (this recipe's selection language again)
  above `shell/ConversationRail.tsx`. No new combobox primitive was built.
- **Body link** — the ratified recipe for an inline link inside body text is
  `text-btn-action underline`. Current call sites include `HealthView.tsx`'s
  `FindingLinks` (the Workspace, PR, and Remediation links) and, in
  `Project.tsx`, the PR-list link, and the doc-ref link — which additionally
  carries `text-sm`; this list is representative of current usage, not an
  exhaustive count, and other inline body links should adopt the same
  recipe. `DeliverySummaryPanel.tsx:35` uses the same `text-btn-action`
  token for a similar link but adds `hover:underline` instead of a static
  `underline`, so it is a related but distinct precedent, not a consumer of
  this recipe.
- **Rendered-Markdown chrome (`MarkdownBody.tsx`)** — the shared markdown
  renderer used by `MarkdownPanel` and every markdown surface in the app.
  Its heading ladder shifts markdown `#`/`##`/`###` down to `<h4>`/`<h5>`/`<h6>`
  elements so they never collide with a page's own `h1`/`h2`; markdown
  `####`/`#####`/`######` continue that ladder by all clamping to `<h6>`
  elements (there is no `<h7>`/`<h8>`), distinguished only by className
  (`text-sm font-semibold` / `text-xs font-semibold` / `text-xs font-medium`).
  GFM (via `remark-gfm`) pipe tables render as a real `<table>` wrapped in an
  `overflow-x-auto` div (the app shell's `max-w-5xl` clamp means no measure
  cap is needed, but a wide table still needs its own horizontal scroll).
  Fenced code renders a bordered, `overflow-x-auto` `<pre>`; inline code
  renders a `rounded bg-surface px-1 py-0.5 font-mono text-xs` chip — the
  `pre` override neutralizes the chip's background/padding on its child
  `<code>` (`[&>code]:bg-transparent [&>code]:p-0`) since react-markdown v10
  has no `inline` prop to branch on. Links render `text-btn-action underline`
  plus `target="_blank" rel="noopener noreferrer"`. Blockquotes and `<hr>`
  get minimal chrome (`border-l-2 border-border` / `my-3 border-border`).
  Deliberately unstyled, by design: `em`, `del`, and the task-list checkbox
  `input` — their UA defaults are acceptable and don't warrant an override.
  `MarkdownSourceToggle.tsx` is the single shared source/formatted control — a
  ghost `Button` driven by a closed `labelSet: "source" | "edit"` union (never
  a free-form label prop, so no future call site can set arbitrary text):

  | `labelSet` | Formatted showing | Source/edit showing | `aria-pressed` |
  |---|---|---|---|
  | `"source"` (default) | "View source" | "View formatted" | present, tracks `showSource` |
  | `"edit"` | "Edit" | "Preview" | absent |

  `MarkdownPanel` uses the `"source"` variant: its labels name *which view is
  currently showing* ("View source" while formatted is showing, "View
  formatted" while source is showing), so `aria-pressed` stays a meaningful,
  non-redundant addition alongside them. `ChatSummaryTab.tsx` (the chat
  summary brief) uses the `"edit"` variant: its labels name *what the next
  click does* ("Edit" while the formatted brief is showing, "Preview" while
  the editable field is showing) rather than the current state — a control
  whose accessible name already describes the destination must not also
  announce a pressed state, since in the "Preview" case that would be an
  outright contradiction (pressed while offering to un-press). The `"edit"`
  variant therefore omits `aria-pressed` entirely rather than tracking it.
  This is a pressed-button control, not the `role="tab"` pattern described
  under "Tab/toggle presentation" below — the two are distinct dimensions and
  should not be conflated. In chat
  (`ChatTranscript.tsx`), assistant replies render through `MarkdownBody`
  once a turn completes, matching this same rendered-Markdown chrome; the
  user's own messages always stay literal `<pre>` text, unchanged from what
  they typed.
- **Icon-button affordance (resolved by
  repo-chrome-colour-semantics-and-accent)** — `Button.tsx` now carries a
  proper `iconOnly` treatment instead of the earlier one-off: passing
  `iconOnly` (which requires `aria-label`, enforced by a discriminated union
  on `ButtonProps`) swaps `Button`'s padded `px-3 py-1.5` base for a compact
  `min-h-11 min-w-11` square with no horizontal padding, and swaps each
  variant's saturated fill for a quiet, text-only colour
  (`ICON_ONLY_VARIANT_CLASSES` — e.g. `destroy` renders `text-btn-destroy
  hover:bg-btn-destroy/15` rather than a solid red square; `ghost` keeps its
  existing bordered look unchanged). `Button.tsx` additionally exports
  `GearIcon`/`TrashIcon`, two small inline SVGs (`fill="currentColor"`, so
  they inherit whichever quiet colour the variant applies) shared by every
  icon-only consumer rather than duplicated per call site. Every hand-rolled
  icon-only button in the shell now routes through this one treatment:
  `App.tsx`'s global settings gear, `features/home/components/RepoActionControl.tsx`'s
  per-repository Settings control, `shell/RailRow.tsx` and
  `components/ChatDraftRows.tsx`'s delete controls (now a red bin, replacing
  the earlier neutral "×"), and `shell/RepoShell.tsx`'s
  `CollapsedRailIndicator` pair. No second one-off should reappear; a new
  icon-only control should reach for `Button`'s `iconOnly` prop. `Button`
  additionally takes an opt-in `density` prop (`"default"` | `"compact"`;
  the default output is byte-identical to before): `"compact"` swaps the
  44px minimum for `min-h-6 min-w-6` (plus `px-2 py-0.5 text-xs` on the text
  and link forms), for both the padded and the `iconOnly` treatment. It is
  reserved for controls on graph-canvas node cards — see "Spacing & tap
  targets."
- **Run full-view page** — `/workspaces/:taskId/invocations/:invId`
  (`pages/WorkflowView.tsx`) is a user-facing page, not a debugging
  leftover. Every run node in the workspace page's combined run graph
  (`InvocationGraph.tsx`'s `InvGroupNode`) carries a visible "Open full
  view" link — a sibling of the expand/collapse toggle, never nested inside
  it — and the page's own "Back to workspace" link returns. Step cards
  behave the same on both surfaces.
- **Step-detail pop-up** — `NodeDetailModal.tsx` follows the standard
  modal-key convention: `modalKeys.ts`'s `handleModalKeyDown` for Escape
  plus an `onCancel` that calls `preventDefault`, so one Escape closes it
  exactly once and focus returns to the control that opened it. It is named
  by its `<h2>` step label through `aria-labelledby`, switches Output,
  Prompt and Config through the `UnderlineTab` tablist, and closes through
  an icon-only `Button` wrapped in a `HoverReveal` description with an `Esc`
  `ShortcutHint`.
- **Disclosure — collapsible sections (resolved by
  make-workspace-sections-visibly-collapsible)** — every collapsible section on
  the workspace page, the run full view and the prep chat renders through
  `src/ui/Disclosure.tsx`, the single owner of the native `<details>`/
  `<summary>` recipe (with the Health page panels and `MarkdownPanel` already
  on it). This replaces the earlier state where four different signals
  (a hidden marker, the browser's default triangle, a bare glyph button, no
  signal at all) and no rule for which sections start open coexisted.
  **The required affordance is `▸/▾`:** `▸` while closed and `▾` while open,
  in an `aria-hidden` span placed before the label — beside the status pill on
  `InstructionBadge` — with the browser marker suppressed inside the
  primitive and nowhere else. The summary opens and closes on a pointer click,
  Enter and Space, carries the standard `focus-visible:ring-2
  ring-text-primary/60` ring, and exposes its open state through the native
  `<details open>` attribute (never `aria-expanded` on the `<summary>`). A
  new collapsible section takes one of the presets (`panel`, `panelXs`,
  `markdown`, `group`, `badge`, `row`, `rowMuted`) rather than a new class
  string. **The default-open rule: primary content starts open and secondary
  detail starts closed.** Primary content is what the page is about — each
  per-task group of runs and the planning group. Secondary detail is what a
  reader opens on purpose — the cost breakdown, an instruction's delivered
  text, the handover detail sections (Full Synthesis, Prior Attempts Summary
  and each scenario's "Why" row) and a raw stream event's JSON. **Amended by
  redesign-workspace-detail-page-layout: the workspace task description moved
  from primary to secondary and now starts closed**, alongside the
  side-column "Details" disclosure introduced by the same item (see "Workspace
  detail page structure" below) — both read as background a returning visitor
  already has, not the thing the page is newly about, and the redesign's own
  goal ("what did it promise, and what did it deliver" ranks above "where is
  this workspace") is better served by the situation card and the acceptance
  checklist opening first. The one size-dependent case is the delivery panel's
  changed-files list: open at five files or fewer, closed from six
  (`CHANGED_FILES_OPEN_LIMIT` in `DeliverySummaryPanel.tsx`). The stream
  panel's list-level collapse (`isInitiallyCollapsed`) follows the step's
  running state, not this rule, and stays as it is. Three sites still author a
  native `<summary>` — `EngineConfigReadOnly.tsx`, `Project.tsx`'s
  force-complete disclosure and `ProposalPanel.tsx` — and are frozen,
  shrink-only (a fourth, `PromptOverridesDisclosure.tsx`, left the list when
  the prompt-overrides UI was removed): they keep their current look until a
  later item moves them. `src/ui/disclosure.drift.test.ts`
  fails on any `details-marker` override or new native `<summary>` outside
  `src/ui/`, so a new section cannot quietly reintroduce a hidden or native
  marker.
- **Agent catalogue and prompt reading (add-agent-setup-catalogue-and-run-detail)**
  — the repository-scoped screen at `/repos/:owner/:repo/agents`
  (`features/engine/pages/RepoAgents.tsx`) and the step-detail pop-up's
  Prompt and Config tabs read an agent's setup through two shared components
  and add no new primitive or `Disclosure` preset. **`AgentPromptViewer.tsx`**
  shows a prompt as plain read-only text in a single `<pre>` (never rendered
  markdown) with `<mark>` highlights — the current match in a solid amber
  with dark text, the rest a translucent amber over the page text colour, so
  both stay AA on the dark surface — Previous/Next match `Button`s, a polite
  `role="status"` count and a Copy `Button` with its own polite status. A
  run-time placeholder is a dashed `{{LABEL}}` chip described in a legend
  under the text, and a shared prompt block is marked inline
  (`[shared block: …]`, `select-none` so it never lands in a copy) and in the
  same legend. **`LaunchSetupList.tsx`** is the one definition of a complete
  launch setup — a `<dl>` of model, effort, turn limit, permission mode,
  stored / effective / denied tools, extra directories, session, working
  directory, timeouts, environment (names and categories only) and process
  priority — stacked label-over-value below `sm` and label-beside-value from
  `sm`, with every value wrapping (`break-words`), so nothing scrolls
  sideways on a phone. A value alan does not set reads "CLI default", never a
  blank, and a field a run did not record reads "not recorded for this run",
  never a guess. Catalogue rows are the controlled `Disclosure` `row` preset
  with a `flex-wrap` label span; the label's name, chips and
  `model · effort` text are given equal heights (`leading-6`, chips
  `h-6`) because `items-baseline` on unequal heights reads as wrapped bands to
  the desktop audit's `actionGroupFit`. The prompt viewer's heading sits in a
  `min-h-11` box for the same reason (it shares a row with `min-h-11`
  controls). The search field and the model / effort / kind filters use
  `Input` (`tapTarget`, `focusRing`) with a visible `<label htmlFor>`; the
  three filter fields carry distinct width classes (`sm:w-64`, `sm:w-48`,
  `sm:w-56`) because three identical-class siblings each holding a `<select>`
  are one repeating group whose first-text anchor (the closed select's
  `<option>`) fails `crossAxisAlignment`. The result count is a polite status
  region and the no-match state offers one clear action. Flags reuse the
  amber and slate tints of the other status badges and always spell the
  severity out ("Warning" / "Note"), never colour alone. `agents` and
  `agents-expanded` are registered in the mobile and desktop audit ratchets
  at zero counts.
- **Workspace detail page structure
  (redesign-workspace-detail-page-layout)** — `features/workspace/pages/
  Workspace.tsx` is a layout shell with five named regions, in DOM order:
  header, page banners, aside, situation, and main. `WorkspaceHeader.tsx`
  owns the header: a back link naming the workspace's own repository
  (`/repos/{engine_key}`, falling back to the listed default repository — the
  built-in fallback until the engine list loads — when `engine_key` is
  absent) and leading to that repository's dashboard, the page's sole `h1` (previously the page carried no `h1` at all — see the
  Typography section's `xl` row, revised by this item to name
  `WorkspaceHeader.tsx` as this route's `h1` owner), the status badge, the
  PR number as a link (`GithubRef`, degrading to a non-clickable span when
  the repository can't be resolved, the same degraded treatment
  `Deliverables.tsx` already uses) when `WorkspaceDetail.pr_number` is set,
  a "live since" timestamp once delivered, and the lifecycle action row
  (`WorkspaceHeaderActions.tsx`) sharing the `h1`'s flex row on wide screens
  and wrapping onto its own line below on narrow ones. No header action is
  solid-filled: Stop/Resume/Re-queue and Archive/Unarchive stay `ghost`, and
  Delete is the icon-only `destroy` control described above. Busy and error
  state for both actions is lifted into `features/workspace/hooks/
  useWorkspaceLifecycle.ts`, called once per page render and threaded to
  `WorkspaceHeader` for the controls themselves and to the page's own banner
  region for `toggleError`/`stopResumeError` (rendered as `ErrorBanner`s
  beside the live-updates warning) — so a later item can reuse the same busy
  state for the situation card's own Stop/Resume/Re-queue affordance without
  a second copy of the archive/stop/resume request logic. Background facts
  (repository, created date, elapsed and lead time, total cost with its
  on-demand breakdown, the originating conversation link, the project link,
  the persisted instruction, and the personal note) move out of the main
  column into `WorkspaceDetailsAside.tsx`, mounted exactly once. Below
  header and page banners, `Workspace.tsx` lays the rest of the page out as
  a `grid grid-cols-1 sm:grid-cols-[minmax(0,1fr)_22rem]` with two children:
  the aside column (background facts) and the main column (situation slot
  plus the existing main slot). Tailwind `order` utilities — `order-1
  sm:order-2` on the aside, `order-2 sm:order-1` on the main column — place
  the aside first below the header on narrow screens (matching its own
  collapsed-by-default "Details" framing) while keeping it the wide second
  column beside a first, wider main column; the underlying DOM order (aside
  before main) is unchanged from before this grid existed, so the CSS
  `order` is doing the responsive repositioning, not a DOM reshuffle. A new
  `hooks/useMediaQuery.ts` (backed by `useSyncExternalStore`, not a
  `useEffect` + `setState` pair, to stay clear of the
  `react-hooks/set-state-in-effect` rule) reports whether the viewport
  matches `WIDE_SCREEN_QUERY` (`(min-width: 640px)`, the same `sm:` boundary
  every other sticky/responsive rule in this doc already keys off), and the
  aside's own wrapper switches between a bare `<aside>` on a match and a
  closed `Disclosure` labelled "Details" otherwise. That wrapper swap
  changes the host element type at that tree position, which would
  ordinarily make React discard and remount whatever it contains — losing
  unsaved `WorkspaceNoteEditor` text on every resize across the breakpoint.
  `WorkspaceDetailsAside.tsx` avoids this by portaling
  `WorkspaceDetailsContent` into one detached `<div>` created once via a
  lazy ref and never replaced (so the portal's own container identity never
  changes, which is what React's reconciliation actually keys its "reuse vs.
  remount" decision on — a naive portal that retargets itself at the
  visible wrapper on every swap remounts just as eagerly as no portal at
  all), then imperatively re-parenting that div into whichever wrapper —
  `<aside>` or the disclosure's own inner div — is current, in a
  `useLayoutEffect` keyed on the wide/narrow reading. The content component
  itself is unaware of any of this and renders once regardless of
  breakpoint. `src/test/setup.ts` installs a global `matchMedia` stub
  defaulting every test to the wide reading, so existing and new tests see
  the `<aside>` by default and a test that wants the narrow reading opts in
  with its own `vi.spyOn(window, "matchMedia")` override. Page-level total
  cost now reads through `lib/format.ts`'s `formatCostUsd` (two fixed
  decimals, `tabular-nums`) rather than the ad hoc four-decimal string the
  header used to inline — the per-category breakdown rows underneath keep
  their own existing precision, since only the page-level total is in scope
  here. The three background-fact rows (repository, created, elapsed) lay
  their label and value out on one baseline (`flex items-baseline gap-2`)
  rather than stacked, so their repeated two-child shape doesn't read as a
  misaligned table row to the desktop-UI audit's cross-axis-alignment check;
  the header's own title/badges group carries a matching `sm:min-h-11` so it
  shares the lifecycle action row's height and the two stay on one visual
  row at the wide breakpoint instead of the shorter group centering a few
  pixels out of the taller row's band.
- **Situation card (redesign-workspace-detail-page-layout)** — the page's
  `situation` region (see "Workspace detail page structure" above) mounts
  exactly one `SituationCard.tsx`, the only card on the whole workspace detail
  page: nothing else the page renders carries its own rounded/bordered/
  `bg-surface-elevated` treatment. **Revised by the acceptance-checklist
  sibling subtask:** `DeliverySummaryPanel.tsx`'s root is now flat
  (`space-y-4`, no card chrome) and fetches only `api.getHandovers`, leaving a
  Retrospective + Deferred section; its former What-shipped block (PR title
  link, files changed, commits) and its separate `getDelivery` fetch are gone
  — that information now renders once, from the situation card's own
  completed-state delivery facts in `SituationFacts.tsx`.
  `hooks/situation.ts`'s `situationOf(ws, live)` is a pure,
  table-driven selector with a fixed precedence — `paused > completed >
  needs_you (awaiting_input) > escalated/failed/partial > deploy_wait
  (active with a pending deploy gate) > running (active) > archived` — so a
  workspace with, say, both a stale `terminal_outcome` and a fresh
  `pending_deploy_gate` still reads as exactly one state, never two
  conflicting signals at once. Each state carries a `headline`, a `body`,
  and at most one `primaryAction` (`"stop" | "resume" | "requeue" | null`),
  the last of which is derived by calling `useWorkspaceLifecycle.ts`'s own
  `stopResumeModeFor(ws)` rather than re-deriving the rule a second time —
  the card's own Stop/Resume/Re-queue control (`situation-stop-button` /
  `situation-resume-button` / `situation-requeue-button`, distinct testids
  from the header's `stop-button` / `resume-button` / `requeue-button` so
  both can coexist) shares the exact busy/error state
  `WorkspaceHeader.tsx` already renders from the same
  `useWorkspaceLifecycle` call, not a second copy of the request. Escalated
  and partial are the two states whose headline is required to read the
  same word `StatusBadge.tsx` renders for that status — `STATUS_LABELS`,
  `STATUS_STYLES` and `STATUS_GLYPHS` are now exported and are the single
  source both `StatusBadge` and `situationOf`'s `glyph`/`toneClass` fields
  read from, rather than a second copy of the tone/glyph tables on the
  card, so the two surfaces cannot drift: `partial` carries no glyph in
  either place (`StatusBadge.test.tsx` already pins this — text alone is
  its pairing), while `escalated` carries `↑` in both. `SituationCard`
  cannot mount a second `StatusBadge` beside its own headline —
  `StatusBadge`'s `status-${status}` testid must stay page-unique, since
  many existing tests assert on it by status value — so the headline
  itself carries the tone/glyph pairing, styled directly from `toneClass`.
  Every lower-precedence signal — the terminal outcome's
  block-class chip, the park reason and blockers, the blocking decision
  (still `decision-card`, still read-only, still no `radiogroup`), the
  autonomous-unblocker record, the "What happened" manager summary, the
  live activity feed and sticky notice, and — only in the `completed`
  state — the PR title link, changed files, commits and an "Open PR" link
  from `api.getDelivery` — renders as a flat fact block inside
  `SituationFacts.tsx`, never re-wrapped in its own card chrome. This
  replaces seven former standalone surfaces outright
  (`TerminalOutcomeBanner.tsx`, `StuckBanner.tsx`, `UnblockNotice.tsx`,
  `PendingDeployGateNotice.tsx`, and the inline `ParkReasonPanel`,
  `DecisionCard` and `ManagerSummaryPanel` blocks `Workspace.tsx` used to
  define) — each fact about why a workspace stopped now renders exactly
  once. The "What happened" summary shows a `manager-summary-written-at`
  line from `ManagerSummary.written_at` and, when `ManagerSummary.stale` is
  true, an explicit `manager-summary-stale` note plus a muted (not
  `text-text-primary`) treatment of the summary body — a stale summary is
  never presented as describing the workspace's current state. The
  summarizer's own run cost (`RunPricing`) no longer renders beside it at
  all, per the cost-breakdown consolidation this item starts. The
  `pending-deploy-gate-notice` testid survives as the `deploy_wait` state's
  own body wrapper (an `<output aria-label="Deploy verification status">`,
  carrying the implicit `status` role a plain `<p>` body doesn't get in
  every other state) — deliberately, since it is the only piece of prior
  DOM shape a Playwright spec outside this item's own file scope
  (`e2e/deploy-verify-pending-gate.spec.ts`) still depends on by testid.
  `alan/web/e2e/workspace-situation-card.spec.ts` asserts, per state, that
  exactly one `situation-card` renders and stays within the viewport's
  width.
- **Runs list, with the graph kept one tab away (redesign-workspace-detail-page-layout)** —
  `RunsSection.tsx` is what the main column's "Invocations" heading now
  mounts (in place of a bare `InvocationGraph`/`GroupedInvocations` pair),
  for both the single-task workspace and, per task, inside
  `TaskInvocationSection.tsx`. It defaults to a flat `List` view — one
  `RunRow.tsx` per run — with a `Graph` tab that mounts the existing
  `InvocationGraph.tsx` canvas unchanged; the two views share a `role="tab"`
  pair built from the existing `UnderlineTab`/`handleTablistKeyDown`
  primitives (the same tab convention `NodeDetailModal.tsx` already uses),
  rather than a bespoke pressed-button toggle, so arrow-key switching comes
  for free. The graph is not mounted at all until `Graph` is selected —
  `RunsSection`'s own `view` state gates which of the two branches renders,
  so the ReactFlow canvas and its node/edge computation never run for a
  reader who never leaves the list. A workspace with zero invocations skips
  the tabs entirely and renders `InvocationGraph` directly, since that
  component already owns the canonical "No dispatches yet." empty state
  (`invocation-graph-empty`) — `RunsSection` reuses it rather than a second
  empty-state string. Each `RunRow` shows, on its always-visible summary
  line: a plain-language description of the run (`formatTeamLabel(doc.team)`,
  the same humanizer the graph's own node card already used), the status
  badge, an instruction badge when a dispatch-instructions readback exists,
  the start time (`formatDate`), the duration (`InvocationCard.tsx`'s
  existing `renderInvocationDuration`), the cost — via `formatCostUsd`
  (two fixed decimals, `tabular-nums`), not the four-decimal
  `formatUsageRow` the graph node and full run view still use, since a list
  row is scanned at a glance rather than audited — and, when a handover
  anchors to this run, its hand-off target. A `Button` `ghost` sibling
  (`density="compact"`, not nested inside the row's own `<summary>` to avoid
  a click on the link also toggling the row) links to the full run view,
  mirroring the graph node's own "Open full view" control. Expanding the row
  (the same `Disclosure` `group` preset every other collapsible section
  uses) reveals a flat list of the run's steps — role, status, duration and
  the existing four-decimal `formatUsageRow`, reusing `WorkflowDAG.tsx`'s
  `computeDuration` rather than a third duration formatter — and, when a
  handover anchors to this run, the full `HandoverDetailPanel.tsx` inline
  as the row's "handover highlights" (reusing the shared component wholesale
  rather than a second, condensed summary of the same fields). A new
  `hooks/useHandovers.ts` carries the joined-key `useDocQuery` read
  `InvocationGraph.tsx` used to define inline (`docs/architecture/
  frontend.md`'s "Deferred" list explicitly kept this one hand-written for
  want of a second consumer — `RunsSection`/`RunRow` are that second
  consumer, so the extraction that note deferred is what this item does),
  plus the pure `resolveHandoverTargetLabel`/`findHandoverFrom` helpers both
  the graph's handover node and `RunRow` now share instead of each keeping
  its own copy of the terminal/escalation/`to_team_suggestions[0]` mapping.
  Multi-task workspaces still group runs under `TaskInvocationSection.tsx`'s
  per-task disclosures exactly as before — only what that disclosure mounts
  changed, from `InvocationGraph` to `RunsSection` — and `InterTaskDAG.tsx`'s
  task nodes gained a small status dot (reusing the `--color-status-*`
  tokens, keyed off the same done/needs-you/failed/in-progress buckets
  `TaskInvocationSection.tsx`'s own status label already classifies tasks
  into) beside the existing planning-state glyph, so the task dependency
  view reads each task's status without opening anything. `StreamPanel.tsx`'s
  event list and `HandoverDetailPanel.tsx`'s own root now cap their height
  (`max-h-96`/`max-h-[32rem]`, both `overflow-y-auto`) since both are detail
  panels opened from a run — a long stream or a handover with many sections
  no longer grows the page itself; `HandoverDetailPanel`'s inner "Full
  synthesis" region deliberately keeps no cap of its own (unchanged, still
  asserted by its own test), since the outer panel's scroll is what bounds
  it now, not a second nested scroll region.
- **Described icon-only button** — `components/DescribedIconButton.tsx` is the
  control for glyph-only closers and toggles on these pages: a `Button` with
  `iconOnly` inside a `HoverReveal`, taking a required accessible
  name (`label`), a short purpose `description` shown on hover and on
  keyboard focus, an `aria-hidden` glyph, optional `expanded`/`controls`
  state, and the standard 44px minimum target (`density="compact"` is the
  graph-canvas exception only). It defaults to the `ghost` variant; an
  optional `variant` prop (`"ghost" | "destroy"`, added by
  redesign-workspace-detail-page-layout) lets a call site opt into the quiet
  destructive treatment instead — `WorkspaceHeaderActions.tsx`'s Delete
  control is the first `destroy` consumer, replacing its earlier
  text-labelled `DescribedButton` so the header's only destructive action
  reads as a quiet icon, never a solid red button. `glyph` is typed as
  `ReactNode`, not a bare string, for the same reason: `Delete` passes
  `Button.tsx`'s existing `<TrashIcon />` rather than a new glyph character,
  reusing the icon that surface already established for delete/trash affordances. It carries the stream panel and handover
  panel close controls (`InvocationGraph.tsx`), the stream panel's
  expand/collapse toggle (`StreamPanel.tsx`), the run card's toggle
  (`InvocationCard.tsx`) and the chat `ActivityLine` toggle. A control that
  is only a bare glyph does not ship; the pop-up close in `NodeDetailModal.tsx`
  (which adds the `Esc` `ShortcutHint`) keeps its own `HoverReveal` wiring.
- **Described text button, and the workspace-detail header-action treatment
  (workspace-header-actions-on-shared-controls)** —
  `components/DescribedButton.tsx` is `DescribedIconButton`'s text-labelled
  sibling: a `ghost` or `destroy` `Button` (never `iconOnly`) inside a
  `HoverReveal`, taking a visible `label`, a required `description` shown on
  hover and on keyboard focus, a `testId`, an optional `busy` flag (forwarded
  to `Button`'s own `busy` prop, so the control stays focusable and reports
  `aria-busy` while its caller is mid-action), an optional `ariaLabel`
  override for when the accessible name must say more than the visible label
  does, and an optional `align` (`"start"` default, `"end"` to right-align
  the revealed description, matching `DescribedIconButton`'s own prop). It is
  explicitly not `SubduedActionControl`, which stays Home-only — this
  primitive never carries that control's `opacity-70` resting-vs-disabled
  treatment. `features/workspace/components/WorkspaceHeaderActions.tsx` is
  its first consumer: the workspace-detail page's header-action row (Stop,
  Resume, Re-queue, Archive/Unarchive, Delete) renders as a `<fieldset>`
  (reset to `border-0 m-0 p-0`, `aria-label="Workspace actions"` in place of
  a `<legend>`; SonarCloud's `typescript:S6819` rule requires one of
  `<details>`/`<fieldset>`/`<optgroup>`/`<address>` in place of an explicit
  `group` role, and `<fieldset>`'s native implicit role matches here), `flex
  flex-wrap` cluster of `DescribedButton`s in that order, Delete last
  and `align="end"` so its revealed description doesn't overflow the
  viewport, and carrying the `destroy` variant so it can't be mistaken for
  Archive's `ghost` one. Each action's busy state (`"Stopping…"`,
  `"Resuming…"`, `"Re-queuing…"`, `"Archiving…"`, `"Unarchiving…"`) is the
  `label` passed for that render, not a second string `DescribedButton` computes
  itself — the busy-vs-idle choice stays with the caller, matching the
  `STOP_RESUME_INFO`/`archiveInfoFor` tables in that component. The
  sticky-instruction Clear control
  (`features/workspace/components/StickyInstructionCard.tsx`) and the
  workspace-detail prep-chat link
  (`features/workspace/pages/Workspace.tsx`'s `PrepChatLink`) are further
  consumers of the described pattern: Clear keeps its visible `Clear`/
  `Clearing…` text but gets an `ariaLabel` of `"Clear instruction"`/
  `"Clearing instruction…"` so its accessible name says what it clears, plus
  a description stating the instruction stops applying to future
  automatically-routed runs and cannot be restored; the prep-chat link is
  wrapped directly in `HoverReveal` (not `DescribedButton`, since it is a
  `Link`, not a `Button`) with a static description naming what the
  conversation is, on the existing ratified body-link recipe
  (`inline-flex min-h-11 items-center text-btn-action underline`, see
  `features/home/components/NeedsYouLines.tsx`) plus the standard
  `focus-visible` ring added to it.
- **Two-tier error colour rule** — block-level error banners (page/section
  -scoped, typically `role="alert"` on a wrapping `<div>`) use
  `rounded-md bg-banner-error-tint/15 px-3 py-2` combined with
  `text-banner-error-fg` — 16 call sites across 12 files on current main
  (wider than the 11-site count the archived review measured, confirming
  the pattern has grown, not shrunk, since triage). Field-level errors
  (inline under one specific form field) use the `text-btn-destroy` token
  instead — e.g. `text-sm text-btn-destroy`. This two-tier split is a
  deliberate, consistently applied convention, not a bypass:
  `WorkspaceList.tsx`'s health-check error and `Projects.tsx`'s new-project
  error both moved from muted styling onto `text-btn-destroy` since the
  archived review, closing that half of the original finding. **Resolved
  by W1.1:** the block-level banner colour is now backed by
  `--color-banner-error-tint`/`-fg`, distinct tokens whose values are
  identical to `--color-status-danger-tint`/`-fg` — the banner's red is the
  same red as the danger state's, satisfied by construction rather than by
  coincidence.
- **Clause-list marker slot (show-which-dod-clauses-nothing-ever-checked)** —
  `features/workspace/components/ClauseList.tsx`'s `renderMarker` slot is the one shared marker
  convention for a flat clause row: a `shrink-0 font-mono uppercase`
  `<span>` carrying an `aria-label="status: <word>"` and a single glyph,
  consumed by both `RequirementsChecklist.tsx` (pass/fail/pending
  requirement clauses) and `TaskDoDPanel.tsx` (evidenced/pending
  definition-of-done clauses) — the same icon/class/label triple shape in
  both. `RequirementsChecklist` adds a third, orthogonal marker state a
  clause the acceptance gate never evaluated at all — no bound test
  reference and no coverage by any subtask definition-of-done clause
  (`alan.acceptance.unproven_clause_ids`, threaded to the browser as
  `WorkspaceDetail.unproven_clause_ids`) — renders as `⊘` in
  `text-status-warning-fg` with `aria-label="status: not mechanically
  checked"`, checked *before* the ordinary pass/fail/pending marker so a
  clause that is both unproven and still `PENDING` never reads as merely
  pending, which would wrongly imply a check is still coming. Membership is
  threaded down from `features/workspace/pages/Workspace.tsx` as a plain `Set<string>` built
  from that field; the component itself never re-derives "no test_ref and
  not covered" — that rule belongs to `alan/acceptance.py` alone, and
  duplicating it in TypeScript would drift. A section-level summary line
  above the list ("N of M requirements never mechanically checked") makes
  the gap legible without reading every row, and is omitted outright when
  nothing in the list is unproven. `--color-status-warning-fg` is reused
  rather than minting a new token — already ratified above for the
  workspace `partial` and severity `high` states — and
  `repoAccentContrast.test.ts` asserts it also clears the normal-text
  contrast minimum at this marker's small, `font-mono` size, against both
  `--color-surface` and `--color-surface-elevated`, not just the
  large-badge size it was previously verified at. The fleet-wide count of
  the same quantity (`QualityMetrics.unproven_clause_count`) renders in
  `HealthMetricsPanel.tsx`'s quality-metrics row immediately beside "Build
  success rate (all-time)", sharing that tile's `MetricCell` shape; a
  repository-scoped view (`_scopedQualityMetrics`) has no per-repository
  breakdown for this quantity yet, so it omits the key outright rather than
  reporting a misleading zero, which `_fmtIntOrDash` renders as an em dash
  — the same "not yet broken down per repository" treatment
  `format_error_rate`/`recovery_rate` already use, but via key omission
  instead of an explicit `null`, since this field is optional rather than
  required on `QualityMetrics`.
- **Force-ship variant — open product question for the owner** — `Project.tsx`'s
  "Force ship" button uses `variant="destroy"` (red). The archived review
  flagged this as low-confidence: `--color-btn-destroy` is documented
  "Destructive actions," and force-ship overrides an incompleteness guard
  rather than deleting anything, so `--color-btn-reset` (amber,
  "Reset/caution") reads closer to some readers, while "irreversible
  override" is a defensible destroy-adjacent reading to others. Left open
  rather than ratified either way — **Owner: confirm `destroy` (keep
  as-is) or switch to `reset`.** The button is unchanged pending that
  answer; this document does not take a position and this item does not
  touch `Project.tsx`.

## Structural invariants (desktop, geometry-enforced)

Two invariants are mechanically enforced at 1440×900 by the desktop-audit
gate (`docs/architecture/mobile_ui_spec.md`'s desktop-audit section) — not
aesthetic guidance, geometry a blocking CI check measures directly:

- **An action row's controls must fit their container on one line.** If a
  flex-row group of controls (column/column-reverse excluded) would fit
  within its container's width and instead wraps onto a second line, that is
  a structural defect, not a legitimate narrow-width wrap.
- **Repeating rows must agree on cross-axis alignment.** Within one row of a
  repeating grid (a table body, a list), every column's text must share the
  same vertical anchor; a column that sits noticeably higher or lower than
  its siblings in the same row is a structural defect.

Both invariants were violated in production before this gate existed: a
repository action row split across two rows, and an active-workspaces grid
whose name column disagreed with its sibling columns on vertical alignment.
**The action-row split is now discharged.** `RepoActionRow.tsx`'s
Maintenance/Hide/Settings group (line ~266) wrapped to its own line, and
further wrapped internally, because each control's full-sentence
`aria-describedby` caption rendered unconstrained-width on one line,
inflating the flex item's hypothetical size well past its button. The fix
is geometry, not content: the group gained `sm:flex-nowrap` (still
`flex-wrap` below the `sm:` breakpoint, so mobile's narrow-width wrap is
unchanged) and each control's wrapper span gained `max-w-40`, so the same
caption sentence now wraps within a fixed column instead of forcing the row
wide — all five controls' full descriptions and `role=status` notices are
unchanged. `home@standard`'s `actionGroupFit` dropped 8 → 5 and
`home@max`'s dropped 13 → 6 (see `docs/architecture/mobile_ui_spec.md`'s
desktop-audit section for the remaining tolerated-on-arrival entries this
item did not touch: `RecurringProcessesPanel`'s per-row label/chip group,
out of this item's file scope, and one `RepoSection` table primary-cell
wrap newly visible at `@max`, also out of scope). **Both discharged by
conversations-rail-and-chat-in-shell:** that item's own wider content-width
clamp (`max-w-7xl`) made `RecurringProcessesPanel`'s pre-existing per-row
`items-center` band mismatch newly visible as an `actionGroupFit` hit (the
same false-"wrapped" heuristic gap this section describes above — a shorter
status-chip sibling vertically centered against a taller text sibling), and
the fix was the same one-line `items-center` → `items-start` swap; landing
it also cleared the ALREADY-tolerated instance of the identical shape, so
`home@standard`'s `actionGroupFit` is now 0 and `home@max`'s is now 0.

The alignment invariant (`crossAxisAlignment`) is **not** addressed by this
item, and a spike traced why: `gradeCrossAxisAlignment`
(`alan/web/src/desktop-audit/audit-helpers.ts:99-108`) grades each
repeating group's rows by calling `rowMisaligned` on one repeated unit's
**own children** (via `buildRepeatingGroupCandidate`,
`alan/web/src/desktop-audit/geometry.ts:180-182`, which maps each repeated
sibling to `visibleChildren(row).map(nodeOf)` — that sibling's own
children, not its siblings) — it is never a sibling-vs-sibling comparison.
For `RepoActionRow`'s Maintenance/Hide/Settings spans specifically (three
same-signature `flex flex-col` wrappers, each stacking a button above its
caption), the "row" being graded is that one button-over-caption pair —
its two children are, by construction, at different vertical text anchors,
since one sits directly above the other. No sanctioned edit (adopting
`min-h-11`, capping caption width, or anything short of removing the
caption or un-stacking the layout) changes that: **this specific
`crossAxisAlignment` violation is unfixable within this invariant's
current semantics**, not a defect this item declined to fix.

**One further instance of the identical stacked-pair shape was discharged by
compact-repo-dashboard-geometry**, distinct from the still-open
`RepoActionRow` one above: `RepoStatusStrip.tsx`'s `StatusGroup` and
`StatusGroupChips` (the Main CI/Open PRs/Cost/Delivery time/Last
deploy/Health groups) used to stack a `text-[10px]` label above its
`AggregateChip`, the same graded-misaligned-by-construction shape this
section describes — and, being a `flex-col` pair, was likewise unfixable
while stacked. That item converted each group to a single row (label beside
chip, `items-start` so the two children's outer boxes share a top edge
regardless of their own height difference, `shrink-0` so a `flex-nowrap`
sibling can't squeeze one group's chip row into wrapping internally) —
un-stacking, exactly the escape hatch this section names as the only way
out. The now-wider row of six groups no longer fits on one line at
`RepoSection`'s content width, so `StatusGroups`' own outer container is
`flex-nowrap gap-4 overflow-x-auto` rather than `flex-wrap`: a container that
never wraps can never trip `gradeActionGroupFit`'s wrap-detection, so a
row that's genuinely too wide for one line scrolls horizontally instead of
either wrapping (a `bandsOf` hit against the heuristic's label-only width
estimate, which was never designed to price in a value chip's own width)
or being flagged. `home@standard`'s `crossAxisAlignment` count is now 8 (was
9), `home@max`'s is now 9 (was 10); `repo-rail-collapsed@standard` and
`repo-drawer-open@standard` — which render the same dashboard chrome — drop
the same way, 9 → 8. `actionGroupFit` for all four stayed at its prior value
(0, or 1 for `repo-drawer-open@standard`'s pre-existing, unrelated drawer-
header hit) — the `StatusGroups` conversion is a pure `crossAxisAlignment`
win with no `actionGroupFit` cost, once paired with the nowrap-and-scroll
container change above. The `RepoActionRow` instance is untouched by this
item and remains the reason these counts are not yet zero.

**Follow-up within the same item, after review:** the `flex-nowrap
overflow-x-auto` container above shipped keyboard-inaccessible — nothing
exposed the scroll affordance to assistive tech, so a keyboard-only user
could not reach the Cost/Delivery time groups past the fold at all. The fix
scopes the nowrap-and-scroll behaviour to `lg:` and up (`flex-wrap` by
default, `lg:flex-nowrap lg:overflow-x-auto`) rather than applying it
unconditionally: this document's own desktop-audit viewport (1440×900, both
`standard` and `max` states) sits above the `lg` breakpoint, so the
`crossAxisAlignment`/`actionGroupFit` counts recorded above are unchanged,
while a viewport (or 400% zoom) below `lg` falls back to wrapping instead of
requiring two-dimensional scrolling. The container also gained
`tabIndex={0}` and an `aria-label`, so whenever it does scroll it is itself a
focusable, labelled region — `axe-core`'s `scrollable-region-focusable` rule
/ WCAG 2.1 SC 2.1.1. The element itself is a `<fieldset>` (reset to
`border-0 m-0 p-0 min-w-0` so its UA-stylesheet box model doesn't move the
audit's measured geometry) rather than a `<div role="group">`: SonarCloud's
`typescript:S6819` rule requires one of `<details>`/`<fieldset>`/`<optgroup>`/
`<address>` in place of an explicit `group` role, and `<fieldset>` is the one
of those four whose native implicit role (`group`) actually matches this
container's semantics.

`StatusGroups`' six children are also reordered — Health, Main CI, Open
PRs, and Last deploy (the four groups that can carry an alert-worthy value)
now come first, ahead of Cost and Delivery time (a five-bucket breakdown
that is the single widest group and the least urgent to see without
scrolling) — so the groups most worth seeing without scrolling land inside
the ~878px of the ~1623px total row that renders before the scrollbar, at
this document's own measured 1440×900 viewport.

These counts are therefore measured-truth-only, not a committed target —
`actionGroupFit` is the only committed target here.

That same item also merged two previously separate-line heading rows onto
one line each — `shell/ConversationRail.tsx`'s `Conversations` heading plus
its now-inline `rail-collapse` control, and `RecurringProcessesPanel.tsx`'s
`Recurring Processes` heading plus its disclosure summary — and each pairing
hit the identical height-mismatch shape `gradeActionGroupFit`'s `bandsOf`
step is sensitive to: a short heading centered (`items-center`) against a
`min-h-11` control lands the two children's outer boxes more than
`bandEpsilonPx` (4px) apart, reading as two wrapped bands even though
nothing visually wraps. Both rows moved to `items-start` — the same escape
hatch as the `StatusGroups` fix above, applied here to a genuine false
positive rather than genuine overflow — bringing every screen that renders
the persistent rail (`build`, `explore-architecture`, `repo-engine-config`,
and every repository-scoped screen) back to `actionGroupFit: 0`.

The still-open alignment case (the active-workspaces/table-row defect that
motivated this invariant) is a **different** repeating group — table rows,
where one row's plain single-line cells sit alongside the primary cell's
multi-line stack — and remains a following cycle, out of this item's file
scope (`RepoSection.tsx`/`HomeRow.tsx`). The eventual fix there is expected
to be **adoption of an existing recipe, not invention of a new one**:
`min-h-11`'s centered-box pattern (already used, e.g., by `HomeRow.tsx`'s
`ActionCell` and the nav-link chrome recipe above) is applied unevenly
across the app's repeating grids today — the fix is almost certainly making
that application consistent, not designing a new alignment primitive. Note
that this recipe would only help genuine sibling-vs-sibling table-cell
misalignment; it cannot help the `RepoActionRow` case above, which isn't
that kind of defect.

**compact-repo-dashboard-and-shell-density discharges the `RepoActionRow`
`crossAxisAlignment` case named above as unfixable.** That passage's premise
— Maintenance/Hide/Settings rendered as three `flex-col` button-over-caption
pairs — no longer holds: the three controls moved into
`features/home/components/RepoSection.tsx`'s header row (see "Direct repo action row"
above), and their one-line consequence text is no longer a permanent second
line at all — it is `components/HoverReveal.tsx`'s hover/focus-revealed
description, `sr-only` until triggered. There is no more stacked pair to
grade, so the violation this section spent three paragraphs explaining as
structurally unfixable is gone by construction, not by a change to the
grading semantics. `features/home/components/RepoActionRow.tsx` itself now holds only the
two post-action outcome notices, unaffected by this invariant either way.

**The same item replaces `RepoStatusStrip.tsx`'s single scrolling line with
two static rows, retiring the `lg:flex-nowrap lg:overflow-x-auto` container
described above.** `StatusGroups` now renders two plain
`flex flex-wrap items-start gap-4` rows (Health/Main CI/Open PRs/Last
deploy, then Cost/Delivery time — the same field ordering the scrolling
version used) inside the unchanged `<fieldset>`, with no `tabIndex`, no
`aria-label` scroll-accommodation, and no horizontal overflow at any
viewport width: every field is visible without scrolling, so the
`scrollable-region-focusable` concern the earlier fix addressed no longer
applies (there is nothing left to scroll). The reordering that put
alert-worthy fields ahead of the wide Cost/Delivery time breakdown is now
moot too, since both rows render in full regardless of viewport width.

**The two `items-center` → `items-start` heading-row merges this document
recorded above are reversed by this same item**, not by a change to
`gradeActionGroupFit`'s heuristics: `shell/ConversationRail.tsx`'s
`Conversations` heading no longer shares a row with `rail-collapse` at all
— the collapse control moved below the conversation-list scroll region (see
"Conversation rail, collapse, and drawer" above), so there is no shared row
left to misalign. `RecurringProcessesPanel.tsx`'s `Recurring Processes`
heading likewise moved back onto its own line, off the disclosure's
`<summary>` row, so its merged summary/failing-process text (see
"Recurring-process status" in `docs/architecture/repo_status.md`) sits on a
row with nothing else to band-mismatch against. Both screens still measure
`actionGroupFit: 0` — confirmed by this item's own audit run — but for the
structural reason that the false-positive-prone pairing no longer exists,
not because `items-start` is still doing the work described above.

## Deferred follow-ups

Baseline-recording is not redesign: the divergences recorded honestly above
need call-site migrations that are out of scope for this item (no
application code changes ride it). Each becomes its own follow-up brief
through the normal pipeline:

1. Converge the 5 filled badge shapes onto one recipe — the ratified
   `rounded` pair, `ChatDraftRows.tsx`/`HealthView.tsx`'s `rounded-full ...
   font-medium` pair, `WorkspaceList.tsx`'s `dep-badge` (`rounded-full`, no
   font-weight utility), the nav count-pill (`rounded-full ...
   font-semibold`, `px-1.5`), and `TriageSweepCard.tsx`/
   `TerminalOutcomeBanner.tsx`/`Workspace.tsx`'s `rounded ... px-1.5 ...
   font-medium` chip — see "Badge / status-pill recipe." (That
   section's `ConfidenceBadge`/`confidenceBadgeClass` and the three
   bordered, unfilled chips sit outside this filled-pill convergence.)
2. Converge the pill (`WorkspaceList.tsx`) and underline
   (`components/UnderlineTab.tsx`) tab-toggle visual treatments onto one — see
   "Tab/toggle presentation." Semantics are already shared; only visual
   treatment remains.
3. ~~Add `min-w-11 gap-1.5` to the `Projects` nav link in `App.tsx` for
   byte-identical parity with `InboxBadge`/`HealthStatusBadge`~~ — **resolved
   by W1.4**: the link is retired rather than patched — see "Nav-link
   chrome."
4. Name a dedicated icon-button variant or primitive if a second in-scope
   hand-rolled instance appears beyond `ChatDraftRows.tsx`'s delete control
   — see "Icon-button affordance."
5. ~~Evaluate a `--color-banner-error`-style token to back the block-level
   error-banner recipe, the way `--color-btn-destroy` backs the
   field-level token~~ — **resolved by W1.1**: `--color-banner-error-tint`/
   `-fg` now back the block-level banner recipe, with values identical to
   `--color-status-danger-tint`/`-fg` — see "Two-tier error colour rule."
6. Resolve the Force-ship variant open question with the owner and, if
   `reset` is chosen, migrate `Project.tsx`'s button — see "Force-ship
   variant."

## How it's enforced

**Governance line:** this document has two kinds of content. *Drift-guarded*
sections state facts asserted against code by an automated test — Colour
tokens, Typography, the Interaction states section's disabled and
focus-ring facts, and each ratified recipe quoted under Component
conventions (Badge, Nav-link chrome, the two-tier error rule's block-level
and field-level classes) — a claim here can never silently desync from what
the code actually does. Everything else — the Spacing & tap targets
narrative, the un-guarded Component conventions bullets (`Button`, shared
chat primitives, the modal-key convention, empty-state copy, shell-screen
casing, the shipped/completed split, hover, and every "Known divergence" /
"open question" callout above) — is convention prose: team consensus
recorded for humans, with no automated check. If a bullet doesn't name a
guard, assume it's prose.

- **Colour drift guard** — `alan/web/src/design-system.drift.test.ts` parses
  every `--color-*` token out of `index.css` and asserts its name and value
  appear verbatim in this document's colour table, so the two can never
  desync unnoticed. The token-value pattern admits both hex (`#rrggbb`) and
  `oklch(...)` values, since the status/banner tokens copy Tailwind's oklch
  palette verbatim.
- **Measurement drift guard** — `alan/web/src/ui-spec.drift.test.ts` asserts
  the `--spacing-tap-target` CSS token equals `tapTargetMinPx` from
  `ui-spec.ts`.
- **Typography drift guard** — the same `design-system.drift.test.ts` scans
  every non-test `.ts`/`.tsx` file under `src/` and asserts the set of
  `text-*` classes in use is exactly the six-step scale documented above —
  bidirectional: a used-but-undocumented step, or a documented-but-unused
  step, both fail.
- **Interaction-state drift guard** — the same test file asserts
  `disabled:opacity-50` is the only `disabled:opacity-*` value anywhere in
  source, and that the `ring-text-primary/60` focus value appears under all
  three documented prefixes (`focus-visible:`, `focus:`, `focus-within:`)
  somewhere in source.
- **Component-recipe drift guard** — the same test file asserts each
  ratified recipe quoted above (the badge recipe, the nav-link chrome
  string, the block-level banner classes, the `text-btn-destroy` field-level
  token) is present verbatim somewhere in source. This is doc→code
  existence only, never the inverse: it catches a ratified recipe being
  deleted or silently renamed without a doc update. It does not fail on the
  recorded divergences (`ChatDraftRows.tsx`'s badges, the pill/underline
  toggle split, the `Projects` nav-link gap) — those are known debt, not
  something this guard polices, and it must stay that way so the guard
  never becomes a conformance gate on pre-existing divergence.
- **Team prompts** — the `implementation` and `implementation_fix` teams'
  system prompts, and the verification team's `diff_reviewer` prompt,
  reference this document by path so it is the normative target when
  producing or reviewing UI changes.
- **Planning prompts (advisory)** — the brainstorming worker prompt, the
  `implementation_plan` team's `frontend_analyst` prompt, and its
  `devil_advocate` UX-consistency angle also reference this document, so
  proposals ground themselves in it before implementation starts. The
  architecture team's `judge` role — the always-last synthesis step whose
  output the `implementation_plan` team reads as its input — carries the
  same lens, so its `## Final Architecture Note` states design-system
  impact advisorily even on analysis-only runs where the change-listing
  role short-circuits. Unlike the normative implementation/review prompts
  above, this reference is advisory grounding only — it prevents
  plan-vs-convention rework, never gates or enforces.
- **New-screen ratchet convention** — when a PR builds a new top-level
  route/screen, add it to `alan/web/e2e/mobile-audit/screens.ts` and seed
  its counts in `alan/web/e2e/mobile-audit/baseline.json` in the same PR
  (so a future new page enters the gate on day one instead of being exempt
  by omission).
