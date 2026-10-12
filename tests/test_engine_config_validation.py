from __future__ import annotations

import pytest

from alan.engine_config_validation import (
    CacheEnvValidationError,
    FlowGraphPolicy,
    FlowGraphValidationError,
    ForgeConfigValidationError,
    _build_predecessors,
    _check_bounded_cycles,
    _check_internal_only_constraints,
    _check_terminal_reachability,
    _check_verification_precedes_merge,
    _is_nontrivial_scc,
    _is_safe_relative_subdir,
    _reachable,
    _scc_has_exit,
    _tarjan_sccs,
    _TarjanState,
    validate_cache_env,
    validate_flow_graph,
    validate_forge_credential_env_name,
)


def _policy(
    *,
    verifier_teams: frozenset[str] = frozenset({"verifier"}),
    merge_teams: frozenset[str] = frozenset({"merger"}),
    terminal_teams: frozenset[str] = frozenset({"done"}),
    internal_only_teams: frozenset[str] = frozenset(),
    restricted_predecessors: dict[str, frozenset[str]] | None = None,
    revisit_cap: int = 3,
) -> FlowGraphPolicy:
    return FlowGraphPolicy(
        verifier_teams=verifier_teams,
        merge_teams=merge_teams,
        terminal_teams=terminal_teams,
        internal_only_teams=internal_only_teams,
        restricted_predecessors=restricted_predecessors or {},
        revisit_cap=revisit_cap,
    )


def test_flow_graph_policy_fields() -> None:
    p = _policy()
    assert p.verifier_teams == frozenset({"verifier"})
    assert p.merge_teams == frozenset({"merger"})
    assert p.terminal_teams == frozenset({"done"})
    assert p.internal_only_teams == frozenset()
    assert p.restricted_predecessors == {}
    assert p.revisit_cap == 3


def test_flow_graph_policy_coerces_lists() -> None:
    from pydantic import TypeAdapter

    raw = {
        "verifier_teams": ["a", "b"],
        "merge_teams": ["m"],
        "terminal_teams": ["t"],
        "internal_only_teams": [],
        "restricted_predecessors": {"x": ["p"]},
        "revisit_cap": 5,
    }
    p = TypeAdapter(FlowGraphPolicy).validate_python(raw)
    assert p.verifier_teams == frozenset({"a", "b"})
    assert p.restricted_predecessors == {"x": frozenset({"p"})}
    assert p.revisit_cap == 5


def test_flow_graph_policy_rejects_extra_field() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        FlowGraphPolicy(
            verifier_teams=frozenset(),
            merge_teams=frozenset(),
            terminal_teams=frozenset(),
            internal_only_teams=frozenset(),
            restricted_predecessors={},
            revisit_cap=3,
            unknown="x",  # type: ignore[call-arg]
        )


def test_build_predecessors_basic() -> None:
    g = {"a": ["b", "c"], "b": ["c"]}
    preds = _build_predecessors(g)
    assert preds["b"] == frozenset({"a"})
    assert preds["c"] == frozenset({"a", "b"})
    assert "a" not in preds


def test_build_predecessors_empty() -> None:
    assert _build_predecessors({}) == {}


def test_reachable_includes_start() -> None:
    g: dict[str, list[str]] = {"a": []}
    assert "a" in _reachable(g, "a")


def test_reachable_transitive() -> None:
    g = {"a": ["b"], "b": ["c"], "c": []}
    assert _reachable(g, "a") == frozenset({"a", "b", "c"})


def test_reachable_handles_cycle() -> None:
    g = {"a": ["b"], "b": ["a"]}
    assert _reachable(g, "a") == frozenset({"a", "b"})


def test_verif_before_merge_accepts_direct_verifier_predecessor() -> None:
    g = {"v": ["m"], "m": ["done"], "done": []}
    _check_verification_precedes_merge(
        g, _policy(verifier_teams=frozenset({"v"}), merge_teams=frozenset({"m"}))
    )


