from __future__ import annotations

import json
import os
from pathlib import Path

from alan.trajectory import (
    _build_merge_survived_index,
    _build_pr_groups,
    _collapse_verifications,
    _deduplicate_node_turns,
    _find_revert,
    _inv_seq,
    _parse_node_turns,
    _parse_ts,
    _parse_verification_rows,
    derive_trajectories,
)
from alan.workspace import Workspace

_TS1 = "2026-01-01T00:00:00+00:00"
_TS2 = "2026-01-01T01:00:00+00:00"
_TS3 = "2026-01-01T02:00:00+00:00"
_TS_REVERT_FAST = "2026-01-02T00:00:00+00:00"
_TS_REVERT_SLOW = "2026-03-01T00:00:00+00:00"


def _jl(*rows: dict[str, object]) -> str:
    return "\n".join(json.dumps(r) for r in rows)


def _turns_row(
    task_id: str, inv_id: str, team: str, turns: int | None = None
) -> dict[str, object]:
    row: dict[str, object] = {
        "timestamp_utc": _TS1,
        "task_id": task_id,
        "inv_id": inv_id,
        "team": team,
    }
    if turns is not None:
        row["turns"] = turns
    return row


def _verif_row(
    task_id: str,
    pr_number: int,
    verdict: str,
    inv_id: str | None = None,
    ts: str = _TS2,
) -> dict[str, object]:
    row: dict[str, object] = {
        "timestamp_utc": ts,
        "task_id": task_id,
        "action": "review_ok",
        "pr_number": pr_number,
        "verdict": verdict,
    }
    if inv_id is not None:
        row["inv_id"] = inv_id
    return row


def _merge_row(task_id: str, pr_number: int, ts: str = _TS3) -> dict[str, object]:
    return {
        "timestamp_utc": ts,
        "task_id": task_id,
        "action": "merge_ok",
        "pr_number": pr_number,
    }


def _git_log(*entries: tuple[str, str]) -> str:
    sep = "\x1e"
    parts = []
    for ts, subject in entries:
        parts.append(f"{ts}\x00{subject}\x00")
    return sep.join(parts)


def test_derive_empty_inputs() -> None:
    assert derive_trajectories("", "", "", "") == []


def test_derive_happy_path_reward_true() -> None:
    turns = _jl(_turns_row("t1", "inv_001", "implementation", 10))
    verif = _jl(_verif_row("t1", 1, "APPROVE", "inv_001"))
    merge = _jl(_merge_row("t1", 1))
    records = derive_trajectories(turns, verif, merge, "")
    assert len(records) == 1
    rec = records[0]
    assert rec.task_id == "t1"
    assert rec.inv_id == "inv_001"
    assert rec.pr_number == 1
    assert rec.team == "implementation"
    assert rec.verdict == "APPROVE"
    assert rec.reward is True
    assert rec.fix_cycles == 0
    assert rec.revisits == 0
    assert rec.turns == 10


def test_derive_censored_no_verification() -> None:
    turns = _jl(_turns_row("t1", "inv_001", "implementation", 5))
    records = derive_trajectories(turns, "", "", "")
    assert len(records) == 1
    rec = records[0]
    assert rec.reward is None
    assert rec.pr_number is None
    assert rec.verdict is None
    assert rec.fix_cycles == 0
    assert rec.revisits == 0


def test_derive_reward_false_verified_but_no_merge() -> None:
    turns = _jl(_turns_row("t1", "inv_001", "implementation", 8))
    verif = _jl(_verif_row("t1", 1, "APPROVE", "inv_001"))
    records = derive_trajectories(turns, verif, "", "")
    assert len(records) == 1
    rec = records[0]
    assert rec.reward is False
    assert rec.verdict == "APPROVE"


def test_derive_reward_true_despite_request_changes_verdict() -> None:
    turns = _jl(_turns_row("t1", "inv_001", "implementation"))
    verif = _jl(_verif_row("t1", 1, "REQUEST_CHANGES", "inv_001"))
    merge = _jl(_merge_row("t1", 1))
    records = derive_trajectories(turns, verif, merge, "")
    assert len(records) == 1
    assert records[0].verdict == "REQUEST_CHANGES"
    assert records[0].reward is True


