from __future__ import annotations

import itertools
import json
import logging
import math
import threading
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, TypeAlias, TypeVar, cast

from pydantic import BaseModel, ValidationError

from alan import delivery_stats as delivery_stats_mod
from alan import engine_config, secrets_store, store
from alan import project as project_mod
from alan.branch_protection_compat import GH_API_TIMEOUT_S
from alan.engine_config import EngineConfig, ForgeTarget, RepoIdentity, forge_target_for_workspace
from alan.events import PubSub
from alan.forge import RowsRead, active_forge
from alan.health import HealthVerdict, is_stale, load_health_state
from alan.queue import Queue
from alan.teams import _pr_audit_log, _pr_state_paths
from alan.teams._base import TeamEvent
from alan.teams.deploy_verify import (
    _AUDIT_FILE as _DEPLOY_AUDIT_FILE,  # pyright: ignore[reportPrivateUsage]
)
from alan.workspace_index import WorkspaceIndex, WorkspaceRow
from alan.workspace_size import SizeClass

logger = logging.getLogger(__name__)

_JsonDict: TypeAlias = dict[str, Any]


FORGE_STATUS_TTL_S = 600.0

_CACHE_FILENAME = "repo_status_forge.json"
_PR_LIST_LIMIT = 100
_CI_FAILURE_CONCLUSIONS: frozenset[str] = frozenset(
    {"failure", "cancelled", "timed_out", "startup_failure"}
)
_DEPLOY_CONFIRMED_ACTIONS: frozenset[str] = frozenset({"deploy_confirmed", "dry_run_deploy"})
_DEPLOY_ESCALATE_ACTIONS: frozenset[str] = frozenset({"deploy_escalate", "escalate_no_merged_at"})
_DEPLOY_RELEVANT_ACTIONS: frozenset[str] = _DEPLOY_CONFIRMED_ACTIONS | _DEPLOY_ESCALATE_ACTIONS
_NO_FORGE_DETAIL = "engine has no forge configured"
_COST_CAPTION = (
    "every workspace attributable to this repository (live and archived), "
    "all six model-spend categories"
)
_DELIVERY_TIME_CAPTION = (
    "median active working time for delivered workspaces (completed or archived, "
    "matched to a merged PR), by size; archived history is included so automatic "
    "archiving never erodes the sample"
)

ProvenanceStatus = Literal["fresh", "stale", "refreshing", "unknown"]


class Provenance(BaseModel, frozen=True):
    status: ProvenanceStatus
    detail: str


CountedOver: TypeAlias = Literal["main_ci_workflow", "all_checks"]


class MainCiField(BaseModel, frozen=True):
    provenance: Provenance
    green_count: int | None = None
    red_count: int | None = None
    counted_over: CountedOver | None = None


class OpenPrsField(BaseModel, frozen=True):
    provenance: Provenance
    total: int | None = None
    authored_by_alan: int | None = None


class CostField(BaseModel, frozen=True):
    provenance: Provenance
    total_cost_usd: float | None = None
    unknown_count: int = 0


class SizeStat(BaseModel, frozen=True):
    size: SizeClass
    median_active_seconds: float | None = None
    delivered_count: int = 0


class DeliveryTimeField(BaseModel, frozen=True):
    provenance: Provenance
    by_size: list[SizeStat]


class LastDeployField(BaseModel, frozen=True):
    provenance: Provenance
    outcome: Literal["confirmed", "escalate"] | None = None
    pr_number: int | None = None
    at: str | None = None


class HealthField(BaseModel, frozen=True):
    provenance: Provenance
    finding_count: int | None = None
    verdict: HealthVerdict | None = None


class ForgeStatus(BaseModel, frozen=True):
    fetched_at: str
    main_ci: MainCiField
    open_prs: OpenPrsField


