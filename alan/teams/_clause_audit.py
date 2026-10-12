from __future__ import annotations

import contextvars
import os
import subprocess
from dataclasses import dataclass, field
from typing import Literal, NamedTuple, cast

import alan.teams._base as _base
import alan.teams._clause_actionability as _clause_actionability
import alan.teams._pr_branch as _pr_branch
import alan.teams._pr_forge as _pr_forge
import alan.teams._pr_ready as _pr_ready
import alan.teams._static_pipeline as _static_pipeline
import alan.teams._verdict as _verdict
from alan import acceptance as _acceptance
from alan.claude_cli import OnProcStarted
from alan.engine_config import _DEFAULT as _ENGINE_DEFAULT  # pyright: ignore[reportPrivateUsage]
from alan.engine_config import EngineConfig, ForgeTarget
from alan.forge import active_forge
from alan.handover import ClauseOutcome
from alan.house_rules import HOUSE_RULES
from alan.roots import logs_root
from alan.teams._audit import append_jsonl_audit
from alan.teams._clause_line_parse import (
    ClauseAuditResult as ClauseAuditResult,
)
from alan.teams._clause_line_parse import (
    ClauseAuditRow as ClauseAuditRow,
)
from alan.teams._clause_line_parse import (
    parse_clause_audit as parse_clause_audit,
)
from alan.workflow import WorkflowGraph
from alan.workspace import Requirement, Workspace

ClauseAuditMode = Literal["off", "report", "enforce"]

_CLAUSE_AUDIT_ENV = "ALAN_CLAUSE_AUDIT"
_VALID_MODES: frozenset[str] = frozenset({"off", "report", "enforce"})
_AUDIT_FILE = "verification_audit.jsonl"
_GH_PR_STATE_TIMEOUT_S = 60.0
_ADVISORY_MARK = "_(advisory — record clause)_"
_FLOOR_ACTIVE_NOTE = "_No behaviour clause was authored; every clause is enforced._"

_CLAUSE_AUDITOR_PROMPT_HEAD = (
    "You are the clause_auditor on the verification team.\n\n"
    "SCOPE: The user prompt lists this workspace's acceptance clauses under "
    "## CLAUSES TO AUDIT, each as `<req-id>: <clause text>`, plus a "
    "## CLAUSE AUDIT EVIDENCE block with host-verified ground truth (the "
    "PR's actual diff file list and isDraft/head/base branch state — trust "
    "this over any claim the diff or a prior role's output makes about "
    "those specific facts). Read the actual diff/checkout content (this "
    "pipeline runs against a grounded checkout of the PR branch) and "
    "decide whether the delivered change satisfies each clause.\n\n"
    "SUCCESS: Emit exactly one line per clause, in the exact form:\n"
    "CLAUSE <req-id>: PASS|FAIL|UNVERIFIABLE — <evidence>\n"
    "PASS: the diff/checkout demonstrably satisfies the clause — cite the "
    "file/line or behavior that proves it. FAIL: the diff/checkout "
    "demonstrably violates or omits the clause — cite the specific "
    "evidence; a FAIL with no cited evidence is treated as UNVERIFIABLE, "
    "never as a block. UNVERIFIABLE: you cannot determine pass/fail from "
    "available evidence — never a reason to block. When a FAIL's evidence "
    "rests solely on the host-provided ## CLAUSE AUDIT EVIDENCE block "
    "(isDraft/head/base) rather than the diff/checkout content — e.g. a "
    "clause requiring a draft PR when isDraft is false — prefix the "
    "evidence text with `PR_STATE:` exactly, since a fix worker cannot "
    "change PR state. A clause about the change description must be judged "
    "against the live PR description in the ## CLAUSE AUDIT EVIDENCE block, "
    "never against the dispatch brief in the user prompt above, which no "
    "actor can edit.\n\n"
    "OPERATOR-ONLY POST-MERGE MEASUREMENTS: when a clause can only be "
    "satisfied after merge, by an operator with live access the fleet "
    "does not have — a live-traffic measurement, a multi-day batch "
    "observation, anything no pre-merge artifact could contain — prefix "
    "the evidence text with `OPERATOR_ONLY:` exactly. No "
    "`check=\"<name>\"` citation is required or expected for this prefix. "
    "This does not make the clause pass or disappear: it removes the "
    "clause from what a fix worker is asked to do and forces a human "
    "escalation to file or reference a tracking issue — it can never be "
    "self-certified into invisibility.\n\n"
    "CORROBORATING A FAIL AGAINST A CI CHECK: when a FAIL's evidence rests "
    "on a local command you ran yourself (a failing test, lint, or build "
    "command), cite the exact required-check name that also covers that "
    "command, in the form `check=\"<exact check name>\"` inside the "
    "evidence text — this is a nomination, not a verdict; the host "
    "corroborates it against the check's live conclusion before it can "
    "change anything. The same citation is owed when a FAIL rests on a "
    "command this pass did not or may not run — an end-to-end or rendered-"
    "UI audit run, a gate reserved for the pipeline — and a required check "
    "runs exactly that command: say the run was not observed here and cite "
    "the check, rather than leaving the FAIL uncited. Take the name "
    "verbatim from the host's required-check list in the ## CLAUSE AUDIT "
    "EVIDENCE block when it is present, otherwise from the `name:` of the "
    "job in this checkout's `.github/workflows/` that runs the command; "
    "cite it even when you cannot tell whether it has finished — the host "
    "reads the check's conclusion when it decides, not when you wrote the "
    "line. When you suspect the failure is caused by pollution "
    "in your own checkout or host environment (stale build artifacts, "
    "leftover state) rather than a defect in the diff, additionally prefix "
    "the evidence text with `ENV:` exactly. Never use `ENV:` without also "
    "citing `check=\"<name>\"` in the same evidence text.\n\n"
)

