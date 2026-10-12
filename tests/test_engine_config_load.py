from __future__ import annotations

from pathlib import Path

import pytest

from alan.engine_config import (
    PROJECTION_EXCLUDE,
    CodeQualityConfig,
    DependencySweepConfig,
    EngineConfig,
    Posture,
    PostureCeilingError,
    PromptOverride,
    QuarantinedConfig,
    RepoIdentity,
    apply_stored_projection,
    file_ceiling,
    is_file_backed,
    lookup_by_repo,
    quarantined_configs,
    register,
    register_replacing,
    set_file_ceilings,
    set_posture_and_persist,
    set_quarantined_configs,
    unhide_and_persist,
)


@pytest.fixture
def engines_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "engines"
    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(root))
    return root


_REPO = RepoIdentity(owner="acme", repo="widget")


def test_file_ceiling_defaults_to_autonomous_when_unregistered() -> None:
    assert file_ceiling(_REPO) == Posture.autonomous


def test_is_file_backed_false_by_default() -> None:
    assert is_file_backed(_REPO) is False


def test_set_file_ceilings_makes_repo_file_backed() -> None:
    set_file_ceilings({_REPO: Posture.observe})
    assert is_file_backed(_REPO) is True
    assert file_ceiling(_REPO) == Posture.observe


def test_set_file_ceilings_replaces_prior_map() -> None:
    other = RepoIdentity(owner="acme", repo="gadget")
    set_file_ceilings({_REPO: Posture.observe})
    set_file_ceilings({other: Posture.propose})
    assert is_file_backed(_REPO) is False
    assert is_file_backed(other) is True


def test_set_posture_above_ceiling_raises(engines_root: Path) -> None:
    _ = engines_root
    cfg = EngineConfig(repo=_REPO, posture=Posture.observe)
    register(cfg)
    set_file_ceilings({_REPO: Posture.propose})
    with pytest.raises(PostureCeilingError, match="config/engines.d/widget.yaml"):
        set_posture_and_persist(_REPO, Posture.autonomous, source="api")


def test_set_posture_at_ceiling_succeeds(engines_root: Path) -> None:
    _ = engines_root
    cfg = EngineConfig(repo=_REPO, posture=Posture.observe)
    register(cfg)
    set_file_ceilings({_REPO: Posture.propose})
    updated = set_posture_and_persist(_REPO, Posture.propose, source="api")
    assert updated.posture == Posture.propose


def test_set_posture_below_ceiling_succeeds(engines_root: Path) -> None:
    _ = engines_root
    cfg = EngineConfig(repo=_REPO, posture=Posture.autonomous)
    register(cfg)
    set_file_ceilings({_REPO: Posture.self_merge})
    updated = set_posture_and_persist(_REPO, Posture.observe, source="api")
    assert updated.posture == Posture.observe


def test_set_posture_file_less_engine_ceiling_is_autonomous(engines_root: Path) -> None:
    _ = engines_root
    cfg = EngineConfig(repo=_REPO, posture=Posture.observe)
    register(cfg)
    updated = set_posture_and_persist(_REPO, Posture.autonomous, source="api")
    assert updated.posture == Posture.autonomous


def test_set_posture_noop_shortcut_precedes_ceiling_check(engines_root: Path) -> None:
    _ = engines_root
    cfg = EngineConfig(repo=_REPO, posture=Posture.autonomous)
    register(cfg)
    set_file_ceilings({_REPO: Posture.observe})
    updated = set_posture_and_persist(_REPO, Posture.autonomous, source="api")
    assert updated.posture == Posture.autonomous


def test_set_posture_above_ceiling_still_raises_when_already_above_ceiling(
    engines_root: Path,
) -> None:
    _ = engines_root
    cfg = EngineConfig(repo=_REPO, posture=Posture.self_merge)
    register(cfg)
    set_file_ceilings({_REPO: Posture.propose})
    with pytest.raises(PostureCeilingError):
        set_posture_and_persist(_REPO, Posture.autonomous, source="api")
    live = lookup_by_repo(_REPO)
    assert live is not None
    assert live.posture == Posture.self_merge


def test_set_file_ceilings_default_fallback_is_autonomous(engines_root: Path) -> None:
    _ = engines_root
    cfg = EngineConfig(repo=_REPO, posture=Posture.observe)
    register(cfg)
    set_file_ceilings({})
    updated = set_posture_and_persist(_REPO, Posture.autonomous, source="api")
    assert updated.posture == Posture.autonomous


def test_set_file_ceilings_custom_fallback_applies_to_unlisted_repo(
    engines_root: Path,
) -> None:
    _ = engines_root
    cfg = EngineConfig(repo=_REPO, posture=Posture.observe)
    register(cfg)
    set_file_ceilings({}, fallback=Posture.observe)
    assert file_ceiling(_REPO) == Posture.observe
    with pytest.raises(PostureCeilingError):
        set_posture_and_persist(_REPO, Posture.propose, source="api")


def test_quarantined_configs_empty_by_default() -> None:
    assert quarantined_configs() == []


def test_set_quarantined_configs_round_trips() -> None:
    record = QuarantinedConfig(path=Path("config/engines.d/widget.yaml"), error="boom", repo=_REPO)
    set_quarantined_configs([record])
    assert quarantined_configs() == [record]


def test_set_quarantined_configs_replaces_prior_list() -> None:
    first = QuarantinedConfig(path=Path("a.yaml"), error="boom", repo=None)
    second = QuarantinedConfig(path=Path("b.yaml"), error="bang", repo=_REPO)
    set_quarantined_configs([first])
    set_quarantined_configs([second])
    assert quarantined_configs() == [second]


