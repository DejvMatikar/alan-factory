from __future__ import annotations

import json
import subprocess
import time as _time_mod
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest

from alan import process_runner
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.teams import _pr_branch, _pr_post
from alan.teams import _verification_review_post as review_post_mod
from alan.teams import verification as verif_mod
from alan.teams._base import TeamEvent
from alan.teams._clause_actionability import ClauseActionability, DemotedClause
from alan.teams._clause_audit import ClauseAuditRow
from alan.teams.verification import (
    TEAM,
)
from alan.workspace import Workspace
from tests import _teams_verification_support as support
from tests._helpers import seed_and_run
from tests.fakes.forge import FakeForge
from tests.forge_stubs import ForgeScript, always, api_result, login_read

_INLINE_JUDGE_OUTPUT = support._INLINE_JUDGE_OUTPUT
_patch_streaming = support.patch_streaming
_impl_handover = support.impl_handover


@pytest.fixture(autouse=True)
def _stub_external_gates_default(monkeypatch: pytest.MonkeyPatch) -> None:
    support.stub_external_gates(monkeypatch)


@pytest.fixture(autouse=True)
def _stub_checkout_internals_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    support.stub_checkout_internals(monkeypatch, tmp_path)


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return support.make_workspace(tmp_path)


@pytest.fixture
def forge(_active_fake_forge: FakeForge) -> ForgeScript:
    return ForgeScript(_active_fake_forge)


@pytest.fixture
def audit_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return support.set_audit_root(tmp_path, monkeypatch)


def test_run_review_failure_does_not_raise(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path
) -> None:
    monkeypatch.delenv("ALAN_VERIFICATION_DRY_RUN", raising=False)
    _patch_streaming(monkeypatch)

    def failing_run(
        cmd: list[str], *, cwd: object, timeout: float, env: Mapping[str, str] | None = None  # noqa: ARG001
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="gh failed")

    monkeypatch.setattr(process_runner, "run_subprocess", failing_run)
    prior = _impl_handover(workspace)
    events: list[TeamEvent] = []
    handover = seed_and_run(TEAM, workspace, prior, events.append)
    assert handover.pr_number == 42
    assert handover.to_team_suggestions == []
    entries = [
        json.loads(line)
        for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
    ]
    assert any(e["action"] == "review_failed" for e in entries)
    assert any(e["action"] == "review_post_failed" for e in entries)
    assert any(e.kind == "review_post_failed" for e in events)


def test_run_review_timeout_does_not_raise(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path
) -> None:
    monkeypatch.delenv("ALAN_VERIFICATION_DRY_RUN", raising=False)
    _patch_streaming(monkeypatch)

    def timeout_run(
        cmd: list[str], *, cwd: object, timeout: float, env: Mapping[str, str] | None = None  # noqa: ARG001
    ) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)

    monkeypatch.setattr(process_runner, "run_subprocess", timeout_run)
    prior = _impl_handover(workspace)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert handover.to_team_suggestions == []
    entries = [
        json.loads(line)
        for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
    ]
    assert any(e["action"] == "review_failed" for e in entries)
    assert any(e["action"] == "review_post_failed" for e in entries)


def test_run_review_missing_gh_does_not_raise(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path
) -> None:
    monkeypatch.delenv("ALAN_VERIFICATION_DRY_RUN", raising=False)
    _patch_streaming(monkeypatch)

    def missing(
        cmd: list[str], *, cwd: object, timeout: float, env: Mapping[str, str] | None = None  # noqa: ARG001
    ) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("gh not installed")

    monkeypatch.setattr(process_runner, "run_subprocess", missing)
    prior = _impl_handover(workspace)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert handover.to_team_suggestions == []
    entries = [
        json.loads(line)
        for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
    ]
    assert any(e["action"] == "review_failed" for e in entries)
    assert any(e["action"] == "review_post_failed" for e in entries)


