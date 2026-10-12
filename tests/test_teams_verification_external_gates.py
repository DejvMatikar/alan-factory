from __future__ import annotations

import json
from pathlib import Path

import pytest

import alan.teams._verification_gates as _verification_gates
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.teams import _pr_branch, _pr_flake_ledger, _pr_ready, _pr_state_paths
from alan.teams import verification as verif_mod
from alan.teams.verification import (
    TEAM,
    suggestions_for_verdict,
)
from alan.workspace import Workspace
from tests import _teams_verification_support as support
from tests._helpers import seed_and_run

_WIDGET_FORGE = support._WIDGET_FORGE
_E2E_GATE = support._E2E_GATE
_patch_streaming = support.patch_streaming
_impl_handover = support.impl_handover
_blocked_readiness = support.blocked_readiness
_flaky_gate_readiness = support.flaky_gate_readiness


@pytest.fixture(autouse=True)
def _stub_external_gates_default(monkeypatch: pytest.MonkeyPatch) -> None:
    support.stub_external_gates(monkeypatch)


@pytest.fixture(autouse=True)
def _stub_checkout_internals_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    support.stub_checkout_internals(monkeypatch, tmp_path)


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return support.make_workspace(tmp_path)


@pytest.fixture
def audit_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return support.set_audit_root(tmp_path, monkeypatch)


def _ok_readiness() -> _pr_ready.PrReadiness:
    return _pr_ready.PrReadiness(
        ok=True,
        merge_state_status="CLEAN",
        review_decision="APPROVED",
    )


def test_external_gates_downgrade_flips_verdict_and_routes_to_fix(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path
) -> None:
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    _patch_streaming(monkeypatch)
    monkeypatch.setattr(
        _pr_ready, "check_pr_verifier_blockers", lambda _p, _t, **_kw: _blocked_readiness()
    )
    prior = _impl_handover(workspace)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert handover.to_team_suggestions == ["implementation_fix"]
    assert "External Gate Status" in handover.full_synthesis
    assert "mergeStateStatus=BLOCKED" in handover.full_synthesis
    entries = [
        json.loads(line)
        for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
    ]
    assert any(
        e["action"] == "dry_run" and e["event"] == "REQUEST_CHANGES" for e in entries
    )


