from __future__ import annotations

from pathlib import Path

import pytest

from alan.engine_config import (
    EngineConfig,
    EngineNotFoundError,
    Posture,
    RepoIdentity,
    apply_stored_projection,
    lookup_by_repo,
    register,
    set_accent_and_persist,
)
from alan.engine_registry_store import list_engines as _persisted_engines
from alan.repo_accent import AccentChoice


@pytest.fixture
def engines_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "engines"
    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(root))
    return root


_REPO = RepoIdentity(owner="acme", repo="widget")


def test_engine_config_accent_defaults_to_none(engines_root: Path) -> None:
    _ = engines_root
    cfg = EngineConfig(repo=_REPO)
    assert cfg.accent is None


def test_set_accent_and_persist_sets_explicit_choice(engines_root: Path) -> None:
    _ = engines_root
    register(EngineConfig(repo=_REPO))
    choice = AccentChoice(color="iris", intensity="bold")
    updated = set_accent_and_persist(_REPO, choice)
    assert updated.accent == choice
    live = lookup_by_repo(_REPO)
    assert live is not None
    assert live.accent == choice


def test_set_accent_and_persist_persists_to_disk(engines_root: Path) -> None:
    _ = engines_root
    register(EngineConfig(repo=_REPO))
    choice = AccentChoice(color="violet", intensity="soft")
    set_accent_and_persist(_REPO, choice)
    [payload] = _persisted_engines()
    assert payload["accent"] == {"color": "violet", "intensity": "soft"}


def test_set_accent_and_persist_explicit_no_accent(engines_root: Path) -> None:
    _ = engines_root
    register(EngineConfig(repo=_REPO))
    no_accent = AccentChoice(color=None)
    updated = set_accent_and_persist(_REPO, no_accent)
    assert updated.accent == no_accent
    assert updated.accent is not None
    assert updated.accent.color is None


def test_set_accent_and_persist_none_resets_to_automatic(engines_root: Path) -> None:
    _ = engines_root
    register(EngineConfig(repo=_REPO, accent=AccentChoice(color="ruby")))
    updated = set_accent_and_persist(_REPO, None)
    assert updated.accent is None


def test_set_accent_and_persist_noop_shortcut_skips_write(engines_root: Path) -> None:
    _ = engines_root
    choice = AccentChoice(color="plum", intensity="standard")
    register(EngineConfig(repo=_REPO, accent=choice))
    updated = set_accent_and_persist(_REPO, choice)
    assert updated.accent == choice
    assert _persisted_engines() == []


def test_set_accent_and_persist_unknown_engine_raises(engines_root: Path) -> None:
    _ = engines_root
    with pytest.raises(EngineNotFoundError):
        set_accent_and_persist(_REPO, AccentChoice(color="iris"))


def test_apply_stored_projection_overlays_explicit_accent(engines_root: Path) -> None:
    _ = engines_root
    file_cfg = EngineConfig(repo=_REPO, posture=Posture.autonomous)
    choice = AccentChoice(color="orchid", intensity="bold")
    stored = EngineConfig(repo=_REPO, accent=choice)
    merged = apply_stored_projection(file_cfg, stored)
    assert merged.accent == choice


def test_apply_stored_projection_overlays_explicit_no_accent(engines_root: Path) -> None:
    _ = engines_root
    file_cfg = EngineConfig(repo=_REPO)
    no_accent = AccentChoice(color=None)
    stored = EngineConfig(repo=_REPO, accent=no_accent)
    merged = apply_stored_projection(file_cfg, stored)
    assert merged.accent == no_accent


def test_apply_stored_projection_leaves_automatic_accent_when_stored_is_automatic(
    engines_root: Path,
) -> None:
    _ = engines_root
    file_cfg = EngineConfig(repo=_REPO)
    stored = EngineConfig(repo=_REPO, accent=None)
    merged = apply_stored_projection(file_cfg, stored)
    assert merged.accent is None
