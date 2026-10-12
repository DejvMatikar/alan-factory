from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from alan import deploy_wait_gate, process_runner
from alan.deploy_verify import GateRecord
from alan.forge import JsonObject
from alan.router import RouterDecision
from alan.teams.deploy_verify import TEAM
from alan.workspace import Workspace, create
from tests._helpers import seed_and_run
from tests.fakes.forge import FakeForge
from tests.forge_stubs import ForgeScript, pr_read


@pytest.fixture(autouse=True)
def _silence_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_DEPLOY_VERIFY_AUDIT_DISABLE", "1")


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return create("dv-wait-signal", "deploy wait signal test", root=tmp_path / "ws")


def _prior(workspace: Workspace, pr_number: int = 42) -> Any:
    from alan.handover import Handover

    return Handover(
        handover_id="handover_pr_merge_001",
        handover_type="terminal",
        task_id=workspace.task_id,
        from_team="pr_merge",
        from_invocation_id="inv_001",
        to_team_suggestions=[],
        iteration=3,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="PR merged",
        pr_number=pr_number,
        decisions=[],
    )


class _GhScript:
    def __init__(self) -> None:
        self._responses: dict[tuple[str, ...], list[subprocess.CompletedProcess[str]]] = {}

    def queue(
        self, argv_prefix: tuple[str, ...], *, rc: int = 0, stdout: str = "", stderr: str = ""
    ) -> None:
        self._responses.setdefault(argv_prefix, []).append(
            subprocess.CompletedProcess([], rc, stdout=stdout, stderr=stderr)
        )

    def __call__(self, cmd: Any, *, cwd: Any = None, timeout: float = 0.0) -> Any:
        del cwd, timeout
        argv = list(cmd)
        for prefix_len in range(len(argv), 0, -1):
            key = tuple(argv[:prefix_len])
            slot = self._responses.get(key)
            if slot:
                return slot.pop(0)
        raise AssertionError(f"no response queued for: {argv}")


@pytest.fixture
def gh(monkeypatch: pytest.MonkeyPatch) -> _GhScript:
    script = _GhScript()
    monkeypatch.setattr(process_runner, "run_subprocess", script)
    return script


@pytest.fixture
def forge(_active_fake_forge: FakeForge) -> ForgeScript:
    return ForgeScript(_active_fake_forge)


def _merged_at_fields(seconds_ago: float = 5.0) -> JsonObject:
    ts = datetime.now(UTC) - timedelta(seconds=seconds_ago)
    return {"state": "MERGED", "mergedAt": ts.strftime("%Y-%m-%dT%H:%M:%SZ")}


def _empty_runs_json() -> str:
    return json.dumps([])


def _unhealthy_mock() -> Any:
    from unittest.mock import MagicMock

    m = MagicMock()
    m.__enter__ = MagicMock(return_value=m)
    m.__exit__ = MagicMock(return_value=False)
    m.status = 200
    m.read.return_value = json.dumps({"ok": False}).encode()
    return m


def _quick_intervals(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_DEPLOY_VERIFY_BUDGET_S", "3600")
    monkeypatch.setenv("ALAN_DEPLOY_VERIFY_MERGE_RACE_BUDGET_S", "300")


def test_merged_wait_handover_carries_merged_at(
    workspace: Workspace, gh: _GhScript, forge: ForgeScript, monkeypatch: pytest.MonkeyPatch
) -> None:
    _quick_intervals(monkeypatch)
    forge.queue("read_pr", pr_read(_merged_at_fields(seconds_ago=5.0)))
    gh.queue(("gh", "run", "list"), stdout=_empty_runs_json())

    with patch("urllib.request.urlopen", return_value=_unhealthy_mock()):
        prior = _prior(workspace)
        handover = seed_and_run(TEAM, workspace, prior, lambda e: None)

    assert handover.handover_type == "terminal"
    assert handover.deploy_wait is not None
    assert handover.deploy_wait.merged_at is not None
    assert handover.deploy_wait.race_deadline is None


def test_merge_race_wait_handover_has_no_merged_at(
    workspace: Workspace, forge: ForgeScript, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_DEPLOY_VERIFY_MERGE_RACE_BUDGET_S", "9999")
    forge.queue("read_pr", pr_read({"state": "MERGED", "mergedAt": None}))

    prior = _prior(workspace)
    handover = seed_and_run(TEAM, workspace, prior, lambda e: None)

    assert handover.handover_type == "terminal"
    assert handover.deploy_wait is not None
    assert handover.deploy_wait.merged_at is None
    assert handover.deploy_wait.race_deadline is not None


def test_merged_wait_handover_reparks_as_awaiting_live_end_to_end(
    workspace: Workspace, gh: _GhScript, forge: ForgeScript, monkeypatch: pytest.MonkeyPatch
) -> None:
    _quick_intervals(monkeypatch)
    forge.queue("read_pr", pr_read(_merged_at_fields(seconds_ago=5.0)))
    gh.queue(("gh", "run", "list"), stdout=_empty_runs_json())

    with patch("urllib.request.urlopen", return_value=_unhealthy_mock()):
        prior = _prior(workspace)
        handover = seed_and_run(TEAM, workspace, prior, lambda e: None)

    assert handover.deploy_wait is not None
    decision = deploy_wait_gate.apply(handover, RouterDecision(kind="done", reason="terminal"))
    assert decision.kind == "enqueue"
    record = GateRecord.from_json(decision.instructions)
    assert record is not None
    assert record.path == "awaiting_live"
    assert record.merged_at == handover.deploy_wait.merged_at


def test_merged_wait_handover_recovers_merge_sha_when_gate_record_lacks_it(
    workspace: Workspace, gh: _GhScript, forge: ForgeScript, monkeypatch: pytest.MonkeyPatch
) -> None:
    _quick_intervals(monkeypatch)
    ts = datetime.now(UTC) - timedelta(seconds=5)
    forge.queue(
        "read_pr",
        pr_read({
            "state": "MERGED",
            "mergedAt": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "mergeCommit": {"oid": "d" * 40},
        }),
    )
    gh.queue(("gh", "run", "list"), stdout=_empty_runs_json())
    stale_record = GateRecord(merge_sha=None, path="awaiting_live", deferred_at=None)

    with patch("urllib.request.urlopen", return_value=_unhealthy_mock()):
        prior = _prior(workspace)
        handover = seed_and_run(
            TEAM, workspace, prior, lambda e: None, instructions=stale_record.to_json()
        )

    assert handover.deploy_wait is not None
    assert handover.deploy_wait.merge_sha == "d" * 40
    decision = deploy_wait_gate.apply(handover, RouterDecision(kind="done", reason="terminal"))
    record = GateRecord.from_json(decision.instructions)
    assert record is not None
    assert record.merge_sha == "d" * 40


def test_merge_race_wait_handover_reparks_as_awaiting_restart_end_to_end(
    workspace: Workspace, forge: ForgeScript, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_DEPLOY_VERIFY_MERGE_RACE_BUDGET_S", "9999")
    forge.queue("read_pr", pr_read({"state": "MERGED", "mergedAt": None}))

    prior = _prior(workspace)
    handover = seed_and_run(TEAM, workspace, prior, lambda e: None)

    decision = deploy_wait_gate.apply(handover, RouterDecision(kind="done", reason="terminal"))
    assert decision.kind == "enqueue"
    record = GateRecord.from_json(decision.instructions)
    assert record is not None
    assert record.path == "awaiting_restart"
    assert record.merged_at is None
