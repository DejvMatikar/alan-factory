from __future__ import annotations

from pathlib import Path

import pytest

from alan import driver_loop_pause as pause_mod
from alan.detached_mode import DIAL_ENV


@pytest.fixture(autouse=True)
def _redirect_driver_loop_state_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALAN_DRIVER_LOOP_STATE_ROOT", str(tmp_path))


def test_pause_dir_lives_under_driver_loop_state_root(tmp_path: Path) -> None:
    assert pause_mod.pause_dir() == tmp_path / "driver_loops" / "pause"


def test_pause_file_path_is_named_for_loop() -> None:
    assert pause_mod.pause_file("babysit").name == "babysit.pause"


def test_is_paused_false_when_no_file() -> None:
    assert pause_mod.is_paused("babysit") is False


def test_pause_refuses_when_not_supervised(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DIAL_ENV, "off")
    with pytest.raises(pause_mod.DriverLoopPauseError) as exc:
        pause_mod.pause("babysit")
    assert exc.value.reason == "not_supervised"
    assert pause_mod.is_paused("babysit") is False


def test_resume_refuses_when_not_supervised(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DIAL_ENV, "off")
    with pytest.raises(pause_mod.DriverLoopPauseError) as exc:
        pause_mod.resume("babysit")
    assert exc.value.reason == "not_supervised"


def test_pause_and_resume_round_trip_when_supervised(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DIAL_ENV, "on")
    pause_mod.pause("babysit")
    assert pause_mod.is_paused("babysit") is True
    pause_mod.resume("babysit")
    assert pause_mod.is_paused("babysit") is False


def test_pause_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DIAL_ENV, "on")
    pause_mod.pause("babysit")
    pause_mod.pause("babysit")
    assert pause_mod.is_paused("babysit") is True


def test_resume_is_idempotent_when_never_paused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DIAL_ENV, "on")
    pause_mod.resume("babysit")
    assert pause_mod.is_paused("babysit") is False


def test_pause_allowed_for_disabled_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DIAL_ENV, "on")
    pause_mod.pause("nightly-driver")
    assert pause_mod.is_paused("nightly-driver") is True


def test_pause_is_per_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DIAL_ENV, "on")
    pause_mod.pause("babysit")
    assert pause_mod.is_paused("nightly-driver") is False
