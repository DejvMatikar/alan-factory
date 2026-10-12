# Collision-check classifier

## Purpose

`alan/collision.py` provides an on-demand, model-assisted collision-risk check
that a frontend surface can call before submitting a new task. It answers the
question: "Does any live or recent work already cover this brief?"

The check is **advisory only**. It never blocks or rejects work. For valid
requests the endpoint returns HTTP 200, degrading to `green` on any internal
failure. Request-validation errors (e.g. missing `brief`) return 422.

## Entry point

```python
check_collision(
    brief: str, *,
    workspaces_root: Path | None = None,
    stream_path: Path | None = None,
    exclude_task_id: str | None = None,
    scope_engine_key: str | None = None,
) -> CollisionResult
```

- Never raises. Returns `_SAFE` (green, no match) on any internal error.
- `workspaces_root` follows the standard `WorkspacesRootDep` convention; pass
  `None` to use the default `~/.alan/workspaces` root.
- `exclude_task_id` (since 2026-07-21): the workspace id the check is being
  run *for*, so that workspace never appears as its own candidate —
  `_ws_candidates` skips it before Jaccard scoring, the same way
  `predispatch_scan.scan_predispatch`'s own `exclude_task_id` parameter skips
  the current workspace during dispatch-time scanning. The two parameters are
  separate concepts that happen to share a name: collision's is an optional
  hint carried across the `POST /api/collision-check` HTTP boundary from a
  chat surface's propose-time collision fold (build-chat/explore-chat — the
  hint is length-bounded and never
  validated against real workspaces — an unknown or bogus id just fails to
  match anything and degrades to today's behaviour); `scan_predispatch`'s is
  an in-process argument passed by the dispatch path that already holds the
  live workspace object. Do not conflate the two call sites when reading either module.
- `scope_engine_key` (since 2026-07-21, R2.3 engine scoping): the `engine_key`
  governing the workspace/brief the check is being run *for* — `None` means the
  default engine, matching a workspace created with no `engine_key`. Both the
  workspace pre-filter (`_ws_candidates`) and the PR-overlap gate below compare
  through `engine_config.canonical_scope_key`, never raw `engine_key` equality — see
  "Engine scoping" below.
- `stream_path` (since 2026-07-03, session-scoping): `None` keeps today's
  global shared `collision/stream.jsonl` file (the dispatch modal and
  workspace creation always pass `None`, unchanged and uncounted). A concrete
  `Path` scopes the Haiku call's usage capture to that file instead — see
  "Session-scoped collision usage" below. `read_collision_usage` takes the
  same optional `stream_path` keyword and reads back whichever file was
  written.

## Return type

```python
@dataclass(frozen=True)
class CollisionResult:
    state: CollisionState          # "green" | "yellow" | "red"
    sentence: str                  # human-readable prose from the Haiku model
    match: CollisionMatch | None   # highest-risk candidate, or None when green
```

`CollisionMatch` carries `label`, `kind` (`"pr"` or `"workspace"`), and
`deep_link` (PR URL or `/workspaces/{task_id}`).

## Color semantics

| State | Condition |
|---|---|
| `red` | A candidate is **live** AND the model confirmed overlap |
| `yellow` | Text-similar but not live, OR live but no overlap confirmed |
| `green` | No candidate cleared the Jaccard pre-filter, or model returned no overlapping verdicts |

The **model never picks the color**. `_classify(live, overlapping)` is a pure
deterministic function: `red` iff `live and overlapping`, `yellow` otherwise.
When multiple candidates hit, `max(..., key=_order)` takes the worst.

## Pre-filter (candidates)

### PR candidates (`_pr_candidates`)

1. `_fetch_prs()` calls `active_forge().open_prs(forge_target=DEFAULT_FORGE_TARGET,
   limit=_PR_LIST_LIMIT, timeout=_GH_TIMEOUT_S)` — the `ForgePort` `open_prs`
   operation (`gh pr list --repo {slug} --state open --json number,title,url
   --limit 100`, run under the configured transport credential, not ambient
   `gh` authentication). On any outcome other than `"ok"` with rows present, it
   logs `collision pr list unavailable outcome=... rc=... stderr=...` at
   WARNING and returns `[]` — a transport failure (a raised exception, a
   non-zero exit, or an unparseable payload) leaves an operator-visible record
   instead of failing silently.
