from __future__ import annotations

import contextvars
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import alan.teams._clause_audit as _clause_audit
import alan.teams._clause_stall as _clause_stall
import alan.teams._scenario_gate as _scenario_gate
import alan.teams._scenario_runner as _scenario_runner
import alan.teams._verdict as _verdict
import alan.teams._verification_merged_guard as _verification_merged_guard
import alan.teams._verification_run_tokens as _run_tokens
import alan.teams._verifier_thread_resolution as _verifier_thread_resolution
from alan import acceptance as _acceptance_mod
from alan import overlap as _overlap_mod
from alan import prompt_templates, scenario_binding
from alan import secrets_store as _secrets_store
from alan import workflow as _workflow_mod
from alan.claude_cli import OnProcStarted
from alan.claude_cli_grants import with_gate_reachability
from alan.engine_config import _DEFAULT as _ENGINE_DEFAULT  # pyright: ignore[reportPrivateUsage]
from alan.engine_config import EngineConfig, ForgeTarget
from alan.engine_config import forge_target_for_workspace as _forge_target_for_workspace
from alan.handover import Handover, TestScenario
from alan.scenario_binding import ScenarioMode
from alan.teams import _pr_branch, _pr_forge, _pr_handover, _pr_post, _review_context
from alan.teams._base import (
    EmitFn,
    WorkerOverrides,
    build_forward_handover,
    render_allowed_tools,
    run_or_resume_node,
    workflow_lifecycle,
)
from alan.teams._inline_findings import (
    parse_inline_findings,
    strip_inline_findings_block,
)
from alan.teams._review_context import (
    _format_manifest_deviation_block,  # pyright: ignore[reportPrivateUsage]
)
from alan.teams._scenario_runner import (
    ScenarioResult,
    format_results_block,
)
from alan.teams._static_pipeline import (
    Role,
    Stage,
    StaticPipelineTeam,
    _depends_on_map,  # pyright: ignore[reportPrivateUsage]
    _run_stage_roles,  # pyright: ignore[reportPrivateUsage]
    assemble_role_system_prompt,
    build_role_user_text,
)
from alan.teams._verification_artifacts import (
    _write_review_artifact as _write_review_artifact,  # pyright: ignore[reportPrivateUsage]
)
from alan.teams._verification_artifacts import (
    append_audit as append_audit,
)
from alan.teams._verification_artifacts import (
    dry_run_enabled as dry_run_enabled,
)
from alan.teams._verification_artifacts import reviewer_count_var
from alan.teams._verification_capability import (
    capability_boundary_waived as capability_boundary_waived,
)
from alan.teams._verification_capability import (
    declared_capability_boundary as declared_capability_boundary,
)
from alan.teams._verification_gates import (
    GATE_ACTIONABLE_CHECKS_PREFIX as GATE_ACTIONABLE_CHECKS_PREFIX,
)
from alan.teams._verification_gates import (
    GATE_ACTIONABLE_NO as GATE_ACTIONABLE_NO,
)
from alan.teams._verification_gates import (
    GATE_ACTIONABLE_PREFIX as GATE_ACTIONABLE_PREFIX,
)
from alan.teams._verification_gates import (
    GATE_ACTIONABLE_YES as GATE_ACTIONABLE_YES,
)
from alan.teams._verification_gates import (
    GATE_BLOCK_MARKER as GATE_BLOCK_MARKER,
)
from alan.teams._verification_gates import (
    GATE_SECTION_RE as GATE_SECTION_RE,
)
from alan.teams._verification_gates import (
    gate_block_section as gate_block_section,
)
from alan.teams._verification_gates import (
    parse_actionable_gate_checks as parse_actionable_gate_checks,
)
from alan.teams._verification_gates import (
    resolve_external_gates as resolve_external_gates,
)
from alan.teams._verification_grounding import (
    _emit_cannot_verify as _emit_cannot_verify,  # pyright: ignore[reportPrivateUsage]
)
from alan.teams._verification_grounding import (
    _try_acquire_grounded_checkout as _try_acquire_grounded_checkout,  # pyright: ignore[reportPrivateUsage]
)
from alan.teams._verification_prompts import (
    _DIFF_REVIEWER_PROMPT as _DIFF_REVIEWER_PROMPT,  # pyright: ignore[reportPrivateUsage]
)
from alan.teams._verification_prompts import (
    _JUDGE_PROMPT as _JUDGE_PROMPT,  # pyright: ignore[reportPrivateUsage]
)
from alan.teams._verification_prompts import (
    _JUDGE_RETRY_NUDGE as _JUDGE_RETRY_NUDGE,  # pyright: ignore[reportPrivateUsage]
)
from alan.teams._verification_prompts import (
    _SCENARIO_RUNNER_PROMPT as _SCENARIO_RUNNER_PROMPT,  # pyright: ignore[reportPrivateUsage]
)
from alan.teams._verification_review_post import (
    _JUDGE_PARSE_FAIL_QUESTION as _JUDGE_PARSE_FAIL_QUESTION,  # pyright: ignore[reportPrivateUsage]
)
from alan.teams._verification_review_post import (
    ReviewPostResult as ReviewPostResult,
)
from alan.teams._verification_review_post import (
    _emit_capability_boundary_escalation as _emit_capability_boundary_escalation,  # pyright: ignore[reportPrivateUsage]
)
from alan.teams._verification_review_post import (
    _emit_clause_pr_state_escalation as _emit_clause_pr_state_escalation,  # pyright: ignore[reportPrivateUsage]
)
from alan.teams._verification_review_post import (
    _emit_clause_stall_escalation as _emit_clause_stall_escalation,  # pyright: ignore[reportPrivateUsage]
)
from alan.teams._verification_review_post import (
    _emit_judge_parse_failed as _emit_judge_parse_failed,  # pyright: ignore[reportPrivateUsage]
)
from alan.teams._verification_review_post import (
    _emit_review_post_failed as _emit_review_post_failed,  # pyright: ignore[reportPrivateUsage]
)
from alan.teams._verification_review_post import (
    _post_review_with_retries as _post_review_with_retries,  # pyright: ignore[reportPrivateUsage]
)
from alan.teams._verification_routing import (
    _pr_precondition_reason as _pr_precondition_reason,  # pyright: ignore[reportPrivateUsage]
)
from alan.teams._verification_routing import (
    _verdict_decisions as _verdict_decisions,  # pyright: ignore[reportPrivateUsage]
)
from alan.teams._verification_routing import (
    suggestions_for_verdict as suggestions_for_verdict,
)
from alan.teams._verification_scenarios import (
    _changed_e2e_results as _changed_e2e_results,  # pyright: ignore[reportPrivateUsage]
)
from alan.teams._verification_scenarios import (
    _format_dod_scope_block as _format_dod_scope_block,  # pyright: ignore[reportPrivateUsage]
)
from alan.teams._verification_scenarios import (
    _format_scenarios_block as _format_scenarios_block,  # pyright: ignore[reportPrivateUsage]
)
from alan.teams._verification_scenarios import (
    _scenario_runner_already_succeeded as _scenario_runner_already_succeeded,  # pyright: ignore[reportPrivateUsage]
)
from alan.teams._verification_scenarios import (
    _scenarios_waived as _scenarios_waived,  # pyright: ignore[reportPrivateUsage]
)
from alan.teams._verification_scenarios import (
    _to_scenario_outcomes as _to_scenario_outcomes,  # pyright: ignore[reportPrivateUsage]
)
from alan.workspace import Task as _Task
from alan.workspace import Workspace

