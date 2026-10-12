from __future__ import annotations

import enum
import re
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictInt


class Posture(enum.StrEnum):
    observe = "observe"
    propose = "propose"
    self_merge = "self_merge"
    autonomous = "autonomous"


_POSTURE_RANK: dict[Posture, int] = {
    Posture.observe: 0,
    Posture.propose: 1,
    Posture.self_merge: 2,
    Posture.autonomous: 3,
}


class PostureOrthogonalityError(ValueError):
    pass


def _check_merge_verifier_pair(
    team_min_posture: dict[str, Posture],
    merge_team: str,
    verifier_team: str,
) -> None:
    if merge_team not in team_min_posture:
        return
    if verifier_team not in team_min_posture:
        return
    merge_rank = _POSTURE_RANK[team_min_posture[merge_team]]
    verifier_rank = _POSTURE_RANK[team_min_posture[verifier_team]]
    if merge_rank < verifier_rank:
        raise PostureOrthogonalityError(
            f"merge team {merge_team!r} admitted at posture "
            f"{team_min_posture[merge_team]!r} but verifier "
            f"{verifier_team!r} requires {team_min_posture[verifier_team]!r}"
        )


def check_posture_orthogonality(
    team_min_posture: dict[str, Posture],
    policy: FlowGraphPolicy,
) -> None:
    for merge_team in policy.merge_teams:
        for verifier_team in policy.verifier_teams:
            _check_merge_verifier_pair(team_min_posture, merge_team, verifier_team)


class FlowGraphPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    verifier_teams: frozenset[str]
    merge_teams: frozenset[str]
    terminal_teams: frozenset[str]
    internal_only_teams: frozenset[str]
    restricted_predecessors: dict[str, frozenset[str]]
    revisit_cap: int


class FlowGraphValidationError(ValueError):
    pass


def validate_flow_graph(
    graph: dict[str, list[str]], policy: FlowGraphPolicy
) -> None:
    _check_verification_precedes_merge(graph, policy)
    _check_terminal_reachability(graph, policy)
    _check_bounded_cycles(graph, policy)
    _check_internal_only_constraints(graph, policy)


def _build_predecessors(graph: dict[str, list[str]]) -> dict[str, frozenset[str]]:
    acc: dict[str, list[str]] = {}
    for src, dsts in graph.items():
        for dst in dsts:
            acc.setdefault(dst, []).append(src)
    return {k: frozenset(v) for k, v in acc.items()}


def _reachable(graph: dict[str, list[str]], start: str) -> frozenset[str]:
    visited: set[str] = set()
    queue = [start]
    while queue:
        node = queue.pop()
        if node in visited:
            continue
        visited.add(node)
        for neighbor in graph.get(node, []):
            if neighbor not in visited:
                queue.append(neighbor)
    return frozenset(visited)


def _check_verification_precedes_merge(
    graph: dict[str, list[str]], policy: FlowGraphPolicy
) -> None:
    preds = _build_predecessors(graph)
    for merge_team in policy.merge_teams:
        incoming = preds.get(merge_team, frozenset())
        if merge_team in graph and not incoming:
            raise FlowGraphValidationError(
                f"merge team {merge_team!r} has no predecessors in the graph"
            )
        bad = incoming - policy.verifier_teams
        if bad:
            raise FlowGraphValidationError(
                f"merge team {merge_team!r} reachable from non-verifier teams: {bad!r}"
            )


def _check_terminal_reachability(
    graph: dict[str, list[str]], policy: FlowGraphPolicy
) -> None:
    for team in graph:
        if not _reachable(graph, team) & policy.terminal_teams:
            raise FlowGraphValidationError(
                f"team {team!r} cannot reach any terminal state"
            )


def _check_internal_only_constraints(
    graph: dict[str, list[str]], policy: FlowGraphPolicy
) -> None:
    preds = _build_predecessors(graph)
    for team in policy.internal_only_teams:
        allowed = policy.restricted_predecessors.get(team, frozenset())
        bad = preds.get(team, frozenset()) - allowed
        if bad:
            raise FlowGraphValidationError(
                f"internal-only team {team!r} has unauthorized predecessors: {bad!r}"
            )


