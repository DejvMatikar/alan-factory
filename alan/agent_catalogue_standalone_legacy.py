from __future__ import annotations

from collections.abc import Callable

from alan import agent_key
from alan.agent_catalogue_helpers import ResolvedAgent, ResolverGroup, runtime_slot
from alan.agent_catalogue_standalone_common import (
    ONE_SHOT_TIMEOUTS_DETAIL,
    SONNET,
    HelperOptions,
    builtin_source,
    group,
    helper_agent,
)
from alan.claude_cli import format_user_text, oneshot_spec
from alan.engine_config import EngineConfig
from alan.teams import implementation_plan
from alan.teams._static_pipeline import Role

TEAM_NAME = "implementation_plan"

_WHEN_RUNS = (
    "Only when the standalone planning endpoint is called, which nothing in the app does. "
    "The roles run one after another, each seeing the outputs of the roles before it."
)

_MODEL_SOURCE = builtin_source(
    "The planning endpoint passes no model, so every role runs on the one-shot default "
    f"({SONNET}); the model set on the role itself is ignored."
)


def _user_text(index: int) -> str:
    base = format_user_text(
        runtime_slot("LEGACY_PLAN_TITLE", "Title of the task being planned"),
        runtime_slot("LEGACY_PLAN_DESCRIPTION", "Description of the task being planned"),
    )
    earlier = [
        (
            role.name,
            runtime_slot(
                f"LEGACY_{role.name.upper()}_OUTPUT",
                f"Output of the {role.name} role earlier in the same run",
            ),
        )
        for role in implementation_plan.ROLES[:index]
    ]
    return implementation_plan._build_user_text(base, earlier)  # pyright: ignore[reportPrivateUsage]


def _legacy_role(role: Role, index: int) -> ResolvedAgent:
    key = agent_key.legacy_planning(role.name)
    return helper_agent(
        key,
        name=f"Legacy planning / {role.name}",
        when_runs=_WHEN_RUNS,
        spec=oneshot_spec(role.system_prompt, agent=key),
        user_message=_user_text(index),
        model_source=_MODEL_SOURCE,
        timeouts_detail=ONE_SHOT_TIMEOUTS_DETAIL,
        options=HelperOptions(
            tags=("legacy",),
            team=TEAM_NAME,
            role=role.name,
            configured_model=role.model,
        ),
    )


def _resolver(role: Role, index: int) -> Callable[[EngineConfig], list[ResolvedAgent]]:
    def resolve(_cfg: EngineConfig) -> list[ResolvedAgent]:
        return [_legacy_role(role, index)]

    return resolve


GROUPS: tuple[ResolverGroup, ...] = tuple(
    group(
        agent_key.legacy_planning(role.name),
        f"Legacy planning / {role.name}",
        _resolver(role, index),
    )
    for index, role in enumerate(implementation_plan.ROLES)
)

__all__ = ["GROUPS", "TEAM_NAME"]
