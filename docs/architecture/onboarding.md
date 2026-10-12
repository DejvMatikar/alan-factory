# Atomic onboarding — `alan/onboarding.py` + `POST /DELETE /api/onboarding`

`alan/onboarding.py` is the server-side orchestrator behind the project wizard's single
"create" action: from a pasted GitHub token, a title, a chosen repo, and a stack template,
it brings a stored credential, a registered `EngineConfig`, a warm slot pool, and a
`Project` into existence **together** — or leaves no trace of the attempt at all.

> Setting up your own copy of alan? Start from `./setup.sh` — see [setup.md](setup.md).

## `create_attempt` — composition order and rollback

`create_attempt(*, dispatcher, title, owner, repo, template, token) -> Project` composes
six steps, in order, inside a single `contextlib.ExitStack`:

1. `_reserve_slug` — refuses (`OnboardingError(409, ...)`) if `owner/repo` is already a
   registered engine. Pure read; zero writes on refusal.
2. `_ensure_secret` — writes a `<owner/repo>:transport` secrets-store record from `token`,
   **unless one already exists**, in which case it is reused verbatim
   (`secret_created=False`) and the attempt never touches it again, including on rollback.
   Only a secret this attempt itself created is registered for rollback deletion.
3. `_register_engine` — builds an `EngineConfig` via `engine_presets.build_config(template,
   owner, repo)` (see below) and `engine_config.register_and_persist`. A name not in
   `engine_presets.PRESETS` is `OnboardingError(422, ...)`; a repo identity that raced to
   get registered between step 1 and here is `OnboardingError(409, ...)` (`DuplicateEngineError`).
4. `_gate_preflight` — runs the real `engine_preflight.run_preflight(cfg)` (unchanged,
   see `engine_preflight.md`) and refuses with `OnboardingError(422, ...)` naming every
   non-passing finding (plus its remedy when one is given) unless `overall == "pass"`.
