from __future__ import annotations

import json
from pathlib import Path

import pytest

from alan import workflow as workflow_mod
from alan.claude_cli import AgentStreamingResult
from alan.teams import _base as base
from alan.teams import _block_correction_audit as bca
from alan.teams import _static_pipeline as sp
from alan.teams._static_pipeline import Role, StaticPipelineParseError, StaticPipelineTeam
from alan.workflow import PendingTaskSpec
from alan.workspace import Workspace, create


def _seed_success_node(
    ws: Workspace, team_name: str, role_name: str, *, session_id: str = "sess-1"
) -> str:
    specs = [PendingTaskSpec(id=role_name, role=role_name, model="sonnet")]
    inv_id = workflow_mod.seed_pending_workflow(ws, team=team_name, tasks=specs)
    graph = workflow_mod.load_graph(ws, inv_id)
    graph.start_node(role_name, role=role_name, model="sonnet", depends_on=[])
    graph.set_session_id(role_name, session_id)
    graph.complete_node(role_name)
    workflow_mod.save_workflow(ws, graph.doc)
    return inv_id


def _patch_streaming(monkeypatch: pytest.MonkeyPatch, texts: list[str]) -> None:
    results = iter(texts)

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
        return AgentStreamingResult(result_text=next(results))

    monkeypatch.setattr(base, "run_agent_streaming", fake)


def _read_bc_rows() -> list[dict[str, object]]:
    path = bca.audit_path()
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _team() -> StaticPipelineTeam:
    return StaticPipelineTeam(name="t", roles=(Role("r", "P", "sonnet"),), to_team_suggestions=())


def test_decisions_block_needed_repair_false_when_no_block() -> None:
    assert sp.decisions_block_needed_repair("no block here") is False


def test_decisions_block_needed_repair_false_when_valid_yaml() -> None:
    text = "```yaml decisions.yaml\ndecisions:\n  - id: d1\n```"
    assert sp.decisions_block_needed_repair(text) is False


def test_decisions_block_needed_repair_true_when_invalid_yaml_repairable() -> None:
    text = '```yaml decisions.yaml\ndecisions:\n  - statement: bad: value\n```'
    assert sp.decisions_block_needed_repair(text) is True


def test_legacy_call_without_block_name_is_unaffected(tmp_path: Path) -> None:
    ws = create("ok", "d", root=tmp_path / "ws")
    result = _team()._parse_or_invalidate(  # noqa: SLF001
        ws, "inv_001", lambda t: t.upper(), "hello"
    )
    assert result == "HELLO"


def test_correction_recovers_on_second_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    ws = create("ws", "d", root=tmp_path / "ws")
    inv_id = _seed_success_node(ws, "t", "r")
    _patch_streaming(monkeypatch, ["fixed"])

    def _parse(text: str) -> str:
        if text == "fixed":
            return text.upper()
        raise StaticPipelineParseError("bad")

    result = _team()._parse_or_invalidate(  # noqa: SLF001
        ws,
        inv_id,
        _parse,
        "broken",
        block_name="decisions.yaml",
        emit=lambda _e: None,
        on_proc_started=None,
    )
    assert result == "FIXED"
    rows = _read_bc_rows()
    assert rows[-1]["outcome"] == "corrected"
    assert rows[-1]["attempts"] == 2
    assert rows[-1]["site"] == "decisions.yaml"


def test_correction_exhausts_and_raises_with_attempt_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    ws = create("ws", "d", root=tmp_path / "ws")
    inv_id = _seed_success_node(ws, "t", "r")
    _patch_streaming(monkeypatch, ["still broken 1", "still broken 2"])

    def _parse(text: str) -> str:
        raise StaticPipelineParseError(f"bad: {text}")

    with pytest.raises(StaticPipelineParseError, match="exhausted"):
        _team()._parse_or_invalidate(  # noqa: SLF001
            ws,
            inv_id,
            _parse,
            "broken",
            block_name="decisions.yaml",
            emit=lambda _e: None,
            on_proc_started=None,
        )
    doc = workflow_mod.load_workflow(workflow_mod.workflow_path(ws, inv_id))
    assert doc.status == "failed"
    node = next(t for t in doc.tasks if t.id == "r")
    assert node.status == "failed"
    assert "attempt 1" in (node.error or "")
    rows = _read_bc_rows()
    assert rows[-1]["outcome"] == "exhausted"


def test_correction_unreachable_author_when_no_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    ws = create("ws", "d", root=tmp_path / "ws")

    def _parse(text: str) -> str:
        raise StaticPipelineParseError("bad")

    with pytest.raises(StaticPipelineParseError):
        _team()._parse_or_invalidate(  # noqa: SLF001
            ws,
            "inv_001",
            _parse,
            "broken",
            block_name="decisions.yaml",
            emit=lambda _e: None,
            on_proc_started=None,
        )
    rows = _read_bc_rows()
    assert rows[-1]["outcome"] == "unreachable_author"


def test_correction_detect_repair_relabels_clean_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(tmp_path / "logs"))
    ws = create("ws", "d", root=tmp_path / "ws")
    inv_id = _seed_success_node(ws, "t", "r")

    result = _team()._parse_or_invalidate(  # noqa: SLF001
        ws,
        inv_id,
        lambda t: t.upper(),
        "hello",
        block_name="decisions.yaml",
        emit=lambda _e: None,
        on_proc_started=None,
        detect_repair=lambda _t: True,
    )
    assert result == "HELLO"
    rows = _read_bc_rows()
    assert rows[-1]["outcome"] == "repaired"
