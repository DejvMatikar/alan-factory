from __future__ import annotations

import io
import json
import subprocess
import threading
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from alan import agent_key
from alan import manager_summary as ms
from alan.claude_cli import _HEADLESS_EXECUTION_BLOCK
from alan.claude_cli_grants import NO_TOOLS_BLOCK
from alan.run_summary import RunSummary, derive_run_summary

_REAL_TRIGGER_MANAGER_SUMMARY = ms.trigger_manager_summary


@pytest.fixture(autouse=True)
def _restore_real_trigger_manager_summary(
    _no_real_manager_summary_spawn: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ms, "trigger_manager_summary", _REAL_TRIGGER_MANAGER_SUMMARY)


def test_status_label_known() -> None:
    assert ms._status_label("completed") == "completed successfully"  # noqa: SLF001
    assert ms._status_label("failed") == "failed"  # noqa: SLF001
    assert ms._status_label("awaiting_input") == "paused — human input needed"  # noqa: SLF001
    assert ms._status_label("escalated") == "escalated — the agent could not proceed autonomously"  # noqa: SLF001


def test_status_label_unknown() -> None:
    assert ms._status_label("some_unknown_status") == "some_unknown_status"  # noqa: SLF001


def test_build_user_prompt_without_synthesis() -> None:
    prompt = ms._build_user_prompt("My Task", "completed", None)  # noqa: SLF001
    assert "Task: My Task" in prompt
    assert "completed successfully" in prompt
    assert "Handover synthesis" not in prompt
    assert "Stop reason" not in prompt


def test_build_user_prompt_with_synthesis() -> None:
    prompt = ms._build_user_prompt("My Task", "failed", "The agent encountered an error.")  # noqa: SLF001
    assert "Task: My Task" in prompt
    assert "failed" in prompt
    assert "Handover synthesis" in prompt
    assert "The agent encountered an error." in prompt


def test_build_user_prompt_with_stop_reason() -> None:
    prompt = ms._build_user_prompt("My Task", "awaiting_input", None, "DAG stalled after t1")  # noqa: SLF001
    assert "Stop reason: DAG stalled after t1" in prompt
    assert "Handover synthesis" not in prompt


def test_build_user_prompt_truncates_synthesis() -> None:
    long_synthesis = "x" * 3000
    prompt = ms._build_user_prompt("T", "completed", long_synthesis)  # noqa: SLF001
    assert "x" * 2000 in prompt
    assert len([c for c in prompt if c == "x"]) == 2000


def test_write_sidecar(tmp_path: Path) -> None:
    data: dict[str, Any] = {"ok": True, "text": "hello"}
    ms._write_sidecar(tmp_path, data)  # noqa: SLF001
    written = json.loads((tmp_path / "manager_summary.json").read_text())
    assert written == {"ok": True, "text": "hello"}


def test_write_sidecar_overwrites_existing(tmp_path: Path) -> None:
    ms._write_sidecar(tmp_path, {"ok": True, "text": "first"})  # noqa: SLF001
    ms._write_sidecar(tmp_path, {"ok": True, "text": "second"})  # noqa: SLF001
    written = json.loads((tmp_path / "manager_summary.json").read_text())
    assert written["text"] == "second"


def test_read_manager_summary_success(tmp_path: Path) -> None:
    (tmp_path / "manager_summary.json").write_text('{"ok": true, "text": "done"}')
    result = ms.read_manager_summary(tmp_path)
    assert result == {"ok": True, "text": "done"}


def test_read_manager_summary_missing(tmp_path: Path) -> None:
    assert ms.read_manager_summary(tmp_path) is None


def test_read_manager_summary_invalid_json(tmp_path: Path) -> None:
    (tmp_path / "manager_summary.json").write_text("not json{{{")
    assert ms.read_manager_summary(tmp_path) is None


def test_read_manager_summary_mtime_missing_returns_none(tmp_path: Path) -> None:
    assert ms.read_manager_summary_written_at(tmp_path) is None


