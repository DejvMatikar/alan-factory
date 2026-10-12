from __future__ import annotations

import contextlib
import re
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

import alan.claude_cli as claude_cli
from alan import project as project_mod
from alan import project_doc as project_doc_mod
from alan import workspace as workspace_mod
from alan._dispatcher_loop import _resolve_project_doc_path
from alan.claude_cli import OnProcStarted
from alan.claude_cli_grants import NO_TOOLS_BLOCK
from alan.dispatcher import (
    Dispatcher,
    _extract_project_fact,
    _format_project_fact_bullet,
    _maybe_harvest_project_fact,
)
from alan.handover import Handover
from alan.project_doc_context import get_active_project_doc_path, set_active_project_doc_path
from alan.queue import Queue
from alan.teams._base import TeamEvent
from alan.workflow import PendingTaskSpec
from alan.workspace import Workspace

_FACT_BLOCK = (
    "Here is my synthesis of the change.\n\n"
    "## PROJECT_FACT\n"
    "```\n"
    "The payments service caps retries at 3.\n"
    "```\n\n"
    "Rest of the write-up."
)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


@pytest.fixture
def projects_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    p = tmp_path / "projects"
    monkeypatch.setenv("ALAN_PROJECTS_ROOT", str(p))
    return p


@pytest.fixture
def queue(tmp_path: Path) -> Iterator[Queue]:
    q = Queue(db_path=tmp_path / "queue.db")
    try:
        yield q
    finally:
        with contextlib.suppress(Exception):
            q.close()


@pytest.fixture(autouse=True)
def _no_summary_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("alan.manager_summary.trigger_manager_summary", lambda *_a, **_kw: None)


@pytest.fixture(autouse=True)
def _clear_active_project_doc_path() -> Iterator[None]:
    set_active_project_doc_path(None)
    yield
    set_active_project_doc_path(None)


def _handover(task_id: str, synthesis: str, *, from_team: str = "implementation") -> Handover:
    return Handover(
        handover_id="h-1",
        handover_type="forward",
        task_id=task_id,
        from_team=from_team,
        from_invocation_id="inv_001",
        to_team_suggestions=["implementation_plan"],
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis=synthesis,
    )