2. Each PR title is scored against the brief via `jaccard_trigrams(0.12)` (same
   threshold as `predispatch_scan`).
3. Matching PRs get their changed-file set fetched via
   `active_forge().changed_files` (still delegating to
   `_pr_branch.fetch_diff_files` verbatim) and are capped at `_CANDIDATE_CAP = 5`.

### Workspace candidates (`_ws_candidates`)

1. `workspace.list_workspaces(root=workspaces_root)` — lists all workspace dirs.
2. Each workspace is first filtered to the caller's engine scope (see "Engine
   scoping" below) — out-of-scope workspaces never reach the Jaccard comparison.
3. Each remaining workspace's `title + description` is scored via
   `jaccard_trigrams(0.12)`.
4. Liveness: `{ACTIVE, AWAITING_INPUT, PAUSED}` → live; `PARTIAL`, `COMPLETED`,
   `FAILED`, `ARCHIVED`, `ESCALATED` → not-live (PARTIAL intentionally excluded).
5. Capped at `_CANDIDATE_CAP = 5`.

## Engine scoping (R2.3)

Once a second engine exists, `_ws_candidates` and `check_collision`'s PR-overlap
branch must both be scoped to the workspace/brief's own engine — otherwise a
sprocket-engine brief would surface an alan workspace (or vice versa) as a false
collision. Scoping compares `engine_config.canonical_scope_key(...)` on **both**
sides, never raw `engine_key` string equality — a raw `==` would pass "sprocket never
collides with alan" but silently break "two differently-represented alan workspaces
(`None` vs the explicit `DejvMatikar/alan` slug) still collide with each other." See
`docs/architecture/engine_config.md`'s "Scoping" section for the full canonicalizer
contract.

- `_ws_candidates(..., scope_engine_key=...)` skips any workspace whose
  `canonical_scope_key(ws.meta.engine_key)` differs from
  `canonical_scope_key(scope_engine_key)`, before the Jaccard filter and the
  `_CANDIDATE_CAP` count.
- `check_collision` gates the PR-overlap branch (`_pr_candidates`/`_fetch_prs`) on
  `canonical_scope_key(scope_engine_key) == canonical_scope_key(None)` — a non-default
  scope resolves `pr_cands = []` and skips `gh pr list` entirely, since `gh pr list`
  has no per-engine `cwd` yet (real per-engine forge PR listing is R2.2). This is the
  same gate `predispatch_scan.scan_predispatch` applies to its own in-flight-PR check.
- `scope_engine_key` defaults to `None` everywhere, so with only the default engine
  registered every call resolves to the same scope and candidate selection is
  byte-identical to pre-R2.3 behavior — the scoping is a no-op until a second engine
  is registered.

## Model call (`_call_model`)

When at least one candidate passes the pre-filter, a single Haiku call is made:

```python
run_agent(_SYS, prompt, model="haiku", deny_all_tools=True, stream_path=_stream_path())
```

This mirrors the `manager_summary` pattern: no `max_turns`, cheapest tier,
tools disabled. The system prompt instructs the model to respond with a JSON
object only:

```json
{
  "verdicts": [{"id": "<pr_N|ws_ID>", "overlapping": true|false, "label": "..."}],
  "sentence": "<one-sentence user-facing summary, or empty string>"
}
```

`_call_model` catches every exception and returns `{"verdicts": [], "sentence": ""}`
on failure (logged at WARNING). The fallback means a model failure results in
no hits → `_SAFE` green.

## Usage capture (since 2026-07-03)

The Haiku call is routed through `run_agent`'s streaming capture seam (see
`docs/architecture/claude_cli_streaming.md`) instead of the plain one-shot
JSON path, so its token/cost activity is persisted:

