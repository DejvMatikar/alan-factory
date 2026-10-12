from __future__ import annotations

import pytest

from alan import default_profile
from alan.default_profile import DefaultProfile, Resolved, Source
from alan.engine_config_validation import Posture
from alan.teams import _never_live_budget


def _resolved(never_live_budget_s: float | None) -> Resolved:
    profile = DefaultProfile(
        owner="acme",
        repo="widget",
        posture=Posture.observe,
        never_live_budget_s=never_live_budget_s,
    )
    return Resolved(profile, Source.profile)


@pytest.fixture(autouse=True)
def _clear_budget_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALAN_DEPLOY_VERIFY_BUDGET_S", raising=False)


def test_profile_budget_wins_over_the_environment_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(default_profile, "resolved", lambda: _resolved(1200.0))
    monkeypatch.setenv("ALAN_DEPLOY_VERIFY_BUDGET_S", "999")
    assert _never_live_budget.effective_budget_s() == 1200.0


def test_environment_override_wins_when_the_profile_budget_is_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(default_profile, "resolved", lambda: _resolved(None))
    monkeypatch.setenv("ALAN_DEPLOY_VERIFY_BUDGET_S", "999")
    assert _never_live_budget.effective_budget_s() == 999.0


def test_default_budget_applies_when_neither_profile_nor_environment_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(default_profile, "resolved", lambda: _resolved(None))
    assert _never_live_budget.effective_budget_s() == 5400.0


def test_idle_wait_bound_is_the_budget_minus_the_margin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(default_profile, "resolved", lambda: _resolved(2000.0))
    assert _never_live_budget.idle_wait_bound_s() == 2000.0 - _never_live_budget.IDLE_WAIT_MARGIN_S


def test_stale_threshold_is_the_budget_plus_the_allowance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(default_profile, "resolved", lambda: _resolved(2000.0))
    assert (
        _never_live_budget.stale_threshold_s()
        == 2000.0 + _never_live_budget.STALE_ALLOWANCE_S
    )
