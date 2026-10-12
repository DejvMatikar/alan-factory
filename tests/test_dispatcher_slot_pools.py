from __future__ import annotations

import contextlib
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan import workspace as workspace_mod
from alan import worktree as worktree_mod
from alan.claude_cli import OnProcStarted
from alan.dispatcher import Dispatcher, NoPoolForEngineError
from alan.engine_config import EngineConfig, RepoIdentity
from alan.handover import Handover
from alan.queue import JobStatus, Queue
from alan.slot_pool import PoolBusyError, SlotPool, get_active_pool, set_active_pool
from alan.teams._base import TeamEvent
from alan.workflow import PendingTaskSpec
from alan.workspace import EngineSnapshot, Workspace


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


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
def _no_summary_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("alan.manager_summary.trigger_manager_summary", lambda *_a, **_kw: None)


@pytest.fixture(autouse=True)
def _clear_active_pool() -> Iterator[None]:
    set_active_pool(None)
    yield
    set_active_pool(None)


def _make_handover(task_id: str) -> Handover:
    return Handover(
        handover_id=f"h_{task_id}",
        handover_type="forward",
        task_id=task_id,
        from_team="research",
        from_invocation_id="inv_001",
        to_team_suggestions=["implementation_plan"],
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="ok",
    )


def _sprocket_config() -> EngineConfig:
    return EngineConfig(repo=RepoIdentity(owner="sprocket-co", repo="sprocket"))


def _default_config() -> EngineConfig:
    return EngineConfig(repo=RepoIdentity(owner="DejvMatikar", repo="alan"))


def _pin(ws: Workspace, config: EngineConfig) -> Workspace:
    snapshot = EngineSnapshot(posture=config.posture, transition_graph={}, config=config)
    return workspace_mod.stamp_engine_snapshot(ws, snapshot)


class _PoolCapturingTeam:
    name = "research"
    supports_resume = False

    def __init__(self) -> None:
        self.observed: list[SlotPool | None] = []

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
        inv_id: str = "inv_001",  # noqa: ARG002
        resume_inv_id: str | None = None,  # noqa: ARG002
        instructions: str | None = None,  # noqa: ARG002
        on_proc_started: OnProcStarted | None = None,  # noqa: ARG002
        task_id: str | None = None,  # noqa: ARG002
    ) -> Handover:
        self.observed.append(get_active_pool())
        return _make_handover(ws.task_id)


def test_pool_for_workspace_default_engine_returns_default_pool(
    queue: Queue, root: Path, tmp_path: Path
) -> None:
    default_pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path))
    d = Dispatcher(queue, {}, workspaces_root=root, slot_pool=default_pool)
    ws = workspace_mod.create("default-engine-ws", "d", root=root)
    assert d._pool_for_workspace(ws) is default_pool  # noqa: SLF001


def test_pool_for_workspace_secondary_engine_returns_keyed_pool(
    queue: Queue, root: Path, tmp_path: Path
) -> None:
    default_pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path))
    sprocket_pool = SlotPool(root=tmp_path / "sprocket-slots", size=1, repo_root=str(tmp_path))
    d = Dispatcher(
        queue, {}, workspaces_root=root,
        slot_pool=default_pool,
        slot_pools_by_engine={"sprocket-co/sprocket": sprocket_pool},
    )
    ws = workspace_mod.create("sprocket-ws", "d", root=root)
    ws = _pin(ws, _sprocket_config())
    assert d._pool_for_workspace(ws) is sprocket_pool  # noqa: SLF001


def test_pool_for_workspace_secondary_engine_without_keyed_pool_raises(
    queue: Queue, root: Path, tmp_path: Path
) -> None:
    default_pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path))
    d = Dispatcher(queue, {}, workspaces_root=root, slot_pool=default_pool)
    ws = workspace_mod.create("unpooled-ws", "d", root=root)
    ws = _pin(ws, _sprocket_config())
    with pytest.raises(NoPoolForEngineError, match="sprocket-co/sprocket"):
        d._pool_for_workspace(ws)