- `_stream_path()` returns `state_dir() / "collision" / "stream.jsonl"` —
  `state_dir()` is the same `ALAN_STATE_ROOT`-overridable helper
  `alan/teams/_pr_branch.py` uses for the Copilot-comment acked-IDs sidecar,
  so collision's stream file resolves through the same container-root
  override as existing sidecars and never touches the live user home
  directory when `ALAN_STATE_ROOT` is set (see `alan/teams/_pr_branch.py::state_dir`).
  This is one shared file for all collision checks (there is no per-request
  identity to key on) — `run_agent_streaming`'s append-mode sink means
  repeated checks accumulate into the same file rather than overwriting it.
- `read_collision_usage() -> RunSummary | None` reads that file back via the
  shared `alan.run_summary.derive_run_summary` reader (frozen output
  shape/aggregation — see `docs/architecture/run_summary.md`), returning the
  summed cost/token usage across every recorded collision run, or `None` when
  no runs have been recorded yet. Nothing currently calls this reader outside
  tests — folding auxiliary spend into any workspace or group total, and any
  UI surface for it, is deferred to a later slice.
- This changes *how* the Haiku call executes (one streaming subprocess
  instead of one JSON subprocess) but not *what* it returns: `_call_model`'s
  contract, `_apply_verdicts`, and the fail-closed `_SAFE` degrade path are
  unchanged. Streaming failures are caught by the same `except Exception`
  in `_call_model`.

## Session-scoped collision usage (since 2026-07-03)

`POST /api/collision-check` (`alan/server.py`) optionally carries `chat_family`
(`"build" | "explore"`) and `session_id`. `_resolve_collision_stream_path`
first calls `_chat_core.load_session(cfg, session_id, root=root)` to confirm
the session actually exists (not just that `session_id` is UUID-shaped) —
a well-formed but nonexistent id, or a malformed one, raises `ChatCoreError`
and the resolver falls back to `None` (the global path) rather than resolving
a per-session file. This prevents a request carrying an arbitrary UUID from
creating an orphan session directory: `check_collision` → `run_agent` →
`_open_stream_sink` creates parent directories on write, so resolving a path
for a session that was never actually created would otherwise leave a bare
directory (no `session.json`) under the sessions root that later session-list
sweeps would flag as "session not found".

Only once the session is confirmed to exist does the endpoint resolve a
per-session file — `_chat_core.collision_stream_path(cfg, session_id,
root=root)`, a sibling of the per-session `stream_path()` helper that writes
`collision_stream.jsonl` instead of `stream.jsonl` — and pass it as
`check_collision(..., stream_path=...)`. This file resolves through the same
`ALAN_BUILD_CHAT_SESSIONS_ROOT` / `ALAN_EXPLORE_CHAT_SESSIONS_ROOT`
container-root overrides as the rest of that chat session, so it never
touches the live user home directory under test/CI isolation.

`check_collision` `unlink(missing_ok=True)`s that file at the very top of the
try block, before the candidate pre-filter's `_SAFE` early return — a rerun
against a brief with no candidates always starts from an empty file, so it
can never report a prior run's cost. The global (`stream_path=None`) path is
never unlinked and keeps accumulating exactly as before.

`alan/routes/_chat_router.py::compute_collision_run_summary` reads the same
per-session file back via `read_collision_usage(stream_path=...)` and attaches
it to `ChatSessionResponse.collision_run_summary` (`RunSummary | None`) — a
flat sibling of the conversation's own `run_summary` field, populated
independently and never merged into it (see
`docs/architecture/run_summary.md`). Missing, empty, or unreadable usage
degrades to `None`; nothing on this path ever raises or blocks the collision
verdict or the chat session response.

When `chat_family`/`session_id` are absent (the dispatch modal, and workspace
creation), `check_collision` and `read_collision_usage` fall back to
`_stream_path()` exactly as before — the global entry point is byte-identical
and remains uncounted.

## Scope resolution for explore-chat (since 2026-07-23, bound-project repo)