def test_external_gates_ok_keeps_approve_routing(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    _patch_streaming(monkeypatch)
    monkeypatch.setattr(
        _pr_ready, "check_pr_verifier_blockers", lambda _p, _t, **_kw: _ok_readiness()
    )
    prior = _impl_handover(workspace)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert handover.to_team_suggestions == ["pr_merge"]
    assert "External Gate Status" not in handover.full_synthesis


def test_external_gates_transient_failure_does_not_downgrade(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    transient = _pr_ready.ReadinessUnavailable("gh not installed")
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    _patch_streaming(monkeypatch)
    monkeypatch.setattr(
        _pr_ready, "check_pr_verifier_blockers", lambda _p, _t, **_kw: transient
    )
    prior = _impl_handover(workspace)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert handover.to_team_suggestions == ["pr_merge"]


def test_external_gates_check_skipped_when_no_pr(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    _patch_streaming(monkeypatch)
    called = {"n": 0}

    def fake(*_a: object, **_k: object) -> _pr_ready.PrReadiness:
        called["n"] += 1
        return _blocked_readiness()

    monkeypatch.setattr(_pr_ready, "check_pr_verifier_blockers", fake)
    prior = _impl_handover(workspace, pr_number=None)
    seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert called["n"] == 0


def test_external_gates_check_skipped_when_criticals_present(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    judge = "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 1\n"
    _patch_streaming(monkeypatch, judge_output=judge)
    called = {"n": 0}

    def fake(*_a: object, **_k: object) -> _pr_ready.PrReadiness:
        called["n"] += 1
        return _blocked_readiness()

    monkeypatch.setattr(_pr_ready, "check_pr_verifier_blockers", fake)
    prior = _impl_handover(workspace)
    seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert called["n"] == 0


def test_external_gates_resolved_when_request_changes_zero_criticals(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    judge = "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 0\n"
    _patch_streaming(monkeypatch, judge_output=judge)
    called = {"n": 0}

    def fake(*_a: object, **_k: object) -> _pr_ready.PrReadiness:
        called["n"] += 1
        return _blocked_readiness()

    monkeypatch.setattr(_pr_ready, "check_pr_verifier_blockers", fake)
    prior = _impl_handover(workspace)
    seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert called["n"] == 1


def test_maybe_downgrade_helper_pr_none_returns_unchanged() -> None:
    judge = "VERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 0\n"
    result = verif_mod.resolve_external_gates(
        judge, None, "t", forge_target=DEFAULT_FORGE_TARGET
    )
    assert result.output == judge
    assert result.downgraded is False
    assert result.flaky_only is False


def test_maybe_downgrade_rewrites_decorated_verdict_cleanly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _pr_ready, "check_pr_verifier_blockers", lambda _p, _t, **_kw: _blocked_readiness()
    )
    judge = "## Verdict\n\n**VERDICT: APPROVE**\n\n`DIFF_FINDINGS_CRITICAL: 0`\n"
    result = verif_mod.resolve_external_gates(
        judge, 252, "t", forge_target=DEFAULT_FORGE_TARGET
    )
    assert result.downgraded is True
    assert result.flaky_only is False
    out = result.output
    assert "VERDICT: REQUEST_CHANGES" in out
    assert "**VERDICT" not in out
    assert "REQUEST_CHANGES**" not in out
    assert out.startswith(_verification_gates._GATE_PARK_HEADER_ACTIONABLE)
    assert "PARKED ON EXTERNAL GATES" in out


_JUDGE_APPROVE = "VERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 0\n"


def test_downgrade_with_actionable_checks_opens_with_the_in_scope_banner() -> None:
    out = _verification_gates._downgrade_output(
        _JUDGE_APPROVE, _blocked_readiness(), ("sonar",)
    )
    assert out.startswith(_verification_gates._GATE_PARK_HEADER_ACTIONABLE)
    assert not out.startswith(_verification_gates._GATE_PARK_HEADER)
    assert _verification_gates.GATE_ACTIONABLE_YES in out


def test_downgrade_without_actionable_checks_keeps_the_original_banner() -> None:
    out = _verification_gates._downgrade_output(_JUDGE_APPROVE, _blocked_readiness(), ())
    assert out.startswith(_verification_gates._GATE_PARK_HEADER)
    assert _verification_gates.GATE_ACTIONABLE_NO in out


def test_downgrade_for_a_conflicted_branch_only_keeps_the_original_banner() -> None:
    out = _verification_gates._downgrade_output(
        _JUDGE_APPROVE, support.dirty_readiness(), ()
    )
    assert out.startswith(_verification_gates._GATE_PARK_HEADER)
    assert _verification_gates.GATE_ACTIONABLE_YES in out


def test_in_scope_banner_drops_the_not_a_code_quality_rejection_wording() -> None:
    assert "not a code-quality rejection" not in _verification_gates._GATE_PARK_HEADER_ACTIONABLE
    assert "not a code-quality rejection" in _verification_gates._GATE_PARK_HEADER
    assert "in scope for the fix round" in _verification_gates._GATE_PARK_HEADER_ACTIONABLE
    assert _verification_gates._GATE_PARK_HEADER_ACTIONABLE.startswith(
        "> PARKED ON EXTERNAL GATES"
    )


def test_in_scope_banner_now_states_it_posts_a_blocking_change_request() -> None:
    assert (
        "Posted as a blocking change request"
        in _verification_gates._GATE_PARK_HEADER_ACTIONABLE
    )
    assert "non-blocking comment" not in _verification_gates._GATE_PARK_HEADER_ACTIONABLE
    assert "non-blocking comment" in _verification_gates._GATE_PARK_HEADER


def _patch_rerun(
    monkeypatch: pytest.MonkeyPatch, *, count: int, rerun_ok: bool,
    settle: _pr_ready.PrReadiness,
) -> dict[str, int]:
    calls = {"rerun": 0, "bump": 0, "record": 0}
    exhausted = count >= _pr_flake_ledger.max_gate_reruns()

    def _rerun(*_a: object, **_k: object) -> bool:
        calls["rerun"] += 1
        return rerun_ok

    def _bump(_pr: int, _f: object = None, **_kw: object) -> int:
        calls["bump"] += 1
        return count + 1

    def _record(*_a: object, **_k: object) -> None:
        calls["record"] += 1

    monkeypatch.setattr(_pr_flake_ledger, "gate_reruns_exhausted", lambda *_a, **_kw: exhausted)
    monkeypatch.setattr(_pr_branch, "rerun_external_gate", _rerun)
    monkeypatch.setattr(_pr_state_paths, "bump_gate_rerun_count", _bump)
    monkeypatch.setattr(_pr_flake_ledger, "record_flake_occurrence", _record)
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_k: "sha-1")
    monkeypatch.setattr(_pr_flake_ledger, "failing_e2e_spec_names", lambda *_a, **_k: {})
    monkeypatch.setattr(_verification_gates, "_settle_wait", lambda *_a, **_k: settle)
    return calls


def test_rerun_captures_failing_specs_before_rerun_and_threads_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    received: dict[str, object] = {}

    monkeypatch.setattr(
        _pr_ready, "check_pr_verifier_blockers",
        lambda _p, _t, **_kw: _flaky_gate_readiness(),
    )
    monkeypatch.setattr(_pr_flake_ledger, "gate_reruns_exhausted", lambda *_a, **_kw: False)
    monkeypatch.setattr(
        _pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_k: "sha-1"
    )

    def _fetch_specs(*_a: object, **_k: object) -> dict[str, tuple[str, ...]]:
        order.append("fetch_specs")
        return {_E2E_GATE: ("e2e/chatty.spec.ts",)}

    def _rerun(*_a: object, **_k: object) -> bool:
        order.append("rerun")
        return True

    def _record(*_a: object, **_k: object) -> None:
        received["rescued_specs"] = _k.get("rescued_specs")

    monkeypatch.setattr(_pr_flake_ledger, "failing_e2e_spec_names", _fetch_specs)
    monkeypatch.setattr(_pr_branch, "rerun_external_gate", _rerun)
    monkeypatch.setattr(_pr_state_paths, "bump_gate_rerun_count", lambda *_a, **_k: 1)
    monkeypatch.setattr(_pr_flake_ledger, "record_flake_occurrence", _record)
    monkeypatch.setattr(_verification_gates, "_settle_wait", lambda *_a, **_k: _ok_readiness())
    judge = "VERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 0\n"
    verif_mod.resolve_external_gates(
        judge, 252, "t", forge_target=DEFAULT_FORGE_TARGET
    )
    assert order == ["fetch_specs", "rerun"]
    assert received["rescued_specs"] == {_E2E_GATE: ("e2e/chatty.spec.ts",)}


def test_flaky_gate_rerun_settles_green_keeps_approve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _pr_ready, "check_pr_verifier_blockers",
        lambda _p, _t, **_kw: _flaky_gate_readiness(),
    )
    calls = _patch_rerun(monkeypatch, count=0, rerun_ok=True, settle=_ok_readiness())
    judge = "VERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 0\n"
    result = verif_mod.resolve_external_gates(
        judge, 252, "t", forge_target=DEFAULT_FORGE_TARGET
    )
    assert result.downgraded is False
    assert result.output == judge
    assert calls == {"rerun": 1, "bump": 1, "record": 1}


def test_flaky_gate_rerun_settles_still_failed_records_nothing_and_downgrades(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _pr_ready, "check_pr_verifier_blockers",
        lambda _p, _t, **_kw: _flaky_gate_readiness(),
    )
    calls = _patch_rerun(
        monkeypatch, count=0, rerun_ok=True, settle=_flaky_gate_readiness()
    )
    survival_calls: list[tuple[int, tuple[str, ...], str]] = []

    def _record_survival(
        pr_number: int, survived: tuple[str, ...], head_sha: str, **_k: object
    ) -> None:
        survival_calls.append((pr_number, survived, head_sha))

    monkeypatch.setattr(_pr_flake_ledger, "record_flake_survival", _record_survival)
    judge = "VERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 0\n"
    result = verif_mod.resolve_external_gates(
        judge, 252, "t", forge_target=DEFAULT_FORGE_TARGET
    )
    assert result.downgraded is True
    assert result.flaky_only is True
    assert "VERDICT: REQUEST_CHANGES" in result.output
    assert calls == {"rerun": 1, "bump": 1, "record": 0}
    assert survival_calls == [(252, (_E2E_GATE,), "sha-1")]


def test_flaky_gate_rerun_mid_budget_still_failed_routes_pr_merge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _pr_ready, "check_pr_verifier_blockers",
        lambda _p, _t, **_kw: _flaky_gate_readiness(),
    )
    calls = _patch_rerun(
        monkeypatch, count=1, rerun_ok=True, settle=_flaky_gate_readiness()
    )
    monkeypatch.setattr(_pr_flake_ledger, "record_flake_survival", lambda *_a, **_kw: None)
    judge = "VERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 0\n"
    result = verif_mod.resolve_external_gates(
        judge, 252, "t", forge_target=DEFAULT_FORGE_TARGET
    )
    assert result.downgraded is True
    assert result.flaky_only is True
    assert calls["rerun"] == 1
    assert suggestions_for_verdict(
        result.output, 252, gate_flaky_only=result.flaky_only
    ) == ("pr_merge",)


def test_flaky_gate_rerun_head_sha_mismatch_records_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _pr_ready, "check_pr_verifier_blockers",
        lambda _p, _t, **_kw: _flaky_gate_readiness(),
    )
    calls = _patch_rerun(monkeypatch, count=0, rerun_ok=True, settle=_ok_readiness())
    sha_seq = iter(["sha-1", "sha-2"])
    monkeypatch.setattr(
        _pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_k: next(sha_seq)
    )
    judge = "VERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 0\n"
    result = verif_mod.resolve_external_gates(
        judge, 252, "t", forge_target=DEFAULT_FORGE_TARGET
    )
    assert result.downgraded is False
    assert result.output == judge
    assert calls == {"rerun": 1, "bump": 1, "record": 0}


