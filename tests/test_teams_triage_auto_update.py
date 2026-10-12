from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from alan import default_profile
from alan.default_profile import DefaultProfile, Resolved, Source
from alan.engine_config_validation import Posture
from alan.self_update import Reason, StatusRecord, Verdict, _journal
from alan.self_update._state import UpdaterState
from alan.teams import _never_live_budget, _triage_auto_update

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_SHA_A = "a" * 40
_SHA_B = "b" * 40
_ALL_BLOCKED_REASONS = (
    Reason.SHALLOW_CLONE,
    Reason.NOT_ON_MAIN,
    Reason.REJECTED_COMMIT,
    Reason.DIRTY_TREE,
    Reason.NOT_FAST_FORWARD,
    Reason.NO_GITHUB_ORIGIN,
)


def _profile_resolved(source: Source) -> Resolved:
    profile = DefaultProfile(owner="acme", repo="widget", posture=Posture.observe)
    return Resolved(profile, source)


def _set_source(monkeypatch: pytest.MonkeyPatch, source: Source) -> None:
    monkeypatch.setattr(default_profile, "resolved", lambda: _profile_resolved(source))


def _status(
    verdict: Verdict, reason: Reason, checked_at: str = "2026-01-01T00:00:00+00:00"
) -> StatusRecord:
    return StatusRecord(verdict, reason, _SHA_A, _SHA_B, checked_at)


def _set_state(
    monkeypatch: pytest.MonkeyPatch,
    *,
    status: StatusRecord | None,
    journal: _journal.Journal | None = None,
) -> None:
    monkeypatch.setattr(
        _triage_auto_update, "load_state", lambda: UpdaterState(status=status, journal=journal)
    )


@pytest.fixture(autouse=True)
def _clear_budget_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALAN_DEPLOY_VERIFY_BUDGET_S", raising=False)


def test_no_surfaces_when_the_profile_source_is_the_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_source(monkeypatch, Source.owner)
    _set_state(monkeypatch, status=_status(Verdict.BLOCKED, Reason.DIRTY_TREE))
    assert _triage_auto_update.auto_update_surfaces(now=_NOW) == []


def test_no_surfaces_when_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_source(monkeypatch, Source.unconfigured)
    _set_state(monkeypatch, status=_status(Verdict.BLOCKED, Reason.DIRTY_TREE))
    assert _triage_auto_update.auto_update_surfaces(now=_NOW) == []


def test_no_surfaces_when_there_is_no_status_record(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_source(monkeypatch, Source.profile)
    _set_state(monkeypatch, status=None)
    assert _triage_auto_update.auto_update_surfaces(now=_NOW) == []


@pytest.mark.parametrize("reason", _ALL_BLOCKED_REASONS)
def test_one_finding_per_blocked_reason(
    monkeypatch: pytest.MonkeyPatch, reason: Reason
) -> None:
    _set_source(monkeypatch, Source.profile)
    _set_state(monkeypatch, status=_status(Verdict.BLOCKED, reason))
    surfaces = _triage_auto_update.auto_update_surfaces(now=_NOW)
    assert [s.blocker_reason for s in surfaces] == [reason.value]
    assert surfaces[0].kind == "auto_update"
    assert surfaces[0].is_blocked is True


def test_a_red_main_produces_a_main_red_finding(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_source(monkeypatch, Source.profile)
    _set_state(monkeypatch, status=_status(Verdict.RED, Reason.RUN_NOT_SUCCESSFUL))
    surfaces = _triage_auto_update.auto_update_surfaces(now=_NOW)
    assert [s.blocker_reason for s in surfaces] == ["main_red"]


def test_a_rolled_back_journal_produces_a_rolled_back_finding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_source(monkeypatch, Source.profile)
    status = _status(Verdict.UP_TO_DATE, Reason.ALREADY_CURRENT)
    journal = _journal.Journal(_journal.Phase.ROLLED_BACK, _SHA_A, _SHA_B, None, True)
    _set_state(monkeypatch, status=status, journal=journal)
    surfaces = _triage_auto_update.auto_update_surfaces(now=_NOW)
    assert [s.blocker_reason for s in surfaces] == ["rolled_back"]


def test_a_failed_rollback_journal_produces_a_rollback_failed_finding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_source(monkeypatch, Source.profile)
    status = _status(Verdict.UP_TO_DATE, Reason.ALREADY_CURRENT)
    journal = _journal.Journal(_journal.Phase.ROLLBACK_FAILED, _SHA_A, _SHA_B, None, True)
    _set_state(monkeypatch, status=status, journal=journal)
    surfaces = _triage_auto_update.auto_update_surfaces(now=_NOW)
    assert [s.blocker_reason for s in surfaces] == ["rollback_failed"]


def test_other_journal_phases_produce_no_finding(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_source(monkeypatch, Source.profile)
    status = _status(Verdict.UP_TO_DATE, Reason.ALREADY_CURRENT)
    journal = _journal.Journal(_journal.Phase.LAST_GOOD, _SHA_A, _SHA_B, None, True)
    _set_state(monkeypatch, status=status, journal=journal)
    assert _triage_auto_update.auto_update_surfaces(now=_NOW) == []


def test_a_stale_status_record_produces_a_stale_finding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_source(monkeypatch, Source.profile)
    threshold = _never_live_budget.stale_threshold_s()
    old = (_NOW - timedelta(seconds=threshold + 1)).isoformat()
    _set_state(monkeypatch, status=_status(Verdict.UP_TO_DATE, Reason.ALREADY_CURRENT, old))
    surfaces = _triage_auto_update.auto_update_surfaces(now=_NOW)
    assert [s.blocker_reason for s in surfaces] == ["stale"]


def test_a_recent_status_record_produces_no_stale_finding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_source(monkeypatch, Source.profile)
    recent = (_NOW - timedelta(seconds=60)).isoformat()
    _set_state(monkeypatch, status=_status(Verdict.UP_TO_DATE, Reason.ALREADY_CURRENT, recent))
    assert _triage_auto_update.auto_update_surfaces(now=_NOW) == []


def test_a_malformed_checked_at_produces_no_stale_finding_but_keeps_other_findings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_source(monkeypatch, Source.profile)
    _set_state(
        monkeypatch, status=_status(Verdict.BLOCKED, Reason.DIRTY_TREE, "not-a-timestamp")
    )
    surfaces = _triage_auto_update.auto_update_surfaces(now=_NOW)
    assert {s.blocker_reason for s in surfaces} == {"dirty_tree"}


def test_a_naive_checked_at_produces_no_stale_finding_but_keeps_other_findings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_source(monkeypatch, Source.profile)
    _set_state(
        monkeypatch,
        status=_status(Verdict.BLOCKED, Reason.DIRTY_TREE, "2000-01-01T00:00:00"),
    )
    surfaces = _triage_auto_update.auto_update_surfaces(now=_NOW)
    assert {s.blocker_reason for s in surfaces} == {"dirty_tree"}


def test_a_blocked_and_stale_status_produces_both_findings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_source(monkeypatch, Source.profile)
    threshold = _never_live_budget.stale_threshold_s()
    old = (_NOW - timedelta(seconds=threshold + 1)).isoformat()
    _set_state(monkeypatch, status=_status(Verdict.BLOCKED, Reason.DIRTY_TREE, old))
    surfaces = _triage_auto_update.auto_update_surfaces(now=_NOW)
    assert {s.blocker_reason for s in surfaces} == {"dirty_tree", "stale"}
