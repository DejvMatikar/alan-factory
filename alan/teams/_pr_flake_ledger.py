from __future__ import annotations

import json
import logging
import re
import threading
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any, TypeGuard, cast

from alan import e2e_failures, store
from alan.engine_config import ForgeTarget
from alan.forge import active_forge
from alan.teams import _pr_branch, _pr_ready
from alan.teams._env import positive_int_env
from alan.teams._pr_audit_log import append_audit
from alan.teams._pr_json import _JsonList  # pyright: ignore[reportPrivateUsage]
from alan.teams._pr_state_paths import (
    _engine_state_dir,  # pyright: ignore[reportPrivateUsage]
    _read_json_with_legacy_fallback,  # pyright: ignore[reportPrivateUsage]
    _reset_aware_count,  # pyright: ignore[reportPrivateUsage]
    load_gate_rerun_state,
    state_dir,
)

logger = logging.getLogger(__name__)

_MAX_GATE_RERUNS_ENV = "ALAN_MAX_EXTERNAL_GATE_RERUNS"
_DEFAULT_MAX_GATE_RERUNS = 2
_GATE_RERUN_LIFETIME_CAP = 6

_E2E_CHECK_NAME = "Frontend E2E (Playwright, blocking)"

SHARED_FLAKY_ALLOWLIST: frozenset[str] = frozenset(
    {
        _E2E_CHECK_NAME,
        "Backend tests + coverage",
        "Frontend tests + coverage (100/100/100/100)",
    }
)

REBASE_SAFE_FLAKY_CHECKS: frozenset[str] = frozenset({_E2E_CHECK_NAME})


def flaky_only_failing(readiness: _pr_ready.PrReadiness) -> bool:
    if not readiness.failing_checks:
        return False
    if readiness.merge_state_status == "DIRTY":
        return False
    if readiness.review_decision == "CHANGES_REQUESTED":
        return False
    return all(c in SHARED_FLAKY_ALLOWLIST for c in readiness.failing_checks)


def actionable_failing_checks(readiness: _pr_ready.PrReadiness) -> tuple[str, ...]:
    quarantined = frozenset(readiness.quarantined_checks)
    return tuple(
        c
        for c in readiness.failing_checks
        if c not in SHARED_FLAKY_ALLOWLIST and c not in quarantined
    )


def rebase_blocking_failing_checks(readiness: _pr_ready.PrReadiness) -> tuple[str, ...]:
    quarantined = frozenset(readiness.quarantined_checks)
    return tuple(
        c
        for c in readiness.failing_checks
        if c not in REBASE_SAFE_FLAKY_CHECKS and c not in quarantined
    )


def aggregate_set_aside(
    readiness: _pr_ready.PrReadiness, forge_target: ForgeTarget,
) -> _pr_ready.PrReadiness:
    aggregates = forge_target.aggregate_checks & forge_target.authoritative_checks
    kept = tuple(c for c in readiness.failing_checks if c not in aggregates)
    if not kept or len(kept) == len(readiness.failing_checks):
        return readiness
    return replace(readiness, failing_checks=kept)


def aggregate_set_aside_result(
    readiness: _pr_ready.ReadinessResult, forge_target: ForgeTarget,
) -> _pr_ready.ReadinessResult:
    if isinstance(readiness, _pr_ready.ReadinessUnavailable):
        return readiness
    return aggregate_set_aside(readiness, forge_target)


def max_gate_reruns() -> int:
    return positive_int_env(_MAX_GATE_RERUNS_ENV, _DEFAULT_MAX_GATE_RERUNS)


def gate_reruns_exhausted(
    pr_number: int, head_sha: str | None,
    *, forge_target: ForgeTarget,
) -> bool:
    state = load_gate_rerun_state(pr_number, forge_target)
    if state.lifetime >= _GATE_RERUN_LIFETIME_CAP:
        return True
    return _reset_aware_count(state, head_sha) >= max_gate_reruns()


