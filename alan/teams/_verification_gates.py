from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import alan.teams._pr_flake_ledger as _pr_flake_ledger
import alan.teams._pr_ready as _pr_ready
import alan.teams._pr_state_paths as _pr_state_paths
import alan.teams._verdict as _verdict
from alan import e2e_failures
from alan.engine_config import ForgeTarget
from alan.teams import _pr_branch, _pr_post
from alan.teams._env import float_env, positive_int_env
from alan.teams._verification_artifacts import append_audit

GATE_BLOCK_MARKER = "## External Gate Status"
GATE_ACTIONABLE_PREFIX = "GATE_ACTIONABLE:"
GATE_ACTIONABLE_YES = f"{GATE_ACTIONABLE_PREFIX} yes"
GATE_ACTIONABLE_NO = f"{GATE_ACTIONABLE_PREFIX} no"
GATE_ACTIONABLE_CHECKS_PREFIX = "GATE_ACTIONABLE_CHECKS:"
GATE_FAILING_TEST_PREFIX = "GATE_FAILING_TEST:"
_GATE_CHECK_SEPARATOR = " | "
_GATE_ACTIONABLE_CHECKS_RE = re.compile(
    rf"^{re.escape(GATE_ACTIONABLE_CHECKS_PREFIX)}(.+)$", re.MULTILINE
)
GATE_SECTION_RE = re.compile(
    re.escape(GATE_BLOCK_MARKER) + r".*?(?=\n##|\Z)", re.DOTALL
)


def gate_block_section(synthesis: str) -> str:
    match = GATE_SECTION_RE.search(synthesis)
    return match.group(0) if match else ""


_GATE_PARK_HEADER = (
    "> PARKED ON EXTERNAL GATES — not a code-quality rejection. The judge "
    "found no blocking code issues; merge is held only by GitHub gates "
    "(CI / merge state) that are not yet green. Posted as a non-blocking "
    "comment — see the External Gate Status block below for the specific "
    "blockers.\n\n"
)
_GATE_PARK_HEADER_ACTIONABLE = (
    "> PARKED ON EXTERNAL GATES — the judge found no blocking code issues, "
    "but the checks named in the External Gate Status block below are red "
    "on this diff and are in scope for the fix round. Posted as a blocking "
    "change request; it clears when those checks go green on a new push.\n\n"
)

_SETTLE_MAX_POLLS_ENV = "ALAN_VERIFIER_SETTLE_MAX_POLLS"
_SETTLE_POLL_INTERVAL_ENV = "ALAN_VERIFIER_SETTLE_POLL_INTERVAL_S"
_DEFAULT_SETTLE_MAX_POLLS = 108
_DEFAULT_SETTLE_POLL_INTERVAL_S = 30.0


def _settle_max_polls() -> int:
    return positive_int_env(_SETTLE_MAX_POLLS_ENV, _DEFAULT_SETTLE_MAX_POLLS)


def _settle_poll_interval_s() -> float:
    return float_env(_SETTLE_POLL_INTERVAL_ENV, _DEFAULT_SETTLE_POLL_INTERVAL_S)


_SETTLE_BACKOFF_MULTIPLIERS = (1, 1, 2, 4, 8)
_SETTLE_BACKOFF_CAP_MULTIPLIER = 10
_SETTLE_PROBE_UNAVAILABLE_ACTION = "verifier_settle_probe_unavailable"
_SETTLE_HEAD_MOVED_ACTION = "verifier_settle_head_moved"


def _settle_sleep_s(poll: int, interval: float) -> float:
    if poll < len(_SETTLE_BACKOFF_MULTIPLIERS):
        return interval * _SETTLE_BACKOFF_MULTIPLIERS[poll]
    return interval * _SETTLE_BACKOFF_CAP_MULTIPLIER


def _settle_next_sleep_s(poll: int, interval: float, deadline: float) -> float | None:
    if interval <= 0:
        return 0.0
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return None
    return min(_settle_sleep_s(poll, interval), remaining)


