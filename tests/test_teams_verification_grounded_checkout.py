from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager as _cm
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from alan import process_runner
from alan import slot_pool as slot_pool_mod
from alan import workflow as workflow_mod
from alan import worktree as worktree_mod
from alan.claude_cli import AgentStreamingResult
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.handover import TestScenario
from alan.teams import _base as base_mod
from alan.teams import _forge_errors, _pr_branch, _scenario_runner
from alan.teams import _verification_grounding as grounding_mod
from alan.teams._base import TeamEvent
from alan.teams._scenario_runner import ScenarioResult
from alan.teams.verification import (
    TEAM,
    VerificationTeam,
)
from alan.workspace import Workspace
from tests import _teams_verification_support as support
from tests._helpers import seed_and_run
from tests.fakes.forge import FakeForge
from tests.forge_stubs import ForgeScript, api_result

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


def test_try_acquire_grounded_checkout_trigger_file_yields_none(
    workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, audit_root: Path  # noqa: ARG001
) -> None:
    trigger = tmp_path / "trigger"
    trigger.write_text("")
    monkeypatch.setenv(grounding_mod._CANNOT_VERIFY_FILE_ENV, str(trigger))  # noqa: SLF001
    with grounding_mod._try_acquire_grounded_checkout(
        workspace, 42, "branch", "t1", "inv1", lambda _e: None,
    ) as path:  # noqa: SLF001
        assert path is None


def test_try_acquire_grounded_checkout_trigger_path_directory_proceeds(
    workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, audit_root: Path  # noqa: ARG001
) -> None:
    monkeypatch.setenv(grounding_mod._CANNOT_VERIFY_FILE_ENV, str(tmp_path))  # noqa: SLF001
    fake_path = tmp_path / "grounded"
    fake_path.mkdir(exist_ok=True)

    @_cm
    def _fake_wt(*_a: object, **_k: object) -> Iterator[object]:
        handle = MagicMock()
        handle.path = fake_path
        handle.slot_index = None
        yield handle

    monkeypatch.setattr(worktree_mod, "implementation_worktree", _fake_wt)
    with grounding_mod._try_acquire_grounded_checkout(
        workspace, 42, "branch", "t1", "inv1", lambda _e: None,
    ) as path:  # noqa: SLF001
        assert path == fake_path


def test_try_acquire_grounded_checkout_trigger_scoped_to_task_yields_none(
    workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, audit_root: Path  # noqa: ARG001
) -> None:
    trigger = tmp_path / "trigger"
    trigger.write_text("t1")
    monkeypatch.setenv(grounding_mod._CANNOT_VERIFY_FILE_ENV, str(trigger))  # noqa: SLF001
    with grounding_mod._try_acquire_grounded_checkout(
        workspace, 42, "branch", "t1", "inv1", lambda _e: None,
    ) as path:  # noqa: SLF001
        assert path is None


def test_try_acquire_grounded_checkout_trigger_for_other_task_proceeds(
    workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, audit_root: Path  # noqa: ARG001
) -> None:
    trigger = tmp_path / "trigger"
    trigger.write_text("some-other-task")
    monkeypatch.setenv(grounding_mod._CANNOT_VERIFY_FILE_ENV, str(trigger))  # noqa: SLF001
    fake_path = tmp_path / "grounded"
    fake_path.mkdir(exist_ok=True)

    @_cm
    def _fake_wt(*_a: object, **_k: object) -> Iterator[object]:
        handle = MagicMock()
        handle.path = fake_path
        handle.slot_index = None
        yield handle

    monkeypatch.setattr(worktree_mod, "implementation_worktree", _fake_wt)
    with grounding_mod._try_acquire_grounded_checkout(
        workspace, 42, "branch", "t1", "inv1", lambda _e: None,
    ) as path:  # noqa: SLF001
        assert path == fake_path


def test_try_acquire_grounded_checkout_env_set_no_file_proceeds(
    workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, audit_root: Path  # noqa: ARG001
) -> None:
    monkeypatch.setenv(grounding_mod._CANNOT_VERIFY_FILE_ENV, str(tmp_path / "no-such-file"))  # noqa: SLF001
    fake_path = tmp_path / "grounded"
    fake_path.mkdir(exist_ok=True)

    @_cm
    def _fake_wt(*_a: object, **_k: object) -> Iterator[object]:
        handle = MagicMock()
        handle.path = fake_path
        handle.slot_index = None
        yield handle

    monkeypatch.setattr(worktree_mod, "implementation_worktree", _fake_wt)
    with grounding_mod._try_acquire_grounded_checkout(
        workspace, 42, "branch", "t1", "inv1", lambda _e: None,
    ) as path:  # noqa: SLF001
        assert path == fake_path


