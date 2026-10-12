from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import httpx
import pytest
from fastapi.testclient import TestClient

from alan import clause_withdrawal, control_plane, server
from alan.events import PubSub
from alan.queue import Queue
from alan.workspace import Requirement, Status, Workspace, create, read, transition


@pytest.fixture
def client(ws_root: Path, queue: Queue, pubsub: PubSub) -> Iterator[TestClient]:
    server.app.dependency_overrides[server._queue_dep] = lambda: queue  # noqa: SLF001
    server.app.dependency_overrides[server._pubsub_dep] = lambda: pubsub  # noqa: SLF001
    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: ws_root  # noqa: SLF001
    try:
        with TestClient(server.app, follow_redirects=True) as c:
            yield c
    finally:
        server.app.dependency_overrides.clear()


def _control_headers() -> dict[str, str]:
    return {control_plane.CONTROL_TOKEN_HEADER: os.environ[control_plane.CONTROL_TOKEN_ENV]}


def _ws_with_clause(ws_root: Path, title: str) -> tuple[Workspace, Requirement]:
    req = Requirement(text="acceptance clause")
    ws = create(title, "d", root=ws_root, requirements=[req])
    return ws, req


def _put_withdrawal(
    client: TestClient,
    ws: Workspace,
    clause_id: str,
    *,
    headers: dict[str, str] | None = None,
    body: dict[str, object] | None = None,
) -> httpx.Response:
    return cast(
        httpx.Response,
        client.put(
            f"/api/workspaces/{ws.task_id}/clauses/{clause_id}/withdrawal",
            json=body if body is not None else {"reason": "operator repair"},
            headers=_control_headers() if headers is None else headers,
        ),
    )


def _delete_withdrawal(
    client: TestClient,
    ws: Workspace,
    clause_id: str,
    *,
    headers: dict[str, str] | None = None,
    body: dict[str, object] | None = None,
) -> httpx.Response:
    return cast(
        httpx.Response,
        client.request(
            "DELETE",
            f"/api/workspaces/{ws.task_id}/clauses/{clause_id}/withdrawal",
            json=body if body is not None else {"reason": "operator reversal"},
            headers=_control_headers() if headers is None else headers,
        ),
    )


def test_withdraws_the_named_clause(client: TestClient, ws_root: Path) -> None:
    ws, req = _ws_with_clause(ws_root, "Operator Withdraw WS")
    res = _put_withdrawal(client, ws, req.id)
    assert res.status_code == 200
    data = res.json()
    [withdrawn] = [r for r in data["requirements"] if r["id"] == req.id]
    assert withdrawn["withdrawn_by_question_id"] == "operator:withdrawal"


def test_writes_clause_withdrawal_audit_row(
    client: TestClient, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs_root = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs_root))
    ws, req = _ws_with_clause(ws_root, "Operator Audit WS")
    _put_withdrawal(client, ws, req.id, body={"reason": "manual repair"})
    entry = json.loads(
        (logs_root / "scenario_override_audit.jsonl").read_text().splitlines()[0]
    )
    assert entry["kind"] == "clause_withdrawal"
    assert entry["workspace_id"] == ws.task_id
    assert entry["clause_id"] == req.id
    assert entry["reason"] == "manual repair"


def test_unknown_workspace_404(client: TestClient) -> None:
    res = client.put(
        "/api/workspaces/ghost/clauses/req-x/withdrawal",
        json={"reason": "r"},
        headers=_control_headers(),
    )
    assert res.status_code == 404


def test_archived_workspace_409(client: TestClient, ws_root: Path) -> None:
    ws, req = _ws_with_clause(ws_root, "Operator Archived WS")
    transition(ws, Status.ARCHIVED)
    res = _put_withdrawal(client, ws, req.id)
    assert res.status_code == 409


def test_unknown_clause_id_404(client: TestClient, ws_root: Path) -> None:
    ws, _req = _ws_with_clause(ws_root, "Operator Unknown Clause WS")
    res = _put_withdrawal(client, ws, "req-does-not-exist")
    assert res.status_code == 404


