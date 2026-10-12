from __future__ import annotations

import logging
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, TypeVar, cast

from alan import default_profile
from alan.engine_config import ForgeTarget
from alan.forge import FileRead, active_forge, decode_contents_payload
from alan.teams import (
    _forge_errors,
    _pr_audit_log,
    _pr_feedback,
    _pr_forge,
    _pr_merge_conflict,
    _pr_ready,
)
from alan.teams._audit import append_jsonl_audit
from alan.workspace import Workspace

logger = logging.getLogger(__name__)

_UNRESOLVED_CEILING = 100

ChecksKind = Literal[
    "pending", "failed", "unresolved", "unresolved_ceiling",
    "copilot_unresolved", "timeout", "ready", "error",
    "codeowners_blocked", "codeowners_unsupported",
]

_T = TypeVar("_T")

_CODEOWNERS_PATH = ".github/CODEOWNERS"
_OWNERSHIP_READ_ATTEMPTS = 3
_OWNERSHIP_RETRY_INTERVAL_S = 5.0
_DISALLOWED_OWNERSHIP_GLYPHS: tuple[str, ...] = ("*", "?", "[", "]", "!", "\\")

_PENDING_CHECK_STATES: frozenset[str] = frozenset(
    {"PENDING", "QUEUED", "IN_PROGRESS"}
)
_FAILURE_STATES: frozenset[str] = frozenset({"FAILURE", "ERROR", "TIMED_OUT"})
_UNCLASSIFIED_CHECK_STATES: frozenset[str] = frozenset({""})

_MERGEABLE_MERGE_STATES = _pr_merge_conflict.MERGEABLE_MERGE_STATES

PENDING_FALLBACK: list[dict[str, str]] = [{"name": "?", "state": "PENDING"}]

_ADVISORY_AUDIT_FILE = "merge_gate_audit.jsonl"


class UnsupportedCodeownersSyntax(ValueError):
    pass


@dataclass(frozen=True)
class _OwnershipRule:
    segments: tuple[str, ...]
    anchored: bool


def _reject_unsupported_pattern(line: str, pattern: str) -> None:
    if "#" in line or any(glyph in pattern for glyph in _DISALLOWED_OWNERSHIP_GLYPHS):
        raise UnsupportedCodeownersSyntax(f"unsupported CODEOWNERS syntax: {line!r}")


def _pattern_core(pattern: str) -> tuple[str, bool, bool]:
    any_depth = pattern.startswith("**/")
    core_pattern = pattern[3:] if any_depth else pattern
    leading = core_pattern.startswith("/")
    core = core_pattern[1:] if leading else core_pattern
    core = core[:-1] if core.endswith("/") else core
    return core, leading, any_depth


def _parse_rule(line: str) -> _OwnershipRule:
    tokens = line.split()
    if len(tokens) < 2:
        raise UnsupportedCodeownersSyntax(f"unsupported CODEOWNERS syntax: {line!r}")
    core, leading, any_depth = _pattern_core(tokens[0])
    _reject_unsupported_pattern(line, core)
    segments = tuple(segment for segment in core.split("/") if segment)
    anchored = (leading or "/" in core) and not any_depth
    return _OwnershipRule(segments=segments, anchored=anchored)


def _parse_codeowners(text: str) -> tuple[_OwnershipRule, ...]:
    rules: list[_OwnershipRule] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        rules.append(_parse_rule(line))
    return tuple(rules)


def _path_is_owned(path: str, rule: _OwnershipRule) -> bool:
    parts = tuple(path.split("/"))
    if rule.anchored:
        return parts[: len(rule.segments)] == rule.segments
    window = len(rule.segments)
    return bool(window) and any(
        parts[i : i + window] == rule.segments for i in range(len(parts) - window + 1)
    )


def _retry_transient(op: Callable[[], tuple[_T | None, bool]]) -> _T | None:
    value, transient = op()
    tries = 1
    while transient and tries < _OWNERSHIP_READ_ATTEMPTS:
        time.sleep(_OWNERSHIP_RETRY_INTERVAL_S)
        value, transient = op()
        tries += 1
    return value


