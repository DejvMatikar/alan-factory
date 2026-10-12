from __future__ import annotations

import json
from pathlib import Path

import pytest

from alan import workflow as workflow_mod
from alan.claude_cli import AgentStreamingResult, ClaudeCliError
from alan.teams import _base as base
from alan.teams import _block_correction_audit as bca
from alan.teams import _block_correction_fleet as fleet
from alan.workflow import PendingTaskSpec
from alan.workspace import Workspace, create


def _seed_node_with_session(
    ws: Workspace, team_name: str, role_name: str, *, session_id: str | None = "sess-1"
) -> str:
    specs = [PendingTaskSpec(id=role_name, role=role_name, model="sonnet")]
    inv_id = workflow_mod.seed_pending_workflow(ws, team=team_name, tasks=specs)
    graph = workflow_mod.load_graph(ws, inv_id)
    graph.start_node(role_name, role=role_name, model="sonnet", depends_on=[])
    if session_id is not None:
        graph.set_session_id(role_name, session_id)
    graph.complete_node(role_name)
    workflow_mod.save_workflow(ws, graph.doc)
    return inv_id


def _patch_streaming(monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    def fake(
        system_prompt: str,
        user_text: str,
        *,
        model: str = "sonnet",
        on_event: object = None,
        stream_path: Path | None = None,
        max_turns: int = 10,
        cwd: object = None,
        **_kwargs: object,
    ) -> AgentStreamingResult:
        del system_prompt, user_text, model, on_event, max_turns, cwd
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
        return AgentStreamingResult(result_text=text)

    monkeypatch.setattr(base, "run_agent_streaming", fake)


def _read_audit_rows() -> list[dict[str, object]]:
    path = bca.audit_path()
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_captured_session_id_returns_none_when_no_workflow(tmp_path: Path) -> None:
    ws = create("ws", "d", root=tmp_path / "ws")
    assert fleet._captured_session_id(ws, "inv_001", "role") is None  # noqa: SLF001


def test_captured_session_id_returns_none_when_node_absent(tmp_path: Path) -> None:
    ws = create("ws", "d", root=tmp_path / "ws")
    inv_id = _seed_node_with_session(ws, "t", "other_role")
    assert fleet._captured_session_id(ws, inv_id, "role") is None  # noqa: SLF001


def test_captured_session_id_returns_session_id_when_present(tmp_path: Path) -> None:
    ws = create("ws", "d", root=tmp_path / "ws")
    inv_id = _seed_node_with_session(ws, "t", "role", session_id="sess-xyz")
    assert fleet._captured_session_id(ws, inv_id, "role") == "sess-xyz"  # noqa: SLF001


def test_build_re_ask_returns_none_when_no_captured_session(tmp_path: Path) -> None:
    ws = create("ws", "d", root=tmp_path / "ws")
    inv_id = _seed_node_with_session(ws, "t", "role", session_id=None)
    re_ask = fleet.build_re_ask(
        ws,
        inv_id,
        "role",
        lambda _e: None,
        system_prompt="SYS",
        overrides=None,
        on_proc_started=None,
        correction_prompt=lambda err: f"fix: {err}",
    )
    assert re_ask(1, "prior", "boom") is None


def test_build_re_ask_returns_none_when_load_graph_raises_before_mark_interrupted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = create("ws", "d", root=tmp_path / "ws")
    inv_id = _seed_node_with_session(ws, "t", "role")
    real_load_graph = workflow_mod.load_graph
    calls = {"n": 0}

    def flaky(workspace: Workspace, inv: str) -> workflow_mod.WorkflowGraph:
        calls["n"] += 1
        if calls["n"] == 2:
            raise workflow_mod.WorkflowError("boom")
        return real_load_graph(workspace, inv)

    monkeypatch.setattr(workflow_mod, "load_graph", flaky)
    re_ask = fleet.build_re_ask(
        ws,
        inv_id,
        "role",
        lambda _e: None,
        system_prompt="SYS",
        overrides=None,
        on_proc_started=None,
        correction_prompt=lambda err: f"fix: {err}",
    )
    assert re_ask(1, "prior", "boom") is None


def test_build_re_ask_returns_none_when_resume_raises_claude_cli_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = create("ws", "d", root=tmp_path / "ws")
    inv_id = _seed_node_with_session(ws, "t", "role")

    def _boom(*_a: object, **_kw: object) -> str:
        raise ClaudeCliError("boom")

    monkeypatch.setattr(fleet, "resume_workflow_node", _boom)
    re_ask = fleet.build_re_ask(
        ws,
        inv_id,
        "role",
        lambda _e: None,
        system_prompt="SYS",
        overrides=None,
        on_proc_started=None,
        correction_prompt=lambda err: f"fix: {err}",
    )
    assert re_ask(1, "prior", "boom") is None


def test_build_re_ask_returns_none_when_resume_raises_workflow_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = create("ws", "d", root=tmp_path / "ws")
    inv_id = _seed_node_with_session(ws, "t", "role")

    def _boom(*_a: object, **_kw: object) -> str:
        raise workflow_mod.WorkflowError("boom")

    monkeypatch.setattr(fleet, "resume_workflow_node", _boom)
    re_ask = fleet.build_re_ask(
        ws,
        inv_id,
        "role",
        lambda _e: None,
        system_prompt="SYS",
        overrides=None,
        on_proc_started=None,
        correction_prompt=lambda err: f"fix: {err}",
    )
    assert re_ask(1, "prior", "boom") is None


def test_build_re_ask_returns_corrected_text_on_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = create("ws", "d", root=tmp_path / "ws")
    inv_id = _seed_node_with_session(ws, "t", "role")
    _patch_streaming(monkeypatch, "corrected output")
    prompts: list[str] = []

    def _prompt(err: str) -> str:
        prompts.append(err)
        return f"fix: {err}"

    re_ask = fleet.build_re_ask(
        ws,
        inv_id,
        "role",
        lambda _e: None,
        system_prompt="SYS",
        overrides=None,
        on_proc_started=None,
        correction_prompt=_prompt,
    )
    result = re_ask(1, "prior text", "parse error here")
    assert result == "corrected output"
    assert prompts == ["parse error here"]
    doc = workflow_mod.load_graph(ws, inv_id).doc
    node = next(t for t in doc.tasks if t.id == "role")
    assert node.status == "success"


def _finalize_seeded_invocation(ws: Workspace, inv_id: str) -> None:
    graph = workflow_mod.load_graph(ws, inv_id)
    graph.finalize(status="success")
    workflow_mod.save_workflow(ws, graph.doc)


def _persisted_doc(ws: Workspace, inv_id: str) -> workflow_mod.WorkflowDoc:
    return workflow_mod.load_workflow(workflow_mod.workflow_path(ws, inv_id))


def test_build_re_ask_refinalizes_the_invocation_after_a_successful_correction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = create("ws", "d", root=tmp_path / "ws")
    inv_id = _seed_node_with_session(ws, "t", "role")
    _finalize_seeded_invocation(ws, inv_id)
    _patch_streaming(monkeypatch, "corrected output")

    re_ask = fleet.build_re_ask(
        ws,
        inv_id,
        "role",
        lambda _e: None,
        system_prompt="SYS",
        overrides=None,
        on_proc_started=None,
        correction_prompt=lambda err: f"fix: {err}",
    )
    assert re_ask(1, "prior text", "parse error here") == "corrected output"

    doc = _persisted_doc(ws, inv_id)
    assert doc.status == "success"
    node = next(t for t in doc.tasks if t.id == "role")
    assert node.completed_at is not None
    assert doc.completed_at is not None
    assert doc.completed_at >= node.completed_at


def test_build_re_ask_refinalizes_the_invocation_when_the_resume_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = create("ws", "d", root=tmp_path / "ws")
    inv_id = _seed_node_with_session(ws, "t", "role")
    _finalize_seeded_invocation(ws, inv_id)

    def _boom(*_a: object, **_kw: object) -> str:
        raise ClaudeCliError("boom")

    monkeypatch.setattr(fleet, "resume_workflow_node", _boom)
    re_ask = fleet.build_re_ask(
        ws,
        inv_id,
        "role",
        lambda _e: None,
        system_prompt="SYS",
        overrides=None,
        on_proc_started=None,
        correction_prompt=lambda err: f"fix: {err}",
    )
    assert re_ask(1, "prior", "boom") is None
    assert _persisted_doc(ws, inv_id).status == "success"


def test_run_fleet_correction_relabels_clean_as_repaired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    outcome = fleet.run_fleet_correction(
        "text",
        validate=lambda t: t.upper(),
        re_ask=lambda *_a: None,
        site="decisions.yaml",
        role="judge",
        task_id="t1",
        detect_repair=lambda _t: True,
    )
    assert outcome.outcome == "clean"
    rows = _read_audit_rows()
    assert rows[0]["outcome"] == "repaired"


def test_run_fleet_correction_no_relabel_when_detect_repair_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    fleet.run_fleet_correction(
        "text",
        validate=lambda t: t.upper(),
        re_ask=lambda *_a: None,
        site="decisions.yaml",
        role="judge",
        task_id="t1",
        detect_repair=lambda _t: False,
    )
    rows = _read_audit_rows()
    assert rows[0]["outcome"] == "clean"


def test_run_fleet_correction_no_relabel_when_detect_repair_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    fleet.run_fleet_correction(
        "text",
        validate=lambda t: t.upper(),
        re_ask=lambda *_a: None,
        site="decisions.yaml",
        role="judge",
        task_id="t1",
    )
    rows = _read_audit_rows()
    assert rows[0]["outcome"] == "clean"


def test_run_fleet_correction_no_relabel_when_outcome_not_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))

    def _fail(_t: str) -> str:
        raise ValueError("bad")

    outcome = fleet.run_fleet_correction(
        "text",
        validate=_fail,
        re_ask=lambda *_a: None,
        site="decisions.yaml",
        role="judge",
        task_id="t1",
        max_attempts=2,
        detect_repair=lambda _t: True,
    )
    assert outcome.outcome == "unreachable_author"
    rows = _read_audit_rows()
    assert rows[0]["outcome"] == "unreachable_author"


def test_fleet_max_attempts_constant_is_three() -> None:
    assert fleet.FLEET_MAX_ATTEMPTS == 3
