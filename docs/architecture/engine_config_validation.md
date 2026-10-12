# Flow-graph validator

`alan/engine_config_validation.py` — load-time safety validator for the team-to-team transition graph declared in `EngineConfig.transition_graph`.

## Purpose

The flow graph is configuration-driven, so an incorrect hand-authored graph can be loaded silently. This module runs four safety invariants when a graph is loaded or registered, rejecting it loudly before any runtime routing decision is made.

## Posture and orthogonality check

`Posture` is a `StrEnum` (four values: `observe`, `propose`, `self_merge`, `autonomous`) defined in this module so it is available to the orthogonality validator without any upward import.

`_POSTURE_RANK: dict[Posture, int]` maps each level to its integer rank (0–3). `check_posture_orthogonality(team_min_posture, policy)` uses this dict to enforce that for every merge team M and every verifier team V that both appear in `team_min_posture`: `rank(M) >= rank(V)`. A violation raises `PostureOrthogonalityError`. Teams absent from the table are skipped (they are withheld at all postures). The check is called from `engine_config.load_yaml` and `engine_config.register` whenever `flow_policy` is present, so an inconsistent table is rejected at load time, not at runtime. It is also called directly in tests to verify the error path with crafted tables.

The invariant this enforces: no posture exists at which a merge team is admitted while a verifier team that co-guards it is not. This means the dial can only *remove* capabilities relative to fully-autonomous — it cannot add an edge or relax verification-before-merge at any posture.

## FlowGraphPolicy

`FlowGraphPolicy` is a frozen Pydantic v2 model that carries the safety classifications for a specific engine's graph:

| Field | Purpose |
|---|---|
| `verifier_teams` | Teams that count as a verification step |
| `merge_teams` | Teams that perform a merge; must be preceded by a verifier |
| `terminal_teams` | Teams that are valid end states; every team must be able to reach at least one |
| `internal_only_teams` | Teams with restricted predecessors |
| `restricted_predecessors` | Maps each restricted team to the set of teams allowed to have an edge to it |
| `revisit_cap` | Loop-guard revisit ceiling; stored in the policy for runtime use but not read by the static validator — the bounded-cycle invariant uses structural SCC analysis only |

`FlowGraphPolicy` is optional on `EngineConfig` (`flow_policy: FlowGraphPolicy | None = None`). Validation runs only when it is present; configs without a policy are accepted unchanged.

## Invariants

All four are checked by `validate_flow_graph(graph, policy)` and raise `FlowGraphValidationError` (a `ValueError` subclass) on violation:

1. **Verification precedes merge** — for every merge team M, every direct predecessor of M in the graph must belong to `verifier_teams`. This is an O(E) local in-edge check equivalent to "all paths to M must pass through a verifier."

2. **Terminal reachability** — from every team that appears as a key in `transition_graph`, at least one terminal team must be reachable via BFS. Teams that are only destinations (not keys) are excluded from this check; they are reachable from some key and represent natural sinks.

3. **Bounded cycles** — Tarjan's SCC algorithm identifies strongly-connected components. Any non-trivial SCC (size > 1, or a singleton with a self-loop) must either contain a terminal team or have at least one structural out-edge leaving the SCC. A closed SCC with no exit is an unbounded loop that the loop-guard's revisit cap cannot rescue: the cap triggers escalation, which the validator treats as not a structural exit.

4. **Internal-only constraints** — for each team in `internal_only_teams`, every predecessor in the graph must belong to `restricted_predecessors[team]`. This prevents routing to internal-only teams via unauthorized paths.

## Wiring

`validate_flow_graph` is called from two places in `engine_config.py`:

- `load_yaml(path)` — validates immediately after Pydantic model construction, before the config is returned.
- `register(config)` — validates after the env-var completeness check, before storing the config.

The default config (`default_engine_config()`) carries `_DEFAULT_FLOW_POLICY` and passes all four invariants. The singleton `_DEFAULT` is not validated at import time (to keep import side-effect-free); a pinned test asserts it passes.

## Import-linter position

`engine_config_validation` is the **bottom layer** in the import-linter contract in `pyproject.toml`. It imports only from the standard library and Pydantic — never from any `alan.*` module. `engine_config` (the layer above it) imports `FlowGraphPolicy`, `FlowGraphValidationError`, and `validate_flow_graph` from it.

## Tarjan SCC implementation

The SCC computation is factored into `_TarjanState` (a class with `_visit`, `_process_neighbors`, `_maybe_emit_scc`, and `compute` methods) to keep each method under the CCN 7 gate enforced by `lizard`. The implementation is recursive and adequate for flow graphs (≪ 1000 nodes).
