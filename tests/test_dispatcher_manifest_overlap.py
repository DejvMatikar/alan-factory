from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan import engine_config
from alan import workspace as workspace_mod
from alan._dispatcher_reconcile import _normalized_files, _sibling_manifests
from alan.dispatcher import Dispatcher
from alan.engine_config import (
    DEFAULT_FORGE_TARGET,
    EngineConfig,
    ForgeConfig,
    RepoIdentity,
    forge_target_for_workspace,
)
from alan.handover import Handover, HandoverType
from alan.queue import Queue
from alan.router import RouterDecision
from alan.teams import _pr_audit_log, _pr_branch
from alan.teams._base import TeamEvent
from alan.workspace import Task, TaskStatus, Workspace

_PR_NUMBER = 777


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


def _task(
    task_id: str,
    *,
    parent: str | None = None,
    files: list[str] | None = None,
    status: TaskStatus = TaskStatus.PENDING,
) -> Task:
    return Task(
        id=task_id,
        team="implementation",
        status=status,
        parent_task_id=parent,
        files=files or [],
    )


def _ws(root: Path, tasks: list[Task], *, name: str = "manifest-ws") -> Workspace:
    return workspace_mod.create(name, "desc", root=root, tasks=tasks)


def _handover(
    *,
    task_id: str,
    handover_type: HandoverType = "forward",
    from_team: str = "pr_merge",
    pr_number: int | None = _PR_NUMBER,
    files_to_edit: list[str] | None = None,
) -> Handover:
    return Handover(
        handover_id="h-1",
        handover_type=handover_type,
        task_id=task_id,
        from_team=from_team,
        from_invocation_id="inv_001",
        to_team_suggestions=[],
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="synthesis",
        pr_number=pr_number,
        files_to_edit=files_to_edit or [],
    )


class TestNormalizedFiles:
    def test_strips_and_dedupes_preserving_order(self) -> None:
        assert _normalized_files(["b.py", "a.py ", "a.py", " b.py"]) == ["b.py", "a.py"]

    def test_empty_input(self) -> None:
        assert _normalized_files([]) == []


class TestSiblingManifests:
    def test_unknown_task_id_returns_empty(self, root: Path) -> None:
        ws = _ws(root, [_task("t1", parent="root", files=["a.py"])])
        assert _sibling_manifests(ws, "missing") == {}

    def test_no_parent_returns_empty(self, root: Path) -> None:
        ws = _ws(root, [_task("t1", parent=None, files=["a.py"])])
        assert _sibling_manifests(ws, "t1") == {}

    def test_returns_only_same_parent_siblings_with_files(self, root: Path) -> None:
        ws = _ws(
            root,
            [
                _task("t1", parent="root", files=["a.py"]),
                _task("t2", parent="root", files=["b.py"]),
                _task("t3", parent="other", files=["c.py"]),
                _task("t4", parent="root", files=[]),
            ],
        )
        assert _sibling_manifests(ws, "t1") == {"t2": ["b.py"]}


class TestMaybePopulateKeepManifest:
    def test_non_implementation_decision_skips(self, root: Path, queue: Queue) -> None:
        ws = _ws(root, [_task("root")])
        d = Dispatcher(queue, {}, workspaces_root=root)
        decision = RouterDecision(kind="enqueue", team="verification")
        handover = _handover(task_id=ws.task_id, files_to_edit=["alan/x.py"])
        out = d._maybe_populate_keep_manifest(  # noqa: SLF001
            ws, handover, decision, "root"
        )
        assert out is ws

    def test_empty_files_to_edit_skips(self, root: Path, queue: Queue) -> None:
        ws = _ws(root, [_task("root")])
        d = Dispatcher(queue, {}, workspaces_root=root)
        decision = RouterDecision(kind="enqueue", team="implementation")
        handover = _handover(task_id=ws.task_id, files_to_edit=[])
        out = d._maybe_populate_keep_manifest(  # noqa: SLF001
            ws, handover, decision, "root"
        )
        assert out is ws

    def test_unknown_task_id_skips(self, root: Path, queue: Queue) -> None:
        ws = _ws(root, [_task("root")])
        d = Dispatcher(queue, {}, workspaces_root=root)
        decision = RouterDecision(kind="enqueue", team="implementation")
        handover = _handover(
            task_id=ws.task_id, from_team="decomposer", files_to_edit=["alan/x.py"]
        )
        out = d._maybe_populate_keep_manifest(  # noqa: SLF001
            ws, handover, decision, "missing"
        )
        assert out is ws

    def test_non_decomposer_from_team_skips(self, root: Path, queue: Queue) -> None:
        ws = _ws(root, [_task("root")])
        d = Dispatcher(queue, {}, workspaces_root=root)
        decision = RouterDecision(kind="enqueue", team="implementation")
        handover = _handover(
            task_id=ws.task_id, from_team="pr_merge", files_to_edit=["alan/x.py"]
        )
        out = d._maybe_populate_keep_manifest(  # noqa: SLF001
            ws, handover, decision, "root"
        )
        assert out is ws

    def test_populates_and_persists_normalized_files(
        self, root: Path, queue: Queue
    ) -> None:
        ws = _ws(root, [_task("root")])
        d = Dispatcher(queue, {}, workspaces_root=root)
        decision = RouterDecision(kind="enqueue", team="implementation")
        handover = _handover(
            task_id=ws.task_id,
            from_team="decomposer",
            files_to_edit=["alan/x.py", "alan/x.py", "alan/y.py "],
        )
        out = d._maybe_populate_keep_manifest(  # noqa: SLF001
            ws, handover, decision, "root"
        )
        assert next(t.files for t in out.meta.tasks if t.id == "root") == [
            "alan/x.py",
            "alan/y.py",
        ]
        reloaded = workspace_mod.read(ws.task_id, root=root)
        assert next(t.files for t in reloaded.meta.tasks if t.id == "root") == [
            "alan/x.py",
            "alan/y.py",
        ]


