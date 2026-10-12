from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal, cast

from alan import default_profile, e2e_failures
from alan.engine_config import DEFAULT_FORGE_TARGET, ForgeTarget
from alan.forge import ApiResult, active_forge
from alan.handover import CredentialFailureClass as CredentialFailureClass
from alan.teams import _forge_errors
from alan.teams._pr_audit_log import append_audit
from alan.teams._pr_forge import GH_TIMEOUT_S
from alan.teams._pr_json import (
    _JsonList,  # pyright: ignore[reportPrivateUsage]
    _JsonObject,  # pyright: ignore[reportPrivateUsage]
    _safe_list,  # pyright: ignore[reportPrivateUsage]
    safe_json,
)

logger = logging.getLogger(__name__)

_VERIFIER_LOGIN_ENV = "ALAN_VERIFIER_LOGIN"


@dataclass(frozen=True)
class VerifierIdentity:
    login: str

    def matches(self, author: str | None) -> bool:
        return bool(author) and author == self.login


def verifier_identity() -> VerifierIdentity | None:
    login = (
        os.environ.get(_VERIFIER_LOGIN_ENV, "").strip()
        or default_profile.resolved().profile.verifier_login
    )
    return VerifierIdentity(login) if login else None


def is_verifier(identity: VerifierIdentity | None, author: str | None) -> bool:
    return identity is not None and identity.matches(author)


def _handle_pr_view_failure(
    task_id: str, pr_number: int, context: str, result: ApiResult,
) -> None:
    failure_class = _forge_errors.classify_forge_failure(
        returncode=result.returncode, stderr=result.stderr,
    )
    logger.warning(
        "gh pr view failed (rc=%s, class=%s): %s",
        result.returncode, failure_class, result.stderr,
    )
    if failure_class == "domain_absent":
        append_audit({
            "task_id": task_id,
            "action": "pr_view_not_found",
            "pr_number": pr_number,
            "returncode": result.returncode,
            "stderr": (result.stderr or "")[:500],
        })
        return
    append_audit({
        "task_id": task_id,
        "action": "pr_view_failed",
        "pr_number": pr_number,
        "returncode": result.returncode,
        "failure_class": failure_class,
        "stderr": (result.stderr or "")[:500],
    })
    detail = _forge_errors.infra_failure_detail(
        context, returncode=result.returncode, stderr=result.stderr,
    )
    if failure_class == "rate_limited":
        raise _forge_errors.ForgeRateLimited(
            detail, _forge_errors.forge_rate_limit_reset_at(result.stderr)
        )
    raise _forge_errors.ForgeTransientError(detail)


def resolve_existing_branch(
    pr_number: int | None,
    task_id: str,
    *,
    forge_target: ForgeTarget,
) -> str | None:
    if pr_number is None:
        return None
    context = f"gh pr view PR #{pr_number}"
    try:
        result = active_forge().pr_head_branch(pr_number, forge_target=forge_target)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.warning("gh pr view raised %s", exc)
        append_audit({
            "task_id": task_id,
            "action": "pr_view_failed",
            "pr_number": pr_number,
            "error": str(exc),
        })
        raise _forge_errors.ForgeTransientError(f"{context} raised {exc}") from exc
    if not result.ok:
        _handle_pr_view_failure(task_id, pr_number, context, result)
        return None
    branch = result.stdout.strip()
    if not branch:
        append_audit({
            "task_id": task_id,
            "action": "pr_view_empty",
            "pr_number": pr_number,
        })
        return None
    append_audit({
        "task_id": task_id,
        "action": "pr_view_ok",
        "pr_number": pr_number,
        "branch": branch,
    })
    return branch


CredentialPurpose = Literal["transport", "reviewer"]


def classify_credential_failure(
    *, token_present: bool, returncode: int | None, stderr: str | None,
) -> CredentialFailureClass:
    if not token_present:
        return "missing_token"
    failure_class = _forge_errors.classify_forge_failure(
        returncode=returncode, stderr=stderr,
    )
    if failure_class == "auth":
        return "expired_token"
    return "transient"


