# Connect-repository wizard — shell, composition, credentials (W2.1)

The connect-repository wizard is a four-step, resumable flow —
**identify → configure → make dispatchable → posture** — that walks a user from "I
have a GitHub repo" to "alan can dispatch work against it." W2.1 builds the shell
every step shares, the Settings credentials surface that makes registration
possible at all, and step 1 (identify) end-to-end. Steps 2-4 are **not** built by
this item; only the primitives they consume are.

> Setting up your own copy of alan? Start from `./setup.sh` — see [setup.md](setup.md).

This document is the mandatory design section the roadmap harvests into Appendix
D — W2.2/W2.3/W2.4 plan against it. Two things are load-bearing enough to quote
verbatim rather than paraphrase; both are reproduced below exactly as specified.

## (a) Composition — forced order

This order is forced by what already exists at HEAD, not chosen by this item:

> credential record created in Settings → `POST /api/engines` at step 1 (preset
> baseline via `engine_presets.build_config`) → `POST .../config-proposal` →
> step 2 `PATCH` → scaffold/protect → posture.

The forcing mechanism is `_require_credential_record`
(`alan/routes/engines.py:89`), called at **both** `POST /api/engines` (`:118`,
create) **and** `PATCH /api/engines/{owner}/{repo}` (`:225`, update) — a
`ForgeConfig`-bearing body 422s with `no secret record for '<owner>/<repo>:transport'`
unless a secrets-store record with that exact name already exists
(`secrets_store.record_name(repo.slug, TRANSPORT_PURPOSE)`). There is no delivered
UI that creates that record before this item — the Settings Credentials section
this item ships is the **only** surface that does — so the credential CRUD is on
step 1's critical path, not adjacent to it: a step 1 that deep-links to a
not-yet-built Settings section cannot complete, which is exactly the
half-working state the build brief's shape constraint forbids. See "Decisions
inherited" in the handover for why this ruled out the counter-case's PR split.

### Where the pool and the project come into existence

**The pool** registers with the engine at step 1, not later — `POST /api/engines`
calls `dispatcher.register_pool(body)` synchronously inside the route handler
(`alan/routes/engines.py:127-128`) whenever a dispatcher is live, the same call
`onboarding.create_attempt`'s own step 5 makes (see `onboarding.md`). Registration
is a synchronous dict-insert; the pool's first warm-up still happens
asynchronously afterward (`SlotPool` warms on the dispatcher's background
thread) — "registered" and "warm" are different moments, but *registered* dates
from step 1's `POST /api/engines` call, not from any later step.

**The project** is created lazily at step 4 (make-dispatchable/posture — whichever
PR ships it), titled from the repo name. **No wireframe step in this flow
collects a title** — that is a deliberate divergence from the atomic
`POST /api/onboarding` path, whose `OnboardingCreateBody.title` field this wizard
does not reuse (see "Not a second orchestration path" below). Until step 4 runs,
there is no `Project` bound to the engine at all; `GET /api/projects` returns
nothing for a repo that has only cleared step 1.

### What abandoning tears down, stage by stage

Abandoning mid-flow must use **delivered teardown legs only** — this item
implements no rollback code of its own, and calls `DELETE /api/engines` from no
unmount hook. The table names, for each stage a user might abandon at, which
delivered leg *could* tear it down and whether W2.1 actually invokes it.

| Stage reached | What exists | Delivered teardown leg | W2.1 invokes it? |
|---|---|---|---|
| Before Settings credential is created | Nothing | n/a | n/a — abandoning is a no-op |
| Credential created, engine not yet registered | One secrets-store record (`owner/repo:transport`) | `DELETE /api/secrets {engine, purpose}` | **No — deliberately retained.** See below. |
| Step 1 action fired: engine + pool registered | `EngineConfig` in the registry, a `SlotPool` if a dispatcher is live | `DELETE /api/engines/{owner}/{repo}` (disposes the pool via the dispatcher, then unregisters; 409 if busy or referenced by a non-terminal workspace) | No — this item builds no abandon/cancel handler that calls it |
| Step 2 PATCH applied (out of scope) | Same engine, updated config | Same `DELETE` leg — PATCH mutates in place, it mints no new resource | n/a (step 2 not built here) |
| Scaffold/protect run (out of scope) | A real scaffold PR + branch-protection settings on the live GitHub repo | **None** — scaffold/protect are one-way actions against GitHub itself (see `repo_scaffold.md`); deleting the engine does not close the PR or revert protection | n/a (step 3 not built here) |
| Posture raised (W2.4) | Posture on the engine, changed via the same confirm-gated `POST .../posture` route the rest of the roadmap treats as canonical, invoked from inside step 4's dialog | Same `DELETE` leg (posture has no separate teardown — it dies with the engine) | No — W2.4 builds no abandon/cancel handler either, and adds no second posture write path |
| Project created (W2.4) | A `Project`, titled from the repo, created by step 4's Finish action — bound to the engine only when the last preflight read `pass`; otherwise created unbound, with an honest reason shown on arrival | `DELETE /api/projects/{id}` | No — W2.4 adds no delete/abandon call; the project is a forward-only artifact of finishing the wizard |

**W2.4 confirmation: no new abandon path.** Posture still dies with the engine — the only leg that ever tears it down is the same `DELETE /api/engines/{owner}/{repo}` step 1 already documented above. Step 4 adds a *write* path (the confirm-gated posture route) and a *create* path (the project), neither of which this item wires into any abandon/cancel flow.

**The retained credential is deliberate, and it is also a live dependency, not
just a courtesy.** `_require_credential_record` runs again at `PATCH
.../{owner}/{repo}` (`:225`) — so if a wizard "helpfully" deleted the credential
on abandon, a *different*, already-registered engine that resolves its transport
secret from that same repo-slug-keyed record would be stranded the next time its
config is saved. AC4 pins this as deliberate, not an oversight: abandoning step 1
leaves the credential behind on purpose.

### Route shape and re-entry

Two routes, not one: `/connect` (fresh-start — no identity yet, the user types
`owner`/`repo`) and `/connect/:owner/:repo` (per-repo resumable — identity is
already known from the URL). Both render the same `ConnectWizard` shell; the
only difference is whether `useParams` supplies an identity or the step-1 form
collects one.

**Re-entry recovers state from server reads, never from client storage.**
`useConnectFlowState` holds no wizard-progress state in `localStorage` or
anywhere else client-side; every field it exposes is derived, on mount and on
every `"engines"` change-event tick, from ungated GETs:

- identity — from the URL (resumable route) or the step-1 form (fresh route);
- credential status — `GET /api/secrets`, checking whether
  `record_name(slug, TRANSPORT_PURPOSE)` appears in the list;
- registration + reachability — `GET /api/engines/{owner}/{repo}` (404 ⇒ not yet
  registered, not an error) and, once registered, `GET
  /api/engines/{owner}/{repo}/onboarding`'s `push_repository` finding (422 with
  no forge configured is unreachable here, since step 1 always registers with a
  forge — see the preset-config endpoint below).

Landing on `/connect/acme/widget` after closing the tab mid-flow re-derives
exactly the same three checklist rows a fresh page load would, from the same
three reads — there is nothing else to "resume."

**Two repos mid-setup are independent by construction, not by special-casing.**
State is keyed entirely by the `owner/repo` URL segment and derived from reads
scoped to that repo; a tab open on `/connect/acme/widget` and another on
`/connect/other/thing` never share client state, and the Settings Credentials
list simply enumerates every stored record regardless of which repo's wizard
tab happens to be open. Nothing in this design assumes only one repo is ever
mid-setup at a time.

### Repository identity input — a URL or a slug, one field, never a second path