def test_read_manager_summary_mtime_returns_sidecar_mtime(tmp_path: Path) -> None:
    sidecar = tmp_path / "manager_summary.json"
    sidecar.write_text('{"ok": true, "text": "done"}')
    written_at = ms.read_manager_summary_written_at(tmp_path)
    assert written_at is not None
    assert written_at.timestamp() == pytest.approx(sidecar.stat().st_mtime, abs=1.0)


def test_do_generate_happy_path(tmp_path: Path) -> None:
    ready_calls: list[None] = []

    with mock.patch("alan.manager_summary.run_agent", return_value="Great summary.") as mock_run:
        ms._do_generate(  # noqa: SLF001
            tmp_path, "ws-1", "My Task", "completed", None, lambda: ready_calls.append(None)
        )
        mock_run.assert_called_once_with(
            ms._SYSTEM_PROMPT,  # noqa: SLF001
            mock.ANY,
            model="haiku",
            deny_all_tools=True,
            stream_path=ms._stream_path(tmp_path),  # noqa: SLF001
            agent=agent_key.MANAGER_SUMMARY,
        )

    sidecar = json.loads((tmp_path / "manager_summary.json").read_text())
    assert sidecar == {"ok": True, "text": "Great summary."}
    assert len(ready_calls) == 1


def test_do_generate_passes_synthesis_to_prompt(tmp_path: Path) -> None:
    captured: list[dict[str, str]] = []
    deny_flags: list[bool] = []
    stream_paths: list[Path | None] = []

    def fake_run_agent(
        _system: str,
        user: str,
        *,
        model: str = "sonnet",
        deny_all_tools: bool = False,
        stream_path: Path | None = None,
        **_kwargs: object,
    ) -> str:
        captured.append({"user": user, "model": model})
        deny_flags.append(deny_all_tools)
        stream_paths.append(stream_path)
        return "Summary text."

    with mock.patch("alan.manager_summary.run_agent", side_effect=fake_run_agent):
        ms._do_generate(tmp_path, "ws-2", "Task", "escalated", "synth text", lambda: None)  # noqa: SLF001

    assert len(captured) == 1
    assert "synth text" in captured[0]["user"]
    assert captured[0]["model"] == "haiku"
    assert deny_flags == [True]
    assert stream_paths == [ms._stream_path(tmp_path)]  # noqa: SLF001


