from __future__ import annotations

import itertools
import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from alan.engine_config import ForgeTarget
from alan.handover import (
    Handover,
    HandoverError,
    handover_paths,
    is_answer_recording,
    load_handover,
)
from alan.roots import logs_root
from alan.router import RouterDecision
from alan.teams import _pr_state_paths
from alan.teams._audit import append_jsonl_audit
from alan.teams._verdict import (
    DIFF_FINDINGS_CRITICAL_IN_SCOPE_PREFIX,
    DIFF_FINDINGS_CRITICAL_PREFIX,
    effective_critical_count,
    parse_in_scope_critical_count,
    parse_verdict,
)
from alan.teams.verification import (
    GATE_ACTIONABLE_YES,
    GATE_BLOCK_MARKER,
    GATE_SECTION_RE,
    SCENARIOS_MISSING_MARKER,
    gate_block_section,
    parse_actionable_gate_checks,
    parse_inline_findings,
)
from alan.workspace import Workspace, revisit_key

_log = logging.getLogger(__name__)

REVISIT_CAP: int = 3

_STALL_THRESHOLD = 0.85
_TRIGRAM_SIZE = 3
_AUDIT_FILE = "spiral_audit.jsonl"
_NO_COMMITS_SENTINEL = "produced no commits — escalating"

_BOILERPLATE_LINE_RE = re.compile(
    r"^[^\n]*(?:"
    + "|".join(
        re.escape(m)
        for m in [
            "VERDICT:",
            DIFF_FINDINGS_CRITICAL_IN_SCOPE_PREFIX,
            DIFF_FINDINGS_CRITICAL_PREFIX,
            SCENARIOS_MISSING_MARKER,
        ]
    )
    + r")[^\n]*$",
    re.MULTILINE,
)

SpiralKind = Literal[
    "scenarios_missing",
    "external_gate_blocked",
    "non_actionable_finding",
    "verifier_repeating_unresolved",
    "genuine_defect",
    "unclassified",
]

LoopGuardAuditKind = Literal[
    "scenarios_missing",
    "external_gate_blocked",
    "non_actionable_finding",
    "verifier_repeating_unresolved",
    "genuine_defect",
    "unclassified",
    "cap_exhausted_no_spiral",
    "chain_decay",
    "chain_reset",
]


@dataclass(frozen=True)
class SpiralFinding:
    kind: SpiralKind
    evidence: str


def is_no_commit_escalation(handover: Handover) -> bool:
    return (
        handover.handover_type == "escalation"
        and _NO_COMMITS_SENTINEL in handover.full_synthesis
    )


def append_spiral_audit(entry: dict[str, object]) -> None:
    append_jsonl_audit(
        logs_root() / _AUDIT_FILE, entry, log_label="spiral"
    )


def _emit_cap_exhausted_audit(
    workspace: Workspace, handover: Handover, reason: str
) -> None:
    kind: LoopGuardAuditKind = "cap_exhausted_no_spiral"
    append_spiral_audit({
        "task_id": workspace.task_id,
        "kind": kind,
        "evidence": reason[:300],
        "handover_chain_id": handover.handover_chain_id,
        "revisit_counts_snapshot": dict(workspace.meta.revisit_counts),
    })


def check_loop_guard(
    workspace: Workspace, handover: Handover, next_team: str, *, task_id: str | None = None
) -> RouterDecision | None:
    _task_id = task_id if task_id is not None else workspace.meta.task_id
    key = revisit_key(workspace.workspace_id, _task_id, next_team, handover.handover_chain_id)
    runs = workspace.meta.revisit_counts.get(key, 0)
    if runs > REVISIT_CAP:
        reason = (
            f"revisit cap exceeded for {next_team} "
            f"[chain {handover.handover_chain_id}]: "
            f"{runs} runs > cap {REVISIT_CAP}"
        )
        _emit_cap_exhausted_audit(workspace, handover, reason)
        return RouterDecision(kind="escalate", reason=reason)
    if (
        not has_forward_progress(workspace, handover, next_team)
        and _is_stalled(workspace, handover)
    ):
        reason = f"stall detected: {handover.from_team} repeated itself"
        _emit_cap_exhausted_audit(workspace, handover, reason)
        return RouterDecision(kind="escalate", reason=reason)
    return None


def has_forward_progress(workspace: Workspace, handover: Handover, next_team: str) -> bool:
    if next_team == "pr_merge":
        return True
    prior = _find_latest_prior_for_team(workspace, handover)
    if prior is None:
        return False
    prior_verdict = parse_verdict(prior.full_synthesis)
    cur_verdict = parse_verdict(handover.full_synthesis)
    if prior_verdict is not None and cur_verdict is not None and prior_verdict != cur_verdict:
        return True
    if effective_critical_count(handover.full_synthesis) < effective_critical_count(
        prior.full_synthesis
    ):
        return True
    return _artifacts_changed(prior, handover)


