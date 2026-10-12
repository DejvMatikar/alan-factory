from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, cast

import yaml

from alan.acceptance import live_clauses, resolve_owners
from alan.claude_cli import OnProcStarted
from alan.engine_config import _DEFAULT as _ENGINE_DEFAULT  # pyright: ignore[reportPrivateUsage]
from alan.handover import Decision, Handover, InjectedTask
from alan.roots import logs_root
from alan.teams._audit import append_jsonl_audit
from alan.teams._base import EmitFn, build_forward_handover, build_terminal_handover
from alan.teams._static_pipeline import (
    FinalizeFn,
    Role,
    StaticPipelineParseError,
    StaticPipelineTeam,
)
from alan.workspace import Requirement, TaskDoDClause, TestScenario, Workspace

_CLAUSE_OWNER_AUDIT_FILE = "clause_owner_audit.jsonl"

_MODEL_OPUS = _ENGINE_DEFAULT.roles["judge"].model
_ALLOWED_SUBTASK_TEAMS = frozenset({"implementation", "architecture", "research"})

DecomposerParseError = StaticPipelineParseError

_BLOCK_RE = re.compile(
    r"```yaml\s+decomposition\.yaml\s*\n(.*?)\n```",
    re.DOTALL | re.IGNORECASE,
)

_DECOMPOSER_PROMPT = (
    "You are the decomposer. An implementation_plan team has produced a plan "
    "(carried in the prior handover: its synthesis, decisions, and "
    "files_to_edit). Decide whether the task should ship as ONE piece (keep) "
    "or be split into a dependency-linked set of smaller subtasks (split), and "
    "emit a single fenced YAML block.\n\n"
    "Keep (split_count: 1) when ALL hold: the plan's files_to_edit count is "
    "<= 5; they sit in a single subsystem (one top-level dir under alan/, or "
    "alan/web, or docs/); and no step must strictly precede another. Bias "
    "strongly toward keep: split (split_count: N>=2) is the exception, and "
    "is justified ONLY when BOTH hold: the work cleanly factors into "
    "subtasks that share no files with each other, AND splitting has a "
    "clear, explicit benefit (e.g. independently reviewable PRs, parallel "
    "work across subsystems, or a plan too large to review as one PR). If "
    "either condition fails - the subtasks would still need to touch "
    "overlapping files, or the benefit of splitting is not clear - keep the "
    "task whole; do not split. Only when both split conditions above are "
    "satisfied, split (split_count: N>=2) into subtasks that each ship as "
    "their own PR. Prefer 2-3 subtasks; never more than 4. A purely "
    "sequential chain is NOT itself a reason to split — keep sequential work "
    "whole unless the single PR would be unreviewably large. "
    "List subtasks in execution order: producers before consumers. Each subtask "
    "names the files it edits and declares depends_on, the ids of earlier "
    "subtasks it depends on. Declare a dependency whenever this subtask consumes "
    "something another subtask produces - an API field, an event or SSE "
    "contract, a data file or row, a schema, or a config key - EVEN IF the two "
    "subtasks edit different files. Dependency edges are the union of these "
    "declared edges and edges derived automatically from shared files, so a "
    "subtask is always ordered after any subtask it shares a file with or "
    "declares a dependency on. Truly-independent subtasks declare depends_on: [] "
    "and share no files. Give each subtask a definition-of-done: one or more "
    "clauses, each referencing the acceptance-clause ids it covers (listed "
    "below) when applicable. A requirement id must appear in exactly one "
    "subtask's `dod.covers` — never split the same requirement across two "
    "subtasks' clauses. Give each subtask a short human-readable title "
    "(3-8 words) that describes what it delivers. When parent test scenarios "
    "are listed below, assign EVERY parent scenario id to exactly one "
    "subtask's `scenarios:` list (copy ids verbatim); a scenario left "
    "unassigned runs against every subtask. Assign a scenario or a DoD "
    "clause to a subtask ONLY if it can be made to pass by editing that "
    "subtask's own files: an end-to-end assertion that spans several "
    "subtasks' files (a whole-page layout-stability check, a cross-component "
    "flow) belongs to the LAST subtask in the dependency chain, and earlier "
    "subtasks assert only what their own files control — a subtask fenced "
    "off from the files a scenario depends on can only flake against it.\n\n"
    "Emit ONLY this block (no preamble, no other prose):\n\n"
    "```yaml decomposition.yaml\n"
    "split_count: 2\n"
    'split_rationale: "why it was kept whole, or why and how it was split"\n'
    "subtasks:            # omit entirely when split_count: 1\n"
    "  - id: backend\n"
    '    title: "Add X API endpoint"\n'
    "    team: implementation\n"
    '    instructions: "what this subtask must implement"\n'
    "    depends_on: []\n"
    "    files: [alan/x.py, tests/test_x.py]\n"
    "    scenarios: [s1]\n"
    "    dod:\n"
    '      - text: "the X endpoint returns 200"\n'
    "        covers: [req-abc123def456]\n"
    "  - id: frontend\n"
    '    title: "Wire X into the UI"\n'
    "    team: implementation\n"
    '    instructions: "what this subtask must implement"\n'
    "    depends_on: [backend]\n"
    "    files: [alan/web/src/X.tsx]\n"
    "    scenarios: [s2]\n"
    "    dod:\n"
    '      - text: "X renders the result"\n'
    "        covers: []\n"
    "```\n"
    "Quote every string value. Use `team: implementation` for code subtasks. "
    "Each subtask's `title` must be a short (3-7 word) human-readable label "
    "describing what that subtask delivers — it appears as a section heading in "
    "the workspace UI."
)