logger = logging.getLogger(__name__)

_scenario_results_var: contextvars.ContextVar[
    list[ScenarioResult] | None
] = contextvars.ContextVar("_verification_scenario_results", default=None)

_verification_task_id_var: contextvars.ContextVar[
    str | None
] = contextvars.ContextVar("_verification_task_id", default=None)

_grounded_cwd_var: contextvars.ContextVar[
    Path | None
] = contextvars.ContextVar("_verification_grounded_cwd", default=None)

_review_context_var: contextvars.ContextVar[
    str | None
] = contextvars.ContextVar("_verification_review_context", default=None)


_MODEL_OPUS = _ENGINE_DEFAULT.roles["judge"].model
_MODEL_SONNET = _ENGINE_DEFAULT.roles["diff_reviewer"].model

_JudgeRetryReason = Literal["missing_verdict", "missing_in_scope_count"]
SCENARIOS_WAIVED_MARKER = "SCENARIOS_WAIVED"
SCENARIOS_COVERED_BY_DOD_MARKER = "SCENARIOS_COVERED_BY_DOD"

SCENARIOS_MISSING_MARKER = "SCENARIOS_MISSING"

_JUDGE_RETRY_NODE_ID = "judge_retry"


ROLES: tuple[Role, ...] = (
    Role("diff_reviewer", _DIFF_REVIEWER_PROMPT, _MODEL_SONNET),
    Role("scenario_runner", _SCENARIO_RUNNER_PROMPT, _MODEL_SONNET),
    Role("judge", _JUDGE_PROMPT, _MODEL_OPUS),
)

