from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from alan.engine_config import (
    _DEFAULT,
    CiConfig,
    DependencySweepConfig,
    DuplicateEngineError,
    EngineConfig,
    EngineNotFoundError,
    MissingEnvVarsError,
    Posture,
    PromptOverride,
    RepoIdentity,
    RoleConfig,
    _secret_env_vars,
    admitted_teams,
    apply_prompt_override_and_persist,
    canonical_scope_key,
    default_engine_config,
    delete_engine,
    display_scope_slug,
    get_registered,
    load_directory,
    load_persisted_engines,
    load_yaml,
    lookup_by_repo,
    lookup_by_slug,
    register,
    register_and_persist,
    registered_engines,
    same_repo_scope,
    set_posture_and_persist,
    unregister_and_persist,
    update_and_persist,
)
from alan.engine_registry_store import list_engines as _persisted_engines
from alan.repo_accent import AccentChoice

_FIXTURES = Path(__file__).parent / "fixtures"

_SOURCE_FETCH_TOOL = "Bash(python3 -m alan.source_fetch:*)"
_WEB_RESEARCH_TOOLS = ["WebSearch", "WebFetch", _SOURCE_FETCH_TOOL]


def test_repo_identity_requires_nonempty_fields() -> None:
    with pytest.raises(ValidationError):
        RepoIdentity(owner="", repo="alan")
    with pytest.raises(ValidationError):
        RepoIdentity(owner="org", repo="")


def test_role_config_effort_optional() -> None:
    r = RoleConfig(model="sonnet")
    assert r.effort is None


def test_role_config_with_effort() -> None:
    r = RoleConfig(model="sonnet", effort="xhigh")
    assert r.effort == "xhigh"


def test_ci_config_deploy_workflow_optional() -> None:
    ci = CiConfig(main_ci_workflow="tests.yml")
    assert ci.deploy_workflow is None


def test_ci_config_with_deploy_workflow() -> None:
    ci = CiConfig(main_ci_workflow="tests.yml", deploy_workflow="ci-deploy.yml")
    assert ci.deploy_workflow == "ci-deploy.yml"


def test_ci_config_requires_nonempty_main_ci_workflow() -> None:
    with pytest.raises(ValidationError):
        CiConfig(main_ci_workflow="")


def test_ci_config_rejects_empty_deploy_workflow() -> None:
    with pytest.raises(ValidationError):
        CiConfig(main_ci_workflow="tests.yml", deploy_workflow="")


def test_ci_config_is_frozen() -> None:
    ci = CiConfig(main_ci_workflow="tests.yml")
    with pytest.raises(ValidationError):
        ci.main_ci_workflow = "other.yml"  # type: ignore[misc]


def test_engine_config_ci_defaults_to_none() -> None:
    cfg = EngineConfig(repo=RepoIdentity(owner="a", repo="b"))
    assert cfg.ci is None


def test_engine_config_with_ci() -> None:
    cfg = EngineConfig(
        repo=RepoIdentity(owner="a", repo="b"),
        ci=CiConfig(main_ci_workflow="tests.yml", deploy_workflow=None),
    )
    assert cfg.ci is not None
    assert cfg.ci.main_ci_workflow == "tests.yml"
    assert cfg.ci.deploy_workflow is None


def test_engine_config_minimal_valid() -> None:
    cfg = EngineConfig(repo=RepoIdentity(owner="a", repo="b"))
    assert cfg.code_quality is None
    assert cfg.forge is None
    assert cfg.ci is None
    assert cfg.roles == {}
    assert cfg.gate_commands == []
    assert cfg.base_tools == []
    assert cfg.transition_graph == {}


def test_engine_config_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        EngineConfig.model_validate(
            {"repo": {"owner": "a", "repo": "b"}, "unknown_field": "x"}
        )


def test_engine_config_is_frozen() -> None:
    cfg = default_engine_config()
    with pytest.raises(ValidationError):
        cfg.repo = RepoIdentity(owner="x", repo="y")


def test_default_has_implementer_role_with_xhigh() -> None:
    cfg = default_engine_config()
    assert "implementer" in cfg.roles
    assert cfg.roles["implementer"].model == "sonnet"
    assert cfg.roles["implementer"].effort == "xhigh"


def test_default_has_base_tools() -> None:
    cfg = default_engine_config()
    assert "Read" in cfg.base_tools
    assert "Grep" in cfg.base_tools
    assert any("uv run" in t for t in cfg.base_tools)


def test_default_team_tools_has_impl_extras() -> None:
    cfg = default_engine_config()
    assert "implementation" in cfg.team_tools
    assert "Edit" in cfg.team_tools["implementation"]
    assert "Write" in cfg.team_tools["implementation"]
    assert cfg.team_tools["implementation"] == cfg.team_tools["implementation_fix"]


def test_default_team_tools_grants_web_to_research() -> None:
    cfg = default_engine_config()
    assert cfg.team_tools["research"] == _WEB_RESEARCH_TOOLS


def test_default_team_tools_grants_web_to_brainstorming() -> None:
    cfg = default_engine_config()
    assert cfg.team_tools["brainstorming"] == _WEB_RESEARCH_TOOLS


def test_default_team_tools_grants_web_to_opportunity_scout() -> None:
    cfg = default_engine_config()
    assert cfg.team_tools["opportunity_scout"] == _WEB_RESEARCH_TOOLS


def test_default_team_tools_impl_teams_carry_no_web_tools() -> None:
    cfg = default_engine_config()
    for team in ("implementation", "implementation_fix"):
        assert "WebSearch" not in cfg.team_tools[team]
        assert "WebFetch" not in cfg.team_tools[team]


def test_default_engine_render_allowed_tools_grants_web_to_research() -> None:
    from alan.teams._base import render_allowed_tools

    cfg = default_engine_config()
    for team in ("research", "brainstorming", "opportunity_scout"):
        tools = render_allowed_tools(cfg, team)
        assert "WebSearch" in tools
        assert "WebFetch" in tools


