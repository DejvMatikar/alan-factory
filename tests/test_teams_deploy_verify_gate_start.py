from __future__ import annotations

import json
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from alan import process_runner
from alan import workflow as workflow_mod
from alan.deploy_verify import GateRecord
from alan.forge import JsonObject
from alan.handover import Handover
from alan.teams import deploy_verify as deploy_verify_mod
from alan.teams.deploy_verify import TEAM
from alan.workspace import Workspace, create
from tests.fakes.forge import FakeForge
from tests.forge_stubs import ForgeScript, pr_read


@pytest.fixture(autouse=True)
def _audit_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALAN_DEPLOY_VERIFY_AUDIT_DISABLE", raising=False)


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return create("dv-gate-start", "deploy gate start test", root=tmp_path / "ws")


def _prior(workspace: Workspace, pr_number: int | None = 42) -> Handover:
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


def _run_with_instructions(
    workspace: Workspace, prior: Handover | None, instructions: str | None
) -> Handover:
    specs = TEAM.plan_workflow(workspace, prior)
    inv_id = workflow_mod.seed_pending_workflow(workspace, team=TEAM.name, tasks=specs)
    return TEAM.run(workspace, prior, lambda e: None, inv_id=inv_id, instructions=instructions)


def _audit_entries(tmp_path: Path) -> list[dict[str, Any]]:
    audit_file = tmp_path / "logs" / "deploy_verify_audit.jsonl"
    if not audit_file.exists():
        return []
    return [json.loads(line) for line in audit_file.read_text().splitlines()]


def _verify_starts(tmp_path: Path) -> list[dict[str, Any]]:
    return [e for e in _audit_entries(tmp_path) if e["action"] == "verify_start"]


def _healthy_mock() -> MagicMock:
    m = MagicMock()
    m.__enter__ = MagicMock(return_value=m)
    m.__exit__ = MagicMock(return_value=False)
    m.status = 200
    m.read.return_value = json.dumps({"ok": True}).encode()
    return m


def _merged_at_fields(seconds_ago: float = 5.0) -> JsonObject:
    ts = datetime.now(UTC) - timedelta(seconds=seconds_ago)
    return {"state": "MERGED", "mergedAt": ts.strftime("%Y-%m-%dT%H:%M:%SZ")}


def _successful_run_json(seconds_ago: float = 2.0) -> str:
    ts = datetime.now(UTC) - timedelta(seconds=seconds_ago)
    return json.dumps([{
        "databaseId": 1, "createdAt": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": "completed", "conclusion": "success",
    }])


