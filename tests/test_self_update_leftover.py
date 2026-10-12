from __future__ import annotations

from pathlib import Path

import pytest

from alan.self_update import _journal, _leftover, _ops, _rollback, _soak

REPO = Path("not-a-real-repo")
PLIST = Path("not-a-real-plist")
HEALTH_URL = "http://health.invalid/health"


@pytest.fixture(autouse=True)
def _redirect_state_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))


def sample(phase: _journal.Phase) -> _journal.Journal:
    return _journal.Journal(phase, "a" * 40, "b" * 40, "dist.a", True)


@pytest.mark.parametrize(
    "phase",
    [
        _journal.Phase.APPLYING,
        _journal.Phase.SOAKING,
        _journal.Phase.ROLLING_BACK,
        _journal.Phase.ROLLBACK_FAILED,
        _journal.Phase.ROLLBACK_REFUSED_DIRTY,
    ],
)
def test_needs_resolution_is_true_for_every_leftover_phase(phase: _journal.Phase) -> None:
    assert _leftover.needs_resolution(sample(phase))


@pytest.mark.parametrize("phase", [_journal.Phase.LAST_GOOD, _journal.Phase.ROLLED_BACK])
def test_needs_resolution_is_false_for_terminal_success_phases(phase: _journal.Phase) -> None:
    assert not _leftover.needs_resolution(sample(phase))


def test_needs_resolution_is_false_when_there_is_no_journal() -> None:
    assert not _leftover.needs_resolution(None)


def test_an_applying_leftover_is_rolled_back(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[_journal.Journal] = []
    monkeypatch.setattr(_rollback, "perform", lambda repo, journal, **kw: calls.append(journal))
    _leftover.resolve(
        REPO, sample(_journal.Phase.APPLYING), scheduled=False,
        plist_path=PLIST, health_url=HEALTH_URL,
    )
    assert len(calls) == 1


def test_a_rolling_back_leftover_is_terminal_for_a_scheduled_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[_journal.Journal] = []
    monkeypatch.setattr(_rollback, "perform", lambda repo, journal, **kw: calls.append(journal))
    _leftover.resolve(
        REPO, sample(_journal.Phase.ROLLING_BACK), scheduled=True,
        plist_path=PLIST, health_url=HEALTH_URL,
    )
    assert calls == []


def test_a_rolling_back_leftover_is_retried_by_a_manual_apply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[_journal.Journal] = []
    monkeypatch.setattr(_rollback, "perform", lambda repo, journal, **kw: calls.append(journal))
    _leftover.resolve(
        REPO, sample(_journal.Phase.ROLLING_BACK), scheduled=False,
        plist_path=PLIST, health_url=HEALTH_URL,
    )
    assert len(calls) == 1


def test_a_rollback_failed_leftover_is_terminal_for_a_scheduled_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[_journal.Journal] = []
    monkeypatch.setattr(_rollback, "perform", lambda repo, journal, **kw: calls.append(journal))
    _leftover.resolve(
        REPO, sample(_journal.Phase.ROLLBACK_FAILED), scheduled=True,
        plist_path=PLIST, health_url=HEALTH_URL,
    )
    assert calls == []


def test_a_rollback_failed_leftover_is_retried_once_by_a_manual_apply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[_journal.Journal] = []
    monkeypatch.setattr(_rollback, "perform", lambda repo, journal, **kw: calls.append(journal))
    _leftover.resolve(
        REPO, sample(_journal.Phase.ROLLBACK_FAILED), scheduled=False,
        plist_path=PLIST, health_url=HEALTH_URL,
    )
    assert len(calls) == 1


def test_a_refused_dirty_leftover_changes_nothing_while_the_tree_stays_dirty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[_journal.Journal] = []
    monkeypatch.setattr(_rollback, "perform", lambda repo, journal, **kw: calls.append(journal))
    monkeypatch.setattr(_ops, "is_dirty", lambda repo: True)
    _leftover.resolve(
        REPO, sample(_journal.Phase.ROLLBACK_REFUSED_DIRTY), scheduled=False,
        plist_path=PLIST, health_url=HEALTH_URL,
    )
    assert calls == []


def test_a_refused_dirty_leftover_retries_once_the_tree_is_clean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[_journal.Journal] = []
    monkeypatch.setattr(_rollback, "perform", lambda repo, journal, **kw: calls.append(journal))
    monkeypatch.setattr(_ops, "is_dirty", lambda repo: False)
    _leftover.resolve(
        REPO, sample(_journal.Phase.ROLLBACK_REFUSED_DIRTY), scheduled=False,
        plist_path=PLIST, health_url=HEALTH_URL,
    )
    assert len(calls) == 1


def test_a_soaking_leftover_that_completes_cleanly_records_last_good(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rollback_calls: list[_journal.Journal] = []
    monkeypatch.setattr(
        _rollback, "perform", lambda repo, journal, **kw: rollback_calls.append(journal)
    )
    monkeypatch.setattr(_soak, "run", lambda target, url: True)
    journal = sample(_journal.Phase.SOAKING)
    _leftover.resolve(
        REPO, journal, scheduled=False, plist_path=PLIST, health_url=HEALTH_URL
    )
    assert rollback_calls == []
    stored = _journal.read()
    assert stored is not None
    assert stored.phase is _journal.Phase.LAST_GOOD
    assert stored.target_sha == journal.target_sha


def test_a_soaking_leftover_that_fails_is_rolled_back(monkeypatch: pytest.MonkeyPatch) -> None:
    rollback_calls: list[_journal.Journal] = []
    monkeypatch.setattr(
        _rollback, "perform", lambda repo, journal, **kw: rollback_calls.append(journal)
    )
    monkeypatch.setattr(_soak, "run", lambda target, url: False)
    _leftover.resolve(
        REPO, sample(_journal.Phase.SOAKING), scheduled=False,
        plist_path=PLIST, health_url=HEALTH_URL,
    )
    assert len(rollback_calls) == 1