_FLAKE_FAILURES_FILE = "flake_failures.json"
_FLAKE_QUARANTINE_THRESHOLD_ENV = "ALAN_FLAKE_QUARANTINE_THRESHOLD"
_DEFAULT_FLAKE_QUARANTINE_THRESHOLD = 3
_FLAKE_QUARANTINE_LABEL = "flake-quarantine"
# Single lock spans every engine's flake_failures.json — the RMW below reads,
# mutates, and writes one engine's file via store.write_text_mkstemp per call,
# but the lock is not sharded per engine so two engines' writes can never
# interleave.
_flake_failures_lock = threading.Lock()


def flake_failures_path(forge: ForgeTarget) -> Path:
    return _engine_state_dir(forge) / _FLAKE_FAILURES_FILE


def _legacy_flake_failures_path() -> Path:
    return state_dir() / _FLAKE_FAILURES_FILE


def _is_pr_number(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _parse_flake_failures(raw: Any) -> dict[str, list[int]]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[int]] = {}
    for key, value in cast(dict[str, Any], raw).items():
        if isinstance(value, list):
            values = cast(_JsonList, value)
            out[key] = sorted({v for v in values if _is_pr_number(v)})
    return out


def _load_flake_failures(
    forge: ForgeTarget,
) -> dict[str, list[int]]:
    return _read_json_with_legacy_fallback(
        flake_failures_path(forge),
        _legacy_flake_failures_path(),
        forge,
        {},
        _parse_flake_failures,
    )


def _save_flake_failures(
    data: dict[str, list[int]], forge: ForgeTarget
) -> None:
    p = flake_failures_path(forge)
    p.parent.mkdir(parents=True, exist_ok=True)
    store.write_text_mkstemp(p, json.dumps(data), prefix=".flake_failures_")


def _flake_quarantine_threshold() -> int:
    return positive_int_env(
        _FLAKE_QUARANTINE_THRESHOLD_ENV, _DEFAULT_FLAKE_QUARANTINE_THRESHOLD
    )


def flake_rescued_checks(
    before: _pr_ready.PrReadiness,
    after: _pr_ready.ReadinessResult,
    *, head_sha_before: str | None, head_sha_after: str | None,
) -> tuple[str, ...]:
    if isinstance(after, _pr_ready.ReadinessUnavailable):
        return ()
    if after.pending_checks:
        return ()
    if not head_sha_before or head_sha_before != head_sha_after:
        return ()
    return tuple(c for c in before.failing_checks if c not in after.failing_checks)


_SPEC_KEY_PREFIX = "spec:"


def _spec_basename(spec: str) -> str:
    return spec.rsplit("/", 1)[-1]


def _spec_ledger_key(spec: str) -> str:
    return _SPEC_KEY_PREFIX + _spec_basename(spec)


def failing_e2e_spec_names(
    pr_number: int,
    readiness: _pr_ready.PrReadiness,
    *, forge_target: ForgeTarget,
) -> dict[str, tuple[str, ...]]:
    flaky_failing = [c for c in readiness.failing_checks if c == _E2E_CHECK_NAME]
    if not flaky_failing:
        return {}
    tests = _pr_branch.fetch_failing_e2e_tests(pr_number, forge_target=forge_target)
    if not tests:
        return {}
    specs = tuple(dict.fromkeys(t.spec for t in tests))
    return dict.fromkeys(flaky_failing, specs)


def flatten_spec_names(specs: Mapping[str, tuple[str, ...]]) -> tuple[str, ...]:
    flat: list[str] = []
    for values in specs.values():
        flat.extend(values)
    return tuple(dict.fromkeys(flat))


def failing_e2e_specs(
    pr_number: int,
    readiness: _pr_ready.PrReadiness,
    *, forge_target: ForgeTarget,
) -> tuple[str, ...]:
    return flatten_spec_names(
        failing_e2e_spec_names(pr_number, readiness, forge_target=forge_target)
    )


def _spec_basenames(specs: tuple[str, ...]) -> set[str]:
    return {_spec_basename(s) for s in specs}