# scenario_runner's evidence is precomputed earlier — independent of diff_reviewer; judge sees both.
_VERIFICATION_STAGES: tuple[Stage, ...] = (
    ("diff_reviewer", "scenario_runner"),
    ("judge",),
)


def _changed_e2e_env(workspace: Workspace, cfg: EngineConfig) -> Mapping[str, str] | None:
    forge_target = _forge_target_for_workspace(workspace.meta.engine_key, cfg)
    try:
        return _secrets_store.transport_env(forge_target)
    except _secrets_store.MissingSecretError:
        return None


def _model_for(cfg: EngineConfig, role: str) -> str:
    role_cfg = cfg.roles.get(role)
    if role_cfg is not None:
        return role_cfg.model
    return _ENGINE_DEFAULT.roles[role].model


def roles_for_config(cfg: EngineConfig) -> tuple[Role, ...]:
    sonnet = _model_for(cfg, "diff_reviewer")
    opus = _model_for(cfg, "judge")
    judge_prompt = prompt_templates.render_prompt(cfg, "verification", "judge", _JUDGE_PROMPT)
    return (
        Role("diff_reviewer", _DIFF_REVIEWER_PROMPT, sonnet),
        Role("scenario_runner", _SCENARIO_RUNNER_PROMPT, sonnet),
        Role("judge", judge_prompt, opus),
    )


def judge_system_prompt() -> str:
    terse = TEAM._role_terse("judge")  # pyright: ignore[reportPrivateUsage]
    return assemble_role_system_prompt(_JUDGE_PROMPT, terse=terse)


def _maybe_post_review(
    workspace: Workspace, prior: Handover | None, judge_output: str,
    *, forge_target: ForgeTarget,
) -> ReviewPostResult:
    if prior is None or prior.pr_number is None:
        append_audit({
            "task_id": workspace.task_id,
            "action": "skip",
            "reason": "no pr_number on inbound handover",
        })
        return ReviewPostResult("skipped")
    impl_inv_id = prior.from_invocation_id
    effective = _verdict.effective_verdict(judge_output)
    if effective is None:
        append_audit({
            "task_id": workspace.task_id,
            "action": "skip",
            "reason": "judge output missing VERDICT line",
            "pr_number": prior.pr_number,
        })
        return ReviewPostResult("skipped")
    critical = _verdict.parse_critical_count(judge_output)
    event = github_review_event(judge_output)
    if dry_run_enabled():
        append_audit({
            "task_id": workspace.task_id,
            "action": "dry_run",
            "pr_number": prior.pr_number,
            "verdict": effective,
            "event": event,
            "critical": critical,
            "inv_id": impl_inv_id,
        })
        return ReviewPostResult("skipped")
    outcome, failure_class, attempts = _post_review_with_retries(
        prior.pr_number, effective, judge_output, workspace.task_id, event, impl_inv_id,
        forge_target=forge_target,
    )
    if outcome.ok:
        _verifier_thread_resolution.resolve_after_approval(
            event, critical, prior.pr_number, task_id=workspace.task_id,
            inv_id=impl_inv_id, forge_target=forge_target,
            counts=reviewer_count_var.get(),
        )
        return ReviewPostResult("posted")
    return ReviewPostResult("failed", failure_class, attempts)