def test_verif_before_merge_accepts_no_merge_team() -> None:
    g = {"a": ["b"], "b": []}
    _check_verification_precedes_merge(g, _policy(merge_teams=frozenset()))


def test_verif_before_merge_rejects_non_verifier_predecessor() -> None:
    g = {"impl": ["merger"], "merger": ["done"], "done": []}
    p = _policy(verifier_teams=frozenset({"verifier"}), merge_teams=frozenset({"merger"}))
    with pytest.raises(FlowGraphValidationError, match="non-verifier"):
        _check_verification_precedes_merge(g, p)


def test_verif_before_merge_rejects_when_merge_team_has_no_verifier_in_path() -> None:
    g = {"impl": ["merger"], "verifier": ["done"], "merger": [], "done": []}
    p = _policy(verifier_teams=frozenset({"verifier"}), merge_teams=frozenset({"merger"}))
    with pytest.raises(FlowGraphValidationError, match="non-verifier"):
        _check_verification_precedes_merge(g, p)


def test_verif_before_merge_rejects_isolated_merge_node() -> None:
    g = {"a": ["done"], "merger": [], "done": []}
    p = _policy(merge_teams=frozenset({"merger"}))
    with pytest.raises(FlowGraphValidationError, match="no predecessors"):
        _check_verification_precedes_merge(g, p)


def test_terminal_reachability_accepts_direct_terminal() -> None:
    g = {"a": ["done"], "done": []}
    _check_terminal_reachability(g, _policy(terminal_teams=frozenset({"done"})))


def test_terminal_reachability_accepts_empty_graph() -> None:
    _check_terminal_reachability({}, _policy())


def test_terminal_reachability_team_is_itself_terminal() -> None:
    g: dict[str, list[str]] = {"done": []}
    _check_terminal_reachability(g, _policy(terminal_teams=frozenset({"done"})))


def test_terminal_reachability_rejects_closed_cycle() -> None:
    g = {"a": ["b"], "b": ["a"]}
    p = _policy(terminal_teams=frozenset({"done"}))
    with pytest.raises(FlowGraphValidationError, match="cannot reach any terminal"):
        _check_terminal_reachability(g, p)


def test_terminal_reachability_rejects_missing_exit() -> None:
    g = {"a": ["b"], "b": ["a"], "done": []}
    p = _policy(terminal_teams=frozenset({"done"}))
    with pytest.raises(FlowGraphValidationError, match="cannot reach any terminal"):
        _check_terminal_reachability(g, p)


def test_bounded_cycles_accepts_acyclic_graph() -> None:
    g = {"a": ["b"], "b": ["c"], "c": []}
    _check_bounded_cycles(g, _policy(terminal_teams=frozenset({"c"})))


def test_bounded_cycles_accepts_cycle_with_exit() -> None:
    g = {"a": ["b"], "b": ["a", "done"], "done": []}
    _check_bounded_cycles(g, _policy(terminal_teams=frozenset({"done"})))


def test_bounded_cycles_accepts_cycle_where_exit_reaches_terminal() -> None:
    g = {"v": ["fix", "merge"], "fix": ["v"], "merge": ["done"], "done": []}
    _check_bounded_cycles(g, _policy(terminal_teams=frozenset({"done"})))


def test_bounded_cycles_rejects_closed_two_node_cycle() -> None:
    g = {"a": ["b"], "b": ["a"]}
    with pytest.raises(FlowGraphValidationError, match="cycle with no terminal or exit edge"):
        _check_bounded_cycles(g, _policy(terminal_teams=frozenset()))


def test_bounded_cycles_rejects_self_loop() -> None:
    g = {"a": ["a"]}
    with pytest.raises(FlowGraphValidationError, match="cycle with no terminal or exit edge"):
        _check_bounded_cycles(g, _policy(terminal_teams=frozenset()))


