from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from alan import (
    default_engine_boot,
    default_profile,
    default_profile_writer,
    engine_config,
    engine_registry_store,
)
from alan.default_profile_writer import GATE_CHECK, WriteOutcome
from alan.engine_config import (
    EngineConfig,
    ForgeConfig,
    ForgeTarget,
    RepoIdentity,
    forge_target_for_workspace,
)

_FIXTURES = Path(__file__).parent / "fixtures"
_OWNER = "acme-corp"
_REPO = "widget-app"
_SLUG = f"{_OWNER}/{_REPO}"
_DECLARED = frozenset({GATE_CHECK})


def _use_profile_installed_in(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolved = default_profile.resolve(root, tmp_path / "no-owner.yaml")
    monkeypatch.setattr(default_profile, "resolved", lambda: resolved)


@pytest.fixture
def engines_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "engines"
    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(root))
    return root


@pytest.fixture
def gate_profile(engines_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    outcome = default_profile_writer.write_profile(engines_root, _OWNER, _REPO)
    assert outcome is WriteOutcome.written
    _use_profile_installed_in(engines_root, tmp_path, monkeypatch)
    return engines_root


def _forge_section() -> dict[str, Any]:
    return {
        "repo": {"owner": _OWNER, "repo": _REPO},
        "credential_env_name": "ACME_WIDGET_GH_TOKEN",
        "authoritative_checks": [GATE_CHECK],
    }


@pytest.mark.usefixtures("gate_profile")
def test_default_engine_config_declares_gate() -> None:
    forge = engine_config.default_engine_config().forge
    assert forge is not None
    assert forge.repo.slug == _SLUG
    assert forge.authoritative_checks == _DECLARED
    assert forge.aggregate_checks == _DECLARED


@pytest.mark.usefixtures("gate_profile")
def test_default_forge_target_builder_declares_gate() -> None:
    target = engine_config._default_forge_target()
    assert target.repo.slug == _SLUG
    assert target.authoritative_checks == _DECLARED
    assert target.aggregate_checks == _DECLARED


@pytest.mark.usefixtures("gate_profile")
def test_boot_merge_default_declares_gate_with_prefield_saved_forge() -> None:
    default = engine_config.default_engine_config()
    record = default.model_dump(mode="json")
    del record["forge"]["aggregate_checks"]
    engine_registry_store.write_connected(default.repo.slug, record)
    default_engine_boot.merge_saved_engines()
    booted = engine_config.get_registered()
    assert booted is not None
    assert booted.repo.slug == _SLUG
    assert booted.forge is not None
    assert booted.forge.aggregate_checks == _DECLARED


def test_profile_without_gate_authoritative_declares_nothing(
    engines_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = default_profile_writer.build_profile(_OWNER, _REPO).model_copy(
        update={
            "authoritative_checks": frozenset({"Widget CI"}),
            "authoritative_check_apps": {},
        }
    )
    engines_root.mkdir()
    (engines_root / default_profile.PROFILE_FILE_NAME).write_text(
        default_profile_writer.render_profile(profile)
    )
    _use_profile_installed_in(engines_root, tmp_path, monkeypatch)
    forge = engine_config.default_engine_config().forge
    target = engine_config._default_forge_target()
    assert forge is not None
    assert forge.authoritative_checks == frozenset({"Widget CI"})
    assert forge.aggregate_checks == frozenset()
    assert target.aggregate_checks == frozenset()


def test_unconfigured_profile_has_no_forge(
    engines_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_profile_installed_in(engines_root, tmp_path, monkeypatch)
    assert default_profile.resolved().source is default_profile.Source.unconfigured
    assert engine_config.default_engine_config().forge is None
    assert engine_config._default_forge_target().aggregate_checks == frozenset()


@pytest.mark.usefixtures("gate_profile")
def test_forge_target_for_workspace_paths_carry_declaration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = engine_config.default_engine_config()
    assert forge_target_for_workspace(None, snapshot).aggregate_checks == _DECLARED
    engine_config.register(snapshot, validate_env=False)
    assert forge_target_for_workspace(_SLUG, None).aggregate_checks == _DECLARED
    monkeypatch.setattr(
        engine_config, "DEFAULT_FORGE_TARGET", engine_config._default_forge_target()
    )
    assert forge_target_for_workspace(None, None).aggregate_checks == _DECLARED


def test_default_forge_target_constant_is_builder_output() -> None:
    assert engine_config.DEFAULT_FORGE_TARGET == engine_config._default_forge_target()


def test_forge_target_copies_the_declaration_from_the_forge_config() -> None:
    forge = ForgeConfig(
        repo=RepoIdentity(owner=_OWNER, repo=_REPO),
        credential_env_name="ACME_WIDGET_GH_TOKEN",
        authoritative_checks=_DECLARED,
        aggregate_checks=_DECLARED,
    )
    assert ForgeTarget.from_forge_config(forge).aggregate_checks == _DECLARED


def test_registered_engine_with_independent_authoritative_check_declares_no_aggregate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ACME_WIDGET_GH_TOKEN", "fake-token")
    registered = engine_config.load_yaml(_FIXTURES / "engine_config_forge_secondary.yaml")
    assert registered.forge is not None
    assert registered.forge.authoritative_checks == frozenset({"Widget CI"})
    target = forge_target_for_workspace(_SLUG, None)
    assert target.authoritative_checks == frozenset({"Widget CI"})
    assert target.aggregate_checks == frozenset()


def test_prefield_saved_engine_record_loads() -> None:
    record = {"repo": {"owner": _OWNER, "repo": _REPO}, "forge": _forge_section()}
    config = EngineConfig.model_validate(record)
    assert config.forge is not None
    assert config.forge.aggregate_checks == frozenset()
    assert ForgeTarget.from_forge_config(config.forge).aggregate_checks == frozenset()


def test_forge_section_with_unknown_field_validates() -> None:
    unknown = {**_forge_section(), "field_from_a_later_release": [GATE_CHECK]}
    forge = ForgeConfig.model_validate(unknown)
    assert forge.authoritative_checks == _DECLARED
    assert "field_from_a_later_release" not in forge.model_dump()
    config = EngineConfig.model_validate(
        {"repo": {"owner": _OWNER, "repo": _REPO}, "forge": unknown}
    )
    assert config.forge == forge


def test_engine_config_still_forbids_unknown_fields_and_forge_config_does_not() -> None:
    assert EngineConfig.model_config.get("extra") == "forbid"
    assert ForgeConfig.model_config.get("extra") != "forbid"
    assert "aggregate_checks" not in EngineConfig.model_fields
    with pytest.raises(ValidationError):
        EngineConfig.model_validate(
            {"repo": {"owner": _OWNER, "repo": _REPO}, "aggregate_checks": [GATE_CHECK]}
        )
