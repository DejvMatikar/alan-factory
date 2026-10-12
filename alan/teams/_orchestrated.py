from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from alan import workflow as workflow_mod
from alan.claude_cli import OnProcStarted, format_user_text
from alan.claude_cli_grants import with_gate_reachability
from alan.compression import DEFAULT_MAX_CHARS
from alan.engine_config import _DEFAULT as _ENGINE_DEFAULT  # pyright: ignore[reportPrivateUsage]
from alan.engine_config import EngineConfig
from alan.handover import ArtifactRef, Handover, OpenQuestion
from alan.teams import _native_capture
from alan.teams._base import (
    UNTRUSTED_CONTENT_NOTICE,
    EmitFn,
    WorkerOverrides,
    build_forward_handover,
    compose_system_prompt,
    format_dispatch_instructions,
    render_allowed_tools,
    render_base_tools,
    run_or_resume_node,
    workflow_lifecycle,
)
from alan.teams._env import float_env
from alan.teams._pr_forge import governing_config_for_ws
from alan.workflow import PendingTaskSpec, WorkflowGraph
from alan.workspace import Workspace

ORCHESTRATOR_ROLE = "orchestrator"
SYNTHESIZER_ROLE = "synthesizer"
VERIFIER_ROLE = "verifier"
_ROLE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_RESERVED_ROLES = frozenset({ORCHESTRATOR_ROLE, SYNTHESIZER_ROLE, VERIFIER_ROLE})
_REQUIRED_FIELDS: tuple[str, ...] = ("SCOPE", "COVERAGE", "SUCCESS", "CONTEXT")
_FIELD_LINE_RE = {
    field: re.compile(rf"(?m)^\s*{field}:\s*\S", re.MULTILINE)
    for field in _REQUIRED_FIELDS
}

FOUR_FIELD_REQUIREMENT = (
    "REQUIRED — every prompt in the dispatched agents list MUST include all four "
    "field markers, each on its own line, with substantive content:\n"
    "  SCOPE: One sentence stating the specific sub-question this agent answers — "
    "NOT the whole problem, one narrow angle.\n"
    '  COVERAGE: "Other agents this run: [role_A] covers [topic], [role_B] covers '
    "[topic]. Do NOT duplicate — your scope is only [Y].\"\n"
    "  SUCCESS: Concrete stopping criteria (e.g. \"compared at least 3 options with "
    "numbers\", \"identified the exact file:line\").\n"
    "  CONTEXT: Pasted prior findings, file paths, stack details, constraints — not "
    '"see above".\n'
    "Dispatches missing any of these four markers are rejected before the worker "
    "runs."
)

CONFIDENCE_REPORTING_BLOCK = (
    "CONFIDENCE REPORTING — end every response with this exact block:\n"
    "---\n"
    "AGENT_STATUS: complete|partial|failed\n"
    "CONFIDENCE: 0.0-1.0 (how sure are you the answer is correct and complete)\n"
    "COMPLETENESS: 0.0-1.0 (what fraction of your assigned scope did you cover)\n"
    'GAPS: [one line — what you could NOT answer and why, or "none"]\n'
    "---\n"
    "Be honest. Low confidence is useful signal. Fake high confidence wastes tokens."
)

_COMPLETENESS_RE = re.compile(r"(?im)^[ \t]*COMPLETENESS:[ \t]*([01](?:\.\d+)?)[ \t]*$")
_CONFIDENCE_RE = re.compile(r"(?im)^[ \t]*CONFIDENCE:[ \t]*([01](?:\.\d+)?)[ \t]*$")
_GAPS_HEADER_RE = re.compile(r"(?im)^[ \t]*GAPS:(.*)$")
_GAPS_ITEM_RE = re.compile(r"^[ \t]*-[ \t]+(\S.*)$")
_GAPS_MAX_ITEMS = 20
_GAPS_EXCERPT_MAX = 200
_COMPLETENESS_THRESHOLD_ENV = "ALAN_COMPLETENESS_THRESHOLD"
_DEFAULT_COMPLETENESS_THRESHOLD = 0.7
_LOW_COMPLETENESS_QUESTION_ID = "low_completeness"
_LOW_COMPLETENESS_MAX_GAP_OPTIONS = 2
_LOW_COMPLETENESS_RERUN_OPTION = (
    "Re-dispatch this team to re-run with expanded scope covering the "
    "reported gaps."
)
_LOW_COMPLETENESS_ACCEPT_OPTION = (
    "Accept the result at its current completeness and forward it anyway."
)