def test_flaky_gate_rerun_fetches_head_sha_before_trigger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _pr_ready, "check_pr_verifier_blockers",
        lambda _p, _t, **_kw: _flaky_gate_readiness(),
    )
    monkeypatch.setattr(_pr_flake_ledger, "gate_reruns_exhausted", lambda *_a, **_kw: False)
    order: list[str] = []

    def _sha(*_a: object, **_k: object) -> str:
        order.append("sha")
        return "sha-1"

    def _rerun(*_a: object, **_k: object) -> bool:
        order.append("rerun")
        return True

    def _bump(_pr: int, _f: object = None, **_kw: object) -> int:
        order.append("bump")
        return 1

    def _settle(*_a: object, **_k: object) -> _pr_ready.PrReadiness:
        order.append("settle")
        return _ok_readiness()

    def _record(*_a: object, **_k: object) -> None:
        order.append("record")

    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", _sha)
    monkeypatch.setattr(_pr_branch, "rerun_external_gate", _rerun)
    monkeypatch.setattr(_pr_state_paths, "bump_gate_rerun_count", _bump)
    monkeypatch.setattr(_verification_gates, "_settle_wait", _settle)
    monkeypatch.setattr(_pr_flake_ledger, "record_flake_occurrence", _record)
    judge = "VERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 0\n"
    verif_mod.resolve_external_gates(
        judge, 252, "t", forge_target=DEFAULT_FORGE_TARGET
    )
    assert order == ["sha", "rerun", "bump", "settle", "sha", "record"]


