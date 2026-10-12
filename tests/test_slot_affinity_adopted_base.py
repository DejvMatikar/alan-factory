from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import NoReturn

import pytest

from alan import workflow as workflow_mod
from alan.slot_pool import Reservation, SlotPool, set_active_pool
from alan.teams import _pr_handover, implementation_fix
from alan.teams._base import node_turns_audit_dir
from alan.teams._pr_push import PushOutcome, PushResult
from alan.teams._slot_affinity import _ADOPTION_AUDIT_FILE, _git_ref, leased_with_affinity
from alan.workspace import Workspace, create
from alan.worktree import captured_commits
from tests.test_slot_affinity import _init_git_repo, _seed_graph, _write_survivor


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return create("affinity-adopted-task", "desc", root=tmp_path / "ws")


@pytest.fixture(autouse=True)
def _state_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "state"))
    return tmp_path / "state"


def _commit_file(repo: Path, name: str, msg: str) -> str:
    (repo / name).write_text(name)
    subprocess.run(["git", "add", name], cwd=str(repo), check=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=str(repo), check=True)
    return _git_ref(["rev-parse", "HEAD"], repo)


def _read_audit_rows() -> list[dict[str, object]]:
    path = node_turns_audit_dir() / _ADOPTION_AUDIT_FILE
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _no_touch_factory(*_args: object, **_kwargs: object) -> NoReturn:
    pytest.fail("must not touch worktree_factory")


