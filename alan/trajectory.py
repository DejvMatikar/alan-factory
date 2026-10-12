from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from alan import store
from alan.eval_metrics import (
    merge_survived,
    parse_git_log_output,
    parse_merge_audit_rows,
    revert_targets_pr,
)

_INV_SEQ_RE = re.compile(r"^inv_(\d+)$")
_VERDICT_FIELD = "verdict"
_REVIEW_OK_ACTION = "review_ok"
_REVIEW_OK_INLINE_ACTION = "review_ok_inline"
_DRY_RUN_REVIEW_ACTION = "dry_run"
_VERIFIED_ACTIONS = frozenset({_REVIEW_OK_ACTION, _REVIEW_OK_INLINE_ACTION, _DRY_RUN_REVIEW_ACTION})


@dataclass(frozen=True)
class TrajectoryRecord:
    task_id: str
    inv_id: str | None
    pr_number: int | None
    team: str
    verdict: str | None
    reward: bool | None
    fix_cycles: int
    revisits: int
    turns: int | None
    verified_at: str | None = None


def _inv_seq(inv_id: str | None) -> int:
    if inv_id is None:
        return -1
    m = _INV_SEQ_RE.match(inv_id)
    return int(m.group(1)) if m else -1


def _valid_node_turns_row(row: dict[str, Any]) -> bool:
    task_id = row.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        return False
    inv_id = row.get("inv_id")
    if not isinstance(inv_id, str) or not inv_id:
        return False
    team = row.get("team")
    if not isinstance(team, str) or not team:
        return False
    return True


