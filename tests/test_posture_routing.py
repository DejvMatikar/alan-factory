from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from alan import server
from alan import workspace as workspace_mod
from alan.claude_cli import OnProcStarted
from alan.dispatcher import Dispatcher, PubSub, pinned_engine_config
from alan.engine_config import Posture, admitted_teams
from alan.handover import Handover
from alan.queue import Queue
from alan.router import TEAM_TRANSITION_POLICY, RouterDecision, select_next_team
from alan.teams import TEAMS
from alan.teams._base import Team, TeamEvent
from alan.workflow import PendingTaskSpec
from alan.workspace import EngineSnapshot, Workspace, stamp_engine_snapshot


def _make_handover(
    from_team: str,
    task_id: str = "t1",
    *,
    to_team_suggestions: list[str] | None = None,
    next_hop: str | None = None,
) -> Handover:
    return Handover(
        handover_id=f"ho_{from_team}",
        handover_type="forward",
        task_id=task_id,
        from_team=from_team,
        from_invocation_id="inv_001",
        to_team_suggestions=to_team_suggestions or [],
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        next_hop=next_hop,
        full_synthesis="x",
    )


def _ws_with_posture(root: Path, posture: Posture) -> Workspace:
    ws = workspace_mod.create("posture test", "d", root=root)
    snapshot = EngineSnapshot(
        posture=posture,
        transition_graph={k: list(v) for k, v in TEAM_TRANSITION_POLICY.items()},
    )
    return stamp_engine_snapshot(ws, snapshot)


def test_router_withheld_team_not_forward_target() -> None:
    teams_propose = {
        k: v for k, v in TEAMS.items() if k in admitted_teams(set(TEAMS), Posture.propose)
    }
    assert "verification" not in teams_propose
    h = _make_handover("implementation", to_team_suggestions=["verification"])
    d = select_next_team(h, teams_propose)
    assert d.kind == "await_human"


def test_router_withheld_team_not_next_hop() -> None:
    teams_self_merge = {
        k: v
        for k, v in TEAMS.items()
        if k in admitted_teams(set(TEAMS), Posture.self_merge)
    }
    assert "deploy_verify" not in teams_self_merge
    assert "implementation_fix" in teams_self_merge
    h = _make_handover(
        "pr_merge",
        to_team_suggestions=["implementation_fix"],
        next_hop="deploy_verify",
    )
    d = select_next_team(h, teams_self_merge)
    assert d.kind == "enqueue"
    assert d.team == "implementation_fix"


def test_router_withheld_blocking_resolver_falls_back_to_await_human() -> None:
    from alan.handover import OpenQuestion

    teams_propose = {
        k: v for k, v in TEAMS.items() if k in admitted_teams(set(TEAMS), Posture.propose)
    }
    question = OpenQuestion(
        id="q1",
        question="?",
        resolver_role="verification",
        blocking=True,
        prior_attempts=[],
    )
    h = Handover(
        handover_id="ho_impl",
        handover_type="forward",
        task_id="t1",
        from_team="implementation",
        from_invocation_id="inv_001",
        to_team_suggestions=[],
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="x",
        open_questions=[question],
    )
    d = select_next_team(h, teams_propose)
    assert d.kind == "await_human"


def test_router_admitted_team_reachable_at_propose_posture() -> None:
    teams_propose = {
        k: v for k, v in TEAMS.items() if k in admitted_teams(set(TEAMS), Posture.propose)
    }
    assert "implementation" in teams_propose
    h = _make_handover("decomposer", to_team_suggestions=["implementation"])
    d = select_next_team(h, teams_propose)
    assert d.kind == "enqueue"
    assert d.team == "implementation"


def test_router_autonomous_admitted_set_equals_full_teams() -> None:
    full = set(TEAMS)
    auto_admitted = admitted_teams(full, Posture.autonomous)
    assert auto_admitted == full


