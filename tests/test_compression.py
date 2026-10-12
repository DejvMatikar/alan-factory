from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan.compression import (
    DEFAULT_MAX_CHARS,
    load_cross_team_prior_outputs,
    summarize_handover,
    summarize_handover_decisions_only,
)
from alan.handover import Decision, Handover, OpenQuestion, record_answer, save_handover
from alan.workspace import Workspace, create, read


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "workspaces"


def _h(
    workspace: Workspace,
    *,
    handover_id: str | None = None,
    from_team: str = "research",
    to_team_suggestions: list[str] | None = None,
    decisions: list[Decision] | None = None,
    open_questions: list[OpenQuestion] | None = None,
    forbidden_actions: list[str] | None = None,
    summary: str = "",
    iteration: int = 1,
) -> Handover:
    return Handover(
        handover_id=handover_id or f"handover_{workspace.task_id}_{from_team}",
        handover_type="forward",
        task_id=workspace.task_id,
        from_team=from_team,
        from_invocation_id="inv_001",
        to_team_suggestions=to_team_suggestions or [],
        iteration=iteration,
        timestamp_utc=datetime.now(UTC),
        decisions=decisions or [],
        open_questions=open_questions or [],
        forbidden_actions=forbidden_actions or [],
        full_synthesis=summary,
    )


def test_default_max_chars_constant() -> None:
    assert DEFAULT_MAX_CHARS == 2500


def test_summarize_handover_decisions_only(ws_root: Path) -> None:
    ws = create("dec-only", "d", root=ws_root)
    handover = _h(
        ws,
        decisions=[
            Decision(
                id="d1",
                statement="use json",
                rationale="simple",
                confidence=0.9,
                reversibility="high",
            ),
            Decision(
                id="d2",
                statement="atomic writes",
                rationale="safety",
                confidence=0.8,
                reversibility="medium",
            ),
        ],
    )
    out = summarize_handover(handover)
    assert "Decisions:" in out
    assert "use json" in out
    assert "simple" in out
    assert "atomic writes" in out
    assert "Open questions:" not in out
    assert "Summary:" not in out


def test_summarize_handover_questions_only(ws_root: Path) -> None:
    ws = create("q-only", "d", root=ws_root)
    handover = _h(
        ws,
        open_questions=[
            OpenQuestion(
                id="q1",
                question="Which db?",
                resolver_role="brainstorming",
                blocking=True,
                prior_attempts=[],
            ),
            OpenQuestion(
                id="q2",
                question="Soft question — skipped",
                resolver_role="research",
                blocking=False,
                prior_attempts=[],
            ),
        ],
    )
    out = summarize_handover(handover)
    assert "Open questions:" in out
    assert "Which db?" in out
    assert "brainstorming" in out
    assert "Soft question" not in out
    assert "Decisions:" not in out


def test_summarize_handover_inline_answered_question_shows_answer(ws_root: Path) -> None:
    ws = create("q-inline-answered", "d", root=ws_root)
    handover = _h(
        ws,
        open_questions=[
            OpenQuestion(
                id="q1",
                question="Which db?",
                proposed_answer_space=["sqlite", "duckdb"],
                resolver_role="brainstorming",
                blocking=True,
                answer="sqlite",
            ),
        ],
    )
    out = summarize_handover(handover)
    assert "Open questions:" in out
    assert "Which db?" in out
    assert "[ANSWERED: sqlite]" in out


def test_summarize_handover_inline_pending_external_answer_shows_notice(ws_root: Path) -> None:
    ws = create("q-inline-pending-external", "d", root=ws_root)
    handover = _h(
        ws,
        open_questions=[
            OpenQuestion(
                id="q1",
                question="resolve outside the fleet or waive?",
                proposed_answer_space=["resolve outside the fleet", "waive"],
                resolver_role="human",
                blocking=True,
                answer="resolve outside the fleet",
                option_effects={"resolve outside the fleet": "pending_external", "waive": "closes"},
            ),
        ],
    )
    out = summarize_handover(handover)
    assert "Open questions:" in out
    assert "resolve outside the fleet or waive?" in out
    assert "A human undertook to perform this step outside the fleet" in out
    assert "[ANSWERED:" not in out


