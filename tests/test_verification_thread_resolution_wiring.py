from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import alan.teams._verifier_thread_resolution as vtr_mod
from alan import process_runner
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.teams import _verdict
from alan.teams.verification import TEAM
from alan.workspace import Workspace
from tests import _teams_verification_support as support
from tests._helpers import seed_and_run
from tests.fakes.forge import FakeForge
from tests.forge_stubs import ForgeScript, api_result, login_read
from tests.thread_forge_fake import (
    ThreadForgeFake,
    resolve_mutation_stdout,
    review_threads_stdout,
    thread_node,
)

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
def audit_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return support.set_audit_root(tmp_path, monkeypatch)


@pytest.fixture
def forge(_active_fake_forge: FakeForge) -> ForgeScript:
    return ForgeScript(_active_fake_forge)


def spy_resolve_after_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[tuple[object, ...], dict[str, object]]]:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def fake(*args: object, **kwargs: object) -> None:
        calls.append((args, kwargs))

    monkeypatch.setattr(vtr_mod, "resolve_after_approval", fake)
    return calls


def read_audit(audit_root: Path) -> list[dict[str, object]]:
    path = audit_root / "verification_audit.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_run_calls_resolve_after_approval_on_successful_approve(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path,
) -> None:
    monkeypatch.delenv("ALAN_VERIFICATION_DRY_RUN", raising=False)
    _patch_streaming(monkeypatch)
    monkeypatch.setattr(
        process_runner, "run_subprocess",
        lambda *a, **k: subprocess.CompletedProcess([], 0, stdout="", stderr=""),  # noqa: ARG005
    )
    calls = spy_resolve_after_approval(monkeypatch)
    prior = _impl_handover(workspace)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert handover.pr_number == 42
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[0] == "APPROVE"
    assert args[1] == 0
    assert args[2] == 42
    assert kwargs["task_id"] == workspace.task_id
    assert kwargs["inv_id"] == prior.from_invocation_id
    assert kwargs["forge_target"] == DEFAULT_FORGE_TARGET
    counts = kwargs["counts"]
    assert isinstance(counts, _verdict.CriticalCountReport)
    assert counts.judge_reported is True
    assert counts.reviewer is None


def test_failed_post_never_attempts_resolution(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path,
) -> None:
    monkeypatch.delenv("ALAN_VERIFICATION_DRY_RUN", raising=False)
    _patch_streaming(monkeypatch)
    monkeypatch.setattr(
        process_runner, "run_subprocess",
        lambda *a, **k: subprocess.CompletedProcess(  # noqa: ARG005
            [], 1, stdout="", stderr="gh failed"
        ),
    )
    calls = spy_resolve_after_approval(monkeypatch)
    prior = _impl_handover(workspace)
    seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert calls == []


def test_dry_run_never_attempts_resolution(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path,
) -> None:
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    _patch_streaming(monkeypatch)
    calls = spy_resolve_after_approval(monkeypatch)
    prior = _impl_handover(workspace)
    seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert calls == []


def test_run_resolves_verifier_threads_after_successful_approve(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path, forge: ForgeScript,
) -> None:
    monkeypatch.delenv("ALAN_VERIFICATION_DRY_RUN", raising=False)
    monkeypatch.setenv("ALAN_VERIFIER_GH_TOKEN", "verifier-tok")
    _patch_streaming(monkeypatch)
    nodes = [thread_node("t1", author_login="alan-verifier", total_count=1)]
    fake = ThreadForgeFake()
    forge.queue("viewer_login", login_read("alan-verifier"))
    forge.queue("unresolved_threads", api_result(review_threads_stdout(nodes)))
    forge.queue("resolve_thread", api_result(resolve_mutation_stdout(True)))
    monkeypatch.setattr(process_runner, "run_subprocess", fake.run_subprocess)
    prior = _impl_handover(workspace)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert handover.pr_number == 42
    entries = read_audit(audit_root)
    assert any(
        e["action"] == vtr_mod.RESOLVED_ACTION and e["thread_id"] == "t1" for e in entries
    )