`collision_check_endpoint`'s request body still carries a raw `engine_key`
field, but for `chat_family == "explore"` with a `session_id` it is no longer
trusted verbatim: `_resolve_collision_scope_engine_key` (`alan/server.py`)
loads the named explore session and resolves `scope_engine_key` from *that
session's own* `project_id` via `project.engine_key_for` instead — mirroring
what `alan.routes.explore_chat`'s `/propose` door already does for its own
`scan_predispatch`/`check_collision` closures (see
[explore_chat.md](explore_chat.md#scope-consistency-between-the-two-propose-time-checks)),
so both dispatch-readiness checks a non-alan-bound explore session runs agree
on the same scope. Any resolution failure — session not found, malformed id,
no `project_id` on the session — degrades to `None` (the alan default), the
same fail-closed posture every other branch on this endpoint already uses.
`chat_family != "explore"` (including `None`, the build door, and every
existing caller) is unaffected: `body.engine_key` is forwarded exactly as
before.

## Verdict application (`_apply_verdicts`)

- Each verdict is matched back to a candidate by its `id` prefix (`pr_N` →
  `_PrCandidate`, `ws_<task_id>` → `_WsCandidate`).
- Unrecognised IDs (model hallucination) are silently dropped.
- `_classify(live, overlapping)` maps each hit to a color.
- `max(hits, key=_order)` selects the worst hit.
- The model's `sentence` is used if non-empty; otherwise a default
  `f"Possible overlap with {match.label[:80]}."` is generated.

## HTTP endpoint

`POST /api/collision-check` (in `alan/server.py`):

```jsonc
// Request shape
{"brief": "...", "chat_family": "build" | "explore" | null, "session_id": "..." | null, "exclude_task_id": "..." | null, "engine_key": "..." | null}

// Global request (dispatch modal, workspace creation) — unchanged, uncounted
{"brief": "add auth flow"}

// Session-scoped request (chat surfaces)
{"brief": "add auth flow", "chat_family": "build", "session_id": "8f14e1f0-..."}

// Workspace-advisor dispatch modal — excludes the workspace it's running for
{"brief": "add auth flow", "exclude_task_id": "ws-abc123"}

// Response (200 for valid requests; 422 for request-validation errors)
{"state": "green|yellow|red", "sentence": "...", "match": null | {"label": "...", "kind": "pr|workspace", "deep_link": "..."}}
```

`chat_family`, `session_id`, `exclude_task_id`, and `engine_key` are optional and
independent of the response shape above — `chat_family`/`session_id` only
affect *where* usage is captured (see "Session-scoped collision usage"),
never the verdict contract; `exclude_task_id` is a candidate-exclusion hint
only (see above) and never errors the check even when it names an unknown or
malformed id; `engine_key` is forwarded verbatim to `check_collision`'s
`scope_engine_key` (see "Engine scoping" above) and, like `exclude_task_id`,
never errors on an unknown slug — an unregistered engine just canonicalizes to
the default scope. Omitting all four (the build door's dispatch modal, which
has no workspace yet, and today's workspace creation) is unchanged from
before these fields existed — the endpoint has carried no UI wiring for
`engine_key` since engine selection itself is R3.x, out of scope here.

`check_collision` is called via `run_in_threadpool` (blocking I/O in `gh` +
file system). The endpoint wraps in its own `except Exception` as belt-and-suspenders: if `check_collision` somehow escapes its own catch, the endpoint
still returns 200 green.

## Import-linter layer

`collision` sits between `predispatch_scan` and `loop_guard` in the layered
architecture contract in `pyproject.toml`. It may import from `loop_guard`
(uses `jaccard_trigrams`), from `alan.forge` (`active_forge().open_prs` and
`.changed_files` — the latter still delegates to `_pr_branch.fetch_diff_files`
verbatim, one layer below `forge`; collision no longer imports
`teams._pr_branch` directly), and from `engine_config` (uses
`canonical_scope_key` — see "Engine scoping" above), but nothing above it in
the layer stack may import it except `server` and `routes`.

## Relationship to `predispatch_scan`

Both modules use `jaccard_trigrams(0.12)` and `list_workspaces`. They differ in:

| | `predispatch_scan` | `collision` |
|---|---|---|
| Model call | None — fully deterministic | Haiku (overlap verdict + sentence) |
| Trigger | Automatic at workspace creation | On-demand via `POST /api/collision-check` |
| Output | `list[ScopeGapFinding]` stored in `Meta.scope_audit` | `CollisionResult` returned to caller |
| Color | N/A | Deterministic from `(live, overlapping)` |
| Blocking | Never | Never |