def _spy_append(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    calls: list[object] = []

    def _fake(*args: object, **kwargs: object) -> Path:
        calls.append((args, kwargs))
        return Path("/dev/null")

    monkeypatch.setattr(project_doc_mod, "append_discovered_fact", _fake)
    return calls


class TestProjectDocContext:
    def test_default_is_none(self) -> None:
        assert get_active_project_doc_path() is None

    def test_set_then_get_round_trips(self, tmp_path: Path) -> None:
        doc_path = str(tmp_path / "project.md")
        set_active_project_doc_path(doc_path)
        assert get_active_project_doc_path() == doc_path

    def test_set_none_clears_value(self, tmp_path: Path) -> None:
        set_active_project_doc_path(str(tmp_path / "project.md"))
        set_active_project_doc_path(None)
        assert get_active_project_doc_path() is None


class TestProjectDocBlock:
    def test_empty_when_no_active_path(self) -> None:
        set_active_project_doc_path(None)
        assert claude_cli._project_doc_block() == ""  # noqa: SLF001

    def test_empty_when_path_does_not_exist(self, tmp_path: Path) -> None:
        missing = tmp_path / "no-such-project.md"
        set_active_project_doc_path(str(missing))
        assert claude_cli._project_doc_block() == ""  # noqa: SLF001

    def test_includes_at_reference_and_read_only_notice_when_file_exists(
        self, tmp_path: Path
    ) -> None:
        doc = tmp_path / "project.md"
        doc.write_text("## Vision\n\nShip it.\n", encoding="utf-8")
        set_active_project_doc_path(str(doc))
        block = claude_cli._project_doc_block()  # noqa: SLF001
        assert f"@{doc}" in block
        assert "READ-ONLY" in block

    def test_build_cmd_matches_pre_feature_shape_without_project(self) -> None:
        set_active_project_doc_path(None)
        cmd = claude_cli._build_cmd(  # noqa: SLF001
            "sys prompt", output_format="json", max_turns=1
        )
        prompt_arg = cmd[cmd.index("--append-system-prompt") + 1]
        assert prompt_arg == f"sys prompt\n\n{claude_cli._HEADLESS_EXECUTION_BLOCK}"  # noqa: SLF001

    def test_build_cmd_matches_pre_feature_shape_when_doc_missing(
        self, tmp_path: Path
    ) -> None:
        set_active_project_doc_path(str(tmp_path / "absent.md"))
        cmd = claude_cli._build_cmd(  # noqa: SLF001
            "sys prompt", output_format="json", max_turns=1
        )
        prompt_arg = cmd[cmd.index("--append-system-prompt") + 1]
        assert prompt_arg == f"sys prompt\n\n{claude_cli._HEADLESS_EXECUTION_BLOCK}"  # noqa: SLF001

    def test_build_cmd_injects_at_reference_when_doc_present(self, tmp_path: Path) -> None:
        doc = tmp_path / "project.md"
        doc.write_text("## Vision\n", encoding="utf-8")
        set_active_project_doc_path(str(doc))
        cmd = claude_cli._build_cmd(  # noqa: SLF001
            "sys prompt", output_format="json", max_turns=1
        )
        prompt_arg = cmd[cmd.index("--append-system-prompt") + 1]
        assert prompt_arg.startswith(f"sys prompt\n\n{claude_cli._HEADLESS_EXECUTION_BLOCK}")  # noqa: SLF001
        assert f"@{doc}" in prompt_arg

    def test_build_cmd_deny_all_tools_suppresses_at_reference_even_when_doc_present(
        self, tmp_path: Path
    ) -> None:
        doc = tmp_path / "project.md"
        doc.write_text("## Vision\n", encoding="utf-8")
        set_active_project_doc_path(str(doc))
        cmd = claude_cli._build_cmd(  # noqa: SLF001
            "sys prompt", output_format="json", max_turns=1, deny_all_tools=True
        )
        prompt_arg = cmd[cmd.index("--append-system-prompt") + 1]
        assert NO_TOOLS_BLOCK in prompt_arg
        assert f"@{doc}" not in prompt_arg
        assert claude_cli._HEADLESS_EXECUTION_BLOCK not in prompt_arg  # noqa: SLF001


class TestResolveProjectDocPath:
    def test_no_project_id_returns_none(self, root: Path) -> None:
        ws = workspace_mod.create("no-project-ws", "d", root=root)
        assert _resolve_project_doc_path(ws) is None

    def test_unknown_project_id_returns_none(self, root: Path, projects_root: Path) -> None:
        ws = workspace_mod.create(
            "ghost-project-ws", "d", root=root, project_id="does-not-exist"
        )
        assert _resolve_project_doc_path(ws) is None

    def test_invalid_doc_ref_returns_none(self, root: Path, projects_root: Path) -> None:
        project = project_mod.create("Bad Ref", root=projects_root, doc_ref="../escape.md")
        ws = workspace_mod.create("bad-ref-ws", "d", root=root, project_id=project.id)
        assert _resolve_project_doc_path(ws) is None

    def test_returns_absolute_doc_path_for_project_with_no_doc_yet(
        self, root: Path, projects_root: Path
    ) -> None:
        project = project_mod.create("Fresh Project", root=projects_root)
        ws = workspace_mod.create("fresh-project-ws", "d", root=root, project_id=project.id)
        resolved = _resolve_project_doc_path(ws)
        assert resolved is not None
        assert resolved == str(project_doc_mod.doc_path(project, root=projects_root))
        assert not Path(resolved).is_file()

    def test_returns_absolute_doc_path_for_project_with_doc(
        self, root: Path, projects_root: Path
    ) -> None:
        project = project_mod.create("Real Project", root=projects_root)
        project, path = project_doc_mod.ensure_doc(project, root=projects_root)
        ws = workspace_mod.create("real-project-ws", "d", root=root, project_id=project.id)
        resolved = _resolve_project_doc_path(ws)
        assert resolved is not None
        assert resolved == str(path)
        assert Path(resolved).is_file()


class _DocPathCapturingTeam:
    name = "research"
    supports_resume = False

    def __init__(self) -> None:
        self.observed: list[str | None] = []

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
        inv_id: str = "inv_001",
        resume_inv_id: str | None = None,
        instructions: str | None = None,
        on_proc_started: OnProcStarted | None = None,
        task_id: str | None = None,
    ) -> Handover:
        self.observed.append(get_active_project_doc_path())
        return _handover(ws.task_id, "ok", from_team="research")


