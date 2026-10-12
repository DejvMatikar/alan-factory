from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan import dispatch as dispatch_mod
from alan import workflow as workflow_mod
from alan import workspace as workspace_mod
from alan._dispatcher_base import enqueue_with_pending_workflow
from alan.dispatch import (
    assert_team_dispatchable,
    dispatch_team_core,
    prior_handover_for_task,
    recover_prior_pr,
)
from alan.dispatcher import Dispatcher
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.errors import AppError
from alan.events import PubSub
from alan.handover import Handover, find_handover_by_id, record_pr_attachment, save_handover
from alan.queue import Queue
from alan.teams import _pr_state_paths
from alan.workflow import PendingTaskSpec, WorkflowDoc
from alan.workspace import Task, TaskStatus, Workspace
from tests.test_dispatch import _SKIPPED_MERGE_GATE, _StubFixTeam  # noqa: PLC2701

_WORKER = [PendingTaskSpec(id="worker", role="worker", model="sonnet")]


@pytest.fixture(autouse=True)
def _fix_cycle_state_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "subtask-prior-fix-cycle-state"))


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


@pytest.fixture
def queue(tmp_path: Path) -> Iterator[Queue]:
    q = Queue(db_path=tmp_path / "queue.db")
    try:
        yield q
    finally:
        with contextlib.suppress(Exception):
            q.close()


@pytest.fixture
def dispatcher(queue: Queue, ws_root: Path) -> Dispatcher:
    return Dispatcher(queue=queue, teams={}, workspaces_root=ws_root)


def _split(root: Path, *subtask_ids: str) -> Workspace:
    return workspace_mod.create(
        "subtask-prior",
        "d",
        root=root,
        tasks=[Task(id=t, status=TaskStatus.PENDING) for t in subtask_ids],
    )


def _invocation(ws: Workspace, source_task_id: str | None) -> str:
    return workflow_mod.seed_pending_workflow(
        ws, team="implementation_fix", tasks=_WORKER, source_task_id=source_task_id
    )


def _save(ws: Workspace, label: str, inv_id: str, pr_number: int | None = None) -> Handover:
    handover = Handover(
        handover_id=f"h-{label}",
        handover_type="escalation",
        task_id=ws.task_id,
        from_team="implementation_fix",
        from_invocation_id=inv_id,
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis=f"synthesis {label}",
        forbidden_actions=[f"forbidden {label}"],
        pr_number=pr_number,
    )
    save_handover(ws, handover)
    return handover


def _write(ws: Workspace, owner: str, label: str, pr_number: int | None = None) -> Handover:
    return _save(ws, label, _invocation(ws, owner), pr_number)


def _pin(ws: Workspace, subtask_id: str, handover: Handover) -> Workspace:
    return workspace_mod.update_task_status(
        ws, subtask_id, TaskStatus.AWAITING_INPUT, blocked_handover_id=handover.handover_id
    )


def _prior(ws: Workspace, task_id: str) -> Handover:
    prior = prior_handover_for_task(ws, task_id)
    assert prior is not None
    return prior


def _missing_invocation(ws: Workspace) -> str:
    del ws
    return "inv_998"


def _malformed_invocation(ws: Workspace) -> str:
    del ws
    return "inv1"


def _corrupt_invocation(ws: Workspace) -> str:
    inv_id = _invocation(ws, "sub-a")
    workflow_mod.workflow_path(ws, inv_id).write_text("[unclosed", encoding="utf-8")
    return inv_id


def _non_utf8_invocation(ws: Workspace) -> str:
    inv_id = _invocation(ws, "sub-a")
    workflow_mod.workflow_path(ws, inv_id).write_bytes(b"\xff\xfe\x00\x80")
    return inv_id


def _directory_invocation(ws: Workspace) -> str:
    inv_id = _invocation(ws, "sub-a")
    path = workflow_mod.workflow_path(ws, inv_id)
    path.unlink()
    path.mkdir()
    return inv_id


def _blank_source_invocation(ws: Workspace) -> str:
    return _invocation(ws, "")


def _unseeded_source_invocation(ws: Workspace) -> str:
    return _invocation(ws, None)