def test_do_generate_run_agent_fails(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    ready_calls: list[None] = []

    with mock.patch("alan.manager_summary.run_agent", side_effect=RuntimeError("no claude")):
        with caplog.at_level("ERROR", logger="alan.manager_summary"):
            ms._do_generate(  # noqa: SLF001
                tmp_path, "ws-3", "T", "failed", None, lambda: ready_calls.append(None)
            )

    sidecar = json.loads((tmp_path / "manager_summary.json").read_text())
    assert sidecar["ok"] is False
    assert sidecar["text"] is None
    assert len(ready_calls) == 1
    assert any("generation failed" in r.message for r in caplog.records)


def test_do_generate_sidecar_write_fails(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    ready_calls: list[None] = []

    with mock.patch("alan.manager_summary.run_agent", return_value="text"):
        with mock.patch(
            "alan.manager_summary._write_sidecar", side_effect=OSError("disk full")
        ):
            with caplog.at_level("ERROR", logger="alan.manager_summary"):
                ms._do_generate(  # noqa: SLF001
                    tmp_path, "ws-4", "T", "completed", None, lambda: ready_calls.append(None)
                )

    assert len(ready_calls) == 0
    assert any("sidecar/notify failed" in r.message for r in caplog.records)


def test_trigger_manager_summary_starts_daemon_thread(tmp_path: Path) -> None:
    with mock.patch("alan.manager_summary.threading.Thread") as mock_thread:
        ms.trigger_manager_summary(tmp_path, "ws-5", "T", "completed", None, lambda: None)
        mock_thread.assert_called_once()
        assert mock_thread.call_args.kwargs["daemon"] is True
        mock_thread.return_value.start.assert_called_once()


def test_trigger_manager_summary_passes_correct_args(tmp_path: Path) -> None:
    with mock.patch("alan.manager_summary.threading.Thread") as mock_thread:
        on_ready = mock.Mock()
        ms.trigger_manager_summary(
            tmp_path, "ws-6", "Task Title", "awaiting_input", "synth", on_ready,
            stop_reason="DAG stalled",
        )
        kwargs = mock_thread.call_args.kwargs
        (
            ws_path_arg,
            workspace_id_arg,
            title_arg,
            status_arg,
            synthesis_arg,
            on_ready_arg,
            stop_reason_arg,
        ) = kwargs["args"]
        assert ws_path_arg == tmp_path
        assert workspace_id_arg == "ws-6"
        assert title_arg == "Task Title"
        assert status_arg == "awaiting_input"
        assert synthesis_arg == "synth"
        assert on_ready_arg is on_ready
        assert stop_reason_arg == "DAG stalled"


def test_do_generate_overwrites_stale_sidecar(tmp_path: Path) -> None:
    with mock.patch("alan.manager_summary.run_agent", return_value="First summary."):
        ms._do_generate(tmp_path, "ws-8a", "T", "completed", None, lambda: None)  # noqa: SLF001
    first = json.loads((tmp_path / "manager_summary.json").read_text())
    assert first["text"] == "First summary."

    with mock.patch("alan.manager_summary.run_agent", return_value="Second summary."):
        ms._do_generate(tmp_path, "ws-8a", "T", "failed", None, lambda: None)  # noqa: SLF001
    second = json.loads((tmp_path / "manager_summary.json").read_text())
    assert second["text"] == "Second summary."


def test_trigger_manager_summary_end_to_end(tmp_path: Path) -> None:
    results: list[dict[str, Any]] = []
    ready_event = threading.Event()

    def on_ready() -> None:
        result = ms.read_manager_summary(tmp_path)
        if result is not None:
            results.append(result)
        ready_event.set()

    with mock.patch("alan.manager_summary.run_agent", return_value="summary text"):
        ms.trigger_manager_summary(tmp_path, "ws-7", "T", "completed", None, on_ready)
        ready_event.wait(timeout=5.0)

    assert results == [{"ok": True, "text": "summary text"}]


def test_stream_path_resolves_inside_ws_path(tmp_path: Path) -> None:
    assert ms._stream_path(tmp_path) == tmp_path / "manager_summary_stream.jsonl"  # noqa: SLF001


def _write_result_event(path: Path, *, cost: float, input_tokens: int, output_tokens: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "type": "result",
        "total_cost_usd": cost,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event) + "\n")


def test_read_manager_summary_usage_missing_stream_returns_none(tmp_path: Path) -> None:
    assert ms.read_manager_summary_usage(tmp_path) is None


def test_read_manager_summary_usage_sums_across_runs(tmp_path: Path) -> None:
    stream = ms._stream_path(tmp_path)  # noqa: SLF001
    _write_result_event(stream, cost=0.001, input_tokens=100, output_tokens=50)
    _write_result_event(stream, cost=0.002, input_tokens=200, output_tokens=75)

    summary = ms.read_manager_summary_usage(tmp_path)

    assert summary is not None
    assert summary.model == "haiku"
    assert summary.total_cost_usd == pytest.approx(0.003)
    assert summary.total_input_tokens == 300
    assert summary.total_output_tokens == 125
    assert summary.tool_counts == []


def test_do_generate_resets_stream_last_write_wins(tmp_path: Path) -> None:
    def fake_run_agent(
        _system: str,
        _user: str,
        *,
        model: str = "sonnet",
        deny_all_tools: bool = False,
        stream_path: Path | None = None,
        **_kwargs: object,
    ) -> str:
        assert stream_path is not None
        _write_result_event(stream_path, cost=0.001, input_tokens=10, output_tokens=5)
        return "summary"

    with mock.patch("alan.manager_summary.run_agent", side_effect=fake_run_agent):
        ms._do_generate(tmp_path, "ws-9", "T", "completed", None, lambda: None)  # noqa: SLF001
        ms._do_generate(tmp_path, "ws-9", "T", "completed", None, lambda: None)  # noqa: SLF001

    summary = ms.read_manager_summary_usage(tmp_path)
    assert summary is not None
    assert summary.total_cost_usd == pytest.approx(0.001)
    assert summary.total_input_tokens == 10


def test_do_generate_unlinks_stream_before_run_agent(tmp_path: Path) -> None:
    stream = ms._stream_path(tmp_path)  # noqa: SLF001
    _write_result_event(stream, cost=0.5, input_tokens=999, output_tokens=999)
    assert stream.exists()

    seen_before_call: list[bool] = []

    def fake_run_agent(
        _system: str,
        _user: str,
        *,
        model: str = "sonnet",
        deny_all_tools: bool = False,
        stream_path: Path | None = None,
        **_kwargs: object,
    ) -> str:
        assert stream_path is not None
        seen_before_call.append(stream_path.exists())
        return "summary"

    with mock.patch("alan.manager_summary.run_agent", side_effect=fake_run_agent):
        ms._do_generate(tmp_path, "ws-9b", "T", "completed", None, lambda: None)  # noqa: SLF001

    assert seen_before_call == [False]


def test_do_generate_unlink_guarded_when_stream_absent(tmp_path: Path) -> None:
    assert not ms._stream_path(tmp_path).exists()  # noqa: SLF001

    with mock.patch("alan.manager_summary.run_agent", return_value="summary"):
        ms._do_generate(tmp_path, "ws-9c", "T", "completed", None, lambda: None)  # noqa: SLF001

    sidecar = json.loads((tmp_path / "manager_summary.json").read_text())
    assert sidecar == {"ok": True, "text": "summary"}


def test_do_generate_unlink_failure_does_not_abort_generation(tmp_path: Path) -> None:
    stream = ms._stream_path(tmp_path)  # noqa: SLF001
    _write_result_event(stream, cost=0.5, input_tokens=999, output_tokens=999)

    with (
        mock.patch.object(Path, "unlink", side_effect=OSError("permission denied")),
        mock.patch("alan.manager_summary.run_agent", return_value="summary"),
    ):
        ms._do_generate(tmp_path, "ws-9d", "T", "completed", None, lambda: None)  # noqa: SLF001

    sidecar = json.loads((tmp_path / "manager_summary.json").read_text())
    assert sidecar == {"ok": True, "text": "summary"}


def test_build_user_prompt_with_park_reason() -> None:
    prompt = ms._build_user_prompt(  # noqa: SLF001
        "T", "awaiting_input", None, park_reason="merge_block"
    )
    assert "Park reason: merge_block" in prompt


def test_build_user_prompt_without_park_reason_omits_line() -> None:
    prompt = ms._build_user_prompt("T", "completed", None)  # noqa: SLF001
    assert "Park reason" not in prompt


def test_build_user_prompt_with_dod_evidence() -> None:
    prompt = ms._build_user_prompt(  # noqa: SLF001
        "T", "completed", None, dod_evidence=["Clause A: PR #12", "Clause B: PR #12"]
    )
    assert "Definition-of-done evidence:" in prompt
    assert "- Clause A: PR #12" in prompt
    assert "- Clause B: PR #12" in prompt


def test_build_user_prompt_dod_evidence_truncated_to_ten() -> None:
    evidence = [f"Clause {i}: done" for i in range(20)]
    prompt = ms._build_user_prompt("T", "completed", None, dod_evidence=evidence)  # noqa: SLF001
    assert prompt.count("- Clause") == 10


def test_build_user_prompt_without_dod_evidence_omits_section() -> None:
    prompt = ms._build_user_prompt("T", "completed", None, dod_evidence=[])  # noqa: SLF001
    assert "Definition-of-done evidence" not in prompt


def test_read_meta_extras_missing_file(tmp_path: Path) -> None:
    park_reason, evidence = ms._read_meta_extras(tmp_path)  # noqa: SLF001
    assert park_reason is None
    assert evidence == []


def test_read_meta_extras_invalid_json(tmp_path: Path) -> None:
    (tmp_path / "meta.json").write_text("not json{{{")
    park_reason, evidence = ms._read_meta_extras(tmp_path)  # noqa: SLF001
    assert park_reason is None
    assert evidence == []


def test_read_meta_extras_non_dict_json(tmp_path: Path) -> None:
    (tmp_path / "meta.json").write_text("[1, 2, 3]")
    park_reason, evidence = ms._read_meta_extras(tmp_path)  # noqa: SLF001
    assert park_reason is None
    assert evidence == []


def test_read_meta_extras_extracts_park_reason_and_dod_evidence(tmp_path: Path) -> None:
    meta = {
        "park_reason": "merge_block",
        "tasks": [
            {
                "id": "t1",
                "dod_clauses": [
                    {"text": "Clause A", "evidence": "PR #12"},
                    {"text": "Clause B", "evidence": None},
                    {"text": "Clause C"},
                    "not-a-dict-clause",
                ],
            },
            "not-a-dict",
            {"id": "t2", "dod_clauses": "not-a-list"},
        ],
    }
    (tmp_path / "meta.json").write_text(json.dumps(meta))
    park_reason, evidence = ms._read_meta_extras(tmp_path)  # noqa: SLF001
    assert park_reason == "merge_block"
    assert evidence == ["Clause A: PR #12"]


def test_read_meta_extras_tasks_not_a_list(tmp_path: Path) -> None:
    (tmp_path / "meta.json").write_text(json.dumps({"park_reason": None, "tasks": "nope"}))
    park_reason, evidence = ms._read_meta_extras(tmp_path)  # noqa: SLF001
    assert park_reason is None
    assert evidence == []


def test_do_generate_enriches_prompt_from_meta_json(tmp_path: Path) -> None:
    meta = {
        "park_reason": "escalated",
        "tasks": [
            {"dod_clauses": [{"text": "Ship it", "evidence": "PR #99 merged"}]},
        ],
    }
    (tmp_path / "meta.json").write_text(json.dumps(meta))
    captured: list[str] = []

    def fake_run_agent(
        _system: str,
        user: str,
        *,
        model: str = "sonnet",
        deny_all_tools: bool = False,
        stream_path: Path | None = None,
        **_kwargs: object,
    ) -> str:
        captured.append(user)
        return "summary"

    with mock.patch("alan.manager_summary.run_agent", side_effect=fake_run_agent):
        ms._do_generate(tmp_path, "ws-meta", "T", "escalated", None, lambda: None)  # noqa: SLF001

    assert len(captured) == 1
    assert "Park reason: escalated" in captured[0]
    assert "Ship it: PR #99 merged" in captured[0]


def test_do_generate_without_meta_json_omits_enrichment(tmp_path: Path) -> None:
    captured: list[str] = []

    def fake_run_agent(
        _system: str,
        user: str,
        *,
        model: str = "sonnet",
        deny_all_tools: bool = False,
        stream_path: Path | None = None,
        **_kwargs: object,
    ) -> str:
        captured.append(user)
        return "summary"

    with mock.patch("alan.manager_summary.run_agent", side_effect=fake_run_agent):
        ms._do_generate(tmp_path, "ws-no-meta", "T", "completed", None, lambda: None)  # noqa: SLF001

    assert len(captured) == 1
    assert "Park reason" not in captured[0]
    assert "Definition-of-done evidence" not in captured[0]


def test_do_generate_stream_stays_under_ws_path_not_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    ws_path = tmp_path / "workspace"
    ws_path.mkdir()

    def fake_run_agent(
        _system: str,
        _user: str,
        *,
        model: str = "sonnet",
        deny_all_tools: bool = False,
        stream_path: Path | None = None,
        **_kwargs: object,
    ) -> str:
        assert stream_path is not None
        _write_result_event(stream_path, cost=0.001, input_tokens=1, output_tokens=1)
        return "summary"

    with mock.patch("alan.manager_summary.run_agent", side_effect=fake_run_agent):
        ms._do_generate(ws_path, "ws-10", "T", "completed", None, lambda: None)  # noqa: SLF001

    assert (ws_path / "manager_summary_stream.jsonl").exists()
    assert not any(fake_home.rglob("*"))


@pytest.fixture(autouse=True)
def _clear_usage_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ms, "_USAGE_CACHE", {})