def credential_recovery_recipe(
    purpose: CredentialPurpose, forge_target: ForgeTarget | None = None,
) -> str:
    """Rotate-under-stable-fallback-then-re-dispatch recipe, parametrized by
    which identity failed (transport = author push/merge, reviewer = the
    verifier's review-post) and, when the forge target is known (multi-repo
    is live), which repo it failed against."""
    scope = f" for {forge_target.repo.slug}" if forge_target is not None else ""
    fallback_name = (
        forge_target.credential_env_name
        if forge_target is not None
        else "the engine's forge credential_env_name"
    )
    return (
        f"Rotate the {purpose} credential{scope} under its stable fallback "
        f"name ({fallback_name}) — store the new value in the secrets store "
        f"under the same purpose, or update {fallback_name} directly — then "
        "re-dispatch this workspace. Per-workspace engine pinning is "
        "preserved across rotation."
    )


class BranchUpdateOutcome(str, Enum):
    UPDATED = "updated"
    CONFLICT = "conflict"
    FAILED = "failed"


_UPDATE_BRANCH_CONFLICT_MARKERS: tuple[str, ...] = (
    "merge conflict",
    "not mergeable",
)


def _is_update_branch_conflict(stderr: str) -> bool:
    text = (stderr or "").lower()
    return any(marker in text for marker in _UPDATE_BRANCH_CONFLICT_MARKERS)


def update_pr_branch_outcome(
    pr_number: int, task_id: str, *, forge_target: ForgeTarget,
) -> BranchUpdateOutcome:
    try:
        result = active_forge().request_branch_update(pr_number, forge_target=forge_target)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.warning("gh pr update-branch raised %s", exc)
        append_audit({
            "task_id": task_id,
            "action": "pr_update_branch_failed",
            "pr_number": pr_number,
            "error": str(exc),
        })
        return BranchUpdateOutcome.FAILED
    if not result.ok:
        logger.warning(
            "gh pr update-branch failed (rc=%s): %s",
            result.returncode,
            result.stderr,
        )
        append_audit({
            "task_id": task_id,
            "action": "pr_update_branch_failed",
            "pr_number": pr_number,
            "returncode": result.returncode,
            "stderr": (result.stderr or "")[:500],
        })
        if _is_update_branch_conflict(result.stderr or ""):
            return BranchUpdateOutcome.CONFLICT
        return BranchUpdateOutcome.FAILED
    append_audit({
        "task_id": task_id,
        "action": "pr_update_branch_ok",
        "pr_number": pr_number,
    })
    return BranchUpdateOutcome.UPDATED


def pr_is_open(
    pr_number: int, task_id: str, *, forge_target: ForgeTarget,
) -> bool | None:
    del task_id
    try:
        result = active_forge().pr_open_state(pr_number, forge_target=forge_target)
    except Exception:  # noqa: BLE001
        return None
    if not result.ok:
        return None
    state = result.stdout.strip()
    return state == "OPEN" if state else None


def _fetch_diff_files(
    pr_number: int, *, forge_target: ForgeTarget,
) -> frozenset[str] | None:
    return active_forge().changed_files(pr_number, forge_target=forge_target)


fetch_diff_files = _fetch_diff_files


def _audit_sonar_failure(pr_number: int, reason: str) -> None:
    append_audit({
        "action": "list_sonarcloud_annotations_failed",
        "pr_number": pr_number,
        "reason": reason,
    })


def _fetch_pr_head_sha(
    pr_number: int, *, forge_target: ForgeTarget,
    on_fail: Callable[[int, str], None] = _audit_sonar_failure,
) -> str | None:
    try:
        result = active_forge().head_sha(pr_number, forge_target=forge_target)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.warning(
            "_fetch_pr_head_sha: raised %s for PR #%s", exc, pr_number,
        )
        on_fail(pr_number, f"gh subprocess error: {exc}")
        return None
    if not result.ok:
        logger.warning(
            "_fetch_pr_head_sha: rc=%s stderr=%s",
            result.returncode, result.stderr,
        )
        on_fail(
            pr_number,
            f"gh returncode={result.returncode}: {(result.stderr or '')[:200]}",
        )
        return None
    sha = result.stdout.strip()
    if not sha:
        logger.warning("_fetch_pr_head_sha: empty sha for PR #%s", pr_number)
        on_fail(pr_number, "head_sha_empty")
        return None
    return sha


def fetch_flake_guard_head_sha(
    pr_number: int, task_id: str, *, forge_target: ForgeTarget,
    action: str = "flake_head_sha_fetch_failed",
) -> str | None:
    def _on_fail(pr: int, reason: str) -> None:
        append_audit({
            "task_id": task_id, "action": action,
            "pr_number": pr, "reason": reason,
        })
    return _fetch_pr_head_sha(pr_number, forge_target=forge_target, on_fail=_on_fail)


