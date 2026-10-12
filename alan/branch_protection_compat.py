from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from typing import Any, Literal, cast

from pydantic import BaseModel

from alan import secrets_store
from alan.engine_config import ForgeTarget

GH_API_TIMEOUT_S = 30.0

_ADMIN_SCOPE_MESSAGE = "cannot read branch protection — token lacks admin scope"
_ADMIN_RIGHTS_ERROR_TEXT = "admin rights"


class Finding(BaseModel, frozen=True):
    check: str
    status: Literal["pass", "fail", "unknown"]
    message: str
    remedy: str = ""
    remedy_url: str = ""


class CompatibilityRow(BaseModel, frozen=True):
    key: str
    assumption: str
    required_setting: str
    remedy: str


COMPATIBILITY_MATRIX: tuple[CompatibilityRow, ...] = (
    CompatibilityRow(
        key="conversation_resolution",
        assumption=(
            "pr_merge treats an unresolved review thread as a visible ESCALATED "
            "failure, not a silent merge (pr_iteration_pipeline.md D13)"
        ),
        required_setting=(
            "branch protection required_conversation_resolution.enabled == true "
            "on the default branch"
        ),
        remedy=(
            "Enable 'Require conversation resolution before merging' in the "
            "repo's branch protection rule for the default branch"
        ),
    ),
    CompatibilityRow(
        key="required_approvals",
        assumption=(
            "verification posts at most one verifier APPROVE review; a required "
            "human-approval count above zero would block autonomous merge"
        ),
        required_setting=(
            "required_pull_request_reviews.required_approving_review_count == 0 "
            "(or the rule is absent)"
        ),
        remedy=(
            "Set 'Required approvals' to 0 in the branch protection rule, or "
            "remove the required-reviews requirement"
        ),
    ),
    CompatibilityRow(
        key="squash_merge_enabled",
        assumption="pr_merge always calls `gh pr merge --squash --delete-branch`",
        required_setting="repo setting allow_squash_merge == true",
        remedy="Enable 'Allow squash merging' in repo Settings -> General -> Pull Requests",
    ),
    CompatibilityRow(
        key="delete_branch_on_merge",
        assumption="pr_merge relies on --delete-branch cleaning up the head branch after merge",
        required_setting="repo setting delete_branch_on_merge == true",
        remedy=(
            "Enable 'Automatically delete head branches' in repo Settings -> "
            "General -> Pull Requests"
        ),
    ),
    CompatibilityRow(
        key="required_checks",
        assumption=(
            "merge_recovery treats forge.authoritative_checks (e.g. "
            "'SonarCloud Code Analysis') as blocking-and-must-pass; a check "
            "GitHub itself does not require can silently be skipped instead of "
            "gating merge — the same applies to Copilot code review if this "
            "repo relies on it as a required check"
        ),
        required_setting=(
            "branch protection required_status_checks contexts/checks is a "
            "superset of the engine's forge.authoritative_checks"
        ),
        remedy=(
            "Add each of the engine's authoritative checks (and "
            "'copilot_code_review', if used) to the branch protection rule's "
            "required status checks"
        ),
    ),
)


class BranchProtectionUnreadableError(RuntimeError):
    pass


class _GhApiCallError(RuntimeError):
    pass


def _resolve_transport_token(forge: ForgeTarget) -> str | None:
    return secrets_store.resolve_secret(
        forge.repo.slug, secrets_store.TRANSPORT_PURPOSE, env_fallback=forge.credential_env_name
    )


def _is_admin_scope_403(stderr: str) -> bool:
    return "403" in stderr and _ADMIN_RIGHTS_ERROR_TEXT in stderr.lower()