_UNATTRIBUTABLE = pytest.mark.parametrize(
    "make_invocation",
    [
        _missing_invocation,
        _malformed_invocation,
        _corrupt_invocation,
        _non_utf8_invocation,
        _directory_invocation,
        _blank_source_invocation,
        _unseeded_source_invocation,
    ],
    ids=[
        "missing",
        "malformed-id",
        "corrupt",
        "non-utf8",
        "directory",
        "blank-source",
        "no-source",
    ],
)


def test_sibling_writing_the_newest_handover_does_not_become_the_prior(ws_root: Path) -> None:
    ws = _split(ws_root, "sub-a", "sub-b")
    own = _write(ws, "sub-a", "a1", 11)
    sibling = _write(ws, "sub-b", "b1", 22)
    assert _prior(ws, "sub-a").handover_id == own.handover_id
    assert _prior(ws, "sub-b").handover_id == sibling.handover_id


def test_most_recent_own_handover_wins_over_older_own_and_newer_sibling(ws_root: Path) -> None:
    ws = _split(ws_root, "sub-a", "sub-b")
    _write(ws, "sub-a", "a1", 11)
    newest_own = _write(ws, "sub-a", "a2", 11)
    _write(ws, "sub-b", "b1", 22)
    assert _prior(ws, "sub-a").handover_id == newest_own.handover_id


def test_subtask_that_never_produced_a_handover_inherits_nothing(ws_root: Path) -> None:
    ws = _split(ws_root, "sub-a", "sub-b", "sub-c")
    _write(ws, "sub-a", "a1", 11)
    _write(ws, "sub-b", "b1", 22)
    assert prior_handover_for_task(ws, "sub-c") is None


def test_sibling_pr_less_escalation_does_not_change_the_pull_request(ws_root: Path) -> None:
    ws = _split(ws_root, "sub-a", "sub-b")
    _write(ws, "sub-a", "a1", 11)
    _write(ws, "sub-b", "b-esc")
    assert _prior(ws, "sub-a").pr_number == 11


def test_interleaved_subtasks_each_resolve_their_own_pull_request(ws_root: Path) -> None:
    ws = _split(ws_root, "sub-a", "sub-b")
    _write(ws, "sub-a", "a1", 11)
    _write(ws, "sub-b", "b1", 22)
    a_latest = _write(ws, "sub-a", "a2")
    b_latest = _write(ws, "sub-b", "b2")
    a_prior = _prior(ws, "sub-a")
    b_prior = _prior(ws, "sub-b")
    assert (a_prior.handover_id, a_prior.pr_number) == (a_latest.handover_id, 11)
    assert (b_prior.handover_id, b_prior.pr_number) == (b_latest.handover_id, 22)


def test_pinned_subtask_recovers_only_the_missing_pull_request_number(ws_root: Path) -> None:
    ws = _split(ws_root, "sub-a", "sub-b")
    _write(ws, "sub-a", "a1", 11)
    escalation = _write(ws, "sub-a", "a-esc")
    _write(ws, "sub-b", "b1", 22)
    ws = _pin(ws, "sub-a", escalation)
    prior = _prior(ws, "sub-a")
    assert prior.pr_number == 11
    assert prior.model_dump(exclude={"pr_number"}) == escalation.model_dump(exclude={"pr_number"})
    assert find_handover_by_id(ws, prior.handover_id) is not None


def test_pinned_subtask_without_own_pull_request_keeps_the_handover_as_is(
    ws_root: Path,
) -> None:
    ws = _split(ws_root, "sub-a", "sub-b")
    _write(ws, "sub-b", "b1", 22)
    escalation = _write(ws, "sub-a", "a-esc")
    ws = _pin(ws, "sub-a", escalation)
    assert _prior(ws, "sub-a") == escalation


def test_pinned_subtask_ignores_newer_own_handover(ws_root: Path) -> None:
    ws = _split(ws_root, "sub-a")
    pinned = _write(ws, "sub-a", "a1", 11)
    _write(ws, "sub-a", "a2", 12)
    ws = _pin(ws, "sub-a", pinned)
    assert _prior(ws, "sub-a") == pinned


def test_dangling_pin_resolves_nothing_and_never_falls_through_to_recency(
    ws_root: Path,
) -> None:
    ws = _split(ws_root, "sub-a", "sub-b")
    _write(ws, "sub-a", "a1", 11)
    ws = workspace_mod.update_task_status(
        ws, "sub-a", TaskStatus.AWAITING_INPUT, blocked_handover_id="h-gone"
    )
    assert prior_handover_for_task(ws, "sub-a") is None


