from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

import alan._dispatcher_base as dispatcher_mod
from alan import investigator
from alan.claude_cli import AgentStreamingResult, TurnCeilingReached
from alan.handover import Handover
from alan.incident import derive_incidents
from alan.park import ParkReason
from alan.queue import Queue
from alan.rca_store import RcaDraft, get_rca_draft, load_rca_drafts, save_rca_draft
from alan.teams import TEAMS
from alan.workspace import Meta, Status, Workspace

_REAL_TRIGGER_INVESTIGATOR = investigator.trigger_investigator


@pytest.fixture(autouse=True)
def _restore_real_trigger_investigator(
    _no_real_investigator_spawn: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(investigator, "trigger_investigator", _REAL_TRIGGER_INVESTIGATOR)


def _make_ws(
    task_id: str = "ws1",
    *,
    status: Status = Status.ACTIVE,
    park_reason: ParkReason | None = None,
    team: str | None = None,
) -> Workspace:
    ts = datetime(2026, 1, 1, tzinfo=UTC)
    meta = Meta(
        task_id=task_id,
        title="t",
        description="d",
        status=status,
        park_reason=park_reason,
        team=team,
        created_at=ts,
        updated_at=ts,
    )
    return Workspace(task_id=task_id, path=Path(task_id), meta=meta)


def _make_draft(**overrides: Any) -> RcaDraft:
    defaults: dict[str, Any] = {
        "source": "investigator",
        "proposed_disposition": None,
        "timeline": (),
        "falsification_test": "rerun and confirm",
        "recurrence_signature": "sig",
        "recipe": "",
        "recurrence_signal": "prior-signal",
        "verified": False,
    }
    defaults.update(overrides)
    return RcaDraft(**defaults)


def test_investigator_allowed_tools_no_write() -> None:
    forbidden = {"Edit", "Write", "NotebookEdit"}
    assert not (forbidden & set(investigator._INVESTIGATOR_ALLOWED_TOOLS))  # noqa: SLF001
    dangerous = ("push", "commit", "merge", "create", " rm", "mv:", "cp:", "git:*", "gh:*")
    for tool in investigator._INVESTIGATOR_ALLOWED_TOOLS:  # noqa: SLF001
        lowered = tool.lower()
        assert not any(bad in lowered for bad in dangerous)


def test_investigator_not_in_TEAMS_unreachable() -> None:
    assert "investigator" not in TEAMS
    assert not hasattr(investigator, "TEAM")


def test_fabricated_citation_dropped(tmp_path: Path) -> None:
    real_file = tmp_path / "audit.jsonl"
    real_file.write_text('{"error": "boom"}\n')
    raw_items: list[Any] = [
        {
            "description": "real",
            "citation": {"kind": "log_line", "file": str(real_file), "grep_pattern": "boom"},
        },
        {
            "description": "fabricated",
            "citation": {
                "kind": "log_line", "file": str(real_file), "grep_pattern": "never-appears",
            },
        },
    ]
    resolved, all_resolved = investigator._resolve_timeline(raw_items)  # noqa: SLF001
    assert [e.description for e in resolved] == ["real"]
    assert all_resolved is False


def test_fabricated_citation_missing_file_dropped(tmp_path: Path) -> None:
    raw_items: list[Any] = [
        {
            "description": "missing file",
            "citation": {
                "kind": "audit_row",
                "file": str(tmp_path / "does-not-exist.jsonl"),
                "json_path": "$.error",
            },
        },
    ]
    resolved, all_resolved = investigator._resolve_timeline(raw_items)  # noqa: SLF001
    assert resolved == ()
    assert all_resolved is False


def test_audit_row_citation_resolves(tmp_path: Path) -> None:
    real_file = tmp_path / "job_failure_audit.jsonl"
    real_file.write_text('{"error": "boom", "task_id": "x"}\n')
    raw_items: list[Any] = [
        {
            "description": "audit row",
            "citation": {"kind": "audit_row", "file": str(real_file), "json_path": "error"},
        },
    ]
    resolved, all_resolved = investigator._resolve_timeline(raw_items)  # noqa: SLF001
    assert len(resolved) == 1
    assert all_resolved is True


def test_replay_command_off_allowlist_rejected() -> None:
    raw_items: list[Any] = [
        {
            "description": "destructive replay",
            "citation": {"kind": "replay_command", "argv": ["rm", "-rf", "/"]},
        },
    ]
    resolved, all_resolved = investigator._resolve_timeline(raw_items)  # noqa: SLF001
    assert resolved == ()
    assert all_resolved is False


def test_replay_command_on_allowlist_resolves() -> None:
    raw_items: list[Any] = [
        {
            "description": "read-only replay",
            "citation": {"kind": "replay_command", "argv": ["git", "log", "--oneline", "-5"]},
        },
    ]
    resolved, all_resolved = investigator._resolve_timeline(raw_items)  # noqa: SLF001
    assert len(resolved) == 1
    assert all_resolved is True


def test_replay_command_resolves_empty_argv_false() -> None:
    assert investigator._replay_command_resolves(None) is False  # noqa: SLF001
    assert investigator._replay_command_resolves(()) is False  # noqa: SLF001


def test_resolve_timeline_empty_not_verified() -> None:
    resolved, all_resolved = investigator._resolve_timeline([])  # noqa: SLF001
    assert resolved == ()
    assert all_resolved is False


def test_locatorless_citation_dropped_before_resolution() -> None:
    raw_items: list[Any] = [
        {"description": "no locator", "citation": {"kind": "audit_row", "file": "x"}},
    ]
    resolved, all_resolved = investigator._resolve_timeline(raw_items)  # noqa: SLF001
    assert resolved == ()
    assert all_resolved is False


def test_citation_from_raw_not_a_dict() -> None:
    assert investigator._citation_from_raw("not-a-dict") is None  # noqa: SLF001


def test_citation_from_raw_missing_kind() -> None:
    assert investigator._citation_from_raw({"file": "x"}) is None  # noqa: SLF001


def test_citation_from_raw_argv_not_a_list() -> None:
    raw = {"kind": "replay_command", "argv": "git log"}
    assert investigator._citation_from_raw(raw) is None  # noqa: SLF001


def test_timeline_entry_from_raw_not_a_dict() -> None:
    assert investigator._timeline_entry_from_raw("nope") is None  # noqa: SLF001


def test_timeline_entry_from_raw_invalid_citation() -> None:
    assert investigator._timeline_entry_from_raw({"citation": {"kind": "bogus"}}) is None  # noqa: SLF001


def test_timeline_entry_from_raw_missing_description_defaults_empty() -> None:
    raw = {"citation": {"kind": "replay_command", "argv": ["cat", "x"]}}
    entry = investigator._timeline_entry_from_raw(raw)  # noqa: SLF001
    assert entry is not None
    assert entry.description == ""


def test_file_contains_missing_file_returns_false(tmp_path: Path) -> None:
    assert investigator._file_contains(str(tmp_path / "nope.log"), "x") is False  # noqa: SLF001


def test_file_contains_needle_spanning_chunk_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(investigator, "_FILE_SCAN_CHUNK_SIZE", 8)
    needle = "boundary-needle"
    real_file = tmp_path / "big.log"
    real_file.write_text(("x" * 5) + needle + ("y" * 20))
    assert investigator._file_contains(str(real_file), needle) is True  # noqa: SLF001
    assert investigator._file_contains(str(real_file), "not-present") is False  # noqa: SLF001


def test_parse_rca_yaml_no_marker_returns_none() -> None:
    assert investigator._parse_rca_yaml("just some prose, no block here") is None  # noqa: SLF001


def test_parse_rca_yaml_malformed_yaml_returns_none() -> None:
    text = "## RCA_DRAFT_YAML\n```yaml\nfoo: [unclosed\n```\n"
    assert investigator._parse_rca_yaml(text) is None  # noqa: SLF001


def test_parse_rca_yaml_not_a_mapping_returns_none() -> None:
    text = "## RCA_DRAFT_YAML\n```yaml\n- a\n- b\n```\n"
    assert investigator._parse_rca_yaml(text) is None  # noqa: SLF001


def test_parse_rca_yaml_valid_block() -> None:
    text = (
        "Some analysis.\n\n"
        "## RCA_DRAFT_YAML\n"
        "```yaml\n"
        "falsification_test: rerun it\n"
        "recipe: fix it\n"
        "```\n"
    )
    parsed = investigator._parse_rca_yaml(text)  # noqa: SLF001
    assert parsed == {"falsification_test": "rerun it", "recipe": "fix it"}


def _build_draft(
    parsed: dict[str, Any], valid_dispositions: frozenset[str] = frozenset()
) -> RcaDraft | None:
    return investigator._build_draft(parsed, "sig", valid_dispositions, None, 0.0)  # noqa: SLF001


def test_build_draft_missing_falsification_test_none() -> None:
    assert _build_draft({}) is None


def test_build_draft_falsification_test_not_a_string_none() -> None:
    parsed = {"falsification_test": 123}
    assert _build_draft(parsed) is None


def test_build_draft_falsification_test_empty_string_none() -> None:
    parsed = {"falsification_test": ""}
    assert _build_draft(parsed) is None


def test_build_draft_timeline_not_a_list_treated_empty() -> None:
    parsed = {"falsification_test": "check", "timeline": "not-a-list"}
    draft = _build_draft(parsed)
    assert draft is not None
    assert draft.timeline == ()
    assert draft.verified is False


def test_build_draft_proposed_disposition_not_a_string() -> None:
    parsed = {"falsification_test": "check", "proposed_disposition": 5}
    draft = _build_draft(parsed, frozenset({"root_caused"}))
    assert draft is not None
    assert draft.proposed_disposition is None


def test_build_draft_proposed_disposition_not_in_valid_set() -> None:
    parsed = {"falsification_test": "check", "proposed_disposition": "made_up"}
    draft = _build_draft(parsed, frozenset({"root_caused"}))
    assert draft is not None
    assert draft.proposed_disposition is None


def test_build_draft_fully_valid_verified() -> None:
    parsed = {
        "falsification_test": "check",
        "proposed_disposition": "root_caused",
        "recurrence_signature": "sig-x",
        "recipe": "do the thing",
        "timeline": [
            {
                "description": "step",
                "citation": {"kind": "replay_command", "argv": ["cat", "x"]},
            }
        ],
    }
    draft = _build_draft(parsed, frozenset({"root_caused"}))
    assert draft is not None
    assert draft.proposed_disposition == "root_caused"
    assert draft.verified is True
    assert len(draft.timeline) == 1


def test_build_user_prompt_contains_fingerprint_and_dispositions() -> None:
    prompt = investigator._build_user_prompt(  # noqa: SLF001
        "fp-1", {"status": "failed"}, frozenset({"root_caused", "known_flake"})
    )
    assert "fp-1" in prompt
    assert "known_flake" in prompt
    assert "root_caused" in prompt
    assert "failed" in prompt


def test_compute_recurrence_signal_deterministic() -> None:
    a = investigator._compute_recurrence_signal({"x": 1, "y": 2})  # noqa: SLF001
    b = investigator._compute_recurrence_signal({"y": 2, "x": 1})  # noqa: SLF001
    assert a == b
    c = investigator._compute_recurrence_signal({"x": 1, "y": 3})  # noqa: SLF001
    assert a != c


def test_stream_path_resolves_inside_ws_path(tmp_path: Path) -> None:
    assert investigator._stream_path(tmp_path) == tmp_path / "investigator_stream.jsonl"  # noqa: SLF001


def test_dedupe_skip_unchanged_recurrence_no_llm(queue: Queue, tmp_path: Path) -> None:
    evidence = {"status": "failed", "note": "x"}
    signal = investigator._compute_recurrence_signal(evidence)  # noqa: SLF001
    existing = _make_draft(recurrence_signal=signal)
    save_rca_draft(queue, "fp-dedupe", existing)

    with mock.patch("alan.investigator.run_agent_streaming") as mock_run:
        investigator._do_investigate(  # noqa: SLF001
            tmp_path, "fp-dedupe", evidence, frozenset({"root_caused"}), queue, None
        )
        mock_run.assert_not_called()

    assert get_rca_draft(queue, "fp-dedupe") == existing


def test_recurrence_advanced_writes_new_draft(queue: Queue, tmp_path: Path) -> None:
    old_evidence = {"status": "failed", "note": "old"}
    old_signal = investigator._compute_recurrence_signal(old_evidence)  # noqa: SLF001
    save_rca_draft(queue, "fp-advance", _make_draft(recurrence_signal=old_signal))

    new_evidence = {"status": "failed", "note": "new"}
    yaml_text = (
        "Analysis.\n\n"
        "## RCA_DRAFT_YAML\n"
        "```yaml\n"
        "falsification_test: rerun and confirm it no longer reproduces\n"
        "recurrence_signature: sig2\n"
        "recipe: fix it\n"
        "timeline: []\n"
        "```\n"
    )
    with mock.patch(
        "alan.investigator.run_agent_streaming",
        return_value=AgentStreamingResult(result_text=yaml_text),
    ) as mock_run:
        investigator._do_investigate(  # noqa: SLF001
            tmp_path, "fp-advance", new_evidence, frozenset({"root_caused"}), queue, None
        )
        mock_run.assert_called_once()

    saved = get_rca_draft(queue, "fp-advance")
    assert saved is not None
    assert saved.recurrence_signal == investigator._compute_recurrence_signal(new_evidence)  # noqa: SLF001
    assert saved.falsification_test == "rerun and confirm it no longer reproduces"


def test_do_investigate_resets_stale_stream_before_generating(
    queue: Queue, tmp_path: Path
) -> None:
    stream_path = investigator._stream_path(tmp_path)  # noqa: SLF001
    stream_path.write_text('{"stale": "event"}\n')
    yaml_text = "## RCA_DRAFT_YAML\n```yaml\nfalsification_test: rerun\ntimeline: []\n```\n"
    seen_at_call: dict[str, bool] = {}

    def _fake_run(*args: Any, **kwargs: Any) -> AgentStreamingResult:
        seen_at_call["stream_existed"] = stream_path.exists()
        return AgentStreamingResult(result_text=yaml_text)

    with mock.patch("alan.investigator.run_agent_streaming", side_effect=_fake_run):
        investigator._do_investigate(  # noqa: SLF001
            tmp_path, "fp-stream-reset", {"status": "failed"}, frozenset(), queue, None
        )
    assert seen_at_call["stream_existed"] is False


def test_do_investigate_stream_reset_failure_still_generates(
    queue: Queue, tmp_path: Path
) -> None:
    stream_path = investigator._stream_path(tmp_path)  # noqa: SLF001
    stream_path.mkdir()
    yaml_text = "## RCA_DRAFT_YAML\n```yaml\nfalsification_test: rerun\ntimeline: []\n```\n"
    with mock.patch(
        "alan.investigator.run_agent_streaming",
        return_value=AgentStreamingResult(result_text=yaml_text),
    ) as mock_run:
        investigator._do_investigate(  # noqa: SLF001
            tmp_path, "fp-stream-reset-fail", {"status": "failed"}, frozenset(), queue, None
        )
        mock_run.assert_called_once()
    saved = get_rca_draft(queue, "fp-stream-reset-fail")
    assert saved is not None


def test_do_investigate_grants_read_access_to_the_workspace_under_investigation(
    queue: Queue, tmp_path: Path
) -> None:
    with mock.patch(
        "alan.investigator.run_agent_streaming",
        return_value=AgentStreamingResult(result_text="prose only"),
    ) as mock_run:
        investigator._do_investigate(  # noqa: SLF001
            tmp_path, "fp-add-dirs", {"status": "failed"}, frozenset(), queue, None
        )
    assert mock_run.call_args.kwargs["add_dirs"] == [str(tmp_path)]


def test_do_investigate_turn_ceiling_is_logged_as_such(
    queue: Queue, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with (
        mock.patch(
            "alan.investigator.run_agent_streaming",
            side_effect=TurnCeilingReached("claude CLI: error_max_turns; num_turns=41"),
        ),
        caplog.at_level("ERROR", logger="alan.investigator"),
    ):
        investigator._do_investigate(  # noqa: SLF001
            tmp_path, "fp-ceiling", {"status": "failed"}, frozenset(), queue, None
        )
    assert "turn ceiling" in caplog.text
    assert str(investigator._MAX_TURNS) in caplog.text  # noqa: SLF001
    assert get_rca_draft(queue, "fp-ceiling") is None


def test_do_investigate_generation_failure_no_save(queue: Queue, tmp_path: Path) -> None:
    with mock.patch(
        "alan.investigator.run_agent_streaming", side_effect=RuntimeError("no claude")
    ):
        investigator._do_investigate(  # noqa: SLF001
            tmp_path, "fp-fail", {"status": "failed"}, frozenset(), queue, None
        )
    assert get_rca_draft(queue, "fp-fail") is None


def test_do_investigate_no_yaml_block_no_save(queue: Queue, tmp_path: Path) -> None:
    with mock.patch(
        "alan.investigator.run_agent_streaming",
        return_value=AgentStreamingResult(result_text="just prose, no block"),
    ):
        investigator._do_investigate(  # noqa: SLF001
            tmp_path, "fp-noblock", {"status": "failed"}, frozenset(), queue, None
        )
    assert get_rca_draft(queue, "fp-noblock") is None


def _bc_rows(logs_root: Path) -> list[dict[str, Any]]:
    import json

    path = logs_root / "block_correction_audit.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_do_investigate_no_yaml_block_records_unreachable_author(
    queue: Queue, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs))
    with mock.patch(
        "alan.investigator.run_agent_streaming",
        return_value=AgentStreamingResult(result_text="just prose, no block"),
    ):
        investigator._do_investigate(  # noqa: SLF001
            tmp_path, "fp-noblock2", {"status": "failed"}, frozenset(), queue, None
        )
    rows = _bc_rows(logs)
    assert rows[-1]["site"] == "RCA_DRAFT_YAML"
    assert rows[-1]["outcome"] == "unreachable_author"


