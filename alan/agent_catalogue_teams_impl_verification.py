from __future__ import annotations

from dataclasses import dataclass

from alan import agent_catalogue_teams_impl_slots as slots
from alan import agent_key
from alan.agent_catalogue_helpers import (
    ROLE_OUTPUT_NONCE,
    ResolvedAgent,
    ResolverGroup,
    ValueSource,
    runtime_slot,
)
from alan.agent_catalogue_node import NodeOptions, team_node
from alan.agent_catalogue_teams_impl_common import (
    NO_ENGINE_EFFORT_DETAIL,
    engine_model_source,
    prompt_overridden,
)
from alan.agent_catalogue_teams_impl_user import verification_base_text
from alan.claude_cli_grants import with_gate_reachability
from alan.engine_config import _DEFAULT as _ENGINE_DEFAULT  # pyright: ignore[reportPrivateUsage]
from alan.engine_config import EngineConfig
from alan.teams import _clause_audit  # pyright: ignore[reportPrivateUsage]
from alan.teams._base import WorkerOverrides, render_allowed_tools
from alan.teams._static_pipeline import (
    Role,
    _coverage_block,  # pyright: ignore[reportPrivateUsage]
    assemble_role_system_prompt,
    build_role_user_text,
)
from alan.teams.verification import TEAM, roles_for_config

_DERIVED_EFFORT = ValueSource(kind="builtin", detail=NO_ENGINE_EFFORT_DETAIL)
_WORKING_DIRECTORY = (
    "The grounded checkout of the pull request's branch that verification acquired for this "
    "pass (a leased slot or worktree), falling back to the workspace directory; resolved at "
    "run time."
)


@dataclass(frozen=True)
class _RoleFacts:
    when_runs: str
    engine_role: str
    user_prefix: str = ""
    prior_outputs: tuple[str, ...] = ()
    coverage: str | None = None
    user_suffix: str = ""


_FACTS: dict[str, _RoleFacts] = {
    "diff_reviewer": _RoleFacts(
        when_runs=(
            "First verification stage, alongside scenario_runner: reviews the pull request's "
            "diff against the approved plan and reports findings with severities and a "
            "critical-finding count."
        ),
        engine_role="diff_reviewer",
        user_prefix=slots.DIFF_REVIEW_CONTEXT,
    ),
    "scenario_runner": _RoleFacts(
        when_runs=(
            "First verification stage, alongside diff_reviewer: interprets the host-run test "
            "scenario results and the changed end-to-end specs. Its model comes from the "
            "engine's diff_reviewer role."
        ),
        engine_role="diff_reviewer",
        user_prefix=slots.SCENARIO_EVIDENCE,
    ),
    "clause_auditor": _RoleFacts(
        when_runs=(
            "First verification stage, only when the acceptance-clause audit is enabled and "
            "the workspace has live acceptance clauses: audits the delivered change against "
            "each clause. Its model comes from the engine's diff_reviewer role. When its "
            "answer has no parseable clause line it is re-run once as a retry node, which is "
            "the same agent given a retry variant of the test-ownership paragraph."
        ),
        engine_role="diff_reviewer",
        user_prefix=slots.CLAUSE_AUDIT_EVIDENCE,
        coverage=slots.CLAUSE_AUDITOR_COVERAGE,
    ),
    "judge": _RoleFacts(
        when_runs=(
            "Second and last verification stage: consolidates the other roles' output into "
            "the verdict that routes the pull request. When its output lacks the mandatory "
            "verdict line it is re-run once as a retry node, which is the same agent with a "
            "retry nudge appended to its user message."
        ),
        engine_role="judge",
        prior_outputs=("diff_reviewer", "scenario_runner"),
        user_suffix=slots.JUDGE_CLAUSE_AUDITOR_SECTION + slots.JUDGE_RETRY_NUDGE,
    ),
}


def _output_slot(role: str) -> str:
    return runtime_slot(
        f"VERIFICATION_{role.upper()}_OUTPUT",
        f"Full output of the {role} role in the verification team, produced earlier in this "
        "pipeline",
    )


def _user_message(name: str) -> str:
    facts = _FACTS[name]
    outputs = [(role, _output_slot(role)) for role in facts.prior_outputs]
    coverage = facts.coverage or _coverage_block(name, TEAM.roles)
    text = build_role_user_text(
        verification_base_text(), outputs, coverage, nonce=ROLE_OUTPUT_NONCE
    )
    return facts.user_prefix + text + facts.user_suffix


def _verification_overrides(cfg: EngineConfig) -> WorkerOverrides:
    return WorkerOverrides(
        cwd=None,
        allowed_tools=with_gate_reachability(
            render_allowed_tools(cfg, TEAM.name), cfg.gate_commands
        ),
    )


def _clause_auditor_prompt(cfg: EngineConfig) -> str:
    prompt = _clause_audit.clause_auditor_prompt(cfg)
    ownership = prompt.removeprefix(
        _clause_audit._CLAUSE_AUDITOR_PROMPT_HEAD  # pyright: ignore[reportPrivateUsage]
    ).removesuffix(
        _clause_audit._CLAUSE_AUDITOR_PROMPT_TAIL  # pyright: ignore[reportPrivateUsage]
    )
    return prompt.replace(ownership, slots.CLAUSE_TEST_OWNERSHIP, 1)


def _roles(cfg: EngineConfig) -> list[Role]:
    diff_reviewer, scenario_runner, judge = roles_for_config(cfg)
    clause_auditor = Role("clause_auditor", _clause_auditor_prompt(cfg), diff_reviewer.model)
    return [diff_reviewer, scenario_runner, clause_auditor, judge]


def _role_agent(cfg: EngineConfig, role: Role) -> ResolvedAgent:
    facts = _FACTS[role.name]
    return team_node(
        team=TEAM.name,
        role=role.name,
        system_prompt=assemble_role_system_prompt(
            role.system_prompt,
            terse=TEAM._role_terse(role.name),  # pyright: ignore[reportPrivateUsage]
        ),
        user_message=_user_message(role.name),
        model=role.model,
        effort=None,
        model_source=engine_model_source(
            cfg, facts.engine_role, _ENGINE_DEFAULT.roles[facts.engine_role].model
        ),
        effort_source=_DERIVED_EFFORT,
        when_runs=facts.when_runs,
        working_directory=_WORKING_DIRECTORY,
        overrides=_verification_overrides(cfg),
        options=NodeOptions(
            stored_tools=render_allowed_tools(cfg, TEAM.name),
            prompt_overridden=prompt_overridden(cfg, TEAM.name, role.name),
            engine_role_hint=facts.engine_role,
        ),
    )


def _resolve(cfg: EngineConfig) -> list[ResolvedAgent]:
    return [_role_agent(cfg, role) for role in _roles(cfg)]


VERIFICATION_GROUP = ResolverGroup(
    id="team:verification",
    name="verification team",
    key=agent_key.team_role(TEAM.name, "diff_reviewer"),
    resolve=_resolve,
)

__all__ = ["VERIFICATION_GROUP"]
