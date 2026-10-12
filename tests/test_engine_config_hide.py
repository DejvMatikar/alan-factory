from __future__ import annotations

import json
from pathlib import Path

import pytest

from alan import engine_registry_store
from alan.engine_config import (
    EngineConfig,
    EngineNotFoundError,
    Posture,
    RepoIdentity,
    hide_and_persist,
    load_persisted_engines,
    lookup_by_repo,
    register_and_persist,
    unhide_and_persist,
)

_AUDIT_FILENAME = "posture_audit.jsonl"


def _audit_path() -> Path:
    return engine_registry_store.engines_root() / _AUDIT_FILENAME


@pytest.fixture(autouse=True)
def _engines_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "engines"
    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(root))
    return root


def _engine(posture: Posture = Posture.self_merge) -> EngineConfig:
    return EngineConfig(repo=RepoIdentity(owner="acme", repo="widgets"), posture=posture)


def test_engine_config_hidden_defaults_false() -> None:
    cfg = EngineConfig(repo=RepoIdentity(owner="a", repo="b"))
    assert cfg.hidden is False
    assert cfg.hidden_restore_posture is None


def test_hide_and_persist_floors_posture_and_remembers_prior() -> None:
    cfg = _engine(Posture.self_merge)
    register_and_persist(cfg, validate_env=False)

    hidden = hide_and_persist(cfg.repo)

    assert hidden.hidden is True
    assert hidden.posture is Posture.observe
    assert hidden.hidden_restore_posture is Posture.self_merge
    assert lookup_by_repo(cfg.repo) == hidden


def test_hide_and_persist_persists_across_reload() -> None:
    from alan.engine_config import _registry as _reg  # pyright: ignore[reportPrivateUsage]

    cfg = _engine(Posture.autonomous)
    register_and_persist(cfg, validate_env=False)
    hide_and_persist(cfg.repo)

    _reg.clear()
    loaded = load_persisted_engines()
    reloaded = next(c for c in loaded if c.repo == cfg.repo)

    assert reloaded.hidden is True
    assert reloaded.posture is Posture.observe
    assert reloaded.hidden_restore_posture is Posture.autonomous


def test_hide_and_persist_is_idempotent() -> None:
    cfg = _engine(Posture.propose)
    register_and_persist(cfg, validate_env=False)
    hide_and_persist(cfg.repo)

    again = hide_and_persist(cfg.repo)

    assert again.hidden is True
    assert again.hidden_restore_posture is Posture.propose


def test_hide_and_persist_unknown_repo_raises() -> None:
    with pytest.raises(EngineNotFoundError):
        hide_and_persist(RepoIdentity(owner="no", repo="such"))


def test_hide_and_persist_writes_audit_entry() -> None:
    cfg = _engine(Posture.self_merge)
    register_and_persist(cfg, validate_env=False)

    hide_and_persist(cfg.repo)

    lines = _audit_path().read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["repo"] == cfg.repo.slug
    assert entry["from"] == "self_merge"
    assert entry["to"] == "observe"
    assert entry["source"] == "hide"


def test_hide_and_persist_no_audit_when_already_at_observe() -> None:
    cfg = _engine(Posture.observe)
    register_and_persist(cfg, validate_env=False)

    hide_and_persist(cfg.repo)

    assert not _audit_path().exists()


def test_hide_and_persist_second_call_writes_no_further_audit() -> None:
    cfg = _engine(Posture.propose)
    register_and_persist(cfg, validate_env=False)
    hide_and_persist(cfg.repo)

    hide_and_persist(cfg.repo)

    lines = _audit_path().read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1


def test_unhide_and_persist_restores_exact_posture_and_clears_remembered() -> None:
    cfg = _engine(Posture.self_merge)
    register_and_persist(cfg, validate_env=False)
    hide_and_persist(cfg.repo)

    restored = unhide_and_persist(cfg.repo)

    assert restored.hidden is False
    assert restored.posture is Posture.self_merge
    assert restored.hidden_restore_posture is None
    assert lookup_by_repo(cfg.repo) == restored


def test_unhide_and_persist_falls_back_to_observe_when_remembered_is_none() -> None:
    cfg = _engine(Posture.propose).model_copy(
        update={"hidden": True, "hidden_restore_posture": None}
    )
    register_and_persist(cfg, validate_env=False)

    restored = unhide_and_persist(cfg.repo)

    assert restored.hidden is False
    assert restored.posture is Posture.observe
    assert restored.hidden_restore_posture is None


def test_unhide_and_persist_is_idempotent_when_not_hidden() -> None:
    cfg = _engine(Posture.self_merge)
    register_and_persist(cfg, validate_env=False)

    result = unhide_and_persist(cfg.repo)

    assert result.hidden is False
    assert result == lookup_by_repo(cfg.repo)


def test_unhide_and_persist_unknown_repo_raises() -> None:
    with pytest.raises(EngineNotFoundError):
        unhide_and_persist(RepoIdentity(owner="no", repo="such"))


def test_unhide_and_persist_writes_audit_entry() -> None:
    cfg = _engine(Posture.self_merge)
    register_and_persist(cfg, validate_env=False)
    hide_and_persist(cfg.repo)

    unhide_and_persist(cfg.repo)

    lines = _audit_path().read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    entry = json.loads(lines[-1])
    assert entry["repo"] == cfg.repo.slug
    assert entry["from"] == "observe"
    assert entry["to"] == "self_merge"
    assert entry["source"] == "unhide"


def test_unhide_and_persist_no_audit_when_restore_equals_current() -> None:
    cfg = _engine(Posture.observe)
    register_and_persist(cfg, validate_env=False)
    hide_and_persist(cfg.repo)

    unhide_and_persist(cfg.repo)

    assert not _audit_path().exists()


def test_unhide_and_persist_no_audit_when_not_hidden() -> None:
    cfg = _engine(Posture.self_merge)
    register_and_persist(cfg, validate_env=False)

    unhide_and_persist(cfg.repo)

    assert not _audit_path().exists()


def test_unhide_and_persist_persists_across_reload() -> None:
    from alan.engine_config import _registry as _reg  # pyright: ignore[reportPrivateUsage]

    cfg = _engine(Posture.autonomous)
    register_and_persist(cfg, validate_env=False)
    hide_and_persist(cfg.repo)
    unhide_and_persist(cfg.repo)

    _reg.clear()
    loaded = load_persisted_engines()
    reloaded = next(c for c in loaded if c.repo == cfg.repo)

    assert reloaded.hidden is False
    assert reloaded.posture is Posture.autonomous
    assert reloaded.hidden_restore_posture is None
