from __future__ import annotations

import re
from pathlib import Path

import pytest

from alan import engine_preflight as ep
from alan.engine_config import RepoIdentity, lookup_by_slug
from alan.engine_presets import (
    PRESETS,
    build_config,
    dotnet_preset,
    get_preset,
    ios_preset,
    list_presets,
    node_preset,
    python_preset,
)
from alan.teams._base import render_allowed_tools


def test_presets_are_never_registered_in_the_live_registry() -> None:
    dotnet_preset()
    python_preset()
    node_preset()
    assert lookup_by_slug("_preset/dotnet") is None
    assert lookup_by_slug("_preset/python") is None
    assert lookup_by_slug("_preset/node") is None


def test_all_presets_carry_no_per_project_identity() -> None:
    for cfg in (dotnet_preset(), python_preset(), node_preset()):
        assert cfg.code_quality is None
        assert cfg.forge is None
        assert cfg.ci is None


def test_all_presets_default_to_observe_posture() -> None:
    from alan.engine_config import Posture

    for cfg in (dotnet_preset(), python_preset(), node_preset()):
        assert cfg.posture == Posture.observe


def test_all_presets_use_sentinel_repo_identities() -> None:
    assert dotnet_preset().repo == RepoIdentity(owner="_preset", repo="dotnet")
    assert python_preset().repo == RepoIdentity(owner="_preset", repo="python")
    assert node_preset().repo == RepoIdentity(owner="_preset", repo="node")


def test_all_presets_inherit_default_roles_and_transition_graph() -> None:
    from alan.engine_config import default_engine_config

    default = default_engine_config()
    for cfg in (dotnet_preset(), python_preset(), node_preset()):
        assert cfg.roles == default.roles
        assert cfg.transition_graph == default.transition_graph
        assert cfg.flow_policy == default.flow_policy


def test_preset_marker_records_own_name() -> None:
    assert dotnet_preset().preset == "dotnet"
    assert python_preset().preset == "python"
    assert node_preset().preset == "node"


def test_dotnet_preset_widens_base_tools_with_dotnet_bash_prefix() -> None:
    cfg = dotnet_preset()
    assert "Bash(dotnet:*)" in cfg.base_tools


def test_dotnet_preset_inherits_rest_of_default_base_tools() -> None:
    from alan.engine_config import default_engine_config

    cfg = dotnet_preset()
    default = default_engine_config()
    assert set(default.base_tools) <= set(cfg.base_tools)


def test_dotnet_preset_defines_build_test_and_format_gates() -> None:
    cfg = dotnet_preset()
    assert ["dotnet", "build"] in cfg.gate_commands
    assert ["dotnet", "test"] in cfg.gate_commands
    assert ["dotnet", "format", "--verify-no-changes"] in cfg.gate_commands


def test_dotnet_preset_defines_restore_warmup() -> None:
    cfg = dotnet_preset()
    assert cfg.warmup_commands == [["dotnet", "restore"]]


def test_dotnet_preset_declares_nuget_cache_env() -> None:
    assert dotnet_preset().cache_env == {"NUGET_PACKAGES": "nuget"}


def test_python_preset_gates_are_generic_not_alan_repo_specific() -> None:
    cfg = python_preset()
    rendered = [" ".join(cmd) for cmd in [*cfg.gate_commands, *cfg.warmup_commands]]
    assert cfg.gate_commands
    assert cfg.warmup_commands
    for cmd in rendered:
        assert "alan" not in cmd


def test_node_preset_gates_are_generic_not_alan_repo_specific() -> None:
    cfg = node_preset()
    rendered = [" ".join(cmd) for cmd in [*cfg.gate_commands, *cfg.warmup_commands]]
    assert cfg.gate_commands
    assert cfg.warmup_commands
    for cmd in rendered:
        assert "alan" not in cmd


def test_python_and_node_presets_do_not_widen_base_tools() -> None:
    from alan.engine_config import default_engine_config

    default = default_engine_config()
    assert python_preset().base_tools == default.base_tools
    assert node_preset().base_tools == default.base_tools


def test_python_preset_overrides_cache_env_to_uv_only() -> None:
    assert python_preset().cache_env == {"UV_CACHE_DIR": "uv"}


def test_node_preset_overrides_cache_env_to_npm_only() -> None:
    assert node_preset().cache_env == {"npm_config_cache": "npm"}


def test_presets_override_rather_than_inherit_default_cache_env() -> None:
    assert "npm_config_cache" not in python_preset().cache_env
    assert "UV_CACHE_DIR" not in node_preset().cache_env
    assert dotnet_preset().cache_env == {"NUGET_PACKAGES": "nuget"}


