from __future__ import annotations

import base64
import json
import logging
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import BaseModel, ValidationError

from alan import secrets_store, store
from alan.branch_protection_compat import GH_API_TIMEOUT_S
from alan.engine_config import ForgeTarget
from alan.events import PubSub
from alan.forge import active_forge
from alan.repo_status import (
    REFRESH_EXECUTOR,
    Provenance,
    begin_refresh,
    end_refresh,
    now_iso,
    unknown_provenance,
)
from alan.teams._base import TeamEvent
from alan.teams._pr_state_paths import (
    _engine_state_dir as _engine_state_dir,  # pyright: ignore[reportPrivateUsage]
)

logger = logging.getLogger(__name__)

_CACHE_FILENAME = "recurring_status_forge.json"
RECURRING_FORGE_TTL_S = 900.0
_INFLIGHT_KEY_PREFIX = "recurring:"
_DEPENDABOT_PATHS: tuple[str, ...] = (".github/dependabot.yml", ".github/dependabot.yaml")
_ON_KEYS: tuple[object, ...] = ("on", True)
_DictStrAny = dict[str, Any]
_ListAny = list[Any]


class WorkflowSchedule(BaseModel, frozen=True):
    workflow_id: int
    name: str
    manual: bool
    cron_entries: tuple[str, ...] = ()
    last_run_provenance: Provenance
    last_run_at: str | None = None
    last_run_succeeded: bool | None = None
    last_run_outcome: str | None = None
    last_run_duration_seconds: float | None = None
    last_run_status: str | None = None
    last_run_conclusion: str | None = None
    last_run_url: str | None = None
    last_run_confirmed_absent: bool = False


class DependencyEcosystemUpdate(BaseModel, frozen=True):
    ecosystem: str
    schedule_interval: str | None = None
    last_pr_provenance: Provenance
    last_pr_at: str | None = None
    last_pr_url: str | None = None


class RecurringForgeStatus(BaseModel, frozen=True):
    fetched_at: str
    workflows: tuple[WorkflowSchedule, ...] = ()
    workflows_provenance: Provenance
    dependency_updates: tuple[DependencyEcosystemUpdate, ...] = ()
    dependency_updates_provenance: Provenance


def _decode_contents(payload: str) -> str | None:
    try:
        data: object = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    body = cast(_DictStrAny, data)
    content, encoding = body.get("content"), body.get("encoding")
    if not isinstance(content, str) or encoding != "base64":
        return None
    try:
        return base64.b64decode(content).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


def _fetch_file_text_or_404(
    forge: ForgeTarget, path: str, *, ref: str
) -> tuple[str | None, bool]:
    read = active_forge().repo_file(path, forge_target=forge, timeout=GH_API_TIMEOUT_S, ref=ref)
    if read.outcome == "absent":
        return None, True
    if read.outcome != "ok":
        return None, False
    return _decode_contents(read.text or ""), False


def _fetch_file_text(forge: ForgeTarget, path: str, *, ref: str) -> str | None:
    text, _is_404 = _fetch_file_text_or_404(forge, path, ref=ref)
    return text


def _default_branch(forge: ForgeTarget) -> str | None:
    return active_forge().default_branch(forge_target=forge, timeout=GH_API_TIMEOUT_S)


# ── scheduled CI workflows ───────────────────────────────────────────────


def _list_workflow_files(forge: ForgeTarget) -> list[dict[str, Any]] | None:
    read = active_forge().workflows(forge_target=forge, timeout=GH_API_TIMEOUT_S)
    if read.outcome != "ok" or read.rows is None:
        return None
    return list(read.rows)


def _parse_yaml_dict(text: str) -> dict[str, Any] | None:
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    return cast(_DictStrAny, parsed) if isinstance(parsed, dict) else None


def _on_section(doc: dict[str, Any]) -> Any:
    for key, value in doc.items():
        if key in _ON_KEYS:
            return value
    return None


