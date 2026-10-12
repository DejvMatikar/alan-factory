from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan import process_runner
from alan.claude_cli import AgentStreamingResult
from alan.handover import Handover, save_handover
from alan.teams import _base as base_mod
from alan.teams import _pr_branch
from alan.teams.verification import TEAM
from alan.workspace import Requirement, Workspace
from tests import _teams_verification_support as support
from tests._helpers import seed_and_run
from tests.forge_stubs import gh_unreachable

_impl_handover = support.impl_handover
_REQ_TEXT = "the endpoint must be idempotent"

_REQUEST_CHANGES_JUDGE = (
    "## Verdict\n\nVERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 0\n\n"
    "## Rationale\nnot idempotent"
)
_CAPABILITY_BOUNDARY_JUDGE = (
    "## Verdict\n\nVERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 0\n\n"
    "## Rationale\nnot idempotent\n"
    "NO_FLEET_ROLE_CAN_FIX: needs a human to rotate credentials.\n"
)


@pytest.fixture(autouse=True)
def _stub_external_gates_default(monkeypatch: pytest.MonkeyPatch) -> None:
    support.stub_external_gates(monkeypatch)


@pytest.fixture(autouse=True)
def _stub_checkout_internals_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    support.stub_checkout_internals(monkeypatch, tmp_path)


@pytest.fixture(autouse=True)
def _gh_down() -> None:
    gh_unreachable()


@pytest.fixture(autouse=True)
def _reset_clause_audit_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALAN_CLAUSE_AUDIT", raising=False)


@pytest.fixture
def audit_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return support.set_audit_root(tmp_path, monkeypatch)


def _workspace(tmp_path: Path, *, requirements: list[Requirement]) -> Workspace:
    ws = support.make_workspace(tmp_path)
    meta = ws.meta.model_copy(update={"requirements": requirements})
    return ws.model_copy(update={"meta": meta})


def _role_fake(outputs_by_role: dict[str, str]) -> Callable[..., AgentStreamingResult]:
    def fake(
        system_prompt: str,
        user_text: str,  # noqa: ARG001
        *,
        model: str = "sonnet",  # noqa: ARG001
        stream_path: Path | None = None,
        **_kwargs: object,
    ) -> AgentStreamingResult:
        role = next(
            (name for name in outputs_by_role if system_prompt.startswith(f"You are the {name}")),
            None,
        )
        text = outputs_by_role[role] if role is not None else "DIFF_FINDINGS_CRITICAL: 0"
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            stream_path.write_text(json.dumps({"type": "result", "result": text}) + "\n")
        return AgentStreamingResult(result_text=text)

    return fake


def _fix_handover(ws: Workspace, *, chain_id: str, pr_number: int = 42) -> Handover:
    return Handover(
        handover_id=f"handover_{ws.task_id}_fix1",
        handover_chain_id=chain_id,
        handover_type="forward",
        task_id=ws.task_id,
        from_team="implementation_fix",
        from_invocation_id="inv_fix_001",
        to_team_suggestions=["verification"],
        iteration=2,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="attempted a fix",
        pr_number=pr_number,
    )


def _stub_no_gh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_pr_branch, "fetch_diff_files", lambda *_a, **_kw: frozenset())
    monkeypatch.setattr(
        process_runner, "run_subprocess",
        lambda *_a, **_kw: subprocess.CompletedProcess([], 1, stdout="", stderr=""),
    )


def _run_verification(
    ws: Workspace,
    monkeypatch: pytest.MonkeyPatch,
    prior: Handover,
    req: Requirement,
    *,
    judge_output: str = _REQUEST_CHANGES_JUDGE,
    clause_auditor_verdict: str = "FAIL",
) -> Handover:
    auditor_output = (
        f"CLAUSE {req.id}: {clause_auditor_verdict} — POST /x is not idempotent\n"
        f"CLAUSE_FAILURES: {1 if clause_auditor_verdict == 'FAIL' else 0}\n"
    )
    fake = _role_fake({
        "diff_reviewer": "DIFF_FINDINGS_CRITICAL: 0",
        "scenario_runner": "no scenarios",
        "clause_auditor": auditor_output,
        "judge": judge_output,
    })
    monkeypatch.setattr(base_mod, "run_agent_streaming", fake)
    return seed_and_run(TEAM, ws, prior, lambda _e: None)