class RepoStatus(BaseModel, frozen=True):
    slug: str
    is_default: bool
    main_ci: MainCiField
    open_prs: OpenPrsField
    cost: CostField
    delivery_time: DeliveryTimeField
    last_deploy: LastDeployField
    health: HealthField


def unknown_provenance(detail: str) -> Provenance:
    return Provenance(status="unknown", detail=detail)


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _forge_cache_path(forge: ForgeTarget) -> Path:
    return _pr_state_paths._engine_state_dir(forge) / _CACHE_FILENAME  # pyright: ignore[reportPrivateUsage]


def _read_forge_cache(forge: ForgeTarget) -> ForgeStatus | None:
    try:
        raw = _forge_cache_path(forge).read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        return ForgeStatus.model_validate_json(raw)
    except ValidationError:
        return None


def _write_forge_cache(forge: ForgeTarget, status: ForgeStatus) -> None:
    path = _forge_cache_path(forge)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        store.write_text_atomic(path, status.model_dump_json())
    except OSError:
        logger.warning("repo_status: failed to write forge cache for %s", forge.repo.slug)


def _cache_age_s(cached: ForgeStatus) -> float:
    try:
        fetched_at = datetime.fromisoformat(cached.fetched_at)
    except ValueError:
        return math.inf
    if fetched_at.tzinfo is None:
        return math.inf
    return (datetime.now(UTC) - fetched_at).total_seconds()


_ForgeField = TypeVar("_ForgeField", MainCiField, OpenPrsField)


def _mark_stale(field: _ForgeField) -> _ForgeField:
    if field.provenance.status != "fresh":
        return field
    return field.model_copy(
        update={"provenance": field.provenance.model_copy(update={"status": "stale"})}
    )


REFRESH_EXECUTOR = ThreadPoolExecutor(max_workers=2)
_INFLIGHT_LOCK = threading.Lock()
_INFLIGHT_SLUGS: set[str] = set()


def begin_refresh(slug: str) -> bool:
    with _INFLIGHT_LOCK:
        if slug in _INFLIGHT_SLUGS:
            return False
        _INFLIGHT_SLUGS.add(slug)
        return True


def end_refresh(slug: str) -> None:
    with _INFLIGHT_LOCK:
        _INFLIGHT_SLUGS.discard(slug)


_CheckRunScope = tuple[list[_JsonDict], str, CountedOver]


def _head_sha(parsed: list[_JsonDict]) -> str | None:
    for run in parsed:
        sha = run.get("head_sha")
        if isinstance(sha, str) and sha:
            return sha
    return None


def _workflow_suite_id(forge: ForgeTarget, workflow: str, head_sha: str) -> int | None:
    read = active_forge().latest_workflow_run(
        workflow, forge_target=forge, timeout=GH_API_TIMEOUT_S, head_sha=head_sha
    )
    if read.outcome != "ok" or not read.rows:
        return None
    suite_id = read.rows[0].get("check_suite_id")
    return suite_id if isinstance(suite_id, int) else None


def _all_checks_scope(parsed: list[_JsonDict], reason: str) -> _CheckRunScope:
    detail = f"{reason} — counted over all {len(parsed)} default-branch check-run(s)"
    return parsed, detail, "all_checks"


def _suite_scope(
    parsed: list[_JsonDict], suite_id: int, workflow: str, head_sha: str
) -> _CheckRunScope:
    scoped = [r for r in parsed if _check_suite_id(r) == suite_id]
    detail = f"counted over the {len(scoped)} check(s) of the {workflow} run for {head_sha[:7]}"
    return scoped, detail, "main_ci_workflow"


def _check_suite_id(run: _JsonDict) -> int | None:
    suite = run.get("check_suite")
    if not isinstance(suite, dict):
        return None
    suite_id = cast(_JsonDict, suite).get("id")
    return suite_id if isinstance(suite_id, int) else None


