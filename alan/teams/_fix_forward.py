from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import yaml

from alan import acceptance
from alan.handover import (
    ArtifactRef,
    ClauseOutcome,
    Decision,
    Handover,
    TestScenario,
    list_handovers,
)
from alan.teams import _declared_blocker, _fence, _pr_audit_log, _pr_handover, _scenario_block
from alan.teams._base import UNTRUSTED_CONTENT_NOTICE, chain_id_for, make_handover_id
from alan.teams._pr_body import BodyAmendResult
from alan.teams.verification import SCENARIOS_MISSING_MARKER
from alan.workspace import Workspace

TEAM_NAME = "implementation_fix"
NEXT_TEAM: tuple[str, ...] = ("verification",)
DECISION_PREFIX = "fix"
REVIEW_ARTIFACT_PREFIX = "full judge review"
DEFAULT_FORBIDDEN_ACTIONS: tuple[str, ...] = (
    "do not modify files outside the scope of the review findings",
)

ABSOLUTE_FORBIDDEN_ACTIONS: tuple[str, ...] = (
    "do not run `pip install -e .` or any editable install — alan is uv-managed",
    "do not amend prior commits — emit a new commit per finding",
    "do not push or publish anything beyond what the host process already does",
    "do not widen a tightening-only ratchet baseline (a flake baseline, a "
    "stress floor, a size-ratchet cap, a UI audit baseline, the "
    "duplication threshold, or a CI duration budget) in this pull request — "
    "a legitimate raise belongs in its own pull request against the "
    "flake-quarantine issue, citing its rerun-rescued evidence",
)

_LEDGER_LIMITS = (
    "This ledger is authoritative about what the fleet recorded, not "
    "about what it did not: a run interrupted before writing its "
    "handover can leave commits on the branch with no entry here. "
    "Absence from this ledger means no record exists, never that a "
    "human wrote the commit. Never infer authorship from git author "
    "identity or `Co-Authored-By` trailers — attribution comes from this "
    "ledger, the branch prefix and the commit message, never from author "
    "fields. "
    "Each bullet's SHA is what is verified; the subject shown beside it "
    "is for legibility only and never by itself authorizes a revert, "
    "since Conventional Commit subjects can coincide between two fleet "
    "commits or between a fleet commit and an unrelated human one."
)

_BARE_YAML_BLOCK_RE = re.compile(
    r"```ya?ml[ \t]*\r?\n(.*?)\n```",
    re.DOTALL,
)


def _load_block_yaml(block: str) -> Any | None:
    try:
        return yaml.safe_load(block)
    except yaml.YAMLError:
        return None


def _scenarios_from_items(raw: Any, *, strict: bool) -> list[TestScenario]:
    if not isinstance(raw, list):
        return []
    items = cast(list[Any], raw)  # type: ignore[redundant-cast]
    result: list[TestScenario] = []
    for item in items:
        scenario = _scenario_block.validate_scenario(item)
        if scenario is None:
            if strict:
                return []
            continue
        result.append(scenario)
    return result


def _scenarios_from_mapping(raw: Any) -> list[TestScenario]:
    if not isinstance(raw, dict):
        return []
    mapping = cast(dict[str, Any], raw)
    return _scenarios_from_items(mapping.get("test_scenarios"), strict=False)


def _parse_scoped_block(block: str) -> list[TestScenario]:
    raw = _load_block_yaml(block)
    if raw is None:
        return _scenario_block.recover_entries(block)
    return _scenarios_from_items(raw, strict=False) or _scenarios_from_mapping(raw)


def _parse_exact_fence(synthesis: str) -> list[TestScenario]:
    match = _scenario_block.SCENARIOS_BLOCK_RE.search(synthesis)
    return _parse_scoped_block(match.group(1)) if match is not None else []


def _parse_heading(synthesis: str) -> list[TestScenario]:
    match = _scenario_block.SCENARIOS_HEADING_RE.search(synthesis)
    return _parse_scoped_block(match.group(1)) if match is not None else []