def test_flaky_gate_rerun_capped_skips_rerun_and_downgrades(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _pr_ready, "check_pr_verifier_blockers",
        lambda _p, _t, **_kw: _flaky_gate_readiness(),
    )
    calls = _patch_rerun(monkeypatch, count=2, rerun_ok=True, settle=_ok_readiness())
    judge = "VERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 0\n"
    result = verif_mod.resolve_external_gates(
        judge, 252, "t", forge_target=DEFAULT_FORGE_TARGET
    )
    assert result.downgraded is True
    assert result.flaky_only is True
    assert calls == {"rerun": 0, "bump": 0, "record": 0}


def test_flaky_gate_rerun_trigger_fails_no_bump_downgrades(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _pr_ready, "check_pr_verifier_blockers",
        lambda _p, _t, **_kw: _flaky_gate_readiness(),
    )
    calls = _patch_rerun(monkeypatch, count=0, rerun_ok=False, settle=_ok_readiness())
    judge = "VERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 0\n"
    result = verif_mod.resolve_external_gates(
        judge, 252, "t", forge_target=DEFAULT_FORGE_TARGET
    )
    assert result.downgraded is True
    assert result.flaky_only is True
    assert calls == {"rerun": 1, "bump": 0, "record": 0}


def test_flaky_gates_eligible_mixed_failing_is_ineligible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*_a: object, **_kw: object) -> bool:
        raise AssertionError("must not check exhaustion when not flaky-only")

    monkeypatch.setattr(_pr_flake_ledger, "gate_reruns_exhausted", boom)
    mixed = _flaky_gate_readiness(
        failing=(_E2E_GATE, "SonarCloud (blocking quality gate)")
    )
    assert (
        _verification_gates._flaky_gates_eligible(1, mixed, "sha-1", DEFAULT_FORGE_TARGET)
        is False
    )