def _scope_check_runs(
    parsed: list[_JsonDict], forge: ForgeTarget, workflow: str | None
) -> _CheckRunScope:
    if workflow is None:
        return _all_checks_scope(parsed, "no ci.main_ci_workflow declared for this engine")
    head_sha = _head_sha(parsed)
    if head_sha is None:
        return _all_checks_scope(parsed, "the default branch's check-runs carry no head SHA")
    suite_id = _workflow_suite_id(forge, workflow, head_sha)
    if suite_id is None:
        return _all_checks_scope(parsed, f"no {workflow} run found for this commit")
    return _suite_scope(parsed, suite_id, workflow, head_sha)


def _count_conclusions(scoped: list[_JsonDict]) -> tuple[int, int]:
    green = sum(1 for r in scoped if r.get("conclusion") == "success")
    red = sum(1 for r in scoped if r.get("conclusion") in _CI_FAILURE_CONCLUSIONS)
    return green, red


def _summarize_check_runs(
    runs: Sequence[_JsonDict], forge: ForgeTarget, workflow: str | None
) -> MainCiField:
    scoped, detail, counted_over = _scope_check_runs(list(runs), forge, workflow)
    green, red = _count_conclusions(scoped)
    return MainCiField(
        provenance=Provenance(status="fresh", detail=detail),
        green_count=green,
        red_count=red,
        counted_over=counted_over,
    )


def _fetch_main_ci(forge: ForgeTarget, *, main_ci_workflow: str | None) -> MainCiField:
    port = active_forge()
    branch = port.default_branch(forge_target=forge, timeout=GH_API_TIMEOUT_S)
    if branch is None:
        return MainCiField(
            provenance=unknown_provenance("could not read the repo's default branch")
        )
    checks = port.commit_check_runs(branch, forge_target=forge, timeout=GH_API_TIMEOUT_S)
    if checks.outcome != "ok":
        return MainCiField(
            provenance=unknown_provenance("could not read check-runs for the default branch")
        )
    if checks.rows is None:
        return MainCiField(
            provenance=unknown_provenance("check-runs response was missing a check_runs array")
        )
    return _summarize_check_runs(checks.rows, forge, main_ci_workflow)


def _pr_count(read: RowsRead) -> int | None:
    return len(read.rows) if read.outcome == "ok" and read.rows is not None else None


def _fetch_open_prs(forge: ForgeTarget) -> OpenPrsField:
    port = active_forge()
    total = _pr_count(
        port.open_prs(forge_target=forge, limit=_PR_LIST_LIMIT, timeout=GH_API_TIMEOUT_S)
    )
    if total is None:
        return OpenPrsField(provenance=unknown_provenance("gh pr list failed"))
    mine = _pr_count(
        port.prs_by_author(
            "@me", ("number",), forge_target=forge, state="open",
            limit=_PR_LIST_LIMIT, timeout=GH_API_TIMEOUT_S,
        )
    )
    if mine is None:
        return OpenPrsField(provenance=unknown_provenance("gh pr list --author @me failed"))
    return OpenPrsField(
        provenance=Provenance(status="fresh", detail="from gh pr list"),
        total=total,
        authored_by_alan=mine,
    )


def refresh_forge_status(forge: ForgeTarget, *, main_ci_workflow: str | None) -> ForgeStatus:
    if secrets_store.transport_credential(forge) is None:
        unknown = unknown_provenance("no credential available for this repo's forge")
        return ForgeStatus(
            fetched_at=now_iso(),
            main_ci=MainCiField(provenance=unknown),
            open_prs=OpenPrsField(provenance=unknown),
        )
    return ForgeStatus(
        fetched_at=now_iso(),
        main_ci=_fetch_main_ci(forge, main_ci_workflow=main_ci_workflow),
        open_prs=_fetch_open_prs(forge),
    )


def _do_refresh_and_publish(
    forge: ForgeTarget, pubsub: PubSub, main_ci_workflow: str | None
) -> None:
    try:
        status = refresh_forge_status(forge, main_ci_workflow=main_ci_workflow)
        _write_forge_cache(forge, status)
        pubsub.publish_global(TeamEvent("repo_status_refreshed", {"slug": forge.repo.slug}))
    finally:
        end_refresh(forge.repo.slug)