def _probe_unavailable_auditor(pinned_sha: str) -> Callable[[int, str], None]:
    def _on_fail(pr: int, reason: str) -> None:
        append_audit({
            "action": _SETTLE_PROBE_UNAVAILABLE_ACTION,
            "pr_number": pr, "head_sha": pinned_sha, "reason": reason,
        })

    return _on_fail


def _head_has_pending_check_runs(
    pr_number: int, pinned_sha: str, *, forge_target: ForgeTarget,
) -> bool | None:
    if not pinned_sha:
        return None
    on_fail = _probe_unavailable_auditor(pinned_sha)
    current_sha = _pr_branch._fetch_pr_head_sha(  # pyright: ignore[reportPrivateUsage]
        pr_number, forge_target=forge_target, on_fail=on_fail,
    )
    if current_sha is None:
        return None
    if current_sha != pinned_sha:
        append_audit({
            "action": _SETTLE_HEAD_MOVED_ACTION, "pr_number": pr_number,
            "pinned_sha": pinned_sha, "head_sha": current_sha,
        })
        return False
    check_runs = _pr_branch._iter_check_runs(  # pyright: ignore[reportPrivateUsage]
        forge_target, current_sha, pr_number, on_fail=on_fail, strict=True,
    )
    if check_runs is None:
        return None
    return bool(_pr_ready.pending_check_run_names(check_runs))


def _settle_fetch_exit(
    pr_number: int, task_id: str, readiness: _pr_ready.ReadinessResult, poll: int,
) -> bool:
    if isinstance(readiness, _pr_ready.ReadinessUnavailable):
        append_audit({
            "task_id": task_id, "action": "verifier_settle_transient",
            "pr_number": pr_number, "poll": poll,
        })
        return True
    if readiness.pending_checks:
        return False
    action = "verifier_settle_fail" if readiness.failed_reasons else "verifier_settle_green"
    append_audit({
        "task_id": task_id, "action": action,
        "pr_number": pr_number, "poll": poll + 1,
        "failed": list(readiness.failed_reasons),
    })
    return True


def _verifier_readiness(
    pr_number: int, task_id: str, *, forge_target: ForgeTarget,
) -> _pr_ready.ReadinessResult:
    readiness = _pr_ready.check_pr_verifier_blockers(
        pr_number, task_id, forge_target=forge_target
    )
    return _pr_flake_ledger.aggregate_set_aside_result(
        _pr_ready.verifier_lane_without_advisory(
            readiness, pr_number, task_id, forge_target=forge_target
        ),
        forge_target,
    )


def _settle_wait(
    pr_number: int, task_id: str, initial_readiness: _pr_ready.PrReadiness,
    *, forge_target: ForgeTarget,
) -> _pr_ready.ReadinessResult:
    max_polls = _settle_max_polls()
    interval = _settle_poll_interval_s()
    budget = max_polls * interval
    deadline = time.monotonic() + budget
    readiness = initial_readiness
    poll = 0
    while poll < max_polls and (
        sleep_s := _settle_next_sleep_s(poll, interval, deadline)
    ) is not None:
        append_audit({
            "task_id": task_id, "action": "verifier_settle_poll",
            "pr_number": pr_number, "poll": poll,
            "pending": list(readiness.pending_checks), "sleep_s": sleep_s,
        })
        time.sleep(sleep_s)
        probe = _head_has_pending_check_runs(
            pr_number, readiness.head_sha, forge_target=forge_target
        )
        if probe is not True:
            fetched = _verifier_readiness(pr_number, task_id, forge_target=forge_target)
            if _settle_fetch_exit(pr_number, task_id, fetched, poll) or isinstance(
                fetched, _pr_ready.ReadinessUnavailable
            ):
                return fetched
            readiness = fetched
        poll += 1
    append_audit({
        "task_id": task_id, "action": "verifier_settle_timeout",
        "pr_number": pr_number, "polls": poll, "budget_s": budget,
    })
    return readiness


