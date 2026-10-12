from __future__ import annotations

from pathlib import Path

from alan import agent_key, roots
from alan.agent_catalogue_helpers import ResolvedAgent, ResolverGroup, ValueSource, runtime_slot
from alan.agent_catalogue_models import ConditionalDirOut, SessionOut, TurnLimitOut
from alan.agent_catalogue_standalone_common import ALLOWLISTED_ENVIRONMENT_NOTE, group
from alan.engine_config import EngineConfig
from alan.launch_spec import LaunchSpec, with_add_dirs
from alan.operator_launch import operator_spec
from alan.operator_prompt import Scope, workspace_prompt

_SESSION = SessionOut(
    behaviour="interactive",
    detail=(
        "An interactive Claude Code terminal hosted in tmux on a private socket; the "
        "operator talks to it directly. No permission mode or tool is pre-approved, so the "
        "operator approves tool use in the terminal. At most one session exists at a time."
    ),
)

_NO_CHOICE_SOURCE = ValueSource(
    kind="cli_default",
    detail="alan passes no model or effort; the session uses the CLI's own defaults.",
)

_TURN_LIMIT = TurnLimitOut(
    max_turns=None,
    note="An interactive session has no turn limit; it lasts until the operator ends it.",
)

_PROCESS_DETAIL = (
    "Runs inside a tmux server that outlives the service; started with an explicit "
    "allow-listed environment plus the terminal type."
)

_DOCS_DIR = ConditionalDirOut(
    label="OPERATOR_DOCS_DIR",
    condition=(
        "Added only when the workspace belongs to a different repository than the install's "
        "own: the docs folder of the install's checkout."
    ),
)

_TIMEOUTS_DETAIL = "No timeout; the session lasts until the operator ends it."

_NO_FIRST_MESSAGE = "No first message: the session sits idle until the operator types."


def _operator_agent(
    scope: Scope,
    *,
    name: str,
    when_runs: str,
    spec: LaunchSpec,
    user_message: str,
    working_directory: str,
    repo_scoped: bool = False,
    conditional_add_dirs: tuple[ConditionalDirOut, ...] = (),
) -> ResolvedAgent:
    return ResolvedAgent(
        key=agent_key.operator_session(scope.value),
        name=name,
        when_runs=when_runs,
        scope="repo" if repo_scoped else "machine",
        spec=spec,
        user_message=user_message,
        model_source=_NO_CHOICE_SOURCE,
        effort_source=_NO_CHOICE_SOURCE,
        turn_limit=_TURN_LIMIT,
        session=_SESSION,
        working_directory=working_directory,
        conditional_add_dirs=conditional_add_dirs,
        timeouts_detail=_TIMEOUTS_DETAIL,
        process_detail=_PROCESS_DETAIL,
        environment_note=ALLOWLISTED_ENVIRONMENT_NOTE,
    )


def _fleet(_cfg: EngineConfig) -> list[ResolvedAgent]:
    folders = (roots.workspaces_root(), roots.logs_root(), roots.state_root())
    return [
        _operator_agent(
            Scope.FLEET,
            name="Operator terminal / fleet",
            when_runs=(
                "When an operator starts a fleet-wide terminal from the Operator page. "
                f"{_NO_FIRST_MESSAGE}"
            ),
            spec=operator_spec(Scope.FLEET, None, folders),
            user_message="",
            working_directory=(
                "The operator repos root, which holds a checkout of every connected repository."
            ),
        )
    ]


def _repo(cfg: EngineConfig) -> list[ResolvedAgent]:
    return [
        _operator_agent(
            Scope.REPO,
            name="Operator terminal / repository",
            when_runs=(
                f"When an operator starts a terminal for one repository. {_NO_FIRST_MESSAGE}"
            ),
            spec=operator_spec(Scope.REPO, None, ()),
            user_message="",
            working_directory=(
                f"The operator checkout of {cfg.repo.slug}, brought up to date by "
                "fast-forward before the session starts."
            ),
            repo_scoped=True,
        )
    ]


def _workspace_prompt_and_dirs() -> tuple[str, tuple[str, ...]]:
    workspace_dir = runtime_slot(
        "OPERATOR_WORKSPACE_DIR", "Folder of the workspace being examined"
    )
    docs_dir = runtime_slot(
        "OPERATOR_DOCS_DIR", "Docs folder of the install's own checkout"
    )
    logs_dir = roots.logs_root().absolute()
    prompt = workspace_prompt(
        task_id=runtime_slot("OPERATOR_TASK_ID", "Task id of the workspace being examined"),
        repo_slug=runtime_slot(
            "OPERATOR_REPO", "Repository (owner/name) the workspace belongs to"
        ),
        workspace_dir=Path(workspace_dir),
        logs_dir=logs_dir,
        docs_dir=Path(docs_dir),
    )
    return prompt, (workspace_dir, str(logs_dir))


def _workspace(_cfg: EngineConfig) -> list[ResolvedAgent]:
    prompt, add_dirs = _workspace_prompt_and_dirs()
    spec = with_add_dirs(operator_spec(Scope.WORKSPACE, prompt, ()), add_dirs)
    return [
        _operator_agent(
            Scope.WORKSPACE,
            name="Operator terminal / workspace",
            when_runs=(
                "When an operator starts a terminal from a workspace page; the first message "
                "points the session at that workspace."
            ),
            spec=spec,
            user_message=prompt,
            working_directory=(
                "The operator checkout of the workspace's own repository, brought up to date "
                "by fast-forward before the session starts."
            ),
            conditional_add_dirs=(_DOCS_DIR,),
        )
    ]


GROUPS: tuple[ResolverGroup, ...] = (
    group(agent_key.operator_session(Scope.FLEET.value), "Operator terminal / fleet", _fleet),
    group(agent_key.operator_session(Scope.REPO.value), "Operator terminal / repository", _repo),
    group(
        agent_key.operator_session(Scope.WORKSPACE.value),
        "Operator terminal / workspace",
        _workspace,
    ),
)

__all__ = ["GROUPS"]
