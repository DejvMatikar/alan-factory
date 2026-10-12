from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from alan import _chat_core, server
from alan import build_chat as build_chat_mod
from alan import collision as collision_mod
from alan import explore_chat as explore_chat_mod
from alan import reconciler as _reconciler_mod
from alan.collision import (
    _CANDIDATE_CAP,
    _SAFE,
    SAFE_RESULT,
    CollisionMatch,
    CollisionResult,
    _apply_verdicts,
    _build_prompt,
    _call_model,
    _classify,
    _fetch_prs,
    _files_note,
    _match_for_verdict,
    _order,
    _parse_pr_item,
    _pr_candidates,
    _PrCandidate,
    _verdict_hit,
    _ws_candidates,
    _WsCandidate,
    check_collision,
)
from alan.engine_config import (
    DEFAULT_FORGE_TARGET,
    EngineConfig,
    ForgeConfig,
    RepoIdentity,
    register,
)
from alan.forge import RowsRead, active_forge
from alan.forge_gh import GhForge
from alan.workspace import Status, create
from tests.fakes.forge import FakeForge


@contextmanager
def _stub_changed_files(files: frozenset[str] | None = None) -> Iterator[None]:
    forge = active_forge()
    if isinstance(forge, FakeForge):
        forge.stub("changed_files", lambda *_a, **_k: files)
        yield
        return
    with patch.object(GhForge, "changed_files", return_value=files):
        yield


@pytest.fixture(autouse=True)
def _stub_reconciler(monkeypatch):
    monkeypatch.setattr(
        _reconciler_mod, "seed_triage_sweep", lambda *, queue, workspaces_root: None
    )
    monkeypatch.setattr(
        _reconciler_mod, "seed_dep_sweep", lambda *, queue, workspaces_root: None
    )


@pytest.fixture
def client(tmp_path):
    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: tmp_path
    try:
        with TestClient(server.app, follow_redirects=True) as c:
            yield c
    finally:
        server.app.dependency_overrides.clear()


def _make_pr(
    number: int = 42,
    title: str = "add authentication flow to API",
    url: str = "https://github.com/owner/repo/pull/42",
    diff_files: frozenset[str] | None = None,
) -> _PrCandidate:
    return _PrCandidate(pr_number=number, title=title, url=url, diff_files=diff_files)


def _make_ws(task_id: str = "ws-abc", title: str = "auth work", live: bool = True) -> _WsCandidate:
    return _WsCandidate(task_id=task_id, title=title, live=live)


class TestSafeResult:
    def test_public_name_is_the_same_object_as_the_legacy_private_alias(self):
        assert SAFE_RESULT is _SAFE

    def test_shape(self):
        assert SAFE_RESULT.state == "green"
        assert SAFE_RESULT.sentence == "No overlapping work found."
        assert SAFE_RESULT.match is None


def _stub_open_prs(result: RowsRead) -> None:
    cast(FakeForge, active_forge()).stub("open_prs", lambda **_k: result)