def test_run_one_carries_clause_outcome_with_no_stall(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, audit_root: Path  # noqa: ARG001
) -> None:
    req = Requirement(text=_REQ_TEXT)
    ws = _workspace(tmp_path, requirements=[req])
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    _stub_no_gh(monkeypatch)
    prior = _impl_handover(ws)
    handover = _run_verification(ws, monkeypatch, prior, req)
    assert handover.to_team_suggestions == ["implementation_fix"]
    assert handover.clause_outcome is not None
    assert handover.clause_outcome.actionable_ids == (req.id,)
    assert handover.open_questions == []


def test_second_consecutive_stall_escalates_instead_of_dispatching_fix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, audit_root: Path
) -> None:
    req = Requirement(text=_REQ_TEXT)
    ws = _workspace(tmp_path, requirements=[req])
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    _stub_no_gh(monkeypatch)

    prior1 = _impl_handover(ws)
    handover1 = _run_verification(ws, monkeypatch, prior1, req)
    assert handover1.to_team_suggestions == ["implementation_fix"]
    save_handover(ws, handover1)

    prior2 = _fix_handover(ws, chain_id=handover1.handover_chain_id)
    handover2 = _run_verification(ws, monkeypatch, prior2, req)

    assert handover2.to_team_suggestions == []
    assert len(handover2.open_questions) == 1
    oq = handover2.open_questions[0]
    assert oq.id == f"clause-stall-{req.id}"
    assert oq.blocking is True
    assert oq.resolver_role == "human"
    assert oq.question_class == "verification_escalation"
    entries = [
        json.loads(line)
        for line in (audit_root / "verification_audit.jsonl").read_text().splitlines()
    ]
    assert any(e["action"] == "clause_stall_detected" for e in entries)


def test_capability_boundary_precedes_clause_stall(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, audit_root: Path
) -> None:
    req = Requirement(text=_REQ_TEXT)
    ws = _workspace(tmp_path, requirements=[req])
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    _stub_no_gh(monkeypatch)

    prior1 = _impl_handover(ws)
    handover1 = _run_verification(ws, monkeypatch, prior1, req)
    save_handover(ws, handover1)

    prior2 = _fix_handover(ws, chain_id=handover1.handover_chain_id)
    handover2 = _run_verification(
        ws, monkeypatch, prior2, req, judge_output=_CAPABILITY_BOUNDARY_JUDGE,
    )

    assert handover2.to_team_suggestions == []
    assert len(handover2.open_questions) == 1
    oq = handover2.open_questions[0]
    assert not oq.id.startswith("clause-stall-")
    assert oq.recommended == (
        "Resolve this outside the fix-worker loop, then re-dispatch verification."
    )


def test_escalate_unactionable_precedes_clause_stall(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, audit_root: Path
) -> None:
    req = Requirement(text=_REQ_TEXT)
    ws = _workspace(tmp_path, requirements=[req])
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    _stub_no_gh(monkeypatch)

    prior1 = _impl_handover(ws)
    handover1 = _run_verification(ws, monkeypatch, prior1, req)
    save_handover(ws, handover1)

    prior2 = _fix_handover(ws, chain_id=handover1.handover_chain_id)
    pr_state_auditor_output = (
        f"CLAUSE {req.id}: FAIL — PR_STATE: isDraft is false, clause requires "
        "a draft PR\nCLAUSE_FAILURES: 1\n"
    )
    fake = _role_fake({
        "diff_reviewer": "DIFF_FINDINGS_CRITICAL: 0",
        "scenario_runner": "no scenarios",
        "clause_auditor": pr_state_auditor_output,
        "judge": _REQUEST_CHANGES_JUDGE,
    })
    monkeypatch.setattr(base_mod, "run_agent_streaming", fake)
    handover2 = seed_and_run(TEAM, ws, prior2, lambda _e: None)

    assert handover2.to_team_suggestions == []
    assert len(handover2.open_questions) == 1
    oq = handover2.open_questions[0]
    assert oq.id == "clause-audit-pr-state"


