from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from alan import process_runner
from alan.engine_config import ForgeTarget
from alan.forge import JsonObject
from alan.handover import Decision, Handover
from alan.teams import _pr_feedback, _pr_merge_checks
from alan.teams.pr_merge import TEAM
from alan.workspace import Workspace, create
from tests import _advisory_check_runs_support as support
from tests._helpers import seed_and_run
from tests.fakes.forge import FakeForge
from tests.forge_stubs import ForgeScript, checks_read, merge_attempt

_THREAD_ZERO = _pr_feedback.ThreadInfo(
    copilot_threads=(), human_unresolved=0, other_bot_unresolved=0, total=0
)
_THREAD_OPEN = _pr_feedback.ThreadInfo(
    copilot_threads=(), human_unresolved=1, other_bot_unresolved=0, total=1
)
_COMMENT_ZERO = _pr_feedback.CommentInfo(
    copilot_items=(), human_items=0, other_bot_items=0, total=0
)
_SONAR_LISTING = [support.check_run(support.SONAR, support.SONAR_APP)]
_SONAR_FAILED: list[dict[str, Any]] = [
    {"name": support.SONAR, "state": "FAILURE"},
    {"name": "ci", "state": "SUCCESS"},
]
_CLEAN_GRAPHQL = json.dumps({"data": {"repository": {"pullRequest": {
    "reviewThreads": {"totalCount": 0, "nodes": []},
}}}})


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return create("advisory-task", "merge past the advisory check", root=tmp_path / "ws")


@pytest.fixture
def audit_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(root))
    return root


@pytest.fixture
def forge(_active_fake_forge: FakeForge) -> ForgeScript:
    return ForgeScript(_active_fake_forge)


def _sonar_red(merge_state: str = "UNSTABLE") -> dict[str, Any]:
    return support.pr_payload(
        [support.failing_run(support.SONAR), support.passing_run("ci")], merge_state=merge_state,
    )


def _resolve(
    workspace: Workspace,
    checks: list[dict[str, str]],
    *,
    threads: _pr_feedback.ThreadInfo = _THREAD_ZERO,
    forge_target: ForgeTarget = support.FORGE,
) -> tuple[str, str, object]:
    return _pr_merge_checks.resolve_checks_kind(
        workspace, 42, checks, threads, _COMMENT_ZERO, forge_target=forge_target,
    )


def test_advisory_only_failed_checks_resolve_to_ready_and_write_an_audit_row(
    workspace: Workspace, audit_root: Path,
) -> None:
    support.serve_check_runs(_SONAR_LISTING)
    support.serve_pr_reads(_sonar_red())
    checks = list(_SONAR_FAILED)
    assert _resolve(workspace, checks) == ("ready", support.HEAD_SHA, None)
    assert checks == _SONAR_FAILED
    [row] = support.impl_audit_rows(audit_root, "advisory_check_failures_ignored")
    assert row["lane"] == "pr_merge"
    assert row["task_id"] == workspace.task_id
    assert row["pr_number"] == 42
    assert row["head_sha"] == support.HEAD_SHA
    assert row["ignored_checks"] == [support.SONAR]


def test_blocking_failed_check_next_to_an_advisory_one_stays_failed(
    workspace: Workspace, audit_root: Path,
) -> None:
    support.serve_check_runs([
        *_SONAR_LISTING, support.check_run(support.BACKEND, support.ACTIONS_APP),
    ])
    support.serve_pr_reads(support.pr_payload([
        support.failing_run(support.SONAR), support.failing_run(support.BACKEND),
    ]))
    checks = [*_SONAR_FAILED, {"name": support.BACKEND, "state": "FAILURE"}]
    assert _resolve(workspace, checks) == ("failed", "", None)
    assert support.impl_audit_rows(audit_root, "advisory_check_failures_ignored") == []


def test_red_check_posted_by_github_actions_stays_failed(workspace: Workspace) -> None:
    support.serve_check_runs([support.check_run(support.DRIFT_GATE, support.ACTIONS_APP)])
    support.serve_pr_reads(support.pr_payload([support.failing_run(support.DRIFT_GATE)]))
    checks = [{"name": support.DRIFT_GATE, "state": "FAILURE"}]
    assert _resolve(workspace, checks) == ("failed", "", None)


def test_unavailable_readiness_keeps_the_failed_verdict(workspace: Workspace) -> None:
    shas = support.serve_check_runs(_SONAR_LISTING)
    support.serve_unavailable_pr_reads()
    assert _resolve(workspace, _SONAR_FAILED) == ("failed", "", None)
    assert shas == []


def test_unreadable_listing_keeps_the_failed_verdict(
    workspace: Workspace, audit_root: Path,
) -> None:
    support.serve_check_runs(None)
    support.serve_pr_reads(_sonar_red())
    assert _resolve(workspace, _SONAR_FAILED) == ("failed", "", None)
    assert support.impl_audit_rows(audit_root, "advisory_check_runs_query_failed")


def test_authoritative_check_keeps_the_failed_verdict(workspace: Workspace) -> None:
    shas = support.serve_check_runs(_SONAR_LISTING)
    support.serve_pr_reads(_sonar_red())
    result = _resolve(workspace, _SONAR_FAILED, forge_target=support.AUTHORITATIVE_SONAR_FORGE)
    assert result == ("failed", "", None)
    assert shas == []


