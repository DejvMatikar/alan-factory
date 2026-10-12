# UX/UI Authoring Evidence

Teardown appendix for `docs/UX_AUTHORING_RULES.md`. Per exemplar: what they do,
where it is documented, and which rules derive from it. **No screenshots** —
none of the fetched sources published one under a usable licence, and the
shipped-product screens were not captured this run. URLs are the citation of
record.

## Shipped ops consoles

### Linear — `linear.app`
**What they do.** A single "inverted-L" shell (persistent left sidebar + thin top
bar framing one content pane) with no secondary tab strip. Their 2024–25 redesign
explicitly *reduced* brand blue in chrome ("limiting how much chrome was used")
and restored hierarchy by adjusting neutral text/icon contrast — lighter in dark
mode — rather than adding accent colours. Their stated principle: "a redesign should
not completely disassemble the product to its atomic parts." They migrated the theme
engine from ~98 hand-set HSL variables per theme to **three inputs** (base colour,
accent colour, contrast level) computed in **LCH**, because LCH holds perceptual
uniformity across hues where HSL does not; a contrast slider (30–100) is a first-class
accessibility axis. Headings use a display cut of the typeface (Inter Display) while
body keeps the regular cut. Linear Mobile ships a **separately authored, customizable
navigation**, not a responsive collapse of the desktop sidebar.
**Derives:** rules 2, 8, 9, 43. Supports M1's "derive, don't hand-author" argument.
**URLs:** `https://linear.app/now/how-we-redesigned-the-linear-ui` · `https://linear.app/changelog/2026-01-22-customize-your-navigation-in-linear-mobile` · `https://linear.app/docs/priority`
**Do not cite:** `github.com/voltagent/awesome-design-md/blob/main/design-md/linear.app/DESIGN.md` — a third-party reverse-engineered token dump of Linear's **marketing site**, not the product. Its 4px base unit, 13-step type scale, and weight band are unverified against the app.

### Vercel — Geist
**What they do.** Pure black/white base; **every** chromatic colour is reserved for status/workflow meaning (Ship Red, Preview Pink, Develop Blue) and never used decoratively inside the product. Status colour is physically constrained to indicators of ~10px or less, never a background fill. The `StatusDot` component covers five deployment states (`QUEUED`, `BUILDING`, `READY`, `ERROR`, `CANCELED`), each with a distinct colour **plus** a sentence-cased text label **plus** a composed accessible title — colour is never the sole channel. It **pulses only in non-terminal states** and goes static at terminal ones. Three explicit anti-patterns are documented by Vercel itself: *"Don't flash the colour through every transitional state on a polling tick; only update when the readyState changes"*; *"Don't add separate spinners alongside the dot"*; *"Don't use for non-deployment statuses (use Badge instead)."* Guidance pairs the dot with a relative-time label, not a raw timestamp. No weight 700 exists in the system; Geist Mono is reserved for code and metrics.
**Derives:** rules 8, 20, 23, 29, 30, 36, 37, 49. The single densest source in this appendix.
**URLs:** `https://vercel.com/geist/status-dot` · `https://vercel.com/geist/introduction`
**Weaker:** the no-700 and mono-reservation claims come from third-party Geist breakdowns, not a fetched Vercel page.

