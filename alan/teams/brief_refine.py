from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from alan import workspace as workspace_mod
from alan.block_correction import BlockCorrectionError
from alan.claude_cli import OnProcStarted, ServerErrorRetryable, UsageLimitError
from alan.claude_cli_grants import with_gate_reachability
from alan.engine_config import _DEFAULT as _ENGINE_DEFAULT  # pyright: ignore[reportPrivateUsage]
from alan.handover import Handover
from alan.house_rules import HOUSE_RULES
from alan.obligation_direction import direction_preserved
from alan.roots import logs_root
from alan.teams import _block_correction_fleet, _pr_forge
from alan.teams._audit import append_jsonl_audit
from alan.teams._base import EmitFn, WorkerOverrides, build_terminal_handover, render_allowed_tools
from alan.teams._brief_refine_block import (
    BRIEF_REFINE_BLOCK_GUIDANCE,
    BriefRefinePayload,
    RefinedRequirementItem,
    parse_brief_refine,
)
from alan.teams._static_pipeline import FinalizeFn, Role, StaticPipelineTeam
from alan.workspace import Requirement, Workspace

SOURCE_FILE_NAME = "brief_refine_source.md"
AUDIT_FILE = "brief_refine_audit.jsonl"

_CITATION_RE = re.compile(r"(#\d+|https?://\S+)")

_MODEL = _ENGINE_DEFAULT.roles["judge"].model

_SYSTEM_PROMPT = (
    "You are the brief_refine role — the very first thing that runs on a "
    "brand new workspace, before any planning. Read the brief (title, "
    "description, and any prior requirements below) plus, when present, the "
    "source conversation the brief was distilled from. Split what the brief "
    "asks for into two buckets: requirements (things you can trace to the "
    "user's own words, a cited issue/PR/URL, or a prior requirement you are "
    "carrying forward, possibly reworded) and assumptions (things you are "
    "inferring on your own behalf, with no such trace). Be conservative: "
    "when in doubt, put an item under assumptions rather than requirements. "
    "Never invent a rewording whose obligation direction ('must do X' vs "
    "'must not do X') differs from the original — if you cannot reword it "
    "faithfully, leave the original text alone. Phrase any "
    "verification-shaped obligation as a floor or as intent — 'at least "
    "one negative-control fixture', 'negative-control coverage exists' — "
    "never as an exact count, since delivering more coverage than asked "
    "must never register as a failure. A standing set of house "
    "rules is applied automatically after you finish; do not restate "
    "them.\n\n" + BRIEF_REFINE_BLOCK_GUIDANCE
)

ROLES: tuple[Role, ...] = (Role("brief_refiner", _SYSTEM_PROMPT, _MODEL),)


@dataclass(frozen=True)
class BriefRefineOutcome:
    requirements: list[Requirement] | None
    assumptions: list[str]
    audit_rows: list[dict[str, Any]] = field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]


def _append_audit(row: dict[str, Any]) -> None:
    append_jsonl_audit(logs_root() / AUDIT_FILE, row, log_label="brief_refine")


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _read_source_file_text(workspace: Workspace) -> str | None:
    path = workspace.path / SOURCE_FILE_NAME
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _combined_source_text(workspace: Workspace, transcript_text: str | None) -> str:
    parts = [workspace.meta.title, workspace.meta.description]
    if transcript_text:
        parts.append(transcript_text)
    return "\n\n".join(p for p in parts if p)


def _prior_requirement_line(r: Requirement) -> str:
    suffix = " [WITHDRAWN]" if r.withdrawn_by_question_id is not None else ""
    return f"- {r.id}: {r.text}{suffix}"


def _format_prior_requirements(requirements: list[Requirement] | None) -> str:
    if not requirements:
        return ""
    lines = "\n".join(_prior_requirement_line(r) for r in requirements)
    return f"\n\n## Prior requirements (by id — carry every one forward)\n{lines}"


def _build_user_text(workspace: Workspace) -> str:
    prior_block = _format_prior_requirements(workspace.meta.requirements)
    source_ref = ""
    source_path = workspace.path / SOURCE_FILE_NAME
    if source_path.is_file():
        source_ref = f"\n\n## Source conversation\n\n@{source_path}\n"
    return (
        f"## Brief\n\nTitle: {workspace.meta.title}\n\n"
        f"Description:\n{workspace.meta.description}"
        f"{prior_block}{source_ref}"
    )


