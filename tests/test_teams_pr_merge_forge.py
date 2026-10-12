from __future__ import annotations

import json
from pathlib import Path

import pytest

from alan import process_runner
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.forge import ApiResult
from alan.teams import (
    _forge_errors,
    _pr_branch,
    _pr_flake_ledger,
    _pr_merge_checks,
    _pr_ready,
    _pr_state_paths,
)
from alan.teams import pr_merge as pr_merge_mod
from alan.teams.pr_merge import TEAM
from alan.workspace import Workspace, create
from tests._helpers import seed_and_run
from tests._pr_branch_support import _audit_entries
from tests.fakes.forge import FakeForge
from tests.forge_stubs import ForgeScript, api_result, checks_read, merge_attempt
from tests.test_teams_pr_merge import (
    _approve_prior,
    _GhScript,
    _patch_poll_intervals,
    _queue_diagnostics_responses,
    _queue_open_green_checks,
    _readiness_read,
)

_RATE_LIMIT_STDERR = "You have exceeded a secondary rate limit (HTTP 403)"


@pytest.fixture(autouse=True)
def _silence_audit_log(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALAN_PR_MERGE_AUDIT_DISABLE", "1")


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return create("pr-merge-task", "gate the merge", root=tmp_path / "ws")


@pytest.fixture
def gh(monkeypatch: pytest.MonkeyPatch) -> _GhScript:
    script = _GhScript()
    monkeypatch.setattr(process_runner, "run_subprocess", script)
    return script


@pytest.fixture
def forge(_active_fake_forge: FakeForge) -> ForgeScript:
    return ForgeScript(_active_fake_forge)


def _queue_green_poll_to_merge(gh: _GhScript, forge: ForgeScript) -> None:
    _queue_open_green_checks(gh, forge)
    forge.queue("read_pr", *[_readiness_read(head_sha="sha-forge")] * 2)


def test_merge_rate_limit_raises_forge_rate_limited(
    workspace: Workspace, gh: _GhScript, forge: ForgeScript, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_poll_intervals(monkeypatch)
    _queue_green_poll_to_merge(gh, forge)
    forge.queue("merge_pr", merge_attempt(returncode=1, stderr=_RATE_LIMIT_STDERR))
    with pytest.raises(_forge_errors.ForgeRateLimited):
        seed_and_run(TEAM, workspace, _approve_prior(workspace, 42), lambda _e: None)


def test_merge_network_failure_raises_forge_transient(
    workspace: Workspace, gh: _GhScript, forge: ForgeScript, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_poll_intervals(monkeypatch)
    _queue_green_poll_to_merge(gh, forge)
    forge.queue("merge_pr", merge_attempt(returncode=1, stderr="dial tcp: i/o timeout"))
    with pytest.raises(_forge_errors.ForgeTransientError):
        seed_and_run(TEAM, workspace, _approve_prior(workspace, 42), lambda _e: None)


def test_merge_spawn_failure_raises_forge_transient(
    workspace: Workspace, gh: _GhScript, forge: ForgeScript, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_poll_intervals(monkeypatch)
    _queue_green_poll_to_merge(gh, forge)
    forge.queue("merge_pr", FileNotFoundError("gh"))
    with pytest.raises(_forge_errors.ForgeTransientError):
        seed_and_run(TEAM, workspace, _approve_prior(workspace, 42), lambda _e: None)


def test_merge_unknown_failure_keeps_escalation_without_credential_park(
    workspace: Workspace, gh: _GhScript, forge: ForgeScript, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_poll_intervals(monkeypatch)
    _queue_green_poll_to_merge(gh, forge)
    forge.queue("merge_pr", merge_attempt(returncode=1, stderr="Pull request is not mergeable"))
    _queue_diagnostics_responses(gh, forge)
    handover = seed_and_run(
        TEAM, workspace, _approve_prior(workspace, 42), lambda _e: None
    )
    assert handover.handover_type == "escalation"
    assert handover.credential_failure is None


def test_gh_pr_checks_rate_limit_without_json_raises(forge: ForgeScript) -> None:
    forge.queue("read_checks", checks_read(returncode=1, stderr=_RATE_LIMIT_STDERR))
    with pytest.raises(_forge_errors.ForgeRateLimited):
        pr_merge_mod._gh_pr_checks(42, forge_target=DEFAULT_FORGE_TARGET)


def test_gh_pr_checks_rc8_with_json_still_trusted(forge: ForgeScript) -> None:
    forge.queue("read_checks", checks_read([{"name": "ci", "state": "FAILURE"}], returncode=8))
    assert pr_merge_mod._gh_pr_checks(42, forge_target=DEFAULT_FORGE_TARGET) == [
        {"name": "ci", "state": "FAILURE"},
    ]


def test_gh_pr_checks_unknown_failure_without_json_keeps_pending_fallback(
    forge: ForgeScript,
) -> None:
    forge.queue("read_checks", checks_read(returncode=8, stderr="exit status 8"))
    checks = pr_merge_mod._gh_pr_checks(42, forge_target=DEFAULT_FORGE_TARGET)
    assert checks == _pr_merge_checks.PENDING_FALLBACK


def test_diagnostics_readiness_lines_absorbs_forge_transient_error(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a: object, **_kw: object) -> _pr_ready.PrReadiness:
        raise _forge_errors.ForgeTransientError("gh down")

    monkeypatch.setattr(_pr_ready, "check_pr_merge_readiness", boom)
    lines = pr_merge_mod._diagnostics_readiness_lines(
        42, workspace.task_id, forge_target=DEFAULT_FORGE_TARGET
    )
    assert lines == ["- readiness: unavailable (gh query failed)"]


def test_diagnostics_readiness_lines_absorbs_forge_rate_limited(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a: object, **_kw: object) -> _pr_ready.PrReadiness:
        raise _forge_errors.ForgeRateLimited("rate limited", 0.0)

    monkeypatch.setattr(_pr_ready, "check_pr_merge_readiness", boom)
    lines = pr_merge_mod._diagnostics_readiness_lines(
        42, workspace.task_id, forge_target=DEFAULT_FORGE_TARGET
    )
    assert lines == ["- readiness: unavailable (gh query failed)"]


def test_escalation_diagnostics_does_not_raise_on_forge_outage(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a: object, **_kw: object) -> _pr_ready.PrReadiness:
        raise _forge_errors.ForgeTransientError("gh down")

    monkeypatch.setattr(_pr_ready, "check_pr_merge_readiness", boom)
    monkeypatch.setattr(_pr_branch, "fetch_flake_guard_head_sha", lambda *_a, **_kw: None)
    monkeypatch.setattr(_pr_state_paths, "gate_rerun_count_for_sha", lambda *_a, **_kw: 0)
    monkeypatch.setattr(_pr_flake_ledger, "max_gate_reruns", lambda: 2)
    monkeypatch.setattr(_pr_flake_ledger, "load_gate_survived_checks", lambda *_a, **_kw: {})
    monkeypatch.setattr(_pr_branch, "fetch_failing_e2e_tests", lambda *_a, **_kw: None)
    diag = pr_merge_mod._escalation_diagnostics(
        workspace, 42, merge_attempt(returncode=1, stderr="nope"),
        forge_target=DEFAULT_FORGE_TARGET,
    )
    assert "readiness: unavailable" in diag


_HEAD_SHA_ARGV = (
    "gh", "pr", "view", "42", "--repo", "DejvMatikar/alan",
    "--json", "headRefOid", "-q", ".headRefOid",
)


def _run_behind_during_merge(
    workspace: Workspace, gh: _GhScript, forge: ForgeScript,
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, attempt: ApiResult,
) -> None:
    _patch_poll_intervals(monkeypatch)
    monkeypatch.delenv("ALAN_PR_MERGE_DRY_RUN", raising=False)
    monkeypatch.delenv("ALAN_PR_MERGE_AUDIT_DISABLE", raising=False)
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    _queue_open_green_checks(gh, forge)
    forge.queue("read_pr", *[_readiness_read("BEHIND")] * 2)
    forge.queue("request_branch_update", attempt)
    gh.queue(_HEAD_SHA_ARGV, stdout="sha-behind\n")
    gh.queue(_HEAD_SHA_ARGV, stdout="sha-behind\n")
    seed_and_run(TEAM, workspace, _approve_prior(workspace, 42), lambda _e: None)


def _assert_update_attempt_recorded(
    forge: ForgeScript, workspace: Workspace, tmp_path: Path, *, action: str, update_ok: bool,
) -> None:
    assert forge.calls("request_branch_update") == [
        ((42,), {"forge_target": DEFAULT_FORGE_TARGET}),
    ]
    rows = [e for e in _audit_entries(tmp_path) if str(e["action"]).startswith("pr_update_branch_")]
    assert [(e["action"], e["task_id"], e["pr_number"]) for e in rows] == [
        (action, workspace.task_id, 42),
    ]
    gate_file = tmp_path / "logs" / pr_merge_mod._MERGE_GATE_AUDIT_FILE
    gate_rows = [json.loads(line) for line in gate_file.read_text().splitlines()]
    deferred = [r for r in gate_rows if r["action"] == "merge_deferred_behind"]
    assert [(r["task_id"], r["pr_number"], r["update_ok"]) for r in deferred] == [
        (workspace.task_id, 42, update_ok),
    ]


def test_behind_during_merge_update_succeeds_records_ok_row_for_task(
    workspace: Workspace, gh: _GhScript, forge: ForgeScript, monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _run_behind_during_merge(workspace, gh, forge, monkeypatch, tmp_path, api_result())
    _assert_update_attempt_recorded(
        forge, workspace, tmp_path, action="pr_update_branch_ok", update_ok=True,
    )


def test_behind_during_merge_update_fails_records_failed_row_for_task(
    workspace: Workspace, gh: _GhScript, forge: ForgeScript, monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    attempt = api_result(returncode=1, stderr="boom")
    _run_behind_during_merge(workspace, gh, forge, monkeypatch, tmp_path, attempt)
    _assert_update_attempt_recorded(
        forge, workspace, tmp_path, action="pr_update_branch_failed", update_ok=False,
    )


def test_behind_during_merge_update_conflict_records_failed_row_for_task(
    workspace: Workspace, gh: _GhScript, forge: ForgeScript, monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    attempt = api_result(returncode=1, stderr="merge conflict")
    _run_behind_during_merge(workspace, gh, forge, monkeypatch, tmp_path, attempt)
    _assert_update_attempt_recorded(
        forge, workspace, tmp_path, action="pr_update_branch_failed", update_ok=False,
    )