def test_request_changes_never_attempts_resolution(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path, forge: ForgeScript,
) -> None:
    monkeypatch.delenv("ALAN_VERIFICATION_DRY_RUN", raising=False)
    monkeypatch.setenv("ALAN_VERIFIER_GH_TOKEN", "verifier-tok")
    judge_output = (
        "## Verdict\n\nVERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 1\n\n"
        "## Rationale\nfound a bug."
    )
    _patch_streaming(monkeypatch, judge_output=judge_output)
    fake = ThreadForgeFake()
    monkeypatch.setattr(process_runner, "run_subprocess", fake.run_subprocess)
    prior = _impl_handover(workspace)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert handover.pr_number == 42
    entries = read_audit(audit_root)
    assert not any(str(e["action"]).startswith("verifier_thread") for e in entries)
    assert forge.calls("unresolved_threads") == []


def test_comment_only_review_never_attempts_resolution(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path, forge: ForgeScript,
) -> None:
    monkeypatch.delenv("ALAN_VERIFICATION_DRY_RUN", raising=False)
    monkeypatch.setenv("ALAN_VERIFIER_GH_TOKEN", "verifier-tok")
    judge_output = (
        "## Verdict\n\nVERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 0\n\n"
        "## External Gate Status\nFrontend E2E (Playwright, blocking): failing.\n"
    )
    _patch_streaming(monkeypatch, judge_output=judge_output)
    fake = ThreadForgeFake()
    monkeypatch.setattr(process_runner, "run_subprocess", fake.run_subprocess)
    prior = _impl_handover(workspace)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert handover.pr_number == 42
    entries = read_audit(audit_root)
    assert not any(str(e["action"]).startswith("verifier_thread") for e in entries)
    assert forge.calls("viewer_login") == []
    assert forge.calls("unresolved_threads") == []
    assert forge.calls("resolve_thread") == []


def test_resolution_forge_failure_leaves_verdict_event_and_routing_unchanged(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path, tmp_path: Path,
    forge: ForgeScript,
) -> None:
    monkeypatch.delenv("ALAN_VERIFICATION_DRY_RUN", raising=False)
    monkeypatch.setenv("ALAN_VERIFIER_GH_TOKEN", "verifier-tok")
    _patch_streaming(monkeypatch)
    nodes = [thread_node("t1", author_login="alan-verifier", total_count=1)]
    failing_fake = ThreadForgeFake()
    forge.queue("viewer_login", login_read("alan-verifier"))
    forge.queue("unresolved_threads", api_result(review_threads_stdout(nodes)))
    forge.queue("resolve_thread", api_result(returncode=1, stderr="forge down"))
    monkeypatch.setattr(process_runner, "run_subprocess", failing_fake.run_subprocess)
    prior = _impl_handover(workspace)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)

    entries = read_audit(audit_root)
    review_ok = [e for e in entries if e["action"] == "review_ok"]
    assert review_ok
    assert review_ok[-1]["event"] == "APPROVE"
    refused = [e for e in entries if e["action"] == vtr_mod.REFUSED_ACTION]
    assert refused
    assert refused[0]["reason"] == "forge_refused"
    assert refused[0]["error"] == "forge down"

    baseline_workspace = support.make_workspace(tmp_path / "baseline")
    baseline_fake = ThreadForgeFake()
    forge.queue("viewer_login", login_read("alan-verifier"))
    forge.queue("unresolved_threads", api_result("{}"))
    monkeypatch.setattr(process_runner, "run_subprocess", baseline_fake.run_subprocess)
    baseline_prior = _impl_handover(baseline_workspace)
    baseline_handover = seed_and_run(TEAM, baseline_workspace, baseline_prior, lambda _e: None)

    assert handover.handover_type == baseline_handover.handover_type
    assert handover.to_team_suggestions == baseline_handover.to_team_suggestions
    assert handover.pr_number == baseline_handover.pr_number