def test_derive_reverted_merge_reward_false() -> None:
    turns = _jl(_turns_row("t1", "inv_001", "implementation"))
    verif = _jl(_verif_row("t1", 1, "APPROVE", "inv_001"))
    merge = _jl(_merge_row("t1", 1, _TS3))
    git = _git_log((_TS_REVERT_FAST, 'Revert "feature (#1)"'))
    records = derive_trajectories(turns, verif, merge, git)
    assert len(records) == 1
    assert records[0].reward is False


def test_derive_merge_survived_after_window() -> None:
    turns = _jl(_turns_row("t1", "inv_001", "implementation"))
    verif = _jl(_verif_row("t1", 1, "APPROVE", "inv_001"))
    merge = _jl(_merge_row("t1", 1, _TS3))
    git = _git_log((_TS_REVERT_SLOW, 'Revert "feature (#1)"'))
    records = derive_trajectories(turns, verif, merge, git)
    assert len(records) == 1
    assert records[0].reward is True


def test_derive_multi_verif_collapse_takes_final() -> None:
    turns = _jl(_turns_row("t1", "inv_001", "implementation"))
    verif = _jl(
        _verif_row("t1", 1, "REQUEST_CHANGES", "inv_001", ts=_TS1),
        _verif_row("t1", 1, "APPROVE", "inv_001", ts=_TS2),
    )
    merge = _jl(_merge_row("t1", 1))
    records = derive_trajectories(turns, verif, merge, "")
    assert len(records) == 1
    assert records[0].verdict == "APPROVE"
    assert records[0].reward is True


def test_derive_multi_verif_collapse_later_request_changes() -> None:
    turns = _jl(_turns_row("t1", "inv_001", "implementation"))
    verif = _jl(
        _verif_row("t1", 1, "APPROVE", "inv_001", ts=_TS1),
        _verif_row("t1", 1, "REQUEST_CHANGES", "inv_001", ts=_TS2),
    )
    merge = _jl(_merge_row("t1", 1))
    records = derive_trajectories(turns, verif, merge, "")
    assert len(records) == 1
    assert records[0].verdict == "REQUEST_CHANGES"


def test_derive_schema_version_variant_no_inv_id_tolerated() -> None:
    turns = _jl(_turns_row("t1", "inv_001", "implementation"))
    verif = _jl(_verif_row("t1", 1, "APPROVE"))
    merge = _jl(_merge_row("t1", 1))
    records = derive_trajectories(turns, verif, merge, "")
    assert len(records) == 1
    assert records[0].reward is None
    assert records[0].verdict is None


def test_derive_fix_cycles_and_revisits() -> None:
    turns = _jl(
        _turns_row("t1", "inv_001", "implementation", 20),
        _turns_row("t1", "inv_002", "implementation_fix", 15),
        _turns_row("t1", "inv_003", "implementation_fix", 10),
    )
    verif = _jl(
        _verif_row("t1", 1, "REQUEST_CHANGES", "inv_001"),
        _verif_row("t1", 1, "REQUEST_CHANGES", "inv_002"),
        _verif_row("t1", 1, "APPROVE", "inv_003"),
    )
    merge = _jl(_merge_row("t1", 1))
    records = derive_trajectories(turns, verif, merge, "")
    by_inv = {r.inv_id: r for r in records}
    r1 = by_inv["inv_001"]
    assert r1.fix_cycles == 0
    assert r1.revisits == 0
    r2 = by_inv["inv_002"]
    assert r2.fix_cycles == 0
    assert r2.revisits == 1
    r3 = by_inv["inv_003"]
    assert r3.fix_cycles == 1
    assert r3.revisits == 2


def test_derive_censored_not_counted_as_failure_has_none_reward() -> None:
    turns = _jl(
        _turns_row("t1", "inv_001", "implementation"),
        _turns_row("t1", "inv_002", "implementation", 3),
    )
    verif = _jl(_verif_row("t1", 1, "APPROVE", "inv_002"))
    merge = _jl(_merge_row("t1", 1))
    records = derive_trajectories(turns, verif, merge, "")
    by_inv = {r.inv_id: r for r in records}
    assert by_inv["inv_001"].reward is None
    assert by_inv["inv_002"].reward is True


