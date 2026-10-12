from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan import merge_gate
from alan import workspace as workspace_mod
from alan.handover import Handover
from alan.routes._workspaces_merge_gate import apply_merge_gate_to_manual_dispatch
from alan.teams import _pr_closing, _pr_freshness, _pr_ready

_UNAVAILABLE = _pr_ready.ReadinessUnavailable("gh not installed")
_DESCRIPTION_WITH_TRAILER = "Ship it.\n\nDelivers-Issues: 12"


@pytest.fixture(autouse=True)
def _isolated_logs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))


@pytest.fixture(autouse=True)
def _unavailable_readiness(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_pr_ready, "check_pr_merge_readiness", lambda *_a, **_kw: _UNAVAILABLE)


@pytest.fixture
def stale_baseline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_pr_freshness, "is_measured_baseline_stale", lambda *_a, **_kw: True)


def _workspace(root: Path, name: str) -> workspace_mod.Workspace:
    return workspace_mod.create(name, _DESCRIPTION_WITH_TRAILER, root=root)


def _prior_handover(task_id: str) -> Handover:
    return Handover(
        handover_id=f"handover_{task_id}_prior",
        handover_type="forward",
        task_id=task_id,
        from_team="pr_merge",
        from_invocation_id="inv_001",
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="ok",
        pr_number=555,
    )


@pytest.mark.usefixtures("stale_baseline")
def test_unavailable_readiness_passes_through_as_not_attempted(tmp_path: Path) -> None:
    ws = _workspace(tmp_path / "ws", "unavailable-not-attempted")
    attempt = merge_gate.attempt_recovery(ws, 42, recovered_outcome="pr_merge")
    assert attempt == merge_gate.MergeGateNotAttempted(_UNAVAILABLE)


def test_unavailable_readiness_consults_neither_the_baseline_nor_the_closing_links_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    consulted: list[str] = []

    def _stale(*_a: object, **_kw: object) -> bool:
        consulted.append("baseline")
        return False

    def _closing(*_a: object, **_kw: object) -> frozenset[int]:
        consulted.append("closing_links")
        return frozenset()

    monkeypatch.setattr(_pr_freshness, "is_measured_baseline_stale", _stale)
    monkeypatch.setattr(_pr_closing, "gh_pr_closing_issue_numbers", _closing)
    ws = _workspace(tmp_path / "ws", "unavailable-no-checks")
    merge_gate.attempt_recovery(ws, 42, recovered_outcome="pr_merge")
    assert consulted == []


@pytest.mark.usefixtures("stale_baseline")
@pytest.mark.parametrize("instructions", ["keep this operator note", None])
async def test_unavailable_readiness_appends_no_remediation_text_to_a_manual_dispatch(
    tmp_path: Path, instructions: str | None,
) -> None:
    ws = _workspace(tmp_path / "ws", "unavailable-manual")
    decision, returned = await apply_merge_gate_to_manual_dispatch(
        ws, _prior_handover(ws.task_id), instructions,
    )
    assert returned == instructions
    assert isinstance(decision.attempt, merge_gate.MergeGateNotAttempted)
