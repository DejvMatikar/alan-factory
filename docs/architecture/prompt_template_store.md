# Per-engine prompt template store

`alan/prompt_templates.py` — a near-leaf renderer that lets an operator override the system prompt a specific engine's team runs, through the API, without a code change (the editing UI has been removed — see "UI (removed)" below; the saved text still applies at render time and shows read-only, marked "overridden", in the agent catalogue). Covers the full per-engine-prompt-template-store build brief: the foundation (DoD 1-7 — overrides, save-time validation, the confirmation gate, the non-removable safety clause) plus the additive stage (DoD 8-9 — override history, rollback, and drift), in `alan/prompt_override_history.py`.

## Storage: `EngineConfig.prompt_overrides`

`alan/engine_config.py` adds two pieces, both additive (default `{}`, so every existing persisted engine round-trips unchanged):

```python
class PromptOverride(BaseModel, frozen=True):
    text: str
    base_fingerprint: str

class EngineConfig(BaseModel):
    ...
    prompt_overrides: dict[str, PromptOverride] = Field(default_factory=dict)
```

`prompt_overrides` is sparse and keyed by `alan.prompt_templates.override_key(team, role)` (`"<team>/<role>"`, e.g. `"implementation/implementer"`). `base_fingerprint` is a sha256 hex digest of the (team, role)'s raw pre-substitution base prompt text, captured at save time — unused by the renderer today, seeded from day one so the deferred drift-indicator stage can start comparing it against the live base's fingerprint without a schema migration.

Every write builds a **fresh** `dict` (`dict(current.prompt_overrides)` then `[key] = ...`, in `engine_config.apply_prompt_override_and_persist`) and installs it via `current.model_copy(update={"prompt_overrides": new_overrides})` — never `cfg.prompt_overrides[key] = ...` in place. `EngineConfig` is frozen, but a plain `dict` value on a frozen Pydantic model is still mutable in place; building a new dict object for every write is what keeps a previously-pinned `EngineSnapshot.config` (see `engine_config.md` "Workspace selection and pinning") inert to a later override save — `tests/test_engine_snapshot.py` asserts the two dict objects are never the same identity.

## Fail-closed overridable set

`alan/prompt_templates.py::OVERRIDABLE_ROLES` is the single source of truth for which (team, role) pairs may carry an override — exactly three, matching the build brief's "not every team is overridable" boundary:

| (team, role) | kind | required content |
|---|---|---|
| `("implementation", "implementer")` | `composed` | `{{ENVIRONMENT}}`, `{{SUCCESS}}`, `{{REPO_GUIDANCE}}` |
| `("implementation_fix", "implementer")` | `composed` | `{{ENVIRONMENT}}`, `{{SUCCESS}}`, `{{REPO_GUIDANCE}}` |
| `("verification", "judge")` | `wholesale` | `VERDICT:`, `DIFF_FINDINGS_CRITICAL:` |

Internal orchestration teams (`decomposer`, `research` orchestrator/synthesizer, etc.), the merge-execution team (`pr_merge`), and the non-`judge` verification roles (`diff_reviewer`, `scenario_runner`) are excluded — `is_overridable(team, role)` returns `False` for anything not in the table, and both the renderer (`render_prompt`, defense-in-depth) and the route (`POST /api/engines/{owner}/{repo}/prompt-overrides`, the actual enforcement point) reject any other pair.

`kind="composed"` roles are cross-cutting scaffolding around three sub-blocks that are computed per-invocation (the environment block depends on `cfg.gate_commands`/`warmup_commands`; the success block depends on `cfg.gate_commands`; the repo-guidance block depends on whether the worktree has a `CLAUDE.md`) — an override replaces only the *static* scaffolding text and must re-declare the three placeholder tokens so those sub-blocks still get spliced back in at render time. `kind="wholesale"` (`verification/judge`) has no required placeholders — the judge prompt is not composed from environment/success/repo-guidance sub-blocks — but must retain the two machine-readable markers (`VERDICT:`, `DIFF_FINDINGS_CRITICAL:`) the merge machinery downstream parses out of the judge's actual output.

## Rendering: `render_prompt`