def test_summarize_handover_inline_closes_answer_unaffected_by_option_effects(
    ws_root: Path,
) -> None:
    ws = create("q-inline-closes", "d", root=ws_root)
    handover = _h(
        ws,
        open_questions=[
            OpenQuestion(
                id="q1",
                question="resolve outside the fleet or waive?",
                proposed_answer_space=["resolve outside the fleet", "waive"],
                resolver_role="human",
                blocking=True,
                answer="waive",
                option_effects={"resolve outside the fleet": "pending_external", "waive": "closes"},
            ),
        ],
    )
    out = summarize_handover(handover)
    assert "[ANSWERED: waive]" in out
    assert "A human undertook to perform this step outside the fleet" not in out


def test_summarize_handover_chain_recorded_answer_shows_answer_when_workspace_passed(
    ws_root: Path,
) -> None:
    ws = create("q-chain-answered", "d", root=ws_root)
    q = OpenQuestion(
        id="q1",
        question="Which db?",
        proposed_answer_space=["sqlite", "duckdb"],
        resolver_role="brainstorming",
        blocking=True,
    )
    blocking = _h(ws, open_questions=[q], summary="the situation that prompted the question")
    save_handover(ws, blocking)
    record_answer(ws, blocking, "q1", "duckdb", from_team=blocking.from_team)

    out = summarize_handover(blocking, workspace=ws)
    assert "Which db?" in out
    assert "[ANSWERED: duckdb]" in out
    assert "the situation that prompted the question" in out


def test_summarize_handover_chain_recorded_answer_not_shown_without_workspace(
    ws_root: Path,
) -> None:
    ws = create("q-chain-no-ws", "d", root=ws_root)
    q = OpenQuestion(
        id="q1",
        question="Which db?",
        proposed_answer_space=["sqlite", "duckdb"],
        resolver_role="brainstorming",
        blocking=True,
    )
    blocking = _h(ws, open_questions=[q])
    save_handover(ws, blocking)
    record_answer(ws, blocking, "q1", "duckdb", from_team=blocking.from_team)

    out = summarize_handover(blocking)
    assert "Which db?" in out
    assert "[ANSWERED:" not in out


def test_load_cross_team_prior_outputs_shows_chain_recorded_answer(ws_root: Path) -> None:
    ws = create("q-chain-cross-team", "d", root=ws_root)
    q = OpenQuestion(
        id="q1",
        question="Which db?",
        proposed_answer_space=["sqlite", "duckdb"],
        resolver_role="brainstorming",
        blocking=True,
    )
    blocking = _h(
        ws,
        from_team="research",
        to_team_suggestions=["implementation_plan"],
        open_questions=[q],
        summary="context for the next team",
    )
    save_handover(ws, blocking)
    record_answer(ws, blocking, "q1", "duckdb", from_team=blocking.from_team)

    out = load_cross_team_prior_outputs(ws, "implementation_plan")
    assert "Which db?" in out
    assert "[ANSWERED: duckdb]" in out


def test_summarize_handover_summary_only(ws_root: Path) -> None:
    ws = create("sum-only", "d", root=ws_root)
    handover = _h(ws, summary="plan ready, proceed to implementation")
    out = summarize_handover(handover)
    assert out.startswith("Summary:")
    assert "plan ready" in out
    assert "Decisions:" not in out


def test_summarize_handover_all_three_sections(ws_root: Path) -> None:
    ws = create("all-three", "d", root=ws_root)
    handover = _h(
        ws,
        decisions=[
            Decision(
                id="d1",
                statement="pick option A",
                rationale="cheapest",
                confidence=0.7,
                reversibility="medium",
            )
        ],
        open_questions=[
            OpenQuestion(
                id="q1",
                question="storage choice?",
                resolver_role="brainstorming",
                blocking=True,
                prior_attempts=[],
            )
        ],
        summary="moving forward with option A pending storage answer",
    )
    out = summarize_handover(handover)
    assert "Decisions:" in out
    assert "Open questions:" in out
    assert "Summary:" in out
    assert out.index("Decisions:") < out.index("Open questions:") < out.index("Summary:")


