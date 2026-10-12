# Config proposal — `alan/engine_config_proposal.py` + `alan/engine_stack_detect.py`

`propose_config(engine_key, *, base_preset=None)` inspects a **mixed-stack** target
repository read-only (via a Claude Code chat turn) and proposes an engine configuration
delta — never applies one. A human reviews it in the existing engine-config editor and
must explicitly `PATCH`/`POST` it through the ordinary, already-validated
`create_engine`/`update_engine` routes to make it real; this workspace adds no new
persistence path.

## Fail-closed resolve — the load-bearing invariant

`propose_config` never calls `explore_chat_repo.needs_repo` or
`engine_config._resolve_scope_config` — both have permissive "fall back to the default
engine" semantics that exist for chat surfaces where *some* answer beats none. A config
proposal is the opposite: if the target engine isn't registered, or is registered with
no `forge`, this must abort rather than silently inspect (and propose a configuration
derived from) whatever repo happens to be ambient — which, for every engine dispatched
by this same process, is alan's own checkout.

So `propose_config` does its own direct check before anything else runs:

```python
cfg = engine_config.lookup_by_slug(engine_key)
if cfg is None or cfg.forge is None:
    raise ProposalAbortedError(...)
```

Only once that passes does the pipeline compute a `ForgeTarget` **from `cfg.forge`
directly** (`ForgeTarget.from_forge_config`, never `forge_target_for_workspace`'s
three-tier fallback that ends at alan's own `_DEFAULT_FORGE`) and provision a checkout
scoped to that target. `explore_chat_repo.provision` — the same per-session read-only
clone mechanism project-bound chats already use — is the only thing that ever populates
that checkout directory; a provisioning failure (`RepoProvisionError`: auth, network, or
the engine vanishing between the two lookups) aborts the same way. No new checkout path
is invented, and no filesystem write-protection is relied on — read-only execution comes
from the chat turn's own tool allowlist (see "Inference" below), matching
`docs/architecture/explore_chat.md`'s reused read-only-chat contract.

## Pipeline

1. **Resolve + lock** — the fail-closed resolve above, then a non-blocking per-slug lock
   (`_slug_lock`, a module-level `set[str]` guarded by a `threading.Lock`, mirroring
   `alan.engine_config`'s own `_registry_lock`/`_registry` pattern). A second proposal
   for the same slug while one is in flight raises `ProposalAbortedError` immediately —
   two concurrent pipelines would otherwise race on the same scratch checkout path.
2. **Provision** — `explore_chat_repo.provision(engine_key, dest)` clones the target's
   own default branch into `dest`, a scratch directory under
   `alan.teams._pr_state_paths._engine_state_dir(forge_target)` (i.e. under
   `ALAN_STATE_ROOT`, an existing state-isolation root — no new `ALAN_*_ROOT` var is
   introduced). `dest` is `shutil.rmtree`'d in a `finally` on every exit path, success or
   failure — it is scratch space, never a persisted artifact.
3. **Stack pre-pass** — `alan.engine_stack_detect.detect_stack(dest)`, a deterministic,
   depth-1 (top-level files only, no recursion) scan for each stack's build-file
   markers (`*.csproj`/`*.sln`/`*.fsproj` for dotnet, `package.json` for node,
   `pyproject.toml`/`setup.py`/`setup.cfg`/`requirements.txt`/`Pipfile` for python). It
   recommends the stack with the most marker hits (ties broken by a fixed
   `dotnet`/`node`/`python` priority order, also the fallback when nothing matches at
   all) and reports every other stack with any hit as `additional_stacks` — the "flags
   additional stacks detected" half of the acceptance criteria. This pass runs before
   any model inference and never touches the network.
4. **Baseline** — `alan.engine_presets.build_config(preset_name, owner, repo)`, where
   `preset_name` is the caller-supplied `base_preset` override if given, else the
   pre-pass's `recommended_preset`. An override re-runs everything from this step
   onward against the new preset — the pre-pass result is informational, not sticky.
5. **Inference** — one `alan.claude_cli.run_chat_turn` call with `allowed_tools=None`
   (the existing headless read-only chat default: `Read`/`Grep`/`Glob`/`WebSearch`/
   `WebFetch`/`Bash(git log|diff)`/`ls`/`cat`/`gh pr list|view`, plus the unconditional
   `Write`/`Edit`/`NotebookEdit` denylist baked into `run_chat_turn`'s `claude` CLI
   invocation) and `cwd=dest` — the model only ever sees the target's own checkout,
   never alan's. The system prompt embeds the baseline preset's current value for every
   proposable field and asks for a JSON object containing only the fields that should
   change.
6. **Repair-or-reject** — the model's `result_text` is parsed as JSON (trying a fenced
   ` ```json ` block, the whole trimmed text, and a `{...}` substring extraction, in
   that order — the only "repair" applied to free-form model prose), non-dict output is
   rejected, and any key outside `_PROPOSABLE_FIELDS` is silently dropped **before** the
   one and only `EngineConfig.model_validate` pass. That single pass either succeeds or
   raises `pydantic.ValidationError`, which is always caught and re-raised as
   `ProposalUnrepairableError` — no raw pydantic (or JSON-decode) error ever reaches the
   route.
7. **Validate** — `validate_draft(candidate)` (a one-line reuse of
   `alan.engine_preflight._check_flow_graph`, i.e. `engine_config._validate_before_register`
   under the hood) runs routing-graph and posture-orthogonality validation **without
   persisting** and returns a `Finding` as part of the response — a proposal that would
   fail validation is not blocked or dropped, it is shown to the human as a finding
   alongside the diff. This is the same function `POST .../preflight` check 5 already
   uses and `POST .../config-draft-validate` (below) reuses again — one validator, three
   call sites.
8. **Diff** — `ProposalFieldDiff` per field in `_PROPOSABLE_FIELDS`, each carrying the
   preset baseline's and the candidate's JSON-dumped value plus a `changed` flag.

`ConfigProposalResult` bundles `slug`, `base_preset` (the preset actually used),
`detected_stacks`, `additional_stacks`, `proposed_config` (the full validated
`EngineConfig`, not just the delta), `diff`, and `validation`.

## `_PROPOSABLE_FIELDS` — what the model may ever change

```python
{"gate_commands", "warmup_commands", "base_tools", "team_tools", "roles",
 "transition_graph", "flow_policy"}
```

Every other `EngineConfig` field — `repo`, `code_quality`, `forge`, `ci`, `posture`,
`pool_size`, `preset`, `prompt_overrides`, `dependency_sweep` — is excluded and always
carries the baseline preset's value straight through into `candidate` untouched, because
the merge step only ever overwrites keys already present in `_PROPOSABLE_FIELDS` before
calling `model_validate`. `posture` in particular must never be model-set (a safety
boundary, not an oversight): the baseline preset's `Posture.observe` survives every
proposal unconditionally. Repository conventions (`CONVENTIONS.md`-equivalent content)
are excluded on purpose too — they are not an `EngineConfig` field at all and belong to
`alan.repo_scaffold`'s separate onboarding flow, not this one; if the model's JSON output
includes a `conventions` key regardless, it is silently dropped by the same
not-in-`_PROPOSABLE_FIELDS` filter as any other unrecognized key, with no special-cased
handling required — dropping it is a no-op by construction, not a validated rejection.

## Routes (`alan/routes/engines.py`)

- `POST /api/engines/{owner}/{repo}/config-proposal` — control-token guarded (it drives
  a clone plus a paid model turn against the target's live credentials, unlike the
  unauthenticated `GET` surfaces on this router). Body: `{"base_preset": "dotnet" |
  "node" | "python" | null}`. Success returns `ConfigProposalResult` (200). Any
  `ProposalAbortedError`/`ProposalUnrepairableError` maps to `422` with a
  `branch_protection_compat.Finding` body (`check="config_proposal"`, `status="fail"`,
  `message=str(exc)`, and a fixed `remedy` naming the manual preset-seed fallback in the
  editor) — this is the "couldn't auto-propose, fall back to manual seed, and the
  finding stays visible" resolution the acceptance criteria require; the editor is never
  left blocked or empty because the human always still has the ordinary seed-from-preset
  path this workspace leaves unchanged.
- `POST /api/engines/{owner}/{repo}/config-draft-validate` — control-token guarded, body
  is a full `EngineConfig` (the human-edited draft), repo-mismatch checked the same way
  `update_engine` checks it, and the response is the same `Finding`
  `validate_draft`/step 7 above produces — run at review time on every edit, not only at
  save time, satisfying "surfaces as a finding in the editor at review time, never only
  on save" without a second validator.

## Layering

`alan.engine_config_proposal` sits directly above `alan.explore_chat_repo` in
`[tool.importlinter]`'s layers list (it imports `explore_chat_repo.provision`,
`claude_cli.run_chat_turn`, `engine_presets.build_config`,
`engine_preflight._check_flow_graph`, `engine_config.lookup_by_slug`, and
`teams._pr_state_paths._engine_state_dir` — all of which sit far lower already) — high enough
that `alan.routes.engines` (the `routes` layer, near the top) can import it freely.
`alan.engine_stack_detect` has no `alan.*` dependencies of its own; it is placed
immediately below `engine_preflight` purely to keep the two new modules' layer entries
adjacent to their nearest conceptual neighbor, not because anything requires it there.
