from __future__ import annotations

import json
from pathlib import Path

import pytest

from alan.engine_config import ForgeTarget, RepoIdentity
from alan.teams import _pr_flake_ledger, _pr_ready, _pr_state_paths
from alan.teams import _verification_gates as gates
from tests.aggregate_pr_forge import E2E, GATE, LINT, PrForge
from tests.fakes.forge import FakeForge

_HEAD = "e" * 40
_PR = 42
_JUDGE = "VERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 0\n"

_COPY = ForgeTarget(
    repo=RepoIdentity(owner="acme-corp", repo="widget-app"),
    credential_env_name="ACME_WIDGET_GH_TOKEN",
    authoritative_checks=frozenset({GATE}),
    aggregate_checks=frozenset({GATE}),
)
_UNDECLARED = _COPY.model_copy(update={"aggregate_checks": frozenset()})


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    monkeypatch.setenv("ALAN_VERIFIER_SETTLE_POLL_INTERVAL_S", "0")


@pytest.fixture
def forge(_active_fake_forge: FakeForge) -> PrForge:
    return PrForge(_active_fake_forge, head_sha=_HEAD)


def _audit_rows(tmp_path: Path) -> list[dict[str, object]]:
    path = tmp_path / "logs" / "verification_audit.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


def _exhaust(forge_target: ForgeTarget) -> None:
    for _ in range(_pr_flake_ledger.max_gate_reruns()):
        _pr_state_paths.bump_gate_rerun_count(_PR, forge_target, head_sha=_HEAD)


def _red(*failing: str) -> _pr_ready.PrReadiness:
    return _pr_ready.PrReadiness(
        ok=False,
        merge_state_status="BLOCKED",
        review_decision="REVIEW_REQUIRED",
        failing_checks=failing,
        failed_reasons=(f"failing checks: {', '.join(failing)}",),
        head_sha=_HEAD,
        check_count=len(failing) + 1,
    )


def test_flaky_plus_aggregate_red_reruns_and_rescues_to_green(forge: PrForge) -> None:
    forge.script_reads((E2E, GATE), ())
    forge.run_ids = {E2E: 7001, GATE: 8001}
    result = gates.resolve_external_gates(_JUDGE, _PR, "task-x", forge_target=_COPY)
    assert result.downgraded is False
    assert result.output == _JUDGE
    assert forge.rerun_run_ids == [7001]
    assert _pr_flake_ledger.max_gate_reruns() == 2
    assert _pr_state_paths.gate_rerun_count_for_sha(_PR, _COPY, head_sha=_HEAD) == 1
    ledger = _pr_flake_ledger.flake_failures_path(_COPY).read_text()
    assert json.loads(ledger) == {E2E: [_PR]}
    assert GATE not in ledger
    assert gates._flaky_gates_eligible(_PR, _red(E2E), _HEAD, _UNDECLARED) is True


def test_flaky_plus_aggregate_plus_non_allowlisted_not_eligible_and_verdict_names_job(
    forge: PrForge,
) -> None:
    forge.script_reads((E2E, LINT, GATE))
    result = gates.resolve_external_gates(_JUDGE, _PR, "task-x", forge_target=_COPY)
    assert forge.rerun_run_ids == []
    assert result.downgraded is True
    assert result.actionable_checks == (LINT,)
    assert f"{gates.GATE_ACTIONABLE_CHECKS_PREFIX}{LINT}" in result.output
    assert f"{LINT} | {GATE}" not in result.output
    assert f"failing checks: {E2E}, {LINT}, {GATE}" in result.output


def test_flaky_plus_aggregate_exhausted_budget_not_eligible(forge: PrForge) -> None:
    _exhaust(_COPY)
    forge.script_reads((E2E, GATE))
    result = gates.resolve_external_gates(_JUDGE, _PR, "task-x", forge_target=_COPY)
    assert forge.rerun_run_ids == []
    assert result.downgraded is True
    assert result.flaky_only is True
    assert result.actionable_checks == ()


def test_post_rerun_settle_poll_reads_through_view(forge: PrForge) -> None:
    forge.script_reads((E2E, GATE), (E2E, GATE))
    settled = gates._resolve_gate_readiness(_PR, "task-x", forge_target=_COPY)
    assert isinstance(settled, _pr_ready.PrReadiness)
    assert forge.rerun_run_ids == [7001]
    assert settled.failing_checks == (E2E,)
    assert settled.failed_reasons == (f"failing checks: {E2E}, {GATE}",)


def _decision(forge_target: ForgeTarget) -> tuple[object, ...]:
    result = gates.resolve_external_gates(_JUDGE, _PR, "task-x", forge_target=forge_target)
    return (
        result.output, result.downgraded, result.flaky_only, result.dirty,
        result.actionable_checks, result.non_actionable,
    )


def test_aggregate_alone_verification_matches_undeclared_target(forge: PrForge) -> None:
    forge.script_reads((GATE,))
    declared = _decision(_COPY)
    undeclared = _decision(_UNDECLARED)
    assert declared == undeclared
    assert declared[1] is True
    assert declared[4] == (GATE,)
    assert forge.rerun_run_ids == []


def test_gate_path_audit_omits_set_aside_aggregate(forge: PrForge, tmp_path: Path) -> None:
    forge.script_reads((E2E, LINT, GATE))
    gates.resolve_external_gates(_JUDGE, _PR, "task-x", forge_target=_COPY)
    [row] = [r for r in _audit_rows(tmp_path) if r["action"] == "gate_path"]
    assert row["failing_checks"] == [E2E, LINT]


def test_rerun_request_names_only_flaky_job_once_per_run(forge: PrForge) -> None:
    forge.script_reads((E2E, GATE), ())
    gates.resolve_external_gates(_JUDGE, _PR, "task-x", forge_target=_COPY)
    assert forge.rerun_run_ids == [7001]


def test_flaky_plus_aggregate_on_undeclared_target_is_not_rerun(forge: PrForge) -> None:
    forge.script_reads((E2E, GATE))
    result = gates.resolve_external_gates(_JUDGE, _PR, "task-x", forge_target=_UNDECLARED)
    assert forge.rerun_run_ids == []
    assert result.downgraded is True
    assert result.actionable_checks == (GATE,)
