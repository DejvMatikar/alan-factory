from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from alan.claude_cli import AgentStreamingResult, StreamEvent
from alan.handover import Handover
from alan.teams import _base as base_mod
from alan.teams import _ratchet_direction
from alan.teams import _review_context as ctx_mod
from alan.teams.verification import ROLES, TEAM
from alan.workspace import Workspace
from tests import _teams_verification_support as support
from tests._helpers import seed_and_run
from tests._pr_branch_support import _audit_entries

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


def _fake_streaming(counter: dict[str, int]) -> "Callable[..., AgentStreamingResult]":
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
        del system_prompt, user_text
        counter["n"] += 1
        text = _DEFAULT_JUDGE_OUTPUT if counter["n"] == len(ROLES) else f"out-{counter['n']}"
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            stream_path.write_text(json.dumps({"type": "result", "result": text}) + "\n")
        if on_event is not None:
            on_event({"type": "result", "result": text})
        return AgentStreamingResult(result_text=text)

    return fake


def test_diff_reviewer_prefix_threads_the_grounded_checkout(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path,  # noqa: ARG001
) -> None:
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    captured: dict[str, object] = {}

    def fake_prefix(
        pr_number: int, ws: Workspace, task_id: str, *, checkout: Path,
    ) -> str | None:
        del ws
        captured["pr_number"] = pr_number
        captured["task_id"] = task_id
        captured["checkout"] = checkout
        return None

    monkeypatch.setattr(ctx_mod, "diff_reviewer_prefix", fake_prefix)
    monkeypatch.setattr(base_mod, "run_agent_streaming", _fake_streaming({"n": 0}))
    prior = support.impl_handover(workspace)
    seed_and_run(TEAM, workspace, prior, lambda _e: None)

    assert captured["pr_number"] == 42
    assert captured["task_id"] == workspace.task_id
    assert isinstance(captured["checkout"], Path)


def test_ratchet_block_reaches_diff_reviewer_prompt_only(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path,  # noqa: ARG001
) -> None:
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    findings = (
        _ratchet_direction.Loosening(
            "tests/size_baseline.py", "TEST_FILE_CAP raised from 1000 to 2000",
        ),
    )
    monkeypatch.setattr(_ratchet_direction, "detect_ratchet_loosening", lambda *_a, **_kw: findings)
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
    assert "Ratchet direction observation" in diff_reviewer_call[1]
    assert "tests/size_baseline.py" in diff_reviewer_call[1]
    assert "Ratchet direction observation" not in judge_call[1]


def test_ratchet_loosening_writes_audit_entry_when_findings_present(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, tmp_path: Path,
    audit_root: Path,  # noqa: ARG001
) -> None:
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    findings = (
        _ratchet_direction.Loosening("alan/web/.jscpd.json", "threshold raised from 1 to 2"),
    )
    monkeypatch.setattr(_ratchet_direction, "detect_ratchet_loosening", lambda *_a, **_kw: findings)
    monkeypatch.setattr(base_mod, "run_agent_streaming", _fake_streaming({"n": 0}))
    prior = support.impl_handover(workspace)
    seed_and_run(TEAM, workspace, prior, lambda _e: None)

    entries = [
        e for e in _audit_entries(tmp_path) if e.get("action") == "ratchet_loosening_detected"
    ]
    assert len(entries) == 1
    assert entries[0]["pr_number"] == 42
    assert entries[0]["paths"] == ["alan/web/.jscpd.json"]


def test_no_ratchet_audit_entry_when_no_findings(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, tmp_path: Path,
    audit_root: Path,  # noqa: ARG001
) -> None:
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    monkeypatch.setattr(_ratchet_direction, "detect_ratchet_loosening", lambda *_a, **_kw: ())
    monkeypatch.setattr(base_mod, "run_agent_streaming", _fake_streaming({"n": 0}))
    prior = support.impl_handover(workspace)
    seed_and_run(TEAM, workspace, prior, lambda _e: None)

    entries = [
        e for e in _audit_entries(tmp_path) if e.get("action") == "ratchet_loosening_detected"
    ]
    assert entries == []


def test_no_ratchet_audit_entry_when_base_unresolved(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, tmp_path: Path,
    audit_root: Path,  # noqa: ARG001
) -> None:
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    monkeypatch.setattr(_ratchet_direction, "detect_ratchet_loosening", lambda *_a, **_kw: None)
    monkeypatch.setattr(base_mod, "run_agent_streaming", _fake_streaming({"n": 0}))
    prior = support.impl_handover(workspace)
    seed_and_run(TEAM, workspace, prior, lambda _e: None)

    entries = [
        e for e in _audit_entries(tmp_path) if e.get("action") == "ratchet_loosening_detected"
    ]
    assert entries == []


def test_unresolved_base_block_reaches_diff_reviewer_prompt(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path,  # noqa: ARG001
) -> None:
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    monkeypatch.setattr(_ratchet_direction, "detect_ratchet_loosening", lambda *_a, **_kw: None)
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
    assert "base version could not be resolved" in diff_reviewer_call[1]


def test_ratchet_finding_does_not_affect_verdict_or_readiness(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, audit_root: Path,  # noqa: ARG001
) -> None:
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    findings = (
        _ratchet_direction.Loosening(
            "tests/size_baseline.py", "TEST_FILE_CAP raised from 1000 to 2000",
        ),
    )

    def _run(suffix: str, loosening: tuple[_ratchet_direction.Loosening, ...]) -> Handover:
        ws = support.make_workspace(tmp_path / suffix)
        monkeypatch.setattr(
            _ratchet_direction, "detect_ratchet_loosening", lambda *_a, **_kw: loosening,
        )
        monkeypatch.setattr(base_mod, "run_agent_streaming", _fake_streaming({"n": 0}))
        prior = support.impl_handover(ws)
        return seed_and_run(TEAM, ws, prior, lambda _e: None)

    with_finding = _run("with", findings)
    without_finding = _run("without", ())

    assert with_finding.to_team_suggestions == without_finding.to_team_suggestions
    assert [d.statement for d in with_finding.decisions] == [
        d.statement for d in without_finding.decisions
    ]
    assert with_finding.pr_number == without_finding.pr_number
    assert with_finding.next_hop == without_finding.next_hop


def test_no_block_at_all_when_no_findings(
    monkeypatch: pytest.MonkeyPatch, workspace: Workspace, audit_root: Path,  # noqa: ARG001
) -> None:
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    monkeypatch.setattr(_ratchet_direction, "detect_ratchet_loosening", lambda *_a, **_kw: ())
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
    assert "Ratchet direction observation" not in diff_reviewer_call[1]
