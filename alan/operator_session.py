from __future__ import annotations

import functools
import logging
import shutil
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ValidationError

import alan.workspace as workspace_mod
from alan import (
    detached_mode,
    engine_config,
    operator_checkout,
    operator_launch,
    operator_prompt,
    operator_tmux,
    roots,
)
from alan.driver_loop_cmd import build_env
from alan.engine_config import RepoIdentity
from alan.operator_prompt import Scope
from alan.store import write_text_atomic, write_text_mkstemp

logger = logging.getLogger("alan.operator_session")

_REQUIRED_TOOLS: Final[tuple[str, ...]] = ("tmux", operator_launch.CLAUDE_EXECUTABLE, "git")
_TRANSCRIPT_MODE: Final[int] = 0o600
_UNAVAILABLE_DETAILS: Final[dict[str, str]] = {
    "detached_mode_off": (
        "Detached worker mode is off, so a session would not survive a service restart. "
        f"Turn it on with {detached_mode.DIAL_ENV} or configure the working-area pool."
    ),
    "tmux_missing": "tmux is not installed or not on the service PATH. Run setup again.",
    "claude_missing": "The claude CLI is not installed or not on the service PATH.",
    "git_missing": "git is not installed or not on the service PATH.",
    "checkout_clone_failed": "The operator checkout could not be cloned.",
}

_lock = threading.Lock()


@dataclass(frozen=True)
class Unavailable:
    reason: str
    detail: str


@dataclass(frozen=True)
class SessionInfo:
    scope: Scope
    target: str | None
    started_at: str | None
    update_warning: str | None
    exited: bool


@dataclass(frozen=True)
class Status:
    session: SessionInfo | None
    unavailable: Unavailable | None


class SessionMeta(BaseModel):
    scope: Scope
    target: str | None = None
    started_at: str
    update_warning: str | None = None


class OperatorUnavailable(Exception):
    def __init__(self, unavailable: Unavailable) -> None:
        super().__init__(unavailable.reason)
        self.unavailable = unavailable


class OperatorSessionActive(Exception):
    pass


class OperatorTargetNotFound(Exception):
    pass


class OperatorStartFailed(Exception):
    pass


class OperatorEndFailed(Exception):
    pass


def unavailable_for(reason: str, detail: str | None = None) -> Unavailable:
    return Unavailable(reason=reason, detail=detail or _UNAVAILABLE_DETAILS[reason])


def meta_path() -> Path:
    return operator_tmux.state_dir() / "session.json"


def _read_meta() -> SessionMeta | None:
    try:
        return SessionMeta.model_validate_json(meta_path().read_text(encoding="utf-8"))
    except (OSError, ValidationError):
        return None


def current() -> SessionInfo | None:
    if not operator_tmux.has_session():
        return None
    exited = operator_tmux.pane_exited()
    meta = _read_meta()
    if meta is None:
        return SessionInfo(Scope.FLEET, None, None, None, exited)
    return SessionInfo(meta.scope, meta.target, meta.started_at, meta.update_warning, exited)


def availability() -> Unavailable | None:
    if not detached_mode.resolve_detached_mode():
        return unavailable_for("detached_mode_off")
    search_path = build_env().get("PATH", "")
    for tool in _REQUIRED_TOOLS:
        if shutil.which(tool, path=search_path) is None:
            return unavailable_for(f"{tool}_missing")
    return None


def status() -> Status:
    session = current()
    return Status(session=session, unavailable=None if session else availability())


@dataclass(frozen=True)
class _Resolved:
    workspace: workspace_mod.Workspace | None
    plan: Callable[[], operator_launch.LaunchPlan]


def _connected_repo(slug: str) -> RepoIdentity:
    cfg = engine_config.lookup_by_slug(slug)
    if cfg is None or cfg.hidden:
        raise OperatorTargetNotFound(f"repository not connected: {slug}")
    return cfg.repo


def _read_workspace(task_id: str, workspaces_root: Path | None) -> workspace_mod.Workspace:
    try:
        return workspace_mod.read(task_id, root=workspaces_root)
    except workspace_mod.WorkspaceError as exc:
        raise OperatorTargetNotFound(f"workspace not found: {task_id}") from exc


def _workspace_repo(ws: workspace_mod.Workspace) -> RepoIdentity:
    key = ws.meta.engine_key
    cfg = engine_config.get_registered() if key is None else engine_config.lookup_by_slug(key)
    if cfg is None:
        raise OperatorTargetNotFound(f"repository of workspace {ws.task_id} is not connected")
    return cfg.repo