def test_derive_dry_run_merge_excluded_reward_false() -> None:
    turns = _jl(_turns_row("t1", "inv_001", "implementation"))
    verif = _jl(_verif_row("t1", 1, "APPROVE", "inv_001"))
    dry_merge = _jl({
        "timestamp_utc": _TS3,
        "task_id": "t1",
        "action": "dry_run_merge",
        "pr_number": 1,
    })
    records = derive_trajectories(turns, verif, dry_merge, "")
    assert len(records) == 1
    assert records[0].reward is False


def test_derive_idempotent() -> None:
    turns = _jl(_turns_row("t1", "inv_001", "implementation", 7))
    verif = _jl(_verif_row("t1", 1, "APPROVE", "inv_001"))
    merge = _jl(_merge_row("t1", 1))
    r1 = derive_trajectories(turns, verif, merge, "")
    r2 = derive_trajectories(turns, verif, merge, "")
    assert r1 == r2


def test_derive_turns_none_when_missing() -> None:
    turns = _jl(_turns_row("t1", "inv_001", "implementation"))
    records = derive_trajectories(turns, "", "", "")
    assert records[0].turns is None


def test_derive_dry_run_review_action_counts_as_verdict() -> None:
    turns = _jl(_turns_row("t1", "inv_001", "implementation"))
    verif = _jl({
        "timestamp_utc": _TS2,
        "task_id": "t1",
        "action": "dry_run",
        "pr_number": 1,
        "verdict": "APPROVE",
        "inv_id": "inv_001",
    })
    merge = _jl(_merge_row("t1", 1))
    records = derive_trajectories(turns, verif, merge, "")
    assert records[0].verdict == "APPROVE"
    assert records[0].reward is True


def test_derive_review_ok_inline_action_counts_as_verdict() -> None:
    turns = _jl(_turns_row("t1", "inv_001", "implementation"))
    verif = _jl({
        "timestamp_utc": _TS2,
        "task_id": "t1",
        "action": "review_ok_inline",
        "pr_number": 1,
        "verdict": "APPROVE",
        "inv_id": "inv_001",
    })
    merge = _jl(_merge_row("t1", 1))
    records = derive_trajectories(turns, verif, merge, "")
    assert records[0].verdict == "APPROVE"
    assert records[0].reward is True


def test_derive_review_ok_inline_request_changes_counts_as_verdict() -> None:
    turns = _jl(_turns_row("t1", "inv_001", "implementation"))
    verif = _jl({
        "timestamp_utc": _TS2,
        "task_id": "t1",
        "action": "review_ok_inline",
        "pr_number": 1,
        "verdict": "REQUEST_CHANGES",
        "inv_id": "inv_001",
    })
    records = derive_trajectories(turns, verif, "", "")
    assert records[0].verdict == "REQUEST_CHANGES"
    assert records[0].reward is False


def test_inv_seq_valid() -> None:
    assert _inv_seq("inv_001") == 1
    assert _inv_seq("inv_042") == 42


def test_inv_seq_none() -> None:
    assert _inv_seq(None) == -1


def test_inv_seq_invalid_format() -> None:
    assert _inv_seq("inv_abc") == -1
    assert _inv_seq("noprefix") == -1


def test_parse_node_turns_missing_task_id() -> None:
    text = _jl({"inv_id": "inv_001", "team": "implementation"})
    assert _parse_node_turns(text) == []


def test_parse_node_turns_non_string_task_id() -> None:
    text = _jl({"task_id": 123, "inv_id": "inv_001", "team": "implementation"})
    assert _parse_node_turns(text) == []


def test_parse_node_turns_empty_task_id() -> None:
    text = _jl({"task_id": "", "inv_id": "inv_001", "team": "implementation"})
    assert _parse_node_turns(text) == []


def test_parse_node_turns_missing_inv_id() -> None:
    text = _jl({"task_id": "t1", "team": "implementation"})
    assert _parse_node_turns(text) == []