def _schedule_crons(on_section: Any) -> list[str]:
    if not isinstance(on_section, dict):
        return []
    section = cast(_DictStrAny, on_section)
    schedule = section.get("schedule")
    if not isinstance(schedule, list):
        return []
    schedule_list = cast(_ListAny, schedule)  # type: ignore[redundant-cast]
    crons: list[str] = []
    for raw_entry in schedule_list:
        if not isinstance(raw_entry, dict):
            continue
        cron = cast(_DictStrAny, raw_entry).get("cron")
        if isinstance(cron, str) and cron.strip():
            crons.append(cron.strip())
    return crons


def _trigger_keys(on_section: Any) -> frozenset[str]:
    if isinstance(on_section, dict):
        return frozenset(str(k) for k in cast(_DictStrAny, on_section))
    if isinstance(on_section, list):
        on_list = cast(_ListAny, on_section)  # type: ignore[redundant-cast]
        return frozenset(str(k) for k in on_list)
    if isinstance(on_section, str):
        return frozenset({on_section})
    return frozenset()


_DISPATCH_ONLY_TRIGGERS: frozenset[str] = frozenset({"workflow_dispatch"})


def _is_dispatch_only(on_section: Any) -> bool:
    return _trigger_keys(on_section) == _DISPATCH_ONLY_TRIGGERS


def _fetch_latest_run(forge: ForgeTarget, workflow_id: int) -> tuple[dict[str, Any] | None, bool]:
    read = active_forge().latest_workflow_run(
        str(workflow_id), forge_target=forge, timeout=GH_API_TIMEOUT_S
    )
    if read.outcome != "ok" or read.rows is None:
        return None, False
    if not read.rows:
        return None, True
    return read.rows[0], False


def _run_provenance(run: dict[str, Any] | None) -> Provenance:
    if run is None:
        return unknown_provenance("no run history could be read for this workflow")
    return Provenance(status="fresh", detail="most recent run from gh actions workflow-runs API")


def _run_started_at(run: dict[str, Any] | None) -> str | None:
    if run is None:
        return None
    started = run.get("run_started_at") or run.get("created_at")
    return started if isinstance(started, str) else None


def _run_succeeded(run: dict[str, Any] | None) -> bool | None:
    if run is None or run.get("status") != "completed":
        return None
    conclusion = run.get("conclusion")
    return conclusion == "success" if isinstance(conclusion, str) else None


def _run_outcome(run: dict[str, Any] | None) -> str | None:
    if run is None or run.get("status") != "completed":
        return None
    conclusion = run.get("conclusion")
    return conclusion if isinstance(conclusion, str) else None


def _run_status(run: dict[str, Any] | None) -> str | None:
    if run is None:
        return None
    status = run.get("status")
    return status if isinstance(status, str) else None


def _run_html_url(run: dict[str, Any] | None) -> str | None:
    if run is None:
        return None
    url = run.get("html_url")
    return url if isinstance(url, str) else None


def _normalize_z(value: str) -> str:
    return value[:-1] + "+00:00" if value.endswith("Z") else value


def _run_duration_seconds(run: dict[str, Any] | None) -> float | None:
    if run is None or run.get("status") != "completed":
        return None
    started, updated = run.get("run_started_at"), run.get("updated_at")
    if not isinstance(started, str) or not isinstance(updated, str):
        return None
    try:
        start_dt = datetime.fromisoformat(_normalize_z(started))
        end_dt = datetime.fromisoformat(_normalize_z(updated))
    except ValueError:
        return None
    seconds = (end_dt - start_dt).total_seconds()
    return seconds if seconds >= 0 else None


def _workflow_identity(wf: dict[str, Any]) -> tuple[int, str, str] | None:
    workflow_id, path, name = wf.get("id"), wf.get("path"), wf.get("name")
    if isinstance(workflow_id, int) and isinstance(path, str) and isinstance(name, str):
        return workflow_id, path, name
    return None


def _workflow_triggers(
    forge: ForgeTarget, path: str, *, branch: str
) -> tuple[list[str], bool] | None:
    text = _fetch_file_text(forge, path, ref=branch)
    doc = _parse_yaml_dict(text) if text is not None else None
    if doc is None:
        return None
    on_section = _on_section(doc)
    crons = _schedule_crons(on_section)
    manual = not crons and _is_dispatch_only(on_section)
    return (crons, manual) if crons or manual else None