def _resolve(scope: Scope, target: str | None, workspaces_root: Path | None) -> _Resolved:
    if scope is Scope.REPO:
        repo_plan = functools.partial(operator_launch.repo_plan, _connected_repo(str(target)))
        return _Resolved(workspace=None, plan=repo_plan)
    if scope is Scope.WORKSPACE:
        ws = _read_workspace(str(target), workspaces_root)
        workspace_plan = functools.partial(
            operator_launch.workspace_plan,
            _workspace_repo(ws),
            task_id=ws.task_id,
            workspace_dir=(workspaces_root or roots.workspaces_root()) / ws.task_id,
        )
        return _Resolved(workspace=ws, plan=workspace_plan)
    return _Resolved(workspace=None, plan=operator_launch.global_plan)


def _unused_transcript_path(directory: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = directory / f"operator_transcript-{stamp}.txt"
    attempt = 1
    while path.exists():
        attempt += 1
        path = directory / f"operator_transcript-{stamp}-{attempt}.txt"
    return path


def _archive_transcript(target: str, workspaces_root: Path | None) -> Path | None:
    transcript = operator_tmux.capture_pane()
    if transcript is None:
        logger.warning("operator transcript not captured for %s", target)
        return None
    try:
        ws = workspace_mod.read(target, root=workspaces_root)
        path = _unused_transcript_path(ws.path)
        write_text_mkstemp(
            path, transcript, prefix=".operator_transcript-", mode=_TRANSCRIPT_MODE
        )
    except (workspace_mod.WorkspaceError, OSError):
        logger.warning("operator transcript not archived for %s", target, exc_info=True)
        return None
    return path


def _kill_and_confirm() -> bool:
    operator_tmux.kill_session()
    return operator_tmux.probe_session() is False


def _finalize(info: SessionInfo, workspaces_root: Path | None) -> None:
    if info.scope is Scope.WORKSPACE and info.target is not None:
        _archive_transcript(info.target, workspaces_root)
    if not _kill_and_confirm():
        raise OperatorEndFailed(
            "the operator session could not be confirmed stopped after kill-session"
        )
    meta_path().unlink(missing_ok=True)


def _spawn(plan: operator_launch.LaunchPlan) -> None:
    try:
        result = operator_tmux.new_session(list(plan.argv), cwd=plan.cwd)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OperatorStartFailed(operator_checkout.redact(f"tmux did not start: {exc}")) from exc
    if result.returncode != 0:
        raise OperatorStartFailed(operator_checkout.redact(f"tmux refused: {result.stderr}"))


def _remember(meta: SessionMeta) -> None:
    try:
        write_text_atomic(meta_path(), meta.model_dump_json(), mode=_TRANSCRIPT_MODE)
    except OSError as exc:
        orphaned = "" if _kill_and_confirm() else "; the new session could not be stopped"
        raise OperatorStartFailed(f"session record not written: {exc}{orphaned}") from exc


def _prepare_launch(resolved: _Resolved) -> operator_launch.LaunchPlan:
    try:
        return resolved.plan()
    except operator_checkout.CheckoutCloneFailed as exc:
        raise OperatorUnavailable(unavailable_for("checkout_clone_failed", str(exc))) from exc


def _record_intervention(ws: workspace_mod.Workspace) -> None:
    try:
        workspace_mod.record_intervention(ws, workspace_mod.InterventionAction.OPERATOR_TERMINAL)
    except Exception:  # noqa: BLE001 - best-effort marker, the live session matters more
        logger.warning("operator intervention not recorded for %s", ws.task_id, exc_info=True)


def start(
    scope: Scope,
    target: str | None,
    *,
    replace: bool = False,
    workspaces_root: Path | None = None,
) -> SessionInfo:
    checked = operator_prompt.validate_target(scope, target)
    resolved = _resolve(scope, checked, workspaces_root)
    with _lock:
        live = current()
        if live is not None and not replace:
            raise OperatorSessionActive()
        unavailable = availability()
        if unavailable is not None:
            raise OperatorUnavailable(unavailable)
        if live is not None:
            _finalize(live, workspaces_root)
        plan = _prepare_launch(resolved)
        _spawn(plan)
        meta = SessionMeta(
            scope=scope,
            target=target,
            started_at=datetime.now(UTC).isoformat(),
            update_warning=plan.warning,
        )
        _remember(meta)
    if resolved.workspace is not None:
        _record_intervention(resolved.workspace)
    return SessionInfo(scope, target, meta.started_at, meta.update_warning, exited=False)


def end(*, workspaces_root: Path | None = None) -> bool:
    with _lock:
        live = current()
        if live is None:
            return False
        _finalize(live, workspaces_root)
        return True


__all__ = [
    "OperatorEndFailed",
    "OperatorSessionActive",
    "OperatorStartFailed",
    "OperatorTargetNotFound",
    "OperatorUnavailable",
    "SessionInfo",
    "Status",
    "Unavailable",
    "availability",
    "current",
    "end",
    "start",
    "status",
]