def _invoke_gh_api(
    args: list[str], *, token: str, timeout: float
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["gh", "api", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "GH_TOKEN": token},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _GhApiCallError(f"gh api {' '.join(args)} raised: {exc}") from exc


def _parse_gh_response(
    args: list[str], result: subprocess.CompletedProcess[str]
) -> dict[str, Any]:
    if result.returncode != 0:
        stderr = result.stderr or ""
        if _is_admin_scope_403(stderr):
            raise BranchProtectionUnreadableError(_ADMIN_SCOPE_MESSAGE)
        raise _GhApiCallError(
            f"gh api {' '.join(args)} failed (rc={result.returncode}): {stderr[:300]}"
        )
    try:
        parsed: object = json.loads(result.stdout or "")
    except json.JSONDecodeError as exc:
        raise _GhApiCallError(f"gh api {' '.join(args)} returned invalid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise _GhApiCallError(f"gh api {' '.join(args)} did not return a JSON object")
    return cast("dict[str, Any]", parsed)


def _gh_api_json(args: list[str], *, token: str, timeout: float) -> dict[str, Any]:
    result = _invoke_gh_api(args, token=token, timeout=timeout)
    return _parse_gh_response(args, result)


def _default_branch(repo_json: dict[str, Any]) -> str:
    branch = repo_json.get("default_branch")
    return branch if isinstance(branch, str) and branch else "main"


def _as_dict(value: object) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return cast("dict[str, Any]", value)
    return None


def _as_object_list(value: object) -> list[object]:
    if isinstance(value, list):
        return cast("list[object]", value)
    return []


def _as_str_list(value: object) -> list[str]:
    return [v for v in _as_object_list(value) if isinstance(v, str)]


def _extract_conversation_resolution(
    repo: dict[str, Any], protection: dict[str, Any], forge: ForgeTarget
) -> bool:
    del repo, forge
    block = _as_dict(protection.get("required_conversation_resolution"))
    return block is not None and block.get("enabled") is True


def _extract_required_approvals(
    repo: dict[str, Any], protection: dict[str, Any], forge: ForgeTarget
) -> bool:
    del repo, forge
    block = _as_dict(protection.get("required_pull_request_reviews"))
    if block is None:
        return True
    count = block.get("required_approving_review_count")
    return isinstance(count, int) and count == 0


def _extract_squash_merge_enabled(
    repo: dict[str, Any], protection: dict[str, Any], forge: ForgeTarget
) -> bool:
    del protection, forge
    return repo.get("allow_squash_merge") is True


def _extract_delete_branch_on_merge(
    repo: dict[str, Any], protection: dict[str, Any], forge: ForgeTarget
) -> bool:
    del protection, forge
    return repo.get("delete_branch_on_merge") is True


def _check_list_contexts(checks_raw: object) -> set[str]:
    contexts: set[str] = set()
    for item_obj in _as_object_list(checks_raw):
        item = _as_dict(item_obj)
        if item is None:
            continue
        context = item.get("context")
        if isinstance(context, str):
            contexts.add(context)
    return contexts


def _required_check_contexts(protection: dict[str, Any]) -> set[str]:
    block = _as_dict(protection.get("required_status_checks"))
    if block is None:
        return set()
    return set(_as_str_list(block.get("contexts"))) | _check_list_contexts(block.get("checks"))


_RowExtractor = Callable[[dict[str, Any], dict[str, Any], ForgeTarget], bool]

_ROW_EXTRACTORS: dict[str, _RowExtractor] = {
    "conversation_resolution": _extract_conversation_resolution,
    "required_approvals": _extract_required_approvals,
    "squash_merge_enabled": _extract_squash_merge_enabled,
    "delete_branch_on_merge": _extract_delete_branch_on_merge,
}


def _row_finding(row: CompatibilityRow, ok: bool) -> Finding:
    if ok:
        return Finding(check=row.key, status="pass", message=f"satisfies: {row.required_setting}")
    return Finding(
        check=row.key,
        status="fail",
        message=f"does not satisfy: {row.required_setting}",
        remedy=row.remedy,
    )


def _effective_check_apps(forge: ForgeTarget) -> dict[str, int]:
    return {
        check: app_id
        for check, app_id in forge.authoritative_check_apps.items()
        if check in forge.authoritative_checks
    }


_MISSING_PIN_REMEDY_TEMPLATE = (
    "Require {check!r} from GitHub App id {app_id} in the branch protection "
    "rule's required status checks"
)

_ADMIN_ENFORCEMENT_REMEDY = (
    "Enforce the branch protection rule for administrators "
    "('Do not allow bypassing the above settings')"
)


def _check_app_pair(item_obj: object) -> tuple[str, int] | None:
    item = _as_dict(item_obj)
    if item is None:
        return None
    context, app_id = item.get("context"), item.get("app_id")
    if isinstance(context, str) and isinstance(app_id, int) and not isinstance(app_id, bool):
        return (context, app_id)
    return None


def _live_check_app_pairs(protection: dict[str, Any]) -> set[tuple[str, int]]:
    block = _as_dict(protection.get("required_status_checks"))
    checks_raw = block.get("checks") if block is not None else None
    pairs = (_check_app_pair(item) for item in _as_object_list(checks_raw))
    return {pair for pair in pairs if pair is not None}


def _admin_enforcement_enabled(protection: dict[str, Any]) -> bool:
    block = _as_dict(protection.get("enforce_admins"))
    return block is not None and block.get("enabled") is True


def _required_checks_remedies(protection: dict[str, Any], forge: ForgeTarget) -> list[str]:
    live_pairs = _live_check_app_pairs(protection)
    remedies = [
        _MISSING_PIN_REMEDY_TEMPLATE.format(check=check, app_id=app_id)
        for check, app_id in sorted(_effective_check_apps(forge).items())
        if (check, app_id) not in live_pairs
    ]
    if forge.enforce_admins and not _admin_enforcement_enabled(protection):
        remedies.append(_ADMIN_ENFORCEMENT_REMEDY)
    return remedies


def _required_checks_finding(
    row: CompatibilityRow, protection: dict[str, Any], forge: ForgeTarget
) -> Finding:
    superset_ok = forge.authoritative_checks <= _required_check_contexts(protection)
    part_remedies = _required_checks_remedies(protection, forge)
    if superset_ok and not part_remedies:
        return Finding(check=row.key, status="pass", message=f"satisfies: {row.required_setting}")
    remedies = ([] if superset_ok else [row.remedy]) + part_remedies
    return Finding(
        check=row.key,
        status="fail",
        message=f"does not satisfy: {row.required_setting}",
        remedy="; ".join(remedies),
    )


def _finding_for_row(
    row: CompatibilityRow,
    repo_json: dict[str, Any],
    protection_json: dict[str, Any],
    forge: ForgeTarget,
) -> Finding:
    if row.key == "required_checks":
        return _required_checks_finding(row, protection_json, forge)
    return _row_finding(row, _ROW_EXTRACTORS[row.key](repo_json, protection_json, forge))


def _diff_against_matrix(
    repo_json: dict[str, Any], protection_json: dict[str, Any], forge: ForgeTarget
) -> tuple[Finding, ...]:
    return tuple(
        _finding_for_row(row, repo_json, protection_json, forge) for row in COMPATIBILITY_MATRIX
    )


def compare_branch_protection(forge: ForgeTarget) -> tuple[Finding, ...]:
    token = _resolve_transport_token(forge)
    if not token:
        return (
            Finding(
                check="branch_protection_readable",
                status="unknown",
                message="no credential available to read branch protection",
                remedy=(
                    "configure a transport credential for this repo (secrets "
                    "store or its credential_env_name)"
                ),
            ),
        )
    try:
        repo_json = _gh_api_json(
            [f"repos/{forge.repo.slug}"], token=token, timeout=GH_API_TIMEOUT_S
        )
        default_branch = _default_branch(repo_json)
        protection_json = _gh_api_json(
            [f"repos/{forge.repo.slug}/branches/{default_branch}/protection"],
            token=token,
            timeout=GH_API_TIMEOUT_S,
        )
    except BranchProtectionUnreadableError:
        return (
            Finding(
                check="branch_protection_readable",
                status="fail",
                message=_ADMIN_SCOPE_MESSAGE,
                remedy=(
                    "grant the transport token admin access to this repository, "
                    "or supply a token with repo admin scope"
                ),
            ),
        )
    except _GhApiCallError as exc:
        return (
            Finding(
                check="branch_protection_readable",
                status="unknown",
                message=f"could not read branch protection: {exc}",
                remedy="retry once GitHub API access is restored",
            ),
        )
    return _diff_against_matrix(repo_json, protection_json, forge)


class ApplyOutcome(BaseModel, frozen=True):
    key: str
    status: Literal["applied", "deferred"]
    finding: Finding | None = None


_REPO_SETTING_ROW_KEYS: frozenset[str] = frozenset({
    "squash_merge_enabled", "delete_branch_on_merge",
})


def _remedy_url(forge: ForgeTarget) -> str:
    return f"https://github.com/{forge.repo.slug}/settings/branches"


def _deferred_outcome(
    row: CompatibilityRow, forge: ForgeTarget, *, status: Literal["fail", "unknown"], message: str
) -> ApplyOutcome:
    return ApplyOutcome(
        key=row.key,
        status="deferred",
        finding=Finding(
            check=row.key, status=status, message=message,
            remedy=row.remedy, remedy_url=_remedy_url(forge),
        ),
    )


def _deferred_all(
    forge: ForgeTarget, *, status: Literal["fail", "unknown"], message: str
) -> tuple[ApplyOutcome, ...]:
    return tuple(_deferred_outcome(row, forge, status=status, message=message)
                 for row in COMPATIBILITY_MATRIX)


def _invoke_gh_api_write(
    args: list[str], *, token: str, timeout: float, body: dict[str, Any]
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["gh", "api", *args, "--input", "-"],
            input=json.dumps(body),
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "GH_TOKEN": token},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _GhApiCallError(f"gh api {' '.join(args)} raised: {exc}") from exc


def _repo_patch_body() -> dict[str, Any]:
    return {"allow_squash_merge": True, "delete_branch_on_merge": True}


def _required_status_checks_block(
    forge: ForgeTarget, effective_apps: dict[str, int]
) -> dict[str, Any]:
    if not effective_apps:
        return {"strict": False, "contexts": sorted(forge.authoritative_checks)}
    checks: list[dict[str, Any]] = []
    for check in sorted(forge.authoritative_checks):
        entry: dict[str, Any] = {"context": check}
        if check in effective_apps:
            entry["app_id"] = effective_apps[check]
        checks.append(entry)
    return {"strict": False, "checks": checks}


def _protection_put_body(forge: ForgeTarget) -> dict[str, Any]:
    return {
        "required_status_checks": _required_status_checks_block(
            forge, _effective_check_apps(forge)
        ),
        "enforce_admins": forge.enforce_admins or None,
        "required_pull_request_reviews": {"required_approving_review_count": 0},
        "required_conversation_resolution": True,
        "restrictions": None,
    }


def _apply_repo_settings(forge: ForgeTarget, token: str) -> bool:
    try:
        result = _invoke_gh_api_write(
            ["-X", "PATCH", f"repos/{forge.repo.slug}"],
            token=token, timeout=GH_API_TIMEOUT_S, body=_repo_patch_body(),
        )
    except _GhApiCallError:
        return False
    return result.returncode == 0


def _apply_protection_settings(forge: ForgeTarget, token: str, default_branch: str) -> bool:
    try:
        result = _invoke_gh_api_write(
            ["-X", "PUT", f"repos/{forge.repo.slug}/branches/{default_branch}/protection"],
            token=token, timeout=GH_API_TIMEOUT_S,
            body=_protection_put_body(forge),
        )
    except _GhApiCallError:
        return False
    return result.returncode == 0


def _row_outcome(
    row: CompatibilityRow, forge: ForgeTarget, *, repo_ok: bool, protection_ok: bool
) -> ApplyOutcome:
    ok = repo_ok if row.key in _REPO_SETTING_ROW_KEYS else protection_ok
    if ok:
        return ApplyOutcome(key=row.key, status="applied")
    return _deferred_outcome(
        row, forge, status="fail",
        message=f"gh api call failed while applying: {row.required_setting}",
    )


def apply_branch_protection(forge: ForgeTarget) -> tuple[ApplyOutcome, ...]:
    token = _resolve_transport_token(forge)
    if not token:
        return _deferred_all(
            forge, status="unknown",
            message="no credential available to apply branch protection",
        )
    try:
        repo_json = _gh_api_json(
            [f"repos/{forge.repo.slug}"], token=token, timeout=GH_API_TIMEOUT_S
        )
        default_branch = _default_branch(repo_json)
    except BranchProtectionUnreadableError:
        return _deferred_all(forge, status="fail", message=_ADMIN_SCOPE_MESSAGE)
    except _GhApiCallError as exc:
        return _deferred_all(
            forge, status="unknown", message=f"could not read repo settings: {exc}"
        )
    repo_ok = _apply_repo_settings(forge, token)
    protection_ok = _apply_protection_settings(forge, token, default_branch)
    return tuple(
        _row_outcome(row, forge, repo_ok=repo_ok, protection_ok=protection_ok)
        for row in COMPATIBILITY_MATRIX
    )