def test_register_replacing_overwrites_existing_registration() -> None:
    register(EngineConfig(repo=_REPO, posture=Posture.observe))
    replacement = EngineConfig(repo=_REPO, posture=Posture.propose)
    register_replacing(replacement)
    assert lookup_by_repo(_REPO) == replacement


def test_register_replacing_registers_new_repo() -> None:
    cfg = EngineConfig(repo=_REPO, posture=Posture.propose)
    register_replacing(cfg)
    assert lookup_by_repo(_REPO) == cfg


def test_register_replacing_skips_env_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SOME_UNSET_TOKEN_VAR", raising=False)
    cfg = EngineConfig(
        repo=_REPO,
        code_quality=CodeQualityConfig(
            project_key="p", organization="o", host_url="https://example.com",
            token_env_var="SOME_UNSET_TOKEN_VAR",
        ),
    )
    register_replacing(cfg)
    assert lookup_by_repo(_REPO) == cfg


def test_apply_stored_projection_none_returns_file_config_unchanged() -> None:
    file_cfg = EngineConfig(repo=_REPO, posture=Posture.autonomous)
    assert apply_stored_projection(file_cfg, None) is file_cfg


def test_apply_stored_projection_posture_takes_lower_of_the_two() -> None:
    file_cfg = EngineConfig(repo=_REPO, posture=Posture.autonomous)
    stored = EngineConfig(repo=_REPO, posture=Posture.observe)
    merged = apply_stored_projection(file_cfg, stored)
    assert merged.posture == Posture.observe


def test_apply_stored_projection_never_raises_file_ceiling_above_stored() -> None:
    file_cfg = EngineConfig(repo=_REPO, posture=Posture.observe)
    stored = EngineConfig(repo=_REPO, posture=Posture.autonomous)
    merged = apply_stored_projection(file_cfg, stored)
    assert merged.posture == Posture.observe


def test_apply_stored_projection_only_overlays_dependency_sweep_enabled() -> None:
    file_cfg = EngineConfig(
        repo=_REPO,
        dependency_sweep=DependencySweepConfig(
            enabled=False, cadence_seconds=123, enable_python_vuln_scan=True
        ),
    )
    stored = EngineConfig(
        repo=_REPO,
        dependency_sweep=DependencySweepConfig(
            enabled=True, cadence_seconds=999, enable_python_vuln_scan=False
        ),
    )
    merged = apply_stored_projection(file_cfg, stored)
    assert merged.dependency_sweep.enabled is True
    assert merged.dependency_sweep.cadence_seconds == 123
    assert merged.dependency_sweep.enable_python_vuln_scan is True


def test_apply_stored_projection_overlays_hidden_state() -> None:
    file_cfg = EngineConfig(repo=_REPO, hidden=False, posture=Posture.autonomous)
    stored = EngineConfig(
        repo=_REPO, hidden=True, hidden_restore_posture=Posture.self_merge
    )
    merged = apply_stored_projection(file_cfg, stored)
    assert merged.hidden is True
    assert merged.hidden_restore_posture == Posture.self_merge


def test_apply_stored_projection_clamps_hidden_restore_posture_to_file_ceiling() -> None:
    file_cfg = EngineConfig(repo=_REPO, posture=Posture.propose)
    stored = EngineConfig(
        repo=_REPO, hidden=True, hidden_restore_posture=Posture.autonomous
    )
    merged = apply_stored_projection(file_cfg, stored)
    assert merged.hidden_restore_posture == Posture.propose


def test_apply_stored_projection_overlays_prompt_overrides() -> None:
    override = PromptOverride(
        text="x {{ENVIRONMENT}} {{SUCCESS}} {{REPO_GUIDANCE}}", base_fingerprint="ab"
    )
    file_cfg = EngineConfig(repo=_REPO)
    stored = EngineConfig(
        repo=_REPO, prompt_overrides={"implementation/implementer": override}
    )
    merged = apply_stored_projection(file_cfg, stored)
    assert merged.prompt_overrides == {"implementation/implementer": override}


def test_apply_stored_projection_leaves_structural_fields_from_file() -> None:
    file_cfg = EngineConfig(repo=_REPO, base_tools=["Read"])
    stored = EngineConfig(repo=_REPO, base_tools=["Read", "Write", "Edit"])
    merged = apply_stored_projection(file_cfg, stored)
    assert merged.base_tools == ["Read"]


def test_hide_then_lower_ceiling_then_boot_then_unhide_clamps_restore_posture(
    engines_root: Path,
) -> None:
    _ = engines_root
    stored = EngineConfig(
        repo=_REPO,
        posture=Posture.observe,
        hidden=True,
        hidden_restore_posture=Posture.autonomous,
    )
    register(stored)

    file_cfg = EngineConfig(repo=_REPO, posture=Posture.propose)
    effective = apply_stored_projection(file_cfg, stored)
    register_replacing(effective)
    set_file_ceilings({_REPO: Posture.propose})

    restored = unhide_and_persist(_REPO)

    assert restored.hidden is False
    assert restored.posture == Posture.propose


def test_projection_exclude_covers_exactly_the_narrow_projection() -> None:
    assert set(PROJECTION_EXCLUDE) == {
        "posture",
        "dependency_sweep",
        "hidden",
        "hidden_restore_posture",
        "prompt_overrides",
        "accent",
    }
    assert PROJECTION_EXCLUDE["dependency_sweep"] == {"enabled"}
