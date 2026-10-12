# Stack presets — `alan/engine_presets.py` + `GET /api/engine-presets`

A stack preset is a complete, non-repo-specific `EngineConfig` **skeleton** for one
language toolchain (`dotnet`, `python`, `node`) — the gates, dependency-warmup step,
tool allowlist, roles, workflow graph, and posture that fit that stack out of the box.
Presets exist so a future project-creation wizard can hand a user "start from the
.NET/Python/Node preset" instead of hand-building an engine from scratch, and so the
concrete driver for this work — a hand-built .NET engine that had no build/test gates
and could not invoke the .NET toolchain at all — has a template that closes that gap.

## What a preset is, and is not

`alan/engine_presets.py` builds each preset via
`default_engine_config().model_copy(update={...})`: it starts from the alan engine's own
defaults (roles, `team_tools`, `transition_graph`, `flow_policy`) and overrides only what
must differ — `repo` (a sentinel identity, see below), `code_quality`/`forge`/`ci` (all
nulled — presets carry no per-project identity), `posture` (`Posture.observe`, diverging
from the default engine's `Posture.autonomous`), `preset` (the preset's own name), and the
language-specific `gate_commands`/`warmup_commands`/`base_tools`.

Presets are **templates, never live engines**: `dotnet_preset()`/`python_preset()`/
`node_preset()` build plain in-memory `EngineConfig` values and are never passed to
`register()` or `register_and_persist()`. They never appear in `engine_config`'s registry,
`GET /api/engines`, or the persisted `ALAN_ENGINES_ROOT` store. A wizard (out of scope
here) is expected to fetch a preset, overlay per-project identity (`repo`, `forge`,
`code_quality`, `ci`) the way it would for any hand-built config, and `POST` the result to
the existing `/api/engines` create path — this workspace deliberately does **not** add a
new "create an engine from a preset name" endpoint; the preset marker travels on the
normal engine-creation payload.

### Sentinel repos

Each preset's `repo` is a placeholder identity — `_preset/dotnet`, `_preset/python`,
`_preset/node` — since a preset by definition has no real target repository yet. Nothing
in `create_engine`/`update_engine` rejects a caller who submits one of these sentinels
verbatim; that is a wizard-side integration contract; `tests/test_engine_presets.py`
documents the garbage-in behavior with one test rather than adding rejection logic.

## The `preset` marker

`EngineConfig.preset: str | None = Field(default=None)` is a free-form string, not an
enum — it is never validated against the current preset name set. This is the entire
backward-compatibility story: `default_engine_config()` leaves it `None` (alan's own
engine is not built from a preset), every engine persisted before this change has no
`preset` key in its stored JSON at all, and `EngineConfig.model_validate` on a payload
missing the key falls through to the `None` default rather than raising — so a pre-existing
persisted engine still loads unchanged after this PR (`load_persisted_engines`,
`update_and_persist` round-trips). A later change to the set of named presets can never
break an engine that recorded a preset name that has since been renamed or retired.

## `forge=None` → `_DEFAULT_FORGE` fallback caveat

Every preset nulls `forge`. `engine_config.forge_target_for_workspace` and
`engine_preflight.run_preflight` both resolve a config's effective forge via that
function's three-tier fallback (snapshot → registry lookup by `engine_key` → module
constant `_DEFAULT_FORGE`, see `engine_config.md`). Calling `run_preflight` directly on a
raw preset therefore does **not** fail closed — it silently resolves to `_DEFAULT_FORGE`,
which is alan's own production repo (`DejvMatikar/alan`) and credential
(`ALAN_VERIFIER_GH_TOKEN`). A test (or a wizard integration) that wants to preflight a
preset-derived config for real must first overlay a genuine `ForgeConfig` — mirroring what
a wizard does at engine-creation time — and, in tests, must patch `subprocess` and
`secrets_store` before calling `run_preflight`; leaving `forge=None` on a preset and
calling `run_preflight` unmocked would authenticate as alan's own bot and hit GitHub for
real instead of producing a clean failure. `tests/test_engine_preflight.py` exercises this
as a "wizard simulation": a preset overlaid with a real test-double `ForgeConfig` plus
mocked credentials/subprocess.

