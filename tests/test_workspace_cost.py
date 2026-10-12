from __future__ import annotations

import contextlib
import json
import os
import shutil
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from alan import _chat_core, prep_chat, store, workspace_cost
from alan import investigator as investigator_mod
from alan.workflow import WorkflowRowSummary
from tests.fs_timestamp_support import bump_mtime

_SESSION_A = "11111111-1111-1111-1111-111111111111"
_SESSION_B = "22222222-2222-2222-2222-222222222222"
_SESSION_C = "33333333-3333-3333-3333-333333333333"


def _write_result_stream(path: Path, *, cost: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    event = {"type": "result", "total_cost_usd": cost, "usage": {}}
    path.write_text(json.dumps(event) + "\n", encoding="utf-8")


def _derive(
    tmp_path: Path,
    *,
    inv_summaries: dict[str, WorkflowRowSummary] | None = None,
    chat_guards: workspace_cost.ChatCostGuards | None = None,
) -> workspace_cost.WorkspaceCostTotals:
    return workspace_cost.derive_cost_totals(
        inv_summaries=inv_summaries or {},
        ws_path=tmp_path,
        chat_guards=chat_guards or workspace_cost.ChatCostGuards(),
    )


def test_total_cost_usd_none_when_all_categories_none() -> None:
    assert workspace_cost.total_cost_usd(workspace_cost.WorkspaceCostTotals()) is None


def test_total_cost_usd_treats_absent_categories_as_zero() -> None:
    totals = workspace_cost.WorkspaceCostTotals(
        team_runs_usd=0.01, discussion_usd=0.02, investigator_usd=0.03
    )
    assert workspace_cost.total_cost_usd(totals) == pytest.approx(0.06)


def test_total_cost_usd_sums_all_six_categories() -> None:
    totals = workspace_cost.WorkspaceCostTotals(
        team_runs_usd=0.01,
        status_summary_usd=0.02,
        discussion_usd=0.03,
        collision_check_usd=0.04,
        investigator_usd=0.05,
        preparation_usd=0.06,
    )
    assert workspace_cost.total_cost_usd(totals) == pytest.approx(0.21)


def test_synthesize_cost_breakdown_has_six_entries_in_order() -> None:
    entries = workspace_cost.synthesize_cost_breakdown(workspace_cost.WorkspaceCostTotals())
    assert [e.key for e in entries] == [
        "team_runs",
        "status_summary",
        "discussion",
        "collision_check",
        "investigator",
        "preparation",
    ]
    assert all(e.run_summary is None for e in entries)


def test_synthesize_cost_breakdown_populates_run_summary_for_present_cost() -> None:
    totals = workspace_cost.WorkspaceCostTotals(discussion_usd=0.42)
    entries = workspace_cost.synthesize_cost_breakdown(totals)
    discussion = next(e for e in entries if e.key == "discussion")
    assert discussion.run_summary is not None
    assert discussion.run_summary.total_cost_usd == pytest.approx(0.42)
    others = [e for e in entries if e.key != "discussion"]
    assert all(e.run_summary is None for e in others)


def test_synthesize_cost_breakdown_labels() -> None:
    entries = workspace_cost.synthesize_cost_breakdown(workspace_cost.WorkspaceCostTotals())
    labels = {e.key: e.label for e in entries}
    assert labels["team_runs"] == "Team & agent runs"
    assert labels["status_summary"] == "Status summary"
    assert labels["discussion"] == "Discussion chat"
    assert labels["collision_check"] == "Collision check"
    assert labels["investigator"] == "Investigator"
    assert labels["preparation"] == "Preparation chat"


def test_ignores_non_dir_and_non_uuid_entries_under_chat(tmp_path: Path) -> None:
    chat_dir = tmp_path / "chat"
    chat_dir.mkdir()
    (chat_dir / "system_prompt.txt").write_text("prompt", encoding="utf-8")
    _write_result_stream(chat_dir / "not-a-uuid" / "stream.jsonl", cost=99.0)
    _write_result_stream(chat_dir / _SESSION_A / "stream.jsonl", cost=0.5)

    totals = _derive(tmp_path)
    assert totals.discussion_usd == pytest.approx(0.5)


def test_counts_every_session_dir_under_chat(tmp_path: Path) -> None:
    _write_result_stream(tmp_path / "chat" / _SESSION_A / "stream.jsonl", cost=0.1)
    _write_result_stream(tmp_path / "chat" / _SESSION_B / "stream.jsonl", cost=0.2)

    totals = _derive(tmp_path)
    assert totals.discussion_usd == pytest.approx(0.3)


def test_collision_check_is_per_session_only_never_global(tmp_path: Path) -> None:
    _write_result_stream(tmp_path / "chat" / _SESSION_A / "collision_stream.jsonl", cost=0.07)
    _write_result_stream(tmp_path / "collision" / "stream.jsonl", cost=999.0)

    totals = _derive(tmp_path)
    assert totals.collision_check_usd == pytest.approx(0.07)


def test_investigator_is_a_single_workspace_root_file(tmp_path: Path) -> None:
    _write_result_stream(tmp_path / "investigator_stream.jsonl", cost=1.23)
    totals = _derive(tmp_path)
    assert totals.investigator_usd == pytest.approx(1.23)


def test_folds_team_runs_from_inv_summaries(tmp_path: Path) -> None:
    inv_summaries = {
        "inv_1": WorkflowRowSummary(team="research", status="success", cost=0.1),
        "inv_2": WorkflowRowSummary(team="implementation", status="running", cost=0.2),
    }
    totals = _derive(tmp_path, inv_summaries=inv_summaries)
    assert totals.team_runs_usd == pytest.approx(0.3)


def test_status_summary_reads_manager_summary_stream(tmp_path: Path) -> None:
    _write_result_stream(tmp_path / "manager_summary_stream.jsonl", cost=0.09)
    totals = _derive(tmp_path)
    assert totals.status_summary_usd == pytest.approx(0.09)


def test_all_categories_none_when_nothing_present(tmp_path: Path) -> None:
    assert _derive(tmp_path) == workspace_cost.WorkspaceCostTotals()


def test_survives_status_summary_read_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(_ws_path: Path) -> None:
        raise RuntimeError("disk exploded")

    monkeypatch.setattr(workspace_cost, "read_manager_summary_usage", _boom)
    totals = _derive(tmp_path)
    assert totals.status_summary_usd is None


def test_folds_all_six_categories_together(tmp_path: Path) -> None:
    _write_result_stream(tmp_path / "manager_summary_stream.jsonl", cost=0.02)
    _write_result_stream(tmp_path / "chat" / _SESSION_A / "stream.jsonl", cost=0.03)
    _write_result_stream(tmp_path / "chat" / _SESSION_A / "collision_stream.jsonl", cost=0.04)
    _write_result_stream(tmp_path / "investigator_stream.jsonl", cost=0.05)
    _write_result_stream(tmp_path / "prep-chat" / _SESSION_B / "stream.jsonl", cost=0.06)
    inv_summaries = {"inv_1": WorkflowRowSummary(team="research", status="success", cost=0.01)}

    totals = _derive(tmp_path, inv_summaries=inv_summaries)
    assert totals.team_runs_usd == pytest.approx(0.01)
    assert totals.status_summary_usd == pytest.approx(0.02)
    assert totals.discussion_usd == pytest.approx(0.03)
    assert totals.collision_check_usd == pytest.approx(0.04)
    assert totals.investigator_usd == pytest.approx(0.05)
    assert totals.preparation_usd == pytest.approx(0.06)
    assert workspace_cost.total_cost_usd(totals) == pytest.approx(0.21)


def test_include_new_categories_false_defers_only_the_four_new_categories(
    tmp_path: Path,
) -> None:
    _write_result_stream(tmp_path / "manager_summary_stream.jsonl", cost=0.02)
    _write_result_stream(tmp_path / "chat" / _SESSION_A / "stream.jsonl", cost=0.03)
    _write_result_stream(tmp_path / "investigator_stream.jsonl", cost=0.04)
    _write_result_stream(tmp_path / "prep-chat" / _SESSION_B / "stream.jsonl", cost=0.05)
    inv_summaries = {"inv_1": WorkflowRowSummary(team="research", status="success", cost=0.01)}

    totals = workspace_cost.derive_cost_totals(
        inv_summaries=inv_summaries,
        ws_path=tmp_path,
        chat_guards=workspace_cost.ChatCostGuards(),
        include_new_categories=False,
    )
    assert totals.team_runs_usd == pytest.approx(0.01)
    assert totals.status_summary_usd == pytest.approx(0.02)
    assert totals.discussion_usd is None
    assert totals.collision_check_usd is None
    assert totals.investigator_usd is None
    assert totals.preparation_usd is None


def test_preparation_cost_sums_stream_and_collision_together(tmp_path: Path) -> None:
    _write_result_stream(tmp_path / "prep-chat" / _SESSION_A / "stream.jsonl", cost=0.10)
    _write_result_stream(tmp_path / "prep-chat" / _SESSION_A / "collision_stream.jsonl", cost=0.02)
    totals = _derive(tmp_path)
    assert totals.preparation_usd == pytest.approx(0.12)


def test_preparation_cost_none_when_no_archive(tmp_path: Path) -> None:
    totals = _derive(tmp_path)
    assert totals.preparation_usd is None


def test_preparation_cost_is_separate_from_discussion_and_collision_check(
    tmp_path: Path,
) -> None:
    _write_result_stream(tmp_path / "prep-chat" / _SESSION_A / "stream.jsonl", cost=0.5)
    totals = _derive(tmp_path)
    assert totals.discussion_usd is None
    assert totals.collision_check_usd is None
    assert totals.preparation_usd == pytest.approx(0.5)


def test_preparation_cost_ignores_non_dir_and_non_uuid_entries(tmp_path: Path) -> None:
    prep_dir = tmp_path / "prep-chat"
    prep_dir.mkdir()
    (prep_dir / "not-a-uuid").mkdir()
    _write_result_stream(prep_dir / _SESSION_A / "stream.jsonl", cost=0.2)
    totals = _derive(tmp_path)
    assert totals.preparation_usd == pytest.approx(0.2)


def test_preparation_cost_vanished_session_dropped_from_totals(tmp_path: Path) -> None:
    import shutil

    _write_result_stream(tmp_path / "prep-chat" / _SESSION_A / "stream.jsonl", cost=0.1)
    guards = workspace_cost.ChatCostGuards()
    first = _derive(tmp_path, chat_guards=guards)
    assert first.preparation_usd == pytest.approx(0.1)

    shutil.rmtree(tmp_path / "prep-chat" / _SESSION_A)
    second = _derive(tmp_path, chat_guards=guards)
    assert second.preparation_usd is None


def test_warm_second_call_does_not_recompute_unchanged_streams(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_result_stream(tmp_path / "chat" / _SESSION_A / "stream.jsonl", cost=0.1)
    _write_result_stream(tmp_path / "chat" / _SESSION_A / "collision_stream.jsonl", cost=0.2)
    _write_result_stream(tmp_path / "investigator_stream.jsonl", cost=0.3)
    guards = workspace_cost.ChatCostGuards()
    first = _derive(tmp_path, chat_guards=guards)

    def _boom(*_a: object, **_kw: object) -> None:
        raise AssertionError("derive_run_summary must not run on an unchanged stream")

    monkeypatch.setattr(workspace_cost, "derive_run_summary", _boom)
    second = _derive(tmp_path, chat_guards=guards)

    assert second.discussion_usd == first.discussion_usd == pytest.approx(0.1)
    assert second.collision_check_usd == first.collision_check_usd == pytest.approx(0.2)
    assert second.investigator_usd == first.investigator_usd == pytest.approx(0.3)


def test_stream_append_detected_without_chat_dir_mtime_change(tmp_path: Path) -> None:
    stream = tmp_path / "chat" / _SESSION_A / "stream.jsonl"
    _write_result_stream(stream, cost=0.1)
    guards = workspace_cost.ChatCostGuards()
    first = _derive(tmp_path, chat_guards=guards)
    assert first.discussion_usd == pytest.approx(0.1)

    chat_dir_mtime_before = (tmp_path / "chat").stat().st_mtime_ns
    with stream.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "result", "total_cost_usd": 0.05, "usage": {}}) + "\n")
    assert (tmp_path / "chat").stat().st_mtime_ns == chat_dir_mtime_before

    second = _derive(tmp_path, chat_guards=guards)
    assert second.discussion_usd == pytest.approx(0.15)


