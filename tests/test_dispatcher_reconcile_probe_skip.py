from __future__ import annotations

import contextlib
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from alan import workspace as workspace_mod
from alan.dispatcher import Dispatcher
from alan.engine_config import EngineConfig, ForgeConfig, Posture, RepoIdentity
from alan.handover import Handover, TestScenario
from alan.queue import Queue
from alan.workspace import (
    EngineSnapshot,
    Requirement,
    Task,
    TaskStatus,
    Workspace,
    stamp_engine_snapshot,
)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


@pytest.fixture
def queue(tmp_path: Path) -> Iterator[Queue]:
    q = Queue(db_path=tmp_path / "queue.db")
    try:
        yield q
    finally:
        with contextlib.suppress(Exception):
            q.close()


def _non_default_cfg() -> EngineConfig:
    return EngineConfig(
        repo=RepoIdentity(owner="acme", repo="widget"),
        forge=ForgeConfig(
            repo=RepoIdentity(owner="acme", repo="widget"),
            credential_env_name="ACME_WIDGET_GH_TOKEN",
        ),
    )


def _scenario(sid: str = "s1") -> TestScenario:
    return TestScenario(
        id=sid, description="d", given="g", when="uv run pytest tests/test_x.py", then="",
    )


def _non_default_ws(
    root: Path,
    *,
    tasks: list[Task] | None = None,
    requirements: list[Requirement] | None = None,
) -> Workspace:
    ws = workspace_mod.create(
        "reconcile-skip-ws", "d", root=root, tasks=tasks or [], requirements=requirements,
    )
    return stamp_engine_snapshot(
        ws,
        EngineSnapshot(
            posture=Posture.autonomous, transition_graph={}, config=_non_default_cfg()
        ),
    )


class TestRunBoundScenariosSkip:
    def test_non_default_forge_returns_empty_without_running(
        self, root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ws = _non_default_ws(root)
        d = Dispatcher(queue, {}, workspaces_root=root)
        spy = MagicMock(side_effect=AssertionError("must not run scenarios"))
        monkeypatch.setattr("alan._dispatcher_reconcile.run_scenarios", spy)
        result = d._run_bound_scenarios(ws, [_scenario()])  # noqa: SLF001
        assert result == []
        spy.assert_not_called()

    def test_non_default_forge_logs_named_skip_reason(
        self, root: Path, queue: Queue, caplog: pytest.LogCaptureFixture
    ) -> None:
        ws = _non_default_ws(root)
        d = Dispatcher(queue, {}, workspaces_root=root)
        with caplog.at_level("WARNING", logger="alan.dispatcher"):
            d._run_bound_scenarios(ws, [_scenario()])  # noqa: SLF001
        assert any("acme/widget" in r.message for r in caplog.records)

    def test_empty_scenarios_short_circuits_before_checkout_resolution(
        self, root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ws = _non_default_ws(root)
        d = Dispatcher(queue, {}, workspaces_root=root)
        spy = MagicMock(side_effect=AssertionError("must not resolve checkout"))
        monkeypatch.setattr("alan._dispatcher_selfheal._probe_checkout_for", spy)
        result = d._run_bound_scenarios(ws, [])  # noqa: SLF001
        assert result == []
        spy.assert_not_called()


class TestRequirementsStayPendingOnSkip:
    def test_handover_carried_scenarios_leave_requirement_pending(
        self, root: Path, queue: Queue
    ) -> None:
        ws = _non_default_ws(
            root,
            tasks=[
                Task(id="a", status=TaskStatus.COMPLETED, depends_on=[], scenarios=[_scenario()])
            ],
            requirements=[Requirement(text="r", test_ref="s1")],
        )
        d = Dispatcher(queue, {}, workspaces_root=root)
        handover = Handover(
            handover_id="h1", handover_type="forward", task_id=ws.task_id,
            from_team="implementation", from_invocation_id="inv1", iteration=1,
            timestamp_utc=datetime.now(UTC), full_synthesis="ok",
            test_scenarios=[_scenario()],
        )
        updated = d._maybe_evaluate_requirements(ws, handover)  # noqa: SLF001
        assert updated.meta.requirements is not None
        assert updated.meta.requirements[0].status == "PENDING"

    def test_out_of_band_scenarios_leave_requirement_pending(
        self, root: Path, queue: Queue
    ) -> None:
        ws = _non_default_ws(
            root,
            tasks=[
                Task(id="a", status=TaskStatus.COMPLETED, depends_on=[], scenarios=[_scenario()])
            ],
            requirements=[Requirement(text="r", test_ref="s1")],
        )
        d = Dispatcher(queue, {}, workspaces_root=root)
        results = d._run_oob_requirement_scenarios(ws)  # noqa: SLF001
        assert results == []
