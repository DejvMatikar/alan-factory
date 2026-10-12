from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.forge import ApiResult
from alan.teams import (
    _forge_errors,
    _pr_branch,
    _pr_flake_ledger,
    _pr_merge_checks,
    _pr_state_paths,
)
from alan.teams import pr_merge as pr_merge_mod
from alan.workspace import Workspace, create
from tests.fakes.forge import FakeForge
from tests.forge_stubs import (
    ForgeScript,
    api_result,
    checks_read,
    merge_attempt,
    pr_read,
    readiness_fields,
)

_SHA = "a" * 40
_TARGET = DEFAULT_FORGE_TARGET
_RATE_LIMIT = "You have exceeded a secondary rate limit (HTTP 403)"
_GREEN = [{"name": "ci", "conclusion": "SUCCESS", "status": "COMPLETED"}]


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return create("pr-merge-task", "gate the merge", root=tmp_path / "ws")


@pytest.fixture
def forge(_active_fake_forge: FakeForge) -> ForgeScript:
    script = ForgeScript(_active_fake_forge)
    script.queue("read_pr", pr_read(readiness_fields(rollup=_GREEN, head_sha=_SHA)))
    return script


@pytest.fixture
def audits(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    monkeypatch.setattr(pr_merge_mod, "_audit", rows.append)
    return rows


def _merge(workspace: Workspace, forge: ForgeScript, attempt: object) -> object:
    forge.queue("merge_pr", attempt)
    return pr_merge_mod._execute_merge(workspace, 42, _SHA, forge_target=_TARGET)


def test_merge_ok_returns_ready_and_pins_the_expected_head(
    workspace: Workspace, forge: ForgeScript, audits: list[dict[str, object]]
) -> None:
    outcome = _merge(workspace, forge, merge_attempt())
    assert isinstance(outcome, pr_merge_mod.PrMergeOutcome)
    assert outcome.kind == "ready"
    assert forge.calls("merge_pr") == [((42, _SHA), {"forge_target": _TARGET, "timeout": 60.0})]
    assert audits[-1]["action"] == "merge_ok"


def test_merge_policy_refusal_holds_without_an_outcome(
    workspace: Workspace, forge: ForgeScript, audits: list[dict[str, object]]
) -> None:
    refusal = merge_attempt(returncode=1, stderr="GraphQL: Base branch policy prohibits the merge")
    assert _merge(workspace, forge, refusal) is None
    assert audits[-1]["action"] == "merge_policy_hold"


def test_merge_head_mismatch_refusal_retries_without_an_outcome(
    workspace: Workspace, forge: ForgeScript, monkeypatch: pytest.MonkeyPatch
) -> None:
    stages: list[str] = []
    monkeypatch.setattr(
        pr_merge_mod, "_audit_head_mismatch", lambda *_a, stage, **_k: stages.append(stage),
    )
    refusal = merge_attempt(returncode=1, stderr="Head branch was modified. Review and try again.")
    assert _merge(workspace, forge, refusal) is None
    assert stages == ["gh_pr_merge_refusal"]


def test_merge_generic_failure_returns_an_error_outcome_with_diagnostics(
    workspace: Workspace, forge: ForgeScript, audits: list[dict[str, object]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pr_merge_mod, "_escalation_diagnostics", lambda *_a, **_k: "diag")
    outcome = _merge(
        workspace, forge, merge_attempt(returncode=1, stderr="Pull request is not mergeable"),
    )
    assert isinstance(outcome, pr_merge_mod.PrMergeOutcome)
    assert outcome.kind == "error"
    assert (outcome.diagnostics, outcome.credential_failure) == ("diag", None)
    assert outcome.reason == "gh pr merge rc=1: Pull request is not mergeable"
    assert audits[-1]["action"] == "merge_failed"


@pytest.mark.parametrize(
    "spawn_error", [FileNotFoundError("gh"), subprocess.TimeoutExpired("gh", 60.0)],
)
def test_merge_spawn_failure_raises_forge_transient_and_audits(
    workspace: Workspace, forge: ForgeScript, audits: list[dict[str, object]],
    spawn_error: BaseException,
) -> None:
    with pytest.raises(_forge_errors.ForgeTransientError):
        _merge(workspace, forge, spawn_error)
    assert audits[-1]["action"] == "merge_failed"


def test_escalation_diagnostics_renders_the_merge_attempt_and_live_readiness(
    workspace: Workspace, forge: ForgeScript, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_k: None)
    monkeypatch.setattr(_pr_state_paths, "gate_rerun_count_for_sha", lambda *_a, **_k: 0)
    monkeypatch.setattr(_pr_flake_ledger, "max_gate_reruns", lambda: 2)
    monkeypatch.setattr(_pr_flake_ledger, "load_gate_survived_checks", lambda *_a, **_k: {})
    monkeypatch.setattr(_pr_branch, "fetch_failing_e2e_tests", lambda *_a, **_k: None)
    diag = pr_merge_mod._escalation_diagnostics(
        workspace, 42, merge_attempt(returncode=1, stderr="nope"), forge_target=_TARGET,
    )
    assert "gh pr merge rc=1" in diag
    assert "nope" in diag
    assert "- mergeStateStatus: CLEAN" in diag


def test_checks_nonzero_exit_with_rows_is_still_trusted(forge: ForgeScript) -> None:
    forge.queue("read_checks", checks_read([{"name": "ci", "state": "FAILURE"}], returncode=8))
    assert pr_merge_mod._gh_pr_checks(42, forge_target=_TARGET) == [
        {"name": "ci", "state": "FAILURE"},
    ]
    assert forge.calls("read_checks") == [((42,), {"forge_target": _TARGET, "timeout": 60.0})]


def test_checks_infra_failure_without_rows_raises(forge: ForgeScript) -> None:
    forge.queue("read_checks", checks_read(returncode=1, stderr=_RATE_LIMIT))
    with pytest.raises(_forge_errors.ForgeRateLimited):
        pr_merge_mod._gh_pr_checks(42, forge_target=_TARGET)


def test_checks_unclassified_failure_without_rows_keeps_the_pending_fallback(
    forge: ForgeScript,
) -> None:
    forge.queue("read_checks", checks_read(returncode=8, stderr="exit status 8"))
    assert pr_merge_mod._gh_pr_checks(42, forge_target=_TARGET) == _pr_merge_checks.PENDING_FALLBACK


def test_checks_clean_exit_with_no_rows_returns_empty(forge: ForgeScript) -> None:
    forge.queue("read_checks", checks_read())
    assert pr_merge_mod._gh_pr_checks(42, forge_target=_TARGET) == []


@pytest.mark.parametrize(
    ("attempt", "updated"),
    [
        (api_result(), True),
        (api_result(returncode=1, stderr="boom"), False),
        (api_result(returncode=1, stderr="merge conflict"), False),
    ],
    ids=["updated", "failed", "conflict"],
)
def test_behind_during_merge_reports_the_branch_update_result(
    workspace: Workspace, forge: ForgeScript, monkeypatch: pytest.MonkeyPatch,
    attempt: ApiResult, updated: bool,
) -> None:
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_k: _SHA)
    monkeypatch.setattr(_pr_state_paths, "save_system_head", lambda *_a, **_k: None)
    forge.queue("request_branch_update", attempt)
    outcome = pr_merge_mod._behind_during_merge_outcome(workspace, 42, forge_target=_TARGET)
    suffix = "update-branch triggered" if updated else "update-branch failed"
    assert outcome.kind == "error"
    assert outcome.reason.endswith(suffix)
    assert forge.calls("request_branch_update") == [((42,), {"forge_target": _TARGET})]
