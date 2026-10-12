from __future__ import annotations

import time

import pytest

from alan import e2e_health_fixture
from alan.health import HEALTH_META_KEY, load_health_state
from alan.queue import Queue

_HEALTH_ENV = "ALAN_E2E_HEALTH_FIXTURE"
_DESKTOP_MAX_ENV = "ALAN_E2E_DESKTOP_MAX_HEALTH_FIXTURE"
_REPOS_ENV = "ALAN_E2E_HEALTH_REPOS_FIXTURE"


@pytest.fixture(autouse=True)
def _clear_health_repos_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_REPOS_ENV, raising=False)


def test_both_unset_is_a_no_op(queue: Queue, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_HEALTH_ENV, raising=False)
    monkeypatch.delenv(_DESKTOP_MAX_ENV, raising=False)
    e2e_health_fixture.seed_e2e_health_fixtures(queue)
    assert queue.get_meta(HEALTH_META_KEY) is None
    assert queue.paused_until() is None


def test_health_fixture_blank_is_a_no_op(queue: Queue, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_HEALTH_ENV, "")
    monkeypatch.delenv(_DESKTOP_MAX_ENV, raising=False)
    e2e_health_fixture.seed_e2e_health_fixtures(queue)
    assert queue.get_meta(HEALTH_META_KEY) is None
    assert queue.paused_until() is None


@pytest.mark.parametrize("value", ["0", "true", "yes", "11"])
def test_health_fixture_non_exact_one_is_a_no_op(
    queue: Queue, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(_HEALTH_ENV, value)
    monkeypatch.delenv(_DESKTOP_MAX_ENV, raising=False)
    e2e_health_fixture.seed_e2e_health_fixtures(queue)
    assert queue.get_meta(HEALTH_META_KEY) is None
    assert queue.paused_until() is None


def test_health_fixture_seeds_populated_state_and_long_pause(
    queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(_HEALTH_ENV, "1")
    monkeypatch.delenv(_DESKTOP_MAX_ENV, raising=False)
    e2e_health_fixture.seed_e2e_health_fixtures(queue)

    assert queue.get_meta(HEALTH_META_KEY) is not None
    state = load_health_state(queue)
    assert state is not None
    assert state.verdict == "degraded"
    assert len(state.findings) == 3
    assert any(f.blocker_flags for f in state.findings)
    assert any(f.in_flight for f in state.findings)

    paused_until = queue.paused_until()
    assert paused_until is not None
    assert paused_until > time.time() + 300 * 24 * 3600


def test_desktop_max_fixture_unset_is_a_no_op(
    queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(_HEALTH_ENV, raising=False)
    monkeypatch.delenv(_DESKTOP_MAX_ENV, raising=False)
    e2e_health_fixture.seed_e2e_health_fixtures(queue)
    assert queue.get_meta(HEALTH_META_KEY) is None


@pytest.mark.parametrize("value", ["0", "true", "yes", "11", ""])
def test_desktop_max_fixture_non_exact_one_is_a_no_op(
    queue: Queue, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.delenv(_HEALTH_ENV, raising=False)
    monkeypatch.setenv(_DESKTOP_MAX_ENV, value)
    e2e_health_fixture.seed_e2e_health_fixtures(queue)
    assert queue.get_meta(HEALTH_META_KEY) is None
    assert queue.paused_until() is None


def test_desktop_max_fixture_seeds_richer_state_spanning_every_severity(
    queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(_HEALTH_ENV, raising=False)
    monkeypatch.setenv(_DESKTOP_MAX_ENV, "1")
    e2e_health_fixture.seed_e2e_health_fixtures(queue)

    assert queue.get_meta(HEALTH_META_KEY) is not None
    state = load_health_state(queue)
    assert state is not None
    assert state.verdict == "degraded"
    assert len(state.findings) == 4
    assert {f.severity for f in state.findings} == {"critical", "high", "medium"}
    assert any(f.blocker_flags for f in state.findings)
    assert any(f.in_flight for f in state.findings)
    assert any(f.repo is not None for f in state.findings)

    paused_until = queue.paused_until()
    assert paused_until is not None
    assert paused_until > time.time() + 300 * 24 * 3600


def test_both_set_raises_runtime_error(queue: Queue, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_HEALTH_ENV, "1")
    monkeypatch.setenv(_DESKTOP_MAX_ENV, "1")
    with pytest.raises(RuntimeError, match="at most one of"):
        e2e_health_fixture.seed_e2e_health_fixtures(queue)
    assert queue.get_meta(HEALTH_META_KEY) is None


def test_all_three_set_raises_runtime_error(
    queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(_HEALTH_ENV, "1")
    monkeypatch.setenv(_DESKTOP_MAX_ENV, "1")
    monkeypatch.setenv(_REPOS_ENV, "1")
    with pytest.raises(RuntimeError, match="at most one of"):
        e2e_health_fixture.seed_e2e_health_fixtures(queue)
    assert queue.get_meta(HEALTH_META_KEY) is None


def test_repos_fixture_unset_is_a_no_op(queue: Queue, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_HEALTH_ENV, raising=False)
    monkeypatch.delenv(_DESKTOP_MAX_ENV, raising=False)
    e2e_health_fixture.seed_e2e_health_fixtures(queue)
    assert queue.get_meta(HEALTH_META_KEY) is None
    assert queue.paused_until() is None


def test_repos_fixture_seeds_findings_across_two_repos(
    queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.engine_config import lookup_by_slug

    monkeypatch.delenv(_HEALTH_ENV, raising=False)
    monkeypatch.delenv(_DESKTOP_MAX_ENV, raising=False)
    monkeypatch.setenv(_REPOS_ENV, "1")
    e2e_health_fixture.seed_e2e_health_fixtures(queue)

    assert queue.get_meta(HEALTH_META_KEY) is not None
    state = load_health_state(queue)
    assert state is not None
    assert len(state.findings) == 3
    repos = {f.engine_key for f in state.findings}
    assert repos == {"acme-platform/checkout-service", "acme-platform/quiet-repo"}
    assert lookup_by_slug("acme-platform/checkout-service") is not None
    assert lookup_by_slug("acme-platform/quiet-repo") is not None

    paused_until = queue.paused_until()
    assert paused_until is not None
    assert paused_until > time.time() + 300 * 24 * 3600


def test_repos_fixture_registration_is_idempotent(
    queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(_HEALTH_ENV, raising=False)
    monkeypatch.delenv(_DESKTOP_MAX_ENV, raising=False)
    monkeypatch.setenv(_REPOS_ENV, "1")
    e2e_health_fixture.seed_e2e_health_fixtures(queue)
    e2e_health_fixture.seed_e2e_health_fixtures(queue)
    state = load_health_state(queue)
    assert state is not None
    assert len(state.findings) == 3