def test_summarize_handover_empty_handover_returns_empty(ws_root: Path) -> None:
    ws = create("empty", "d", root=ws_root)
    handover = _h(ws)
    assert summarize_handover(handover) == ""


def test_summarize_handover_truncates_to_max_chars(ws_root: Path) -> None:
    ws = create("trunc", "d", root=ws_root)
    handover = _h(ws, summary="x" * 5000)
    out = summarize_handover(handover, max_chars=100)
    assert len(out) == 100


def test_load_cross_team_prior_outputs_empty_workspace(ws_root: Path) -> None:
    ws = create("empty-ws", "d", root=ws_root)
    assert load_cross_team_prior_outputs(ws, "implementation_plan") == ""


def test_load_cross_team_prior_outputs_no_matches(ws_root: Path) -> None:
    ws = create("no-match", "d", root=ws_root)
    h = _h(ws, from_team="research", to_team_suggestions=["brainstorming"])
    save_handover(ws, h)
    assert load_cross_team_prior_outputs(ws, "implementation_plan") == ""


def test_load_cross_team_prior_outputs_returns_newest_first(ws_root: Path) -> None:
    ws = create("ordering", "d", root=ws_root)
    h1 = _h(
        ws,
        handover_id=f"handover_{ws.task_id}_first",
        from_team="research",
        to_team_suggestions=["implementation_plan"],
        summary="older summary content here",
    )
    h2 = _h(
        ws,
        handover_id=f"handover_{ws.task_id}_second",
        from_team="brainstorming",
        to_team_suggestions=["implementation_plan"],
        summary="newer summary content here",
    )
    save_handover(ws, h1)
    save_handover(ws, h2)
    out = load_cross_team_prior_outputs(ws, "implementation_plan")
    assert out.index("newer summary") < out.index("older summary")
    assert "Prior handover from brainstorming" in out
    assert "Prior handover from research" in out


def test_load_cross_team_prior_outputs_respects_max_chars(ws_root: Path) -> None:
    ws = create("cap", "d", root=ws_root)
    for i in range(5):
        save_handover(
            ws,
            _h(
                ws,
                handover_id=f"handover_{ws.task_id}_n{i}",
                from_team="research",
                to_team_suggestions=["implementation_plan"],
                summary="A" * 400,
            ),
        )
    out = load_cross_team_prior_outputs(ws, "implementation_plan", max_chars=500)
    assert len(out) <= 1000


def test_load_cross_team_prior_outputs_skips_handovers_with_empty_body(
    ws_root: Path,
) -> None:
    ws = create("skip-empty", "d", root=ws_root)
    save_handover(
        ws,
        _h(
            ws,
            handover_id=f"handover_{ws.task_id}_skip",
            from_team="research",
            to_team_suggestions=["implementation_plan"],
            summary="",
        ),
    )
    save_handover(
        ws,
        _h(
            ws,
            handover_id=f"handover_{ws.task_id}_keep",
            from_team="research",
            to_team_suggestions=["implementation_plan"],
            summary="real content here",
        ),
    )
    out = load_cross_team_prior_outputs(ws, "implementation_plan")
    assert "real content here" in out


def test_load_cross_team_prior_outputs_zero_max_chars(ws_root: Path) -> None:
    ws = create("zero", "d", root=ws_root)
    save_handover(
        ws,
        _h(
            ws,
            from_team="research",
            to_team_suggestions=["implementation_plan"],
            summary="anything",
        ),
    )
    assert load_cross_team_prior_outputs(ws, "implementation_plan", max_chars=0) == ""


