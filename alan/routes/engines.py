from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from starlette.responses import Response

from alan import (
    engine_config,
    engine_config_proposal,
    engine_registry_store,
    prompt_override_history,
    prompt_templates,
    repo_scaffold,
    secrets_store,
)
from alan import project as project_mod
from alan import project_doc as project_doc_mod
from alan import repo_status as repo_status_mod
from alan import workspace as workspace_mod
from alan.branch_protection_compat import Finding
from alan.dispatcher import Dispatcher
from alan.engine_config import (
    DuplicateEngineError,
    EngineConfig,
    EngineNotFoundError,
    Posture,
    PostureCeilingError,
    PromptOverride,
    RepoIdentity,
    forge_target_for_workspace,
    posture_rank,
)
from alan.engine_config import (
    _validate_before_register as _validate_engine_config,  # pyright: ignore[reportPrivateUsage]
)
from alan.engine_config_validation import (
    FlowGraphValidationError,
    PostureOrthogonalityError,
)
from alan.engine_preflight import (
    PreflightResult,
    check_dependency_sweep_enable,
    load_last_preflight,
    run_preflight,
)
from alan.repo_scaffold import OnboardingState, ScaffoldError
from alan.routes._deps import (
    ENGINE_NOT_FOUND_DETAIL,
    GUARDED,
    OptionalDispatcherDep,
    PubSubDep,
    QueueDep,
    WorkspaceIndexDep,
    WorkspacesRootDep,
    require_control_token,
)
from alan.slot_pool import PoolBusyError, PoolDisposeIncompleteError
from alan.teams import implementation as _implementation_team
from alan.teams import implementation_fix as _implementation_fix_team
from alan.teams._base import TeamEvent
from alan.teams.dependency_sweep import load_dependency_sweep_status
from alan.teams.verification import (
    _JUDGE_PROMPT as _VERIFICATION_JUDGE_BASE_PROMPT,  # pyright: ignore[reportPrivateUsage]
)

engines_router = APIRouter(prefix="/api/engines")

_REPO_MISMATCH_DETAIL = "repo in body must match owner/repo in URL"
_ENGINE_IS_DEFAULT_DETAIL = "cannot delete the default engine"
_ENGINE_REFERENCED_DETAIL = "engine is referenced by non-terminal workspace(s)"
_NO_POOL_DETAIL = "no pool active for this engine"
_STRUCTURAL_PATCH_READONLY_DETAIL = (
    "engine is file-backed; structural fields are read-only here and must be "
    "edited in config/engines.d/<slug>.yaml"
)


def _structural_fields_diverge(old_cfg: EngineConfig, body: EngineConfig) -> bool:
    excluded = engine_config.PROJECTION_EXCLUDE
    return old_cfg.model_dump(exclude=excluded) != body.model_dump(exclude=excluded)


_TERMINAL_STATUSES: frozenset[workspace_mod.Status] = frozenset({
    workspace_mod.Status.ARCHIVED,
    workspace_mod.Status.COMPLETED,
})


class EngineListResponse(BaseModel):
    engines: list[EngineConfig]
    default_engine_key: str | None = None


def _credential_record_name(config: EngineConfig) -> str | None:
    if config.forge is None:
        return None
    return secrets_store.record_name(config.forge.repo.slug, secrets_store.TRANSPORT_PURPOSE)


def _secret_record_names() -> set[str]:
    return {meta.name for meta in secrets_store.list_secrets()}


async def _require_engine(identity: RepoIdentity) -> EngineConfig:
    cfg = await run_in_threadpool(engine_config.lookup_by_repo, identity)
    if cfg is None:
        raise HTTPException(status_code=404, detail=ENGINE_NOT_FOUND_DETAIL)
    return cfg


async def _require_credential_record(config: EngineConfig) -> None:
    record_name = _credential_record_name(config)
    if record_name is None:
        return
    existing = await run_in_threadpool(_secret_record_names)
    if record_name not in existing:
        raise HTTPException(status_code=422, detail=f"no secret record for {record_name!r}")