class _TarjanState:
    def __init__(self, graph: dict[str, list[str]]) -> None:
        self._graph = graph
        self._index: dict[str, int] = {}
        self._lowlink: dict[str, int] = {}
        self._on_stack: dict[str, bool] = {}
        self._stack: list[str] = []
        self._counter: int = 0
        self._sccs: list[frozenset[str]] = []

    def _process_neighbors(self, v: str) -> None:
        for w in self._graph.get(v, []):
            if w not in self._index:
                self._visit(w)
                self._lowlink[v] = min(self._lowlink[v], self._lowlink[w])
            elif self._on_stack[w]:
                self._lowlink[v] = min(self._lowlink[v], self._index[w])

    def _maybe_emit_scc(self, v: str) -> None:
        if self._lowlink[v] != self._index[v]:
            return
        scc: list[str] = []
        while True:
            w = self._stack.pop()
            self._on_stack[w] = False
            scc.append(w)
            if w == v:
                break
        self._sccs.append(frozenset(scc))

    def _visit(self, v: str) -> None:
        self._index[v] = self._lowlink[v] = self._counter
        self._counter += 1
        self._stack.append(v)
        self._on_stack[v] = True
        self._process_neighbors(v)
        self._maybe_emit_scc(v)

    def compute(self) -> list[frozenset[str]]:
        for v in self._graph:
            if v not in self._index:
                self._visit(v)
        return self._sccs


def _tarjan_sccs(graph: dict[str, list[str]]) -> list[frozenset[str]]:
    return _TarjanState(graph).compute()


def _is_nontrivial_scc(scc: frozenset[str], graph: dict[str, list[str]]) -> bool:
    if len(scc) > 1:
        return True
    (node,) = scc
    return node in graph.get(node, [])


def _scc_has_exit(
    scc: frozenset[str],
    terminal_teams: frozenset[str],
    graph: dict[str, list[str]],
) -> bool:
    if scc & terminal_teams:
        return True
    for node in scc:
        for neighbor in graph.get(node, []):
            if neighbor not in scc:
                return True
    return False


def _check_bounded_cycles(
    graph: dict[str, list[str]], policy: FlowGraphPolicy
) -> None:
    for scc in _tarjan_sccs(graph):
        if _is_nontrivial_scc(scc, graph) and not _scc_has_exit(
            scc, policy.terminal_teams, graph
        ):
            raise FlowGraphValidationError(
                f"cycle with no terminal or exit edge: {scc!r}"
            )


class ForgeConfigValidationError(ValueError):
    pass


_ENV_VAR_NAME_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*$")


def validate_forge_credential_env_name(name: str) -> None:
    if not _ENV_VAR_NAME_PATTERN.fullmatch(name):
        raise ForgeConfigValidationError(
            f"credential_env_name must look like an environment variable name, got {name!r}"
        )


_CACHE_ENV_KEY_PATTERN = re.compile(r"^[A-Za-z_]\w*$", re.ASCII)


class CacheEnvValidationError(ValueError):
    pass


def _is_safe_relative_subdir(value: str) -> bool:
    if not value or value.startswith("/") or value.startswith("~"):
        return False
    return ".." not in value.split("/")


def validate_cache_env(cache_env: dict[str, str], reserved_keys: frozenset[str]) -> None:
    for name, subdir in cache_env.items():
        if not _CACHE_ENV_KEY_PATTERN.fullmatch(name):
            raise CacheEnvValidationError(
                f"cache_env key must look like an environment variable name, got {name!r}"
            )
        if name in reserved_keys:
            raise CacheEnvValidationError(f"cache_env key {name!r} is reserved")
        if not _is_safe_relative_subdir(subdir):
            raise CacheEnvValidationError(
                f"cache_env value for {name!r} must be a relative subdirectory with no "
                f"leading '/' and no '..' segment, got {subdir!r}"
            )


CheckAppId = Annotated[StrictInt, Field(gt=0)]