def test_parse_node_turns_non_string_inv_id() -> None:
    text = _jl({"task_id": "t1", "inv_id": 1, "team": "implementation"})
    assert _parse_node_turns(text) == []


def test_parse_node_turns_invalid_json_line() -> None:
    text = "{invalid}\n" + _jl(_turns_row("t1", "inv_001", "implementation", 5))
    rows = _parse_node_turns(text)
    assert len(rows) == 1


def test_parse_node_turns_valid() -> None:
    rows = _parse_node_turns(_jl(_turns_row("t1", "inv_001", "implementation", 5)))
    assert len(rows) == 1
    assert rows[0]["task_id"] == "t1"


def test_parse_node_turns_missing_team() -> None:
    text = _jl({"task_id": "t1", "inv_id": "inv_001"})
    assert _parse_node_turns(text) == []


def test_parse_node_turns_non_string_team() -> None:
    text = _jl({"task_id": "t1", "inv_id": "inv_001", "team": 123})
    assert _parse_node_turns(text) == []


def test_parse_node_turns_empty_team() -> None:
    text = _jl({"task_id": "t1", "inv_id": "inv_001", "team": ""})
    assert _parse_node_turns(text) == []


def test_parse_verification_rows_invalid_json_line() -> None:
    text = "{bad}\n" + _jl(_verif_row("t1", 1, "APPROVE", "inv_001"))
    rows = _parse_verification_rows(text)
    assert len(rows) == 1


def test_parse_verification_rows_wrong_action() -> None:
    text = _jl({
        "task_id": "t1", "action": "skip",
        "pr_number": 1, "verdict": "APPROVE",
    })
    assert _parse_verification_rows(text) == []


def test_parse_verification_rows_missing_verdict() -> None:
    text = _jl({"task_id": "t1", "action": "review_ok", "pr_number": 1})
    assert _parse_verification_rows(text) == []


def test_parse_verification_rows_non_string_verdict() -> None:
    text = _jl({
        "task_id": "t1", "action": "review_ok",
        "pr_number": 1, "verdict": 42,
    })
    assert _parse_verification_rows(text) == []


def test_parse_verification_rows_missing_pr_number() -> None:
    text = _jl({"task_id": "t1", "action": "review_ok", "verdict": "APPROVE"})
    assert _parse_verification_rows(text) == []


def test_parse_verification_rows_non_int_pr_number() -> None:
    text = _jl({
        "task_id": "t1", "action": "review_ok",
        "pr_number": "1", "verdict": "APPROVE",
    })
    assert _parse_verification_rows(text) == []


def test_parse_verification_rows_valid() -> None:
    rows = _parse_verification_rows(_jl(_verif_row("t1", 1, "APPROVE", "inv_001")))
    assert len(rows) == 1
    assert rows[0]["verdict"] == "APPROVE"


def test_parse_verification_rows_missing_task_id() -> None:
    text = _jl({"action": "review_ok", "pr_number": 1, "verdict": "APPROVE"})
    assert _parse_verification_rows(text) == []


def test_parse_verification_rows_non_string_task_id() -> None:
    text = _jl({
        "task_id": 42, "action": "review_ok",
        "pr_number": 1, "verdict": "APPROVE",
    })
    assert _parse_verification_rows(text) == []


def test_parse_verification_rows_empty_task_id() -> None:
    text = _jl({
        "task_id": "", "action": "review_ok",
        "pr_number": 1, "verdict": "APPROVE",
    })
    assert _parse_verification_rows(text) == []


def test_collapse_verifications_single_row() -> None:
    rows = [{"task_id": "t1", "inv_id": "inv_001", "pr_number": 1,
             "verdict": "APPROVE", "timestamp_utc": _TS1, "action": "review_ok"}]
    index = _collapse_verifications(rows)
    assert index[("t1", "inv_001", 1)] == ("APPROVE", _TS1)


