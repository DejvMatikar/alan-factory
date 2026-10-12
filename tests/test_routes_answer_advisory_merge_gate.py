from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import httpx
import pytest
from fastapi.testclient import TestClient

from alan import control_plane, merge_gate, merge_recovery, server
from alan.events import PubSub
from alan.forge import JsonObject, set_active_forge
from alan.handover import Handover, OpenQuestion, save_handover
from alan.merge_recovery import MergeBlockClass
from alan.queue import Queue
from alan.teams import _pr_ready, _pr_state_paths
from alan.workspace import Status, Workspace, create, read
from tests import _advisory_check_runs_support as support
from tests.fakes.forge import FakeForge

_CONFLICT_BRANCH = "teams/answer-advisory-fix-inv_1"
_SONAR_LISTING = [support.check_run(support.SONAR, support.SONAR_APP)]


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


@pytest.fixture(autouse=True)
def _fake_forge_stays_active(client: TestClient, _active_fake_forge: FakeForge) -> None:
    set_active_forge(_active_fake_forge)


@pytest.fixture
def audit_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "advisory-logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(root))
    return root


@pytest.fixture(autouse=True)
def _no_system_head(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_pr_state_paths, "load_system_head", lambda *_a, **_kw: None)


@pytest.fixture(autouse=True)
def _baseline_is_fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(merge_gate, "stale_measured_baseline_forces_check", lambda *_a: False)


def _headers() -> dict[str, str]:
    return {control_plane.CONTROL_TOKEN_HEADER: os.environ[control_plane.CONTROL_TOKEN_ENV]}


def _escalation(ws: Workspace, pr_number: int) -> Handover:
    question = OpenQuestion.model_validate(dict(
        id="q1", question="how to unblock?", proposed_answer_space=["proceed", "wait"],
        question_class="merge_block_escalation", resolver_role="human", blocking=True,
    ))
    return Handover(
        handover_id=f"handover_{ws.task_id}_esc",
        handover_type="escalation",
        task_id=ws.task_id,
        from_team="pr_merge",
        from_invocation_id="inv_001",
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="escalated for human input",
        open_questions=[question],
        pr_number=pr_number,
    )


def _post_answer(client: TestClient, ws: Workspace) -> httpx.Response:
    url = f"/api/workspaces/{ws.task_id}/tasks/{ws.task_id}/answer"
    body = {"question_id": "q1", "chosen_option": "proceed"}
    return cast(httpx.Response, client.post(url, json=body, headers=_headers()))


def _spy_recovery(
    monkeypatch: pytest.MonkeyPatch, block_class: MergeBlockClass,
) -> list[_pr_ready.PrReadiness]:
    seen: list[_pr_ready.PrReadiness] = []

    def fake(
        _pr: int, _task: str, readiness: _pr_ready.PrReadiness, *_a: object, **_kw: object,
    ) -> tuple[bool, MergeBlockClass, None]:
        seen.append(readiness)
        return (False, block_class, None)

    monkeypatch.setattr(merge_recovery, "attempt_mechanical_recovery", fake)
    return seen


def _sonar_red(merge_state: str = "UNSTABLE", head_ref_name: str = "") -> JsonObject:
    payload = support.pr_payload(
        [support.failing_run(support.SONAR), support.passing_run("ci")], merge_state=merge_state,
    )
    return {**payload, "headRefName": head_ref_name} if head_ref_name else payload


def test_answer_dispatches_pr_merge_for_an_advisory_only_red_pr_without_recovery(
    client: TestClient, ws_root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch,
    audit_root: Path,
) -> None:
    ws = create("Answer Advisory Only", "d", root=ws_root)
    save_handover(ws, _escalation(ws, 101))
    support.serve_check_runs(_SONAR_LISTING)
    support.serve_pr_reads(_sonar_red())
    seen = _spy_recovery(monkeypatch, MergeBlockClass.GENUINE)

    res = _post_answer(client, ws)

    assert res.status_code == 200
    assert res.json()["dispatch"]["team"] == "pr_merge"
    assert queue.list_jobs()
    assert seen == []
    assert read(ws.task_id, root=ws_root).meta.status is not Status.AWAITING_INPUT
    [row] = support.impl_audit_rows(audit_root, "advisory_check_failures_ignored")
    assert row["lane"] == "merge_gate"
    assert row["ignored_checks"] == [support.SONAR]


def test_answer_sends_the_unfiltered_readiness_to_recovery_when_the_listing_is_unreadable(
    client: TestClient, ws_root: Path, monkeypatch: pytest.MonkeyPatch, audit_root: Path,
) -> None:
    ws = create("Answer Advisory Unreadable", "d", root=ws_root)
    save_handover(ws, _escalation(ws, 202))
    support.serve_check_runs(None)
    support.serve_pr_reads(_sonar_red())
    seen = _spy_recovery(monkeypatch, MergeBlockClass.GENUINE)

    res = _post_answer(client, ws)

    assert res.status_code == 200
    assert [readiness.failing_checks for readiness in seen] == [(support.SONAR,)]
    assert support.impl_audit_rows(audit_root, "advisory_check_failures_ignored") == []


def test_answer_routes_a_dirty_pr_with_only_advisory_red_to_the_conflict_fix(
    client: TestClient, ws_root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch,
    audit_root: Path,
) -> None:
    ws = create("Answer Advisory Dirty", "d", root=ws_root)
    save_handover(ws, _escalation(ws, 303))
    support.serve_check_runs(_SONAR_LISTING)
    support.serve_pr_reads(_sonar_red("DIRTY", _CONFLICT_BRANCH))
    seen = _spy_recovery(monkeypatch, MergeBlockClass.DIRTY)

    res = _post_answer(client, ws)

    assert res.status_code == 200
    assert res.json()["dispatch"]["team"] == "implementation_fix"
    assert [readiness.failed_reasons for readiness in seen] == [("mergeStateStatus=DIRTY",)]
    assert [j.team for j in queue.list_jobs()] == ["implementation_fix"]