def schedule_refresh(
    forge: ForgeTarget, pubsub: PubSub, *, main_ci_workflow: str | None
) -> bool:
    if not begin_refresh(forge.repo.slug):
        return False
    REFRESH_EXECUTOR.submit(_do_refresh_and_publish, forge, pubsub, main_ci_workflow)
    return True


def _forge_fields(
    forge: ForgeTarget, *, pubsub: PubSub, main_ci_workflow: str | None
) -> tuple[MainCiField, OpenPrsField]:
    cached = _read_forge_cache(forge)
    if cached is None:
        schedule_refresh(forge, pubsub, main_ci_workflow=main_ci_workflow)
        refreshing = Provenance(
            status="refreshing", detail="no cached data yet — refresh in progress"
        )
        return MainCiField(provenance=refreshing), OpenPrsField(provenance=refreshing)
    if _cache_age_s(cached) >= FORGE_STATUS_TTL_S:
        schedule_refresh(forge, pubsub, main_ci_workflow=main_ci_workflow)
        return _mark_stale(cached.main_ci), _mark_stale(cached.open_prs)
    return cached.main_ci, cached.open_prs


def _scoped_rows(
    identity: RepoIdentity, workspace_index: WorkspaceIndex
) -> tuple[list[WorkspaceRow], list[WorkspaceRow], set[str]]:
    project_ids = {
        p.id
        for p in project_mod.list_projects()
        if engine_config.same_repo_scope(p.engine_key, identity.slug)
    }
    rows, archived = workspace_index.validated_rows()
    return rows, archived, project_ids


def _fold_known_costs(scoped: list[WorkspaceRow]) -> tuple[float | None, int]:
    if not scoped:
        return None, 0
    total = 0.0
    unknown_count = 0
    for row in scoped:
        if row.total_cost_usd is None:
            unknown_count += 1
        else:
            total += row.total_cost_usd
    return total, unknown_count


def _cost_field(
    rows: list[WorkspaceRow],
    archived: list[WorkspaceRow],
    project_ids: set[str],
    slug: str,
) -> CostField:
    target = engine_config.canonical_scope_key(slug)
    scoped = [
        row
        for row in itertools.chain(rows, archived)
        if delivery_stats_mod.in_repo_scope(row, slug, project_ids, target=target)
    ]
    total_cost_usd, unknown_count = _fold_known_costs(scoped)
    return CostField(
        provenance=Provenance(status="fresh", detail=_COST_CAPTION),
        total_cost_usd=total_cost_usd,
        unknown_count=unknown_count,
    )


def _delivery_time_field(
    rows: list[WorkspaceRow],
    archived: list[WorkspaceRow],
    project_ids: set[str],
    slug: str,
) -> DeliveryTimeField:
    delivered = delivery_stats_mod.delivered_rows_for_repo(rows, archived, slug, project_ids)
    by_size_stats = delivery_stats_mod.delivery_time_by_size(delivered)
    by_size = [
        SizeStat(
            size=size,
            median_active_seconds=by_size_stats[size][0],
            delivered_count=by_size_stats[size][1],
        )
        for size in SizeClass
    ]
    return DeliveryTimeField(
        provenance=Provenance(status="fresh", detail=_DELIVERY_TIME_CAPTION), by_size=by_size
    )


def _parse_relevant_audit_line(line: str) -> _JsonDict | None:
    stripped = line.strip()
    if not stripped:
        return None
    try:
        parsed: object = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    entry = cast(_JsonDict, parsed)
    return entry if entry.get("action") in _DEPLOY_RELEVANT_ACTIONS else None