@_UNATTRIBUTABLE
def test_unattributable_handover_is_never_returned_without_the_pin(
    ws_root: Path, make_invocation: Callable[[Workspace], str]
) -> None:
    ws = _split(ws_root, "sub-a", "sub-b")
    own = _write(ws, "sub-a", "a1", 11)
    _save(ws, "unattributed", make_invocation(ws), 33)
    assert _prior(ws, "sub-a").handover_id == own.handover_id
    assert prior_handover_for_task(ws, "sub-b") is None


@_UNATTRIBUTABLE
def test_unattributable_handover_is_still_returned_through_the_pin(
    ws_root: Path, make_invocation: Callable[[Workspace], str]
) -> None:
    ws = _split(ws_root, "sub-a", "sub-b")
    _write(ws, "sub-a", "a1", 11)
    unattributed = _save(ws, "unattributed", make_invocation(ws))
    ws = _pin(ws, "sub-a", unattributed)
    prior = _prior(ws, "sub-a")
    assert prior.handover_id == unattributed.handover_id
    assert prior.pr_number == 11


def test_unattributable_pull_request_is_never_a_donor(ws_root: Path) -> None:
    ws = _split(ws_root, "sub-a", "sub-b")
    _save(ws, "unattributed", "inv_998", 33)
    _write(ws, "sub-a", "a1")
    assert _prior(ws, "sub-a").pr_number is None


def test_nothing_attributable_resolves_to_the_latest_handover(ws_root: Path) -> None:
    ws = _split(ws_root, "sub-a", "sub-b")
    _save(ws, "u1", "inv_998", 11)
    latest = _save(ws, "u2", "inv_999")
    assert _prior(ws, "sub-a") == latest
    assert _prior(ws, "sub-b") == latest


def test_nothing_attributable_with_a_pin_resolves_to_the_pinned_handover(
    ws_root: Path,
) -> None:
    ws = _split(ws_root, "sub-a")
    pinned = _save(ws, "u1", "inv_998", 11)
    _save(ws, "u2", "inv_999")
    ws = _pin(ws, "sub-a", pinned)
    assert _prior(ws, "sub-a") == pinned


def test_nothing_attributable_with_a_dangling_pin_resolves_nothing(ws_root: Path) -> None:
    ws = _split(ws_root, "sub-a")
    _save(ws, "u1", "inv_998")
    ws = workspace_mod.update_task_status(
        ws, "sub-a", TaskStatus.AWAITING_INPUT, blocked_handover_id="h-gone"
    )
    assert prior_handover_for_task(ws, "sub-a") is None


def test_workspace_without_handovers_resolves_nothing(ws_root: Path) -> None:
    ws = _split(ws_root, "sub-a")
    assert prior_handover_for_task(ws, "sub-a") is None


def test_only_pre_split_invocations_resolve_as_before_on_a_split_workspace(
    ws_root: Path,
) -> None:
    ws = _split(ws_root, "sub-a", "sub-b")
    _write(ws, ws.task_id, "pre1", 11)
    latest = _write(ws, ws.task_id, "pre2")
    assert _prior(ws, "sub-a") == latest
    assert _prior(ws, "sub-b") == latest


def test_pre_split_handover_is_not_inherited_once_a_subtask_has_run(ws_root: Path) -> None:
    ws = _split(ws_root, "sub-a", "sub-b")
    _write(ws, ws.task_id, "pre1", 11)
    own = _write(ws, "sub-a", "a1")
    _write(ws, ws.task_id, "pre2", 12)
    assert _prior(ws, "sub-a").handover_id == own.handover_id
    assert _prior(ws, "sub-a").pr_number is None
    assert prior_handover_for_task(ws, "sub-b") is None


def test_unsplit_workspace_inherits_the_latest_handover_with_the_pr_recovered(
    ws_root: Path,
) -> None:
    ws = workspace_mod.create("unsplit-prior", "d", root=ws_root)
    _write(ws, ws.task_id, "h1", 9)
    latest = _write(ws, ws.task_id, "h2")
    prior = _prior(ws, ws.task_id)
    assert prior.handover_id == latest.handover_id
    assert prior.pr_number == 9


