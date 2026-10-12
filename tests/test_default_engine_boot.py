from __future__ import annotations

import logging
from pathlib import Path

import pytest
from pydantic import ValidationError

from alan import default_engine_boot, default_profile, engine_config, engine_registry_store
from alan.default_profile import ProfileRequirement
from alan.engine_config import (
    CiConfig,
    EngineConfig,
    Posture,
    PostureCeilingError,
    PromptOverride,
    RepoIdentity,
)
from alan.engine_registry_store import _default_origin_path
from alan.repo_accent import AccentChoice

_ACCENT = AccentChoice(color="iris", intensity="standard")
_OVERRIDES = {"implementation/implementer": PromptOverride(text="custom", base_fingerprint="abc")}
_OLD_PROFILE = (
    "authoritative_checks: [Old CI]\n"
    "code_quality:\n  organization: old-org\n  project_key: old_key\n"
    "main_ci_workflow: old.yml\ndeploy_workflow: old-deploy.yml\n"
    "requirements: [reviewer_credential]\n"
)
_NEW_PROFILE = (
    "authoritative_checks: [New CI]\n"
    "code_quality:\n  organization: new-org\n  project_key: new_key\n"
    "main_ci_workflow: new.yml\ndeploy_workflow: new-deploy.yml\n"
    "requirements: [code_quality]\n"
)


def _no_file_backed() -> None:
    return None


@pytest.fixture(autouse=True)
def engines_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "engines"
    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(root))
    return root


def _profile(
    tmp_path: Path,
    repo: str,
    *,
    posture: str = "autonomous",
    extra: str = "",
    tag: str = "",
) -> default_profile.Resolved:
    state = tmp_path / f"profile-{repo}{tag}"
    state.mkdir()
    (state / "default_profile.yaml").write_text(
        f"owner: acme\nrepo: {repo}\nposture: {posture}\n{extra}"
    )
    return default_profile.resolve(state, tmp_path / "no-owner.yaml")


def _use(monkeypatch: pytest.MonkeyPatch, resolved: default_profile.Resolved) -> None:
    monkeypatch.setattr(default_profile, "resolved", lambda: resolved)


def _restart(monkeypatch: pytest.MonkeyPatch, resolved: default_profile.Resolved) -> None:
    _use(monkeypatch, resolved)
    monkeypatch.setattr(engine_config, "_registry", {})
    monkeypatch.setattr(engine_config, "_default_key", None)


def _save_default(
    monkeypatch: pytest.MonkeyPatch, resolved: default_profile.Resolved, **update: object
) -> EngineConfig:
    _use(monkeypatch, resolved)
    saved = engine_config.default_engine_config().model_copy(update=update)
    engine_registry_store.write_engine(saved.repo.slug, saved.model_dump(mode="json"))
    return saved


def _boot(monkeypatch: pytest.MonkeyPatch, resolved: default_profile.Resolved) -> EngineConfig:
    _restart(monkeypatch, resolved)
    default_engine_boot.load_engines(_no_file_backed)
    booted = engine_config.get_registered()
    assert booted is not None
    return booted


def _bare(repo: str) -> dict[str, object]:
    return {"repo": {"owner": "acme", "repo": repo}}


def _mark(slug: str, when: str) -> None:
    _default_origin_path(slug).write_text(when)


def _repo(name: str) -> RepoIdentity:
    return RepoIdentity(owner="acme", repo=name)


def _snapshot(root: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in root.iterdir()}


@pytest.fixture
def repointed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> EngineConfig:
    alpha = _profile(tmp_path, "alpha")
    beta = _profile(tmp_path, "beta")
    _use(monkeypatch, alpha)
    default = engine_config.default_engine_config()
    _save_default(
        monkeypatch,
        alpha,
        accent=_ACCENT,
        hidden=True,
        hidden_restore_posture=Posture.propose,
        posture=Posture.propose,
        prompt_overrides=_OVERRIDES,
        dependency_sweep=default.dependency_sweep.model_copy(update={"enabled": True}),
    )
    return _boot(monkeypatch, beta)


def test_a_repoint_registers_the_new_repository_as_the_default_engine(
    repointed: EngineConfig,
) -> None:
    assert repointed.repo == _repo("beta")
    assert engine_config.get_registered() == repointed


def test_a_repoint_carries_the_default_engines_accent_over(repointed: EngineConfig) -> None:
    assert repointed.accent == _ACCENT


def test_a_repoint_carries_the_default_engines_hidden_state_over(
    repointed: EngineConfig,
) -> None:
    assert repointed.hidden is True
    assert repointed.hidden_restore_posture == Posture.propose


def test_a_repoint_carries_the_default_engines_prompt_overrides_over(
    repointed: EngineConfig,
) -> None:
    assert repointed.prompt_overrides == _OVERRIDES


