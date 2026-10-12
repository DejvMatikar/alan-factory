from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from alan.digest import (
    AuditWindowCounts,
    DigestDoc,
    bucket_audit_rows,
    digest_from_dict,
    digest_to_dict,
    render_digest,
)
from alan.handover import list_handovers
from alan.health import HealthState, resolve_digest_deadline
from alan.incident import AuditRow, Incident, derive_incidents, digest_overdue, gather_audit_rows
from alan.incident_store import (
    DispositionRecord,
    load_dispositions,
    prune_dispositions,
    reconcile_reopen,
)
from alan.queue import Queue
from alan.roots import logs_root
from alan.shop_stats import assemble_shop_stats_block
from alan.workspace import list_workspaces

logger = logging.getLogger("alan.digest_sweep")

_EPOCH_ISO = datetime.fromtimestamp(0, tz=UTC).isoformat()
_FALLBACK_WINDOW = timedelta(days=1)

DIGEST_LAST_DATE_META_KEY = "digest_last_date"
DIGEST_LATEST_META_KEY = "digest_latest_v1"

_WEBHOOK_URL_ENV = "ALAN_DIGEST_WEBHOOK_URL"
_WEBHOOK_TIMEOUT_S = 10.0


def load_latest_digest(queue: Queue) -> DigestDoc | None:
    raw = queue.get_meta(DIGEST_LATEST_META_KEY)
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return digest_from_dict(cast("dict[str, Any]", parsed))


def _previous_since(queue: Queue) -> str:
    prev = load_latest_digest(queue)
    return prev.generated_at if prev is not None else _EPOCH_ISO


def _parse_aware(since: str, now: datetime) -> datetime:
    try:
        since_dt = datetime.fromisoformat(since)
    except ValueError:
        return now
    return since_dt if since_dt.tzinfo is not None else now


def _prior_window_start(since: str, now: datetime) -> str:
    since_dt = _parse_aware(since, now)
    window = now - since_dt
    if window.total_seconds() <= 0:
        window = _FALLBACK_WINDOW
    return (since_dt - window).isoformat()


def _push_digest_webhook(doc: DigestDoc) -> None:
    url = os.environ.get(_WEBHOOK_URL_ENV, "").strip()
    if not url:
        logger.info("digest_sweep: digest webhook unset; digest not pushed")
        return
    payload = json.dumps(digest_to_dict(doc)).encode("utf-8")
    request = urllib.request.Request(  # noqa: S310
        url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=_WEBHOOK_TIMEOUT_S):  # noqa: S310  # nosec B310
            pass
    except (urllib.error.URLError, OSError, ValueError):
        logger.warning("digest_sweep: digest webhook push failed", exc_info=True)


def _emit_digest(
    queue: Queue,
    dispositions: dict[str, DispositionRecord],
    fresh_incidents: list[Incident],
    audit_rows: list[AuditRow],
    now: datetime,
    deadline: datetime,
) -> None:
    since = _previous_since(queue)
    prior_since = _prior_window_start(since, now)
    audit_counts: AuditWindowCounts = bucket_audit_rows(
        audit_rows, since=since, prior_since=prior_since
    )
    shop_stats = assemble_shop_stats_block(queue)
    doc = render_digest(dispositions, fresh_incidents, audit_counts, since, now, shop_stats)
    queue.set_meta(DIGEST_LATEST_META_KEY, json.dumps(digest_to_dict(doc)))
    queue.set_meta(DIGEST_LAST_DATE_META_KEY, deadline.date().isoformat())
    _push_digest_webhook(doc)


def _maybe_emit_digest(
    queue: Queue,
    dispositions: dict[str, DispositionRecord],
    digest_last_date: str | None,
    fresh_incidents: list[Incident],
    audit_rows: list[AuditRow],
    now: datetime,
    deadline: datetime,
) -> None:
    if not digest_overdue(digest_last_date, now, deadline):
        return
    _emit_digest(queue, dispositions, fresh_incidents, audit_rows, now, deadline)


def _prune_dispositions_safe(queue: Queue, *, now: datetime) -> int:
    try:
        return prune_dispositions(queue, now=now)
    except Exception:  # noqa: BLE001
        logger.exception("digest_sweep: prune_dispositions failed; digest emission unaffected")
        return 0


def reconcile_incidents_sweep(
    queue: Queue,
    workspaces_root: Path | None,
    health_state: HealthState,
    now: datetime,
) -> None:
    all_ws = list_workspaces(root=workspaces_root)
    handovers_by_task = {ws.task_id: list_handovers(ws) for ws in all_ws}
    engine_key_by_workspace: dict[str, str | None] = {
        ws.task_id: ws.meta.engine_key for ws in all_ws
    }
    audit_rows = gather_audit_rows(logs_root())
    digest_last_date = queue.get_meta(DIGEST_LAST_DATE_META_KEY)
    deadline = resolve_digest_deadline(now)
    fresh_incidents = derive_incidents(
        workspaces=all_ws,
        handovers_by_workspace=handovers_by_task,
        health_state=health_state,
        audit_rows=audit_rows,
        digest_last_date=digest_last_date,
        now=now,
        digest_deadline=deadline,
        engine_key_by_workspace=engine_key_by_workspace,
    )
    dispositions = reconcile_reopen(queue, fresh_incidents, now=now)
    if _prune_dispositions_safe(queue, now=now):
        dispositions = load_dispositions(queue)
    _maybe_emit_digest(
        queue, dispositions, digest_last_date, fresh_incidents, audit_rows, now, deadline
    )


__all__ = [
    "DIGEST_LAST_DATE_META_KEY",
    "DIGEST_LATEST_META_KEY",
    "load_latest_digest",
    "reconcile_incidents_sweep",
]
