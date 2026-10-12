from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from alan import control_plane, operator_session, operator_ticket, server
from alan.operator_session import Unavailable
from alan.teams._base import TeamEvent
from alan.workspace import InterventionAction, Workspace, create, read
from tests.conftest import FakeGit, FakeTmux, git_reply, register_test_engine

_STATUS = "/api/operator/status"
_SESSION = "/api/operator/session"
_TICKET = "/api/operator/attach-ticket"


@pytest.fixture(autouse=True)
def _connected_repos() -> None:
    register_test_engine("o/r", default=True)
    register_test_engine("some-owner/some-repo")


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


@pytest.fixture
def client(
    operator_state: Path, ws_root: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    del operator_state
    monkeypatch.setattr(operator_session, "availability", lambda: None)
    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: ws_root  # noqa: SLF001
    operator_ticket.clear()
    try:
        with TestClient(server.app) as c:
            yield c
    finally:
        server.app.dependency_overrides.clear()
        operator_ticket.clear()


@pytest.fixture
def events(client: TestClient) -> list[TeamEvent]:
    seen: list[TeamEvent] = []
    del client
    server.app.state.pubsub.subscribe_all(seen.append)
    return seen


def _headers() -> dict[str, str]:
    return {control_plane.CONTROL_TOKEN_HEADER: os.environ[control_plane.CONTROL_TOKEN_ENV]}


def _start(client: TestClient, **body: Any) -> Any:
    return client.post(_SESSION, json={"scope": "fleet", **body}, headers=_headers())


def test_status_is_readable_without_the_control_token(client: TestClient) -> None:
    res = client.get(_STATUS)
    assert res.status_code == 200
    assert res.json() == {"session": None, "unavailable": None}


def test_status_names_the_exact_unavailable_cause(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        operator_session,
        "availability",
        lambda: Unavailable(reason="tmux_missing", detail="tmux is not installed."),
    )
    body = client.get(_STATUS).json()
    assert body["session"] is None
    assert body["unavailable"] == {"reason": "tmux_missing", "detail": "tmux is not installed."}


def test_status_reports_a_live_session(client: TestClient) -> None:
    started = _start(client, scope="repo", target="some-owner/some-repo").json()
    body = client.get(_STATUS).json()
    assert body["unavailable"] is None
    assert body["session"] == {
        "scope": "repo",
        "target": "some-owner/some-repo",
        "started_at": started["started_at"],
        "update_warning": None,
        "exited": False,
    }


@pytest.mark.parametrize(
    ("method", "path"), [("POST", _SESSION), ("DELETE", _SESSION), ("POST", _TICKET)]
)
def test_every_mutation_refuses_a_request_without_the_control_token(
    client: TestClient, fake_tmux: FakeTmux, method: str, path: str
) -> None:
    body = {"json": {"scope": "fleet"}} if method == "POST" and path == _SESSION else {}
    res = client.request(method, path, **body)
    assert res.status_code == 401
    assert fake_tmux.calls == []


@pytest.mark.parametrize(
    ("method", "path"), [("POST", _SESSION), ("DELETE", _SESSION), ("POST", _TICKET)]
)
def test_every_mutation_refuses_a_wrong_control_token(
    client: TestClient, fake_tmux: FakeTmux, method: str, path: str
) -> None:
    headers = {control_plane.CONTROL_TOKEN_HEADER: "not-the-token"}
    body = {"json": {"scope": "fleet"}} if method == "POST" and path == _SESSION else {}
    res = client.request(method, path, headers=headers, **body)
    assert res.status_code == 403
    assert fake_tmux.calls == []


def test_start_spawns_a_session_and_returns_it(
    client: TestClient, fake_tmux: FakeTmux, events: list[TeamEvent]
) -> None:
    res = _start(client)
    assert res.status_code == 200
    body = res.json()
    assert body["scope"] == "fleet"
    assert body["target"] is None
    assert body["exited"] is False
    assert fake_tmux.live is True
    assert [e.kind for e in events] == ["operator_session_changed"]


def test_start_over_a_live_session_needs_an_explicit_replace(
    client: TestClient, fake_tmux: FakeTmux
) -> None:
    _start(client)
    refused = _start(client, scope="repo", target="o/r")
    assert refused.status_code == 409
    assert "replace" in refused.json()["detail"]
    assert len(fake_tmux.calls_of("new-session")) == 1
    replaced = _start(client, scope="repo", target="o/r", replace=True)
    assert replaced.status_code == 200
    assert replaced.json()["scope"] == "repo"
    assert len(fake_tmux.calls_of("new-session")) == 2
    assert len(fake_tmux.calls_of("kill-session")) == 1


def test_a_replace_whose_old_session_will_not_stop_is_a_structured_500(
    client: TestClient, fake_tmux: FakeTmux
) -> None:
    _start(client)
    fake_tmux.kill_leaves_session = True
    res = _start(client, scope="repo", target="o/r", replace=True)
    assert res.status_code == 500
    assert "could not be confirmed stopped" in res.json()["detail"]
    assert len(fake_tmux.calls_of("new-session")) == 1


def test_start_reports_unavailability_as_503_with_the_reason(
    client: TestClient, fake_tmux: FakeTmux, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        operator_session,
        "availability",
        lambda: Unavailable(reason="detached_mode_off", detail="Detached mode is off."),
    )
    res = _start(client)
    assert res.status_code == 503
    assert res.json() == {
        "detail": {"reason": "detached_mode_off", "detail": "Detached mode is off."}
    }
    assert fake_tmux.calls_of("new-session") == []


def test_a_replace_that_fails_after_the_old_session_ended_leaves_none_and_announces_it(
    client: TestClient,
    fake_tmux: FakeTmux,
    fake_git: FakeGit,
    events: list[TeamEvent],
) -> None:
    _start(client)
    events.clear()
    register_test_engine("late/arrival")
    fake_git.fail("clone", "late/arrival", git_reply(returncode=128, stderr="offline"))
    res = _start(client, scope="repo", target="late/arrival", replace=True)
    assert res.status_code == 503
    assert fake_tmux.live is False
    assert client.get(_STATUS).json()["session"] is None
    assert [e.kind for e in events] == ["operator_session_changed"]


def test_a_start_without_replace_that_fails_announces_nothing(
    client: TestClient, events: list[TeamEvent], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        operator_session,
        "availability",
        lambda: Unavailable(reason="tmux_missing", detail="tmux is not installed."),
    )
    assert _start(client).status_code == 503
    assert events == []


def test_start_reports_a_clone_failure_as_503(client: TestClient, fake_git: FakeGit) -> None:
    fake_git.fail("clone", "install-repo", git_reply(returncode=128, stderr="offline"))
    res = _start(client)
    assert res.status_code == 503
    assert res.json()["detail"] == {
        "reason": "checkout_clone_failed",
        "detail": "clone failed: offline",
    }


def test_start_of_a_repo_whose_first_clone_fails_is_503(
    client: TestClient, fake_git: FakeGit, fake_tmux: FakeTmux
) -> None:
    fake_git.fail("clone", "o/r", git_reply(returncode=128, stderr="offline"))
    res = _start(client, scope="repo", target="o/r")
    assert res.status_code == 503
    assert res.json()["detail"]["reason"] == "checkout_clone_failed"
    assert fake_tmux.calls_of("new-session") == []


def test_start_carries_a_checkout_update_warning_to_the_page(
    client: TestClient, fake_git: FakeGit, operator_state: Path
) -> None:
    (operator_state / "o" / "r" / ".git").mkdir(parents=True)
    fake_git.fail("fetch", "o/r", git_reply(stderr="offline"))
    warning = _start(client, scope="repo", target="o/r").json()["update_warning"]
    assert warning.startswith("checkout not updated (fetch failed)")
    assert client.get(_STATUS).json()["session"]["update_warning"] == warning


def test_a_global_start_that_cannot_clone_another_repo_still_starts_with_a_warning(
    client: TestClient, fake_git: FakeGit, fake_tmux: FakeTmux
) -> None:
    fake_git.fail("clone", "some-owner/some-repo", git_reply(returncode=128, stderr="gone"))
    res = _start(client)
    assert res.status_code == 200
    assert res.json()["update_warning"] == "some-owner/some-repo: clone failed: gone"
    assert fake_tmux.live is True


def test_start_of_a_repo_that_is_not_connected_is_404(
    client: TestClient, fake_tmux: FakeTmux, fake_git: FakeGit
) -> None:
    res = _start(client, scope="repo", target="stranger/unknown")
    assert res.status_code == 404
    assert "stranger/unknown" in res.json()["detail"]
    assert fake_tmux.calls == []
    assert fake_git.calls == []


def test_start_of_a_hidden_repo_is_404(
    client: TestClient, fake_tmux: FakeTmux, fake_git: FakeGit
) -> None:
    register_test_engine("quiet/repo", hidden=True)
    res = _start(client, scope="repo", target="quiet/repo")
    assert res.status_code == 404
    assert fake_tmux.calls == []
    assert fake_git.calls == []


def test_start_of_a_workspace_whose_repository_is_not_connected_is_404(
    client: TestClient, fake_tmux: FakeTmux, ws_root: Path
) -> None:
    workspace = create("Orphan", "d", root=ws_root, engine_key="stranger/unknown")
    res = _start(client, scope="workspace", target=workspace.task_id)
    assert res.status_code == 404
    assert fake_tmux.calls == []


def test_start_reports_a_tmux_failure_as_500(client: TestClient, fake_tmux: FakeTmux) -> None:
    fake_tmux.new_session_rc = 1
    fake_tmux.new_session_stderr = "boom"
    res = _start(client)
    assert res.status_code == 500
    assert "boom" in res.json()["detail"]


@pytest.mark.parametrize(
    "body",
    [
        {"scope": "everything"},
        {"scope": "repo"},
        {"scope": "repo", "target": "no-slash"},
        {"scope": "workspace", "target": "../up"},
        {"scope": "fleet", "target": "x"},
    ],
)
def test_start_refuses_an_invalid_scope_or_target_with_422(
    client: TestClient, fake_tmux: FakeTmux, body: dict[str, Any]
) -> None:
    res = client.post(_SESSION, json=body, headers=_headers())
    assert res.status_code == 422
    assert fake_tmux.calls == []


def test_start_refuses_an_oversized_target_with_422(client: TestClient) -> None:
    res = _start(client, scope="workspace", target="x" * 201)
    assert res.status_code == 422


def test_start_of_an_unknown_workspace_is_404(client: TestClient, fake_tmux: FakeTmux) -> None:
    res = _start(client, scope="workspace", target="no-such-workspace")
    assert res.status_code == 404
    assert "no-such-workspace" in res.json()["detail"]
    assert fake_tmux.calls == []


def test_a_workspace_scoped_start_records_the_intervention_on_that_workspace(
    client: TestClient, ws_root: Path
) -> None:
    workspace: Workspace = create("Stuck thing", "d", root=ws_root)
    res = _start(client, scope="workspace", target=workspace.task_id)
    assert res.status_code == 200
    actions = [i.action for i in read(workspace.task_id, root=ws_root).meta.interventions]
    assert actions == [InterventionAction.OPERATOR_TERMINAL]


def test_end_stops_the_session_and_announces_it(
    client: TestClient, fake_tmux: FakeTmux, events: list[TeamEvent]
) -> None:
    _start(client)
    events.clear()
    res = client.delete(_SESSION, headers=_headers())
    assert res.status_code == 204
    assert fake_tmux.live is False
    assert [e.kind for e in events] == ["operator_session_changed"]
    assert client.get(_STATUS).json()["session"] is None


def test_end_archives_a_workspace_scoped_transcript(
    client: TestClient, fake_tmux: FakeTmux, ws_root: Path
) -> None:
    workspace = create("Stuck thing", "d", root=ws_root)
    _start(client, scope="workspace", target=workspace.task_id)
    fake_tmux.capture = "what happened\n"
    assert client.delete(_SESSION, headers=_headers()).status_code == 204
    archived = list(workspace.path.glob("operator_transcript-*.txt"))
    assert [p.read_text() for p in archived] == ["what happened\n"]


def test_end_with_no_session_is_404_and_announces_nothing(
    client: TestClient, events: list[TeamEvent]
) -> None:
    res = client.delete(_SESSION, headers=_headers())
    assert res.status_code == 404
    assert res.json() == {"detail": "no operator session"}
    assert events == []


def test_end_of_a_session_that_will_not_stop_is_500(
    client: TestClient, fake_tmux: FakeTmux
) -> None:
    _start(client)
    fake_tmux.kill_leaves_session = True
    res = client.delete(_SESSION, headers=_headers())
    assert res.status_code == 500
    assert "could not be confirmed stopped" in res.json()["detail"]


def test_an_attach_ticket_is_single_use_and_valid_for_thirty_seconds(
    client: TestClient,
) -> None:
    _start(client)
    body = client.post(_TICKET, headers=_headers()).json()
    assert body["expires_in_s"] == 30.0
    assert operator_ticket.consume(body["ticket"]) is True
    assert operator_ticket.consume(body["ticket"]) is False


def test_each_attach_ticket_request_gets_a_fresh_ticket(client: TestClient) -> None:
    _start(client)
    first = client.post(_TICKET, headers=_headers()).json()["ticket"]
    second = client.post(_TICKET, headers=_headers()).json()["ticket"]
    assert first != second


def test_an_attach_ticket_needs_a_live_session(client: TestClient) -> None:
    res = client.post(_TICKET, headers=_headers())
    assert res.status_code == 404
    assert res.json() == {"detail": "no operator session"}