def test_read_manager_summary_usage_cached_derives_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stream = ms._stream_path(tmp_path)  # noqa: SLF001
    _write_result_event(stream, cost=0.001, input_tokens=100, output_tokens=50)
    call_count = 0

    def spy(
        stream_path: Path, *, system_prompt: str, model: str = "haiku"
    ) -> RunSummary | None:
        nonlocal call_count
        call_count += 1
        return derive_run_summary(stream_path, system_prompt=system_prompt, model=model)

    monkeypatch.setattr(ms, "derive_run_summary", spy)
    first = ms.read_manager_summary_usage(tmp_path)
    second = ms.read_manager_summary_usage(tmp_path)
    assert call_count == 1
    assert first is not None
    assert second is not None
    assert first.total_cost_usd == second.total_cost_usd == pytest.approx(0.001)


def test_read_manager_summary_usage_miss_on_stream_append(tmp_path: Path) -> None:
    stream = ms._stream_path(tmp_path)  # noqa: SLF001
    _write_result_event(stream, cost=0.001, input_tokens=100, output_tokens=50)
    first = ms.read_manager_summary_usage(tmp_path)
    assert first is not None
    assert first.total_cost_usd == pytest.approx(0.001)
    _write_result_event(stream, cost=0.002, input_tokens=200, output_tokens=75)
    second = ms.read_manager_summary_usage(tmp_path)
    assert second is not None
    assert second.total_cost_usd == pytest.approx(0.003)