def test_default_flow_policy_present() -> None:
    cfg = default_engine_config()
    assert cfg.flow_policy is not None
    assert "pr_merge" in cfg.flow_policy.merge_teams
    assert "verification" in cfg.flow_policy.verifier_teams
    assert "deploy_verify" in cfg.flow_policy.terminal_teams


def test_default_flow_policy_passes_validation() -> None:
    from alan.engine_config_validation import validate_flow_graph

    cfg = default_engine_config()
    assert cfg.flow_policy is not None
    validate_flow_graph(cfg.transition_graph, cfg.flow_policy)


def test_default_transition_graph_matches_policy() -> None:
    cfg = default_engine_config()
    assert frozenset(cfg.transition_graph["implementation"]) == frozenset({"verification"})
    assert frozenset(cfg.transition_graph["verification"]) == frozenset(
        {"implementation_fix", "pr_merge"}
    )
    assert cfg.transition_graph["maintenance"] == []


def test_default_gate_commands_nonempty() -> None:
    cfg = default_engine_config()
    assert len(cfg.gate_commands) > 0
    first_cmds = [cmd[0] for cmd in cfg.gate_commands]
    assert "uv" in first_cmds
    assert "npm" in first_cmds


def test_default_warmup_commands() -> None:
    cfg = default_engine_config()
    assert len(cfg.warmup_commands) >= 1
    assert cfg.warmup_commands[0][0] == "uv"


def test_dependency_sweep_config_defaults_to_disabled() -> None:
    cfg = EngineConfig(repo=RepoIdentity(owner="a", repo="b"))
    assert cfg.dependency_sweep.enabled is False
    assert cfg.dependency_sweep.enable_python_vuln_scan is False
    assert cfg.dependency_sweep.cadence_seconds == 7 * 24 * 3600


def test_dependency_sweep_config_is_frozen() -> None:
    cfg = DependencySweepConfig()
    with pytest.raises(ValidationError):
        cfg.enabled = True  # type: ignore[misc]


def test_dependency_sweep_config_rejects_nonpositive_cadence() -> None:
    with pytest.raises(ValidationError):
        DependencySweepConfig(cadence_seconds=0)


def test_engine_config_backward_compat_missing_dependency_sweep_key_defaults_disabled() -> None:
    payload = EngineConfig(repo=RepoIdentity(owner="a", repo="b")).model_dump(mode="json")
    del payload["dependency_sweep"]
    cfg = EngineConfig.model_validate(payload)
    assert cfg.dependency_sweep.enabled is False


def test_module_singleton_equals_default() -> None:
    assert _DEFAULT == default_engine_config()


def test_repo_identity_slug_property() -> None:
    ri = RepoIdentity(owner="acme", repo="widget")
    assert ri.slug == "acme/widget"


def test_repo_identity_from_slug_valid() -> None:
    ri = RepoIdentity.from_slug("acme/widget")
    assert ri.owner == "acme"
    assert ri.repo == "widget"


def test_repo_identity_from_slug_splits_on_first_slash() -> None:
    ri = RepoIdentity.from_slug("acme/widget/extra")
    assert ri.owner == "acme"
    assert ri.repo == "widget/extra"


def test_repo_identity_from_slug_no_slash_raises() -> None:
    with pytest.raises(ValueError, match="invalid slug"):
        RepoIdentity.from_slug("noslash")


def test_repo_identity_from_slug_empty_owner_raises() -> None:
    with pytest.raises(ValueError, match="invalid slug"):
        RepoIdentity.from_slug("/repo")


def test_repo_identity_from_slug_empty_repo_raises() -> None:
    with pytest.raises(ValueError, match="invalid slug"):
        RepoIdentity.from_slug("owner/")


def test_load_yaml_file_not_found() -> None:
    with pytest.raises(OSError, match="cannot read file"):
        load_yaml("/nonexistent/path/config.yaml")