def test_pool_for_workspace_disposed_engine_pool_raises(
    queue: Queue, root: Path, tmp_path: Path
) -> None:
    sprocket_pool = SlotPool(root=tmp_path / "sprocket-slots", size=1, repo_root=str(tmp_path))
    d = Dispatcher(
        queue, {}, workspaces_root=root,
        slot_pools_by_engine={"sprocket-co/sprocket": sprocket_pool},
    )
    ws = workspace_mod.create("disposed-ws", "d", root=root)
    ws = _pin(ws, _sprocket_config())
    assert d._pool_for_workspace(ws) is sprocket_pool

    d.dispose_pool("sprocket-co/sprocket")

    with pytest.raises(NoPoolForEngineError, match="sprocket-co/sprocket"):
        d._pool_for_workspace(ws)


def test_pool_for_workspace_no_pools_configured_returns_none(
    queue: Queue, root: Path
) -> None:
    d = Dispatcher(queue, {}, workspaces_root=root)
    ws = workspace_mod.create("no-pool-ws", "d", root=root)
    assert d._pool_for_workspace(ws) is None


def test_execute_sets_active_pool_matching_workspace_engine(
    queue: Queue, root: Path, tmp_path: Path
) -> None:
    default_pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path))
    team = _PoolCapturingTeam()
    d = Dispatcher(
        queue, {"research": team}, workspaces_root=root, slot_pool=default_pool
    )
    ws = workspace_mod.create("default-job-ws", "d", root=root)
    queue.enqueue(ws.task_id, "research")
    d.run_once()
    assert team.observed == [default_pool]


def test_execute_selects_different_pools_across_jobs_on_same_thread(
    queue: Queue, root: Path, tmp_path: Path
) -> None:
    default_pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path))
    sprocket_pool = SlotPool(root=tmp_path / "sprocket-slots", size=1, repo_root=str(tmp_path))
    team = _PoolCapturingTeam()
    d = Dispatcher(
        queue, {"research": team}, workspaces_root=root,
        slot_pool=default_pool,
        slot_pools_by_engine={"sprocket-co/sprocket": sprocket_pool},
    )

    default_ws = workspace_mod.create("mixed-default-ws", "d", root=root)
    sprocket_ws = workspace_mod.create("mixed-sprocket-ws", "d", root=root)
    sprocket_ws = _pin(sprocket_ws, _sprocket_config())

    queue.enqueue(default_ws.task_id, "research")
    d.run_once()
    queue.enqueue(sprocket_ws.task_id, "research")
    d.run_once()

    assert team.observed == [default_pool, sprocket_pool]


def test_execute_sets_active_cache_env_matching_workspace_engine(
    queue: Queue, root: Path, tmp_path: Path
) -> None:
    from alan import claude_cli as claude_cli_mod
    from alan.engine_config import default_engine_config

    default_pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path))
    observed: list[dict[str, str] | None] = []

    class _CacheEnvCapturingTeam:
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
            inv_id: str = "inv_001",  # noqa: ARG002
            resume_inv_id: str | None = None,  # noqa: ARG002
            instructions: str | None = None,  # noqa: ARG002
            on_proc_started: OnProcStarted | None = None,  # noqa: ARG002
            task_id: str | None = None,  # noqa: ARG002
        ) -> Handover:
            observed.append(claude_cli_mod.get_active_cache_env())
            return _make_handover(ws.task_id)

    team = _CacheEnvCapturingTeam()
    d = Dispatcher(queue, {"research": team}, workspaces_root=root, slot_pool=default_pool)
    ws = workspace_mod.create("cache-env-ws", "d", root=root)
    queue.enqueue(ws.task_id, "research")

    d.run_once()

    assert observed == [default_engine_config().cache_env]


