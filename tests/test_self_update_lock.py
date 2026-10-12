from __future__ import annotations

from pathlib import Path

import pytest

from alan import roots
from alan.self_update import _lock


@pytest.fixture(autouse=True)
def _redirect_state_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))


def test_a_single_acquire_succeeds() -> None:
    with _lock.acquire() as acquired:
        assert acquired


def test_a_nested_acquire_while_the_first_is_held_fails_immediately() -> None:
    with _lock.acquire() as first:
        assert first
        with _lock.acquire() as second:
            assert not second


def test_the_lock_is_released_on_exit_and_can_be_acquired_again() -> None:
    with _lock.acquire() as first:
        assert first
    with _lock.acquire() as second:
        assert second


def test_the_lock_directory_is_created_when_missing() -> None:
    assert not roots.deploy_marker_root().exists()
    with _lock.acquire() as acquired:
        assert acquired
    assert roots.deploy_marker_root().is_dir()
