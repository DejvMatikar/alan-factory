from __future__ import annotations

import contextlib
import subprocess
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient

from alan import forge as forge_mod
from alan import server
from alan.dispatcher import PubSub
from alan.engine_config import EngineConfig, ForgeConfig, ForgeTarget, Posture, RepoIdentity
from alan.forge import PrRead
from alan.handover import Decision, Handover, save_handover
from alan.queue import Queue
from alan.workspace import (
    EngineSnapshot,
    Status,
    Workspace,
    create,
    stamp_engine_snapshot,
    transition,
)
from tests.fakes.forge import FakeForge
from tests.forge_stubs import ForgeScript, pr_read


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
def pubsub() -> PubSub:
    return PubSub()


@pytest.fixture
def client(ws_root: Path, queue: Queue, pubsub: PubSub) -> Iterator[TestClient]:
    server.app.dependency_overrides[server._queue_dep] = lambda: queue
    server.app.dependency_overrides[server._pubsub_dep] = lambda: pubsub
    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: ws_root
    try:
        with TestClient(server.app) as c:
            yield c
    finally:
        server.app.dependency_overrides.clear()


@pytest.fixture
def forge(client: TestClient, _active_fake_forge: FakeForge) -> ForgeScript:
    forge_mod.set_active_forge(_active_fake_forge)
    return ForgeScript(_active_fake_forge)


def _completed_workspace(ws_root: Path, title: str = "delivery-ws") -> Workspace:
    ws = create(title, "desc", root=ws_root)
    return transition(ws, Status.COMPLETED)


def _handover_with_pr(ws: Workspace, pr_number: int) -> Handover:
    return Handover(
        handover_id=f"handover_{ws.task_id}_001",
        handover_type="forward",
        task_id=ws.task_id,
        from_team="verification",
        from_invocation_id="inv_001",
        to_team_suggestions=["pr_merge"],
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        decisions=[
            Decision(
                id="d1",
                statement="approve",
                rationale="ok",
                confidence=1.0,
                reversibility="low",
            )
        ],
        full_synthesis="VERDICT: APPROVE",
        pr_number=pr_number,
    )


def _handover_no_pr(ws: Workspace) -> Handover:
    return Handover(
        handover_id=f"handover_{ws.task_id}_001",
        handover_type="forward",
        task_id=ws.task_id,
        from_team="verification",
        from_invocation_id="inv_001",
        to_team_suggestions=["pr_merge"],
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        decisions=[
            Decision(
                id="d1",
                statement="approve",
                rationale="ok",
                confidence=1.0,
                reversibility="low",
            )
        ],
        full_synthesis="VERDICT: APPROVE",
        pr_number=None,
    )


def _gh_success_output(pr_number: int = 42) -> PrRead:
    return pr_read({
        "title": "Add delivery endpoint",
        "mergedAt": "2026-05-20T10:00:00Z",
        "url": f"https://github.com/owner/repo/pull/{pr_number}",
        "files": [
            {"path": "alan/delivery.py", "additions": 90, "deletions": 0},
            {"path": "alan/server.py", "additions": 20, "deletions": 0},
        ],
        "commits": [
            {"oid": "abc123", "messageHeadline": "feat: add delivery endpoint"}
        ],
    })


