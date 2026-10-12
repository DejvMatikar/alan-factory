from __future__ import annotations

import json
import time as _time_mod
from pathlib import Path
from typing import Any

import pytest

from alan.engine_config import DEFAULT_FORGE_TARGET, ForgeTarget
from alan.teams import _pr_branch, _pr_ready, _verification_gates, triage_pregate

_FORGE = DEFAULT_FORGE_TARGET
_PENDING_RUN = {"name": "ci", "status": "in_progress", "conclusion": None}
_DONE_RUN = {"name": "ci", "status": "completed", "conclusion": "success"}
_DEFAULT_SCHEDULE = [30.0, 30.0, 60.0, 120.0, 240.0] + [300.0] * 9 + [60.0]


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def monotonic(self) -> float:
        return self.now


@pytest.fixture
def audit_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("ALAN_VERIFIER_SETTLE_MAX_POLLS", raising=False)
    monkeypatch.delenv("ALAN_VERIFIER_SETTLE_POLL_INTERVAL_S", raising=False)
    monkeypatch.delenv("ALAN_VERIFIER_GH_TOKEN", raising=False)
    root = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(root))
    return root


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    fake = _Clock()
    monkeypatch.setattr(_time_mod, "sleep", fake.sleep)
    monkeypatch.setattr(_time_mod, "monotonic", fake.monotonic)
    return fake


def _rows(audit_root: Path, action: str | None = None) -> list[dict[str, Any]]:
    path = audit_root / "verification_audit.jsonl"
    if not path.exists():
        return []
    rows = [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]
    for row in rows:
        row.pop("timestamp_utc", None)
    return [r for r in rows if action is None or r["action"] == action]


def _pending(head_sha: str = "sha-1") -> _pr_ready.PrReadiness:
    return _pr_ready.PrReadiness(
        ok=False, merge_state_status="BLOCKED", review_decision="APPROVED",
        pending_checks=("ci",), head_sha=head_sha,
    )


def _green(head_sha: str = "sha-1") -> _pr_ready.PrReadiness:
    return _pr_ready.PrReadiness(
        ok=True, merge_state_status="CLEAN", review_decision="APPROVED", head_sha=head_sha,
    )


def _sequenced_blockers(
    monkeypatch: pytest.MonkeyPatch, readiness_seq: list[_pr_ready.ReadinessResult]
) -> list[int]:
    calls: list[int] = []

    def fake(pr: int, tid: str, **_kw: object) -> _pr_ready.ReadinessResult:  # noqa: ARG001
        calls.append(pr)
        return readiness_seq.pop(0)

    monkeypatch.setattr(_pr_ready, "check_pr_verifier_blockers", fake)
    return calls


def _sequenced_head_shas(
    monkeypatch: pytest.MonkeyPatch, shas: list[str | None]
) -> list[int]:
    seen: list[int] = []

    def fake(pr: int, *, forge_target: Any, on_fail: Any) -> str | None:
        assert forge_target is _FORGE
        seen.append(pr)
        sha = shas.pop(0)
        if sha is None:
            on_fail(pr, "gh returncode=1: head unavailable")
        return sha

    monkeypatch.setattr(_pr_branch, "_fetch_pr_head_sha", fake)
    return seen


def _sequenced_probe(
    monkeypatch: pytest.MonkeyPatch,
    run_lists: list[list[dict[str, Any]] | None],
    *,
    clock: _Clock | None = None,
    cost_s: float = 0.0,
) -> list[tuple[ForgeTarget, str, int]]:
    seen: list[tuple[ForgeTarget, str, int]] = []

    def fake(forge_target: ForgeTarget, sha: str, pr: int, *, on_fail: Any, strict: bool) -> Any:
        assert strict is True
        seen.append((forge_target, sha, pr))
        if clock is not None:
            clock.now += cost_s
        runs = run_lists.pop(0)
        if runs is None:
            on_fail(pr, "gh returncode=1: rate limited")
        return runs

    monkeypatch.setattr(_pr_branch, "_iter_check_runs", fake)
    return seen


def _never_probe(*_a: object, **_k: object) -> None:
    raise AssertionError("the REST probe must not run on this path")


def _never_fetch(*_a: object, **_k: object) -> _pr_ready.PrReadiness:
    raise AssertionError("readiness must not be fetched on this path")