@engines_router.post(
    "",
    status_code=201,
    dependencies=[Depends(require_control_token)],
    responses={
        **GUARDED,
        409: {"description": "engine already registered"},
        422: {"description": "invalid config or missing credential record"},
    },
)
async def create_engine(
    body: EngineConfig, dispatcher: OptionalDispatcherDep, pubsub: PubSubDep
) -> EngineConfig:
    # prompt_overrides can only ever be populated through the dedicated, validated,
    # confirm-gated POST .../prompt-overrides endpoint below — a brand-new engine
    # always starts with none, regardless of what the create body carried.
    if body.prompt_overrides:
        body = body.model_copy(update={"prompt_overrides": {}})
    await _require_credential_record(body)
    try:
        await run_in_threadpool(engine_config.register_and_persist, body, validate_env=False)
    except DuplicateEngineError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (FlowGraphValidationError, PostureOrthogonalityError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if dispatcher is not None:
        await run_in_threadpool(dispatcher.register_pool, body)
    pubsub.publish_global(TeamEvent("engine_created", {"slug": body.repo.slug}))
    return body


def _engine_listing() -> EngineListResponse:
    return EngineListResponse(
        engines=engine_config.registered_engines(),
        default_engine_key=engine_config.canonical_scope_key(None).slug,
    )


@engines_router.get("")
async def list_engines_endpoint() -> EngineListResponse:
    return await run_in_threadpool(_engine_listing)


@engines_router.get(
    "/{owner}/{repo}",
    responses={404: {"description": ENGINE_NOT_FOUND_DETAIL}},
)
async def get_engine(owner: str, repo: str) -> EngineConfig:
    return await _require_engine(RepoIdentity(owner=owner, repo=repo))


@engines_router.get(
    "/{owner}/{repo}/status",
    responses={404: {"description": ENGINE_NOT_FOUND_DETAIL}},
)
async def get_engine_repo_status(
    owner: str,
    repo: str,
    queue: QueueDep,
    workspace_index: WorkspaceIndexDep,
    pubsub: PubSubDep,
) -> repo_status_mod.RepoStatus:
    identity = RepoIdentity(owner=owner, repo=repo)
    result = await run_in_threadpool(
        repo_status_mod.get_repo_status,
        identity,
        queue=queue,
        workspace_index=workspace_index,
        pubsub=pubsub,
    )
    if result is None:
        raise HTTPException(status_code=404, detail=ENGINE_NOT_FOUND_DETAIL)
    return result


async def _dispose_pool_or_error(dispatcher: Dispatcher, slug: str) -> None:
    try:
        await run_in_threadpool(dispatcher.dispose_pool, slug)
    except PoolBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PoolDisposeIncompleteError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


async def _validate_config_or_error(body: EngineConfig) -> None:
    try:
        await run_in_threadpool(_validate_engine_config, body, validate_env=False)
    except (FlowGraphValidationError, PostureOrthogonalityError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


async def _persist_updated_engine(body: EngineConfig) -> EngineConfig:
    # Flow/posture validity is already enforced by _validate_config_or_error
    # before this runs, so update_and_persist's own re-validation of the same
    # (immutable) body can never raise FlowGraphValidationError /
    # PostureOrthogonalityError here — only EngineNotFoundError is reachable.
    try:
        return await run_in_threadpool(engine_config.update_and_persist, body, validate_env=False)
    except EngineNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _pool_size_changed(old_cfg: EngineConfig | None, body: EngineConfig) -> bool:
    return old_cfg is not None and old_cfg.pool_size != body.pool_size


def _is_default_engine(default_cfg: EngineConfig | None, identity: RepoIdentity) -> bool:
    return default_cfg is not None and default_cfg.repo == identity


async def _resize_pool_if_needed(
    dispatcher: Dispatcher | None,
    identity: RepoIdentity,
    old_cfg: EngineConfig | None,
    body: EngineConfig,
    *,
    is_default: bool,
) -> bool:
    if is_default or dispatcher is None or not _pool_size_changed(old_cfg, body):
        return False
    await _dispose_pool_or_error(dispatcher, identity.slug)
    return True


@engines_router.patch(
    "/{owner}/{repo}",
    dependencies=[Depends(require_control_token)],
    responses={
        **GUARDED,
        404: {"description": ENGINE_NOT_FOUND_DETAIL},
        409: {
            "description": (
                "engine's pool has active leases and cannot be resized, or a "
                "file-backed engine's structural fields diverge from its config file"
            )
        },
        422: {"description": "invalid config, repo mismatch, or missing credential record"},
        500: GUARDED[500],
    },
)
async def update_engine(
    owner: str,
    repo: str,
    body: EngineConfig,
    dispatcher: OptionalDispatcherDep,
    pubsub: PubSubDep,
) -> EngineConfig:
    identity = RepoIdentity(owner=owner, repo=repo)
    if body.repo != identity:
        raise HTTPException(status_code=422, detail=_REPO_MISMATCH_DETAIL)
    await _require_credential_record(body)
    await _validate_config_or_error(body)
    default_cfg = await run_in_threadpool(engine_config.get_registered)
    old_cfg = await run_in_threadpool(engine_config.lookup_by_repo, identity)
    if (
        old_cfg is not None
        and engine_config.is_file_backed(identity)
        and _structural_fields_diverge(old_cfg, body)
    ):
        raise HTTPException(status_code=409, detail=_STRUCTURAL_PATCH_READONLY_DETAIL)
    resized = await _resize_pool_if_needed(
        dispatcher, identity, old_cfg, body, is_default=_is_default_engine(default_cfg, identity)
    )
    persisted = await _persist_updated_engine(body)
    if resized and dispatcher is not None:
        await run_in_threadpool(dispatcher.register_pool, persisted)
    pubsub.publish_global(TeamEvent("engine_updated", {"slug": persisted.repo.slug}))
    return persisted


class PostureChange(BaseModel):
    posture: Posture
    confirm: Literal[True]


@engines_router.post(
    "/{owner}/{repo}/posture",
    dependencies=[Depends(require_control_token)],
    responses={
        **GUARDED,
        404: {"description": ENGINE_NOT_FOUND_DETAIL},
        422: {"description": "invalid posture, confirm not true, or above the file ceiling"},
    },
)
async def set_engine_posture(
    owner: str, repo: str, body: PostureChange, pubsub: PubSubDep
) -> EngineConfig:
    identity = RepoIdentity(owner=owner, repo=repo)
    try:
        cfg = await run_in_threadpool(
            engine_config.set_posture_and_persist, identity, body.posture, source="api"
        )
    except EngineNotFoundError as exc:
        raise HTTPException(status_code=404, detail=ENGINE_NOT_FOUND_DETAIL) from exc
    except PostureCeilingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    pubsub.publish_global(TeamEvent("engine_posture_changed", {"slug": identity.slug}))
    return cfg


class DependencySweepToggle(BaseModel):
    enabled: bool


_DEP_SWEEP_ENABLE_BLOCKED_DETAIL = "engine-preflight finding blocks enabling the dependency sweep"


@engines_router.post(
    "/{owner}/{repo}/dependency-sweep",
    dependencies=[Depends(require_control_token)],
    responses={
        **GUARDED,
        404: {"description": ENGINE_NOT_FOUND_DETAIL},
        422: {"description": _DEP_SWEEP_ENABLE_BLOCKED_DETAIL},
    },
)
async def set_dependency_sweep_enabled(
    owner: str, repo: str, body: DependencySweepToggle, pubsub: PubSubDep
) -> EngineConfig:
    identity = RepoIdentity(owner=owner, repo=repo)
    cfg = await _require_engine(identity)
    if body.enabled:
        finding = await run_in_threadpool(check_dependency_sweep_enable, cfg)
        if finding is not None:
            raise HTTPException(status_code=422, detail=finding.model_dump())
    updated = cfg.model_copy(
        update={"dependency_sweep": cfg.dependency_sweep.model_copy(
            update={"enabled": body.enabled}
        )}
    )
    persisted = await run_in_threadpool(
        engine_config.update_and_persist, updated, validate_env=False
    )
    pubsub.publish_global(TeamEvent("dep_sweep_changed", {"slug": persisted.repo.slug}))
    return persisted


class DependencySweepStatusResponse(BaseModel):
    slug: str
    enabled: bool
    last_run_at: str | None
    next_run_at: str | None


@engines_router.get(
    "/{owner}/{repo}/dependency-sweep",
    responses={404: {"description": ENGINE_NOT_FOUND_DETAIL}},
)
async def get_dependency_sweep_status(owner: str, repo: str) -> DependencySweepStatusResponse:
    identity = RepoIdentity(owner=owner, repo=repo)
    cfg = await _require_engine(identity)
    forge = forge_target_for_workspace(identity.slug, cfg)
    status = await run_in_threadpool(load_dependency_sweep_status, forge)
    return DependencySweepStatusResponse(
        slug=identity.slug,
        enabled=cfg.dependency_sweep.enabled,
        last_run_at=status.last_run_at if status is not None else None,
        next_run_at=status.next_run_at if status is not None else None,
    )


_INVALID_PROMPT_ROLE_DETAIL = "team/role is not an overridable prompt"
_REQUIRES_CONFIRMATION_DETAIL = "requires_confirmation"

# The (team, role)'s raw pre-substitution base prompt text, as defined in code today.
# Only used to seed PromptOverride.base_fingerprint at save time (alan.prompt_templates
# stays pure and cannot import these — see its module docstring / import-linter layer).
_BASE_PROMPT_TEXT: dict[tuple[str, str], str] = {
    ("implementation", "implementer"): _implementation_team._SYSTEM_PROMPT,  # pyright: ignore[reportPrivateUsage]
    ("implementation_fix", "implementer"): (
        _implementation_fix_team._SYSTEM_PROMPT  # pyright: ignore[reportPrivateUsage]
    ),
    ("verification", "judge"): _VERIFICATION_JUDGE_BASE_PROMPT,
}


class PromptOverrideRequest(BaseModel):
    team: str
    role: str
    text: str
    confirm: bool = False


def _base_fingerprint(team: str, role: str) -> str:
    base_text = _BASE_PROMPT_TEXT[(team, role)]
    return hashlib.sha256(base_text.encode("utf-8")).hexdigest()


def _is_safety_critical_override(cfg: EngineConfig, team: str) -> bool:
    return team == "verification" or posture_rank(cfg.posture) >= posture_rank(Posture.self_merge)


def _reject_invalid_override(team: str, role: str, text: str) -> None:
    if not prompt_templates.is_overridable(team, role):
        raise HTTPException(status_code=422, detail=_INVALID_PROMPT_ROLE_DETAIL)
    missing_ph = prompt_templates.missing_placeholders(team, role, text)
    if missing_ph:
        raise HTTPException(
            status_code=422,
            detail={
                "detail": (
                    "override is missing required placeholder(s): "
                    + ", ".join(sorted(missing_ph))
                ),
                "missing": sorted(missing_ph),
            },
        )
    missing_mk = prompt_templates.missing_markers(team, role, text)
    if missing_mk:
        raise HTTPException(
            status_code=422,
            detail={
                "detail": (
                    "override is missing required marker(s): " + ", ".join(sorted(missing_mk))
                ),
                "missing": sorted(missing_mk),
            },
        )


@engines_router.post(
    "/{owner}/{repo}/prompt-overrides",
    dependencies=[Depends(require_control_token)],
    responses={
        **GUARDED,
        404: {"description": ENGINE_NOT_FOUND_DETAIL},
        409: {"description": "safety-critical override requires confirm=true"},
        422: {
            "description": (
                "team/role is not overridable, or override text is missing a required "
                "placeholder/marker"
            )
        },
    },
)
async def save_prompt_override(
    owner: str, repo: str, body: PromptOverrideRequest, pubsub: PubSubDep
) -> EngineConfig:
    identity = RepoIdentity(owner=owner, repo=repo)
    cfg = await _require_engine(identity)
    _reject_invalid_override(body.team, body.role, body.text)
    if _is_safety_critical_override(cfg, body.team) and not body.confirm:
        raise HTTPException(status_code=409, detail=_REQUIRES_CONFIRMATION_DETAIL)
    key = prompt_templates.override_key(body.team, body.role)
    override = PromptOverride(
        text=body.text, base_fingerprint=_base_fingerprint(body.team, body.role)
    )
    persisted = await run_in_threadpool(
        engine_config.apply_prompt_override_and_persist, identity, key, override
    )
    await run_in_threadpool(
        prompt_override_history.append_entry,
        identity.slug, body.team, body.role, body.text, override.base_fingerprint,
    )
    pubsub.publish_global(TeamEvent("engine_updated", {"slug": persisted.repo.slug}))
    return persisted


class PromptOverrideHistoryResponse(BaseModel):
    team: str
    role: str
    entries: list[prompt_override_history.HistoryEntry]
    drifted: bool


@engines_router.get(
    "/{owner}/{repo}/prompt-overrides/history",
    responses={
        404: {"description": ENGINE_NOT_FOUND_DETAIL},
        422: {"description": _INVALID_PROMPT_ROLE_DETAIL},
    },
)
async def get_prompt_override_history(
    owner: str, repo: str, team: str, role: str
) -> PromptOverrideHistoryResponse:
    identity = RepoIdentity(owner=owner, repo=repo)
    cfg = await _require_engine(identity)
    if not prompt_templates.is_overridable(team, role):
        raise HTTPException(status_code=422, detail=_INVALID_PROMPT_ROLE_DETAIL)
    entries = await run_in_threadpool(
        prompt_override_history.list_entries, identity.slug, team, role
    )
    override = cfg.prompt_overrides.get(prompt_templates.override_key(team, role))
    drifted = override is not None and prompt_templates.has_drifted(
        override, _base_fingerprint(team, role)
    )
    return PromptOverrideHistoryResponse(
        team=team, role=role, entries=list(reversed(entries)), drifted=drifted
    )


_NO_SUCH_VERSION_DETAIL = "no such prompt override history version"


class PromptOverrideRollbackRequest(BaseModel):
    team: str
    role: str
    version: int
    confirm: bool = False


@engines_router.post(
    "/{owner}/{repo}/prompt-overrides/rollback",
    dependencies=[Depends(require_control_token)],
    responses={
        **GUARDED,
        404: {"description": f"{ENGINE_NOT_FOUND_DETAIL}, or no such history version"},
        409: {"description": "safety-critical override requires confirm=true"},
        422: {"description": _INVALID_PROMPT_ROLE_DETAIL},
    },
)
async def rollback_prompt_override(
    owner: str, repo: str, body: PromptOverrideRollbackRequest, pubsub: PubSubDep
) -> EngineConfig:
    identity = RepoIdentity(owner=owner, repo=repo)
    cfg = await _require_engine(identity)
    if not prompt_templates.is_overridable(body.team, body.role):
        raise HTTPException(status_code=422, detail=_INVALID_PROMPT_ROLE_DETAIL)
    entry = await run_in_threadpool(
        prompt_override_history.get_entry, identity.slug, body.team, body.role, body.version
    )
    if entry is None:
        raise HTTPException(status_code=404, detail=_NO_SUCH_VERSION_DETAIL)
    _reject_invalid_override(body.team, body.role, entry.text)
    if _is_safety_critical_override(cfg, body.team) and not body.confirm:
        raise HTTPException(status_code=409, detail=_REQUIRES_CONFIRMATION_DETAIL)
    # Forward-write of the historical entry's own text + base_fingerprint — never
    # recomputed — so a rollback to a pre-drift version still reports as drifted
    # against today's base until the operator re-authors it against the new one.
    key = prompt_templates.override_key(body.team, body.role)
    restored = PromptOverride(text=entry.text, base_fingerprint=entry.base_fingerprint)
    persisted = await run_in_threadpool(
        engine_config.apply_prompt_override_and_persist, identity, key, restored
    )
    await run_in_threadpool(
        prompt_override_history.append_entry,
        identity.slug, body.team, body.role, entry.text, entry.base_fingerprint,
    )
    pubsub.publish_global(TeamEvent("engine_updated", {"slug": persisted.repo.slug}))
    return persisted


@engines_router.post(
    "/{owner}/{repo}/preflight",
    dependencies=[Depends(require_control_token)],
    responses={**GUARDED, 404: {"description": ENGINE_NOT_FOUND_DETAIL}},
)
async def preflight_engine(owner: str, repo: str, pubsub: PubSubDep) -> PreflightResult:
    identity = RepoIdentity(owner=owner, repo=repo)
    cfg = await _require_engine(identity)
    result = await run_in_threadpool(run_preflight, cfg)
    pubsub.publish_global(TeamEvent("preflight_completed", {"slug": identity.slug}))
    return result


@engines_router.get(
    "/{owner}/{repo}/preflight",
    responses={404: {"description": ENGINE_NOT_FOUND_DETAIL}},
)
async def get_engine_preflight(owner: str, repo: str) -> PreflightResult | None:
    identity = RepoIdentity(owner=owner, repo=repo)
    cfg = await _require_engine(identity)
    forge = forge_target_for_workspace(identity.slug, cfg)
    return await run_in_threadpool(load_last_preflight, forge)


_CONFIG_PROPOSAL_ABORTED_DETAIL = (
    "proposal pipeline aborted or produced an unrepairable result"
)
_MANUAL_SEED_REMEDY = (
    "seed the engine configuration from a stack preset manually in the engine "
    "configuration editor"
)


def _manual_seed_finding(exc: Exception) -> Finding:
    return Finding(
        check="config_proposal", status="fail", message=str(exc), remedy=_MANUAL_SEED_REMEDY
    )


class ConfigProposalRequest(BaseModel):
    base_preset: Literal["dotnet", "python", "node", "ios"] | None = None


@engines_router.post(
    "/{owner}/{repo}/config-proposal",
    dependencies=[Depends(require_control_token)],
    responses={
        **GUARDED,
        422: {"description": _CONFIG_PROPOSAL_ABORTED_DETAIL},
    },
)
async def propose_engine_config(
    owner: str, repo: str, body: ConfigProposalRequest
) -> engine_config_proposal.ConfigProposalResult:
    identity = RepoIdentity(owner=owner, repo=repo)
    try:
        return await run_in_threadpool(
            engine_config_proposal.propose_config,
            identity.slug,
            base_preset=body.base_preset,
        )
    except (
        engine_config_proposal.ProposalAbortedError,
        engine_config_proposal.ProposalUnrepairableError,
    ) as exc:
        raise HTTPException(
            status_code=422, detail=_manual_seed_finding(exc).model_dump()
        ) from exc


@engines_router.post(
    "/{owner}/{repo}/config-draft-validate",
    dependencies=[Depends(require_control_token)],
    responses={
        **GUARDED,
        422: {"description": _REPO_MISMATCH_DETAIL},
    },
)
async def validate_engine_config_draft(owner: str, repo: str, body: EngineConfig) -> Finding:
    identity = RepoIdentity(owner=owner, repo=repo)
    if body.repo != identity:
        raise HTTPException(status_code=422, detail=_REPO_MISMATCH_DETAIL)
    return await run_in_threadpool(engine_config_proposal.validate_draft, body)


def _project_for_engine(identity: RepoIdentity) -> project_mod.Project | None:
    for candidate in project_mod.list_projects():
        if candidate.engine_key == identity.slug:
            return candidate
    return None


def _project_context_for(identity: RepoIdentity) -> tuple[str, str]:
    project = _project_for_engine(identity)
    if project is None:
        return "", ""
    try:
        doc_text = project_doc_mod.doc_path(project).read_text(encoding="utf-8")
    except (OSError, project_doc_mod.ProjectDocError):
        doc_text = ""
    return project.title, doc_text


@engines_router.post(
    "/{owner}/{repo}/scaffold",
    dependencies=[Depends(require_control_token)],
    responses={
        **GUARDED,
        404: {"description": ENGINE_NOT_FOUND_DETAIL},
        422: {"description": "scaffold could not be opened"},
    },
)
async def scaffold_engine(owner: str, repo: str, pubsub: PubSubDep) -> OnboardingState:
    identity = RepoIdentity(owner=owner, repo=repo)
    cfg = await _require_engine(identity)
    title, doc = await run_in_threadpool(_project_context_for, identity)
    try:
        state = await run_in_threadpool(
            repo_scaffold.scaffold, cfg, project_title=title, project_doc=doc
        )
    except ScaffoldError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    pubsub.publish_global(TeamEvent("engine_scaffolded", {"slug": identity.slug}))
    return state


@engines_router.post(
    "/{owner}/{repo}/protect",
    dependencies=[Depends(require_control_token)],
    responses={
        **GUARDED,
        404: {"description": ENGINE_NOT_FOUND_DETAIL},
        422: {"description": "engine has no forge configured"},
    },
)
async def protect_engine(owner: str, repo: str, pubsub: PubSubDep) -> OnboardingState:
    identity = RepoIdentity(owner=owner, repo=repo)
    cfg = await _require_engine(identity)
    try:
        state = await run_in_threadpool(repo_scaffold.protect, cfg)
    except ScaffoldError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    pubsub.publish_global(TeamEvent("engine_protected", {"slug": identity.slug}))
    return state


class OnboardingReadiness(BaseModel):
    state: OnboardingState
    checklist: tuple[Finding, ...]
    overall: Literal["pass", "fail", "unknown"]


def _onboarding_readiness(cfg: EngineConfig) -> OnboardingReadiness:
    state = repo_scaffold.onboarding_status(cfg)
    checklist = repo_scaffold.build_checklist(cfg, state)
    return OnboardingReadiness(
        state=state, checklist=checklist, overall=repo_scaffold.overall_status(checklist)
    )


@engines_router.get(
    "/{owner}/{repo}/onboarding",
    responses={
        404: {"description": ENGINE_NOT_FOUND_DETAIL},
        422: {"description": "engine has no forge configured"},
    },
)
async def get_engine_onboarding(owner: str, repo: str) -> OnboardingReadiness:
    identity = RepoIdentity(owner=owner, repo=repo)
    cfg = await _require_engine(identity)
    try:
        return await run_in_threadpool(_onboarding_readiness, cfg)
    except ScaffoldError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _references_engine(ws: workspace_mod.Workspace, repo: RepoIdentity) -> bool:
    if ws.meta.engine_key == repo.slug:
        return True
    snapshot = ws.meta.engine_snapshot
    return snapshot is not None and snapshot.config is not None and snapshot.config.repo == repo


def _blocking_workspace_ids(repo: RepoIdentity, workspaces_root: Path) -> list[str]:
    return [
        ws.task_id
        for ws in workspace_mod.list_workspaces(root=workspaces_root)
        if ws.meta.status not in _TERMINAL_STATUSES and _references_engine(ws, repo)
    ]


class PinnedWorkResponse(BaseModel):
    count: int
    workspace_ids: list[str]


@engines_router.get(
    "/{owner}/{repo}/pinned-work",
    responses={404: {"description": ENGINE_NOT_FOUND_DETAIL}},
)
async def get_engine_pinned_work(
    owner: str, repo: str, workspaces_root: WorkspacesRootDep
) -> PinnedWorkResponse:
    identity = RepoIdentity(owner=owner, repo=repo)
    await _require_engine(identity)
    workspace_ids = await run_in_threadpool(_blocking_workspace_ids, identity, workspaces_root)
    return PinnedWorkResponse(count=len(workspace_ids), workspace_ids=workspace_ids)


async def _check_delete_blockers(
    identity: RepoIdentity, workspaces_root: Path, *, force: bool
) -> None:
    if force:
        return
    blocking = await run_in_threadpool(_blocking_workspace_ids, identity, workspaces_root)
    if blocking:
        raise HTTPException(
            status_code=409,
            detail={"detail": _ENGINE_REFERENCED_DETAIL, "blocking_workspace_ids": blocking},
        )


@engines_router.delete(
    "/{owner}/{repo}",
    status_code=204,
    dependencies=[Depends(require_control_token)],
    responses={
        **GUARDED,
        404: {"description": ENGINE_NOT_FOUND_DETAIL},
        409: {
            "description": (
                "engine is the default engine, or referenced by non-terminal workspace(s)"
            )
        },
        500: GUARDED[500],
    },
)
async def delete_engine(
    owner: str,
    repo: str,
    workspaces_root: WorkspacesRootDep,
    dispatcher: OptionalDispatcherDep,
    pubsub: PubSubDep,
    force: bool = False,
) -> Response:
    identity = RepoIdentity(owner=owner, repo=repo)
    await _require_engine(identity)
    default_cfg = await run_in_threadpool(engine_config.get_registered)
    if default_cfg is not None and default_cfg.repo == identity:
        raise HTTPException(status_code=409, detail=_ENGINE_IS_DEFAULT_DETAIL)
    await _check_delete_blockers(identity, workspaces_root, force=force)
    if dispatcher is not None:
        await _dispose_pool_or_error(dispatcher, identity.slug)
    removed = await run_in_threadpool(engine_config.unregister_and_persist, identity)
    if not removed:
        raise HTTPException(status_code=404, detail=ENGINE_NOT_FOUND_DETAIL)
    pubsub.publish_global(TeamEvent("engine_deleted", {"slug": identity.slug}))
    return Response(status_code=204)


class PoolStateResponse(BaseModel):
    slug: str
    size: int
    free_count: int
    leased_count: int
    warm: bool
    quiescing: bool
    disk_usage_bytes: int
    warmup_seconds: float | None = None


@engines_router.get(
    "/{owner}/{repo}/pool",
    responses={404: {"description": "no dispatcher, engine not registered, or no active pool"}},
)
async def get_engine_pool(
    owner: str, repo: str, dispatcher: OptionalDispatcherDep
) -> PoolStateResponse:
    if dispatcher is None:
        raise HTTPException(status_code=404, detail=_NO_POOL_DETAIL)
    identity = RepoIdentity(owner=owner, repo=repo)
    await _require_engine(identity)
    states = await run_in_threadpool(dispatcher.pool_states)
    state = states.get(identity.slug)
    if state is None:
        raise HTTPException(status_code=404, detail=_NO_POOL_DETAIL)
    warmup_seconds = await run_in_threadpool(
        engine_registry_store.read_warmup_seconds, identity.slug
    )
    return PoolStateResponse(
        slug=state.slug,
        size=state.size,
        free_count=state.free_count,
        leased_count=state.leased_count,
        warm=state.warm,
        quiescing=state.quiescing,
        disk_usage_bytes=state.disk_usage_bytes,
        warmup_seconds=warmup_seconds,
    )