def test_a_repoint_carries_the_default_engines_dependency_sweep_toggle_over(
    repointed: EngineConfig,
) -> None:
    assert repointed.dependency_sweep.enabled is True


def test_a_repoint_carries_the_default_engines_dialled_posture_over(
    repointed: EngineConfig,
) -> None:
    assert repointed.posture == Posture.propose


def test_the_previous_defaults_leftover_record_never_becomes_a_connected_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    alpha = _profile(tmp_path, "alpha")
    _save_default(monkeypatch, alpha, accent=_ACCENT)
    _boot(monkeypatch, _profile(tmp_path, "beta"))
    assert [cfg.repo for cfg in engine_config.registered_engines()] == [_repo("beta")]
    assert engine_config.lookup_by_repo(_repo("alpha")) is None


def test_the_leftover_record_stays_on_disk_and_serves_a_repoint_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    alpha = _profile(tmp_path, "alpha")
    _save_default(monkeypatch, alpha, accent=_ACCENT)
    _boot(monkeypatch, _profile(tmp_path, "beta"))
    assert engine_registry_store.read_engine("acme/alpha") is not None
    back = _boot(monkeypatch, alpha)
    assert back.repo == _repo("alpha")
    assert back.accent == _ACCENT


def test_a_saved_record_missing_newer_fields_still_loads_as_the_defaults_own_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine_registry_store.write_connected("acme/alpha", {**_bare("alpha"), "posture": "propose"})
    booted = _boot(monkeypatch, _profile(tmp_path, "alpha"))
    assert booted.posture == Posture.propose
    assert booted.accent is None
    assert booted.unblock_enabled is True
    assert booted.prompt_overrides == {}


def test_a_leftover_record_missing_newer_fields_still_loads_after_a_repoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use(monkeypatch, _profile(tmp_path, "alpha"))
    engine_registry_store.write_engine("acme/alpha", _bare("alpha"))
    booted = _boot(monkeypatch, _profile(tmp_path, "beta"))
    assert booted.repo == _repo("beta")
    assert booted.posture == Posture.observe


def test_a_repoint_with_saved_records_raises_no_duplicate_engine_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    alpha = _profile(tmp_path, "alpha")
    beta = _profile(tmp_path, "beta")
    _save_default(monkeypatch, alpha, accent=_ACCENT)
    _save_default(monkeypatch, beta, posture=Posture.propose)
    _restart(monkeypatch, beta)
    engine_config.register(engine_config.default_engine_config(), default=True, validate_env=False)
    default_engine_boot.load_engines(_no_file_backed)
    booted = engine_config.get_registered()
    assert booted is not None
    assert booted.posture == Posture.propose
    assert booted.accent is None


def test_the_profile_wins_over_a_record_saved_by_an_accent_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = _profile(tmp_path, "alpha", extra=_OLD_PROFILE, tag="-old")
    new = _profile(tmp_path, "alpha", extra=_NEW_PROFILE, tag="-new")
    _restart(monkeypatch, old)
    default = engine_config.default_engine_config()
    engine_config.register(default, default=True, validate_env=False)
    engine_config.set_accent_and_persist(default.repo, _ACCENT)
    booted = _boot(monkeypatch, new)
    assert booted.accent == _ACCENT
    assert booted.forge is not None
    assert booted.forge.authoritative_checks == frozenset({"New CI"})
    assert booted.code_quality is not None
    assert (booted.code_quality.organization, booted.code_quality.project_key) == (
        "new-org",
        "new_key",
    )
    assert booted.ci == CiConfig(main_ci_workflow="new.yml", deploy_workflow="new-deploy.yml")


def test_the_profile_requirements_stay_the_installs_and_never_enter_an_engine_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = _profile(tmp_path, "alpha", extra=_OLD_PROFILE, tag="-old")
    new = _profile(tmp_path, "alpha", extra=_NEW_PROFILE, tag="-new")
    _save_default(monkeypatch, old, accent=_ACCENT)
    booted = _boot(monkeypatch, new)
    assert default_profile.resolved().profile.requirements == [ProfileRequirement.code_quality]
    assert "requirements" not in booted.model_dump()
    assert "requirements" not in (engine_registry_store.read_engine("acme/alpha") or {})


def test_the_profiles_identity_and_forge_win_over_a_leftover_saved_under_the_old_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    alpha = _profile(tmp_path, "alpha", extra=_OLD_PROFILE)
    _save_default(monkeypatch, alpha, accent=_ACCENT)
    booted = _boot(monkeypatch, _profile(tmp_path, "beta", extra=_NEW_PROFILE))
    assert booted.repo == _repo("beta")
    assert booted.forge is not None
    assert booted.forge.repo == _repo("beta")
    assert booted.forge.authoritative_checks == frozenset({"New CI"})
    assert booted.accent == _ACCENT


