from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable
from typing import TypeVar, cast

from alan.engine_config import ForgeTarget
from alan.forge import JsonObject, active_forge
from alan.teams import _pr_audit_log
from alan.teams._pr_forge import GH_TIMEOUT_S

_T = TypeVar("_T")

logger = logging.getLogger(__name__)

MEASURED_BASELINE_STALE_REASON = "measured_baseline_stale"

MEASURED_BASELINE_PATHS: frozenset[str] = frozenset({
    "alan/web/e2e/desktop-audit/baseline.json",
    "alan/web/e2e/desktop-audit/baseline-high-water.ts",
    "alan/web/e2e/mobile-audit/baseline.json",
    "alan/web/e2e/mobile-audit/baseline-high-water.ts",
    "tests/size_baseline.py",
    "tests/test_size_ratchets.py",
    "tests/gh_read_baseline.py",
    "alan/web/.jscpd.json",
    "alan/web/e2e/flaky-baseline.json",
    "alan/web/e2e/stress-floor.json",
    ".github/ci-duration-budgets.json",
})


def _audit_probe_failed(pr_number: int, task_id: str, reason: str) -> None:
    _pr_audit_log.append_audit({
        "task_id": task_id,
        "action": "baseline_freshness_probe_failed",
        "pr_number": pr_number,
        "reason": reason,
    })


def _audit_compare_failed(pr_number: int, task_id: str, reason: str) -> None:
    _pr_audit_log.append_audit({
        "task_id": task_id,
        "action": "baseline_freshness_compare_failed",
        "pr_number": pr_number,
        "reason": reason,
    })


_AuditFailFn = Callable[[int, str, str], None]


def _guarded(
    pr_number: int, task_id: str, on_fail: _AuditFailFn, op: str,
    call: Callable[[], _T],
) -> _T | None:
    try:
        return call()
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.warning(
            "is_measured_baseline_stale: %s raised %s for PR #%s", op, exc, pr_number,
        )
        on_fail(pr_number, task_id, f"gh subprocess error: {exc}")
        return None


def _succeeded(
    pr_number: int, task_id: str, on_fail: _AuditFailFn, op: str,
    ok: bool, returncode: int, stderr: str,
) -> bool:
    if ok:
        return True
    logger.warning(
        "is_measured_baseline_stale: %s rc=%s stderr=%r for PR #%s",
        op, returncode, stderr[:200], pr_number,
    )
    on_fail(pr_number, task_id, f"gh returncode={returncode}: {stderr[:200]}")
    return False


def _is_nonempty_str(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _read_refs(
    pr_number: int, task_id: str, *, forge_target: ForgeTarget,
) -> JsonObject | None:
    read = _guarded(
        pr_number, task_id, _audit_probe_failed, "read_pr",
        lambda: active_forge().read_pr(
            pr_number, ("headRefOid", "baseRefName"),
            forge_target=forge_target, timeout=GH_TIMEOUT_S,
        ),
    )
    if read is None:
        return None
    if not _succeeded(
        pr_number, task_id, _audit_probe_failed, "read_pr",
        read.ok, read.returncode, read.stderr,
    ):
        return None
    if not read.fields:
        _audit_probe_failed(pr_number, task_id, "json_malformed")
        return None
    return read.fields


def _fetch_pr_refs(
    pr_number: int, task_id: str, *, forge_target: ForgeTarget,
) -> tuple[str, str] | None:
    fields = _read_refs(pr_number, task_id, forge_target=forge_target)
    if fields is None:
        return None
    head_sha = fields.get("headRefOid")
    base_ref = fields.get("baseRefName")
    if not _is_nonempty_str(head_sha) or not _is_nonempty_str(base_ref):
        _audit_probe_failed(pr_number, task_id, "head_or_base_ref_missing")
        return None
    return cast(str, head_sha), cast(str, base_ref)


def _compare_gap(
    pr_number: int, task_id: str, head_sha: str, base_ref: str,
    *, forge_target: ForgeTarget,
) -> tuple[int, frozenset[str], bool] | None:
    read = _guarded(
        pr_number, task_id, _audit_compare_failed, "compare_base",
        lambda: active_forge().compare_base(
            head_sha, base_ref, forge_target=forge_target, timeout=GH_TIMEOUT_S,
        ),
    )
    if read is None:
        return None
    if not _succeeded(
        pr_number, task_id, _audit_compare_failed, "compare_base",
        read.ok, read.returncode, read.stderr,
    ):
        return None
    if read.behind_by is None:
        _audit_compare_failed(pr_number, task_id, "ahead_by_missing")
        return None
    return read.behind_by, read.files, read.truncated


def is_measured_baseline_stale(
    pr_number: int, task_id: str, *, forge_target: ForgeTarget,
) -> bool:
    refs = _fetch_pr_refs(pr_number, task_id, forge_target=forge_target)
    if refs is None:
        return False
    head_sha, base_ref = refs
    compared = _compare_gap(pr_number, task_id, head_sha, base_ref, forge_target=forge_target)
    if compared is None:
        return False
    ahead_by, gap_files, truncated = compared
    if ahead_by == 0:
        return False
    if truncated:
        return True
    if gap_files & MEASURED_BASELINE_PATHS:
        return True
    diff_files = active_forge().changed_files(pr_number, forge_target=forge_target)
    if diff_files is None:
        return False
    return bool(diff_files & MEASURED_BASELINE_PATHS)
