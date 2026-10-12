# ui_review — on-demand UI review team and the supervised refinement loop

## Overview

`ui_review` (`alan/teams/ui_review.py`, U4.1 / #724) is a dispatchable `OrchestratedTeam` that
reviews the *existing* web UI against the design system and returns a ranked, actionable inventory
of UI debt plus ready-to-dispatch improvement briefs. It reviews; it never edits. It is modeled on
brainstorming's shape — a perspective catalog, an orchestrator that picks 2–4 relevant
perspectives, a synthesizer — **minus** brainstorming's output-length self-cap, because here the
inventory is the product.

Perspectives (`PERSPECTIVE_ROLES`, exact code identifiers): `design_system_reviewer`
(token/primitive/convention conformance vs `docs/DESIGN_SYSTEM.md` + `ui-spec.ts`; where the
system is silent it reports "no norm exists, observed N variants" as evidence, never a violation),
`ux_reviewer` (flows, affordances, terminology, empty/loading/error states — grounded in component
source, since static capture cannot show states), `accessibility_reviewer` (WCAG-AA prose lens;
reads mobile-audit and axe artifacts when available), `consistency_reviewer` (cross-screen drift:
spacing, casing, variants, hand-rolled UI that should be a shared primitive).

## Dispatch

Two front doors, both human-initiated (never scheduled, never a health check):

- **Chat:** the `ui_review` explore-chat consultant (`/explore/ui_review`, U4.2 / #725) — scope the
  review conversationally (whole app vs. a screen vs. a flow), then hand off to the dispatch.
- **API:** create a workspace whose description is the review scope, then
  `POST /api/workspaces/{id}/tasks/{id}/dispatch {"team": "ui_review"}`.

Registration is fail-closed and complete across `TEAMS` / `TEAM_ROLE_SUMMARIES` /
`_TEAM_MIN_POSTURE` (posture `propose`+) / the generated taxonomy table; the team is both
manually- and initial-dispatchable. Grounding-run discipline: at most ONE designated worker
(default `accessibility_reviewer`) best-effort runs `capture:screens` and reads the existing
`alan/web/e2e/mobile-audit/` artifacts; **no worker ever invokes the live `audit:mobile` runner**
(fixed port/state root — concurrent live runs collide by construction; reading existing output is
always the fallback). All other workers Read the designated worker's artifacts or degrade to
source review.

**This is engine-conditional, not universal.** The paragraph above describes the `alan`
engine, where a real `alan/web/` checkout exists to capture from. On the `ios` engine preset,
`alan/teams/_orchestrated.py` threads a config-derived block (`native_capture_block`, `alan/teams/
_native_capture.py`) into every dispatched worker's prompt: on that preset, **every** perspective —
including whichever one would otherwise hold grounding duty — receives a structural-absence
statement instead of the capture instruction, because orchestrated worker nodes run with no working
directory and this engine's `ui_review` therefore executes inside alan's own shared production
checkout, not a second engine's checkout. **Known, unfixed residue**, named here rather than fixed: on the
`ios` preset `_GROUNDING_RULE` and `_PERSPECTIVE_CATALOG` still instruct perspectives to read
`alan/web/e2e/mobile-audit/`, `docs/DESIGN_SYSTEM.md`, and `alan/web/src/` — none of which exist in
that execution context. Only the capture instruction itself is engine-conditional today.

## Output contract

The synthesizer emits a severity × effort findings inventory (`docs/architecture/ui_backend_gaps.md`
format precedent) plus a `## Proposed briefs` section, each brief in the six-field shape
(Goal / DoD / Constraints / Out-of-scope / References / Suggested team). The full output persists
**untruncated** in the handover's `full_synthesis` (stored whole on disk; only consumers slice) and
is read from the workspace UI (node result / handover detail). Any FORWARD-routed consumer would
see only `full_synthesis[:2500]` — therefore briefs are consumed by a human from the UI and
dispatched as **fresh** workspaces; the ui_review handover is never routed onward as the carrier
(`to_team_suggestions=()` — the router pauses for the human, by design).

## The supervised refinement loop (roadmap U5.2 shape)

The loop that turns a review into merged UI improvement, human-triaged at the acceptance step:

1. **Dispatch** — the owner starts a `ui_review` run (chat or API) with an explicit scope.
2. **Triage** — the owner reads the inventory from the workspace UI and accepts/declines briefs.
   Brief acceptance is the human step; findings are never auto-dispatched.
3. **Integrate** — accepted briefs become roadmap items (first run: U5.2a–U5.2e, spliced in a
   owner-reviewed docs PR) and the inventory is archived verbatim under `docs/audits/`. Items cite findings by
   NAME in the archive; line anchors drift as fixes land and are re-verified at plan time.
4. **Execute** — the accepted items ship through the normal pipeline (plan → implement → verify →
   merge), which now enforces the design system on the way back in (U1 lenses). Items sharing a
   hot file are wave-partitioned, not blindly parallelized.
5. **Ratchet down** — where a merged fix eliminates tolerated violations on a ratchet-measured
   dimension, a follow-up PR lowers `alan/web/e2e/mobile-audit/baseline.json` to the freshly
   MEASURED counts (never predicted). Tightening is the only allowed direction
   (`assertBaselineNotRaised` is untouched); this is where the loop's roadmap item flips done.

## Boundaries (load-bearing)

- **Advisory-never-gate is absolute.** No ui_review output feeds `DIFF_FINDINGS_CRITICAL`,
  `VERDICT`, or any gate. Severity/effort labels exist for human ranking only. This applies to
  ui_review itself regardless of how many blocking channels exist elsewhere — see below.
- **Read-only.** Base tools only; no write/edit capability; no elevated tool grant.
- **The mobile-audit ratchet is no longer the only blocking UI surface (deliberate overturn,
  2026-08-18).** A second blocking channel now exists: the desktop layout ratchet
  (`alan/web/e2e/desktop-audit/`, `docs/architecture/mobile_ui_spec.md`'s desktop-audit section),
  which measures action-group line-fit and repeating-group cross-axis alignment at 1440×900 —
  geometry only, never aesthetic or taste judgment. Both ratchets tighten only from
  human-accepted, pipeline-verified fixes; neither is fed by ui_review's advisory output. The
  accepted consequence: the same underlying defect can now be reported twice, in two vocabularies
  — an advisory severity label from ui_review, and a hard baseline count from the desktop gate —
  and "nothing is paid for twice" is deliberately traded for determinism, because the mobile-audit
  ratchet alone provably missed defects visible only at desktop width. ui_review itself remains
  advisory-never-gate and read-only, unchanged by this — it prevents and inventories; it is the
  gates that now number two instead of one.
