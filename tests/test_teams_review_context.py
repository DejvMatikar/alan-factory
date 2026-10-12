from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from alan.claude_cli import AgentStreamingResult, StreamEvent
from alan.teams import _base as base_mod
from alan.teams import _pr_audit_log, _ratchet_direction
from alan.teams import _review_context as ctx_mod
from alan.teams.verification import ROLES, TEAM
from alan.workspace import Workspace
from tests import _teams_verification_support as support
from tests._helpers import seed_and_run

_DEFAULT_JUDGE_OUTPUT = support._DEFAULT_JUDGE_OUTPUT  # noqa: SLF001


@pytest.fixture(autouse=True)
def _stub_external_gates_default(monkeypatch: pytest.MonkeyPatch) -> None:
    support.stub_external_gates(monkeypatch)


@pytest.fixture(autouse=True)
def _stub_checkout_internals_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    support.stub_checkout_internals(monkeypatch, tmp_path)


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return support.make_workspace(tmp_path)


@pytest.fixture
def audit_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return support.set_audit_root(tmp_path, monkeypatch)


def test_format_manifest_deviation_block_lists_files() -> None:
    block = ctx_mod._format_manifest_deviation_block(["alan/unrelated.py"])  # noqa: SLF001
    assert block.startswith("## Manifest deviation (advisory)")
    assert "- alan/unrelated.py" in block
    assert "advisory" in block.lower()


def test_diff_reviewer_receives_review_context_end_to_end(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path,  # noqa: ARG001
) -> None:
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    captured: list[tuple[str, str]] = []
    counter = {"n": 0}

    def fake(
        system_prompt: str,
        user_text: str,
        *,
        model: str = "sonnet",  # noqa: ARG001
        on_event: Callable[[StreamEvent], None] | None = None,
        stream_path: Path | None = None,
        max_turns: int = 10,  # noqa: ARG001
        cwd: object = None,  # noqa: ARG001
        **_kwargs: object,
    ) -> AgentStreamingResult:
        counter["n"] += 1
        captured.append((system_prompt, user_text))
        text = _DEFAULT_JUDGE_OUTPUT if counter["n"] == len(ROLES) else f"out-{counter['n']}"
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            stream_path.write_text(json.dumps({"type": "result", "result": text}) + "\n")
        if on_event is not None:
            on_event({"type": "result", "result": text})
        return AgentStreamingResult(result_text=text)

    monkeypatch.setattr(base_mod, "run_agent_streaming", fake)
    prior = support.impl_handover(workspace)
    seed_and_run(TEAM, workspace, prior, lambda _e: None)

    diff_reviewer_call = next(
        c for c in captured if "diff_reviewer on the verification team" in c[0]
    )
    judge_call = next(c for c in captured if "judge on the verification team" in c[0])
    assert "PR EVIDENCE" in diff_reviewer_call[1]
    assert "PR EVIDENCE" not in judge_call[1]


def test_ratchet_loosening_audit_receives_split_subtask_task_id_end_to_end(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path,  # noqa: ARG001
) -> None:
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    findings = (
        _ratchet_direction.Loosening(
            "alan/web/.jscpd.json", "threshold raised from 1 to 2",
        ),
    )
    monkeypatch.setattr(_ratchet_direction, "detect_ratchet_loosening", lambda *_a, **_kw: findings)
    captured_task_ids: list[str] = []

    def fake_audit(pr_number: int, task_id: str, loosening: object) -> None:
        del pr_number, loosening
        captured_task_ids.append(task_id)

    monkeypatch.setattr(ctx_mod, "_audit_ratchet_loosening", fake_audit)
    counter = {"n": 0}

    def fake(
        system_prompt: str,  # noqa: ARG001
        user_text: str,  # noqa: ARG001
        *,
        model: str = "sonnet",  # noqa: ARG001
        on_event: Callable[[StreamEvent], None] | None = None,
        stream_path: Path | None = None,
        max_turns: int = 10,  # noqa: ARG001
        cwd: object = None,  # noqa: ARG001
        **_kwargs: object,
    ) -> AgentStreamingResult:
        counter["n"] += 1
        text = _DEFAULT_JUDGE_OUTPUT if counter["n"] == len(ROLES) else f"out-{counter['n']}"
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            stream_path.write_text(json.dumps({"type": "result", "result": text}) + "\n")
        if on_event is not None:
            on_event({"type": "result", "result": text})
        return AgentStreamingResult(result_text=text)

    monkeypatch.setattr(base_mod, "run_agent_streaming", fake)
    prior = support.impl_handover(workspace)
    assert workspace.task_id != "split-subtask-id"
    seed_and_run(TEAM, workspace, prior, lambda _e: None, task_id="split-subtask-id")

    assert captured_task_ids == ["split-subtask-id"]


def test_diff_reviewer_prefix_returns_evidence_and_scope_when_nothing_else_to_say(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, tmp_path: Path,
) -> None:
    monkeypatch.setattr(_ratchet_direction, "detect_ratchet_loosening", lambda *_a, **_kw: ())
    block = ctx_mod.diff_reviewer_prefix(1, workspace, "task-x", checkout=tmp_path)
    assert block is not None
    assert "PR EVIDENCE" in block
    assert "TEST SCOPE" in block


def test_diff_reviewer_prefix_audits_only_when_findings_present(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, tmp_path: Path,
) -> None:
    audited: list[dict[str, object]] = []
    monkeypatch.setattr(_pr_audit_log, "append_audit", audited.append)
    findings = (
        _ratchet_direction.Loosening(
            "alan/web/.jscpd.json", "threshold raised from 1 to 2",
        ),
    )
    monkeypatch.setattr(_ratchet_direction, "detect_ratchet_loosening", lambda *_a, **_kw: findings)
    ctx_mod.diff_reviewer_prefix(7, workspace, "task-y", checkout=tmp_path)
    assert len(audited) == 1
    assert audited[0]["action"] == "ratchet_loosening_detected"
    assert audited[0]["pr_number"] == 7
    assert audited[0]["task_id"] == "task-y"
    assert audited[0]["paths"] == ["alan/web/.jscpd.json"]


def test_diff_reviewer_prefix_no_audit_when_base_unresolved(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, tmp_path: Path,
) -> None:
    audited: list[dict[str, object]] = []
    monkeypatch.setattr(_pr_audit_log, "append_audit", audited.append)
    monkeypatch.setattr(_ratchet_direction, "detect_ratchet_loosening", lambda *_a, **_kw: None)
    block = ctx_mod.diff_reviewer_prefix(1, workspace, "task-x", checkout=tmp_path)
    assert block is not None
    assert "base version could not be resolved" in block
    assert audited == []
