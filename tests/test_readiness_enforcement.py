from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import alan._dispatcher_loop as loop_mod
from alan import control_plane, lane_limit, server
from alan import engine_preflight as ep
from alan import workspace as workspace_mod
from alan.app import workspace_creation
from alan.dispatch import assert_team_dispatchable, team_is_dispatchable
from alan.errors import AppError
from alan.events import PubSub
from alan.handover import Handover, OpenQuestion, list_handovers, save_handover
from alan.queue import READINESS_HOLD_DELAY_S, JobStatus, Queue, QueueError
from alan.teams._base import TeamEvent
from tests.fake_clock import FakeClock
from tests.readiness_support import (
    CODE_QUALITY,
    CONNECTED_SLUG,
    NOT_READY_CASES,
    REVIEWER,
    SEEDED_COUNTERS,
    Install,
    SweepTeam,
    enqueue_sweep_job,
    make_dispatcher,
)

_CASES = list(NOT_READY_CASES)
_NOW = 1_000_000.0


@pytest.fixture
def install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Install:
    return Install.isolated(tmp_path, monkeypatch)


@pytest.fixture(autouse=True)
def _no_open_pr_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("alan.predispatch_scan.list_open_prs", lambda: [])


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(epoch=_NOW)


@pytest.fixture
def queue(tmp_path: Path, clock: FakeClock) -> Iterator[Queue]:
    q = Queue(db_path=tmp_path / "queue.db", clock=clock)
    try:
        yield q
    finally:
        q.close()


@pytest.fixture
def client(ws_root: Path, queue: Queue, pubsub: PubSub) -> Iterator[TestClient]:
    server.app.dependency_overrides[server._queue_dep] = lambda: queue
    server.app.dependency_overrides[server._pubsub_dep] = lambda: pubsub
    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: ws_root
    try:
        with TestClient(server.app, follow_redirects=True) as c:
            yield c
    finally:
        server.app.dependency_overrides.clear()


def _control_headers() -> dict[str, str]:
    return {control_plane.CONTROL_TOKEN_HEADER: os.environ[control_plane.CONTROL_TOKEN_ENV]}


def _creation_refusal() -> AppError:
    with pytest.raises(AppError) as raised:
        workspace_creation.resolve_engine_key(None, None, engine_key_provided=False)
    return raised.value


def _on_a_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def _escalation(task_id: str) -> Handover:
    question = OpenQuestion(
        id="verif-judge-parse",
        question="judge failed to parse",
        proposed_answer_space=["retry", "review manually"],
        recommended="retry",
        question_class="verification_escalation",
        resolver_role="human",
        blocking=True,
    )
    return Handover(
        handover_id=f"handover_{task_id}_esc",
        handover_type="escalation",
        task_id=task_id,
        from_team="verification",
        from_invocation_id="inv_001",
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="escalated for human input",
        open_questions=[question],
    )


@pytest.mark.parametrize("case", _CASES)
def test_creation_is_refused_naming_the_engine_the_requirement_and_the_fix(
    install: Install, case: str
) -> None:
    arrange, requirement = NOT_READY_CASES[case]
    arrange(install)
    refusal = _creation_refusal()
    assert refusal.status_code == 409
    assert install.default_cfg().repo.slug in refusal.detail
    assert requirement in refusal.detail
    assert ep.readiness_fix(requirement, install.default_cfg()) in refusal.detail


@pytest.mark.parametrize("case", _CASES)
def test_creating_a_workspace_over_http_is_refused_before_any_workspace_exists(
    client: TestClient, install: Install, ws_root: Path, case: str
) -> None:
    arrange, requirement = NOT_READY_CASES[case]
    arrange(install)
    res = client.post("/api/workspaces", json={"title": "Held", "description": "d"})
    assert res.status_code == 409
    assert requirement in res.json()["detail"]
    assert workspace_mod.list_workspaces(root=ws_root) == []