### Sentry — `sentry.io`
**What they do.** Issue Details is a fixed three-region layout: header (error message, occurrence and user counts) → main content (stack trace, breadcrumbs, event graph) → sidebar (first/last seen, activity timeline, integrations). Sentry's own documentation calls the stack trace *"the most important piece of information"* on the page and promotes Event Highlights to the top for faster debugging — evidence above metadata. Dark mode was built by **semantic alias re-binding**, not a forked stylesheet: `textColor` resolves to `gray500` in light and `white` in dark, one name with two bound value-sets. Severity taxonomy runs six named levels (fatal/error/warning/info/debug/sample).
**Derives:** rules 4, 5 (partly). Its 6-level palette is the scoping counterexample that forced rule 17/18's split.
**Anti-precedent:** Sentry's project dashboard grid is reported hard-capped at 3 columns regardless of viewport width — a live complaint, not doctrine, and the opposite of Datadog's reflow rules.
**URLs:** `https://docs.sentry.io/product/issues/issue-details/` · `https://blog.sentry.io/building-dark-mode/` · `https://github.com/getsentry/sentry/issues/44029` · `https://docs.sentry.io/product/dashboards/sentry-dashboards/mobile/` *(this last is a dashboard **about** mobile apps, not a mobile-usable dashboard — do not conflate)*

### Datadog
**What they do.** Widgets must live inside a thematically-named group, never loose on the background. Shipped integration dashboards have a mandatory section order: About (logo + description) → Overview (service checks, key metrics, monitor summary) → per-feature groups → Logs last. Minimum widget widths are specified in **grid columns per widget type**, not global pixels (timeseries ≥4 columns or it is "squashed"; stream widgets ≥6). Responsive QA runs at five fixed checkpoints (1280/1440/1680/1920/2560px). The categorical palette is **capped at 6**; beyond 6 series, colours repeat rather than the palette growing. Two palettes ship — "Classic" (colour-blind support) and "Semantic/Consistent" (auto-maps `error`→red but has **no** accessibility support) — with Datadog documenting the tradeoff rather than solving it. Naming case is part of the system: group titles Title Case, widget titles sentence case, graph titles lead with the most important word. Query-value widgets must never be bare numbers — a background timeseries is required. Adjacent graphs in a group must share x-axis alignment and legend visibility. "High Density Mode" auto-splits the 12-column grid into 2×12 above a width threshold, with guidance to keep the halves near-symmetrical.
**Derives:** rules 5, 6, 11, 25, 47. Its 6-colour cap anchors rule 18's ceiling.
**URLs:** `https://datadoghq.dev/integrations-core/guidelines/dashboards/` · `https://docs.datadoghq.com/dashboards/guide/widget_colors/` · `https://github.com/DataDog/integrations-core/blob/master/docs/developer/guidelines/dashboards.md`

### Grafana
**What they do.** Alert state uses exactly three values — red firing, yellow pending, green ok. The state machine mandates a debounce: Pending (condition met, inside the pending period) → Firing (sustained), with docs explicit that *"it will take at least two evaluation cycles before an alert is fired."* Colour-to-meaning binding is dashboard-wide: *"using the same colours for the same type of information shown on different panels makes it easier to understand."* Alert state *transitions* are annotated directly on the time-series panel, not only reported as a current-state badge elsewhere.
**Derives:** rules 17, 24, 25, 39.
**URLs:** `https://grafana.com/docs/grafana/latest/alerting/fundamentals/alert-rule-evaluation/` · `https://grafana.com/docs/grafana/latest/alerting/monitor-status/view-alert-state/` · `https://grafana.com/docs/grafana/latest/visualizations/panels-visualizations/`

### GitHub Actions
**What they do.** Two deliberately separate information architectures for the same data: a desktop log viewer (full run graph + single virtualized scrolling list, chosen over pagination for thousands-of-lines performance) and a purpose-built mobile flow (PR → Checks summary → per-check detail → single-tap log). Opening a *currently streaming* job immediately backfills the prior **1,000 lines**, called out as a deliberate fix for giving "immediate context into the run's progress and status." Colour is used specifically to let users "quickly locate the item that needs attention," with a stated intent to be "mindful" not to overuse it. Three status colours (green success, red failure, yellow covering both queued and in-progress). Logs render monospace with ANSI/8-bit/24-bit pass-through. Line-level deep links make a failure shareable at line granularity. Mobile exposes cancel and re-run inline "throughout these new screens," with push notifications as the default attention mechanism ("we don't expect you to be glued to the screen") and an opt-in failed-runs-only filter.
**Derives:** rules 17, 21, 30, 35, 38, 43, 44.
**URLs:** `https://github.blog/news-insights/product-news/a-better-logs-experience-with-github-actions/` · `https://github.blog/news-insights/product-news/bringing-github-actions-to-github-mobile/`