def test_do_investigate_valid_block_records_clean(
    queue: Queue, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(logs))
    yaml_text = "## RCA_DRAFT_YAML\n```yaml\nfalsification_test: rerun\ntimeline: []\n```\n"
    with mock.patch(
        "alan.investigator.run_agent_streaming",
        return_value=AgentStreamingResult(result_text=yaml_text),
    ):
        investigator._do_investigate(  # noqa: SLF001
            tmp_path, "fp-clean", {"status": "failed"}, frozenset(), queue, None
        )
    rows = _bc_rows(logs)
    assert rows[-1]["outcome"] == "clean"


def test_do_investigate_malformed_draft_no_save(queue: Queue, tmp_path: Path) -> None:
    yaml_text = "## RCA_DRAFT_YAML\n```yaml\nrecipe: no falsification test here\n```\n"
    with mock.patch(
        "alan.investigator.run_agent_streaming",
        return_value=AgentStreamingResult(result_text=yaml_text),
    ):
        investigator._do_investigate(  # noqa: SLF001
            tmp_path, "fp-malformed", {"status": "failed"}, frozenset(), queue, None
        )
    assert get_rca_draft(queue, "fp-malformed") is None


def test_trigger_investigator_starts_daemon_thread(tmp_path: Path, queue: Queue) -> None:
    with mock.patch("alan.investigator.threading.Thread") as mock_thread:
        investigator.trigger_investigator(tmp_path, "fp-x", {}, frozenset(), queue, None)
        mock_thread.assert_called_once()
        assert mock_thread.call_args.kwargs["daemon"] is True
        mock_thread.return_value.start.assert_called_once()