def test_bounded_cycles_rejects_three_node_closed_cycle() -> None:
    g = {"a": ["b"], "b": ["c"], "c": ["a"]}
    with pytest.raises(FlowGraphValidationError, match="cycle with no terminal or exit edge"):
        _check_bounded_cycles(g, _policy(terminal_teams=frozenset()))


def test_internal_only_accepts_authorized_predecessor() -> None:
    g = {"plan": ["internal"], "internal": ["done"], "done": []}
    p = _policy(
        internal_only_teams=frozenset({"internal"}),
        restricted_predecessors={"internal": frozenset({"plan"})},
    )
    _check_internal_only_constraints(g, p)


def test_internal_only_accepts_team_not_in_graph() -> None:
    g = {"a": ["b"], "b": []}
    p = _policy(
        internal_only_teams=frozenset({"secret"}),
        restricted_predecessors={"secret": frozenset()},
    )
    _check_internal_only_constraints(g, p)


def test_internal_only_rejects_unauthorized_predecessor() -> None:
    g = {"bad": ["internal"], "internal": ["done"], "done": []}
    p = _policy(
        internal_only_teams=frozenset({"internal"}),
        restricted_predecessors={"internal": frozenset({"plan"})},
    )
    with pytest.raises(FlowGraphValidationError, match="unauthorized predecessors"):
        _check_internal_only_constraints(g, p)


def test_internal_only_rejects_any_predecessor_when_none_allowed() -> None:
    g = {"anyone": ["secret"], "secret": []}
    p = _policy(
        internal_only_teams=frozenset({"secret"}),
        restricted_predecessors={"secret": frozenset()},
    )
    with pytest.raises(FlowGraphValidationError, match="unauthorized predecessors"):
        _check_internal_only_constraints(g, p)


def test_validate_accepts_minimal_valid_graph() -> None:
    g = {
        "impl": ["verifier"],
        "impl_fix": ["verifier"],
        "verifier": ["impl_fix", "merger"],
        "merger": ["done"],
        "done": [],
    }
    p = _policy(
        verifier_teams=frozenset({"verifier"}),
        merge_teams=frozenset({"merger"}),
        terminal_teams=frozenset({"done"}),
    )
    validate_flow_graph(g, p)


def test_default_graph_passes_validation() -> None:
    from alan.engine_config import default_engine_config

    cfg = default_engine_config()
    assert cfg.flow_policy is not None
    validate_flow_graph(cfg.transition_graph, cfg.flow_policy)


def test_nonpython_fixture_graph_passes_validation() -> None:
    from pathlib import Path

    from alan.engine_config import load_yaml

    fixture = Path(__file__).parent / "fixtures" / "engine_config_nonpython.yaml"
    cfg = load_yaml(fixture)
    assert cfg.flow_policy is not None
    validate_flow_graph(cfg.transition_graph, cfg.flow_policy)


def test_direct_edge_to_merge_without_verifier_raises() -> None:
    g = {"impl": ["merger"], "merger": ["done"], "done": []}
    p = _policy(
        verifier_teams=frozenset({"verifier"}),
        merge_teams=frozenset({"merger"}),
        terminal_teams=frozenset({"done"}),
    )
    with pytest.raises(FlowGraphValidationError, match="non-verifier"):
        validate_flow_graph(g, p)


def test_dead_end_node_with_no_out_edges_and_not_terminal_raises() -> None:
    g = {"a": ["b"], "b": ["a"], "done": []}
    p = _policy(terminal_teams=frozenset({"done"}), merge_teams=frozenset())
    with pytest.raises(FlowGraphValidationError):
        validate_flow_graph(g, p)


def test_closed_scc_no_exit_no_terminal_raises() -> None:
    g = {"a": ["b"], "b": ["a"]}
    p = _policy(terminal_teams=frozenset(), merge_teams=frozenset())
    with pytest.raises(FlowGraphValidationError):
        validate_flow_graph(g, p)