### Railway and Height — **cut, with reason**
Railway's engineering blog and changelog describe features, not visual decisions; no public colour, typography, or density documentation exists. Height sunset its collaborative-PM product in 2025 to pivot to an AI agent tool and never publicly documented its design system. `prior_art_finder` dropped both to single-line mentions and reallocated the effort to the five above — this is the brief's own "cutting the exemplar list is the cheapest way to shorten the run" allowance, exercised deliberately, not an omission.
**URL checked:** `https://blog.railway.com/p/building-railway-on-railway` (no design content).

## Density-oriented design systems

### IBM Carbon
Four explicit data-table row sizes (compact 24px / short 32px / medium 40–48px / tall 64px), with tall justified only when "data is expected to have 2 lines of content." Spacing scale `spacing-01`–`13`: 2, 4, 8, 12, 16, 24, 32, 40, 48, 64, 80, 96, 160px. Toolbar size is *paired* to row density (small toolbar with short/compact rows, large with normal/tall). Severity is encoded as **colour + shape + symbol** — three redundant channels. Skeletons "should only appear for a few seconds," use motion "to convey that the page is not stuck loading," and have an explicit exclusion list: never toasts, overflow menus, dropdown items, modals, or loaders. Type scale is formula-generated from a 12px seed, with parallel Productive (denser, fixed) and Expressive (larger, responsive) sets.
**Derives:** rules 12, 16, 19, 32, 48. **Quarantined:** M5 (literal px), M8 (a 6th hue).
**URLs:** `https://v10.carbondesignsystem.com/guidelines/spacing/overview/` · `https://v10.carbondesignsystem.com/components/data-table/usage/`

### Atlassian Design System
Spacing scale `space.0`–`space.1000` on an 8px base: 0, 2, 4, 6, 8, 12, 16, 20, 24, 32, 40, 48, 64, 80px — with a clear split between inline/intra-component values (2–8px) and layout values (32–80px). Three named density modes (Compact/Cozy/Comfortable), distinguished by gutter rather than row height. Ten type steps: seven heading (32/28/24/20/16/14/12) **all Bold**, plus three body, on a minor-third ratio. Empty-state copy is 1–2 sentences, sentence case, imperative CTA, and structurally distinguishes never-tried "blank slate" from completed-task "empty state" from error message.
**Derives:** rules 13, 28, 33.
**URL:** `https://atlassian.design/foundations/spacing`

### Shopify Polaris
Density is **binary**: high (index pages, tables — scan and compare) vs low (focused editing, e.g. a product page — single task). Explicit rules: never mix densities within one card; no vertical dividers in low-density/card contexts, where they are "confusing and hard to understand"; high density substitutes surface-colour zoning for whitespace. Five Badge tones (info/success/warning/critical/attention), where "attention" deliberately reuses warning's yellow rather than getting a unique hue. `EmptyState` is full-page only, never per-cell. Skeletons must mimic the layout they replace. Type scale is major-third (1.2), rounded to 4px multiples.
**Derives:** rules 1, 3, 14, 15, 18, 31, 34, 45.
**URL:** `https://polaris-react.shopify.com/design/layout/density`

### GitHub Primer
Eight type steps (`f00`–`f6`), with the scale explicitly stopping at `f3` (20px) below normal weight "for legibility." Five status colours (success/attention/severe/danger/done). Skeletons avoid interactive elements — "instead hiding them until loading is complete." Blankslate graphics must **not** be playful or delight-oriented when representing an error.
**Derives:** rules 18, 26, 27, 32, 33.
**Note:** Primer's spacing and typography pages are JS-rendered and returned only titles via fetch; the values above come from secondary summaries.