def test_summarize_handover_renders_forbidden_block(ws_root: Path) -> None:
    ws = create("forbid", "d", root=ws_root)
    h = _h(
        ws,
        forbidden_actions=["do not push branches", "do not bypass hooks"],
        summary="proceed",
    )
    out = summarize_handover(h)
    assert "Forbidden:" in out
    assert "- do not push branches" in out
    assert "- do not bypass hooks" in out


def test_summarize_handover_skips_empty_forbidden_entries(ws_root: Path) -> None:
    ws = create("forbid2", "d", root=ws_root)
    h = _h(
        ws,
        forbidden_actions=["", "   ", "real entry"],
        summary="ok",
    )
    out = summarize_handover(h)
    assert "Forbidden:" in out
    assert "- real entry" in out


def test_summarize_handover_omits_forbidden_when_all_blank(ws_root: Path) -> None:
    ws = create("forbid3", "d", root=ws_root)
    h = _h(
        ws,
        forbidden_actions=["", "  "],
        summary="ok",
    )
    out = summarize_handover(h)
    assert "Forbidden:" not in out


def test_summarize_handover_appends_pr_number(ws_root: Path) -> None:
    ws = create("pr", "d", root=ws_root)
    h = _h(ws, summary="ok")
    h_with_pr = h.model_copy(update={"pr_number": 42})
    out = summarize_handover(h_with_pr)
    assert "PR: #42" in out


def test_summarize_handover_omits_pr_number_when_none(ws_root: Path) -> None:
    ws = create("nopr", "d", root=ws_root)
    out = summarize_handover(_h(ws, summary="ok"))
    assert "PR:" not in out


@pytest.mark.parametrize("layout", ["new", "legacy"])
def test_load_cross_team_prior_outputs_layout(ws_root: Path, layout: str) -> None:
    task_id = "layout-ws"
    if layout == "new":
        ws = create("Layout Ws", "d", root=ws_root)
    else:
        ws_path = ws_root / task_id
        ws_path.mkdir(parents=True)
        (ws_path / "handovers").mkdir()
        now = datetime.now(UTC)
        (ws_path / "meta.json").write_text(
            json.dumps({
                "task_id": task_id,
                "title": "Layout Ws",
                "description": "d",
                "status": "active",
                "budget": "medium",
                "revisit_counts": {},
                "tasks": [{"id": task_id, "effort": "medium", "status": "ready"}],
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
            }),
            encoding="utf-8",
        )
        ws = read(task_id, root=ws_root)
    save_handover(
        ws,
        _h(ws, from_team="research", to_team_suggestions=["implementation_plan"], summary="ok"),
    )
    assert load_cross_team_prior_outputs(ws, "implementation_plan") != ""


def test_summarize_decisions_only_fn_skips_full_synthesis(ws_root: Path) -> None:
    ws = create("do-skip-synth", "d", root=ws_root)
    h = _h(
        ws,
        decisions=[
            Decision(
                id="d1",
                statement="use json",
                rationale="simple",
                confidence=0.9,
                reversibility="high",
            )
        ],
        summary="this full synthesis must be absent",
    )
    out = summarize_handover_decisions_only(h)
    assert "Decisions:" in out
    assert "use json" in out
    assert "Summary:" not in out
    assert "full synthesis must be absent" not in out


def test_summarize_decisions_only_fn_preserves_blocking_questions(ws_root: Path) -> None:
    ws = create("do-blocking-q", "d", root=ws_root)
    h = _h(
        ws,
        open_questions=[
            OpenQuestion(
                id="q1",
                question="Which db?",
                resolver_role="brainstorming",
                blocking=True,
                prior_attempts=[],
            ),
            OpenQuestion(
                id="q2",
                question="Non-blocking soft question",
                resolver_role="research",
                blocking=False,
                prior_attempts=[],
            ),
        ],
        summary="synthesis here",
    )
    out = summarize_handover_decisions_only(h)
    assert "Which db?" in out
    assert "Non-blocking soft question" not in out
    assert "Summary:" not in out