def test_sole_operator_only_batch_escalates_without_fix_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, audit_root: Path
) -> None:
    req = Requirement(text=_REQ_TEXT)
    ws = _workspace(tmp_path, requirements=[req])
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    _stub_no_gh(monkeypatch)

    prior = _impl_handover(ws)
    operator_only_auditor_output = (
        f"CLAUSE {req.id}: FAIL — OPERATOR_ONLY: needs a live traffic measurement\n"
        "CLAUSE_FAILURES: 1\n"
    )
    fake = _role_fake({
        "diff_reviewer": "DIFF_FINDINGS_CRITICAL: 0",
        "scenario_runner": "no scenarios",
        "clause_auditor": operator_only_auditor_output,
        "judge": _REQUEST_CHANGES_JUDGE,
    })
    monkeypatch.setattr(base_mod, "run_agent_streaming", fake)
    handover = seed_and_run(TEAM, ws, prior, lambda _e: None)

    assert handover.to_team_suggestions == []
    assert len(handover.open_questions) == 1
    oq = handover.open_questions[0]
    assert oq.id == "clause-audit-operator-only"
    assert oq.resolver_role == "human"
    assert oq.blocking is True
    assert "tracking issue" in " ".join(oq.proposed_answer_space).lower()


def test_capability_boundary_precedes_operator_only_escalation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, audit_root: Path
) -> None:
    req = Requirement(text=_REQ_TEXT)
    ws = _workspace(tmp_path, requirements=[req])
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    _stub_no_gh(monkeypatch)

    prior = _impl_handover(ws)
    operator_only_auditor_output = (
        f"CLAUSE {req.id}: FAIL — OPERATOR_ONLY: needs a live traffic measurement\n"
        "CLAUSE_FAILURES: 1\n"
    )
    fake = _role_fake({
        "diff_reviewer": "DIFF_FINDINGS_CRITICAL: 0",
        "scenario_runner": "no scenarios",
        "clause_auditor": operator_only_auditor_output,
        "judge": _CAPABILITY_BOUNDARY_JUDGE,
    })
    monkeypatch.setattr(base_mod, "run_agent_streaming", fake)
    handover = seed_and_run(TEAM, ws, prior, lambda _e: None)

    assert handover.to_team_suggestions == []
    assert len(handover.open_questions) == 1
    oq = handover.open_questions[0]
    assert not oq.id.startswith("clause-audit-")
    assert oq.recommended == (
        "Resolve this outside the fix-worker loop, then re-dispatch verification."
    )


def test_genuinely_fixed_clause_absent_from_run_two_routes_normally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, audit_root: Path
) -> None:
    req = Requirement(text=_REQ_TEXT)
    ws = _workspace(tmp_path, requirements=[req])
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    _stub_no_gh(monkeypatch)

    prior1 = _impl_handover(ws)
    handover1 = _run_verification(ws, monkeypatch, prior1, req)
    save_handover(ws, handover1)

    prior2 = _fix_handover(ws, chain_id=handover1.handover_chain_id)
    judge_still_critical = (
        "## Verdict\n\nVERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 1\n\n"
        "## Rationale\nan unrelated defect"
    )
    handover2 = _run_verification(
        ws, monkeypatch, prior2, req,
        judge_output=judge_still_critical, clause_auditor_verdict="PASS",
    )

    assert handover2.to_team_suggestions == ["implementation_fix"]
    assert handover2.open_questions == []
    assert handover2.clause_outcome is None