def test_trigger_investigator_end_to_end(tmp_path: Path, queue: Queue) -> None:
    yaml_text = "## RCA_DRAFT_YAML\n```yaml\nfalsification_test: rerun\ntimeline: []\n```\n"
    done = threading.Event()

    def _save_and_signal(*args: Any, **kwargs: Any) -> RcaDraft:
        result = save_rca_draft(*args, **kwargs)
        done.set()
        return result

    with (
        mock.patch(
            "alan.investigator.run_agent_streaming",
            return_value=AgentStreamingResult(result_text=yaml_text),
        ),
        mock.patch("alan.investigator.save_rca_draft", side_effect=_save_and_signal),
    ):
        investigator.trigger_investigator(tmp_path, "fp-e2e", {}, frozenset(), queue, None)
        assert done.wait(timeout=5.0)

    saved = get_rca_draft(queue, "fp-e2e")
    assert saved is not None
    assert saved.falsification_test == "rerun"


def test_trigger_fires_six_dod_classes_skips_triage_question_harvest(
    queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Path, str, dict[str, Any], frozenset[str], str | None]] = []

    def fake_trigger(
        ws_path: Path,
        fingerprint: str,
        evidence: dict[str, Any],
        valid_dispositions: frozenset[str],
        q: Queue,
        workspace_key: str | None,
    ) -> None:
        assert q is queue
        calls.append((ws_path, fingerprint, evidence, valid_dispositions, workspace_key))

    monkeypatch.setattr("alan.investigator.trigger_investigator", fake_trigger)

    fires = [
        _make_ws("ws-escalation", status=Status.ESCALATED),
        _make_ws("ws-failed", status=Status.FAILED),
        _make_ws("ws-partial", status=Status.PARTIAL),
        _make_ws("ws-crash", status=Status.AWAITING_INPUT, park_reason=ParkReason.CRASH),
        _make_ws(
            "ws-routing", status=Status.AWAITING_INPUT, park_reason=ParkReason.ROUTING_ERROR
        ),
        _make_ws("ws-stall", status=Status.AWAITING_INPUT, park_reason=ParkReason.STALL),
    ]
    for ws in fires:
        dispatcher_mod._maybe_trigger_investigator(ws, queue)  # noqa: SLF001
    assert len(calls) == 6
    for _ws_path, fingerprint, _evidence, valid_dispositions, _workspace_key in calls:
        assert fingerprint
        assert "root_caused" in valid_dispositions
    assert [workspace_key for *_rest, workspace_key in calls] == [ws.task_id for ws in fires]

    calls.clear()
    triage_ws = _make_ws("ws-triage", status=Status.ESCALATED, team="triage")
    dispatcher_mod._maybe_trigger_investigator(triage_ws, queue)  # noqa: SLF001
    assert calls == []

    question_ws = _make_ws(
        "ws-question", status=Status.AWAITING_INPUT, park_reason=ParkReason.QUESTION
    )
    dispatcher_mod._maybe_trigger_investigator(question_ws, queue)  # noqa: SLF001
    assert calls == []

    harvest_ws = _make_ws("ws-harvest", status=Status.AWAITING_INPUT, team="architecture")
    dispatcher_mod._maybe_trigger_investigator(harvest_ws, queue)  # noqa: SLF001
    assert calls == []


