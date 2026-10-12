from __future__ import annotations

import alan.teams._verdict as _verdict
from alan.handover import Decision, Handover


def suggestions_for_verdict(
    judge_output: str, pr_number: int | None,
    *, gate_flaky_only: bool = False, gate_dirty: bool = False,
    gate_non_actionable: bool = False,
) -> tuple[str, ...]:
    verdict = _verdict.effective_verdict(judge_output)
    if verdict == "REQUEST_CHANGES":
        gate_owned = any((gate_dirty, gate_flaky_only, gate_non_actionable))
        if gate_owned and pr_number is not None:
            return ("pr_merge",)
        return ("implementation_fix",)
    if verdict == "APPROVE" and pr_number is not None:
        return ("pr_merge",)
    return ()


def _decision_from_judge(judge_output: str, verdict: _verdict.Verdict) -> Decision:
    first_line = _first_nonblank_line(judge_output, fallback="verification ran")
    return Decision(
        id="verif-01",
        statement=f"verification verdict: {verdict}",
        rationale=first_line[:300],
        confidence=0.9,
        reversibility="high",
    )


def _approve_decision(pr_number: int, judge_output: str) -> Decision:
    first_line = _first_nonblank_line(judge_output, fallback="judge approved")
    return Decision(
        id="verif-approve",
        statement=f"judge approved PR #{pr_number}",
        rationale=first_line[:300],
        confidence=1.0,
        reversibility="low",
    )


def _first_nonblank_line(text: str, *, fallback: str) -> str:
    return next(
        (line.strip() for line in text.splitlines() if line.strip()),
        fallback,
    )


def _verdict_decisions(  # pyright: ignore[reportUnusedFunction]
    judge_output: str,
    suggestions: tuple[str, ...],
    pr_number: int | None,
) -> list[Decision]:
    if "implementation_fix" in suggestions:
        verdict = _verdict.effective_verdict(judge_output) or "REQUEST_CHANGES"
        return [_decision_from_judge(judge_output, verdict)]
    if "pr_merge" in suggestions and pr_number is not None:
        if _verdict.effective_verdict(judge_output) == "REQUEST_CHANGES":
            return [_decision_from_judge(judge_output, "REQUEST_CHANGES")]
        return [_approve_decision(pr_number, judge_output)]
    return []


def _pr_precondition_reason(  # pyright: ignore[reportUnusedFunction]
    pr_number: int | None, last_ref: Handover | None
) -> str:
    if pr_number is None:
        base = (
            "verification dispatched without a resolvable PR (pr_number=None); "
            "cannot review a closed, unknown, or missing PR — escalating to human."
        )
    else:
        base = (
            f"verification dispatched with pr_number={pr_number!r} but GitHub "
            "confirms it cannot be resolved: not found, or its head branch is "
            "gone (closed or merged with the branch deleted); cannot review a "
            "missing PR — escalating to human."
        )
    if last_ref is None:
        return f"{base} No prior handover in this chain carried a pr_number."
    return (
        f"{base} Last handover carrying a pr_number: {last_ref.handover_id!r} "
        f"from {last_ref.from_team} (invocation {last_ref.from_invocation_id})."
    )