def _run_verification_failing(
    ws: Workspace,
    monkeypatch: pytest.MonkeyPatch,
    prior: Handover,
    failing: list[Requirement],
) -> Handover:
    lines = "".join(
        f"CLAUSE {r.id}: FAIL — POST /x is not idempotent\n" for r in failing
    )
    auditor_output = f"{lines}CLAUSE_FAILURES: {len(failing)}\n"
    fake = _role_fake({
        "diff_reviewer": "DIFF_FINDINGS_CRITICAL: 0",
        "scenario_runner": "no scenarios",
        "clause_auditor": auditor_output,
        "judge": _REQUEST_CHANGES_JUDGE,
    })
    monkeypatch.setattr(base_mod, "run_agent_streaming", fake)
    return seed_and_run(TEAM, ws, prior, lambda _e: None)


def test_partially_overlapping_actionable_sets_escalate_instead_of_dispatching_fix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, audit_root: Path  # noqa: ARG001
) -> None:
    req_a = Requirement(text=_REQ_TEXT)
    req_b = Requirement(text="the audit-directory wrappers are gone")
    ws = _workspace(tmp_path, requirements=[req_a, req_b])
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    _stub_no_gh(monkeypatch)

    handover1 = _run_verification_failing(
        ws, monkeypatch, _impl_handover(ws), [req_a, req_b],
    )
    save_handover(ws, handover1)

    prior2 = _fix_handover(ws, chain_id=handover1.handover_chain_id)
    handover2 = _run_verification_failing(ws, monkeypatch, prior2, [req_a])

    assert handover2.to_team_suggestions == []
    assert len(handover2.open_questions) == 1
    assert handover2.open_questions[0].id == f"clause-stall-{req_a.id}"
    assert handover2.clause_outcome is not None
    assert handover2.clause_outcome.actionable_ids == (req_a.id,)


def test_stall_escalation_carries_every_currently_actionable_clause_for_withdrawal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, audit_root: Path  # noqa: ARG001
) -> None:
    req_a = Requirement(text=_REQ_TEXT)
    req_b = Requirement(text="the audit-directory wrappers are gone")
    ws = _workspace(tmp_path, requirements=[req_a, req_b])
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    _stub_no_gh(monkeypatch)

    handover1 = _run_verification_failing(ws, monkeypatch, _impl_handover(ws), [req_a])
    save_handover(ws, handover1)

    prior2 = _fix_handover(ws, chain_id=handover1.handover_chain_id)
    handover2 = _run_verification_failing(ws, monkeypatch, prior2, [req_a, req_b])

    oq = handover2.open_questions[0]
    assert oq.id == f"clause-stall-{req_a.id}"
    assert req_a.id in oq.question
    assert req_b.id in oq.question
    assert handover2.clause_outcome is not None
    assert set(handover2.clause_outcome.actionable_ids) == {req_a.id, req_b.id}


def test_disjoint_actionable_sets_route_to_implementation_fix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, audit_root: Path  # noqa: ARG001
) -> None:
    req_a = Requirement(text=_REQ_TEXT)
    req_b = Requirement(text="the audit-directory wrappers are gone")
    ws = _workspace(tmp_path, requirements=[req_a, req_b])
    monkeypatch.setenv("ALAN_VERIFICATION_DRY_RUN", "1")
    _stub_no_gh(monkeypatch)

    handover1 = _run_verification_failing(ws, monkeypatch, _impl_handover(ws), [req_a])
    save_handover(ws, handover1)

    prior2 = _fix_handover(ws, chain_id=handover1.handover_chain_id)
    handover2 = _run_verification_failing(ws, monkeypatch, prior2, [req_b])

    assert handover2.to_team_suggestions == ["implementation_fix"]
    assert handover2.open_questions == []