def _origin_resolves(item: RefinedRequirementItem, source_lower: str) -> bool:
    if item.origin == "house_rule":
        return False
    if item.origin == "user_text":
        return item.locator is not None and _normalize(item.locator) in source_lower
    if item.origin == "cited_source":
        return item.locator is not None and _CITATION_RE.search(item.locator) is not None
    return False


def _carry_forward(
    prior: Requirement, item: RefinedRequirementItem
) -> tuple[Requirement, dict[str, Any] | None]:
    text = item.text
    if text == prior.text:
        return prior, None
    if not direction_preserved(prior.text, text):
        return prior, {"action": "rewrite_direction_reversal_rejected", "req_id": prior.id}
    rewritten = Requirement(
        text=text, test_ref=prior.test_ref, status=prior.status,
        merged_pr_url=prior.merged_pr_url,
        withdrawn_by_question_id=prior.withdrawn_by_question_id,
        kind=prior.kind,
    )
    return rewritten, {"action": "requirement_rewritten", "req_id": prior.id}


def _append_house_rules(requirements: list[Requirement], audit_rows: list[dict[str, Any]]) -> None:
    existing_ids = {r.id for r in requirements}
    for hr in HOUSE_RULES:
        candidate = Requirement(text=hr.text)
        if candidate.id in existing_ids:
            continue
        requirements.append(candidate)
        existing_ids.add(candidate.id)
        audit_rows.append({"action": "house_rule_added", "house_rule_id": hr.id})


def _merge_no_gate(payload: BriefRefinePayload) -> BriefRefineOutcome:
    assumptions = [*payload.assumptions, *(item.text for item in payload.requirements)]
    audit_rows = [
        {"action": "requirement_demoted_no_gate", "text": item.text[:200]}
        for item in payload.requirements
    ]
    return BriefRefineOutcome(requirements=None, assumptions=assumptions, audit_rows=audit_rows)


def _demote(
    item: RefinedRequirementItem,
    action: str,
    assumptions: list[str],
    audit_rows: list[dict[str, Any]],
) -> None:
    assumptions.append(item.text)
    audit_rows.append({"action": action, "origin": item.origin, "text": item.text[:200]})


def _merge_item(
    item: RefinedRequirementItem,
    prior_by_id: dict[str, Requirement],
    source_lower: str,
    requirements: list[Requirement],
    assumptions: list[str],
    audit_rows: list[dict[str, Any]],
) -> None:
    prior = prior_by_id.get(item.id) if item.id is not None else None
    if prior is not None:
        kept, row = _carry_forward(prior, item)
        requirements.append(kept)
        if row is not None:
            audit_rows.append(row)
        return
    if prior_by_id:
        _demote(item, "requirement_demoted_supplied_list", assumptions, audit_rows)
        return
    if _origin_resolves(item, source_lower):
        requirements.append(Requirement(text=item.text))
        audit_rows.append(
            {"action": "requirement_accepted", "origin": item.origin, "text": item.text[:200]}
        )
    else:
        _demote(item, "requirement_demoted", assumptions, audit_rows)


def merge(
    prior_requirements: list[Requirement] | None,
    payload: BriefRefinePayload,
    *,
    source_text: str,
) -> BriefRefineOutcome:
    if prior_requirements is None:
        return _merge_no_gate(payload)
    prior_by_id = {r.id: r for r in prior_requirements}
    source_lower = _normalize(source_text)
    requirements: list[Requirement] = []
    assumptions = list(payload.assumptions)
    audit_rows: list[dict[str, Any]] = []
    for item in payload.requirements:
        _merge_item(item, prior_by_id, source_lower, requirements, assumptions, audit_rows)
    _append_house_rules(requirements, audit_rows)
    return BriefRefineOutcome(
        requirements=requirements, assumptions=assumptions, audit_rows=audit_rows
    )


def _prior_ids(prior_requirements: list[Requirement] | None) -> frozenset[str]:
    if prior_requirements is None:
        return frozenset()
    return frozenset(r.id for r in prior_requirements)


def _correction_user_text(error: str) -> str:
    return (
        "Your previous response's fenced ```yaml brief_refine.yaml``` block "
        f"failed to parse: {error}\n\n"
        "Re-emit your COMPLETE previous response, unchanged, except for a "
        "corrected ```yaml brief_refine.yaml``` block that parses cleanly "
        "and satisfies the invariants (every prior requirement id present "
        "by id, at least one requirement carried forward when priors "
        "existed)."
    )