def test_flaky_gates_eligible_dirty_is_ineligible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*_a: object, **_kw: object) -> bool:
        raise AssertionError("must not check exhaustion when not flaky-only")

    monkeypatch.setattr(_pr_flake_ledger, "gate_reruns_exhausted", boom)
    dirty = _flaky_gate_readiness(merge_state="DIRTY")
    assert (
        _verification_gates._flaky_gates_eligible(1, dirty, "sha-1", DEFAULT_FORGE_TARGET)
        is False
    )


def test_flaky_gates_eligible_human_changes_requested_is_ineligible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*_a: object, **_kw: object) -> bool:
        raise AssertionError("must not check exhaustion when not flaky-only")

    monkeypatch.setattr(_pr_flake_ledger, "gate_reruns_exhausted", boom)
    reviewed = _flaky_gate_readiness(review="CHANGES_REQUESTED")
    assert (
        _verification_gates._flaky_gates_eligible(
            1, reviewed, "sha-1", DEFAULT_FORGE_TARGET
        )
        is False
    )


def test_flaky_gates_eligible_no_failing_is_ineligible() -> None:
    assert (
        _verification_gates._flaky_gates_eligible(
            1, _ok_readiness(), "sha-1", DEFAULT_FORGE_TARGET
        )
        is False
    )


def test_flaky_gates_eligible_happy_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_pr_flake_ledger, "gate_reruns_exhausted", lambda *_a, **_kw: False)
    assert (
        _verification_gates._flaky_gates_eligible(
            1, _flaky_gate_readiness(), "sha-1", DEFAULT_FORGE_TARGET
        )
        is True
    )


