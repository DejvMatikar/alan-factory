from __future__ import annotations

import contextlib
import logging
import threading
import time
from dataclasses import dataclass

from alan import engine_config, engine_presets, secrets_store
from alan import project as project_mod
from alan.branch_protection_compat import Finding
from alan.dispatcher import Dispatcher
from alan.engine_config import EngineConfig, RepoIdentity
from alan.engine_preflight import run_preflight
from alan.project import Project, ProjectStatus
from alan.slot_pool import PoolBusyError, PoolDisposeIncompleteError

logger = logging.getLogger(__name__)

_WARM_TIMEOUT_S = 90.0
_WARM_POLL_INTERVAL_S = 1.0


class OnboardingError(RuntimeError):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class Attempt:
    project_id: str
    repo: RepoIdentity | None
    secret_created: bool


_ATTEMPTS: dict[str, Attempt] = {}
_ATTEMPTS_LOCK = threading.Lock()

_IN_FLIGHT: set[RepoIdentity] = set()
_IN_FLIGHT_LOCK = threading.Lock()


def _reserve_slug(repo_identity: RepoIdentity) -> None:
    if engine_config.lookup_by_repo(repo_identity) is not None:
        raise OnboardingError(409, f"{repo_identity.slug} is already onboarded")


def _reserve_in_flight(repo_identity: RepoIdentity) -> None:
    with _IN_FLIGHT_LOCK:
        if repo_identity in _IN_FLIGHT:
            raise OnboardingError(409, f"{repo_identity.slug} onboarding already in progress")
        _IN_FLIGHT.add(repo_identity)


def _release_in_flight(repo_identity: RepoIdentity) -> None:
    with _IN_FLIGHT_LOCK:
        _IN_FLIGHT.discard(repo_identity)


def _ensure_secret(
    repo_identity: RepoIdentity, token: str, stack: contextlib.ExitStack
) -> bool:
    existing = secrets_store.resolve_secret(
        repo_identity.slug, secrets_store.TRANSPORT_PURPOSE, env_fallback=None
    )
    if existing:
        return False
    secrets_store.write_secret(repo_identity.slug, secrets_store.TRANSPORT_PURPOSE, token)
    stack.callback(
        secrets_store.delete_secret, repo_identity.slug, secrets_store.TRANSPORT_PURPOSE
    )
    return True


def _register_engine(
    repo_identity: RepoIdentity, template: str, stack: contextlib.ExitStack
) -> EngineConfig:
    cfg = engine_presets.build_config(template, repo_identity.owner, repo_identity.repo)
    if cfg is None:
        raise OnboardingError(422, f"unknown template: {template!r}")
    try:
        engine_config.register_and_persist(cfg, validate_env=False)
    except engine_config.DuplicateEngineError as exc:
        raise OnboardingError(409, str(exc)) from exc
    stack.callback(engine_config.delete_engine, repo_identity)
    return cfg


def _finding_message(finding: Finding) -> str:
    if finding.remedy:
        return f"{finding.check}: {finding.message} (remedy: {finding.remedy})"
    return f"{finding.check}: {finding.message}"


def _gate_preflight(cfg: EngineConfig) -> None:
    result = run_preflight(cfg)
    if result.overall == "pass":
        return
    detail = "; ".join(_finding_message(f) for f in result.findings if f.status != "pass")
    if result.overall == "unknown":
        logger.warning("onboarding preflight unknown for %s: %s", cfg.repo.slug, detail)
        return
    raise OnboardingError(422, f"preliminary checks failed: {detail}")


def _dispose_pool_best_effort(dispatcher: Dispatcher, slug: str) -> None:
    try:
        dispatcher.dispose_pool(slug)
    except (PoolBusyError, PoolDisposeIncompleteError):
        logger.exception("onboarding rollback: failed to dispose pool for %s", slug)


def _provision_pool(
    dispatcher: Dispatcher, cfg: EngineConfig, stack: contextlib.ExitStack
) -> None:
    dispatcher.register_pool(cfg)
    stack.callback(_dispose_pool_best_effort, dispatcher, cfg.repo.slug)


