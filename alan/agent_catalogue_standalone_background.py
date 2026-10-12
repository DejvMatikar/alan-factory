from __future__ import annotations

import json

from alan import agent_key
from alan import collision as collision_mod
from alan import compression as compression_mod
from alan import engine_config_proposal as proposal_mod
from alan import investigator as investigator_mod
from alan import manager_summary as manager_summary_mod
from alan.agent_catalogue_helpers import ResolvedAgent, ResolverGroup, runtime_slot
from alan.agent_catalogue_models import SessionOut
from alan.agent_catalogue_standalone_common import (
    CHAT_TIMEOUTS_DETAIL,
    HAIKU,
    SONNET,
    STREAMED_TIMEOUTS_DETAIL,
    HelperOptions,
    builtin_source,
    chat_turn_spec,
    group,
    helper_agent,
    streamed_helper_spec,
)
from alan.claude_cli import ProcessConfig, streaming_spec
from alan.engine_config import EngineConfig, default_engine_config
from alan.engine_stack_detect import StackDetection

_COLLISION_PROMPT = collision_mod._SYS  # pyright: ignore[reportPrivateUsage]
_SUMMARY_PROMPT = manager_summary_mod._SYSTEM_PROMPT  # pyright: ignore[reportPrivateUsage]
_INVESTIGATOR_PROMPT = investigator_mod._SYSTEM_PROMPT  # pyright: ignore[reportPrivateUsage]
_INVESTIGATOR_TOOLS = investigator_mod._INVESTIGATOR_ALLOWED_TOOLS  # pyright: ignore[reportPrivateUsage]
_INVESTIGATOR_MAX_TURNS = investigator_mod._MAX_TURNS  # pyright: ignore[reportPrivateUsage]
_COMPACTOR_PROMPT = compression_mod._COMPACTOR_SYSTEM_PROMPT  # pyright: ignore[reportPrivateUsage]
_COMPACT_TIMEOUT = compression_mod._COMPACT_TIMEOUT_SECONDS  # pyright: ignore[reportPrivateUsage]
_PROPOSAL_MODEL = proposal_mod._INFERENCE_MODEL  # pyright: ignore[reportPrivateUsage]
_PROPOSAL_MAX_TURNS = proposal_mod._INFERENCE_MAX_TURNS  # pyright: ignore[reportPrivateUsage]


_PROPOSAL_SESSION = SessionOut(
    behaviour="new",
    detail="Each proposal starts one new CLI session with a generated id; it is never resumed.",
)


def _collision_user_message() -> str:
    brief = runtime_slot(
        "COLLISION_BRIEF", "The dispatch brief being checked, cut to its first 5000 characters"
    )
    candidates = runtime_slot(
        "COLLISION_CANDIDATES",
        "One line per similar open pull request or workspace: id, whether it is live, title "
        "and, for pull requests, the changed files",
    )
    return collision_mod._build_prompt(brief, [], []) + candidates  # pyright: ignore[reportPrivateUsage]


def _collision(_cfg: EngineConfig) -> list[ResolvedAgent]:
    key = agent_key.COLLISION
    return [
        helper_agent(
            key,
            name="Collision check",
            when_runs=(
                "When a dispatch proposal is requested and at least one open pull request or "
                "workspace looks similar to the brief; with no similar work no model call is "
                "made."
            ),
            spec=streamed_helper_spec(_COLLISION_PROMPT, model=HAIKU, agent=key),
            user_message=_collision_user_message(),
            model_source=builtin_source(
                "Fixed in the collision check's model call; it never reads an engine role."
            ),
            timeouts_detail=STREAMED_TIMEOUTS_DETAIL,
        )
    ]


def _summary_user_message() -> str:
    title = runtime_slot("SUMMARY_TASK_TITLE", "Title of the finished task")
    status = runtime_slot(
        "SUMMARY_FINAL_STATUS",
        "How the task ended, in words (completed, failed, paused, escalated)",
    )
    details = runtime_slot(
        "SUMMARY_DETAILS",
        "Park reason, stop reason, definition-of-done evidence and the handover synthesis "
        "(cut to 2000 characters); each part is included only when it exists",
    )
    text = manager_summary_mod._build_user_prompt(title, status, None)  # pyright: ignore[reportPrivateUsage]
    head, _, tail = text.rpartition("\n\n")
    return f"{head}{details}\n\n{tail}"


def _manager_summary(_cfg: EngineConfig) -> list[ResolvedAgent]:
    key = agent_key.MANAGER_SUMMARY
    return [
        helper_agent(
            key,
            name="Manager summary",
            when_runs=(
                "When a workspace reaches a final state (completed, failed, paused for input "
                "or escalated) and needs a plain-language summary for the operator."
            ),
            spec=streamed_helper_spec(_SUMMARY_PROMPT, model=HAIKU, agent=key),
            user_message=_summary_user_message(),
            model_source=builtin_source(
                "Fixed in the summary's model call; it never reads an engine role."
            ),
            timeouts_detail=STREAMED_TIMEOUTS_DETAIL,
        )
    ]


