from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.handover import Handover
from alan.teams import _pr_merge_escalation
from alan.teams import pr_merge as pr_merge_mod
from alan.teams._pr_merge_checks import ChecksKind
from alan.teams.pr_merge import TEAM
from alan.workspace import Workspace, create
from tests._helpers import seed_and_run
from tests.fakes.forge import FakeForge
from tests.forge_stubs import (
    ForgeScript,
    api_result,
    checks_read,
    merge_attempt,
    pr_read,
    readiness_fields,
)
from tests.test_pr_feedback_stranger_threads import (
    COPILOT,
    PR,
    audit_records,
    call_args,
    feedback_payload,
    ok_permission,
    resolved_ok,
    stub_forge,
    thread_node,
    unresolved_ok,
    write_calls,
)
from tests.test_teams_pr_merge import _approve_prior, _patch_poll_intervals

_PASSING: list[dict[str, object]] = [{"name": "ci", "state": "SUCCESS"}]
_GREEN_ROLLUP = [{"name": "ci", "conclusion": "SUCCESS", "status": "COMPLETED"}]
_OPEN: dict[str, object] = {"state": "OPEN", "mergeStateStatus": "CLEAN"}


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return create("pr-merge-stranger-task", "gate the merge", root=tmp_path / "ws")


@pytest.fixture
def forge(_active_fake_forge: FakeForge) -> ForgeScript:
    return ForgeScript(_active_fake_forge)


@pytest.fixture(autouse=True)
def _isolated_merge_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_poll_intervals(monkeypatch)
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))


def _readiness(head_sha: str = "sha-merge") -> object:
    return pr_read(readiness_fields("CLEAN", rollup=_GREEN_ROLLUP, head_sha=head_sha))


def _queue_full_poll(forge: ForgeScript, *, readiness_reads: int) -> None:
    forge.queue_read_pr(("state", "mergeStateStatus"), pr_read(_OPEN))
    forge.queue("read_checks", checks_read(_PASSING))
    if readiness_reads:
        forge.queue("read_pr", *[_readiness()] * readiness_reads)


def _run(workspace: Workspace) -> Handover:
    return seed_and_run(TEAM, workspace, _approve_prior(workspace, PR), lambda _e: None)


def _evaluate(workspace: Workspace) -> tuple[ChecksKind, dict[str, Any]]:
    return pr_merge_mod._evaluate_checks(  # noqa: SLF001
        workspace, PR, time.monotonic(), 0, 600.0, 20, forge_target=DEFAULT_FORGE_TARGET,
    )