def test_vanished_session_dropped_from_totals(tmp_path: Path) -> None:
    import shutil

    _write_result_stream(tmp_path / "chat" / _SESSION_A / "stream.jsonl", cost=0.1)
    _write_result_stream(tmp_path / "chat" / _SESSION_B / "stream.jsonl", cost=0.2)
    guards = workspace_cost.ChatCostGuards()
    first = _derive(tmp_path, chat_guards=guards)
    assert first.discussion_usd == pytest.approx(0.3)

    shutil.rmtree(tmp_path / "chat" / _SESSION_B)
    second = _derive(tmp_path, chat_guards=guards)
    assert second.discussion_usd == pytest.approx(0.1)


def test_warm_refresh_listing_and_signature_calls_match_exact_formula(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chat_ids = [_SESSION_A, _SESSION_B, _SESSION_C]
    prep_ids = ["44444444-4444-4444-4444-444444444444", "55555555-5555-5555-5555-555555555555"]
    cleared_ids = ["66666666-6666-6666-6666-666666666666", "77777777-7777-7777-7777-777777777777"]
    for sid in chat_ids:
        _write_result_stream(tmp_path / "chat" / sid / "stream.jsonl", cost=0.1)
    for sid in prep_ids:
        _write_result_stream(tmp_path / "prep-chat" / sid / "stream.jsonl", cost=0.1)
    for sid in cleared_ids:
        _write_tombstone(tmp_path, sid, discussion=0.1, collision=None)
    _write_result_stream(tmp_path / "investigator_stream.jsonl", cost=0.2)

    guards = workspace_cost.ChatCostGuards()
    first = _derive(tmp_path, chat_guards=guards)

    calls = {"list_dir": 0, "listing_entry": 0, "stat_signature": 0}
    originals: dict[str, Callable[..., Any]] = {
        "list_dir": store.list_dir,
        "listing_entry": store.listing_entry,
        "stat_signature": store.stat_signature,
    }

    def _counted(name: str) -> Callable[..., Any]:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            calls[name] += 1
            return originals[name](*args, **kwargs)

        return wrapper

    for name in calls:
        monkeypatch.setattr(store, name, _counted(name))
    second = _derive(tmp_path, chat_guards=guards)

    assert second == first
    sessions = len(chat_ids) + len(prep_ids)
    assert calls["list_dir"] == 3
    assert calls["listing_entry"] == sessions + len(cleared_ids)
    assert calls["stat_signature"] == 2 * sessions + 1


def test_warm_read_parses_no_stream_or_cleared_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_result_stream(tmp_path / "chat" / _SESSION_A / "stream.jsonl", cost=0.1)
    _write_result_stream(tmp_path / "prep-chat" / _SESSION_B / "stream.jsonl", cost=0.2)
    _write_result_stream(tmp_path / "investigator_stream.jsonl", cost=0.3)
    _write_tombstone(tmp_path, _SESSION_C, discussion=0.4, collision=None)
    guards = workspace_cost.ChatCostGuards()
    first = _derive(tmp_path, chat_guards=guards)

    def _boom(*_a: object, **_kw: object) -> None:
        raise AssertionError("a warm read must not parse")

    monkeypatch.setattr(workspace_cost, "derive_run_summary", _boom)
    monkeypatch.setattr(workspace_cost.ClearedSessionCost, "model_validate_json", _boom)
    assert _derive(tmp_path, chat_guards=guards) == first


def test_session_dir_listing_ignores_regular_file_named_like_session(tmp_path: Path) -> None:
    chat_dir = tmp_path / "chat"
    chat_dir.mkdir()
    (chat_dir / _SESSION_A).write_text("not a directory", encoding="utf-8")
    _write_result_stream(chat_dir / _SESSION_B / "stream.jsonl", cost=0.2)
    guards = workspace_cost.ChatCostGuards()
    totals = _derive(tmp_path, chat_guards=guards)
    assert guards.session_ids == (_SESSION_B,)
    assert totals.discussion_usd == pytest.approx(0.2)


def test_warm_guards_equal_fresh_guards_after_mixed_mutations(tmp_path: Path) -> None:
    _write_result_stream(tmp_path / "chat" / _SESSION_A / "stream.jsonl", cost=0.1)
    _write_result_stream(tmp_path / "prep-chat" / _SESSION_A / "stream.jsonl", cost=0.2)
    _write_tombstone(tmp_path, _SESSION_A, discussion=0.3, collision=None)
    guards = workspace_cost.ChatCostGuards()
    _derive(tmp_path, chat_guards=guards)

    with contextlib.ExitStack() as held:
        for name in ("chat", "prep-chat", "cleared-chat-cost"):
            held.enter_context(_held_mtime(tmp_path / name))
        shutil.rmtree(tmp_path / "chat" / _SESSION_A)
        _write_result_stream(tmp_path / "chat" / _SESSION_B / "stream.jsonl", cost=0.4)
        shutil.rmtree(tmp_path / "prep-chat" / _SESSION_A)
        _write_result_stream(tmp_path / "prep-chat" / _SESSION_C / "stream.jsonl", cost=0.5)
        (tmp_path / "cleared-chat-cost" / f"{_SESSION_A}.json").unlink()
        _write_tombstone(tmp_path, _SESSION_C, discussion=0.6, collision=0.7)

    warm = _derive(tmp_path, chat_guards=guards)
    fresh_guards = workspace_cost.ChatCostGuards()
    assert warm == _derive(tmp_path, chat_guards=fresh_guards)
    assert guards.session_ids == fresh_guards.session_ids
    assert guards.prep_session_ids == fresh_guards.prep_session_ids
    assert guards.cleared_costs == fresh_guards.cleared_costs


def test_legacy_categories_off_lists_no_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_result_stream(tmp_path / "chat" / _SESSION_A / "stream.jsonl", cost=0.1)
    listed: list[Path] = []
    real = store.list_dir

    def spy(path: Path) -> list[Path]:
        listed.append(path)
        return real(path)

    monkeypatch.setattr(store, "list_dir", spy)
    workspace_cost.derive_cost_totals(
        inv_summaries={},
        ws_path=tmp_path,
        chat_guards=workspace_cost.ChatCostGuards(),
        include_new_categories=False,
    )
    assert listed == []


def test_stream_filenames_locked_to_chat_core() -> None:
    assert workspace_cost._STREAM_FILE == _chat_core._STREAM_FILE
    assert workspace_cost._COLLISION_STREAM_FILE == _chat_core._COLLISION_STREAM_FILE


def test_session_id_regex_locked_to_chat_core() -> None:
    assert workspace_cost._SESSION_ID_RE.pattern == _chat_core._SESSION_ID_RE.pattern


def test_investigator_stream_filename_locked_to_investigator_module() -> None:
    assert workspace_cost._INVESTIGATOR_STREAM_FILE == investigator_mod._STREAM_FILE


def test_prep_chat_dir_name_locked_to_prep_chat_module() -> None:
    assert workspace_cost._PREP_CHAT_DIR_NAME == prep_chat.PREP_CHAT_DIR_NAME


def _write_tombstone(
    ws_path: Path, session_id: str, *, discussion: float | None, collision: float | None
) -> None:
    cleared_dir = ws_path / "cleared-chat-cost"
    cleared_dir.mkdir(parents=True, exist_ok=True)
    tombstone = workspace_cost.ClearedSessionCost(
        discussion_usd=discussion, collision_check_usd=collision
    )
    (cleared_dir / f"{session_id}.json").write_text(
        tombstone.model_dump_json(), encoding="utf-8"
    )


def test_live_session_excludes_its_own_tombstone(tmp_path: Path) -> None:
    _write_result_stream(tmp_path / "chat" / _SESSION_A / "stream.jsonl", cost=0.1)
    _write_tombstone(tmp_path, _SESSION_A, discussion=999.0, collision=None)
    totals = _derive(tmp_path)
    assert totals.discussion_usd == pytest.approx(0.1)


def test_cleared_session_counted_once_after_live_dir_removed(tmp_path: Path) -> None:
    import shutil

    session_dir = tmp_path / "chat" / _SESSION_A
    _write_result_stream(session_dir / "stream.jsonl", cost=0.1)
    _write_tombstone(tmp_path, _SESSION_A, discussion=0.1, collision=None)
    shutil.rmtree(session_dir)
    totals = _derive(tmp_path)
    assert totals.discussion_usd == pytest.approx(0.1)


def test_cleared_session_preserves_collision_check_cost(tmp_path: Path) -> None:
    _write_tombstone(tmp_path, _SESSION_A, discussion=0.1, collision=0.03)
    totals = _derive(tmp_path)
    assert totals.discussion_usd == pytest.approx(0.1)
    assert totals.collision_check_usd == pytest.approx(0.03)


def test_cleared_cost_keeps_discussion_and_collision_categories(tmp_path: Path) -> None:
    _write_tombstone(tmp_path, _SESSION_A, discussion=0.2, collision=0.05)
    totals = _derive(tmp_path)
    entries = {e.key: e for e in workspace_cost.synthesize_cost_breakdown(totals)}
    assert entries["discussion"].run_summary is not None
    assert entries["discussion"].run_summary.total_cost_usd == pytest.approx(0.2)
    assert entries["collision_check"].run_summary is not None
    assert entries["collision_check"].run_summary.total_cost_usd == pytest.approx(0.05)


def test_multiple_cleared_sessions_sum_independently(tmp_path: Path) -> None:
    _write_tombstone(tmp_path, _SESSION_A, discussion=0.1, collision=None)
    _write_tombstone(tmp_path, _SESSION_B, discussion=0.2, collision=0.01)
    totals = _derive(tmp_path)
    assert totals.discussion_usd == pytest.approx(0.3)
    assert totals.collision_check_usd == pytest.approx(0.01)


def test_repeated_clear_restart_accumulates_total(tmp_path: Path) -> None:
    _write_tombstone(tmp_path, _SESSION_A, discussion=0.1, collision=None)
    first = workspace_cost.total_cost_usd(_derive(tmp_path))

    _write_tombstone(tmp_path, _SESSION_B, discussion=0.2, collision=None)
    second = workspace_cost.total_cost_usd(_derive(tmp_path))

    _write_tombstone(tmp_path, _SESSION_C, discussion=0.15, collision=None)
    third = workspace_cost.total_cost_usd(_derive(tmp_path))

    live_session = "44444444-4444-4444-4444-444444444444"
    _write_result_stream(tmp_path / "chat" / live_session / "stream.jsonl", cost=0.05)
    fourth = workspace_cost.total_cost_usd(_derive(tmp_path))

    assert first == pytest.approx(0.1)
    assert second == pytest.approx(0.3)
    assert third == pytest.approx(0.45)
    assert fourth == pytest.approx(0.5)
    assert first is not None
    assert second is not None
    assert third is not None
    assert fourth is not None
    assert first <= second <= third <= fourth


def test_missing_cleared_cost_dir_yields_no_contribution(tmp_path: Path) -> None:
    totals = _derive(tmp_path)
    assert totals.discussion_usd is None
    assert totals.collision_check_usd is None


def test_corrupt_cleared_cost_file_is_skipped_and_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    cleared_dir = tmp_path / "cleared-chat-cost"
    cleared_dir.mkdir(parents=True)
    (cleared_dir / f"{_SESSION_A}.json").write_text("not json{{{", encoding="utf-8")
    _write_tombstone(tmp_path, _SESSION_B, discussion=0.3, collision=None)
    with caplog.at_level("WARNING", logger="alan.workspace_cost"):
        totals = _derive(tmp_path)
    assert totals.discussion_usd == pytest.approx(0.3)
    assert any("cleared-cost" in rec.message for rec in caplog.records)


def test_unreadable_cleared_cost_file_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_tombstone(tmp_path, _SESSION_A, discussion=0.1, collision=None)
    _write_tombstone(tmp_path, _SESSION_B, discussion=0.2, collision=None)

    original_read_text = Path.read_text

    def boom(self: Path, encoding: str | None = None, errors: str | None = None) -> str:
        if self.name == f"{_SESSION_A}.json":
            raise OSError("permission denied")
        return original_read_text(self, encoding=encoding, errors=errors)

    monkeypatch.setattr(Path, "read_text", boom)
    totals = _derive(tmp_path)
    assert totals.discussion_usd == pytest.approx(0.2)


def test_cleared_costs_dir_mtime_guard_picks_up_new_tombstone(tmp_path: Path) -> None:
    guards = workspace_cost.ChatCostGuards()
    _write_tombstone(tmp_path, _SESSION_A, discussion=0.1, collision=None)
    first = _derive(tmp_path, chat_guards=guards)
    assert first.discussion_usd == pytest.approx(0.1)

    _write_tombstone(tmp_path, _SESSION_B, discussion=0.2, collision=None)
    bump_mtime(tmp_path / "cleared-chat-cost")
    second = _derive(tmp_path, chat_guards=guards)
    assert second.discussion_usd == pytest.approx(0.3)

    third = _derive(tmp_path, chat_guards=guards)
    assert third.discussion_usd == pytest.approx(0.3)


def test_include_new_categories_false_skips_cleared_costs(tmp_path: Path) -> None:
    _write_tombstone(tmp_path, _SESSION_A, discussion=0.1, collision=None)
    totals = workspace_cost.derive_cost_totals(
        inv_summaries={},
        ws_path=tmp_path,
        chat_guards=workspace_cost.ChatCostGuards(),
        include_new_categories=False,
    )
    assert totals.discussion_usd is None


@contextlib.contextmanager
def _held_mtime(directory: Path) -> Iterator[None]:
    ns = directory.stat().st_mtime_ns
    yield
    os.utime(directory, ns=(ns, ns))
    assert directory.stat().st_mtime_ns == ns


def _fresh_totals(tmp_path: Path) -> workspace_cost.WorkspaceCostTotals:
    return _derive(tmp_path, chat_guards=workspace_cost.ChatCostGuards())


def test_prep_session_moved_into_empty_dir_counted_when_dir_mtime_held(
    tmp_path: Path,
) -> None:
    prep_dir = tmp_path / "prep-chat"
    prep_dir.mkdir()
    guards = workspace_cost.ChatCostGuards()
    assert _derive(tmp_path, chat_guards=guards).preparation_usd is None

    staged = tmp_path / "staging" / _SESSION_A
    _write_result_stream(staged / "stream.jsonl", cost=0.4)
    with _held_mtime(prep_dir):
        staged.rename(prep_dir / _SESSION_A)

    second = _derive(tmp_path, chat_guards=guards)
    assert second.preparation_usd == pytest.approx(0.4)
    assert second == _fresh_totals(tmp_path)


def test_prep_session_removed_dropped_from_known_ids_when_dir_mtime_held(
    tmp_path: Path,
) -> None:
    session_dir = tmp_path / "prep-chat" / _SESSION_A
    _write_result_stream(session_dir / "stream.jsonl", cost=0.1)
    guards = workspace_cost.ChatCostGuards()
    _derive(tmp_path, chat_guards=guards)
    assert set(guards.prep_session_ids) == {_SESSION_A}
    assert set(guards.prep_sessions) == {_SESSION_A}

    with _held_mtime(tmp_path / "prep-chat"):
        shutil.rmtree(session_dir)

    _derive(tmp_path, chat_guards=guards)
    assert guards.prep_session_ids == ()
    assert guards.prep_sessions == {}


def test_cleared_record_added_counted_when_dir_mtime_held(tmp_path: Path) -> None:
    _write_tombstone(tmp_path, _SESSION_A, discussion=0.1, collision=None)
    guards = workspace_cost.ChatCostGuards()
    assert _derive(tmp_path, chat_guards=guards).discussion_usd == pytest.approx(0.1)

    with _held_mtime(tmp_path / "cleared-chat-cost"):
        _write_tombstone(tmp_path, _SESSION_B, discussion=0.2, collision=None)

    second = _derive(tmp_path, chat_guards=guards)
    assert second.discussion_usd == pytest.approx(0.3)
    assert second == _fresh_totals(tmp_path)


def test_cleared_record_removed_not_counted_when_dir_mtime_held(tmp_path: Path) -> None:
    _write_tombstone(tmp_path, _SESSION_A, discussion=0.1, collision=None)
    _write_tombstone(tmp_path, _SESSION_B, discussion=0.2, collision=None)
    guards = workspace_cost.ChatCostGuards()
    assert _derive(tmp_path, chat_guards=guards).discussion_usd == pytest.approx(0.3)

    with _held_mtime(tmp_path / "cleared-chat-cost"):
        (tmp_path / "cleared-chat-cost" / f"{_SESSION_B}.json").unlink()

    second = _derive(tmp_path, chat_guards=guards)
    assert second.discussion_usd == pytest.approx(0.1)
    assert second == _fresh_totals(tmp_path)


def test_chat_session_added_counted_when_dir_mtime_held(tmp_path: Path) -> None:
    chat_dir = tmp_path / "chat"
    chat_dir.mkdir()
    guards = workspace_cost.ChatCostGuards()
    assert _derive(tmp_path, chat_guards=guards).discussion_usd is None

    with _held_mtime(chat_dir):
        _write_result_stream(chat_dir / _SESSION_A / "stream.jsonl", cost=0.5)

    second = _derive(tmp_path, chat_guards=guards)
    assert second.discussion_usd == pytest.approx(0.5)
    assert second == _fresh_totals(tmp_path)


def test_chat_session_removed_dropped_from_known_ids_when_dir_mtime_held(
    tmp_path: Path,
) -> None:
    session_dir = tmp_path / "chat" / _SESSION_A
    _write_result_stream(session_dir / "stream.jsonl", cost=0.1)
    guards = workspace_cost.ChatCostGuards()
    _derive(tmp_path, chat_guards=guards)
    assert set(guards.session_ids) == {_SESSION_A}
    assert set(guards.sessions) == {_SESSION_A}

    with _held_mtime(tmp_path / "chat"):
        shutil.rmtree(session_dir)

    _derive(tmp_path, chat_guards=guards)
    assert guards.session_ids == ()
    assert guards.sessions == {}