Both places a user establishes a repository identity — step 1's identity form and
the Settings Credentials form (`CredentialsSection.tsx`) — take a single text
field and resolve it with `alan/web/src/lib/repoUrlIdentity.ts`'s
`parseRepoIdentity(input)`, a pure frontend module with no backend counterpart and
no resolve endpoint: a full `https://github.com/owner/repo` URL (`.git` suffix,
trailing slash, `www.` subdomain, mixed-case host, and a deep link to a file or PR
all collapse to the same `owner/repo`), an `ssh://` or scp-style
`git@github.com:owner/repo` address, or a bare `owner/repo` slug all resolve to the
same `{ owner, repo }` pair. A host other than `github.com`/`www.github.com` is
refused as `not_github`, never silently coerced — GitHub is the only forge this
flow (or the rest of alan) supports. Owner/repo segments are returned **verbatim,
never lower-cased**: the slug is a path component and `ENGINE_SEGMENT_RE` is
case-permissive, so folding case would change identity.

**Parsing is a convenience in front of validation, never a replacement for it.**
Both call sites still run every parsed `{owner, repo}` through
`ENGINE_SEGMENT_RE.test(...)` before treating it as real — a URL whose path
segments happen to contain characters outside the safe charset (`api.ts`'s
`assertSafeSegment` would throw building the request URL) is still rejected inline,
with the same `SEGMENT_HELP_TEXT` copy step 1 has used since W2.1. Nothing about
this item changes what identity is allowed to reach `POST /api/engines` or
`POST /api/secrets` — it only changes how a user gets there from a pasted string.

**The resolved identity is echoed before the user commits**, and that echo is
`role="status" aria-live="polite"` on both surfaces (`connect-identity-resolved` on
step 1, `credential-preview` on the Credentials form) — a screen reader announces
the resolved `owner/repo` as it updates, not just a sighted user watching the text
change.

**The advanced engine-configuration editor (`EngineConfigEditor.tsx`) is
deliberately out of scope and keeps its split owner/repo fields.** Its fields
mirror the underlying `RepoIdentity` being edited directly, field-for-field — a
URL-paste convenience layer would be an inconsistency with the rest of that
editor's shape, not a simplification of it.

### Record identity vs. the env-fallback field — the two sentences AC3/AC8 pin

> The record identity the user creates and the wizard displays is
> `owner/repo:transport`. `{OWNER}_{REPO}_GH_TOKEN` is the engine config's
> env-fallback field, consumed only at git-transport time, and is never
> presented as the thing the user creates — nor "fixed" toward the wireframe's
> `ALAN_GH_TOKEN` frame.

Concretely: `secrets_store.record_name(engine, purpose)` (`alan/secrets_store.py:55`)
returns `f"{engine}:{purpose}"`, where `engine` carries the **repo slug**
(`owner/repo`), never a single shared name. `engine_presets._credential_env_name`
(`alan/engine_presets.py:87`) independently synthesizes
`{OWNER}_{REPO}_GH_TOKEN` and writes it into `ForgeConfig.credential_env_name` —
this is an `os.environ` **fallback** `secrets_store.resolve_secret` only
consults if the store has no record for the slug (`alan/secrets_store.py:235-245`).
Because the Credentials section always writes a store record before step 1's
`POST /api/engines` can succeed (the credential gate above), the env-fallback
path is realistically never taken for a wizard-registered engine — it exists
for direct API/YAML-authored engines, not this flow. The wizard therefore never
renders `{OWNER}_{REPO}_GH_TOKEN` as something the user "sets"; it renders
`owner/repo:transport` as the record identity, in the Credentials list and in
step 1's checklist alike. The wireframes' `ALAN_GH_TOKEN` framing (a single
global token name) does not match either identity and must not survive into
shipped copy.

**AC8 — the wireframes' "a token never passes through the browser" claim is
false at HEAD and must not survive into shipped copy either.** `POST
/api/secrets`'s `SecretCreateBody.value` field is exactly the token, submitted
from the Credentials form over the same HTTPS connection as every other
mutation. What *is* true, and is what the UI should say instead: the value is
write-only by construction — `SecretRecordResponse` (`alan/routes/secrets.py:28`)
has no `value` field at all, so the wizard and the Credentials list can never
render or hold a submitted value in state, before or after the round trip. The
Credentials form clears its input on submit before the request resolves, so no
value survives even transiently once the network call is in flight.

### Control token vs. repository credential — AC6's two different secrets

A missing or mismatched control token and a missing repository credential are
**different secrets with different jobs**, and the copy must not conflate them:

- The **control token** (`ALAN_CONTROL_TOKEN` / `X-Alan-Control-Token`) is one
  bearer secret, entered once in Settings, that gates every mutating request
  server-wide (`require_control_token`, `alan/control_plane.py`) — it has
  nothing to do with any one repository.
- The **repository credential** (`owner/repo:transport`) is a per-repo GitHub
  PAT, created via the Settings Credentials section this item ships, that lets
  alan clone/push that one repo and is what `_require_credential_record` checks.

Every mutation on the step-1 path (`POST /api/engines`, `POST
.../config-proposal`, `POST`/`DELETE /api/secrets`) is control-token-gated
(401 missing / 403 mismatch / 500 not configured); **every read on the resume
path is ungated** (`GET /api/engines`, `GET /api/engines/{o}/{r}`, `GET
/api/secrets`, `GET .../onboarding`, `GET .../preflight`). A user with no stored
control token can therefore read their entire setup state on landing but cannot
act on it — `MutationErrorNotice` renders that as a typed, actionable state
("Enter your control token in Settings to make changes," linking to the
Settings surface this item extends) — never a raw `401: control token
required` string, and never worded as if the *repository* credential were
missing.

### Not a second orchestration path

`GET /api/engine-presets/{name}/config?owner=&repo=` (added by this item, see
below) is a pure, ungated **read** that returns `engine_presets.build_config`
verbatim — it registers nothing. The wizard still calls `POST /api/engines`
itself with that body; the new endpoint only closes the gap that
`engine_presets.build_config` had no HTTP surface before this item (a
forge-less create body would otherwise silently 201 past
`_require_credential_record`, since that check only fires when `body.forge is
not None`). `POST /api/onboarding` (`alan/routes/onboarding.py`) remains the
atomic path for API callers; this wizard does not route through it, and this
item adds no second path that reaches `onboarding.create_attempt` or its
rollback.

### Posture stays untouched

Step 1 renders no posture field, chip, value, or default anywhere.
`engine_presets.build_config` seeds `posture: observe` into every `EngineConfig`
it returns (the lowest posture, per `Posture.observe` = rank 0), and step 1's
`POST /api/engines` forwards that value unchanged — there is no user-facing
control to raise it. Posture is a safety dial; W2.1
does not relax it, propose raising it, or weaken the `confirm`-gated
`POST .../posture` contract in any way.

### Step 1's action — five calls behind one button, one literally-true caption

AC7 requires the button's caption to stay literally true about the
*repository* even though the action both registers the engine and fires a
read-only server-side scan. Clicking step 1's single primary action
(`WizardFooter`'s one saturated-fill button, per rule 7) runs, in order:

1. `GET /api/engine-presets/python/config?owner=&repo=` — the preset baseline.
   `python` is a fixed internal seed (`STEP1_BASE_PRESET`), never surfaced to
   the user, and never the final word on the repo's actual stack (see next
   point).
2. `POST /api/engines` with that baseline body — registers the engine (and its
   pool, if a dispatcher is live).
3. `POST /api/engines/{owner}/{repo}/config-proposal` with `{"base_preset":
   null}` — `base_preset: null` (not `"python"`) so
   `engine_config_proposal`'s deterministic stack pre-pass infers the repo's
   *real* stack from its own checkout rather than assuming the step-1 seed.
   This call is read-only: it inspects the target repo and proposes a config
   delta, it does not push, write, or register anything further. Its result is
   what a later PR's step 2 `PATCH` applies — out of scope here.
4. `GET /api/engines/{owner}/{repo}/onboarding` — refreshes the reachability
   finding now that the engine has a real forge attached.
