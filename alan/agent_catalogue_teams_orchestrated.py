from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import partial
from typing import cast

from alan import agent_key
from alan.agent_catalogue_helpers import (
    ResolvedAgent,
    ResolverGroup,
    ValueSource,
    runtime_slot,
)
from alan.agent_catalogue_models import FlagOut
from alan.agent_catalogue_node import NodeOptions, team_node
from alan.claude_cli_grants import with_gate_reachability
from alan.engine_config import EngineConfig, canonical_scope_key
from alan.teams import _native_capture, brainstorming, opportunity_scout, research, ui_review
from alan.teams._base import (
    UNTRUSTED_CONTENT_NOTICE,
    WorkerOverrides,
    compose_system_prompt,
    render_allowed_tools,
    render_base_tools,
)
from alan.teams._orchestrated import (
    ORCHESTRATOR_ROLE,
    SYNTHESIZER_ROLE,
    VERIFIER_ROLE,
    DispatchedAgent,
    OrchestratedTeam,
    _orchestrator_user_text,  # pyright: ignore[reportPrivateUsage]
    _peer_outputs_text,  # pyright: ignore[reportPrivateUsage]
    _synthesizer_user_text,  # pyright: ignore[reportPrivateUsage]
    _worker_overrides,  # pyright: ignore[reportPrivateUsage]
    assemble_worker_prompt,
)
from alan.workspace import Workspace

Builder = Callable[[OrchestratedTeam, EngineConfig], ResolvedAgent]

JUDGE_ENGINE_ROLE = "judge"
DIFF_REVIEWER_ENGINE_ROLE = "diff_reviewer"
NATIVE_CAPTURE_LABEL = "native_capture_block"
SOURCE_FETCH_COMMAND = "python3 -m alan.source_fetch"
SOURCE_FETCH_GRANTS = frozenset({f"Bash({SOURCE_FETCH_COMMAND}:*)", "Bash(python3:*)"})

WORKING_DIRECTORY = (
    "Not set: the agent starts in the alan service's own working directory, which is "
    "alan's checkout, not a per-repository checkout."
)

EFFORT_SOURCE = ValueSource(
    "builtin",
    "Not set by alan: derived from the model by alan's model-to-effort rule, never read "
    "from the engine's role settings.",
)


@dataclass(frozen=True)
class RuntimeSlot:
    name: str
    description: str

    def text(self) -> str:
        return runtime_slot(self.name, self.description)


TASK_TITLE = RuntimeSlot("TASK_TITLE", "Title of the workspace task the user entered")
TASK_DESCRIPTION = RuntimeSlot(
    "TASK_DESCRIPTION", "Description of the workspace task the user entered"
)
OPTIONAL_DISPATCH_CONTEXT = RuntimeSlot(
    "OPTIONAL_DISPATCH_CONTEXT",
    "Present only when applicable, otherwise nothing: a '## User dispatch instructions' "
    "section holding instructions the user gave when dispatching, then a 'Prior handover "
    "from <team> (iteration <n>):' section holding the previous team's synthesis, "
    "truncated to a fixed size",
)
WORKER_ROLE_NAME = RuntimeSlot(
    "WORKER_ROLE_NAME",
    "Role name the orchestrator chose for this worker, a snake_case identifier",
)
DISPATCHED_PROMPT = RuntimeSlot(
    "DISPATCHED_PROMPT",
    "The prompt the orchestrator wrote for this worker; it must carry the SCOPE, "
    "COVERAGE, SUCCESS and CONTEXT fields",
)
WORKER_OUTPUT = RuntimeSlot(
    "WORKER_OUTPUT",
    "A dispatched worker's full output; this heading-and-output block repeats once for "
    "each dispatched worker, in dispatch order",
)
VERIFIER_OUTPUT = RuntimeSlot(
    "VERIFIER_OUTPUT", "The verifier's full output, as written by the verifier"
)

_TEAMS: tuple[OrchestratedTeam, ...] = (
    research.TEAM,
    brainstorming.TEAM,
    opportunity_scout.TEAM,
    ui_review.TEAM,
)

_TEAM_BLOCKS: dict[str, tuple[tuple[str, str], ...]] = {
    "research": (
        ("coverage_union_rule", research._COVERAGE_UNION_RULE),  # pyright: ignore[reportPrivateUsage]
    ),
    "opportunity_scout": (
        ("coverage_union_rule", opportunity_scout._COVERAGE_UNION_RULE),  # pyright: ignore[reportPrivateUsage]
        ("four_filter_rules", opportunity_scout._FILTER_RULES),  # pyright: ignore[reportPrivateUsage]
        ("ui_debt_lens", opportunity_scout._UI_DEBT_LENS),  # pyright: ignore[reportPrivateUsage]
    ),
    "ui_review": (
        ("ui_review_scope_statement", ui_review._SCOPE_STATEMENT),  # pyright: ignore[reportPrivateUsage]
    ),
}