def _artifacts_changed(prior: Handover, current: Handover) -> bool:
    return (
        {a.path for a in prior.artifacts_produced}
        != {a.path for a in current.artifacts_produced}
    )


def _is_impl_fix_stuck(impl_fix: Handover) -> bool:
    if _NO_COMMITS_SENTINEL in impl_fix.full_synthesis:
        return True
    return any(
        q.id.startswith("non_actionable_feedback_") and q.blocking and q.resolver_role == "human"
        for q in impl_fix.open_questions
    )


def _has_actionable_gate_block(verif_h: Handover) -> bool:
    return GATE_ACTIONABLE_YES in gate_block_section(verif_h.full_synthesis)


def _is_clean_external_block(verif_h: Handover) -> bool:
    s = verif_h.full_synthesis
    return (
        (GATE_BLOCK_MARKER in s or SCENARIOS_MISSING_MARKER in s)
        and effective_critical_count(s) == 0
    )


def _verifier_reports_persistent_clean_block(
    workspace: Workspace, verif_h: Handover
) -> bool:
    if not _is_clean_external_block(verif_h):
        return False
    prior = _find_latest_prior_for_team(workspace, verif_h)
    if prior is None:
        return False
    return _is_clean_external_block(prior)


def _spiral_anchors_to_classify(
    workspace: Workspace, handover: Handover, *, unchanged: tuple[str, ...] = ()
) -> tuple[Handover, Handover] | None:
    anchors = _find_spiral_anchors(workspace, handover)
    if anchors is None:
        return None
    verif_h, impl_fix_h = anchors
    if _has_actionable_gate_block(verif_h) and not unchanged:
        return None
    if not (
        _is_impl_fix_stuck(impl_fix_h)
        or _verifier_reports_persistent_clean_block(workspace, verif_h)
        or _verifier_repeats_unresolved(workspace, verif_h) is not None
    ):
        return None
    return anchors


def detect_spiral(
    workspace: Workspace, handover: Handover, next_team: str, *, task_id: str | None = None
) -> SpiralFinding | None:
    if not _is_spiral_pair(handover.from_team, next_team):
        return None
    _task_id = task_id if task_id is not None else workspace.meta.task_id
    key = revisit_key(workspace.workspace_id, _task_id, next_team, handover.handover_chain_id)
    unchanged = _unchanged_for_handover(workspace, handover)
    if workspace.meta.revisit_counts.get(key, 0) < 1 and not unchanged:
        return None
    anchors = _spiral_anchors_to_classify(workspace, handover, unchanged=unchanged)
    if anchors is None:
        return None
    verif_h, impl_fix_h = anchors
    finding = _classify_spiral(verif_h, impl_fix_h, workspace=workspace, unchanged_checks=unchanged)
    append_spiral_audit({
        "task_id": workspace.task_id,
        "kind": finding.kind,
        "evidence": finding.evidence,
        "handover_chain_id": handover.handover_chain_id,
        "revisit_counts_snapshot": dict(workspace.meta.revisit_counts),
    })
    return finding


def _is_spiral_pair(from_team: str, next_team: str) -> bool:
    return (from_team == "implementation_fix" and next_team == "verification") or (
        from_team == "verification" and next_team == "implementation_fix"
    )


def _find_spiral_anchors(
    workspace: Workspace, handover: Handover
) -> tuple[Handover, Handover] | None:
    if handover.from_team == "verification":
        impl_fix_h = _find_latest_on_chain(workspace, handover, "implementation_fix")
        return (handover, impl_fix_h) if impl_fix_h is not None else None
    verif_h = _find_latest_on_chain(workspace, handover, "verification")
    return (verif_h, handover) if verif_h is not None else None


def _find_latest_on_chain(
    workspace: Workspace, handover: Handover, team: str
) -> Handover | None:
    for path in reversed(handover_paths(workspace)):
        try:
            candidate = load_handover(path)
        except (HandoverError, OSError):
            _log.warning(
                "_find_latest_on_chain: skipping unreadable %s", path.name, exc_info=True
            )
            continue
        if (
            candidate.from_team == team
            and candidate.handover_chain_id == handover.handover_chain_id
        ):
            return candidate
    return None