5. `useConnectFlowState.refetch()` — re-runs the ungated reads (credential,
   registration) so all three checklist rows reflect the new server state.

The caption stays literally true by describing only what happens to the
*repository*: registration and a read-only scan, nothing pushed or changed.
Example (exact wording is an implementation, not frozen prose here): "Registers
`{owner}/{repo}` with alan and starts a read-only scan of its stack — nothing is
pushed or changed in the repository." No wireframe step collects a stack name,
and every preset-differing field the proposal might surface is a step-2 `PATCH`
field that never executes at `posture: observe` — step 1 only ever *proposes*.

### The inverted reading gets its own test

A form that mints a record for the wrong identity — a different repo, or one
that differs only by case — must 422 registration with a typed block naming
the *exact* expected record name, never a raw error string. This is exercised
directly (see the e2e spec below) rather than assumed from the two sentences
above.

## (b) Shared step primitives

Four visually similar step screens landing across four PRs (W2.1-W2.4) would
breach the duplication gate against each other unless the shell is shared.
Four of the five primitives live in `alan/web/src/features/connect/components/`
because only the wizard renders them; `MutationErrorNotice` stays at
`alan/web/src/components/wizard/MutationErrorNotice.tsx` because
`CredentialsSection` also renders it. All five are written generically over
"a step," not over any one step's specific content:

- **`WizardStepShell`** — the outer per-step layout: a page-title heading
  (`text-xl`, matching every other Shell screen per `DESIGN_SYSTEM.md`), a
  content region, and a slot for the rail. Takes `{ title, children }`; carries
  no step-specific knowledge.
- **`WizardRail`** — the four-step progress rail (identify → configure → make
  dispatchable → posture), rendering each step as done / current / upcoming.
  Steps 2-4 render as generic upcoming placeholders since they have no
  component yet — the rail takes a list of step labels and a current index, it
  does not special-case "not built yet" as an error state.
- **`ChecklistRow`** — one row of the identity/credential/reachability/proposal
  checklist. Props are `{ label, status, detail?, remedy?, action? }` —
  `status` is the same closed `pass | fail | unknown` (plus a `pending`
  variant for "not checked yet") shape `PreflightStatus` already uses. This is
  a **new**, distinct component from `FindingRow.tsx`: `FindingRow` is keyed on
  the backend's `EnginePreflightFinding` shape and carries the raw (W1.1-owned)
  hues; `ChecklistRow` is keyed on the wizard's own rail-shaped props and
  reuses the *tokenized* status chip described next. Building a fourth
  near-copy of `StatusBadge.tsx` was rejected — no new chip component, no new
  `--color-*` token.
- **`WizardFooter`** — the one primary action per step (rule 7: exactly one
  saturated fill in the viewport) plus its caption line, directly under the
  button, stating in plain language what the action actually does (AC7).
- **`MutationErrorNotice`** — shared between step 1 and `CredentialsSection`.
  Takes the `MutationBlock` discriminated union `connectFlowErrors.ts` produces
  and renders one of a small set of typed, actionable states — never a raw
  error string — including the no-token / mismatched-token state that deep
  links to Settings (AC6). Reuses the ratified, tokenized block-level error
  banner recipe (`bg-banner-error-tint/15` / `text-banner-error-fg`, PR #891)
  rather than authoring a sixth variant.

**Status-chip tokens.** The new chips reuse the existing button-color tokens as
translucent fills — `bg-btn-create/15 text-btn-create` (pass),
`bg-btn-destroy/15 text-btn-destroy` (fail), `bg-btn-reset/15 text-btn-reset`
(unknown/needs-attention), `text-text-muted` on a neutral background (pending,
not yet checked) — rather than minting a new `--color-status-*` token family
for the checklist rows specifically. `index.css` already carries the `--color-
status-*` and `--color-banner-error-*` tokens PR #891 landed; `ChecklistRow`
reuses the button-color tokens instead of those because a checklist row is an
action-adjacent affordance (pass/fail/unknown), not one of the six lifecycle
states `--color-status-*` names — reusing `--color-btn-*` avoids implying a
checklist row is a workspace-lifecycle state it isn't, while
`MutationErrorNotice` (a true error banner) reuses `--color-banner-error-*`
verbatim since that is exactly what it is.

## Non-goals restated (W2.1)

- Steps 2, 3, 4 are not built — only `WizardStepShell`/`WizardRail`/
  `ChecklistRow`/`WizardFooter`/`MutationErrorNotice` are, for later items to
  consume. **Superseded by W2.2/W2.3/W2.4 below**: all four steps are now
  built.
- Home is untouched — the connect CTA (W1.4) and the blank-slate resume copy
  (W2.5) are later items' scope; `/connect` and `/connect/:owner/:repo` are
  reachable only by direct navigation today.
- The existing control-token status pill in `SettingsModal.tsx` (`:85-87`, raw
  `bg-green-500/15`/`bg-zinc-500/15`) is unmigrated — deliberately deferred,
  unrelated to this item's new tokenized chips.
- No new environment variable, no new dispatch side door, no relaxation of the
  posture confirm contract.

## W2.2 — Step 2 (configure engine: proposal → wizard step)

Delivers the last unbuilt rail placeholder: a table-shaped review of the
`config-proposal` pipeline's output, edited in place, saved as a `PATCH`
against the engine step 1 already registered. Consumed unchanged: `_field_diff`
(`alan/engine_config_proposal.py`), `EngineConfigProposalPanel.tsx`'s value
summarizers and its `ArgvListEditor`/`KeyedListDictEditor`/`RoleConfigTable`
editors (now shared via `engineConfigFieldSummary.ts` and
`components/EngineConfigFieldEditor.tsx`, pure moves with no behaviour
change), `WizardStepShell`/`WizardRail`/`WizardFooter`/`MutationErrorNotice`.

### The completion rule derives from repo writes, not a marker

There is no server-derived "step 2 saved" signal — `OnboardingState` carries
no config marker, and `EngineConfig` has no saved-at field. Minting one would
widen this M item's backend surface for a fact the rest of the flow doesn't
otherwise need. Instead, `wizardStepSelection.ts`'s `isConfigureDone(readiness)`
reuses the same downstream signal that already proves a step-2 `PATCH` must
have run:

```ts
readiness !== null && (readiness.state.scaffold_pr_number !== null || isDispatchableReady(readiness))
```

"Configure is behind you once repo writes have started" — either a scaffold
PR already exists (step 3 was reached), or dispatchable is already fully
ready (which itself implies a scaffold PR was merged). The accepted cost: a
fresh page load between registering and ever having opened a scaffold PR
re-runs the proposal pipeline every time step 2 is re-entered. This is
bounded — the in-session draft cache (below) covers the common case of
navigating within the wizard during a single visit — and is consistent with
the proposal's own "informational, not sticky" stance elsewhere in this item.

Flipping `configure`'s `built` flag to `true` changes which step
`deriveCurrentStep` resolves to for any completion state where `identify` is
done but `configure` is not — this is the same class of change W2.3/W2.4
warned about (a newly-built step intercepts derivation for everyone). Fixed
here: `connect-wizard-step3.spec.ts`'s three affected `goto`s (offering no
scaffold PR yet, where the bare URL would now land on step 2) now deep-link to
`/dispatchable`. The fourth (with a scaffold PR already open) is left on its
bare URL deliberately — by that point configure already reads done, so the
bare URL's derivation correctly skips past it, which is now a *stronger*
assertion than before this item.

### Posture no-op by construction — the client's obligation, not a server guarantee

`PATCH /api/engines/{owner}/{repo}` (`update_engine`) accepts and persists a
whole `EngineConfig` with **no posture guard**, and `engine_presets.build_config`
seeds every fresh config at `posture: observe`. Nothing server-side stops this
step's save from silently downgrading an engine whose posture was
deliberately raised through step 4's confirm-gated contract. The fix is
entirely client-side, in `connectStep2ConfigureLogic.ts`:

