from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from alan.default_profile import DefaultProfile, DefaultProfileError, Resolved, Source, resolve
from alan.engine_config import EngineConfig, ForgeConfig, ForgeTarget, Posture, RepoIdentity

_REPO_ROOT = Path(__file__).resolve().parent.parent
_REPO = RepoIdentity(owner="acme", repo="widget")
_CREDENTIAL_ENV = "ACME_GH_TOKEN"  # noqa: S105

_INVALID_APP_IDS: tuple[object, ...] = (-1, 0, True, "5", 5.0)


@pytest.mark.parametrize("app_id", _INVALID_APP_IDS)
def test_forge_config_rejects_invalid_check_app_id(app_id: object) -> None:
    with pytest.raises(ValidationError):
        ForgeConfig(
            repo=_REPO,
            credential_env_name=_CREDENTIAL_ENV,
            authoritative_check_apps={"gate": app_id},  # type: ignore[dict-item]
        )


@pytest.mark.parametrize("app_id", _INVALID_APP_IDS)
def test_forge_target_rejects_invalid_check_app_id(app_id: object) -> None:
    with pytest.raises(ValidationError):
        ForgeTarget(
            repo=_REPO,
            credential_env_name=_CREDENTIAL_ENV,
            authoritative_check_apps={"gate": app_id},  # type: ignore[dict-item]
        )


@pytest.mark.parametrize("app_id", _INVALID_APP_IDS)
def test_default_profile_rejects_invalid_check_app_id(app_id: object) -> None:
    with pytest.raises(ValidationError):
        DefaultProfile(
            owner="acme", repo="widget", posture=Posture.observe,
            authoritative_check_apps={"gate": app_id},  # type: ignore[dict-item]
        )


def test_forge_config_accepts_a_positive_check_app_id() -> None:
    cfg = ForgeConfig(
        repo=_REPO, credential_env_name=_CREDENTIAL_ENV,
        authoritative_check_apps={"gate": 15368},
    )
    assert cfg.authoritative_check_apps == {"gate": 15368}


def test_forge_config_defaults_are_empty_and_off() -> None:
    cfg = ForgeConfig(repo=_REPO, credential_env_name=_CREDENTIAL_ENV)
    assert cfg.authoritative_check_apps == {}
    assert cfg.enforce_admins is False


def test_forge_target_defaults_are_empty_and_off() -> None:
    target = ForgeTarget(repo=_REPO, credential_env_name=_CREDENTIAL_ENV)
    assert target.authoritative_check_apps == {}
    assert target.enforce_admins is False


def test_default_profile_defaults_are_empty_and_off() -> None:
    profile = DefaultProfile(owner="acme", repo="widget", posture=Posture.observe)
    assert profile.authoritative_check_apps == {}
    assert profile.enforce_admins is False


def test_from_forge_config_copies_pins_and_admin_switch() -> None:
    forge_cfg = ForgeConfig(
        repo=_REPO, credential_env_name=_CREDENTIAL_ENV,
        authoritative_checks=frozenset({"gate"}),
        authoritative_check_apps={"gate": 15368}, enforce_admins=True,
    )
    target = ForgeTarget.from_forge_config(forge_cfg)
    assert target.authoritative_check_apps == {"gate": 15368}
    assert target.enforce_admins is True


def test_non_authoritative_pin_is_kept_on_the_model_as_written() -> None:
    cfg = ForgeConfig(
        repo=_REPO, credential_env_name=_CREDENTIAL_ENV,
        authoritative_checks=frozenset(),
        authoritative_check_apps={"unrelated-check": 15368},
    )
    assert cfg.authoritative_check_apps == {"unrelated-check": 15368}


def test_engine_config_dict_without_new_keys_loads_unchanged() -> None:
    cfg = EngineConfig.model_validate({
        "repo": {"owner": "acme", "repo": "widget"},
        "forge": {
            "repo": {"owner": "acme", "repo": "widget"},
            "credential_env_name": _CREDENTIAL_ENV,
        },
    })
    assert cfg.forge is not None
    assert cfg.forge.authoritative_check_apps == {}
    assert cfg.forge.enforce_admins is False


def test_engines_d_style_yaml_without_new_keys_loads_unchanged() -> None:
    path = _REPO_ROOT / "tests" / "fixtures" / "engine_config_forge_secondary.yaml"
    cfg = EngineConfig.model_validate(yaml.safe_load(path.read_text()))
    assert cfg.forge is not None
    assert cfg.forge.authoritative_check_apps == {}
    assert cfg.forge.enforce_admins is False


def test_profile_yaml_without_new_keys_loads_unchanged(tmp_path: Path) -> None:
    state = tmp_path / "engines"
    state.mkdir()
    owner_dir = tmp_path / "owner"
    owner_dir.mkdir()
    owner_file = owner_dir / "default_profile.yaml"
    owner_file.write_text("owner: acme\nrepo: widget\nposture: observe\n")
    resolved = resolve(state, owner_file)
    assert resolved.profile.authoritative_check_apps == {}
    assert resolved.profile.enforce_admins is False


def test_forge_fields_includes_pins_and_admin_switch() -> None:
    profile = DefaultProfile(
        owner="acme", repo="widget", posture=Posture.observe,
        authoritative_checks=frozenset({"gate"}),
        authoritative_check_apps={"gate": 15368}, enforce_admins=True,
    )
    resolved = Resolved(profile, Source.owner, None)
    fields = resolved.forge_fields()
    assert fields["authoritative_check_apps"] == {"gate": 15368}
    assert fields["enforce_admins"] is True
    forge_cfg = ForgeConfig(**fields)
    assert forge_cfg.authoritative_check_apps == {"gate": 15368}
    assert forge_cfg.enforce_admins is True


def test_engine_fields_forge_validates_with_pins() -> None:
    profile = DefaultProfile(
        owner="acme", repo="widget", posture=Posture.autonomous,
        authoritative_checks=frozenset({"gate"}),
        authoritative_check_apps={"gate": 15368}, enforce_admins=True,
    )
    resolved = Resolved(profile, Source.owner, None)
    cfg = EngineConfig(**resolved.engine_fields())
    assert cfg.forge is not None
    assert cfg.forge.authoritative_check_apps == {"gate": 15368}
    assert cfg.forge.enforce_admins is True


def test_invalid_pin_in_profile_yaml_surfaces_as_default_profile_error(tmp_path: Path) -> None:
    state = tmp_path / "engines"
    state.mkdir()
    owner_dir = tmp_path / "owner"
    owner_dir.mkdir()
    owner_file = owner_dir / "default_profile.yaml"
    owner_file.write_text(
        "owner: acme\nrepo: widget\nposture: observe\n"
        "authoritative_check_apps:\n  gate: -1\n"
    )
    with pytest.raises(DefaultProfileError):
        resolve(state, owner_file)


def test_engine_config_with_pins_round_trips_through_json_dump_and_validate() -> None:
    cfg = EngineConfig(
        repo=_REPO,
        forge=ForgeConfig(
            repo=_REPO, credential_env_name=_CREDENTIAL_ENV,
            authoritative_checks=frozenset({"gate"}),
            authoritative_check_apps={"gate": 15368}, enforce_admins=True,
        ),
    )
    dumped = cfg.model_dump(mode="json")
    reloaded = EngineConfig.model_validate(dumped)
    assert reloaded.forge is not None
    assert reloaded.forge.authoritative_check_apps == {"gate": 15368}
    assert reloaded.forge.enforce_admins is True
