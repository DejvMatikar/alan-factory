from __future__ import annotations

import json
import re
import shutil
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from alan import roots, store

_PROJECT_FILE = "project.json"
_SLUG_MAX_LEN = 32
_MAX_SLUG_SUFFIX = 100


class ProjectStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    COMPLETED = "completed"
    PROVISIONING = "provisioning"


AutonomyMode = Literal["propose", "auto_continue"]


class Project(BaseModel):
    id: str
    title: str
    status: ProjectStatus = ProjectStatus.ACTIVE
    doc_ref: str | None = None
    engine_key: str | None = None
    intended_engine_key: str | None = None
    autonomy: AutonomyMode = "propose"
    budget_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    created_at: datetime
    updated_at: datetime


class ProjectError(RuntimeError):
    pass


def create(
    title: str,
    *,
    root: Path | None = None,
    doc_ref: str | None = None,
    intended_engine_key: str | None = None,
) -> Project:
    base = _root(root)
    base.mkdir(parents=True, exist_ok=True)
    slug = _slugify(title)
    project_id, project_dir = _claim_project_dir(slug, base)
    now = datetime.now(UTC)
    project = Project(
        id=project_id,
        title=title,
        doc_ref=doc_ref,
        intended_engine_key=intended_engine_key,
        created_at=now,
        updated_at=now,
    )
    _write_project(project_dir, project)
    return project


def read(project_id: str, *, root: Path | None = None) -> Project:
    project_dir = _root(root) / project_id
    if not (project_dir / _PROJECT_FILE).is_file():
        raise ProjectError(f"project not found: {project_id}")
    return _read_project(project_dir)


def cached_read(project_id: str, cache: dict[str, Project | None]) -> Project | None:
    """Fail-open id-to-Project lookup, memoized in a caller-owned per-request `cache`.

    Mirrors `_resolve_project_doc_path` (`alan/dispatcher.py`): never raises. A
    dangling `project_id` (deleted or malformed project) resolves to `None` rather
    than propagating `ProjectError`, and a transient `OSError` (e.g. project.json
    removed between the `is_file()` check and `read_text()`, or a permission error)
    is treated the same way rather than 500ing the caller. Callers share one `cache`
    dict across a single request so N rows referencing the same project pay for one
    `read` — the primitive behind `proposal_items.resolve_project_burn` and the
    workspace-list route's project-title join.
    """
    if project_id not in cache:
        try:
            cache[project_id] = read(project_id)
        except (ProjectError, OSError):
            cache[project_id] = None
    return cache[project_id]


def engine_key_for(project_id: str) -> str | None:
    project = cached_read(project_id, {})
    return project.engine_key if project is not None else None


def list_projects(*, root: Path | None = None) -> list[Project]:
    base = _root(root)
    if not base.is_dir():
        return []
    out: list[Project] = []
    for entry in sorted(base.iterdir()):
        if not entry.is_dir():
            continue
        if not (entry / _PROJECT_FILE).is_file():
            continue
        try:
            out.append(_read_project(entry))
        except ProjectError:
            continue
    return out


def update(
    project: Project,
    updates: dict[str, Any],
    *,
    root: Path | None = None,
) -> Project:
    merged = {**project.model_dump(), **updates, "updated_at": datetime.now(UTC)}
    try:
        new_project = Project.model_validate(merged)
    except ValidationError as exc:
        raise ProjectError(f"invalid project update: {exc}") from exc
    _write_project(_root(root) / project.id, new_project)
    return new_project


def delete(project_id: str, *, root: Path | None = None) -> None:
    p_dir = _root(root) / project_id
    if not p_dir.is_dir():
        raise ProjectError(f"project not found: {project_id}")
    try:
        shutil.rmtree(p_dir)
    except FileNotFoundError as exc:
        raise ProjectError(f"project not found: {project_id}") from exc


def project_dir(project_id: str, *, root: Path | None = None) -> Path:
    return _root(root) / project_id


def _root(root: Path | None) -> Path:
    if root is not None:
        return root
    return roots.projects_root()


def _slugify(title: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return cleaned[:_SLUG_MAX_LEN] or "project"


def _claim_project_dir(slug: str, base: Path) -> tuple[str, Path]:
    for i in range(1, _MAX_SLUG_SUFFIX + 1):
        candidate_id = slug if i == 1 else f"{slug}-{i}"
        candidate = base / candidate_id
        try:
            candidate.mkdir()
        except FileExistsError:
            continue
        return candidate_id, candidate
    raise ProjectError(f"no unique project_id for {slug}")


def _write_project(project_dir: Path, project: Project) -> None:
    _write_json(project_dir / _PROJECT_FILE, project.model_dump(mode="json"))


def _read_project(project_dir: Path) -> Project:
    raw = (project_dir / _PROJECT_FILE).read_text(encoding="utf-8")
    try:
        return Project.model_validate_json(raw)
    except ValidationError as exc:
        raise ProjectError(f"{_PROJECT_FILE} malformed in {project_dir.name}") from exc


def _write_json(path: Path, data: Any) -> None:
    store.write_text_atomic(path, json.dumps(data, indent=2, sort_keys=False))