ROLES: tuple[Role, ...] = (Role("decomposer", _DECOMPOSER_PROMPT, _MODEL_OPUS),)


@dataclass(frozen=True)
class _SubtaskSpec:
    id: str
    team: str
    instructions: str | None
    files: list[str]
    depends_on: list[str]
    dod: list[TaskDoDClause]
    title: str | None = None
    scenarios: list[str] = field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    scenarios_waived: bool = False


@dataclass(frozen=True)
class ClauseOwnerRepair:
    req_id: str
    losing_subtask_id: str
    owning_subtask_id: str


@dataclass(frozen=True)
class Decomposition:
    split: bool
    rationale: str
    subtasks: list[InjectedTask]
    repairs: tuple[ClauseOwnerRepair, ...] = ()


def _load_block(text: str) -> dict[str, Any]:
    match = _BLOCK_RE.search(text)
    if match is None:
        raise DecomposerParseError(
            "decomposer output missing ```yaml decomposition.yaml block"
        )
    try:
        raw = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise DecomposerParseError(
            f"decomposition.yaml is not valid YAML: {exc}"
        ) from exc
    if not isinstance(raw, dict):
        raise DecomposerParseError("decomposition.yaml must be a mapping")
    return cast(dict[str, Any], raw)


def _require_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise DecomposerParseError(
            f"decomposition.yaml {key} must be a non-empty string"
        )
    return value.strip()