- `Step2Draft` is a `Pick<EngineConfig, ProposableEngineConfigField |
  "code_quality" | "pool_size">` — posture is not a member of this type, so
  the draft can never carry a posture value by construction, not by
  discipline.
- `buildStep2Payload(live, draft)` is `{ ...live, ...draft }` — `live` is the
  engine's own config, read fresh at mount from `GET
  /api/engines/{owner}/{repo}`. Since `draft` structurally cannot contain a
  `posture` key, spreading it over `live` can never clobber `live`'s posture
  (or its `forge`, `ci`, `repo`, `preset`, `prompt_overrides` — every field
  this step doesn't render rides through unchanged too).

A downgrade here would be fail-safe in direction (dropping to a *lower*
posture, never escalating one), which is exactly why it would go unnoticed
without this guard — nothing about a silent drop to `observe` looks like an
error. Test-pinned, including the specific step-4-raised-then-stepped-back
scenario (`ConnectStep2Configure.test.tsx`, `connect-wizard-step2.spec.ts`).

### The proposal's in-flight state is a named phase, not a spinner

Re-entry re-runs a `sonnet` inference over a fresh checkout of the target
repo — tens of seconds, sometimes longer. `ConnectStep2Configure.tsx` models
this as a closed `Step2Phase` union (`proposing | ready | fallback | error`)
rendered through a single persistent `role="status"` element so the
loading→ready transition is announced, plus phase-specific content below it
(the diff table when ready, the delivered finding when the proposal 422s,
a plain error message for anything else). The mount effect is a single
`useRef`-guarded `Promise.allSettled([proposePromise, getEngineConfig(...)])`
per `engineKey`, mirroring `useStep4Mount`'s shape exactly — the same
StrictMode double-invoke guard, the same stale-`engineKey` discard on
resolution.

### The V1 correction, structurally

`_PROPOSABLE_FIELDS` is exactly seven fields; `code_quality`, `pool_size`, and
`posture` are not among them. `EngineConfigProposalDiffTable.tsx` renders
`proposal.diff` unfiltered (no `changed` predicate — `_field_diff` already
emits a row per proposable field, sorted, regardless of whether it changed,
so the seven-row pin comes free) with **Baseline** and **Now** as the two
data columns; every "Now" cell is `EngineConfigFieldEditor`, edit-in-place,
with no separate accept/edit/reject decision model. `code_quality` and
`pool_size` render in a visually and structurally distinct bordered
`BaselineFieldsBlock`, headed "Not proposed — edit directly" — never inside
the diff table. `posture` appears nowhere on this step. No copy on this step
claims CI provenance anywhere — the pipeline reads the repo, it does not
parse CI.

`flow_policy` gained an inline editor it never had in the original
accept/edit/reject panel (that panel only ever offered accept/reject for it):
`EngineConfigFieldEditor`'s `FlowPolicyEditor` reuses `KeyedListDictEditor`
over the four team lists (`verifier_teams`/`merge_teams`/`terminal_teams`/
`internal_only_teams`) plus a plain numeric `revisit_cap` input.
`restricted_predecessors` is left out of the editor, matching
`engineConfigFieldSummary.ts`'s own summarizer, which never surfaced it
either.

### Degrading on the 422 — the finding's own words, never a guessed cause

`_manual_seed_finding` (`alan/routes/engines.py`) wraps **both**
`ProposalAbortedError` and `ProposalUnrepairableError` into the identical
`Finding` shape — "a proposal is already running for this repo" and "the
checkout is unreadable" are indistinguishable except by the message text.
`connectStep2ConfigureLogic.ts`'s `fallbackFindingOf(err)` recovers the
delivered `Finding` from a `ConfigProposalBlockedError` and nothing else —
any other rejection (a plain `ApiError`, a network failure) renders a generic
load error instead, never a fabricated finding. When a finding is recovered,
the step renders its `message` and `remedy` verbatim beside
`EnginePresetSeedButton` (the same component `EngineConfigForm.tsx` already
uses, applying its `transition_graph`/`flow_policy` overlay onto the draft),
without advancing the step and without any backend copy change.

### Live validation and the draft cache

Every draft edit re-runs `POST .../config-draft-validate`, debounced 400ms,
rendering the returned finding under the fixed caption "validated as a draft
— nothing saved yet" — the same debounce/cancel shape
`EngineConfigProposalPanel.tsx`'s own validation effect already used, adapted
to `Step2Draft` instead of `fieldStates`.

`connectStep2ConfigureLogic.ts` holds a **module-scope** `Map<string,
Step2CacheEntry>` (`{ proposal, draft }`), keyed by `engineKey`. This is
in-memory only: it survives navigation within the wizard for the lifetime of
the page (React Router doesn't reload the JS module on a client-side
navigation) but is wiped by any real page reload — a `page.goto()` in
Playwright, a browser refresh, closing the tab. A warm cache hit on mount
skips the `config-proposal` POST entirely (only `getEngineConfig` still runs,
to keep `live`, and therefore posture, current); `invalidateStep2Cache` is
called by the preset-chip repropose action and by a successful save (a saved
config should re-derive from the server on the next visit, not replay a
stale draft).

### The mobile-audit limitation

`connect-wizard-step2` is registered in `screens.ts`, reusing the same
`step3-fixture` engine as steps 3 and 4 via the `/configure` deep-link
override, with its baseline captured in this PR
(`overflow: 0, tapTarget: 0, contrast: 0`). `screens.ts` entries are
`{name, route}` with no per-screen stub hook and the mobile-audit run seeds
no control token, so the audited page's `config-proposal` call 401s and the
step renders its plain load-error state, not the diff table — deterministic
and baseline-able, but the table's own tap targets and contrast never get
scanned by this audit. Closing that gap needs either a stub hook in the
audit harness or a seeded control token plus a stubbed proposal response for
this one screen — both out of scope here.

## W2.3 — Step 3 (make dispatchable), shared step selection, the client-driven poll

Delivers the two-phase scaffold/protect backend (`alan/repo_scaffold.py`,
`docs/architecture/repo_scaffold.md`) as wizard step 3, and — implied by step
3's own DoD, not appended scope — the step-selection mechanism `ConnectWizard.tsx`
needed since W2.1 shipped only one step. W2.2/W2.4 consume both unchanged.

### The widened fourth read

`useConnectFlowState`'s `ConnectFlowReady` gained one field:

```ts
export interface ConnectFlowReady {
  registered: boolean;
  credentialPresent: boolean;
  reachability: ReachabilityStatus;
  readiness: OnboardingReadiness | null;   // new
}
```

`readiness` is the exact `GET .../onboarding` response `reachability` already
derived `push_repository`'s status from — there is no second onboarding
fetch. It is `null` whenever `reachability` would itself be `"unknown"` or
`"no_forge"` (engine not yet registered, or 422 with no forge configured);
step 3's checklist, the shared step-selection mechanism, and the readiness
poll all read this one field.

### Step-selection contract (`alan/web/src/lib/wizardStepSelection.ts`)

A registry of `{ key, label, built }`, reusing W2.1's `ConnectWizard.tsx`
`STEPS` keys verbatim (`identify`, `configure`, `dispatchable`, `posture`) —
`built` replaces the informal "not built yet" comment with a field
`selectStep` actually reads:

- `deriveCompletion({ registered, credentialPresent, readiness })` — step 1
  done ⇔ `registered && credentialPresent` (the stack-scan proposal's status
  is deliberately excluded: it is `ConnectStep1Identify`-local React state,
  never server-derived, and step selection must never read client state).
  Step 3 done ⇔ `isDispatchableReady(readiness)`.
- `isDispatchableReady(readiness)` — scaffold PR merged **and** `protect()`
  has actually run **and** no protection finding is still deferred. The
  middle clause is load-bearing and was not obvious from the wireframe: a
  fresh `OnboardingState` has **both** `protection_applied_keys` and
  `protection_deferred` empty before `protect()` ever executes, so "no
  deferred finding" alone is indistinguishable from "protection never
  attempted" — an e2e run against a real merge caught this exact false
  positive (the poll's merge-detection effect saw `isDispatchableReady` already
  `true` and skipped calling `protect()` entirely). The fix requires
  `protection_applied_keys.length > 0 || protection_deferred.length > 0` in
  addition to the deferred-empty check.
- `selectStep(steps, completion, requestedKey)` — the current step is the
  first incomplete **built** step, else the last built step (never an unbuilt
  one, so a not-yet-shipped W2.2/W2.4 step can never strand the wizard on
  empty content). `requestedKey` is a **deep-link override**, honored only
  when it names a built step; an unbuilt or unknown key falls through to the
  derived step exactly as if no key had been given.

### The `:step` route is override-only, never the mechanism itself

`App.tsx` adds a third literal route, `/connect/:owner/:repo/:step`, alongside
the existing `/connect` and `/connect/:owner/:repo`. `ConnectWizard.tsx`'s
`ResumableWizard` always computes `currentKey` via `selectStep` and renders
it directly — the bare `/connect/:owner/:repo` URL **never redirects** on the
ordinary path; there is no "advance the URL" navigation call for the common
case, only rendering off the freshly-derived key. The **only** navigation
`ResumableWizard` ever performs is a replace-navigate back to the bare URL
when the `:step` segment names something `selectStep` would not honor (an
unbuilt step, a typo, a stale bookmark) — this is what "never lands on an
unbuilt step" means operationally: a bad deep link self-corrects instead of
rendering nothing. `EngineOpsPanel`'s "Open setup →" link is the one
in-product producer of a valid override (`/connect/{owner}/{repo}/dispatchable`),
and the mobile-audit fixture and e2e spec use it the same way to reach step 3
directly without needing step 1 to be complete.

### One `useConnectFlowState` call per page load

`ResumableWizard` is the **single** reader: it calls `useConnectFlowState`
once (to compute `completion`/`currentKey`) and passes `{ engineKey, state,
refetch }` down as props to whichever step component renders —
`ConnectStep3Dispatchable` does not call the hook itself. This is not just
tidiness: `POST .../scaffold` and `POST .../protect` publish
`"engine_scaffolded"`/`"engine_protected"` `TeamEvent`s that
`alan/routes/events.py`'s `_ENGINE_KINDS` maps to the `"engines"` SSE kind —
**contrary to an earlier assumption, these actions do fire an "engines"
change event** — and `useChangeRefetch("engines", …, true)` (inside
`useConnectFlowState`) reacts to it automatically. A second independent
`useConnectFlowState` call on the same page therefore does not just double an
HTTP GET (harmless in production); against the e2e `gh` stub's
per-call-consumed `ALAN_E2E_GH_PR_STATE_FILE` queue (`review_and_merge_scaffold_pr`'s
`gh pr view`), two near-simultaneous reads drain more of the queue than one
read would, and non-deterministically skip a transition the test expected to
observe. `ConnectStep1Identify.tsx` still calls the hook itself (unchanged,
per its byte-identical requirement) — harmless there today because no step 1
scenario has a scaffold PR yet to poll.

### The poll is client-driven, on purpose (`alan/web/src/features/connect/hooks/useReadinessPoll.ts`)

There is no server daemon and no PR-watching event. While step 3 is mounted
and `readiness.state.scaffold_pr_number !== null && !isDispatchableReady`, a
`setTimeout` loop (never `setInterval`, so a slow tick cannot overlap the
next one) refetches onboarding at a 20s base cadence, doubling (capped at
60s) after a tick whose surrounding read reported an error and resetting to
base on a healthy one. A separate effect watches the **current** `readiness`
value (not the poll's own tick) for "merged but not yet protected" and fires
`POST .../protect` — a ref latch set **before** the `await` prevents a
concurrent effect run (e.g. a `visibilitychange`-triggered refetch landing in
the same async gap) from firing a second `protect()` call for the same
detected merge, and is reset unconditionally once the call settles so a
transient failure (not a permanent admin-403) gets retried on the next
readiness update rather than latching forever. `visibilitychange` also
triggers an immediate refetch independent of the timer — genuinely correct
behaviour (a backgrounded-then-foregrounded tab should not wait out a stale
delay) and the deterministic seam the e2e walk drives merge/protect
transitions through, rather than depending on the PR-state queue's exact
depth (see the e2e fixture note below).

### e2e: a real local git remote, not a second `gh` stub

`alan/web/e2e/fixtures/server-env.ts` sets `GIT_CONFIG_GLOBAL` to a per-worker
gitconfig file with `[url "file://<root>/gh-remotes/"] insteadOf =
https://github.com/`. `repo_scaffold._clone_repo`/`_check_push_repo` hardcode
`https://github.com/{slug}.git`; the natural fix — a rewrite passed via
`GIT_CONFIG_COUNT`/`GIT_CONFIG_KEY_0`/`GIT_CONFIG_VALUE_0` — is clobbered by
`secrets_store.transport_env`, which hardcodes that exact triple and is
merged last. A file-based global gitconfig is a different mechanism (git
reads it independently of any per-call env override) and survives untouched;
`alan/web/e2e/connect-wizard-step3.spec.ts` `git init --bare`s a real, empty
(then one-commit-seeded) repository per test under that prefix — no network,
no real GitHub credential, and `apply_branch_protection`'s `gh api
repos/<slug>` / `repos/<slug>/branches/<branch>/protection` reads (previously
unmatched by the stub's leading-slash-only case, deferring every protection
row as unknown) are now handled by `alan/web/e2e/fixtures/gh`'s no-leading-slash
cases. Because `ALAN_E2E_GH_PR_STATE_FILE`'s queue is consumed per `gh pr
view` call and shared with `pr_merge`'s own poll loop, the spec seeds a
generous run of the same value (`Array(50).fill("OPEN")`) rather than a
single entry — a lone entry is drained by the very first of the (more than
one, see above) reads a single page load can trigger, before the test's own
assertion ever observes the intermediate state.

### The checklist tells the truth before the button that needs it — `workflow_write` and the real push failure

Two gaps in step 3's checklist were closed together, since both share a shape:
information the backend already has, previously arriving too late (after a click)
or not at all (a raw internal name instead of the real reason).

**The `workflow_write` row.** `connectStep3Rows.ts`'s `workflowWriteRow` maps
`repo_scaffold.build_checklist`'s `workflow_write` finding (see
`docs/architecture/repo_scaffold.md`'s "readiness checklist" section for the
backend probe itself) the same way `reachabilityRow` maps `push_repository`: `fail`
→ the danger chip with the verbatim remedy, `pass`/`unknown` (no finding, or an
absent transport credential) → the pass chip or `unverifiable`. It sits between
`reachabilityRow` and `scaffoldRow` in `buildConnectStep3Rows`, matching the
backend's own `push_repository` → `mint_credentials` → `workflow_write` →
`grant_reviewer_collaborator_access` → `review_and_merge_scaffold_pr` order.
Unlike `reachabilityRow`, its `unverifiable` branch still carries the `remedy`
through — the fine-grained-token forward-looking note is the only way that
particular gap is visible to the user at all.

**The scaffold push failure.** `POST .../scaffold` 422s for four structurally
different causes (no transport credential, no forge, a git/gh subprocess failure,
an unparseable `gh pr create` result) behind one `ScaffoldError`. Rather than
inventing a push-specific `MutationBlock` kind that would be factually wrong for
the other three, one neutral kind — `scaffold_failed`, headline "The scaffold pull
request couldn't be opened." — carries whatever `ScaffoldError` says as its detail
line, through the same `classifyMutationError(err, "scaffold")` →
`MutationErrorNotice` path every other mutation on this flow already uses.
`ConnectStep3Dispatchable.tsx`'s scaffold-error state is a `MutationBlock | null`,
not a raw string, specifically so a push rejection's forge-stated reason (see
`repo_scaffold.md`'s `PushResult.detail` truncation note) reaches the user instead
of the `PUSH_FAILED` enum name a naive `String(err)` would have shown.

### A credential fix reaches the checklist without a page reload — why `secret_written` had to become an "engines" event

`useReadinessPoll` (the `setTimeout` loop that keeps step 3's checklist current
after a scaffold PR exists) is gated off entirely while
`readiness.state.scaffold_pr_number === null` — there is nothing to poll for yet.
That means a user who hits the `workflow_write` failure, leaves to regenerate their
token, and saves it again via `POST /api/secrets` had, before this item, no live
signal telling the page to re-read onboarding: the credential fix would sit
unseen until a manual reload. `alan/routes/secrets.py`'s `create_secret` now
publishes `TeamEvent("secret_written", {"engine", "purpose"})` on the global
pubsub, and `alan/routes/events.py`'s `_ENGINE_KINDS` maps it to the same
`"engines"` SSE kind `engine_scaffolded`/`engine_protected`/`preflight_completed`
already use (no token or secret value in the payload — same redaction contract
every other secrets-route response already keeps). `useConnectFlowState`'s
`useChangeRefetch("engines", …, true)` — the same subscription W2.3 already wired
for `engine_scaffolded`/`engine_protected` — reacts to it automatically, so the
checklist flips from `fail` to `pass` on its own once the SSE frame lands, with no
poll, no reload, and no new frontend wiring beyond the existing "engines" refetch
this flow already had.

## W2.4 — Step 4 (posture, then finish)

Delivers the wizard's fourth and final step: the user picks how far alan may
act on the repo, sees whether it is ready to dispatch, and finishes into a
real project. This is the flow's safety decision,
so every path through it is deliberate, honestly described, and impossible
to trigger by accident.

### The pure logic module is `connectStep4PostureLogic.ts`, not
`connectStep4Posture.ts`

The page component is `ConnectStep4Posture.tsx`; its pure decision logic
lives in a sibling module. Naming that module `connectStep4Posture.ts` —
differing from the component's filename only in the first letter's case —
compiles and runs fine on a case-sensitive filesystem but fails `tsc -b`
outright on a case-insensitive one (macOS APFS, Windows): TypeScript's own
module resolution flags the two paths as colliding (`TS1149`) the moment
both are reachable from the same program, regardless of their different
extensions. The module is named `connectStep4PostureLogic.ts` instead —
follow this precedent (a distinct name, not a re-cased one) for any future
page/logic-module pair in this wizard.

### The three-register copy map lives in exactly one place

`preflightRegister(overall: PreflightStatus | null): "ready to dispatch" |
"blocked" | "partially ready"` (`connectStep4PostureLogic.ts`) is the *only*
place this copy is written. `overall` only ever holds `pass`/`fail`/`unknown`
(`EnginePreflightResult.overall`); the mapping is total —
`pass`→ready, `fail`→blocked, everything else (`unknown`, and `null` for a
check that has never run or is still in flight) →partially ready. There is
no fourth register, and a never-run check reads as partially ready, never as
blocked — a freshly connected repo must never look broken before it has even
been checked. `ConnectStep4Posture.tsx`'s `ReadinessChip` renders
`{register}` directly rather than re-deriving or re-wording it, so the copy
can't drift between the logic module and the screen.

### Captions are written against `admitted_teams`, not the wireframe

`buildPostureRungs(mergedWork: boolean): PostureRung[]` returns the four
postures in rank order, each with a caption. The captions are pinned against
`_TEAM_MIN_POSTURE` / `admitted_teams` (`alan/engine_config.py:62-88`), not
against the wireframe's phrasing:

- **`observe`** admits no team at all — no team declares a minimum posture of
  `observe`, so `admitted_teams` returns the empty set at this rung. Alan
  dispatches nothing.
- **`propose`** admits research/brainstorming/architecture/planning/
  implementation work, but no verify-or-merge team — nothing this rung
  dispatches can merge itself.
- **`self_merge`** adds `verification` and `pr_merge` on top of `propose`'s
  set.
- **`autonomous`** adds the unattended maintenance teams (`triage`,
  `dependency_sweep`, `deploy_verify`, `maintenance`) on top of
  `self_merge`'s set.

### Greying — three independent enforcement points, not one

`buildPostureRungs(mergedWork)` greys `self_merge` and `autonomous` (the two
higher-rank rungs) until `hasPrBearingWorkspace(workspaces, engineKey)` is
true — `some(w => w.engine_key === engineKey && w.pr_number != null)` over
`GET /api/workspaces`'s default (non-archived) bucket. The name says exactly
what the predicate can claim: a PR exists, not that it merged. `observe` and
`propose` are always selectable and `observe` is always pre-selected;
**nothing higher is ever pre-selected**. This one predicate is the sole
decider of selectability, but it is enforced at three independent points so
the DoD's adversarial clause ("no path — including from inside the
confirmation dialog — can select or set a posture the step has marked
unavailable") is testable rather than aspirational:

1. The radio input's `disabled` attribute — verified empirically (not just
   assumed), this is not merely decorative: React does not invoke `onChange`
   for a disabled controlled input even when a `change` event is dispatched
   directly at the DOM node, bypassing the browser's own click-activation
   gating entirely. `ConnectStep4Posture.test.tsx`'s adversarial case pins
   this — a raw `fireEvent.change` can flip jsdom's own DOM property, but
   React's controlled state (and everything downstream of it) never observes
   the attempt.
2. A hard `isSelectable(rungs, selected)` re-check inside the finish handler,
   before any network call. Given point 1, this line is unreachable through
   any test that respects React's own guarantee — it is `/* v8 ignore
   next */`-marked — and is kept as defense-in-depth against a future change
   desyncing `selected` from `rungs`, not because today's delivered code can
   reach it.
3. Structurally, via `PostureConfirmModal`'s extended contract below — the
   modal's free `<select>` is never mounted when the caller supplies a
   target, so there is no in-dialog path to a posture the step never even
   offers as a target.

### `PostureConfirmModal`'s extended contract

`PostureConfirmModal` gained two optional props, and `EngineOpsPanel`'s call
site is unchanged (passes neither, byte-identical):

- **`targetPosture?: EnginePosture`** — when supplied, the free `<select>`
  block is not rendered *at all* (not merely disabled); a locked
  `<p data-testid="posture-confirm-target">` names the target instead, and
  the dialog's internal `posture` state initialises to it. This is the
  closing move on the safety hole a step-4-only greying would otherwise
  leave open: if step 4 greys `self_merge`/`autonomous` but the confirm
  dialog still offered a free choice, a user could reach full autonomy from
  inside the very dialog meant to make the decision deliberate. Not mounting
  the picker makes the greying structural rather than decorative.
- **`onConfirmed?: () => void`** — fired after a *resolved*
  `api.setEnginePosture` call, before `onClose()`. Step 4 uses it to chain
  the project-create-and-navigate sequence only once the write has actually
  succeeded; a rejected write (shown inline in the dialog, same as before)
  never fires it, and Cancel never fires it either.

`api.setEnginePosture` (`{posture, confirm: true}`) stays the only write —
this item adds no second posture write path, and the `source` field the
server persists into `posture_audit.jsonl` stays hardcoded to `"api"`
regardless of caller; there is no `"ui-confirm"` source value to wire up.

Step 4 opens the dialog **only when the selection differs from the engine's
current posture** — not narrowly "only on a rank-raise" — so any actual
change goes through the same deliberate confirm step, and a selection equal
to the current posture (the common case: a freshly connected engine is
always at `observe`, and the radio starts there too) finishes with **no**
posture write at all. `set_posture_and_persist` (`alan/engine_config.py:354`)
itself returns early and writes no audit line when `target == current` — so
"no audit line" on the unchanged path is a legitimate no-op, never read as a
failure, and DoD's audit pin is specifically a *raise-path* assertion
(`tests/test_routes_engines.py`'s `test_set_posture_raise_appends_exactly_one_audit_entry_with_source_api`
/ `test_set_posture_no_op_appends_no_audit_entry_and_still_returns_200`).

### Finishing: posture-first, bind only on `pass`

The Finish button (`WizardFooter`, one saturated fill, no sibling) drives a
single chain, posture first so a posture failure can never leave an orphan
project behind:

1. Re-check `isSelectable` (enforcement point 2 above).
2. If the selection differs from the current posture, open the confirm
   dialog and wait for `onConfirmed`; a failed or cancelled confirm aborts
   the whole chain here — nothing is created.
3. Find or create the `Project` — `GET /api/projects?include=archived` is
   searched by `selectReusableProject` (`connectStep4PostureLogic.ts`), which
   returns a project whose `engine_key` already matches this engine, else an
   unbound project whose `intended_engine_key` matches (never the reverse
   order, and never a project bound to a *different* engine even if it
   carries this one as intent — the same precedence `lib/fold.ts`'s
   placement rule already ratified). Re-finishing, or reaching step 4 again
   after finishing once, reuses that match rather than minting a duplicate;
   otherwise `POST /api/projects` creates one titled from the repo segment of
   the engine key (`owner/repo` → `repo`). Reuse still searches the archived
   bucket, so a re-finish after archiving the reused project resurrects it —
   pre-existing behavior on the bound path, kept as parity rather than
   widened. Projects minted before `intended_engine_key` existed carry no
   provenance, so pre-existing duplicates on a live instance are neither
   adopted nor cleaned up by this logic; the next finish for that engine
   mints one further orphan, then reuse holds from there on.
4. Bind only when the client's own last-read preflight `overall === "pass"`
   — this is a **substrate-forced narrowing**, not a weakening of intent.
   `CreateProjectBody` has no `engine_key` field, and the only bind path
   (`PATCH /api/projects/{id}` with `engine_key`) runs
   `_check_engine_rebind_gates` (`alan/routes/projects.py:425`), which 422s
   `"engine preflight not passing"` unless the persisted last preflight
   already reads `pass` — a real safety gate this item must not bypass,
   duplicate, or re-implement. So: **the DoD phrase "lands on the bound
   project page" reads as "lands on the project page — bound when preflight
   passes, honestly and visibly unbound with the reason when it does not."**
   An always-bound guarantee would require changing that backend gate and is
   not this item's call.
5. Navigate to `/projects/{id}`, passing `{connectUnboundReason}` in
   `location.state` only when the project was *not* bound.

### The four-reason unbound taxonomy

`components/ConnectUnboundNotice.tsx` closes a four-value reason union,
read out of `location.state` through a real type guard
(`readConnectUnboundState`) rather than trusted directly — `location.state`
is history-controlled (survives back/forward, manual URL edits) and must
never be trusted into copy unchecked:

- **`preflight_blocked`** — the client's own last read of `overall` was
  `"fail"`.
- **`preflight_unknown`** — `overall` was `"unknown"` or the check had never
  run (`null`) at finish time.
- **`bind_rejected`** — the bind `PATCH` itself 422'd (the persisted result
  the backend re-checks at bind time no longer read `pass`, a race between
  the client's cached read and the server's own check).
- **`bind_conflict`** — the bind `PATCH` 409'd (`project_has_live_workspaces`
  — structurally different from a preflight problem, and must never print
  preflight copy on this safety-adjacent screen).

`Project.tsx` renders `<ConnectUnboundNotice reason={...} />` at the top of
the detail body when the guard returns non-null. It is arrival-only and
non-dismissible by design: `location.state` clears on the next navigation,
so revisiting or refreshing the project page never re-shows it. The existing
`project-detail-error` region is an early return before this render, so the
two can never co-render. The notice uses the **attention** tint
(`bg-status-attention-tint`), not the error tint `MutationErrorNotice`
reuses — the project itself was created successfully; only its bind to the
repo is pending or failed, which is an explained condition, not an error.

### Two stated limitations

- **The PR-bearing-workspace signal reads the default workspace list only.**
  `hasPrBearingWorkspace` calls `api.listWorkspaces()` with no `bucket`
  option, which returns the non-archived bucket; the backend's
  terminal-status set (`workspace.Status.COMPLETED | .ARCHIVED`) includes
  `ARCHIVED`. A repo whose only PR-bearing work has since been archived reads
  as having none, and `self_merge`/`autonomous` stay greyed for it. Accepted:
  at connect time this fold is near-vacuous anyway (a freshly connected repo
  has no PR-bearing work), and widening the read to the archived bucket is a
  straightforward follow-up if it ever matters in practice.
- **"Lands on the bound project page" is not always achievable** — see
  "Finishing: posture-first, bind only on `pass`" above. The project is
  always reachable; the bind is conditional on the backend's own safety
  gate, which this item does not relax.

### No new abandon path

Posture still dies with the engine — see the teardown table above. The
project this step creates has its own, unrelated teardown leg
(`DELETE /api/projects/{id}`), and this item wires neither posture nor the
project into any abandon/cancel flow; both are forward-only artifacts of
finishing the wizard.

## W2.5 — Home connect states: blank slate + needs-you lines

Gives Home its two connect states, closing the loop from Home into this
wizard and back. No backend files change — the correction below is in what
Home *reads*, not in what the server *reports*.

### `OnboardingReadiness.overall` can never signal "setup finished"

The item's own written trigger ("a non-default engine whose `GET
.../onboarding` overall !== pass") is unsatisfiable: `build_checklist`
(`alan/repo_scaffold.py`) unconditionally includes
`_check_collaborator_access`, hard-coded `"unknown"`, and `overall_status`
returns `unknown` if any status is unknown — so `overall` can never read
`pass`. Built verbatim, every healthy connected repo would carry a
permanent "Finish setup" line for the life of the deployment. Home must
never use `overall` as a "setup finished" signal, now or later.

### Home and the wizard share one completion contract

Home's mid-setup predicate is derived from `wizardStepSelection.ts`'s own
`deriveCompletion` — the exact function `ConnectWizard.tsx` uses — never a
second implementation:

```ts
isMidSetup(completion) = !completion.identify || !completion.dispatchable
```

`configure` and `posture` are deliberately excluded: `configure` can be
skipped on a working preset baseline and still reach `dispatchable`
(`deriveCompletion` derives it from downstream signals, not a real
"finished" marker — see the W2.2 section above), and posture is a *choice*,
not unfinished setup — a repo deliberately left at `observe` must never
nag. `features/home/hooks/connectState.ts`'s `resumeStepNumber(completion)` is
`WIZARD_STEPS.findIndex(selectStep(WIZARD_STEPS, completion, undefined)) +
1` — the label only; the resume line's link is always the bare
`/connect/:owner/:repo` URL, which re-derives this exact step itself, so
the two can never disagree about where the user lands.

### The operational-evidence exclusion — a population filter, not a completion change

A repository registered before this wizard existed can never satisfy
`isMidSetup`'s completion contract — there is no path back to a "finished"
state for it, so it nagged the resume line forever. `operationalEngineKeys`
(`features/home/hooks/connectState.ts`) is a **candidate filter** applied by
`useConnectStates` *before* the per-repository onboarding read, not a change
to what `isMidSetup`/`deriveCompletion` mean: a repository is dropped from
the resume-line population entirely once it clears either arm of a
disjunction —

```ts
operational = (a project is bound to this engine_key) OR
              (a workspace for this engine_key is PR-bearing, per hasPrBearingWorkspace)
