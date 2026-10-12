from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import yaml

from alan.handover import DeployWaitSignal, Handover, load_handover, save_handover
from alan.workspace import create


def _make_handover(task_id: str, **overrides: object) -> Handover:
    base: dict[str, object] = {
        "handover_id": f"handover_{task_id}_001",
        "handover_type": "terminal",
        "task_id": task_id,
        "from_team": "deploy_verify",
        "from_invocation_id": "inv_001",
        "to_team_suggestions": [],
        "iteration": 1,
        "timestamp_utc": datetime.now(UTC),
        "full_synthesis": "awaiting restart",
    }
    base.update(overrides)
    return Handover.model_validate(base)


def test_deploy_wait_defaults_none() -> None:
    assert _make_handover("t1").deploy_wait is None


def test_deploy_wait_round_trips(tmp_path: Path) -> None:
    ws = create("deploy-wait-rt", "d", root=tmp_path / "ws")
    h = _make_handover(
        ws.task_id,
        deploy_wait=DeployWaitSignal(
            merge_sha="a" * 40,
            deadline=1900.0,
            race_deadline=None,
            reason="PR #7: awaiting the deploy restart signal",
        ).model_dump(),
    )
    path = save_handover(ws, h)
    loaded = load_handover(path)
    assert loaded.schema_version == "1.23"
    assert loaded.deploy_wait is not None
    assert loaded.deploy_wait.merge_sha == "a" * 40
    assert loaded.deploy_wait.deadline == 1900.0
    assert loaded.deploy_wait.race_deadline is None
    assert loaded.deploy_wait.reason == "PR #7: awaiting the deploy restart signal"


def test_deploy_wait_merged_at_round_trips(tmp_path: Path) -> None:
    ws = create("deploy-wait-merged-at-rt", "d", root=tmp_path / "ws")
    h = _make_handover(
        ws.task_id,
        deploy_wait=DeployWaitSignal(
            merge_sha="a" * 40,
            deadline=1900.0,
            race_deadline=None,
            reason="PR #7: awaiting the deploy restart signal",
            merged_at=1200.0,
        ).model_dump(),
    )
    path = save_handover(ws, h)
    loaded = load_handover(path)
    assert loaded.deploy_wait is not None
    assert loaded.deploy_wait.merged_at == 1200.0


def test_deploy_wait_merged_at_defaults_none() -> None:
    signal = DeployWaitSignal(
        merge_sha=None, deadline=100.0, race_deadline=None, reason="x"
    )
    assert signal.merged_at is None


def test_deploy_wait_race_deadline_round_trips(tmp_path: Path) -> None:
    ws = create("deploy-wait-race-rt", "d", root=tmp_path / "ws")
    h = _make_handover(
        ws.task_id,
        deploy_wait=DeployWaitSignal(
            merge_sha=None,
            deadline=2200.0,
            race_deadline=2200.0,
            reason="PR #9: mergedAt not yet available",
        ).model_dump(),
    )
    path = save_handover(ws, h)
    loaded = load_handover(path)
    assert loaded.deploy_wait is not None
    assert loaded.deploy_wait.race_deadline == 2200.0


def test_v120_payload_loads_with_deploy_wait_none(tmp_path: Path) -> None:
    ws = create("deploy-wait-legacy", "d", root=tmp_path / "ws")
    payload = {
        "schema_version": "1.20",
        "handover_id": f"handover_{ws.task_id}_001",
        "handover_type": "terminal",
        "task_id": ws.task_id,
        "from_team": "deploy_verify",
        "from_invocation_id": "inv_001",
        "to_team_suggestions": [],
        "iteration": 1,
        "revisit_of": None,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "full_synthesis": "v1.20 payload predating deploy_wait",
    }
    target = ws.path / "handovers" / f"handover_{ws.task_id}_001.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(payload), encoding="utf-8")
    loaded = load_handover(target)
    assert loaded.schema_version == "1.20"
    assert loaded.deploy_wait is None