def _optional_str(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _str_list(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise DecomposerParseError("expected a list of strings")
    items = cast(list[Any], value)  # type: ignore[redundant-cast]
    out: list[str] = []
    for item in items:
        if not isinstance(item, str) or not item.strip():
            raise DecomposerParseError("list entries must be non-empty strings")
        out.append(item.strip())
    return out


def _one_dod(entry: object, idx: int) -> TaskDoDClause:
    if not isinstance(entry, dict):
        raise DecomposerParseError(f"dod[{idx}] must be a mapping")
    data = cast(dict[str, Any], entry)
    text = data.get("text")
    if not isinstance(text, str) or not text.strip():
        raise DecomposerParseError(f"dod[{idx}].text must be a non-empty string")
    return TaskDoDClause(text=text.strip(), covers=_str_list(data.get("covers")))


def _dod_clauses(value: object) -> list[TaskDoDClause]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise DecomposerParseError("dod must be a list")
    items = cast(list[Any], value)  # type: ignore[redundant-cast]
    return [_one_dod(entry, idx) for idx, entry in enumerate(items)]


def _parse_subtask(entry: object, idx: int) -> _SubtaskSpec:
    if not isinstance(entry, dict):
        raise DecomposerParseError(f"subtasks[{idx}] must be a mapping")
    data = cast(dict[str, Any], entry)
    team = _require_str(data, "team")
    if team not in _ALLOWED_SUBTASK_TEAMS:
        raise DecomposerParseError(
            f"subtasks[{idx}].team {team!r} must be one of "
            f"{sorted(_ALLOWED_SUBTASK_TEAMS)}"
        )
    scenarios_raw = data.get("scenarios")
    scenarios_waived = data.get("scenarios_waived", False)
    if not isinstance(scenarios_waived, bool):
        raise DecomposerParseError(
            f"subtasks[{idx}].scenarios_waived must be a boolean"
        )
    return _SubtaskSpec(
        id=_require_str(data, "id"),
        team=team,
        title=_optional_str(data.get("title")),
        instructions=_optional_str(data.get("instructions")),
        files=_str_list(data.get("files")),
        depends_on=_str_list(data.get("depends_on")),
        dod=_dod_clauses(data.get("dod")),
        scenarios=_str_list(scenarios_raw),
        scenarios_waived=scenarios_waived,
    )


def _derive_dependencies(file_lists: list[list[str]]) -> list[list[int]]:
    result: list[list[int]] = []
    for j, files in enumerate(file_lists):
        current = set(files)
        result.append([i for i in range(j) if current & set(file_lists[i])])
    return result


def _union_deps(
    spec: _SubtaskSpec,
    derived_ids: list[str],
    id_to_index: dict[str, int],
    self_index: int,
) -> list[str]:
    ordered: list[str] = list(derived_ids)
    seen = set(derived_ids)
    for dep in spec.depends_on:
        idx = id_to_index.get(dep)
        if idx is None or idx >= self_index or dep in seen:
            continue
        seen.add(dep)
        ordered.append(dep)
    return ordered


def _to_injected(specs: list[_SubtaskSpec]) -> list[InjectedTask]:
    deps = _derive_dependencies([s.files for s in specs])
    id_to_index = {spec.id: idx for idx, spec in enumerate(specs)}
    return [
        InjectedTask(
            id=spec.id,
            team=spec.team,
            title=spec.title,
            instructions=spec.instructions,
            depends_on=_union_deps(
                spec, [specs[j].id for j in deps[idx]], id_to_index, idx
            ),
            dod_clauses=spec.dod,
            scenarios=spec.scenarios,
            scenarios_waived=spec.scenarios_waived,
            files=spec.files,
        )
        for idx, spec in enumerate(specs)
    ]


def _validate_scenario_assignment(
    specs: list[_SubtaskSpec],
    parent_scenarios: list[TestScenario] | None,
) -> None:
    known_ids: set[str] = {s.id for s in parent_scenarios} if parent_scenarios else set()
    for idx, spec in enumerate(specs):
        for sid in spec.scenarios:
            if sid not in known_ids:
                raise DecomposerParseError(
                    f"subtasks[{idx}].scenarios references unknown scenario id {sid!r}"
                )


def _iter_clause_covers(specs: list[_SubtaskSpec]) -> list[tuple[int, str]]:
    return [
        (idx, rid)
        for idx, spec in enumerate(specs)
        for clause in spec.dod
        for rid in clause.covers
    ]


def _validate_clause_coverage(
    specs: list[_SubtaskSpec],
    parent_requirements: list[Requirement] | None,
) -> None:
    known_ids: set[str] = (
        {r.id for r in live_clauses(parent_requirements)} if parent_requirements else set()
    )
    for idx, rid in _iter_clause_covers(specs):
        if rid not in known_ids:
            raise DecomposerParseError(
                f"subtasks[{idx}].dod covers unknown requirement id {rid!r}"
            )


def _repair_clause(
    clause: TaskDoDClause,
    spec_id: str,
    owners: dict[str, str],
    repairs: list[ClauseOwnerRepair],
) -> TaskDoDClause:
    kept: list[str] = []
    for rid in clause.covers:
        if owners.get(rid) == spec_id:
            kept.append(rid)
        else:
            repairs.append(
                ClauseOwnerRepair(
                    req_id=rid, losing_subtask_id=spec_id, owning_subtask_id=owners[rid]
                )
            )
    if kept == clause.covers:
        return clause
    return clause.model_copy(update={"covers": kept})


def _repair_spec(
    spec: _SubtaskSpec, owners: dict[str, str], repairs: list[ClauseOwnerRepair]
) -> _SubtaskSpec:
    new_dod = [_repair_clause(c, spec.id, owners, repairs) for c in spec.dod]
    if new_dod == spec.dod:
        return spec
    return _SubtaskSpec(
        id=spec.id,
        team=spec.team,
        instructions=spec.instructions,
        files=spec.files,
        depends_on=spec.depends_on,
        dod=new_dod,
        title=spec.title,
        scenarios=spec.scenarios,
        scenarios_waived=spec.scenarios_waived,
    )


def _reconcile_clause_owners(
    specs: list[_SubtaskSpec],
) -> tuple[list[_SubtaskSpec], list[ClauseOwnerRepair]]:
    owners = resolve_owners(
        [(spec.id, {cid for c in spec.dod for cid in c.covers}) for spec in specs]
    )
    repairs: list[ClauseOwnerRepair] = []
    repaired_specs = [_repair_spec(spec, owners, repairs) for spec in specs]
    return repaired_specs, repairs


def _write_clause_repairs(task_id: str, repairs: tuple[ClauseOwnerRepair, ...]) -> None:
    for repair in repairs:
        append_jsonl_audit(
            logs_root() / _CLAUSE_OWNER_AUDIT_FILE,
            {
                "task_id": task_id,
                "action": "clause_owner_repaired",
                "req_id": repair.req_id,
                "losing_subtask_id": repair.losing_subtask_id,
                "owning_subtask_id": repair.owning_subtask_id,
            },
            log_label="decomposer",
        )


def _format_files_to_edit_context(files: list[str]) -> str:
    if not files:
        return ""
    bullets = "\n".join(f"- {path}" for path in files)
    return f"## Files to edit (from plan)\n{bullets}"


def _format_plan_decisions_context(decisions: list[Decision]) -> str:
    if not decisions:
        return ""
    bullets = "\n".join(
        f"- {d.statement.strip()} (because: {d.rationale.strip()})"
        for d in decisions
    )
    return f"## Plan decisions\n{bullets}"


def _format_scenarios_context(scenarios: list[TestScenario]) -> str:
    if not scenarios:
        return ""
    header = "## Parent test scenarios — assign by id to each subtask's `scenarios:` list"
    rows = "\n".join(f"- {s.id}: {s.description}" for s in scenarios)
    return f"{header}\n{rows}"


def _parse_subtasks(
    value: object,
    expected: int,
    parent_scenarios: list[TestScenario] | None = None,
    parent_requirements: list[Requirement] | None = None,
) -> tuple[list[InjectedTask], list[ClauseOwnerRepair]]:
    if not isinstance(value, list):
        raise DecomposerParseError(
            "decomposition.yaml subtasks must be a list when split"
        )
    items = cast(list[Any], value)  # type: ignore[redundant-cast]
    if len(items) != expected:
        raise DecomposerParseError(
            "decomposition.yaml subtasks length must equal split_count"
        )
    specs = [_parse_subtask(entry, idx) for idx, entry in enumerate(items)]
    ids = [spec.id for spec in specs]
    if len(set(ids)) != len(ids):
        raise DecomposerParseError("decomposition.yaml subtask ids must be unique")
    _validate_scenario_assignment(specs, parent_scenarios)
    _validate_clause_coverage(specs, parent_requirements)
    repaired, repairs = _reconcile_clause_owners(specs)
    return _to_injected(repaired), repairs


def parse_decomposition(
    text: str,
    parent_scenarios: list[TestScenario] | None = None,
    parent_requirements: list[Requirement] | None = None,
) -> Decomposition:
    payload = _load_block(text)
    rationale = _require_str(payload, "split_rationale")
    count = payload.get("split_count")
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise DecomposerParseError(
            "decomposition.yaml split_count must be an integer >= 1"
        )
    if count < 2:
        return Decomposition(split=False, rationale=rationale, subtasks=[])
    subtasks, repairs = _parse_subtasks(
        payload.get("subtasks"), count, parent_scenarios, parent_requirements
    )
    return Decomposition(
        split=True, rationale=rationale, subtasks=subtasks, repairs=tuple(repairs)
    )


def _format_clause_context(requirements: list[Requirement] | None) -> str:
    live = live_clauses(requirements) if requirements else []
    if not live:
        return ""
    lines = "\n".join(f"- {r.id}: {r.text}" for r in live)
    return (
        "\n\n## Acceptance clauses (reference these ids in each subtask dod.covers)"
        f"\n{lines}"
    )


def _build_keep_handover(
    workspace: Workspace,
    prior: Handover | None,
    inv_id: str,
    summary: str,
    plan: Decomposition,
) -> Handover:
    handover = build_forward_handover(
        workspace,
        from_team="decomposer",
        to_team_suggestions=["implementation"],
        summary=summary,
        prior=prior,
        invocation_id=inv_id,
    )
    return handover.model_copy(
        update={
            "decisions": list(prior.decisions) if prior is not None else [],
            "files_to_edit": list(prior.files_to_edit) if prior is not None else [],
            "next_hop": "implementation",
            "split_rationale": plan.rationale,
            "plan_synthesis": prior.full_synthesis if prior is not None else None,
        }
    )


def _build_split_handover(
    workspace: Workspace,
    prior: Handover | None,
    inv_id: str,
    summary: str,
    plan: Decomposition,
) -> Handover:
    handover = build_terminal_handover(
        workspace,
        from_team="decomposer",
        summary=summary,
        prior=prior,
        invocation_id=inv_id,
    )
    update: dict[str, Any] = {
        "injected_tasks": plan.subtasks,
        "split_rationale": plan.rationale,
    }
    if prior is not None:
        update["decisions"] = list(prior.decisions)
        update["files_to_edit"] = list(prior.files_to_edit)
        update["test_scenarios"] = list(prior.test_scenarios)
    return handover.model_copy(update=update)


@dataclass(frozen=True)
class DecomposerTeam(StaticPipelineTeam):
    name: str = "decomposer"
    roles: tuple[Role, ...] = ROLES
    to_team_suggestions: tuple[str, ...] = field(
        default=("implementation",), init=False, repr=False
    )
    finalize: FinalizeFn | None = None

    def _build_base_user_text(
        self,
        workspace: Workspace,
        prior: Handover | None,
        instructions: str | None = None,
    ) -> str:
        base = super()._build_base_user_text(workspace, prior, instructions)
        files_ctx = _format_files_to_edit_context(
            prior.files_to_edit if prior is not None else []
        )
        decisions_ctx = _format_plan_decisions_context(
            prior.decisions if prior is not None else []
        )
        scenarios_ctx = _format_scenarios_context(
            prior.test_scenarios if prior is not None else []
        )
        clause_ctx = _format_clause_context(workspace.meta.requirements)
        suffix = "\n\n".join(
            p for p in [files_ctx, decisions_ctx, scenarios_ctx, clause_ctx] if p
        )
        if not suffix:
            return base
        return f"{base}\n\n{suffix}"

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
        del resume_inv_id, task_id
        last_output = self.run_pipeline(
            workspace,
            prior,
            emit,
            inv_id=inv_id,
            instructions=instructions,
            on_proc_started=on_proc_started,
        )
        parent_sc = prior.test_scenarios if prior is not None else None
        parent_reqs = workspace.meta.requirements

        def _parse_decomp(text: str) -> Decomposition:
            return parse_decomposition(
                text, parent_scenarios=parent_sc, parent_requirements=parent_reqs
            )

        plan = self._parse_or_invalidate(
            workspace,
            inv_id,
            _parse_decomp,
            last_output,
            block_name="decomposition.yaml",
            emit=emit,
            on_proc_started=on_proc_started,
        )
        if plan.repairs:
            _write_clause_repairs(workspace.task_id, plan.repairs)
        if plan.split:
            return _build_split_handover(workspace, prior, inv_id, last_output, plan)
        return _build_keep_handover(workspace, prior, inv_id, last_output, plan)


TEAM = DecomposerTeam()