def test_flaky_gates_eligible_uses_forge_scoped_rerun_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[int, object, object]] = []

    def _fake_exhausted(pr: int, head_sha: object, *, forge_target: object) -> bool:
        seen.append((pr, head_sha, forge_target))
        return True

    monkeypatch.setattr(_pr_flake_ledger, "gate_reruns_exhausted", _fake_exhausted)
    assert (
        _verification_gates._flaky_gates_eligible(
            7, _flaky_gate_readiness(), "sha-9", _WIDGET_FORGE
        )
        is False
    )
    assert seen == [(7, "sha-9", _WIDGET_FORGE)]


def test_local_flaky_gate_constants_removed() -> None:
    assert not hasattr(verif_mod, "_max_gate_reruns")
    assert not hasattr(verif_mod, "_RERUNNABLE_FLAKY_GATES")
    assert not hasattr(verif_mod, "_MAX_GATE_RERUNS_ENV")
    assert not hasattr(verif_mod, "_DEFAULT_MAX_GATE_RERUNS")


def test_external_gate_machinery_not_reexported_from_verification() -> None:
    for name in (
        "_settle_wait",
        "_settle_max_polls",
        "_settle_poll_interval_s",
        "_SETTLE_MAX_POLLS_ENV",
        "_SETTLE_POLL_INTERVAL_ENV",
        "_DEFAULT_SETTLE_MAX_POLLS",
        "_DEFAULT_SETTLE_POLL_INTERVAL_S",
        "_flaky_gates_eligible",
        "_resolve_actionable_gate_checks",
        "_format_gate_block",
        "_gate_actionability_lines",
        "_downgrade_output",
        "_maybe_rerun_flaky_gates",
        "_resolve_gate_readiness",
        "_GATE_PARK_HEADER",
        "_GATE_CHECK_SEPARATOR",
        "_GATE_ACTIONABLE_CHECKS_RE",
        "GateDowngradeResult",
    ):
        assert not hasattr(verif_mod, name), name


def test_flaky_gate_rerun_reached_from_pending_then_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pending = _pr_ready.PrReadiness(
        ok=False, merge_state_status="BLOCKED", review_decision="REVIEW_REQUIRED",
        failing_checks=(), pending_checks=(_E2E_GATE,), failed_reasons=(),
    )
    monkeypatch.setattr(
        _pr_ready, "check_pr_verifier_blockers", lambda _p, _t, **_kw: pending
    )
    settle_seq = [_flaky_gate_readiness(), _ok_readiness()]
    monkeypatch.setattr(_verification_gates, "_settle_wait", lambda *_a, **_k: settle_seq.pop(0))
    calls = {"rerun": 0, "bump": 0, "record": 0}

    def _rerun(*_a: object, **_k: object) -> bool:
        calls["rerun"] += 1
        return True

    def _bump(_pr: int, _f: object = None, **_kw: object) -> int:
        calls["bump"] += 1
        return 1

    def _record(*_a: object, **_k: object) -> None:
        calls["record"] += 1

    monkeypatch.setattr(_pr_flake_ledger, "gate_reruns_exhausted", lambda *_a, **_kw: False)
    monkeypatch.setattr(_pr_branch, "rerun_external_gate", _rerun)
    monkeypatch.setattr(_pr_state_paths, "bump_gate_rerun_count", _bump)
    monkeypatch.setattr(_pr_flake_ledger, "record_flake_occurrence", _record)
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_k: "sha-1")
    judge = "VERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 0\n"
    result = verif_mod.resolve_external_gates(
        judge, 252, "t", forge_target=DEFAULT_FORGE_TARGET
    )
    assert result.downgraded is False
    assert result.output == judge
    assert calls == {"rerun": 1, "bump": 1, "record": 1}


