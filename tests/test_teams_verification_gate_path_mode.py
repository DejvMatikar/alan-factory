from __future__ import annotations

import json
import time as _time_mod
from pathlib import Path
from typing import Any

import pytest

import alan.teams._verification_gates as _verification_gates
from alan.engine_config import DEFAULT_FORGE_TARGET, ForgeTarget
from alan.teams import (
    _pr_branch,
    _pr_flake_ledger,
    _pr_post,
    _pr_ready,
    _pr_state_paths,
)
from alan.teams.verification import TEAM
from alan.workspace import Workspace
from tests import _teams_verification_support as support
from tests._helpers import seed_and_run
from tests.fakes.forge import FakeForge
from tests.forge_stubs import ForgeScript, api_result

_E2E_GATE = support._E2E_GATE
_patch_streaming = support.patch_streaming
_impl_handover = support.impl_handover
_flaky_gate_readiness = support.flaky_gate_readiness


@pytest.fixture
def forge(_active_fake_forge: FakeForge) -> ForgeScript:
    return ForgeScript(_active_fake_forge)


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


def _audit_entries(audit_root: Path) -> list[dict[str, Any]]:
    log_path = audit_root / "verification_audit.jsonl"
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]


def _gate_path_rows(audit_root: Path) -> list[dict[str, Any]]:
    return [e for e in _audit_entries(audit_root) if e["action"] == "gate_path"]


def test_rescue_only_reruns_flaky_gate(
    monkeypatch: pytest.MonkeyPatch, audit_root: Path,
) -> None:
    monkeypatch.setattr(
        _pr_ready, "check_pr_verifier_blockers",
        lambda _p, _t, **_kw: _flaky_gate_readiness(),
    )
    monkeypatch.setattr(_pr_flake_ledger, "gate_reruns_exhausted", lambda *_a, **_kw: False)
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_k: "sha-1")
    monkeypatch.setattr(_pr_flake_ledger, "failing_e2e_spec_names", lambda *_a, **_k: {})
    calls = {"rerun": 0}

    def _rerun(*_a: object, **_k: object) -> bool:
        calls["rerun"] += 1
        return True

    monkeypatch.setattr(_pr_branch, "rerun_external_gate", _rerun)
    monkeypatch.setattr(_pr_state_paths, "bump_gate_rerun_count", lambda *_a, **_k: 1)
    monkeypatch.setattr(_pr_flake_ledger, "record_flake_survival", lambda *_a, **_k: None)
    monkeypatch.setattr(
        _verification_gates, "_settle_wait", lambda *_a, **_k: _flaky_gate_readiness()
    )
    judge = "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 0\n"
    result = _verification_gates.resolve_external_gates(
        judge, 252, "t", forge_target=DEFAULT_FORGE_TARGET,
    )
    assert calls["rerun"] == 1
    assert result.output is judge
    assert result.downgraded is False
    assert result.flaky_only is False
    assert _verification_gates.GATE_BLOCK_MARKER not in result.output
    rows = _gate_path_rows(audit_root)
    assert len(rows) == 1
    assert rows[0]["mode"] == "rescue_only"
    assert _E2E_GATE in rows[0]["failing_checks"]