class TestFetchPrs:
    def test_returns_candidates_on_ok_rows(self):
        _stub_open_prs(
            RowsRead(
                outcome="ok",
                rows=({"number": 42, "title": "add auth flow", "url": "https://gh/42"},),
                returncode=0, stderr="",
            )
        )
        prs = _fetch_prs()
        assert len(prs) == 1
        assert prs[0].pr_number == 42
        assert prs[0].title == "add auth flow"
        assert prs[0].url == "https://gh/42"
        assert prs[0].diff_files is None

    def test_skips_rows_with_wrong_types(self):
        _stub_open_prs(
            RowsRead(
                outcome="ok", rows=({"number": "bad", "title": "t", "url": "u"},),
                returncode=0, stderr="",
            )
        )
        assert _fetch_prs() == []

    def test_returns_empty_and_no_warning_for_ok_empty_rows(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        _stub_open_prs(RowsRead(outcome="ok", rows=(), returncode=0, stderr=""))
        with caplog.at_level(logging.WARNING):
            assert _fetch_prs() == []
        assert not caplog.records

    def test_returns_empty_and_warns_when_rows_is_none(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        _stub_open_prs(RowsRead(outcome="ok", rows=None, returncode=0, stderr=""))
        with caplog.at_level(logging.WARNING):
            assert _fetch_prs() == []
        assert any("collision pr list unavailable" in r.message for r in caplog.records)

    def test_returns_empty_and_warns_on_failed_outcome(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        _stub_open_prs(RowsRead(outcome="failed", rows=None, returncode=1, stderr="denied"))
        with caplog.at_level(logging.WARNING):
            assert _fetch_prs() == []
        assert any("collision pr list unavailable" in r.message for r in caplog.records)

    def test_returns_empty_and_warns_on_raised_outcome(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        _stub_open_prs(RowsRead(outcome="raised", rows=None, returncode=-1, stderr="boom"))
        with caplog.at_level(logging.WARNING):
            assert _fetch_prs() == []
        assert any("collision pr list unavailable" in r.message for r in caplog.records)

    def test_calls_open_prs_with_the_default_forge_target_limit_and_timeout(self):
        _stub_open_prs(RowsRead(outcome="ok", rows=(), returncode=0, stderr=""))
        fake = cast(FakeForge, active_forge())
        _fetch_prs()
        (call,) = fake.calls
        name, _args, kwargs = call
        assert name == "open_prs"
        assert kwargs["forge_target"] == DEFAULT_FORGE_TARGET
        assert kwargs["limit"] == 100
        assert kwargs["timeout"] == 60.0


class TestParsePrItem:
    def test_returns_none_for_non_dict(self):
        assert _parse_pr_item(None) is None
        assert _parse_pr_item("string") is None

    def test_returns_none_when_types_wrong(self):
        assert _parse_pr_item({"number": "bad", "title": "t", "url": "u"}) is None

    def test_returns_candidate_on_valid_item(self):
        item = _parse_pr_item({"number": 42, "title": "add auth", "url": "https://gh/42"})
        assert item is not None
        assert item.pr_number == 42
        assert item.title == "add auth"

    def test_uses_empty_string_for_missing_url(self):
        item = _parse_pr_item({"number": 1, "title": "t"})
        assert item is not None
        assert item.url == ""


class TestPrCandidates:
    def _stub_prs(self, prs):
        return patch("alan.collision._fetch_prs", return_value=prs)

    def _stub_diff(self, files=None):
        return _stub_changed_files(files)

    def test_returns_empty_when_no_prs(self):
        with self._stub_prs([]):
            assert _pr_candidates("add authentication flow") == []

    def test_filters_low_similarity(self):
        pr = _make_pr(title="fix typo in readme")
        with self._stub_prs([pr]), self._stub_diff():
            assert _pr_candidates("add authentication flow to API") == []

    def test_includes_high_similarity_pr(self):
        pr = _make_pr(title="add authentication flow to API")
        with self._stub_prs([pr]), self._stub_diff():
            cands = _pr_candidates("add authentication flow to API")
        assert len(cands) == 1
        assert cands[0].pr_number == 42

    def test_fetches_diff_files_for_candidate(self):
        pr = _make_pr(title="add authentication flow to API")
        files = frozenset({"auth.py", "models.py"})
        with self._stub_prs([pr]), self._stub_diff(files):
            cands = _pr_candidates("add authentication flow to API")
        assert cands[0].diff_files == files

    def test_calls_changed_files_with_the_default_forge_target(self):
        pr = _make_pr(title="add authentication flow to API")
        fake = cast(FakeForge, active_forge())
        with self._stub_prs([pr]), self._stub_diff(frozenset({"a.py"})):
            _pr_candidates("add authentication flow to API")
        (call,) = [c for c in fake.calls if c[0] == "changed_files"]
        assert call[1] == (pr.pr_number,)
        assert call[2]["forge_target"] == DEFAULT_FORGE_TARGET

    def test_caps_at_candidate_cap(self):
        prs = [
            _make_pr(number=i, title="add authentication flow to API")
            for i in range(_CANDIDATE_CAP + 3)
        ]
        with self._stub_prs(prs), self._stub_diff():
            cands = _pr_candidates("add authentication flow to API")
        assert len(cands) == _CANDIDATE_CAP


class TestWsCandidates:
    def test_returns_empty_when_no_workspaces(self, tmp_path):
        cands = _ws_candidates("add auth flow", workspaces_root=tmp_path)
        assert cands == []

    def test_filters_low_similarity(self, tmp_path):
        create("fix typo in readme", "small edit", root=tmp_path)
        cands = _ws_candidates("add authentication flow to the API", workspaces_root=tmp_path)
        assert cands == []

    def test_includes_similar_workspace(self, tmp_path):
        create("add authentication flow to the API", "implement oauth2", root=tmp_path)
        cands = _ws_candidates("add authentication flow to API", workspaces_root=tmp_path)
        assert len(cands) == 1

    def test_active_workspace_is_live(self, tmp_path):
        create("add authentication flow to the API", "implement oauth2", root=tmp_path)
        cands = _ws_candidates("add authentication flow to API", workspaces_root=tmp_path)
        assert cands[0].live is True

    def test_partial_workspace_is_not_live(self, tmp_path):
        from alan.workspace import transition
        ws = create("add authentication flow to the API", "oauth2 work", root=tmp_path)
        transition(ws, Status.PARTIAL)
        cands = _ws_candidates("add authentication flow to API", workspaces_root=tmp_path)
        assert cands[0].live is False

    def test_completed_workspace_is_not_live(self, tmp_path):
        from alan.workspace import transition
        ws = create("add authentication flow to the API", "oauth2 work", root=tmp_path)
        transition(ws, Status.COMPLETED)
        cands = _ws_candidates("add authentication flow to API", workspaces_root=tmp_path)
        assert cands[0].live is False

    def test_paused_workspace_is_live(self, tmp_path):
        from alan.workspace import transition
        ws = create("add authentication flow to the API", "oauth2 work", root=tmp_path)
        transition(ws, Status.PAUSED)
        cands = _ws_candidates("add authentication flow to API", workspaces_root=tmp_path)
        assert cands[0].live is True

    def test_caps_at_candidate_cap(self, tmp_path):
        for i in range(_CANDIDATE_CAP + 2):
            create(
                f"add authentication flow to the API version {i}",
                "oauth2 impl",
                root=tmp_path,
            )
        cands = _ws_candidates("add authentication flow to API", workspaces_root=tmp_path)
        assert len(cands) == _CANDIDATE_CAP

    def test_excludes_self_even_when_live(self, tmp_path):
        ws = create("add authentication flow to the API", "implement oauth2", root=tmp_path)
        cands = _ws_candidates(
            "add authentication flow to API",
            workspaces_root=tmp_path,
            exclude_task_id=ws.task_id,
        )
        assert cands == []

    def test_excludes_self_when_awaiting_input(self, tmp_path):
        from alan.workspace import transition
        ws = create("add authentication flow to the API", "implement oauth2", root=tmp_path)
        transition(ws, Status.AWAITING_INPUT)
        cands = _ws_candidates(
            "add authentication flow to API",
            workspaces_root=tmp_path,
            exclude_task_id=ws.task_id,
        )
        assert cands == []

    def test_exclude_task_id_with_no_match_is_a_no_op(self, tmp_path):
        create("add authentication flow to the API", "implement oauth2", root=tmp_path)
        cands = _ws_candidates(
            "add authentication flow to API",
            workspaces_root=tmp_path,
            exclude_task_id="ws-does-not-exist",
        )
        assert len(cands) == 1

    def test_exclude_task_id_does_not_relax_the_similarity_threshold(self, tmp_path):
        create("fix typo in readme", "small edit", root=tmp_path)
        cands = _ws_candidates(
            "add authentication flow to the API",
            workspaces_root=tmp_path,
            exclude_task_id="unrelated-id",
        )
        assert cands == []

    def test_cap_boundary_excluded_self_does_not_consume_a_cap_slot(self, tmp_path):
        self_ws = create(
            "add authentication flow to the API self", "oauth2 impl", root=tmp_path
        )
        for i in range(_CANDIDATE_CAP):
            create(
                f"add authentication flow to the API version {i}",
                "oauth2 impl",
                root=tmp_path,
            )
        cands = _ws_candidates(
            "add authentication flow to API",
            workspaces_root=tmp_path,
            exclude_task_id=self_ws.task_id,
        )
        assert len(cands) == _CANDIDATE_CAP
        assert all(c.task_id != self_ws.task_id for c in cands)


class TestFilesNote:
    def test_returns_empty_when_none(self):
        assert _files_note(None) == ""

    def test_returns_empty_when_empty_set(self):
        assert _files_note(frozenset()) == ""

    def test_returns_note_with_files(self):
        note = _files_note(frozenset({"auth.py", "models.py"}))
        assert note.startswith("; files: ")
        assert "auth.py" in note
        assert "models.py" in note


class TestBuildPrompt:
    def test_includes_brief(self):
        prompt = _build_prompt("add auth", [], [])
        assert "add auth" in prompt

    def test_includes_pr_candidate(self):
        pr = _make_pr(42, "add auth flow", "https://gh/42", frozenset({"a.py"}))
        prompt = _build_prompt("add auth", [pr], [])
        assert "pr_42" in prompt
        assert "live: true" in prompt
        assert "a.py" in prompt

    def test_includes_pr_candidate_without_files(self):
        pr = _make_pr(42, "add auth flow", "https://gh/42", None)
        prompt = _build_prompt("add auth", [pr], [])
        assert "pr_42" in prompt
        assert "; files:" not in prompt

    def test_includes_ws_candidate_live(self):
        ws = _make_ws("ws-abc", "auth work", live=True)
        prompt = _build_prompt("add auth", [], [ws])
        assert "ws_ws-abc" in prompt
        assert "live: true" in prompt

    def test_includes_ws_candidate_not_live(self):
        ws = _make_ws("ws-abc", "auth work", live=False)
        prompt = _build_prompt("add auth", [], [ws])
        assert "live: false" in prompt

    def test_truncates_long_brief(self):
        long_brief = "x" * 6000
        prompt = _build_prompt(long_brief, [], [])
        assert len(prompt) < len(long_brief) + 100


class TestCallModel:
    def test_returns_parsed_dict_on_success(self):
        raw = '{"verdicts": [], "sentence": "ok"}'
        with patch("alan.collision.run_agent", return_value=raw):
            out = _call_model("test prompt")
        assert out == {"verdicts": [], "sentence": "ok"}

    def test_returns_safe_on_exception(self):
        with patch("alan.collision.run_agent", side_effect=RuntimeError("fail")):
            out = _call_model("test prompt")
        assert out == {"verdicts": [], "sentence": ""}

    def test_returns_safe_on_invalid_json(self):
        with patch("alan.collision.run_agent", return_value="not json"):
            out = _call_model("test prompt")
        assert out == {"verdicts": [], "sentence": ""}

    def test_returns_safe_when_result_not_dict(self):
        with patch("alan.collision.run_agent", return_value="[1, 2]"):
            out = _call_model("test prompt")
        assert out == {"verdicts": [], "sentence": ""}

    def test_passes_stream_path_to_run_agent(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
        captured: dict[str, Any] = {}

        def fake_run_agent(*args, **kwargs):
            captured.update(kwargs)
            return '{"verdicts": [], "sentence": ""}'

        with patch("alan.collision.run_agent", side_effect=fake_run_agent):
            _call_model("test prompt")
        assert captured["stream_path"] == collision_mod._stream_path()

    def test_passes_custom_stream_path_to_run_agent(self, tmp_path):
        custom = tmp_path / "session-scoped" / "collision_stream.jsonl"
        captured: dict[str, Any] = {}

        def fake_run_agent(*args, **kwargs):
            captured.update(kwargs)
            return '{"verdicts": [], "sentence": ""}'

        with patch("alan.collision.run_agent", side_effect=fake_run_agent):
            _call_model("test prompt", stream_path=custom)
        assert captured["stream_path"] == custom


class TestStreamPath:
    def test_default_resolves_under_home_state_dir(self, monkeypatch):
        monkeypatch.delenv("ALAN_STATE_ROOT", raising=False)
        assert collision_mod._stream_path() == (
            Path.home() / ".alan" / "state" / "collision" / "stream.jsonl"
        )

    def test_respects_state_root_override(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
        assert collision_mod._stream_path() == tmp_path / "collision" / "stream.jsonl"

    def test_stream_write_stays_under_override_not_home(self, monkeypatch, tmp_path):
        fake_home = tmp_path / "fake-home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        state_root = tmp_path / "state"
        monkeypatch.setenv("ALAN_STATE_ROOT", str(state_root))

        def fake_run_agent(*args, **kwargs):
            stream_path = kwargs["stream_path"]
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            stream_path.write_text(
                '{"type": "result", "total_cost_usd": 0.001, '
                '"usage": {"input_tokens": 1, "output_tokens": 1}}\n'
            )
            return '{"verdicts": [], "sentence": ""}'

        with patch("alan.collision.run_agent", side_effect=fake_run_agent):
            _call_model("test prompt")

        assert (state_root / "collision" / "stream.jsonl").exists()
        assert not any(fake_home.rglob("*"))


class TestReadCollisionUsage:
    def test_returns_none_when_no_stream_file(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
        assert collision_mod.read_collision_usage() is None

    def test_sums_usage_across_runs(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path))
        stream = collision_mod._stream_path()
        stream.parent.mkdir(parents=True, exist_ok=True)
        events = [
            {
                "type": "result",
                "total_cost_usd": 0.001,
                "usage": {"input_tokens": 10, "output_tokens": 20},
            },
            {
                "type": "result",
                "total_cost_usd": 0.002,
                "usage": {"input_tokens": 15, "output_tokens": 25},
            },
        ]
        with stream.open("a", encoding="utf-8") as fh:
            for event in events:
                fh.write(json.dumps(event) + "\n")

        summary = collision_mod.read_collision_usage()

        assert summary is not None
        assert summary.model == "haiku"
        assert summary.total_cost_usd == pytest.approx(0.003)
        assert summary.total_input_tokens == 25
        assert summary.total_output_tokens == 45
        assert summary.tool_counts == []

    def test_reads_from_custom_stream_path_independent_of_global(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ALAN_STATE_ROOT", str(tmp_path / "global-state"))
        custom = tmp_path / "session-scoped" / "collision_stream.jsonl"
        custom.parent.mkdir(parents=True, exist_ok=True)
        custom.write_text(
            json.dumps(
                {
                    "type": "result",
                    "total_cost_usd": 0.005,
                    "usage": {"input_tokens": 7, "output_tokens": 3},
                }
            )
            + "\n"
        )

        summary = collision_mod.read_collision_usage(stream_path=custom)

        assert summary is not None
        assert summary.total_cost_usd == pytest.approx(0.005)
        assert summary.total_input_tokens == 7
        assert summary.total_output_tokens == 3
        assert not collision_mod._stream_path().exists()

    def test_returns_none_when_custom_stream_path_missing(self, tmp_path):
        missing = tmp_path / "does-not-exist" / "collision_stream.jsonl"
        assert collision_mod.read_collision_usage(stream_path=missing) is None


class TestClassify:
    def test_red_when_live_and_overlapping(self):
        assert _classify(True, True) == "red"

    def test_yellow_when_live_not_overlapping(self):
        assert _classify(True, False) == "yellow"

    def test_yellow_when_not_live_overlapping(self):
        assert _classify(False, True) == "yellow"

    def test_yellow_when_not_live_not_overlapping(self):
        assert _classify(False, False) == "yellow"


class TestMatchForVerdict:
    def _pr(self) -> _PrCandidate:
        return _make_pr(42, "add auth flow", "https://gh/42")

    def _ws(self) -> _WsCandidate:
        return _make_ws("ws-abc", "auth work", live=True)

    def test_returns_pr_match_when_found(self):
        found = _match_for_verdict("pr_42", [self._pr()], [])
        assert found is not None
        match, live = found
        assert match.kind == "pr"
        assert match.label == "add auth flow"
        assert match.deep_link == "https://gh/42"
        assert live is True

    def test_returns_none_when_pr_not_found(self):
        assert _match_for_verdict("pr_99", [self._pr()], []) is None

    def test_returns_ws_match_live(self):
        found = _match_for_verdict("ws_ws-abc", [], [self._ws()])
        assert found is not None
        match, live = found
        assert match.kind == "workspace"
        assert match.label == "auth work"
        assert match.deep_link == "/workspaces/ws-abc"
        assert live is True

    def test_returns_ws_match_not_live(self):
        ws = _make_ws("ws-abc", "auth work", live=False)
        found = _match_for_verdict("ws_ws-abc", [], [ws])
        assert found is not None
        _, live = found
        assert live is False

    def test_returns_none_when_ws_not_found(self):
        assert _match_for_verdict("ws_ws-xyz", [], [self._ws()]) is None

    def test_returns_none_for_unknown_prefix(self):
        assert _match_for_verdict("unknown_42", [self._pr()], [self._ws()]) is None

    def test_pr_prefix_but_no_match_falls_through_to_none(self):
        assert _match_for_verdict("pr_99", [self._pr()], []) is None


class TestVerdictHit:
    def _pr(self) -> _PrCandidate:
        return _make_pr(42, "add auth flow", "https://gh/42")

    def _ws(self) -> _WsCandidate:
        return _make_ws("ws-abc", "auth work", live=True)

    def test_returns_none_for_non_dict(self):
        assert _verdict_hit("not a dict", [], []) is None

    def test_returns_none_when_id_not_string(self):
        assert _verdict_hit({"id": 42, "overlapping": True}, [self._pr()], []) is None

    def test_returns_none_when_match_not_found(self):
        v = {"id": "pr_99", "overlapping": True}
        assert _verdict_hit(v, [self._pr()], []) is None

    def test_returns_red_for_live_overlapping(self):
        v = {"id": "pr_42", "overlapping": True}
        hit = _verdict_hit(v, [self._pr()], [])
        assert hit is not None
        state, _ = hit
        assert state == "red"

    def test_returns_yellow_for_live_not_overlapping(self):
        v = {"id": "pr_42", "overlapping": False}
        hit = _verdict_hit(v, [self._pr()], [])
        assert hit is not None
        state, _ = hit
        assert state == "yellow"

    def test_returns_yellow_for_not_live_overlapping(self):
        ws = _make_ws("ws-abc", "auth work", live=False)
        v = {"id": "ws_ws-abc", "overlapping": True}
        hit = _verdict_hit(v, [], [ws])
        assert hit is not None
        state, _ = hit
        assert state == "yellow"


class TestOrder:
    def test_green_is_lowest(self):
        cm = CollisionMatch(label="t", kind="pr", deep_link="u")
        assert _order(("green", cm)) < _order(("yellow", cm))
        assert _order(("yellow", cm)) < _order(("red", cm))


class TestApplyVerdicts:
    def _pr(self) -> _PrCandidate:
        return _make_pr(42, "add auth flow", "https://gh/42")

    def _ws(self) -> _WsCandidate:
        return _make_ws("ws-abc", "auth work", live=True)

    def test_returns_safe_when_verdicts_not_list(self):
        out = _apply_verdicts({"verdicts": "not-list", "sentence": ""}, [], [])
        assert out is _SAFE

    def test_returns_safe_when_no_hits(self):
        out = _apply_verdicts({"verdicts": [], "sentence": ""}, [], [])
        assert out is _SAFE

    def test_returns_safe_when_verdicts_dont_match(self):
        out = _apply_verdicts(
            {"verdicts": [{"id": "pr_99", "overlapping": True}], "sentence": "x"},
            [self._pr()], [],
        )
        assert out is _SAFE

    def test_returns_red_for_live_overlapping_pr(self):
        model_out: dict[str, Any] = {
            "verdicts": [{"id": "pr_42", "overlapping": True, "label": "PR 42"}],
            "sentence": "Live overlap with PR 42.",
        }
        result = _apply_verdicts(model_out, [self._pr()], [])
        assert result.state == "red"
        assert result.sentence == "Live overlap with PR 42."
        assert result.match is not None
        assert result.match.kind == "pr"

    def test_returns_yellow_for_not_overlapping_live(self):
        model_out: dict[str, Any] = {
            "verdicts": [{"id": "pr_42", "overlapping": False, "label": "PR 42"}],
            "sentence": "Similar but different scope.",
        }
        result = _apply_verdicts(model_out, [self._pr()], [])
        assert result.state == "yellow"

    def test_picks_worst_state_across_verdicts(self):
        ws = _make_ws("ws-abc", "auth work", live=False)
        model_out: dict[str, Any] = {
            "verdicts": [
                {"id": "ws_ws-abc", "overlapping": True},
                {"id": "pr_42", "overlapping": True},
            ],
            "sentence": "Red collision.",
        }
        result = _apply_verdicts(model_out, [self._pr()], [ws])
        assert result.state == "red"

    def test_uses_default_sentence_when_empty(self):
        model_out: dict[str, Any] = {
            "verdicts": [{"id": "pr_42", "overlapping": True}],
            "sentence": "",
        }
        result = _apply_verdicts(model_out, [self._pr()], [])
        assert "add auth flow" in result.sentence

    def test_skips_non_dict_verdict(self):
        model_out: dict[str, Any] = {
            "verdicts": ["bad", {"id": "pr_42", "overlapping": True}],
            "sentence": "hit",
        }
        result = _apply_verdicts(model_out, [self._pr()], [])
        assert result.state == "red"

    def test_ws_match_included(self):
        model_out: dict[str, Any] = {
            "verdicts": [{"id": "ws_ws-abc", "overlapping": True}],
            "sentence": "WS overlap.",
        }
        result = _apply_verdicts(model_out, [], [self._ws()])
        assert result.state == "red"
        assert result.match is not None
        assert result.match.kind == "workspace"
        assert result.match.deep_link == "/workspaces/ws-abc"


class TestCheckCollision:
    def test_returns_green_when_no_candidates(self, tmp_path):
        with patch("alan.collision._fetch_prs", return_value=[]):
            result = check_collision("add auth flow", workspaces_root=tmp_path)
        assert result is _SAFE

    def test_transport_failure_in_pr_list_returns_safe_verdict_and_logs_a_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture,
    ) -> None:
        _stub_open_prs(RowsRead(outcome="raised", rows=None, returncode=-1, stderr="boom"))
        with caplog.at_level(logging.WARNING):
            result = check_collision("add auth flow", workspaces_root=tmp_path)
        assert result is _SAFE
        assert any("collision pr list unavailable" in r.message for r in caplog.records)

    def test_returns_red_for_live_overlapping_pr(self, tmp_path):
        pr = _make_pr(42, "add authentication flow to API", "https://gh/42")
        model_response = '{"verdicts":[{"id":"pr_42","overlapping":true}],"sentence":"red overlap"}'
        with (
            patch("alan.collision._fetch_prs", return_value=[pr]),
            _stub_changed_files(None),
            patch("alan.collision.run_agent", return_value=model_response),
        ):
            result = check_collision("add authentication flow to API", workspaces_root=tmp_path)
        assert result.state == "red"
        assert result.sentence == "red overlap"

    def test_returns_yellow_for_completed_ws(self, tmp_path):
        from alan.workspace import transition
        ws = create("add authentication flow to the API", "oauth2 impl", root=tmp_path)
        transition(ws, Status.COMPLETED)
        model_response = (
            f'{{"verdicts":[{{"id":"ws_{ws.task_id}","overlapping":true}}],'
            f'"sentence":"yellow"}}'
        )
        with (
            patch("alan.collision._fetch_prs", return_value=[]),
            patch("alan.collision.run_agent", return_value=model_response),
        ):
            result = check_collision("add authentication flow to API", workspaces_root=tmp_path)
        assert result.state == "yellow"

    def test_degrades_to_green_on_unexpected_exception(self, tmp_path):
        with patch("alan.collision._pr_candidates", side_effect=RuntimeError("boom")):
            result = check_collision("anything", workspaces_root=tmp_path)
        assert result.state == "green"

    def test_degrades_to_green_when_model_fails(self, tmp_path):
        pr = _make_pr(42, "add authentication flow to API", "https://gh/42")
        with (
            patch("alan.collision._fetch_prs", return_value=[pr]),
            _stub_changed_files(None),
            patch("alan.collision.run_agent", side_effect=RuntimeError("model fail")),
        ):
            result = check_collision("add authentication flow to API", workspaces_root=tmp_path)
        assert result.state == "green"

    def test_passes_workspaces_root_to_ws_candidates(self, tmp_path):
        seen_roots: list[Path | None] = []
        original = _ws_candidates

        def recording_ws_candidates(
            brief, *, workspaces_root, exclude_task_id=None, scope_engine_key=None
        ):
            seen_roots.append(workspaces_root)
            return original(
                brief,
                workspaces_root=workspaces_root,
                exclude_task_id=exclude_task_id,
                scope_engine_key=scope_engine_key,
            )

        with (
            patch("alan.collision._fetch_prs", return_value=[]),
            patch("alan.collision._ws_candidates", side_effect=recording_ws_candidates),
        ):
            check_collision("anything", workspaces_root=tmp_path)
        assert seen_roots == [tmp_path]

    def test_stream_path_none_never_unlinked(self, tmp_path):
        with patch("alan.collision._fetch_prs", return_value=[]):
            result = check_collision("add auth flow", workspaces_root=tmp_path, stream_path=None)
        assert result is _SAFE

    def test_session_stream_unlinked_before_candidate_less_early_return(self, tmp_path):
        session_stream = tmp_path / "session" / "collision_stream.jsonl"
        session_stream.parent.mkdir(parents=True, exist_ok=True)
        session_stream.write_text(
            json.dumps(
                {
                    "type": "result",
                    "total_cost_usd": 0.01,
                    "usage": {"input_tokens": 100, "output_tokens": 100},
                }
            )
            + "\n"
        )

        with patch("alan.collision._fetch_prs", return_value=[]):
            result = check_collision(
                "add auth flow", workspaces_root=tmp_path, stream_path=session_stream
            )

        assert result is _SAFE
        assert not session_stream.exists()
        assert collision_mod.read_collision_usage(stream_path=session_stream) is None

    def test_session_stream_passed_through_to_call_model(self, tmp_path):
        session_stream = tmp_path / "session" / "collision_stream.jsonl"
        pr = _make_pr(42, "add authentication flow to API", "https://gh/42")
        captured: dict[str, Any] = {}

        def fake_run_agent(*args, **kwargs):
            captured.update(kwargs)
            return '{"verdicts": [], "sentence": ""}'

        with (
            patch("alan.collision._fetch_prs", return_value=[pr]),
            _stub_changed_files(None),
            patch("alan.collision.run_agent", side_effect=fake_run_agent),
        ):
            check_collision(
                "add authentication flow to API",
                workspaces_root=tmp_path,
                stream_path=session_stream,
            )
        assert captured["stream_path"] == session_stream

    def test_session_scoped_usage_readable_back_per_session(self, tmp_path):
        session_stream = tmp_path / "session" / "collision_stream.jsonl"
        pr = _make_pr(42, "add authentication flow to API", "https://gh/42")

        def fake_run_agent(*args, **kwargs):
            stream_path = kwargs["stream_path"]
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            stream_path.write_text(
                json.dumps(
                    {
                        "type": "result",
                        "total_cost_usd": 0.004,
                        "usage": {"input_tokens": 12, "output_tokens": 8},
                    }
                )
                + "\n"
            )
            return '{"verdicts": [], "sentence": ""}'

        with (
            patch("alan.collision._fetch_prs", return_value=[pr]),
            _stub_changed_files(None),
            patch("alan.collision.run_agent", side_effect=fake_run_agent),
        ):
            check_collision(
                "add authentication flow to API",
                workspaces_root=tmp_path,
                stream_path=session_stream,
            )

        summary = collision_mod.read_collision_usage(stream_path=session_stream)
        assert summary is not None
        assert summary.total_cost_usd == pytest.approx(0.004)
        assert summary.total_input_tokens == 12
        assert summary.total_output_tokens == 8
        assert collision_mod.read_collision_usage() is None

    def test_candidate_less_session_rerun_degrades_and_never_blocks(self, tmp_path):
        session_stream = tmp_path / "session" / "collision_stream.jsonl"
        session_stream.parent.mkdir(parents=True, exist_ok=True)
        session_stream.write_text(
            json.dumps(
                {
                    "type": "result",
                    "total_cost_usd": 0.09,
                    "usage": {"input_tokens": 900, "output_tokens": 900},
                }
            )
            + "\n"
        )
        with patch("alan.collision._pr_candidates", side_effect=RuntimeError("boom")):
            result = check_collision(
                "anything", workspaces_root=tmp_path, stream_path=session_stream
            )
        assert result.state == "green"

    def test_exclude_task_id_still_surfaces_other_near_duplicate_workspace(self, tmp_path):
        self_ws = create("add authentication flow to the API", "oauth2 self", root=tmp_path)
        other_ws = create("add authentication flow to the API", "oauth2 other", root=tmp_path)
        model_response = (
            f'{{"verdicts":[{{"id":"ws_{other_ws.task_id}","overlapping":true}}],'
            f'"sentence":"still overlaps"}}'
        )
        with (
            patch("alan.collision._fetch_prs", return_value=[]),
            patch("alan.collision.run_agent", return_value=model_response),
        ):
            result = check_collision(
                "add authentication flow to the API",
                workspaces_root=tmp_path,
                exclude_task_id=self_ws.task_id,
            )
        assert result.state == "red"
        assert result.match is not None
        assert result.match.deep_link == f"/workspaces/{other_ws.task_id}"

    def test_exclude_task_id_matching_only_a_pr_candidate_is_a_noop(self, tmp_path):
        pr = _make_pr(42, "add authentication flow to API", "https://gh/42")
        model_response = '{"verdicts":[{"id":"pr_42","overlapping":true}],"sentence":"red overlap"}'
        with (
            patch("alan.collision._fetch_prs", return_value=[pr]),
            _stub_changed_files(None),
            patch("alan.collision.run_agent", return_value=model_response),
        ):
            result = check_collision(
                "add authentication flow to API",
                workspaces_root=tmp_path,
                exclude_task_id="pr_42",
            )
        assert result.state == "red"


class TestCollisionCheckEndpoint:
    def test_returns_200_green_when_no_candidates(self, client, tmp_path):
        safe = CollisionResult(state="green", sentence="No overlapping work found.", match=None)
        with patch("alan.collision.check_collision", return_value=safe):
            res = client.post("/api/collision-check", json={"brief": "add auth flow"})
        assert res.status_code == 200
        data = res.json()
        assert data["state"] == "green"
        assert data["match"] is None

    def test_returns_200_red_with_match(self, client):
        match = CollisionMatch(label="PR #42", kind="pr", deep_link="https://gh/42")
        red = CollisionResult(state="red", sentence="Live overlap.", match=match)
        with patch("alan.collision.check_collision", return_value=red):
            res = client.post("/api/collision-check", json={"brief": "add auth"})
        assert res.status_code == 200
        data = res.json()
        assert data["state"] == "red"
        assert data["sentence"] == "Live overlap."
        assert data["match"]["label"] == "PR #42"
        assert data["match"]["kind"] == "pr"
        assert data["match"]["deep_link"] == "https://gh/42"

    def test_returns_200_yellow_with_ws_match(self, client):
        match = CollisionMatch(label="auth ws", kind="workspace", deep_link="/workspaces/ws-abc")
        yellow = CollisionResult(state="yellow", sentence="Similar work.", match=match)
        with patch("alan.collision.check_collision", return_value=yellow):
            res = client.post("/api/collision-check", json={"brief": "add auth"})
        assert res.status_code == 200
        data = res.json()
        assert data["state"] == "yellow"
        assert data["match"]["kind"] == "workspace"

    def test_returns_422_when_brief_missing(self, client):
        res = client.post("/api/collision-check", json={})
        assert res.status_code == 422

    def test_returns_200_green_even_when_check_raises(self, client):
        with patch(
            "alan.collision.check_collision",
            side_effect=RuntimeError("unexpected"),
        ):
            res = client.post("/api/collision-check", json={"brief": "add auth"})
        assert res.status_code == 200
        data = res.json()
        assert data["state"] == "green"

    def test_excludes_self_via_exclude_task_id(self, client, tmp_path):
        ws = create("add authentication flow to the API", "oauth2 impl", root=tmp_path)
        res = client.post(
            "/api/collision-check",
            json={
                "brief": "add authentication flow to the API",
                "exclude_task_id": ws.task_id,
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["state"] == "green"
        assert data["match"] is None

    def test_unknown_exclude_task_id_forwards_through_and_degrades_normally(self, client):
        with patch(
            "alan.collision.check_collision", return_value=_SAFE
        ) as mocked:
            res = client.post(
                "/api/collision-check",
                json={"brief": "add auth flow", "exclude_task_id": "does-not-exist"},
            )
        assert res.status_code == 200
        assert mocked.call_args.kwargs["exclude_task_id"] == "does-not-exist"

    def test_empty_string_exclude_task_id_is_accepted(self, client):
        res = client.post(
            "/api/collision-check",
            json={"brief": "add auth flow", "exclude_task_id": ""},
        )
        assert res.status_code == 200

    def test_over_length_exclude_task_id_returns_422(self, client):
        res = client.post(
            "/api/collision-check",
            json={"brief": "add auth flow", "exclude_task_id": "x" * 201},
        )
        assert res.status_code == 422

    def test_omitted_exclude_task_id_forwards_none_and_keeps_baseline_shape(
        self, client, monkeypatch
    ):
        captured: dict[str, Any] = {}

        def fake_check_collision(
            brief, *, workspaces_root=None, stream_path=None, exclude_task_id=None,
            scope_engine_key=None,
        ):
            captured["exclude_task_id"] = exclude_task_id
            captured["scope_engine_key"] = scope_engine_key
            return _SAFE

        monkeypatch.setattr(collision_mod, "check_collision", fake_check_collision)
        res = client.post("/api/collision-check", json={"brief": "add auth flow"})
        assert res.status_code == 200
        data = res.json()
        assert data == {"state": "green", "sentence": "No overlapping work found.", "match": None}
        assert captured["exclude_task_id"] is None
        assert captured["scope_engine_key"] is None

    def test_engine_key_forwarded_as_scope_engine_key(self, client, monkeypatch):
        captured: dict[str, Any] = {}

        def fake_check_collision(
            brief, *, workspaces_root=None, stream_path=None, exclude_task_id=None,
            scope_engine_key=None,
        ):
            captured["scope_engine_key"] = scope_engine_key
            return _SAFE

        monkeypatch.setattr(collision_mod, "check_collision", fake_check_collision)
        res = client.post(
            "/api/collision-check",
            json={"brief": "add auth flow", "engine_key": "sprocket-co/sprocket"},
        )
        assert res.status_code == 200
        assert captured["scope_engine_key"] == "sprocket-co/sprocket"


def _register_sprocket_engine() -> None:
    register(EngineConfig(repo=RepoIdentity(owner="sprocket-co", repo="sprocket")))


class TestWsCandidatesEngineScoping:
    def test_default_scope_excludes_sprocket_keyed_workspace(self, tmp_path):
        _register_sprocket_engine()
        create(
            "add authentication flow to the API",
            "implement oauth2",
            root=tmp_path,
            engine_key="sprocket-co/sprocket",
        )
        cands = _ws_candidates(
            "add authentication flow to API", workspaces_root=tmp_path, scope_engine_key=None
        )
        assert cands == []

    def test_explicit_default_scope_still_matches_none_keyed_workspace(self, tmp_path):
        create(
            "add authentication flow to the API",
            "implement oauth2",
            root=tmp_path,
        )
        cands = _ws_candidates(
            "add authentication flow to API",
            workspaces_root=tmp_path,
            scope_engine_key="DejvMatikar/alan",
        )
        assert len(cands) == 1

    def test_none_scope_still_matches_explicit_default_slug_keyed_workspace(self, tmp_path):
        create(
            "add authentication flow to the API",
            "implement oauth2",
            root=tmp_path,
            engine_key="DejvMatikar/alan",
        )
        cands = _ws_candidates(
            "add authentication flow to API", workspaces_root=tmp_path, scope_engine_key=None
        )
        assert len(cands) == 1

    def test_sprocket_scope_never_matches_alan_workspace(self, tmp_path):
        _register_sprocket_engine()
        create("add authentication flow to the API", "implement oauth2", root=tmp_path)
        cands = _ws_candidates(
            "add authentication flow to API",
            workspaces_root=tmp_path,
            scope_engine_key="sprocket-co/sprocket",
        )
        assert cands == []

    def test_sprocket_scope_matches_sprocket_keyed_workspace(self, tmp_path):
        _register_sprocket_engine()
        create(
            "add authentication flow to the API",
            "implement oauth2",
            root=tmp_path,
            engine_key="sprocket-co/sprocket",
        )
        cands = _ws_candidates(
            "add authentication flow to API",
            workspaces_root=tmp_path,
            scope_engine_key="sprocket-co/sprocket",
        )
        assert len(cands) == 1

    def test_unregistered_scope_engine_key_folds_to_default(self, tmp_path):
        create("add authentication flow to the API", "implement oauth2", root=tmp_path)
        cands = _ws_candidates(
            "add authentication flow to API",
            workspaces_root=tmp_path,
            scope_engine_key="unregistered-co/unregistered",
        )
        assert len(cands) == 1


class TestCheckCollisionEngineScoping:
    def test_non_default_scope_skips_pr_overlap_branch(self, tmp_path):
        _register_sprocket_engine()
        pr = _make_pr(42, "add authentication flow to API", "https://gh/42")
        with patch("alan.collision._fetch_prs", return_value=[pr]) as fetch_mock:
            result = check_collision(
                "add authentication flow to API",
                workspaces_root=tmp_path,
                scope_engine_key="sprocket-co/sprocket",
            )
        fetch_mock.assert_not_called()
        assert result is _SAFE

    def test_default_scope_still_checks_pr_overlap(self, tmp_path):
        pr = _make_pr(42, "add authentication flow to API", "https://gh/42")
        model_response = '{"verdicts":[{"id":"pr_42","overlapping":true}],"sentence":"red"}'
        with (
            patch("alan.collision._fetch_prs", return_value=[pr]),
            _stub_changed_files(None),
            patch("alan.collision.run_agent", return_value=model_response),
        ):
            result = check_collision(
                "add authentication flow to API", workspaces_root=tmp_path, scope_engine_key=None
            )
        assert result.state == "red"

    def test_explicit_default_slug_scope_still_checks_pr_overlap(self, tmp_path):
        pr = _make_pr(42, "add authentication flow to API", "https://gh/42")
        model_response = '{"verdicts":[{"id":"pr_42","overlapping":true}],"sentence":"red"}'
        with (
            patch("alan.collision._fetch_prs", return_value=[pr]),
            _stub_changed_files(None),
            patch("alan.collision.run_agent", return_value=model_response),
        ):
            result = check_collision(
                "add authentication flow to API",
                workspaces_root=tmp_path,
                scope_engine_key="DejvMatikar/alan",
            )
        assert result.state == "red"

    def test_sprocket_scope_ws_candidate_still_flows_through_to_model(self, tmp_path):
        _register_sprocket_engine()
        create(
            "add authentication flow to the API",
            "oauth2 impl",
            root=tmp_path,
            engine_key="sprocket-co/sprocket",
        )
        with (
            patch("alan.collision._fetch_prs", return_value=[]),
            patch(
                "alan.collision.run_agent", return_value='{"verdicts": [], "sentence": ""}'
            ) as run_agent_mock,
        ):
            check_collision(
                "add authentication flow to API",
                workspaces_root=tmp_path,
                scope_engine_key="sprocket-co/sprocket",
            )
        run_agent_mock.assert_called_once()


class TestCollisionCheckSessionScoping:
    _SID = "12345678-1234-1234-1234-1234567890ab"

    @pytest.fixture
    def client(self, tmp_path):
        build_root = tmp_path / "build-chat-sessions"
        explore_root = tmp_path / "explore-chat-sessions"
        server.app.dependency_overrides[server._workspaces_root_dep] = (
            lambda: tmp_path / "workspaces"
        )
        server.app.dependency_overrides[server._build_chat_sessions_root_dep] = (
            lambda: build_root
        )
        server.app.dependency_overrides[server._explore_chat_sessions_root_dep] = (
            lambda: explore_root
        )
        try:
            with TestClient(server.app, follow_redirects=True) as c:
                yield c, build_root, explore_root
        finally:
            server.app.dependency_overrides.clear()

    def _record_stream_path(self, monkeypatch):
        captured: dict[str, Any] = {}

        def fake_check_collision(
            brief, *, workspaces_root=None, stream_path=None, exclude_task_id=None,
            scope_engine_key=None,
        ):
            captured["stream_path"] = stream_path
            return _SAFE

        monkeypatch.setattr(collision_mod, "check_collision", fake_check_collision)
        return captured

    def test_global_request_omits_family_and_session_resolves_none(self, client, monkeypatch):
        c, _, _ = client
        captured = self._record_stream_path(monkeypatch)
        res = c.post("/api/collision-check", json={"brief": "add auth"})
        assert res.status_code == 200
        assert captured["stream_path"] is None

    def test_build_family_resolves_session_scoped_path(self, client, monkeypatch):
        c, build_root, _ = client
        session = build_chat_mod.create_session(root=build_root)
        captured = self._record_stream_path(monkeypatch)
        res = c.post(
            "/api/collision-check",
            json={
                "brief": "add auth",
                "chat_family": "build",
                "session_id": session.session_id,
            },
        )
        assert res.status_code == 200
        assert captured["stream_path"] == _chat_core.collision_stream_path(
            build_chat_mod.CONFIG, session.session_id, root=build_root
        )

    def test_explore_family_resolves_session_scoped_path(self, client, monkeypatch):
        c, _, explore_root = client
        session = explore_chat_mod.create_session("brainstorming", root=explore_root)
        captured = self._record_stream_path(monkeypatch)
        res = c.post(
            "/api/collision-check",
            json={
                "brief": "add auth",
                "chat_family": "explore",
                "session_id": session.session_id,
            },
        )
        assert res.status_code == 200
        assert captured["stream_path"] == _chat_core.collision_stream_path(
            explore_chat_mod.CONFIG, session.session_id, root=explore_root
        )

    def test_nonexistent_session_degrades_to_global_none(self, client, monkeypatch):
        c, _, _ = client
        captured = self._record_stream_path(monkeypatch)
        res = c.post(
            "/api/collision-check",
            json={"brief": "add auth", "chat_family": "build", "session_id": self._SID},
        )
        assert res.status_code == 200
        assert captured["stream_path"] is None

    def test_nonexistent_session_creates_no_orphan_directory(self, client):
        c, build_root, _ = client
        res = c.post(
            "/api/collision-check",
            json={"brief": "add auth", "chat_family": "build", "session_id": self._SID},
        )
        assert res.status_code == 200
        assert not (build_root / self._SID).exists()

    def test_invalid_session_id_degrades_to_global_none(self, client, monkeypatch):
        c, _, _ = client
        captured = self._record_stream_path(monkeypatch)
        res = c.post(
            "/api/collision-check",
            json={"brief": "add auth", "chat_family": "build", "session_id": "not-a-uuid"},
        )
        assert res.status_code == 200
        assert captured["stream_path"] is None

    def test_session_id_without_family_resolves_none(self, client, monkeypatch):
        c, _, _ = client
        captured = self._record_stream_path(monkeypatch)
        res = c.post(
            "/api/collision-check",
            json={"brief": "add auth", "session_id": self._SID},
        )
        assert res.status_code == 200
        assert captured["stream_path"] is None

    def test_session_scoped_usage_round_trips_through_endpoint(self, client):
        c, build_root, _ = client
        session = build_chat_mod.create_session(root=build_root)
        pr = _make_pr(42, "add authentication flow to API", "https://gh/42")

        def fake_run_agent(*args, **kwargs):
            stream_path = kwargs["stream_path"]
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            stream_path.write_text(
                json.dumps(
                    {
                        "type": "result",
                        "total_cost_usd": 0.002,
                        "usage": {"input_tokens": 5, "output_tokens": 9},
                    }
                )
                + "\n"
            )
            return '{"verdicts": [], "sentence": ""}'

        with (
            patch("alan.collision._fetch_prs", return_value=[pr]),
            _stub_changed_files(None),
            patch("alan.collision.run_agent", side_effect=fake_run_agent),
        ):
            res = c.post(
                "/api/collision-check",
                json={
                    "brief": "add authentication flow to API",
                    "chat_family": "build",
                    "session_id": session.session_id,
                },
            )
        assert res.status_code == 200

        expected_path = build_root / session.session_id / "collision_stream.jsonl"
        assert expected_path.exists()
        summary = collision_mod.read_collision_usage(stream_path=expected_path)
        assert summary is not None
        assert summary.total_cost_usd == pytest.approx(0.002)

    def test_state_isolation_nothing_written_under_real_home(self, monkeypatch, tmp_path):
        fake_home = tmp_path / "fake-home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        build_root = tmp_path / "build-chat-sessions"
        server.app.dependency_overrides[server._workspaces_root_dep] = (
            lambda: tmp_path / "workspaces"
        )
        server.app.dependency_overrides[server._build_chat_sessions_root_dep] = (
            lambda: build_root
        )
        server.app.dependency_overrides[server._explore_chat_sessions_root_dep] = (
            lambda: tmp_path / "explore-chat-sessions"
        )
        session = build_chat_mod.create_session(root=build_root)
        pr = _make_pr(42, "add authentication flow to API", "https://gh/42")

        def fake_run_agent(*args, **kwargs):
            stream_path = kwargs["stream_path"]
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            stream_path.write_text(
                json.dumps(
                    {
                        "type": "result",
                        "total_cost_usd": 0.001,
                        "usage": {"input_tokens": 1, "output_tokens": 1},
                    }
                )
                + "\n"
            )
            return '{"verdicts": [], "sentence": ""}'

        try:
            with TestClient(server.app, follow_redirects=True) as c:
                with (
                    patch("alan.collision._fetch_prs", return_value=[pr]),
                    _stub_changed_files(None),
                    patch("alan.collision.run_agent", side_effect=fake_run_agent),
                ):
                    res = c.post(
                        "/api/collision-check",
                        json={
                            "brief": "add authentication flow to API",
                            "chat_family": "build",
                            "session_id": session.session_id,
                        },
                    )
        finally:
            server.app.dependency_overrides.clear()

        assert res.status_code == 200
        assert (build_root / session.session_id / "collision_stream.jsonl").exists()
        assert not any(fake_home.rglob("*"))

    def test_session_id_and_exclude_task_id_both_forwarded(self, client, monkeypatch):
        c, build_root, _ = client
        session = build_chat_mod.create_session(root=build_root)
        captured: dict[str, Any] = {}

        def fake_check_collision(
            brief, *, workspaces_root=None, stream_path=None, exclude_task_id=None,
            scope_engine_key=None,
        ):
            captured["stream_path"] = stream_path
            captured["exclude_task_id"] = exclude_task_id
            return _SAFE

        monkeypatch.setattr(collision_mod, "check_collision", fake_check_collision)
        res = c.post(
            "/api/collision-check",
            json={
                "brief": "add auth",
                "chat_family": "build",
                "session_id": session.session_id,
                "exclude_task_id": "ws-self",
            },
        )
        assert res.status_code == 200
        assert captured["exclude_task_id"] == "ws-self"
        assert captured["stream_path"] == _chat_core.collision_stream_path(
            build_chat_mod.CONFIG, session.session_id, root=build_root
        )


class TestCollisionCheckExploreScopeResolution:
    @pytest.fixture
    def client(self, tmp_path):
        build_root = tmp_path / "build-chat-sessions"
        explore_root = tmp_path / "explore-chat-sessions"
        server.app.dependency_overrides[server._workspaces_root_dep] = (
            lambda: tmp_path / "workspaces"
        )
        server.app.dependency_overrides[server._build_chat_sessions_root_dep] = (
            lambda: build_root
        )
        server.app.dependency_overrides[server._explore_chat_sessions_root_dep] = (
            lambda: explore_root
        )
        try:
            with TestClient(server.app, follow_redirects=True) as c:
                yield c, build_root, explore_root
        finally:
            server.app.dependency_overrides.clear()

    def _record_scope_engine_key(self, monkeypatch):
        captured: dict[str, Any] = {}

        def fake_check_collision(
            brief, *, workspaces_root=None, stream_path=None, exclude_task_id=None,
            scope_engine_key=None,
        ):
            captured["scope_engine_key"] = scope_engine_key
            return _SAFE

        monkeypatch.setattr(collision_mod, "check_collision", fake_check_collision)
        return captured

    def _register_sprocket_engine(self):
        repo = RepoIdentity(owner="sprocket-co", repo="sprocket")
        cfg = EngineConfig(
            repo=repo, forge=ForgeConfig(repo=repo, credential_env_name="SPROCKET_GH_TOKEN")
        )
        register(cfg, validate_env=False)
        return cfg

    def test_explore_family_resolves_bound_engine_key_without_project(
        self, client, monkeypatch
    ):
        c, _, explore_root = client
        self._register_sprocket_engine()
        session = explore_chat_mod.create_session(
            "brainstorming", root=explore_root, engine_key="sprocket-co/sprocket"
        )
        assert session.project_id is None
        captured = self._record_scope_engine_key(monkeypatch)
        res = c.post(
            "/api/collision-check",
            json={
                "brief": "add auth",
                "chat_family": "explore",
                "session_id": session.session_id,
            },
        )
        assert res.status_code == 200
        assert captured["scope_engine_key"] == "sprocket-co/sprocket"

    def test_explore_family_ignores_client_supplied_engine_key(
        self, client, monkeypatch
    ):
        c, _, explore_root = client
        session = explore_chat_mod.create_session("brainstorming", root=explore_root)
        captured = self._record_scope_engine_key(monkeypatch)
        res = c.post(
            "/api/collision-check",
            json={
                "brief": "add auth",
                "chat_family": "explore",
                "session_id": session.session_id,
                "engine_key": "attacker/repo",
            },
        )
        assert res.status_code == 200
        assert captured["scope_engine_key"] is None

    def test_explore_family_no_bound_engine_key_resolves_none(self, client, monkeypatch):
        c, _, explore_root = client
        session = explore_chat_mod.create_session("brainstorming", root=explore_root)
        captured = self._record_scope_engine_key(monkeypatch)
        res = c.post(
            "/api/collision-check",
            json={
                "brief": "add auth",
                "chat_family": "explore",
                "session_id": session.session_id,
            },
        )
        assert res.status_code == 200
        assert captured["scope_engine_key"] is None

    def test_explore_family_unresolvable_session_resolves_none(self, client, monkeypatch):
        c, _, _ = client
        captured = self._record_scope_engine_key(monkeypatch)
        res = c.post(
            "/api/collision-check",
            json={
                "brief": "add auth",
                "chat_family": "explore",
                "session_id": "12345678-1234-1234-1234-1234567890ab",
                "engine_key": "attacker/repo",
            },
        )
        assert res.status_code == 200
        assert captured["scope_engine_key"] is None

    def test_build_family_engine_key_still_forwarded_verbatim(self, client, monkeypatch):
        c, build_root, _ = client
        session = build_chat_mod.create_session(root=build_root)
        captured = self._record_scope_engine_key(monkeypatch)
        res = c.post(
            "/api/collision-check",
            json={
                "brief": "add auth",
                "chat_family": "build",
                "session_id": session.session_id,
                "engine_key": "sprocket-co/sprocket",
            },
        )
        assert res.status_code == 200
        assert captured["scope_engine_key"] == "sprocket-co/sprocket"
