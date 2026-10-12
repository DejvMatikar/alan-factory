from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from alan import control_plane, server
from alan import driver_loop_pause as pause_mod
from alan import driver_loop_runs as runs_mod
from alan import driver_loop_status as status_mod
from alan.detached_mode import DIAL_ENV
from alan.driver_loop_registry import DriverLoopEntry
from alan.driver_loop_runs import RunOutcome, RunRecord
from alan.teams._base import TeamEvent


def _entry(**overrides: object) -> DriverLoopEntry:
    fields: dict[str, object] = {
        "name": "alan-issue-driver",
        "command": "/alan-issue-driver",
        "cwd": "/opt/checkouts/alan-issue-driver",
        "self_paced": True,
        "timeout_seconds": 3600,
        "enabled": True,
    }
    fields.update(overrides)
    return DriverLoopEntry.model_validate(fields)


@pytest.fixture(autouse=True)
def _state_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "state"
    monkeypatch.setenv("ALAN_STATE_ROOT", str(root))
    return root


@pytest.fixture(autouse=True)
def _driver_loop_state_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "driver-loop-state"
    monkeypatch.setenv("ALAN_DRIVER_LOOP_STATE_ROOT", str(root))
    return root


@pytest.fixture(autouse=True)
def _not_supervised_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DIAL_ENV, "off")


@pytest.fixture(autouse=True)
def _empty_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(status_mod, "load_registry", lambda: ([], []))


@pytest.fixture
def engines_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "engines"
    monkeypatch.setenv("ALAN_ENGINES_ROOT", str(root))
    return root


@pytest.fixture
def secrets_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "secrets"
    monkeypatch.setenv("ALAN_SECRETS_STORE_ROOT", str(root))
    return root


@pytest.fixture
def workspaces_root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


@pytest.fixture
def build_chat_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "build-chat"
    monkeypatch.setenv("ALAN_BUILD_CHAT_SESSIONS_ROOT", str(root))
    return root


@pytest.fixture
def explore_chat_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "explore-chat"
    monkeypatch.setenv("ALAN_EXPLORE_CHAT_SESSIONS_ROOT", str(root))
    return root


@pytest.fixture
def client(
    engines_root: Path,
    secrets_root: Path,
    workspaces_root: Path,
    build_chat_root: Path,
    explore_chat_root: Path,
) -> Iterator[TestClient]:
    _ = engines_root, secrets_root, build_chat_root, explore_chat_root
    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: workspaces_root  # noqa: SLF001
    try:
        with TestClient(server.app) as c:
            yield c
    finally:
        server.app.dependency_overrides.pop(server._workspaces_root_dep, None)  # noqa: SLF001


def _control_headers() -> dict[str, str]:
    return {control_plane.CONTROL_TOKEN_HEADER: os.environ[control_plane.CONTROL_TOKEN_ENV]}


def test_list_driver_loops_requires_no_control_token(client: TestClient) -> None:
    res = client.get("/api/driver-loops")
    assert res.status_code == 200


def test_list_driver_loops_reports_not_supervised(client: TestClient) -> None:
    res = client.get("/api/driver-loops")
    assert res.json()["supervised"] is False


def test_list_driver_loops_reports_supervised(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DIAL_ENV, "on")
    res = client.get("/api/driver-loops")
    assert res.json()["supervised"] is True


