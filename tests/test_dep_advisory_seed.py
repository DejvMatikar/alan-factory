from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from alan import dep_advisory_seed, retention_registry
from alan import workflow as workflow_mod
from alan import workspace as workspace_mod
from alan.app.workspace_creation import is_hidden_by_default
from alan.dep_advisory_seed import seed_advisory_checks
from alan.dispatch_provenance import DispatchSurface
from alan.engine_config import EngineConfig
from alan.queue import JobStatus, Queue
from alan.teams.dep_advisory import DEP_ADVISORY_TITLE, checked_run_key, pending_run_key
from alan.teams.triage import RedMainRun
from alan.workspace import Status, Workspace
from tests.dep_advisory_support import isolate_state, register_engine

_SLUG = "acme/widget"


@pytest.fixture(autouse=True)
def _state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    isolate_state(monkeypatch, tmp_path)


def _run(run_id: str = "100", slug: str = _SLUG) -> RedMainRun:
    return RedMainRun(slug, run_id, "abc123")


def _advisory_workspaces(ws_root: Path) -> list[Workspace]:
    return [
        ws for ws in workspace_mod.list_workspaces(root=ws_root)
        if ws.meta.title == DEP_ADVISORY_TITLE
    ]


def test_an_opted_in_engine_gets_a_hidden_advisory_workspace_and_one_job(
    queue: Queue, ws_root: Path
) -> None:
    register_engine()

    seed_advisory_checks(queue, ws_root, [_run()])

    (ws,) = _advisory_workspaces(ws_root)
    (job,) = queue.list_jobs(task_id=ws.task_id, status=JobStatus.PENDING)
    assert ws.meta.team == "dependency_sweep"
    assert ws.meta.engine_key == _SLUG
    assert ws.meta.dispatch_provenance.surface == DispatchSurface.DEP_ADVISORY_CHECK
    assert is_hidden_by_default(ws.meta.team, ws.meta.title) is True
    assert job.team == "dependency_sweep"
    assert queue.get_meta(pending_run_key(_SLUG)) == "100"


def test_the_job_carries_a_pending_workflow_for_the_sweep_runner_node(
    queue: Queue, ws_root: Path
) -> None:
    register_engine()

    seed_advisory_checks(queue, ws_root, [_run()])

    (ws,) = _advisory_workspaces(ws_root)
    (job,) = queue.list_jobs(task_id=ws.task_id, status=JobStatus.PENDING)
    graph = workflow_mod.load_graph(ws, job.triggered_by_invocation_id or "")
    assert graph.doc.team == "dependency_sweep"
    assert [task.id for task in graph.doc.tasks] == ["sweep_runner"]
    assert graph.doc.tasks[0].status == "pending"


def test_seeding_never_touches_the_weekly_cadence_keys(queue: Queue, ws_root: Path) -> None:
    register_engine()
    queue.set_meta(f"dep_sweep_next_run:{_SLUG}", "1234.5")

    seed_advisory_checks(queue, ws_root, [_run()])

    assert queue.get_meta(f"dep_sweep_next_run:{_SLUG}") == "1234.5"
    assert queue.get_meta(f"dep_sweep_last_scheduled_at:{_SLUG}") is None


def test_a_pending_job_makes_a_second_trigger_a_no_op(queue: Queue, ws_root: Path) -> None:
    register_engine()
    seed_advisory_checks(queue, ws_root, [_run("100")])

    seed_advisory_checks(queue, ws_root, [_run("101")])

    (ws,) = _advisory_workspaces(ws_root)
    assert len(queue.list_jobs(task_id=ws.task_id, status=JobStatus.PENDING)) == 1
    assert queue.get_meta(pending_run_key(_SLUG)) == "100"


def test_a_running_job_makes_a_second_trigger_a_no_op(queue: Queue, ws_root: Path) -> None:
    register_engine()
    seed_advisory_checks(queue, ws_root, [_run("100")])
    claimed = queue.claim()
    assert claimed is not None

    seed_advisory_checks(queue, ws_root, [_run("101")])

    (ws,) = _advisory_workspaces(ws_root)
    assert queue.list_jobs(task_id=ws.task_id, status=JobStatus.PENDING) == []
    assert len(queue.list_jobs(task_id=ws.task_id, status=JobStatus.RUNNING)) == 1


