from __future__ import annotations

import contextvars
import subprocess
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import pytest

from alan import _dispatcher_base, claude_cli, roots, worker_records
from alan import workspace as workspace_mod
from alan.claude_cli import get_active_cache_env, set_active_cache_env
from alan.engine_config import EngineConfig, Posture, RepoIdentity
from alan.routes.infra import _engine_pool_dirname
from alan.slot_pool import Reservation, SlotPool, set_active_pool
from alan.teams._pr_forge import governing_config_for_ws
from alan.teams._slot_affinity import leased_with_affinity
from alan.teams._slot_derived_data import DERIVED_DATA_ENV, engine_dirname, slot_segment
from alan.workspace import EngineSnapshot, Workspace, create
from alan.worktree import WorktreeHandle


@pytest.fixture(autouse=True)
def _state_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    return tmp_path / "state"


@pytest.fixture(autouse=True)
def _reset_context_vars() -> Iterator[None]:
    yield
    set_active_cache_env(None)
    set_active_pool(None)


def _engine_workspace(tmp_path: Path, slug: str, *, name: str) -> Workspace:
    ws = create(f"{name}-task", "desc", root=tmp_path / name)
    snapshot = EngineSnapshot(
        posture=Posture.observe,
        transition_graph={},
        config=EngineConfig(repo=RepoIdentity.from_slug(slug)),
    )
    return workspace_mod.stamp_engine_snapshot(ws, snapshot)


def _fake_factory(handle: WorktreeHandle, captured: dict[str, object]):  # type: ignore[no-untyped-def]
    @contextmanager
    def _factory(*args: object, **kwargs: object) -> Iterator[WorktreeHandle]:
        captured["args"] = args
        captured["kwargs"] = kwargs
        yield handle

    return _factory


def _recording_factory(handle: WorktreeHandle, sink: dict[str, object]):  # type: ignore[no-untyped-def]
    @contextmanager
    def _factory(*args: object, **kwargs: object) -> Iterator[WorktreeHandle]:
        try:
            yield handle
        finally:
            sink["cache_env_at_teardown"] = get_active_cache_env()

    return _factory


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
) -> None:
    worker_records.write_initial(
        worker_records.WorkerRecord(
            schema_version=worker_records.RECORD_VERSION,
            job_id=1, task_id=task_id, workspace_id=task_id, team="implementation",
            inv_id=inv_id, node_id="implementer", role="implementer", model="sonnet",
            engine="default", slot_path=str(slot_path), session_id=session_id,
            output_path=str(slot_path / "stream.jsonl"),
            pid=None, started_at=1.0, lstart=None, cmdline=None,
            deadline_at=None, idle_timeout_s=None, max_turns=150,
            service_pid=1, service_started_at="Thu Jan  1 00:00:00 1970",
            host=worker_records.current_host(), pgid=424242, journal_offset=0, err_offset=0,
        )
    )


def test_two_slots_of_one_engine_get_distinct_derived_data_paths(tmp_path: Path) -> None:
    ws = _engine_workspace(tmp_path, "acme/widget", name="two-slots")
    set_active_cache_env({DERIVED_DATA_ENV: "deriveddata"})
    handle0 = WorktreeHandle(
        path=tmp_path / "s0", base_ref="origin/main", branch="b", slot_index=0
    )
    with leased_with_affinity(
        ws, None, "t1", "inv1", lambda _e: None, worktree_factory=_fake_factory(handle0, {}),
    ):
        path0 = claude_cli._worker_env()[DERIVED_DATA_ENV]  # noqa: SLF001
    handle1 = WorktreeHandle(
        path=tmp_path / "s1", base_ref="origin/main", branch="b", slot_index=1
    )
    with leased_with_affinity(
        ws, None, "t1", "inv1", lambda _e: None, worktree_factory=_fake_factory(handle1, {}),
    ):
        path1 = claude_cli._worker_env()[DERIVED_DATA_ENV]  # noqa: SLF001
    assert path0 != path1


