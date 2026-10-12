from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from alan import workflow as workflow_mod
from alan.claude_cli import TurnCeilingReached
from alan.handover import Decision, Handover
from alan.teams import _pr_handover as ph
from alan.teams._base import WorkerOverrides
from alan.workspace import Task, TaskDoDClause, TaskStatus, Workspace, create
from alan.worktree import WorktreeHandle


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    for cmd in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(cmd, cwd=str(repo), check=True, capture_output=True)  # noqa: S603
    (repo / "README.md").write_text("seed\n")
    subprocess.run(  # noqa: S603,S607
        ["git", "add", "README.md"], cwd=str(repo), check=True, capture_output=True
    )
    subprocess.run(  # noqa: S603,S607
        ["git", "commit", "-q", "-m", "initial"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


@pytest.fixture
def workspace(root: Path) -> Workspace:
    return create("pr-handover-test", "desc", root=root)


def _noop_emit(event: Any) -> None:
    pass


def test_run_or_resume_delegates_to_base_with_pending_node(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams import _pr_handover as ph

    calls: list[dict[str, object]] = []

    def _fake(*_a: object, **kw: object) -> str:
        calls.append(kw)
        return "delegated output"

    monkeypatch.setattr(ph, "run_or_resume_node", _fake)

    inv_id = workflow_mod.seed_pending_workflow(
        workspace,
        team="implementation",
        tasks=[workflow_mod.PendingTaskSpec(id="impl", role="impl", model="sonnet")],
    )
    graph = workflow_mod.load_graph(workspace, inv_id)

    result = ph.run_or_resume(
        workspace,
        graph,
        inv_id,
        _noop_emit,
        node_id="impl",
        role="impl",
        model="sonnet",
        effort="xhigh",
        system_prompt="sys",
        user_text="txt",
        overrides=WorkerOverrides(),
        resume_inv_id=None,
    )

    assert result == "delegated output"
    assert len(calls) == 1
    kw = calls[0]
    assert kw["node_id"] == "impl"
    assert kw["role"] == "impl"
    assert kw["model"] == "sonnet"
    assert kw["effort"] == "xhigh"
    assert kw["depends_on"] == []
    assert kw["system_prompt"] == "sys"
    assert kw["user_text"] == "txt"
    assert kw.get("task_id") is None


def test_run_or_resume_delegates_to_base_with_interrupted_node(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams import _pr_handover as ph

    calls: list[dict[str, object]] = []

    def _fake(*_a: object, **kw: object) -> str:
        calls.append(kw)
        return "delegated output"

    monkeypatch.setattr(ph, "run_or_resume_node", _fake)

    inv_id = workflow_mod.seed_pending_workflow(
        workspace,
        team="implementation",
        tasks=[workflow_mod.PendingTaskSpec(id="impl", role="impl", model="sonnet")],
    )
    graph = workflow_mod.load_graph(workspace, inv_id)
    graph.start_node("impl", role="impl", model="sonnet")
    graph.set_session_id("impl", "sid-abc")
    graph.mark_interrupted("impl", error="restart")
    workflow_mod.save_workflow(workspace, graph.doc)
    graph = workflow_mod.load_graph(workspace, inv_id)

    result = ph.run_or_resume(
        workspace,
        graph,
        inv_id,
        _noop_emit,
        node_id="impl",
        role="impl",
        model="sonnet",
        system_prompt="sys",
        user_text="txt",
        overrides=WorkerOverrides(),
        resume_inv_id="inv_001",
    )

    assert result == "delegated output"
    assert len(calls) == 1
    assert calls[0]["node_id"] == "impl"
    assert calls[0].get("task_id") is None


def test_worktree_iteration_sets_base_ref_sha_on_first_use(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from alan.teams import _pr_handover as ph
    from alan.worktree import WorktreeHandle

    fake_sha = "b" * 40
    fake_handle = WorktreeHandle(
        path=tmp_path / "slot", base_ref=fake_sha, branch="teams/t-inv_001",
        slot_index=0, preserved_commits=0, rescued_ref=None,
    )

    inv_id = workflow_mod.seed_pending_workflow(
        workspace,
        team="implementation",
        tasks=[workflow_mod.PendingTaskSpec(id="impl", role="impl", model="sonnet")],
    )

    saved_docs: list[Any] = []

    def fake_save(ws: Any, doc: Any) -> None:
        saved_docs.append(doc.base_ref_sha)

    monkeypatch.setattr(workflow_mod, "save_workflow", fake_save)

    from contextlib import contextmanager

    @contextmanager
    def fake_worktree(*args: Any, **kwargs: Any):  # type: ignore[no-untyped-def]
        yield fake_handle

    monkeypatch.setattr(ph, "implementation_worktree", fake_worktree)

    @contextmanager
    def fake_lifecycle(ws: Any, *, team_name: Any, inv_id: Any, emit: Any):  # type: ignore[no-untyped-def]
        graph = workflow_mod.load_graph(workspace, inv_id)
        graph.set_slot_index(0)
        yield graph

    monkeypatch.setattr(ph, "workflow_lifecycle", fake_lifecycle)

    emitted: list[Any] = []
    with ph.worktree_iteration(
        workspace, "implementation", inv_id, emitted.append, existing_branch=None
    ) as (graph, handle):
        pass

    assert fake_sha in saved_docs


def test_worktree_iteration_emits_slot_work_preserved_event(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from alan.teams import _pr_handover as ph
    from alan.worktree import WorktreeHandle

    fake_handle = WorktreeHandle(
        path=tmp_path / "slot", base_ref="origin/main", branch="teams/t-inv_001",
        slot_index=0, preserved_commits=3, rescued_ref=None,
    )

    inv_id = workflow_mod.seed_pending_workflow(
        workspace,
        team="implementation",
        tasks=[workflow_mod.PendingTaskSpec(id="impl", role="impl", model="sonnet")],
    )

    monkeypatch.setattr(workflow_mod, "save_workflow", lambda ws, doc: None)
    monkeypatch.setattr(ph, "_node_turns_audit_dir", lambda: tmp_path)
    monkeypatch.setattr(ph, "append_jsonl_audit", lambda *a, **kw: None)

    from contextlib import contextmanager

    @contextmanager
    def fake_worktree(*args: Any, **kwargs: Any):  # type: ignore[no-untyped-def]
        yield fake_handle

    monkeypatch.setattr(ph, "implementation_worktree", fake_worktree)

    @contextmanager
    def fake_lifecycle(ws: Any, *, team_name: Any, inv_id: Any, emit: Any):  # type: ignore[no-untyped-def]
        graph = workflow_mod.load_graph(workspace, inv_id)
        graph.set_slot_index(0)
        yield graph

    monkeypatch.setattr(ph, "workflow_lifecycle", fake_lifecycle)

    events: list[Any] = []
    with ph.worktree_iteration(
        workspace, "implementation", inv_id, events.append, existing_branch=None
    ) as _pair:
        pass

    event_kinds = [e.kind for e in events]
    assert "slot_work_preserved" in event_kinds
    preserved_evt = next(e for e in events if e.kind == "slot_work_preserved")
    assert preserved_evt.payload["n_commits"] == "3"


def test_worktree_iteration_emits_slot_work_rescued_event(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from alan.teams import _pr_handover as ph
    from alan.worktree import WorktreeHandle

    fake_handle = WorktreeHandle(
        path=tmp_path / "slot", base_ref="fb_base", branch="teams/t-inv_001",
        slot_index=0, preserved_commits=0, rescued_ref="rescue/t@inv_001",
    )

    inv_id = workflow_mod.seed_pending_workflow(
        workspace,
        team="implementation",
        tasks=[workflow_mod.PendingTaskSpec(id="impl", role="impl", model="sonnet")],
    )

    monkeypatch.setattr(workflow_mod, "save_workflow", lambda ws, doc: None)
    monkeypatch.setattr(ph, "_node_turns_audit_dir", lambda: tmp_path)
    monkeypatch.setattr(ph, "append_jsonl_audit", lambda *a, **kw: None)

    from contextlib import contextmanager

    @contextmanager
    def fake_worktree(*args: Any, **kwargs: Any):  # type: ignore[no-untyped-def]
        yield fake_handle

    monkeypatch.setattr(ph, "implementation_worktree", fake_worktree)

    @contextmanager
    def fake_lifecycle(ws: Any, *, team_name: Any, inv_id: Any, emit: Any):  # type: ignore[no-untyped-def]
        graph = workflow_mod.load_graph(workspace, inv_id)
        graph.set_slot_index(0)
        yield graph

    monkeypatch.setattr(ph, "workflow_lifecycle", fake_lifecycle)

    events: list[Any] = []
    with ph.worktree_iteration(
        workspace, "implementation", inv_id, events.append, existing_branch=None
    ) as _pair:
        pass

    event_kinds = [e.kind for e in events]
    assert "slot_work_rescued" in event_kinds
    rescued_evt = next(e for e in events if e.kind == "slot_work_rescued")
    assert rescued_evt.payload["rescue_ref"] == "rescue/t@inv_001"


def test_worktree_iteration_updates_base_ref_sha_on_rescue(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from alan.teams import _pr_handover as ph
    from alan.worktree import WorktreeHandle

    fake_handle = WorktreeHandle(
        path=tmp_path / "slot", base_ref="fresh_base", branch="teams/t-inv_001",
        slot_index=0, preserved_commits=0, rescued_ref="rescue/t@inv_001",
    )

    inv_id = workflow_mod.seed_pending_workflow(
        workspace,
        team="implementation",
        tasks=[workflow_mod.PendingTaskSpec(id="impl", role="impl", model="sonnet")],
    )

    saved_shas: list[Any] = []

    def fake_save(ws: Any, doc: Any) -> None:
        saved_shas.append(doc.base_ref_sha)

    monkeypatch.setattr(workflow_mod, "save_workflow", fake_save)
    monkeypatch.setattr(ph, "_node_turns_audit_dir", lambda: tmp_path)
    monkeypatch.setattr(ph, "append_jsonl_audit", lambda *a, **kw: None)

    from contextlib import contextmanager

    @contextmanager
    def fake_worktree(*args: Any, **kwargs: Any):  # type: ignore[no-untyped-def]
        yield fake_handle

    monkeypatch.setattr(ph, "implementation_worktree", fake_worktree)

    @contextmanager
    def fake_lifecycle(ws: Any, *, team_name: Any, inv_id: Any, emit: Any):  # type: ignore[no-untyped-def]
        graph = workflow_mod.load_graph(workspace, inv_id)
        graph.set_slot_index(0)
        graph.set_base_ref_sha("old_sha")
        yield graph

    monkeypatch.setattr(ph, "workflow_lifecycle", fake_lifecycle)

    with ph.worktree_iteration(
        workspace, "implementation", inv_id, _noop_emit, existing_branch=None
    ) as _pair:
        pass

    assert "fresh_base" in saved_shas


def test_worktree_iteration_does_not_overwrite_base_ref_sha_on_normal_resume(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from alan.teams import _pr_handover as ph
    from alan.worktree import WorktreeHandle

    fake_handle = WorktreeHandle(
        path=tmp_path / "slot", base_ref="origin/main", branch="teams/t-inv_001",
        slot_index=0, preserved_commits=2, rescued_ref=None,
    )

    inv_id = workflow_mod.seed_pending_workflow(
        workspace,
        team="implementation",
        tasks=[workflow_mod.PendingTaskSpec(id="impl", role="impl", model="sonnet")],
    )

    saved_shas: list[Any] = []

    def fake_save(ws: Any, doc: Any) -> None:
        saved_shas.append(doc.base_ref_sha)

    monkeypatch.setattr(workflow_mod, "save_workflow", fake_save)
    monkeypatch.setattr(ph, "_node_turns_audit_dir", lambda: tmp_path)
    monkeypatch.setattr(ph, "append_jsonl_audit", lambda *a, **kw: None)

    from contextlib import contextmanager

    @contextmanager
    def fake_worktree(*args: Any, **kwargs: Any):  # type: ignore[no-untyped-def]
        yield fake_handle

    monkeypatch.setattr(ph, "implementation_worktree", fake_worktree)

    @contextmanager
    def fake_lifecycle(ws: Any, *, team_name: Any, inv_id: Any, emit: Any):  # type: ignore[no-untyped-def]
        graph = workflow_mod.load_graph(workspace, inv_id)
        graph.set_slot_index(0)
        graph.set_base_ref_sha("stored_sha")
        yield graph

    monkeypatch.setattr(ph, "workflow_lifecycle", fake_lifecycle)

    with ph.worktree_iteration(
        workspace, "implementation", inv_id, _noop_emit, existing_branch=None
    ) as _pair:
        pass

    assert "origin/main" not in saved_shas
    assert "stored_sha" in saved_shas


def test_run_or_resume_resume_inv_id_does_not_leak_into_delegation(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alan.teams import _pr_handover as ph

    calls: list[dict[str, object]] = []

    def _fake(*_a: object, **kw: object) -> str:
        calls.append(kw)
        return "delegated output"

    monkeypatch.setattr(ph, "run_or_resume_node", _fake)

    inv_id = workflow_mod.seed_pending_workflow(
        workspace,
        team="implementation",
        tasks=[workflow_mod.PendingTaskSpec(id="impl", role="impl", model="sonnet")],
    )
    graph = workflow_mod.load_graph(workspace, inv_id)

    result = ph.run_or_resume(
        workspace,
        graph,
        inv_id,
        _noop_emit,
        node_id="impl",
        role="impl",
        model="sonnet",
        system_prompt="sys",
        user_text="txt",
        overrides=WorkerOverrides(),
        resume_inv_id="inv_001",
    )

    assert result == "delegated output"
    assert len(calls) == 1
    assert "resume_inv_id" not in calls[0]


def _task(**kw: Any) -> Task:
    base: dict[str, Any] = {"id": "sub-1", "status": TaskStatus.RUNNING}
    base.update(kw)
    return Task(**base)


def test_your_task_block_renders_title_instructions_and_dod() -> None:
    block = ph.your_task_block(
        _task(
            title="Notice",
            instructions="edit workspaceNotices.ts",
            dod_clauses=[TaskDoDClause(text="renders an error notice")],
        )
    )
    assert "## Your task: Notice" in block
    assert "### Instructions\nedit workspaceNotices.ts" in block
    assert "### Definition of Done" in block
    assert "renders an error notice" in block


def test_your_task_block_instructions_only_omits_dod() -> None:
    block = ph.your_task_block(_task(instructions="do x"))
    assert "### Instructions" in block
    assert "### Definition of Done" not in block


def test_your_task_block_dod_only_omits_instructions() -> None:
    block = ph.your_task_block(_task(dod_clauses=[TaskDoDClause(text="clause")]))
    assert "### Definition of Done" in block
    assert "### Instructions" not in block


def test_your_task_block_empty_when_no_instructions_or_dod() -> None:
    assert ph.your_task_block(_task(instructions=None)) == ""


def test_your_task_block_without_title_uses_bare_header() -> None:
    block = ph.your_task_block(_task(title=None, instructions="do x"))
    assert "## Your task\n" in block
    assert "## Your task:" not in block


def test_your_task_block_none_returns_empty() -> None:
    assert ph.your_task_block(None) == ""


def test_worker_prompt_shows_declared_files() -> None:
    block = ph.your_task_block(
        _task(instructions="do x", files=["alan/x.py", "alan/web/y.tsx"])
    )
    assert "### Your files\n- alan/x.py\n- alan/web/y.tsx" in block


def test_your_task_block_omits_files_section_when_empty() -> None:
    block = ph.your_task_block(_task(instructions="do x"))
    assert "### Your files" not in block


def test_your_task_block_renders_with_only_files() -> None:
    block = ph.your_task_block(_task(instructions=None, files=["alan/x.py"]))
    assert "## Your task" in block
    assert "### Your files\n- alan/x.py" in block
    assert "### Instructions" not in block


def test_your_task_block_strips_and_omits_blank_file_entries() -> None:
    block = ph.your_task_block(
        _task(instructions="do x", files=["  alan/x.py  ", "   ", ""])
    )
    assert "### Your files\n- alan/x.py" in block
    assert "-  " not in block
    assert "- \n" not in block


def test_your_task_block_omits_files_section_when_all_entries_blank() -> None:
    block = ph.your_task_block(_task(instructions="do x", files=["   ", ""]))
    assert "### Your files" not in block


def test_lookup_task_none_id_returns_none(workspace: Workspace) -> None:
    assert ph.lookup_task(workspace, None) is None


def test_lookup_task_non_matching_returns_none(workspace: Workspace) -> None:
    workspace.meta.tasks.append(_task())
    assert ph.lookup_task(workspace, "nope") is None


def test_lookup_task_matching_returns_task(workspace: Workspace) -> None:
    workspace.meta.tasks.append(_task(title="Found"))
    found = ph.lookup_task(workspace, "sub-1")
    assert found is not None
    assert found.title == "Found"


def test_compose_user_text_current_task_carries_dispatch_instructions_as_addendum(
    workspace: Workspace,
) -> None:
    workspace.meta.tasks.append(_task(instructions="real subtask body"))
    text = ph.compose_user_text(
        workspace, None,
        mid_blocks=lambda _p: [],
        config=_TEST_TEAM_CONFIG,
        instructions="manual addendum",
        current_task=ph.lookup_task(workspace, "sub-1"),
    )
    assert "## Your task" in text
    assert "real subtask body" in text
    assert "manual addendum" in text
    assert text.index("real subtask body") < text.index("manual addendum")
    assert "this section wins" in text


def test_compose_user_text_omits_precedence_notice_without_a_task_block(
    workspace: Workspace,
) -> None:
    text = ph.compose_user_text(
        workspace, None,
        mid_blocks=lambda _p: [],
        config=_TEST_TEAM_CONFIG,
        instructions="manual addendum",
        current_task=None,
    )
    assert "manual addendum" in text
    assert "this section wins" not in text


def test_compose_user_text_omits_addendum_when_instructions_empty(
    workspace: Workspace,
) -> None:
    workspace.meta.tasks.append(_task(instructions="real subtask body"))
    text = ph.compose_user_text(
        workspace, None,
        mid_blocks=lambda _p: [],
        config=_TEST_TEAM_CONFIG,
        instructions="   ",
        current_task=ph.lookup_task(workspace, "sub-1"),
    )
    assert "## Your task" in text
    assert "## User dispatch instructions" not in text
    assert "this section wins" not in text


def test_compose_user_text_dispatch_instructions_when_no_current_task(
    workspace: Workspace,
) -> None:
    text = ph.compose_user_text(
        workspace, None,
        mid_blocks=lambda _p: [],
        config=_TEST_TEAM_CONFIG,
        instructions="manual addendum",
        current_task=None,
    )
    assert "manual addendum" in text
    assert "## Your task" not in text


def _prior_handover(workspace: Workspace, **kwargs: object) -> Handover:
    base: dict[str, object] = {
        "handover_id": f"handover_{workspace.task_id}_001",
        "handover_type": "forward",
        "task_id": workspace.task_id,
        "from_team": "implementation_plan",
        "from_invocation_id": "inv_001",
        "to_team_suggestions": ["implementation"],
        "iteration": 1,
        "timestamp_utc": datetime.now(UTC),
        "decisions": [
            Decision(
                id="d1", statement="x", rationale="y",
                confidence=0.9, reversibility="medium",
            )
        ],
        "full_synthesis": "ready",
    }
    base.update(kwargs)
    return Handover.model_validate(base)


def test_compose_user_text_authorization_block_rendered_when_flag_set(
    workspace: Workspace,
) -> None:
    prior = _prior_handover(workspace, dependency_bot_authorized=True)
    text = ph.compose_user_text(
        workspace, prior,
        mid_blocks=lambda _p: [],
        config=_TEST_TEAM_CONFIG,
    )
    assert "## Authorization" in text
    assert "pre-authorized" in text


def test_compose_user_text_authorization_block_omitted_when_flag_unset(
    workspace: Workspace,
) -> None:
    prior = _prior_handover(workspace, dependency_bot_authorized=False)
    text = ph.compose_user_text(
        workspace, prior,
        mid_blocks=lambda _p: [],
        config=_TEST_TEAM_CONFIG,
    )
    assert "## Authorization" not in text


def test_compose_user_text_authorization_block_no_team_specific_instruction(
    workspace: Workspace,
) -> None:
    prior = _prior_handover(workspace, dependency_bot_authorized=True)
    text = ph.compose_user_text(
        workspace, prior,
        mid_blocks=lambda _p: [],
        config=_TEST_TEAM_CONFIG,
    )
    assert "Proceed directly to implementation" not in text


_TEST_TEAM_CONFIG = ph.TeamConfig(
    team_name="implementation",
    default_forbidden=(),
    next_team=("verification",),
    decision_prefix="I",
)


def test_build_handover_propagates_dependency_bot_authorized(
    workspace: Workspace,
) -> None:
    prior = _prior_handover(workspace, dependency_bot_authorized=True)
    result = ph.build_handover(
        workspace,
        prior,
        "inv_002",
        "synthesis text",
        ["abc1234 add something"],
        42,
        _TEST_TEAM_CONFIG,
    )
    assert result.dependency_bot_authorized is True


def test_build_handover_propagates_dependency_bot_authorized_via_escalation(
    workspace: Workspace,
) -> None:
    prior = _prior_handover(workspace, dependency_bot_authorized=True)
    result = ph.build_handover(
        workspace,
        prior,
        "inv_002",
        "synthesis text",
        [],
        None,
        _TEST_TEAM_CONFIG,
    )
    assert result.dependency_bot_authorized is True


def test_salvage_uncommitted_wip_returns_clean_on_clean_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    handle = WorktreeHandle(path=repo, base_ref="HEAD", branch="teams/t-inv_001")
    monkeypatch.setattr(ph, "_node_turns_audit_dir", lambda: tmp_path / "logs")

    assert ph.salvage_uncommitted_wip(handle, "task-1", "implementation") == "clean"
    assert not (tmp_path / "logs" / "impl_audit.jsonl").exists()


def test_salvage_uncommitted_wip_commits_under_alan_salvage_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "untracked.txt").write_text("left behind\n")
    handle = WorktreeHandle(path=repo, base_ref="HEAD", branch="teams/t-inv_001")
    monkeypatch.setattr(ph, "_node_turns_audit_dir", lambda: tmp_path / "logs")

    outcome = ph.salvage_uncommitted_wip(handle, "task-1", "implementation")
    assert outcome == "salvaged"

    show = subprocess.run(  # noqa: S603,S607
        ["git", "show", "-s", "--format=%an%x09%ae%x09%cn%x09%ce%x09%B", "HEAD"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    author_name, author_email, committer_name, committer_email, body = show.split(
        "\t", 4
    )
    assert author_name == "alan-salvage"
    assert committer_name == "alan-salvage"
    assert author_email == "alan-salvage@users.noreply.github.com"
    assert committer_email == "alan-salvage@users.noreply.github.com"
    assert "Salvaged-by: alan-salvage" in body
    assert body.startswith("chore: salvage uncommitted implementation output")

    entries = [
        json.loads(line)
        for line in (tmp_path / "logs" / "impl_audit.jsonl").read_text().splitlines()
    ]
    assert any(
        e["action"] == "wip_salvage_commit" and e["ok"] is True and e["team"] == "implementation"
        for e in entries
    )


def test_salvage_uncommitted_wip_reports_failure_on_subprocess_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "untracked.txt").write_text("x\n")
    handle = WorktreeHandle(path=repo, base_ref="HEAD", branch="teams/t-inv_001")
    monkeypatch.setattr(ph, "_node_turns_audit_dir", lambda: tmp_path / "logs")

    real_run = subprocess.run

    def fake_run(cmd: Any, **kwargs: Any) -> Any:
        if cmd[:2] == ["git", "add"]:
            raise subprocess.TimeoutExpired(cmd, 30)
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)

    outcome = ph.salvage_uncommitted_wip(handle, "task-1", "implementation")
    assert outcome == "failed"
    entries = [
        json.loads(line)
        for line in (tmp_path / "logs" / "impl_audit.jsonl").read_text().splitlines()
    ]
    assert any(e["action"] == "wip_salvage_commit" and e["ok"] is False for e in entries)


def test_audit_ceiling_salvage_writes_expected_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    handle = WorktreeHandle(path=tmp_path / "wt", base_ref="HEAD", branch="teams/t-inv_001")
    monkeypatch.setattr(ph, "_node_turns_audit_dir", lambda: tmp_path / "logs")

    ph.audit_ceiling_salvage("task-1", "implementation", handle, ["abc123 fix: x"])

    entries = [
        json.loads(line)
        for line in (tmp_path / "logs" / "impl_audit.jsonl").read_text().splitlines()
    ]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["task_id"] == "task-1"
    assert entry["action"] == "ceiling_salvage"
    assert entry["team"] == "implementation"
    assert entry["branch"] == "teams/t-inv_001"
    assert entry["commit_count"] == 1
    assert entry["has_commits"] is True


def test_ceiling_salvage_synthesis_lists_commits() -> None:
    text = ph.ceiling_salvage_synthesis(
        "implementation", ["abc123 fix: x", "def456 fix: y"], salvaged="salvaged"
    )
    assert "implementation exhausted its turn budget" in text
    assert "- abc123 fix: x" in text
    assert "- def456 fix: y" in text
    assert "alan-salvage" in text


def test_ceiling_salvage_synthesis_omits_host_committed_claim_when_clean() -> None:
    text = ph.ceiling_salvage_synthesis(
        "implementation", ["abc123 fix: x"], salvaged="clean"
    )
    assert "host recovery path committed the work" not in text
    assert "worker's own commits" in text
    assert "- abc123 fix: x" in text


def test_ceiling_salvage_synthesis_reports_lost_diff_when_salvage_failed() -> None:
    text = ph.ceiling_salvage_synthesis(
        "implementation", ["abc123 fix: x"], salvaged="failed"
    )
    assert "host recovery path committed the work" not in text
    assert "worker's own commits" not in text
    assert "NOT recovered" in text
    assert "- abc123 fix: x" in text


def test_ceiling_salvage_synthesis_requires_salvaged_keyword() -> None:
    with pytest.raises(TypeError):
        ph.ceiling_salvage_synthesis("implementation", ["abc123 fix: x"])  # type: ignore[call-arg]


def test_with_salvage_notice_appends_notice_when_salvaged() -> None:
    result = ph.with_salvage_notice("## Summary\nDone.", "salvaged")
    assert result.startswith("## Summary\nDone.")
    assert "alan-salvage identity" in result
    assert "must be re-run" in result


def test_with_salvage_notice_appends_failure_notice_when_salvage_failed() -> None:
    result = ph.with_salvage_notice("## Summary\nDone.", "failed")
    assert result.startswith("## Summary\nDone.")
    assert "NOT recovered" in result
    assert "alan-salvage identity" not in result


def test_with_salvage_notice_returns_same_object_when_clean() -> None:
    synthesis = "## Summary\nDone."
    assert ph.with_salvage_notice(synthesis, "clean") is synthesis


def test_salvage_notice_append_leaves_fenced_block_parsers_intact() -> None:
    from alan.teams import _fix_forward, _pr_body, _scenario_block
    from alan.teams.implementation_fix import _parse_non_actionable_reason

    synthesis = (
        "## Summary\nFix landed.\n\n"
        "```pr_body\nThe PR body.\n```\n\n"
        "```yaml non_actionable_feedback.yaml\n"
        "reason: CI flake unrelated to this PR\n"
        "```\n\n"
        "```yaml test_scenarios.yaml\n"
        "- id: s1\n"
        '  description: "d"\n'
        '  given: "g"\n'
        '  when: "w"\n'
        '  then: "t"\n'
        "```"
    )
    noticed = ph.with_salvage_notice(synthesis, "salvaged")

    assert _pr_body.extract_authored_body(noticed) == "The PR body."
    assert _parse_non_actionable_reason(noticed) == "CI flake unrelated to this PR"
    scenarios = _fix_forward.parse_fix_scenarios(noticed)
    assert [s.id for s in scenarios] == ["s1"]
    assert (
        _scenario_block.unparsed_audit_row(
            noticed, scenarios, task_id="t1", team="implementation_fix"
        )
        is None
    )


def test_salvage_on_ceiling_reraises_when_tree_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    handle = WorktreeHandle(path=repo, base_ref="HEAD", branch="teams/t-inv_001")
    monkeypatch.setattr(ph, "_node_turns_audit_dir", lambda: tmp_path / "logs")

    try:
        raise TurnCeilingReached("ceiling")
    except TurnCeilingReached as exc:
        with pytest.raises(TurnCeilingReached):
            ph.salvage_on_ceiling(handle, "task-1", "implementation", exc)

    entries = [
        json.loads(line)
        for line in (tmp_path / "logs" / "impl_audit.jsonl").read_text().splitlines()
    ]
    assert any(
        e["action"] == "ceiling_salvage" and e["commit_count"] == 0
        and e["has_commits"] is False
        for e in entries
    )


def test_salvage_on_ceiling_returns_synthesis_and_commits_when_dirty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    base_sha = subprocess.run(  # noqa: S603,S607
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    (repo / "wip.py").write_text("x = 1\n")
    handle = WorktreeHandle(path=repo, base_ref=base_sha, branch="teams/t-inv_001")
    monkeypatch.setattr(ph, "_node_turns_audit_dir", lambda: tmp_path / "logs")

    try:
        raise TurnCeilingReached("ceiling")
    except TurnCeilingReached as exc:
        synthesis, commits = ph.salvage_on_ceiling(handle, "task-1", "implementation", exc)

    assert len(commits) == 1
    assert commits[0].split(maxsplit=1)[1] == (
        "chore: salvage uncommitted implementation output"
    )
    assert "salvage uncommitted implementation output" in synthesis
    assert "- " + commits[0] in synthesis


def test_salvage_on_ceiling_clean_tree_with_prior_commits_uses_worker_authored_wording(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    base_sha = subprocess.run(  # noqa: S603,S607
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    (repo / "wip.py").write_text("x = 1\n")
    subprocess.run(  # noqa: S603,S607
        ["git", "add", "-A"], cwd=str(repo), check=True, capture_output=True
    )
    subprocess.run(  # noqa: S603,S607
        ["git", "commit", "-q", "-m", "feat: worker committed this"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )
    handle = WorktreeHandle(path=repo, base_ref=base_sha, branch="teams/t-inv_001")
    monkeypatch.setattr(ph, "_node_turns_audit_dir", lambda: tmp_path / "logs")

    try:
        raise TurnCeilingReached("ceiling")
    except TurnCeilingReached as exc:
        synthesis, commits = ph.salvage_on_ceiling(handle, "task-1", "implementation", exc)

    assert len(commits) == 1
    assert "host recovery path committed the work" not in synthesis
    assert "worker's own commits" in synthesis


def test_salvage_on_ceiling_reports_lost_diff_when_dirty_salvage_commit_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    base_sha = subprocess.run(  # noqa: S603,S607
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    (repo / "worker.py").write_text("y = 2\n")
    subprocess.run(  # noqa: S603,S607
        ["git", "add", "-A"], cwd=str(repo), check=True, capture_output=True
    )
    subprocess.run(  # noqa: S603,S607
        ["git", "commit", "-q", "-m", "feat: worker committed this"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )
    (repo / "untracked.txt").write_text("left behind\n")
    handle = WorktreeHandle(path=repo, base_ref=base_sha, branch="teams/t-inv_001")
    monkeypatch.setattr(ph, "_node_turns_audit_dir", lambda: tmp_path / "logs")

    real_run = subprocess.run

    def fake_run(cmd: Any, **kwargs: Any) -> Any:
        if cmd[:2] == ["git", "commit"]:
            raise subprocess.TimeoutExpired(cmd, 30)
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)

    try:
        raise TurnCeilingReached("ceiling")
    except TurnCeilingReached as exc:
        synthesis, commits = ph.salvage_on_ceiling(handle, "task-1", "implementation", exc)

    assert len(commits) == 1
    assert "host recovery path committed the work" not in synthesis
    assert "worker's own commits" not in synthesis
    assert "NOT recovered" in synthesis


def test_no_pr_escalation_handover_without_prior(workspace: Workspace) -> None:
    handover = ph.no_pr_escalation_handover(
        workspace, None, "inv_005",
        team_name="verification",
        reason="no PR resolvable",
        prior_pr=None,
        default_forbidden=("do not X",),
        dependency_bot_authorized=False,
    )
    assert handover.handover_type == "escalation"
    assert handover.from_team == "verification"
    assert handover.from_invocation_id == "inv_005"
    assert handover.to_team_suggestions == []
    assert handover.iteration == 1
    assert handover.revisit_of is None
    assert handover.full_synthesis == "no PR resolvable"
    assert handover.pr_number is None
    assert handover.forbidden_actions == ["do not X"]
    assert handover.test_scenarios == []
    assert handover.dependency_bot_authorized is False


def test_no_pr_escalation_handover_with_prior_chains_iteration_and_scenarios(
    workspace: Workspace,
) -> None:
    from alan.handover import TestScenario

    prior = _prior_handover(
        workspace,
        iteration=2,
        pr_number=42,
        dependency_bot_authorized=True,
        test_scenarios=[
            TestScenario(id="s1", description="d", given="g", when="w", then="t")
        ],
    )
    handover = ph.no_pr_escalation_handover(
        workspace, prior, "inv_006",
        team_name="implementation_fix",
        reason="dispatched without a resolvable PR",
        prior_pr=prior.pr_number,
        default_forbidden=("baseline forbidden",),
        dependency_bot_authorized=False,
    )
    assert handover.iteration == 3
    assert handover.revisit_of == prior.handover_id
    assert handover.handover_chain_id == prior.handover_chain_id
    assert [s.id for s in handover.test_scenarios] == ["s1"]
    assert handover.forbidden_actions == ["baseline forbidden"]
    assert handover.pr_number == 42
    assert handover.dependency_bot_authorized is False


def test_no_pr_escalation_handover_dependency_bot_authorized_explicit_true(
    workspace: Workspace,
) -> None:
    prior = _prior_handover(workspace, dependency_bot_authorized=False)
    handover = ph.no_pr_escalation_handover(
        workspace, prior, "inv_007",
        team_name="verification",
        reason="r",
        prior_pr=None,
        default_forbidden=(),
        dependency_bot_authorized=True,
    )
    assert handover.dependency_bot_authorized is True


def test_find_last_pr_reference_none_when_no_handovers(workspace: Workspace) -> None:
    assert ph.find_last_pr_reference(workspace) is None


def test_find_last_pr_reference_none_when_no_handover_carries_pr_number(
    workspace: Workspace,
) -> None:
    from alan.handover import save_handover

    h1 = _prior_handover(workspace, pr_number=None)
    save_handover(workspace, h1)
    assert ph.find_last_pr_reference(workspace) is None


def test_find_last_pr_reference_returns_newest_with_pr_number(
    workspace: Workspace,
) -> None:
    from alan.handover import save_handover

    h1 = _prior_handover(workspace, from_invocation_id="inv_001", pr_number=10)
    save_handover(workspace, h1)
    h2 = _prior_handover(workspace, from_invocation_id="inv_002", pr_number=None)
    save_handover(workspace, h2)
    h3 = _prior_handover(workspace, from_invocation_id="inv_003", pr_number=99)
    save_handover(workspace, h3)

    found = ph.find_last_pr_reference(workspace)
    assert found is not None
    assert found.from_invocation_id == "inv_003"
    assert found.pr_number == 99


def test_cwd_overrides_preserves_every_non_cwd_field() -> None:
    import dataclasses

    src = WorkerOverrides(
        cwd=Path("/old"),
        allowed_tools=["Read"],
        add_dirs=["/live/.alan"],
        permission_mode="acceptEdits",
        max_turns=7,
    )
    assert all(
        getattr(src, f.name) is not None
        for f in dataclasses.fields(WorkerOverrides)
    ), "give every WorkerOverrides field a non-default value here, then copy it in cwd_overrides"

    out = ph.cwd_overrides(src, Path("/new"))
    assert out.cwd == Path("/new")
    for f in dataclasses.fields(WorkerOverrides):
        if f.name != "cwd":
            assert getattr(out, f.name) == getattr(src, f.name)


def test_carried_scenarios_none_prior_returns_empty_list() -> None:
    assert ph.carried_scenarios(None) == []


def test_carried_scenarios_copies_priors_list(workspace: Workspace) -> None:
    from alan.handover import TestScenario

    prior = _prior_handover(
        workspace,
        test_scenarios=[
            TestScenario(id="s1", description="d", given="g", when="w", then="t")
        ],
    )
    result = ph.carried_scenarios(prior)
    assert result == prior.test_scenarios
    assert result is not prior.test_scenarios


def test_handover_context_build_test_scenarios_unchanged_with_prior(
    workspace: Workspace,
) -> None:
    from alan.handover import TestScenario

    prior = _prior_handover(
        workspace,
        test_scenarios=[
            TestScenario(id="s1", description="d", given="g", when="w", then="t")
        ],
    )
    ctx = ph.HandoverContext.build(
        workspace, prior, "inv_002",
        team_name="implementation", default_forbidden=(), next_team=(),
    )
    assert [s.id for s in ctx.test_scenarios] == ["s1"]


def test_handover_context_build_test_scenarios_unchanged_without_prior(
    workspace: Workspace,
) -> None:
    ctx = ph.HandoverContext.build(
        workspace, None, "inv_002",
        team_name="implementation", default_forbidden=(), next_team=(),
    )
    assert ctx.test_scenarios == []