def test_list_driver_loops_serializes_entries(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(status_mod, "load_registry", lambda: ([_entry(name="a")], []))
    res = client.get("/api/driver-loops")
    body = res.json()
    assert [loop["name"] for loop in body["loops"]] == ["a"]
    loop = body["loops"][0]
    assert loop["enabled"] is True
    assert loop["dedicated_clone"] is False
    assert loop["paused"] is False
    assert loop["last_outcome"] is None
    assert loop["final_message"] == ""
    assert loop["denial_count"] == 0
    assert loop["run_now_pending"] is False
    assert "decision" in loop
    assert loop["decision"]["action"] in {"hold", "idle", "spawn"}


def test_list_driver_loops_serializes_run_outcome(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(status_mod, "load_registry", lambda: ([_entry(name="a")], []))
    monkeypatch.setattr(
        runs_mod,
        "list_runs",
        lambda name: [
            RunRecord(
                run_id="1",
                loop_name=name,
                started_at=1.0,
                deadline_at=2.0,
                stdout_log="x",
                stderr_log="y",
                ended_at=2.0,
                outcome=RunOutcome.RAN_WITH_DENIALS,
                denial_count=3,
                final_message="done",
            )
        ],
    )
    res = client.get("/api/driver-loops")
    loop = res.json()["loops"][0]
    assert loop["last_outcome"] == "ran_with_denials"
    assert loop["denial_count"] == 3
    assert loop["final_message"] == "done"


def test_list_driver_loops_response_carries_no_secrets_or_log_paths(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(status_mod, "load_registry", lambda: ([_entry(name="a")], []))
    monkeypatch.setattr(
        runs_mod,
        "list_runs",
        lambda name: [
            RunRecord(
                run_id="1",
                loop_name=name,
                started_at=1.0,
                deadline_at=2.0,
                stdout_log="/secret/path/stdout.log",
                stderr_log="/secret/path/stderr.log",
                ended_at=2.0,
                outcome=RunOutcome.SUCCEEDED,
                pid=1234,
                cmdline="claude -p /alan-issue-driver",
            )
        ],
    )
    res = client.get("/api/driver-loops")
    body_text = res.text
    assert "stdout.log" not in body_text
    assert "stderr.log" not in body_text
    assert "cmdline" not in body_text
    assert "1234" not in body_text


def test_list_driver_loops_redacts_credential_bearing_url_in_detail(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DIAL_ENV, "on")
    monkeypatch.setattr(status_mod, "load_registry", lambda: ([_entry(name="a")], []))
    status_mod.record_fast_forward_failure(
        "a",
        "git fetch failed: fatal: unable to access "
        "'https://x-access-token:ghp_supersecrettoken@github.com/org/repo.git/': "
        "The requested URL returned error: 403",
    )
    res = client.get("/api/driver-loops")
    body_text = res.text
    assert "ghp_supersecrettoken" not in body_text
    assert "x-access-token" not in body_text
    assert "github.com" not in body_text
    loop = res.json()["loops"][0]
    assert loop["decision"]["reason"] == "fast_forward_failed"
    assert "[redacted-url]" in loop["decision"]["detail"]
    assert "The requested URL returned error: 403" in loop["decision"]["detail"]


def test_pause_requires_control_token(client: TestClient) -> None:
    res = client.post("/api/driver-loops/alan-issue-driver/pause")
    assert res.status_code == 401


def test_pause_wrong_token_403(client: TestClient) -> None:
    res = client.post(
        "/api/driver-loops/alan-issue-driver/pause",
        headers={control_plane.CONTROL_TOKEN_HEADER: "wrong-token"},
    )
    assert res.status_code == 403


def test_pause_refuses_with_409_when_not_supervised(client: TestClient) -> None:
    res = client.post(
        "/api/driver-loops/alan-issue-driver/pause", headers=_control_headers()
    )
    assert res.status_code == 409
    assert res.json()["detail"] == "not_supervised"
    assert pause_mod.is_paused("alan-issue-driver") is False


def test_pause_sets_pause_file_and_returns_ack(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DIAL_ENV, "on")
    res = client.post(
        "/api/driver-loops/alan-issue-driver/pause", headers=_control_headers()
    )
    assert res.status_code == 200
    assert res.json() == {"name": "alan-issue-driver", "paused": True}
    assert pause_mod.is_paused("alan-issue-driver") is True


def test_pause_works_for_disabled_entry(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DIAL_ENV, "on")
    monkeypatch.setattr(
        status_mod, "load_registry", lambda: ([_entry(name="nightly-driver", enabled=False)], [])
    )
    res = client.post("/api/driver-loops/nightly-driver/pause", headers=_control_headers())
    assert res.status_code == 200
    assert pause_mod.is_paused("nightly-driver") is True


def test_resume_refuses_with_409_when_not_supervised(client: TestClient) -> None:
    res = client.post(
        "/api/driver-loops/alan-issue-driver/resume", headers=_control_headers()
    )
    assert res.status_code == 409
    assert res.json()["detail"] == "not_supervised"


def test_resume_removes_pause_file(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DIAL_ENV, "on")
    pause_mod.pause("alan-issue-driver")
    res = client.post(
        "/api/driver-loops/alan-issue-driver/resume", headers=_control_headers()
    )
    assert res.status_code == 200
    assert res.json() == {"name": "alan-issue-driver", "paused": False}
    assert pause_mod.is_paused("alan-issue-driver") is False


def test_pause_requires_control_token_even_when_supervised(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DIAL_ENV, "on")
    res = client.post("/api/driver-loops/alan-issue-driver/pause")
    assert res.status_code == 401
    assert pause_mod.is_paused("alan-issue-driver") is False


def test_pause_publishes_driver_loop_changed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DIAL_ENV, "on")
    events: list[TeamEvent] = []
    unsubscribe = server.app.state.pubsub.subscribe_all(events.append)
    try:
        client.post("/api/driver-loops/alan-issue-driver/pause", headers=_control_headers())
    finally:
        unsubscribe()
    assert any(e.kind == "driver_loop_changed" for e in events)


def test_run_now_requires_control_token(client: TestClient) -> None:
    res = client.post("/api/driver-loops/alan-issue-driver/run-now")
    assert res.status_code == 401


def test_run_now_unknown_loop_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DIAL_ENV, "on")
    res = client.post("/api/driver-loops/no-such-loop/run-now", headers=_control_headers())
    assert res.status_code == 404
    assert res.json()["detail"] == "unknown_loop"


def test_run_now_refused_when_disabled_409(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DIAL_ENV, "on")
    monkeypatch.setattr(
        status_mod, "load_registry", lambda: ([_entry(name="a", enabled=False)], [])
    )
    res = client.post("/api/driver-loops/a/run-now", headers=_control_headers())
    assert res.status_code == 409
    assert res.json()["detail"] == "disabled"


def test_run_now_refused_when_running_409(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DIAL_ENV, "on")
    monkeypatch.setattr(status_mod, "load_registry", lambda: ([_entry(name="a")], []))
    monkeypatch.setattr(
        runs_mod,
        "list_runs",
        lambda name: [
            RunRecord(
                run_id="1", loop_name=name, started_at=1.0, deadline_at=99999.0,
                stdout_log="x", stderr_log="y",
            )
        ],
    )
    res = client.post("/api/driver-loops/a/run-now", headers=_control_headers())
    assert res.status_code == 409
    assert res.json()["detail"] == "running"


def test_run_now_refused_when_paused_409(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DIAL_ENV, "on")
    monkeypatch.setattr(status_mod, "load_registry", lambda: ([_entry(name="a")], []))
    pause_mod.pause("a")
    res = client.post("/api/driver-loops/a/run-now", headers=_control_headers())
    assert res.status_code == 409
    assert res.json()["detail"] == "paused"


def test_run_now_refused_when_not_supervised_409(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(status_mod, "load_registry", lambda: ([_entry(name="a")], []))
    res = client.post("/api/driver-loops/a/run-now", headers=_control_headers())
    assert res.status_code == 409
    assert res.json()["detail"] == "not_supervised"


def test_run_now_succeeds_and_returns_refreshed_status(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DIAL_ENV, "on")
    monkeypatch.setattr(status_mod, "load_registry", lambda: ([_entry(name="a")], []))
    res = client.post("/api/driver-loops/a/run-now", headers=_control_headers())
    assert res.status_code == 200
    body = res.json()
    assert body["name"] == "a"
    assert body["run_now_pending"] is True
    status_mod.clear_run_now_request("a")


def test_run_now_404_when_entry_vanishes_between_accept_and_refresh(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DIAL_ENV, "on")
    calls = {"n": 0}

    def _load_registry() -> tuple[list[DriverLoopEntry], list[object]]:
        calls["n"] += 1
        if calls["n"] == 1:
            return [_entry(name="a")], []
        return [], []

    monkeypatch.setattr(status_mod, "load_registry", _load_registry)
    res = client.post("/api/driver-loops/a/run-now", headers=_control_headers())
    assert res.status_code == 404
    assert res.json()["detail"] == "unknown_loop"
    assert status_mod.run_now_pending("a") is False


def test_run_now_publishes_driver_loop_changed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DIAL_ENV, "on")
    monkeypatch.setattr(status_mod, "load_registry", lambda: ([_entry(name="a")], []))
    events: list[TeamEvent] = []
    unsubscribe = server.app.state.pubsub.subscribe_all(events.append)
    try:
        client.post("/api/driver-loops/a/run-now", headers=_control_headers())
    finally:
        unsubscribe()
        status_mod.clear_run_now_request("a")
    assert any(e.kind == "driver_loop_changed" for e in events)