def _settle(readiness: _pr_ready.PrReadiness) -> _pr_ready.ReadinessResult:
    return _verification_gates._settle_wait(42, "t", readiness, forge_target=_FORGE)  # noqa: SLF001


def test_readiness_fetch_is_skipped_while_the_probe_reports_pending_check_runs(
    monkeypatch: pytest.MonkeyPatch, audit_root: Path, clock: _Clock
) -> None:
    heads = _sequenced_head_shas(monkeypatch, ["sha-1"] * 3)
    probes = _sequenced_probe(monkeypatch, [[_PENDING_RUN], [_PENDING_RUN], [_DONE_RUN]])
    fetches = _sequenced_blockers(monkeypatch, [_green()])
    result = _settle(_pending())
    assert isinstance(result, _pr_ready.PrReadiness)
    assert result.ok is True
    assert fetches == [42]
    assert heads == [42, 42, 42]
    assert probes == [(_FORGE, "sha-1", 42)] * 3
    assert clock.sleeps == [30.0, 30.0, 60.0]
    polls = _rows(audit_root, "verifier_settle_poll")
    assert [(r["poll"], r["sleep_s"], r["pending"]) for r in polls] == [
        (0, 30.0, ["ci"]), (1, 30.0, ["ci"]), (2, 60.0, ["ci"]),
    ]
    assert [(r["poll"], r["failed"]) for r in _rows(audit_root, "verifier_settle_green")] == [
        (3, []),
    ]
    assert _rows(audit_root, "verifier_settle_probe_unavailable") == []
    assert _rows(audit_root, "verifier_settle_head_moved") == []


def test_unavailable_probe_falls_back_to_the_full_fetch_on_every_wake_up(
    monkeypatch: pytest.MonkeyPatch, audit_root: Path, clock: _Clock
) -> None:
    _sequenced_head_shas(monkeypatch, ["sha-1"] * 3)
    _sequenced_probe(monkeypatch, [None, None, None])
    fetches = _sequenced_blockers(monkeypatch, [_pending(), _pending(), _green()])
    result = _settle(_pending())
    assert isinstance(result, _pr_ready.PrReadiness)
    assert result.ok is True
    assert fetches == [42, 42, 42]
    unavailable = _rows(audit_root, "verifier_settle_probe_unavailable")
    assert [(r["pr_number"], r["head_sha"]) for r in unavailable] == [(42, "sha-1")] * 3
    assert all("rate limited" in r["reason"] for r in unavailable)
    assert len(_rows(audit_root, "verifier_settle_green")) == 1


def test_head_sha_fetch_failure_takes_the_unavailable_path_without_probing(
    monkeypatch: pytest.MonkeyPatch, audit_root: Path, clock: _Clock
) -> None:
    heads = _sequenced_head_shas(monkeypatch, [None, None])
    monkeypatch.setattr(_pr_branch, "_iter_check_runs", _never_probe)
    fetches = _sequenced_blockers(monkeypatch, [_pending(), _green()])
    result = _settle(_pending())
    assert isinstance(result, _pr_ready.PrReadiness)
    assert result.ok is True
    assert heads == [42, 42]
    assert fetches == [42, 42]
    unavailable = _rows(audit_root, "verifier_settle_probe_unavailable")
    assert [r["reason"] for r in unavailable] == ["gh returncode=1: head unavailable"] * 2
    assert [r["head_sha"] for r in unavailable] == ["sha-1", "sha-1"]


def test_no_head_sha_never_reads_the_head_or_probes(
    monkeypatch: pytest.MonkeyPatch, audit_root: Path, clock: _Clock
) -> None:
    monkeypatch.setattr(_pr_branch, "_fetch_pr_head_sha", _never_probe)
    monkeypatch.setattr(_pr_branch, "_iter_check_runs", _never_probe)
    fetches = _sequenced_blockers(monkeypatch, [_pending(""), _green("")])
    result = _settle(_pending(""))
    assert isinstance(result, _pr_ready.PrReadiness)
    assert result.ok is True
    assert fetches == [42, 42]
    assert len(_rows(audit_root, "verifier_settle_poll")) == 2