def _workflow_schedule(
    forge: ForgeTarget, wf: dict[str, Any], *, branch: str
) -> WorkflowSchedule | None:
    identity = _workflow_identity(wf)
    if identity is None:
        return None
    workflow_id, path, name = identity
    triggers = _workflow_triggers(forge, path, branch=branch)
    if triggers is None:
        return None
    crons, manual = triggers
    run, confirmed_no_runs = _fetch_latest_run(forge, workflow_id)
    outcome = _run_outcome(run)
    return WorkflowSchedule(
        workflow_id=workflow_id,
        name=name,
        manual=manual,
        cron_entries=tuple(crons),
        last_run_provenance=_run_provenance(run),
        last_run_at=_run_started_at(run),
        last_run_succeeded=_run_succeeded(run),
        last_run_outcome=outcome,
        last_run_duration_seconds=_run_duration_seconds(run),
        last_run_status=_run_status(run),
        last_run_conclusion=outcome,
        last_run_url=_run_html_url(run),
        last_run_confirmed_absent=confirmed_no_runs,
    )


def _scheduled_ci_workflows(
    forge: ForgeTarget, *, branch: str
) -> tuple[tuple[WorkflowSchedule, ...], Provenance]:
    files = _list_workflow_files(forge)
    if files is None:
        return (), unknown_provenance("could not list this repo's workflow configuration")
    schedules = [
        s
        for s in (_workflow_schedule(forge, wf, branch=branch) for wf in files)
        if s is not None
    ]
    return tuple(schedules), Provenance(status="fresh", detail="from gh actions workflow API")


# ── Dependabot per-ecosystem updates ─────────────────────────────────────


def _schedule_interval(entry: dict[str, Any]) -> str | None:
    schedule = entry.get("schedule")
    if not isinstance(schedule, dict):
        return None
    interval = cast(_DictStrAny, schedule).get("interval")
    return interval if isinstance(interval, str) else None


def _parse_dependabot_updates(text: str) -> list[tuple[str, str | None]] | None:
    doc = _parse_yaml_dict(text)
    if doc is None:
        return None
    updates = doc.get("updates")
    if not isinstance(updates, list):
        return []
    update_list = cast(_ListAny, updates)  # type: ignore[redundant-cast]
    result: list[tuple[str, str | None]] = []
    for raw_entry in update_list:
        if not isinstance(raw_entry, dict):
            continue
        entry = cast(_DictStrAny, raw_entry)
        ecosystem = entry.get("package-ecosystem")
        if isinstance(ecosystem, str) and ecosystem:
            result.append((ecosystem, _schedule_interval(entry)))
    return result


def _fetch_dependabot_text(forge: ForgeTarget, *, branch: str) -> tuple[str | None, bool]:
    for path in _DEPENDABOT_PATHS:
        text, is_404 = _fetch_file_text_or_404(forge, path, ref=branch)
        if text is not None:
            return text, False
        if not is_404:
            return None, False
    return None, True


def _latest_dependabot_pr(forge: ForgeTarget) -> tuple[str | None, str | None]:
    read = active_forge().prs_by_author(
        "app/dependabot", ("createdAt", "url"),
        forge_target=forge, state="all", limit=1, timeout=GH_API_TIMEOUT_S,
    )
    if read.outcome != "ok" or not read.rows:
        return None, None
    created, url = read.rows[0].get("createdAt"), read.rows[0].get("url")
    return (
        created if isinstance(created, str) else None,
        url if isinstance(url, str) else None,
    )


