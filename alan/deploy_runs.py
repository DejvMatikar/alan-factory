from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, cast

from alan.claude_cli import ServerErrorRetryable
from alan.deploy_verify import DeployRun
from alan.engine_config import ForgeTarget
from alan.forge import JsonObject, active_forge

_GH_TIMEOUT_S = 20.0
_GH_RUN_LIMIT = 15


def parse_iso_datetime(raw: str) -> datetime | None:
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def post_merge_runs(runs: list[DeployRun], merged_at: datetime) -> list[DeployRun]:
    return [r for r in runs if r.created_at >= merged_at]


def fetch_deploy_runs(forge_target: ForgeTarget, deploy_workflow: str | None) -> list[DeployRun]:
    if deploy_workflow is None:
        return []
    result = active_forge().workflow_runs(
        deploy_workflow, forge_target=forge_target, limit=_GH_RUN_LIMIT, timeout=_GH_TIMEOUT_S,
    )
    if result.outcome == "raised":
        raise ServerErrorRetryable(
            f"gh run list --repo {forge_target.repo.slug} raised {result.stderr}"
        )
    if result.outcome == "failed":
        raise ServerErrorRetryable(
            f"gh run list --repo {forge_target.repo.slug} failed (rc={result.returncode}): "
            f"{result.stderr[:200]}"
        )
    if result.rows is None:
        raise ServerErrorRetryable("gh run list returned unparseable or non-list JSON payload")
    return _runs_from_rows(result.rows)


def _runs_from_rows(rows: Sequence[JsonObject]) -> list[DeployRun]:
    runs: list[DeployRun] = []
    for item in rows:
        run = _parse_deploy_run(cast(dict[str, Any], item))
        if run is not None:
            runs.append(run)
    return runs


def parse_deploy_runs_payload(stdout: str) -> list[DeployRun] | None:
    try:
        raw_data: object = json.loads(stdout)
    except (ValueError, TypeError):
        return None
    if not isinstance(raw_data, list):
        return None
    items = [
        cast(JsonObject, item)
        for item in cast("list[object]", raw_data)
        if isinstance(item, dict)
    ]
    return _runs_from_rows(items)


def _parse_deploy_run(item: dict[str, Any]) -> DeployRun | None:
    run_id_raw = item.get("databaseId")  # pyright: ignore[reportUnknownMemberType]
    created_at_raw = item.get("createdAt")  # pyright: ignore[reportUnknownMemberType]
    conclusion_raw = item.get("conclusion")  # pyright: ignore[reportUnknownMemberType]
    status_raw = item.get("status", "")  # pyright: ignore[reportUnknownMemberType]
    if not isinstance(created_at_raw, str):
        return None
    created_at = parse_iso_datetime(created_at_raw)
    if created_at is None:
        return None
    run_id = str(run_id_raw) if run_id_raw is not None else "?"
    status = status_raw if isinstance(status_raw, str) else ""
    conclusion = conclusion_raw if isinstance(conclusion_raw, str) else None
    return DeployRun(
        run_id=run_id,
        created_at=created_at,
        status=status,
        conclusion=conclusion,
    )


__all__ = [
    "fetch_deploy_runs",
    "parse_deploy_runs_payload",
    "parse_iso_datetime",
    "post_merge_runs",
]