def _parse_bare_blocks(synthesis: str) -> list[TestScenario]:
    for match in _BARE_YAML_BLOCK_RE.finditer(synthesis):
        raw = _load_block_yaml(match.group(1))
        found = _scenarios_from_mapping(raw) or _scenarios_from_items(raw, strict=True)
        if found:
            return found
    return []


def parse_fix_scenarios(synthesis: str) -> list[TestScenario]:
    for extract in (_parse_exact_fence, _parse_heading, _parse_bare_blocks):
        found = extract(synthesis)
        if found:
            return found
    return []


def _is_fleet_commit_artifact(artifact: ArtifactRef) -> bool:
    sha = _pr_handover.commit_sha(artifact.path)
    return bool(sha) and _pr_handover.commit_sha(artifact.description) == sha


def _fleet_commit_descriptions(handover: Handover) -> list[str]:
    return [
        artifact.description
        for artifact in handover.artifacts_produced
        if _is_fleet_commit_artifact(artifact)
    ]


def _fleet_commit_groups(
    workspace: Workspace,
) -> tuple[dict[tuple[str, str], list[str]], list[tuple[str, str]]]:
    groups: dict[tuple[str, str], list[str]] = {}
    order: list[tuple[str, str]] = []
    for handover in list_handovers(workspace):
        if handover.handover_type != "forward":
            continue
        descriptions = _fleet_commit_descriptions(handover)
        if not descriptions:
            continue
        key = (handover.from_team, handover.from_invocation_id)
        if key not in groups:
            order.append(key)
        groups.setdefault(key, []).extend(descriptions)
    return groups, order


def render_fleet_commit_ledger(workspace: Workspace) -> str:
    groups, order = _fleet_commit_groups(workspace)
    if not order:
        return ""
    body_lines: list[str] = []
    for team, inv_id in order:
        body_lines.append(f"\n### {team} ({inv_id})")
        body_lines.extend(f"- {description}" for description in groups[(team, inv_id)])
    return (
        "\n\n## Fleet commit ledger\n"
        f"{UNTRUSTED_CONTENT_NOTICE}\n"
        f"{_LEDGER_LIMITS}\n"
        + _fence.fence("\n".join(body_lines))
    )


_FAILING_CLAUSE_RULES = (
    "1. Satisfy each clause below as written.\n"
    "2. A remedy the review offers that does not satisfy the clause text is not a remedy.\n"
    "3. Under `## Decisions`, name each clause identifier alongside the commit that "
    "satisfies it."
)


def _live_failing_clauses(workspace: Workspace, outcome: ClauseOutcome) -> list[str]:
    withdrawn = {
        req.id for req in workspace.meta.requirements or [] if acceptance.is_withdrawn(req)
    }
    lines: list[str] = []
    for clause_id in sorted(set(outcome.actionable_ids) - withdrawn):
        text = " ".join(outcome.clause_texts.get(clause_id, "").split())
        if text:
            lines.append(f"- {clause_id}: {text}")
    return lines


def render_failing_clause_block(
    workspace: Workspace, prior: Handover, task_id: str | None
) -> str:
    outcome = prior.clause_outcome
    if outcome is None or outcome.task_id != (task_id or workspace.task_id):
        return ""
    lines = _live_failing_clauses(workspace, outcome)
    if not lines:
        return ""
    return (
        "\n\n## Failing acceptance clauses\n"
        "The acceptance clause audit failed the clauses listed below; the auditor's "
        "evidence for each is in the clause table of the judge review above.\n"
        f"{_FAILING_CLAUSE_RULES}\n\n" + "\n".join(lines)
    )


def resolve_review_text(prior: Handover) -> str:
    for artifact in prior.artifacts_produced:
        if not artifact.description.startswith(REVIEW_ARTIFACT_PREFIX):
            continue
        try:
            return Path(artifact.path).read_text(encoding="utf-8")
        except OSError:
            break
    return prior.full_synthesis


def _inbound_scenarios_missing(prior: Handover | None) -> bool:
    return prior is not None and SCENARIOS_MISSING_MARKER in resolve_review_text(prior)