def _audited_forge_result(
    label: str, pr_number: int, on_fail: Callable[[int, str], None],
    call: Callable[[], ApiResult],
) -> ApiResult | None:
    try:
        result = call()
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.warning("%s: raised %s for PR #%s", label, exc, pr_number)
        on_fail(pr_number, f"gh subprocess error: {exc}")
        return None
    if not result.ok:
        logger.warning("%s: rc=%s stderr=%s", label, result.returncode, result.stderr)
        on_fail(pr_number, f"gh returncode={result.returncode}: {result.stderr[:200]}")
        return None
    return result


def _iter_check_runs(
    forge_target: ForgeTarget, sha: str, pr_number: int,
    *, on_fail: Callable[[int, str], None] = _audit_sonar_failure,
    strict: bool = False,
) -> list[_JsonObject] | None:
    result = _audited_forge_result(
        "_iter_check_runs", pr_number, on_fail,
        lambda: active_forge().commit_check_runs_page(sha, forge_target=forge_target),
    )
    if result is None:
        return None
    return _check_runs_from_payload(safe_json(result.stdout), pr_number, on_fail, strict=strict)


def _has_check_run_list(payload: _JsonObject) -> bool:
    return isinstance(payload.get("check_runs"), list)


def _check_runs_from_payload(
    raw: object, pr_number: int, on_fail: Callable[[int, str], None], *, strict: bool,
) -> list[_JsonObject] | None:
    if not isinstance(raw, dict):
        on_fail(pr_number, "check_runs_json_malformed")
        return None
    payload = cast(_JsonObject, raw)
    if strict and not _has_check_run_list(payload):
        on_fail(pr_number, "check_runs_field_missing")
        return None
    return _safe_list(payload.get("check_runs"))


def fetch_check_run_annotations(
    forge_target: ForgeTarget, check_run_id: int, pr_number: int,
    *, on_fail: Callable[[int, str], None] = _audit_sonar_failure,
    as_transport: bool = False,
    timeout_s: float = GH_TIMEOUT_S,
) -> list[_JsonObject] | None:
    forge = active_forge()
    read = forge.transport_check_run_annotations if as_transport else forge.check_run_annotations
    result = _audited_forge_result(
        "fetch_check_run_annotations", pr_number, on_fail,
        lambda: read(check_run_id, forge_target=forge_target, timeout=timeout_s),
    )
    if result is None:
        return None
    raw = safe_json(result.stdout)
    if not isinstance(raw, list):
        on_fail(pr_number, "annotations_json_malformed")
        return None
    return cast("list[_JsonObject]", raw)


@dataclass(frozen=True)
class OpenPrInfo:
    number: int
    title: str


def list_open_prs() -> list[OpenPrInfo]:
    try:
        result = active_forge().open_pr_titles(forge_target=DEFAULT_FORGE_TARGET)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if not result.ok:
        return []
    return _parse_open_prs(result.stdout)


def _parse_open_prs(stdout: str) -> list[OpenPrInfo]:
    try:
        data: Any = json.loads(stdout)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    out: list[OpenPrInfo] = []
    for raw in cast(_JsonList, data):
        if not isinstance(raw, dict):
            continue
        obj = cast(_JsonObject, raw)
        number = obj.get("number")
        title = obj.get("title")
        if isinstance(number, int) and isinstance(title, str):
            out.append(OpenPrInfo(number=number, title=title))
    return out


_CHECK_SUMMARY_MAX_LEN = 2000


@dataclass(frozen=True)
class CheckFailureDetail:
    name: str
    title: str
    summary: str
    details_url: str
    check_run_id: int = 0


def _check_details_url(node: _JsonObject) -> str:
    for key in ("details_url", "html_url"):
        value = node.get(key)
        if value:
            return str(value)
    return ""


def _check_output(node: _JsonObject) -> _JsonObject:
    output = node.get("output")
    return cast(_JsonObject, output) if isinstance(output, dict) else {}