def _parse_node_turns(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        row = store.parse_jsonl_row(line)
        if row is None or not _valid_node_turns_row(row):
            continue
        rows.append(row)
    return rows


def _valid_verification_row(row: dict[str, Any]) -> bool:
    if row.get("action") not in _VERIFIED_ACTIONS:
        return False
    task_id = row.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        return False
    verdict = row.get(_VERDICT_FIELD)
    if not isinstance(verdict, str) or not verdict:
        return False
    pr_number = row.get("pr_number")
    return isinstance(pr_number, int) and not isinstance(pr_number, bool)


def _parse_verification_rows(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        row = store.parse_jsonl_row(line)
        if row is None or not _valid_verification_row(row):
            continue
        rows.append(row)
    return rows


def _collapse_verifications(
    rows: list[dict[str, Any]],
) -> dict[tuple[str, str | None, int], tuple[str, str | None]]:
    by_key: dict[tuple[str, str | None, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        task_id = str(row.get("task_id") or "")
        inv_id_raw = row.get("inv_id")
        inv_id: str | None = inv_id_raw if isinstance(inv_id_raw, str) else None
        pr_number = int(row["pr_number"])
        by_key[(task_id, inv_id, pr_number)].append(row)
    result: dict[tuple[str, str | None, int], tuple[str, str | None]] = {}
    for key, group in by_key.items():
        sorted_group = sorted(
            group,
            key=lambda r: str(r.get("timestamp_utc") or ""),
        )
        final_row = sorted_group[-1]
        final_verdict = str(final_row[_VERDICT_FIELD])
        ts_raw = final_row.get("timestamp_utc")
        final_ts: str | None = ts_raw if isinstance(ts_raw, str) else None
        result[key] = (final_verdict, final_ts)
    return result


def _parse_ts(row: dict[str, Any]) -> datetime | None:
    ts_raw = row.get("timestamp_utc")
    if not isinstance(ts_raw, str):
        return None
    try:
        return datetime.fromisoformat(ts_raw)
    except ValueError:
        return None


def _find_revert(
    pr_number: int, git_records: list[tuple[str, str, str]]
) -> datetime | None:
    for ts_str, subject, body in git_records:
        if not revert_targets_pr(subject, body, pr_number):
            continue
        try:
            return datetime.fromisoformat(ts_str)
        except ValueError:
            continue
    return None


def _build_merge_survived_index(
    merge_rows: list[dict[str, Any]],
    git_records: list[tuple[str, str, str]],
) -> dict[tuple[str, int], bool]:
    index: dict[tuple[str, int], bool] = {}
    for row in merge_rows:
        task_id = row.get("task_id")
        if not isinstance(task_id, str):
            continue
        pr_number = row.get("pr_number")
        if not isinstance(pr_number, int) or isinstance(pr_number, bool):
            continue
        merge_ts = _parse_ts(row)
        if merge_ts is None:
            continue
        index[(task_id, pr_number)] = merge_survived(merge_ts, _find_revert(pr_number, git_records))
    return index


def _deduplicate_node_turns(
    rows: list[dict[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    last: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (str(row["task_id"]), str(row["inv_id"]))
        last[key] = row
    return last


def _build_pr_groups(
    turns_index: dict[tuple[str, str], dict[str, Any]],
    inv_to_pr: dict[tuple[str, str], int],
) -> dict[tuple[str, int], list[tuple[int, str]]]:
    groups: dict[tuple[str, int], list[tuple[int, str]]] = defaultdict(list)
    for (task_id, inv_id), row in turns_index.items():
        pr_number = inv_to_pr.get((task_id, inv_id))
        if pr_number is None:
            continue
        team = str(row.get("team") or "")
        seq = _inv_seq(inv_id)
        groups[(task_id, pr_number)].append((seq, team))
    return groups


def _build_inv_lookup(
    verif_index: dict[tuple[str, str | None, int], tuple[str, str | None]],
) -> tuple[
    dict[tuple[str, str], int], dict[tuple[str, str], str], dict[tuple[str, str], str]
]:
    inv_to_pr: dict[tuple[str, str], int] = {}
    inv_to_verdict: dict[tuple[str, str], str] = {}
    inv_to_ts: dict[tuple[str, str], str] = {}
    for (task_id, inv_id, pr_number), (v, ts) in verif_index.items():
        if inv_id is None:
            continue
        key = (task_id, inv_id)
        current_ts = ts or ""
        existing_ts = inv_to_ts.get(key, "")
        if key not in inv_to_pr or current_ts > existing_ts:
            inv_to_pr[key] = pr_number
            inv_to_verdict[key] = v
            inv_to_ts[key] = current_ts
    return inv_to_pr, inv_to_verdict, inv_to_ts


def _compute_cycles(
    task_id: str,
    inv_id: str,
    pr_number: int,
    pr_groups: dict[tuple[str, int], list[tuple[int, str]]],
) -> tuple[int, int]:
    this_seq = _inv_seq(inv_id)
    fix_cycles = 0
    revisits = 0
    for seq, grp_team in pr_groups.get((task_id, pr_number), []):
        if seq >= this_seq:
            continue
        revisits += 1
        if grp_team == "implementation_fix":
            fix_cycles += 1
    return fix_cycles, revisits


def _build_trajectory_record(
    task_id: str,
    inv_id: str,
    row: dict[str, Any],
    inv_to_pr: dict[tuple[str, str], int],
    inv_to_verdict: dict[tuple[str, str], str],
    inv_to_ts: dict[tuple[str, str], str],
    merge_index: dict[tuple[str, int], bool],
    pr_groups: dict[tuple[str, int], list[tuple[int, str]]],
) -> TrajectoryRecord:
    team = str(row.get("team") or "")
    turns_raw = row.get("turns")
    turns: int | None = turns_raw if isinstance(turns_raw, int) else None
    pr_number: int | None = inv_to_pr.get((task_id, inv_id))
    verdict: str | None = inv_to_verdict.get((task_id, inv_id))
    if pr_number is None:
        reward: bool | None = None
        fix_cycles, revisits = 0, 0
    else:
        survived = merge_index.get((task_id, pr_number))
        reward = bool(survived) if survived is not None else False
        fix_cycles, revisits = _compute_cycles(task_id, inv_id, pr_number, pr_groups)
    return TrajectoryRecord(
        task_id=task_id, inv_id=inv_id, pr_number=pr_number, team=team,
        verdict=verdict, reward=reward, fix_cycles=fix_cycles,
        revisits=revisits, turns=turns,
        verified_at=inv_to_ts.get((task_id, inv_id)) or None,
    )


def derive_trajectories(
    node_turns_text: str,
    verification_text: str,
    merge_audit_text: str,
    git_log_text: str,
) -> list[TrajectoryRecord]:
    turns_index = _deduplicate_node_turns(_parse_node_turns(node_turns_text))
    verif_index = _collapse_verifications(_parse_verification_rows(verification_text))
    merge_index = _build_merge_survived_index(
        parse_merge_audit_rows(merge_audit_text),
        parse_git_log_output(git_log_text),
    )
    inv_to_pr, inv_to_verdict, inv_to_ts = _build_inv_lookup(verif_index)
    pr_groups = _build_pr_groups(turns_index, inv_to_pr)
    return [
        _build_trajectory_record(
            task_id, inv_id, row, inv_to_pr, inv_to_verdict, inv_to_ts, merge_index, pr_groups
        )
        for (task_id, inv_id), row in sorted(turns_index.items())
    ]


__all__ = ["TrajectoryRecord", "derive_trajectories"]