def spec_gated_survivors(
    survived: tuple[str, ...],
    *, specs_before: tuple[str, ...], specs_after: tuple[str, ...],
) -> tuple[str, ...]:
    if not specs_before or not specs_after:
        return survived
    if _spec_basenames(specs_before) & _spec_basenames(specs_after):
        return survived
    return tuple(c for c in survived if c != _E2E_CHECK_NAME)


def _flake_ledger_keys(
    eligible: list[str], rescued_specs: Mapping[str, tuple[str, ...]] | None
) -> list[str]:
    keys: list[str] = []
    for check_name in eligible:
        specs = tuple((rescued_specs or {}).get(check_name, ()))
        if specs:
            keys.extend(_spec_ledger_key(s) for s in specs)
        else:
            keys.append(check_name)
    return keys


def record_flake_occurrence(
    pr_number: int,
    rescued_checks: tuple[str, ...],
    task_id: str,
    *, forge_target: ForgeTarget,
    rescued_specs: Mapping[str, tuple[str, ...]] | None = None,
) -> None:
    eligible = [c for c in rescued_checks if c in SHARED_FLAKY_ALLOWLIST]
    if not eligible:
        return
    keys = _flake_ledger_keys(eligible, rescued_specs)
    with _flake_failures_lock:
        data = _load_flake_failures(forge_target)
        for key in keys:
            prs = set(data.get(key, ()))
            prs.add(pr_number)
            data[key] = sorted(prs)
        _save_flake_failures(data, forge_target)
        snapshot = {key: list(data[key]) for key in keys}
    for key, pr_numbers in snapshot.items():
        _maybe_open_flake_proposal(key, pr_numbers, task_id, forge_target=forge_target)


_GATE_SURVIVED_FILE_TEMPLATE = "pr_{n}_gate_survived.json"


def gate_survived_checks_path(
    pr_number: int, forge: ForgeTarget
) -> Path:
    return _engine_state_dir(forge) / _GATE_SURVIVED_FILE_TEMPLATE.format(n=pr_number)


def _legacy_gate_survived_checks_path(pr_number: int) -> Path:
    return state_dir() / _GATE_SURVIVED_FILE_TEMPLATE.format(n=pr_number)


