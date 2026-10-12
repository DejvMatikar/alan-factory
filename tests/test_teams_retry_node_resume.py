from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from alan import workflow as workflow_mod
from alan import workflow_interrupt
from alan.claude_cli import AgentStreamingResult
from alan.engine_config import EngineConfig, Posture, RepoIdentity
from alan.teams import _base as base_mod
from alan.teams import _clause_audit as ca_mod
from alan.teams import verification as verif_mod
from alan.teams._base import WorkerOverrides
from alan.workspace import EngineSnapshot, Requirement, Workspace, create, stamp_engine_snapshot


@pytest.fixture
def audit_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(root))
    return root


def _ws(tmp_path: Path, *, requirements: list[Requirement] | None = None) -> Workspace:
    base = create("retry-resume-ws", "d", root=tmp_path / "ws")
    meta = base.meta.model_copy(update={"requirements": requirements})
    return base.model_copy(update={"meta": meta})


def _checkout(tmp_path: Path) -> Path:
    checkout = tmp_path / "checkout"
    (checkout / ".git").mkdir(parents=True, exist_ok=True)
    return checkout


def _grounded_overrides(checkout: Path) -> WorkerOverrides:
    return WorkerOverrides(cwd=checkout, allowed_tools=["Read", "Bash(dotnet:*)"])


def _fixed_result_fake(text: str) -> object:
    def fake(*_a: object, **kwargs: object) -> AgentStreamingResult:
        stream_path = kwargs.get("stream_path")
        if isinstance(stream_path, Path):
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            stream_path.write_text(json.dumps({"type": "result", "result": text}) + "\n")
        return AgentStreamingResult(result_text=text)

    return fake


def _capturing_fake(text: str, captured: dict[str, object]) -> object:
    def fake(*_a: object, **kwargs: object) -> AgentStreamingResult:
        captured["cwd"] = getattr(kwargs.get("proc_config"), "cwd", None)
        captured["allowed_tools"] = kwargs.get("allowed_tools")
        captured["resume_session_id"] = kwargs.get("resume_session_id")
        stream_path = kwargs.get("stream_path")
        if isinstance(stream_path, Path):
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            stream_path.write_text(json.dumps({"type": "result", "result": text}) + "\n")
        return AgentStreamingResult(result_text=text)

    return fake


def test_clause_auditor_retry_runs_in_supplied_checkout_with_supplied_grant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, audit_root: Path  # noqa: ARG001
) -> None:
    req = Requirement(text="A")
    ws = _ws(tmp_path, requirements=[req])
    inv_id = workflow_mod.seed_pending_workflow(ws, team="verification", tasks=[])
    graph = workflow_mod.load_graph(ws, inv_id)
    checkout = _checkout(tmp_path)

    captured: dict[str, object] = {}
    monkeypatch.setattr(
        base_mod, "run_agent_streaming",
        _capturing_fake(f"CLAUSE {req.id}: PASS — ok\nCLAUSE_FAILURES: 0\n", captured),
    )
    result = ca_mod._run_clause_auditor_retry(  # noqa: SLF001
        ws, graph, inv_id, lambda _e: None, "base text", [], None, "not parseable",
        overrides=_grounded_overrides(checkout),
    )
    assert captured["cwd"] == checkout
    assert captured["allowed_tools"] == ["Read", "Bash(dotnet:*)"]
    assert captured["allowed_tools"] != list(base_mod.BASE_ALLOWED_TOOLS)
    assert "PASS" in result


def test_clause_auditor_retry_resumed_in_flight_runs_in_supplied_checkout_with_supplied_grant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, audit_root: Path  # noqa: ARG001
) -> None:
    req = Requirement(text="A")
    ws = _ws(tmp_path, requirements=[req])
    cfg = EngineConfig(
        repo=RepoIdentity(owner="acme", repo="widget"),
        base_tools=["Read", "Bash(dotnet:*)"],
        posture=Posture.autonomous,
    )
    ws = stamp_engine_snapshot(
        ws, EngineSnapshot(posture=Posture.autonomous, transition_graph={}, config=cfg)
    )
    inv_id = workflow_mod.seed_pending_workflow(ws, team="verification", tasks=[])
    graph = workflow_mod.load_graph(ws, inv_id)
    graph.start_node(
        ca_mod._CLAUSE_AUDITOR_RETRY_NODE_ID, role="clause_auditor", model="sonnet",
        depends_on=["clause_auditor"],
    )
    graph.set_session_id(ca_mod._CLAUSE_AUDITOR_RETRY_NODE_ID, "sid-inflight")
    graph.mark_interrupted(ca_mod._CLAUSE_AUDITOR_RETRY_NODE_ID, error="interrupted_by_restart")
    workflow_mod.save_workflow(ws, graph.doc)

    checkout = _checkout(tmp_path)
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        base_mod, "run_agent_streaming",
        _capturing_fake(f"CLAUSE {req.id}: PASS — resumed\nCLAUSE_FAILURES: 0\n", captured),
    )
    resumed_graph = workflow_mod.load_graph(ws, inv_id)
    token = verif_mod._grounded_cwd_var.set(checkout)  # noqa: SLF001
    try:
        overrides = verif_mod.TEAM._role_overrides("clause_auditor", ws)  # noqa: SLF001
        result = ca_mod._run_clause_auditor_retry(  # noqa: SLF001
            ws, resumed_graph, inv_id, lambda _e: None, "base text", [], None, "not parseable",
            overrides=overrides,
        )
    finally:
        verif_mod._grounded_cwd_var.reset(token)  # noqa: SLF001
    assert captured["resume_session_id"] == "sid-inflight"
    assert captured["cwd"] == checkout
    assert (overrides.allowed_tools or [])[:2] == ["Read", "Bash(dotnet:*)"]
    assert captured["allowed_tools"] == overrides.allowed_tools
    assert "resumed" in result


