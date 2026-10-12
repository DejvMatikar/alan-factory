"""Baseline report for alan's governing metric: interventions per merged PR.

An "intervention" is a manual unblocking action the autonomous pipeline could not
complete on its own. The headline is a FLOOR on total attention-plus-proxy cost,
not a strict human-only count: the durable traces this script reads cannot tell
a person apart from an autonomous proxy hitting the same interfaces (e.g. the
roadmap-driver). That proxy-inclusive reading is the intended metric — a
self-sufficient pipeline should need none of it, from either source.

CAVEATS (emitted verbatim by `main`'s report; the archived roadmap that
baselined the metric keeps its original wording):
  - This headline is a proxy-inclusive FLOOR on unblocking actions the pipeline
    could not self-complete; it cannot separate the owner from an autonomous proxy.
  - After 2026-07-03 the roadmap-driver autonomous proxy began contributing its
    own chain_reset traces, inflating the trend independent of real pipeline
    reliability — read trend lines with that in mind.
  - job_failure_audit.jsonl is truncated to 2026-06-11..2026-06-30 on this host
    (a known undercount). It is not part of this metric's numerator, but is a
    reminder that host logs are not guaranteed append-only forever.

Tiers (per class, labelled by measurability — never blended):
  Tier 1 — cleanly measurable, folded into the headline:
    * manual/admin PR merges: set-difference of every PR merged into `main`
      (derived from `git log`'s squash-merge "(#N)" trailer — no `gh` dependency,
      following the same convention as `alan.eval_metrics.extract_pr_number` /
      `parse_git_log_output`, already used by scripts/learning_report.py and
      scripts/derive_trajectories.py) minus the PRs alan's own `pr_merge` team
      recorded merging itself (`action=="merge_ok"` rows in pr_merge_audit.jsonl,
      after dropping the P0.1-documented `task_id=="pr-merge-task"`/
      `pr_number==42` unit-test fixture burst — 1,780 of 1,941 raw rows, 91.8%,
      re-verified against this host's current log — reusing that audit's exact
      exclusion signature rather than re-deriving one).
    * re-dispatch/answer events: `spiral_audit.jsonl` rows with
      `kind=="chain_reset"` — the durable trace of an outside actor (human or
      proxy) poking a stuck workspace back to life.
  Tier 2 — estimable only by netting out the pipeline's own automation, reported
    as a separate labelled band, NEVER folded into the headline:
    * manual CI reruns and manual review dismissals. Both require GitHub-side
      gross counts (`gh run list`/`gh api` for reruns; a PR timeline query for
      dismissals) that this script accepts via --rerun-json/--dismissal-json
      overrides. NOTE: the baseline recorded in the archived roadmap was produced
      from an implementation sandbox whose Claude Code tool allowlist has no
      `gh` entry (verified against alan/engine_config.py's _BASE_TOOLS /
      _IMPL_EXTRA_TOOLS) — Tier 2's GitHub-side gross counts could not be
      captured in that pass and are recorded as "not captured this pass," not
      fabricated. Even once captured, the CI-rerun estimate is gross-only: no
      durable pipeline-rerun audit exists on this host to net against (unlike
      review dismissals, which alan's own verifier logs as
      `action=="review_dismissed"` in verification_audit.jsonl and which this
      script nets out automatically, after dropping the P0.1-documented
      `task_id` in {"verify-task", "t", "t1"} unit-test fixture cluster — 15 of
      1,551 raw rows on this host, the same exclusion signature P0.1 used for
      this file).
  Tier 3 — no durable trace, named but never counted: manual state surgery on
    workspace files, and a parallel unblocking session opened by a person.
    `InterventionBaseline` carries these as always-`None`-typed fields
    (`tier3_*_raw_count` / `tier3_*_dedup_incidents`) — a dataclass-level
    guarantee, not just a print-time convention, that no numeric value can
    ever be fabricated for either class.

Numerator convention: RAW ACTIONS for the headline (each manual merge / rerun /
re-dispatch counts once). The prior failure-class audit instead de-duplicates repeat
rows into incidents, keyed by (task_id, pr_number) for PR-scoped sources or
(task_id, handover_chain_id) for spiral_audit's chain-scoped rows — that
alternative is exposed here (`chain_reset_incident_count`) for consistency, not
used for the headline.

Usage:
    uv run python scripts/intervention_report.py \\
        --logs-root ~/.alan/logs --git-ref origin/main \\
        [--merged-prs-json FILE] [--rerun-json FILE] [--dismissal-json FILE] \\
        [--window-start 2026-05-15] [--window-end 2026-07-03]

--merged-prs-json accepts the shape of `gh pr list --state merged --search
"merged:<start>..<end>" --json number,mergedAt` — a JSON array of
{"number": int, "mergedAt"|"merged_at": ISO8601} (gh's own camelCase
"mergedAt", "Z"-suffixed or not, and the snake_case "merged_at" form are both
accepted); when omitted the script derives the same fact from `git log` on
--git-ref, which needs no `gh` access at all.
--rerun-json accepts a JSON array of {"pr_number": int, "created_at"|"createdAt":
ISO8601} records approximating one row per manual (run_attempt > 1) workflow
re-run (both the snake_case and gh's own camelCase timestamp field are read).
--dismissal-json accepts a JSON array of {"pr_number": int,
"dismissed_at"|"dismissedAt": ISO8601} records approximating a PR timeline's
review-dismissal events (same snake_case/camelCase acceptance).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from alan import store
from alan.eval_metrics import (
    extract_pr_number,
    parse_git_log_output,
    parse_merge_audit_rows,
)

_LOGS_ROOT_DEFAULT = Path.home() / ".alan" / "logs"
_PR_MERGE_FILE = "pr_merge_audit.jsonl"
_SPIRAL_FILE = "spiral_audit.jsonl"
_VERIFICATION_FILE = "verification_audit.jsonl"

_FIXTURE_PR_MERGE_TASK_ID = "pr-merge-task"
_FIXTURE_PR_MERGE_PR_NUMBER = 42
_FIXTURE_VERIFICATION_TASK_IDS = frozenset({"verify-task", "t", "t1"})
# spiral_audit.jsonl has no chain_reset-specific fixture signature documented by
# P0.1 (its fixture noise is confined to kind=="cap_exhausted_no_spiral"), and a
# length-based heuristic is unsafe here: real chain_reset task_ids include short
# dependency-sweep slugs like "dep-450" (docs/architecture/dep_sweep.md). Reuse
# the known cross-file fixture task_ids instead, as a defensive guard against a
# future chain_reset fixture row landing under one of these already-known ids.
_FIXTURE_SPIRAL_TASK_IDS = _FIXTURE_VERIFICATION_TASK_IDS | {_FIXTURE_PR_MERGE_TASK_ID}

_CHAIN_RESET_KIND = "chain_reset"
_REVIEW_DISMISSED_ACTION = "review_dismissed"

# Candidate timestamp field names for --rerun-json/--dismissal-json overrides,
# checked in order: the snake_case shape this script's own examples use, and
# gh's own camelCase shape (e.g. `gh api .../timeline`'s "createdAt").
_CREATED_AT_FIELDS = ("created_at", "createdAt")
_DISMISSED_AT_FIELDS = ("dismissed_at", "dismissedAt")

_WINDOW_START_DEFAULT = "2026-05-15"
_WINDOW_END_DEFAULT = "2026-07-03"
_GIT_REF_DEFAULT = "main"

_METRIC_PREFIX = "METRIC"
_ESTIMATE_PREFIX = "ESTIMATE"
_UNMEASURED_PREFIX = "UNMEASURED"
_CAVEAT_PREFIX = "CAVEAT"

CAVEATS: tuple[str, ...] = (
    "proxy-inclusive FLOOR on unblocking actions the pipeline could not "
    "self-complete; cannot separate the owner from an autonomous proxy",
    "after 2026-07-03 the roadmap-driver autonomous proxy contributes its own "
    "chain_reset traces, inflating the trend independent of real pipeline "
    "reliability",
    "job_failure_audit.jsonl is truncated to 2026-06-11..2026-06-30 on this "
    "host (known undercount; not part of this metric's numerator)",
)


@dataclass(frozen=True)
class InterventionBaseline:
    window_start: str
    window_end: str
    merged_pr_count: int
    manual_merge_count: int
    chain_reset_raw_count: int
    chain_reset_incident_count: int
    tier1_raw_total: int
    tier1_incident_total: int
    headline_ratio: float | None
    ci_rerun_gross: int | None
    review_dismissal_gross: int | None
    alan_review_dismissed_count: int
    review_dismissal_estimate: int | None
    tier3_state_surgery_raw_count: None
    tier3_state_surgery_dedup_incidents: None
    tier3_parallel_session_raw_count: None
    tier3_parallel_session_dedup_incidents: None


def _utc_date(ts: datetime) -> date:
    return ts.astimezone(UTC).date()


def _read_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _read_git_log(git_log_path: Path | None, git_ref: str) -> str:
    if git_log_path is not None:
        return _read_file(git_log_path)
    try:
        result = subprocess.run(
            ["git", "log", git_ref, "--format=%x1e%aI%x00%s%x00%b"],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout if result.returncode == 0 else ""
    except FileNotFoundError:
        return ""


def merged_pr_numbers_from_git_log(
    git_log_text: str, window_start: date, window_end: date
) -> frozenset[int]:
    numbers: set[int] = set()
    for ts_str, subject, _body in parse_git_log_output(git_log_text):
        try:
            ts = datetime.fromisoformat(ts_str)
        except ValueError:
            continue
        if not (window_start <= _utc_date(ts) <= window_end):
            continue
        pr_number = extract_pr_number(subject)
        if pr_number is not None:
            numbers.add(pr_number)
    return frozenset(numbers)


def merged_pr_numbers_from_json(
    raw_json_text: str, window_start: date, window_end: date
) -> frozenset[int]:
    try:
        data: Any = json.loads(raw_json_text)
    except json.JSONDecodeError:
        return frozenset()
    if not isinstance(data, list):
        return frozenset()
    numbers: set[int] = set()
    for entry in data:
        if not isinstance(entry, dict):
            continue
        number = entry.get("number")
        merged_at = entry.get("merged_at")
        if merged_at is None:
            merged_at = entry.get("mergedAt")  # gh pr list --json's camelCase shape
        if not isinstance(number, int) or isinstance(number, bool):
            continue
        if not isinstance(merged_at, str):
            continue
        try:
            ts = datetime.fromisoformat(merged_at)
        except ValueError:
            continue
        if window_start <= _utc_date(ts) <= window_end:
            numbers.add(number)
    return frozenset(numbers)


def _is_fixture_pr_merge_row(row: dict[str, object]) -> bool:
    return (
        row.get("task_id") == _FIXTURE_PR_MERGE_TASK_ID
        and row.get("pr_number") == _FIXTURE_PR_MERGE_PR_NUMBER
    )


def alan_merged_pr_numbers(
    merge_audit_text: str, window_start: date, window_end: date
) -> frozenset[int]:
    numbers: set[int] = set()
    for row in parse_merge_audit_rows(merge_audit_text):
        if _is_fixture_pr_merge_row(row):
            continue
        ts_raw = row.get("timestamp_utc")
        if not isinstance(ts_raw, str):
            continue
        try:
            ts = datetime.fromisoformat(ts_raw)
        except ValueError:
            continue
        if not (window_start <= _utc_date(ts) <= window_end):
            continue
        pr_number = row.get("pr_number")
        if isinstance(pr_number, int) and not isinstance(pr_number, bool):
            numbers.add(pr_number)
    return frozenset(numbers)


def _is_fixture_spiral_row(row: dict[str, object]) -> bool:
    return row.get("task_id") in _FIXTURE_SPIRAL_TASK_IDS


def parse_spiral_chain_resets(
    spiral_audit_text: str, window_start: date, window_end: date
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for line in spiral_audit_text.splitlines():
        row: dict[str, object] | None = store.parse_jsonl_row(line)
        if row is None or row.get("kind") != _CHAIN_RESET_KIND:
            continue
        if _is_fixture_spiral_row(row):
            continue
        ts_raw = row.get("timestamp_utc")
        if not isinstance(ts_raw, str):
            continue
        try:
            ts = datetime.fromisoformat(ts_raw)
        except ValueError:
            continue
        if window_start <= _utc_date(ts) <= window_end:
            rows.append(row)
    return rows


def chain_reset_incident_count(rows: list[dict[str, object]]) -> int:
    keys: set[tuple[str, str]] = set()
    for row in rows:
        task_id = row.get("task_id")
        if not isinstance(task_id, str):
            continue
        chain_id_raw = row.get("handover_chain_id")
        chain_id = chain_id_raw if isinstance(chain_id_raw, str) else task_id
        keys.add((task_id, chain_id))
    return len(keys)


def _is_fixture_verification_row(row: dict[str, object]) -> bool:
    task_id = row.get("task_id")
    return isinstance(task_id, str) and task_id in _FIXTURE_VERIFICATION_TASK_IDS


def alan_review_dismissed_count_in_window(
    verification_text: str, window_start: date, window_end: date
) -> int:
    count = 0
    for line in verification_text.splitlines():
        row: dict[str, object] | None = store.parse_jsonl_row(line)
        if row is None or _is_fixture_verification_row(row):
            continue
        if row.get("action") != _REVIEW_DISMISSED_ACTION:
            continue
        ts_raw = row.get("timestamp_utc")
        if not isinstance(ts_raw, str):
            continue
        try:
            ts = datetime.fromisoformat(ts_raw)
        except ValueError:
            continue
        if window_start <= _utc_date(ts) <= window_end:
            count += 1
    return count


def _gross_count_from_json(
    raw_json_text: str, window_start: date, window_end: date, ts_fields: tuple[str, ...]
) -> int | None:
    if not raw_json_text:
        return None
    try:
        data: Any = json.loads(raw_json_text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, list):
        return None
    count = 0
    for entry in data:
        if not isinstance(entry, dict):
            continue
        ts_raw: str | None = None
        for field in ts_fields:
            candidate = entry.get(field)
            if isinstance(candidate, str):
                ts_raw = candidate
                break
        if not isinstance(ts_raw, str):
            continue
        try:
            ts = datetime.fromisoformat(ts_raw)
        except ValueError:
            continue
        if window_start <= _utc_date(ts) <= window_end:
            count += 1
    return count


def compute_baseline(
    *,
    merged_pr_numbers: frozenset[int],
    alan_merged_pr_numbers: frozenset[int],
    chain_reset_rows: list[dict[str, object]],
    window_start: str,
    window_end: str,
    ci_rerun_gross: int | None,
    review_dismissal_gross: int | None,
    alan_review_dismissed_count: int,
) -> InterventionBaseline:
    manual_merge_count = len(merged_pr_numbers - alan_merged_pr_numbers)
    chain_reset_raw_count = len(chain_reset_rows)
    chain_reset_incidents = chain_reset_incident_count(chain_reset_rows)
    tier1_raw_total = manual_merge_count + chain_reset_raw_count
    tier1_incident_total = manual_merge_count + chain_reset_incidents
    merged_pr_count = len(merged_pr_numbers)
    headline_ratio = (
        tier1_raw_total / merged_pr_count if merged_pr_count > 0 else None
    )
    review_dismissal_estimate = (
        max(review_dismissal_gross - alan_review_dismissed_count, 0)
        if review_dismissal_gross is not None
        else None
    )
    return InterventionBaseline(
        window_start=window_start,
        window_end=window_end,
        merged_pr_count=merged_pr_count,
        manual_merge_count=manual_merge_count,
        chain_reset_raw_count=chain_reset_raw_count,
        chain_reset_incident_count=chain_reset_incidents,
        tier1_raw_total=tier1_raw_total,
        tier1_incident_total=tier1_incident_total,
        headline_ratio=headline_ratio,
        ci_rerun_gross=ci_rerun_gross,
        review_dismissal_gross=review_dismissal_gross,
        alan_review_dismissed_count=alan_review_dismissed_count,
        review_dismissal_estimate=review_dismissal_estimate,
        tier3_state_surgery_raw_count=None,
        tier3_state_surgery_dedup_incidents=None,
        tier3_parallel_session_raw_count=None,
        tier3_parallel_session_dedup_incidents=None,
    )


def _print_report(b: InterventionBaseline) -> None:
    print(f"{_METRIC_PREFIX} window {b.window_start}..{b.window_end}")
    print(f"{_METRIC_PREFIX} merged_pr_count {b.merged_pr_count}")
    print(f"{_METRIC_PREFIX} tier1_manual_merge_count {b.manual_merge_count}")
    print(
        f"{_METRIC_PREFIX} tier1_chain_reset_count {b.chain_reset_raw_count} "
        f"(raw) / {b.chain_reset_incident_count} (incident-deduped, for consistency "
        "with the P0.1 audit's accounting — not used for the headline)"
    )
    print(f"{_METRIC_PREFIX} tier1_raw_total {b.tier1_raw_total}")
    if b.headline_ratio is None:
        print(
            f"{_METRIC_PREFIX} headline_interventions_per_merged_pr n/a "
            "(no merged PRs in window)"
        )
    else:
        print(
            f"{_METRIC_PREFIX} headline_interventions_per_merged_pr "
            f"{b.headline_ratio:.4f} ({b.tier1_raw_total}/{b.merged_pr_count})"
        )
    if b.ci_rerun_gross is None:
        print(
            f"{_ESTIMATE_PREFIX} tier2_ci_rerun_gross n/a — not captured this pass "
            "(requires `gh run list`/`gh api`; gross-only even once captured — no "
            "durable pipeline-rerun audit exists to net against)"
        )
    else:
        print(
            f"{_ESTIMATE_PREFIX} tier2_ci_rerun_gross {b.ci_rerun_gross} "
            "(gross-only — no durable pipeline-rerun audit exists to net against)"
        )
    if b.review_dismissal_estimate is None:
        print(
            f"{_ESTIMATE_PREFIX} tier2_review_dismissal_estimate n/a — GitHub-side "
            "gross dismissals not captured this pass (requires a `gh api` PR "
            f"timeline query); alan's own auto-dismissals in window: "
            f"{b.alan_review_dismissed_count} (netted out once gross is available)"
        )
    else:
        print(
            f"{_ESTIMATE_PREFIX} tier2_review_dismissal_estimate "
            f"{b.review_dismissal_estimate} (gross {b.review_dismissal_gross} minus "
            f"alan's own {b.alan_review_dismissed_count} auto-dismissals)"
        )
    print(f"{_UNMEASURED_PREFIX} tier3_state_surgery n/a — no durable trace")
    print(
        f"{_UNMEASURED_PREFIX} tier3_parallel_human_session n/a — no durable trace"
    )
    for caveat in CAVEATS:
        print(f"{_CAVEAT_PREFIX} {caveat}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--logs-root", default=str(_LOGS_ROOT_DEFAULT))
    parser.add_argument("--git-log", default=None, help="Pre-captured git log file")
    parser.add_argument("--git-ref", default=_GIT_REF_DEFAULT)
    parser.add_argument("--merged-prs-json", default=None)
    parser.add_argument("--rerun-json", default=None)
    parser.add_argument("--dismissal-json", default=None)
    parser.add_argument("--window-start", default=_WINDOW_START_DEFAULT)
    parser.add_argument("--window-end", default=_WINDOW_END_DEFAULT)
    args = parser.parse_args(argv)

    logs_root = Path(args.logs_root)
    window_start = date.fromisoformat(args.window_start)
    window_end = date.fromisoformat(args.window_end)

    if args.merged_prs_json:
        merged_prs = merged_pr_numbers_from_json(
            _read_file(Path(args.merged_prs_json)), window_start, window_end
        )
    else:
        git_log_path = Path(args.git_log) if args.git_log else None
        git_log_text = _read_git_log(git_log_path, args.git_ref)
        merged_prs = merged_pr_numbers_from_git_log(
            git_log_text, window_start, window_end
        )

    merge_audit_text = _read_file(logs_root / _PR_MERGE_FILE)
    alan_merged = alan_merged_pr_numbers(merge_audit_text, window_start, window_end)

    spiral_text = _read_file(logs_root / _SPIRAL_FILE)
    chain_reset_rows = parse_spiral_chain_resets(spiral_text, window_start, window_end)

    verification_text = _read_file(logs_root / _VERIFICATION_FILE)
    alan_dismissed = alan_review_dismissed_count_in_window(
        verification_text, window_start, window_end
    )

    ci_rerun_gross = None
    if args.rerun_json:
        ci_rerun_gross = _gross_count_from_json(
            _read_file(Path(args.rerun_json)),
            window_start,
            window_end,
            _CREATED_AT_FIELDS,
        )

    dismissal_gross = None
    if args.dismissal_json:
        dismissal_gross = _gross_count_from_json(
            _read_file(Path(args.dismissal_json)),
            window_start,
            window_end,
            _DISMISSED_AT_FIELDS,
        )

    baseline = compute_baseline(
        merged_pr_numbers=merged_prs,
        alan_merged_pr_numbers=alan_merged,
        chain_reset_rows=chain_reset_rows,
        window_start=args.window_start,
        window_end=args.window_end,
        ci_rerun_gross=ci_rerun_gross,
        review_dismissal_gross=dismissal_gross,
        alan_review_dismissed_count=alan_dismissed,
    )
    _print_report(baseline)
    return 0


if __name__ == "__main__":
    sys.exit(main())