def _effective_task_id(workspace: Workspace) -> str:
    task_id = _verification_task_id_var.get()
    return task_id if task_id is not None else workspace.task_id


def _scoped_dod_for_current(
    workspace: Workspace,
) -> tuple[list[str], list[str]] | None:
    task_id = _verification_task_id_var.get()
    if task_id is None:
        return None
    return _acceptance_mod.scoped_dod(workspace.meta, task_id)


def _scope_block_present(workspace: Workspace) -> bool:
    scoped = _scoped_dod_for_current(workspace)
    return scoped is not None and bool(scoped[0])


def _judge_retry_reason(
    workspace: Workspace, judge_output: str
) -> _JudgeRetryReason | None:
    if _verdict.effective_verdict(judge_output) is None:
        return "missing_verdict"
    if (
        _scope_block_present(workspace)
        and _verdict.parse_in_scope_critical_count(judge_output) is None
    ):
        return "missing_in_scope_count"
    return None


def _current_task_manifest(workspace: Workspace) -> list[str]:
    task = _find_current_task(workspace)
    return task.files if task is not None else []


def _out_of_manifest_files(workspace: Workspace, prior: Handover | None) -> list[str]:
    if prior is None or prior.pr_number is None:
        return []
    manifest = _current_task_manifest(workspace)
    if not manifest:
        return []
    diff_files = _pr_branch.fetch_diff_files(
        prior.pr_number, forge_target=_pr_forge.forge_target_for_ws(workspace)
    )
    if diff_files is None:
        return []
    return _overlap_mod.find_out_of_manifest(sorted(diff_files), manifest)


def _find_current_task(workspace: Workspace) -> _Task | None:
    task_id = _effective_task_id(workspace)
    for task in workspace.meta.tasks:
        if task.id == task_id:
            return task
    return None


def _scenario_mode(workspace: Workspace, prior: Handover | None) -> ScenarioMode:
    task = _find_current_task(workspace)
    if _scenarios_waived(task, prior):
        return "WAIVED"
    return scenario_binding.scenario_mode(
        workspace, prior, task_id=_effective_task_id(workspace)
    )


def _effective_scenarios(
    workspace: Workspace, prior: Handover | None
) -> list[TestScenario]:
    return scenario_binding.effective_scenarios(
        workspace, prior, task_id=_effective_task_id(workspace)
    )


def github_review_event(judge_output: str) -> _pr_post.ReviewEvent:
    verdict = _verdict.effective_verdict(judge_output)
    if verdict is None:
        return "COMMENT"
    if (
        verdict == "REQUEST_CHANGES"
        and _verdict.effective_critical_count(judge_output) == 0
        and GATE_BLOCK_MARKER in judge_output
        and not parse_actionable_gate_checks(judge_output)
    ):
        return "COMMENT"
    return verdict


def _apply_dod_scope_block(
    base: str, workspace: Workspace
) -> tuple[str, tuple[list[str], list[str]] | None]:
    scoped = _scoped_dod_for_current(workspace)
    if scoped is None:
        return base, None
    in_scope, out_of_scope = scoped
    if not in_scope:
        return base, None
    return f"{base}\n\n{_format_dod_scope_block(in_scope, out_of_scope)}", scoped


def _apply_scenario_block(
    base: str,
    workspace: Workspace,
    prior: Handover | None,
    scoped: tuple[list[str], list[str]] | None,
) -> str:
    mode = _scenario_mode(workspace, prior)
    if mode == "WAIVED":
        return f"{base}\n\n{SCENARIOS_WAIVED_MARKER}"
    if mode == "MISSING":
        if scoped is not None:
            return f"{base}\n\n{SCENARIOS_COVERED_BY_DOD_MARKER}"
        return base
    scenarios = _effective_scenarios(workspace, prior)
    waived = scenario_binding.waived_ids(workspace.meta, scenarios)
    return f"{base}\n\n{_format_scenarios_block(scenarios, waived)}"


