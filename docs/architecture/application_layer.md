# Application layer: thin routes and an HTTP-free core

Roadmap item X2.14. HTTP concerns (status codes, response bodies, the control-token header) live in `alan/routes/` and at the app edge; everything that decides *whether* a request is valid raises a plain application error instead of an HTTP one. This was a relocation, not a behaviour change: the published OpenAPI schema (`alan/web/src/api/schema.ts`) is byte-identical to its merge-base version and `npm run check:api-types` passes from the working tree.

## The rule

- Only `alan/routes/` raises `HTTPException`. Every other module raises `alan.errors.AppError`.
- `AppError(status_code, detail)` is one class with an explicit status code. There is no error-type-to-status-code table to keep in sync: the raiser names the status, exactly as it did with `HTTPException`. `detail` is whatever the raiser passed (a `str`, or a `dict` for the structured 400 of `reject_uncovered_clauses`).
- One handler, `alan.routes._deps.app_error_handler`, registered on the app in `alan/server.py` (`app.add_exception_handler(AppError, app_error_handler)`), renders `JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})` — the same bytes and content type FastAPI's own `HTTPException` handler produces. `tests/test_app_errors.py` pins that equality per status and for structured details.
- A caller that used to `except HTTPException` around dispatch logic now catches `AppError` (`alan/unblock_sweep.py` ×2, `alan/unblock_recipes.py` ×1). Converting a raiser without its catchers would let the exception escape into a live sweep while every acceptance test stayed green, so raiser and catchers change in the same commit.
- `alan/routes/_chat_router.py`'s `load_or_404` and the route-layer helpers (`read_workspace_or_404`, `create_within_lane_limit`, …) keep raising `HTTPException`; they are route code.
- `tests/test_app_layer_structure.py` enforces the rule: no `raise HTTPException` outside `alan/routes/`, no `HTTPException` name anywhere under `alan/app/`, no fastapi import in `alan/control_plane.py` or `alan/errors.py`.

## Layer placement (two entries, deliberately)

`[tool.importlinter]` in `pyproject.toml` carries two new entries in the exhaustive `Layered architecture` list:

- `app` sits directly beneath `routes` (above `repo_quiesce`). `alan/app/workspace_creation.py` depends on dispatch, the queue, the team registry and the reconciler, so it must be above all of them and below only the route layer that calls it.
- `errors` sits immediately above `engine_config_validation` — near the bottom, below `dispatch` (which raises for ownership, posture, roster and fix-cycle ceilings) and `project_amendment` (fingerprint validation), and below `unblock_sweep`/`unblock_recipes`, `server`, `routes` and `app`.

The original brief proposed a single `alan.app` package holding both modules and resolving the contradiction by moving the control-token raising into the route layer so the lowest-level module stopped raising. The control-token move was made (below), but `dispatch` and `project_amendment` still raise after it, and they sit far below where `alan.app` must live. One package cannot occupy both positions, so the error type is `alan/errors.py`, a top-level module with its own slot. This is a deliberate deviation from that resolution, not an oversight.

`errors` is not the very last entry because `tests/test_base_ref.py` and `tests/test_engine_config.py` pin the tail of the list (`layers[-14:]`, `layers[-1] == "block_correction"`). Any module that ends up *below* `errors` cannot import it; the thirteen entries beneath it (`engine_config_validation` … `block_correction`) are pure leaf modules that raise plain exceptions.

`alan.app` may not name `HTTPException`, but it may use `fastapi.concurrency.run_in_threadpool`; "HTTP-free" means no HTTP status/response types, not no starlette import.

## What moved where

