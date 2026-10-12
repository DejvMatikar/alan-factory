from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager as _cm
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from alan import base_ref as base_ref_mod
from alan import worktree as worktree_mod
from alan.claude_cli import AgentStreamingResult, StreamEvent
from alan.engine_config import ForgeTarget, RepoIdentity
from alan.handover import Decision, Handover
from alan.teams import _base as base_mod
from alan.teams import _pr_body, _pr_branch, _pr_flake_ledger, _pr_post, _pr_ready
from alan.teams import _verification_merged_guard as _merged_guard
from alan.workspace import Workspace, create

_WIDGET_FORGE = ForgeTarget(
    repo=RepoIdentity(owner="acme-corp", repo="widget-app"),
    credential_env_name="ACME_WIDGET_GH_TOKEN",  # noqa: S106  # nosec B106
)

_DEFAULT_JUDGE_OUTPUT = (
    "## Verdict\n\n"
    "VERDICT: APPROVE\n"
    "DIFF_FINDINGS_CRITICAL: 0\n\n"
    "## Rationale\nLooks good."
)

_INLINE_JUDGE_OUTPUT = (
    "## Verdict\n\n"
    "VERDICT: REQUEST_CHANGES\n"
    "DIFF_FINDINGS_CRITICAL: 2\n\n"
    "## Findings\n"
    "see inline comments\n\n"
    "## INLINE_FINDINGS_YAML\n"
    "```yaml\n"
    "- path: alan/foo.py\n"
    "  line: 10\n"
    "  body: |\n"
    "    SQL injection in query()\n"
    "- path: alan/bar.py\n"
    "  line: 20\n"
    "  body: |\n"
    "    Missing null check\n"
    "```\n"
)

_E2E_GATE = "Frontend E2E (Playwright, blocking)"


def patch_streaming(
    monkeypatch: pytest.MonkeyPatch,
    *,
    judge_output: str = _DEFAULT_JUDGE_OUTPUT,
    reviewer_output: str | None = None,
) -> None:
    counter = {"n": 0}

    def fake(
        system_prompt: str,
        user_text: str,  # noqa: ARG001
        *,
        model: str = "sonnet",  # noqa: ARG001
        on_event: Callable[[StreamEvent], None] | None = None,
        stream_path: Path | None = None,
        max_turns: int = 10,  # noqa: ARG001
        cwd: object = None,  # noqa: ARG001,
        **_kwargs: object,
    ) -> AgentStreamingResult:
        counter["n"] += 1
        if system_prompt.startswith("You are the judge"):
            text = judge_output
        elif reviewer_output is not None and system_prompt.startswith("You are the diff_reviewer"):
            text = reviewer_output
        else:
            text = f"out-{counter['n']}"
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            stream_path.write_text(
                json.dumps({"type": "result", "result": text}) + "\n"
            )
        if on_event is not None:
            on_event({"type": "result", "result": text})
        return AgentStreamingResult(result_text=text)

    monkeypatch.setattr(base_mod, "run_agent_streaming", fake)


def make_workspace(tmp_path: Path) -> Workspace:
    return create("verify-task", "do verification", root=tmp_path / "ws")


def set_audit_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(root))
    monkeypatch.delenv("ALAN_VERIFIER_GH_TOKEN", raising=False)
    return root


def stub_external_gates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        _pr_ready,
        "check_pr_verifier_blockers",
        lambda _p, _t, **_kw: _pr_ready.PrReadiness(
            ok=True, merge_state_status="CLEAN", review_decision="APPROVED",
        ),
    )
    monkeypatch.setattr(_pr_post, "dismiss_stale_verifier_reviews", lambda _p, _t, **_kw: False)
    monkeypatch.setattr(_pr_branch, "fetch_failing_e2e_tests", lambda *_a, **_kw: None)
    monkeypatch.setattr(_pr_branch, "_iter_check_runs", lambda *_a, **_kw: None)
    monkeypatch.setattr(_pr_branch, "_fetch_pr_head_sha", lambda *_a, **_kw: None)
    monkeypatch.setattr(_pr_body, "fetch_pr_body", lambda *_a, **_kw: None)
    monkeypatch.setattr(_pr_flake_ledger, "fetch_pr_diff_patch", lambda *_a, **_kw: None)


def stub_checkout_internals(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_path = tmp_path / "grounded"
    (fake_path / ".git").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(_pr_branch, "resolve_existing_branch", lambda _p, _t, **_kw: "fake-branch")
    monkeypatch.setattr(base_ref_mod, "resolve", lambda *_a, **_kw: "HEAD")
    monkeypatch.setattr(_merged_guard, "pr_state_name", lambda *_a, **_kw: None)

    @_cm
    def _fake_worktree(*_a: object, **_k: object) -> Iterator[object]:
        handle = MagicMock()
        handle.path = fake_path
        handle.slot_index = None
        yield handle

    monkeypatch.setattr(worktree_mod, "implementation_worktree", _fake_worktree)


def impl_handover(workspace: Workspace, pr_number: int | None = 42) -> Handover:
    return Handover(
        handover_id="handover_impl_001",
        handover_type="forward",
        task_id=workspace.task_id,
        from_team="implementation",
        from_invocation_id="inv_001",
        to_team_suggestions=["verification"],
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        decisions=[
            Decision(
                id="impl-01",
                statement="abc1234 feat: a",
                rationale="see synthesis",
                confidence=0.8,
                reversibility="medium",
            )
        ],
        full_synthesis="implementation done",
        pr_number=pr_number,
    )


def blocked_readiness() -> _pr_ready.PrReadiness:
    return _pr_ready.PrReadiness(
        ok=False,
        merge_state_status="BLOCKED",
        review_decision="REVIEW_REQUIRED",
        failing_checks=("sonar",),
        pending_checks=(),
        failed_reasons=("mergeStateStatus=BLOCKED", "failing checks: sonar"),
    )


def dirty_readiness() -> _pr_ready.PrReadiness:
    return _pr_ready.PrReadiness(
        ok=False,
        merge_state_status="DIRTY",
        review_decision="APPROVED",
        failed_reasons=("mergeStateStatus=DIRTY",),
    )


def flaky_gate_readiness(
    failing: tuple[str, ...] = (_E2E_GATE,),
    merge_state: str = "BLOCKED",
    review: str = "REVIEW_REQUIRED",
) -> _pr_ready.PrReadiness:
    return _pr_ready.PrReadiness(
        ok=False,
        merge_state_status=merge_state,
        review_decision=review,
        failing_checks=failing,
        pending_checks=(),
        failed_reasons=("failing checks: " + ", ".join(failing),),
    )
