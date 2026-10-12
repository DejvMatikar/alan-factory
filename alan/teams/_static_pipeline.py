from __future__ import annotations

import contextvars
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, NamedTuple, TypeVar, cast

import yaml

from alan import workflow as workflow_mod
from alan.block_correction import CorrectionOutcome
from alan.claude_cli import OnProcStarted
from alan.claude_cli_grants import with_gate_reachability
from alan.handover import Decision, Handover, TestScenario
from alan.teams import _block_correction_fleet, _pr_forge
from alan.teams._base import (
    UNTRUSTED_CONTENT_NOTICE,
    EmitFn,
    WorkerOverrides,
    build_forward_handover,
    build_prior_context_user_text,
    build_terminal_handover,
    compose_system_prompt,
    render_allowed_tools,
    run_or_resume_node,
    workflow_lifecycle,
)
from alan.teams.role_output_fence import fence_prior_outputs
from alan.workflow import PendingTaskSpec, WorkflowGraph
from alan.workspace import Workspace

_T = TypeVar("_T")

FinalizeFn = Callable[[Workspace, str], None]
Stage = tuple[str, ...]


class Role(NamedTuple):
    name: str
    system_prompt: str
    model: str


class StaticPipelineParseError(RuntimeError):
    pass


_DECISIONS_BLOCK_RE = re.compile(
    r"```yaml\s+decisions\.yaml\s*\n(.*?)\n```",
    re.DOTALL | re.IGNORECASE,
)

# Decision's numeric/fixed-vocabulary fields and TestScenario's optional
# test_ref are structured, never free text — subtracting them (rather than
# listing the free-text keys literally) means a new free-text field on
# either model is picked up automatically instead of silently unrepaired.
_DECISION_NON_FREETEXT_FIELDS = frozenset({"confidence", "reversibility"})
_SCENARIO_NON_FREETEXT_FIELDS = frozenset({"test_ref"})

_FREETEXT_KEYS: frozenset[str] = (
    frozenset(Decision.model_fields) - _DECISION_NON_FREETEXT_FIELDS
) | (frozenset(TestScenario.model_fields) - _SCENARIO_NON_FREETEXT_FIELDS)

_FREETEXT_LINE_RE = re.compile(
    r"^(?P<prefix>\s*(?:-\s+)?)(?P<key>\w+): (?P<value>.*)$"
)
_OFFENDING_RE = re.compile(r": |:$|[{}]")
_ALREADY_SAFE_PREFIXES = ('"', "'", "|", ">", "[", "{")
_BLOCK_SCALAR_HEADER_RE = re.compile(r"^[|>][+-]?\d*$")


def _needs_quoting(key: str, value: str) -> bool:
    if key not in _FREETEXT_KEYS:
        return False
    stripped = value.strip()
    if not stripped or stripped.startswith(_ALREADY_SAFE_PREFIXES):
        return False
    return _OFFENDING_RE.search(stripped) is not None