def _apply_payload(workspace: Workspace, payload: BriefRefinePayload | None) -> BriefRefineOutcome:
    prior_requirements = workspace.meta.requirements
    if payload is None:
        _append_audit({"action": "brief_refine_rejected", "task_id": workspace.task_id})
        return BriefRefineOutcome(
            requirements=prior_requirements, assumptions=list(workspace.meta.assumptions)
        )
    source_text = _combined_source_text(workspace, _read_source_file_text(workspace))
    outcome = merge(prior_requirements, payload, source_text=source_text)
    for row in outcome.audit_rows:
        _append_audit({**row, "task_id": workspace.task_id})
    _append_audit({"action": "brief_refine_accepted", "task_id": workspace.task_id})
    updated = workspace
    if outcome.requirements is not None:
        updated = workspace_mod.update_requirements(updated, outcome.requirements)
    workspace_mod.update_assumptions(updated, outcome.assumptions)
    return outcome


def _build_handover(
    workspace: Workspace, inv_id: str, worker_output: str, outcome: BriefRefineOutcome
) -> Handover:
    handover = build_terminal_handover(
        workspace,
        from_team="brief_refine",
        summary=worker_output,
        prior=None,
        invocation_id=inv_id,
    )
    return handover.model_copy(update={"assumptions": outcome.assumptions})


@dataclass(frozen=True)
class BriefRefineTeam(StaticPipelineTeam):
    name: str = "brief_refine"
    roles: tuple[Role, ...] = ROLES
    to_team_suggestions: tuple[str, ...] = field(default=(), init=False, repr=False)
    finalize: FinalizeFn | None = None

    def _build_base_user_text(
        self,
        workspace: Workspace,
        prior: Handover | None,
        instructions: str | None = None,
    ) -> str:
        del prior, instructions
        return _build_user_text(workspace)

    def _role_overrides(self, role_name: str, workspace: Workspace) -> WorkerOverrides | None:
        del role_name
        cfg = _pr_forge.governing_config_for_ws(workspace)
        source_path = workspace.path / SOURCE_FILE_NAME
        add_dirs = [str(workspace.path)] if source_path.is_file() else None
        return WorkerOverrides(
            allowed_tools=with_gate_reachability(
                render_allowed_tools(cfg, self.name), cfg.gate_commands
            ),
            add_dirs=add_dirs,
        )

    def run(
        self,
        workspace: Workspace,
        prior: Handover | None,
        emit: EmitFn,
        *,
        inv_id: str,
        resume_inv_id: str | None = None,
        instructions: str | None = None,
        on_proc_started: OnProcStarted | None = None,
        task_id: str | None = None,
    ) -> Handover:
        del prior, resume_inv_id, task_id, instructions
        try:
            last_output = self.run_pipeline(
                workspace,
                None,
                emit,
                inv_id=inv_id,
                instructions=None,
                on_proc_started=on_proc_started,
            )
        except (UsageLimitError, ServerErrorRetryable):
            raise
        except Exception as exc:  # noqa: BLE001
            _append_audit({
                "action": "brief_refine_pipeline_failed",
                "task_id": workspace.task_id,
                "error": str(exc)[:500],
            })
            outcome = _apply_payload(workspace, None)
            return _build_handover(
                workspace, inv_id, f"brief_refine pipeline failed: {exc}", outcome
            )
        return self._correct_and_apply(workspace, inv_id, emit, on_proc_started, last_output)

    def _correct_and_apply(
        self,
        workspace: Workspace,
        inv_id: str,
        emit: EmitFn,
        on_proc_started: OnProcStarted | None,
        last_output: str,
    ) -> Handover:
        prior_ids = _prior_ids(workspace.meta.requirements)

        def _validate(text: str) -> BriefRefinePayload:
            payload = parse_brief_refine(text, prior_ids)
            if payload is None:
                raise BlockCorrectionError(
                    "brief_refine.yaml block missing, malformed, or violates invariants"
                )
            return payload

        role_name = ROLES[0].name
        re_ask = _block_correction_fleet.build_re_ask(
            workspace,
            inv_id,
            role_name,
            emit,
            system_prompt=ROLES[0].system_prompt,
            overrides=self._role_overrides(role_name, workspace),
            on_proc_started=on_proc_started,
            correction_prompt=_correction_user_text,
        )
        correction = _block_correction_fleet.run_fleet_correction(
            last_output,
            validate=_validate,
            re_ask=re_ask,
            site="brief_refine.yaml",
            role=role_name,
            task_id=workspace.task_id,
        )
        final_text = correction.attempts[-1].text
        outcome = _apply_payload(workspace, correction.value)
        return _build_handover(workspace, inv_id, final_text, outcome)


TEAM = BriefRefineTeam()

__all__ = ["TEAM", "BriefRefineOutcome", "BriefRefineTeam", "merge"]