def test_internal_team_reachable_from_wrong_predecessor_raises() -> None:
    g = {"bad": ["secret"], "secret": ["done"], "done": []}
    p = _policy(
        merge_teams=frozenset(),
        terminal_teams=frozenset({"done"}),
        internal_only_teams=frozenset({"secret"}),
        restricted_predecessors={"secret": frozenset({"allowed"})},
    )
    with pytest.raises(FlowGraphValidationError, match="unauthorized"):
        validate_flow_graph(g, p)


def test_tarjan_singleton_no_cycle() -> None:
    sccs = _tarjan_sccs({"a": ["b"], "b": []})
    sizes = sorted(len(s) for s in sccs)
    assert sizes == [1, 1]


def test_tarjan_two_node_cycle() -> None:
    sccs = _tarjan_sccs({"a": ["b"], "b": ["a"]})
    assert len(sccs) == 1
    assert sccs[0] == frozenset({"a", "b"})


def test_tarjan_self_loop() -> None:
    sccs = _tarjan_sccs({"a": ["a"]})
    assert len(sccs) == 1
    assert sccs[0] == frozenset({"a"})


def test_tarjan_state_compute_empty() -> None:
    assert _TarjanState({}).compute() == []


def test_is_nontrivial_scc_size_gt_1() -> None:
    assert _is_nontrivial_scc(frozenset({"a", "b"}), {}) is True


def test_is_nontrivial_scc_singleton_no_self_loop() -> None:
    assert _is_nontrivial_scc(frozenset({"a"}), {"a": ["b"]}) is False


def test_is_nontrivial_scc_singleton_with_self_loop() -> None:
    assert _is_nontrivial_scc(frozenset({"a"}), {"a": ["a"]}) is True


def test_scc_has_exit_via_out_edge() -> None:
    scc = frozenset({"a", "b"})
    g = {"a": ["b"], "b": ["c"]}
    assert _scc_has_exit(scc, frozenset(), g) is True


def test_scc_has_no_exit_closed_cycle() -> None:
    scc = frozenset({"a", "b"})
    g = {"a": ["b"], "b": ["a"]}
    assert _scc_has_exit(scc, frozenset(), g) is False


def test_scc_has_exit_via_terminal_in_scc() -> None:
    scc = frozenset({"a", "done"})
    g = {"a": ["done"], "done": []}
    assert _scc_has_exit(scc, frozenset({"done"}), g) is True


def test_nonpython_engine_has_different_teams_from_default() -> None:
    from pathlib import Path

    from alan.engine_config import default_engine_config, load_yaml

    fixture = Path(__file__).parent / "fixtures" / "engine_config_nonpython.yaml"
    nonpython = load_yaml(fixture)
    default = default_engine_config()

    assert nonpython.flow_policy is not None
    assert default.flow_policy is not None
    assert nonpython.flow_policy.verifier_teams != default.flow_policy.verifier_teams
    assert nonpython.flow_policy.merge_teams != default.flow_policy.merge_teams
    assert set(nonpython.transition_graph) != set(default.transition_graph)


def test_validate_forge_credential_env_name_accepts_valid_names() -> None:
    for name in ("ALAN_VERIFIER_GH_TOKEN", "FOO", "FOO_BAR_2", "_PRIVATE"):
        validate_forge_credential_env_name(name)


def test_validate_forge_credential_env_name_rejects_lowercase() -> None:
    with pytest.raises(ForgeConfigValidationError, match="environment variable name"):
        validate_forge_credential_env_name("alan_verifier_gh_token")


def test_validate_forge_credential_env_name_rejects_leading_digit() -> None:
    with pytest.raises(ForgeConfigValidationError):
        validate_forge_credential_env_name("1FOO")


def test_validate_forge_credential_env_name_rejects_whitespace() -> None:
    with pytest.raises(ForgeConfigValidationError):
        validate_forge_credential_env_name("FOO BAR")