def test_router_pinned_policy_used_when_provided() -> None:
    custom_policy: dict[str, frozenset[str]] = {
        "research": frozenset({"brainstorming"}),
        "brainstorming": frozenset(),
    }
    h = _make_handover(
        "research",
        to_team_suggestions=["brainstorming"],
    )
    d = select_next_team(h, TEAMS, custom_policy)
    assert d.kind == "enqueue"
    assert d.team == "brainstorming"


def test_router_pinned_policy_unknown_from_team_escalates() -> None:
    custom_policy: dict[str, frozenset[str]] = {"alpha": frozenset({"beta"})}
    h = _make_handover("ghost", to_team_suggestions=["research"])
    d = select_next_team(h, TEAMS, custom_policy)
    assert d.kind == "escalate"
    assert d.reason is not None and "unknown from_team" in d.reason


class _NoopTeam:
    name = "research"
    supports_resume = False

    def plan_workflow(
        self, _ws: Workspace, _prior: Handover | None
    ) -> list[PendingTaskSpec]:
        return []

    def run(
        self,
        ws: Workspace,
        _prior: Handover | None,
        _emit: Callable[[TeamEvent], None],
        *,
        inv_id: str = "inv",  # noqa: ARG002
        resume_inv_id: str | None = None,  # noqa: ARG002
        instructions: str | None = None,  # noqa: ARG002
        on_proc_started: OnProcStarted | None = None,  # noqa: ARG002
        task_id: str | None = None,  # noqa: ARG002
    ) -> Handover:
        return _make_handover(self.name, ws.task_id)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "ws"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "queue.db"


@pytest.fixture
def queue(db_path: Path) -> Iterator[Queue]:
    q = Queue(db_path=db_path)
    try:
        yield q
    finally:
        with contextlib.suppress(Exception):
            q.close()


@pytest.fixture(autouse=True)
def _no_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("alan.manager_summary.trigger_manager_summary", lambda *_a, **_kw: None)


def _make_dispatcher(queue: Queue, teams: dict[str, Team], root: Path) -> Dispatcher:
    return Dispatcher(queue=queue, teams=teams, workspaces_root=root)


def test_effective_teams_propose_withholds_consequential(
    queue: Queue, root: Path
) -> None:
    ws = _ws_with_posture(root, Posture.propose)
    d = _make_dispatcher(queue, dict(TEAMS), root)
    effective = d._effective_teams(ws)  # pyright: ignore[reportPrivateUsage]
    assert "implementation" in effective
    assert "verification" not in effective
    assert "pr_merge" not in effective
    assert "triage" not in effective
    assert "deploy_verify" not in effective


def test_effective_teams_autonomous_admits_all(queue: Queue, root: Path) -> None:
    ws = _ws_with_posture(root, Posture.autonomous)
    d = _make_dispatcher(queue, dict(TEAMS), root)
    effective = d._effective_teams(ws)  # pyright: ignore[reportPrivateUsage]
    assert set(effective) == set(TEAMS)


def test_effective_teams_observe_withholds_all_mapped(queue: Queue, root: Path) -> None:
    ws = _ws_with_posture(root, Posture.observe)
    d = _make_dispatcher(queue, dict(TEAMS), root)
    effective = d._effective_teams(ws)  # pyright: ignore[reportPrivateUsage]
    assert not effective


def test_pinned_policy_returns_snapshot_graph(queue: Queue, root: Path) -> None:
    custom_graph: dict[str, list[str]] = {"alpha": ["beta"], "beta": []}
    snapshot = EngineSnapshot(posture=Posture.autonomous, transition_graph=custom_graph)
    ws = workspace_mod.create("custom graph", "d", root=root)
    ws = stamp_engine_snapshot(ws, snapshot)
    d = _make_dispatcher(queue, dict(TEAMS), root)
    policy = d._pinned_policy(ws)  # pyright: ignore[reportPrivateUsage]
    assert policy == {"alpha": frozenset({"beta"}), "beta": frozenset()}