5. `_provision_pool` — `dispatcher.register_pool(cfg)` (see `slot_pool.md` "Runtime
   lifecycle"): synchronous dict-insert, pool warms asynchronously on Dispatcher's own
   background thread. Provisioning is not yet warm at this point — see "Warming and
   activation" below.
6. `_create_and_bind_project` — `project.create(title)` then `project.update(...,
   {"engine_key": repo_slug, "status": ProjectStatus.PROVISIONING})` in one call. The
   project is visible (in `PROVISIONING` status) from this point on.

Every step but the first registers an `ExitStack.callback` the instant its side effect
lands, so a later step's `OnboardingError` (or any other exception) unwinds everything
already done, **LIFO**, before propagating: dispose the pool, delete the engine, delete
the secret **only if this attempt created it**, delete the project. On full success the
stack is disarmed via `stack.pop_all()` (discarded, never closed) — the standard
"commit" idiom for `ExitStack` — so nothing is undone.

### Concurrency guard — `_IN_FLIGHT`

Steps 2-3 (`_ensure_secret` + `_register_engine`) are a check-then-act race: two
concurrent `create_attempt` calls for a brand-new slug can both pass `_reserve_slug`
(neither has persisted an `EngineConfig` yet) and both find no existing secret via
`_ensure_secret`. Without a guard, the loser's rollback would call
`secrets_store.delete_secret` on the **shared, slug-keyed** secret the winner's engine
now depends on. `_reserve_in_flight(repo_identity)` — a module-level `set[RepoIdentity]`
guarded by `_IN_FLIGHT_LOCK` — closes that window: `create_attempt` reserves the slug
immediately after `_reserve_slug` and releases it in a `finally` spanning exactly
`_ensure_secret` + `_register_engine`, so a second concurrent attempt for the same slug
sees the reservation and raises `OnboardingError(409, ...)` **before** touching the
secrets store at all. The reservation is released as soon as `_register_engine` returns
(success or failure) — from that point the persisted `EngineConfig` registry is itself
the source of truth `_reserve_slug` checks, so holding the in-flight marker any longer
would only block a legitimate future attempt at the same slug after this one finishes.

`create_attempt` never rolls back on a *warm timeout* — only on a synchronous failure of
steps 1-6. Once the project exists in `PROVISIONING` status, warming failure is left for
the user to explicitly tear down (see "Teardown" below), not auto-rolled-back, since a
pool can legitimately still warm up seconds after the request returns.

## Warming and activation

Readiness is `run_preflight(cfg).overall == "pass"` (checked once, synchronously, during
`create_attempt`) **and** the engine's pool being warm. The second half is asynchronous:
`create_attempt` calls the module-level `_spawn_warm_task(dispatcher, project_id, slug)`
— a plain function reference, not a class, so a test can `monkeypatch.setattr` it to a
synchronous stub and never leak a real background thread — which by default spawns a
daemon thread running `_warm_and_activate`.

`_warm_and_activate` polls `dispatcher.pool_states()[slug].warm` once a second
(`_WARM_POLL_INTERVAL_S`) for up to `_WARM_TIMEOUT_S` (90s, both module constants a test
can shrink). The instant the pool reports warm, `_activate_project` flips the project
`PROVISIONING -> ACTIVE` (a no-op if the project was deleted or already moved on in the
meantime) and pops the project's `_ATTEMPTS` entry — once a project is `ACTIVE` it can
never again reach `rollback_attempt` (which refuses any non-`PROVISIONING` project), so
keeping the in-memory record around would only leak for the life of the process. If the
deadline passes first, the function simply returns — **the project is left in
`PROVISIONING`**, which is itself the durable signal that this attempt never finished;
the wizard's UI is expected to offer the teardown action (below) from that state, and the
`_ATTEMPTS` entry (or its post-restart reconstruction) is still there to serve it.

## Teardown — `DELETE /api/onboarding/{project_id}`

`rollback_attempt(dispatcher, project_id)` is the manual counterpart to the automatic
LIFO unwind above, for the case where `create_attempt` already returned (project exists)
but warming never completed:

- `_read_provisioning_project_or_error` 404s (`OnboardingError(404, ...)`) on an unknown
  project id and 409s (`OnboardingError(409, ...)`) on any project whose status is not
  `PROVISIONING` — this endpoint tears down a stuck onboarding attempt, never a live
  project.
- The in-memory `_ATTEMPTS` dict (keyed by `project_id`, holding the repo identity and
  whether this attempt created the secret) is the fast path: a process that never
  restarted since the attempt was created has this and tears down exactly the same way
  `create_attempt`'s own rollback would, including preserving a pre-existing credential.
- **After a restart, `_ATTEMPTS` is empty.** `_reconstruct_attempt` rebuilds a synthetic
  `Attempt` from the project's own `engine_key` field alone — this is what makes a
  `PROVISIONING`-status project the durable teardown signal the module docstring above
  promises. The reconstructed attempt always sets `secret_created=False`, so a
  post-restart teardown **never deletes the secret** — provenance of "did this specific
  attempt create the credential" is lost across a restart, and the safe default is to
  assume it did not and leave it alone. (A secret genuinely written by this attempt then
  survives an interrupted-then-restarted rollback; the user can delete it explicitly via
  `DELETE /api/secrets` if they want it gone.)
- Either way, `_teardown_attempt` disposes the pool (best-effort — a busy or
  incomplete-dispose pool is logged, not raised, since the project is going away
  regardless), deletes the engine, conditionally deletes the secret, then deletes the
  project (best-effort — already-gone is fine).

## Routes (`alan/routes/onboarding.py`)

`onboarding_router = APIRouter(prefix="/api/onboarding", dependencies=[Depends(require_control_token)])`
— the whole router is control-token-gated, not just individual routes (unlike
`engines_router`, which mixes gated mutation routes with ungated reads; onboarding has no
ungated route). `POST /api/onboarding` and `DELETE /api/onboarding/{project_id}` both take
a non-optional `DispatcherDep` (503 when the dispatcher is disabled — onboarding cannot
provision a pool without one) and translate `onboarding.OnboardingError` into an
`HTTPException` with the same `status_code`/`detail`.

Repo listing for the wizard's repo picker is **not** duplicated here — it reuses the
existing `POST /api/github/repos` (`alan/routes/github.py`, `github_repos.md`) unchanged.

`ProjectStatus.PROVISIONING` is a new member of the frontend's `ProjectStatus` union
(`alan/web/src/api.ts`) alongside `active`/`archived`/`completed`, since a project a
wizard just created and a project returned by `GET /api/projects` share the same shape.
`Projects.tsx`'s `STATUS_LABELS`/`STATUS_STYLES` (both `Record<ProjectStatus, string>`)
carry a `provisioning` entry so the closed set stays exhaustive and `ProjectStatusBadge`
renders it like any other status.

## `engine_presets.build_config` — overlaying a real identity on a preset skeleton

`engine_presets.build_config(name, owner, repo) -> EngineConfig | None` is what turns a
preset **skeleton** (see `engine_presets.md` — `repo=_preset/<name>`, `forge=None`) into a
config a real onboarding attempt can preflight and register: it swaps `repo` for the
caller's `RepoIdentity(owner, repo)` and overlays a `ForgeConfig` pointed at the same
identity, with a synthesized `credential_env_name` (`_credential_env_name`, sanitized to
`^[A-Z_][A-Z0-9_]*$` — required by `ForgeConfig`'s own field validator, never actually read
from `os.environ` in practice since `_ensure_secret` always writes the transport secret
into the store keyed by the repo slug, which `secrets_store.resolve_secret` checks first).
Returns `None` for a name outside `engine_presets.PRESETS`.

## Preflight gating — `fail` blocks, `unknown` warns

`_gate_preflight` only raises `OnboardingError(422, ...)` when `run_preflight(cfg).overall
== "fail"`. An `overall == "unknown"` result is logged (`logger.warning`, naming every
non-passing finding) and treated as **non-blocking** — the attempt proceeds to
`_provision_pool`. This matters because `_ensure_secret` writes only the **transport**
secret: `run_preflight`'s `reviewer_distinct_from_author` check (see `engine_preflight.md`
"Two-credential model") resolves the reviewer identity via
`secrets_store.verifier_gh_env`, which falls back to the same
`ForgeConfig.credential_env_name` `_ensure_secret` never populates in `os.environ` — so a
freshly onboarded engine has no way to resolve a reviewer login and this check reports
`unknown`, not `pass`. Blocking on `unknown` (the orchestrator's original behavior) would
have refused every real onboarding attempt, since the v1 wizard scope (title + repo +
template + one token; see the wizard's product brief) does not collect a second token. A
hard `fail` (e.g. the repo isn't clonable with the given token at all) still blocks
onboarding; only the "can't yet tell" reviewer-identity gap is downgraded to a warning.
Closing that gap — writing a `reviewer`-purpose secret for the new engine's slug via
`POST /api/secrets`, mirroring the two-credential model's existing `author`/`reviewer`
split — is a natural follow-up once the wizard UI grows a reviewer-credential step, not a
change to this orchestrator's contract.

## Layering

`alan.onboarding` sits directly below `routes` and above `reconciler`/`dispatcher` in
`[tool.importlinter]`'s layers list — it needs to import `alan.dispatcher` (`Dispatcher`,
for `register_pool`/`dispose_pool`/`pool_states`), which is otherwise a high layer only
`server`/`routes`/`reconciler` may reach. Everything else it imports (`engine_config`,
`engine_presets`, `engine_preflight`, `secrets_store`, `project`, `slot_pool`,
`branch_protection_compat`) sits far enough below that position is not load-bearing.
`alan.routes.onboarding` lives in the `routes` layer per usual and is listed in the
"Routes siblings are independent" contract alongside every other `alan.routes.*` module.