def test_collapse_verifications_takes_final_by_timestamp() -> None:
    rows = [
        {"task_id": "t1", "inv_id": "inv_001", "pr_number": 1,
         "verdict": "REQUEST_CHANGES", "timestamp_utc": _TS1},
        {"task_id": "t1", "inv_id": "inv_001", "pr_number": 1,
         "verdict": "APPROVE", "timestamp_utc": _TS2},
    ]
    index = _collapse_verifications(rows)
    assert index[("t1", "inv_001", 1)] == ("APPROVE", _TS2)


def test_collapse_verifications_none_inv_id() -> None:
    rows = [{"task_id": "t1", "pr_number": 1,
             "verdict": "APPROVE", "timestamp_utc": _TS1}]
    index = _collapse_verifications(rows)
    assert ("t1", None, 1) in index


def test_build_merge_survived_index_no_task_id() -> None:
    rows = [{"pr_number": 1, "timestamp_utc": _TS3, "action": "merge_ok"}]
    index = _build_merge_survived_index(rows, [])
    assert index == {}


def test_build_merge_survived_index_no_pr_number() -> None:
    rows = [{"task_id": "t1", "timestamp_utc": _TS3, "action": "merge_ok"}]
    index = _build_merge_survived_index(rows, [])
    assert index == {}


def test_build_merge_survived_index_no_timestamp() -> None:
    rows = [{"task_id": "t1", "pr_number": 1, "action": "merge_ok"}]
    index = _build_merge_survived_index(rows, [])
    assert index == {}


def test_build_merge_survived_index_survived() -> None:
    rows = [{"task_id": "t1", "pr_number": 1,
             "timestamp_utc": _TS3, "action": "merge_ok"}]
    index = _build_merge_survived_index(rows, [])
    assert index[("t1", 1)] is True


def test_build_merge_survived_index_reverted() -> None:
    rows = [{"task_id": "t1", "pr_number": 1,
             "timestamp_utc": _TS3, "action": "merge_ok"}]
    git = [(_TS_REVERT_FAST, 'Revert "fix (#1)"', "")]
    index = _build_merge_survived_index(rows, git)
    assert index[("t1", 1)] is False


def test_build_merge_survived_index_non_revert_git_skipped() -> None:
    rows = [{"task_id": "t1", "pr_number": 1,
             "timestamp_utc": _TS3, "action": "merge_ok"}]
    git = [(_TS_REVERT_SLOW, "Normal unrelated commit", "")]
    index = _build_merge_survived_index(rows, git)
    assert index[("t1", 1)] is True


def test_parse_ts_invalid_iso_string() -> None:
    assert _parse_ts({"timestamp_utc": "not-a-date"}) is None


def test_find_revert_invalid_ts_skipped() -> None:
    bad_git = [("NOTADATE", 'Revert "fix (#1)"', "")]
    assert _find_revert(1, bad_git) is None


def test_deduplicate_node_turns_single() -> None:
    row = _turns_row("t1", "inv_001", "implementation", 5)
    idx = _deduplicate_node_turns([row])
    assert idx == {("t1", "inv_001"): row}


def test_deduplicate_node_turns_last_wins() -> None:
    r1 = {**_turns_row("t1", "inv_001", "implementation", 5), "turns": 5}
    r2 = {**_turns_row("t1", "inv_001", "implementation", 10), "turns": 10}
    idx = _deduplicate_node_turns([r1, r2])
    assert idx[("t1", "inv_001")]["turns"] == 10


def test_build_pr_groups_skips_no_pr_number() -> None:
    turns = {("t1", "inv_001"): _turns_row("t1", "inv_001", "implementation")}
    inv_to_pr: dict[tuple[str, str], int] = {}
    groups = _build_pr_groups(turns, inv_to_pr)
    assert groups == {}


def test_build_pr_groups_includes_invocation() -> None:
    turns = {("t1", "inv_001"): _turns_row("t1", "inv_001", "implementation")}
    inv_to_pr = {("t1", "inv_001"): 1}
    groups = _build_pr_groups(turns, inv_to_pr)
    assert groups == {("t1", 1): [(1, "implementation")]}