def test_read_manager_summary_usage_missing_stream_not_cached(tmp_path: Path) -> None:
    assert ms.read_manager_summary_usage(tmp_path) is None
    assert ms._USAGE_CACHE == {}  # noqa: SLF001


def test_do_generate_real_run_agent_omits_headless_and_project_doc_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured_cmds: list[list[str]] = []

    class _FP:
        def __init__(self, cmd: list[str], **_kw: Any) -> None:
            captured_cmds.append(cmd)
            self.stdin = io.StringIO()
            line = json.dumps(
                {"type": "result", "subtype": "success", "result": "Great summary."}
            )
            self.stdout: Any = io.StringIO(line + "\n")
            self.stderr = io.StringIO("")
            self.returncode: int | None = None

        def wait(self, timeout: float | None = None) -> int:
            self.returncode = 0
            return 0

        def kill(self) -> None:
            pass

    monkeypatch.setattr(subprocess, "Popen", _FP)
    monkeypatch.setattr("alan.claude_cli._project_doc_block", lambda: "PROJECT_DOC_SENTINEL")
    ms._do_generate(tmp_path, "ws-real", "T", "completed", None, lambda: None)  # noqa: SLF001

    sidecar = json.loads((tmp_path / "manager_summary.json").read_text())
    assert sidecar == {"ok": True, "text": "Great summary."}
    assert len(captured_cmds) == 1
    cmd = captured_cmds[0]
    assert cmd[cmd.index("--disallowedTools") + 1] == "*"
    prompt_arg = cmd[cmd.index("--append-system-prompt") + 1]
    assert prompt_arg.startswith(ms._SYSTEM_PROMPT)  # noqa: SLF001
    assert NO_TOOLS_BLOCK in prompt_arg
    assert _HEADLESS_EXECUTION_BLOCK not in prompt_arg
    assert "PROJECT_DOC_SENTINEL" not in prompt_arg