def test_already_withdrawn_409(client: TestClient, ws_root: Path) -> None:
    ws, req = _ws_with_clause(ws_root, "Operator Already Withdrawn WS")
    first = _put_withdrawal(client, ws, req.id)
    assert first.status_code == 200
    second = _put_withdrawal(client, ws, req.id)
    assert second.status_code == 409


def test_without_control_token_401(client: TestClient, ws_root: Path) -> None:
    ws, req = _ws_with_clause(ws_root, "Operator 401 WS")
    res = _put_withdrawal(client, ws, req.id, headers={})
    assert res.status_code == 401


def test_wrong_control_token_403(client: TestClient, ws_root: Path) -> None:
    ws, req = _ws_with_clause(ws_root, "Operator 403 WS")
    res = _put_withdrawal(
        client, ws, req.id, headers={control_plane.CONTROL_TOKEN_HEADER: "wrong-token"}
    )
    assert res.status_code == 403


def test_empty_reason_422(client: TestClient, ws_root: Path) -> None:
    ws, req = _ws_with_clause(ws_root, "Operator Empty Reason WS")
    res = _put_withdrawal(client, ws, req.id, body={"reason": ""})
    assert res.status_code == 422


def test_does_not_record_an_intervention(client: TestClient, ws_root: Path) -> None:
    ws, req = _ws_with_clause(ws_root, "Operator No Intervention WS")
    _put_withdrawal(client, ws, req.id)
    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.interventions == []


