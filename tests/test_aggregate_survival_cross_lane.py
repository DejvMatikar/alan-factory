from __future__ import annotations

from pathlib import Path

import pytest

from alan import merge_recovery
from alan.engine_config import ForgeTarget, RepoIdentity
from alan.merge_recovery import MergeBlockClass
from alan.teams import _pr_flake_ledger, _pr_ready, _pr_state_paths
from alan.teams import _verification_gates as gates
from tests.aggregate_pr_forge import E2E, GATE, PrForge
from tests.fakes.forge import FakeForge

_HEAD = "9" * 40
_PR = 42

_COPY = ForgeTarget(
    repo=RepoIdentity(owner="acme-corp", repo="widget-app"),
    credential_env_name="ACME_WIDGET_GH_TOKEN",
    authoritative_checks=frozenset({GATE}),
    aggregate_checks=frozenset({GATE}),
)


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    monkeypatch.setenv("ALAN_VERIFIER_SETTLE_POLL_INTERVAL_S", "0")


@pytest.fixture
def forge(_active_fake_forge: FakeForge) -> PrForge:
    return PrForge(_active_fake_forge, head_sha=_HEAD)


def _red_on_e2e_and_aggregate() -> _pr_ready.PrReadiness:
    failing = (E2E, GATE)
    return _pr_ready.PrReadiness(
        ok=False,
        merge_state_status="BLOCKED",
        review_decision="REVIEW_REQUIRED",
        failing_checks=failing,
        failed_reasons=(f"failing checks: {', '.join(failing)}",),
        head_sha=_HEAD,
        check_count=3,
    )


def _verification_red_to_red(
    forge: PrForge, *, specs_before: tuple[str, ...], specs_after: tuple[str, ...],
) -> None:
    forge.script_reads((E2E, GATE), (E2E, GATE))
    forge.script_specs(specs_before, specs_after)
    settled = gates._resolve_gate_readiness(_PR, "task-x", forge_target=_COPY)
    assert isinstance(settled, _pr_ready.PrReadiness)
    assert settled.failing_checks == (E2E,)
    assert forge.rerun_run_ids == [7001]


def _merge_lane_attempt(forge: PrForge) -> tuple[bool, MergeBlockClass]:
    forge.rerun_succeeds = False
    forge.show_failing((E2E, GATE))
    ok, block_class, _ = merge_recovery.attempt_mechanical_recovery(
        _PR, "task-x", _red_on_e2e_and_aggregate(), forge_target=_COPY
    )
    return ok, block_class


def test_rerun_still_red_disjoint_specs_survivors_exclude_aggregate_and_merge_lane_is_transient_flake(  # noqa: E501
    forge: PrForge,
) -> None:
    _verification_red_to_red(
        forge, specs_before=("before.spec.ts",), specs_after=("after.spec.ts",)
    )
    survived = _pr_flake_ledger.survived_at_head_checks(_PR, _HEAD, forge_target=_COPY)
    assert GATE not in survived
    assert survived == frozenset()
    assert _merge_lane_attempt(forge) == (False, MergeBlockClass.TRANSIENT_FLAKE)
    assert not _pr_flake_ledger.gate_reruns_exhausted(_PR, _HEAD, forge_target=_COPY)
    assert (
        _pr_state_paths.gate_rerun_count_for_sha(_PR, _COPY, head_sha=_HEAD)
        == _pr_flake_ledger.max_gate_reruns() - 1
    )


def test_rerun_still_red_overlapping_specs_merge_lane_is_genuine_and_e2e_evidenced(
    forge: PrForge,
) -> None:
    _verification_red_to_red(
        forge, specs_before=("chatty.spec.ts",), specs_after=("chatty.spec.ts",)
    )
    survived = _pr_flake_ledger.survived_at_head_checks(_PR, _HEAD, forge_target=_COPY)
    assert survived == frozenset({E2E})
    assert _merge_lane_attempt(forge) == (False, MergeBlockClass.GENUINE)
    evidenced = _pr_flake_ledger.evidenced_actionable_failing_checks(
        _red_on_e2e_and_aggregate(), _PR, _HEAD, forge_target=_COPY
    )
    assert evidenced == (E2E,)