_FailingTests = tuple[e2e_failures.FailingE2ETest, ...]


@dataclass(frozen=True)
class GateDowngradeResult:
    output: str
    downgraded: bool
    flaky_only: bool = False
    dirty: bool = False
    actionable_checks: tuple[str, ...] = ()
    non_actionable: bool = False


def _format_gate_block(
    readiness: _pr_ready.PrReadiness, actionable: tuple[str, ...],
    failing_tests: _FailingTests = (),
) -> str:
    lines = [
        "",
        GATE_BLOCK_MARKER,
        "Downgraded APPROVE → REQUEST_CHANGES because GitHub reports the "
        "PR is not currently mergeable (mergeStateStatus / reviewDecision "
        "/ required checks):",
    ]
    lines.extend(f"- {reason}" for reason in readiness.failed_reasons)
    lines.append(
        f"(mergeStateStatus={readiness.merge_state_status or '?'}, "
        f"reviewDecision={readiness.review_decision or '?'})"
    )
    lines.extend(
        _gate_actionability_lines(
            actionable,
            dirty=readiness.merge_state_status == "DIRTY",
            failing_tests=failing_tests,
        )
    )
    return "\n".join(lines)


def _failing_e2e_tests(
    readiness: _pr_ready.PrReadiness, pr_number: int, *, forge_target: ForgeTarget,
) -> _FailingTests:
    e2e_check = _pr_flake_ledger._E2E_CHECK_NAME  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    if e2e_check not in readiness.failing_checks:
        return ()
    return _pr_branch.fetch_failing_e2e_tests(pr_number, forge_target=forge_target) or ()


def _resolve_actionable_gate_checks(
    readiness: _pr_ready.PrReadiness, pr_number: int, head_sha: str | None,
    *, forge_target: ForgeTarget,
) -> tuple[tuple[str, ...], _FailingTests]:
    failing_tests = _failing_e2e_tests(readiness, pr_number, forge_target=forge_target)
    actionable = _pr_flake_ledger.evidenced_actionable_failing_checks(
        readiness, pr_number, head_sha, forge_target=forge_target
    )
    if actionable:
        return actionable, failing_tests
    promoted = _pr_flake_ledger.diff_attributable_e2e_checks(
        readiness, pr_number, forge_target=forge_target, tests=failing_tests,
    )
    return promoted, failing_tests


def _failing_test_lines(failing_tests: _FailingTests) -> list[str]:
    return [f"{GATE_FAILING_TEST_PREFIX} {t.spec} — {t.title}" for t in failing_tests]


def _gate_actionability_lines(
    actionable: tuple[str, ...], *, dirty: bool = False, failing_tests: _FailingTests = (),
) -> list[str]:
    if actionable:
        return [
            GATE_ACTIONABLE_YES,
            GATE_ACTIONABLE_CHECKS_PREFIX + _GATE_CHECK_SEPARATOR.join(actionable),
            "The checks listed above are red on this diff and are neither "
            "quarantined nor known-flaky — they are this diff's own defects and "
            "ARE fixable by code changes.",
            *_failing_test_lines(failing_tests),
        ]
    if dirty:
        return [
            GATE_ACTIONABLE_YES,
            "The branch conflicts with its base — no fixable check here, but "
            "the merge gate's mechanical reconciliation actor will rebase this "
            "branch onto its base and republish it the next time pr_merge is "
            "dispatched. Code changes cannot clear this block.",
        ]
    return [
        GATE_ACTIONABLE_NO,
        "No failing check is attributable to this diff — every blocker is "
        "merge state, a pending check, or a quarantined/known-flaky check. "
        "Code changes cannot clear this block.",
        *_failing_test_lines(failing_tests),
    ]