def test_try_acquire_grounded_checkout_accepts_preresolved_branch_no_second_gh_call(
    workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, audit_root: Path  # noqa: ARG001
) -> None:
    def _boom(*_a: object, **_k: object) -> str:
        pytest.fail("_try_acquire_grounded_checkout must not re-resolve the branch")

    monkeypatch.setattr(_pr_branch, "resolve_existing_branch", _boom)
    fake_path = tmp_path / "grounded"
    fake_path.mkdir(exist_ok=True)

    @_cm
    def _fake_wt(*_a: object, **_k: object) -> Iterator[object]:
        handle = MagicMock()
        handle.path = fake_path
        handle.slot_index = None
        yield handle

    monkeypatch.setattr(worktree_mod, "implementation_worktree", _fake_wt)
    with grounding_mod._try_acquire_grounded_checkout(
        workspace, 42, "pre-resolved-branch", "t1", "inv1", lambda _e: None,
    ) as path:
        assert path == fake_path


def test_try_acquire_grounded_checkout_worktree_raises_yields_none(
    workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, audit_root: Path
) -> None:
    def _fail_wt(*_a: object, **_k: object) -> object:
        raise RuntimeError("git worktree failed")

    monkeypatch.setattr(worktree_mod, "implementation_worktree", _fail_wt)
    with grounding_mod._try_acquire_grounded_checkout(
        workspace, 42, "branch", "t1", "inv1", lambda _e: None,
    ) as path:  # noqa: SLF001
        assert path is None
    entries = [
        json.loads(ln)
        for ln in (audit_root / "verification_audit.jsonl").read_text().splitlines()
    ]
    assert any(
        e["action"] == "verifier_checkout_failed" and e["error"] == "git worktree failed"
        for e in entries
    )


def test_try_acquire_grounded_checkout_success_audits_ok(
    workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, audit_root: Path
) -> None:
    fake_path = tmp_path / "grounded"
    fake_path.mkdir(exist_ok=True)

    @_cm
    def _fake_wt(*_a: object, **_k: object) -> Iterator[object]:
        handle = MagicMock()
        handle.path = fake_path
        handle.slot_index = None
        yield handle

    monkeypatch.setattr(worktree_mod, "implementation_worktree", _fake_wt)
    with grounding_mod._try_acquire_grounded_checkout(
        workspace, 42, "branch", "t1", "inv1", lambda _e: None,
    ) as path:  # noqa: SLF001
        assert path == fake_path
    entries = [
        json.loads(ln)
        for ln in (audit_root / "verification_audit.jsonl").read_text().splitlines()
    ]
    assert any(e["action"] == "verifier_checkout_ok" and e["pr_number"] == 42 for e in entries)


def test_try_acquire_grounded_checkout_threads_emit_to_on_blocked(
    workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, audit_root: Path,  # noqa: ARG001
) -> None:
    fake_path = tmp_path / "grounded"
    fake_path.mkdir(exist_ok=True)
    captured: dict[str, object] = {}

    @_cm
    def _fake_wt(*_a: object, **kwargs: object) -> Iterator[object]:
        captured["on_blocked"] = kwargs.get("on_blocked")
        handle = MagicMock()
        handle.path = fake_path
        handle.slot_index = None
        yield handle

    monkeypatch.setattr(worktree_mod, "implementation_worktree", _fake_wt)
    events: list[TeamEvent] = []
    with grounding_mod._try_acquire_grounded_checkout(
        workspace, 42, "branch", "t1", "inv1", events.append,
    ) as path:  # noqa: SLF001
        assert path == fake_path
    on_blocked = captured["on_blocked"]
    assert callable(on_blocked)
    on_blocked()
    evt = next(e for e in events if e.kind == "slot_resume_waiting")
    assert evt.payload["task_id"] == "t1"
    assert evt.payload["inv_id"] == "inv1"