def test_a_run_that_was_already_checked_is_skipped(queue: Queue, ws_root: Path) -> None:
    register_engine()
    queue.set_meta(checked_run_key(_SLUG), "100")

    seed_advisory_checks(queue, ws_root, [_run("100")])

    assert _advisory_workspaces(ws_root) == []
    assert queue.get_meta(pending_run_key(_SLUG)) is None


def test_a_new_run_after_a_checked_one_is_audited_again(queue: Queue, ws_root: Path) -> None:
    register_engine()
    queue.set_meta(checked_run_key(_SLUG), "100")

    seed_advisory_checks(queue, ws_root, [_run("101")])

    (ws,) = _advisory_workspaces(ws_root)
    assert len(queue.list_jobs(task_id=ws.task_id, status=JobStatus.PENDING)) == 1
    assert queue.get_meta(pending_run_key(_SLUG)) == "101"


@pytest.mark.parametrize(
    ("hidden", "enabled", "vuln_scan"),
    [(True, True, True), (False, False, True), (False, True, False)],
)
def test_engines_that_have_not_opted_in_are_skipped(
    queue: Queue, ws_root: Path, hidden: bool, enabled: bool, vuln_scan: bool
) -> None:
    register_engine(hidden=hidden, enabled=enabled, vuln_scan=vuln_scan)

    seed_advisory_checks(queue, ws_root, [_run()])

    assert _advisory_workspaces(ws_root) == []
    assert queue.get_meta(pending_run_key(_SLUG)) is None


def test_an_unregistered_engine_is_skipped(queue: Queue, ws_root: Path) -> None:
    seed_advisory_checks(queue, ws_root, [_run(slug="ghost/none")])

    assert _advisory_workspaces(ws_root) == []


def test_a_finished_advisory_workspace_is_woken_and_reused(queue: Queue, ws_root: Path) -> None:
    register_engine()
    seed_advisory_checks(queue, ws_root, [_run("100")])
    (first,) = _advisory_workspaces(ws_root)
    queue.delete_jobs(first.task_id)
    workspace_mod.transition(first, Status.COMPLETED)

    seed_advisory_checks(queue, ws_root, [_run("101")])

    (ws,) = _advisory_workspaces(ws_root)
    assert ws.task_id == first.task_id
    assert ws.meta.status == Status.ACTIVE
    assert len(queue.list_jobs(task_id=ws.task_id, status=JobStatus.PENDING)) == 1


def test_an_archived_advisory_workspace_is_woken_and_reused(queue: Queue, ws_root: Path) -> None:
    register_engine()
    seed_advisory_checks(queue, ws_root, [_run("100")])
    (first,) = _advisory_workspaces(ws_root)
    queue.delete_jobs(first.task_id)
    workspace_mod.transition(first, Status.ARCHIVED)

    seed_advisory_checks(queue, ws_root, [_run("101")])

    (ws,) = _advisory_workspaces(ws_root)
    assert ws.meta.status == Status.ACTIVE


def test_each_engine_gets_its_own_advisory_workspace(queue: Queue, ws_root: Path) -> None:
    register_engine()
    register_engine("other-org", "gadget")

    seed_advisory_checks(queue, ws_root, [_run(), _run("200", "other-org/gadget")])

    keys = {ws.meta.engine_key for ws in _advisory_workspaces(ws_root)}
    assert keys == {_SLUG, "other-org/gadget"}
    assert queue.get_meta(pending_run_key("other-org/gadget")) == "200"