def test_derive_inv_to_pr_dedup_later_ts_wins() -> None:
    turns = _jl(_turns_row("t1", "inv_001", "implementation"))
    verif = _jl(
        _verif_row("t1", 1, "REQUEST_CHANGES", "inv_001", ts=_TS1),
        _verif_row("t1", 2, "APPROVE", "inv_001", ts=_TS2),
    )
    merge = _jl(_merge_row("t1", 2))
    records = derive_trajectories(turns, verif, merge, "")
    assert len(records) == 1
    assert records[0].pr_number == 2
    assert records[0].reward is True


def test_derive_inv_to_pr_dedup_later_ts_wins_over_higher_pr() -> None:
    turns = _jl(_turns_row("t1", "inv_001", "implementation"))
    verif = _jl(
        _verif_row("t1", 5, "APPROVE", "inv_001", ts=_TS1),
        _verif_row("t1", 3, "REQUEST_CHANGES", "inv_001", ts=_TS2),
    )
    merge = _jl(_merge_row("t1", 5))
    records = derive_trajectories(turns, verif, merge, "")
    assert len(records) == 1
    assert records[0].pr_number == 3
    assert records[0].verdict == "REQUEST_CHANGES"
    assert records[0].reward is False


def test_derive_inv_to_pr_dedup_earlier_ts_skipped() -> None:
    turns = _jl(_turns_row("t1", "inv_001", "implementation"))
    verif = _jl(
        _verif_row("t1", 5, "APPROVE", "inv_001", ts=_TS2),
        _verif_row("t1", 3, "REQUEST_CHANGES", "inv_001", ts=_TS1),
    )
    merge = _jl(_merge_row("t1", 5))
    records = derive_trajectories(turns, verif, merge, "")
    assert len(records) == 1
    assert records[0].pr_number == 5
    assert records[0].verdict == "APPROVE"
    assert records[0].reward is True


def test_derive_verified_at_is_the_final_verification_timestamp() -> None:
    turns = _jl(_turns_row("t1", "inv_001", "implementation"))
    verif = _jl(
        _verif_row("t1", 1, "REQUEST_CHANGES", "inv_001", ts=_TS1),
        _verif_row("t1", 1, "APPROVE", "inv_001", ts=_TS2),
    )
    assert derive_trajectories(turns, verif, "", "")[0].verified_at == _TS2


def test_derive_verified_at_follows_timestamp_not_file_order() -> None:
    turns = _jl(_turns_row("t1", "inv_001", "implementation"))
    verif = _jl(
        _verif_row("t1", 1, "APPROVE", "inv_001", ts=_TS2),
        _verif_row("t1", 1, "REQUEST_CHANGES", "inv_001", ts=_TS1),
    )
    record = derive_trajectories(turns, verif, "", "")[0]
    assert record.verified_at == _TS2
    assert record.verdict == "APPROVE"


def test_derive_verified_at_is_carried_without_a_merge() -> None:
    turns = _jl(_turns_row("t1", "inv_001", "implementation"))
    verif = _jl(_verif_row("t1", 1, "REQUEST_CHANGES", "inv_001", ts=_TS3))
    record = derive_trajectories(turns, verif, "", "")[0]
    assert record.reward is False
    assert record.verified_at == _TS3


def test_derive_verified_at_is_none_when_the_invocation_was_never_verified() -> None:
    turns = _jl(_turns_row("t1", "inv_001", "implementation"))
    assert derive_trajectories(turns, "", "", "")[0].verified_at is None


def test_derive_verified_at_is_none_when_the_verification_row_has_no_timestamp() -> None:
    turns = _jl(_turns_row("t1", "inv_001", "implementation"))
    verif = _jl({
        "task_id": "t1",
        "action": "review_ok",
        "pr_number": 1,
        "verdict": "APPROVE",
        "inv_id": "inv_001",
    })
    record = derive_trajectories(turns, verif, "", "")[0]
    assert record.verdict == "APPROVE"
    assert record.verified_at is None


def test_derive_verified_at_belongs_to_the_pr_whose_verification_won() -> None:
    turns = _jl(_turns_row("t1", "inv_001", "implementation"))
    verif = _jl(
        _verif_row("t1", 5, "APPROVE", "inv_001", ts=_TS1),
        _verif_row("t1", 3, "REQUEST_CHANGES", "inv_001", ts=_TS2),
    )
    record = derive_trajectories(turns, verif, "", "")[0]
    assert record.pr_number == 3
    assert record.verified_at == _TS2