@dataclass(frozen=True)
class _TaskMeta:
    title: str
    description: str


@dataclass(frozen=True)
class _TaskWorkspace:
    meta: _TaskMeta


def _task_workspace() -> Workspace:
    meta = _TaskMeta(TASK_TITLE.text(), TASK_DESCRIPTION.text())
    return cast(Workspace, _TaskWorkspace(meta))


def _peer_inputs() -> tuple[list[DispatchedAgent], dict[str, str]]:
    role = WORKER_ROLE_NAME.text()
    return [DispatchedAgent(role=role, prompt="")], {role: WORKER_OUTPUT.text()}


def orchestrator_message() -> str:
    base = _orchestrator_user_text(_task_workspace(), None, None)
    return f"{base}{OPTIONAL_DISPATCH_CONTEXT.text()}"


def verifier_message() -> str:
    dispatched, outputs = _peer_inputs()
    return _peer_outputs_text(_task_workspace(), dispatched, outputs)


def synthesizer_message(team: OrchestratedTeam) -> str:
    dispatched, outputs = _peer_inputs()
    verifier_output = VERIFIER_OUTPUT.text() if team.verifier_prompt is not None else None
    return _synthesizer_user_text(_task_workspace(), dispatched, outputs, verifier_output)


def _model_source(field: str, engine_role: str) -> ValueSource:
    return ValueSource(
        "builtin",
        f"Team constant '{field}', copied from the default engine's '{engine_role}' role "
        "when alan starts. Engine role settings are not read when the agent launches.",
    )


def _checkout_flags(cfg: EngineConfig) -> tuple[FlagOut, ...]:
    if cfg.repo == canonical_scope_key(None):
        return ()
    slug = cfg.repo.slug
    return (
        FlagOut(
            code="other_mismatch",
            severity="warn",
            title="Runs in alan's own checkout",
            detail=(
                f"This engine is for {slug}, but orchestrated team agents are started with "
                f"no working directory, so they run in the alan service's own checkout and "
                f"read alan's files, not {slug}'s."
            ),
        ),
    )


def _source_fetch_flags(team: OrchestratedTeam, tools: Sequence[str]) -> tuple[FlagOut, ...]:
    if SOURCE_FETCH_COMMAND not in team.worker_system_template:
        return ()
    if SOURCE_FETCH_GRANTS.intersection(tools):
        return ()
    return (
        FlagOut(
            code="other_mismatch",
            severity="warn",
            title="Worker prompt names a tool the grants do not include",
            detail=(
                f"The {team.name} worker prompt tells the agent to run "
                f"`{SOURCE_FETCH_COMMAND}`, but the tools granted to it do not include "
                "that command, so it cannot run it."
            ),
        ),
    )


def _agent(
    team: OrchestratedTeam,
    cfg: EngineConfig,
    role: str,
    *,
    system_prompt: str,
    user_message: str,
    model: str,
    model_field: str,
    engine_role: str,
    when_runs: str,
    stored_tools: Sequence[str],
    overrides: WorkerOverrides,
    extra_blocks: tuple[tuple[str, str], ...] = (),
    extra_flags: tuple[FlagOut, ...] = (),
) -> ResolvedAgent:
    return team_node(
        team=team.name,
        role=role,
        system_prompt=system_prompt,
        user_message=user_message,
        model=model,
        effort=None,
        model_source=_model_source(model_field, engine_role),
        effort_source=EFFORT_SOURCE,
        when_runs=when_runs,
        working_directory=WORKING_DIRECTORY,
        overrides=overrides,
        options=NodeOptions(
            stored_tools=stored_tools,
            scope="repo",
            engine_role_hint=engine_role,
            extra_flags=(*_checkout_flags(cfg), *extra_flags),
            extra_shared_blocks=(*_TEAM_BLOCKS.get(team.name, ()), *extra_blocks),
        ),
    )


