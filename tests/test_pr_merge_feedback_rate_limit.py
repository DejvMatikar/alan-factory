from __future__ import annotations

import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from alan import process_runner
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.handover import Decision, Handover
from alan.teams import _forge_errors
from alan.teams import pr_merge as pr_merge_mod
from alan.teams.pr_merge import TEAM
from alan.workspace import Workspace, create
from tests._helpers import seed_and_run
from tests.fakes.forge import FakeForge
from tests.forge_stubs import ForgeScript, checks_read, pr_read

_PASSING_CI: list[dict[str, object]] = [{"name": "ci", "state": "SUCCESS"}]


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return create("pr-merge-rl-task", "gate the merge", root=tmp_path / "ws")


@pytest.fixture
def forge(_active_fake_forge: FakeForge) -> ForgeScript:
    return ForgeScript(_active_fake_forge)


def _approve_prior(workspace: Workspace, pr_number: int) -> Handover:
    return Handover(
        handover_id="handover_verif_001",
        handover_type="forward",
        task_id=workspace.task_id,
        from_team="verification",
        from_invocation_id="inv_001",
        to_team_suggestions=["pr_merge"],
        iteration=2,
        timestamp_utc=datetime.now(UTC),
        decisions=[
            Decision(
                id="verif-approve",
                statement=f"judge approved PR #{pr_number}",
                rationale="ok",
                confidence=1.0,
                reversibility="low",
            )
        ],
        full_synthesis="## Verdict\nVERDICT: APPROVE",
        pr_number=pr_number,
    )


def _patch_poll_intervals(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_PR_MERGE_POLL_INTERVAL_S", "0")
    monkeypatch.setenv("ALAN_PR_MERGE_TIMEOUT_S", "10")
    monkeypatch.setenv("ALAN_PR_MERGE_MAX_POLLS", "20")
    monkeypatch.delenv("ALAN_PR_MERGE_DRY_RUN", raising=False)


def test_evaluate_checks_propagates_rate_limit_from_feedback_fetch(
    monkeypatch: pytest.MonkeyPatch, forge: ForgeScript,
) -> None:
    ws_stub: Any = None
    forge.queue("read_checks", checks_read(_PASSING_CI))

    def stub(cmd: Any, *, cwd: Any = None, timeout: float = 0.0) -> Any:
        del cwd, timeout
        argv = list(cmd)
        if argv[:3] == ["gh", "api", "graphql"]:
            return subprocess.CompletedProcess(
                argv, 1, stdout="", stderr="API rate limit exceeded for installation ID 1.",
            )
        raise AssertionError(f"unexpected call: {argv}")

    monkeypatch.setattr(process_runner, "run_subprocess", stub)
    with pytest.raises(_forge_errors.ForgeRateLimited):
        pr_merge_mod._evaluate_checks(
            ws_stub, pr_number=42, start=time.monotonic(), polls=0, timeout=600.0, max_polls=20,
            forge_target=DEFAULT_FORGE_TARGET,
        )


def test_poll_until_done_stops_after_a_single_feedback_poll_when_rate_limited(
    monkeypatch: pytest.MonkeyPatch, forge: ForgeScript,
) -> None:
    ws_stub: Any = None
    calls: list[list[str]] = []
    forge.queue_read_pr(
        ("state", "mergeStateStatus"), pr_read({"state": "OPEN", "mergeStateStatus": "CLEAN"}),
    )
    forge.queue("read_checks", checks_read(_PASSING_CI))

    def stub(cmd: Any, *, cwd: Any = None, timeout: float = 0.0) -> Any:
        del cwd, timeout
        argv = list(cmd)
        calls.append(argv)
        if argv[:3] == ["gh", "api", "graphql"]:
            return subprocess.CompletedProcess(
                argv, 1, stdout="", stderr="API rate limit exceeded for installation ID 1.",
            )
        raise AssertionError(f"unexpected call: {argv}")

    monkeypatch.setattr(process_runner, "run_subprocess", stub)
    with pytest.raises(_forge_errors.ForgeRateLimited):
        pr_merge_mod._poll_until_done(ws_stub, 42, forge_target=DEFAULT_FORGE_TARGET)

    graphql_calls = [c for c in calls if c[:3] == ["gh", "api", "graphql"]]
    assert len(graphql_calls) == 1


def test_evaluate_checks_still_returns_pending_for_non_rate_limited_feedback_failure(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, forge: ForgeScript,
) -> None:
    forge.queue("read_checks", checks_read(_PASSING_CI))

    def stub(cmd: Any, *, cwd: Any = None, timeout: float = 0.0) -> Any:
        del cwd, timeout
        argv = list(cmd)
        if argv[:3] == ["gh", "api", "graphql"]:
            return subprocess.CompletedProcess(
                argv, 1, stdout="", stderr="Pull request is not mergeable",
            )
        raise AssertionError(f"unexpected call: {argv}")

    monkeypatch.setattr(process_runner, "run_subprocess", stub)
    kind, ctx = pr_merge_mod._evaluate_checks(
        workspace, pr_number=42, start=time.monotonic(), polls=0, timeout=600.0, max_polls=20,
        forge_target=DEFAULT_FORGE_TARGET,
    )
    assert kind == "pending"
    assert "reason" in ctx


def test_team_run_escalates_forge_rate_limited_uncaught(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, forge: ForgeScript,
) -> None:
    _patch_poll_intervals(monkeypatch)
    forge.queue_read_pr(
        ("state", "mergeStateStatus"), pr_read({"state": "OPEN", "mergeStateStatus": "CLEAN"}),
    )
    forge.queue("read_checks", checks_read(_PASSING_CI))

    def stub(cmd: Any, *, cwd: Any = None, timeout: float = 0.0) -> Any:
        del cwd, timeout
        argv = list(cmd)
        if argv[:3] == ["gh", "api", "graphql"]:
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="secondary rate limit")
        raise AssertionError(f"unexpected call: {argv}")

    monkeypatch.setattr(process_runner, "run_subprocess", stub)
    with pytest.raises(_forge_errors.ForgeRateLimited):
        seed_and_run(TEAM, workspace, _approve_prior(workspace, 42), lambda _e: None)