- **Control-token dependency.** `require_control_token`, its four detail constants and `_is_expired` moved from `alan/control_plane.py` to `alan/routes/_deps.py`, body verbatim. `alan/control_plane.py` is now HTTP-free: it keeps `CONTROL_TOKEN_ENV`, `CONTROL_TOKEN_HEADER`, `bootstrap_control_token`, `clear_control_token` and gains `expected_control_token()`. The env read stays in `control_plane` because `tests/test_no_raw_root_resolution.py` resolves an `os.environ` key only when it is a literal in the same module, and `DYNAMIC_ENV_READS` is capped at six.
- **Workspace creation.** `alan/routes/workspace_create_models.py` moved to `alan/app/workspace_create_models.py` (its four raises are `AppError`) because the extracted pipeline takes `CreateWorkspaceBody` and `alan.app` may not import from `alan.routes`. `alan/app/workspace_creation.py` holds the business half of `POST /api/workspaces`: team/engine/instruction resolution, the pre-dispatch scope scan (`scan_scope_findings`) and audit rows, `build_create_fn`, and the dispatch-or-rollback pipeline (`dispatch_root_task_or_rollback`, `refine_and_dispatch_root_task`). The route handlers stay in `alan/routes/workspaces.py` with unchanged names and signatures.
- **Two route-layer seams cross the boundary as arguments.** `refine_and_dispatch_root_task` takes `write_refine_source` (a callback the handler builds from `write_brief_refine_source` and the chat roots) and `merge_gate` (the handler passes `create_dispatch_skip_decision()`), because `_brief_refine_source` and `_workspaces_merge_gate` are route modules the app layer may not import. `create_within_lane_limit` stays in the handler for the same reason.
- **`server.py` re-exports.** The fifteen-odd aliased `from alan.routes._deps import x as _x  # noqa: F401` imports are now one `from alan.routes import _deps` (and `_infra`) plus plain assignments (`_queue_dep = _deps.queue_dep`, …). Attribute identity is unchanged, so no test that reads `server._queue_dep` and friends moved, and `alan/server.py` carries no unused-import suppression (its one unrelated `BLE001` stays).
- **Engine lookup-then-404.** `alan/routes/engines.py` has `_require_engine(identity)`, an awaited module-level helper collapsing the fourteen `lookup_by_repo` → `if cfg is None: raise 404` triples. It is deliberately *not* a FastAPI dependency: a dependency would move `owner`/`repo` out of the handler signature and is the one shape that can shift the generated schema. The seven other 404 sites (repo-status `None`, the `EngineNotFoundError` in the update path whose body is `str(exc)`, the posture change, the rollback missing-version, the delete race, and the two pool cases) keep their status codes and bodies; `tests/test_engines_other_404_bodies.py` pins one function per site.

## Shared response descriptions

`alan/routes/_deps.py` owns the documented control-token responses so no route writes a `401` entry inline (`tests/test_app_layer_structure.py` walks every route module's dict literals):

- `GUARDED` — 401 `control token missing`, 403 `control token mismatch`, 500 `control token not configured`.
- `GUARDED_EXPIRABLE` — the same, with 401 `control token missing or expired`; used only by the three `/api/control-token/{extend,rotate,revoke}` endpoints, which can be refused for an expired token. Two named 401 wordings exist because they are both correct; do not "fix" the variant. If that ever changes it is a separate change with its own schema-diff review.
Use them as `responses={**GUARDED, 404: …}`, writing every non-401 status key literally in the decorator: SonarCloud's undocumented-`HTTPException` rule reads only literal keys, so a further spread (a `GUARDED` plus the engine 404 constant) hides the 404 and 500 that `_require_engine` and `_dispose_pool_or_error` raise on behalf of the route. The dumped schema is `sort_keys`-stable, so dict ordering does not matter.

## The schema constraint

The generated schema keys each operation on FastAPI's operation id, derived from the handler function name plus path and method. **No route handler may be renamed, and no handler signature (parameter names, order, response model) may change, by a relocation like this one** — a pure rename moves `schema.ts` with zero behaviour change. Moving a handler between modules is safe (the id does not include the module). After any edit under `alan/routes/`, run `npm --prefix alan/web run check:api-types` (see `api_types.md`).

## Size and line accounting

Measured against the merge base: `alan/routes/workspaces.py` 1266 → 1044 physical lines (936 non-blank; the `PINNED_SOURCE_MODULES` pin dropped from 1186 to 936), `alan/routes/engines.py` 910 → 856, `alan/server.py` 504 → 467. No route module other than `_deps.py` (258 → 309) grew. The backend non-blank total changed by −56 lines (net physical −47): the new modules and `_deps.py` additions are paid for by the deleted route, `control_plane` and `server.py` lines. `tests/test_routes_workspaces.py` and `tests/test_routes_engines.py` are pinned shrink-only; new tests live in sibling files (`tests/test_app_*.py`, `tests/test_routes_deps_control_token.py`, `tests/test_engines_*.py`, `tests/test_unblock_catches_app_error.py`) and monkeypatch targets that moved were repointed line-for-line.

The roadmap's original `engines.py` ≤ 700 target is amended to ≤ 860 physical lines: the relocation described here is worth roughly fifty lines against the two hundred and ten that target needs. Closing the rest is a module split into a new route sibling, tracked as a follow-up issue rather than done here.