```python
def render_prompt(
    cfg: EngineConfig, team: str, role: str, base_text: str, *,
    environment_block: str = "", success_block: str = "", repo_guidance_block: str = "",
) -> str: ...
```

- **No override configured** (`cfg.prompt_overrides.get(override_key(team, role)) is None`): returns `base_text` **verbatim, byte-for-byte** — no substitution, no safety-clause suffix. This is the regression floor: an unconfigured engine's prompt is provably identical to today's hardcoded text, because the renderer never touches it.
- **Override configured**: for a `composed` role, `{{ENVIRONMENT}}`/`{{SUCCESS}}`/`{{REPO_GUIDANCE}}` in the override text are replaced with the caller-supplied blocks (computed once per render call by the same functions the pre-override code already used — `render_environment_block`, `generic_success_block`, `repo_guidance_block` in `alan/teams/implementation.py`); a `wholesale` role's text is used as-is. Then one or two non-removable safety clauses are appended **after** the (possibly-substituted) text:
  - `VERIFIER_ANTI_SELF_APPROVAL_CLAUSE` — always, for `("verification", "judge")` only.
  - `MERGE_GATE_CLAUSE` — for **any** overridable role, whenever `posture_rank(cfg.posture) >= posture_rank(Posture.self_merge)` (evaluated fresh from `cfg.posture` at render time, so it tracks the pinned snapshot's posture, not the live registry).

  Because both clauses are appended as plain string suffixes *after* the override's own text has already been substituted, no wording inside the override — including one deliberately written to negate, quote, or contradict the clause — can remove or precede it in what the worker actually receives.

`render_prompt` raises `UnoverridableRoleError` (a defense-in-depth fail-closed check; the route is the real gate) if called with a (team, role) outside `OVERRIDABLE_ROLES`.

## Call sites — the three teams' render seam

- `alan/teams/implementation.py::render_system_prompt(cfg, worktree_path)` and `alan/teams/implementation_fix.py::render_system_prompt(cfg, worktree_path)` build the pre-override composed text exactly as before (`_SYSTEM_PROMPT` for the default engine, `_compose_generic_system_prompt(cfg, worktree_path)` otherwise), then pass that text as `render_prompt`'s `base_text`, threading the three blocks through as keyword args. Because these two functions' own composition logic is untouched, `render_prompt`'s no-override fast path reproduces their historical output exactly.
- `alan/teams/verification.py::roles_for_config(cfg)` passes `_JUDGE_PROMPT` (module constant, untouched) through `render_prompt(cfg, "verification", "judge", _JUDGE_PROMPT)` to build the `Role("judge", ...)` tuple's `system_prompt`. The module-level `ROLES`/`_JUDGE_PROMPT` constants (used only as the `VerificationTeam.roles` dataclass-field default, never for an actual run — `plan_workflow`/`_collect_outputs` both call `roles_for_config(cfg)`) are unaffected.

## Save-time validation and the confirmation gate

`POST /api/engines/{owner}/{repo}/prompt-overrides` (`alan/routes/engines.py`) — body `{team, role, text, confirm=false}` — is the sole write path:

1. 404 if the engine isn't registered.
2. 422 `team/role is not an overridable prompt` if `(team, role)` isn't in `OVERRIDABLE_ROLES`.
3. 422 naming the missing placeholder(s)/marker(s) (`prompt_templates.missing_placeholders`/`missing_markers`) — structural, mechanical checks; they verify required elements survive, not that the wording is "good".
4. 409 `requires_confirmation` (config left untouched) when the override is safety-critical (`team == "verification"`, unconditionally — or `posture_rank(cfg.posture) >= posture_rank(Posture.self_merge)` for **any** overridable role, since some engines can self-merge) and `confirm` was not passed as `true`. Mirrors `POST /api/engines/{owner}/{repo}/posture`'s `PostureChange(confirm: Literal[True])` gate — one explicit flag, no two-step handshake.
5. On success: computes `base_fingerprint` from the current in-code base prompt text (`_BASE_PROMPT_TEXT`, a route-local mapping — kept out of `prompt_templates.py` itself, since that module sits below `alan.teams` in the import-linter layer stack and cannot import the team modules' prompt constants), and persists via `engine_config.apply_prompt_override_and_persist` (below), and publishes `engine_updated` on the pubsub.

It is the sole write path structurally, not merely by convention: `prompt_overrides` is a plain field on the full-`EngineConfig` body that `PATCH /api/engines/{owner}/{repo}` and `POST /api/engines` also accept, so those generic routes would otherwise be an unvalidated, unconfirmed side door onto a safety-critical field. They are closed off the same way `update_and_persist` already closes the equivalent side door for `posture`:

- `POST /api/engines` (`create_engine`) discards the body's `prompt_overrides` outright (replaces it with `{}`) before registering — a brand-new engine can never be created with overrides pre-set.
- `PATCH /api/engines/{owner}/{repo}` (`update_engine` → `engine_config.update_and_persist`) re-reads `prompt_overrides` fresh from the registry under the same lock as the write, exactly like it already does for `posture`, and silently reverts any delta in the PATCH body — so a full-config PATCH cannot change overrides no matter what it sends.
- The only function that actually writes a new `prompt_overrides` value is `engine_config.apply_prompt_override_and_persist`, called exclusively by `save_prompt_override` after the validation and confirmation checks above have passed — mirroring how `set_posture_and_persist` is the only function that writes a new `posture` value, bypassing `update_and_persist`'s revert.

## Import-linter position

`prompt_templates` sits directly above `engine_config` in the `pyproject.toml` layers list (between `secrets_store` and `engine_config`) — below every `alan.teams.*` module, so `alan/teams/implementation.py`, `implementation_fix.py`, and `verification.py` can import it, and above `engine_config`, so it can import `EngineConfig`/`Posture`/`posture_rank`. It cannot import `alan.teams.verification` (a higher layer), which is why the `VERDICT:`/`DIFF_FINDINGS_CRITICAL:` marker literals are duplicated by value rather than imported — `tests/test_prompt_templates_marker_sync.py` asserts the duplicated constants match `alan.teams.verification.VERDICT_PREFIX`/`DIFF_FINDINGS_CRITICAL_PREFIX`.

`prompt_override_history` sits directly below `engine_config` and above `engine_registry_store` in the same layers list — below every `alan.teams.*` module (it doesn't need team access; the (team, role) strings it's keyed by are opaque to it), and above `engine_registry_store` so it can call `engines_root()`. It intentionally imports neither `alan.engine_config` nor `alan.prompt_templates`: `HistoryEntry` is its own plain model, not `PromptOverride`, and `has_drifted` lives in `prompt_templates.py` instead (which already imports `PromptOverride` and sits above `engine_config`) — keeping the history store a narrow, dependency-light append log.

## UI (removed)

The "Prompt overrides" section of the repository-scoped engine-config screen is gone: `PromptOverridesDisclosure`, `PromptOverrideRow`, `PromptOverrideHistoryPanel`, `PromptOverrideConfirmModal`, the `usePromptOverrides` hook, their tests and `e2e/prompt-override-editor.spec.ts` were deleted, together with the `savePromptOverride`, `getPromptOverrideHistory` and `rollbackPromptOverride` wrappers in `alan/web/src/api.ts` and the MSW history handler. Nothing in the frontend writes an override any more. The backend is unchanged: the save, history and rollback routes, the stored `prompt_overrides`, the history store and render-time application all behave exactly as described in this document, and the generated schema components (`PromptOverride`, `PromptOverrideRequest`, `HistoryEntry`, …) stay in `schema.ts`.

What the frontend still shows is the **applied** result: `GET /api/engines/{owner}/{repo}/agents` builds each agent's prompt through the same `render_prompt` the run path calls, so an overridden `implementation/implementer`, `implementation_fix/implementer` or `verification/judge` entry carries the override text and `prompt_overridden: true`, and the catalogue row is marked "overridden" (see [agent_catalogue.md](agent_catalogue.md) and the "Agent catalogue" section of [frontend.md](frontend.md)). It is read-only; changing an override is an API call.

## History, rollback, and drift (DoD 8-9)

`alan/prompt_override_history.py` is an append-only history store, one immutable entry per override save, keyed by `(repo_slug, team, role)`:

```python
class HistoryEntry(BaseModel, frozen=True):
    version: int
    text: str
    base_fingerprint: str
    saved_at: str
```

Storage layout mirrors `alan.handover`'s O_EXCL immutable-slot pattern rather than `engine_registry_store`'s single-file-per-key model, because a history is a *sequence* of immutable entries, not one mutable record: each `(repo_slug, team, role)` gets its own directory under `engine_registry_store.engines_root() / "prompt_override_history" / <sha256(f"{repo_slug}\n{team}\n{role}")>`, and each save claims the next `{version:06d}.json` slot with `os.open(..., O_EXCL)` — scanning upward from 1, exactly like `handover._claim_handover_slot`. A race on the same version number resolves by the loser continuing to scan, never by clobbering the winner's file. `list_entries` returns ascending by version (oldest first); `get_entry` fetches one version directly. Nothing ever rewrites an existing `NNNNNN.json` — the module has no delete/update path, only `append_entry`.

**Every override save appends a history entry.** `POST /api/engines/{owner}/{repo}/prompt-overrides` (`alan/routes/engines.py::save_prompt_override`) calls `prompt_override_history.append_entry(...)` immediately after `engine_config.apply_prompt_override_and_persist` succeeds, with the same `text`/`base_fingerprint` just persisted to `EngineConfig.prompt_overrides`.

**Rollback is a forward-write, never a rewrite of history.** `POST /api/engines/{owner}/{repo}/prompt-overrides/rollback` (body `{team, role, version, confirm}`):

1. 404 if the engine isn't registered, or if `version` names no history entry for `(team, role)`.
2. 422 if `(team, role)` isn't overridable, or (defense-in-depth — a normally-saved entry can never actually fail this) if the historical entry's text no longer satisfies `missing_placeholders`/`missing_markers`.
3. 409 `requires_confirmation` — same safety-critical rule as save (`team == "verification"`, or any overridable role when `posture_rank(cfg.posture) >= posture_rank(Posture.self_merge)`) — when `confirm` isn't `true`. Config is left untouched.
4. On success: persists the target version's **exact stored `text` and `base_fingerprint`** — both taken verbatim from the `HistoryEntry`, never recomputed — via the same `engine_config.apply_prompt_override_and_persist` the save endpoint uses, then appends a **new** history entry carrying that same text/fingerprint. Rolling back from version 3 to version 1 produces version 4, not a rewound version 3 — `tests/test_prompt_override_history.py::test_rollback_appends_new_history_entry_not_destructive` asserts the count only grows and every prior entry's content is unchanged.

Reusing the historical entry's *original* `base_fingerprint` (rather than recomputing it against today's base) is deliberate: it is what makes drift correct across a rollback. If it were recomputed, rolling back to old wording would silently report as "not drifted" even though the restored text was authored against a base that has since changed — see `test_rollback_preserves_original_base_fingerprint_not_recomputed`.

**Drift** (`alan.prompt_templates.has_drifted(override, current_base_fingerprint)`) is one comparison: `override.base_fingerprint != current_base_fingerprint`. `GET /api/engines/{owner}/{repo}/prompt-overrides/history?team=&role=` returns both the entry list (newest first) and this comparison in one response — `{team, role, entries, drifted}` — computed against `_base_fingerprint(team, role)` (the same route-local function the save endpoint uses to seed a new override's fingerprint). An engine with no override for `(team, role)` always reports `drifted: false` (there is nothing to compare); an override whose stored fingerprint still matches the current base text reports `false` too. Only an override derived from a base that has since changed reports `true`.

The history/drift GET is read-only (no control-token dependency, like `get_engine`/`get_engine_preflight`); the rollback POST requires the control token like every other mutating engine route.

## UI (history/drift, removed)

The history panel, the eager drift badge, the rollback button and the hooks behind them (`usePromptOverrideHistory`, `usePromptOverrideRollback`) were removed with the rest of the overrides UI; the history and rollback endpoints above are unchanged and have no frontend caller. The drift comparison is still computed by `GET .../prompt-overrides/history`, which nothing in the app currently reads.
