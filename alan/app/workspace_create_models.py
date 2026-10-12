from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, cast

from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, model_validator

import alan.prep_chat as prep_chat_mod
import alan.workspace as workspace_mod
from alan.acceptance import unproven_clause_ids
from alan.engine_config import default_engine_config, get_registered, lookup_by_slug
from alan.errors import AppError
from alan.scope_audit import CounterCase
from alan.teams import MANUALLY_DISPATCHABLE_TEAMS, TEAMS
from alan.workspace import (
    Requirement,
    Task,
    TaskDependency,
    TaskDoDClause,
    TaskStatus,
    validate_dag_inputs,
)

logger = logging.getLogger("alan.server")

UNCOVERED_CLAUSES_DETAIL = "dod_clauses do not cover every live acceptance clause"
_SUPPLIED_CLAUSE_ID_DETAIL = (
    "acceptance clause ids are derived from clause text; omit id"
)
_UNKNOWN_TEAM_DETAIL = "task {task_id!r} names unknown team {team!r}"
_NOT_MANUALLY_DISPATCHABLE_DETAIL = (
    "task {task_id!r} names team {team!r} which is not manually dispatchable "
    "(internal-only team)"
)
_REACHES_DECOMPOSER_DETAIL = (
    "task {task_id!r} names team {team!r} which can reach the decomposer step "
    "and would re-plan the whole item"
)


class TaskCreateEntry(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    team: str | None = Field(default=None, min_length=1, max_length=64)
    dod_clauses: list[TaskDoDClause] = Field(default_factory=list[TaskDoDClause])

    def to_task(self) -> Task:
        return Task(
            id=self.id,
            team=self.team,
            status=TaskStatus.PENDING,
            depends_on=[],
            dod_clauses=[c.model_copy(update={"evidence": None}) for c in self.dod_clauses],
        )


class CreateWorkspaceBody(BaseModel):
    title: str = Field(min_length=1, max_length=2000)
    description: str = Field(default="", max_length=20000)
    tasks: list[TaskCreateEntry] = Field(default_factory=list[TaskCreateEntry])
    dependencies: list[TaskDependency] = Field(default_factory=list[TaskDependency])
    requirements: list[Requirement] | None = None
    engine_key: str | None = Field(default=None, min_length=1, max_length=200)
    project_id: str | None = Field(default=None, min_length=1, max_length=200)
    roadmap_item_id: str | None = Field(default=None, min_length=1, max_length=200)
    team: str | None = Field(default=None, min_length=1, max_length=64)
    instructions: str | None = Field(default=None, max_length=40000)
    counter_case: CounterCase | None = None
    prep_chat_kind: prep_chat_mod.PrepChatKind | None = None
    prep_chat_session_id: str | None = Field(default=None, min_length=1, max_length=64)
    dispatch_actor: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9_-]*$"
    )

    @model_validator(mode="after")
    def _validate_tasks(self) -> "CreateWorkspaceBody":
        if self.team is not None and self.tasks:
            raise ValueError("team is mutually exclusive with a non-empty tasks list")
        if not self.tasks and not self.dependencies:
            return self
        ids_list = [t.id for t in self.tasks]
        if len(ids_list) != len(set(ids_list)):
            raise ValueError("duplicate task id")
        validate_dag_inputs(set(ids_list), self.dependencies)
        return self

    @model_validator(mode="after")
    def _validate_prep_chat_ref(self) -> "CreateWorkspaceBody":
        if (self.prep_chat_kind is None) != (self.prep_chat_session_id is None):
            raise ValueError(
                "prep_chat_kind and prep_chat_session_id must be provided together"
            )
        return self

    @model_validator(mode="before")
    @classmethod
    def _reject_supplied_clause_ids(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        d = cast(dict[str, Any], data)
        requirements = cast("list[Any]", d.get("requirements") or [])
        for item in requirements:
            if isinstance(item, dict) and cast(dict[str, Any], item).get("id"):
                raise ValueError(_SUPPLIED_CLAUSE_ID_DETAIL)
        return d


class CreateWorkspaceResponse(BaseModel):
    task_id: str
    root_task_ids: list[str]


def _canonical_requirements(requirements: list[Requirement]) -> list[Requirement]:
    return [Requirement(text=r.text, test_ref=r.test_ref) for r in requirements]


def reject_uncovered_clauses(body: CreateWorkspaceBody, tasks: list[Task]) -> None:
    if len(tasks) < 2 or not body.requirements:
        return
    canonical = _canonical_requirements(body.requirements)
    uncovered = unproven_clause_ids(canonical, tasks)
    if uncovered:
        raise AppError(
            status_code=400,
            detail={
                "error": UNCOVERED_CLAUSES_DETAIL,
                "uncovered_clause_ids": sorted(uncovered),
            },
        )


def _reaches_decomposer(graph: dict[str, list[str]], team: str) -> bool:
    seen: set[str] = set()
    worklist: list[str] = [team]
    while worklist:
        node = worklist.pop()
        if node in seen:
            continue
        seen.add(node)
        if node == "decomposer":
            return True
        worklist.extend(graph.get(node, []))
    return False


def _resolve_transition_graph(engine_key: str | None) -> dict[str, list[str]]:
    if engine_key is not None:
        cfg = lookup_by_slug(engine_key)
        if cfg is not None:
            return cfg.transition_graph
    cfg = get_registered() or default_engine_config()
    return cfg.transition_graph


def _reject_if_unseedable(entry: TaskCreateEntry, graph: dict[str, list[str]]) -> None:
    team = entry.team
    if team is None:
        return
    if team not in TEAMS:
        raise AppError(
            status_code=400,
            detail=_UNKNOWN_TEAM_DETAIL.format(task_id=entry.id, team=team),
        )
    if team not in MANUALLY_DISPATCHABLE_TEAMS:
        raise AppError(
            status_code=400,
            detail=_NOT_MANUALLY_DISPATCHABLE_DETAIL.format(
                task_id=entry.id, team=team
            ),
        )
    if _reaches_decomposer(graph, team):
        raise AppError(
            status_code=400,
            detail=_REACHES_DECOMPOSER_DETAIL.format(task_id=entry.id, team=team),
        )


def reject_unseedable_teams(body: CreateWorkspaceBody, engine_key: str | None) -> None:
    if not body.tasks:
        return
    graph = _resolve_transition_graph(engine_key)
    for entry in body.tasks:
        _reject_if_unseedable(entry, graph)


async def archive_prep_chat_best_effort(
    body: CreateWorkspaceBody,
    ws: workspace_mod.Workspace,
    build_chat_root: Path,
    explore_chat_root: Path,
) -> None:
    prep_chat_kind = body.prep_chat_kind
    prep_chat_session_id = body.prep_chat_session_id
    if prep_chat_kind is None or prep_chat_session_id is None:
        return
    root = (
        build_chat_root
        if prep_chat_kind is prep_chat_mod.PrepChatKind.BUILD
        else explore_chat_root
    )
    try:
        await run_in_threadpool(
            prep_chat_mod.archive,
            prep_chat_kind,
            prep_chat_session_id,
            ws.path,
            title=ws.meta.title,
            root=root,
        )
    except Exception:  # noqa: BLE001 - archive failure must never roll back an enqueued dispatch
        safe_session_id = prep_chat_session_id.replace("\r", "").replace("\n", "")
        logger.warning(
            "create_workspace: prep-chat archive failed for %s session %s",
            prep_chat_kind.value,
            safe_session_id,
            exc_info=True,
        )
