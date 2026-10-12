from __future__ import annotations

import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from alan import worker_records
from alan import workflow as workflow_mod
from alan.slot_pool import Reservation, SlotPool, set_active_pool
from alan.teams._base import TeamEvent
from alan.teams._slot_affinity import (
    _ANCESTOR_CHECK_ERROR_RC,
    _ancestor_check_rc,
    _find_survivor_reservation,
    _git_ref,
    _resume_base_ref,
    leased_with_affinity,
)
from alan.workspace import Workspace, create
from alan.worktree import WorktreeHandle


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return create("affinity-task", "desc", root=tmp_path / "ws")


def _seed_graph(
    workspace: Workspace,
    *,
    slot_index: int | None = None,
    base_ref_sha: str | None = None,
) -> tuple[str, workflow_mod.WorkflowGraph]:
    inv_id = workflow_mod.seed_pending_workflow(
        workspace,
        team="implementation",
        tasks=[workflow_mod.PendingTaskSpec(id="impl", role="impl", model="sonnet")],
    )
    graph = workflow_mod.load_graph(workspace, inv_id)
    if slot_index is not None:
        graph.set_slot_index(slot_index)
    if base_ref_sha is not None:
        graph.set_base_ref_sha(base_ref_sha)
    workflow_mod.save_workflow(workspace, graph.doc)
    return inv_id, workflow_mod.load_graph(workspace, inv_id)


def _fake_factory(handle: WorktreeHandle, captured: dict[str, object]):  # type: ignore[no-untyped-def]
    @contextmanager
    def _factory(*args: object, **kwargs: object) -> Iterator[WorktreeHandle]:
        captured["args"] = args
        captured["kwargs"] = kwargs
        yield handle

    return _factory


def test_graph_none_takes_no_affinity_path(workspace: Workspace, tmp_path: Path) -> None:
    handle = WorktreeHandle(
        path=tmp_path / "slot", base_ref="origin/main", branch="teams/t-inv_001", slot_index=0
    )
    captured: dict[str, object] = {}
    with leased_with_affinity(
        workspace, None, "t1", "inv_001", lambda _e: None,
        worktree_factory=_fake_factory(handle, captured),
    ) as h:
        assert h is handle
    assert captured["kwargs"]["preferred_slot"] is None  # type: ignore[index]
    assert captured["kwargs"]["resume_base_ref"] is None  # type: ignore[index]