class _GhScript:
    def __init__(self) -> None:
        self._responses: dict[tuple[str, ...], list[subprocess.CompletedProcess[str]]] = {}

    def queue(self, argv_prefix: tuple[str, ...], *, rc: int = 0, stdout: str = "") -> None:
        self._responses.setdefault(argv_prefix, []).append(
            subprocess.CompletedProcess([], rc, stdout=stdout, stderr="")
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


def _quick_intervals(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_DEPLOY_VERIFY_BUDGET_S", "3600")
    monkeypatch.setenv("ALAN_DEPLOY_VERIFY_MERGE_RACE_BUDGET_S", "0")


def test_verify_start_audit_degrades_to_ceiling_when_instructions_none(
    workspace: Workspace,
    gh: _GhScript,
    forge: ForgeScript,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _quick_intervals(monkeypatch)
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    forge.queue("read_pr", pr_read(_merged_at_fields()))
    gh.queue(("gh", "run", "list"), stdout=_successful_run_json())
    with patch("urllib.request.urlopen", return_value=_healthy_mock()):
        _run_with_instructions(workspace, _prior(workspace), None)
    starts = _verify_starts(tmp_path)
    assert len(starts) == 1
    assert starts[0]["pr_number"] == 42
    assert starts[0]["path"] == "ceiling"
    assert starts[0]["waited_s"] == 0.0


def test_verify_start_audit_reports_signal_path(
    workspace: Workspace,
    gh: _GhScript,
    forge: ForgeScript,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _quick_intervals(monkeypatch)
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    forge.queue("read_pr", pr_read(_merged_at_fields()))
    gh.queue(("gh", "run", "list"), stdout=_successful_run_json())
    record = GateRecord(merge_sha="a" * 40, path="signal", deferred_at=None)
    with patch("urllib.request.urlopen", return_value=_healthy_mock()):
        _run_with_instructions(workspace, _prior(workspace), record.to_json())
    starts = _verify_starts(tmp_path)
    assert len(starts) == 1
    assert starts[0]["path"] == "signal"
    assert starts[0]["waited_s"] == 0.0


def test_verify_start_audit_reports_restart_free_path(
    workspace: Workspace,
    gh: _GhScript,
    forge: ForgeScript,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _quick_intervals(monkeypatch)
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    forge.queue("read_pr", pr_read(_merged_at_fields()))
    gh.queue(("gh", "run", "list"), stdout=_successful_run_json())
    record = GateRecord(merge_sha="a" * 40, path="restart_free", deferred_at=None)
    with patch("urllib.request.urlopen", return_value=_healthy_mock()):
        _run_with_instructions(workspace, _prior(workspace), record.to_json())
    starts = _verify_starts(tmp_path)
    assert len(starts) == 1
    assert starts[0]["path"] == "restart_free"
    assert starts[0]["waited_s"] == 0.0


def test_verify_start_audit_reports_ceiling_wait_duration(
    workspace: Workspace,
    gh: _GhScript,
    forge: ForgeScript,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _quick_intervals(monkeypatch)
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    forge.queue("read_pr", pr_read(_merged_at_fields()))
    gh.queue(("gh", "run", "list"), stdout=_successful_run_json())
    deferred_at = time.time() - 42.0
    record = GateRecord(merge_sha=None, path="ceiling", deferred_at=deferred_at)
    with patch("urllib.request.urlopen", return_value=_healthy_mock()):
        _run_with_instructions(workspace, _prior(workspace), record.to_json())
    starts = _verify_starts(tmp_path)
    assert len(starts) == 1
    assert starts[0]["path"] == "ceiling"
    assert starts[0]["waited_s"] == pytest.approx(42.0, abs=2.0)


def test_verify_start_audit_degrades_to_ceiling_for_unparseable_instructions(
    workspace: Workspace,
    gh: _GhScript,
    forge: ForgeScript,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _quick_intervals(monkeypatch)
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    forge.queue("read_pr", pr_read(_merged_at_fields()))
    gh.queue(("gh", "run", "list"), stdout=_successful_run_json())
    with patch("urllib.request.urlopen", return_value=_healthy_mock()):
        _run_with_instructions(workspace, _prior(workspace), "please look at the login flow")
    starts = _verify_starts(tmp_path)
    assert len(starts) == 1
    assert starts[0]["path"] == "ceiling"
    assert starts[0]["waited_s"] == 0.0


def test_verify_start_audit_reports_awaiting_restart_path(
    workspace: Workspace,
    gh: _GhScript,
    forge: ForgeScript,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _quick_intervals(monkeypatch)
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    forge.queue("read_pr", pr_read(_merged_at_fields()))
    gh.queue(("gh", "run", "list"), stdout=_successful_run_json())
    record = GateRecord(
        merge_sha="a" * 40, path="awaiting_restart", deferred_at=time.time() - 30.0
    )
    with patch("urllib.request.urlopen", return_value=_healthy_mock()):
        _run_with_instructions(workspace, _prior(workspace), record.to_json())
    starts = _verify_starts(tmp_path)
    assert len(starts) == 1
    assert starts[0]["path"] == "awaiting_restart"
    assert starts[0]["waited_s"] == pytest.approx(30.0, abs=2.0)


def test_verify_start_audit_written_once_per_evaluation_across_re_entry(
    workspace: Workspace,
    gh: _GhScript,
    forge: ForgeScript,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _quick_intervals(monkeypatch)
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    merged = _merged_at_fields()
    forge.queue("read_pr", pr_read(merged), pr_read(merged))
    gh.queue(("gh", "run", "list"), stdout=json.dumps([]))
    gh.queue(("gh", "run", "list"), stdout=_successful_run_json())
    with patch("urllib.request.urlopen", return_value=_healthy_mock()):
        first = _run_with_instructions(workspace, _prior(workspace), None)
        assert first.deploy_wait is not None
        record = GateRecord(
            merge_sha=None,
            path="awaiting_restart",
            deferred_at=None,
            deadline=first.deploy_wait.deadline,
        )
        second = _run_with_instructions(workspace, _prior(workspace), record.to_json())
    starts = _verify_starts(tmp_path)
    assert len(starts) == 2
    assert starts[0]["path"] == "ceiling"
    assert starts[1]["path"] == "awaiting_restart"
    assert second.handover_type == "terminal"


def test_dry_run_never_emits_verify_start_audit(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_DEPLOY_VERIFY_DRY_RUN", "1")
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    _run_with_instructions(workspace, _prior(workspace), None)
    assert _verify_starts(tmp_path) == []


def test_run_threads_instructions_into_evaluate_once(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[str | None] = []
    real_evaluate = deploy_verify_mod._evaluate_once

    def _spy(
        ws: Workspace, pr_number: int, gate_instructions: str | None = None
    ) -> deploy_verify_mod.DeployVerifyOutcome:
        captured.append(gate_instructions)
        return real_evaluate(ws, pr_number, gate_instructions)

    monkeypatch.setattr(deploy_verify_mod, "_evaluate_once", _spy)
    monkeypatch.setenv("ALAN_DEPLOY_VERIFY_DRY_RUN", "1")
    _run_with_instructions(workspace, _prior(workspace), "some-gate-instructions")
    assert captured == ["some-gate-instructions"]
