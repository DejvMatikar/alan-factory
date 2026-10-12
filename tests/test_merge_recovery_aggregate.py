from __future__ import annotations

from pathlib import Path

import pytest

from alan import merge_recovery
from alan.engine_config import ForgeTarget, RepoIdentity
from alan.merge_recovery import MergeBlockClass
from alan.teams import _pr_flake_ledger, _pr_state_paths
from alan.teams._pr_ready import PrReadiness
from alan.teams._pr_sonar import SonarAnnotation
from tests.aggregate_pr_forge import E2E, GATE, LINT, PrForge
from tests.fakes.forge import FakeForge

_SONAR = "Widget Sonar"
_HEAD = "f" * 40
_PR = 42

_COPY = ForgeTarget(
    repo=RepoIdentity(owner="acme-corp", repo="widget-app"),
    credential_env_name="ACME_WIDGET_GH_TOKEN",
    authoritative_checks=frozenset({GATE}),
    aggregate_checks=frozenset({GATE}),
)
_UNDECLARED = _COPY.model_copy(update={"aggregate_checks": frozenset()})
_WITH_SONAR = _COPY.model_copy(update={"authoritative_checks": frozenset({GATE, _SONAR})})
_WITH_SONAR_UNDECLARED = _WITH_SONAR.model_copy(update={"aggregate_checks": frozenset()})


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))


@pytest.fixture
def forge(_active_fake_forge: FakeForge) -> PrForge:
    return PrForge(_active_fake_forge, head_sha=_HEAD)


def _readiness(
    failing: tuple[str, ...], *, merge_state_status: str = "BLOCKED",
) -> PrReadiness:
    reasons = [f"failing checks: {', '.join(failing)}"]
    if merge_state_status != "CLEAN":
        reasons.insert(0, f"mergeStateStatus={merge_state_status}")
    return PrReadiness(
        ok=False,
        merge_state_status=merge_state_status,
        review_decision="APPROVED",
        failing_checks=failing,
        failed_reasons=tuple(reasons),
        head_sha=_HEAD,
        check_count=len(failing) + 1,
    )


def _annotation(title: str = "Make sure this is safe") -> SonarAnnotation:
    return SonarAnnotation(path="widget.py", line=1, title=title, rule_url="")


def test_classify_block_flaky_plus_aggregate_is_transient_flake() -> None:
    readiness = _readiness((E2E, GATE))
    assert merge_recovery.classify_block(readiness, (), _COPY) is MergeBlockClass.TRANSIENT_FLAKE
    assert merge_recovery.classify_block(readiness, (), _UNDECLARED) is MergeBlockClass.GENUINE


def test_attempt_mechanical_recovery_reruns_only_flaky_for_aggregate_shape(
    forge: PrForge,
) -> None:
    forge.run_ids = {E2E: 7001, GATE: 8001}
    forge.show_failing((E2E, GATE))
    recovered = merge_recovery.attempt_mechanical_recovery(
        _PR, "task-x", _readiness((E2E, GATE)), forge_target=_COPY
    )
    assert recovered == (True, MergeBlockClass.TRANSIENT_FLAKE, None)
    assert forge.rerun_run_ids == [7001]
    assert _pr_state_paths.gate_rerun_checks_for_sha(
        _PR, _COPY, head_sha=_HEAD
    ) == frozenset({E2E})


def test_classify_block_flaky_plus_aggregate_plus_non_allowlisted_is_genuine(
    forge: PrForge,
) -> None:
    readiness = _readiness((E2E, LINT, GATE))
    forge.show_failing((E2E, LINT, GATE))
    assert merge_recovery.classify_block(readiness, (), _COPY) is MergeBlockClass.GENUINE
    recovered = merge_recovery.attempt_mechanical_recovery(
        _PR, "task-x", readiness, forge_target=_COPY
    )
    assert recovered == (False, MergeBlockClass.GENUINE, None)
    assert forge.rerun_run_ids == []


def test_behind_with_flaky_plus_aggregate_is_behind_plus_transient(forge: PrForge) -> None:
    readiness = _readiness((E2E, GATE), merge_state_status="BEHIND")
    assert (
        merge_recovery.classify_block(readiness, (), _COPY)
        is MergeBlockClass.BEHIND_PLUS_TRANSIENT
    )
    recovered = merge_recovery.attempt_mechanical_recovery(
        _PR, "task-x", readiness, forge_target=_COPY
    )
    assert recovered == (True, MergeBlockClass.BEHIND_PLUS_TRANSIENT, None)
    assert forge.calls("request_branch_update") == [((_PR,), {"forge_target": _COPY})]
    assert forge.rerun_run_ids == []


def test_behind_with_flaky_plus_aggregate_on_undeclared_target_is_genuine(
    forge: PrForge,
) -> None:
    readiness = _readiness((E2E, GATE), merge_state_status="BEHIND")
    recovered = merge_recovery.attempt_mechanical_recovery(
        _PR, "task-x", readiness, forge_target=_UNDECLARED
    )
    assert recovered == (False, MergeBlockClass.GENUINE, None)
    assert forge.calls("request_branch_update") == []
    assert forge.rerun_run_ids == []


def test_aggregate_failing_alone_matches_undeclared_target() -> None:
    readiness = _readiness((GATE,))
    assert merge_recovery.classify_block(readiness, (), _COPY) is merge_recovery.classify_block(
        readiness, (), _UNDECLARED
    )
    viewed = _pr_flake_ledger.aggregate_set_aside(readiness, _COPY)
    assert viewed is readiness
    assert _pr_flake_ledger.actionable_failing_checks(viewed) == (GATE,)
    assert _pr_flake_ledger.rebase_blocking_failing_checks(viewed) == (GATE,)
    assert merge_recovery.needs_sonar_annotations(
        readiness, _COPY
    ) is merge_recovery.needs_sonar_annotations(readiness, _UNDECLARED)


def test_needs_sonar_annotations_sees_set_aside_readiness() -> None:
    readiness = _readiness((_SONAR, GATE), merge_state_status="CLEAN")
    only_sonar = _readiness((_SONAR,), merge_state_status="CLEAN")
    annotations = (_annotation(),)
    assert merge_recovery.needs_sonar_annotations(readiness, _WITH_SONAR) is True
    assert merge_recovery.needs_sonar_annotations(readiness, _WITH_SONAR_UNDECLARED) is False
    declared_class = merge_recovery.classify_block(readiness, annotations, _WITH_SONAR)
    assert declared_class is merge_recovery.classify_block(
        only_sonar, annotations, _WITH_SONAR_UNDECLARED
    )
    assert declared_class is MergeBlockClass.SONAR_HOTSPOT


def test_classify_block_twice_is_idempotent() -> None:
    readiness = _readiness((E2E, GATE))
    viewed = _pr_flake_ledger.aggregate_set_aside(readiness, _COPY)
    assert merge_recovery.classify_block(viewed, (), _COPY) is merge_recovery.classify_block(
        readiness, (), _COPY
    )