def test_a_posture_dialled_down_stays_down_across_a_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    alpha = _profile(tmp_path, "alpha")
    _save_default(monkeypatch, alpha, posture=Posture.propose)
    assert _boot(monkeypatch, alpha).posture == Posture.propose
    assert _boot(monkeypatch, alpha).posture == Posture.propose


def test_nothing_rises_above_the_profiles_posture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _save_default(
        monkeypatch,
        _profile(tmp_path, "alpha"),
        posture=Posture.autonomous,
        hidden=True,
        hidden_restore_posture=Posture.autonomous,
    )
    capped = _profile(tmp_path, "alpha", posture="propose", tag="-capped")
    booted = _boot(monkeypatch, capped)
    assert booted.posture == Posture.propose
    assert booted.hidden_restore_posture == Posture.propose


def test_a_repoint_to_a_lower_posture_profile_caps_the_carried_posture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _save_default(monkeypatch, _profile(tmp_path, "alpha"), posture=Posture.self_merge)
    booted = _boot(monkeypatch, _profile(tmp_path, "beta", posture="observe"))
    assert booted.posture == Posture.observe


def test_a_legacy_unmarked_record_for_the_default_repository_supplies_its_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    alpha = _profile(tmp_path, "alpha")
    _use(monkeypatch, alpha)
    legacy = engine_config.default_engine_config().model_copy(update={"accent": _ACCENT})
    engine_registry_store.write_connected("acme/alpha", legacy.model_dump(mode="json"))
    assert _boot(monkeypatch, alpha).accent == _ACCENT


def test_an_unmarked_record_for_another_repository_stays_a_connected_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine_registry_store.write_connected("acme/gadget", {**_bare("gadget"), "accent": None})
    _restart(monkeypatch, _profile(tmp_path, "alpha"))
    engine_config.register(engine_config.default_engine_config(), default=True, validate_env=False)
    default_engine_boot.load_engines(_no_file_backed)
    assert engine_config.lookup_by_repo(_repo("gadget")) is not None
    default = engine_config.get_registered()
    assert default is not None
    assert default.repo == _repo("alpha")


def test_an_unmarked_record_of_a_former_default_stays_connected_and_supplies_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    alpha = _profile(tmp_path, "alpha")
    _use(monkeypatch, alpha)
    legacy = engine_config.default_engine_config().model_copy(update={"accent": _ACCENT})
    engine_registry_store.write_connected("acme/alpha", legacy.model_dump(mode="json"))
    beta = _profile(tmp_path, "beta")
    _restart(monkeypatch, beta)
    engine_config.register(engine_config.default_engine_config(), default=True, validate_env=False)
    default_engine_boot.load_engines(_no_file_backed)
    kept = engine_config.lookup_by_repo(_repo("alpha"))
    assert kept is not None
    assert kept.accent == _ACCENT
    default = engine_config.get_registered()
    assert default is not None
    assert default.repo == _repo("beta")
    assert default.accent is None


def test_boot_writes_no_markers_and_changes_no_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, engines_root: Path
) -> None:
    alpha = _profile(tmp_path, "alpha")
    _save_default(monkeypatch, alpha, accent=_ACCENT)
    engine_registry_store.write_connected("acme/legacy", _bare("legacy"))
    before = _snapshot(engines_root)
    _boot(monkeypatch, _profile(tmp_path, "beta"))
    _boot(monkeypatch, alpha)
    assert _snapshot(engines_root) == before
    assert len(list(engines_root.glob("*.default-origin"))) == 1


def test_an_unreadable_marker_fails_boot_instead_of_reviving_the_former_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _save_default(monkeypatch, _profile(tmp_path, "alpha"), accent=_ACCENT)
    marker = _default_origin_path("acme/alpha")
    marker.unlink()
    marker.mkdir()
    with pytest.raises(OSError, match="directory"):
        _boot(monkeypatch, _profile(tmp_path, "beta"))
    assert engine_config.lookup_by_repo(_repo("alpha")) is None


def test_the_newest_marked_leftover_supplies_the_default_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for repo, posture, when in (
        ("alpha", Posture.observe, "2026-01-01T00:00:00.000000+00:00"),
        ("gamma", Posture.self_merge, "2026-03-01T00:00:00.000000+00:00"),
        ("delta", Posture.propose, "2026-02-01T00:00:00.000000+00:00"),
    ):
        _save_default(monkeypatch, _profile(tmp_path, repo), posture=posture)
        _mark(f"acme/{repo}", when)
    booted = _boot(monkeypatch, _profile(tmp_path, "beta"))
    assert booted.posture == Posture.self_merge