def test_enforce_deploy_gate_skipped_when_withheld(queue: Queue, root: Path) -> None:

    ws = _ws_with_posture(root, Posture.self_merge)
    d = _make_dispatcher(queue, dict(TEAMS), root)
    ho = Handover(
        handover_id="ho_merge",
        handover_type="terminal",
        task_id="t1",
        from_team="pr_merge",
        from_invocation_id="inv",
        to_team_suggestions=[],
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="x",
        pr_number=42,
    )
    decision = RouterDecision(kind="done", reason="terminal handover")
    effective = d._effective_teams(ws)  # pyright: ignore[reportPrivateUsage]
    assert "deploy_verify" not in effective
    result = d._enforce_deploy_gate(ho, decision, effective)  # pyright: ignore[reportPrivateUsage]
    assert result.kind == "done"


def test_enforce_deploy_gate_fires_when_admitted(queue: Queue, root: Path) -> None:
    ws = _ws_with_posture(root, Posture.autonomous)
    d = _make_dispatcher(queue, dict(TEAMS), root)
    ho = Handover(
        handover_id="ho_merge",
        handover_type="terminal",
        task_id="t1",
        from_team="pr_merge",
        from_invocation_id="inv",
        to_team_suggestions=[],
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="x",
        pr_number=42,
    )
    decision = RouterDecision(kind="done", reason="terminal handover")
    effective = d._effective_teams(ws)  # pyright: ignore[reportPrivateUsage]
    assert "deploy_verify" in effective
    result = d._enforce_deploy_gate(ho, decision, effective)  # pyright: ignore[reportPrivateUsage]
    assert result.kind == "enqueue"
    assert result.team == "deploy_verify"
    assert result.eligible_at is not None


def test_pr_merge_remediation_await_human_when_impl_fix_withheld(
    queue: Queue, root: Path
) -> None:
    from alan.engine_config import DEFAULT_FORGE_TARGET
    from alan.merge_gate import MergeGateBlocked
    from alan.merge_recovery import MergeBlockClass
    from alan.teams._pr_ready import PrReadiness

    d = _make_dispatcher(queue, dict(TEAMS), root)
    ho = _make_handover("verification")
    decision = RouterDecision(kind="enqueue", team="pr_merge")
    readiness = PrReadiness(
        ok=False, merge_state_status="BLOCKED", review_decision="NONE",
        failed_reasons=("gate failed",),
    )
    ws = _ws_with_posture(root, Posture.autonomous)
    effective = {k: v for k, v in TEAMS.items() if k != "implementation_fix"}
    attempt = MergeGateBlocked(readiness, MergeBlockClass.GENUINE, None, DEFAULT_FORGE_TARGET)
    result = d._pr_merge_remediation(  # pyright: ignore[reportPrivateUsage]
        ws, ho, decision, attempt, effective,
    )
    assert result.kind == "await_human"


def test_pr_merge_remediation_enqueues_impl_fix_when_admitted(
    queue: Queue, root: Path
) -> None:
    from alan.engine_config import DEFAULT_FORGE_TARGET
    from alan.merge_gate import MergeGateBlocked
    from alan.merge_recovery import MergeBlockClass
    from alan.teams._pr_ready import PrReadiness

    ws = _ws_with_posture(root, Posture.autonomous)
    d = _make_dispatcher(queue, dict(TEAMS), root)
    ho = _make_handover("verification")
    decision = RouterDecision(kind="enqueue", team="pr_merge")
    readiness = PrReadiness(
        ok=False, merge_state_status="BLOCKED", review_decision="NONE",
        failed_reasons=("gate failed",),
    )
    effective = d._effective_teams(ws)  # pyright: ignore[reportPrivateUsage]
    assert "implementation_fix" in effective
    attempt = MergeGateBlocked(readiness, MergeBlockClass.GENUINE, None, DEFAULT_FORGE_TARGET)
    result = d._pr_merge_remediation(  # pyright: ignore[reportPrivateUsage]
        ws, ho, decision, attempt, effective,
    )
    assert result.kind == "enqueue"
    assert result.team == "implementation_fix"


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "ws"