_DEFAULT_OWNED_RUNS = (
    "the one full backend suite run (`uv run --no-active pytest`, no path "
    "args, no `--no-cov`) and the one frontend coverage run (`npm "
    "--prefix alan/web run test:coverage`, after an `npm --prefix "
    "alan/web ci` warmup since the worktree has no node_modules)"
)

_DEFAULT_TEST_OWNERSHIP_BLOCK = (
    "TEST OWNERSHIP: you are this verification pass's sole owner of "
    + _DEFAULT_OWNED_RUNS
    + " — run each exactly once here as empirical evidence for the audit. "
    "Capture each run the one time you run it, keeping its own exit "
    "status — `uv run --no-active pytest > pytest_full.log 2>&1; rc=$?; "
    "tail -60 pytest_full.log; (exit $rc)` and `npm --prefix alan/web run "
    "test:coverage > vitest_full.log 2>&1; rc=$?; tail -60 "
    "vitest_full.log; (exit $rc)` — and cite from those files (`grep -n`) "
    "for every clause; re-formatting output (`-q`, `--co`, `| tail`, "
    "`| grep`) is never a reason to run a suite again, and a second run "
    "of either suite is a defect in the audit, not extra evidence. "
    "diff_reviewer and scenario_runner are directed at targeted, "
    "diff-scoped test selections and do not repeat either full "
    "suite.\n\n"
)

_RETRY_NO_RERUN_SENTENCE = (
    " Do NOT run any test suite or gate command in this retry: the first "
    "attempt's full output is in the user prompt as the `### "
    "clause_auditor` section, and any log files it captured are still in "
    "this checkout — re-read those, cite from them, and re-emit the CLAUSE "
    "lines. diff_reviewer and scenario_runner are directed at targeted, "
    "diff-scoped test selections and do not repeat those runs.\n\n"
)

_NO_GATES_DECLARED = (
    "this engine declares no local quality gates — do not invent any."
)

_CLAUSE_AUDITOR_PROMPT_TAIL = (
    "End with one line verbatim: CLAUSE_FAILURES: <n> (n = count of FAIL "
    "lines above)."
)

_CLAUSE_AUDITOR_PROMPT = (
    _CLAUSE_AUDITOR_PROMPT_HEAD
    + _DEFAULT_TEST_OWNERSHIP_BLOCK
    + _CLAUSE_AUDITOR_PROMPT_TAIL
)


def _generic_owned_runs(cfg: EngineConfig) -> str:
    gate_list = ", ".join(f"`{' '.join(cmd)}`" for cmd in cfg.gate_commands)
    return f"this engine's gate command(s) — {gate_list} —"