def test_load_yaml_invalid_yaml(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("key: [unclosed")
    with pytest.raises(ValueError, match="invalid YAML"):
        load_yaml(bad)


def test_load_yaml_non_mapping_root(tmp_path: Path) -> None:
    bad = tmp_path / "list.yaml"
    bad.write_text("- item1\n- item2\n")
    with pytest.raises(ValueError, match="expected mapping at root"):
        load_yaml(bad)


def test_load_yaml_raises_on_duplicate_repo(tmp_path: Path) -> None:
    yaml_file = tmp_path / "dup.yaml"
    yaml_file.write_text("repo:\n  owner: x\n  repo: y\n")
    load_yaml(yaml_file)
    with pytest.raises(DuplicateEngineError, match="x/y"):
        load_yaml(yaml_file)


def test_load_directory_missing_dir_returns_empty(tmp_path: Path) -> None:
    assert load_directory(tmp_path / "does-not-exist") == []


def test_load_directory_empty_dir_returns_empty(tmp_path: Path) -> None:
    assert load_directory(tmp_path) == []


def test_load_directory_path_is_a_file_returns_empty(tmp_path: Path) -> None:
    f = tmp_path / "not-a-dir.yaml"
    f.write_text("repo:\n  owner: a\n  repo: b\n")
    assert load_directory(f) == []


def test_load_directory_loads_yaml_and_yml_files(tmp_path: Path) -> None:
    (tmp_path / "one.yaml").write_text("repo:\n  owner: eng-one\n  repo: repo-one\n")
    (tmp_path / "two.yml").write_text("repo:\n  owner: eng-two\n  repo: repo-two\n")
    (tmp_path / "ignore-me.txt").write_text("not an engine config")

    loaded = load_directory(tmp_path)

    assert {cfg.repo.slug for cfg in loaded} == {"eng-one/repo-one", "eng-two/repo-two"}


def test_load_directory_registers_each_config(tmp_path: Path) -> None:
    (tmp_path / "widget.yaml").write_text("repo:\n  owner: widget-co\n  repo: widget\n")

    load_directory(tmp_path)

    assert lookup_by_slug("widget-co/widget") is not None


def test_load_directory_propagates_duplicate_engine_error(tmp_path: Path) -> None:
    (tmp_path / "a.yaml").write_text("repo:\n  owner: dup\n  repo: engine\n")
    (tmp_path / "b.yaml").write_text("repo:\n  owner: dup\n  repo: engine\n")

    with pytest.raises(DuplicateEngineError, match="dup/engine"):
        load_directory(tmp_path)


def test_load_directory_propagates_missing_env_vars_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MISSING_TOKEN", raising=False)
    (tmp_path / "needs-token.yaml").write_text(
        "repo:\n  owner: needs\n  repo: token\n"
        "forge:\n  repo:\n    owner: needs\n    repo: token\n"
        "  credential_env_name: MISSING_TOKEN\n"
    )

    with pytest.raises(MissingEnvVarsError, match="MISSING_TOKEN"):
        load_directory(tmp_path)


def test_load_directory_nonpython_fixture_via_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RUSTCO_GH_TOKEN", "fake-token")
    fixture = _FIXTURES / "engine_config_forge_nonpython.yaml"
    (tmp_path / "nonpython.yaml").write_text(fixture.read_text())

    loaded = load_directory(tmp_path)

    assert len(loaded) == 1
    assert loaded[0].repo.slug == "rustco/cargo-service"
    assert loaded[0].forge is not None
    assert loaded[0].forge.authoritative_checks == frozenset()


def test_nonpython_fixture_validates() -> None:
    cfg = load_yaml(_FIXTURES / "engine_config_nonpython.yaml")
    assert cfg.code_quality is None
    assert cfg.repo.owner == "example-org"


def test_nonpython_fixture_has_no_uv_commands() -> None:
    cfg = load_yaml(_FIXTURES / "engine_config_nonpython.yaml")
    for cmd in cfg.gate_commands:
        assert cmd[0] != "uv", f"Expected no uv gate commands; got {cmd}"
    for cmd in cfg.warmup_commands:
        assert cmd[0] != "uv", f"Expected no uv warmup commands; got {cmd}"


def test_nonpython_fixture_has_roles() -> None:
    cfg = load_yaml(_FIXTURES / "engine_config_nonpython.yaml")
    assert "implementer" in cfg.roles


def test_secret_env_vars_with_code_quality() -> None:
    cfg = default_engine_config()
    assert _secret_env_vars(cfg) == ["SONAR_TOKEN", "ALAN_VERIFIER_GH_TOKEN"]


def test_secret_env_vars_without_code_quality() -> None:
    cfg = EngineConfig(repo=RepoIdentity(owner="a", repo="b"))
    assert _secret_env_vars(cfg) == []


def test_register_rejects_missing_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SONAR_TOKEN", raising=False)
    cfg = default_engine_config()
    with pytest.raises(MissingEnvVarsError, match="SONAR_TOKEN"):
        register(cfg)


def test_register_succeeds_when_env_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SONAR_TOKEN", "fake-token")
    monkeypatch.setenv("ALAN_VERIFIER_GH_TOKEN", "fake-gh-token")
    cfg = default_engine_config()
    register(cfg, default=True)
    assert get_registered() == cfg


def test_register_no_code_quality_always_succeeds() -> None:
    cfg = EngineConfig(repo=RepoIdentity(owner="a", repo="b"))
    register(cfg)
    assert lookup_by_repo(cfg.repo) == cfg


def test_register_no_flow_policy_skips_validation() -> None:
    cfg = EngineConfig(repo=RepoIdentity(owner="a", repo="b"))
    register(cfg)
    assert lookup_by_repo(cfg.repo) == cfg


def test_load_yaml_no_flow_policy_skips_validation(tmp_path: Path) -> None:
    yaml_file = tmp_path / "minimal.yaml"
    yaml_file.write_text("repo:\n  owner: x\n  repo: y\n")
    cfg = load_yaml(yaml_file)
    assert cfg.flow_policy is None


def test_register_raises_flow_graph_error_on_bad_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    from alan.engine_config import FlowGraphPolicy
    from alan.engine_config_validation import FlowGraphValidationError

    monkeypatch.setenv("SONAR_TOKEN", "fake-token")
    bad_policy = FlowGraphPolicy(
        verifier_teams=frozenset({"verification"}),
        merge_teams=frozenset({"pr_merge"}),
        terminal_teams=frozenset({"deploy_verify"}),
        internal_only_teams=frozenset(),
        restricted_predecessors={},
        revisit_cap=3,
    )
    cfg = EngineConfig(
        repo=RepoIdentity(owner="a", repo="b"),
        transition_graph={"implementation": ["pr_merge"], "pr_merge": []},
        flow_policy=bad_policy,
    )
    with pytest.raises(FlowGraphValidationError, match="non-verifier"):
        register(cfg)


def test_engine_config_cache_env_defaults_to_empty_dict() -> None:
    cfg = EngineConfig(repo=RepoIdentity(owner="a", repo="b"))
    assert cfg.cache_env == {}


def test_default_engine_config_cache_env_reproduces_todays_uv_npm_mapping() -> None:
    cfg = default_engine_config()
    assert cfg.cache_env == {"UV_CACHE_DIR": "uv", "npm_config_cache": "npm"}


def test_engine_config_backward_compat_missing_cache_env_key_defaults_to_empty() -> None:
    payload = default_engine_config().model_dump(mode="json")
    del payload["cache_env"]
    cfg = EngineConfig.model_validate(payload)
    assert cfg.cache_env == {}


def test_slot_cache_base_defaults_to_home_alan_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    from alan.engine_config import _slot_cache_base  # noqa: SLF001

    monkeypatch.delenv("ALAN_STATE_ROOT", raising=False)
    assert _slot_cache_base() == Path.home() / ".alan" / "cache"


def test_slot_cache_base_derives_from_state_root_sibling(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from alan.engine_config import _slot_cache_base  # noqa: SLF001

    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    assert _slot_cache_base() == tmp_path / "cache"


def test_resolve_cache_env_derives_path_from_slot_cache_base(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from alan.engine_config import resolve_cache_env

    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    assert resolve_cache_env({"NUGET_PACKAGES": "nuget"}) == {
        "NUGET_PACKAGES": str(tmp_path / "cache" / "nuget")
    }


def test_resolve_cache_env_uv_honors_isolation_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from alan.engine_config import resolve_cache_env

    monkeypatch.setenv("ALAN_SLOT_UV_CACHE", str(tmp_path))
    assert resolve_cache_env({"UV_CACHE_DIR": "uv"}) == {"UV_CACHE_DIR": str(tmp_path)}


def test_resolve_cache_env_npm_honors_isolation_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from alan.engine_config import resolve_cache_env

    monkeypatch.setenv("ALAN_SLOT_NPM_CACHE", str(tmp_path))
    assert resolve_cache_env({"npm_config_cache": "npm"}) == {"npm_config_cache": str(tmp_path)}


def test_resolve_cache_env_empty_dict_returns_empty() -> None:
    from alan.engine_config import resolve_cache_env

    assert resolve_cache_env({}) == {}


def test_register_rejects_cache_env_reserved_sandbox_root_key() -> None:
    from alan.engine_config_validation import CacheEnvValidationError

    cfg = EngineConfig(
        repo=RepoIdentity(owner="a", repo="b"),
        cache_env={"ALAN_STATE_ROOT": "evil"},
    )
    with pytest.raises(CacheEnvValidationError, match="reserved"):
        register(cfg, validate_env=False)


def test_register_rejects_cache_env_control_token_key() -> None:
    from alan.engine_config_validation import CacheEnvValidationError

    cfg = EngineConfig(
        repo=RepoIdentity(owner="a", repo="b"),
        cache_env={"ALAN_CONTROL_TOKEN": "evil"},
    )
    with pytest.raises(CacheEnvValidationError, match="reserved"):
        register(cfg, validate_env=False)


def test_register_rejects_cache_env_default_secret_name() -> None:
    from alan.engine_config_validation import CacheEnvValidationError

    cfg = EngineConfig(
        repo=RepoIdentity(owner="a", repo="b"),
        cache_env={"SONAR_TOKEN": "evil"},
    )
    with pytest.raises(CacheEnvValidationError, match="reserved"):
        register(cfg, validate_env=False)


def test_register_rejects_cache_env_path_and_home() -> None:
    from alan.engine_config_validation import CacheEnvValidationError

    for name in ("PATH", "HOME"):
        cfg = EngineConfig(repo=RepoIdentity(owner="a", repo="b"), cache_env={name: "evil"})
        with pytest.raises(CacheEnvValidationError, match="reserved"):
            register(cfg, validate_env=False)


def test_register_rejects_cache_env_absolute_path_value() -> None:
    from alan.engine_config_validation import CacheEnvValidationError

    cfg = EngineConfig(
        repo=RepoIdentity(owner="a", repo="b"),
        cache_env={"NUGET_PACKAGES": "/etc/passwd"},
    )
    with pytest.raises(CacheEnvValidationError, match="relative"):
        register(cfg, validate_env=False)


def test_register_rejects_cache_env_dotdot_path_value() -> None:
    from alan.engine_config_validation import CacheEnvValidationError

    cfg = EngineConfig(
        repo=RepoIdentity(owner="a", repo="b"),
        cache_env={"NUGET_PACKAGES": "../escape"},
    )
    with pytest.raises(CacheEnvValidationError, match="relative"):
        register(cfg, validate_env=False)


def test_register_rejects_cache_env_bad_key_pattern() -> None:
    from alan.engine_config_validation import CacheEnvValidationError

    cfg = EngineConfig(repo=RepoIdentity(owner="a", repo="b"), cache_env={"1BAD": "x"})
    with pytest.raises(CacheEnvValidationError):
        register(cfg, validate_env=False)


def test_register_accepts_lowercase_npm_style_cache_env_key() -> None:
    cfg = EngineConfig(
        repo=RepoIdentity(owner="a", repo="b"), cache_env={"npm_config_cache": "npm"}
    )
    register(cfg, validate_env=False)
    assert lookup_by_repo(cfg.repo) == cfg


def test_register_accepts_relative_subdir_cache_env_value() -> None:
    cfg = EngineConfig(
        repo=RepoIdentity(owner="a", repo="b"), cache_env={"NUGET_PACKAGES": "nuget"}
    )
    register(cfg, validate_env=False)
    assert lookup_by_repo(cfg.repo) == cfg


def test_engine_config_cache_env_roundtrips_through_persistence() -> None:
    cfg = EngineConfig(
        repo=RepoIdentity(owner="a", repo="b"), cache_env={"NUGET_PACKAGES": "nuget"}
    )
    register_and_persist(cfg)
    reloaded = lookup_by_repo(cfg.repo)
    assert reloaded is not None
    assert reloaded.cache_env == {"NUGET_PACKAGES": "nuget"}


def test_duplicate_engine_error_is_value_error() -> None:
    assert issubclass(DuplicateEngineError, ValueError)


def test_register_raises_on_duplicate_repo() -> None:
    cfg = EngineConfig(repo=RepoIdentity(owner="a", repo="b"))
    register(cfg)
    with pytest.raises(DuplicateEngineError, match="a/b"):
        register(cfg)


def test_register_validate_env_false_skips_env_check(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SONAR_TOKEN", raising=False)
    cfg = default_engine_config()
    register(cfg, validate_env=False)
    assert lookup_by_repo(cfg.repo) == cfg


def test_lookup_by_repo_returns_config() -> None:
    cfg = EngineConfig(repo=RepoIdentity(owner="a", repo="b"))
    register(cfg)
    assert lookup_by_repo(RepoIdentity(owner="a", repo="b")) == cfg


def test_lookup_by_repo_returns_none_for_unknown() -> None:
    assert lookup_by_repo(RepoIdentity(owner="no", repo="exist")) is None


def test_lookup_by_slug_returns_config() -> None:
    cfg = EngineConfig(repo=RepoIdentity(owner="a", repo="b"))
    register(cfg)
    assert lookup_by_slug("a/b") == cfg


def test_lookup_by_slug_returns_none_for_unknown() -> None:
    assert lookup_by_slug("no/exist") is None


def test_lookup_by_slug_returns_none_for_invalid_slug() -> None:
    assert lookup_by_slug("noslash") is None


def test_get_registered_returns_none_without_default() -> None:
    cfg = EngineConfig(repo=RepoIdentity(owner="a", repo="b"))
    register(cfg)
    assert get_registered() is None


def test_get_registered_returns_none_when_registry_empty() -> None:
    assert get_registered() is None


def test_second_engine_fixture_defaults_to_observe() -> None:
    cfg = load_yaml(_FIXTURES / "engine_config_secondary.yaml")
    assert cfg.posture == Posture.observe


def test_second_engine_admitted_teams_empty() -> None:
    cfg = load_yaml(_FIXTURES / "engine_config_secondary.yaml")
    from alan.engine_config import _TEAM_MIN_POSTURE  # pyright: ignore[reportPrivateUsage]
    all_teams = set(_TEAM_MIN_POSTURE.keys())
    assert admitted_teams(all_teams, cfg.posture) == set()


def test_registry_holds_multiple_engines(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SONAR_TOKEN", "fake-token")
    monkeypatch.setenv("ALAN_VERIFIER_GH_TOKEN", "fake-gh-token")
    default = default_engine_config()
    secondary = load_yaml(_FIXTURES / "engine_config_secondary.yaml")
    register(default, default=True)
    assert get_registered() == default
    assert lookup_by_repo(secondary.repo) == secondary
    assert lookup_by_slug(secondary.repo.slug) == secondary


def test_default_engine_distinct_from_second_engine() -> None:
    secondary = load_yaml(_FIXTURES / "engine_config_secondary.yaml")
    assert secondary.repo != default_engine_config().repo


def test_engine_config_preset_defaults_to_none() -> None:
    cfg = EngineConfig(repo=RepoIdentity(owner="a", repo="b"))
    assert cfg.preset is None


def test_default_engine_config_preset_is_none() -> None:
    assert default_engine_config().preset is None


def test_engine_config_preset_accepts_arbitrary_free_form_value() -> None:
    cfg = EngineConfig(repo=RepoIdentity(owner="a", repo="b"), preset="rust-custom")
    assert cfg.preset == "rust-custom"


def test_engine_config_backward_compat_missing_preset_key_defaults_to_none() -> None:
    payload = default_engine_config().model_dump(mode="json")
    del payload["preset"]
    cfg = EngineConfig.model_validate(payload)
    assert cfg.preset is None


def test_engine_config_preset_field_roundtrips_through_persistence() -> None:
    cfg = EngineConfig(repo=RepoIdentity(owner="a", repo="b"), preset="dotnet")
    register_and_persist(cfg)
    payloads = _persisted_engines()
    assert payloads[0]["preset"] == "dotnet"
    reloaded = EngineConfig.model_validate(payloads[0])
    assert reloaded == cfg


def test_default_registered_at_module_level(
    module_init_registry_state: tuple[dict[Any, Any], Any],
) -> None:
    registry, default_key = module_init_registry_state
    assert _DEFAULT.repo in registry
    assert default_key == _DEFAULT.repo


def test_engine_config_validation_is_fourteenth_to_bottom_import_layer() -> None:
    toml_path = Path(__file__).parent.parent / "pyproject.toml"
    data = tomllib.loads(toml_path.read_text())
    layered = next(c for c in data["tool"]["importlinter"]["contracts"] if c["type"] == "layers")
    layers: list[str] = layered["layers"]
    assert layers[-15:] == [
        "engine_config_validation", "worker_liveness", "worker_records", "detached_mode",
        "store", "roots", "live_staleness", "deploy_signal", "served_sha", "deploy_restart",
        "clock", "source_fetch", "base_ref", "block_correction", "process_runner",
    ], layers[-15:]


@pytest.fixture(autouse=True)
def _engines_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "engines"
    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(root))
    return root


def test_register_and_persist_writes_to_store() -> None:
    cfg = EngineConfig(repo=RepoIdentity(owner="a", repo="b"))
    register_and_persist(cfg)
    payloads = _persisted_engines()
    assert len(payloads) == 1
    assert payloads[0]["repo"] == {"owner": "a", "repo": "b"}


def test_register_and_persist_registers_in_memory() -> None:
    cfg = EngineConfig(repo=RepoIdentity(owner="a", repo="b"))
    register_and_persist(cfg)
    assert lookup_by_repo(cfg.repo) == cfg


def test_register_and_persist_duplicate_does_not_write_twice() -> None:
    cfg = EngineConfig(repo=RepoIdentity(owner="a", repo="b"))
    register_and_persist(cfg)
    with pytest.raises(DuplicateEngineError):
        register_and_persist(cfg)
    assert len(_persisted_engines()) == 1


def test_register_and_persist_missing_env_does_not_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SONAR_TOKEN", raising=False)
    cfg = default_engine_config()
    with pytest.raises(MissingEnvVarsError):
        register_and_persist(cfg)
    assert _persisted_engines() == []


def test_load_persisted_engines_reloads_after_fresh_registry() -> None:
    from alan.engine_config import _registry as _reg  # pyright: ignore[reportPrivateUsage]

    cfg = EngineConfig(repo=RepoIdentity(owner="a", repo="b"))
    register_and_persist(cfg)
    _reg.clear()
    assert lookup_by_repo(cfg.repo) is None

    loaded = load_persisted_engines()

    assert loaded == [cfg]
    assert lookup_by_repo(cfg.repo) == cfg


def test_load_persisted_engines_empty_store_returns_empty() -> None:
    assert load_persisted_engines() == []


def test_load_persisted_engines_replaces_an_already_registered_repo() -> None:
    from alan.engine_registry_store import write_engine

    cfg = EngineConfig(repo=RepoIdentity(owner="a", repo="b"))
    register(cfg)
    stored = cfg.model_copy(update={"accent": AccentChoice(color="iris", intensity="standard")})
    write_engine(stored.repo.slug, stored.model_dump(mode="json"))

    loaded = load_persisted_engines()

    assert loaded == [stored]
    assert lookup_by_repo(cfg.repo) == stored


def test_load_persisted_engines_keeps_the_default_key_when_replacing_the_default_repo() -> None:
    from alan.engine_registry_store import write_engine

    cfg = default_engine_config()
    register(cfg, default=True, validate_env=False)
    stored = cfg.model_copy(update={"accent": AccentChoice(color="iris", intensity="standard")})
    write_engine(stored.repo.slug, stored.model_dump(mode="json"))

    load_persisted_engines()

    assert get_registered() == stored


def test_load_persisted_engines_reload_bypasses_env_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alan.engine_registry_store import write_engine

    monkeypatch.delenv("SONAR_TOKEN", raising=False)
    cfg = default_engine_config()
    write_engine(cfg.repo.slug, cfg.model_dump(mode="json"))

    loaded = load_persisted_engines()

    assert loaded == [cfg]
    assert lookup_by_repo(cfg.repo) == cfg


def test_register_and_persist_disk_write_failure_leaves_registry_unmodified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alan import engine_registry_store

    def _boom(slug: str, payload: dict[str, object]) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(engine_registry_store, "write_connected", _boom)
    cfg = EngineConfig(repo=RepoIdentity(owner="a", repo="b"))

    with pytest.raises(OSError, match="disk full"):
        register_and_persist(cfg)

    assert lookup_by_repo(cfg.repo) is None
    assert _persisted_engines() == []


def test_register_concurrent_distinct_engines_all_succeed() -> None:
    import threading

    configs = [EngineConfig(repo=RepoIdentity(owner="a", repo=f"b{i}")) for i in range(20)]
    errors: list[BaseException] = []
    errors_lock = threading.Lock()

    def _worker(cfg: EngineConfig) -> None:
        try:
            register(cfg)
        except BaseException as exc:  # noqa: BLE001
            with errors_lock:
                errors.append(exc)

    threads = [threading.Thread(target=_worker, args=(cfg,)) for cfg in configs]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert len(registered_engines()) == len(configs)


def test_register_and_persist_concurrent_distinct_engines_all_persisted() -> None:
    import threading

    configs = [EngineConfig(repo=RepoIdentity(owner="a", repo=f"c{i}")) for i in range(20)]
    errors: list[BaseException] = []
    errors_lock = threading.Lock()

    def _worker(cfg: EngineConfig) -> None:
        try:
            register_and_persist(cfg)
        except BaseException as exc:  # noqa: BLE001
            with errors_lock:
                errors.append(exc)

    threads = [threading.Thread(target=_worker, args=(cfg,)) for cfg in configs]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert len(_persisted_engines()) == len(configs)


def test_register_concurrent_duplicate_repo_exactly_one_wins() -> None:
    import threading

    cfg = EngineConfig(repo=RepoIdentity(owner="a", repo="b"))
    successes: list[int] = []
    failures: list[int] = []
    result_lock = threading.Lock()

    def _worker() -> None:
        try:
            register(cfg)
        except DuplicateEngineError:
            with result_lock:
                failures.append(1)
        else:
            with result_lock:
                successes.append(1)

    threads = [threading.Thread(target=_worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(successes) == 1
    assert len(failures) == 19
    assert len(registered_engines()) == 1


def test_update_and_persist_replaces_registry_entry() -> None:
    cfg = EngineConfig(repo=RepoIdentity(owner="a", repo="b"))
    register_and_persist(cfg)
    updated = EngineConfig(repo=RepoIdentity(owner="a", repo="b"), pool_size=4)

    update_and_persist(updated, validate_env=False)

    assert lookup_by_repo(cfg.repo) == updated


def test_update_and_persist_writes_to_store() -> None:
    cfg = EngineConfig(repo=RepoIdentity(owner="a", repo="b"))
    register_and_persist(cfg)
    updated = EngineConfig(repo=RepoIdentity(owner="a", repo="b"), pool_size=4)

    update_and_persist(updated, validate_env=False)

    payloads = _persisted_engines()
    assert len(payloads) == 1
    assert payloads[0]["pool_size"] == 4


def test_update_and_persist_missing_engine_raises_not_found() -> None:
    cfg = EngineConfig(repo=RepoIdentity(owner="a", repo="b"))
    with pytest.raises(EngineNotFoundError, match="a/b"):
        update_and_persist(cfg, validate_env=False)


def test_update_and_persist_preserves_default_flag() -> None:
    cfg = default_engine_config()
    register_and_persist(cfg, default=True, validate_env=False)
    updated = cfg.model_copy(update={"pool_size": 4})

    update_and_persist(updated, validate_env=False)

    assert get_registered() == updated


def test_update_and_persist_returns_persisted_config() -> None:
    cfg = EngineConfig(repo=RepoIdentity(owner="a", repo="b"))
    register_and_persist(cfg)
    updated = EngineConfig(repo=RepoIdentity(owner="a", repo="b"), pool_size=4)

    result = update_and_persist(updated, validate_env=False)

    assert result == updated


def test_update_and_persist_ignores_posture_in_config() -> None:
    cfg = EngineConfig(repo=RepoIdentity(owner="a", repo="b"), posture=Posture.observe)
    register_and_persist(cfg, validate_env=False)
    updated = cfg.model_copy(update={"posture": Posture.propose})

    result = update_and_persist(updated, validate_env=False)

    assert result.posture == Posture.observe
    stored = lookup_by_repo(cfg.repo)
    assert stored is not None
    assert stored.posture == Posture.observe


def test_update_and_persist_preserves_live_posture_against_stale_body() -> None:
    cfg = EngineConfig(repo=RepoIdentity(owner="a", repo="b"), posture=Posture.self_merge)
    register_and_persist(cfg, validate_env=False)
    set_posture_and_persist(cfg.repo, Posture.observe, source="api")
    stale_body = cfg.model_copy(update={"posture": Posture.self_merge, "pool_size": 4})

    result = update_and_persist(stale_body, validate_env=False)

    assert result.posture == Posture.observe
    assert result.pool_size == 4
    stored = lookup_by_repo(cfg.repo)
    assert stored is not None
    assert stored.posture == Posture.observe


def test_update_and_persist_ignores_prompt_overrides_in_config() -> None:
    cfg = EngineConfig(repo=RepoIdentity(owner="a", repo="b"))
    register_and_persist(cfg, validate_env=False)
    updated = cfg.model_copy(
        update={
            "prompt_overrides": {
                "verification/judge": PromptOverride(text="evil", base_fingerprint="fp"),
            }
        }
    )

    result = update_and_persist(updated, validate_env=False)

    assert result.prompt_overrides == {}
    stored = lookup_by_repo(cfg.repo)
    assert stored is not None
    assert stored.prompt_overrides == {}


def test_update_and_persist_preserves_live_prompt_overrides_against_stale_body() -> None:
    cfg = EngineConfig(repo=RepoIdentity(owner="a", repo="b"))
    register_and_persist(cfg, validate_env=False)
    override = PromptOverride(text="legit override", base_fingerprint="fp")
    apply_prompt_override_and_persist(cfg.repo, "verification/judge", override)
    stale_body = cfg.model_copy(update={"prompt_overrides": {}, "pool_size": 4})

    result = update_and_persist(stale_body, validate_env=False)

    assert result.prompt_overrides == {"verification/judge": override}
    assert result.pool_size == 4
    stored = lookup_by_repo(cfg.repo)
    assert stored is not None
    assert stored.prompt_overrides == {"verification/judge": override}


def test_update_and_persist_bad_flow_graph_raises() -> None:
    from alan.engine_config import FlowGraphPolicy
    from alan.engine_config_validation import FlowGraphValidationError

    cfg = EngineConfig(repo=RepoIdentity(owner="a", repo="b"))
    register_and_persist(cfg)
    bad_policy = FlowGraphPolicy(
        verifier_teams=frozenset({"verification"}),
        merge_teams=frozenset({"pr_merge"}),
        terminal_teams=frozenset({"deploy_verify"}),
        internal_only_teams=frozenset(),
        restricted_predecessors={},
        revisit_cap=3,
    )
    bad_update = EngineConfig(
        repo=RepoIdentity(owner="a", repo="b"),
        transition_graph={"implementation": ["pr_merge"], "pr_merge": []},
        flow_policy=bad_policy,
    )
    with pytest.raises(FlowGraphValidationError, match="non-verifier"):
        update_and_persist(bad_update, validate_env=False)


def test_unregister_and_persist_removes_from_registry_and_store() -> None:
    cfg = EngineConfig(repo=RepoIdentity(owner="a", repo="b"))
    register_and_persist(cfg)

    result = unregister_and_persist(cfg.repo)

    assert result is True
    assert lookup_by_repo(cfg.repo) is None
    assert _persisted_engines() == []


def test_unregister_and_persist_missing_engine_returns_false() -> None:
    result = unregister_and_persist(RepoIdentity(owner="no", repo="such"))
    assert result is False


def test_unregister_and_persist_clears_default_key() -> None:
    cfg = default_engine_config()
    register_and_persist(cfg, default=True, validate_env=False)

    unregister_and_persist(cfg.repo)

    assert get_registered() is None


def test_delete_engine_removes_from_registry_and_store() -> None:
    cfg = EngineConfig(repo=RepoIdentity(owner="a", repo="b"))
    register_and_persist(cfg)

    delete_engine(cfg.repo)

    assert lookup_by_repo(cfg.repo) is None
    assert _persisted_engines() == []


def test_delete_engine_missing_engine_is_a_silent_noop() -> None:
    delete_engine(RepoIdentity(owner="no", repo="such"))


def test_canonical_scope_key_none_folds_to_default_repo_identity() -> None:
    assert canonical_scope_key(None) == default_engine_config().repo


def test_canonical_scope_key_explicit_default_slug_matches_none() -> None:
    assert canonical_scope_key("DejvMatikar/alan") == canonical_scope_key(None)


def test_canonical_scope_key_registered_engine_returns_its_own_repo() -> None:
    register(EngineConfig(repo=RepoIdentity(owner="acme", repo="widget")))
    assert canonical_scope_key("acme/widget") == RepoIdentity(owner="acme", repo="widget")


def test_canonical_scope_key_unregistered_slug_folds_to_default() -> None:
    assert canonical_scope_key("ghost-co/ghost-repo") == canonical_scope_key(None)


def test_canonical_scope_key_unparseable_slug_folds_to_default() -> None:
    assert canonical_scope_key("no-slash-here") == canonical_scope_key(None)


def test_canonical_scope_key_none_prefers_registered_default_over_hardcoded() -> None:
    custom_default = EngineConfig(repo=RepoIdentity(owner="custom", repo="root"))
    register(custom_default, default=True)
    assert canonical_scope_key(None) == RepoIdentity(owner="custom", repo="root")
    assert canonical_scope_key("custom/root") == canonical_scope_key(None)


def test_canonical_scope_key_distinguishes_two_registered_non_default_engines() -> None:
    register(EngineConfig(repo=RepoIdentity(owner="sprocket-co", repo="sprocket")))
    register(EngineConfig(repo=RepoIdentity(owner="acme", repo="widget")))
    assert canonical_scope_key("sprocket-co/sprocket") != canonical_scope_key("acme/widget")
    assert canonical_scope_key("sprocket-co/sprocket") != canonical_scope_key(None)


def test_display_scope_slug_none_is_none() -> None:
    assert display_scope_slug(None) is None


def test_display_scope_slug_explicit_default_slug_is_none() -> None:
    assert display_scope_slug("DejvMatikar/alan") is None


def test_display_scope_slug_unregistered_slug_is_none() -> None:
    assert display_scope_slug("ghost-co/ghost-repo") is None


def test_display_scope_slug_registered_non_default_returns_slug() -> None:
    register(EngineConfig(repo=RepoIdentity(owner="acme", repo="widget")))
    assert display_scope_slug("acme/widget") == "acme/widget"


def test_same_repo_scope_none_and_default_slug_match() -> None:
    default_slug = default_engine_config().repo.slug
    assert same_repo_scope(None, default_slug) is True


def test_same_repo_scope_default_slug_and_none_match() -> None:
    default_slug = default_engine_config().repo.slug
    assert same_repo_scope(default_slug, None) is True


def test_same_repo_scope_both_none_match() -> None:
    assert same_repo_scope(None, None) is True


def test_same_repo_scope_same_registered_other_engine_matches() -> None:
    register(EngineConfig(repo=RepoIdentity(owner="acme", repo="widget")))
    assert same_repo_scope("acme/widget", "acme/widget") is True


def test_same_repo_scope_default_and_other_registered_engine_differ() -> None:
    register(EngineConfig(repo=RepoIdentity(owner="acme", repo="widget")))
    default_slug = default_engine_config().repo.slug
    assert same_repo_scope(default_slug, "acme/widget") is False


def test_same_repo_scope_unregistered_slug_folds_to_default_with_no_exception() -> None:
    assert same_repo_scope("ghost-co/ghost-repo", None) is True


def test_prompt_override_is_frozen() -> None:
    override = PromptOverride(text="hello", base_fingerprint="abc123")
    with pytest.raises(ValidationError):
        override.text = "changed"  # type: ignore[misc]


def test_engine_config_prompt_overrides_defaults_to_empty_dict() -> None:
    cfg = EngineConfig(repo=RepoIdentity(owner="a", repo="b"))
    assert cfg.prompt_overrides == {}


def test_engine_config_backward_compat_missing_prompt_overrides_key_defaults_to_empty(
) -> None:
    payload = default_engine_config().model_dump(mode="json")
    del payload["prompt_overrides"]
    cfg = EngineConfig.model_validate(payload)
    assert cfg.prompt_overrides == {}


def test_engine_config_prompt_overrides_roundtrips_through_persistence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(tmp_path / "engines"))
    cfg = EngineConfig(
        repo=RepoIdentity(owner="a", repo="b"),
        prompt_overrides={
            "implementation/implementer": PromptOverride(
                text="custom {{ENVIRONMENT}} {{SUCCESS}} {{REPO_GUIDANCE}}",
                base_fingerprint="deadbeef",
            )
        },
    )
    register_and_persist(cfg)
    reloaded = lookup_by_repo(cfg.repo)
    assert reloaded == cfg
    assert reloaded is not None
    entry = reloaded.prompt_overrides["implementation/implementer"]
    assert entry.text == "custom {{ENVIRONMENT}} {{SUCCESS}} {{REPO_GUIDANCE}}"
    assert entry.base_fingerprint == "deadbeef"


def test_engine_config_prompt_overrides_preserves_blank_text_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(tmp_path / "engines"))
    cfg = EngineConfig(
        repo=RepoIdentity(owner="a", repo="b"),
        prompt_overrides={
            "verification/judge": PromptOverride(text="", base_fingerprint="fp"),
        },
    )
    register_and_persist(cfg)
    reloaded = lookup_by_repo(cfg.repo)
    assert reloaded is not None
    assert reloaded.prompt_overrides["verification/judge"].text == ""


def test_engine_config_model_copy_prompt_overrides_never_shares_dict_identity() -> None:
    cfg = EngineConfig(repo=RepoIdentity(owner="a", repo="b"))
    new_overrides = dict(cfg.prompt_overrides)
    new_overrides["implementation/implementer"] = PromptOverride(
        text="x {{ENVIRONMENT}} {{SUCCESS}} {{REPO_GUIDANCE}}", base_fingerprint="fp"
    )
    updated = cfg.model_copy(update={"prompt_overrides": new_overrides})
    assert updated.prompt_overrides is not cfg.prompt_overrides
    assert cfg.prompt_overrides == {}


def test_apply_prompt_override_and_persist_adds_entry() -> None:
    cfg = EngineConfig(repo=RepoIdentity(owner="a", repo="b"))
    register_and_persist(cfg, validate_env=False)
    override = PromptOverride(
        text="x {{ENVIRONMENT}} {{SUCCESS}} {{REPO_GUIDANCE}}", base_fingerprint="fp"
    )

    result = apply_prompt_override_and_persist(cfg.repo, "implementation/implementer", override)

    assert result.prompt_overrides == {"implementation/implementer": override}
    stored = lookup_by_repo(cfg.repo)
    assert stored is not None
    assert stored.prompt_overrides == {"implementation/implementer": override}


def test_apply_prompt_override_and_persist_missing_engine_raises_not_found() -> None:
    override = PromptOverride(text="x", base_fingerprint="fp")
    with pytest.raises(EngineNotFoundError, match="a/b"):
        apply_prompt_override_and_persist(
            RepoIdentity(owner="a", repo="b"), "verification/judge", override
        )


def test_apply_prompt_override_and_persist_never_mutates_prior_dict_in_place() -> None:
    cfg = EngineConfig(repo=RepoIdentity(owner="a", repo="b"))
    register_and_persist(cfg, validate_env=False)
    before = lookup_by_repo(cfg.repo)
    assert before is not None
    before_dict = before.prompt_overrides
    override = PromptOverride(text="x", base_fingerprint="fp")

    apply_prompt_override_and_persist(cfg.repo, "verification/judge", override)

    after = lookup_by_repo(cfg.repo)
    assert after is not None
    assert after.prompt_overrides is not before_dict
    assert before_dict == {}


def test_apply_prompt_override_and_persist_replaces_existing_key() -> None:
    cfg = EngineConfig(repo=RepoIdentity(owner="a", repo="b"))
    register_and_persist(cfg, validate_env=False)
    first = PromptOverride(text="first", base_fingerprint="fp1")
    apply_prompt_override_and_persist(cfg.repo, "verification/judge", first)
    second = PromptOverride(text="second", base_fingerprint="fp2")

    result = apply_prompt_override_and_persist(cfg.repo, "verification/judge", second)

    assert result.prompt_overrides == {"verification/judge": second}