def _tail_relevant_audit_entry(path: Path) -> _JsonDict | None:
    latest: _JsonDict | None = None
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                entry = _parse_relevant_audit_line(line)
                if entry is not None:
                    latest = entry
    except OSError:
        return None
    return latest


def _last_deploy_field(*, is_default: bool) -> LastDeployField:
    if not is_default:
        return LastDeployField(
            provenance=unknown_provenance(
                "last deploy is only known for the default engine (deploys are alan-global)"
            )
        )
    path = _pr_audit_log.audit_dir() / _DEPLOY_AUDIT_FILE
    entry = _tail_relevant_audit_entry(path)
    if entry is None:
        return LastDeployField(
            provenance=unknown_provenance("no deploy audit entries recorded yet")
        )
    outcome: Literal["confirmed", "escalate"] = (
        "confirmed" if entry.get("action") in _DEPLOY_CONFIRMED_ACTIONS else "escalate"
    )
    pr_number = entry.get("pr_number")
    at = entry.get("timestamp_utc")
    return LastDeployField(
        provenance=Provenance(status="fresh", detail="tailed from deploy_verify_audit.jsonl"),
        outcome=outcome,
        pr_number=pr_number if isinstance(pr_number, int) else None,
        at=at if isinstance(at, str) else None,
    )


def _health_field(identity: RepoIdentity, *, queue: Queue, is_default: bool) -> HealthField:
    state = load_health_state(queue)
    if state is None or is_stale(state):
        return HealthField(provenance=unknown_provenance("no fresh health computation available"))
    scoped = [
        f for f in state.findings if engine_config.same_repo_scope(f.engine_key, identity.slug)
    ]
    return HealthField(
        provenance=Provenance(status="fresh", detail="engine-filtered finding count"),
        finding_count=len(scoped),
        verdict=state.verdict if is_default else None,
    )


def _resolve_engine(identity: RepoIdentity) -> tuple[EngineConfig, bool] | None:
    cfg = engine_config.lookup_by_repo(identity)
    if cfg is not None:
        default_cfg = engine_config.get_registered()
        return cfg, default_cfg is not None and default_cfg.repo == identity
    fallback = engine_config.get_registered() or engine_config.default_engine_config()
    return (fallback, True) if fallback.repo == identity else None


def _all_unknown_response(identity: RepoIdentity, *, is_default: bool) -> RepoStatus:
    unknown = unknown_provenance(_NO_FORGE_DETAIL)
    return RepoStatus(
        slug=identity.slug,
        is_default=is_default,
        main_ci=MainCiField(provenance=unknown),
        open_prs=OpenPrsField(provenance=unknown),
        cost=CostField(provenance=unknown),
        delivery_time=DeliveryTimeField(provenance=unknown, by_size=[]),
        last_deploy=LastDeployField(provenance=unknown),
        health=HealthField(provenance=unknown),
    )


def get_repo_status(
    identity: RepoIdentity,
    *,
    queue: Queue,
    workspace_index: WorkspaceIndex,
    pubsub: PubSub,
) -> RepoStatus | None:
    resolved = _resolve_engine(identity)
    if resolved is None:
        return None
    cfg, is_default = resolved
    if cfg.forge is None:
        return _all_unknown_response(identity, is_default=is_default)
    forge = forge_target_for_workspace(identity.slug, cfg)
    main_ci, open_prs = _forge_fields(
        forge,
        pubsub=pubsub,
        main_ci_workflow=None if cfg.ci is None else cfg.ci.main_ci_workflow,
    )
    rows, archived, project_ids = _scoped_rows(identity, workspace_index)
    return RepoStatus(
        slug=identity.slug,
        is_default=is_default,
        main_ci=main_ci,
        open_prs=open_prs,
        cost=_cost_field(rows, archived, project_ids, identity.slug),
        delivery_time=_delivery_time_field(rows, archived, project_ids, identity.slug),
        last_deploy=_last_deploy_field(is_default=is_default),
        health=_health_field(identity, queue=queue, is_default=is_default),
    )