class TestExecuteWiresProjectDocPath:
    def test_no_project_workspace_observes_none(self, queue: Queue, root: Path) -> None:
        team = _DocPathCapturingTeam()
        d = Dispatcher(queue, {"research": team}, workspaces_root=root)
        ws = workspace_mod.create("wire-no-project-ws", "d", root=root)
        queue.enqueue(ws.task_id, "research")
        d.run_once()
        assert team.observed == [None]

    def test_project_workspace_observes_resolved_absolute_path(
        self, queue: Queue, root: Path, projects_root: Path
    ) -> None:
        project = project_mod.create("Wired Project", root=projects_root)
        project, path = project_doc_mod.ensure_doc(project, root=projects_root)
        team = _DocPathCapturingTeam()
        d = Dispatcher(queue, {"research": team}, workspaces_root=root)
        ws = workspace_mod.create("wire-project-ws", "d", root=root, project_id=project.id)
        queue.enqueue(ws.task_id, "research")
        d.run_once()
        assert team.observed == [str(path)]

    def test_resets_per_job_across_mixed_workspaces(
        self, queue: Queue, root: Path, projects_root: Path
    ) -> None:
        project = project_mod.create("Mixed Project", root=projects_root)
        project, path = project_doc_mod.ensure_doc(project, root=projects_root)
        team = _DocPathCapturingTeam()
        d = Dispatcher(queue, {"research": team}, workspaces_root=root)
        plain_ws = workspace_mod.create("mixed-plain-ws", "d", root=root)
        project_ws = workspace_mod.create(
            "mixed-project-ws", "d", root=root, project_id=project.id
        )

        queue.enqueue(plain_ws.task_id, "research")
        d.run_once()
        queue.enqueue(project_ws.task_id, "research")
        d.run_once()

        assert team.observed == [None, str(path)]

    def test_cleared_after_job_completes(
        self, queue: Queue, root: Path, projects_root: Path
    ) -> None:
        project = project_mod.create("Cleared Project", root=projects_root)
        project, _path = project_doc_mod.ensure_doc(project, root=projects_root)
        team = _DocPathCapturingTeam()
        d = Dispatcher(queue, {"research": team}, workspaces_root=root)
        ws = workspace_mod.create("wire-cleared-ws", "d", root=root, project_id=project.id)
        queue.enqueue(ws.task_id, "research")
        d.run_once()
        assert get_active_project_doc_path() is None

    def test_cleared_after_load_prior_fails(self, queue: Queue, root: Path) -> None:
        team = _DocPathCapturingTeam()
        d = Dispatcher(queue, {"research": team}, workspaces_root=root)
        ws = workspace_mod.create("wire-missing-parent-ws", "d", root=root)
        queue.enqueue(ws.task_id, "research", parent_handover_id="does-not-exist")
        d.run_once()
        assert team.observed == []
        assert get_active_project_doc_path() is None


class TestExtractProjectFact:
    def test_no_block_returns_none(self) -> None:
        assert _extract_project_fact("just prose, no fact block here") is None

    def test_unterminated_fence_returns_none(self) -> None:
        text = "## PROJECT_FACT\n```\nsome fact never closed"
        assert _extract_project_fact(text) is None

    def test_empty_block_returns_none(self) -> None:
        text = "## PROJECT_FACT\n```\n\n```\n"
        assert _extract_project_fact(text) is None

    def test_well_formed_block_extracts_stripped_content(self) -> None:
        fact = _extract_project_fact(_FACT_BLOCK)
        assert fact == "The payments service caps retries at 3."

    def test_language_tagged_fence_extracts_content(self) -> None:
        text = "## PROJECT_FACT\n```text\nThe payments service caps retries at 3.\n```\n"
        assert _extract_project_fact(text) == "The payments service caps retries at 3."

    def test_crlf_line_endings_extract_content(self) -> None:
        text = (
            "## PROJECT_FACT\r\n```\r\nThe payments service caps retries at 3.\r\n```\r\n"
        )
        assert _extract_project_fact(text) == "The payments service caps retries at 3."

    def test_multiline_fact_normalizes_to_single_line(self) -> None:
        text = "## PROJECT_FACT\n```\nThe payments service\ncaps retries   at 3.\n```\n"
        assert _extract_project_fact(text) == "The payments service caps retries at 3."