def _seed_verification_inv(workspace: Workspace) -> str:
    return workflow_mod.seed_pending_workflow(
        workspace, team="verification", tasks=TEAM.plan_workflow(workspace, None)
    )


def _fake_worktree_capturing(
    fake_path: Path, slot_index: int | None, captured: dict[str, object]
) -> Callable[..., object]:
    @_cm
    def _fake_wt(*_a: object, **kwargs: object) -> Iterator[object]:
        captured["preferred_slot"] = kwargs.get("preferred_slot")
        handle = MagicMock()
        handle.path = fake_path
        handle.slot_index = slot_index
        yield handle

    return _fake_wt


def test_grounded_checkout_persists_slot_index_on_first_run(
    workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    audit_root: Path,  # noqa: ARG001
) -> None:
    inv_id = _seed_verification_inv(workspace)
    assert workflow_mod.load_graph(workspace, inv_id).slot_index is None
    monkeypatch.setattr(slot_pool_mod, "get_active_pool", lambda: MagicMock())
    fake_path = tmp_path / "grounded"
    fake_path.mkdir(exist_ok=True)
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        worktree_mod, "implementation_worktree",
        _fake_worktree_capturing(fake_path, 3, captured),
    )
    with grounding_mod._try_acquire_grounded_checkout(  # noqa: SLF001
        workspace, 42, "branch", workspace.task_id, inv_id, lambda _e: None,
    ) as path:
        assert path == fake_path
    assert captured["preferred_slot"] is None
    assert workflow_mod.load_graph(workspace, inv_id).slot_index == 3


def test_grounded_checkout_pins_preferred_slot_on_resume(
    workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    audit_root: Path,  # noqa: ARG001
) -> None:
    inv_id = _seed_verification_inv(workspace)
    graph = workflow_mod.load_graph(workspace, inv_id)
    graph.set_slot_index(5)
    workflow_mod.save_workflow(workspace, graph.doc)
    monkeypatch.setattr(slot_pool_mod, "get_active_pool", lambda: MagicMock())
    fake_path = tmp_path / "grounded"
    fake_path.mkdir(exist_ok=True)
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        worktree_mod, "implementation_worktree",
        _fake_worktree_capturing(fake_path, 5, captured),
    )
    with grounding_mod._try_acquire_grounded_checkout(  # noqa: SLF001
        workspace, 42, "branch", workspace.task_id, inv_id, lambda _e: None,
    ) as path:
        assert path == fake_path
    assert captured["preferred_slot"] == 5


def test_grounded_checkout_unreadable_graph_degrades_to_no_affinity(
    workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    audit_root: Path,  # noqa: ARG001
) -> None:
    inv_id = _seed_verification_inv(workspace)
    monkeypatch.setattr(slot_pool_mod, "get_active_pool", lambda: MagicMock())

    def _raise(*_a: object, **_k: object) -> object:
        raise workflow_mod.WorkflowError("corrupt workflow.yaml")

    monkeypatch.setattr(workflow_mod, "load_graph", _raise)
    fake_path = tmp_path / "grounded"
    fake_path.mkdir(exist_ok=True)
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        worktree_mod, "implementation_worktree",
        _fake_worktree_capturing(fake_path, None, captured),
    )
    with grounding_mod._try_acquire_grounded_checkout(  # noqa: SLF001
        workspace, 42, "branch", workspace.task_id, inv_id, lambda _e: None,
    ) as path:
        assert path == fake_path
    assert captured["preferred_slot"] is None


def test_grounded_checkout_no_pool_leaves_slot_index_none(
    workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    audit_root: Path,  # noqa: ARG001
) -> None:
    inv_id = _seed_verification_inv(workspace)
    monkeypatch.setattr(slot_pool_mod, "get_active_pool", lambda: None)
    fake_path = tmp_path / "grounded"
    fake_path.mkdir(exist_ok=True)
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        worktree_mod, "implementation_worktree",
        _fake_worktree_capturing(fake_path, None, captured),
    )
    with grounding_mod._try_acquire_grounded_checkout(  # noqa: SLF001
        workspace, 42, "branch", workspace.task_id, inv_id, lambda _e: None,
    ) as path:
        assert path == fake_path
    assert captured["preferred_slot"] is None
    assert workflow_mod.load_graph(workspace, inv_id).slot_index is None