@pytest.fixture
def route_queue(tmp_path: Path) -> Iterator[Queue]:
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
def client(
    ws_root: Path, route_queue: Queue, pubsub: PubSub
) -> Iterator[TestClient]:
    server.app.dependency_overrides[server._queue_dep] = lambda: route_queue
    server.app.dependency_overrides[server._pubsub_dep] = lambda: pubsub
    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: ws_root
    try:
        with TestClient(server.app, follow_redirects=True) as c:
            yield c
    finally:
        server.app.dependency_overrides.clear()


def test_dispatch_withheld_team_returns_422(
    client: TestClient, route_queue: Queue, ws_root: Path
) -> None:
    ws = _ws_with_posture(ws_root, Posture.propose)
    res = client.post(
        f"/api/workspaces/{ws.task_id}/tasks/{ws.task_id}/dispatch",
        json={"team": "verification"},
    )
    assert res.status_code == 422
    assert "verification" in res.json()["detail"]
    assert not route_queue.list_jobs()


def test_dispatch_withheld_team_does_not_enqueue(
    client: TestClient, route_queue: Queue, ws_root: Path
) -> None:
    ws = _ws_with_posture(ws_root, Posture.propose)
    client.post(
        f"/api/workspaces/{ws.task_id}/tasks/{ws.task_id}/dispatch",
        json={"team": "pr_merge"},
    )
    assert not route_queue.list_jobs()


def test_dispatch_admitted_team_at_propose_succeeds(
    client: TestClient, route_queue: Queue, ws_root: Path
) -> None:
    ws = _ws_with_posture(ws_root, Posture.propose)
    res = client.post(
        f"/api/workspaces/{ws.task_id}/tasks/{ws.task_id}/dispatch",
        json={"team": "research"},
    )
    assert res.status_code == 202
    assert route_queue.list_jobs()


def test_dispatch_admitted_team_at_autonomous_unchanged(
    client: TestClient, route_queue: Queue, ws_root: Path
) -> None:
    ws = _ws_with_posture(ws_root, Posture.autonomous)
    res = client.post(
        f"/api/workspaces/{ws.task_id}/tasks/{ws.task_id}/dispatch",
        json={"team": "pr_merge"},
    )
    assert res.status_code == 202
    assert route_queue.list_jobs()


def test_dispatch_autonomous_posture_all_manually_dispatchable_admitted(
    client: TestClient, route_queue: Queue, ws_root: Path
) -> None:
    from alan.teams import MANUALLY_DISPATCHABLE_TEAMS

    ws = _ws_with_posture(ws_root, Posture.autonomous)
    manually = list(MANUALLY_DISPATCHABLE_TEAMS)
    team = manually[0]
    res = client.post(
        f"/api/workspaces/{ws.task_id}/tasks/{ws.task_id}/dispatch",
        json={"team": team},
    )
    assert res.status_code == 202


def test_dispatch_withheld_triage_returns_422(
    client: TestClient, route_queue: Queue, ws_root: Path
) -> None:
    ws = _ws_with_posture(ws_root, Posture.self_merge)
    res = client.post(
        f"/api/workspaces/{ws.task_id}/tasks/{ws.task_id}/dispatch",
        json={"team": "maintenance"},
    )
    assert res.status_code == 422
    assert not route_queue.list_jobs()


def test_pinned_engine_config_no_snapshot_falls_back_to_default(
    root: Path,
) -> None:
    ws = workspace_mod.create("no snapshot", "d", root=root)
    assert ws.meta.engine_snapshot is None
    posture, _ = pinned_engine_config(ws)
    assert posture == Posture.autonomous