def test_judge_retry_uses_run_or_resume_node(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, audit_root: Path  # noqa: ARG001
) -> None:
    ws = _ws(tmp_path)
    inv_id = workflow_mod.seed_pending_workflow(ws, team="verification", tasks=[])
    graph = workflow_mod.load_graph(ws, inv_id)
    checkout = _checkout(tmp_path)

    calls: list[tuple[str, object]] = []
    original = base_mod.run_or_resume_node

    def spy(*args: Any, **kwargs: Any) -> str:
        calls.append((str(kwargs.get("node_id")), kwargs.get("overrides")))
        return original(*args, **kwargs)

    monkeypatch.setattr(verif_mod, "run_or_resume_node", spy)
    monkeypatch.setattr(
        base_mod, "run_agent_streaming",
        _fixed_result_fake("VERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 0\n"),
    )
    outputs = [("diff_reviewer", "x"), ("scenario_runner", "y"), ("judge", "no verdict")]
    token = verif_mod._grounded_cwd_var.set(checkout)  # noqa: SLF001
    try:
        result = verif_mod.TEAM._run_judge_retry(  # noqa: SLF001
            ws, graph, inv_id, lambda _e: None, "base text", outputs, None,
            verif_mod.ROLES, reason="missing_verdict",
        )
    finally:
        verif_mod._grounded_cwd_var.reset(token)  # noqa: SLF001
    assert calls
    node_id, overrides = calls[0]
    assert node_id == verif_mod._JUDGE_RETRY_NODE_ID
    assert overrides is not None
    assert "VERDICT: APPROVE" in result


def test_clause_auditor_retry_resume_reuses_recorded_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, audit_root: Path  # noqa: ARG001
) -> None:
    req = Requirement(text="A")
    ws = _ws(tmp_path, requirements=[req])
    inv_id = workflow_mod.seed_pending_workflow(ws, team="verification", tasks=[])
    graph = workflow_mod.load_graph(ws, inv_id)
    monkeypatch.setattr(
        base_mod, "run_agent_streaming",
        _fixed_result_fake(f"CLAUSE {req.id}: FAIL — retried\nCLAUSE_FAILURES: 1\n"),
    )
    ca_mod._run_clause_auditor_retry(  # noqa: SLF001
        ws, graph, inv_id, lambda _e: None, "base text", [], None, "not parseable",
        overrides=_grounded_overrides(_checkout(tmp_path)),
    )
    doc = workflow_mod.load_workflow(workflow_mod.workflow_path(ws, inv_id))
    assert doc.tasks[0].status == "success"

    resumed_graph = workflow_mod.load_graph(ws, inv_id)
    monkeypatch.setattr(
        base_mod, "run_agent_streaming",
        lambda *_a, **_kw: pytest.fail("must not re-invoke the worker on resume"),
    )
    result = ca_mod._run_clause_auditor_retry(  # noqa: SLF001
        ws, resumed_graph, inv_id, lambda _e: None, "base text", [], None, "not parseable",
        overrides=_grounded_overrides(_checkout(tmp_path)),
    )
    assert "retried" in result