def test_execute_selects_different_cache_envs_across_jobs_on_same_thread(
    queue: Queue, root: Path, tmp_path: Path
) -> None:
    from alan import claude_cli as claude_cli_mod
    from alan.engine_config import default_engine_config

    default_pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path))
    sprocket_pool = SlotPool(root=tmp_path / "sprocket-slots", size=1, repo_root=str(tmp_path))
    observed: list[dict[str, str] | None] = []

    class _CacheEnvCapturingTeam:
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
            inv_id: str = "inv_001",  # noqa: ARG002
            resume_inv_id: str | None = None,  # noqa: ARG002
            instructions: str | None = None,  # noqa: ARG002
            on_proc_started: OnProcStarted | None = None,  # noqa: ARG002
            task_id: str | None = None,  # noqa: ARG002
        ) -> Handover:
            observed.append(claude_cli_mod.get_active_cache_env())
            return _make_handover(ws.task_id)

    team = _CacheEnvCapturingTeam()
    d = Dispatcher(
        queue, {"research": team}, workspaces_root=root,
        slot_pool=default_pool,
        slot_pools_by_engine={"sprocket-co/sprocket": sprocket_pool},
    )
    default_ws = workspace_mod.create("cache-default-ws", "d", root=root)
    sprocket_config = EngineConfig(
        repo=RepoIdentity(owner="sprocket-co", repo="sprocket"),
        cache_env={"NUGET_PACKAGES": "nuget"},
    )
    sprocket_ws = workspace_mod.create("cache-sprocket-ws", "d", root=root)
    sprocket_ws = _pin(sprocket_ws, sprocket_config)

    queue.enqueue(default_ws.task_id, "research")
    d.run_once()
    queue.enqueue(sprocket_ws.task_id, "research")
    d.run_once()

    assert observed == [default_engine_config().cache_env, {"NUGET_PACKAGES": "nuget"}]


def test_slot_pools_by_engine_defaults_to_empty_dict(queue: Queue, root: Path) -> None:
    d = Dispatcher(queue, {}, workspaces_root=root)
    assert d._slot_pools_by_engine == {}  # noqa: SLF001


class _WorktreeAttemptingTeam:
    name = "research"
    supports_resume = False

    def __init__(self, checkout_root: Path) -> None:
        self.called = False
        self._checkout_root = checkout_root

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
        inv_id: str = "inv_001",  # noqa: ARG002
        resume_inv_id: str | None = None,  # noqa: ARG002
        instructions: str | None = None,  # noqa: ARG002
        on_proc_started: OnProcStarted | None = None,  # noqa: ARG002
        task_id: str | None = None,  # noqa: ARG002
    ) -> Handover:
        self.called = True
        with worktree_mod.implementation_worktree(
            ws.task_id, "inv_001", repo_root=self._checkout_root
        ):
            pass
        return _make_handover(ws.task_id)


def test_execute_refuses_secondary_engine_with_empty_pool_dict_and_never_creates_worktree(
    queue: Queue, root: Path, tmp_path: Path
) -> None:
    checkout_root = tmp_path / "alan-checkout"
    checkout_root.mkdir()
    team = _WorktreeAttemptingTeam(checkout_root)
    d = Dispatcher(queue, {"research": team}, workspaces_root=root)
    ws = workspace_mod.create("secondary-empty-dict-ws", "d", root=root)
    ws = _pin(ws, _sprocket_config())
    queue.enqueue(ws.task_id, "research")

    d.run_once()

    assert team.called is False
    assert not (checkout_root / worktree_mod.WORKTREES_DIR).exists()
    failed = queue.list_jobs_for_workspace(ws.task_id, status=JobStatus.FAILED)
    assert len(failed) == 1
    assert "sprocket-co/sprocket" in (failed[0].error or "")


def test_execute_default_engine_with_no_slot_pool_is_not_refused(
    queue: Queue, root: Path
) -> None:
    team = _PoolCapturingTeam()
    d = Dispatcher(queue, {"research": team}, workspaces_root=root)
    ws = workspace_mod.create("default-no-pool-ws", "d", root=root)
    queue.enqueue(ws.task_id, "research")

    d.run_once()

    assert team.observed == [None]
    failed = queue.list_jobs_for_workspace(ws.task_id, status=JobStatus.FAILED)
    assert failed == []