def test_creation_proceeds_when_every_requirement_is_satisfied(
    client: TestClient, install: Install, ws_root: Path
) -> None:
    install.satisfy_everything()
    assert workspace_creation.resolve_engine_key(None, None, engine_key_provided=False) is None
    res = client.post("/api/workspaces", json={"title": "Fine", "description": "d"})
    assert res.status_code == 201
    assert len(workspace_mod.list_workspaces(root=ws_root)) == 1


def test_the_creation_check_runs_off_the_event_loop_outside_the_lane_lock_and_before_creation(
    client: TestClient, install: Install, monkeypatch: pytest.MonkeyPatch, ws_root: Path
) -> None:
    install.satisfy_everything()
    seen: list[tuple[bool, bool, int]] = []
    real = ep.evaluate_readiness

    def _spy(cfg: Any, **kwargs: Any) -> Any:
        seen.append(
            (
                _on_a_loop(),
                lane_limit._creation_lock.locked(),  # noqa: SLF001
                len(workspace_mod.list_workspaces(root=ws_root)),
            )
        )
        return real(cfg, **kwargs)

    monkeypatch.setattr(ep, "evaluate_readiness", _spy)
    res = client.post("/api/workspaces", json={"title": "Order", "description": "d"})
    assert res.status_code == 201
    assert seen == [(False, False, 0)]


def test_an_unknown_engine_is_still_a_400_ahead_of_readiness(install: Install) -> None:
    install.require(CODE_QUALITY)
    with pytest.raises(AppError) as raised:
        workspace_creation.resolve_engine_key("ghost/none", None, engine_key_provided=True)
    assert raised.value.status_code == 400


def test_creation_on_a_connected_repository_is_refused_with_its_own_fix(
    install: Install,
) -> None:
    install.require(REVIEWER)
    install.connect()
    with pytest.raises(AppError) as raised:
        workspace_creation.resolve_engine_key(CONNECTED_SLUG, None, engine_key_provided=True)
    assert raised.value.status_code == 409
    assert CONNECTED_SLUG in raised.value.detail
    assert f"store a reviewer credential for {CONNECTED_SLUG}" in raised.value.detail


def test_creation_on_a_connected_repository_proceeds_once_its_reviewer_is_stored(
    install: Install,
) -> None:
    install.require(REVIEWER)
    connected = install.connect()
    install.store_reviewer(CONNECTED_SLUG)
    install.save_preflight("pass", cfg=connected)
    resolved = workspace_creation.resolve_engine_key(
        CONNECTED_SLUG, None, engine_key_provided=True
    )
    assert resolved == CONNECTED_SLUG


@pytest.mark.parametrize("case", _CASES)
def test_a_directly_enqueued_sweep_job_is_held_at_pickup_naming_the_requirement(
    install: Install,
    queue: Queue,
    ws_root: Path,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    case: str,
) -> None:
    arrange, requirement = NOT_READY_CASES[case]
    arrange(install)
    _ws, job = enqueue_sweep_job(queue, ws_root)
    team = SweepTeam()
    pools: list[object] = []
    monkeypatch.setattr(loop_mod, "set_active_pool", pools.append)
    with caplog.at_level(logging.WARNING, logger="alan.engine_preflight"):
        assert make_dispatcher(queue, ws_root, team, clock=clock).run_once() is True
    held = queue.get_job(job.id)
    assert held is not None
    assert held.status is JobStatus.PENDING
    assert held.started_at is None
    assert held.eligible_at == _NOW + READINESS_HOLD_DELAY_S
    assert held.error is None
    for column, value in SEEDED_COUNTERS.items():
        assert getattr(held, column) == value, column
    assert team.runs == 0
    assert pools == []
    report = ep.readiness_report()
    assert any(i.requirement == requirement and i.state == "not_ready" for i in report)
    assert requirement in caplog.text


