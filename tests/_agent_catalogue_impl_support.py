from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from alan import agent_catalogue_teams_impl
from alan.agent_catalogue_helpers import ResolvedAgent, segments_from_text, to_entry
from alan.agent_catalogue_models import PromptSegmentOut
from alan.claude_cli import streaming_spec
from alan.engine_config import (
    EngineConfig,
    Posture,
    PromptOverride,
    RepoIdentity,
    RoleConfig,
    default_engine_config,
)
from alan.handover import Handover
from alan.launch_spec import TURN_BUDGET_PREFIX, appended_prompt
from alan.lifecycle import Status
from alan.workspace import EngineSnapshot, Meta, Task, TaskDoDClause, TaskStatus, Workspace

REPO = RepoIdentity(owner="acme", repo="widget")
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
SUBTASK = Task(
    id="subtask-1",
    status=TaskStatus.READY,
    title="Subtask",
    instructions="Do the subtask",
    dod_clauses=[TaskDoDClause(text="It is done")],
    files=["a.py"],
)

IMPLEMENTER_KEY = "team_role:implementation/implementer"
BACKSTOP_KEY = "team_role:implementation/drift_backstop"
FIX_KEY = "team_role:implementation_fix/implementer"
DIFF_REVIEWER_KEY = "team_role:verification/diff_reviewer"
SCENARIO_RUNNER_KEY = "team_role:verification/scenario_runner"
CLAUSE_AUDITOR_KEY = "team_role:verification/clause_auditor"
JUDGE_KEY = "team_role:verification/judge"

ALL_KEYS = (
    IMPLEMENTER_KEY,
    BACKSTOP_KEY,
    FIX_KEY,
    DIFF_REVIEWER_KEY,
    SCENARIO_RUNNER_KEY,
    CLAUSE_AUDITOR_KEY,
    JUDGE_KEY,
)

IMPLEMENTER_OVERRIDE = PromptOverride(
    text="OVERRIDDEN START {{ENVIRONMENT}} MIDDLE {{SUCCESS}} END {{REPO_GUIDANCE}} TAIL",
    base_fingerprint="fingerprint",
)
JUDGE_OVERRIDE = PromptOverride(
    text="OVERRIDDEN JUDGE VERDICT: x DIFF_FINDINGS_CRITICAL: y",
    base_fingerprint="fingerprint",
)


def default_config() -> EngineConfig:
    return default_engine_config()


def custom_config() -> EngineConfig:
    return EngineConfig(
        repo=REPO,
        roles={
            "implementer": RoleConfig(model="opus", effort="max"),
            "judge": RoleConfig(model="haiku", effort="low"),
            "diff_reviewer": RoleConfig(model="opus", effort="medium"),
        },
        gate_commands=[["make", "test"], ["make", "lint"]],
        warmup_commands=[["make", "setup"]],
        base_tools=["Read", "Grep"],
        team_tools={
            "implementation": ["Edit", "Write", "Bash(make:*)"],
            "implementation_fix": ["Edit", "Bash(make:*)"],
            "verification": ["WebSearch"],
        },
    )


def bare_config() -> EngineConfig:
    return EngineConfig(repo=REPO)


def overridden_config() -> EngineConfig:
    return EngineConfig(
        repo=REPO,
        posture=Posture.self_merge,
        gate_commands=[["make", "test"]],
        prompt_overrides={
            "implementation/implementer": IMPLEMENTER_OVERRIDE,
            "implementation_fix/implementer": IMPLEMENTER_OVERRIDE,
            "verification/judge": JUDGE_OVERRIDE,
        },
    )


def ios_config() -> EngineConfig:
    return EngineConfig(repo=REPO, preset="ios", gate_commands=[["make", "test"]])


CONFIG_FACTORIES: dict[str, Callable[[], EngineConfig]] = {
    "default": default_config,
    "custom": custom_config,
    "bare": bare_config,
    "overridden": overridden_config,
    "ios": ios_config,
}


def resolve_agents(cfg: EngineConfig) -> dict[str, ResolvedAgent]:
    return {
        agent.key.id: agent
        for group in agent_catalogue_teams_impl.GROUPS
        for agent in group.resolve(cfg)
    }


def with_role(cfg: EngineConfig, name: str, role: RoleConfig) -> EngineConfig:
    return cfg.model_copy(update={"roles": {**cfg.roles, name: role}})


def segments_regex(segments: Sequence[PromptSegmentOut]) -> re.Pattern[str]:
    parts: list[str] = []
    for segment in segments:
        if segment.kind == "text":
            parts.append(re.escape(segment.text))
        elif not parts or parts[-1] != ".*":
            parts.append(".*")
    return re.compile("".join(parts), re.DOTALL)


def prompt_regex(agent: ResolvedAgent) -> re.Pattern[str]:
    return segments_regex(to_entry(agent, []).prompt)


def launched_prompt(system_prompt: str, turns: int = 150) -> str:
    spec = streaming_spec(f"{system_prompt}{TURN_BUDGET_PREFIX}{turns}", model="sonnet")
    return appended_prompt(spec) or ""


def real_workspace(cfg: EngineConfig, path: Path, *, with_task: bool = False) -> Workspace:
    tasks = [SUBTASK] if with_task else []
    meta = Meta(
        task_id="catalogue-test",
        title="title",
        description="description",
        status=Status.ACTIVE,
        tasks=tasks,
        created_at=EPOCH,
        updated_at=EPOCH,
        engine_snapshot=EngineSnapshot(
            posture=cfg.posture, transition_graph=cfg.transition_graph, config=cfg
        ),
    )
    return Workspace(task_id="catalogue-test", path=path, meta=meta)


def real_handover(**fields: Any) -> Handover:
    values: dict[str, Any] = {
        "handover_id": "handover-1",
        "handover_type": "forward",
        "task_id": "catalogue-test",
        "from_team": "implementation_plan",
        "from_invocation_id": "inv_001",
        "iteration": 2,
        "timestamp_utc": EPOCH,
        "full_synthesis": "full synthesis",
    }
    values.update(fields)
    return Handover(**values)


def user_regex(agent: ResolvedAgent) -> re.Pattern[str]:
    return segments_regex(segments_from_text(agent.user_message, "user_message"))


def placeholder_labels(segments: Sequence[PromptSegmentOut]) -> list[str]:
    return [segment.label for segment in segments if segment.kind == "placeholder"]


def shared_labels(segments: Sequence[PromptSegmentOut]) -> set[str]:
    return {segment.label for segment in segments if segment.shared and segment.kind == "text"}