def test_register_pool_inserts_synchronously_and_warms_async(
    queue: Queue, root: Path, tmp_path: Path
) -> None:
    built: list[SlotPool] = []

    def factory(cfg: EngineConfig) -> SlotPool:
        pool = SlotPool(
            root=tmp_path / "sprocket-slots", size=2, repo_root=str(tmp_path)
        )
        built.append(pool)
        return pool

    d = Dispatcher(queue, {}, workspaces_root=root, pool_factory=factory)
    d.register_pool(_sprocket_config())

    ws = workspace_mod.create("register-pool-ws", "d", root=root)
    ws = _pin(ws, _sprocket_config())
    assert d._pool_for_workspace(ws) is built[0]

    deadline = time.monotonic() + 2.0
    while not built[0].warm and time.monotonic() < deadline:
        time.sleep(0.01)
    assert built[0].warm is True


def test_register_pool_publishes_pool_lifecycle_changed_synchronously(
    queue: Queue, root: Path, tmp_path: Path
) -> None:
    def factory(cfg: EngineConfig) -> SlotPool:
        return SlotPool(root=tmp_path / "sprocket-slots", size=1, repo_root=str(tmp_path))

    d = Dispatcher(queue, {}, workspaces_root=root, pool_factory=factory)
    events: list[TeamEvent] = []
    d.pubsub.subscribe_all(events.append)

    d.register_pool(_sprocket_config())

    assert events
    assert events[0].kind == "pool_lifecycle_changed"
    assert events[0].payload == {"slug": "sprocket-co/sprocket"}


def test_register_pool_noop_without_pool_factory(queue: Queue, root: Path) -> None:
    d = Dispatcher(queue, {}, workspaces_root=root)
    events: list[TeamEvent] = []
    d.pubsub.subscribe_all(events.append)

    d.register_pool(_sprocket_config())

    assert d._slot_pools_by_engine == {}  # noqa: SLF001
    assert events == []


def test_warm_pool_success_publishes_pool_lifecycle_changed(
    queue: Queue, root: Path, tmp_path: Path
) -> None:
    pool = SlotPool(root=tmp_path / "sprocket-slots", size=1, repo_root=str(tmp_path))
    d = Dispatcher(queue, {}, workspaces_root=root)
    events: list[TeamEvent] = []
    d.pubsub.subscribe_all(events.append)

    d._warm_pool(pool, "sprocket-co/sprocket")  # noqa: SLF001

    assert pool.warm is True
    assert [e.kind for e in events] == ["pool_lifecycle_changed"]
    assert events[0].payload == {"slug": "sprocket-co/sprocket"}