def test_push_mid_settle_triggers_the_authoritative_fetch_on_the_next_wake_up(
    monkeypatch: pytest.MonkeyPatch, audit_root: Path, clock: _Clock
) -> None:
    heads = _sequenced_head_shas(monkeypatch, ["sha-2", "sha-2"])
    probes = _sequenced_probe(monkeypatch, [[_DONE_RUN]])
    fetches = _sequenced_blockers(monkeypatch, [_pending("sha-2"), _green("sha-2")])
    result = _settle(_pending("sha-1"))
    assert isinstance(result, _pr_ready.PrReadiness)
    assert result.head_sha == "sha-2"
    assert heads == [42, 42]
    assert [p[1] for p in probes] == ["sha-2"]
    assert fetches == [42, 42]
    moved = _rows(audit_root, "verifier_settle_head_moved")
    assert [(r["pr_number"], r["pinned_sha"], r["head_sha"]) for r in moved] == [
        (42, "sha-1", "sha-2"),
    ]
    assert _rows(audit_root, "verifier_settle_probe_unavailable") == []


def test_stale_pinned_head_with_a_still_queued_check_cannot_hold_the_wait(
    monkeypatch: pytest.MonkeyPatch, audit_root: Path, clock: _Clock
) -> None:
    _sequenced_head_shas(monkeypatch, ["sha-2"])
    monkeypatch.setattr(_pr_branch, "_iter_check_runs", _never_probe)
    fetches = _sequenced_blockers(monkeypatch, [_green("sha-2")])
    result = _settle(_pending("sha-1"))
    assert isinstance(result, _pr_ready.PrReadiness)
    assert result.head_sha == "sha-2"
    assert fetches == [42]
    assert len(_rows(audit_root, "verifier_settle_head_moved")) == 1


def test_probe_repins_to_the_head_sha_the_authoritative_fetch_reports(
    monkeypatch: pytest.MonkeyPatch, audit_root: Path, clock: _Clock
) -> None:
    _sequenced_head_shas(monkeypatch, ["sha-1", "sha-2", "sha-2"])
    probes = _sequenced_probe(monkeypatch, [[_DONE_RUN], [_PENDING_RUN], [_DONE_RUN]])
    fetches = _sequenced_blockers(monkeypatch, [_pending("sha-2"), _green("sha-2")])
    result = _settle(_pending("sha-1"))
    assert isinstance(result, _pr_ready.PrReadiness)
    assert result.head_sha == "sha-2"
    assert [p[1] for p in probes] == ["sha-1", "sha-2", "sha-2"]
    assert fetches == [42, 42]
    assert _rows(audit_root, "verifier_settle_head_moved") == []


def test_transient_fetch_after_a_clear_probe_exits_with_the_transient_row(
    monkeypatch: pytest.MonkeyPatch, audit_root: Path, clock: _Clock
) -> None:
    _sequenced_head_shas(monkeypatch, ["sha-1"])
    _sequenced_probe(monkeypatch, [[_DONE_RUN]])
    transient = _pr_ready.ReadinessUnavailable("x")
    _sequenced_blockers(monkeypatch, [transient])
    assert isinstance(_settle(_pending()), _pr_ready.ReadinessUnavailable)
    assert [r["poll"] for r in _rows(audit_root, "verifier_settle_transient")] == [0]


def test_failed_fetch_after_a_clear_probe_exits_with_the_fail_row(
    monkeypatch: pytest.MonkeyPatch, audit_root: Path, clock: _Clock
) -> None:
    _sequenced_head_shas(monkeypatch, ["sha-1"])
    _sequenced_probe(monkeypatch, [[_DONE_RUN], []])
    failed = _pr_ready.PrReadiness(
        ok=False, merge_state_status="BLOCKED", review_decision="APPROVED",
        failing_checks=("ci",), failed_reasons=("failing checks: ci",), head_sha="sha-1",
    )
    _sequenced_blockers(monkeypatch, [failed])
    result = _settle(_pending())
    assert isinstance(result, _pr_ready.PrReadiness)
    assert result.failed_reasons == ("failing checks: ci",)
    rows = _rows(audit_root, "verifier_settle_fail")
    assert [(r["poll"], r["failed"]) for r in rows] == [(1, ["failing checks: ci"])]