def _investigator_user_message() -> str:
    fingerprint = runtime_slot("INCIDENT_FINGERPRINT", "Fingerprint identifying the incident")
    dispositions = runtime_slot(
        "INCIDENT_DISPOSITIONS", "The dispositions a human may later pick for the incident"
    )
    evidence = runtime_slot(
        "INCIDENT_EVIDENCE", "The incident's evidence, rendered as indented JSON"
    )
    text = investigator_mod._build_user_prompt(  # pyright: ignore[reportPrivateUsage]
        fingerprint, {}, frozenset({dispositions})
    )
    return text.replace("Evidence:\n{}", f"Evidence:\n{evidence}")


def _investigator(_cfg: EngineConfig) -> list[ResolvedAgent]:
    key = agent_key.INVESTIGATOR
    workspace = runtime_slot(
        "INCIDENT_WORKSPACE_DIR", "Folder of the workspace the incident belongs to"
    )
    spec = streaming_spec(
        _INVESTIGATOR_PROMPT,
        model=SONNET,
        max_turns=_INVESTIGATOR_MAX_TURNS,
        allowed_tools=list(_INVESTIGATOR_TOOLS),
        add_dirs=[workspace],
        agent=key,
    )
    return [
        helper_agent(
            key,
            name="Incident investigator",
            when_runs=(
                "When a workspace escalation, failure or park raises an incident class that "
                "gets a root-cause draft and no draft already exists for the same evidence."
            ),
            spec=spec,
            user_message=_investigator_user_message(),
            model_source=builtin_source(
                "Fixed in the investigator's model call; it never reads an engine role."
            ),
            timeouts_detail=STREAMED_TIMEOUTS_DETAIL,
        )
    ]


def _handover_compactor(_cfg: EngineConfig) -> list[ResolvedAgent]:
    key = agent_key.HANDOVER_COMPACTOR
    spec = streaming_spec(
        _COMPACTOR_PROMPT,
        model=HAIKU,
        max_turns=1,
        proc_config=ProcessConfig(timeout_seconds=_COMPACT_TIMEOUT),
        deny_all_tools=True,
        agent=key,
    )
    synthesis = runtime_slot(
        "HANDOVER_SYNTHESIS", "The handover's full synthesis text, trimmed of surrounding space"
    )
    return [
        helper_agent(
            key,
            name="Handover compactor",
            when_runs=(
                "When a handover's synthesis is longer than the compaction threshold, no "
                "cached compaction exists for it, and model compaction is not disabled."
            ),
            spec=spec,
            user_message=synthesis,
            model_source=builtin_source(
                "Fixed in the compactor's model call; it never reads an engine role."
            ),
            timeouts_detail=(
                "Idle ceiling is the compactor's own short timeout; absolute ceiling from the "
                "service's absolute worker timeout."
            ),
        )
    ]


def _proposal_system_prompt() -> str:
    baseline = default_engine_config()
    detection = StackDetection(
        recommended_preset=runtime_slot(
            "PROPOSAL_PRESET", "Stack preset the build-file scan recommended as the baseline"
        ),
        detected_stacks=(),
        additional_stacks=(
            runtime_slot(
                "PROPOSAL_ADDITIONAL_STACKS", "Further stacks the build-file scan found"
            ),
        ),
    )
    baseline_json = json.dumps(
        proposal_mod._proposable_baseline_json(baseline),  # pyright: ignore[reportPrivateUsage]
        indent=2,
    )
    baseline_slot = runtime_slot(
        "PROPOSAL_BASELINE_JSON",
        "The baseline preset's current value for every field the proposal may change, as JSON",
    )
    prompt = proposal_mod._proposal_system_prompt(  # pyright: ignore[reportPrivateUsage]
        baseline, detection
    )
    if baseline_json not in prompt:
        raise ValueError("the engine-config proposal prompt no longer embeds the baseline JSON")
    return prompt.replace(baseline_json, baseline_slot)


def _engine_config_proposal(_cfg: EngineConfig) -> list[ResolvedAgent]:
    key = agent_key.ENGINE_CONFIG_PROPOSAL
    return [
        helper_agent(
            key,
            name="Engine config proposal",
            when_runs=(
                "When someone asks alan to propose an engine configuration for a registered "
                "repository; the run inspects a temporary clone of that repository."
            ),
            spec=chat_turn_spec(
                key, _proposal_system_prompt(), model=_PROPOSAL_MODEL, max_turns=_PROPOSAL_MAX_TURNS
            ),
            user_message=proposal_mod.INFERENCE_USER_TEXT,
            model_source=builtin_source(
                "Fixed in the proposal's inference call; it never reads an engine role."
            ),
            timeouts_detail=CHAT_TIMEOUTS_DETAIL,
            options=HelperOptions(
                session=_PROPOSAL_SESSION,
                working_directory=(
                    "A temporary clone of the repository whose configuration is being "
                    "proposed; it is deleted when the proposal finishes."
                ),
                cwd_is_runtime=True,
            ),
        )
    ]


GROUPS: tuple[ResolverGroup, ...] = (
    group(agent_key.COLLISION, "Collision check", _collision),
    group(agent_key.MANAGER_SUMMARY, "Manager summary", _manager_summary),
    group(agent_key.INVESTIGATOR, "Incident investigator", _investigator),
    group(agent_key.HANDOVER_COMPACTOR, "Handover compactor", _handover_compactor),
    group(agent_key.ENGINE_CONFIG_PROPOSAL, "Engine config proposal", _engine_config_proposal),
)

__all__ = ["GROUPS"]