def test_same_slot_number_in_two_engines_gets_distinct_derived_data_paths(
    tmp_path: Path,
) -> None:
    ws_a = _engine_workspace(tmp_path, "acme/widget", name="engine-a")
    ws_b = _engine_workspace(tmp_path, "other/thing", name="engine-b")
    set_active_cache_env({DERIVED_DATA_ENV: "deriveddata"})
    handle = WorktreeHandle(
        path=tmp_path / "s3", base_ref="origin/main", branch="b", slot_index=3
    )
    with leased_with_affinity(
        ws_a, None, "t1", "inv1", lambda _e: None, worktree_factory=_fake_factory(handle, {}),
    ):
        path_a = claude_cli._worker_env()[DERIVED_DATA_ENV]  # noqa: SLF001
    with leased_with_affinity(
        ws_b, None, "t1", "inv1", lambda _e: None, worktree_factory=_fake_factory(handle, {}),
    ):
        path_b = claude_cli._worker_env()[DERIVED_DATA_ENV]  # noqa: SLF001
    assert path_a != path_b


def test_same_slot_keeps_the_same_derived_data_path_across_leases(tmp_path: Path) -> None:
    ws = _engine_workspace(tmp_path, "acme/widget", name="repeat-slot")
    set_active_cache_env({DERIVED_DATA_ENV: "deriveddata"})
    handle = WorktreeHandle(
        path=tmp_path / "s2", base_ref="origin/main", branch="b", slot_index=2
    )
    with leased_with_affinity(
        ws, None, "t1", "inv1", lambda _e: None, worktree_factory=_fake_factory(handle, {}),
    ):
        first = claude_cli._worker_env()[DERIVED_DATA_ENV]  # noqa: SLF001
    with leased_with_affinity(
        ws, None, "t2", "inv2", lambda _e: None, worktree_factory=_fake_factory(handle, {}),
    ):
        second = claude_cli._worker_env()[DERIVED_DATA_ENV]  # noqa: SLF001
    assert first == second


def test_readopted_slot_gets_its_own_slot_derived_data_path(tmp_path: Path) -> None:
    ws = _engine_workspace(tmp_path, "acme/widget", name="readopt")
    pool = SlotPool(root=tmp_path / "pool", size=2, repo_root=str(tmp_path))
    slot_path = pool.slot_path(1)
    _init_git_repo(slot_path)
    _write_survivor(
        task_id="t1", inv_id="inv1", session_id="sess-survivor", slot_path=slot_path,
    )
    pool.reset_all({slot_path: Reservation("sess-survivor", adoptable=True)})
    set_active_pool(pool)
    set_active_cache_env({DERIVED_DATA_ENV: "deriveddata"})
    try:
        with leased_with_affinity(
            ws, None, "t1", "inv1", lambda _e: None,
            worktree_factory=lambda *_a, **_kw: pytest.fail("must not touch worktree_factory"),
        ):
            path = claude_cli._worker_env()[DERIVED_DATA_ENV]  # noqa: SLF001
    finally:
        set_active_pool(None)
    expected = str(roots.cache_root() / slot_segment("acme/widget", 1))
    assert path == expected


def test_worker_env_is_restored_exactly_after_the_lease_ends(tmp_path: Path) -> None:
    ws = _engine_workspace(tmp_path, "acme/widget", name="restored")
    prior = {DERIVED_DATA_ENV: "deriveddata"}
    set_active_cache_env(prior)
    before = claude_cli._worker_env()  # noqa: SLF001
    handle = WorktreeHandle(
        path=tmp_path / "s0", base_ref="origin/main", branch="b", slot_index=2
    )
    with leased_with_affinity(
        ws, None, "t1", "inv1", lambda _e: None, worktree_factory=_fake_factory(handle, {}),
    ):
        pass
    assert get_active_cache_env() is prior
    after = claude_cli._worker_env()  # noqa: SLF001
    assert after == before