def test_backoff_schedule_spends_the_default_budget_in_fifteen_wake_ups(
    monkeypatch: pytest.MonkeyPatch, audit_root: Path, clock: _Clock
) -> None:
    _sequenced_head_shas(monkeypatch, ["sha-1"] * 15)
    _sequenced_probe(monkeypatch, [[_PENDING_RUN]] * 15)
    monkeypatch.setattr(_pr_ready, "check_pr_verifier_blockers", _never_fetch)
    initial = _pending()
    assert _settle(initial) is initial
    assert clock.sleeps == _DEFAULT_SCHEDULE
    assert sum(clock.sleeps) == 3240.0
    assert clock.now == 1000.0 + 3240.0
    assert [r["sleep_s"] for r in _rows(audit_root, "verifier_settle_poll")] == _DEFAULT_SCHEDULE
    timeout = _rows(audit_root, "verifier_settle_timeout")
    assert [(r["task_id"], r["pr_number"], r["polls"], r["budget_s"]) for r in timeout] == [
        ("t", 42, 15, 3240.0),
    ]


def test_slow_probes_count_against_the_monotonic_deadline(
    monkeypatch: pytest.MonkeyPatch, audit_root: Path, clock: _Clock
) -> None:
    _sequenced_head_shas(monkeypatch, ["sha-1"] * 11)
    _sequenced_probe(monkeypatch, [[_PENDING_RUN]] * 11, clock=clock, cost_s=100.0)
    monkeypatch.setattr(_pr_ready, "check_pr_verifier_blockers", _never_fetch)
    _settle(_pending())
    assert clock.sleeps == [30.0, 30.0, 60.0, 120.0, 240.0] + [300.0] * 5 + [260.0]
    assert sum(clock.sleeps) + 100.0 * (len(clock.sleeps) - 1) == 3240.0
    assert _rows(audit_root, "verifier_settle_timeout")[0]["polls"] == 11


def test_zero_interval_keeps_the_max_poll_recheck_count(
    monkeypatch: pytest.MonkeyPatch, audit_root: Path, clock: _Clock
) -> None:
    monkeypatch.setenv("ALAN_VERIFIER_SETTLE_POLL_INTERVAL_S", "0")
    monkeypatch.setenv("ALAN_VERIFIER_SETTLE_MAX_POLLS", "4")
    _sequenced_head_shas(monkeypatch, ["sha-1"] * 4)
    _sequenced_probe(monkeypatch, [[_DONE_RUN]] * 4)
    fetches = _sequenced_blockers(monkeypatch, [_pending()] * 4)
    _settle(_pending())
    assert fetches == [42] * 4
    assert clock.sleeps == [0.0] * 4
    timeout = _rows(audit_root, "verifier_settle_timeout")
    assert [(r["polls"], r["budget_s"]) for r in timeout] == [(4, 0.0)]


def test_zero_interval_still_exits_on_the_first_green_recheck(
    monkeypatch: pytest.MonkeyPatch, audit_root: Path, clock: _Clock
) -> None:
    monkeypatch.setenv("ALAN_VERIFIER_SETTLE_POLL_INTERVAL_S", "0")
    monkeypatch.setenv("ALAN_VERIFIER_SETTLE_MAX_POLLS", "4")
    _sequenced_head_shas(monkeypatch, ["sha-1"])
    _sequenced_probe(monkeypatch, [[_DONE_RUN]])
    fetches = _sequenced_blockers(monkeypatch, [_green()])
    result = _settle(_pending())
    assert isinstance(result, _pr_ready.PrReadiness)
    assert result.ok is True
    assert fetches == [42]
    assert _rows(audit_root, "verifier_settle_timeout") == []


def test_two_poll_budget_still_means_two_thirty_second_wake_ups(
    monkeypatch: pytest.MonkeyPatch, audit_root: Path, clock: _Clock
) -> None:
    monkeypatch.setenv("ALAN_VERIFIER_SETTLE_MAX_POLLS", "2")
    _sequenced_blockers(monkeypatch, [_pending(""), _pending("")])
    _settle(_pending(""))
    assert clock.sleeps == [30.0, 30.0]
    assert _rows(audit_root, "verifier_settle_timeout")[0]["polls"] == 2