def test_review_post_failed_emits_team_event_with_pr_number(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    monkeypatch.delenv("ALAN_VERIFICATION_DRY_RUN", raising=False)
    _patch_streaming(monkeypatch)

    def failing_run(
        cmd: list[str], *, cwd: object, timeout: float, env: Mapping[str, str] | None = None  # noqa: ARG001
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="401 Unauthorized")

    monkeypatch.setattr(process_runner, "run_subprocess", failing_run)
    prior = _impl_handover(workspace)
    events: list[TeamEvent] = []
    seed_and_run(TEAM, workspace, prior, events.append)
    post_failed = [e for e in events if e.kind == "review_post_failed"]
    assert len(post_failed) == 1
    assert post_failed[0].payload["pr_number"] == "42"
    assert post_failed[0].payload["task_id"] == workspace.task_id


def test_review_post_failed_preserves_review_artifact(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    monkeypatch.delenv("ALAN_VERIFICATION_DRY_RUN", raising=False)
    _patch_streaming(monkeypatch)

    def failing_run(
        cmd: list[str], *, cwd: object, timeout: float, env: Mapping[str, str] | None = None  # noqa: ARG001
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")

    monkeypatch.setattr(process_runner, "run_subprocess", failing_run)
    prior = _impl_handover(workspace)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert len(handover.artifacts_produced) == 1
    artifact = handover.artifacts_produced[0]
    assert "verification_review_" in artifact.path
    assert Path(artifact.path).exists()


def test_review_post_failed_summary_names_token(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    monkeypatch.delenv("ALAN_VERIFICATION_DRY_RUN", raising=False)
    _patch_streaming(monkeypatch)

    def failing_run(
        cmd: list[str], *, cwd: object, timeout: float, env: Mapping[str, str] | None = None  # noqa: ARG001
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")

    monkeypatch.setattr(process_runner, "run_subprocess", failing_run)
    prior = _impl_handover(workspace)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert "ALAN_VERIFIER_GH_TOKEN" in handover.full_synthesis


def test_inline_fallback_to_legacy_on_inline_failure_routes_normally(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path, forge: ForgeScript
) -> None:
    monkeypatch.delenv("ALAN_VERIFICATION_DRY_RUN", raising=False)
    _patch_streaming(monkeypatch, judge_output=_INLINE_JUDGE_OUTPUT)
    forge.queue("post_review", api_result(returncode=1, stderr="inline failed"), api_result("{}"))
    prior = _impl_handover(workspace)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert handover.to_team_suggestions == ["implementation_fix"]
    entries = [
        json.loads(line)
        for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
    ]
    assert any(e["action"] == "review_failed_inline" for e in entries)
    assert any(e["action"] == "review_inline_fallback" for e in entries)
    assert any(e["action"] == "review_ok" for e in entries)
    assert not any(e["action"] == "review_post_failed" for e in entries)
    failed = next(e for e in entries if e["action"] == "review_failed_inline")
    assert failed["returncode"] == 1
    assert failed["stderr"] == "inline failed"
    assert "error" not in failed
    payloads = [cast("dict[str, list[object]]", args[1]) for args, _ in forge.calls("post_review")]
    assert [len(payload["comments"]) for payload in payloads] == [2, 0]
    assert not any("error_class" in e or "transient" in e for e in entries)


def test_inline_fallback_to_legacy_audit_trail(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path, forge: ForgeScript
) -> None:
    monkeypatch.delenv("ALAN_VERIFICATION_DRY_RUN", raising=False)
    _patch_streaming(monkeypatch, judge_output=_INLINE_JUDGE_OUTPUT)
    forge.queue("post_review", api_result(returncode=1, stderr="inline failed"), api_result("{}"))
    prior = _impl_handover(workspace)
    seed_and_run(TEAM, workspace, prior, lambda _e: None)
    entries = [
        json.loads(line)
        for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
    ]
    fallback = next(e for e in entries if e["action"] == "review_inline_fallback")
    assert fallback["verdict"] == "REQUEST_CHANGES"
    assert fallback["pr_number"] == 42
    assert "inv_id" in fallback
    review_ok = next(e for e in entries if e["action"] == "review_ok")
    assert set(review_ok) == {
        "task_id", "action", "pr_number", "verdict", "event",
        "gh_user_override", "inv_id", "timestamp_utc",
    }


def test_apply_inline_pr_review_records_returncode_and_stderr(
    monkeypatch: pytest.MonkeyPatch, audit_root: Path
) -> None:
    monkeypatch.setattr(
        _pr_post,
        "post_pr_review_with_inline_comments",
        lambda *_a, **_kw: _pr_post.ReviewPostOutcome(
            ok=False, returncode=1, stderr="x" * 600, error=None
        ),
    )
    findings = [_pr_post.InlineFinding(path="alan/x.py", line=10, body="bad")]
    outcome = review_post_mod._apply_inline_pr_review(  # noqa: SLF001
        42, "REQUEST_CHANGES", "body", "task-x", findings, "REQUEST_CHANGES", "inv_1",
        forge_target=DEFAULT_FORGE_TARGET, as_verifier=False,
    )
    assert outcome.ok is False
    entries = [
        json.loads(line)
        for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
    ]
    row = next(e for e in entries if e["action"] == "review_failed_inline")
    assert row["returncode"] == 1
    assert row["stderr"] == "x" * 500
    assert "error" not in row


def test_apply_inline_pr_review_records_error_on_raise_case(
    monkeypatch: pytest.MonkeyPatch, audit_root: Path
) -> None:
    monkeypatch.setattr(
        _pr_post,
        "post_pr_review_with_inline_comments",
        lambda *_a, **_kw: _pr_post.ReviewPostOutcome(
            ok=False, returncode=None, stderr=None, error="gh"
        ),
    )
    findings = [_pr_post.InlineFinding(path="alan/x.py", line=10, body="bad")]
    outcome = review_post_mod._apply_inline_pr_review(  # noqa: SLF001
        42, "REQUEST_CHANGES", "body", "task-x", findings, "REQUEST_CHANGES", "inv_1",
        forge_target=DEFAULT_FORGE_TARGET, as_verifier=False,
    )
    assert outcome.ok is False
    entries = [
        json.loads(line)
        for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
    ]
    row = next(e for e in entries if e["action"] == "review_failed_inline")
    assert row["error"] == "gh"
    assert "returncode" not in row
    assert "stderr" not in row


def test_inline_and_legacy_both_fail_routes_to_await_human(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path
) -> None:
    monkeypatch.delenv("ALAN_VERIFICATION_DRY_RUN", raising=False)
    _patch_streaming(monkeypatch, judge_output=_INLINE_JUDGE_OUTPUT)

    def fake_run(
        cmd: list[str], *, cwd: object, timeout: float, env: Mapping[str, str] | None = None  # noqa: ARG001
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="auth failure")

    monkeypatch.setattr(process_runner, "run_subprocess", fake_run)
    prior = _impl_handover(workspace)
    events: list[TeamEvent] = []
    handover = seed_and_run(TEAM, workspace, prior, events.append)
    assert handover.to_team_suggestions == []
    assert handover.pr_number == 42
    entries = [
        json.loads(line)
        for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
    ]
    assert any(e["action"] == "review_failed_inline" for e in entries)
    assert any(e["action"] == "review_inline_fallback" for e in entries)
    assert any(e["action"] == "review_failed" for e in entries)
    assert any(e["action"] == "review_post_failed" for e in entries)
    assert any(e.kind == "review_post_failed" for e in events)


def test_emit_review_post_failed_helper(
    workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    prior = _impl_handover(workspace, pr_number=77)
    events: list[TeamEvent] = []
    handover = verif_mod._emit_review_post_failed(  # noqa: SLF001
        workspace, prior, events.append, "inv_x", "judge was here",
        forge_target=DEFAULT_FORGE_TARGET,
    )
    assert handover.to_team_suggestions == []
    assert handover.pr_number == 77
    assert handover.decisions == []
    assert handover.scenario_results == []
    assert len(handover.artifacts_produced) == 1
    assert any(e.kind == "review_post_failed" for e in events)
    entries = [
        json.loads(line)
        for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
    ]
    row = next(e for e in entries if e["action"] == "review_post_failed")
    assert row["pr_number"] == 77
    assert row["inv_id"] == "inv_x"
    artifact_content = Path(handover.artifacts_produced[0].path).read_text()
    assert "judge was here" in artifact_content


def test_emit_review_post_failed_helper_no_prior(
    workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    events: list[TeamEvent] = []
    handover = verif_mod._emit_review_post_failed(  # noqa: SLF001
        workspace, None, events.append, "inv_y", "judge output",
        forge_target=DEFAULT_FORGE_TARGET,
    )
    assert handover.pr_number is None
    assert handover.to_team_suggestions == []
    assert handover.decisions == []
    assert any(e.kind == "review_post_failed" for e in events)
    assert events[0].payload["pr_number"] == ""


def test_classify_review_post_failure_missing_token_when_absent() -> None:
    result = _pr_branch.classify_credential_failure(
        token_present=False, returncode=1, stderr="401 Unauthorized",
    )
    assert result == "missing_token"


def test_classify_review_post_failure_expired_token_marker() -> None:
    result = _pr_branch.classify_credential_failure(
        token_present=True, returncode=1, stderr="HTTP 401: Bad credentials",
    )
    assert result == "expired_token"


def test_classify_review_post_failure_expired_token_case_insensitive() -> None:
    result = _pr_branch.classify_credential_failure(
        token_present=True, returncode=1, stderr="Unauthorized",
    )
    assert result == "expired_token"


def test_classify_review_post_failure_transient_ambiguous_default() -> None:
    result = _pr_branch.classify_credential_failure(
        token_present=True, returncode=1, stderr="boom",
    )
    assert result == "transient"


def test_classify_review_post_failure_transient_when_returncode_none() -> None:
    result = _pr_branch.classify_credential_failure(
        token_present=True, returncode=None, stderr=None,
    )
    assert result == "transient"


def test_classify_review_post_failure_never_misreports_present_token_as_missing() -> None:
    result = _pr_branch.classify_credential_failure(
        token_present=True, returncode=1, stderr="connection reset by peer",
    )
    assert result != "missing_token"
    assert result == "transient"


def test_review_post_failed_body_missing_token_names_env_var() -> None:
    body = review_post_mod._review_post_failed_body(  # noqa: SLF001
        "missing_token", 42, 1, forge_target=DEFAULT_FORGE_TARGET
    )
    assert "PR #42" in body
    assert "ALAN_VERIFIER_GH_TOKEN" in body
    assert "missing credential" in body


def test_review_post_failed_body_expired_token_names_rotate() -> None:
    body = review_post_mod._review_post_failed_body(  # noqa: SLF001
        "expired_token", 42, 1, forge_target=DEFAULT_FORGE_TARGET
    )
    assert "expired credential" in body
    assert "reviewer credential" in body
    assert "stable fallback name" in body
    assert "ALAN_VERIFIER_GH_TOKEN" in body
    assert "re-dispatch" in body


def test_review_post_failed_body_transient_names_attempts_and_redispatch() -> None:
    body = review_post_mod._review_post_failed_body(  # noqa: SLF001
        "transient", 42, 4, forge_target=DEFAULT_FORGE_TARGET
    )
    assert "transient failure" in body
    assert "Attempted 4 time(s)" in body
    assert "Re-dispatch verification" in body


def test_review_post_failed_body_no_pr_number_uses_generic_tag() -> None:
    body = review_post_mod._review_post_failed_body(  # noqa: SLF001
        "transient", None, 1, forge_target=DEFAULT_FORGE_TARGET
    )
    assert "this PR" in body


def test_review_post_retry_max_zero_is_honored_not_treated_as_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_REVIEW_POST_RETRY_MAX", "0")
    assert review_post_mod._review_post_retry_max() == 0  # noqa: SLF001


def test_review_post_retry_max_negative_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_REVIEW_POST_RETRY_MAX", "-1")
    assert (
        review_post_mod._review_post_retry_max()  # noqa: SLF001
        == review_post_mod._DEFAULT_REVIEW_POST_RETRY_MAX  # noqa: SLF001
    )


def test_review_post_retry_max_non_numeric_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALAN_REVIEW_POST_RETRY_MAX", "not-a-number")
    assert (
        review_post_mod._review_post_retry_max()  # noqa: SLF001
        == review_post_mod._DEFAULT_REVIEW_POST_RETRY_MAX  # noqa: SLF001
    )


def test_review_post_retry_max_unset_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALAN_REVIEW_POST_RETRY_MAX", raising=False)
    assert (
        review_post_mod._review_post_retry_max()  # noqa: SLF001
        == review_post_mod._DEFAULT_REVIEW_POST_RETRY_MAX  # noqa: SLF001
    )


def test_post_review_with_retries_zero_max_disables_retry(
    monkeypatch: pytest.MonkeyPatch, audit_root: Path  # noqa: ARG001
) -> None:
    monkeypatch.setenv("ALAN_VERIFIER_GH_TOKEN", "tok-xyz")
    monkeypatch.setenv("ALAN_REVIEW_POST_RETRY_MAX", "0")
    monkeypatch.setattr(_time_mod, "sleep", lambda _: None)

    def failing_run(
        cmd: list[str], *, cwd: object, timeout: float, env: Mapping[str, str] | None = None  # noqa: ARG001
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")

    monkeypatch.setattr(process_runner, "run_subprocess", failing_run)
    outcome, failure_class, attempts = verif_mod._post_review_with_retries(  # noqa: SLF001
        42, "APPROVE", "body", "task-x", "APPROVE", "inv_1",
        forge_target=DEFAULT_FORGE_TARGET,
    )
    assert outcome.ok is False
    assert failure_class == "transient"
    assert attempts == 1


def test_post_review_with_retries_gives_up_returns_attempts(
    monkeypatch: pytest.MonkeyPatch, audit_root: Path  # noqa: ARG001
) -> None:
    monkeypatch.setenv("ALAN_VERIFIER_GH_TOKEN", "tok-xyz")
    monkeypatch.setenv("ALAN_REVIEW_POST_RETRY_MAX", "1")
    monkeypatch.setattr(_time_mod, "sleep", lambda _: None)

    def failing_run(
        cmd: list[str], *, cwd: object, timeout: float, env: Mapping[str, str] | None = None  # noqa: ARG001
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")

    monkeypatch.setattr(process_runner, "run_subprocess", failing_run)
    outcome, failure_class, attempts = verif_mod._post_review_with_retries(  # noqa: SLF001
        42, "APPROVE", "body", "task-x", "APPROVE", "inv_1",
        forge_target=DEFAULT_FORGE_TARGET,
    )
    assert outcome.ok is False
    assert failure_class == "transient"
    assert attempts == 2


def test_transient_review_post_retries_then_gives_up(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path
) -> None:
    monkeypatch.delenv("ALAN_VERIFICATION_DRY_RUN", raising=False)
    monkeypatch.setenv("ALAN_VERIFIER_GH_TOKEN", "tok-123")
    monkeypatch.setenv("ALAN_REVIEW_POST_RETRY_MAX", "2")
    monkeypatch.setattr(_time_mod, "sleep", lambda _: None)
    _patch_streaming(monkeypatch)
    calls: list[int] = []

    def failing_run(
        cmd: list[str], *, cwd: object, timeout: float, env: Mapping[str, str] | None = None  # noqa: ARG001
    ) -> subprocess.CompletedProcess[str]:
        calls.append(1)
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")

    monkeypatch.setattr(process_runner, "run_subprocess", failing_run)
    prior = _impl_handover(workspace)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert handover.to_team_suggestions == []
    entries = [
        json.loads(line)
        for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
    ]
    retries = [e for e in entries if e["action"] == "review_post_retry"]
    assert len(retries) == 2
    assert [r["attempt"] for r in retries] == [1, 2]
    failed = next(e for e in entries if e["action"] == "review_post_failed")
    assert failed["failure_class"] == "transient"
    assert failed["attempts"] == 3
    assert len(calls) == 3
    assert "transient failure" in handover.full_synthesis
    assert "Re-dispatch verification" in handover.full_synthesis
    assert handover.credential_failure is None


def test_transient_review_post_recovers_after_one_retry(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path,
    _active_fake_forge: FakeForge,
) -> None:
    monkeypatch.delenv("ALAN_VERIFICATION_DRY_RUN", raising=False)
    monkeypatch.setenv("ALAN_VERIFIER_GH_TOKEN", "tok-123")
    monkeypatch.setattr(_time_mod, "sleep", lambda _: None)
    _patch_streaming(monkeypatch)
    always(_active_fake_forge, "viewer_login", login_read(None, outcome="failed"))
    calls: dict[str, int] = {"n": 0}

    def flaky_run(
        cmd: list[str], *, cwd: object, timeout: float, env: Mapping[str, str] | None = None  # noqa: ARG001
    ) -> subprocess.CompletedProcess[str]:
        calls["n"] += 1
        if calls["n"] == 1:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(process_runner, "run_subprocess", flaky_run)
    prior = _impl_handover(workspace)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert handover.to_team_suggestions == ["pr_merge"]
    entries = [
        json.loads(line)
        for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
    ]
    assert any(e["action"] == "review_post_retry" for e in entries)
    assert any(e["action"] == "review_ok" for e in entries)
    assert not any(e["action"] == "review_post_failed" for e in entries)
    held = next(e for e in entries if e["action"] == "verifier_thread_resolution_held")
    assert (held["reason"], held["error"]) == ("identity_check_failed", "empty login")
    assert calls["n"] == 2


def test_expired_token_review_post_not_retried(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path
) -> None:
    monkeypatch.delenv("ALAN_VERIFICATION_DRY_RUN", raising=False)
    monkeypatch.setenv("ALAN_VERIFIER_GH_TOKEN", "tok-123")
    monkeypatch.setattr(_time_mod, "sleep", lambda _: None)
    _patch_streaming(monkeypatch)
    calls: list[int] = []

    def failing_run(
        cmd: list[str], *, cwd: object, timeout: float, env: Mapping[str, str] | None = None  # noqa: ARG001
    ) -> subprocess.CompletedProcess[str]:
        calls.append(1)
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="401 Bad credentials")

    monkeypatch.setattr(process_runner, "run_subprocess", failing_run)
    prior = _impl_handover(workspace)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    entries = [
        json.loads(line)
        for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
    ]
    assert not any(e["action"] == "review_post_retry" for e in entries)
    failed = next(e for e in entries if e["action"] == "review_post_failed")
    assert failed["failure_class"] == "expired_token"
    assert len(calls) == 1
    assert "expired credential" in handover.full_synthesis
    assert "stable fallback name" in handover.full_synthesis
    assert handover.credential_failure == "expired_token"


def test_missing_token_review_post_not_retried(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path
) -> None:
    monkeypatch.delenv("ALAN_VERIFICATION_DRY_RUN", raising=False)
    monkeypatch.delenv("ALAN_VERIFIER_GH_TOKEN", raising=False)
    monkeypatch.setattr(_time_mod, "sleep", lambda _: None)
    _patch_streaming(monkeypatch)
    calls: list[int] = []

    def failing_run(
        cmd: list[str], *, cwd: object, timeout: float, env: Mapping[str, str] | None = None  # noqa: ARG001
    ) -> subprocess.CompletedProcess[str]:
        calls.append(1)
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")

    monkeypatch.setattr(process_runner, "run_subprocess", failing_run)
    prior = _impl_handover(workspace)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    entries = [
        json.loads(line)
        for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
    ]
    assert not any(e["action"] == "review_post_retry" for e in entries)
    failed = next(e for e in entries if e["action"] == "review_post_failed")
    assert failed["failure_class"] == "missing_token"
    assert len(calls) == 1
    assert "missing credential" in handover.full_synthesis
    assert handover.credential_failure is None


def _demoted(classification: ClauseActionability, req_id: str = "req-a") -> DemotedClause:
    row = ClauseAuditRow(req_id=req_id, verdict="FAIL", evidence="evidence")
    return DemotedClause(
        row=row, classification=classification, check_name=None, check_outcome=None,
    )


def test_emit_clause_pr_state_escalation_default_uses_pr_state_options(
    workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    events: list[TeamEvent] = []
    handover = review_post_mod._emit_clause_pr_state_escalation(  # noqa: SLF001
        workspace, None, events.append, "inv_z", "judge output",
    )
    oq = handover.open_questions[0]
    assert oq.question == review_post_mod._CLAUSE_PR_STATE_QUESTION  # noqa: SLF001
    assert oq.proposed_answer_space == list(review_post_mod._CLAUSE_PR_STATE_OPTIONS)  # noqa: SLF001
    assert oq.recommended == review_post_mod._CLAUSE_PR_STATE_OPTIONS[0]  # noqa: SLF001
    assert oq.option_resolvers == {}


def test_emit_judge_parse_failed_still_emits_empty_option_resolvers(
    workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    events: list[TeamEvent] = []
    handover = verif_mod._emit_judge_parse_failed(  # noqa: SLF001
        workspace, None, events.append, "inv_z", "judge output",
    )
    oq = handover.open_questions[0]
    assert oq.question == verif_mod._JUDGE_PARSE_FAIL_QUESTION  # noqa: SLF001
    assert oq.option_resolvers == {}
    assert oq.question_class == "verification_escalation"


def test_emit_capability_boundary_escalation_offers_explicit_resolvers(
    workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    events: list[TeamEvent] = []
    handover = review_post_mod._emit_capability_boundary_escalation(  # noqa: SLF001
        workspace, None, events.append, "inv_z", "## Verdict\n\nVERDICT: REQUEST_CHANGES\n",
        reason="rotating the prod signing key needs a human with vault access.",
    )
    assert handover.to_team_suggestions == []
    assert handover.decisions == []
    assert len(handover.open_questions) == 1
    oq = handover.open_questions[0]
    assert oq.question_class == "verification_escalation"
    assert oq.blocking is True
    assert oq.proposed_answer_space == [
        "Resolve this outside the fix-worker loop, then re-dispatch verification.",
        "Waive this finding and proceed to merge.",
    ]
    assert oq.recommended == (
        "Resolve this outside the fix-worker loop, then re-dispatch verification."
    )
    assert oq.option_resolvers == {
        "Resolve this outside the fix-worker loop, then re-dispatch verification.": "verification",
        "Waive this finding and proceed to merge.": "pr_merge",
    }
    assert "rotating the prod signing key needs a human with vault access." in oq.question
    assert any(e.kind == "capability_boundary_escalation" for e in events)


def test_emit_capability_boundary_escalation_full_synthesis_is_the_question_not_judge_output(
    workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    judge_output = (
        "## Verdict\n\nVERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 1\n\n"
        "## Rationale\nSome detailed reasoning that must not leak into the "
        "open question's full_synthesis.\n"
        "NO_FLEET_ROLE_CAN_FIX: needs a human with prod database access.\n"
    )
    handover = review_post_mod._emit_capability_boundary_escalation(  # noqa: SLF001
        workspace, None, lambda _e: None, "inv_z", judge_output,
        reason="needs a human with prod database access.",
    )
    assert handover.full_synthesis == handover.open_questions[0].question
    assert "DIFF_FINDINGS_CRITICAL" not in handover.full_synthesis
    assert "detailed reasoning" not in handover.full_synthesis
    assert "needs a human with prod database access." in handover.full_synthesis


def test_capability_boundary_marker_parks_without_fix_dispatch(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path
) -> None:
    monkeypatch.delenv("ALAN_VERIFICATION_DRY_RUN", raising=False)
    judge_output = (
        "## Verdict\n\n"
        "VERDICT: REQUEST_CHANGES\n"
        "DIFF_FINDINGS_CRITICAL: 1\n\n"
        "## Findings to address\n"
        "Rotating the prod signing key requires a human with vault access.\n\n"
        "NO_FLEET_ROLE_CAN_FIX: rotating the prod signing key requires "
        "access no fleet worker holds.\n"
    )
    _patch_streaming(monkeypatch, judge_output=judge_output)

    def fake_run(
        cmd: list[str], *, cwd: object, timeout: float, env: Mapping[str, str] | None = None  # noqa: ARG001
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(process_runner, "run_subprocess", fake_run)
    prior = _impl_handover(workspace)
    events: list[TeamEvent] = []
    handover = seed_and_run(TEAM, workspace, prior, events.append)
    assert handover.to_team_suggestions == []
    assert len(handover.open_questions) == 1
    oq = handover.open_questions[0]
    assert oq.question_class == "verification_escalation"
    assert oq.blocking is True
    assert oq.option_resolvers == {
        "Resolve this outside the fix-worker loop, then re-dispatch verification.": "verification",
        "Waive this finding and proceed to merge.": "pr_merge",
    }
    assert "rotating the prod signing key requires" in handover.full_synthesis
    assert "DIFF_FINDINGS_CRITICAL" not in handover.full_synthesis
    entries = [
        json.loads(line)
        for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
    ]
    assert any(e["action"] == "capability_boundary_escalation" for e in entries)


def test_emit_capability_boundary_escalation_question_id_includes_invocation_nonce(
    workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    first = review_post_mod._emit_capability_boundary_escalation(  # noqa: SLF001
        workspace, None, lambda _e: None, "inv_aaaaaaaa11", "judge output",
        reason="needs a human with prod database access.",
    )
    second = review_post_mod._emit_capability_boundary_escalation(  # noqa: SLF001
        workspace, None, lambda _e: None, "inv_bbbbbbbb22", "judge output",
        reason="needs a human with prod database access.",
    )
    first_id = first.open_questions[0].id
    second_id = second.open_questions[0].id
    assert first_id != second_id
    assert first_id == "verif-capability-boundary-inv_aaaa"
    assert second_id == "verif-capability-boundary-inv_bbbb"


def test_capability_boundary_marker_on_approve_verdict_does_not_park(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path
) -> None:
    monkeypatch.delenv("ALAN_VERIFICATION_DRY_RUN", raising=False)
    judge_output = (
        "## Verdict\n\n"
        "VERDICT: APPROVE\n"
        "DIFF_FINDINGS_CRITICAL: 0\n\n"
        "NO_FLEET_ROLE_CAN_FIX: this stray line must not park an approval.\n"
    )
    _patch_streaming(monkeypatch, judge_output=judge_output)

    def fake_run(
        cmd: list[str], *, cwd: object, timeout: float, env: Mapping[str, str] | None = None  # noqa: ARG001
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(process_runner, "run_subprocess", fake_run)
    prior = _impl_handover(workspace)
    events: list[TeamEvent] = []
    handover = seed_and_run(TEAM, workspace, prior, events.append)
    assert handover.to_team_suggestions == ["pr_merge"]
    assert handover.open_questions == []
    entries = [
        json.loads(line)
        for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
    ]
    assert not any(e["action"] == "capability_boundary_escalation" for e in entries)


def test_emit_clause_pr_state_escalation_host_env_recommends_checkout_fix(
    workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    events: list[TeamEvent] = []
    rows = (_demoted(ClauseActionability.HOST_ENV),)
    handover = review_post_mod._emit_clause_pr_state_escalation(  # noqa: SLF001
        workspace, None, events.append, "inv_z", "judge output", unactionable_rows=rows,
    )
    oq = handover.open_questions[0]
    recommended = oq.recommended
    assert recommended is not None
    assert "host-environment pollution" in oq.question
    assert "Update the PR's state on GitHub" not in recommended
    assert "fresh worker checkout" in recommended
    assert oq.proposed_answer_space[-1] == "Override the clause manually and proceed."


def test_emit_clause_pr_state_escalation_check_contradicted_recommends_investigation(
    workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    events: list[TeamEvent] = []
    rows = (_demoted(ClauseActionability.CHECK_CONTRADICTED),)
    handover = review_post_mod._emit_clause_pr_state_escalation(  # noqa: SLF001
        workspace, None, events.append, "inv_z", "judge output", unactionable_rows=rows,
    )
    oq = handover.open_questions[0]
    recommended = oq.recommended
    assert recommended is not None
    assert "Update the PR's state on GitHub" not in recommended
    assert "diverged from the green required check" in recommended


def test_operator_only_escalation_names_clauses_and_offers_tracking_issue(
    workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    events: list[TeamEvent] = []
    rows = (
        _demoted(ClauseActionability.OPERATOR_ONLY, req_id="req-a"),
        _demoted(ClauseActionability.OPERATOR_ONLY, req_id="req-b"),
    )
    handover = review_post_mod._emit_clause_pr_state_escalation(  # noqa: SLF001
        workspace, None, events.append, "inv_z", "judge output", unactionable_rows=rows,
    )
    oq = handover.open_questions[0]
    assert "req-a" in oq.question
    assert "req-b" in oq.question
    assert any("tracking issue" in option for option in oq.proposed_answer_space)
    assert oq.recommended is not None
    assert "tracking issue" in oq.recommended


def test_operator_only_escalation_question_id_distinct_from_pr_state(
    workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    operator_only_handover = review_post_mod._emit_clause_pr_state_escalation(  # noqa: SLF001
        workspace, None, lambda _e: None, "inv_z", "judge output",
        unactionable_rows=(_demoted(ClauseActionability.OPERATOR_ONLY),),
    )
    pr_state_handover = review_post_mod._emit_clause_pr_state_escalation(  # noqa: SLF001
        workspace, None, lambda _e: None, "inv_z", "judge output",
        unactionable_rows=(_demoted(ClauseActionability.PR_STATE),),
    )
    mixed_handover = review_post_mod._emit_clause_pr_state_escalation(  # noqa: SLF001
        workspace, None, lambda _e: None, "inv_z", "judge output",
        unactionable_rows=(
            _demoted(ClauseActionability.OPERATOR_ONLY, req_id="req-a"),
            _demoted(ClauseActionability.PR_STATE, req_id="req-b"),
        ),
    )
    assert operator_only_handover.open_questions[0].id == "clause-audit-operator-only"
    assert pr_state_handover.open_questions[0].id == "clause-audit-pr-state"
    assert mixed_handover.open_questions[0].id == "clause-audit-operator-only"
    assert operator_only_handover.open_questions[0].id != pr_state_handover.open_questions[0].id


def test_mixed_unactionable_batch_only_maps_operator_only_options_to_pr_merge(
    workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    handover = review_post_mod._emit_clause_pr_state_escalation(  # noqa: SLF001
        workspace, None, lambda _e: None, "inv_z", "judge output",
        unactionable_rows=(
            _demoted(ClauseActionability.OPERATOR_ONLY, req_id="req-a"),
            _demoted(ClauseActionability.PR_STATE, req_id="req-b"),
        ),
    )
    oq = handover.open_questions[0]
    pr_state_option = "Update the PR's state on GitHub, then re-dispatch verification."
    assert pr_state_option in oq.proposed_answer_space
    assert pr_state_option not in oq.option_resolvers
    assert pr_state_option not in oq.option_effects
    for option in oq.proposed_answer_space:
        if option == pr_state_option:
            continue
        assert oq.option_resolvers[option] == "pr_merge"
        assert oq.option_effects[option] == "closes"


def test_audit_dir_mkdir_failure_swallowed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    blocker = tmp_path / "logs-blocker"
    blocker.write_text("not a dir")
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(blocker))
    caplog.set_level("WARNING")
    verif_mod.append_audit({"task_id": "t", "action": "test"})
    assert any(
        "verification audit" in r.message for r in caplog.records
    )


def test_audit_fdopen_failure_swallowed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "verification_audit.jsonl").mkdir()
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    caplog.set_level("WARNING")
    verif_mod.append_audit({"task_id": "t", "action": "test"})
    assert any(
        "verification audit write failed" in r.message for r in caplog.records
    )


def test_audit_fdopen_partial_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    import os

    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))

    def boom_fdopen(fd: int, *_a: object, **_k: object) -> object:
        del fd
        raise OSError("disk gone")

    monkeypatch.setattr(os, "fdopen", boom_fdopen)
    caplog.set_level("WARNING")
    verif_mod.append_audit({"task_id": "t", "action": "test"})
    assert any(
        "verification audit write failed" in r.message for r in caplog.records
    )


def test_audit_file_is_owner_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    verif_mod.append_audit({"task_id": "t", "action": "test"})
    audit_path = tmp_path / "logs" / "verification_audit.jsonl"
    mode = audit_path.stat().st_mode & 0o777
    assert mode & 0o077 == 0


def test_audit_write_after_fdopen_failure_logged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    import os

    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))

    class FailingHandle:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def write(self, _data: str) -> int:
            raise OSError("disk full mid-write")

    def fake_fdopen(fd: int, *_a: object, **_k: object) -> object:
        os.close(fd)
        return FailingHandle()

    monkeypatch.setattr(os, "fdopen", fake_fdopen)
    caplog.set_level("WARNING")
    verif_mod.append_audit({"task_id": "t", "action": "test"})
    assert any(
        "verification audit write failed" in r.message for r in caplog.records
    )


def _bc_rows(logs_root: Path) -> list[dict[str, object]]:
    path = logs_root / "block_correction_audit.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_maybe_audit_inline_findings_attempt_skips_when_not_request_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    review_post_mod._maybe_audit_inline_findings_attempt(  # noqa: SLF001
        "t1", "APPROVE", "## INLINE_FINDINGS_YAML\n```yaml\n[]\n```\n", []
    )
    assert _bc_rows(tmp_path / "logs") == []


def test_maybe_audit_inline_findings_attempt_skips_when_no_block_and_no_findings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    review_post_mod._maybe_audit_inline_findings_attempt(  # noqa: SLF001
        "t1", "REQUEST_CHANGES", "no block here at all", []
    )
    assert _bc_rows(tmp_path / "logs") == []


def test_maybe_audit_inline_findings_attempt_records_unreachable_when_malformed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    body = "## INLINE_FINDINGS_YAML\n```yaml\nnot-a-list\n```\n"
    review_post_mod._maybe_audit_inline_findings_attempt(  # noqa: SLF001
        "t1", "REQUEST_CHANGES", body, []
    )
    rows = _bc_rows(tmp_path / "logs")
    assert rows[0]["site"] == "INLINE_FINDINGS_YAML"
    assert rows[0]["outcome"] == "unreachable_author"


def test_maybe_audit_inline_findings_attempt_records_clean_when_findings_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    finding = _pr_post.InlineFinding(path="alan/foo.py", line=1, body="bad")
    review_post_mod._maybe_audit_inline_findings_attempt(  # noqa: SLF001
        "t1", "REQUEST_CHANGES", _INLINE_JUDGE_OUTPUT, [finding]
    )
    rows = _bc_rows(tmp_path / "logs")
    assert rows[0]["outcome"] == "clean"