def test_judge_retry_resume_reuses_recorded_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, audit_root: Path  # noqa: ARG001
) -> None:
    ws = _ws(tmp_path)
    inv_id = workflow_mod.seed_pending_workflow(ws, team="verification", tasks=[])
    graph = workflow_mod.load_graph(ws, inv_id)
    checkout = _checkout(tmp_path)
    monkeypatch.setattr(
        base_mod, "run_agent_streaming",
        _fixed_result_fake("VERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 0\n"),
    )
    outputs = [("diff_reviewer", "x"), ("scenario_runner", "y"), ("judge", "no verdict")]
    token = verif_mod._grounded_cwd_var.set(checkout)  # noqa: SLF001
    try:
        verif_mod.TEAM._run_judge_retry(  # noqa: SLF001
            ws, graph, inv_id, lambda _e: None, "base text", outputs, None,
            verif_mod.ROLES, reason="missing_verdict",
        )
    finally:
        verif_mod._grounded_cwd_var.reset(token)  # noqa: SLF001
    doc = workflow_mod.load_workflow(workflow_mod.workflow_path(ws, inv_id))
    assert doc.tasks[0].status == "success"

    resumed_graph = workflow_mod.load_graph(ws, inv_id)
    monkeypatch.setattr(
        base_mod, "run_agent_streaming",
        lambda *_a, **_kw: pytest.fail("must not re-invoke the worker on resume"),
    )
    token2 = verif_mod._grounded_cwd_var.set(checkout)  # noqa: SLF001
    try:
        result = verif_mod.TEAM._run_judge_retry(  # noqa: SLF001
            ws, resumed_graph, inv_id, lambda _e: None, "base text", outputs, None,
            verif_mod.ROLES, reason="missing_verdict",
        )
    finally:
        verif_mod._grounded_cwd_var.reset(token2)  # noqa: SLF001
    assert "VERDICT: APPROVE" in result


def test_retry_node_resume_raises_when_recorded_result_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, audit_root: Path  # noqa: ARG001
) -> None:
    req = Requirement(text="A")
    ws = _ws(tmp_path, requirements=[req])
    inv_id = workflow_mod.seed_pending_workflow(ws, team="verification", tasks=[])
    graph = workflow_mod.load_graph(ws, inv_id)
    monkeypatch.setattr(
        base_mod, "run_agent_streaming",
        _fixed_result_fake(f"CLAUSE {req.id}: FAIL — retried\nCLAUSE_FAILURES: 1\n"),
    )
    ca_mod._run_clause_auditor_retry(  # noqa: SLF001
        ws, graph, inv_id, lambda _e: None, "base text", [], None, "not parseable",
        overrides=_grounded_overrides(_checkout(tmp_path)),
    )
    workflow_mod.result_path(ws, inv_id, ca_mod._CLAUSE_AUDITOR_RETRY_NODE_ID).unlink()

    resumed_graph = workflow_mod.load_graph(ws, inv_id)
    with pytest.raises(workflow_mod.WorkflowError, match="unreadable"):
        ca_mod._run_clause_auditor_retry(  # noqa: SLF001
            ws, resumed_graph, inv_id, lambda _e: None, "base text", [], None, "not parseable",
            overrides=_grounded_overrides(_checkout(tmp_path)),
        )


def test_clause_auditor_retry_node_interrupted_in_flight_resumes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, audit_root: Path  # noqa: ARG001
) -> None:
    req = Requirement(text="A")
    ws = _ws(tmp_path, requirements=[req])
    inv_id = workflow_mod.seed_pending_workflow(ws, team="verification", tasks=[])
    graph = workflow_mod.load_graph(ws, inv_id)
    graph.start_node(
        ca_mod._CLAUSE_AUDITOR_RETRY_NODE_ID, role="clause_auditor", model="sonnet",
        depends_on=["clause_auditor"],
    )
    graph.set_session_id(ca_mod._CLAUSE_AUDITOR_RETRY_NODE_ID, "sid-inflight")
    graph.mark_interrupted(ca_mod._CLAUSE_AUDITOR_RETRY_NODE_ID, error="interrupted_by_restart")
    workflow_mod.save_workflow(ws, graph.doc)

    captured: list[str | None] = []

    def fake(*_a: object, **kwargs: object) -> AgentStreamingResult:
        captured.append(kwargs.get("resume_session_id"))  # type: ignore[arg-type]
        stream_path = kwargs.get("stream_path")
        text = f"CLAUSE {req.id}: PASS — resumed\nCLAUSE_FAILURES: 0\n"
        if isinstance(stream_path, Path):
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            stream_path.write_text(json.dumps({"type": "result", "result": text}) + "\n")
        return AgentStreamingResult(result_text=text)

    monkeypatch.setattr(base_mod, "run_agent_streaming", fake)
    resumed_graph = workflow_mod.load_graph(ws, inv_id)
    result = ca_mod._run_clause_auditor_retry(  # noqa: SLF001
        ws, resumed_graph, inv_id, lambda _e: None, "base text", [], None, "not parseable",
        overrides=_grounded_overrides(_checkout(tmp_path)),
    )
    assert captured == ["sid-inflight"]
    assert "resumed" in result