def test_validate_forge_credential_env_name_rejects_inlined_secret_shape() -> None:
    with pytest.raises(ForgeConfigValidationError):
        validate_forge_credential_env_name("ghp_abc123def456")


def test_validate_forge_credential_env_name_rejects_empty() -> None:
    with pytest.raises(ForgeConfigValidationError):
        validate_forge_credential_env_name("")


def test_forge_config_validation_error_is_value_error() -> None:
    assert issubclass(ForgeConfigValidationError, ValueError)


def test_cache_env_validation_error_is_value_error() -> None:
    assert issubclass(CacheEnvValidationError, ValueError)


def test_validate_cache_env_accepts_empty_dict() -> None:
    validate_cache_env({}, frozenset())


def test_validate_cache_env_accepts_uppercase_key() -> None:
    validate_cache_env({"NUGET_PACKAGES": "nuget"}, frozenset())


def test_validate_cache_env_accepts_lowercase_npm_style_key() -> None:
    validate_cache_env({"npm_config_cache": "npm"}, frozenset())


def test_validate_cache_env_rejects_key_not_matching_env_var_shape() -> None:
    with pytest.raises(CacheEnvValidationError, match="environment variable name"):
        validate_cache_env({"1BAD": "x"}, frozenset())
    with pytest.raises(CacheEnvValidationError, match="environment variable name"):
        validate_cache_env({"BAD KEY": "x"}, frozenset())


def test_validate_cache_env_rejects_reserved_key() -> None:
    with pytest.raises(CacheEnvValidationError, match="reserved"):
        validate_cache_env({"ALAN_STATE_ROOT": "x"}, frozenset({"ALAN_STATE_ROOT"}))


def test_validate_cache_env_rejects_absolute_path_value() -> None:
    with pytest.raises(CacheEnvValidationError, match="relative"):
        validate_cache_env({"NUGET_PACKAGES": "/etc/passwd"}, frozenset())


def test_validate_cache_env_rejects_home_relative_value() -> None:
    with pytest.raises(CacheEnvValidationError, match="relative"):
        validate_cache_env({"NUGET_PACKAGES": "~/escape"}, frozenset())


def test_validate_cache_env_rejects_dotdot_segment() -> None:
    with pytest.raises(CacheEnvValidationError, match="relative"):
        validate_cache_env({"NUGET_PACKAGES": "a/../../escape"}, frozenset())


def test_validate_cache_env_rejects_empty_value() -> None:
    with pytest.raises(CacheEnvValidationError, match="relative"):
        validate_cache_env({"NUGET_PACKAGES": ""}, frozenset())


def test_is_safe_relative_subdir_accepts_plain_name() -> None:
    assert _is_safe_relative_subdir("nuget") is True


def test_is_safe_relative_subdir_accepts_nested_name() -> None:
    assert _is_safe_relative_subdir("nuget/packages") is True


def test_is_safe_relative_subdir_rejects_empty() -> None:
    assert _is_safe_relative_subdir("") is False


def test_is_safe_relative_subdir_rejects_absolute() -> None:
    assert _is_safe_relative_subdir("/etc") is False


def test_is_safe_relative_subdir_rejects_home() -> None:
    assert _is_safe_relative_subdir("~/x") is False


def test_is_safe_relative_subdir_rejects_dotdot() -> None:
    assert _is_safe_relative_subdir("../x") is False
    assert _is_safe_relative_subdir("a/..") is False


def test_register_with_flow_policy_none_skips_flow_graph_validation() -> None:
    from alan.engine_config import EngineConfig, RepoIdentity, register

    broken_graph = {"a": ["b"], "b": ["a"]}
    with pytest.raises(FlowGraphValidationError):
        validate_flow_graph(broken_graph, _policy(terminal_teams=frozenset()))

    cfg = EngineConfig(
        repo=RepoIdentity(owner="acme", repo="policyless"),
        transition_graph=broken_graph,
        flow_policy=None,
    )
    register(cfg, validate_env=False)