@pytest.mark.parametrize("team_name", ["implementation_fix", "pr_merge", "research"])
def test_unblocker_and_merge_gate_dispatches_are_held_at_pickup_like_any_other(
    install: Install, queue: Queue, ws_root: Path, clock: FakeClock, team_name: str
) -> None:
    install.require(CODE_QUALITY)
    _ws, job = enqueue_sweep_job(queue, ws_root, team_name=team_name)
    team = SweepTeam()
    make_dispatcher(queue, ws_root, team, clock=clock, team_name=team_name).run_once()
    held = queue.get_job(job.id)
    assert held is not None
    assert held.status is JobStatus.PENDING
    assert team.runs == 0


def test_with_every_requirement_satisfied_a_sweep_job_runs_at_pickup(
    install: Install, queue: Queue, ws_root: Path, clock: FakeClock
) -> None:
    install.satisfy_everything()
    enqueue_sweep_job(queue, ws_root)
    team = SweepTeam()
    assert make_dispatcher(queue, ws_root, team, clock=clock).run_once() is True
    assert team.runs == 1


def test_a_held_job_resumes_by_itself_once_the_requirements_hold(
    install: Install, queue: Queue, ws_root: Path, clock: FakeClock
) -> None:
    install.require(CODE_QUALITY)
    _ws, job = enqueue_sweep_job(queue, ws_root)
    team = SweepTeam()
    dispatcher = make_dispatcher(queue, ws_root, team, clock=clock)
    assert dispatcher.run_once() is True
    assert dispatcher.run_once() is False
    install.record_latch()
    assert dispatcher.run_once() is False
    clock.advance(READINESS_HOLD_DELAY_S)
    assert dispatcher.run_once() is True
    assert team.runs == 1
    finished = queue.get_job(job.id)
    assert finished is not None
    assert finished.requeue_count == SEEDED_COUNTERS["requeue_count"]


def test_a_job_stays_held_while_the_requirement_keeps_failing(
    install: Install, queue: Queue, ws_root: Path, clock: FakeClock
) -> None:
    install.require(CODE_QUALITY)
    _ws, job = enqueue_sweep_job(queue, ws_root)
    team = SweepTeam()
    dispatcher = make_dispatcher(queue, ws_root, team, clock=clock)
    for _ in range(3):
        dispatcher.run_once()
        clock.advance(READINESS_HOLD_DELAY_S)
    held = queue.get_job(job.id)
    assert held is not None
    assert held.status is JobStatus.PENDING
    assert held.eligible_at == _NOW + 3 * READINESS_HOLD_DELAY_S
    assert held.interruption_count == SEEDED_COUNTERS["interruption_count"]
    assert team.runs == 0


def test_a_hold_publishes_no_event_and_leaves_the_workspace_untouched(
    install: Install, queue: Queue, ws_root: Path, clock: FakeClock
) -> None:
    install.require(CODE_QUALITY)
    ws, _job = enqueue_sweep_job(queue, ws_root)
    before = workspace_mod.read(ws.task_id, root=ws_root).meta
    pubsub = PubSub()
    events: list[TeamEvent] = []
    pubsub.subscribe_all(events.append)
    make_dispatcher(queue, ws_root, SweepTeam(), clock=clock, pubsub=pubsub).run_once()
    assert events == []
    assert workspace_mod.read(ws.task_id, root=ws_root).meta == before