def test_selected_handover_that_already_carries_a_pull_request_is_untouched(
    ws_root: Path,
) -> None:
    ws = _split(ws_root, "sub-a")
    _write(ws, "sub-a", "a1", 11)
    latest = _write(ws, "sub-a", "a2", 12)
    assert _prior(ws, "sub-a") == latest


def test_pull_request_is_recovered_from_the_nearest_earlier_own_handover(
    ws_root: Path,
) -> None:
    ws = _split(ws_root, "sub-a", "sub-b")
    _write(ws, "sub-a", "a1", 11)
    _write(ws, "sub-b", "b1", 22)
    _write(ws, "sub-a", "a2", 12)
    _write(ws, "sub-a", "a3")
    assert _prior(ws, "sub-a").pr_number == 12


def test_later_own_pull_request_is_not_a_donor_for_a_pinned_earlier_handover(
    ws_root: Path,
) -> None:
    ws = _split(ws_root, "sub-a")
    _write(ws, "sub-a", "a1", 11)
    pinned = _write(ws, "sub-a", "a2")
    _write(ws, "sub-a", "a3", 13)
    ws = _pin(ws, "sub-a", pinned)
    assert _prior(ws, "sub-a").pr_number == 11


def test_pull_request_is_never_recovered_from_a_sibling(ws_root: Path) -> None:
    ws = _split(ws_root, "sub-a", "sub-b")
    _write(ws, "sub-b", "b1", 22)
    latest = _write(ws, "sub-a", "a1")
    assert _prior(ws, "sub-a") == latest


def test_operator_attached_pull_request_survives_the_pin_being_cleared(ws_root: Path) -> None:
    ws = _split(ws_root, "sub-a", "sub-b")
    _write(ws, "sub-a", "a1", 5)
    escalation = _write(ws, "sub-a", "a-esc")
    _write(ws, "sub-b", "b1", 9)
    ws = _pin(ws, "sub-a", escalation)
    attached = record_pr_attachment(ws, escalation, 7)
    ws = _pin(ws, "sub-a", attached)
    ws = workspace_mod.update_task_status(
        ws, "sub-a", TaskStatus.AWAITING_INPUT, clear_blocked_handover_id=True
    )
    prior = _prior(ws, "sub-a")
    assert prior.handover_id == attached.handover_id
    assert prior.pr_number == 7
    assert _prior(ws, "sub-b").pr_number == 9