def _classify_spiral(
    verif: Handover,
    impl_fix: Handover,
    *,
    workspace: Workspace | None = None,
    unchanged_checks: tuple[str, ...] = (),
) -> SpiralFinding:
    finding = _classify_unchanged_gate_checks(unchanged_checks)
    if finding is not None:
        return finding
    finding = _classify_non_actionable(impl_fix)
    if finding is not None:
        return finding
    finding = _classify_scenarios_missing(verif)
    if finding is not None:
        return finding
    finding = _classify_external_gate_blocked(verif)
    if finding is not None:
        return finding
    finding = _verifier_repeats_unresolved(workspace, verif)
    if finding is not None:
        return finding
    finding = _classify_genuine_defect(verif)
    if finding is not None:
        return finding
    return _classify_unclassified(verif)


def _classify_unchanged_gate_checks(
    unchanged_checks: tuple[str, ...],
) -> SpiralFinding | None:
    if not unchanged_checks:
        return None
    return SpiralFinding(
        kind="external_gate_blocked",
        evidence=(
            "unchanged actionable gate checks across 3 verification passes: "
            + ", ".join(unchanged_checks)
        )[:300],
    )


def _classify_non_actionable(impl_fix: Handover) -> SpiralFinding | None:
    for q in impl_fix.open_questions:
        if (
            q.id.startswith("non_actionable_feedback_")
            and q.blocking
            and q.resolver_role == "human"
        ):
            return SpiralFinding(
                kind="non_actionable_finding",
                evidence=f"impl_fix raised non-actionable: {q.id} — {q.question[:200]}"[:300],
            )
    return None


def _classify_scenarios_missing(verif: Handover) -> SpiralFinding | None:
    if SCENARIOS_MISSING_MARKER not in verif.full_synthesis:
        return None
    if effective_critical_count(verif.full_synthesis) > 0:
        return None
    return SpiralFinding(
        kind="scenarios_missing",
        evidence=(
            f"verifier output contains {SCENARIOS_MISSING_MARKER} with 0 critical findings"
        )[:300],
    )


def _classify_external_gate_blocked(verif: Handover) -> SpiralFinding | None:
    if GATE_BLOCK_MARKER not in verif.full_synthesis:
        return None
    if effective_critical_count(verif.full_synthesis) > 0:
        return None
    return SpiralFinding(
        kind="external_gate_blocked",
        evidence=f"verifier output contains '{GATE_BLOCK_MARKER}' with 0 critical findings"[:300],
    )


def _critical_anchors(h: Handover) -> frozenset[tuple[str, int]]:
    return frozenset((f.path, f.line) for f in parse_inline_findings(h.full_synthesis))


def _verifier_repeats_unresolved(
    workspace: Workspace | None, verif: Handover
) -> SpiralFinding | None:
    if workspace is None:
        return None
    n = effective_critical_count(verif.full_synthesis)
    if n <= 0:
        return None
    prior = _find_latest_prior_for_team(workspace, verif)
    if prior is None:
        return None
    anchors = _critical_anchors(verif)
    if not anchors or anchors != _critical_anchors(prior):
        return None
    prefix = _critical_count_prefix(verif.full_synthesis)
    return SpiralFinding(
        kind="verifier_repeating_unresolved",
        evidence=(
            f"verifier repeated {prefix} {n} across two "
            "consecutive passes with no declared capability boundary"
        )[:300],
    )


def _critical_count_prefix(judge_output: str) -> str:
    if parse_in_scope_critical_count(judge_output) is not None:
        return DIFF_FINDINGS_CRITICAL_IN_SCOPE_PREFIX
    return DIFF_FINDINGS_CRITICAL_PREFIX


def _classify_genuine_defect(verif: Handover) -> SpiralFinding | None:
    n = effective_critical_count(verif.full_synthesis)
    if n <= 0:
        return None
    return SpiralFinding(
        kind="genuine_defect",
        evidence=f"{DIFF_FINDINGS_CRITICAL_PREFIX} {n} in verifier output"[:300],
    )


def _classify_unclassified(verif: Handover) -> SpiralFinding:
    collapsed = " ".join(verif.full_synthesis.split())
    return SpiralFinding(
        kind="unclassified",
        evidence=f"no known pattern matched: {collapsed[:200]}"[:300],
    )


def _strip_boilerplate(text: str) -> str:
    text = GATE_SECTION_RE.sub("", text)
    return _BOILERPLATE_LINE_RE.sub("", text)


def _is_stalled(
    workspace: Workspace, handover: Handover, *, threshold: float = _STALL_THRESHOLD
) -> bool:
    prior = _find_latest_prior_for_team(workspace, handover)
    if prior is None:
        return False
    similarity = jaccard_trigrams(
        _strip_boilerplate(prior.full_synthesis),
        _strip_boilerplate(handover.full_synthesis),
    )
    if similarity <= threshold:
        return False
    return _open_questions_ids_unchanged(prior, handover)