def test_rescue_only_settle_wait_polls_pending_checks(
    monkeypatch: pytest.MonkeyPatch, audit_root: Path,
) -> None:
    monkeypatch.setenv("ALAN_VERIFIER_SETTLE_MAX_POLLS", "3")
    monkeypatch.setattr(_time_mod, "sleep", lambda _: None)
    calls: dict[str, int] = {"n": 0}

    def fake_blockers(pr: int, tid: str, **_kw: object) -> _pr_ready.PrReadiness:  # noqa: ARG001
        calls["n"] += 1
        if calls["n"] == 1:
            return _pr_ready.PrReadiness(
                ok=False, merge_state_status="BLOCKED", review_decision="REVIEW_REQUIRED",
                pending_checks=(_E2E_GATE,),
            )
        return _flaky_gate_readiness()

    monkeypatch.setattr(_pr_ready, "check_pr_verifier_blockers", fake_blockers)
    monkeypatch.setattr(_pr_flake_ledger, "gate_reruns_exhausted", lambda *_a, **_kw: False)
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_k: "sha-1")
    monkeypatch.setattr(_pr_flake_ledger, "failing_e2e_spec_names", lambda *_a, **_k: {})
    rerun_calls = {"n": 0}

    def _rerun(*_a: object, **_k: object) -> bool:
        rerun_calls["n"] += 1
        return True

    monkeypatch.setattr(_pr_branch, "rerun_external_gate", _rerun)
    monkeypatch.setattr(_pr_state_paths, "bump_gate_rerun_count", lambda *_a, **_k: 1)
    monkeypatch.setattr(_pr_flake_ledger, "record_flake_survival", lambda *_a, **_k: None)
    judge = "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 0\n"
    result = _verification_gates.resolve_external_gates(
        judge, 252, "t", forge_target=DEFAULT_FORGE_TARGET,
    )
    assert result.output is judge
    assert result.downgraded is False
    entries = _audit_entries(audit_root)
    assert any(e["action"] == "verifier_settle_poll" and e["task_id"] == "t" for e in entries)
    assert rerun_calls["n"] == 1


def test_rescue_only_never_routes_to_pr_merge(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path,  # noqa: ARG001
    forge: ForgeScript,
) -> None:
    monkeypatch.delenv("ALAN_VERIFICATION_DRY_RUN", raising=False)
    monkeypatch.setattr(_time_mod, "sleep", lambda _: None)
    judge = "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 0\n"
    _patch_streaming(monkeypatch, judge_output=judge)
    monkeypatch.setattr(
        _pr_ready, "check_pr_verifier_blockers",
        lambda _p, _t, **_kw: _flaky_gate_readiness(),
    )
    monkeypatch.setattr(_pr_flake_ledger, "gate_reruns_exhausted", lambda *_a, **_kw: False)
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_k: "sha-1")
    monkeypatch.setattr(_pr_flake_ledger, "failing_e2e_spec_names", lambda *_a, **_k: {})
    monkeypatch.setattr(_pr_branch, "rerun_external_gate", lambda *_a, **_k: True)
    monkeypatch.setattr(_pr_state_paths, "bump_gate_rerun_count", lambda *_a, **_k: 1)
    monkeypatch.setattr(_pr_flake_ledger, "record_flake_survival", lambda *_a, **_k: None)
    forge.queue("post_review", api_result("{}"))
    prior = _impl_handover(workspace)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert handover.to_team_suggestions == ["implementation_fix"]
    assert [p["event"] for p in forge.review_payloads()] == ["REQUEST_CHANGES"]


def test_no_audit_row_without_pr_number(
    monkeypatch: pytest.MonkeyPatch, audit_root: Path,
) -> None:
    called = {"n": 0}

    def boom(*_a: object, **_k: object) -> _pr_ready.PrReadiness:
        called["n"] += 1
        raise AssertionError("must not resolve readiness without a pr_number")

    monkeypatch.setattr(_pr_ready, "check_pr_verifier_blockers", boom)
    judge = "VERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 0\n"
    result = _verification_gates.resolve_external_gates(
        judge, None, "t", forge_target=DEFAULT_FORGE_TARGET,
    )
    assert result.output == judge
    assert result.downgraded is False
    assert called["n"] == 0
    assert _gate_path_rows(audit_root) == []


def test_skip_mode_makes_no_gh_call(
    monkeypatch: pytest.MonkeyPatch, audit_root: Path,
) -> None:
    called = {"n": 0}

    def fake(*_a: object, **_k: object) -> _pr_ready.PrReadiness:
        called["n"] += 1
        return _flaky_gate_readiness()

    monkeypatch.setattr(_pr_ready, "check_pr_verifier_blockers", fake)
    judge = "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 1\n"
    result = _verification_gates.resolve_external_gates(
        judge, 252, "t", forge_target=DEFAULT_FORGE_TARGET,
    )
    assert called["n"] == 0
    assert result.output == judge
    assert result.downgraded is False
    rows = _gate_path_rows(audit_root)
    assert len(rows) == 1
    assert rows[0]["mode"] == "skip"
    assert "gh_ok" not in rows[0]
    assert "failing_checks" not in rows[0]
    assert "pending_checks" not in rows[0]