class TestGetWorkspaceDelivery:
    def test_404_unknown_task_id(self, client: TestClient) -> None:
        res = client.get("/api/workspaces/no-such-task/delivery")
        assert res.status_code == 404

    def test_409_when_status_not_completed(
        self, client: TestClient, ws_root: Path
    ) -> None:
        ws = create("active-ws", "desc", root=ws_root)
        res = client.get(f"/api/workspaces/{ws.task_id}/delivery")
        assert res.status_code == 409
        assert "not completed" in res.json()["detail"]

    def test_409_when_no_pr_bearing_handover(
        self, client: TestClient, ws_root: Path
    ) -> None:
        ws = _completed_workspace(ws_root)
        save_handover(ws, _handover_no_pr(ws))
        res = client.get(f"/api/workspaces/{ws.task_id}/delivery")
        assert res.status_code == 409
        assert "no PR" in res.json()["detail"]

    def test_200_happy_path(
        self,
        client: TestClient,
        ws_root: Path,
        forge: ForgeScript,
    ) -> None:
        ws = _completed_workspace(ws_root)
        save_handover(ws, _handover_with_pr(ws, 42))
        forge.queue("read_pr", _gh_success_output(42))

        res = client.get(f"/api/workspaces/{ws.task_id}/delivery")
        assert res.status_code == 200
        body = res.json()
        assert body["pr_number"] == 42
        assert body["repo"] == "DejvMatikar/alan"
        assert body["pr_title"] == "Add delivery endpoint"
        assert body["pr_url"] == "https://github.com/owner/repo/pull/42"
        assert body["merged_at"] is not None
        assert body["files_changed"] == ["alan/delivery.py", "alan/server.py"]
        assert body["commits"] == [
            {"sha": "abc123", "message": "feat: add delivery endpoint"}
        ]

    def test_200_gh_binary_missing(
        self,
        client: TestClient,
        ws_root: Path,
        forge: ForgeScript,
    ) -> None:
        ws = _completed_workspace(ws_root)
        save_handover(ws, _handover_with_pr(ws, 99))
        forge.queue("read_pr", FileNotFoundError("gh not found"))

        res = client.get(f"/api/workspaces/{ws.task_id}/delivery")
        assert res.status_code == 200
        body = res.json()
        assert body["pr_number"] == 99
        assert body["pr_url"] is None
        assert body["pr_title"] is None
        assert body["merged_at"] is None
        assert body["files_changed"] == []
        assert body["commits"] == []

    def test_200_gh_nonzero_rc(
        self,
        client: TestClient,
        ws_root: Path,
        forge: ForgeScript,
    ) -> None:
        ws = _completed_workspace(ws_root)
        save_handover(ws, _handover_with_pr(ws, 7))
        forge.queue("read_pr", pr_read(returncode=1))

        res = client.get(f"/api/workspaces/{ws.task_id}/delivery")
        assert res.status_code == 200
        body = res.json()
        assert body["pr_number"] == 7
        assert body["pr_url"] is None
        assert body["pr_title"] is None
        assert body["merged_at"] is None
        assert body["files_changed"] == []
        assert body["commits"] == []

    def test_200_gh_null_optional_fields(
        self,
        client: TestClient,
        ws_root: Path,
        forge: ForgeScript,
    ) -> None:
        ws = _completed_workspace(ws_root)
        save_handover(ws, _handover_with_pr(ws, 11))
        forge.queue("read_pr", pr_read(
            {"title": "T", "url": None, "mergedAt": None, "files": None, "commits": None}
        ))

        res = client.get(f"/api/workspaces/{ws.task_id}/delivery")
        assert res.status_code == 200
        body = res.json()
        assert body["pr_number"] == 11
        assert body["merged_at"] is None
        assert body["files_changed"] == []
        assert body["commits"] == []

    def test_200_gh_bad_date_and_malformed_items(
        self,
        client: TestClient,
        ws_root: Path,
        forge: ForgeScript,
    ) -> None:
        ws = _completed_workspace(ws_root)
        save_handover(ws, _handover_with_pr(ws, 13))
        forge.queue("read_pr", pr_read({
            "mergedAt": "not-a-date",
            "files": [1, {"path": None}, {"path": "ok.py"}],
            "commits": [
                None,
                {"oid": 99, "messageHeadline": "x"},
                {"oid": "a1b", "messageHeadline": "ok"},
            ],
        }))

        res = client.get(f"/api/workspaces/{ws.task_id}/delivery")
        assert res.status_code == 200
        body = res.json()
        assert body["merged_at"] is None
        assert body["files_changed"] == ["ok.py"]
        assert body["commits"] == [{"sha": "a1b", "message": "ok"}]

    def test_200_gh_invalid_json(
        self,
        client: TestClient,
        ws_root: Path,
        forge: ForgeScript,
    ) -> None:
        ws = _completed_workspace(ws_root)
        save_handover(ws, _handover_with_pr(ws, 17))
        forge.queue("read_pr", pr_read(fields={}))

        res = client.get(f"/api/workspaces/{ws.task_id}/delivery")
        assert res.status_code == 200
        body = res.json()
        assert body["pr_number"] == 17
        assert body["pr_url"] is None
        assert body["files_changed"] == []

    def test_200_gh_json_array(
        self,
        client: TestClient,
        ws_root: Path,
        forge: ForgeScript,
    ) -> None:
        ws = _completed_workspace(ws_root)
        save_handover(ws, _handover_with_pr(ws, 19))
        forge.queue("read_pr", pr_read(fields={}))

        res = client.get(f"/api/workspaces/{ws.task_id}/delivery")
        assert res.status_code == 200
        body = res.json()
        assert body["pr_number"] == 19
        assert body["pr_url"] is None
        assert body["files_changed"] == []