def test_exhausted_deadline_before_max_polls_ends_the_wait(
    monkeypatch: pytest.MonkeyPatch, audit_root: Path, clock: _Clock
) -> None:
    monkeypatch.setenv("ALAN_VERIFIER_SETTLE_MAX_POLLS", "3")
    _sequenced_head_shas(monkeypatch, ["sha-1"] * 3)
    _sequenced_probe(monkeypatch, [[_PENDING_RUN]] * 3, clock=clock, cost_s=45.0)
    monkeypatch.setattr(_pr_ready, "check_pr_verifier_blockers", _never_fetch)
    _settle(_pending())
    assert clock.sleeps == [30.0, 15.0]
    assert _rows(audit_root, "verifier_settle_timeout")[0]["polls"] == 2


@pytest.mark.parametrize(
    ("poll", "expected"),
    [(0, 30.0), (1, 30.0), (2, 60.0), (3, 120.0), (4, 240.0), (5, 300.0), (50, 300.0)],
)
def test_settle_sleep_caps_at_ten_times_the_interval(poll: int, expected: float) -> None:
    assert _verification_gates._settle_sleep_s(poll, 30.0) == expected  # noqa: SLF001


def test_next_sleep_is_zero_for_a_non_positive_interval_regardless_of_deadline(
    clock: _Clock,
) -> None:
    assert _verification_gates._settle_next_sleep_s(3, 0.0, clock.now - 1.0) == 0.0  # noqa: SLF001
    assert _verification_gates._settle_next_sleep_s(3, -1.0, clock.now - 1.0) == 0.0  # noqa: SLF001


def test_next_sleep_is_none_once_the_deadline_has_passed(clock: _Clock) -> None:
    assert _verification_gates._settle_next_sleep_s(0, 30.0, clock.now) is None  # noqa: SLF001
    assert _verification_gates._settle_next_sleep_s(0, 30.0, clock.now + 10.0) == 10.0  # noqa: SLF001


def _fetch_exit(readiness: _pr_ready.ReadinessResult, poll: int) -> bool:
    return _verification_gates._settle_fetch_exit(42, "t", readiness, poll)  # noqa: SLF001


def test_fetch_exit_transient_writes_the_transient_row_with_the_raw_poll_index(
    audit_root: Path,
) -> None:
    transient = _pr_ready.ReadinessUnavailable("x")
    assert _fetch_exit(transient, 4) is True
    assert _rows(audit_root) == [{
        "task_id": "t", "action": "verifier_settle_transient", "pr_number": 42, "poll": 4,
    }]


def test_fetch_exit_keeps_waiting_and_writes_nothing_while_checks_are_pending(
    audit_root: Path,
) -> None:
    assert _fetch_exit(_pending(), 4) is False
    assert _rows(audit_root) == []


def test_fetch_exit_green_writes_the_green_row_with_poll_plus_one_and_no_failures(
    audit_root: Path,
) -> None:
    assert _fetch_exit(_green(), 4) is True
    assert _rows(audit_root) == [{
        "task_id": "t", "action": "verifier_settle_green", "pr_number": 42, "poll": 5,
        "failed": [],
    }]


def test_fetch_exit_fail_writes_the_fail_row_with_poll_plus_one_and_the_reasons(
    audit_root: Path,
) -> None:
    failed = _pr_ready.PrReadiness(
        ok=False, merge_state_status="BLOCKED", review_decision="APPROVED",
        failing_checks=("ci", "e2e"),
        failed_reasons=("failing checks: ci", "failing checks: e2e"),
    )
    assert _fetch_exit(failed, 0) is True
    assert _rows(audit_root) == [{
        "task_id": "t", "action": "verifier_settle_fail", "pr_number": 42, "poll": 1,
        "failed": ["failing checks: ci", "failing checks: e2e"],
    }]


@pytest.mark.parametrize(
    "action",
    [
        _verification_gates._SETTLE_PROBE_UNAVAILABLE_ACTION,  # noqa: SLF001
        _verification_gates._SETTLE_HEAD_MOVED_ACTION,  # noqa: SLF001
    ],
)
def test_settle_probe_actions_are_not_triage_failure_lines(action: str) -> None:
    assert action in {"verifier_settle_probe_unavailable", "verifier_settle_head_moved"}
    assert triage_pregate._is_failure_line(action) is False  # noqa: SLF001