def _delete_project_best_effort(project_id: str) -> None:
    try:
        project_mod.delete(project_id)
    except project_mod.ProjectError:
        logger.warning("onboarding rollback: project %s already gone", project_id)


def _create_and_bind_project(
    title: str, repo_identity: RepoIdentity, stack: contextlib.ExitStack
) -> Project:
    project = project_mod.create(title)
    stack.callback(_delete_project_best_effort, project.id)
    return project_mod.update(
        project,
        {"engine_key": repo_identity.slug, "status": ProjectStatus.PROVISIONING},
    )


def create_attempt(
    *,
    dispatcher: Dispatcher,
    title: str,
    owner: str,
    repo: str,
    template: str,
    token: str,
) -> Project:
    repo_identity = RepoIdentity(owner=owner, repo=repo)
    with contextlib.ExitStack() as stack:
        _reserve_slug(repo_identity)
        _reserve_in_flight(repo_identity)
        try:
            secret_created = _ensure_secret(repo_identity, token, stack)
            cfg = _register_engine(repo_identity, template, stack)
        finally:
            _release_in_flight(repo_identity)
        _gate_preflight(cfg)
        _provision_pool(dispatcher, cfg, stack)
        project = _create_and_bind_project(title, repo_identity, stack)
        stack.pop_all()
    with _ATTEMPTS_LOCK:
        _ATTEMPTS[project.id] = Attempt(
            project_id=project.id, repo=repo_identity, secret_created=secret_created
        )
    _spawn_warm_task(dispatcher, project.id, repo_identity.slug)
    return project


def _activate_project(project_id: str) -> None:
    try:
        project = project_mod.read(project_id)
    except project_mod.ProjectError:
        return
    if project.status != ProjectStatus.PROVISIONING:
        return
    project_mod.update(project, {"status": ProjectStatus.ACTIVE})
    with _ATTEMPTS_LOCK:
        _ATTEMPTS.pop(project_id, None)


def _warm_and_activate(dispatcher: Dispatcher, project_id: str, slug: str) -> None:
    deadline = time.monotonic() + _WARM_TIMEOUT_S
    while time.monotonic() < deadline:
        state = dispatcher.pool_states().get(slug)
        if state is not None and state.warm:
            _activate_project(project_id)
            return
        time.sleep(_WARM_POLL_INTERVAL_S)


def _spawn_warm_task(dispatcher: Dispatcher, project_id: str, slug: str) -> None:
    threading.Thread(
        target=_warm_and_activate, args=(dispatcher, project_id, slug), daemon=True
    ).start()


def _read_provisioning_project_or_error(project_id: str) -> Project:
    try:
        project = project_mod.read(project_id)
    except project_mod.ProjectError as exc:
        raise OnboardingError(404, str(exc)) from exc
    if project.status != ProjectStatus.PROVISIONING:
        raise OnboardingError(409, "project is not a provisioning attempt")
    return project


def _reconstruct_attempt(project_id: str, project: Project) -> Attempt:
    repo = RepoIdentity.from_slug(project.engine_key) if project.engine_key else None
    return Attempt(project_id=project_id, repo=repo, secret_created=False)


def _teardown_attempt(dispatcher: Dispatcher, attempt: Attempt) -> None:
    if attempt.repo is not None:
        _dispose_pool_best_effort(dispatcher, attempt.repo.slug)
        engine_config.delete_engine(attempt.repo)
        if attempt.secret_created:
            secrets_store.delete_secret(attempt.repo.slug, secrets_store.TRANSPORT_PURPOSE)
    _delete_project_best_effort(attempt.project_id)


def rollback_attempt(dispatcher: Dispatcher, project_id: str) -> None:
    project = _read_provisioning_project_or_error(project_id)
    with _ATTEMPTS_LOCK:
        attempt = _ATTEMPTS.pop(project_id, None)
    if attempt is None:
        attempt = _reconstruct_attempt(project_id, project)
    _teardown_attempt(dispatcher, attempt)