def test_derive_verified_at_is_per_invocation() -> None:
    turns = _jl(
        _turns_row("t1", "inv_001", "implementation"),
        _turns_row("t1", "inv_002", "implementation_fix"),
    )
    verif = _jl(
        _verif_row("t1", 1, "REQUEST_CHANGES", "inv_001", ts=_TS1),
        _verif_row("t1", 1, "APPROVE", "inv_002", ts=_TS3),
    )
    by_inv = {r.inv_id: r for r in derive_trajectories(turns, verif, "", "")}
    assert by_inv["inv_001"].verified_at == _TS1
    assert by_inv["inv_002"].verified_at == _TS3


def test_derive_multiple_tasks_independent() -> None:
    turns = _jl(
        _turns_row("t1", "inv_001", "implementation"),
        _turns_row("t2", "inv_001", "implementation"),
    )
    verif = _jl(
        _verif_row("t1", 1, "APPROVE", "inv_001"),
        _verif_row("t2", 2, "REQUEST_CHANGES", "inv_001"),
    )
    merge = _jl(_merge_row("t1", 1), _merge_row("t2", 2))
    records = derive_trajectories(turns, verif, merge, "")
    by_task = {r.task_id: r for r in records}
    assert by_task["t1"].reward is True
    assert by_task["t2"].reward is True
    assert by_task["t1"].pr_number == 1
    assert by_task["t2"].pr_number == 2


def test_emit_node_turns_audit_writes_with_turns(tmp_path: Path) -> None:
    from alan import workflow as workflow_mod
    from alan.teams._base import emit_node_turns_audit

    logs = tmp_path / "logs"
    orig = os.environ.get("ALAN_LOGS_ROOT")
    try:
        os.environ["ALAN_LOGS_ROOT"] = str(logs)
        ws = _make_minimal_workspace(tmp_path)
        specs = [workflow_mod.PendingTaskSpec(id="implementer", role="implementer", model="sonnet")]
        inv_id = workflow_mod.seed_pending_workflow(ws, team="implementation", tasks=specs)
        graph = workflow_mod.load_graph(ws, inv_id)
        graph.start_node("implementer", role="implementer", model="sonnet")
        usage = workflow_mod.NodeUsage(usage_raw={"num_turns": 7, "input_tokens": 100})
        graph.complete_node("implementer", usage=usage)
        emit_node_turns_audit("task_xyz", inv_id, "implementation", graph, "implementer")
        audit_path = logs / "node_turns_audit.jsonl"
        assert audit_path.exists()
        entry = json.loads(audit_path.read_text().strip())
        assert entry["task_id"] == "task_xyz"
        assert entry["inv_id"] == inv_id
        assert entry["team"] == "implementation"
        assert entry["turns"] == 7
    finally:
        if orig is None:
            del os.environ["ALAN_LOGS_ROOT"]
        else:
            os.environ["ALAN_LOGS_ROOT"] = orig


def test_emit_node_turns_audit_writes_without_turns_when_no_num_turns(
    tmp_path: Path,
) -> None:
    from alan import workflow as workflow_mod
    from alan.teams._base import emit_node_turns_audit

    logs = tmp_path / "logs"
    orig = os.environ.get("ALAN_LOGS_ROOT")
    try:
        os.environ["ALAN_LOGS_ROOT"] = str(logs)
        ws = _make_minimal_workspace(tmp_path)
        specs = [workflow_mod.PendingTaskSpec(id="implementer", role="implementer", model="sonnet")]
        inv_id = workflow_mod.seed_pending_workflow(ws, team="implementation", tasks=specs)
        graph = workflow_mod.load_graph(ws, inv_id)
        graph.start_node("implementer", role="implementer", model="sonnet")
        usage = workflow_mod.NodeUsage(usage_raw={"input_tokens": 50})
        graph.complete_node("implementer", usage=usage)
        emit_node_turns_audit("task_xyz", inv_id, "implementation", graph, "implementer")
        audit_path = logs / "node_turns_audit.jsonl"
        entry = json.loads(audit_path.read_text().strip())
        assert "turns" not in entry
    finally:
        if orig is None:
            del os.environ["ALAN_LOGS_ROOT"]
        else:
            os.environ["ALAN_LOGS_ROOT"] = orig


