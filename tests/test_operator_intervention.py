from __future__ import annotations

from pathlib import Path

import pytest

from alan import operator_launch, operator_session
from alan import workspace as workspace_mod
from alan.operator_prompt import Scope
from alan.workspace import InterventionAction, Workspace, create, read
from tests.conftest import FakeTmux, register_test_engine


@pytest.fixture(autouse=True)
def _connected_repo() -> None:
    register_test_engine("o/r", default=True)


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


@pytest.fixture
def workspace(ws_root: Path) -> Workspace:
    return create("Stuck thing", "description", root=ws_root)


@pytest.fixture(autouse=True)
def _available(operator_state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    del operator_state
    monkeypatch.setattr(operator_session, "availability", lambda: None)


def _actions(ws_root: Path, task_id: str) -> list[InterventionAction]:
    return [i.action for i in read(task_id, root=ws_root).meta.interventions]


def test_the_intervention_action_has_a_stable_wire_value() -> None:
    assert InterventionAction.OPERATOR_TERMINAL.value == "operator_terminal"
    assert InterventionAction("operator_terminal") is InterventionAction.OPERATOR_TERMINAL


def test_a_workspace_scoped_start_records_an_operator_intervention(
    fake_tmux: FakeTmux, ws_root: Path, workspace: Workspace
) -> None:
    assert _actions(ws_root, workspace.task_id) == []
    operator_session.start(Scope.WORKSPACE, workspace.task_id, workspaces_root=ws_root)
    assert fake_tmux.live is True
    assert _actions(ws_root, workspace.task_id) == [InterventionAction.OPERATOR_TERMINAL]


def test_the_intervention_is_recorded_only_after_the_spawn_succeeded(
    ws_root: Path, workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    order: list[str] = []
    real_record = workspace_mod.record_intervention

    def record(ws: Workspace, action: InterventionAction) -> Workspace:
        order.append("record")
        return real_record(ws, action)

    def spawn(plan: operator_launch.LaunchPlan) -> None:
        del plan
        order.append("spawn")

    monkeypatch.setattr(workspace_mod, "record_intervention", record)
    monkeypatch.setattr(operator_session, "_spawn", spawn)
    monkeypatch.setattr(operator_session, "_remember", lambda meta: None)
    operator_session.start(Scope.WORKSPACE, workspace.task_id, workspaces_root=ws_root)
    assert order == ["spawn", "record"]


def test_a_failed_spawn_records_no_intervention(
    fake_tmux: FakeTmux, ws_root: Path, workspace: Workspace
) -> None:
    fake_tmux.new_session_rc = 1
    with pytest.raises(operator_session.OperatorStartFailed):
        operator_session.start(Scope.WORKSPACE, workspace.task_id, workspaces_root=ws_root)
    assert _actions(ws_root, workspace.task_id) == []


def test_a_refused_start_records_no_intervention(
    fake_tmux: FakeTmux, ws_root: Path, workspace: Workspace
) -> None:
    operator_session.start(Scope.FLEET, None)
    with pytest.raises(operator_session.OperatorSessionActive):
        operator_session.start(Scope.WORKSPACE, workspace.task_id, workspaces_root=ws_root)
    assert fake_tmux.live is True
    assert _actions(ws_root, workspace.task_id) == []


@pytest.mark.parametrize(("scope", "target"), [(Scope.FLEET, None), (Scope.REPO, "o/r")])
def test_fleet_and_repo_scopes_record_no_intervention(
    ws_root: Path, workspace: Workspace, scope: Scope, target: str | None
) -> None:
    operator_session.start(scope, target, workspaces_root=ws_root)
    assert _actions(ws_root, workspace.task_id) == []


def test_an_intervention_that_cannot_be_recorded_does_not_stop_the_live_session(
    fake_tmux: FakeTmux,
    ws_root: Path,
    workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def refuse(ws: Workspace, action: InterventionAction) -> Workspace:
        raise OSError("read-only volume")

    monkeypatch.setattr(workspace_mod, "record_intervention", refuse)
    info = operator_session.start(Scope.WORKSPACE, workspace.task_id, workspaces_root=ws_root)
    assert info.target == workspace.task_id
    assert fake_tmux.live is True
    assert "operator intervention not recorded" in caplog.text


def test_each_workspace_session_start_adds_one_marker(
    ws_root: Path, workspace: Workspace
) -> None:
    operator_session.start(Scope.WORKSPACE, workspace.task_id, workspaces_root=ws_root)
    operator_session.start(
        Scope.WORKSPACE, workspace.task_id, replace=True, workspaces_root=ws_root
    )
    assert _actions(ws_root, workspace.task_id) == [
        InterventionAction.OPERATOR_TERMINAL,
        InterventionAction.OPERATOR_TERMINAL,
    ]