def parse_actionable_gate_checks(synthesis: str) -> tuple[str, ...]:
    section = gate_block_section(synthesis)
    if GATE_ACTIONABLE_YES not in section:
        return ()
    match = _GATE_ACTIONABLE_CHECKS_RE.search(section)
    if match is None:
        return ()
    names = (n.strip() for n in match.group(1).split(_GATE_CHECK_SEPARATOR))
    return tuple(n for n in names if n)


def _downgrade_output(
    judge_output: str,
    readiness: _pr_ready.PrReadiness,
    actionable: tuple[str, ...],
    failing_tests: _FailingTests = (),
) -> str:
    rewritten = _verdict._VERDICT_RE.sub(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        "VERDICT: REQUEST_CHANGES", judge_output, count=1
    )
    header = _GATE_PARK_HEADER_ACTIONABLE if actionable else _GATE_PARK_HEADER
    return header + rewritten + _format_gate_block(readiness, actionable, failing_tests)


def _flaky_gates_eligible(
    pr_number: int,
    readiness: _pr_ready.PrReadiness,
    head_sha: str | None,
    forge_target: ForgeTarget,
) -> bool:
    if not _pr_flake_ledger.flaky_only_failing(readiness):
        return False
    return not _pr_flake_ledger.gate_reruns_exhausted(
        pr_number, head_sha, forge_target=forge_target
    )


def _record_gate_survival(
    pr_number: int, readiness: _pr_ready.PrReadiness, settled: _pr_ready.ReadinessResult,
    *, sha_before: str | None, sha_after: str | None, specs_before: tuple[str, ...],
    forge_target: ForgeTarget,
) -> None:
    survived = _pr_flake_ledger.flake_survived_checks(
        readiness, settled, head_sha_before=sha_before, head_sha_after=sha_after,
    )
    if not survived or not sha_after or isinstance(settled, _pr_ready.ReadinessUnavailable):
        return
    specs_after = _pr_flake_ledger.failing_e2e_specs(
        pr_number, settled, forge_target=forge_target
    )
    gated = _pr_flake_ledger.spec_gated_survivors(
        survived, specs_before=specs_before, specs_after=specs_after,
    )
    _pr_flake_ledger.record_flake_survival(
        pr_number, gated, sha_after, forge_target=forge_target
    )


def _maybe_rerun_flaky_gates(
    pr_number: int, task_id: str, readiness: _pr_ready.PrReadiness,
    *, forge_target: ForgeTarget,
) -> _pr_ready.ReadinessResult:
    if not _pr_flake_ledger.flaky_only_failing(readiness):
        return readiness
    sha_before = _pr_branch.fetch_flake_guard_head_sha(
        pr_number, task_id, forge_target=forge_target
    )
    if not _flaky_gates_eligible(pr_number, readiness, sha_before, forge_target):
        return readiness
    failing_specs = _pr_flake_ledger.failing_e2e_spec_names(
        pr_number, readiness, forge_target=forge_target
    )
    triggered = _pr_branch.rerun_flaky_checks(
        pr_number, task_id, readiness.failing_checks, forge_target=forge_target
    )
    if not triggered:
        return readiness
    _pr_state_paths.bump_gate_rerun_count(pr_number, forge_target, head_sha=sha_before)
    settled = _settle_wait(pr_number, task_id, readiness, forge_target=forge_target)
    sha_after = _pr_branch.fetch_flake_guard_head_sha(
        pr_number, task_id, forge_target=forge_target
    )
    rescued = _pr_flake_ledger.flake_rescued_checks(
        readiness, settled, head_sha_before=sha_before, head_sha_after=sha_after,
    )
    if rescued:
        _pr_flake_ledger.record_flake_occurrence(
            pr_number, rescued, task_id, forge_target=forge_target,
            rescued_specs=failing_specs,
        )
    _record_gate_survival(
        pr_number, readiness, settled,
        sha_before=sha_before, sha_after=sha_after,
        specs_before=_pr_flake_ledger.flatten_spec_names(failing_specs),
        forge_target=forge_target,
    )
    return settled


