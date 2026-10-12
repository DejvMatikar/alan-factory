from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

from alan import process_runner
from alan.engine_config import _DEFAULT as _ENGINE_DEFAULT  # pyright: ignore[reportPrivateUsage]
from alan.engine_config import ForgeTarget
from alan.teams import _pr_branch
from alan.teams._pr_audit_log import append_audit
from alan.teams._pr_json import (
    _JsonObject,  # pyright: ignore[reportPrivateUsage]
    _safe_int,  # pyright: ignore[reportPrivateUsage]
    _safe_list,  # pyright: ignore[reportPrivateUsage]
    safe_json,
)

logger = logging.getLogger(__name__)

_GH_PAGE_SIZE = 100


def _init_sonar_constants() -> tuple[str, str, str]:
    cq = _ENGINE_DEFAULT.code_quality
    if cq is None:
        return "", "", ""
    return cq.token_env_var, cq.project_key, cq.host_url


_SONAR_TOKEN_ENV, _SONAR_PROJECT_KEY, _SONAR_HOST = _init_sonar_constants()  # noqa: S105  # nosec B105
_SONAR_TIMEOUT_S = 30.0


def sonar_hotspots_pr_url(pr_number: int) -> str | None:
    if not _SONAR_HOST or not _SONAR_PROJECT_KEY:
        return None
    return (
        f"{_SONAR_HOST}/project/security_hotspots"
        f"?id={_SONAR_PROJECT_KEY}&pullRequest={pr_number}"
    )


@dataclass(frozen=True)
class SonarAnnotation:
    path: str
    line: int
    title: str
    rule_url: str


SONAR_CHECK_NAME = "SonarCloud Code Analysis"
_SONAR_APP_SLUG = "sonarqubecloud"
_ACTIONS_APP_SLUG = "github-actions"


def _run_app_slug(run: _JsonObject) -> str:
    app = run.get("app")
    if not isinstance(app, dict):
        return ""
    slug = cast(_JsonObject, app).get("slug")
    return slug if isinstance(slug, str) else ""


def _is_named_sonar_run(run: _JsonObject) -> bool:
    return (
        str(run.get("name", "")) == SONAR_CHECK_NAME
        and _run_app_slug(run) != _ACTIONS_APP_SLUG
    )


def _mapping_runs(check_runs: Sequence[object]) -> list[_JsonObject]:
    return [cast(_JsonObject, item) for item in check_runs if isinstance(item, dict)]


def _sonar_runs(check_runs: Sequence[object]) -> list[_JsonObject]:
    runs = _mapping_runs(check_runs)
    named = [run for run in runs if _is_named_sonar_run(run)]
    return named or [run for run in runs if _run_app_slug(run) == _SONAR_APP_SLUG]


def _sonar_run_has_annotations(item: _JsonObject) -> bool:
    raw_conclusion = item.get("conclusion")
    conclusion = str(raw_conclusion).lower() if raw_conclusion is not None else ""
    if conclusion == "failure":
        return True
    output = item.get("output")
    if not isinstance(output, dict):
        return False
    return _safe_int(cast(_JsonObject, output).get("annotations_count")) > 0


def _extract_check_run_id(item: _JsonObject) -> int | None:
    if not _sonar_run_has_annotations(item):
        return None
    id_val = item.get("id")
    return id_val if isinstance(id_val, int) else None


def _select_sonar_check_run(
    forge_target: ForgeTarget, sha: str, pr_number: int,
) -> int | None:
    check_runs = _pr_branch._iter_check_runs(  # pyright: ignore[reportPrivateUsage]
        forge_target, sha, pr_number, on_fail=_pr_branch._audit_sonar_failure,  # pyright: ignore[reportPrivateUsage]
    )
    if check_runs is None:
        return None
    for item in _sonar_runs(check_runs):
        check_run_id = _extract_check_run_id(item)
        if check_run_id is not None:
            return check_run_id
    return None