def _dependency_ecosystem_updates(
    forge: ForgeTarget, *, branch: str
) -> tuple[tuple[DependencyEcosystemUpdate, ...], Provenance]:
    text, confirmed_absent = _fetch_dependabot_text(forge, branch=branch)
    if text is None:
        if confirmed_absent:
            return (), Provenance(status="fresh", detail="no dependabot.yml on the default branch")
        return (), unknown_provenance("could not read this repo's dependabot configuration")
    entries = _parse_dependabot_updates(text)
    if entries is None:
        return (), unknown_provenance("dependabot.yml could not be parsed")
    if not entries:
        return (), Provenance(status="fresh", detail="dependabot.yml declares no updates")
    last_pr_at, last_pr_url = _latest_dependabot_pr(forge)
    pr_provenance = (
        Provenance(
            status="fresh",
            detail=(
                "approximated from the most recent Dependabot pull request across all "
                "ecosystems in this repo"
            ),
        )
        if last_pr_at is not None
        else unknown_provenance("no Dependabot pull request found for this repository")
    )
    updates = tuple(
        DependencyEcosystemUpdate(
            ecosystem=ecosystem,
            schedule_interval=interval,
            last_pr_provenance=pr_provenance,
            last_pr_at=last_pr_at,
            last_pr_url=last_pr_url,
        )
        for ecosystem, interval in entries
    )
    return updates, Provenance(status="fresh", detail="from this repo's dependabot.yml")


# ── assembly + cache/refresh (mirrors alan.repo_status's cached-forge contract) ──


def _unknown_forge_status(reason: str) -> RecurringForgeStatus:
    unknown = unknown_provenance(reason)
    return RecurringForgeStatus(
        fetched_at=now_iso(), workflows_provenance=unknown, dependency_updates_provenance=unknown
    )


def refresh_recurring_forge_status(forge: ForgeTarget) -> RecurringForgeStatus:
    if secrets_store.transport_credential(forge) is None:
        return _unknown_forge_status("no credential available for this repo's forge")
    branch = _default_branch(forge)
    if branch is None:
        return _unknown_forge_status("could not read the repo's default branch")
    workflows, workflows_provenance = _scheduled_ci_workflows(forge, branch=branch)
    updates, updates_provenance = _dependency_ecosystem_updates(forge, branch=branch)
    return RecurringForgeStatus(
        fetched_at=now_iso(),
        workflows=workflows,
        workflows_provenance=workflows_provenance,
        dependency_updates=updates,
        dependency_updates_provenance=updates_provenance,
    )


def _forge_cache_path(forge: ForgeTarget) -> Path:
    return _engine_state_dir(forge) / _CACHE_FILENAME


def _read_forge_cache(forge: ForgeTarget) -> RecurringForgeStatus | None:
    try:
        raw = _forge_cache_path(forge).read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        return RecurringForgeStatus.model_validate_json(raw)
    except ValidationError:
        return None


def _write_forge_cache(forge: ForgeTarget, status: RecurringForgeStatus) -> None:
    path = _forge_cache_path(forge)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        store.write_text_atomic(path, status.model_dump_json())
    except OSError:
        logger.warning("recurring_forge: failed to write forge cache for %s", forge.repo.slug)


def _cache_age_s(cached: RecurringForgeStatus) -> float:
    try:
        fetched_at = datetime.fromisoformat(cached.fetched_at)
    except ValueError:
        return math.inf
    if fetched_at.tzinfo is None:
        return math.inf
    return (datetime.now(UTC) - fetched_at).total_seconds()


def _inflight_key(forge: ForgeTarget) -> str:
    return f"{_INFLIGHT_KEY_PREFIX}{forge.repo.slug}"


def _do_refresh_and_publish(forge: ForgeTarget, pubsub: PubSub) -> None:
    try:
        status = refresh_recurring_forge_status(forge)
        _write_forge_cache(forge, status)
        pubsub.publish_global(TeamEvent("recurring_status_refreshed", {"slug": forge.repo.slug}))
    finally:
        end_refresh(_inflight_key(forge))


def schedule_refresh(forge: ForgeTarget, pubsub: PubSub) -> bool:
    if not begin_refresh(_inflight_key(forge)):
        return False
    REFRESH_EXECUTOR.submit(_do_refresh_and_publish, forge, pubsub)
    return True


def forge_status(forge: ForgeTarget, *, pubsub: PubSub) -> RecurringForgeStatus | None:
    cached = _read_forge_cache(forge)
    if cached is None:
        schedule_refresh(forge, pubsub)
        return None
    if _cache_age_s(cached) >= RECURRING_FORGE_TTL_S:
        schedule_refresh(forge, pubsub)
    return cached