def test_a_stranger_only_thread_is_resolved_and_the_gate_merges(
    workspace: Workspace, forge: ForgeScript, _active_fake_forge: FakeForge,
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    stub_forge(
        _active_fake_forge, monkeypatch, payload=feedback_payload([thread_node()]),
        permissions={"stranger": ok_permission("read")}, resolve=resolved_ok(),
    )
    _queue_full_poll(forge, readiness_reads=2)
    forge.queue("merge_pr", merge_attempt())
    handover = _run(workspace)
    assert handover.handover_type == "terminal"
    [(args, _kwargs)] = forge.calls("merge_pr")
    assert args == (PR, "sha-merge")
    assert call_args(_active_fake_forge, "resolve_thread_checked") == ["T1"]
    assert [r["action"] for r in audit_records(tmp_path)] == ["stranger_thread_resolved"]


def test_a_collaborator_thread_parks_the_gate_as_unresolved_feedback_and_is_never_resolved(
    workspace: Workspace, forge: ForgeScript, _active_fake_forge: FakeForge,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_forge(
        _active_fake_forge, monkeypatch, payload=feedback_payload([thread_node()]),
        permissions={"stranger": ok_permission("write")},
    )
    _queue_full_poll(forge, readiness_reads=0)
    handover = _run(workspace)
    assert handover.handover_type == "forward"
    assert handover.open_questions
    assert "1 unresolved review thread(s)" in handover.full_synthesis
    assert write_calls(_active_fake_forge) == []
    assert forge.calls("merge_pr") == []


def test_without_a_reviewer_credential_the_thread_stays_counted_and_nothing_is_written(
    workspace: Workspace, forge: ForgeScript, _active_fake_forge: FakeForge,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_forge(
        _active_fake_forge, monkeypatch, payload=feedback_payload([thread_node()]),
        credential=False,
    )
    _queue_full_poll(forge, readiness_reads=0)
    handover = _run(workspace)
    assert handover.handover_type == "forward"
    assert "1 unresolved review thread(s)" in handover.full_synthesis
    assert call_args(_active_fake_forge, "repo_permission") == []
    assert write_calls(_active_fake_forge) == []


def test_a_read_back_with_replies_unresolves_the_thread_and_parks_the_gate(
    workspace: Workspace, forge: ForgeScript, _active_fake_forge: FakeForge,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_forge(
        _active_fake_forge, monkeypatch, payload=feedback_payload([thread_node()]),
        permissions={"stranger": ok_permission("none")},
        resolve=resolved_ok(count=2), unresolve=unresolved_ok(),
    )
    _queue_full_poll(forge, readiness_reads=0)
    handover = _run(workspace)
    assert handover.handover_type == "forward"
    assert "1 unresolved review thread(s)" in handover.full_synthesis
    assert write_calls(_active_fake_forge) == ["resolve_thread_checked", "unresolve_thread"]
    assert forge.calls("merge_pr") == []


def test_a_rate_limited_resolve_parks_the_gate_instead_of_raising(
    workspace: Workspace, forge: ForgeScript, _active_fake_forge: FakeForge,
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    stub_forge(
        _active_fake_forge, monkeypatch, payload=feedback_payload([thread_node()]),
        permissions={"stranger": ok_permission("read")},
        resolve=api_result(returncode=1, stderr="API rate limit exceeded for user ID 1."),
    )
    _queue_full_poll(forge, readiness_reads=0)
    handover = _run(workspace)
    assert handover.handover_type == "forward"
    assert "1 unresolved review thread(s)" in handover.full_synthesis
    assert [r["action"] for r in audit_records(tmp_path)] == ["stranger_thread_resolve_failed"]


def test_every_consumer_of_the_feedback_counts_reads_the_same_settled_numbers(
    workspace: Workspace, forge: ForgeScript, _active_fake_forge: FakeForge,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nodes = [
        thread_node("S1", "alice"),
        thread_node("S2", "bob"),
        thread_node("W1", "carol"),
        thread_node("C1", COPILOT, typename="Bot"),
        thread_node("B1", "dependabot", typename="Bot"),
    ]
    stub_forge(
        _active_fake_forge, monkeypatch, payload=feedback_payload(nodes),
        permissions={
            "alice": ok_permission("read"), "bob": ok_permission("triage"),
            "carol": ok_permission("admin"),
        },
        resolve={"S1": resolved_ok(), "S2": api_result(returncode=1, stderr="refused")},
    )
    forge.queue("read_checks", checks_read(_PASSING))
    kind, ctx = _evaluate(workspace)
    assert kind == "unresolved"
    assert ctx["threads"] == 2
    assert ctx["total_threads"] == 5
    assert ctx["copilot_threads_count"] == 1
    assert "2 unresolved review thread(s)" in _pr_merge_escalation.unresolved_human_reason(PR, ctx)
    assert _pr_merge_escalation.concurrent_blockers(kind, ctx) == (
        "unresolved", "copilot-unresolved",
    )


def test_each_poll_looks_the_author_up_again_and_a_resolved_thread_is_not_a_candidate(
    workspace: Workspace, forge: ForgeScript, _active_fake_forge: FakeForge,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unresolved = feedback_payload([thread_node()])
    resolved = feedback_payload([thread_node(resolved=True)])
    stub_forge(
        _active_fake_forge, monkeypatch, payload=unresolved,
        permissions={"stranger": ok_permission("read")}, resolve=resolved_ok(),
    )
    forge.queue(
        "feedback", api_result(unresolved), api_result(unresolved), api_result(resolved),
    )
    forge.queue("read_checks", checks_read(_PASSING), checks_read(_PASSING), checks_read(_PASSING))
    forge.queue("read_pr", _readiness(), _readiness(), _readiness())
    kinds = [_evaluate(workspace)[0] for _ in range(3)]
    assert kinds == ["ready", "ready", "ready"]
    assert call_args(_active_fake_forge, "repo_permission") == ["stranger", "stranger"]
    assert call_args(_active_fake_forge, "resolve_thread_checked") == ["T1", "T1"]