def test_mis_park_regression_new_sha_resets_budget_and_records_survival(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    p = _pr_state_paths.gate_rerun_count_path(252, DEFAULT_FORGE_TARGET)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"head_sha": "sha-A", "count": 2, "lifetime": 2}))
    monkeypatch.setattr(
        _pr_ready, "check_pr_verifier_blockers",
        lambda _p, _t, **_kw: _flaky_gate_readiness(),
    )
    monkeypatch.setattr(_pr_flake_ledger, "max_gate_reruns", lambda: 2)
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_k: "sha-B")
    monkeypatch.setattr(_pr_branch, "rerun_flaky_checks", lambda *_a, **_kw: True)
    monkeypatch.setattr(_pr_flake_ledger, "failing_e2e_spec_names", lambda *_a, **_k: {})
    monkeypatch.setattr(
        _verification_gates, "_settle_wait", lambda *_a, **_k: _flaky_gate_readiness()
    )
    monkeypatch.setattr(_pr_flake_ledger, "diff_attributable_e2e_checks", lambda *_a, **_kw: ())
    judge = "VERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 0\n"
    result = verif_mod.resolve_external_gates(
        judge, 252, "t", forge_target=DEFAULT_FORGE_TARGET
    )
    assert _pr_flake_ledger.load_gate_survived_checks(
        252, DEFAULT_FORGE_TARGET,
    ) == {_E2E_GATE: "sha-B"}
    assert result.flaky_only is False
    assert suggestions_for_verdict(
        result.output, 252, gate_flaky_only=result.flaky_only
    ) == ("implementation_fix",)