## Craft and standards references

**WAI-ARIA APG** — the four patterns alan uses, verbatim contracts:
- *Tablist:* `role="tablist"`/`"tab"`/`"tabpanel"`; `aria-selected` on **every** tab; `aria-controls` on each tab; `aria-labelledby` on each panel; Tab enters/exits at the active tab; Left/Right arrows move focus with wraparound; Space/Enter activates when activation is not automatic. → rule 41
- *Disclosure:* `role="button"` + `aria-expanded` are the **only** mandatory requirements; Enter or Space toggles. → rule 41
- *Dialog:* `role="dialog"` + `aria-modal="true"` + `aria-labelledby` (or `aria-label`); focus moves inside on open and returns to the invoker on close; Tab/Shift+Tab wrap within the dialog and never escape; Escape closes; **high-risk dialogs place initial focus on the least-destructive option**. → rule 42
- *Live regions:* `polite` queues without interrupting — correct for streaming; `assertive` interrupts and can clear the speech queue — for genuinely time-critical alerts only. Best practice: at most one polite and one assertive region per page, present in the DOM at load, since some screen readers only announce into regions that existed then. → rule 40
**URLs:** `https://www.w3.org/WAI/ARIA/apg/patterns/tabs/` · `https://www.w3.org/WAI/ARIA/apg/patterns/disclosure/` · `https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/`

**WCAG 2.1 AA** — 1.4.3 Contrast: normal text ≥4.5:1, large text (≥24px regular / ~18.67px bold) ≥3:1. 1.4.11 Non-Text Contrast: UI component boundaries, focus indicators, and meaningful icons/graphics ≥3:1 against adjacent colours. **The dark-theme consequence:** a status chip must clear 3:1 for its *fill or edge against the page background* as a check **separate from** the 4.5:1 for its label text — a hue that reads fine for its label can still fail 1.4.11. **Correction of record:** WCAG 2.1 AA contains **no** target-size criterion; 2.5.5 (44px) is AAA under 2.1, and 2.5.8 (24px) only reached AA in WCAG **2.2**. alan's 44px floor is Apple HIG-sourced and exceeds both.

**Refactoring UI** — grey-on-colour rule (rule 10). *Sourced from third-party summaries; no fetchable official text exists.*

## Empirical grounding

| Finding | Rules it grounds |
|---|---|
| 85–99% of clinical alarm signals do not require intervention (Joint Commission, via alarm-fatigue reviews); only 5–13% of ICU alarms are judged actionable in some cohorts | 22, 21 |
| Alarm burden is concentrated, not uniform — roughly a quarter of monitored patients generate about two-thirds of alarms | 21, 22 |
| Categorical colour perception tops out around 10–12 nameable categories, but *task performance* in colour-coded visualizations stays reliably high only to ~7 and degrades beyond (Healey et al., CHI 2023) | 18, 25 |
| Colour-vision deficiency affects ~8% of men, ~0.5% of women (via NN/g) | 19, 20 |
| Preattentive hierarchy: length and 2D position are judged precisely; hue is preattentive only for detecting *that a difference exists*, is unsuitable for quantity, and works best as secondary reinforcement layered on a positional encoding (Few, via NN/g) | 19, 20, 25 |
| ANSI Z535.1, harmonized with ISO 3864-4 since 2017: four severity tiers — red danger (death/serious injury if unaddressed), orange warning (could cause), yellow caution (minor/moderate), green safe | 17, 21, 25 |
**URL:** `https://www.nngroup.com/articles/dashboards-preattentive/`

---

## Low-Confidence Findings

Flagged by the `verifier` and by the source agents' own self-reporting. Nothing below is fabricated; all of it is thinner evidence than the rules it supports would suggest if read alone.