def test_get_preset_returns_matching_config() -> None:
    assert get_preset("dotnet") == dotnet_preset()
    assert get_preset("python") == python_preset()
    assert get_preset("node") == node_preset()
    assert get_preset("ios") == ios_preset()


def test_get_preset_unknown_name_returns_none() -> None:
    assert get_preset("cobol") is None


def test_list_presets_returns_sorted_name_config_pairs() -> None:
    names = [name for name, _cfg in list_presets()]
    assert names == ["dotnet", "ios", "node", "python"]


def test_presets_dict_has_exactly_the_four_named_presets() -> None:
    assert set(PRESETS) == {"dotnet", "python", "node", "ios"}


def test_ios_preset_registered_and_catalogue_sorted() -> None:
    assert "ios" in PRESETS
    assert ios_preset().preset == "ios"
    assert ios_preset().repo == RepoIdentity(owner="_preset", repo="ios")
    names = [name for name, _cfg in list_presets()]
    assert names == sorted(names)


def test_ios_preset_shape() -> None:
    cfg = ios_preset()
    for entry in (
        "Bash(xcodebuild:*)",
        "Bash(tuist:*)",
        "Bash(swiftlint:*)",
        "Bash(swift:*)",
        "Bash(swift-format:*)",
        "Bash(periphery:*)",
        "Bash(xcrun:*)",
        "Bash(lizard:*)",
        "Bash(bash scripts/gate.sh:*)",
    ):
        assert entry in cfg.base_tools
    assert cfg.gate_commands == [["bash", "scripts/gate.sh"]]
    assert cfg.warmup_commands == [["tuist", "install"]]
    assert cfg.cache_env == {"DERIVED_DATA_PATH": "deriveddata"}


def test_ios_preset_warmup_is_tuist_install() -> None:
    assert ios_preset().warmup_commands == [["tuist", "install"]]


def test_ios_preset_scenario_command_prefixes_include_python_interpreter() -> None:
    assert ["python3"] in ios_preset().scenario.command_prefixes


def test_ios_preset_gate_and_warmup_commands_reachable_from_own_allowlist() -> None:
    cfg = ios_preset()
    prefixes = ep._bash_allowlist_prefixes(render_allowed_tools(cfg, "implementation"))  # noqa: SLF001
    for cmd in [*cfg.gate_commands, *cfg.warmup_commands]:
        assert ep._command_reachable(cmd, prefixes), cmd  # noqa: SLF001


def test_ios_cache_env_resolves_under_state_root_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.engine_config import resolve_cache_env

    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    resolved = resolve_cache_env(ios_preset().cache_env)
    assert resolved["DERIVED_DATA_PATH"] == str(tmp_path / "cache" / "deriveddata")


def test_sentinel_repo_is_a_syntactically_valid_repo_identity() -> None:
    cfg = dotnet_preset()
    assert cfg.repo.owner == "_preset"
    assert cfg.repo.slug == "_preset/dotnet"


def test_build_config_swaps_sentinel_repo_for_chosen_identity() -> None:
    cfg = build_config("python", "acme", "widget")
    assert cfg is not None
    assert cfg.repo == RepoIdentity(owner="acme", repo="widget")


def test_build_config_overlays_a_forge_pointed_at_the_same_repo() -> None:
    cfg = build_config("dotnet", "acme", "widget")
    assert cfg is not None
    assert cfg.forge is not None
    assert cfg.forge.repo == RepoIdentity(owner="acme", repo="widget")
    assert cfg.forge.authoritative_checks == frozenset()


def test_build_config_credential_env_name_is_a_valid_env_var_name() -> None:
    cfg = build_config("node", "acme", "widget")
    assert cfg is not None
    assert cfg.forge is not None
    assert re.fullmatch(r"[A-Z_][A-Z0-9_]*", cfg.forge.credential_env_name)


def test_build_config_sanitizes_non_alnum_identity_into_env_name() -> None:
    cfg = build_config("python", "acme-co", "widget.app")
    assert cfg is not None
    assert cfg.forge is not None
    assert cfg.forge.credential_env_name == "ACME_CO_WIDGET_APP_GH_TOKEN"


def test_build_config_prefixes_underscore_when_identity_starts_with_digit() -> None:
    cfg = build_config("python", "1acme", "widget")
    assert cfg is not None
    assert cfg.forge is not None
    assert cfg.forge.credential_env_name.startswith("_")


def test_build_config_preserves_the_rest_of_the_preset_skeleton() -> None:
    cfg = build_config("python", "acme", "widget")
    assert cfg is not None
    assert cfg.posture == python_preset().posture
    assert cfg.gate_commands == python_preset().gate_commands
    assert cfg.preset == "python"


def test_build_config_unknown_name_returns_none() -> None:
    assert build_config("cobol", "acme", "widget") is None