def test_gate_survival_same_spec_before_after_records_e2e_check(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    monkeypatch.setattr(
        _pr_ready, "check_pr_verifier_blockers",
        lambda _p, _t, **_kw: _flaky_gate_readiness(),
    )
    calls = _patch_rerun(monkeypatch, count=0, rerun_ok=True, settle=_flaky_gate_readiness())
    monkeypatch.setattr(
        _pr_flake_ledger, "failing_e2e_spec_names",
        lambda *_a, **_k: {_E2E_GATE: ("e2e/chatty.spec.ts",)},
    )
    judge = "VERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 0\n"
    verif_mod.resolve_external_gates(
        judge, 252, "t", forge_target=DEFAULT_FORGE_TARGET
    )
    assert calls["rerun"] == 1
    assert _pr_flake_ledger.load_gate_survived_checks(
        252, DEFAULT_FORGE_TARGET,
    ) == {_E2E_GATE: "sha-1"}


def test_gate_survival_disjoint_specs_drops_e2e_keeps_coverage_check(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    backend = "Backend tests + coverage"
    mixed = _flaky_gate_readiness(failing=(_E2E_GATE, backend))
    monkeypatch.setattr(_pr_ready, "check_pr_verifier_blockers", lambda _p, _t, **_kw: mixed)
    calls = _patch_rerun(monkeypatch, count=0, rerun_ok=True, settle=mixed)
    spec_seq = iter([
        {_E2E_GATE: ("e2e/before.spec.ts",)},
        {_E2E_GATE: ("e2e/after.spec.ts",)},
    ])
    monkeypatch.setattr(
        _pr_flake_ledger, "failing_e2e_spec_names", lambda *_a, **_k: next(spec_seq)
    )
    judge = "VERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 0\n"
    verif_mod.resolve_external_gates(
        judge, 252, "t", forge_target=DEFAULT_FORGE_TARGET
    )
    assert calls["rerun"] == 2
    assert _pr_flake_ledger.load_gate_survived_checks(
        252, DEFAULT_FORGE_TARGET,
    ) == {backend: "sha-1"}


def test_gate_survival_specs_after_empty_falls_back_to_check_level(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    monkeypatch.setattr(
        _pr_ready, "check_pr_verifier_blockers",
        lambda _p, _t, **_kw: _flaky_gate_readiness(),
    )
    _patch_rerun(monkeypatch, count=0, rerun_ok=True, settle=_flaky_gate_readiness())
    spec_seq = iter([{_E2E_GATE: ("e2e/before.spec.ts",)}, {}])
    monkeypatch.setattr(
        _pr_flake_ledger, "failing_e2e_spec_names", lambda *_a, **_k: next(spec_seq)
    )
    judge = "VERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 0\n"
    verif_mod.resolve_external_gates(
        judge, 252, "t", forge_target=DEFAULT_FORGE_TARGET
    )
    assert _pr_flake_ledger.load_gate_survived_checks(
        252, DEFAULT_FORGE_TARGET,
    ) == {_E2E_GATE: "sha-1"}


def test_gate_survival_specs_before_empty_falls_back_to_check_level(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    monkeypatch.setattr(
        _pr_ready, "check_pr_verifier_blockers",
        lambda _p, _t, **_kw: _flaky_gate_readiness(),
    )
    _patch_rerun(monkeypatch, count=0, rerun_ok=True, settle=_flaky_gate_readiness())
    spec_seq = iter([{}, {_E2E_GATE: ("e2e/after.spec.ts",)}])
    monkeypatch.setattr(
        _pr_flake_ledger, "failing_e2e_spec_names", lambda *_a, **_k: next(spec_seq)
    )
    judge = "VERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 0\n"
    verif_mod.resolve_external_gates(
        judge, 252, "t", forge_target=DEFAULT_FORGE_TARGET
    )
    assert _pr_flake_ledger.load_gate_survived_checks(
        252, DEFAULT_FORGE_TARGET,
    ) == {_E2E_GATE: "sha-1"}


def test_gate_survival_specs_before_reused_no_duplicate_before_side_fetch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
    monkeypatch.setattr(
        _pr_ready, "check_pr_verifier_blockers",
        lambda _p, _t, **_kw: _flaky_gate_readiness(),
    )
    _patch_rerun(monkeypatch, count=0, rerun_ok=True, settle=_flaky_gate_readiness())
    fetch_count = {"n": 0}

    def _fetch_specs(*_a: object, **_k: object) -> dict[str, tuple[str, ...]]:
        fetch_count["n"] += 1
        return {_E2E_GATE: ("e2e/chatty.spec.ts",)}

    monkeypatch.setattr(_pr_flake_ledger, "failing_e2e_spec_names", _fetch_specs)
    judge = "VERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 0\n"
    verif_mod.resolve_external_gates(
        judge, 252, "t", forge_target=DEFAULT_FORGE_TARGET
    )
    assert fetch_count["n"] == 2


def test_gate_rerun_bump_still_omits_checks_and_specs_kwargs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _pr_ready, "check_pr_verifier_blockers",
        lambda _p, _t, **_kw: _flaky_gate_readiness(),
    )
    monkeypatch.setattr(_pr_flake_ledger, "gate_reruns_exhausted", lambda *_a, **_kw: False)
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_k: "sha-1")
    monkeypatch.setattr(_pr_branch, "rerun_external_gate", lambda *_a, **_k: True)
    monkeypatch.setattr(_pr_flake_ledger, "record_flake_occurrence", lambda *_a, **_k: None)
    monkeypatch.setattr(_pr_flake_ledger, "failing_e2e_spec_names", lambda *_a, **_k: {})
    monkeypatch.setattr(_verification_gates, "_settle_wait", lambda *_a, **_k: _ok_readiness())
    captured: dict[str, object] = {}

    def _bump(_pr: int, _f: object = None, *, head_sha: object, **kw: object) -> int:
        del head_sha
        captured.update(kw)
        return 1

    monkeypatch.setattr(_pr_state_paths, "bump_gate_rerun_count", _bump)
    judge = "VERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 0\n"
    verif_mod.resolve_external_gates(
        judge, 252, "t", forge_target=DEFAULT_FORGE_TARGET
    )
    assert "checks" not in captured
    assert "specs" not in captured