def test_an_unknown_readiness_lets_the_job_run_and_warns(
    install: Install,
    queue: Queue,
    ws_root: Path,
    clock: FakeClock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    install.require(REVIEWER)
    install.store_reviewer()
    enqueue_sweep_job(queue, ws_root)
    team = SweepTeam()
    with caplog.at_level(logging.WARNING, logger="alan.engine_preflight"):
        make_dispatcher(queue, ws_root, team, clock=clock).run_once()
    assert team.runs == 1
    assert "reviewer_credential=unknown" in caplog.text


def test_a_queue_error_while_holding_is_swallowed_and_never_fails_the_job(
    install: Install,
    queue: Queue,
    ws_root: Path,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    install.require(CODE_QUALITY)
    _ws, job = enqueue_sweep_job(queue, ws_root)

    def _raise(_job_id: int) -> Any:
        raise QueueError("job is not running")

    monkeypatch.setattr(queue, "hold_until_ready", _raise)
    with caplog.at_level(logging.WARNING, logger="alan.dispatcher"):
        assert make_dispatcher(queue, ws_root, SweepTeam(), clock=clock).run_once() is True
    stored = queue.get_job(job.id)
    assert stored is not None
    assert stored.status is JobStatus.RUNNING
    assert stored.error is None
    assert "no longer RUNNING" in caplog.text


def test_team_is_dispatchable_does_not_consult_readiness(
    install: Install, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install.require(CODE_QUALITY)
    ws = workspace_mod.create("Unblock", "d", root=ws_root)
    calls: list[object] = []

    def _record(*args: Any, **_kwargs: Any) -> tuple[()]:
        calls.append(args)
        return ()

    monkeypatch.setattr(ep, "evaluate_readiness", _record)
    assert team_is_dispatchable(ws, "pr_merge") is True
    assert team_is_dispatchable(ws, "implementation_fix") is True
    assert calls == []


@pytest.mark.parametrize("case", _CASES)
def test_manual_dispatch_is_refused_with_the_same_message_as_creation(
    install: Install, ws_root: Path, case: str
) -> None:
    arrange, _requirement = NOT_READY_CASES[case]
    arrange(install)
    creation = _creation_refusal()
    ws = workspace_mod.create("Manual", "d", root=ws_root)
    with pytest.raises(AppError) as raised:
        assert_team_dispatchable(ws, ws.task_id, "implementation")
    assert raised.value.status_code == 409
    assert raised.value.detail == creation.detail


@pytest.mark.parametrize("case", _CASES)
def test_dispatching_over_http_is_refused_and_enqueues_nothing(
    client: TestClient, install: Install, queue: Queue, ws_root: Path, case: str
) -> None:
    arrange, requirement = NOT_READY_CASES[case]
    arrange(install)
    ws = workspace_mod.create("Manual Http", "d", root=ws_root)
    res = client.post(
        f"/api/workspaces/{ws.task_id}/tasks/{ws.task_id}/dispatch", json={"team": "research"}
    )
    assert res.status_code == 409
    assert requirement in res.json()["detail"]
    assert queue.list_jobs() == []


def test_dispatching_over_http_proceeds_when_every_requirement_is_satisfied(
    client: TestClient, install: Install, queue: Queue, ws_root: Path
) -> None:
    install.satisfy_everything()
    ws = workspace_mod.create("Manual Fine", "d", root=ws_root)
    res = client.post(
        f"/api/workspaces/{ws.task_id}/tasks/{ws.task_id}/dispatch", json={"team": "research"}
    )
    assert res.status_code == 202
    assert len(queue.list_jobs()) == 1


def test_answering_a_question_on_a_not_ready_engine_is_refused_and_the_question_stays_pending(
    client: TestClient, install: Install, queue: Queue, ws_root: Path
) -> None:
    install.require(CODE_QUALITY)
    ws = workspace_mod.create("Answer Held", "d", root=ws_root)
    save_handover(ws, _escalation(ws.task_id))
    url = f"/api/workspaces/{ws.task_id}/tasks/{ws.task_id}/answer"
    body = {"question_id": "verif-judge-parse", "chosen_option": "retry"}

    refused = client.post(url, json=body, headers=_control_headers())
    assert refused.status_code == 409
    assert "code_quality" in refused.json()["detail"]
    assert "./setup.sh" in refused.json()["detail"]
    assert queue.list_jobs() == []
    handovers = list_handovers(ws)
    assert [h.handover_type for h in handovers] == ["escalation"]
    assert handovers[0].open_questions[0].answer is None

    install.record_latch()
    accepted = client.post(url, json=body, headers=_control_headers())
    assert accepted.status_code == 200
    assert accepted.json()["dispatch"]["team"] == "verification"
    assert len(queue.list_jobs()) == 1