def test_adopted_fix_run_captures_and_pushes_from_stored_anchor(
    workspace: Workspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool = SlotPool(root=tmp_path / "pool", size=1, repo_root=str(tmp_path))
    slot_path = pool.slot_path(0)
    _init_git_repo(slot_path)
    a_sha = _git_ref(["rev-parse", "HEAD"], slot_path)
    _commit_file(slot_path, "b.txt", "MARKB")
    _commit_file(slot_path, "c.txt", "MARKC")

    inv_id, graph = _seed_graph(workspace, slot_index=0, base_ref_sha=a_sha)
    _write_survivor(
        task_id="t1", inv_id=inv_id, session_id="sess-1", slot_path=slot_path,
        team="implementation_fix",
    )
    pool.reset_all({slot_path: Reservation("sess-1", adoptable=True)})
    set_active_pool(pool)
    monkeypatch.setattr(implementation_fix, "dry_run_enabled", lambda: False)
    recorded: dict[str, object] = {}

    def _fake_push(
        path: Path, branch: str, task_id: str, base_sha: str, *, forge_target: object
    ) -> PushResult:
        recorded["base_sha"] = base_sha
        return PushResult(outcome=PushOutcome.PUSHED)

    monkeypatch.setattr(implementation_fix, "push_fix_branch_with_reconcile", _fake_push)
    try:
        with leased_with_affinity(
            workspace, graph, "t1", inv_id, lambda _e: None,
            persist_base_ref=True, worktree_factory=_no_touch_factory,
        ) as handle:
            commits = captured_commits(handle)
            assert any("MARKB" in c for c in commits)
            assert any("MARKC" in c for c in commits)
            implementation_fix._push_fix_branch(workspace, handle, commits)
    finally:
        set_active_pool(None)
    assert recorded["base_sha"] == a_sha


def test_adopted_implementation_run_captures_from_stored_anchor(
    workspace: Workspace, tmp_path: Path
) -> None:
    pool = SlotPool(root=tmp_path / "pool", size=1, repo_root=str(tmp_path))
    slot_path = pool.slot_path(0)
    _init_git_repo(slot_path)
    a_sha = _git_ref(["rev-parse", "HEAD"], slot_path)
    _commit_file(slot_path, "b.txt", "MARKB")
    _commit_file(slot_path, "c.txt", "MARKC")

    inv_id, graph = _seed_graph(workspace, slot_index=0, base_ref_sha=a_sha)
    _write_survivor(
        task_id="t1", inv_id=inv_id, session_id="sess-1", slot_path=slot_path,
        team="implementation",
    )
    pool.reset_all({slot_path: Reservation("sess-1", adoptable=True)})
    set_active_pool(pool)
    try:
        with leased_with_affinity(
            workspace, graph, "t1", inv_id, lambda _e: None,
            persist_base_ref=True, worktree_factory=_no_touch_factory,
        ) as handle:
            commits = captured_commits(handle)
            assert any("MARKB" in c for c in commits)
            assert any("MARKC" in c for c in commits)
    finally:
        set_active_pool(None)


def test_adopted_no_stored_anchor_falls_back_to_head(
    workspace: Workspace, tmp_path: Path
) -> None:
    pool = SlotPool(root=tmp_path / "pool", size=1, repo_root=str(tmp_path))
    slot_path = pool.slot_path(0)
    _init_git_repo(slot_path)
    head_sha = _git_ref(["rev-parse", "HEAD"], slot_path)

    inv_id, graph = _seed_graph(workspace, slot_index=0, base_ref_sha=None)
    _write_survivor(task_id="t1", inv_id=inv_id, session_id="sess-1", slot_path=slot_path)
    pool.reset_all({slot_path: Reservation("sess-1", adoptable=True)})
    set_active_pool(pool)
    try:
        with leased_with_affinity(
            workspace, graph, "t1", inv_id, lambda _e: None,
            persist_base_ref=True, worktree_factory=_no_touch_factory,
        ) as handle:
            assert handle.base_ref == head_sha
    finally:
        set_active_pool(None)

    rows = _read_audit_rows()
    assert rows[-1]["resolution"] == "no_stored_anchor"
    assert rows[-1]["ancestor_rc"] is None


def test_adopted_non_ancestor_anchor_falls_back_to_head(
    workspace: Workspace, tmp_path: Path
) -> None:
    pool = SlotPool(root=tmp_path / "pool", size=1, repo_root=str(tmp_path))
    slot_path = pool.slot_path(0)
    _init_git_repo(slot_path)
    a_sha = _git_ref(["rev-parse", "HEAD"], slot_path)
    b_sha = _commit_file(slot_path, "b.txt", "MARKB")
    subprocess.run(["git", "reset", "--hard", a_sha], cwd=str(slot_path), check=True)
    d_sha = _commit_file(slot_path, "d.txt", "MARKD")

    inv_id, graph = _seed_graph(workspace, slot_index=0, base_ref_sha=b_sha)
    _write_survivor(task_id="t1", inv_id=inv_id, session_id="sess-1", slot_path=slot_path)
    pool.reset_all({slot_path: Reservation("sess-1", adoptable=True)})
    set_active_pool(pool)
    try:
        with leased_with_affinity(
            workspace, graph, "t1", inv_id, lambda _e: None,
            persist_base_ref=True, worktree_factory=_no_touch_factory,
        ) as handle:
            assert handle.base_ref == d_sha
    finally:
        set_active_pool(None)

    rows = _read_audit_rows()
    assert rows[-1]["resolution"] == "ancestor_check_failed"
    assert rows[-1]["stored_anchor"] == b_sha


def test_adopted_fallback_anchor_is_persisted_so_a_later_restart_keeps_earlier_commits(
    workspace: Workspace, tmp_path: Path
) -> None:
    pool = SlotPool(root=tmp_path / "pool", size=1, repo_root=str(tmp_path))
    slot_path = pool.slot_path(0)
    _init_git_repo(slot_path)
    a_sha = _git_ref(["rev-parse", "HEAD"], slot_path)
    b_sha = _commit_file(slot_path, "b.txt", "MARKB")
    subprocess.run(["git", "reset", "--hard", a_sha], cwd=str(slot_path), check=True)
    d_sha = _commit_file(slot_path, "d.txt", "MARKD")

    inv_id, graph = _seed_graph(workspace, slot_index=0, base_ref_sha=b_sha)
    _write_survivor(task_id="t1", inv_id=inv_id, session_id="sess-1", slot_path=slot_path)
    pool.reset_all({slot_path: Reservation("sess-1", adoptable=True)})
    set_active_pool(pool)
    try:
        with leased_with_affinity(
            workspace, graph, "t1", inv_id, lambda _e: None,
            persist_base_ref=True, worktree_factory=_no_touch_factory,
        ) as handle:
            assert handle.base_ref == d_sha

        reloaded = workflow_mod.load_graph(workspace, inv_id)
        assert reloaded.doc.base_ref_sha == d_sha

        _commit_file(slot_path, "e.txt", "MARKE")
        _write_survivor(task_id="t1", inv_id=inv_id, session_id="sess-1", slot_path=slot_path)
        pool.reset_all({slot_path: Reservation("sess-1", adoptable=True)})
        with leased_with_affinity(
            workspace, reloaded, "t1", inv_id, lambda _e: None,
            persist_base_ref=True, worktree_factory=_no_touch_factory,
        ) as handle2:
            commits = captured_commits(handle2)
            assert any("MARKE" in c for c in commits)
            assert not any("MARKD" in c for c in commits)
    finally:
        set_active_pool(None)


def test_adopted_audit_rows_append_across_two_adoptions(
    workspace: Workspace, tmp_path: Path
) -> None:
    pool = SlotPool(root=tmp_path / "pool", size=1, repo_root=str(tmp_path))
    slot_path = pool.slot_path(0)
    _init_git_repo(slot_path)
    a_sha = _git_ref(["rev-parse", "HEAD"], slot_path)
    set_active_pool(pool)
    try:
        inv_id1, graph1 = _seed_graph(workspace, slot_index=0, base_ref_sha=None)
        _write_survivor(task_id="t1", inv_id=inv_id1, session_id="sess-1", slot_path=slot_path)
        pool.reset_all({slot_path: Reservation("sess-1", adoptable=True)})
        with leased_with_affinity(
            workspace, graph1, "t1", inv_id1, lambda _e: None,
            persist_base_ref=True, worktree_factory=_no_touch_factory,
        ):
            pass

        b_sha = _commit_file(slot_path, "b.txt", "MARKB")
        subprocess.run(["git", "reset", "--hard", a_sha], cwd=str(slot_path), check=True)
        _commit_file(slot_path, "d.txt", "MARKD")

        inv_id2, graph2 = _seed_graph(workspace, slot_index=0, base_ref_sha=b_sha)
        _write_survivor(task_id="t1", inv_id=inv_id2, session_id="sess-2", slot_path=slot_path)
        pool.reset_all({slot_path: Reservation("sess-2", adoptable=True)})
        with leased_with_affinity(
            workspace, graph2, "t1", inv_id2, lambda _e: None,
            persist_base_ref=True, worktree_factory=_no_touch_factory,
        ):
            pass
    finally:
        set_active_pool(None)

    rows = _read_audit_rows()
    resolutions = {row["resolution"] for row in rows}
    assert resolutions == {"no_stored_anchor", "ancestor_check_failed"}


def test_adopted_run_with_no_new_commits_still_reports_no_commits(
    workspace: Workspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool = SlotPool(root=tmp_path / "pool", size=1, repo_root=str(tmp_path))
    slot_path = pool.slot_path(0)
    _init_git_repo(slot_path)
    a_sha = _git_ref(["rev-parse", "HEAD"], slot_path)

    inv_id, graph = _seed_graph(workspace, slot_index=0, base_ref_sha=a_sha)
    _write_survivor(
        task_id="t1", inv_id=inv_id, session_id="sess-1", slot_path=slot_path,
        team="implementation_fix",
    )
    pool.reset_all({slot_path: Reservation("sess-1", adoptable=True)})
    set_active_pool(pool)
    monkeypatch.setattr(implementation_fix, "dry_run_enabled", lambda: False)
    try:
        with leased_with_affinity(
            workspace, graph, "t1", inv_id, lambda _e: None,
            persist_base_ref=True, worktree_factory=_no_touch_factory,
        ) as handle:
            assert handle.base_ref == a_sha
            commits = captured_commits(handle)
            assert commits == []
            push_result = implementation_fix._push_fix_branch(workspace, handle, commits)
    finally:
        set_active_pool(None)
    assert push_result.outcome is PushOutcome.PUSH_FAILED


def test_adopted_ancestor_check_failed_through_worktree_iteration_emits_no_rescue_event(
    workspace: Workspace, tmp_path: Path
) -> None:
    pool = SlotPool(root=tmp_path / "pool", size=1, repo_root=str(tmp_path))
    slot_path = pool.slot_path(0)
    _init_git_repo(slot_path)
    a_sha = _git_ref(["rev-parse", "HEAD"], slot_path)
    b_sha = _commit_file(slot_path, "b.txt", "MARKB")
    subprocess.run(["git", "reset", "--hard", a_sha], cwd=str(slot_path), check=True)
    _commit_file(slot_path, "d.txt", "MARKD")

    inv_id, _graph = _seed_graph(workspace, slot_index=0, base_ref_sha=b_sha)
    _write_survivor(
        task_id=workspace.task_id, inv_id=inv_id, session_id="sess-1", slot_path=slot_path,
    )
    pool.reset_all({slot_path: Reservation("sess-1", adoptable=True)})
    set_active_pool(pool)
    events: list[object] = []
    try:
        with _pr_handover.worktree_iteration(
            workspace, "implementation", inv_id, events.append, existing_branch=None
        ) as (_graph, handle):
            resolved_base_ref = handle.base_ref
    finally:
        set_active_pool(None)

    assert not any(getattr(event, "kind", None) == "slot_work_rescued" for event in events)
    reloaded = workflow_mod.load_graph(workspace, inv_id)
    assert reloaded.doc.base_ref_sha == resolved_base_ref

    rows = _read_audit_rows()
    assert rows[-1]["resolution"] == "ancestor_check_failed"