def _sprocket_engine_snapshot() -> EngineSnapshot:
    config = EngineConfig(
        repo=RepoIdentity(owner="acme-corp", repo="sprocket"),
        forge=ForgeConfig(
            repo=RepoIdentity(owner="acme-corp", repo="sprocket"),
            credential_env_name="ALAN_VERIFIER_GH_TOKEN",  # noqa: S106  # nosec B106
        ),
    )
    return EngineSnapshot(posture=Posture.autonomous, transition_graph={}, config=config)


class TestGetWorkspaceDeliveryRepoScoped:
    def test_200_non_default_engine_resolves_pinned_repo(
        self,
        client: TestClient,
        ws_root: Path,
        forge: ForgeScript,
    ) -> None:
        ws = _completed_workspace(ws_root, title="sprocket-ws")
        ws = stamp_engine_snapshot(ws, _sprocket_engine_snapshot())
        save_handover(ws, _handover_with_pr(ws, 8))
        forge.queue("read_pr", pr_read({
            "title": "Add sprocket strategy",
            "mergedAt": "2026-05-20T10:00:00Z",
            "url": "https://github.com/acme-corp/sprocket/pull/8",
            "files": [],
            "commits": [],
        }))

        res = client.get(f"/api/workspaces/{ws.task_id}/delivery")
        assert res.status_code == 200
        body = res.json()
        assert body["repo"] == "acme-corp/sprocket"
        assert body["pr_url"] == "https://github.com/acme-corp/sprocket/pull/8"
        [(args, kwargs)] = forge.calls("read_pr")
        assert args[0] == 8
        target = cast(ForgeTarget, kwargs["forge_target"])
        assert target.repo.slug == "acme-corp/sprocket"
        assert kwargs["timeout"] == 60.0

    def test_200_default_engine_repo_unaffected_by_resolver(
        self,
        client: TestClient,
        ws_root: Path,
        forge: ForgeScript,
    ) -> None:
        ws = _completed_workspace(ws_root, title="default-ws")
        save_handover(ws, _handover_with_pr(ws, 21))
        forge.queue("read_pr", _gh_success_output(21))

        res = client.get(f"/api/workspaces/{ws.task_id}/delivery")
        assert res.status_code == 200
        body = res.json()
        assert body["repo"] == "DejvMatikar/alan"
        [(_args, kwargs)] = forge.calls("read_pr")
        target = cast(ForgeTarget, kwargs["forge_target"])
        assert target.repo.slug == "DejvMatikar/alan"

    def test_200_gh_timeout_degrades_to_empty(
        self,
        client: TestClient,
        ws_root: Path,
        forge: ForgeScript,
    ) -> None:
        ws = _completed_workspace(ws_root)
        save_handover(ws, _handover_with_pr(ws, 23))
        forge.queue("read_pr", subprocess.TimeoutExpired(cmd="gh", timeout=60.0))

        res = client.get(f"/api/workspaces/{ws.task_id}/delivery")
        assert res.status_code == 200
        body = res.json()
        assert body["pr_number"] == 23
        assert body["pr_url"] is None
        assert body["files_changed"] == []