def _build_forward_handover(
    workspace: Workspace,
    prior: Handover | None,
    inv_id: str,
    synthesis: str,
    prior_pr: int | None,
    *,
    decision: Decision,
    test_scenarios: list[TestScenario],
) -> Handover:
    iteration = prior.iteration + 1 if prior is not None else 1
    revisit_of = prior.handover_id if prior is not None else None
    new_id = make_handover_id(workspace.task_id)
    return Handover(
        handover_id=new_id,
        handover_chain_id=chain_id_for(prior, new_id),
        handover_type="forward",
        task_id=workspace.task_id,
        from_team=TEAM_NAME,
        from_invocation_id=inv_id,
        to_team_suggestions=list(NEXT_TEAM),
        iteration=iteration,
        revisit_of=revisit_of,
        timestamp_utc=datetime.now(UTC),
        decisions=[decision],
        open_questions=_declared_blocker.parse_declared_blocker(synthesis, inv_id),
        forbidden_actions=list(DEFAULT_FORBIDDEN_ACTIONS),
        full_synthesis=synthesis,
        pr_number=prior_pr,
        test_scenarios=test_scenarios,
    )


def scenarios_only_forward_handover(
    workspace: Workspace,
    prior: Handover | None,
    inv_id: str,
    synthesis: str,
    prior_pr: int | None,
    scenarios: list[TestScenario],
) -> Handover:
    return _build_forward_handover(
        workspace, prior, inv_id, synthesis, prior_pr,
        decision=Decision(
            id=f"{DECISION_PREFIX}-scenarios",
            statement=(
                f"Re-supplied {len(scenarios)} test scenario(s) for the "
                "verification scenario_runner; no code change required."
            ),
            rationale=(
                "Judge review reported SCENARIOS_MISSING with zero code "
                "defects, so the only fix is to re-supply the behavioral "
                "scenarios and re-run verification."
            ),
            confidence=0.8,
            reversibility="medium",
        ),
        test_scenarios=scenarios,
    )


def body_only_forward_handover(
    workspace: Workspace,
    prior: Handover | None,
    inv_id: str,
    synthesis: str,
    prior_pr: int | None,
) -> Handover:
    return _build_forward_handover(
        workspace, prior, inv_id, synthesis, prior_pr,
        decision=Decision(
            id=f"{DECISION_PREFIX}-body",
            statement=(
                "Amended the pull request body to address the review; "
                "no code change was required."
            ),
            rationale=(
                "Every actionable finding concerned the PR body's "
                "contents; the worker supplied a revised body via a "
                "```pr_body block and the host amended it wholesale."
            ),
            confidence=0.8,
            reversibility="medium",
        ),
        test_scenarios=list(prior.test_scenarios) if prior is not None else [],
    )


def no_code_forward_handover(
    workspace: Workspace,
    prior: Handover | None,
    inv_id: str,
    synthesis: str,
    prior_pr: int | None,
    *,
    scenarios: list[TestScenario],
    amend: BodyAmendResult | None,
) -> Handover | None:
    if amend is not None:
        _pr_audit_log.append_audit({
            "task_id": workspace.task_id,
            "action": "pr_body_amend_forward",
            "pr_number": prior_pr,
            "amend_ok": amend.ok,
            "team": TEAM_NAME,
        })
        if amend.ok:
            return body_only_forward_handover(workspace, prior, inv_id, synthesis, prior_pr)
    if scenarios:
        _pr_audit_log.append_audit({
            "task_id": workspace.task_id, "action": "scenarios_only_forward",
            "scenario_count": len(scenarios),
            "inbound_scenarios_missing": _inbound_scenarios_missing(prior),
            "team": TEAM_NAME,
        })
        return scenarios_only_forward_handover(
            workspace, prior, inv_id, synthesis, prior_pr, scenarios
        )
    return None


__all__ = [
    "ABSOLUTE_FORBIDDEN_ACTIONS",
    "DECISION_PREFIX",
    "DEFAULT_FORBIDDEN_ACTIONS",
    "NEXT_TEAM",
    "REVIEW_ARTIFACT_PREFIX",
    "TEAM_NAME",
    "body_only_forward_handover",
    "no_code_forward_handover",
    "parse_fix_scenarios",
    "render_failing_clause_block",
    "render_fleet_commit_ledger",
    "resolve_review_text",
    "scenarios_only_forward_handover",
]