## The dotnet preset — the load-bearing deliverable

`dotnet_preset()` is the only preset this workspace's acceptance criteria hinge on. It:

- widens `base_tools` with `Bash(dotnet:*)` — this is what makes
  `engine_preflight._check_tools_reachable` pass for the .NET toolchain out of the box.
  Neither the inherited `base_tools` nor the inherited `team_tools["implementation"]`
  (which grants `Bash(uv:*)`/`Bash(npm:*)`/`Bash(node:*)`/`Bash(python:*)`/`Bash(pytest:*)`
  but nothing `dotnet`-shaped) would otherwise reach a `dotnet ...` argv — this is exactly
  the gap the hand-built .NET engine that motivated this work had.
- defines non-empty `gate_commands`: `dotnet build`, `dotnet test`,
  `dotnet format --verify-no-changes` (build, test, and format-verification gates).
- defines a `warmup_commands` dependency-restore step: `dotnet restore`.

`python_preset()`/`node_preset()` are generic, tune-later skeletons, not attempts to run
green on an arbitrary repo and not copies of this system's own repo-specific gate set
(alan's own `gate_commands` reference alan's own package layout — `mypy alan/ tests/`,
`lizard --exclude alan/web/* alan/`, `npm --prefix alan/web run …`, `lint-imports`,
`pip-audit` — none of which is meaningful for an arbitrary Python/Node project). They
instead declare a minimal, generic gate set (`uv run pytest` / `uv run ruff check .` for
python; `npm run test` / `npm run lint` for node) reachable through the inherited
`base_tools`/`team_tools["implementation"]` `uv`/`npm` prefixes with no further widening
needed.

## The `ios` preset

`ios_preset()` follows the same `_skeleton("ios")` + `model_copy(update={...})` shape as
`dotnet_preset()`. It widens `base_tools` with nine entries —
`Bash(xcodebuild:*)`, `Bash(tuist:*)`, `Bash(swiftlint:*)`, `Bash(swift:*)`,
`Bash(swift-format:*)`, `Bash(periphery:*)`, `Bash(xcrun:*)`, `Bash(lizard:*)`, and
`Bash(bash scripts/gate.sh:*)` — because the iOS gate stack is a shell script, not a single
binary, and every tool that script shells out to individually must also be reachable in case
a worker invokes it directly.

`gate_commands` is `[["bash", "scripts/gate.sh"]]` — a single headless entrypoint, not an
expanded chain of `tuist generate` / `xcodebuild` / `swiftlint` / … invocations the way
`dotnet_preset()` lists `dotnet build`/`dotnet test`/`dotnet format` separately. The `bash`
form is deliberate over a bare `["scripts/gate.sh"]`: the script's executable bit is
controlled by the target app repository's own scaffold and may not survive it, so invoking it via `bash` does not depend on that bit.
The allowlist entry is the two-word prefix `Bash(bash scripts/gate.sh:*)`, never a bare
`Bash(bash:*)` — `_bash_allowlist_prefixes` splits the captured prefix on whitespace, so the
two-word form matches only this exact invocation shape, not arbitrary `bash ...` commands.
**`scripts/gate.sh` is not part of this repository** — it lives in the target app
repository; this preset declares the invocation shape only.