class TestFormatProjectFactBullet:
    def test_includes_team_timestamp_and_fact(self) -> None:
        bullet = _format_project_fact_bullet("implementation", "caps retries at 3")
        assert "(implementation):" in bullet
        assert "caps retries at 3" in bullet
        match = re.search(r"\*\*(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\*\*", bullet)
        assert match is not None


class TestMaybeHarvestProjectFact:
    def test_no_project_id_skips(self, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        ws = workspace_mod.create("harvest-no-project", "d", root=root)
        calls = _spy_append(monkeypatch)
        _maybe_harvest_project_fact(ws, _handover(ws.task_id, _FACT_BLOCK))
        assert calls == []

    def test_no_fact_block_skips(
        self, root: Path, projects_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project = project_mod.create("No Block", root=projects_root)
        ws = workspace_mod.create("harvest-no-block", "d", root=root, project_id=project.id)
        calls = _spy_append(monkeypatch)
        _maybe_harvest_project_fact(ws, _handover(ws.task_id, "just prose, no block"))
        assert calls == []

    def test_malformed_block_skips(
        self, root: Path, projects_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project = project_mod.create("Malformed", root=projects_root)
        ws = workspace_mod.create("harvest-malformed", "d", root=root, project_id=project.id)
        calls = _spy_append(monkeypatch)
        text = "## PROJECT_FACT\n```\nunterminated"
        _maybe_harvest_project_fact(ws, _handover(ws.task_id, text))
        assert calls == []

    def test_unknown_project_never_crashes(
        self, root: Path, projects_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ws = workspace_mod.create(
            "harvest-ghost", "d", root=root, project_id="does-not-exist"
        )
        calls = _spy_append(monkeypatch)
        _maybe_harvest_project_fact(ws, _handover(ws.task_id, _FACT_BLOCK))
        assert calls == []

    def test_append_failure_never_crashes(
        self, root: Path, projects_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project = project_mod.create("Raises", root=projects_root)
        ws = workspace_mod.create("harvest-raises", "d", root=root, project_id=project.id)

        def _boom(*_a: object, **_kw: object) -> Path:
            raise RuntimeError("disk full")

        monkeypatch.setattr(project_doc_mod, "append_discovered_fact", _boom)
        _maybe_harvest_project_fact(ws, _handover(ws.task_id, _FACT_BLOCK))

    def test_well_formed_block_appends_attributed_timestamped_bullet(
        self, root: Path, projects_root: Path
    ) -> None:
        project = project_mod.create("Harvested", root=projects_root)
        project, path = project_doc_mod.ensure_doc(project, root=projects_root)
        ws = workspace_mod.create("harvest-ok", "d", root=root, project_id=project.id)
        _maybe_harvest_project_fact(
            ws, _handover(ws.task_id, _FACT_BLOCK, from_team="implementation")
        )
        sections = project_doc_mod.parse_anchors(path.read_text(encoding="utf-8"))
        assert "The payments service caps retries at 3." in sections.discovered_facts
        assert "(implementation)" in sections.discovered_facts


class _FactEmittingTeam:
    name = "implementation"
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
        inv_id: str = "inv_001",
        resume_inv_id: str | None = None,
        instructions: str | None = None,
        on_proc_started: OnProcStarted | None = None,
        task_id: str | None = None,
    ) -> Handover:
        return _handover(ws.task_id, _FACT_BLOCK, from_team="implementation")


def test_run_once_harvests_project_fact_end_to_end(
    queue: Queue, root: Path, projects_root: Path
) -> None:
    project = project_mod.create("End To End", root=projects_root)
    project, path = project_doc_mod.ensure_doc(project, root=projects_root)
    team = _FactEmittingTeam()
    d = Dispatcher(queue, {"implementation": team}, workspaces_root=root)
    ws = workspace_mod.create("e2e-harvest-ws", "d", root=root, project_id=project.id)
    queue.enqueue(ws.task_id, "implementation")
    d.run_once()
    sections = project_doc_mod.parse_anchors(path.read_text(encoding="utf-8"))
    assert "The payments service caps retries at 3." in sections.discovered_facts