def test_unparseable_verdict_classifies_skip(
    monkeypatch: pytest.MonkeyPatch, audit_root: Path,  # noqa: ARG001
) -> None:
    assert _verification_gates._gate_path_mode("no verdict here\n") == "skip"  # noqa: SLF001
    called = {"n": 0}

    def fake(*_a: object, **_k: object) -> _pr_ready.PrReadiness:
        called["n"] += 1
        return _flaky_gate_readiness()

    monkeypatch.setattr(_pr_ready, "check_pr_verifier_blockers", fake)
    result = _verification_gates.resolve_external_gates(
        "no verdict here\n", 252, "t", forge_target=DEFAULT_FORGE_TARGET,
    )
    assert called["n"] == 0
    assert result.downgraded is False


def test_in_scope_override_classifies_rescue_only() -> None:
    judge = (
        "VERDICT: REQUEST_CHANGES\n"
        "DIFF_FINDINGS_CRITICAL: 2\n"
        "DIFF_FINDINGS_CRITICAL_IN_SCOPE: 0\n"
    )
    assert _verification_gates._gate_path_mode(judge) == "rescue_only"  # noqa: SLF001


def test_rescue_only_transient_gh_returns_unchanged(
    monkeypatch: pytest.MonkeyPatch, audit_root: Path,
) -> None:
    transient = _pr_ready.ReadinessUnavailable("gh not installed")
    monkeypatch.setattr(_pr_ready, "check_pr_verifier_blockers", lambda _p, _t, **_kw: transient)
    called = {"n": 0}

    def _rerun(*_a: object, **_k: object) -> bool:
        called["n"] += 1
        return True

    monkeypatch.setattr(_pr_branch, "rerun_external_gate", _rerun)
    judge = "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 0\n"
    result = _verification_gates.resolve_external_gates(
        judge, 252, "t", forge_target=DEFAULT_FORGE_TARGET,
    )
    assert result.output is judge
    assert result.downgraded is False
    assert called["n"] == 0
    rows = _gate_path_rows(audit_root)
    assert len(rows) == 1
    assert rows[0]["mode"] == "rescue_only"
    assert "gh_ok" not in rows[0]
    assert "failing_checks" not in rows[0]
    assert "pending_checks" not in rows[0]


def test_rescue_only_unavailable_settle_writes_gate_path_row_with_gh_ok_false_and_empty_lists(
    monkeypatch: pytest.MonkeyPatch, audit_root: Path,
) -> None:
    monkeypatch.setattr(
        _pr_ready, "check_pr_verifier_blockers",
        lambda _p, _t, **_kw: _flaky_gate_readiness(),
    )
    monkeypatch.setattr(_pr_flake_ledger, "gate_reruns_exhausted", lambda *_a, **_kw: False)
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_k: "sha-1")
    monkeypatch.setattr(_pr_flake_ledger, "failing_e2e_spec_names", lambda *_a, **_k: {})
    monkeypatch.setattr(_pr_branch, "rerun_external_gate", lambda *_a, **_k: True)
    monkeypatch.setattr(_pr_state_paths, "bump_gate_rerun_count", lambda *_a, **_k: 1)
    monkeypatch.setattr(
        _verification_gates, "_settle_wait",
        lambda *_a, **_k: _pr_ready.ReadinessUnavailable("timeout"),
    )
    judge = "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 0\n"
    result = _verification_gates.resolve_external_gates(
        judge, 252, "t", forge_target=DEFAULT_FORGE_TARGET,
    )
    assert result.output is judge
    assert result.downgraded is False
    [row] = _gate_path_rows(audit_root)
    assert row["mode"] == "rescue_only"
    assert row["gh_ok"] is False
    assert row["failing_checks"] == []
    assert row["pending_checks"] == []