def test_warm_pool_swallows_and_logs_exception(
    queue: Queue, root: Path, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    pool = SlotPool(root=tmp_path / "sprocket-slots", size=1, repo_root=str(tmp_path))

    def _boom() -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(pool, "reset_all", _boom)
    d = Dispatcher(queue, {}, workspaces_root=root)
    events: list[TeamEvent] = []
    d.pubsub.subscribe_all(events.append)

    with caplog.at_level("ERROR", logger="alan.dispatcher"):
        d._warm_pool(pool, "sprocket-co/sprocket")  # noqa: SLF001

    assert any("warmup failed" in r.message for r in caplog.records)
    assert pool.warm is False
    assert [e.kind for e in events] == ["pool_lifecycle_changed"]
    assert events[0].payload == {"slug": "sprocket-co/sprocket"}


def test_dispose_pool_removes_entry_and_reclaims_disk(
    queue: Queue, root: Path, tmp_path: Path
) -> None:
    pool_root = tmp_path / "sprocket-slots"
    pool_root.mkdir()
    (pool_root / "slot_0").mkdir()
    sprocket_pool = SlotPool(root=pool_root, size=1, repo_root=str(tmp_path))
    d = Dispatcher(
        queue, {}, workspaces_root=root,
        slot_pools_by_engine={"sprocket-co/sprocket": sprocket_pool},
    )
    events: list[TeamEvent] = []
    d.pubsub.subscribe_all(events.append)

    d.dispose_pool("sprocket-co/sprocket")

    assert "sprocket-co/sprocket" not in d._slot_pools_by_engine  # noqa: SLF001
    assert not pool_root.exists()
    assert [e.kind for e in events] == ["pool_lifecycle_changed"]
    assert events[0].payload == {"slug": "sprocket-co/sprocket"}


def test_dispose_pool_raises_pool_busy_when_lease_active_and_registry_survives(
    queue: Queue, root: Path, tmp_path: Path
) -> None:
    sprocket_pool = SlotPool(root=tmp_path / "sprocket-slots", size=1, repo_root=str(tmp_path))
    idx, token = sprocket_pool._claim()  # noqa: SLF001
    d = Dispatcher(
        queue, {}, workspaces_root=root,
        slot_pools_by_engine={"sprocket-co/sprocket": sprocket_pool},
    )
    events: list[TeamEvent] = []
    d.pubsub.subscribe_all(events.append)

    with pytest.raises(PoolBusyError):
        d.dispose_pool("sprocket-co/sprocket")

    assert "sprocket-co/sprocket" in d._slot_pools_by_engine  # noqa: SLF001
    sprocket_pool._release(idx, token)  # noqa: SLF001
    assert events == []


def test_dispose_pool_noop_when_slug_absent(queue: Queue, root: Path) -> None:
    d = Dispatcher(queue, {}, workspaces_root=root)
    events: list[TeamEvent] = []
    d.pubsub.subscribe_all(events.append)

    d.dispose_pool("no-such/engine")

    assert events == []


def test_lease_acquire_release_does_not_emit_pool_lifecycle_changed(
    queue: Queue, root: Path, tmp_path: Path
) -> None:
    sprocket_pool = SlotPool(root=tmp_path / "sprocket-slots", size=1, repo_root=str(tmp_path))
    d = Dispatcher(
        queue, {}, workspaces_root=root,
        slot_pools_by_engine={"sprocket-co/sprocket": sprocket_pool},
    )
    events: list[TeamEvent] = []
    d.pubsub.subscribe_all(events.append)

    idx, token = sprocket_pool._claim()  # noqa: SLF001
    sprocket_pool._release(idx, token)  # noqa: SLF001

    assert events == []


def test_dispose_pool_ignores_stale_entry_replaced_by_concurrent_register(
    queue: Queue, root: Path, tmp_path: Path
) -> None:
    old_pool = SlotPool(root=tmp_path / "old-slots", size=1, repo_root=str(tmp_path))
    new_pool = SlotPool(root=tmp_path / "new-slots", size=1, repo_root=str(tmp_path))
    d = Dispatcher(
        queue, {}, workspaces_root=root,
        slot_pools_by_engine={"sprocket-co/sprocket": old_pool},
    )
    real_begin_dispose = old_pool.begin_dispose

    def _begin_dispose_then_race() -> None:
        real_begin_dispose()
        d._slot_pools_by_engine["sprocket-co/sprocket"] = new_pool  # noqa: SLF001

    old_pool.begin_dispose = _begin_dispose_then_race  # type: ignore[method-assign]

    d.dispose_pool("sprocket-co/sprocket")

    assert d._slot_pools_by_engine["sprocket-co/sprocket"] is new_pool  # noqa: SLF001


def test_pool_states_includes_keyed_and_default_pools(
    queue: Queue, root: Path, tmp_path: Path
) -> None:
    default_pool = SlotPool(root=tmp_path / "slots", size=1, repo_root=str(tmp_path))
    sprocket_pool = SlotPool(root=tmp_path / "sprocket-slots", size=2, repo_root=str(tmp_path))
    d = Dispatcher(
        queue, {}, workspaces_root=root,
        slot_pool=default_pool,
        slot_pools_by_engine={"sprocket-co/sprocket": sprocket_pool},
    )

    states = d.pool_states()

    assert set(states) == {"DejvMatikar/alan", "sprocket-co/sprocket"}
    assert states["sprocket-co/sprocket"].size == 2
    assert states["DejvMatikar/alan"].size == 1


def test_pool_states_empty_when_no_pools(queue: Queue, root: Path) -> None:
    d = Dispatcher(queue, {}, workspaces_root=root)
    assert d.pool_states() == {}