def _maybe_persist_scenario_outcomes(
    workspace: Workspace, inv_id: str, results: list[ScenarioResult]
) -> None:
    if not _scenario_runner_already_succeeded(workspace, inv_id):
        _scenario_gate.save_outcomes(workspace, inv_id, _to_scenario_outcomes(results))


def _capability_boundary_handover(
    workspace: Workspace,
    prior: Handover | None,
    emit: EmitFn,
    inv_id: str,
    last_output: str,
    effective_verdict: _verdict.Verdict,
) -> Handover | None:
    if effective_verdict != "REQUEST_CHANGES":
        return None
    capability_boundary_reason = declared_capability_boundary(last_output)
    if capability_boundary_reason is None or capability_boundary_waived(
        workspace, prior, capability_boundary_reason
    ):
        return None
    return _emit_capability_boundary_escalation(
        workspace, prior, emit, inv_id, last_output,
        reason=capability_boundary_reason,
    )


def _audit_verdict_counts(
    task_id: str, last_output: str, prior_pr: int | None
) -> None:
    report = reviewer_count_var.get()
    reviewer = report.reviewer if report is not None else None
    append_audit({
        "task_id": task_id,
        "action": "verdict_counts",
        "critical_raw": _verdict.parse_critical_count(last_output),
        "critical_in_scope": _verdict.parse_in_scope_critical_count(last_output),
        "effective_verdict": _verdict.effective_verdict(last_output),
        "pr_number": prior_pr,
        "critical_reviewer": reviewer,
        "reviewer_divergence": (
            reviewer is not None and reviewer != _verdict.parse_critical_count(last_output)
        ),
    })