def test_rescue_only_nothing_to_rescue_is_noop(
    monkeypatch: pytest.MonkeyPatch, audit_root: Path,
) -> None:
    clean = _pr_ready.PrReadiness(ok=True, merge_state_status="CLEAN", review_decision="APPROVED")
    monkeypatch.setattr(_pr_ready, "check_pr_verifier_blockers", lambda _p, _t, **_kw: clean)
    judge = "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 0\n"
    result = _verification_gates.resolve_external_gates(
        judge, 252, "t", forge_target=DEFAULT_FORGE_TARGET,
    )
    assert result.output is judge
    assert result.downgraded is False
    rows = _gate_path_rows(audit_root)
    assert len(rows) == 1
    assert rows[0]["mode"] == "rescue_only"
    assert rows[0]["failing_checks"] == []
    assert rows[0]["pending_checks"] == []


def test_rescue_only_non_actionable_flag_stays_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _pr_ready, "check_pr_verifier_blockers",
        lambda _p, _t, **_kw: _flaky_gate_readiness(),
    )
    monkeypatch.setattr(_pr_flake_ledger, "gate_reruns_exhausted", lambda *_a, **_kw: True)
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_k: "sha-1")
    result = _verification_gates.resolve_external_gates(
        "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 0\n", 252, "t",
        forge_target=DEFAULT_FORGE_TARGET,
    )
    assert result.downgraded is False
    assert result.non_actionable is False
    assert result.actionable_checks == ()


def test_approve_downgrade_mode_classified() -> None:
    assert (
        _verification_gates._gate_path_mode(  # noqa: SLF001
            "VERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 0\n"
        )
        == "downgrade"
    )


_CLEAN_SNAPSHOT = _pr_ready.PrReadiness(ok=True, merge_state_status="CLEAN", review_decision="")


def _install_order_probes(
    monkeypatch: pytest.MonkeyPatch, order: list[str], *, dismissed: bool = True,
) -> None:
    def dismiss(pr: int, task_id: str, *, forge_target: ForgeTarget) -> bool:
        order.append(f"dismiss:{pr}:{task_id}:{forge_target.repo.slug}")
        return dismissed

    def snapshot(_p: int, _t: str, **_kw: object) -> _pr_ready.PrReadiness:
        order.append("snapshot")
        return _CLEAN_SNAPSHOT

    monkeypatch.setattr(_pr_post, "dismiss_stale_verifier_reviews", dismiss)
    monkeypatch.setattr(_pr_ready, "check_pr_verifier_blockers", snapshot)


def test_downgrade_mode_dismisses_stale_verifier_reviews_before_the_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    _install_order_probes(monkeypatch, order)
    result = _verification_gates.resolve_external_gates(
        "VERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 0\n", 252, "t",
        forge_target=DEFAULT_FORGE_TARGET,
    )
    assert result.downgraded is False
    assert order == ["dismiss:252:t:DejvMatikar/alan", "snapshot"]


def test_rescue_only_mode_dismisses_stale_verifier_reviews_before_the_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    _install_order_probes(monkeypatch, order)
    _verification_gates.resolve_external_gates(
        "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 0\n", 252, "t",
        forge_target=DEFAULT_FORGE_TARGET,
    )
    assert order == ["dismiss:252:t:DejvMatikar/alan", "snapshot"]


def test_skip_mode_never_dismisses_stale_verifier_reviews(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    _install_order_probes(monkeypatch, order)
    _verification_gates.resolve_external_gates(
        "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 2\n", 252, "t",
        forge_target=DEFAULT_FORGE_TARGET,
    )
    assert order == []


def test_failed_dismissal_does_not_block_the_readiness_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    _install_order_probes(monkeypatch, order, dismissed=False)
    result = _verification_gates.resolve_external_gates(
        "VERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 0\n", 252, "t",
        forge_target=DEFAULT_FORGE_TARGET,
    )
    assert result.downgraded is False
    assert order == ["dismiss:252:t:DejvMatikar/alan", "snapshot"]