def _extract_rule_url(message: str) -> str:
    for token in reversed(message.split()):
        if token.startswith("https://sonarcloud.io/"):
            return token.rstrip(").,;")
    return ""


def _coerce_annotation(raw: object) -> SonarAnnotation | None:
    if not isinstance(raw, dict):
        return None
    node = cast(_JsonObject, raw)
    path = str(node.get("path", "")).strip()
    if not path:
        return None
    line_val = node.get("start_line")
    if not isinstance(line_val, int):
        return None
    title = str(node.get("title", ""))
    message = str(node.get("message", ""))
    return SonarAnnotation(
        path=path,
        line=line_val,
        title=title,
        rule_url=_extract_rule_url(message),
    )


def _parse_annotations(
    payload: list[_JsonObject],
) -> tuple[SonarAnnotation, ...]:
    coerced = (_coerce_annotation(raw) for raw in payload)
    return tuple(a for a in coerced if a is not None)


def list_sonarcloud_annotations(
    pr_number: int, *, forge_target: ForgeTarget,
) -> tuple[SonarAnnotation, ...]:
    sha = _pr_branch._fetch_pr_head_sha(  # pyright: ignore[reportPrivateUsage]
        pr_number, forge_target=forge_target,
        on_fail=_pr_branch._audit_sonar_failure,  # pyright: ignore[reportPrivateUsage]
    )
    if sha is None:
        return ()
    check_run_id = _select_sonar_check_run(forge_target, sha, pr_number)
    if check_run_id is None:
        return ()
    raw_anns = _pr_branch.fetch_check_run_annotations(forge_target, check_run_id, pr_number)
    if raw_anns is None:
        return ()
    return _parse_annotations(raw_anns)


def _extract_hotspot_key(h: object) -> str | None:
    if not isinstance(h, dict):
        return None
    key = cast(_JsonObject, h).get("key")
    return str(key) if key is not None else None


def _hotspot_keys_from_list(raw_list: list[object]) -> list[str]:
    return [k for k in map(_extract_hotspot_key, raw_list) if k is not None]