def _coerce_check_failure(
    raw: object, wanted: frozenset[str]
) -> CheckFailureDetail | None:
    if not isinstance(raw, dict):
        return None
    node = cast(_JsonObject, raw)
    name = str(node.get("name", ""))
    if name not in wanted:
        return None
    out = _check_output(node)
    check_run_id = node.get("id")
    return CheckFailureDetail(
        name=name,
        title=str(out.get("title") or ""),
        summary=str(out.get("summary") or "")[:_CHECK_SUMMARY_MAX_LEN],
        details_url=_check_details_url(node),
        check_run_id=check_run_id if isinstance(check_run_id, int) else 0,
    )


def _audit_gate_detail_failure(pr_number: int, reason: str) -> None:
    append_audit({
        "action": "fetch_failing_check_details_failed",
        "pr_number": pr_number,
        "reason": reason,
    })


def fetch_failing_check_details(
    pr_number: int,
    check_names: Sequence[str],
    *,
    forge_target: ForgeTarget,
) -> tuple[CheckFailureDetail, ...]:
    if not check_names:
        return ()
    sha = _fetch_pr_head_sha(
        pr_number, forge_target=forge_target, on_fail=_audit_gate_detail_failure
    )
    if sha is None:
        return ()
    check_runs = _iter_check_runs(
        forge_target, sha, pr_number, on_fail=_audit_gate_detail_failure
    )
    if check_runs is None:
        return ()
    wanted = frozenset(check_names)
    coerced = (_coerce_check_failure(raw, wanted) for raw in check_runs)
    return tuple(d for d in coerced if d is not None)


def _rerequest_check_suite(
    forge_target: ForgeTarget, check_suite_id: int, pr_number: int,
    *, on_fail: Callable[[int, str], None] = _audit_sonar_failure,
) -> bool:
    return _audited_forge_result(
        "_rerequest_check_suite", pr_number, on_fail,
        lambda: active_forge().rerequest_check_suite(check_suite_id, forge_target=forge_target),
    ) is not None


def _rerun_workflow_run(
    forge_target: ForgeTarget, run_id: int, pr_number: int,
    *, on_fail: Callable[[int, str], None],
) -> bool:
    return _audited_forge_result(
        "_rerun_workflow_run", pr_number, on_fail,
        lambda: active_forge().rerun_failed_jobs(run_id, forge_target=forge_target),
    ) is not None


def _find_check_suite_id_for_name(
    check_runs: list[_JsonObject], check_name: str
) -> int | None:
    for item in check_runs:
        if str(item.get("name", "")) != check_name:
            continue
        suite = item.get("check_suite")
        if not isinstance(suite, dict):
            continue
        suite_id = cast(_JsonObject, suite).get("id")
        if isinstance(suite_id, int):
            return suite_id
    return None


_ACTIONS_RUN_URL_RE = re.compile(r"/actions/runs/(\d+)")


def _find_actions_run_id_for_name(
    check_runs: list[_JsonObject], check_name: str
) -> int | None:
    for item in check_runs:
        if str(item.get("name", "")) != check_name:
            continue
        match = _ACTIONS_RUN_URL_RE.search(str(item.get("details_url", "")))
        if match is not None:
            return int(match.group(1))
    return None


def _audit_gate_rerun(
    pr_number: int, task_id: str, check_name: str, ok: bool, reason: str
) -> None:
    append_audit({
        "task_id": task_id,
        "action": "external_gate_rerun_ok" if ok else "external_gate_rerun_failed",
        "pr_number": pr_number,
        "check_name": check_name,
        "reason": reason,
    })


def _audit_gate_query_failure(pr_number: int, reason: str) -> None:
    append_audit({
        "action": "external_gate_query_failed",
        "pr_number": pr_number,
        "reason": reason,
    })


def rerun_external_gate(
    pr_number: int, task_id: str, check_name: str,
    *, forge_target: ForgeTarget,
) -> bool:
    sha = _fetch_pr_head_sha(
        pr_number, forge_target=forge_target, on_fail=_audit_gate_query_failure
    )
    if sha is None:
        _audit_gate_rerun(pr_number, task_id, check_name, False, "sha_lookup_failed")
        return False
    check_runs = _iter_check_runs(forge_target, sha, pr_number, on_fail=_audit_gate_query_failure)
    if check_runs is None:
        _audit_gate_rerun(pr_number, task_id, check_name, False, "check_runs_lookup_failed")
        return False
    run_id = _find_actions_run_id_for_name(check_runs, check_name)
    if run_id is not None:
        ok = _rerun_workflow_run(
            forge_target, run_id, pr_number, on_fail=_audit_gate_query_failure
        )
        _audit_gate_rerun(pr_number, task_id, check_name, ok, "" if ok else "run_rerun_failed")
        return ok
    suite_id = _find_check_suite_id_for_name(check_runs, check_name)
    if suite_id is None:
        _audit_gate_rerun(pr_number, task_id, check_name, False, "check_suite_not_found")
        return False
    ok = _rerequest_check_suite(
        forge_target, suite_id, pr_number, on_fail=_audit_gate_query_failure
    )
    _audit_gate_rerun(pr_number, task_id, check_name, ok, "" if ok else "rerequest_failed")
    return ok