def test_one_engines_failure_never_blocks_another(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    register_engine()
    register_engine("other-org", "gadget")
    real = dep_advisory_seed._advisory_workspace_if_idle  # noqa: SLF001

    def _flaky(q: Queue, root: Path | None, cfg: EngineConfig) -> Any:
        if cfg.repo.slug == _SLUG:
            raise RuntimeError("disk full")
        return real(q, root, cfg)

    monkeypatch.setattr(dep_advisory_seed, "_advisory_workspace_if_idle", _flaky)

    seed_advisory_checks(queue, ws_root, [_run(), _run("200", "other-org/gadget")])

    assert {ws.meta.engine_key for ws in _advisory_workspaces(ws_root)} == {"other-org/gadget"}
    assert "seeding failed for acme/widget" in caplog.text


def _boom(*_args: object, **_kwargs: object) -> None:
    raise RuntimeError("queue down")


def _invocation_count(ws: Workspace) -> int:
    return len(list((ws.path / "invocations").iterdir()))


def test_an_enqueue_failure_removes_the_new_workspace_and_the_pending_run(
    queue: Queue,
    ws_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    register_engine()
    monkeypatch.setattr(queue, "enqueue", _boom)

    seed_advisory_checks(queue, ws_root, [_run()])

    assert _advisory_workspaces(ws_root) == []
    assert queue.get_meta(pending_run_key(_SLUG)) is None
    assert "seeding failed for acme/widget" in caplog.text


def test_a_workflow_seeding_failure_removes_the_new_workspace(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_engine()
    monkeypatch.setattr(workflow_mod, "seed_pending_workflow", _boom)

    seed_advisory_checks(queue, ws_root, [_run()])

    assert _advisory_workspaces(ws_root) == []
    assert queue.get_meta(pending_run_key(_SLUG)) is None


def test_the_next_sweep_after_a_rolled_back_seed_opens_one_clean_workspace(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_engine()
    real_enqueue = queue.enqueue
    monkeypatch.setattr(queue, "enqueue", _boom)
    seed_advisory_checks(queue, ws_root, [_run()])
    monkeypatch.setattr(queue, "enqueue", real_enqueue)

    seed_advisory_checks(queue, ws_root, [_run()])

    (ws,) = _advisory_workspaces(ws_root)
    assert len(queue.list_jobs(task_id=ws.task_id, status=JobStatus.PENDING)) == 1
    assert _invocation_count(ws) == 1
    assert queue.get_meta(pending_run_key(_SLUG)) == "100"


def test_an_enqueue_failure_on_a_reused_workspace_keeps_it_and_drops_the_new_invocation(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_engine()
    seed_advisory_checks(queue, ws_root, [_run("100")])
    (first,) = _advisory_workspaces(ws_root)
    queue.delete_jobs(first.task_id)
    workspace_mod.transition(first, Status.COMPLETED)
    monkeypatch.setattr(queue, "enqueue", _boom)

    seed_advisory_checks(queue, ws_root, [_run("101")])

    (ws,) = _advisory_workspaces(ws_root)
    assert ws.task_id == first.task_id
    assert _invocation_count(ws) == 1
    assert queue.list_jobs(task_id=ws.task_id, status=JobStatus.PENDING) == []
    assert queue.get_meta(pending_run_key(_SLUG)) is None


def test_duplicate_runs_for_one_engine_collapse_to_the_latest(
    queue: Queue, ws_root: Path
) -> None:
    register_engine()

    seed_advisory_checks(queue, ws_root, [_run("100"), _run("101")])

    (ws,) = _advisory_workspaces(ws_root)
    assert len(queue.list_jobs(task_id=ws.task_id, status=JobStatus.PENDING)) == 1
    assert queue.get_meta(pending_run_key(_SLUG)) == "101"


def test_an_empty_run_list_does_nothing(queue: Queue, ws_root: Path) -> None:
    register_engine()

    seed_advisory_checks(queue, ws_root, [])

    assert _advisory_workspaces(ws_root) == []


def test_the_recurring_workspace_is_pruned_on_every_seed(
    queue: Queue, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_engine()
    seen: list[str] = []
    monkeypatch.setattr(
        retention_registry, "seed_recurring_workspace", lambda ws: seen.append(ws.meta.title)
    )

    seed_advisory_checks(queue, ws_root, [_run()])

    assert seen == [DEP_ADVISORY_TITLE]