`warmup_commands` is `[["tuist", "install"]]`, reachable via the `Bash(tuist:*)`
allowlist entry. An iOS-preset engine's app is a Tuist project (`Project.swift` at its
repository root, with its external dependency manifest under `Tuist/Package.swift`
rather than at the package root), so the previous `swift package resolve` warm-up
was a no-op there — there is no root-level Swift package for it to resolve.
`scripts/gate.sh` (the app repo's own headless gate entrypoint) already runs
`tuist install` itself before `tuist generate`, so this warm-up does **not** remove
work from the gate — it **primes the per-slot dependency cache** once when a slot
is first cloned, so the gate's own `tuist install` call finds a warm cache instead
of a cold one. The win is real only because the prior resolve accomplished nothing
at all on this stack; it is bounded to that priming effect, not to shortening the
gate itself.

### Stack detection markers

`_STACK_MARKERS["ios"] = ("Project.swift", "Package.swift")`. A third marker considered
during planning — the `*.xcodeproj` bundle — is **deliberately dropped**: `detect_stack`
only inspects `_depth1_filenames`, which filters `entry.is_file()`, and an `.xcodeproj` is a
directory, so that marker could never match anything. Recording this as a stated deviation
from the original roadmap item text rather than silently omitting it.

`ios` is appended **last** in `_PRESET_PRIORITY = ("dotnet", "node", "python", "ios")`. This
governs **equal-count ties only** — `_pick_recommended` takes the maximum marker-hit count
first and only falls back to `_PRESET_PRIORITY` order to break a tie among stacks sharing
that maximum. So "ios last" does **not** mean "ios rarely wins": a repository carrying both
`Project.swift` and `Package.swift` scores two marker hits and outranks any single-marker
`dotnet`/`node`/`python` repo. First position in the priority tuple is also the fallback
recommendation when no marker matches at all (`_pick_recommended` returns
`_PRESET_PRIORITY[0]`) — appending `ios` last, rather than inserting it earlier, is what
keeps that fallback at `dotnet`, unchanged from before this preset existed. One further
accepted consequence: a plain SwiftPM *library* repository (`Package.swift` alone, no
`Project.swift`) also resolves to `ios` — a collision with what such a repo actually is, but
one this preset accepts rather than special-cases.

### Per-slot DerivedData

`cache_env` is `{"DERIVED_DATA_PATH": "deriveddata"}`. Outside any slot lease,
`resolve_cache_env` resolves this the same way as every other cache entry: a **fixed
directory**, `roots.cache_root() / "deriveddata"`, i.e. `state_root().parent / "cache" /
"deriveddata"`. This honours `ALAN_STATE_ROOT` redirection exactly like every other preset's
cache dir, so tests and non-production runs never write into the live `~/.alan/cache/` tree.

For the length of a slot lease, `DERIVED_DATA_PATH` is overridden to a location unique to
the leasing engine and slot: `alan/teams/_slot_derived_data.py`'s `per_slot_derived_data`
context manager, invoked from both yield sites of `leased_with_affinity`
(`alan/teams/_slot_affinity.py` — see `slot_pool.md`'s "Shared affinity ctxmgr" section),
saves the ambient cache env, swaps in `slot-deriveddata/<sanitized-slug>-<sha8>/slot_<N>`
for `DERIVED_DATA_PATH` only, and restores the saved value in a `finally` when the lease
ends. Two concurrent iOS builds across the worker pool therefore never share a DerivedData
directory as long as each holds a distinct slot — this is the precondition a co-hosted
engine's own in-flight cap (#1416) needed before it could ever let two of that engine's
workspaces build at once.

No change was needed to the slot pool, `resolve_cache_env`, or
`_CACHE_ISOLATION_OVERRIDES` (`engine_config.md`'s "Stack cache configuration" section) to
get this: the swap is keyed purely on the `DERIVED_DATA_PATH` cache-env name inside the
ambient `claude_cli` cache-env `ContextVar`, so `dotnet`/`python`/`node`'s cache entries
(`NUGET_PACKAGES`, `UV_CACHE_DIR`, `npm_config_cache`) are untouched — none of them is ever
present under that name, so `per_slot_derived_data` is a no-op unless the leasing engine's
ambient cache env actually declares `DERIVED_DATA_PATH`.

### Follow-ups recorded, not fixed here

- The connect-repository wizard's preset-chip row (frontend, out of scope for this
  backend-only addition) will render with **nothing selected** if a config proposal ever
  recommends `ios` — the UI only lists the three chips it already knows about. This is a
  state-legibility gap, not a crash: the request model, the response payload, and the
  underlying config are all correct; only the chip's visual selection state is wrong. Visible
  only through a flow (`POST .../config-proposal` recommending `ios`) that the next manual
  onboarding step does not use, since engine registration for the `ios` stack is itself a
  separate manual step.
- `alan/web/src/api.ts`'s `ConfigProposalBasePreset` union stays a three-name
  (`"dotnet" | "python" | "node"`) type — deliberately not widened in this PR, since the
  backend accepting a fourth name is one-sided and safe (the frontend only ever sends preset
  names it already knows, and receives proposals back as untyped strings).

Preflight's `gate_warmup_tools_reachable` and `flow_graph_and_posture_valid` checks both pass
for `ios_preset()` today (proved by `list_presets()`'s existing loop over every preset in
`tests/test_engine_preflight.py`, plus an explicit ios-only reachability test). What that
does **not** prove: that `scripts/gate.sh` itself succeeds against a real iOS checkout — the
script doesn't exist yet, and preflight is static and read-only by design (see "What 'clears
preflight' means here" below). Post-registration behaviour, once the engine is actually
registered against `<owner>/<repo>` (a separate manual step, P3), is out of scope here.

## What "clears preflight" means here

The acceptance criteria for this workspace are deliberately narrow: a dotnet-preset-derived
engine "clears preflight" means exactly the two **template-controlled** static checks pass
— `gate_warmup_tools_reachable` (every gate/warmup argv is reachable from the engine's own
tool allowlist) and `flow_graph_and_posture_valid` (the inherited `transition_graph`/
`flow_policy` validate) — plus, once a real `forge`/credentials are overlaid the way a
wizard would supply them, the credential/repo-reachability checks that depend on those
(`author_token_valid`, `reviewer_distinct_from_author`, `repo_clonable`,
branch-protection compatibility). See `engine_preflight.md` for what each check actually
proves. **Preflight passing is not proof the `dotnet build`/`dotnet test`/
`dotnet format --verify-no-changes` gates actually succeed against a real .NET repo** —
`run_preflight` is static and read-only by design (see "Dry-run warmup ... explicitly OUT
of scope" in `engine_preflight.md`); whether those gates are green is a runtime concern,
deferred to the config editor and a live run, exactly as it is for every other engine.

## Read-only fetch interface

`GET /api/engine-presets` (`alan/routes/engine_presets.py`, `engine_presets_router`,
registered in `server.py` next to `engines_router`) returns
`EnginePresetListResponse{presets: [EnginePresetInfo{name, config}, ...]}` sorted by name
(`dotnet`, `ios`, `node`, `python`). `GET /api/engine-presets/{name}` returns the single
`EnginePresetInfo` or 404 (`_PRESET_NOT_FOUND_DETAIL`) for an unknown name. Neither route
requires the control token — mirrors the existing unauthenticated `GET /api/engines` /
`GET /api/teams` read surfaces; nothing here mutates state. This is the fetch interface a
future project-creation wizard is expected to consume; building that wizard UI is out of
scope for this workspace.

`GET /api/engine-presets/{name}/config?owner=&repo=` (added by the connect-repository
wizard, W2.1 — see `connect_wizard.md`) is the HTTP surface for `build_config` itself:
it returns `build_config(name, owner, repo)` verbatim as an `EngineConfig`, 404 for an
unknown preset name, 422 for a blank `owner`/`repo` query param. It requires no control
token — same rationale as the two routes above, and consuming it can never itself
register anything. It is **not** a second engine-creation orchestration path: the wizard
still calls `POST /api/engines` itself with the returned body, exactly as any other
hand-assembled create payload would. The endpoint exists only because `build_config` had
no HTTP surface before this: without it, a wizard would have to duplicate
`_credential_env_name`'s sanitization logic client-side, or risk a forge-less create body
silently 201-ing past `alan.routes.engines._require_credential_record` (which only fires
when `body.forge is not None`).

## Layering

`alan.engine_presets` sits in `[tool.importlinter]`'s layers list directly above `teams`,
alongside `engine_preflight` — it imports only `alan.engine_config`, so its exact position
between `engine_preflight` and `engine_config` is not load-bearing, only that it is listed
at all (the layers contract is `exhaustive = true`). `alan.routes.engine_presets` lives in
the `routes` layer per usual and is listed in the "Routes siblings are independent"
contract alongside `alan.routes.engines`.