def test_judge_retry_node_interrupted_in_flight_resumes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, audit_root: Path  # noqa: ARG001
) -> None:
    ws = _ws(tmp_path)
    inv_id = workflow_mod.seed_pending_workflow(ws, team="verification", tasks=[])
    graph = workflow_mod.load_graph(ws, inv_id)
    graph.start_node(
        verif_mod._JUDGE_RETRY_NODE_ID, role="judge", model="opus", depends_on=["judge"],
    )
    graph.set_session_id(verif_mod._JUDGE_RETRY_NODE_ID, "sid-inflight-judge")
    graph.mark_interrupted(verif_mod._JUDGE_RETRY_NODE_ID, error="interrupted_by_restart")
    workflow_mod.save_workflow(ws, graph.doc)
    checkout = _checkout(tmp_path)

    captured: list[str | None] = []

    def fake(*_a: object, **kwargs: object) -> AgentStreamingResult:
        captured.append(kwargs.get("resume_session_id"))  # type: ignore[arg-type]
        stream_path = kwargs.get("stream_path")
        text = "VERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 0\n"
        if isinstance(stream_path, Path):
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            stream_path.write_text(json.dumps({"type": "result", "result": text}) + "\n")
        return AgentStreamingResult(result_text=text)

    monkeypatch.setattr(base_mod, "run_agent_streaming", fake)
    resumed_graph = workflow_mod.load_graph(ws, inv_id)
    token = verif_mod._grounded_cwd_var.set(checkout)  # noqa: SLF001
    try:
        result = verif_mod.TEAM._run_judge_retry(  # noqa: SLF001
            ws, resumed_graph, inv_id, lambda _e: None, "base text",
            [("diff_reviewer", "x"), ("scenario_runner", "y"), ("judge", "no verdict")],
            None, verif_mod.ROLES, reason="missing_verdict",
        )
    finally:
        verif_mod._grounded_cwd_var.reset(token)  # noqa: SLF001
    assert captured == ["sid-inflight-judge"]
    assert "VERDICT: APPROVE" in result


def test_retry_node_without_captured_session_id_still_fails_on_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, audit_root: Path  # noqa: ARG001
) -> None:
    req = Requirement(text="A")
    ws = _ws(tmp_path, requirements=[req])
    inv_id = workflow_mod.seed_pending_workflow(ws, team="verification", tasks=[])
    graph = workflow_mod.load_graph(ws, inv_id)
    graph.start_node(
        ca_mod._CLAUSE_AUDITOR_RETRY_NODE_ID, role="clause_auditor", model="sonnet",
        depends_on=["clause_auditor"],
    )
    workflow_mod.save_workflow(ws, graph.doc)
    workflow_interrupt.interrupt_workflow_on_disk(ws, inv_id, resumable=True)
    doc = workflow_mod.load_workflow(workflow_mod.workflow_path(ws, inv_id))
    assert doc.tasks[0].status == "failed"

    monkeypatch.setattr(
        base_mod, "run_agent_streaming",
        lambda *_a, **_kw: pytest.fail("must not spawn a worker for an unrecoverable node"),
    )
    resumed_graph = workflow_mod.load_graph(ws, inv_id)
    with pytest.raises(workflow_mod.WorkflowError, match="duplicate node id"):
        ca_mod._run_clause_auditor_retry(  # noqa: SLF001
            ws, resumed_graph, inv_id, lambda _e: None, "base text", [], None, "not parseable",
            overrides=_grounded_overrides(_checkout(tmp_path)),
        )


def test_judge_verdict_missing_retry_audit_row_is_appended_per_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, audit_root: Path
) -> None:
    ws = _ws(tmp_path)
    inv_id = workflow_mod.seed_pending_workflow(ws, team="verification", tasks=[])
    checkout = _checkout(tmp_path)
    monkeypatch.setattr(
        base_mod, "run_agent_streaming",
        _fixed_result_fake("VERDICT: APPROVE\nDIFF_FINDINGS_CRITICAL: 0\n"),
    )
    outputs = [("diff_reviewer", "x"), ("scenario_runner", "y"), ("judge", "no verdict")]
    for _ in range(2):
        current_graph = workflow_mod.load_graph(ws, inv_id)
        token = verif_mod._grounded_cwd_var.set(checkout)  # noqa: SLF001
        try:
            verif_mod.TEAM._run_judge_retry(  # noqa: SLF001
                ws, current_graph, inv_id, lambda _e: None, "base text", outputs, None,
                verif_mod.ROLES, reason="missing_verdict",
            )
        finally:
            verif_mod._grounded_cwd_var.reset(token)  # noqa: SLF001
    entries = [
        json.loads(line)
        for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
    ]
    retry_rows = [e for e in entries if e["action"] == "judge_verdict_missing_retry"]
    assert len(retry_rows) == 2