class TestAuditSiblingOverlap:
    def _split_ws(self, root: Path) -> Workspace:
        return _ws(
            root,
            [
                _task("t1", parent="root", files=["alan/a.py"]),
                _task("t2", parent="root", files=["alan/b.py"]),
            ],
        )

    def _dir_manifest_ws(self, root: Path) -> Workspace:
        return _ws(
            root,
            [
                _task("t1", parent="root", files=["alan/a.py"]),
                _task("t2", parent="root", files=["alan/dir"]),
            ],
        )

    def test_non_terminal_handover_skips(
        self, root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ws = self._split_ws(root)
        d = Dispatcher(queue, {}, workspaces_root=root)
        called = _spy_fetch_diff_files(monkeypatch, frozenset({"alan/b.py"}))
        handover = _handover(
            task_id=ws.task_id, handover_type="forward", from_team="pr_merge"
        )
        d._audit_sibling_overlap(ws, handover, "t1")  # noqa: SLF001
        assert called == []

    def test_non_pr_merge_from_team_skips(
        self, root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ws = self._split_ws(root)
        d = Dispatcher(queue, {}, workspaces_root=root)
        called = _spy_fetch_diff_files(monkeypatch, frozenset({"alan/b.py"}))
        handover = _handover(
            task_id=ws.task_id, handover_type="terminal", from_team="verification"
        )
        d._audit_sibling_overlap(ws, handover, "t1")  # noqa: SLF001
        assert called == []

    def test_missing_pr_number_skips(
        self, root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ws = self._split_ws(root)
        d = Dispatcher(queue, {}, workspaces_root=root)
        called = _spy_fetch_diff_files(monkeypatch, frozenset({"alan/b.py"}))
        handover = _handover(
            task_id=ws.task_id,
            handover_type="terminal",
            from_team="pr_merge",
            pr_number=None,
        )
        d._audit_sibling_overlap(ws, handover, "t1")  # noqa: SLF001
        assert called == []

    def test_no_siblings_skips(
        self, root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ws = _ws(root, [_task("root")])
        d = Dispatcher(queue, {}, workspaces_root=root)
        called = _spy_fetch_diff_files(monkeypatch, frozenset({"alan/b.py"}))
        handover = _handover(
            task_id=ws.task_id, handover_type="terminal", from_team="pr_merge"
        )
        d._audit_sibling_overlap(ws, handover, "root")  # noqa: SLF001
        assert called == []

    def test_diff_fetch_failure_skips(
        self, root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ws = self._split_ws(root)
        d = Dispatcher(queue, {}, workspaces_root=root)
        monkeypatch.setattr(_pr_branch, "fetch_diff_files", lambda *_a, **_kw: None)
        events, collect = _make_collector()
        d.pubsub.subscribe(ws.task_id, collect)
        handover = _handover(
            task_id=ws.task_id, handover_type="terminal", from_team="pr_merge"
        )
        d._audit_sibling_overlap(ws, handover, "t1")  # noqa: SLF001
        assert events == []

    def test_no_overlap_skips(
        self, root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        logs = tmp_path / "logs"
        monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs))
        ws = self._split_ws(root)
        d = Dispatcher(queue, {}, workspaces_root=root)
        _spy_fetch_diff_files(monkeypatch, frozenset({"alan/a.py"}))
        events, collect = _make_collector()
        d.pubsub.subscribe(ws.task_id, collect)
        handover = _handover(
            task_id=ws.task_id, handover_type="terminal", from_team="pr_merge"
        )
        d._audit_sibling_overlap(ws, handover, "t1")  # noqa: SLF001
        assert events == []
        assert not (logs / _pr_audit_log.AUDIT_FILE).exists()

    def test_overlap_emits_event_and_audit_row(
        self, root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        logs = tmp_path / "logs"
        monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs))
        ws = self._split_ws(root)
        d = Dispatcher(queue, {}, workspaces_root=root)
        _spy_fetch_diff_files(monkeypatch, frozenset({"alan/b.py"}))
        events, collect = _make_collector()
        d.pubsub.subscribe(ws.task_id, collect)
        handover = _handover(
            task_id=ws.task_id, handover_type="terminal", from_team="pr_merge"
        )
        d._audit_sibling_overlap(ws, handover, "t1")  # noqa: SLF001
        assert len(events) == 1
        event = events[0]
        assert event.kind == "sibling_overlap_detected"
        assert event.payload["task_id"] == "t1"
        assert event.payload["pr_number"] == str(_PR_NUMBER)
        assert "alan/b.py" in event.payload["overlaps"]
        audit = (logs / _pr_audit_log.AUDIT_FILE).read_text(encoding="utf-8")
        assert '"task_id": "t1"' in audit
        assert '"pr_number": 777' in audit
        assert "alan/b.py" in audit

    def test_overlap_hits_are_deterministically_sorted(
        self, root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        logs = tmp_path / "logs"
        monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs))
        ws = self._dir_manifest_ws(root)
        d = Dispatcher(queue, {}, workspaces_root=root)
        _spy_fetch_diff_files(
            monkeypatch, frozenset({"alan/dir/z.py", "alan/dir/a.py"})
        )
        events, collect = _make_collector()
        d.pubsub.subscribe(ws.task_id, collect)
        handover = _handover(
            task_id=ws.task_id, handover_type="terminal", from_team="pr_merge"
        )
        d._audit_sibling_overlap(ws, handover, "t1")  # noqa: SLF001
        assert len(events) == 1
        assert events[0].payload["overlaps"] == (
            '{"t2": ["alan/dir/a.py", "alan/dir/z.py"]}'
        )

    def test_sibling_overlap_reads_diff_from_non_default_engine_target(
        self, root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
        second_engine = RepoIdentity(owner="DejvMatikar", repo="example-repo")
        engine_config.register(
            EngineConfig(
                repo=second_engine,
                forge=ForgeConfig(
                    repo=second_engine, credential_env_name="SECOND_ENGINE_GH_TOKEN"
                ),
            ),
            validate_env=False,
        )
        ws = workspace_mod.create(
            "example-repo-ws", "desc", root=root, engine_key="DejvMatikar/example-repo",
            tasks=[
                _task("t1", parent="root", files=["a.py"]),
                _task("t2", parent="root", files=["b.py"]),
            ],
        )
        d = Dispatcher(queue, {}, workspaces_root=root)
        called = _spy_fetch_diff_files(monkeypatch, frozenset({"b.py"}))
        handover = _handover(
            task_id=ws.task_id, handover_type="terminal", from_team="pr_merge"
        )
        d._audit_sibling_overlap(ws, handover, "t1")  # noqa: SLF001
        second_engine_target = forge_target_for_workspace("DejvMatikar/example-repo", None)
        assert second_engine_target.repo.slug == "DejvMatikar/example-repo"
        assert called == [((_PR_NUMBER,), {"forge_target": second_engine_target})]

    def test_sibling_overlap_reads_diff_from_default_engine_target(
        self, root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
        ws = self._dir_manifest_ws(root)
        d = Dispatcher(queue, {}, workspaces_root=root)
        called = _spy_fetch_diff_files(monkeypatch, frozenset({"alan/dir/a.py"}))
        handover = _handover(
            task_id=ws.task_id, handover_type="terminal", from_team="pr_merge"
        )
        d._audit_sibling_overlap(ws, handover, "t1")  # noqa: SLF001
        assert called == [((_PR_NUMBER,), {"forge_target": DEFAULT_FORGE_TARGET})]
        assert DEFAULT_FORGE_TARGET.repo.slug == "DejvMatikar/alan"


def _spy_fetch_diff_files(
    monkeypatch: pytest.MonkeyPatch, result: frozenset[str] | None
) -> list[object]:
    calls: list[object] = []

    def _fake(*args: object, **kwargs: object) -> frozenset[str] | None:
        calls.append((args, kwargs))
        return result

    monkeypatch.setattr(_pr_branch, "fetch_diff_files", _fake)
    return calls


def _make_collector() -> tuple[list[TeamEvent], Callable[[TeamEvent], None]]:
    events: list[TeamEvent] = []
    return events, events.append