def _fetch_hotspot_keys(pr_number: int, token: str, task_id: str) -> list[str] | None:
    url = (
        f"{_SONAR_HOST}/api/hotspots/search"
        f"?projectKey={_SONAR_PROJECT_KEY}&pullRequest={pr_number}&status=TO_REVIEW&ps=500"
    )
    try:
        result = process_runner.run_subprocess(
            ["curl", "-sf", "-u", f"{token}:", url],
            cwd=None,
            timeout=_SONAR_TIMEOUT_S,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.warning("_fetch_hotspot_keys: raised %s for PR #%s", exc, pr_number)
        append_audit({
            "task_id": task_id, "action": "sonar_ack_hotspots_failed",
            "pr_number": pr_number, "reason": f"curl subprocess error: {exc}",
        })
        return None
    if result.returncode != 0:
        logger.warning(
            "_fetch_hotspot_keys: rc=%s stderr=%s", result.returncode, result.stderr,
        )
        append_audit({
            "task_id": task_id, "action": "sonar_ack_hotspots_failed",
            "pr_number": pr_number,
            "reason": f"curl returncode={result.returncode}: {(result.stderr or '')[:200]}",
        })
        return None
    raw = safe_json(result.stdout or "")
    if not isinstance(raw, dict):
        append_audit({
            "task_id": task_id, "action": "sonar_ack_hotspots_failed",
            "pr_number": pr_number, "reason": "hotspot_search_json_malformed",
        })
        return None
    raw_list = _safe_list(cast(_JsonObject, raw).get("hotspots"))
    return _hotspot_keys_from_list(raw_list)


def _bulk_ack_hotspots(
    pr_number: int, task_id: str, token: str, keys: list[str],
) -> bool:
    url = f"{_SONAR_HOST}/api/hotspots/bulk_change"
    body = f"hotspots={','.join(keys)}&status=ACKNOWLEDGED"
    try:
        result = process_runner.run_subprocess(
            ["curl", "-sf", "-u", f"{token}:", "-X", "POST", "-d", body, url],
            cwd=None,
            timeout=_SONAR_TIMEOUT_S,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.warning("_bulk_ack_hotspots: raised %s for PR #%s", exc, pr_number)
        append_audit({
            "task_id": task_id, "action": "sonar_ack_hotspots_failed",
            "pr_number": pr_number, "reason": f"curl subprocess error: {exc}",
        })
        return False
    if result.returncode != 0:
        logger.warning(
            "_bulk_ack_hotspots: rc=%s stderr=%s", result.returncode, result.stderr,
        )
        append_audit({
            "task_id": task_id, "action": "sonar_ack_hotspots_failed",
            "pr_number": pr_number,
            "reason": f"curl returncode={result.returncode}: {(result.stderr or '')[:200]}",
        })
        return False
    append_audit({
        "task_id": task_id, "action": "sonar_ack_hotspots_ok",
        "pr_number": pr_number, "count": len(keys),
    })
    return True


def sonar_ack_hotspots(pr_number: int, task_id: str) -> bool:
    token = os.environ.get(_SONAR_TOKEN_ENV, "").strip()
    if not token:
        logger.warning(
            "sonar_ack_hotspots: %s not set for PR #%s", _SONAR_TOKEN_ENV, pr_number,
        )
        append_audit({
            "task_id": task_id, "action": "sonar_ack_hotspots_failed",
            "pr_number": pr_number, "reason": "token_missing",
        })
        return False
    hotspot_keys = _fetch_hotspot_keys(pr_number, token, task_id)
    if hotspot_keys is None:
        return False
    if not hotspot_keys:
        append_audit({
            "task_id": task_id, "action": "sonar_ack_hotspots_ok",
            "pr_number": pr_number, "count": 0, "note": "no_hotspots_found",
        })
        return True
    return _bulk_ack_hotspots(pr_number, task_id, token, hotspot_keys)


def _find_sonar_check_suite_id(check_runs: list[_JsonObject]) -> int | None:
    for item in _sonar_runs(check_runs):
        suite = item.get("check_suite")
        if not isinstance(suite, dict):
            continue
        suite_id = cast(_JsonObject, suite).get("id")
        if isinstance(suite_id, int):
            return suite_id
    return None


def retrigger_sonar_analysis(
    pr_number: int, task_id: str, *, forge_target: ForgeTarget,
) -> bool:
    def _on_fail(pr: int, reason: str) -> None:
        append_audit({
            "task_id": task_id, "action": "sonar_retrigger_failed",
            "pr_number": pr, "reason": reason,
        })

    sha = _pr_branch._fetch_pr_head_sha(  # pyright: ignore[reportPrivateUsage]
        pr_number, forge_target=forge_target, on_fail=_on_fail,
    )
    if sha is None:
        append_audit({
            "task_id": task_id, "action": "sonar_retrigger_failed",
            "pr_number": pr_number, "reason": "sha_lookup_failed",
        })
        return False
    check_runs = _pr_branch._iter_check_runs(  # pyright: ignore[reportPrivateUsage]
        forge_target, sha, pr_number, on_fail=_on_fail,
    )
    if check_runs is None:
        append_audit({
            "task_id": task_id, "action": "sonar_retrigger_failed",
            "pr_number": pr_number, "reason": "check_runs_lookup_failed",
        })
        return False
    check_suite_id = _find_sonar_check_suite_id(check_runs)
    if check_suite_id is None:
        append_audit({
            "task_id": task_id, "action": "sonar_retrigger_failed",
            "pr_number": pr_number, "reason": "check_suite_not_found",
        })
        return False
    ok = _pr_branch._rerequest_check_suite(  # pyright: ignore[reportPrivateUsage]
        forge_target, check_suite_id, pr_number, on_fail=_on_fail,
    )
    append_audit({
        "task_id": task_id,
        "action": "sonar_retrigger_ok" if ok else "sonar_retrigger_failed",
        "pr_number": pr_number,
    })
    return ok