def _parse_gate_survived_checks(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    return {
        key: value
        for key, value in cast(dict[str, Any], raw).items()
        if isinstance(value, str)
    }


def load_gate_survived_checks(
    pr_number: int, forge: ForgeTarget
) -> dict[str, str]:
    return _read_json_with_legacy_fallback(
        gate_survived_checks_path(pr_number, forge),
        _legacy_gate_survived_checks_path(pr_number),
        forge,
        {},
        _parse_gate_survived_checks,
    )


def flake_survived_checks(
    before: _pr_ready.PrReadiness,
    after: _pr_ready.ReadinessResult,
    *, head_sha_before: str | None, head_sha_after: str | None,
) -> tuple[str, ...]:
    if isinstance(after, _pr_ready.ReadinessUnavailable):
        return ()
    if after.pending_checks:
        return ()
    if not head_sha_before or head_sha_before != head_sha_after:
        return ()
    return tuple(c for c in before.failing_checks if c in after.failing_checks)


def record_flake_survival(
    pr_number: int,
    survived: tuple[str, ...],
    head_sha: str,
    *, forge_target: ForgeTarget,
) -> None:
    if not survived:
        return
    data = load_gate_survived_checks(pr_number, forge_target)
    for check_name in survived:
        data[check_name] = head_sha
    p = gate_survived_checks_path(pr_number, forge_target)
    p.parent.mkdir(parents=True, exist_ok=True)
    store.write_text_mkstemp(p, json.dumps(data), prefix=".gate_survived_")


def survived_at_head_checks(
    pr_number: int,
    head_sha: str | None,
    *, forge_target: ForgeTarget,
) -> frozenset[str]:
    if head_sha is None:
        return frozenset()
    survived = load_gate_survived_checks(pr_number, forge_target)
    return frozenset(c for c, sha in survived.items() if sha == head_sha)


def evidenced_actionable_failing_checks(
    readiness: _pr_ready.PrReadiness,
    pr_number: int,
    head_sha: str | None,
    *, forge_target: ForgeTarget,
) -> tuple[str, ...]:
    readiness = aggregate_set_aside(readiness, forge_target)
    if head_sha is None:
        return actionable_failing_checks(readiness)
    quarantined = frozenset(readiness.quarantined_checks)
    survived = survived_at_head_checks(pr_number, head_sha, forge_target=forge_target)
    return tuple(
        c
        for c in readiness.failing_checks
        if c not in quarantined
        and (c not in SHARED_FLAKY_ALLOWLIST or c in survived)
    )


_DIFF_PATCH_MAX_BYTES = 1_000_000

_LOCATOR_TOKEN_RES = (
    re.compile(r"getByTestId\(\s*['\"]([^'\"]+)['\"]"),
    re.compile(r"\[data-testid=['\"]?([^'\"\]]+)['\"]?\]"),
)


def _locator_tokens(message: str) -> frozenset[str]:
    tokens: set[str] = set()
    for pattern in _LOCATOR_TOKEN_RES:
        tokens.update(m for m in pattern.findall(message) if len(m) >= 3)
    return frozenset(tokens)


def _patch_removed_added(patch: str) -> tuple[str, str]:
    removed: list[str] = []
    added: list[str] = []
    for line in patch.splitlines():
        if line.startswith(("---", "+++")):
            continue
        if line.startswith("-"):
            removed.append(line)
        elif line.startswith("+"):
            added.append(line)
    return "\n".join(removed), "\n".join(added)


def _spec_matches_diff(spec: str, diff_files: frozenset[str]) -> bool:
    return any(f == spec or f.endswith("/" + spec) for f in diff_files)


def fetch_pr_diff_patch(
    pr_number: int, *, forge_target: ForgeTarget,
) -> str | None:
    patch = active_forge().pr_patch(pr_number, forge_target=forge_target)
    if patch is None:
        logger.warning("fetch_pr_diff_patch failed for PR #%s", pr_number)
        return None
    if len(patch.encode("utf-8", errors="replace")) > _DIFF_PATCH_MAX_BYTES:
        logger.warning(
            "fetch_pr_diff_patch over %s bytes for PR #%s — skipping diff-join",
            _DIFF_PATCH_MAX_BYTES, pr_number,
        )
        return None
    return patch


def _e2e_tokens_removed_by_patch(
    tests: tuple[e2e_failures.FailingE2ETest, ...],
    pr_number: int,
    *, forge_target: ForgeTarget,
) -> bool:
    tokens: set[str] = set()
    for test in tests:
        tokens.update(_locator_tokens(test.message))
    if not tokens:
        return False
    patch = fetch_pr_diff_patch(pr_number, forge_target=forge_target)
    if patch is None:
        return False
    removed, added = _patch_removed_added(patch)
    return any(tok in removed and tok not in added for tok in tokens)


def _allowlisted_failing_checks(readiness: _pr_ready.PrReadiness) -> tuple[str, ...]:
    quarantined = frozenset(readiness.quarantined_checks)
    return tuple(
        c
        for c in readiness.failing_checks
        if c == _E2E_CHECK_NAME and c not in quarantined
    )


def _diff_attributes_tests(
    tests: tuple[e2e_failures.FailingE2ETest, ...],
    pr_number: int,
    *, forge_target: ForgeTarget,
) -> bool:
    diff_files = _pr_branch.fetch_diff_files(pr_number, forge_target=forge_target)
    if diff_files is None:
        return False
    if any(_spec_matches_diff(t.spec, diff_files) for t in tests):
        return True
    return _e2e_tokens_removed_by_patch(tests, pr_number, forge_target=forge_target)


def diff_attributable_e2e_checks(
    readiness: _pr_ready.PrReadiness,
    pr_number: int,
    *, forge_target: ForgeTarget,
    tests: tuple[e2e_failures.FailingE2ETest, ...] | None = None,
) -> tuple[str, ...]:
    flaky_failing = _allowlisted_failing_checks(readiness)
    if not flaky_failing:
        return ()
    if tests is None:
        tests = _pr_branch.fetch_failing_e2e_tests(pr_number, forge_target=forge_target)
    if not tests:
        return ()
    if _diff_attributes_tests(tests, pr_number, forge_target=forge_target):
        return flaky_failing
    return ()


def _flake_proposal_title(ledger_key: str) -> str:
    if ledger_key.startswith(_SPEC_KEY_PREFIX):
        return (
            "Flake quarantine proposal: spec "
            f"{ledger_key.removeprefix(_SPEC_KEY_PREFIX)}"
        )
    return f"Flake quarantine proposal: {ledger_key}"


def _has_open_flake_proposal(
    check_name: str, *, forge_target: ForgeTarget,
) -> bool:
    title = _flake_proposal_title(check_name)
    candidates = active_forge().search_open_issues(
        title, forge_target=forge_target, label=_FLAKE_QUARANTINE_LABEL,
    )
    return any(item.get("title") == title for item in candidates)


def _ensure_flake_label(*, forge_target: ForgeTarget) -> None:
    active_forge().create_label(
        _FLAKE_QUARANTINE_LABEL, forge_target=forge_target,
        color="d4c5f9", description="Proposal to quarantine a flaky external check",
    )


def _open_flake_issue(
    title: str, body: str, *, forge_target: ForgeTarget,
) -> bool:
    return active_forge().create_issue(
        title, body, forge_target=forge_target, label=_FLAKE_QUARANTINE_LABEL,
    )


def _flake_proposal_body(ledger_key: str, pr_numbers: list[int]) -> str:
    prs = ", ".join(f"#{n}" for n in pr_numbers)
    evidence = (
        f"on {len(pr_numbers)} distinct PRs: {prs}.\n\nCounted only when the "
        "check was red before the rerun and green on the same head SHA after "
        "it; failures that stayed red are not counted.\n\n"
        "This is a proposal only — no gate has been weakened, skipped, or "
        "quarantined. "
    )
    if ledger_key.startswith(_SPEC_KEY_PREFIX):
        spec_name = ledger_key.removeprefix(_SPEC_KEY_PREFIX)
        return (
            f"A rerun rescued the E2E gate while `{spec_name}` was among its "
            f"failing specs {evidence}"
            "A human should either (1) fix or `test.fixme` the flaky test in "
            f"`alan/web/e2e/{spec_name}` in its own reviewed PR, or (2) open "
            "a separate pull request whose only content is adding "
            f"`{spec_name}` to `alan/web/e2e/flaky-baseline.json`, "
            "referencing this issue and the rerun-rescued evidence above. "
            "The weekly e2e-stress sweep is the evidence stream for "
            "shrinking the baseline back. Do not widen any gate in "
            "response to this, and never add this spec to the baseline "
            "from inside a pull request that the ratchet is blocking."
        )
    return (
        f"A rerun rescued the check `{ledger_key}` {evidence}"
        "The failing spec files could not be identified from the run logs, so "
        "this proposal is at check granularity — inspect the failing job logs "
        "manually to identify and fix (or baseline) the flaky spec(s). The "
        "check already sits on the shared flaky allowlist that governs "
        "rerun/routing behavior; do not widen any gate in response to this."
    )


def _maybe_open_flake_proposal(
    check_name: str,
    pr_numbers: list[int],
    task_id: str,
    *, forge_target: ForgeTarget,
) -> None:
    if len(pr_numbers) < _flake_quarantine_threshold():
        return
    if _has_open_flake_proposal(check_name, forge_target=forge_target):
        return
    _ensure_flake_label(forge_target=forge_target)
    if _has_open_flake_proposal(check_name, forge_target=forge_target):
        return
    ok = _open_flake_issue(
        _flake_proposal_title(check_name),
        _flake_proposal_body(check_name, pr_numbers),
        forge_target=forge_target,
    )
    append_audit({
        "task_id": task_id,
        "action": "flake_proposal_opened",
        "check_name": check_name,
        "pr_count": len(pr_numbers),
        "ok": ok,
    })