def _requote(value: str) -> str:
    escaped = value.strip().replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _leading_spaces(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _is_block_scalar_header(value: str) -> bool:
    return _BLOCK_SCALAR_HEADER_RE.match(value.strip()) is not None


def _in_block_scalar_continuation(line: str, indent: int) -> bool:
    return line.strip() == "" or _leading_spaces(line) > indent


def _repair_line(
    line: str, block_scalar_indent: int | None
) -> tuple[str, int | None]:
    if block_scalar_indent is not None:
        if _in_block_scalar_continuation(line, block_scalar_indent):
            return line, block_scalar_indent
        block_scalar_indent = None
    match = _FREETEXT_LINE_RE.match(line)
    if match is None:
        return line, None
    if _is_block_scalar_header(match.group("value")):
        return line, len(match.group("prefix"))
    if not _needs_quoting(match.group("key"), match.group("value")):
        return line, None
    repaired = (
        f"{match.group('prefix')}{match.group('key')}: "
        f"{_requote(match.group('value'))}"
    )
    return repaired, None


def _repair_freetext_values(body: str) -> str:
    repaired_lines: list[str] = []
    block_scalar_indent: int | None = None
    for line in body.splitlines():
        repaired_line, block_scalar_indent = _repair_line(line, block_scalar_indent)
        repaired_lines.append(repaired_line)
    return "\n".join(repaired_lines)


def load_decisions_yaml_block(synthesis: str) -> dict[str, Any]:
    match = _DECISIONS_BLOCK_RE.search(synthesis)
    if match is None:
        raise StaticPipelineParseError(
            "pipeline output missing required ```yaml decisions.yaml block"
        )
    body = match.group(1)
    try:
        raw = yaml.safe_load(body)
    except yaml.YAMLError as exc:
        # Repair + retry runs ONLY here, after a first parse failure — never
        # on input that already parses. That ordering is what guarantees
        # repaired behavior adds zero regressions to currently-passing plans.
        try:
            raw = yaml.safe_load(_repair_freetext_values(body))
        except yaml.YAMLError:
            raise StaticPipelineParseError(
                f"decisions.yaml block is not valid YAML: {exc}"
            ) from exc
    if not isinstance(raw, dict):
        raise StaticPipelineParseError(
            "decisions.yaml block must be a mapping with a `decisions` key"
        )
    return cast(dict[str, Any], raw)


def decisions_block_needed_repair(synthesis: str) -> bool:
    match = _DECISIONS_BLOCK_RE.search(synthesis)
    if match is None:
        return False
    try:
        yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return True
    return False


def _build_decision(item: object, idx: int) -> Decision:
    if not isinstance(item, dict):
        raise StaticPipelineParseError(
            f"decisions[{idx}] must be a mapping, got {type(item).__name__}"
        )
    entry = cast(dict[str, Any], item)
    try:
        return Decision.model_validate(entry)
    except Exception as exc:
        raise StaticPipelineParseError(
            f"decisions[{idx}] failed validation: {exc}"
        ) from exc


def _build_files_to_edit(raw: object) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise StaticPipelineParseError(
            "files_to_edit must be a list of repo-relative path strings"
        )
    items = cast(list[Any], raw)  # type: ignore[redundant-cast]
    out: list[str] = []
    for idx, item in enumerate(items):
        if not isinstance(item, str) or not item.strip():
            raise StaticPipelineParseError(
                f"files_to_edit[{idx}] must be a non-empty string"
            )
        out.append(item.strip())
    return out


def parse_decisions_block(synthesis: str) -> tuple[list[Decision], list[str]]:
    payload = load_decisions_yaml_block(synthesis)
    raw_items = payload.get("decisions")
    if not isinstance(raw_items, list) or not raw_items:
        raise StaticPipelineParseError(
            "decisions.yaml block must list at least one decision"
        )
    items = cast(list[Any], raw_items)  # type: ignore[redundant-cast]
    decisions = [_build_decision(item, idx) for idx, item in enumerate(items)]
    files_to_edit = _build_files_to_edit(payload.get("files_to_edit"))
    return decisions, files_to_edit


def _default_stages(roles: tuple[Role, ...]) -> tuple[Stage, ...]:
    return tuple((role.name,) for role in roles)


def _validate_stages(
    name: str, roles: tuple[Role, ...], stages: tuple[Stage, ...]
) -> None:
    flat = [role_name for stage in stages for role_name in stage]
    expected = [role.name for role in roles]
    if flat != expected:
        raise ValueError(
            f"StaticPipelineTeam {name!r} stages must list every role exactly "
            f"once in role order; got {flat!r}, expected {expected!r}"
        )


def _depends_on_map(stages: tuple[Stage, ...]) -> dict[str, list[str]]:
    deps: dict[str, list[str]] = {}
    prev: Stage = ()
    for stage in stages:
        for role_name in stage:
            deps[role_name] = list(prev)
        prev = stage
    return deps


def _run_stage_roles(
    stage_roles: tuple[Role, ...],
    run_role: Callable[[Role], str],
) -> list[tuple[str, str]]:
    if len(stage_roles) == 1:
        role = stage_roles[0]
        return [(role.name, run_role(role))]
    # pool.__exit__ joins every future first, so a sibling exception never orphans another thread.
    with ThreadPoolExecutor(max_workers=len(stage_roles)) as pool:
        futures = [
            pool.submit(contextvars.copy_context().run, run_role, role)
            for role in stage_roles
        ]
    return [
        (role.name, future.result())
        for role, future in zip(stage_roles, futures, strict=True)
    ]


def _fail_node_after_parse_error(
    workspace: Workspace, inv_id: str, node_id: str, error_message: str
) -> None:
    workflow_mod.invalidate_node_result(workspace, inv_id, node_id)
    try:
        graph = workflow_mod.load_graph(workspace, inv_id)
        graph.fail_node(node_id, error=error_message)
        graph.finalize(status="failed")
        workflow_mod.save_workflow(workspace, graph.doc)
    except workflow_mod.WorkflowError:
        pass


def _correction_user_text(block_name: str, error: str) -> str:
    return (
        f"Your previous response's fenced ```yaml {block_name}``` block "
        f"failed to parse: {error}\n\n"
        "Re-emit your COMPLETE previous response, unchanged, except for a "
        f"corrected ```yaml {block_name}``` block that parses cleanly. Do "
        "not omit any other section."
    )


def _correction_exhausted_message(block_name: str, outcome: CorrectionOutcome[Any]) -> str:
    history = "; ".join(
        f"attempt {a.number}: {a.error}" for a in outcome.attempts if a.error is not None
    )
    return f"{block_name} block failed to parse after correction ({outcome.outcome}): {history}"


@dataclass(frozen=True)
class StaticPipelineTeam:
    name: str
    roles: tuple[Role, ...]
    to_team_suggestions: tuple[str, ...]
    finalize: FinalizeFn | None = None
    supports_resume: bool = True
    terminal: bool = False
    stages: tuple[Stage, ...] | None = None

    def __post_init__(self) -> None:
        if not self.roles:
            raise ValueError(
                f"StaticPipelineTeam {self.name!r} requires at least one role"
            )
        if self.stages is not None:
            _validate_stages(self.name, self.roles, self.stages)

    def _effective_stages(self) -> tuple[Stage, ...]:
        return self.stages if self.stages is not None else _default_stages(self.roles)

    def plan_workflow(
        self,
        workspace: Workspace,
        prior: Handover | None,
    ) -> list[PendingTaskSpec]:
        del workspace, prior
        deps = _depends_on_map(self._effective_stages())
        return [
            PendingTaskSpec(
                id=role.name,
                role=role.name,
                model=role.model,
                depends_on=deps[role.name],
            )
            for role in self.roles
        ]

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
        del resume_inv_id, task_id  # workflow.yaml is the source of truth for resume state
        last_output = self.run_pipeline(
            workspace,
            prior,
            emit,
            inv_id=inv_id,
            instructions=instructions,
            on_proc_started=on_proc_started,
        )
        if self.terminal:
            return build_terminal_handover(
                workspace,
                from_team=self.name,
                summary=last_output,
                prior=prior,
                invocation_id=inv_id,
            )
        return build_forward_handover(
            workspace,
            from_team=self.name,
            to_team_suggestions=list(self.to_team_suggestions),
            summary=last_output,
            prior=prior,
            invocation_id=inv_id,
        )

    def run_pipeline(
        self,
        workspace: Workspace,
        prior: Handover | None,
        emit: EmitFn,
        *,
        inv_id: str,
        instructions: str | None = None,
        on_proc_started: OnProcStarted | None = None,
    ) -> str:
        """Drive the role chain and return the LAST role's full output.

        Subclasses use this to access the untruncated synthesizer output
        when `build_forward_handover` would truncate it to 2500 chars
        (e.g. for fenced-block parsing or for posting full review bodies).
        """
        base = self._build_base_user_text(workspace, prior, instructions)
        last_output = self._collect_outputs(workspace, inv_id, base, emit, on_proc_started)
        if self.finalize is not None:
            self.finalize(workspace, last_output)
        return last_output

    def _build_base_user_text(
        self,
        workspace: Workspace,
        prior: Handover | None,
        instructions: str | None = None,
    ) -> str:
        return build_prior_context_user_text(self.name, workspace, prior, instructions)

    def _collect_outputs(
        self,
        workspace: Workspace,
        inv_id: str,
        base: str,
        emit: EmitFn,
        on_proc_started: OnProcStarted | None,
    ) -> str:
        role_by_name = {role.name: role for role in self.roles}
        depends_on_map = _depends_on_map(self._effective_stages())
        with workflow_lifecycle(
            workspace, team_name=self.name, inv_id=inv_id, emit=emit
        ) as graph:
            outputs: list[tuple[str, str]] = []
            for stage in self._effective_stages():
                stage_roles = tuple(role_by_name[name] for name in stage)

                def run_role(role: Role) -> str:
                    return self._run_role(
                        workspace, graph, inv_id, emit, role,
                        depends_on_map[role.name], base, outputs, on_proc_started,
                    )

                outputs.extend(_run_stage_roles(stage_roles, run_role))
        return outputs[-1][1]

    def _role_terse(self, role_name: str) -> bool:
        del role_name
        return True

    def _role_user_prefix(self, role_name: str) -> str:
        del role_name
        return ""

    def _role_system_prompt(self, role: Role, workspace: Workspace) -> str:
        del workspace
        return role.system_prompt

    def _role_overrides(
        self, role_name: str, workspace: Workspace
    ) -> WorkerOverrides | None:
        del role_name
        cfg = _pr_forge.governing_config_for_ws(workspace)
        return WorkerOverrides(
            allowed_tools=with_gate_reachability(
                render_allowed_tools(cfg, self.name), cfg.gate_commands
            )
        )

    def _run_role(
        self,
        workspace: Workspace,
        graph: WorkflowGraph,
        inv_id: str,
        emit: EmitFn,
        role: Role,
        depends_on: list[str],
        base: str,
        outputs: list[tuple[str, str]],
        on_proc_started: OnProcStarted | None,
    ) -> str:
        coverage = _coverage_block(role.name, self.roles)
        prefix = self._role_user_prefix(role.name)
        user_text = build_role_user_text(base, outputs, coverage)
        if prefix:
            user_text = prefix + user_text
        return run_or_resume_node(
            workspace,
            graph,
            inv_id,
            emit,
            node_id=role.name,
            role=role.name,
            model=role.model,
            depends_on=depends_on,
            system_prompt=assemble_role_system_prompt(
                self._role_system_prompt(role, workspace), terse=self._role_terse(role.name)
            ),
            user_text=user_text,
            overrides=self._role_overrides(role.name, workspace),
            on_proc_started=on_proc_started,
        )


    def _parse_or_invalidate(
        self,
        workspace: Workspace,
        inv_id: str,
        parse_fn: Callable[[str], _T],
        synthesis: str,
        *,
        block_name: str | None = None,
        emit: EmitFn | None = None,
        on_proc_started: OnProcStarted | None = None,
        detect_repair: Callable[[str], bool] | None = None,
    ) -> _T:
        node_id = self.roles[-1].name
        if block_name is None or emit is None:
            try:
                return parse_fn(synthesis)
            except StaticPipelineParseError as exc:
                _fail_node_after_parse_error(workspace, inv_id, node_id, str(exc))
                raise
        name = block_name
        re_ask = _block_correction_fleet.build_re_ask(
            workspace,
            inv_id,
            node_id,
            emit,
            system_prompt=assemble_role_system_prompt(
                self._role_system_prompt(self.roles[-1], workspace),
                terse=self._role_terse(node_id),
            ),
            overrides=self._role_overrides(node_id, workspace),
            on_proc_started=on_proc_started,
            correction_prompt=lambda error: _correction_user_text(name, error),
        )
        outcome = _block_correction_fleet.run_fleet_correction(
            synthesis,
            validate=parse_fn,
            re_ask=re_ask,
            site=name,
            role=node_id,
            task_id=workspace.task_id,
            detect_repair=detect_repair,
        )
        if outcome.value is not None:
            return outcome.value
        message = _correction_exhausted_message(name, outcome)
        _fail_node_after_parse_error(workspace, inv_id, node_id, message)
        raise StaticPipelineParseError(message)


def assemble_role_system_prompt(role_prompt: str, *, terse: bool = True) -> str:
    return compose_system_prompt(
        role_prompt,
        terse=terse,
        dynamic_block=UNTRUSTED_CONTENT_NOTICE,
    )


def build_role_user_text(
    base: str,
    prior: list[tuple[str, str]],
    coverage: str,
    *,
    nonce: str | None = None,
) -> str:
    parts = [base, "\n\n## COVERAGE\n\n", coverage]
    if prior:
        parts.append("\n\n## CONTEXT — prior agent outputs in this pipeline:")
        parts.append(fence_prior_outputs(prior, surrounding="".join(parts), nonce=nonce))
    return "".join(parts)


def _coverage_block(current: str, all_roles: tuple[Role, ...]) -> str:
    others = [r.name for r in all_roles if r.name != current]
    if not others:
        return f"You are the only role in this pipeline ({current})."
    return (
        f"Other roles in this pipeline: {', '.join(others)}. "
        f"Your scope is only {current} — do NOT duplicate work that another "
        f"role will cover."
    )


__all__ = [
    "FinalizeFn",
    "Role",
    "Stage",
    "StaticPipelineParseError",
    "StaticPipelineTeam",
    "assemble_role_system_prompt",
    "build_role_user_text",
    "decisions_block_needed_repair",
    "load_decisions_yaml_block",
    "parse_decisions_block",
]