@dataclass(frozen=True)
class VerificationTeam(StaticPipelineTeam):
    name: str = "verification"
    roles: tuple[Role, ...] = ROLES
    to_team_suggestions: tuple[str, ...] = ()
    restart_rederivable = True

    def plan_workflow(
        self, workspace: Workspace, prior: Handover | None
    ) -> list[_workflow_mod.PendingTaskSpec]:
        del prior
        cfg = _pr_forge.governing_config_for_ws(workspace)
        roles = roles_for_config(cfg)
        deps = _depends_on_map(_VERIFICATION_STAGES)
        return [
            _workflow_mod.PendingTaskSpec(
                id=role.name,
                role=role.name,
                model=role.model,
                depends_on=deps[role.name],
            )
            for role in roles
        ]

    def _role_overrides(self, role_name: str, workspace: Workspace) -> WorkerOverrides:
        del role_name
        cwd = _grounded_cwd_var.get() or workspace.path
        cfg = _pr_forge.governing_config_for_ws(workspace)
        return WorkerOverrides(
            cwd=cwd,
            allowed_tools=with_gate_reachability(
                render_allowed_tools(cfg, self.name), cfg.gate_commands
            ),
        )

    def _role_terse(self, role_name: str) -> bool:
        return role_name not in {"judge", "diff_reviewer"}

    def _build_base_user_text(
        self,
        workspace: Workspace,
        prior: Handover | None,
        instructions: str | None = None,
    ) -> str:
        base = super()._build_base_user_text(workspace, prior, instructions)
        base, scoped = _apply_dod_scope_block(base, workspace)
        base = _apply_scenario_block(base, workspace, prior, scoped)
        out_of_manifest = _out_of_manifest_files(workspace, prior)
        if out_of_manifest:
            base = f"{base}\n\n{_format_manifest_deviation_block(out_of_manifest)}"
        return base

    def _role_user_prefix(self, role_name: str) -> str:
        if role_name == "diff_reviewer":
            return _review_context_var.get() or ""
        if role_name == "clause_auditor":
            return _clause_audit.evidence_prefix()
        if role_name != "scenario_runner":
            return ""
        results = _scenario_results_var.get()
        results_prefix = f"{format_results_block(results)}\n\n" if results else ""
        return results_prefix + _review_context.targeted_test_scope_block(
            _grounded_cwd_var.get()
        )

    def _compute_scenario_results(
        self, workspace: Workspace, prior: Handover | None, inv_id: str
    ) -> list[ScenarioResult]:
        mode = _scenario_mode(workspace, prior)
        if mode == "WAIVED":
            return []
        if _scenario_runner_already_succeeded(workspace, inv_id):
            return []
        cwd = _grounded_cwd_var.get() or workspace.path
        cfg = _pr_forge.governing_config_for_ws(workspace)
        results: list[ScenarioResult] = []
        if mode == "RUN":
            effective = _effective_scenarios(workspace, prior)
            results.extend(
                _scenario_runner.run_scenarios(
                    scenario_binding.runnable(workspace.meta, effective),
                    cwd=str(cwd),
                    cfg=cfg,
                    strict_ids=scenario_binding.fix_bound_ids(workspace.meta, effective),
                )
            )
        results.extend(
            _changed_e2e_results(Path(cwd), cfg, env=_changed_e2e_env(workspace, cfg))
        )
        return results

    def _run_judge_retry(
        self,
        workspace: Workspace,
        graph: _workflow_mod.WorkflowGraph,
        inv_id: str,
        emit: EmitFn,
        base: str,
        all_outputs: list[tuple[str, str]],
        on_proc_started: OnProcStarted | None,
        roles: tuple[Role, ...],
        *,
        reason: _JudgeRetryReason,
    ) -> str:
        judge_role = next(r for r in roles if r.name == "judge")
        others = ", ".join(r.name for r in roles if r.name != "judge")
        coverage = (
            f"Other roles in this pipeline: {others}. "
            "Your scope is only judge — do NOT duplicate work that another "
            "role will cover."
        )
        user_text = (
            build_role_user_text(base, all_outputs, coverage)
            + _JUDGE_RETRY_NUDGE
        )
        append_audit({
            "task_id": workspace.task_id,
            "action": "judge_verdict_missing_retry",
            "reason": reason,
        })
        return run_or_resume_node(
            workspace, graph, inv_id, emit,
            node_id=_JUDGE_RETRY_NODE_ID,
            role="judge",
            model=judge_role.model,
            depends_on=["judge"],
            system_prompt=assemble_role_system_prompt(
                judge_role.system_prompt, terse=self._role_terse("judge")
            ),
            user_text=user_text,
            overrides=self._role_overrides("judge", workspace),
            on_proc_started=on_proc_started,
        )

    def _collect_outputs(
        self,
        workspace: Workspace,
        inv_id: str,
        base: str,
        emit: EmitFn,
        on_proc_started: OnProcStarted | None,
    ) -> str:
        cfg = _pr_forge.governing_config_for_ws(workspace)
        roles = roles_for_config(cfg)
        role_by_name = {role.name: role for role in roles}
        stages = _clause_audit.stages_for(_VERIFICATION_STAGES)
        depends_on_map = _depends_on_map(stages)
        with workflow_lifecycle(workspace, team_name=self.name, inv_id=inv_id, emit=emit) as graph:
            outputs: list[tuple[str, str]] = []
            for stage in stages:
                stage_roles = _clause_audit.stage_roles(stage, role_by_name, cfg)

                def run_role(role: Role) -> str:
                    return self._run_role(
                        workspace, graph, inv_id, emit, role,
                        depends_on_map[role.name], base, outputs, on_proc_started,
                    )

                outputs.extend(_clause_audit.absorb(
                    _run_stage_roles(stage_roles, run_role),
                    workspace, graph, inv_id, emit, base, outputs, on_proc_started,
                    overrides=self._role_overrides("clause_auditor", workspace),
                ))
            retry_reason = _judge_retry_reason(workspace, outputs[-1][1])
            if retry_reason is not None:
                retry_out = self._run_judge_retry(
                    workspace, graph, inv_id, emit, base, list(outputs), on_proc_started, roles,
                    reason=retry_reason,
                )
                outputs[-1] = (outputs[-1][0], retry_out)
            reviewer_count_var.set(_verdict.report_critical_counts(outputs[-1][1], outputs[0][1]))
            final = _verdict._apply_reviewer_count_fallback(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
                outputs[-1][1], outputs[0][1]
            )
            if not _scope_block_present(workspace):
                final = _verdict._strip_in_scope_critical_line(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
                    final
                )
        return final

    def _pre_routing_escalation(
        self,
        workspace: Workspace,
        prior: Handover | None,
        emit: EmitFn,
        inv_id: str,
        last_output: str,
        effective_verdict: _verdict.Verdict,
        outcome: _clause_audit.ClauseAuditOutcome,
    ) -> Handover | None:
        capability_handover = _capability_boundary_handover(
            workspace, prior, emit, inv_id, last_output, effective_verdict,
        )
        if capability_handover is not None:
            return capability_handover
        if outcome.escalate_unactionable:
            return _emit_clause_pr_state_escalation(
                workspace, prior, emit, inv_id, last_output,
                unactionable_rows=outcome.unactionable_rows,
                clause_outcome=outcome.clause_outcome,
            )
        stall = _clause_stall.detect(workspace, prior, outcome.clause_outcome)
        if stall is not None:
            return _emit_clause_stall_escalation(
                workspace, prior, emit, inv_id, last_output, stall=stall,
                clause_outcome=outcome.clause_outcome,
            )
        return None

    def _build_verdict_handover(
        self,
        workspace: Workspace,
        prior: Handover | None,
        last_output: str,
        inv_id: str,
        emit: EmitFn,
        forge_target: ForgeTarget,
        scenario_results: list[ScenarioResult],
    ) -> Handover:
        prior_pr = prior.pr_number if prior is not None else None
        downgrade = resolve_external_gates(
            last_output, prior_pr, workspace.task_id, forge_target=forge_target
        )
        outcome = _clause_audit.apply(downgrade.output)
        last_output = _scenario_gate.apply(outcome.judge_output, workspace, inv_id)
        _audit_verdict_counts(workspace.task_id, last_output, prior_pr)
        post_result = _maybe_post_review(
            workspace, prior, last_output, forge_target=forge_target
        )
        if post_result.status == "failed":
            return _emit_review_post_failed(
                workspace, prior, emit, inv_id, last_output,
                failure_class=post_result.failure_class or "transient",
                attempts=post_result.attempts,
                forge_target=forge_target,
            )
        effective_verdict = _verdict.effective_verdict(last_output)
        if effective_verdict is None:
            return _emit_judge_parse_failed(workspace, prior, emit, inv_id, last_output)
        escalation = self._pre_routing_escalation(
            workspace, prior, emit, inv_id, last_output, effective_verdict, outcome,
        )
        if escalation is not None:
            return escalation
        suggestions = suggestions_for_verdict(
            last_output, prior_pr, gate_flaky_only=downgrade.flaky_only,
            gate_dirty=downgrade.dirty, gate_non_actionable=downgrade.non_actionable)
        review_artifact = _write_review_artifact(workspace, inv_id, last_output)
        handover = build_forward_handover(
            workspace,
            from_team=self.name,
            to_team_suggestions=list(suggestions),
            summary=last_output,
            prior=prior,
            invocation_id=inv_id,
        )
        decisions = _verdict_decisions(last_output, suggestions, prior_pr)
        return handover.model_copy(update={
            "pr_number": prior_pr,
            "decisions": decisions,
            "artifacts_produced": [review_artifact],
            "scenario_results": _to_scenario_outcomes(scenario_results),
            "clause_outcome": outcome.clause_outcome,
        })

    def _resolve_pr_and_branch(
        self, prior: Handover | None, workspace: Workspace, forge_target: ForgeTarget
    ) -> tuple[int | None, str | None]:
        pr_number = prior.pr_number if prior is not None else None
        if pr_number is None:
            return None, None
        branch = _pr_branch.resolve_existing_branch(
            pr_number, workspace.task_id, forge_target=forge_target
        )
        return pr_number, branch

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
        del resume_inv_id
        effective_task_id = task_id if task_id is not None else workspace.task_id
        forge_target = _pr_forge.forge_target_for_ws(workspace)
        pr_number, branch = self._resolve_pr_and_branch(prior, workspace, forge_target)
        gate = _verification_merged_guard.resolve_pr_gate(
            workspace, prior, emit, inv_id, pr_number, branch,
            team_name=self.name, forge_target=forge_target,
            precondition_escalation=lambda pr: self._pr_precondition_failed_escalation(
                workspace, prior, emit, inv_id, pr
            ),
        )
        if isinstance(gate, Handover):
            return gate
        pr_number, branch = gate
        with _try_acquire_grounded_checkout(
            workspace, pr_number, branch, workspace.task_id, inv_id, emit,
        ) as grounded_path:
            if grounded_path is None:
                return _emit_cannot_verify(
                    workspace, prior, emit, inv_id, forge_target=forge_target
                )
            with _run_tokens.scope(
                workspace, pr_number, effective_task_id, forge_target, grounded_path,
                grounded_var=_grounded_cwd_var, task_id_var=_verification_task_id_var,
                review_context_var=_review_context_var,
            ):
                results = self._compute_scenario_results(workspace, prior, inv_id)
                _maybe_persist_scenario_outcomes(workspace, inv_id, results)
                scenario_token = _scenario_results_var.set(results)
                try:
                    last_output = self.run_pipeline(
                        workspace, prior, emit,
                        inv_id=inv_id, instructions=instructions,
                        on_proc_started=on_proc_started,
                    )
                finally:
                    _scenario_results_var.reset(scenario_token)
                handover = self._build_verdict_handover(
                    workspace, prior, last_output, inv_id, emit, forge_target, results
                )
        return handover

    def _pr_precondition_failed_escalation(
        self,
        workspace: Workspace,
        prior: Handover | None,
        emit: EmitFn,
        inv_id: str,
        pr_number: int | None,
    ) -> Handover:
        with workflow_lifecycle(workspace, team_name=self.name, inv_id=inv_id, emit=emit):
            last_ref = _pr_handover.find_last_pr_reference(workspace)
            reason = _pr_precondition_reason(pr_number, last_ref)
            entry: dict[str, object] = {
                "task_id": workspace.task_id, "inv_id": inv_id,
                "action": "pr_precondition_failed", "pr_number": pr_number,
            }
            if last_ref is not None:
                entry["last_pr_handover_id"] = last_ref.handover_id
                entry["last_pr_from_team"] = last_ref.from_team
                entry["last_pr_from_invocation_id"] = last_ref.from_invocation_id
            append_audit(entry)
        return _pr_handover.no_pr_escalation_handover(
            workspace, prior, inv_id,
            team_name=self.name,
            reason=reason,
            prior_pr=pr_number,
            default_forbidden=(),
            dependency_bot_authorized=(
                prior.dependency_bot_authorized if prior is not None else False
            ),
        )


TEAM = VerificationTeam()


__all__ = [
    "GATE_ACTIONABLE_CHECKS_PREFIX",
    "GATE_ACTIONABLE_NO",
    "GATE_ACTIONABLE_PREFIX",
    "GATE_ACTIONABLE_YES",
    "GATE_BLOCK_MARKER",
    "GATE_SECTION_RE",
    "ROLES",
    "SCENARIOS_MISSING_MARKER",
    "SCENARIOS_COVERED_BY_DOD_MARKER",
    "SCENARIOS_WAIVED_MARKER",
    "TEAM",
    "VerificationTeam",
    "ScenarioMode",
    "_JUDGE_PARSE_FAIL_QUESTION",
    "_JUDGE_PROMPT",
    "_JUDGE_RETRY_NODE_ID",
    "_effective_scenarios",
    "_emit_judge_parse_failed",
    "_find_current_task",
    "_grounded_cwd_var",
    "_scenario_mode",
    "append_audit",
    "dry_run_enabled",
    "gate_block_section",
    "judge_system_prompt",
    "parse_inline_findings",
    "parse_actionable_gate_checks",
    "strip_inline_findings_block",
    "suggestions_for_verdict",
]