def test_emit_node_turns_audit_writes_without_turns_when_no_usage(
    tmp_path: Path,
) -> None:
    from alan import workflow as workflow_mod
    from alan.teams._base import emit_node_turns_audit

    logs = tmp_path / "logs"
    orig = os.environ.get("ALAN_LOGS_ROOT")
    try:
        os.environ["ALAN_LOGS_ROOT"] = str(logs)
        ws = _make_minimal_workspace(tmp_path)
        specs = [workflow_mod.PendingTaskSpec(id="implementer", role="implementer", model="sonnet")]
        inv_id = workflow_mod.seed_pending_workflow(ws, team="implementation", tasks=specs)
        graph = workflow_mod.load_graph(ws, inv_id)
        graph.start_node("implementer", role="implementer", model="sonnet")
        graph.complete_node("implementer", usage=None)
        emit_node_turns_audit("task_xyz", inv_id, "implementation", graph, "implementer")
        audit_path = logs / "node_turns_audit.jsonl"
        entry = json.loads(audit_path.read_text().strip())
        assert "turns" not in entry
    finally:
        if orig is None:
            del os.environ["ALAN_LOGS_ROOT"]
        else:
            os.environ["ALAN_LOGS_ROOT"] = orig


def test_emit_node_turns_audit_node_not_found(tmp_path: Path) -> None:
    from alan import workflow as workflow_mod
    from alan.teams._base import emit_node_turns_audit

    logs = tmp_path / "logs"
    orig = os.environ.get("ALAN_LOGS_ROOT")
    try:
        os.environ["ALAN_LOGS_ROOT"] = str(logs)
        ws = _make_minimal_workspace(tmp_path)
        specs = [workflow_mod.PendingTaskSpec(id="implementer", role="implementer", model="sonnet")]
        inv_id = workflow_mod.seed_pending_workflow(ws, team="implementation", tasks=specs)
        graph = workflow_mod.load_graph(ws, inv_id)
        emit_node_turns_audit("task_xyz", inv_id, "implementation", graph, "nonexistent_node")
        audit_path = logs / "node_turns_audit.jsonl"
        entry = json.loads(audit_path.read_text().strip())
        assert "turns" not in entry
        assert entry["task_id"] == "task_xyz"
    finally:
        if orig is None:
            del os.environ["ALAN_LOGS_ROOT"]
        else:
            os.environ["ALAN_LOGS_ROOT"] = orig


def test_emit_node_turns_audit_bool_num_turns_excluded(tmp_path: Path) -> None:
    from alan import workflow as workflow_mod
    from alan.teams._base import emit_node_turns_audit

    logs = tmp_path / "logs"
    orig = os.environ.get("ALAN_LOGS_ROOT")
    try:
        os.environ["ALAN_LOGS_ROOT"] = str(logs)
        ws = _make_minimal_workspace(tmp_path)
        specs = [workflow_mod.PendingTaskSpec(id="implementer", role="implementer", model="sonnet")]
        inv_id = workflow_mod.seed_pending_workflow(ws, team="implementation", tasks=specs)
        graph = workflow_mod.load_graph(ws, inv_id)
        graph.start_node("implementer", role="implementer", model="sonnet")
        usage = workflow_mod.NodeUsage(usage_raw={"num_turns": True})
        graph.complete_node("implementer", usage=usage)
        emit_node_turns_audit("task_xyz", inv_id, "implementation", graph, "implementer")
        audit_path = logs / "node_turns_audit.jsonl"
        entry = json.loads(audit_path.read_text().strip())
        assert "turns" not in entry
    finally:
        if orig is None:
            del os.environ["ALAN_LOGS_ROOT"]
        else:
            os.environ["ALAN_LOGS_ROOT"] = orig


def _make_minimal_workspace(tmp_path: Path) -> Workspace:
    from alan.workspace import create
    return create("test-task", "test description", root=tmp_path / "ws")