def test_concurrent_duplicate_withdrawals_only_one_succeeds(
    client: TestClient, ws_root: Path
) -> None:
    ws, req = _ws_with_clause(ws_root, "Operator Concurrent WS")
    results: list[httpx.Response] = []
    results_lock = threading.Lock()

    def _call() -> None:
        res = _put_withdrawal(client, ws, req.id)
        with results_lock:
            results.append(res)

    threads = [threading.Thread(target=_call) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(r.status_code for r in results) == [200, 409, 409, 409, 409]


def test_reinstates_the_named_clause(client: TestClient, ws_root: Path) -> None:
    ws, req = _ws_with_clause(ws_root, "Operator Reinstate WS")
    _put_withdrawal(client, ws, req.id)
    res = _delete_withdrawal(client, ws, req.id)
    assert res.status_code == 200
    data = res.json()
    [reinstated] = [r for r in data["requirements"] if r["id"] == req.id]
    assert reinstated["withdrawn_by_question_id"] is None


def test_reinstate_unknown_workspace_404(client: TestClient) -> None:
    res = client.request(
        "DELETE",
        "/api/workspaces/ghost/clauses/req-x/withdrawal",
        json={"reason": "r"},
        headers=_control_headers(),
    )
    assert res.status_code == 404


def test_reinstate_archived_workspace_409(client: TestClient, ws_root: Path) -> None:
    ws, req = _ws_with_clause(ws_root, "Operator Reinstate Archived WS")
    _put_withdrawal(client, ws, req.id)
    transition(ws, Status.ARCHIVED)
    res = _delete_withdrawal(client, ws, req.id)
    assert res.status_code == 409


def test_reinstate_unknown_clause_id_404(client: TestClient, ws_root: Path) -> None:
    ws, _req = _ws_with_clause(ws_root, "Operator Reinstate Unknown Clause WS")
    res = _delete_withdrawal(client, ws, "req-does-not-exist")
    assert res.status_code == 404


def test_reinstate_not_withdrawn_clause_409(client: TestClient, ws_root: Path) -> None:
    ws, req = _ws_with_clause(ws_root, "Operator Reinstate Live Clause WS")
    res = _delete_withdrawal(client, ws, req.id)
    assert res.status_code == 409
    assert res.json()["detail"] == "clause is not withdrawn"


def test_reinstate_second_call_on_reinstated_clause_409(
    client: TestClient, ws_root: Path
) -> None:
    ws, req = _ws_with_clause(ws_root, "Operator Reinstate Twice WS")
    _put_withdrawal(client, ws, req.id)
    first = _delete_withdrawal(client, ws, req.id)
    assert first.status_code == 200
    second = _delete_withdrawal(client, ws, req.id)
    assert second.status_code == 409
    assert second.json()["detail"] == "clause is not withdrawn"


def test_reinstate_question_withdrawn_clause_409_and_marker_survives(
    client: TestClient, ws_root: Path
) -> None:
    ws, req = _ws_with_clause(ws_root, "Operator Reinstate Question Withdrawn WS")
    clause_withdrawal.withdraw(ws, [req.id], "clause-stall-x")
    res = _delete_withdrawal(client, ws, req.id)
    assert res.status_code == 409
    assert res.json()["detail"] == "clause was withdrawn by a human answering a question"
    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.requirements is not None
    assert reloaded.meta.requirements[0].withdrawn_by_question_id == "clause-stall-x"


def test_reinstate_not_withdrawn_check_precedes_provenance_check(
    client: TestClient, ws_root: Path
) -> None:
    ws, req = _ws_with_clause(ws_root, "Operator Reinstate Order WS")
    res = _delete_withdrawal(client, ws, req.id)
    assert res.status_code == 409
    assert res.json()["detail"] == "clause is not withdrawn"


def test_reinstate_without_control_token_401(client: TestClient, ws_root: Path) -> None:
    ws, req = _ws_with_clause(ws_root, "Operator Reinstate 401 WS")
    _put_withdrawal(client, ws, req.id)
    res = _delete_withdrawal(client, ws, req.id, headers={})
    assert res.status_code == 401


def test_reinstate_wrong_control_token_403(client: TestClient, ws_root: Path) -> None:
    ws, req = _ws_with_clause(ws_root, "Operator Reinstate 403 WS")
    _put_withdrawal(client, ws, req.id)
    res = _delete_withdrawal(
        client, ws, req.id, headers={control_plane.CONTROL_TOKEN_HEADER: "wrong-token"}
    )
    assert res.status_code == 403


def test_reinstate_empty_reason_422(client: TestClient, ws_root: Path) -> None:
    ws, req = _ws_with_clause(ws_root, "Operator Reinstate Empty Reason WS")
    _put_withdrawal(client, ws, req.id)
    res = _delete_withdrawal(client, ws, req.id, body={"reason": ""})
    assert res.status_code == 422


def test_writes_clause_reinstatement_audit_row(
    client: TestClient, ws_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs_root = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs_root))
    ws, req = _ws_with_clause(ws_root, "Operator Reinstate Audit WS")
    _put_withdrawal(client, ws, req.id)
    _delete_withdrawal(client, ws, req.id, body={"reason": "mistyped clause id"})
    lines = (logs_root / "scenario_override_audit.jsonl").read_text().splitlines()
    entry = json.loads(lines[1])
    assert entry["kind"] == "clause_reinstatement"
    assert entry["workspace_id"] == ws.task_id
    assert entry["clause_id"] == req.id
    assert entry["reason"] == "mistyped clause id"


def test_writes_no_audit_row_when_the_reinstatement_mutation_fails(
    client: TestClient,
    ws_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logs_root = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs_root))
    ws, req = _ws_with_clause(ws_root, "Operator Reinstate Failed Mutation WS")
    _put_withdrawal(client, ws, req.id)

    def _boom(*_args: object, **_kwargs: object) -> Workspace:
        raise RuntimeError("mutation failed")

    monkeypatch.setattr(clause_withdrawal, "reinstate", _boom)
    with pytest.raises(RuntimeError):
        _delete_withdrawal(client, ws, req.id)
    path = logs_root / "scenario_override_audit.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    assert not any(r["kind"] == "clause_reinstatement" for r in rows)