def test_summarize_decisions_only_fn_preserves_pr_number(ws_root: Path) -> None:
    ws = create("do-pr", "d", root=ws_root)
    h = _h(ws, summary="some synthesis")
    h_with_pr = h.model_copy(update={"pr_number": 99})
    out = summarize_handover_decisions_only(h_with_pr)
    assert "PR: #99" in out
    assert "Summary:" not in out


def test_summarize_decisions_only_fn_preserves_forbidden(ws_root: Path) -> None:
    ws = create("do-forbidden", "d", root=ws_root)
    h = _h(ws, forbidden_actions=["do not push"], summary="some synthesis")
    out = summarize_handover_decisions_only(h)
    assert "Forbidden:" in out
    assert "do not push" in out
    assert "Summary:" not in out


def test_load_cross_team_prior_outputs_decisions_only_skips_summary(
    ws_root: Path,
) -> None:
    ws = create("co-decisions-only", "d", root=ws_root)
    h = _h(
        ws,
        from_team="research",
        to_team_suggestions=["implementation_plan"],
        decisions=[
            Decision(
                id="d1",
                statement="pick option A",
                rationale="fast",
                confidence=0.9,
                reversibility="high",
            )
        ],
        summary="this synthesis must not appear in decisions_only mode",
    )
    save_handover(ws, h)
    out = load_cross_team_prior_outputs(ws, "implementation_plan", decisions_only=True)
    assert "Decisions:" in out
    assert "pick option A" in out
    assert "Summary:" not in out
    assert "must not appear" not in out


def test_load_cross_team_prior_outputs_decisions_only_false_includes_summary(
    ws_root: Path,
) -> None:
    ws = create("co-full-synth", "d", root=ws_root)
    h = _h(
        ws,
        from_team="research",
        to_team_suggestions=["implementation_plan"],
        summary="full synthesis content here",
    )
    save_handover(ws, h)
    out = load_cross_team_prior_outputs(ws, "implementation_plan", decisions_only=False)
    assert "Summary:" in out
    assert "full synthesis content here" in out


def test_summarize_decisions_only_fn_locks_decision_required_targets_invariant(
    ws_root: Path,
) -> None:
    ws = create("verif-inv", "d", root=ws_root)
    h = _h(
        ws,
        to_team_suggestions=["verification"],
        decisions=[
            Decision(
                id="d1",
                statement="use approach A",
                rationale="safe",
                confidence=0.9,
                reversibility="high",
            )
        ],
        summary="full synthesis text that must be absent",
    )
    out = summarize_handover_decisions_only(h)
    assert "Decisions:" in out
    assert "use approach A" in out
    assert "Summary:" not in out


def test_load_cross_team_prior_outputs_skips_corrupt_file(ws_root: Path) -> None:
    ws = create("skip-corrupt", "d", root=ws_root)
    save_handover(
        ws,
        _h(
            ws,
            handover_id=f"handover_{ws.task_id}_valid",
            from_team="research",
            to_team_suggestions=["implementation_plan"],
            summary="valid summary content",
        ),
    )
    corrupt_path = ws.path / "handovers" / f"handover_{ws.task_id}_002.yaml"
    corrupt_path.write_text("not: valid: yaml: [", encoding="utf-8")
    out = load_cross_team_prior_outputs(ws, "implementation_plan")
    assert "valid summary content" in out


def test_load_cross_team_prior_outputs_corrupt_is_logged(
    ws_root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    ws = create("corrupt-log", "d", root=ws_root)
    handovers_dir = ws.path / "handovers"
    handovers_dir.mkdir(parents=True, exist_ok=True)
    (handovers_dir / f"handover_{ws.task_id}_001.yaml").write_text(
        "!@#$%^&*()", encoding="utf-8"
    )
    with caplog.at_level("WARNING", logger="alan.compression"):
        result = load_cross_team_prior_outputs(ws, "implementation_plan")
    assert result == ""
    assert any("skipping unreadable" in r.message for r in caplog.records)
