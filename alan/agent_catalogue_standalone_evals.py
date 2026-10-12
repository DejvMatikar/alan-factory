from __future__ import annotations

from alan import agent_key, engine_config
from alan import behavioral_eval as behavioral_eval_mod
from alan.agent_catalogue_helpers import ResolvedAgent, ResolverGroup, ValueSource, runtime_slot
from alan.agent_catalogue_standalone_common import (
    ONE_SHOT_TIMEOUTS_DETAIL,
    HelperOptions,
    builtin_source,
    group,
    helper_agent,
)
from alan.claude_cli import oneshot_spec
from alan.engine_config import EngineConfig
from alan.teams.verification import judge_system_prompt

_JUDGE_ROLE = "judge"
_PLANNER_GRADER_MODEL = behavioral_eval_mod._JUDGE_MODEL  # pyright: ignore[reportPrivateUsage]
_PLANNER_GRADER_PROMPT = behavioral_eval_mod._JUDGE_SYSTEM_PROMPT  # pyright: ignore[reportPrivateUsage]

_JUDGE_MODEL_SOURCE = ValueSource(
    kind="engine_role",
    detail=(
        "The judge role of the engine registered as the default, or of the built-in default "
        "engine when none is registered. A repository being viewed that is not the default "
        "engine is not consulted."
    ),
    engine_role=_JUDGE_ROLE,
)


def _default_engine(viewed: EngineConfig) -> EngineConfig:
    registered = engine_config.get_registered() or engine_config.default_engine_config()
    return viewed if viewed.repo == registered.repo else registered


def _judge_eval(cfg: EngineConfig) -> list[ResolvedAgent]:
    key = agent_key.JUDGE_EVAL
    judge_role = _default_engine(cfg).roles[_JUDGE_ROLE]
    case_context = runtime_slot(
        "JUDGE_CASE_CONTEXT",
        "The golden case's context text; when the first answer holds no verdict, the same "
        "text plus a short retry nudge is sent once more",
    )
    return [
        helper_agent(
            key,
            name="Judge eval",
            when_runs=(
                "When the judge evaluation sweep scores the golden judge cases; it is a test "
                "of the judge's prompt and model, not part of any delivery."
            ),
            spec=oneshot_spec(
                judge_system_prompt(), model=judge_role.model, deny_all_tools=True, agent=key
            ),
            user_message=case_context,
            model_source=_JUDGE_MODEL_SOURCE,
            timeouts_detail=ONE_SHOT_TIMEOUTS_DETAIL,
            options=HelperOptions(
                scope="default_engine",
                tags=("eval",),
                configured_effort=judge_role.effort,
            ),
        )
    ]


def _planner_grader_user_message() -> str:
    case = behavioral_eval_mod.GoldenCase(
        case_id="case",
        brief=runtime_slot("PLANNER_CASE_BRIEF", "The golden case's task brief"),
        synthesizer_result=runtime_slot(
            "PLANNER_CASE_PLAN", "The golden case's candidate implementation plan"
        ),
    )
    text = behavioral_eval_mod._build_judge_user_text(case, {})  # pyright: ignore[reportPrivateUsage]
    rubric = runtime_slot(
        "PLANNER_RUBRIC", "The rubric: its scale line and one line per criterion"
    )
    return text.replace("## Rubric\n", f"## Rubric\n{rubric}", 1)


def _planner_grader_eval(_cfg: EngineConfig) -> list[ResolvedAgent]:
    key = agent_key.PLANNER_GRADER_EVAL
    return [
        helper_agent(
            key,
            name="Planner grader eval",
            when_runs=(
                "When the behavioural evaluation sweep grades a golden implementation plan "
                "against the rubric; it is a test of the planner, not part of any delivery."
            ),
            spec=oneshot_spec(
                _PLANNER_GRADER_PROMPT, model=_PLANNER_GRADER_MODEL, deny_all_tools=True, agent=key
            ),
            user_message=_planner_grader_user_message(),
            model_source=builtin_source(
                "Fixed in the grader module; it never reads an engine role."
            ),
            timeouts_detail=ONE_SHOT_TIMEOUTS_DETAIL,
            options=HelperOptions(tags=("eval",)),
        )
    ]


GROUPS: tuple[ResolverGroup, ...] = (
    group(agent_key.JUDGE_EVAL, "Judge eval", _judge_eval),
    group(agent_key.PLANNER_GRADER_EVAL, "Planner grader eval", _planner_grader_eval),
)

__all__ = ["GROUPS"]