```

Both arms are load-bearing, confirmed against a live measurement rather than
assumed: on the instance the bug was reported against, `GET /api/workspaces`
held zero rows for the affected repository (its PR-bearing work had aged out
of the non-archived bucket `listWorkspaces()` reads — the same limitation
`hasPrBearingWorkspace` itself carries, see above), so the workspace arm alone
read `false` for exactly the repository this exclusion exists to fix; only
the bound-project arm resolved it. Conversely a repository with PR-bearing
workspaces and no project of its own needs the workspace arm — a
project-only rule would miss it.

The project arm keys strictly on the project's **bound** `engine_key`, never
on `intended_engine_key`: intent is stamped by this same wizard's own first
finish (see "Finishing," step 3 above), so keying on it would mark every
wizard user's repository operational and suppress the line for precisely the
population this exclusion must keep serving. A project that only *intends*
toward a repository is not evidence the repository is done with the wizard.

This is deliberately **not** "excludes engines the wizard never touched" —
that predicate is not computable from data that exists today.
`onboarding_status(cfg)` returns a default-constructed `OnboardingState` when
no state file exists at all, byte-identical to "registered and did nothing
since" (`alan/repo_scaffold.py`), and `EngineSummary` carries no registration
provenance a client could key on instead. The derivable substitute — no
scaffold PR, no protection applied or deferred — is exactly `isMidSetup`'s
own genuinely-mid-setup population, which three existing end-to-end specs
already pin as required to keep its resume line. Building the "never
touched" version would need a new provenance field that is necessarily
absent on every repository registered before it exists, including the one
this exclusion was written for.

`useConnectStates` reads `workspaces`/`projects` as **props** (Home passes
`live.items`/`projects.items`, the same polled lists Home's tables already
render — never the tab-selected `workspaces` binding, which would swap
buckets on tab switch and flicker the predicate), not a second fetch; the
hook's own fan-out effect is gated on `projects !== null` only, so the
Archived tab's permanently-`null` `live` bucket never blanks the resume line
for a repository whose evidence comes entirely from the project arm.

### Home never calls the side-effecting preflight route

`runEnginePreflight` is a `POST` with side effects (it writes
`engine_preflight.json` and appends to the pinned-work ledger) — Home's
`features/home/hooks/useConnectStates.ts` never calls it. Preflight was considered
and rejected as the resume-line trigger for the same reason: it is a
*readiness* signal re-run at step 4, so a fully set-up repo whose preflight
later degrades would sprout a bogus "Finish setup" line.

### The blank-slate predicate

Home's full-page blank slate (`features/home/components/ConnectBlankSlate.tsx`) renders
exactly when
`useEngines().loaded && !useEngines().loadFailed && switcherEntries(engines, defaultEngineKey).length === 0`
(`lib/fold.ts`). `switcherEntries` lists nothing only when every visible
engine is the listed default and that default's `forge` is null. `forge` is
not a discriminator for every engine in the listing, since `EngineConfig`
permits an ordinary registered engine with `forge=None`; the rule reads it
only on the listed default. There, `forge` is null exactly for
`Source.unconfigured` (`alan/default_profile.py`): a configured default (an
installed profile or the owner profile) always lists a forge target, and the
unconfigured placeholder never does. Both sides are pinned by
`tests/test_default_engine_boot_unconfigured.py` and
`tests/test_default_engine_boot_configured.py`. A configured lone default is
therefore a connected repository, not the blank slate: Home redirects to its
dashboard and the switcher and drawer list it as one entry. This supersedes
Tier-1 #7 for a configured default (#1944); an unconfigured install, which
has no installed profile and a placeholder default with no forge target,
still gets the blank slate. `hasNonDefaultEngine` no longer feeds Home — it
still drives only the multi-repository labels (the Health findings
repository label and the workspace-header repository badge), which exist
to tell repositories apart. `loaded` guards against a flash of the blank slate before the
first `GET /api/engines` settles — it is set in both the resolve and reject
arms of the fetch, so a failed engine list still counts as "settled" rather
than pinning Home in `Loading…` forever. `loadFailed` is the companion flag
that keeps "settled" from being read as "confirmed empty": it is true only
on the reject arm, so a transient `/api/engines` failure falls through to
the populated branch (rendering on the last-known, possibly stale, engine
list) instead of manufacturing a false "no repositories connected" blank
slate for a user who has connected repos. It clears back to `false` the
next time a fetch (SSE-triggered refetch or the 15s fallback poll)
succeeds.

### The host-setup line's credential predicate

"Is the host's forge credential set up at all" is answered by
`useConnectFlowState.ts`'s `hasTransportRecord(secrets)`: true when *any*
secret record's name ends with `:transport`, regardless of which engine it
belongs to. Every secret record is engine-scoped (`{engine}:{purpose}`,
`alan/secrets_store.py:55`) — there is no host-level record — and in the
blank state there is no engine to key on anyway, so keying this on the
default engine's slug would reintroduce the deployment-substrate confusion
Tier-1 #7 exists to kill. Paired with the control token's presence
(`useControlTokenPresent.ts`, backed by a `controlToken.ts` subscriber so a
same-tab `writeControlToken`/`clearControlToken` call is reflected without
a remount), the two clauses combine into one client-derived line — no new
inbox item kind, no new endpoint; Home never becomes a second settings
page.

`useConnectStates.ts` holds its own `transportPresent` as a tri-state
`boolean | null` rather than mirroring `hasTransportRecord`'s boolean
directly: `null` means "never fetched yet, or the last `listSecrets()` call
failed," and is deliberately read as present (clause omitted) when building
the line, since the client does not actually know the credential is
missing in that state and must never assert it is. A successful fetch
always overwrites it with the definitive boolean; a failed fetch leaves the
previous value untouched, so a transient error after a known-missing read
keeps the clause rather than silently dropping it.

### The mobile-audit "home" screen is captured populated, not blank

The roadmap's DoD assumes a stock deployment's mobile-audit run captures
Home in the blank state (the default engine carries a forge identity but no
*connected* repository). That assumption does not hold against the delivered
`mobile-audit.spec.ts`: `seedEngineConfigEditorFixture` and
`ensureConnectStep3FixtureEngine` both register a non-default engine
(`e2e-mobile-audit-co/engine-config-fixture`, `e2e-mobile-audit-connect-co/step3-fixture`)
before the screen loop starts, and `"home"` (route `/`) is the first entry in
`screens.ts`. By the time the loop visits it, the switcher already
lists a non-default engine, so Home renders the **populated** branch — complete with a
resume-setup needs-you line for the still-mid-setup `step3-fixture` engine —
never the 12a blank slate. `baseline.json`'s `home` entry
(`tapTarget: 1, overflow: 1, contrast: 0`) is therefore a measurement of that
populated render, unchanged by this item because nothing on that render path
changed; it needs no edit here. The blank slate itself is not currently
covered by the mobile-audit ratchet at all — its coverage is the Playwright
assertions in `home-connect-states.spec.ts`, not this audit.

### Mobile audit and e2e

`connect-wizard-step4` is registered in
`alan/web/e2e/mobile-audit/screens.ts`, reusing W2.3's `step3-fixture` engine
via the deep-link override (`/connect/<owner>/<repo>/posture`) rather than
seeding a second fixture engine — a deep link honours any *built* step
regardless of completion, per the step-selection contract above. Its
baseline (`alan/web/e2e/mobile-audit/baseline.json`) was captured against
that fixture in the same change; the four posture radio inputs measure
13×16px against the 44px tap-target floor (`tapTarget: 4`) — an accepted
measurement artifact of `PostureRadioRow`'s own layout, not a real 13px tap
target: the wrapping label supplies the min-h-11 clickable region, but the
audit measures the rendered `<input>` element itself.
`alan/web/e2e/connect-wizard-step4.spec.ts` drives the real
confirm-dialog and finish flows against a locally seeded git remote, mirroring
connect-wizard-step3.spec.ts's technique (no network, no real GitHub
credential) — full `pass` preflight is not reachable there (the `gh` stub's
`gh api user` returns no login, so `reviewer_distinct_from_author` never
resolves past `"unknown"`), so its assertions check that the readiness chip
settles on *a* real register rather than a specific one.