def test_worker_env_is_restored_when_the_lease_body_raises(tmp_path: Path) -> None:
    ws = _engine_workspace(tmp_path, "acme/widget", name="restored-raise")
    prior = {DERIVED_DATA_ENV: "deriveddata"}
    set_active_cache_env(prior)
    handle = WorktreeHandle(
        path=tmp_path / "s0", base_ref="origin/main", branch="b", slot_index=2
    )
    with pytest.raises(RuntimeError):
        with leased_with_affinity(
            ws, None, "t1", "inv1", lambda _e: None, worktree_factory=_fake_factory(handle, {}),
        ):
            raise RuntimeError("boom")
    assert get_active_cache_env() is prior


def test_cache_env_is_restored_before_the_lease_is_torn_down(tmp_path: Path) -> None:
    ws = _engine_workspace(tmp_path, "acme/widget", name="restore-order")
    prior = {DERIVED_DATA_ENV: "deriveddata"}
    set_active_cache_env(prior)
    handle = WorktreeHandle(
        path=tmp_path / "s0", base_ref="origin/main", branch="b", slot_index=2
    )
    sink: dict[str, object] = {}
    with leased_with_affinity(
        ws, None, "t1", "inv1", lambda _e: None,
        worktree_factory=_recording_factory(handle, sink),
    ):
        pass
    assert sink["cache_env_at_teardown"] is prior


def test_every_per_slot_path_lies_under_the_redirected_cache_root(tmp_path: Path) -> None:
    ws = _engine_workspace(tmp_path, "acme/widget", name="under-root")
    set_active_cache_env({DERIVED_DATA_ENV: "deriveddata"})
    handle = WorktreeHandle(
        path=tmp_path / "s1", base_ref="origin/main", branch="b", slot_index=1
    )
    with leased_with_affinity(
        ws, None, "t1", "inv1", lambda _e: None, worktree_factory=_fake_factory(handle, {}),
    ):
        path = Path(claude_cli._worker_env()[DERIVED_DATA_ENV])  # noqa: SLF001
    cache_root = roots.cache_root()
    assert path.is_relative_to(cache_root)
    assert not path.is_relative_to(cache_root / "deriveddata")


def test_lease_without_derived_data_variable_leaves_worker_env_unchanged(
    tmp_path: Path,
) -> None:
    ws = _engine_workspace(tmp_path, "acme/widget", name="no-derived-data")
    prior = {"UV_CACHE_DIR": "uv", "npm_config_cache": "npm"}
    set_active_cache_env(prior)
    handle = WorktreeHandle(
        path=tmp_path / "s1", base_ref="origin/main", branch="b", slot_index=1
    )
    with leased_with_affinity(
        ws, None, "t1", "inv1", lambda _e: None, worktree_factory=_fake_factory(handle, {}),
    ):
        assert get_active_cache_env() is prior


def test_lease_without_active_cache_env_injects_nothing(tmp_path: Path) -> None:
    ws = _engine_workspace(tmp_path, "acme/widget", name="no-active-cache")
    set_active_cache_env(None)
    handle = WorktreeHandle(
        path=tmp_path / "s1", base_ref="origin/main", branch="b", slot_index=1
    )
    with leased_with_affinity(
        ws, None, "t1", "inv1", lambda _e: None, worktree_factory=_fake_factory(handle, {}),
    ):
        assert get_active_cache_env() is None


def test_lease_without_slot_index_keeps_the_shared_derived_data_path(tmp_path: Path) -> None:
    ws = _engine_workspace(tmp_path, "acme/widget", name="no-slot-index")
    prior = {DERIVED_DATA_ENV: "deriveddata"}
    set_active_cache_env(prior)
    handle = WorktreeHandle(
        path=tmp_path / "s1", base_ref="origin/main", branch="b", slot_index=None
    )
    with leased_with_affinity(
        ws, None, "t1", "inv1", lambda _e: None, worktree_factory=_fake_factory(handle, {}),
    ):
        assert get_active_cache_env() is prior