def _generic_test_ownership_block(cfg: EngineConfig) -> str:
    if not cfg.gate_commands:
        return (
            "TEST OWNERSHIP: " + _NO_GATES_DECLARED + " diff_reviewer and "
            "scenario_runner are directed at targeted, diff-scoped test "
            "selections; this pass's own empirical evidence is whatever this "
            "engine's own continuous-integration checks report after the PR "
            "is open.\n\n"
        )
    return (
        "TEST OWNERSHIP: you are this verification pass's sole owner of "
        + _generic_owned_runs(cfg)
        + " run each exactly "
        "once here as empirical evidence for the audit. Capture each run's "
        "output to a gitignored file inside this checkout the one time you "
        "run it and cite from that file; never run a gate again just to "
        "re-read or re-format its output — a second run is a defect in the "
        "audit, not extra evidence. diff_reviewer and "
        "scenario_runner are directed at targeted, diff-scoped test "
        "selections and do not repeat these gate runs.\n\n"
    )


def clause_auditor_prompt(cfg: EngineConfig = _ENGINE_DEFAULT) -> str:
    if cfg == _ENGINE_DEFAULT:
        return _CLAUSE_AUDITOR_PROMPT
    return (
        _CLAUSE_AUDITOR_PROMPT_HEAD
        + _generic_test_ownership_block(cfg)
        + _CLAUSE_AUDITOR_PROMPT_TAIL
    )


def _retry_test_ownership_block(cfg: EngineConfig) -> str:
    if cfg == _ENGINE_DEFAULT:
        owned = _DEFAULT_OWNED_RUNS + " —"
    elif cfg.gate_commands:
        owned = _generic_owned_runs(cfg)
    else:
        return "TEST OWNERSHIP (RETRY): " + _NO_GATES_DECLARED + _RETRY_NO_RERUN_SENTENCE
    return (
        "TEST OWNERSHIP (RETRY): the first clause_auditor attempt in this "
        "checkout already owned and ran " + owned + " exactly once as this "
        "pass's empirical evidence." + _RETRY_NO_RERUN_SENTENCE
    )


def clause_auditor_retry_prompt(cfg: EngineConfig = _ENGINE_DEFAULT) -> str:
    return (
        _CLAUSE_AUDITOR_PROMPT_HEAD
        + _retry_test_ownership_block(cfg)
        + _CLAUSE_AUDITOR_PROMPT_TAIL
    )

_CLAUSE_AUDITOR_RETRY_NODE_ID = "clause_auditor_retry"
_CLAUSE_AUDITOR_RETRY_NUDGE = (
    "\n\nRETRY NUDGE — your previous output had no parseable "
    "`CLAUSE <req-id>: PASS|FAIL|UNVERIFIABLE — <evidence>` line. Re-emit "
    "the COMPLETE audit from scratch: one CLAUSE line per clause listed "
    "above, plus a final `CLAUSE_FAILURES: <n>` line. Do NOT run any test "
    "suite or gate command again: your previous attempt's full output is "
    "included above as prior clause_auditor output (the `### "
    "clause_auditor` section), and any log files you captured are still "
    "in this checkout — re-read those and re-emit the lines."
)

_CLAUSE_FAIL_BANNER_TEMPLATE = (
    "> ACCEPTANCE CLAUSE AUDIT — FAILED. Evidenced FAIL on: {ids}. "
    "Downgrading to REQUEST_CHANGES regardless of the judge's own "
    "verdict.\n\n"
)
_CLAUSE_DEMOTED_NOTE_TEMPLATE = (
    "> Also FAILED but not attributable to this change under review, "
    "tracked separately and not the fix worker's to address: {ids}.\n\n"
)
_CLAUSE_UNACTIONABLE_ONLY_BANNER_TEMPLATE = (
    "> ACCEPTANCE CLAUSE AUDIT — FAILED, but not attributable to this "
    "change under review: {cause}. Downgrading to REQUEST_CHANGES pending "
    "human review of the environmental cause; this is not a fix demand and "
    "no fix worker is dispatched.\n\n"
)
_CLAUSE_STOOD_DOWN_NOTE_TEMPLATE = (
    "> ACCEPTANCE CLAUSE AUDIT — stood down: {cause}. Each clause keeps its "
    "own FAIL verdict in the table below, corroborated as not attributable "
    "to this change under review. No verdict rewrite and no human "
    "escalation follow; review proceeds through its normal routing.\n\n"
)


class PrState(NamedTuple):
    is_draft: bool | None
    head_ref: str | None
    base_ref: str | None
    body: str | None