def test_attribution_reads_each_distinct_invocation_once_and_never_lists_invocations(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _split(ws_root, "sub-a", "sub-b")
    inv_a = _invocation(ws, "sub-a")
    inv_b = _invocation(ws, "sub-b")
    _save(ws, "a1", inv_a, 11)
    _save(ws, "a2", inv_a)
    _save(ws, "b1", inv_b, 22)
    _save(ws, "b2", inv_b)
    loaded: list[Path] = []
    real_load = workflow_mod.load_workflow_cached

    def _spy(path: Path) -> WorkflowDoc:
        loaded.append(path)
        return real_load(path)

    def _forbidden(_ws: Workspace) -> list[str]:
        raise AssertionError("list_invocations must not be consulted")

    monkeypatch.setattr(workflow_mod, "load_workflow_cached", _spy)
    monkeypatch.setattr(workflow_mod, "list_invocations", _forbidden)
    expected = sorted(
        [workflow_mod.workflow_path(ws, inv_a), workflow_mod.workflow_path(ws, inv_b)]
    )
    prior = _prior(ws, "sub-a")
    assert sorted(loaded) == expected
    loaded.clear()
    pr_less = find_handover_by_id(ws, "h-a2")
    assert pr_less is not None
    recover_prior_pr(ws, "sub-a", pr_less)
    assert sorted(loaded) == expected
    assert prior.pr_number == 11


def test_recover_prior_pr_returns_a_handover_that_already_has_a_pull_request_without_any_reads(
    ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _split(ws_root, "sub-a")
    own = _write(ws, "sub-a", "a1", 11)

    def _forbidden(_ws: Workspace) -> list[Handover]:
        raise AssertionError("handovers must not be listed")

    monkeypatch.setattr(dispatch_mod, "list_handovers", _forbidden)
    assert recover_prior_pr(ws, "sub-a", own) is own


def test_recover_prior_pr_fills_only_the_missing_number(ws_root: Path) -> None:
    ws = _split(ws_root, "sub-a", "sub-b")
    _write(ws, "sub-a", "a1", 11)
    escalation = _write(ws, "sub-a", "a-esc")
    _write(ws, "sub-b", "b1", 22)
    recovered = recover_prior_pr(ws, "sub-a", escalation)
    assert recovered.pr_number == 11
    assert recovered.model_dump(exclude={"pr_number"}) == escalation.model_dump(
        exclude={"pr_number"}
    )


def test_recover_prior_pr_returns_a_handover_absent_from_the_listing_unchanged(
    ws_root: Path,
) -> None:
    ws = _split(ws_root, "sub-a")
    _write(ws, "sub-a", "a1", 11)
    stray = Handover(
        handover_id="h-stray",
        handover_type="escalation",
        task_id=ws.task_id,
        from_team="implementation_fix",
        from_invocation_id="inv_001",
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="not on disk",
    )
    assert recover_prior_pr(ws, "sub-a", stray) is stray


def test_recover_prior_pr_leaves_an_unattributable_workspace_unchanged(ws_root: Path) -> None:
    ws = _split(ws_root, "sub-a")
    _save(ws, "u1", "inv_998", 11)
    escalation = _save(ws, "u2", "inv_999")
    assert recover_prior_pr(ws, "sub-a", escalation) == escalation


def test_dispatch_records_the_subtasks_own_latest_handover_as_the_parent(
    queue: Queue, ws_root: Path
) -> None:
    ws = _split(ws_root, "sub-a", "sub-b")
    own = _write(ws, "sub-a", "a1", 11)
    _write(ws, "sub-b", "b1", 22)
    outcome = dispatch_team_core(
        workspace=ws, task_id="sub-a", team=_StubFixTeam(), queue=queue, pubsub=PubSub(),
        enqueue_workflow=enqueue_with_pending_workflow, merge_gate=_SKIPPED_MERGE_GATE,
    )
    job = queue.get_job(outcome.job.id)
    assert job is not None
    assert job.parent_handover_id == own.handover_id
    assert find_handover_by_id(ws, own.handover_id) is not None


def test_dispatch_never_bumps_a_sibling_pull_request(queue: Queue, ws_root: Path) -> None:
    ws = _split(ws_root, "sub-a", "sub-b")
    _write(ws, "sub-a", "a1", 11)
    _write(ws, "sub-b", "b-esc")
    dispatch_team_core(
        workspace=ws, task_id="sub-a", team=_StubFixTeam(), queue=queue, pubsub=PubSub(),
        enqueue_workflow=enqueue_with_pending_workflow, merge_gate=_SKIPPED_MERGE_GATE,
    )
    assert _pr_state_paths.load_fix_cycle_state(11, DEFAULT_FORGE_TARGET).count == 1


def test_operator_fix_cycle_bump_acts_on_a_recovered_pull_request(
    queue: Queue, ws_root: Path
) -> None:
    ws = _split(ws_root, "sub-a")
    _write(ws, "sub-a", "a1", 33)
    escalation = _write(ws, "sub-a", "a-esc")
    ws = _pin(ws, "sub-a", escalation)
    dispatch_team_core(
        workspace=ws, task_id="sub-a", team=_StubFixTeam(), queue=queue, pubsub=PubSub(),
        enqueue_workflow=enqueue_with_pending_workflow, merge_gate=_SKIPPED_MERGE_GATE,
    )
    assert _pr_state_paths.load_fix_cycle_state(33, DEFAULT_FORGE_TARGET).count == 1


def test_fix_ceiling_reads_the_subtasks_own_pull_request(ws_root: Path) -> None:
    ws = _split(ws_root, "sub-a", "sub-b")
    _write(ws, "sub-a", "a1", 11)
    _write(ws, "sub-b", "b1", 22)
    for _ in range(6):
        _pr_state_paths.bump_fix_cycle_count(22, DEFAULT_FORGE_TARGET)
    assert_team_dispatchable(ws, "sub-a", "implementation_fix")
    with pytest.raises(AppError) as exc_info:
        assert_team_dispatchable(ws, "sub-b", "implementation_fix")
    assert exc_info.value.status_code == 409
    assert "PR #22" in exc_info.value.detail


def test_fix_ceiling_applies_to_a_recovered_pull_request(ws_root: Path) -> None:
    ws = _split(ws_root, "sub-a")
    _write(ws, "sub-a", "a1", 44)
    escalation = _write(ws, "sub-a", "a-esc")
    ws = _pin(ws, "sub-a", escalation)
    for _ in range(6):
        _pr_state_paths.bump_fix_cycle_count(44, DEFAULT_FORGE_TARGET)
    with pytest.raises(AppError) as exc_info:
        assert_team_dispatchable(ws, "sub-a", "implementation_fix")
    assert "PR #44" in exc_info.value.detail


def test_load_prior_recovers_the_pull_request_the_pinned_escalation_lacks(
    queue: Queue, ws_root: Path, dispatcher: Dispatcher
) -> None:
    ws = _split(ws_root, "sub-a", "sub-b")
    _write(ws, "sub-a", "a1", 11)
    escalation = _write(ws, "sub-a", "a-esc")
    _write(ws, "sub-b", "b1", 22)
    job = queue.enqueue(
        "sub-a", "implementation_fix", workspace_id=ws.workspace_id,
        parent_handover_id=escalation.handover_id,
    )
    loaded = dispatcher._load_prior(ws, job)  # noqa: SLF001
    assert loaded is not None
    assert loaded.handover_id == escalation.handover_id
    assert loaded.pr_number == 11


def test_load_prior_without_a_parent_resolves_nothing(
    queue: Queue, ws_root: Path, dispatcher: Dispatcher
) -> None:
    ws = _split(ws_root, "sub-a")
    job = queue.enqueue("sub-a", "implementation_fix", workspace_id=ws.workspace_id)
    assert dispatcher._load_prior(ws, job) is None  # noqa: SLF001


def test_load_prior_still_raises_for_a_missing_parent_handover(
    queue: Queue, ws_root: Path, dispatcher: Dispatcher
) -> None:
    ws = _split(ws_root, "sub-a")
    _write(ws, "sub-a", "a1", 11)
    job = queue.enqueue(
        "sub-a", "implementation_fix", workspace_id=ws.workspace_id,
        parent_handover_id="h-gone",
    )
    with pytest.raises(RuntimeError, match="h-gone"):
        dispatcher._load_prior(ws, job)  # noqa: SLF001


def test_load_prior_returns_a_parent_absent_from_the_listing_unchanged(
    queue: Queue, ws_root: Path, dispatcher: Dispatcher, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _split(ws_root, "sub-a")
    _write(ws, "sub-a", "a1", 11)
    escalation = _write(ws, "sub-a", "a-esc")
    job = queue.enqueue(
        "sub-a", "implementation_fix", workspace_id=ws.workspace_id,
        parent_handover_id=escalation.handover_id,
    )
    monkeypatch.setattr(dispatch_mod, "list_handovers", lambda _ws: [])
    assert dispatcher._load_prior(ws, job) == escalation  # noqa: SLF001


def test_load_prior_leaves_a_parent_that_already_carries_a_pull_request_unchanged(
    queue: Queue, ws_root: Path, dispatcher: Dispatcher
) -> None:
    ws = _split(ws_root, "sub-a")
    _write(ws, "sub-a", "a1", 11)
    parent = _write(ws, "sub-a", "a2", 12)
    job = queue.enqueue(
        "sub-a", "implementation_fix", workspace_id=ws.workspace_id,
        parent_handover_id=parent.handover_id,
    )
    assert dispatcher._load_prior(ws, job) == parent  # noqa: SLF001


def test_hook_and_host_resolver_agree_on_a_pinned_pr_less_escalation(
    queue: Queue, ws_root: Path, dispatcher: Dispatcher
) -> None:
    ws = _split(ws_root, "sub-a", "sub-b")
    _write(ws, "sub-a", "a1", 11)
    escalation = _write(ws, "sub-a", "a-esc")
    _write(ws, "sub-b", "b1", 22)
    ws = _pin(ws, "sub-a", escalation)
    host = _prior(ws, "sub-a")
    job = queue.enqueue(
        "sub-a", "implementation_fix", workspace_id=ws.workspace_id,
        parent_handover_id=host.handover_id,
    )
    assert dispatcher._load_prior(ws, job) == host  # noqa: SLF001