def _read_base_ref(pr_number: int, *, forge_target: ForgeTarget) -> str | None:
    def _op() -> tuple[str | None, bool]:
        try:
            read = active_forge().read_pr(
                pr_number, ("baseRefName",),
                forge_target=forge_target, timeout=_pr_forge.GH_TIMEOUT_S,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.warning(
                "codeowners ownership gate: read_pr raised %s for PR #%s", exc, pr_number,
            )
            return None, True
        if not read.ok:
            transient = _forge_errors.classify_forge_failure(
                returncode=read.returncode, stderr=read.stderr,
            ) == "transient"
            return None, transient
        base_ref = read.fields.get("baseRefName")
        return (base_ref, False) if isinstance(base_ref, str) and base_ref else (None, False)

    return _retry_transient(_op)


def _read_codeowners_text(
    pr_number: int, base_ref: str, *, forge_target: ForgeTarget,
) -> FileRead | None:
    del pr_number

    def _op() -> tuple[FileRead | None, bool]:
        read = active_forge().repo_file(
            _CODEOWNERS_PATH, forge_target=forge_target,
            timeout=_pr_forge.GH_TIMEOUT_S, ref=base_ref,
        )
        if read.outcome == "raised":
            return None, True
        if read.outcome == "failed":
            transient = _forge_errors.classify_forge_failure(
                returncode=read.returncode, stderr=read.stderr,
            ) == "transient"
            return None, transient
        return read, False

    return _retry_transient(_op)


def _match_verdict(text: str, changed_files: frozenset[str]) -> ChecksKind | None:
    try:
        rules = _parse_codeowners(text)
    except UnsupportedCodeownersSyntax:
        return "codeowners_unsupported"
    owned = any(_path_is_owned(path, rule) for path in changed_files for rule in rules)
    return "codeowners_blocked" if owned else None


def _read_changed_files(pr_number: int, *, forge_target: ForgeTarget) -> frozenset[str] | None:
    try:
        return active_forge().changed_files(pr_number, forge_target=forge_target)
    except OSError:
        return None


def _ownership_verdict(pr_number: int, *, forge_target: ForgeTarget) -> ChecksKind | None:
    base_ref = _read_base_ref(pr_number, forge_target=forge_target)
    if base_ref is None:
        return "codeowners_blocked"
    file_read = _read_codeowners_text(pr_number, base_ref, forge_target=forge_target)
    if file_read is None or file_read.outcome == "absent":
        return "codeowners_blocked"
    text = decode_contents_payload(file_read.text or "")
    if text is None:
        return "codeowners_blocked"
    changed_files = _read_changed_files(pr_number, forge_target=forge_target)
    if changed_files is None:
        return "codeowners_blocked"
    return _match_verdict(text, changed_files)


def _ownership_gate_enforced(forge_target: ForgeTarget) -> bool:
    enforced = default_profile.resolved().profile.codeowners_repos
    return forge_target.repo.slug.casefold() in enforced


def _apply_ownership_gate(
    result: tuple[ChecksKind, str, _pr_ready.PrReadiness | None],
    pr_number: int,
    *, forge_target: ForgeTarget,
) -> tuple[ChecksKind, str, _pr_ready.PrReadiness | None]:
    if result[0] != "ready":
        return result
    if not _ownership_gate_enforced(forge_target):
        return result
    verdict = _ownership_verdict(pr_number, forge_target=forge_target)
    if verdict is None:
        return result
    return verdict, "", None


def _has_check_state(
    checks: list[dict[str, str]], targets: frozenset[str]
) -> bool:
    states = {c.get("state", "").upper() for c in checks}
    return bool(targets & states)


def _classify_feedback(
    thread_info: _pr_feedback.ThreadInfo,
    blocking_comment_info: _pr_feedback.CommentInfo,
) -> ChecksKind:
    if thread_info.total + blocking_comment_info.total > _UNRESOLVED_CEILING:
        return "unresolved_ceiling"
    if _pr_feedback.has_unresolved_human(thread_info, blocking_comment_info):
        return "unresolved"
    if _pr_feedback.has_unresolved_copilot(thread_info, blocking_comment_info.copilot_items):
        return "copilot_unresolved"
    return "ready"


def classify_checks(
    checks: list[dict[str, str]],
    thread_info: _pr_feedback.ThreadInfo,
    blocking_comment_info: _pr_feedback.CommentInfo,
) -> ChecksKind:
    if not checks:
        return "pending"
    if _has_check_state(checks, _PENDING_CHECK_STATES):
        return "pending"
    if _has_check_state(checks, _FAILURE_STATES):
        return "failed"
    return _classify_feedback(thread_info, blocking_comment_info)


def failed_check_names(checks: object) -> list[str]:
    if not isinstance(checks, list):
        return []
    out: list[str] = []
    for raw in checks:  # pyright: ignore[reportUnknownVariableType]
        if not isinstance(raw, dict):
            continue
        item = cast(dict[str, Any], raw)
        state = str(item.get("state") or "").upper()
        if state in _FAILURE_STATES:
            out.append(str(item.get("name", "?")))
    return out


def _advisory_tolerance_eligible(checks: list[dict[str, str]]) -> bool:
    return (
        bool(checks)
        and checks != PENDING_FALLBACK
        and not _has_check_state(checks, _FAILURE_STATES)
        and not _has_check_state(checks, _UNCLASSIFIED_CHECK_STATES)
    )


def _pending_local_names(checks: list[dict[str, str]]) -> frozenset[str]:
    return frozenset(
        str(c.get("name", ""))
        for c in checks
        if c.get("state", "").upper() in _PENDING_CHECK_STATES
    )


def _advisory_floor_possible(
    checks: list[dict[str, str]], forge_target: ForgeTarget
) -> bool:
    authoritative = forge_target.authoritative_checks
    return bool(authoritative) and authoritative.isdisjoint(_pending_local_names(checks))


def _readiness_tolerates_advisory(readiness: _pr_ready.PrReadiness) -> bool:
    return (
        readiness.merge_state_status == "UNSTABLE"
        and not readiness.failing_checks
        and bool(readiness.pending_checks)
        and readiness.review_decision != "CHANGES_REQUESTED"
    )


def _authoritative_floor_met(
    readiness: _pr_ready.PrReadiness, forge_target: ForgeTarget
) -> bool:
    return bool(forge_target.authoritative_checks) and all(
        _pr_ready.resolve_check_outcome(name, readiness, forge_target)
        is _pr_ready.CheckOutcome.PASS
        for name in forge_target.authoritative_checks
    )


def _advisory_readiness(
    workspace: Workspace, pr_number: int, forge_target: ForgeTarget,
) -> _pr_ready.PrReadiness | None:
    readiness = _pr_ready.check_pr_merge_readiness(
        pr_number, workspace.task_id, forge_target=forge_target
    )
    if (
        isinstance(readiness, _pr_ready.ReadinessUnavailable)
        or _pr_ready.results_absent(readiness)
        or not readiness.head_sha
    ):
        return None
    if not _readiness_tolerates_advisory(readiness):
        return None
    if not _authoritative_floor_met(readiness, forge_target):
        return None
    return readiness


def audit_advisory_tolerated(
    workspace: Workspace,
    pr_number: int,
    readiness: _pr_ready.PrReadiness,
    forge_target: ForgeTarget,
) -> None:
    # Callers must only invoke this once the merge it describes has actually
    # happened (or been simulated by a dry run) — a behind/head-mismatch
    # recheck or a `gh pr merge` refusal can still abort a "ready" verdict
    # before any merge occurs (Copilot review on PR #1488).
    append_jsonl_audit(
        _pr_audit_log.audit_dir() / _ADVISORY_AUDIT_FILE,
        {
            "task_id": workspace.task_id,
            "action": "advisory_check_tolerated",
            "pr_number": pr_number,
            "merge_state_status": readiness.merge_state_status,
            "declined_checks": list(readiness.pending_checks),
            "authoritative_checks": sorted(forge_target.authoritative_checks),
        },
        log_label="pr_merge",
    )


def _tolerated_kind(
    workspace: Workspace,
    pr_number: int,
    checks: list[dict[str, str]],
    thread_info: _pr_feedback.ThreadInfo,
    blocking_comment_info: _pr_feedback.CommentInfo,
    *, forge_target: ForgeTarget,
) -> tuple[ChecksKind, str, _pr_ready.PrReadiness | None] | None:
    if not _advisory_tolerance_eligible(checks):
        return None
    if not _advisory_floor_possible(checks, forge_target):
        return None
    readiness = _advisory_readiness(workspace, pr_number, forge_target)
    if readiness is None:
        return None
    kind = _classify_feedback(thread_info, blocking_comment_info)
    if kind != "ready":
        return kind, "", None
    return "ready", readiness.head_sha, readiness


def _failures_cleared(checks: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        {**c, "state": "SUCCESS"} if c.get("state", "").upper() in _FAILURE_STATES else c
        for c in checks
    ]


def _checks_ignoring_advisory(
    workspace: Workspace,
    pr_number: int,
    checks: list[dict[str, str]],
    *, forge_target: ForgeTarget,
) -> list[dict[str, str]] | None:
    readiness = _pr_ready.readiness_or_unavailable(
        pr_number, workspace.task_id, forge_target=forge_target
    )
    if isinstance(readiness, _pr_ready.ReadinessUnavailable):
        return None
    ignored = _pr_ready.advisory_failing_checks(
        readiness, pr_number, workspace.task_id, forge_target=forge_target
    )
    failed = frozenset(failed_check_names(checks))
    if not ignored or not failed <= frozenset(ignored):
        return None
    _pr_ready.audit_advisory_ignored("pr_merge", workspace.task_id, pr_number, readiness, ignored)
    return _failures_cleared(checks)


def _kind_ignoring_advisory(
    kind: ChecksKind,
    workspace: Workspace,
    pr_number: int,
    checks: list[dict[str, str]],
    thread_info: _pr_feedback.ThreadInfo,
    blocking_comment_info: _pr_feedback.CommentInfo,
    *, forge_target: ForgeTarget,
) -> ChecksKind:
    if kind != "failed":
        return kind
    cleared = _checks_ignoring_advisory(workspace, pr_number, checks, forge_target=forge_target)
    if cleared is None:
        return kind
    return classify_checks(cleared, thread_info, blocking_comment_info)


def resolve_checks_kind(
    workspace: Workspace,
    pr_number: int,
    checks: list[dict[str, str]],
    thread_info: _pr_feedback.ThreadInfo,
    blocking_comment_info: _pr_feedback.CommentInfo,
    *, forge_target: ForgeTarget,
) -> tuple[ChecksKind, str, _pr_ready.PrReadiness | None]:
    # Third element is non-None only for a tolerant-path "ready": the
    # PrReadiness the verdict relied on, for the caller to hand to
    # audit_advisory_tolerated once (and only once) a merge actually happens.
    kind = _kind_ignoring_advisory(
        classify_checks(checks, thread_info, blocking_comment_info),
        workspace, pr_number, checks, thread_info, blocking_comment_info,
        forge_target=forge_target,
    )
    if kind == "pending":
        tolerated = _tolerated_kind(
            workspace, pr_number, checks, thread_info, blocking_comment_info,
            forge_target=forge_target,
        )
        if tolerated is not None:
            return _apply_ownership_gate(tolerated, pr_number, forge_target=forge_target)
    confirmed_kind, head_sha = confirm_ready_kind(
        workspace, pr_number, kind, forge_target=forge_target
    )
    return _apply_ownership_gate(
        (confirmed_kind, head_sha, None), pr_number, forge_target=forge_target
    )


def confirm_ready_kind(
    workspace: Workspace,
    pr_number: int,
    kind: ChecksKind,
    *, forge_target: ForgeTarget,
) -> tuple[ChecksKind, str]:
    # Fail-closed second source for the rollup/merge-state race: a check-run
    # freshly reset by `gh run rerun` can vanish from the checks rollup, making
    # an unmergeable PR read all-green here while GitHub's mergeStateStatus
    # still knows a required context is missing; or the rollup can read back
    # entirely empty for a commit whose checks were never created at all,
    # which reads identically to "all checks passed" unless ruled out
    # explicitly via `results_absent`. Re-evaluated on every poll.
    if kind != "ready":
        return kind, ""
    readiness = _pr_ready.check_pr_merge_readiness(
        pr_number, workspace.task_id, forge_target=forge_target
    )
    if isinstance(readiness, _pr_ready.ReadinessUnavailable) or _pr_ready.results_absent(readiness):
        return "pending", ""
    if _readiness_confirms_mergeable_head(readiness):
        return "ready", readiness.head_sha
    return "pending", ""


def _readiness_confirms_mergeable_head(readiness: _pr_ready.PrReadiness) -> bool:
    # A malformed/missing `headRefOid` must never confirm "ready" — that
    # would silently disable every downstream head-match check (Copilot
    # review on PR #1322).
    return readiness.merge_state_status in _MERGEABLE_MERGE_STATES and bool(readiness.head_sha)


def recheck_confirms_target(
    expected_head_sha: str, readiness: _pr_ready.ReadinessResult,
) -> bool:
    return (
        bool(expected_head_sha)
        and isinstance(readiness, _pr_ready.PrReadiness)
        and not _pr_ready.results_absent(readiness)
        and readiness.head_sha == expected_head_sha
    )