**Motion is under-resourced relative to the other seven categories.** `prior_art_finder` found essentially no motion documentation for five of six exemplars; the only genuinely motion-specific finding in the whole research set is Carbon's skeleton-motion note. Rule 48 rests on that single source. Rule 49 extrapolates Vercel's no-duplicate-spinner rule into a general one-animation-per-viewport claim — a reasonable but unsourced generalization. Rule 50 (`prefers-reduced-motion`) is universal platform convention rather than a fetched design-system doc. **These three are the least-evidenced rules in the set.** Their saving grace is that alan has zero motion today, so they cost nothing to adopt and prevent rather than correct.

**Rule 7 ("one primary action") has no fetched supporting quote.** It is convergence-by-absence — Carbon, Polaris, and Primer each ship a primary/secondary/tertiary hierarchy and none defines a two-primaries state — and `design_system_comparator`'s wording closely echoes the brief's own illustrative example rather than an independently sourced finding. Directionally safe and widely believed; weakly cited. Do not present it as documented doctrine.

**Rule 29 (semibold ceiling) is third-party sourced.** Vercel's "no weight 700" comes from a Geist breakdown, not Geist's own docs. The corroborating Linear weight band comes from a reverse-engineered dump of Linear's **marketing site**, not the product. `prior_art_finder` flagged this row (its convergence #13) as one-strong-source-plus-uncertain-echo.

**Unfetched specifics that should not be quoted as primary:** Sentry's severity hex values (no page confirmed them); GitHub Actions' three status colours (cross-referenced search, no official colour-spec page); Linear's spacing and type-scale numbers (marketing-site reverse engineering); Primer's exact spacing scale, Carbon's medium-row value (40 vs 48 disputed across major versions), Atlassian's type numbers, and Material 3's typography tokens — all JS-rendered pages that returned only titles, so those figures rest on secondary summaries.

**No design system in the retrieved set published dark-mode contrast numbers.** alan's 4.5:1 / 3:1 floors come straight from WCAG, not from a peer system's published dark-theme values. There is no exemplar precedent for what a *dark-specific* contrast target should be.

**Two philosophical divergences left unresolved — deliberately, with no rule written either way.** (1) Datadog auto-restructures a dashboard into a 2×12 grid above a width threshold with no user control, while Linear and Grafana treat density as an authored, tested dial per platform. These are opposite philosophies and cannot both be the rule. (2) Density-mode taxonomy has no consensus: Polaris 2 binary modes, Atlassian 3 named, Carbon 4 row variants, Primer none. Rule 3 uses Polaris's binary as the most falsifiable option, not as the industry consensus.

**Sentry's 3-column dashboard cap is a community complaint (GH #44029), not Sentry doctrine.** It is cited as an anti-pattern, which is what a bug report can support; it is not evidence of a Sentry design decision.

**Repo grounding is grep-scoped, not exhaustive.** `feasibility_analyst` did not fully read `Project.tsx`, `Inbox.tsx`, or `HealthView.tsx` (1000+ lines each), sampling by targeted grep instead — per the brief's explicit exclusion of an exhaustive component inventory. The claim "zero motion in front-door files" rests on a `transition|animate-|duration-|ease-` grep; a rare usage outside that pattern set could exist. Home's list-vs-card-stack shape was never established, so rule 1's application to `WorkspaceList.tsx` is a check to run, not a violation to claim. The three findings the `verifier` independently spot-checked — `StatusBadge.tsx:19,21`, `screens.ts`'s missing `/projects/:projectId`, and all 24 `baseline.json` counts — matched verbatim.

**No agent produced the deferred-follow-up verdicts or the merged migration list.** Both were synthesis-stage work, done in `docs/UX_AUTHORING_RULES.md`. The follow-up verdicts in particular are judgement calls derived from the rules, not findings any specialist reported — follow-up #6 in particular is explicitly the owner's ratification, and #5's direction commits to a token migration that has not been costed beyond the M3 row.