def test_failed_check_the_readiness_does_not_list_keeps_the_failed_verdict(
    workspace: Workspace,
) -> None:
    support.serve_check_runs(_SONAR_LISTING)
    support.serve_pr_reads(_sonar_red())
    checks = [*_SONAR_FAILED, {"name": "Unlisted", "state": "TIMED_OUT"}]
    assert _resolve(workspace, checks) == ("failed", "", None)


def test_advisory_only_red_with_unresolved_threads_is_still_unresolved(
    workspace: Workspace,
) -> None:
    support.serve_check_runs(_SONAR_LISTING)
    support.serve_pr_reads(_sonar_red())
    assert _resolve(workspace, _SONAR_FAILED, threads=_THREAD_OPEN) == ("unresolved", "", None)


def test_advisory_only_red_on_a_blocked_pr_is_still_pending(workspace: Workspace) -> None:
    support.serve_check_runs(_SONAR_LISTING)
    support.serve_pr_reads(_sonar_red("BLOCKED"))
    assert _resolve(workspace, _SONAR_FAILED) == ("pending", "", None)


def test_advisory_only_red_on_a_dirty_pr_is_still_pending(workspace: Workspace) -> None:
    support.serve_check_runs(_SONAR_LISTING)
    support.serve_pr_reads(_sonar_red("DIRTY"))
    assert _resolve(workspace, _SONAR_FAILED) == ("pending", "", None)


def test_advisory_only_red_still_meets_the_codeowners_gate(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    support.serve_check_runs(_SONAR_LISTING)
    support.serve_pr_reads(_sonar_red())
    monkeypatch.setattr(_pr_merge_checks, "_ownership_gate_enforced", lambda _target: True)
    monkeypatch.setattr(
        _pr_merge_checks, "_ownership_verdict", lambda *_a, **_kw: "codeowners_blocked"
    )
    assert _resolve(workspace, _SONAR_FAILED) == ("codeowners_blocked", "", None)


def test_pending_check_next_to_a_failed_advisory_one_stays_pending_without_reads(
    workspace: Workspace,
) -> None:
    shas = support.serve_check_runs(_SONAR_LISTING)
    reads = support.serve_pr_reads(_sonar_red())
    checks = [*_SONAR_FAILED, {"name": "slow", "state": "IN_PROGRESS"}]
    assert _resolve(workspace, checks) == ("pending", "", None)
    assert shas == []
    assert reads == []


def test_checks_without_a_failure_read_no_listing(workspace: Workspace) -> None:
    shas = support.serve_check_runs(_SONAR_LISTING)
    support.serve_pr_reads(support.pr_payload([support.passing_run("ci")], merge_state="CLEAN"))
    checks = [{"name": "ci", "state": "SUCCESS"}]
    assert _resolve(workspace, checks) == ("ready", support.HEAD_SHA, None)
    assert shas == []


def _approving_prior(workspace: Workspace) -> Handover:
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
                id="verif-approve", statement="judge approved PR #42", rationale="ok",
                confidence=1.0, reversibility="low",
            )
        ],
        full_synthesis="## Verdict\nVERDICT: APPROVE",
        pr_number=42,
    )


def _serve_clean_feedback(monkeypatch: pytest.MonkeyPatch) -> None:
    def run(cmd: Any, *, cwd: Any = None, timeout: float = 0.0, env: Any = None) -> Any:
        del cwd, timeout, env
        assert list(cmd)[:3] == ["gh", "api", "graphql"]
        return subprocess.CompletedProcess([], 0, stdout=_CLEAN_GRAPHQL, stderr="")

    monkeypatch.setattr(process_runner, "run_subprocess", run)


def _drive_team(
    workspace: Workspace, forge: ForgeScript, monkeypatch: pytest.MonkeyPatch, merge_state: str,
) -> Handover:
    monkeypatch.setenv("ALAN_PR_MERGE_POLL_INTERVAL_S", "0")
    monkeypatch.setenv("ALAN_PR_MERGE_TIMEOUT_S", "10")
    monkeypatch.setenv("ALAN_PR_MERGE_MAX_POLLS", "20")
    monkeypatch.setenv("ALAN_PR_MERGE_AUDIT_DISABLE", "1")
    monkeypatch.delenv("ALAN_PR_MERGE_DRY_RUN", raising=False)
    forge.queue("read_checks", checks_read(_SONAR_FAILED))
    forge.queue("merge_pr", merge_attempt())
    state_read: JsonObject = {"state": "OPEN", "mergeStateStatus": merge_state}
    support.serve_pr_reads(
        _sonar_red(merge_state), by_fields={("state", "mergeStateStatus"): state_read},
    )
    support.serve_check_runs(_SONAR_LISTING)
    _serve_clean_feedback(monkeypatch)
    return seed_and_run(TEAM, workspace, _approving_prior(workspace), lambda _e: None)


def test_team_merges_a_pr_whose_only_red_checks_are_advisory(
    workspace: Workspace, forge: ForgeScript, monkeypatch: pytest.MonkeyPatch,
) -> None:
    handover = _drive_team(workspace, forge, monkeypatch, "UNSTABLE")
    assert handover.handover_type == "terminal"
    [(args, _kwargs)] = forge.calls("merge_pr")
    assert args == (42, support.HEAD_SHA)


def test_team_still_refuses_a_dirty_pr_whose_only_red_checks_are_advisory(
    workspace: Workspace, forge: ForgeScript, monkeypatch: pytest.MonkeyPatch,
) -> None:
    handover = _drive_team(workspace, forge, monkeypatch, "DIRTY")
    assert handover.handover_type != "terminal"
    assert forge.calls("merge_pr") == []