def test_graph_none_never_persists_even_with_slot_index(
    workspace: Workspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    handle = WorktreeHandle(
        path=tmp_path / "slot", base_ref="origin/main", branch="b", slot_index=3
    )
    saved: list[object] = []
    monkeypatch.setattr(workflow_mod, "save_workflow", lambda ws, doc: saved.append(doc))
    with leased_with_affinity(
        workspace, None, "t1", "inv_001", lambda _e: None,
        worktree_factory=_fake_factory(handle, {}),
    ):
        pass
    assert saved == []


def test_first_lease_persists_slot_index_and_base_ref(
    workspace: Workspace, tmp_path: Path
) -> None:
    inv_id, graph = _seed_graph(workspace)
    handle = WorktreeHandle(
        path=tmp_path / "slot", base_ref="origin/main", branch="b", slot_index=2
    )
    with leased_with_affinity(
        workspace, graph, "t1", inv_id, lambda _e: None,
        persist_base_ref=True,
        worktree_factory=_fake_factory(handle, {}),
    ):
        pass
    reloaded = workflow_mod.load_graph(workspace, inv_id)
    assert reloaded.slot_index == 2
    assert reloaded.doc.base_ref_sha == "origin/main"


def test_first_lease_writes_a_durable_slot_hold_when_persist_base_ref(
    workspace: Workspace, tmp_path: Path
) -> None:
    inv_id, graph = _seed_graph(workspace)
    handle = WorktreeHandle(
        path=tmp_path / "slot", base_ref="origin/main", branch="b", slot_index=2
    )
    with leased_with_affinity(
        workspace, graph, "t1", inv_id, lambda _e: None,
        persist_base_ref=True,
        worktree_factory=_fake_factory(handle, {}),
    ):
        pass
    [(_path, hold)] = worker_records.load_all_slot_holds()
    assert hold is not None
    assert hold.workspace_id == workspace.workspace_id
    assert hold.slot_path == str(handle.path)


def test_persist_base_ref_false_never_writes_a_slot_hold(
    workspace: Workspace, tmp_path: Path
) -> None:
    inv_id, graph = _seed_graph(workspace, slot_index=0)
    handle = WorktreeHandle(
        path=tmp_path / "slot", base_ref="origin/main", branch="b", slot_index=4
    )
    with leased_with_affinity(
        workspace, graph, "t1", inv_id, lambda _e: None,
        worktree_factory=_fake_factory(handle, {}),
    ):
        pass
    assert worker_records.load_all_slot_holds() == []


def test_rescue_overwrites_base_ref_even_when_already_set(
    workspace: Workspace, tmp_path: Path
) -> None:
    inv_id, graph = _seed_graph(workspace, slot_index=0, base_ref_sha="old_sha")
    handle = WorktreeHandle(
        path=tmp_path / "slot", base_ref="fresh_base", branch="b",
        slot_index=0, rescued_ref="rescue/t@inv_001",
    )
    with leased_with_affinity(
        workspace, graph, "t1", inv_id, lambda _e: None,
        persist_base_ref=True,
        worktree_factory=_fake_factory(handle, {}),
    ):
        pass
    reloaded = workflow_mod.load_graph(workspace, inv_id)
    assert reloaded.doc.base_ref_sha == "fresh_base"


def test_normal_resume_does_not_overwrite_base_ref(
    workspace: Workspace, tmp_path: Path
) -> None:
    inv_id, graph = _seed_graph(workspace, slot_index=0, base_ref_sha="stored_sha")
    handle = WorktreeHandle(
        path=tmp_path / "slot", base_ref="origin/main", branch="b",
        slot_index=0, rescued_ref=None,
    )
    with leased_with_affinity(
        workspace, graph, "t1", inv_id, lambda _e: None,
        persist_base_ref=True,
        worktree_factory=_fake_factory(handle, {}),
    ):
        pass
    reloaded = workflow_mod.load_graph(workspace, inv_id)
    assert reloaded.doc.base_ref_sha == "stored_sha"


def test_persist_base_ref_false_never_touches_base_ref_sha(
    workspace: Workspace, tmp_path: Path
) -> None:
    inv_id, graph = _seed_graph(workspace, slot_index=0)
    handle = WorktreeHandle(
        path=tmp_path / "slot", base_ref="origin/main", branch="b", slot_index=4
    )
    with leased_with_affinity(
        workspace, graph, "t1", inv_id, lambda _e: None,
        worktree_factory=_fake_factory(handle, {}),
    ):
        pass
    reloaded = workflow_mod.load_graph(workspace, inv_id)
    assert reloaded.slot_index == 4
    assert reloaded.doc.base_ref_sha is None


def test_handle_slot_index_none_skips_save_entirely(
    workspace: Workspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inv_id, graph = _seed_graph(workspace)
    handle = WorktreeHandle(
        path=tmp_path / "slot", base_ref="origin/main", branch="b", slot_index=None
    )
    saved: list[object] = []
    monkeypatch.setattr(workflow_mod, "save_workflow", lambda ws, doc: saved.append(doc))
    with leased_with_affinity(
        workspace, graph, "t1", inv_id, lambda _e: None,
        persist_base_ref=True,
        worktree_factory=_fake_factory(handle, {}),
    ):
        pass
    assert saved == []


def test_preferred_slot_threaded_from_graph(workspace: Workspace, tmp_path: Path) -> None:
    inv_id, graph = _seed_graph(workspace, slot_index=7)
    handle = WorktreeHandle(
        path=tmp_path / "slot", base_ref="origin/main", branch="b", slot_index=7
    )
    captured: dict[str, object] = {}
    with leased_with_affinity(
        workspace, graph, "t1", inv_id, lambda _e: None,
        worktree_factory=_fake_factory(handle, captured),
    ):
        pass
    assert captured["kwargs"]["preferred_slot"] == 7  # type: ignore[index]


def test_resume_base_ref_threaded_only_when_persist_true(
    workspace: Workspace, tmp_path: Path
) -> None:
    inv_id, graph = _seed_graph(workspace, slot_index=1, base_ref_sha="abc123")
    handle = WorktreeHandle(
        path=tmp_path / "slot", base_ref="abc123", branch="b", slot_index=1
    )
    captured: dict[str, object] = {}
    with leased_with_affinity(
        workspace, graph, "t1", inv_id, lambda _e: None,
        persist_base_ref=True,
        worktree_factory=_fake_factory(handle, captured),
    ):
        pass
    assert captured["kwargs"]["resume_base_ref"] == "abc123"  # type: ignore[index]


def test_resume_base_ref_none_when_persist_false(workspace: Workspace, tmp_path: Path) -> None:
    inv_id, graph = _seed_graph(workspace, slot_index=1, base_ref_sha="abc123")
    handle = WorktreeHandle(
        path=tmp_path / "slot", base_ref="abc123", branch="b", slot_index=1
    )
    captured: dict[str, object] = {}
    with leased_with_affinity(
        workspace, graph, "t1", inv_id, lambda _e: None,
        worktree_factory=_fake_factory(handle, captured),
    ):
        pass
    assert captured["kwargs"]["resume_base_ref"] is None  # type: ignore[index]


def test_existing_branch_threaded_through(workspace: Workspace, tmp_path: Path) -> None:
    handle = WorktreeHandle(
        path=tmp_path / "slot", base_ref="origin/main", branch="pr-branch", slot_index=None
    )
    captured: dict[str, object] = {}
    with leased_with_affinity(
        workspace, None, "t1", "inv_001", lambda _e: None,
        existing_branch="pr-branch",
        worktree_factory=_fake_factory(handle, captured),
    ):
        pass
    assert captured["kwargs"]["existing_branch"] == "pr-branch"  # type: ignore[index]


def test_on_blocked_emits_slot_resume_waiting_with_preferred_slot(
    workspace: Workspace, tmp_path: Path
) -> None:
    inv_id, graph = _seed_graph(workspace, slot_index=9)
    handle = WorktreeHandle(
        path=tmp_path / "slot", base_ref="origin/main", branch="b", slot_index=9
    )
    captured: dict[str, object] = {}
    events: list[TeamEvent] = []
    with leased_with_affinity(
        workspace, graph, "task-x", inv_id, events.append,
        worktree_factory=_fake_factory(handle, captured),
    ):
        pass
    on_blocked = captured["kwargs"]["on_blocked"]  # type: ignore[index]
    assert callable(on_blocked)
    on_blocked()
    evt = next(e for e in events if e.kind == "slot_resume_waiting")
    assert evt.payload == {"task_id": "task-x", "inv_id": inv_id, "slot_index": "9"}


def test_on_blocked_slot_index_none_serializes_as_string(
    workspace: Workspace, tmp_path: Path
) -> None:
    handle = WorktreeHandle(
        path=tmp_path / "slot", base_ref="origin/main", branch="b", slot_index=None
    )
    captured: dict[str, object] = {}
    events: list[TeamEvent] = []
    with leased_with_affinity(
        workspace, None, "task-x", "inv_002", events.append,
        worktree_factory=_fake_factory(handle, captured),
    ):
        pass
    on_blocked = captured["kwargs"]["on_blocked"]  # type: ignore[index]
    on_blocked()
    evt = next(e for e in events if e.kind == "slot_resume_waiting")
    assert evt.payload["slot_index"] == "None"


def test_yields_handle_unchanged(workspace: Workspace, tmp_path: Path) -> None:
    handle = WorktreeHandle(
        path=tmp_path / "slot", base_ref="origin/main", branch="b", slot_index=None
    )
    with leased_with_affinity(
        workspace, None, "t1", "inv_001", lambda _e: None,
        worktree_factory=_fake_factory(handle, {}),
    ) as h:
        assert h is handle


@pytest.fixture(autouse=True)
def _state_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    return tmp_path / "state"


def _init_git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=str(path), check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=str(path), check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=str(path), check=True)
    (path / "f.txt").write_text("x")
    subprocess.run(["git", "add", "f.txt"], cwd=str(path), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(path), check=True)


def _write_survivor(
    *, task_id: str, inv_id: str, session_id: str, slot_path: Path,
    team: str = "implementation",
) -> None:
    worker_records.write_initial(
        worker_records.WorkerRecord(
            schema_version=worker_records.RECORD_VERSION,
            job_id=1, task_id=task_id, workspace_id=task_id, team=team,
            inv_id=inv_id, node_id="implementer", role="implementer", model="sonnet",
            engine="default", slot_path=str(slot_path), session_id=session_id,
            output_path=str(slot_path / "stream.jsonl"),
            pid=None, started_at=1.0, lstart=None, cmdline=None,
            deadline_at=None, idle_timeout_s=None, max_turns=150,
            service_pid=1, service_started_at="Thu Jan  1 00:00:00 1970",
            host=worker_records.current_host(), pgid=424242, journal_offset=0, err_offset=0,
        )
    )


def test_leased_with_affinity_adopts_survivor_without_touching_worktree_factory(
    workspace: Workspace, tmp_path: Path
) -> None:
    pool_root = tmp_path / "pool"
    pool = SlotPool(root=pool_root, size=1, repo_root=str(tmp_path))
    slot_path = pool.slot_path(0)
    _init_git_repo(slot_path)
    _write_survivor(
        task_id="t1", inv_id="inv_001", session_id="sess-survivor", slot_path=slot_path,
    )
    pool.reset_all({slot_path: Reservation("sess-survivor", adoptable=True)})
    set_active_pool(pool)
    try:
        with leased_with_affinity(
            workspace, None, "t1", "inv_001", lambda _e: None,
            worktree_factory=lambda *_a, **_kw: pytest.fail("must not touch worktree_factory"),
        ) as handle:
            assert handle.path == slot_path
            assert handle.slot_index == 0
            assert handle.branch in ("main", "master")
        assert pool._free == {0}  # noqa: SLF001
    finally:
        set_active_pool(None)


def test_leased_with_affinity_falls_through_when_pool_active_but_no_survivor(
    workspace: Workspace, tmp_path: Path
) -> None:
    pool = SlotPool(root=tmp_path / "pool", size=1, repo_root=str(tmp_path))
    handle = WorktreeHandle(
        path=tmp_path / "slot", base_ref="origin/main", branch="b", slot_index=0
    )
    captured: dict[str, object] = {}
    set_active_pool(pool)
    try:
        with leased_with_affinity(
            workspace, None, "t1", "inv_001", lambda _e: None,
            worktree_factory=_fake_factory(handle, captured),
        ) as h:
            assert h is handle
        assert captured
    finally:
        set_active_pool(None)


def test_find_survivor_reservation_returns_none_when_no_match() -> None:
    assert _find_survivor_reservation("t1", "inv_001") is None


def test_find_survivor_reservation_ignores_records_without_pgid(tmp_path: Path) -> None:
    _write_survivor(task_id="t1", inv_id="inv_001", session_id="s", slot_path=tmp_path / "s0")
    record = worker_records.load_one("s")
    assert record is not None
    assert _find_survivor_reservation("t1", "inv_001") is not None
    worker_records.remove_record("s")
    worker_records.write_initial(record.model_copy(update={"pgid": None}))
    assert _find_survivor_reservation("t1", "inv_001") is None


def test_find_survivor_reservation_picks_newest_when_duplicates_exist(tmp_path: Path) -> None:
    _write_survivor(task_id="t1", inv_id="inv_001", session_id="old", slot_path=tmp_path / "s0")
    worker_records.stamp_started("old", pid=1, started_at=1.0, lstart=None, cmdline=None)
    _write_survivor(task_id="t1", inv_id="inv_001", session_id="new", slot_path=tmp_path / "s1")
    worker_records.stamp_started("new", pid=2, started_at=99.0, lstart=None, cmdline=None)

    found = _find_survivor_reservation("t1", "inv_001")
    assert found is not None
    assert found.session_id == "new"


def test_git_ref_falls_back_to_head_for_non_git_directory(tmp_path: Path) -> None:
    empty = tmp_path / "not-a-repo"
    empty.mkdir()
    assert _git_ref(["rev-parse", "HEAD"], empty) == "HEAD"


def test_leased_with_affinity_degrades_to_fresh_lease_on_reservation_mismatch(
    workspace: Workspace, tmp_path: Path
) -> None:
    inv_id, graph = _seed_graph(workspace, slot_index=5)
    pool_root = tmp_path / "pool"
    pool = SlotPool(root=pool_root, size=1, repo_root=str(tmp_path))
    slot_path = pool.slot_path(0)
    _init_git_repo(slot_path)
    _write_survivor(
        task_id="t1", inv_id=inv_id, session_id="sess-survivor", slot_path=slot_path,
    )
    pool.reset_all({slot_path: Reservation("some-other-live-token", adoptable=True)})
    set_active_pool(pool)
    handle = WorktreeHandle(
        path=tmp_path / "fresh-slot", base_ref="origin/main", branch="b", slot_index=None
    )
    captured: dict[str, object] = {}
    events: list[TeamEvent] = []
    try:
        with leased_with_affinity(
            workspace, graph, "t1", inv_id, events.append,
            worktree_factory=_fake_factory(handle, captured),
        ) as h:
            assert h is handle
    finally:
        set_active_pool(None)
    assert captured["kwargs"]["preferred_slot"] is None  # type: ignore[index]
    evt = next(e for e in events if e.kind == "slot_adoption_lost")
    assert evt.payload == {"task_id": "t1", "inv_id": inv_id, "slot_path": str(slot_path)}
    assert pool._reserved == {0: "some-other-live-token"}  # noqa: SLF001


def test_leased_with_affinity_degrade_preserves_resume_base_ref(
    workspace: Workspace, tmp_path: Path
) -> None:
    inv_id, graph = _seed_graph(workspace, slot_index=5, base_ref_sha="abc123")
    pool = SlotPool(root=tmp_path / "pool", size=1, repo_root=str(tmp_path))
    slot_path = pool.slot_path(0)
    _init_git_repo(slot_path)
    _write_survivor(
        task_id="t1", inv_id=inv_id, session_id="sess-survivor", slot_path=slot_path,
    )
    pool.reset_all({slot_path: Reservation("some-other-live-token", adoptable=True)})
    set_active_pool(pool)
    handle = WorktreeHandle(
        path=tmp_path / "fresh-slot", base_ref="abc123", branch="b", slot_index=None
    )
    captured: dict[str, object] = {}
    try:
        with leased_with_affinity(
            workspace, graph, "t1", inv_id, lambda _e: None,
            persist_base_ref=True,
            worktree_factory=_fake_factory(handle, captured),
        ):
            pass
    finally:
        set_active_pool(None)
    assert captured["kwargs"]["resume_base_ref"] == "abc123"  # type: ignore[index]


def test_resume_base_ref_returns_none_for_graph_none() -> None:
    assert _resume_base_ref(None, True) is None


def test_resume_base_ref_returns_none_when_persist_false(workspace: Workspace) -> None:
    _inv_id, graph = _seed_graph(workspace, slot_index=0, base_ref_sha="abc123")
    assert _resume_base_ref(graph, False) is None


def test_resume_base_ref_returns_stored_sha_when_persist_true(workspace: Workspace) -> None:
    _inv_id, graph = _seed_graph(workspace, slot_index=0, base_ref_sha="abc123")
    assert _resume_base_ref(graph, True) == "abc123"


def test_ancestor_check_rc_zero_for_valid_ancestor(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    first_sha = _git_ref(["rev-parse", "HEAD"], repo)
    (repo / "f.txt").write_text("y")
    subprocess.run(["git", "add", "f.txt"], cwd=str(repo), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "second"], cwd=str(repo), check=True)
    assert _ancestor_check_rc(first_sha, repo) == 0


def test_ancestor_check_rc_nonzero_for_unresolvable_sha(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    assert _ancestor_check_rc("0" * 40, repo) != 0


def test_ancestor_check_rc_nonzero_for_rebased_away_sha(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    (repo / "f.txt").write_text("y")
    subprocess.run(["git", "add", "f.txt"], cwd=str(repo), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "second"], cwd=str(repo), check=True)
    stray_sha = _git_ref(["rev-parse", "HEAD"], repo)
    subprocess.run(
        ["git", "reset", "--hard", "HEAD~1"], cwd=str(repo), check=True
    )
    assert _ancestor_check_rc(stray_sha, repo) != 0


def test_ancestor_check_rc_returns_sentinel_on_subprocess_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise(*_args: object, **_kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd="git", timeout=1.0)

    monkeypatch.setattr(subprocess, "run", _raise)
    assert _ancestor_check_rc("deadbeef", tmp_path) == _ANCESTOR_CHECK_ERROR_RC