class ClauseAuditOutcome(NamedTuple):
    judge_output: str
    escalate_unactionable: bool
    unactionable_rows: tuple[_clause_actionability.DemotedClause, ...] = ()
    clause_outcome: ClauseOutcome | None = None


@dataclass
class ClauseAuditContext:
    requirements: tuple[Requirement, ...]
    diff_files: frozenset[str] | None
    pr_state: PrState | None
    pr_number: int
    task_id: str
    mode: ClauseAuditMode
    forge_target: ForgeTarget
    readiness: _pr_ready.ReadinessResult | None = None
    result: ClauseAuditResult | None = None
    clause_texts: dict[str, tuple[str, ...]] = field(default_factory=dict)  # pyright: ignore[reportUnknownVariableType]
    floor_active: bool = False


_clause_audit_var: contextvars.ContextVar[ClauseAuditContext | None] = contextvars.ContextVar(
    "_clause_audit_context", default=None
)


def clause_audit_mode() -> ClauseAuditMode:
    raw = os.environ.get(_CLAUSE_AUDIT_ENV, "").strip().lower()
    if raw in _VALID_MODES:
        return cast("ClauseAuditMode", raw)
    return "enforce"


_HOUSE_RULE_IDS = frozenset(Requirement(text=hr.text).id for hr in HOUSE_RULES)


def _append_audit(entry: dict[str, object]) -> None:
    append_jsonl_audit(logs_root() / _AUDIT_FILE, entry, log_label="verification")


def _floor_active(workspace: Workspace) -> bool:
    authored = (r for r in workspace.meta.requirements or [] if r.id not in _HOUSE_RULE_IDS)
    return all(r.kind == "record" for r in authored)