def rerun_flaky_checks(
    pr_number: int,
    task_id: str,
    failing_checks: tuple[str, ...],
    *, forge_target: ForgeTarget,
) -> bool:
    triggered = False
    for check in failing_checks:
        if rerun_external_gate(pr_number, task_id, check, forge_target=forge_target):
            triggered = True
    return triggered


_JOB_ID_RE = re.compile(r"/job/(\d+)")
_FAILING_CONCLUSIONS = frozenset({"FAILURE", "TIMED_OUT", "ACTION_REQUIRED"})


def job_id_from_details_url(url: str) -> int | None:
    m = _JOB_ID_RE.search(url)
    return int(m.group(1)) if m is not None else None


def failed_job_id(check: dict[str, Any]) -> int | None:
    if check.get("conclusion") not in _FAILING_CONCLUSIONS:
        return None
    url = check.get("detailsUrl")
    if not isinstance(url, str):
        return None
    return job_id_from_details_url(url)


def failing_job_ids(rollup: list[object]) -> list[int]:
    ids: list[int] = []
    for check in rollup:
        if isinstance(check, dict):
            jid = failed_job_id(cast(dict[str, Any], check))
            if jid is not None:
                ids.append(jid)
    return ids


def fetch_failing_job_ids(
    pr_number: int, timeout_s: float = GH_TIMEOUT_S,
    *, forge_target: ForgeTarget,
) -> list[int]:
    try:
        result = active_forge().status_check_rollup(
            pr_number, forge_target=forge_target, timeout=timeout_s,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if not result.ok or not result.stdout:
        return []
    try:
        data: Any = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    rollup = cast(dict[str, Any], data).get("statusCheckRollup", [])
    if not isinstance(rollup, list):
        return []
    return failing_job_ids(cast("list[object]", rollup))


def fetch_failed_log(
    job_id: int, timeout_s: float = GH_TIMEOUT_S,
    *, forge_target: ForgeTarget,
) -> str:
    try:
        result = active_forge().failed_job_log(
            job_id, forge_target=forge_target, timeout=timeout_s,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    return result.stdout if result.ok else ""


def _ignore_failure(_pr_number: int, _reason: str) -> None:
    return None


def fetch_check_annotations(
    check_run_id: int, *, forge_target: ForgeTarget,
) -> list[_JsonObject] | None:
    return fetch_check_run_annotations(
        forge_target, check_run_id, 0, on_fail=_ignore_failure, as_transport=True,
    )


_E2E_JOB_INSPECT_CAP = 5


def _audit_e2e_annotation_failure(pr_number: int, reason: str) -> None:
    append_audit({
        "action": "fetch_e2e_annotations_failed",
        "pr_number": pr_number,
        "reason": reason,
    })


def fetch_failing_e2e_tests(
    pr_number: int, *, forge_target: ForgeTarget,
    timeout_s: float = GH_TIMEOUT_S,
) -> tuple[e2e_failures.FailingE2ETest, ...] | None:
    job_ids = fetch_failing_job_ids(pr_number, timeout_s, forge_target=forge_target)
    if not job_ids:
        return None
    seen: dict[tuple[str, str], e2e_failures.FailingE2ETest] = {}
    for job_id in job_ids[:_E2E_JOB_INSPECT_CAP]:
        payload = fetch_check_run_annotations(
            forge_target, job_id, pr_number,
            on_fail=_audit_e2e_annotation_failure, as_transport=True, timeout_s=timeout_s,
        )
        for test in e2e_failures.parse_failing_e2e_annotations(payload or ()):
            seen.setdefault((test.spec, test.title), test)
    if not seen:
        return None
    return tuple(seen.values())