def test_run_no_prior_handover_hits_precondition_gracefully(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path
) -> None:
    def boom(*_a: object, **_k: object) -> object:
        raise AssertionError("gh should not be called with no prior")

    monkeypatch.setattr(process_runner, "run_subprocess", boom)
    handover = seed_and_run(TEAM, workspace, None, lambda _e: None)
    assert handover.handover_type == "escalation"
    entries = [
        json.loads(line)
        for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
    ]
    assert any(e["action"] == "pr_precondition_failed" for e in entries)


def test_emit_cannot_verify_fires_team_event(
    workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    prior = _impl_handover(workspace, pr_number=42)
    events: list[TeamEvent] = []

    def _noop_post(*_a: object, **_k: object) -> None:
        pass

    import unittest.mock as _mock  # noqa: PLC0415
    with _mock.patch.object(grounding_mod, "_post_cannot_verify_comment", _noop_post):
        grounding_mod._emit_cannot_verify(
            workspace, prior, events.append, "inv1",
            forge_target=DEFAULT_FORGE_TARGET,
        )  # noqa: SLF001
    kinds = [e.kind for e in events]
    assert "cannot_verify" in kinds


def test_emit_cannot_verify_no_pr_skips_gh(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    called: list[bool] = []

    def _boom(*_a: object, **_k: object) -> None:
        called.append(True)

    monkeypatch.setattr(grounding_mod, "_post_cannot_verify_comment", _boom)
    prior = _impl_handover(workspace, pr_number=None)
    grounding_mod._emit_cannot_verify(
        workspace, prior, lambda _e: None, "inv1",
        forge_target=DEFAULT_FORGE_TARGET,
    )  # noqa: SLF001
    assert not called


def test_emit_cannot_verify_empty_to_team_suggestions(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    monkeypatch.setattr(grounding_mod, "_post_cannot_verify_comment", lambda *_a, **_k: None)
    prior = _impl_handover(workspace)
    handover = grounding_mod._emit_cannot_verify(
        workspace, prior, lambda _e: None, "inv1",
        forge_target=DEFAULT_FORGE_TARGET,
    )  # noqa: SLF001
    assert handover.to_team_suggestions == []
    assert handover.scenario_results == []


def test_emit_cannot_verify_preserves_pr_number(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    monkeypatch.setattr(grounding_mod, "_post_cannot_verify_comment", lambda *_a, **_k: None)
    prior = _impl_handover(workspace, pr_number=99)
    handover = grounding_mod._emit_cannot_verify(
        workspace, prior, lambda _e: None, "inv1",
        forge_target=DEFAULT_FORGE_TARGET,
    )  # noqa: SLF001
    assert handover.pr_number == 99


def test_emit_cannot_verify_posts_comment_not_request_changes(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    prior = _impl_handover(workspace, pr_number=42)
    grounding_mod._emit_cannot_verify(
        workspace, prior, lambda _e: None, "inv1",
        forge_target=DEFAULT_FORGE_TARGET,
    )  # noqa: SLF001
    entries = [
        json.loads(ln)
        for ln in (audit_root / "verification_audit.jsonl").read_text().splitlines()
        if ln.strip()
    ]
    assert any(e["action"] == "dry_run" and e["event"] == "COMMENT" for e in entries)


def test_emit_cannot_verify_gh_failure_non_fatal(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path,  # noqa: ARG001
    forge: ForgeScript,
) -> None:
    monkeypatch.delenv("ALAN_VERIFICATION_DRY_RUN", raising=False)
    forge.queue("post_review", api_result(returncode=1, stderr="boom"))
    prior = _impl_handover(workspace, pr_number=42)
    grounding_mod._emit_cannot_verify(
        workspace, prior, lambda _e: None, "inv1",
        forge_target=DEFAULT_FORGE_TARGET,
    )  # noqa: SLF001
    assert len(forge.calls("post_review")) == 1


def test_emit_cannot_verify_gh_success_non_fatal(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path,
    forge: ForgeScript,
) -> None:
    monkeypatch.delenv("ALAN_VERIFICATION_DRY_RUN", raising=False)
    forge.queue("post_review", api_result("{}"))
    prior = _impl_handover(workspace, pr_number=42)
    grounding_mod._emit_cannot_verify(
        workspace, prior, lambda _e: None, "inv1",
        forge_target=DEFAULT_FORGE_TARGET,
    )  # noqa: SLF001
    entries = [
        json.loads(ln)
        for ln in (audit_root / "verification_audit.jsonl").read_text().splitlines()
        if ln.strip()
    ]
    assert any(e["action"] == "cannot_verify" for e in entries)


def test_emit_cannot_verify_gh_exception_non_fatal(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path,  # noqa: ARG001
    forge: ForgeScript,
) -> None:
    monkeypatch.delenv("ALAN_VERIFICATION_DRY_RUN", raising=False)
    forge.queue("post_review", FileNotFoundError("no gh"))
    prior = _impl_handover(workspace, pr_number=42)
    grounding_mod._emit_cannot_verify(
        workspace, prior, lambda _e: None, "inv1",
        forge_target=DEFAULT_FORGE_TARGET,
    )  # noqa: SLF001
    assert len(forge.calls("post_review")) == 1


def test_run_precondition_fails_when_pr_number_none(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path
) -> None:
    def boom(*_a: object, **_k: object) -> AgentStreamingResult:
        pytest.fail("must not spawn a worker when pr_number is None")

    monkeypatch.setattr(base_mod, "run_agent_streaming", boom)
    prior = _impl_handover(workspace, pr_number=None)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert handover.handover_type == "escalation"
    assert handover.to_team_suggestions == []
    assert handover.pr_number is None
    entries = [
        json.loads(line)
        for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
    ]
    assert any(
        e["action"] == "pr_precondition_failed" and e["pr_number"] is None for e in entries
    )
    assert "pr_number=None" in handover.full_synthesis
    assert "branch could not be resolved" not in handover.full_synthesis


def test_run_precondition_fails_when_branch_unresolvable(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path
) -> None:
    monkeypatch.setattr(_pr_branch, "resolve_existing_branch", lambda _p, _t, **_kw: None)

    def boom(*_a: object, **_k: object) -> AgentStreamingResult:
        pytest.fail("must not spawn a worker when the branch cannot be resolved")

    monkeypatch.setattr(base_mod, "run_agent_streaming", boom)

    def boom_gh(*_a: object, **_k: object) -> object:
        pytest.fail("must not shell out to gh when the precondition fails")

    monkeypatch.setattr(process_runner, "run_subprocess", boom_gh)
    prior = _impl_handover(workspace, pr_number=42)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert handover.handover_type == "escalation"
    assert handover.to_team_suggestions == []
    assert handover.pr_number == 42
    entries = [
        json.loads(line)
        for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
    ]
    assert any(
        e["action"] == "pr_precondition_failed" and e["pr_number"] == 42 for e in entries
    )
    assert not any(e["action"] == "verifier_checkout_failed" for e in entries)
    assert not any(e["action"] == "cannot_verify" for e in entries)
    assert "pr_number=42 but GitHub confirms it cannot be resolved" in handover.full_synthesis


def test_run_precondition_failure_does_not_call_scenario_results(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    monkeypatch.setattr(_pr_branch, "resolve_existing_branch", lambda _p, _t, **_kw: None)

    def boom(*_a: object, **_k: object) -> list[object]:
        pytest.fail("must not compute scenario results when the precondition fails")

    monkeypatch.setattr(VerificationTeam, "_compute_scenario_results", boom)
    prior = _impl_handover(workspace, pr_number=42)
    seed_and_run(TEAM, workspace, prior, lambda _e: None)


def test_run_precondition_failure_provenance_present(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path
) -> None:
    from alan.handover import save_handover  # noqa: PLC0415

    monkeypatch.setattr(_pr_branch, "resolve_existing_branch", lambda _p, _t, **_kw: None)
    older = _impl_handover(workspace, pr_number=None).model_copy(
        update={"from_invocation_id": "inv_100"}
    )
    save_handover(workspace, older)
    newer = _impl_handover(workspace, pr_number=55).model_copy(
        update={"from_invocation_id": "inv_777"}
    )
    save_handover(workspace, newer)
    current_prior = _impl_handover(workspace, pr_number=None)
    handover = seed_and_run(TEAM, workspace, current_prior, lambda _e: None)
    assert "inv_777" in handover.full_synthesis
    entries = [
        json.loads(line)
        for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
    ]
    entry = next(e for e in entries if e["action"] == "pr_precondition_failed")
    assert entry["last_pr_from_invocation_id"] == "inv_777"
    assert entry["last_pr_handover_id"] == newer.handover_id


def test_run_precondition_failure_provenance_absent_when_never_carried(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path
) -> None:
    monkeypatch.setattr(_pr_branch, "resolve_existing_branch", lambda _p, _t, **_kw: None)
    prior = _impl_handover(workspace, pr_number=None)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert "No prior handover" in handover.full_synthesis
    entries = [
        json.loads(line)
        for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
    ]
    entry = next(e for e in entries if e["action"] == "pr_precondition_failed")
    assert "last_pr_handover_id" not in entry
    assert "last_pr_from_team" not in entry
    assert "last_pr_from_invocation_id" not in entry


def test_run_precondition_failure_dependency_bot_authorized_carried_from_prior(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    monkeypatch.setattr(_pr_branch, "resolve_existing_branch", lambda _p, _t, **_kw: None)
    prior = _impl_handover(workspace, pr_number=42).model_copy(
        update={"dependency_bot_authorized": True}
    )
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert handover.dependency_bot_authorized is True


def test_run_cannot_verify_when_grounded_checkout_fails(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")

    def _fail_wt(*_a: object, **_k: object) -> object:
        raise RuntimeError("git worktree failed")

    monkeypatch.setattr(worktree_mod, "implementation_worktree", _fail_wt)
    prior = _impl_handover(workspace, pr_number=42)
    events: list[TeamEvent] = []
    handover = seed_and_run(TEAM, workspace, prior, events.append)
    assert handover.to_team_suggestions == []
    assert handover.decisions == []
    assert any(e.kind == "cannot_verify" for e in events)


def test_run_precondition_failure_never_sets_grounded_cwd_var(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    from alan.teams.verification import _grounded_cwd_var  # noqa: PLC0415
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    assert _grounded_cwd_var.get() is None
    prior = _impl_handover(workspace, pr_number=None)
    handover = seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert handover.pr_number is None
    assert _grounded_cwd_var.get() is None


def test_grounded_cwd_var_reset_after_cannot_verify(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    from alan.teams.verification import _grounded_cwd_var  # noqa: PLC0415
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")

    def _fail_wt(*_a: object, **_k: object) -> object:
        raise RuntimeError("git worktree failed")

    monkeypatch.setattr(worktree_mod, "implementation_worktree", _fail_wt)
    assert _grounded_cwd_var.get() is None
    prior = _impl_handover(workspace, pr_number=42)
    seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert _grounded_cwd_var.get() is None


def test_grounded_cwd_var_reset_after_normal_run(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path  # noqa: ARG001
) -> None:
    from alan.teams.verification import _grounded_cwd_var  # noqa: PLC0415
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    _patch_streaming(monkeypatch)
    assert _grounded_cwd_var.get() is None
    prior = _impl_handover(workspace)
    seed_and_run(TEAM, workspace, prior, lambda _e: None)
    assert _grounded_cwd_var.get() is None


def test_compute_scenario_results_uses_grounded_cwd(
    workspace: Workspace, tmp_path: Path
) -> None:
    from alan.teams.verification import _grounded_cwd_var  # noqa: PLC0415
    grounded = tmp_path / "grounded"
    grounded.mkdir(exist_ok=True)
    token = _grounded_cwd_var.set(grounded)
    captured_cwds: list[str] = []

    def _fake_run_scenarios(
        _scenarios: object,
        *,
        cwd: str,
        per_scenario_timeout: object = None,
        aggregate_budget: object = None,
        cfg: object = None,
        strict_ids: object = None,
    ) -> list[object]:
        del per_scenario_timeout, aggregate_budget, cfg, strict_ids
        captured_cwds.append(cwd)
        return []

    prior = _impl_handover(workspace).model_copy(update={
        "test_scenarios": [
            TestScenario(id="s1", description="d", given="g", when="uv run pytest", then="ok")
        ]
    })
    try:
        import unittest.mock as _mock  # noqa: PLC0415
        with _mock.patch.object(_scenario_runner, "run_scenarios", _fake_run_scenarios):
            TEAM._compute_scenario_results(workspace, prior, "inv1")  # noqa: SLF001
    finally:
        _grounded_cwd_var.reset(token)
    assert captured_cwds == [str(grounded)]


def test_compute_scenario_results_appends_changed_e2e_in_missing_mode(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace
) -> None:
    from alan.teams import _changed_e2e

    scenario = _changed_e2e.changed_e2e_scenario(("alan/web/e2e/a.spec.ts",))
    selection = _changed_e2e.ChangedE2ESelection(scenario=scenario, base_unresolved=False)
    monkeypatch.setattr(
        _changed_e2e, "build_changed_e2e_scenario", lambda _c, **_kw: selection
    )
    canned = ScenarioResult(
        scenario_id="changed-e2e-specs", status="PASS", diagnostic="",
        stdout_tail="", stderr_tail="", returncode=0, elapsed_s=0.1,
    )
    monkeypatch.setattr(_scenario_runner, "run_scenario", lambda *_a, **_kw: canned)
    prior = _impl_handover(workspace)
    results = TEAM._compute_scenario_results(workspace, prior, "inv_ce2e")
    assert [r.scenario_id for r in results] == ["changed-e2e-specs"]


def test_compute_scenario_results_skips_changed_e2e_when_waived(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace
) -> None:
    from alan.teams import _changed_e2e

    def _boom(_c: object) -> object:
        raise AssertionError("changed-e2e must not run for waived scenarios")

    monkeypatch.setattr(_changed_e2e, "build_changed_e2e_scenario", _boom)
    prior = _impl_handover(workspace)
    prior = prior.model_copy(update={"scenarios_waived": True})
    assert TEAM._compute_scenario_results(workspace, prior, "inv_ce2e") == []


def test_run_propagates_forge_rate_limited_from_branch_resolution(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path
) -> None:
    def _rate_limited(*_a: object, **_k: object) -> str:
        raise _forge_errors.ForgeRateLimited("gh pr view rate limited", 123.0)

    monkeypatch.setattr(_pr_branch, "resolve_existing_branch", _rate_limited)

    def boom(*_a: object, **_k: object) -> AgentStreamingResult:
        pytest.fail("must not spawn a worker when branch resolution is rate limited")

    monkeypatch.setattr(base_mod, "run_agent_streaming", boom)
    prior = _impl_handover(workspace, pr_number=42)
    with pytest.raises(_forge_errors.ForgeRateLimited):
        seed_and_run(TEAM, workspace, prior, lambda _e: None)
    audit_file = audit_root / "verification_audit.jsonl"
    if audit_file.exists():
        entries = [json.loads(line) for line in audit_file.read_text().splitlines()]
        assert not any(e["action"] == "pr_precondition_failed" for e in entries)


def test_grounded_checkout_reraises_recovery_fault_without_cannot_verify_audit(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path
) -> None:
    from alan.slot_pool import PoolBusyError

    def _busy(*_a: object, **_k: object) -> object:
        raise PoolBusyError("pool disposing")

    monkeypatch.setattr(grounding_mod, "leased_with_affinity", _busy)
    with pytest.raises(PoolBusyError):
        with grounding_mod._try_acquire_grounded_checkout(
            workspace, 42, "branch", "t1", "inv1", lambda _e: None,
        ) as _path:
            pytest.fail("must not yield when the lease raised a recovery fault")
    entries = [
        json.loads(line)
        for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
    ] if (audit_root / "verification_audit.jsonl").exists() else []
    assert not any(e["action"] == "verifier_checkout_failed" for e in entries)


def test_grounded_checkout_propagates_retryable_forge_failures(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path
) -> None:
    def _transient(*_a: object, **_k: object) -> object:
        raise _forge_errors.ForgeTransientError("git fetch: dial tcp: i/o timeout")

    monkeypatch.setattr(grounding_mod, "leased_with_affinity", _transient)
    with pytest.raises(_forge_errors.ForgeTransientError):
        with grounding_mod._try_acquire_grounded_checkout(
            workspace, 42, "branch", "t1", "inv1", lambda _e: None,
        ) as _path:
            pytest.fail("must not yield when the lease raised a retryable failure")
    entries = [
        json.loads(line)
        for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
    ] if (audit_root / "verification_audit.jsonl").exists() else []
    assert not any(e["action"] == "verifier_checkout_failed" for e in entries)