_UNCHANGED_WALK_LIMIT = 3


def _is_transparent_amend(handover: Handover) -> bool:
    return handover.handover_type == "revisit_amend" and is_answer_recording(handover)


def _same_open_chain(candidate: Handover, verif: Handover) -> bool:
    return (
        candidate.handover_chain_id == verif.handover_chain_id
        and candidate.handover_id != verif.handover_id
    )


def _load_prior_pass_candidate(path: Path) -> Handover | None:
    try:
        return load_handover(path)
    except (HandoverError, OSError):
        _log.warning(
            "_prior_verification_passes: skipping unreadable %s", path.name, exc_info=True
        )
        return None


def _prior_pass_action(
    candidate: Handover, verif: Handover
) -> Literal["keep", "stop", "skip"]:
    if not _same_open_chain(candidate, verif):
        return "skip"
    if _is_transparent_amend(candidate):
        return "skip"
    if candidate.handover_type == "revisit_amend":
        return "stop"
    if candidate.from_team != "verification":
        return "skip"
    return "keep"


def _prior_pass_candidates(workspace: Workspace, verif: Handover) -> Iterator[Handover]:
    for path in reversed(handover_paths(workspace)):
        candidate = _load_prior_pass_candidate(path)
        if candidate is None:
            continue
        action = _prior_pass_action(candidate, verif)
        if action == "stop":
            return
        if action == "keep":
            yield candidate


def _prior_verification_passes(workspace: Workspace, verif: Handover) -> list[Handover]:
    return list(
        itertools.islice(_prior_pass_candidates(workspace, verif), _UNCHANGED_WALK_LIMIT - 1)
    )


def _actionable_checks_if_clean(verif_h: Handover) -> frozenset[str]:
    if effective_critical_count(verif_h.full_synthesis) > 0:
        return frozenset()
    return frozenset(parse_actionable_gate_checks(verif_h.full_synthesis))


def _unchanged_actionable_checks(
    workspace: Workspace, verif: Handover
) -> tuple[str, ...]:
    common = _actionable_checks_if_clean(verif)
    if not common:
        return ()
    priors = _prior_verification_passes(workspace, verif)
    if len(priors) < _UNCHANGED_WALK_LIMIT - 1:
        return ()
    for prior in priors:
        common &= _actionable_checks_if_clean(prior)
        if not common:
            return ()
    return tuple(sorted(common))


def _unchanged_for_handover(
    workspace: Workspace, handover: Handover
) -> tuple[str, ...]:
    if handover.from_team != "verification":
        return ()
    return _unchanged_actionable_checks(workspace, handover)


def _find_latest_prior_for_team(
    workspace: Workspace, handover: Handover
) -> Handover | None:
    for path in reversed(handover_paths(workspace)):
        try:
            candidate = load_handover(path)
        except (HandoverError, OSError):
            _log.warning(
                "_find_latest_prior_for_team: skipping unreadable %s", path.name, exc_info=True
            )
            continue
        if (
            candidate.from_team == handover.from_team
            and candidate.handover_id != handover.handover_id
            and candidate.handover_chain_id == handover.handover_chain_id
        ):
            return candidate
    return None


def jaccard_trigrams(a: str, b: str) -> float:
    tri_a = _trigrams(a)
    tri_b = _trigrams(b)
    union = tri_a | tri_b
    if not union:
        return 1.0
    return len(tri_a & tri_b) / len(union)


def _trigrams(text: str) -> set[str]:
    if len(text) < _TRIGRAM_SIZE:
        return set()
    return {text[i : i + _TRIGRAM_SIZE] for i in range(len(text) - _TRIGRAM_SIZE + 1)}


def _open_questions_ids_unchanged(prior: Handover, current: Handover) -> bool:
    prior_ids = {q.id for q in prior.open_questions if q.blocking}
    current_ids = {q.id for q in current.open_questions if q.blocking}
    return prior_ids == current_ids


def fix_cycle_ceiling_reached(pr_number: int, forge: ForgeTarget) -> bool:
    state = _pr_state_paths.load_fix_cycle_state(pr_number, forge)
    return state.count >= _pr_state_paths.fix_cycle_budget(pr_number, forge)


def fix_cycle_park_reason(count: int, last_finding: str) -> str:
    return (
        f"fix-cycle ceiling reached ({count} implementation_fix cycles) — "
        f"last verifier finding: {last_finding}"
    )