def _node_agent(
    team: OrchestratedTeam,
    cfg: EngineConfig,
    role: str,
    *,
    system_prompt: str,
    user_message: str,
    model: str,
    model_field: str,
    engine_role: str,
    when_runs: str,
) -> ResolvedAgent:
    stored = render_base_tools(cfg)
    overrides = WorkerOverrides(allowed_tools=with_gate_reachability(stored, cfg.gate_commands))
    return _agent(
        team,
        cfg,
        role,
        system_prompt=system_prompt,
        user_message=user_message,
        model=model,
        model_field=model_field,
        engine_role=engine_role,
        when_runs=when_runs,
        stored_tools=stored,
        overrides=overrides,
    )


def _orchestrator(team: OrchestratedTeam, cfg: EngineConfig) -> ResolvedAgent:
    return _node_agent(
        team,
        cfg,
        ORCHESTRATOR_ROLE,
        system_prompt=team.orchestrator_prompt,
        user_message=orchestrator_message(),
        model=team.orchestrator_model,
        model_field="orchestrator_model",
        engine_role=JUDGE_ENGINE_ROLE,
        when_runs=(
            f"First phase of every {team.name} run: reads the task and any prior handover "
            "and answers with a single JSON object choosing the worker roles and writing "
            "each worker's prompt."
        ),
    )


def _worker(team: OrchestratedTeam, cfg: EngineConfig) -> ResolvedAgent:
    native_block = _native_capture.native_capture_block(cfg, team.name)
    overrides = _worker_overrides(cfg, team.name)
    return _agent(
        team,
        cfg,
        agent_key.WORKER_ROLE,
        system_prompt=assemble_worker_prompt(
            team.worker_system_template, WORKER_ROLE_NAME.text(), extra_block=native_block
        ),
        user_message=DISPATCHED_PROMPT.text(),
        model=team.worker_model,
        model_field="worker_model",
        engine_role=DIFF_REVIEWER_ENGINE_ROLE,
        when_runs=(
            f"Second phase of a {team.name} run: one node per role the orchestrator "
            "dispatched, started one after another. The role names are chosen by the "
            "orchestrator at run time, so this entry stands for every such worker."
        ),
        stored_tools=render_allowed_tools(cfg, team.name),
        overrides=overrides,
        extra_blocks=((NATIVE_CAPTURE_LABEL, native_block),) if native_block.strip() else (),
        extra_flags=_source_fetch_flags(team, overrides.allowed_tools or ()),
    )


def _verifier(team: OrchestratedTeam, cfg: EngineConfig, *, prompt: str) -> ResolvedAgent:
    return _node_agent(
        team,
        cfg,
        VERIFIER_ROLE,
        system_prompt=compose_system_prompt(prompt, dynamic_block=UNTRUSTED_CONTENT_NOTICE),
        user_message=verifier_message(),
        model=team.worker_model,
        model_field="worker_model",
        engine_role=DIFF_REVIEWER_ENGINE_ROLE,
        when_runs=(
            f"Third phase of a {team.name} run, after every worker has finished and before "
            "the synthesizer: reviews the full worker outputs for drift, gaps and "
            "contradictions; its findings are passed on to the synthesizer."
        ),
    )


def _synthesizer(team: OrchestratedTeam, cfg: EngineConfig) -> ResolvedAgent:
    return _node_agent(
        team,
        cfg,
        SYNTHESIZER_ROLE,
        system_prompt=compose_system_prompt(
            team.synthesizer_prompt, dynamic_block=UNTRUSTED_CONTENT_NOTICE
        ),
        user_message=synthesizer_message(team),
        model=team.synthesizer_model,
        model_field="synthesizer_model",
        engine_role=JUDGE_ENGINE_ROLE,
        when_runs=(
            f"Final phase of a {team.name} run: consolidates the task, every worker's "
            "output and any verifier findings into the run's result."
        ),
    )


def _builders(team: OrchestratedTeam) -> dict[str, Builder]:
    builders: dict[str, Builder] = {
        ORCHESTRATOR_ROLE: _orchestrator,
        agent_key.WORKER_ROLE: _worker,
    }
    if team.verifier_prompt is not None:
        builders[VERIFIER_ROLE] = partial(_verifier, prompt=team.verifier_prompt)
    builders[SYNTHESIZER_ROLE] = _synthesizer
    return builders


def _group(team: OrchestratedTeam, role: str, build: Builder) -> ResolverGroup:
    key = agent_key.team_role(team.name, role)
    return ResolverGroup(
        id=key.id,
        name=f"{team.name} / {role}",
        key=key,
        resolve=lambda cfg: [build(team, cfg)],
    )


GROUPS: tuple[ResolverGroup, ...] = tuple(
    _group(team, role, build)
    for team in _TEAMS
    for role, build in _builders(team).items()
)

__all__ = ["GROUPS"]