def _resolve_gate_readiness(
    pr_number: int, task_id: str, *, forge_target: ForgeTarget,
) -> _pr_ready.ReadinessResult | None:
    _pr_post.dismiss_stale_verifier_reviews(pr_number, task_id, forge_target=forge_target)
    readiness = _verifier_readiness(pr_number, task_id, forge_target=forge_target)
    if isinstance(readiness, _pr_ready.ReadinessUnavailable):
        return None
    if readiness.pending_checks and not readiness.failed_reasons:
        readiness = _settle_wait(pr_number, task_id, readiness, forge_target=forge_target)
        if isinstance(readiness, _pr_ready.ReadinessUnavailable):
            return None
    if readiness.failed_reasons:
        readiness = _maybe_rerun_flaky_gates(
            pr_number, task_id, readiness, forge_target=forge_target
        )
    return readiness


_GatePathMode = Literal["downgrade", "rescue_only", "skip"]


def _gate_path_mode(judge_output: str) -> _GatePathMode:
    parsed = _verdict.parse_verdict(judge_output)
    if parsed is None:
        return "skip"
    if _verdict.effective_critical_count(judge_output) > 0:
        return "skip"
    return "downgrade" if parsed == "APPROVE" else "rescue_only"


def _gate_path_readiness_fields(
    readiness: _pr_ready.ReadinessResult | None,
) -> dict[str, object]:
    if readiness is None:
        return {}
    if isinstance(readiness, _pr_ready.ReadinessUnavailable):
        return {"gh_ok": False, "failing_checks": [], "pending_checks": []}
    return {
        "gh_ok": True,
        "failing_checks": list(readiness.failing_checks),
        "pending_checks": list(readiness.pending_checks),
    }


def _audit_gate_path(
    task_id: str, pr_number: int, mode: _GatePathMode,
    readiness: _pr_ready.ReadinessResult | None,
) -> None:
    append_audit({
        "task_id": task_id, "action": "gate_path",
        "pr_number": pr_number, "mode": mode,
        **_gate_path_readiness_fields(readiness),
    })


def _downgrade_result(
    judge_output: str, readiness: _pr_ready.PrReadiness,
    pr_number: int, task_id: str, *, forge_target: ForgeTarget,
) -> GateDowngradeResult:
    head_sha = _pr_branch.fetch_flake_guard_head_sha(pr_number, task_id, forge_target=forge_target)
    actionable, failing_tests = _resolve_actionable_gate_checks(
        readiness, pr_number, head_sha, forge_target=forge_target
    )
    flaky_only = _pr_flake_ledger.flaky_only_failing(readiness) and not actionable
    dirty = readiness.merge_state_status == "DIRTY"
    output = _downgrade_output(judge_output, readiness, actionable, failing_tests)
    return GateDowngradeResult(
        output, True, flaky_only, dirty,
        actionable_checks=actionable,
        non_actionable=not actionable and not dirty,
    )


def resolve_external_gates(
    judge_output: str, pr_number: int | None, task_id: str, *, forge_target: ForgeTarget,
) -> GateDowngradeResult:
    if pr_number is None:
        return GateDowngradeResult(judge_output, False)
    mode = _gate_path_mode(judge_output)
    if mode == "skip":
        _audit_gate_path(task_id, pr_number, mode, None)
        return GateDowngradeResult(judge_output, False)
    readiness = _resolve_gate_readiness(pr_number, task_id, forge_target=forge_target)
    _audit_gate_path(task_id, pr_number, mode, readiness)
    if (
        mode == "rescue_only"
        or readiness is None
        or isinstance(readiness, _pr_ready.ReadinessUnavailable)
        or not readiness.failed_reasons
    ):
        return GateDowngradeResult(judge_output, False)
    return _downgrade_result(judge_output, readiness, pr_number, task_id, forge_target=forge_target)