_log = logging.getLogger(__name__)


class InvocationError(RuntimeError):
    pass


class DispatchedAgent(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: str
    prompt: str


_JsonObjectAdapter: TypeAdapter[dict[str, Any]] = TypeAdapter(dict[str, Any])


@dataclass(frozen=True)
class VerifierMetrics:
    confidence: float | None
    completeness: float | None
    gaps: list[str]


@dataclass(frozen=True)
class PhaseOutcome:
    summary: str
    verifier_output: str | None


@dataclass(frozen=True)
class OrchestratedTeam:
    name: str
    orchestrator_prompt: str
    synthesizer_prompt: str
    worker_system_template: str
    to_team_suggestions: tuple[str, ...]
    orchestrator_model: str = _ENGINE_DEFAULT.roles["judge"].model
    worker_model: str = _ENGINE_DEFAULT.roles["diff_reviewer"].model
    synthesizer_model: str = _ENGINE_DEFAULT.roles["judge"].model
    supports_resume: bool = True
    verifier_prompt: str | None = None
    writes_report: bool = True

    def plan_workflow(
        self,
        workspace: Workspace,
        prior: Handover | None,
    ) -> list[PendingTaskSpec]:
        del workspace, prior
        return [
            PendingTaskSpec(
                id=ORCHESTRATOR_ROLE,
                role=ORCHESTRATOR_ROLE,
                model=self.orchestrator_model,
            )
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
        with workflow_lifecycle(
            workspace, team_name=self.name, inv_id=inv_id, emit=emit
        ) as graph:
            outcome = self._run_phases(
                workspace, prior, emit, inv_id, graph, instructions, on_proc_started
            )
        metrics = parse_verifier_metrics(outcome.verifier_output)
        return build_forward_handover(
            workspace,
            from_team=self.name,
            to_team_suggestions=list(self.to_team_suggestions),
            summary=outcome.summary,
            prior=prior,
            invocation_id=inv_id,
            open_questions=_open_questions_for_metrics(metrics),
            artifacts_produced=self._save_report(workspace, inv_id, outcome.summary),
            verifier_completeness=metrics.completeness if metrics is not None else None,
            verifier_confidence=metrics.confidence if metrics is not None else None,
            verifier_gaps=metrics.gaps if metrics is not None else None,
        )

    def _save_report(
        self, workspace: Workspace, inv_id: str, summary: str
    ) -> list[ArtifactRef] | None:
        if not self.writes_report:
            return None
        try:
            workflow_mod.save_invocation_report(workspace, inv_id, summary)
        except OSError:
            _log.warning(
                "failed to save invocation report for %s/%s",
                self.name,
                inv_id,
                exc_info=True,
            )
            return None
        return [
            ArtifactRef(
                path=f"invocations/{inv_id}/report.md",
                description=f"Full {self.name.replace('_', ' ')} report",
            )
        ]

    def _run_phases(
        self,
        workspace: Workspace,
        prior: Handover | None,
        emit: EmitFn,
        inv_id: str,
        graph: WorkflowGraph,
        instructions: str | None,
        on_proc_started: OnProcStarted | None,
    ) -> PhaseOutcome:
        dispatched = self._phase_orchestrate(
            workspace, prior, emit, inv_id, graph, instructions, on_proc_started
        )
        outputs = self._phase_workers(
            workspace, emit, inv_id, graph, dispatched, on_proc_started
        )
        verifier_output = self._phase_verify(
            workspace, emit, inv_id, graph, dispatched, outputs, on_proc_started
        )
        summary = self._phase_synthesize(
            workspace, emit, inv_id, graph, dispatched, outputs, verifier_output, on_proc_started
        )
        return PhaseOutcome(summary=summary, verifier_output=verifier_output)

    def _phase_orchestrate(
        self,
        workspace: Workspace,
        prior: Handover | None,
        emit: EmitFn,
        inv_id: str,
        graph: WorkflowGraph,
        instructions: str | None,
        on_proc_started: OnProcStarted | None,
    ) -> list[DispatchedAgent]:
        text = run_or_resume_node(
            workspace,
            graph,
            inv_id,
            emit,
            node_id=ORCHESTRATOR_ROLE,
            role=ORCHESTRATOR_ROLE,
            model=self.orchestrator_model,
            depends_on=[],
            system_prompt=self.orchestrator_prompt,
            user_text=_orchestrator_user_text(workspace, prior, instructions),
            overrides=_node_overrides(workspace),
            on_proc_started=on_proc_started,
        )
        try:
            dispatched = parse_dispatch(text)
        except InvocationError:
            # The orchestrator's output failed validation. By this point
            # `run_or_resume_node` has already marked the node `success`
            # and cached the output. If we leave both in place, the next
            # resume would replay the same broken cache forever. Roll the
            # node back to `failed` and delete the cache so a fresh
            # dispatch starts clean.
            workflow_mod.invalidate_node_result(workspace, inv_id, ORCHESTRATOR_ROLE)
            graph.fail_node(
                ORCHESTRATOR_ROLE,
                error="orchestrator output failed parse_dispatch",
            )
            workflow_mod.save_workflow(workspace, graph.doc)
            raise
        graph.set_dispatched(ORCHESTRATOR_ROLE, [a.role for a in dispatched])
        workflow_mod.save_workflow(workspace, graph.doc)
        return dispatched

    def _phase_workers(
        self,
        workspace: Workspace,
        emit: EmitFn,
        inv_id: str,
        graph: WorkflowGraph,
        dispatched: list[DispatchedAgent],
        on_proc_started: OnProcStarted | None,
    ) -> dict[str, str]:
        cfg = governing_config_for_ws(workspace)
        overrides = _worker_overrides(cfg, self.name)
        outputs: dict[str, str] = {}
        for agent in dispatched:
            outputs[agent.role] = run_or_resume_node(
                workspace,
                graph,
                inv_id,
                emit,
                node_id=agent.role,
                role=agent.role,
                model=self.worker_model,
                depends_on=[ORCHESTRATOR_ROLE],
                system_prompt=assemble_worker_prompt(
                    self.worker_system_template,
                    agent.role,
                    extra_block=_native_capture.native_capture_block(cfg, self.name),
                ),
                user_text=agent.prompt,
                overrides=overrides,
                on_proc_started=on_proc_started,
            )
        return outputs

    def _phase_verify(
        self,
        workspace: Workspace,
        emit: EmitFn,
        inv_id: str,
        graph: WorkflowGraph,
        dispatched: list[DispatchedAgent],
        outputs: dict[str, str],
        on_proc_started: OnProcStarted | None,
    ) -> str | None:
        verifier_prompt = self.verifier_prompt
        if verifier_prompt is None:
            return None
        return run_or_resume_node(
            workspace,
            graph,
            inv_id,
            emit,
            node_id=VERIFIER_ROLE,
            role=VERIFIER_ROLE,
            model=self.worker_model,
            depends_on=[a.role for a in dispatched],
            system_prompt=compose_system_prompt(
                verifier_prompt, dynamic_block=UNTRUSTED_CONTENT_NOTICE
            ),
            user_text=_peer_outputs_text(workspace, dispatched, outputs),
            overrides=_node_overrides(workspace),
            on_proc_started=on_proc_started,
        )

    def _phase_synthesize(
        self,
        workspace: Workspace,
        emit: EmitFn,
        inv_id: str,
        graph: WorkflowGraph,
        dispatched: list[DispatchedAgent],
        outputs: dict[str, str],
        verifier_output: str | None,
        on_proc_started: OnProcStarted | None,
    ) -> str:
        depends_on = [a.role for a in dispatched]
        if verifier_output is not None:
            depends_on = [*depends_on, VERIFIER_ROLE]
        return run_or_resume_node(
            workspace,
            graph,
            inv_id,
            emit,
            node_id=SYNTHESIZER_ROLE,
            role=SYNTHESIZER_ROLE,
            model=self.synthesizer_model,
            depends_on=depends_on,
            system_prompt=compose_system_prompt(
                self.synthesizer_prompt, dynamic_block=UNTRUSTED_CONTENT_NOTICE
            ),
            user_text=_synthesizer_user_text(workspace, dispatched, outputs, verifier_output),
            overrides=_node_overrides(workspace),
            on_proc_started=on_proc_started,
        )


def _worker_overrides(cfg: EngineConfig, team_name: str) -> WorkerOverrides:
    return WorkerOverrides(
        allowed_tools=with_gate_reachability(
            render_allowed_tools(cfg, team_name), cfg.gate_commands
        )
    )


def _node_overrides(workspace: Workspace) -> WorkerOverrides:
    cfg = governing_config_for_ws(workspace)
    return WorkerOverrides(
        allowed_tools=with_gate_reachability(render_base_tools(cfg), cfg.gate_commands)
    )


def assemble_worker_prompt(
    template: str, role: str, *, terse: bool = True, extra_block: str = ""
) -> str:
    base = template.format(role=role)
    dynamic_block = f"{UNTRUSTED_CONTENT_NOTICE}\n\n{CONFIDENCE_REPORTING_BLOCK}"
    if extra_block.strip():
        dynamic_block = f"{dynamic_block}\n\n{extra_block}"
    return compose_system_prompt(base, terse=terse, dynamic_block=dynamic_block)


def _orchestrator_user_text(
    workspace: Workspace,
    prior: Handover | None,
    instructions: str | None = None,
) -> str:
    base = format_user_text(workspace.meta.title, workspace.meta.description)
    base = base + format_dispatch_instructions(instructions)
    if prior is None:
        return base
    return (
        f"{base}\n\nPrior handover from {prior.from_team} "
        f"(iteration {prior.iteration}):\n{prior.full_synthesis[:DEFAULT_MAX_CHARS]}"
    )


def _peer_outputs_text(
    workspace: Workspace,
    dispatched: list[DispatchedAgent],
    outputs: dict[str, str],
) -> str:
    base = format_user_text(workspace.meta.title, workspace.meta.description)
    blocks = "\n\n".join(
        f"## {agent.role}\n\n{outputs.get(agent.role, '')}"
        for agent in dispatched
    )
    return f"{base}\n\n{blocks}"


def _synthesizer_user_text(
    workspace: Workspace,
    dispatched: list[DispatchedAgent],
    outputs: dict[str, str],
    verifier_output: str | None = None,
) -> str:
    peer = _peer_outputs_text(workspace, dispatched, outputs)
    if verifier_output is not None:
        return f"{peer}\n\n## {VERIFIER_ROLE}\n\n{verifier_output}"
    return peer


def parse_dispatch(text: str) -> list[DispatchedAgent]:
    try:
        data: object = json.loads(text.strip())
    except json.JSONDecodeError as exc:
        raise InvocationError(
            f"orchestrator output is not valid JSON: {text[:200]}"
        ) from exc
    try:
        data_dict = _JsonObjectAdapter.validate_python(data)
    except ValidationError:
        raise InvocationError("orchestrator output must be a JSON object") from None
    agents_raw = data_dict.get("agents")
    if not isinstance(agents_raw, list) or not agents_raw:
        raise InvocationError("orchestrator output missing non-empty 'agents'")
    agents_list: list[Any] = cast(list[Any], agents_raw)  # type: ignore[redundant-cast]
    agents = [_parse_agent(item) for item in agents_list]
    _ensure_unique_roles(agents)
    return agents


def _parse_agent(item: Any) -> DispatchedAgent:
    try:
        item_dict = _JsonObjectAdapter.validate_python(item)
    except ValidationError:
        raise InvocationError(f"agent entry must be object, got {type(item)}") from None
    role = _validate_agent_role(item_dict.get("role"))
    prompt = item_dict.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise InvocationError(f"agent entry '{role}' missing string 'prompt'")
    cleaned = prompt.strip()
    _validate_four_fields(role, cleaned)
    return DispatchedAgent(role=role, prompt=cleaned)


def _validate_four_fields(role: str, prompt: str) -> None:
    missing = [
        field for field in _REQUIRED_FIELDS if not _FIELD_LINE_RE[field].search(prompt)
    ]
    if missing:
        raise InvocationError(
            f"agent entry {role!r} missing required field(s): "
            f"{', '.join(f'{f}:' for f in missing)}"
        )


def _validate_agent_role(raw: Any) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise InvocationError("agent entry missing string 'role'")
    role = raw.strip()
    if not _ROLE_RE.fullmatch(role):
        raise InvocationError(
            f"agent role {role!r} must match [a-z][a-z0-9_]{{0,63}}"
        )
    if role in _RESERVED_ROLES:
        raise InvocationError(f"agent role {role!r} is reserved")
    return role


def _ensure_unique_roles(agents: list[DispatchedAgent]) -> None:
    seen: set[str] = set()
    for agent in agents:
        if agent.role in seen:
            raise InvocationError(f"duplicate agent role: {agent.role!r}")
        seen.add(agent.role)


def _last_clamped_float(regex: re.Pattern[str], text: str) -> float | None:
    matches = regex.findall(text)
    if not matches:
        return None
    return max(0.0, min(1.0, float(matches[-1])))


def _finalize_gaps(items: list[str]) -> list[str]:
    return [item[:_GAPS_EXCERPT_MAX] for item in items[:_GAPS_MAX_ITEMS]]


def _dash_items_after(text: str, header_end: int) -> list[str]:
    newline_idx = text.find("\n", header_end)
    remainder = "" if newline_idx == -1 else text[newline_idx + 1 :]
    items: list[str] = []
    for line in remainder.splitlines():
        match = _GAPS_ITEM_RE.match(line)
        if match is None:
            break
        items.append(match.group(1).strip())
    return items


def _extract_gaps(text: str) -> list[str]:
    matches = list(_GAPS_HEADER_RE.finditer(text))
    if not matches:
        return []
    last = matches[-1]
    dash_items = _dash_items_after(text, last.end())
    if dash_items:
        return _finalize_gaps(dash_items)
    inline = last.group(1).strip()
    if not inline or inline.lower() == "none":
        return []
    return _finalize_gaps([inline])


def parse_verifier_metrics(text: str | None) -> VerifierMetrics | None:
    if not text:
        return None
    completeness = _last_clamped_float(_COMPLETENESS_RE, text)
    confidence = _last_clamped_float(_CONFIDENCE_RE, text)
    gaps = _extract_gaps(text)
    if completeness is None and confidence is None and not gaps:
        return None
    return VerifierMetrics(confidence=confidence, completeness=completeness, gaps=gaps)


def _completeness_options(gaps: list[str]) -> list[str]:
    gap_options = [f"Address gap: {gap}" for gap in gaps[:_LOW_COMPLETENESS_MAX_GAP_OPTIONS]]
    return [*gap_options, _LOW_COMPLETENESS_RERUN_OPTION, _LOW_COMPLETENESS_ACCEPT_OPTION]


def _open_questions_for_metrics(
    metrics: VerifierMetrics | None,
) -> list[OpenQuestion]:
    if metrics is None or metrics.completeness is None:
        return []
    threshold = float_env(_COMPLETENESS_THRESHOLD_ENV, _DEFAULT_COMPLETENESS_THRESHOLD)
    if metrics.completeness >= threshold:
        return []
    gaps_text = "; ".join(metrics.gaps) if metrics.gaps else "none reported"
    question = (
        f"Verifier scored completeness {metrics.completeness:.2f} "
        f"(threshold {threshold:.2f}). Gaps: {gaps_text}"
    )
    options = _completeness_options(metrics.gaps)
    return [
        OpenQuestion(
            id=_LOW_COMPLETENESS_QUESTION_ID,
            question=question,
            proposed_answer_space=options,
            recommended=options[0],
            question_class="orchestrated_completeness",
            resolver_role="human",
            blocking=True,
        )
    ]


__all__ = [
    "CONFIDENCE_REPORTING_BLOCK",
    "FOUR_FIELD_REQUIREMENT",
    "ORCHESTRATOR_ROLE",
    "SYNTHESIZER_ROLE",
    "VERIFIER_ROLE",
    "DispatchedAgent",
    "InvocationError",
    "OrchestratedTeam",
    "PhaseOutcome",
    "VerifierMetrics",
    "assemble_worker_prompt",
    "parse_dispatch",
    "parse_verifier_metrics",
]