def test_worker_outside_any_lease_keeps_the_shared_derived_data_path(tmp_path: Path) -> None:
    set_active_cache_env({DERIVED_DATA_ENV: "deriveddata"})
    path = claude_cli._worker_env()[DERIVED_DATA_ENV]  # noqa: SLF001
    assert path == str(roots.cache_root() / "deriveddata")


def test_only_derived_data_path_changes_inside_the_lease(tmp_path: Path) -> None:
    ws = _engine_workspace(tmp_path, "acme/widget", name="only-dd-changes")
    prior = {
        DERIVED_DATA_ENV: "deriveddata", "UV_CACHE_DIR": "uv", "NUGET_PACKAGES": "nuget",
    }
    set_active_cache_env(prior)
    handle = WorktreeHandle(
        path=tmp_path / "s1", base_ref="origin/main", branch="b", slot_index=1
    )
    with leased_with_affinity(
        ws, None, "t1", "inv1", lambda _e: None, worktree_factory=_fake_factory(handle, {}),
    ):
        env = claude_cli._worker_env()  # noqa: SLF001
        assert env["UV_CACHE_DIR"] == str(roots.cache_root() / "uv")
        assert env["NUGET_PACKAGES"] == str(roots.cache_root() / "nuget")
        assert env[DERIVED_DATA_ENV] != str(roots.cache_root() / "deriveddata")


def test_parallel_roles_in_copied_context_inherit_the_per_slot_path(tmp_path: Path) -> None:
    ws = _engine_workspace(tmp_path, "acme/widget", name="parallel-roles")
    set_active_cache_env({DERIVED_DATA_ENV: "deriveddata"})
    handle = WorktreeHandle(
        path=tmp_path / "s1", base_ref="origin/main", branch="b", slot_index=1
    )
    with leased_with_affinity(
        ws, None, "t1", "inv1", lambda _e: None, worktree_factory=_fake_factory(handle, {}),
    ):
        expected = claude_cli._worker_env()[DERIVED_DATA_ENV]  # noqa: SLF001
        ctx = contextvars.copy_context()
        with ThreadPoolExecutor(max_workers=1) as executor:
            result = executor.submit(
                ctx.run, lambda: claude_cli._worker_env()[DERIVED_DATA_ENV]  # noqa: SLF001
            ).result()
        assert result == expected


def test_dotdot_engine_slug_yields_a_relative_sanitized_segment() -> None:
    segment = slot_segment("../../etc/passwd", 0)
    assert ".." not in Path(segment).parts
    assert not Path(segment).is_absolute()


def test_absolute_engine_slug_yields_a_relative_sanitized_segment() -> None:
    segment = slot_segment("/etc/passwd", 0)
    assert not Path(segment).is_absolute()


def test_slashed_engine_slug_yields_a_single_directory_segment() -> None:
    dirname = engine_dirname("acme/widget")
    assert "/" not in dirname


def test_sanitized_engine_dirs_do_not_collide_after_cleaning() -> None:
    assert engine_dirname("a_b/c") != engine_dirname("a/b_c")


def test_engine_dirname_matches_the_slot_pool_dirname_format() -> None:
    assert engine_dirname("acme/widget") == _engine_pool_dirname("acme/widget")


def test_pr_forge_and_dispatcher_resolve_the_same_engine_without_a_snapshot(
    tmp_path: Path,
) -> None:
    ws = create("parity-task", "desc", root=tmp_path / "parity")
    assert (
        governing_config_for_ws(ws).repo
        == _dispatcher_base.governing_engine_config(ws).repo
    )

    keyed_meta = ws.meta.model_copy(update={"engine_key": "unregistered/repo"})
    ws_keyed = Workspace(task_id=ws.task_id, path=ws.path, meta=keyed_meta)
    assert (
        governing_config_for_ws(ws_keyed).repo
        == _dispatcher_base.governing_engine_config(ws_keyed).repo
    )