def _as_bool_or_none(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _as_str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _fetch_pr_state(pr_number: int, *, forge_target: ForgeTarget) -> PrState | None:
    try:
        read = active_forge().read_pr(
            pr_number, ("isDraft", "headRefName", "baseRefName", "body"),
            forge_target=forge_target, timeout=_GH_PR_STATE_TIMEOUT_S,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if not read.ok or not read.fields:
        return None
    return PrState(
        is_draft=_as_bool_or_none(read.fields.get("isDraft")),
        head_ref=_as_str_or_none(read.fields.get("headRefName")),
        base_ref=_as_str_or_none(read.fields.get("baseRefName")),
        body=_as_str_or_none(read.fields.get("body")),
    )


def _append_all_withdrawn_audit(workspace: Workspace, task_id: str) -> None:
    _append_audit({
        "task_id": workspace.task_id,
        "action": "clause_audit_skipped_all_withdrawn",
        "audited_task_id": task_id,
    })


def _append_scope_audit_row(
    workspace: Workspace, task_id: str, selected: list[Requirement]
) -> None:
    owners = _acceptance.clause_owner_map(workspace.meta)
    selected_ids = {r.id for r in selected}
    live_ids = {
        r.id for r in _acceptance.live_clauses(list(workspace.meta.requirements or []))
    }
    _append_audit({
        "task_id": workspace.task_id,
        "action": "clause_audit_scope",
        "audited_task_id": task_id,
        "selected": {rid: task_id for rid in sorted(selected_ids)},
        "not_selected": {
            rid: owners[rid] for rid in sorted(live_ids - selected_ids) if rid in owners
        },
    })


def _select_undecomposed_requirements(workspace: Workspace, task_id: str) -> list[Requirement]:
    meta = workspace.meta
    live = _acceptance.live_clauses(list(meta.requirements or []))
    if meta.requirements and not live:
        _append_all_withdrawn_audit(workspace, task_id)
    return live


def _select_decomposed_requirements(workspace: Workspace, task_id: str) -> list[Requirement]:
    reqs = _acceptance.scoped_clause_requirements(workspace.meta, task_id)
    if reqs is None:
        _append_audit({
            "task_id": workspace.task_id,
            "action": "clause_audit_skipped_unscoped",
            "audited_task_id": task_id,
        })
        return []
    if not reqs:
        _append_audit({
            "task_id": workspace.task_id,
            "action": "clause_audit_skipped_no_owned_requirements",
            "audited_task_id": task_id,
        })
        return []
    live = _acceptance.live_clauses(reqs)
    if not live:
        _append_all_withdrawn_audit(workspace, task_id)
    else:
        _append_scope_audit_row(workspace, task_id, live)
    return live


def _select_requirements(workspace: Workspace, task_id: str) -> list[Requirement]:
    if len(workspace.meta.tasks) <= 1:
        return _select_undecomposed_requirements(workspace, task_id)
    return _select_decomposed_requirements(workspace, task_id)


def begin(
    workspace: Workspace, pr_number: int, task_id: str, forge_target: ForgeTarget,
) -> contextvars.Token[ClauseAuditContext | None]:
    mode = clause_audit_mode()
    if mode == "off":
        return _clause_audit_var.set(None)
    requirements = _select_requirements(workspace, task_id)
    if not requirements:
        return _clause_audit_var.set(None)
    diff_files = _pr_branch.fetch_diff_files(pr_number, forge_target=forge_target)
    pr_state = _fetch_pr_state(pr_number, forge_target=forge_target)
    readiness = _pr_ready.check_pr_merge_readiness(pr_number, task_id, forge_target=forge_target)
    live_ids = {r.id for r in requirements}
    clause_texts = {
        rid: tuple(texts)
        for rid, texts in _acceptance.owned_clause_texts(workspace.meta, task_id).items()
        if rid in live_ids
    }
    context = ClauseAuditContext(
        requirements=tuple(requirements), diff_files=diff_files, pr_state=pr_state,
        pr_number=pr_number, task_id=task_id, mode=mode, forge_target=forge_target,
        readiness=readiness, clause_texts=clause_texts, floor_active=_floor_active(workspace),
    )
    return _clause_audit_var.set(context)


def end(token: contextvars.Token[ClauseAuditContext | None]) -> None:
    _clause_audit_var.reset(token)


def stages_for(
    stages: tuple[_static_pipeline.Stage, ...],
) -> tuple[_static_pipeline.Stage, ...]:
    if _clause_audit_var.get() is None:
        return stages
    first, *rest = stages
    return ((*first, "clause_auditor"), *rest)


def stage_roles(
    stage: _static_pipeline.Stage, role_by_name: dict[str, _static_pipeline.Role],
    cfg: EngineConfig = _ENGINE_DEFAULT,
) -> tuple[_static_pipeline.Role, ...]:
    roles: list[_static_pipeline.Role] = []
    for name in stage:
        if name == "clause_auditor":
            model = role_by_name["diff_reviewer"].model
            roles.append(
                _static_pipeline.Role("clause_auditor", clause_auditor_prompt(cfg), model)
            )
        else:
            roles.append(role_by_name[name])
    return tuple(roles)


_DIFF_FILES_MAX_LISTED = 200


def _diff_files_evidence_line(diff_files: frozenset[str]) -> str:
    files = sorted(diff_files)
    shown = files[:_DIFF_FILES_MAX_LISTED]
    omitted = len(files) - len(shown)
    listed = ", ".join(shown)
    if omitted:
        listed += f", … and {omitted} more"
    return f"PR diff files ({len(files)} total): {listed}"


def _diff_files_lines(context: ClauseAuditContext) -> list[str]:
    if context.diff_files is not None:
        return [_diff_files_evidence_line(context.diff_files)]
    return ["PR diff files: unavailable"]


def _pr_state_lines(context: ClauseAuditContext) -> list[str]:
    if context.pr_state is None:
        return ["PR state: unavailable"]
    return [
        f"PR state — isDraft={context.pr_state.is_draft}, "
        f"head={context.pr_state.head_ref}, base={context.pr_state.base_ref}",
        "PR description (live, host-fetched via `gh pr view`) — a clause "
        "about the change description must be judged against this text, "
        "never against the dispatch brief in the user prompt above, which "
        "no actor can edit and which diverges the moment anyone updates "
        "the pull request:",
        context.pr_state.body if context.pr_state.body else "(empty)",
    ]


def _clause_lines(context: ClauseAuditContext) -> list[str]:
    lines: list[str] = []
    for req in context.requirements:
        lines.append(f"- {req.id}: {req.text}")
        for text in context.clause_texts.get(req.id, ()):
            lines.append(f"    - {text}")
    return lines


def evidence_prefix() -> str:
    context = _clause_audit_var.get()
    if context is None:
        return ""
    lines = ["## CLAUSE AUDIT EVIDENCE (host-verified ground truth)", ""]
    lines.extend(_diff_files_lines(context))
    lines.extend(_pr_state_lines(context))
    lines.extend(_clause_actionability.check_name_lines(context.readiness))
    lines.append("")
    lines.append("## CLAUSES TO AUDIT")
    lines.extend(_clause_lines(context))
    return "\n".join(lines) + "\n\n"


def _degraded_result(requirements: tuple[Requirement, ...]) -> ClauseAuditResult:
    rows = tuple(
        ClauseAuditRow(req_id=req.id, verdict="UNVERIFIABLE", evidence="parse failure — degraded")
        for req in requirements
    )
    return ClauseAuditResult(
        rows=rows, reported_failures=None, count_mismatch=False, parse_failed=True,
    )


def _table_row(row: ClauseAuditRow, advisory_ids: frozenset[str]) -> str:
    evidence = row.evidence or "—"
    if row.verdict == "FAIL" and row.req_id in advisory_ids:
        evidence = f"{evidence} {_ADVISORY_MARK}"
    return f"| {row.req_id} | {row.verdict} | {evidence} |"


def _floor_lines(floor_active: bool) -> list[str]:
    return ["", _FLOOR_ACTIVE_NOTE] if floor_active else []


def render_clause_table(
    result: ClauseAuditResult,
    advisory_ids: frozenset[str] = frozenset(),
    floor_active: bool = False,
) -> str:
    lines = [
        "## Acceptance Clause Audit", "",
        "| Clause | Verdict | Evidence |", "| --- | --- | --- |",
    ]
    lines.extend(_table_row(row, advisory_ids) for row in result.rows)
    lines.extend(_floor_lines(floor_active))
    if result.parse_failed:
        lines.append("")
        lines.append(
            "_Auditor output failed to parse after one retry — degraded to "
            "UNVERIFIABLE for every clause._"
        )
    if result.count_mismatch:
        fails = sum(1 for row in result.rows if row.verdict == "FAIL")
        lines.append("")
        lines.append(
            f"_CLAUSE_FAILURES line ({result.reported_failures}) did not match "
            f"the parsed FAIL count ({fails}) — gating used the parsed rows._"
        )
    return "\n".join(lines)


def _clause_auditor_model(workspace: Workspace) -> str:
    cfg = _pr_forge.governing_config_for_ws(workspace)
    role_cfg = cfg.roles.get("diff_reviewer")
    if role_cfg is not None:
        return role_cfg.model
    return _ENGINE_DEFAULT.roles["diff_reviewer"].model


def _run_clause_auditor_retry(
    workspace: Workspace,
    graph: WorkflowGraph,
    inv_id: str,
    emit: _base.EmitFn,
    base: str,
    outputs: list[tuple[str, str]],
    on_proc_started: OnProcStarted | None,
    prior_output: str,
    *,
    overrides: _base.WorkerOverrides,
) -> str:
    coverage = (
        "You are the clause_auditor on the verification team — do not "
        "duplicate other roles' work."
    )
    user_text = (
        evidence_prefix()
        + _static_pipeline.build_role_user_text(
            base, [*outputs, ("clause_auditor", prior_output)], coverage
        )
        + _CLAUSE_AUDITOR_RETRY_NUDGE
    )
    cfg = _pr_forge.governing_config_for_ws(workspace)
    return _base.run_or_resume_node(
        workspace, graph, inv_id, emit,
        node_id=_CLAUSE_AUDITOR_RETRY_NODE_ID,
        role="clause_auditor",
        model=_clause_auditor_model(workspace),
        depends_on=["clause_auditor"],
        system_prompt=_static_pipeline.assemble_role_system_prompt(
            clause_auditor_retry_prompt(cfg), terse=True
        ),
        user_text=user_text,
        overrides=overrides,
        on_proc_started=on_proc_started,
    )


def _parse_with_retry(
    auditor_output: str,
    context: ClauseAuditContext,
    workspace: Workspace,
    graph: WorkflowGraph,
    inv_id: str,
    emit: _base.EmitFn,
    base: str,
    outputs: list[tuple[str, str]],
    on_proc_started: OnProcStarted | None,
    *,
    overrides: _base.WorkerOverrides,
) -> ClauseAuditResult:
    audited_ids = frozenset(r.id for r in context.requirements)
    result = parse_clause_audit(auditor_output, audited_ids)
    if result is not None:
        return result
    retry_output = _run_clause_auditor_retry(
        workspace, graph, inv_id, emit, base, outputs, on_proc_started, auditor_output,
        overrides=overrides,
    )
    result = parse_clause_audit(retry_output, audited_ids)
    if result is not None:
        return result
    _append_audit({
        "task_id": workspace.task_id,
        "action": "clause_audit_parse_failed",
        "pr_number": context.pr_number,
    })
    return _degraded_result(context.requirements)


def _find_auditor_pair(pairs: list[tuple[str, str]]) -> tuple[str, str] | None:
    return next((p for p in pairs if p[0] == "clause_auditor"), None)


def _without_auditor_pair(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    return [p for p in pairs if p[0] != "clause_auditor"]


def absorb(
    pairs: list[tuple[str, str]],
    workspace: Workspace,
    graph: WorkflowGraph,
    inv_id: str,
    emit: _base.EmitFn,
    base: str,
    outputs: list[tuple[str, str]],
    on_proc_started: OnProcStarted | None,
    *,
    overrides: _base.WorkerOverrides,
) -> list[tuple[str, str]]:
    context = _clause_audit_var.get()
    if context is None:
        return pairs
    auditor_pair = _find_auditor_pair(pairs)
    if auditor_pair is None:
        return pairs
    context.result = _parse_with_retry(
        auditor_pair[1], context, workspace, graph, inv_id, emit, base, outputs, on_proc_started,
        overrides=overrides,
    )
    if context.mode == "report":
        return _without_auditor_pair(pairs)
    return pairs


def _clause_verdict_banner(parts: _clause_actionability.ClausePartition) -> str:
    if not parts.actionable:
        return _CLAUSE_UNACTIONABLE_ONLY_BANNER_TEMPLATE.format(
            cause=_clause_actionability.cause_text(parts.unactionable)
        )
    banner = _CLAUSE_FAIL_BANNER_TEMPLATE.format(
        ids=", ".join(row.req_id for row in parts.actionable)
    )
    if parts.unactionable:
        banner += _CLAUSE_DEMOTED_NOTE_TEMPLATE.format(
            ids=", ".join(d.row.req_id for d in parts.unactionable)
        )
    return banner


def _rewrite_verdict_with_banner(judge_output: str, banner: str) -> str:
    return _verdict.rewrite_verdict_to_request_changes(judge_output, banner)


def _audit_demoted_clauses(
    context: ClauseAuditContext,
    demoted: tuple[_clause_actionability.DemotedClause, ...],
) -> None:
    for d in demoted:
        _append_audit({
            "task_id": context.task_id,
            "action": "clause_demoted_unactionable",
            "pr_number": context.pr_number,
            "req_id": d.row.req_id,
            "demotion_class": d.classification.value,
            "corroborating_check": d.check_name,
            "check_outcome": d.check_outcome.value if d.check_outcome is not None else None,
        })


_CLAUSE_OUTCOME_TEXT_MAX_LENGTH = 2000


def _operator_only_ids(
    unactionable: tuple[_clause_actionability.DemotedClause, ...],
) -> tuple[str, ...]:
    return tuple(
        d.row.req_id for d in unactionable
        if d.classification is _clause_actionability.ClauseActionability.OPERATOR_ONLY
    )


def _unactionable_ids(
    unactionable: tuple[_clause_actionability.DemotedClause, ...],
) -> tuple[str, ...]:
    return tuple(d.row.req_id for d in unactionable)


def _clause_outcome_from_rows(
    context: ClauseAuditContext,
    actionable: tuple[ClauseAuditRow, ...],
    unactionable: tuple[_clause_actionability.DemotedClause, ...],
    judge_output: str,
) -> ClauseOutcome | None:
    if not actionable and not unactionable:
        return None
    req_text_by_id = {req.id: req.text for req in context.requirements}
    clause_texts: dict[str, str] = {}
    evidence: dict[str, str] = {}
    for row in actionable:
        sub_texts = context.clause_texts.get(row.req_id, ())
        text = "; ".join(sub_texts) if sub_texts else req_text_by_id.get(row.req_id, "")
        clause_texts[row.req_id] = text[:_CLAUSE_OUTCOME_TEXT_MAX_LENGTH]
        evidence[row.req_id] = row.evidence[:_CLAUSE_OUTCOME_TEXT_MAX_LENGTH]
    return ClauseOutcome(
        task_id=context.task_id,
        actionable_ids=tuple(row.req_id for row in actionable),
        clause_texts=clause_texts,
        evidence=evidence,
        judge_rationale=judge_output[:2000],
        operator_only_ids=_operator_only_ids(unactionable),
        unactionable_ids=_unactionable_ids(unactionable),
    )


def _decision_readiness(
    context: ClauseAuditContext, failed_rows: tuple[ClauseAuditRow, ...],
) -> _pr_ready.PrReadiness | None:
    anchor = context.readiness
    if not isinstance(anchor, _pr_ready.PrReadiness) or not anchor.head_sha:
        return None
    if not _clause_actionability.cites_check(failed_rows):
        return None
    decision = _pr_ready.readiness_or_unavailable(
        context.pr_number, context.task_id, forge_target=context.forge_target,
    )
    return _clause_actionability.same_head(anchor, decision)


def _enforce_outcome(
    context: ClauseAuditContext,
    flipped: str,
    table: str,
    failed_rows: tuple[ClauseAuditRow, ...],
) -> ClauseAuditOutcome:
    parts = _clause_actionability.partition(
        failed_rows, _decision_readiness(context, failed_rows),
    )
    _audit_demoted_clauses(context, parts.unactionable)
    if _clause_actionability.stands_down(parts):
        note = _CLAUSE_STOOD_DOWN_NOTE_TEMPLATE.format(
            cause=_clause_actionability.cause_text(parts.unactionable)
        )
        return ClauseAuditOutcome(f"{note}{flipped}\n\n{table}", False, parts.unactionable)
    output = _rewrite_verdict_with_banner(flipped, _clause_verdict_banner(parts))
    clause_outcome = _clause_outcome_from_rows(
        context, parts.actionable, parts.unactionable, flipped
    )
    return ClauseAuditOutcome(
        f"{output}\n\n{table}", not parts.actionable, parts.unactionable, clause_outcome,
    )


def _advisory_ids(context: ClauseAuditContext) -> frozenset[str]:
    if context.floor_active:
        return frozenset()
    record_ids = frozenset(r.id for r in context.requirements if r.kind == "record")
    return record_ids - frozenset(r.id for r in context.requirements if r.kind != "record")


def _audit_advisory_record_failures(
    context: ClauseAuditContext,
    advisory: tuple[ClauseAuditRow, ...],
    judge_output: str,
) -> None:
    judging_verdict = _verdict.parse_verdict(judge_output)
    for row in advisory:
        _append_audit({
            "task_id": context.task_id,
            "action": "clause_record_advisory",
            "pr_number": context.pr_number,
            "req_id": row.req_id,
            "judging_verdict": judging_verdict,
        })


def _enforce_blocking_failures(
    context: ClauseAuditContext,
    judge_output: str,
    flipped: str,
    table: str,
    failed_rows: tuple[ClauseAuditRow, ...],
) -> ClauseAuditOutcome:
    advisory_ids = _advisory_ids(context)
    blocking = tuple(row for row in failed_rows if row.req_id not in advisory_ids)
    _audit_advisory_record_failures(
        context, tuple(row for row in failed_rows if row.req_id in advisory_ids), judge_output,
    )
    if not blocking:
        return ClauseAuditOutcome(f"{flipped}\n\n{table}", False)
    return _enforce_outcome(context, flipped, table, blocking)


def apply(judge_output: str) -> ClauseAuditOutcome:
    flipped = _verdict._maybe_flag_count_flip(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        judge_output
    )
    context = _clause_audit_var.get()
    if context is None or context.result is None:
        return ClauseAuditOutcome(flipped, False)
    failed_rows = tuple(row for row in context.result.rows if row.verdict == "FAIL")
    table = render_clause_table(context.result, _advisory_ids(context), context.floor_active)
    if context.mode != "enforce" or not failed_rows:
        return ClauseAuditOutcome(f"{flipped}\n\n{table}", False)
    return _enforce_blocking_failures(context, judge_output, flipped, table, failed_rows)


def append_route_audit_row(ws: Workspace) -> None:
    if ws.meta.roadmap_item_id is None:
        return
    if ws.meta.requirements:
        return
    _append_audit({
        "task_id": ws.task_id,
        "action": "clause_audit_route_unaudited",
        "roadmap_item_id": ws.meta.roadmap_item_id,
    })