def test_marked_leftovers_with_the_same_time_are_ordered_by_slug(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for repo, posture in (("delta", Posture.propose), ("gamma", Posture.self_merge)):
        _save_default(monkeypatch, _profile(tmp_path, repo), posture=posture)
        _mark(f"acme/{repo}", "2026-03-01T00:00:00.000000+00:00")
    booted = _boot(monkeypatch, _profile(tmp_path, "beta"))
    assert booted.posture == Posture.self_merge


def test_the_defaults_own_record_wins_over_a_newer_marked_leftover(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _save_default(monkeypatch, _profile(tmp_path, "gamma"), posture=Posture.self_merge)
    alpha = _profile(tmp_path, "alpha")
    _save_default(monkeypatch, alpha, posture=Posture.propose)
    _mark("acme/alpha", "2026-01-01T00:00:00.000000+00:00")
    _mark("acme/gamma", "2026-03-01T00:00:00.000000+00:00")
    assert _boot(monkeypatch, alpha).posture == Posture.propose


def test_an_invalid_saved_record_still_fails_boot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine_registry_store.write_connected("acme/broken", {**_bare("broken"), "nonsense": 1})
    _restart(monkeypatch, _profile(tmp_path, "alpha"))
    with pytest.raises(ValidationError):
        default_engine_boot.load_engines(_no_file_backed)


def test_an_invalid_marked_leftover_still_fails_boot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use(monkeypatch, _profile(tmp_path, "alpha"))
    engine_registry_store.write_engine("acme/alpha", {**_bare("alpha"), "nonsense": 1})
    _restart(monkeypatch, _profile(tmp_path, "beta"))
    with pytest.raises(ValidationError):
        default_engine_boot.load_engines(_no_file_backed)


def test_a_rollback_to_the_previous_release_boots_cleanly_with_markers_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, engines_root: Path
) -> None:
    _save_default(monkeypatch, _profile(tmp_path, "alpha"), accent=_ACCENT)
    engine_registry_store.write_connected("acme/gadget", _bare("gadget"))
    assert len(list(engines_root.glob("*.default-origin"))) == 1
    monkeypatch.setattr(engine_config, "_registry", {})
    loaded = engine_config.load_persisted_engines()
    assert {cfg.repo.repo for cfg in loaded} == {"alpha", "gadget"}
    assert len(engine_registry_store.list_engines()) == 2


def test_the_default_ceiling_is_the_profiles_posture_for_a_default_that_is_not_file_backed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    alpha = _profile(tmp_path, "alpha", posture="propose")
    _save_default(monkeypatch, alpha, posture=Posture.observe)
    engine_config.set_default_ceiling({})
    booted = _boot(monkeypatch, alpha)
    assert booted.posture == Posture.observe
    assert engine_config.file_ceiling(booted.repo) == Posture.propose
    assert engine_config.ceiling_source(booted.repo) == "default"


def test_the_published_ceiling_refuses_a_raise_and_names_the_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    alpha = _profile(tmp_path, "alpha", posture="propose")
    _save_default(monkeypatch, alpha, posture=Posture.observe)
    booted = _boot(monkeypatch, alpha)
    with pytest.raises(PostureCeilingError, match="engines/default_profile.yaml"):
        engine_config.set_posture_and_persist(booted.repo, Posture.autonomous, source="api")
    restored = engine_config.set_posture_and_persist(booted.repo, Posture.propose, source="api")
    assert restored.posture == Posture.propose


def test_a_file_backed_default_keeps_its_file_ceiling_and_publishes_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    alpha = _profile(tmp_path, "alpha")
    _use(monkeypatch, alpha)
    default = engine_config.default_engine_config()
    engine_config.set_default_ceiling({})
    engine_config.set_file_ceilings({default.repo: Posture.propose})
    default_engine_boot.publish_default_ceiling()
    assert engine_config.ceiling_source(default.repo) == "file"
    engine_config.set_file_ceilings({})
    assert engine_config.ceiling_source(default.repo) == "fallback"


def test_boot_runs_the_merge_then_the_file_backed_pass_then_publishes_the_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    monkeypatch.setattr(default_engine_boot, "merge_saved_engines", lambda: order.append("merge"))
    monkeypatch.setattr(
        default_engine_boot, "publish_default_ceiling", lambda: order.append("publish")
    )
    default_engine_boot.load_engines(lambda: order.append("file_backed"))
    assert order == ["merge", "file_backed", "publish"]


def test_boot_logs_the_connected_engines_it_loaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    engine_registry_store.write_connected("acme/gadget", _bare("gadget"))
    _restart(monkeypatch, _profile(tmp_path, "alpha"))
    with caplog.at_level(logging.INFO, logger="alan.default_engine_boot"):
        default_engine_boot.load_engines(_no_file_backed)
    assert any("acme/gadget" in record.getMessage() for record in caplog.records)