def test_maybe_trigger_investigator_no_incident_skips(
    queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[object] = []
    monkeypatch.setattr(
        "alan.investigator.trigger_investigator",
        lambda *a, **kw: calls.append(a),
    )
    active_ws = _make_ws("ws-active", status=Status.ACTIVE)
    dispatcher_mod._maybe_trigger_investigator(active_ws, queue)  # noqa: SLF001
    assert calls == []


def test_maybe_trigger_investigator_non_target_class_skips(
    queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[object] = []
    monkeypatch.setattr(
        "alan.investigator.trigger_investigator",
        lambda *a, **kw: calls.append(a),
    )
    merge_block_ws = _make_ws(
        "ws-merge-block", status=Status.AWAITING_INPUT, park_reason=ParkReason.MERGE_BLOCK
    )
    dispatcher_mod._maybe_trigger_investigator(merge_block_ws, queue)  # noqa: SLF001
    assert calls == []


_RCA_YAML = "## RCA_DRAFT_YAML\n```yaml\nfalsification_test: rerun\ntimeline: []\n```\n"


class _InlineThread:
    def __init__(self, *, target: Any, args: tuple[Any, ...], daemon: bool) -> None:
        self._target = target
        self._args = args

    def start(self) -> None:
        self._target(*self._args)


def _run_investigator_inline(
    ws: Workspace, queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(threading, "Thread", _InlineThread)
    with mock.patch(
        "alan.investigator.run_agent_streaming",
        return_value=AgentStreamingResult(result_text=_RCA_YAML),
    ):
        dispatcher_mod._maybe_trigger_investigator(ws, queue)  # noqa: SLF001


def _only_draft(queue: Queue) -> RcaDraft:
    drafts = load_rca_drafts(queue)
    assert len(drafts) == 1
    return next(iter(drafts.values()))


def test_maybe_trigger_investigator_persists_the_workspace_task_id_on_the_draft(
    queue: Queue, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _make_ws("ws-stamped", status=Status.FAILED)
    ws = Workspace(task_id=ws.task_id, path=tmp_path, meta=ws.meta)

    _run_investigator_inline(ws, queue, monkeypatch)

    draft = _only_draft(queue)
    assert draft.workspace_key == "ws-stamped"
    assert draft.created_at is not None


def test_maybe_trigger_investigator_stamps_task_id_even_when_the_incident_has_a_pr(
    queue: Queue, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _make_ws("ws-with-pr", status=Status.FAILED)
    ws = Workspace(task_id=ws.task_id, path=tmp_path, meta=ws.meta)
    handovers = [
        Handover(
            handover_id="h1",
            handover_type="forward",
            task_id="ws-with-pr",
            from_team="implementation",
            from_invocation_id="inv1",
            iteration=1,
            timestamp_utc=datetime(2026, 1, 1, tzinfo=UTC),
            full_synthesis="s",
            pr_number=77,
        )
    ]
    monkeypatch.setattr(dispatcher_mod, "list_handovers", lambda _ws: handovers)
    incident = derive_incidents(workspaces=[ws], handovers_by_workspace={ws.task_id: handovers})[0]
    assert incident.workspace_id is None
    assert incident.pr_number == 77

    _run_investigator_inline(ws, queue, monkeypatch)

    assert _only_draft(queue).workspace_key == "ws-with-pr"


def test_do_investigate_preserves_a_none_workspace_key(queue: Queue, tmp_path: Path) -> None:
    with mock.patch(
        "alan.investigator.run_agent_streaming",
        return_value=AgentStreamingResult(result_text=_RCA_YAML),
    ):
        investigator._do_investigate(  # noqa: SLF001
            tmp_path, "fp-unkeyed", {"status": "failed"}, frozenset(), queue, None
        )

    saved = get_rca_draft(queue, "fp-unkeyed")
    assert saved is not None
    assert saved.workspace_key is None
    assert saved.created_at is not None


def test_replay_command_grep_resolves() -> None:
    assert investigator._replay_command_resolves(("grep", "-n", "x", "f")) is True  # noqa: SLF001


def test_replay_command_cat_resolves() -> None:
    assert investigator._replay_command_resolves(("cat", "f")) is True  # noqa: SLF001


def test_replay_command_gh_pr_view_resolves() -> None:
    assert investigator._replay_command_resolves(("gh", "pr", "view", "1")) is True  # noqa: SLF001


def test_replay_command_gh_issue_view_resolves() -> None:
    assert investigator._replay_command_resolves(("gh", "issue", "view", "1")) is True  # noqa: SLF001


def test_replay_command_gh_run_view_resolves() -> None:
    assert investigator._replay_command_resolves(("gh", "run", "view", "1")) is True  # noqa: SLF001


def test_replay_command_gh_pr_merge_rejected() -> None:
    assert investigator._replay_command_resolves(("gh", "pr", "merge", "1")) is False  # noqa: SLF001


def test_replay_command_git_push_rejected() -> None:
    assert investigator._replay_command_resolves(("git", "push")) is False  # noqa: SLF001


def test_replay_command_shorter_than_gh_entry_rejected() -> None:
    assert investigator._replay_command_resolves(("gh", "pr")) is False  # noqa: SLF001


def test_replay_command_gh_pr_viewer_rejected() -> None:
    assert investigator._replay_command_resolves(("gh", "pr", "viewer")) is False  # noqa: SLF001


def test_replay_command_single_joined_argument_rejected() -> None:
    assert investigator._replay_command_resolves(("gh pr view",)) is False  # noqa: SLF001
