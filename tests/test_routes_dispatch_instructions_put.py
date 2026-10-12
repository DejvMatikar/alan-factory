from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from alan import control_plane, server, sticky_instructions
from alan import workspace as workspace_mod
from alan.dispatch import OWNERLESS_DECOMPOSED_NO_QUESTION_DETAIL
from alan.handover import Handover, OpenQuestion, save_handover
from alan.queue import JobStatus, Queue
from alan.roots import logs_root
from alan.routes import dispatch_instructions as dispatch_instructions_route
from alan.routes.dispatch_instructions import IN_FLIGHT_NOTICE
from alan.workspace import Task, TaskStatus, Workspace

AUDIT_FILE = "sticky_instruction_write_audit.jsonl"
RULING = "Edit .github/workflows/ci.yml line 41: copy the `permissions` entry from line 12."


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "ws"


@pytest.fixture
def queue(tmp_path: Path) -> Iterator[Queue]:
    q = Queue(db_path=tmp_path / "queue.db")
    try:
        yield q
    finally:
        with contextlib.suppress(Exception):
            q.close()


@pytest.fixture
def client(
    ws_root: Path, queue: Queue, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    server.app.dependency_overrides[server._queue_dep] = lambda: queue  # noqa: SLF001
    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: ws_root  # noqa: SLF001
    try:
        with TestClient(server.app) as c:
            yield c
    finally:
        server.app.dependency_overrides.clear()


@pytest.fixture
def headers(client: TestClient) -> dict[str, str]:
    _ = client
    return {control_plane.CONTROL_TOKEN_HEADER: os.environ[control_plane.CONTROL_TOKEN_ENV]}


def _url(task_id: str) -> str:
    return f"/api/workspaces/{task_id}/sticky-instructions"


def _audit_rows() -> list[dict[str, Any]]:
    path = logs_root() / AUDIT_FILE
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _stored(ws_root: Path, ws: Workspace) -> dict[str, workspace_mod.StickyInstruction]:
    return workspace_mod.read(ws.task_id, root=ws_root).meta.dispatch_instructions


def _blocking_handover(task_id: str, handover_id: str) -> Handover:
    return Handover(
        handover_id=handover_id,
        handover_type="escalation",
        task_id=task_id,
        from_team="verification",
        from_invocation_id="inv_001",
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="escalated",
        open_questions=[
            OpenQuestion(
                id=f"{handover_id}-q",
                question="which?",
                proposed_answer_space=["a", "b"],
                resolver_role="human",
                blocking=True,
            )
        ],
    )


def _decomposed_with_owner(ws_root: Path) -> Workspace:
    tasks = [
        Task(id="sub-idle", status=TaskStatus.PENDING),
        Task(id="sub-blocked", status=TaskStatus.AWAITING_INPUT, blocked_handover_id="h-open"),
    ]
    ws = workspace_mod.create("decomposed-owner", "d", root=ws_root, tasks=tasks)
    save_handover(ws, _blocking_handover(ws.task_id, "h-open"))
    return ws


def _decomposed_without_owner(ws_root: Path) -> Workspace:
    tasks = [
        Task(id="sub-a", status=TaskStatus.PENDING),
        Task(id="sub-b", status=TaskStatus.PENDING),
    ]
    return workspace_mod.create("decomposed-ownerless", "d", root=ws_root, tasks=tasks)


def _fill_to_limit(ws: Workspace, count: int) -> Workspace:
    for index in range(count):
        ws = workspace_mod.set_sticky_instructions(
            ws, f"filler-{index}", f"text {index}", origin="dispatch"
        )
    return ws


def test_put_then_get_returns_what_was_written(
    client: TestClient, headers: dict[str, str], ws_root: Path
) -> None:
    ws = workspace_mod.create("put-get", "d", root=ws_root)
    put = client.put(_url(ws.task_id), json={"text": RULING}, headers=headers)
    assert put.status_code == 200
    assert put.json() == {
        "task_id": ws.task_id,
        "text": RULING,
        "origin": "dispatch",
        "notice": IN_FLIGHT_NOTICE,
    }
    got = client.get(_url(ws.task_id))
    assert got.json() == {"text": RULING, "origin": "dispatch"}


def test_put_normalizes_text_the_way_dispatch_typed_instructions_are(
    client: TestClient, headers: dict[str, str], ws_root: Path
) -> None:
    ws = workspace_mod.create("put-strip", "d", root=ws_root)
    put = client.put(_url(ws.task_id), json={"text": f"  \n{RULING}\n\t "}, headers=headers)
    assert put.status_code == 200
    assert put.json()["text"] == RULING
    assert _stored(ws_root, ws)[ws.task_id].text == RULING


def test_put_replaces_earlier_text_for_the_same_task(
    client: TestClient, headers: dict[str, str], ws_root: Path
) -> None:
    ws = workspace_mod.create("put-replace", "d", root=ws_root)
    client.put(_url(ws.task_id), json={"text": "first ruling"}, headers=headers)
    client.put(_url(ws.task_id), json={"text": "second ruling"}, headers=headers)
    stored = _stored(ws_root, ws)
    assert list(stored) == [ws.task_id]
    assert stored[ws.task_id].text == "second ruling"


def test_put_same_text_twice_leaves_the_stored_state_as_after_the_first_write(
    client: TestClient, headers: dict[str, str], ws_root: Path
) -> None:
    ws = workspace_mod.create("put-idempotent", "d", root=ws_root)
    first = client.put(_url(ws.task_id), json={"text": RULING}, headers=headers)
    after_first = workspace_mod.read(ws.task_id, root=ws_root).meta
    second = client.put(_url(ws.task_id), json={"text": RULING}, headers=headers)
    after_second = workspace_mod.read(ws.task_id, root=ws_root).meta
    assert first.json() == second.json()
    assert after_second == after_first
    assert after_second.updated_at == after_first.updated_at


def test_put_of_identical_text_stored_as_generated_rewrites_it_as_dispatch(
    client: TestClient, headers: dict[str, str], ws_root: Path
) -> None:
    ws = workspace_mod.create("put-generated", "d", root=ws_root)
    workspace_mod.set_sticky_instructions(ws, ws.task_id, RULING, origin="generated")
    put = client.put(_url(ws.task_id), json={"text": RULING}, headers=headers)
    assert put.status_code == 200
    assert _stored(ws_root, ws)[ws.task_id].origin == "dispatch"


def test_put_stores_exactly_what_dispatch_time_persistence_stores(
    client: TestClient, headers: dict[str, str], ws_root: Path
) -> None:
    written = workspace_mod.create("put-parity-a", "d", root=ws_root)
    typed = workspace_mod.create("put-parity-b", "d", root=ws_root)
    client.put(_url(written.task_id), json={"text": RULING}, headers=headers)
    workspace_mod.set_sticky_instructions(typed, typed.task_id, RULING, origin="dispatch")
    assert _stored(ws_root, written)[written.task_id] == _stored(ws_root, typed)[typed.task_id]


def test_put_text_reaches_the_next_enqueue_composition_for_a_single_task_workspace(
    client: TestClient, headers: dict[str, str], ws_root: Path
) -> None:
    ws = workspace_mod.create("put-compose", "d", root=ws_root)
    client.put(_url(ws.task_id), json={"text": RULING}, headers=headers)
    reread = workspace_mod.read(ws.task_id, root=ws_root)
    composed, origin = sticky_instructions.for_enqueue(
        reread, ws.task_id, "implementation_fix", "generated brief"
    )
    assert composed == f"{RULING}\n\ngenerated brief"
    assert origin == "inherited"


def test_put_text_of_exactly_the_composition_ceiling_survives_composition(
    client: TestClient, headers: dict[str, str], ws_root: Path
) -> None:
    ws = workspace_mod.create("put-ceiling", "d", root=ws_root)
    text = "x" * sticky_instructions.MAX_COMPOSED_CHARS
    put = client.put(_url(ws.task_id), json={"text": text}, headers=headers)
    assert put.status_code == 200
    reread = workspace_mod.read(ws.task_id, root=ws_root)
    composed, _origin = sticky_instructions.for_enqueue(
        reread, ws.task_id, "implementation_fix", None
    )
    assert composed == text


def test_put_on_a_decomposed_workspace_targets_the_task_dispatch_targets(
    client: TestClient, headers: dict[str, str], ws_root: Path
) -> None:
    ws = _decomposed_with_owner(ws_root)
    put = client.put(_url(ws.task_id), json={"text": RULING}, headers=headers)
    assert put.status_code == 200
    assert put.json()["task_id"] == "sub-blocked"
    assert list(_stored(ws_root, ws)) == ["sub-blocked"]
    reread = workspace_mod.read(ws.task_id, root=ws_root)
    composed, origin = sticky_instructions.for_enqueue(
        reread, "sub-blocked", "implementation_fix", None
    )
    assert composed == RULING
    assert origin == "inherited"


def test_get_and_delete_on_a_decomposed_workspace_resolve_to_the_owning_subtask(
    client: TestClient, headers: dict[str, str], ws_root: Path
) -> None:
    ws = _decomposed_with_owner(ws_root)
    client.put(_url(ws.task_id), json={"text": RULING}, headers=headers)
    assert client.get(_url(ws.task_id)).json() == {"text": RULING, "origin": "dispatch"}
    assert client.delete(_url(ws.task_id)).status_code == 204
    assert _stored(ws_root, ws) == {}
    assert client.get(_url(ws.task_id)).json() == {"text": None, "origin": None}


def test_put_on_a_decomposed_workspace_with_no_single_owner_is_a_404_and_stores_nothing(
    client: TestClient, headers: dict[str, str], ws_root: Path
) -> None:
    ws = _decomposed_without_owner(ws_root)
    put = client.put(_url(ws.task_id), json={"text": RULING}, headers=headers)
    assert put.status_code == 404
    assert put.json()["detail"] == OWNERLESS_DECOMPOSED_NO_QUESTION_DETAIL.format(
        task_id=ws.task_id, count=0
    )
    assert _stored(ws_root, ws) == {}
    assert _audit_rows() == []


def test_get_and_delete_on_a_decomposed_workspace_with_no_single_owner_use_the_raw_key(
    client: TestClient, ws_root: Path
) -> None:
    ws = _decomposed_without_owner(ws_root)
    workspace_mod.set_sticky_instructions(ws, ws.task_id, RULING, origin="dispatch")
    assert client.get(_url(ws.task_id)).json() == {"text": RULING, "origin": "dispatch"}
    assert client.delete(_url(ws.task_id)).status_code == 204
    assert _stored(ws_root, ws) == {}


def test_put_on_an_unknown_workspace_is_a_404(
    client: TestClient, headers: dict[str, str]
) -> None:
    put = client.put(_url("ghost"), json={"text": RULING}, headers=headers)
    assert put.status_code == 404
    assert _audit_rows() == []


@pytest.mark.parametrize("text", ["", " ", "\n\t  \n"], ids=["empty", "space", "whitespace"])
def test_put_of_empty_or_blank_text_is_a_422_and_stores_nothing(
    client: TestClient, headers: dict[str, str], ws_root: Path, text: str
) -> None:
    ws = workspace_mod.create("put-blank", "d", root=ws_root)
    put = client.put(_url(ws.task_id), json={"text": text}, headers=headers)
    assert put.status_code == 422
    assert _stored(ws_root, ws) == {}
    assert _audit_rows() == []


def test_put_of_text_over_the_composition_ceiling_is_a_422_and_stores_nothing(
    client: TestClient, headers: dict[str, str], ws_root: Path
) -> None:
    ws = workspace_mod.create("put-too-long", "d", root=ws_root)
    text = "x" * (sticky_instructions.MAX_COMPOSED_CHARS + 1)
    put = client.put(_url(ws.task_id), json={"text": text}, headers=headers)
    assert put.status_code == 422
    assert _stored(ws_root, ws) == {}
    assert _audit_rows() == []


def test_put_of_padded_text_whose_trimmed_form_fits_the_ceiling_is_accepted(
    client: TestClient, headers: dict[str, str], ws_root: Path
) -> None:
    ws = workspace_mod.create("put-padded", "d", root=ws_root)
    trimmed = "x" * sticky_instructions.MAX_COMPOSED_CHARS
    put = client.put(_url(ws.task_id), json={"text": f"  \n{trimmed}\n\t  "}, headers=headers)
    assert put.status_code == 200
    assert _stored(ws_root, ws)[ws.task_id].text == trimmed


def test_put_of_padded_text_whose_trimmed_form_exceeds_the_ceiling_is_a_422(
    client: TestClient, headers: dict[str, str], ws_root: Path
) -> None:
    ws = workspace_mod.create("put-padded-long", "d", root=ws_root)
    trimmed = "x" * (sticky_instructions.MAX_COMPOSED_CHARS + 1)
    put = client.put(_url(ws.task_id), json={"text": f"  {trimmed}  "}, headers=headers)
    assert put.status_code == 422
    assert str(sticky_instructions.MAX_COMPOSED_CHARS) in put.json()["detail"]
    assert _stored(ws_root, ws) == {}
    assert _audit_rows() == []


def test_put_without_text_is_a_422(
    client: TestClient, headers: dict[str, str], ws_root: Path
) -> None:
    ws = workspace_mod.create("put-no-text", "d", root=ws_root)
    assert client.put(_url(ws.task_id), json={}, headers=headers).status_code == 422


def test_put_of_a_new_key_at_the_entry_limit_is_a_409_and_stores_nothing(
    client: TestClient, headers: dict[str, str], ws_root: Path
) -> None:
    ws = _fill_to_limit(workspace_mod.create("put-full", "d", root=ws_root), 200)
    put = client.put(_url(ws.task_id), json={"text": RULING}, headers=headers)
    assert put.status_code == 409
    assert "200" in put.json()["detail"]
    assert ws.task_id not in _stored(ws_root, ws)
    assert _audit_rows() == []


def test_put_replacing_an_existing_key_at_the_entry_limit_is_accepted(
    client: TestClient, headers: dict[str, str], ws_root: Path
) -> None:
    ws = workspace_mod.create("put-full-replace", "d", root=ws_root)
    ws = workspace_mod.set_sticky_instructions(ws, ws.task_id, "old ruling", origin="dispatch")
    ws = _fill_to_limit(ws, 199)
    put = client.put(_url(ws.task_id), json={"text": RULING}, headers=headers)
    assert put.status_code == 200
    stored = _stored(ws_root, ws)
    assert len(stored) == 200
    assert stored[ws.task_id].text == RULING


def test_put_without_a_control_token_is_a_401_and_stores_nothing(
    client: TestClient, ws_root: Path
) -> None:
    ws = workspace_mod.create("put-no-token", "d", root=ws_root)
    put = client.put(_url(ws.task_id), json={"text": RULING})
    assert put.status_code == 401
    assert _stored(ws_root, ws) == {}
    assert _audit_rows() == []


def test_put_with_a_wrong_control_token_is_a_403_and_stores_nothing(
    client: TestClient, ws_root: Path
) -> None:
    ws = workspace_mod.create("put-bad-token", "d", root=ws_root)
    bad = {control_plane.CONTROL_TOKEN_HEADER: "wrong-token"}
    put = client.put(_url(ws.task_id), json={"text": RULING}, headers=bad)
    assert put.status_code == 403
    assert _stored(ws_root, ws) == {}
    assert _audit_rows() == []


def test_put_does_not_alter_or_cancel_a_job_already_running(
    client: TestClient, headers: dict[str, str], ws_root: Path, queue: Queue
) -> None:
    ws = workspace_mod.create("put-running", "d", root=ws_root)
    job = queue.enqueue(
        ws.task_id,
        "implementation_fix",
        instructions="the instructions this pass was enqueued with",
        instructions_origin="dispatch",
    )
    claimed = queue.claim()
    assert claimed is not None
    assert claimed.id == job.id
    put = client.put(_url(ws.task_id), json={"text": RULING}, headers=headers)
    assert put.status_code == 200
    after = queue.get_job(job.id)
    assert after is not None
    assert after.status == JobStatus.RUNNING
    assert after.instructions == "the instructions this pass was enqueued with"
    assert after.instructions_origin == "dispatch"


def test_every_accepted_write_including_a_repeat_is_recorded_in_the_audit_file(
    client: TestClient, headers: dict[str, str], ws_root: Path
) -> None:
    ws = workspace_mod.create("put-audit", "d", root=ws_root)
    client.put(_url(ws.task_id), json={"text": RULING}, headers=headers)
    client.put(_url(ws.task_id), json={"text": RULING}, headers=headers)
    client.put(_url(ws.task_id), json={"text": "second — ruling"}, headers=headers)
    rows = _audit_rows()
    assert [row["text"] for row in rows] == [RULING, RULING, "second — ruling"]
    for row in rows:
        assert row["workspace_id"] == ws.task_id
        assert row["task_id"] == ws.task_id
        assert row["origin"] == "dispatch"
        assert row["length"] == len(row["text"])
        assert datetime.fromisoformat(row["timestamp_utc"]).tzinfo is not None


@pytest.fixture(params=["logs-parent-is-a-file", "audit-path-is-a-directory"])
def unwritable_logs(request: pytest.FixtureRequest, tmp_path: Path) -> Path:
    if request.param == "logs-parent-is-a-file":
        blocker = tmp_path / "blocker"
        blocker.write_text("", encoding="utf-8")
        return blocker / "logs"
    logs = tmp_path / "logs"
    (logs / AUDIT_FILE).mkdir(parents=True)
    return logs


def test_a_write_is_accepted_and_stored_when_the_audit_file_cannot_be_written(
    client: TestClient,
    headers: dict[str, str],
    ws_root: Path,
    unwritable_logs: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(dispatch_instructions_route, "logs_root", lambda: unwritable_logs)
    ws = workspace_mod.create("put-audit-fails", "d", root=ws_root)
    put = client.put(_url(ws.task_id), json={"text": RULING}, headers=headers)
    assert put.status_code == 200
    assert put.json()["text"] == RULING
    assert _stored(ws_root, ws)[ws.task_id].text == RULING


def test_the_audit_row_records_the_resolved_task_and_the_addressed_workspace(
    client: TestClient, headers: dict[str, str], ws_root: Path
) -> None:
    ws = _decomposed_with_owner(ws_root)
    client.put(_url(ws.task_id), json={"text": RULING}, headers=headers)
    [row] = _audit_rows()
    assert row["workspace_id"] == ws.task_id
    assert row["task_id"] == "sub-blocked"


def test_the_response_body_states_that_a_running_pass_keeps_its_enqueued_instructions(
    client: TestClient, headers: dict[str, str], ws_root: Path
) -> None:
    ws = workspace_mod.create("put-notice", "d", root=ws_root)
    notices = [
        client.put(_url(ws.task_id), json={"text": RULING}, headers=headers).json()["notice"]
        for _ in range(2)
    ]
    assert notices == [IN_FLIGHT_NOTICE, IN_FLIGHT_NOTICE]
    assert "already running keeps the instructions it was enqueued with" in IN_FLIGHT_NOTICE
    assert "follows this write" in IN_FLIGHT_NOTICE


def test_the_notice_names_the_teams_that_never_receive_the_text_and_the_ceiling() -> None:
    for team in sticky_instructions.NON_CONSUMING_TEAMS:
        assert team in IN_FLIGHT_NOTICE
    assert "composition ceiling" in IN_FLIGHT_NOTICE
    assert "manual dispatch carries only the instructions supplied for it" in IN_FLIGHT_NOTICE


def test_the_openapi_description_states_the_delivery_boundary_and_the_lack_of_atomicity(
    client: TestClient,
) -> None:
    operation = client.get("/openapi.json").json()["paths"][
        "/api/workspaces/{task_id}/sticky-instructions"
    ]["put"]
    assert "already running keeps the instructions it was enqueued with" in operation["description"]
    assert "not atomic" in operation["description"]
    assert {"401", "403", "404", "409", "422"} <= set(operation["responses"])
