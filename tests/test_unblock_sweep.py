from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alan import repo_status as repo_status_mod
from alan import unblock_ledger as ledger
from alan import unblock_recipes as recipes_mod
from alan import unblock_sweep as sweep_mod
from alan import workspace as workspace_mod
from alan.engine_config import DEFAULT_FORGE_TARGET, EngineConfig, RepoIdentity
from alan.events import PubSub
from alan.park import Blocker, ParkReason
from alan.queue import Queue
from alan.teams import _pr_feedback as pr_feedback_mod
from alan.teams import _pr_post as pr_post_mod
from alan.teams import _pr_ready as pr_ready_mod
from alan.workspace import Status
from alan.workspace_index import WorkspaceIndex, WorkspaceRow
from tests import _unblock_support as support
from tests._pr_branch_support import _WIDGET_FORGE


@pytest.fixture(autouse=True)
def _unblock_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(sweep_mod.DISABLE_ENV, raising=False)


@pytest.fixture
def env(tmp_path: Path) -> tuple[Queue, WorkspaceIndex, Path]:
    ws_root = tmp_path / "ws"
    queue = Queue(db_path=tmp_path / "queue.db")
    return queue, WorkspaceIndex(root=ws_root, queue=queue), ws_root


def _row(task_id: str, status: Status = Status.AWAITING_INPUT) -> WorkspaceRow:
    now = datetime.now(UTC)
    return WorkspaceRow(task_id=task_id, title="t", status=status, created_at=now, updated_at=now)


@pytest.mark.parametrize(
    ("value", "expected"),
    [("1", True), ("true", True), (" YES ", True), ("on", True), ("0", False), ("", False)],
)
def test_disabled_reads_truthy_values(
    monkeypatch: pytest.MonkeyPatch, value: str, expected: bool
) -> None:
    monkeypatch.setenv(sweep_mod.DISABLE_ENV, value)
    assert sweep_mod.disabled() is expected


def test_disabled_is_false_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(sweep_mod.DISABLE_ENV, raising=False)
    assert sweep_mod.disabled() is False


def test_sweep_is_inert_when_disabled(
    env: tuple[Queue, WorkspaceIndex, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    queue, index, ws_root = env
    monkeypatch.setenv(sweep_mod.DISABLE_ENV, "1")
    assert (
        sweep_mod.sweep(
            queue=queue,
            pubsub=PubSub(),
            workspace_index=index,
            workspaces_root=ws_root,
            now=0.0,
            lock_for=support.lock_for,
            enqueue_workflow=support.never_enqueue,
        )
        == 0
    )
    assert queue.get_meta(sweep_mod.UNBLOCK_WATCH_META_KEY) is None


def test_sweep_is_inert_while_the_queue_is_paused(env: tuple[Queue, WorkspaceIndex, Path]) -> None:
    queue, index, ws_root = env
    queue.pause_until(10_000.0)
    assert (
        sweep_mod.sweep(
            queue=queue,
            pubsub=PubSub(),
            workspace_index=index,
            workspaces_root=ws_root,
            now=0.0,
            lock_for=support.lock_for,
            enqueue_workflow=support.never_enqueue,
        )
        == 0
    )


def test_watch_round_trips_and_drops_invalid_entries(queue: Queue) -> None:
    entry = sweep_mod.WatchEntry(next_at=1.0, attempts=1, identity="i", terminal=True)
    sweep_mod._save_watch(queue, {"a": entry})  # noqa: SLF001
    raw = json.loads(queue.get_meta(sweep_mod.UNBLOCK_WATCH_META_KEY) or "{}")
    raw["b"] = {"next_at": "x"}
    queue.set_meta(sweep_mod.UNBLOCK_WATCH_META_KEY, json.dumps(raw))
    assert sweep_mod.load_watch(queue) == {"a": entry}


def test_stuck_rows_keeps_only_stuck_statuses() -> None:
    rows = [
        _row("a", Status.ACTIVE),
        _row("b"),
        _row("c", Status.ESCALATED),
        _row("d", Status.COMPLETED),
    ]
    assert [row.task_id for row in sweep_mod.stuck_rows(rows)] == ["b", "c"]


def test_identity_combines_handover_and_updated_at(tmp_path: Path) -> None:
    ws = support.parked_ws(tmp_path)
    stamp = ws.meta.updated_at.isoformat()
    assert sweep_mod._identity(ws, None) == f"none@{stamp}"  # noqa: SLF001
    assert sweep_mod._identity(ws, support.handover(ws)) == (  # noqa: SLF001
        f"handover_{ws.task_id}_seed@{stamp}"
    )


def test_exempt_reason_internal_team(tmp_path: Path) -> None:
    ws = support.parked_ws(tmp_path, team="triage")
    assert sweep_mod.exempt_reason(ws, None, support.ENGINE) == "internal team"


def test_exempt_reason_design_family_without_handovers(tmp_path: Path) -> None:
    ws = support.parked_ws(tmp_path, team="research")
    assert sweep_mod.exempt_reason(ws, None, support.ENGINE) == (
        "design-family workspace awaiting harvest"
    )


def test_exempt_reason_design_family_from_the_latest_handover(tmp_path: Path) -> None:
    ws = support.parked_ws(tmp_path)
    latest = support.handover(ws, from_team="brainstorming", pr_number=None)
    assert sweep_mod.exempt_reason(ws, latest, support.ENGINE) == (
        "design-family workspace awaiting harvest"
    )


def test_exempt_reason_design_family_with_suggestions_is_not_exempt(tmp_path: Path) -> None:
    ws = support.parked_ws(tmp_path, team="architecture")
    latest = support.handover(ws, from_team="architecture", suggestions=["implementation_plan"])
    assert sweep_mod.exempt_reason(ws, latest, support.ENGINE) is None


def test_exempt_reason_delivered(tmp_path: Path) -> None:
    ws = support.parked_ws(tmp_path)
    latest = support.handover(ws, from_team="deploy_verify")
    assert sweep_mod.exempt_reason(ws, latest, support.ENGINE) == "delivered"


def test_exempt_reason_deploy_verify_with_a_question_is_not_delivered(tmp_path: Path) -> None:
    ws = support.parked_ws(tmp_path)
    latest = support.handover(ws, from_team="deploy_verify", questions=[support.question()])
    assert sweep_mod.exempt_reason(ws, latest, support.ENGINE) is None


def test_exempt_reason_hidden_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sweep_mod, "live_hidden", lambda _key: True)
    ws = support.parked_ws(tmp_path)
    assert sweep_mod.exempt_reason(ws, support.handover(ws), support.ENGINE) == "engine hidden"


def test_exempt_reason_engine_flag_off(tmp_path: Path) -> None:
    cfg = EngineConfig(repo=RepoIdentity(owner="a", repo="b"), unblock_enabled=False)
    ws = support.parked_ws(tmp_path)
    assert sweep_mod.exempt_reason(ws, support.handover(ws), cfg) == (
        "unblock disabled for this engine"
    )


def test_candidate_for_returns_none_for_a_missing_workspace(tmp_path: Path) -> None:
    assert sweep_mod.candidate_for(_row("ghost"), workspaces_root=tmp_path) is None


def test_candidate_for_returns_none_for_an_active_workspace(tmp_path: Path) -> None:
    ws = workspace_mod.create("active", "d", root=tmp_path)
    assert sweep_mod.candidate_for(_row(ws.task_id), workspaces_root=tmp_path) is None


def test_candidate_for_returns_none_for_an_exempt_workspace(tmp_path: Path) -> None:
    ws = support.parked_ws(tmp_path, team="triage")
    assert sweep_mod.candidate_for(_row(ws.task_id), workspaces_root=tmp_path) is None


def test_candidate_for_builds_the_candidate(tmp_path: Path) -> None:
    ws = support.parked_ws(tmp_path)
    latest = support.save(ws, support.handover(ws, questions=[support.question()]))
    cand = sweep_mod.candidate_for(_row(ws.task_id), workspaces_root=tmp_path)
    assert cand is not None
    assert cand.pr_number == 123
    assert cand.latest is not None and cand.latest.handover_id == latest.handover_id
    assert cand.blocking is cand.latest
    assert cand.question is not None and cand.question.id == "pr-merge-timeout"
    assert cand.blocker == Blocker(kind="reason", name="pr-merge-timeout", detail="timeout")
    assert cand.identity.startswith(latest.handover_id)
    assert cand.engine_cfg.unblock_enabled is True


def test_candidate_for_without_a_question_has_no_blocking_handover(tmp_path: Path) -> None:
    ws = support.parked_ws(tmp_path, park_reason=ParkReason.CRASH)
    cand = sweep_mod.candidate_for(_row(ws.task_id), workspaces_root=tmp_path)
    assert cand is not None
    assert cand.blocking is None
    assert cand.pr_number is None


@pytest.mark.parametrize(
    ("attempts", "expected"), [(0, 300.0), (1, 600.0), (2, 1200.0), (5, 1200.0)]
)
def test_next_interval_doubles_to_a_cap(attempts: int, expected: float) -> None:
    assert sweep_mod._next_interval(attempts) == expected  # noqa: SLF001


def test_has_running_job_sees_a_claimed_job(queue: Queue) -> None:
    queue.enqueue("t", "verification", workspace_id="t")
    assert sweep_mod._has_running_job(queue, "t") is False  # noqa: SLF001
    queue.claim()
    assert sweep_mod._has_running_job(queue, "t") is True  # noqa: SLF001


def _candidate(tmp_path: Path, *, with_question: bool, with_blocker: bool) -> sweep_mod.Candidate:
    ws = support.parked_ws(tmp_path, blockers=None if with_blocker else [])
    questions = [support.question()] if with_question else []
    latest = support.handover(ws, questions=questions, pr_number=123 if with_question else None)
    return sweep_mod.Candidate(
        row=_row(ws.task_id),
        ws=ws,
        latest=latest,
        blocking=latest if with_question else None,
        question=latest.open_questions[0] if with_question else None,
        blocker=ws.meta.blockers[0] if with_blocker else None,
        pr_number=latest.pr_number,
        forge_target=_WIDGET_FORGE,
        engine_cfg=support.ENGINE,
        identity="id",
    )


def test_fingerprint_keys_on_question_then_blocker_then_nothing(tmp_path: Path) -> None:
    recipe = recipes_mod.RECIPES[0]
    by_question = sweep_mod._fingerprint(
        recipe, _candidate(tmp_path, with_question=True, with_blocker=True)
    )  # noqa: SLF001
    by_blocker = sweep_mod._fingerprint(
        recipe, _candidate(tmp_path, with_question=False, with_blocker=True)
    )  # noqa: SLF001
    bare = sweep_mod._fingerprint(
        recipe, _candidate(tmp_path, with_question=False, with_blocker=False)
    )  # noqa: SLF001
    assert len({by_question, by_blocker, bare}) == 3


def test_case_body_renders_without_a_pr_or_blocker(tmp_path: Path) -> None:
    cand = _candidate(tmp_path, with_question=False, with_blocker=False)
    body = sweep_mod._case_body(cand, shape="s", lines=["a: b"])  # noqa: SLF001
    assert "no PR" in body
    assert "blocker -" in body
    assert body.endswith("- a: b")


def test_default_deps_wire_the_forge_helpers(
    env: tuple[Queue, WorkspaceIndex, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    queue, index, _root = env
    seen: list[tuple[str, object]] = []

    def _stream(pr: int, *, forge_target: object) -> str:
        seen.append(("stream", (pr, forge_target)))
        return "[]"

    def _feedback(pr: int, *, forge_target: object) -> pr_feedback_mod.FeedbackSnapshot:
        del forge_target
        seen.append(("feedback", pr))
        return support.snapshot()

    def _dismiss(pr: int, task_id: str, *, forge_target: object) -> bool:
        del forge_target
        seen.append(("dismiss", (pr, task_id)))
        return True

    monkeypatch.setattr(pr_post_mod, "fetch_review_stream", _stream)
    monkeypatch.setattr(pr_feedback_mod, "read_feedback", _feedback)
    monkeypatch.setattr(pr_post_mod, "dismiss_stale_verifier_reviews", _dismiss)
    monkeypatch.setattr(repo_status_mod, "get_repo_status", lambda *_a, **_k: None)
    deps = sweep_mod.default_deps(queue, index, PubSub())
    assert deps.review_stream(1, _WIDGET_FORGE) == "[]"
    assert deps.feedback(2, _WIDGET_FORGE).ok is True
    assert deps.dismiss_stale_reviews(3, "t", _WIDGET_FORGE) is True
    assert deps.main_ci(support.ENGINE) is None
    assert [kind for kind, _ in seen] == ["stream", "feedback", "dismiss"]
    assert seen[0][1] == (1, _WIDGET_FORGE)


def test_default_deps_feedback_reads_without_settling_stranger_threads(
    env: tuple[Queue, WorkspaceIndex, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    queue, index, _root = env
    called: list[str] = []

    def _read(pr: int, *, forge_target: object) -> pr_feedback_mod.FeedbackSnapshot:
        del pr, forge_target
        called.append("read_feedback")
        return support.snapshot()

    def _fetch(pr: int, *, forge_target: object) -> pr_feedback_mod.FeedbackSnapshot:
        del pr, forge_target
        called.append("fetch_feedback")
        return support.snapshot()

    monkeypatch.setattr(pr_feedback_mod, "read_feedback", _read)
    monkeypatch.setattr(pr_feedback_mod, "fetch_feedback", _fetch)
    deps = sweep_mod.default_deps(queue, index, PubSub())
    deps.feedback(2, _WIDGET_FORGE)
    assert called == ["read_feedback"]


def test_default_deps_main_ci_reads_the_repo_status(
    env: tuple[Queue, WorkspaceIndex, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    queue, index, _root = env

    class _Status:
        main_ci = support.main_ci()

    monkeypatch.setattr(repo_status_mod, "get_repo_status", lambda *_a, **_k: _Status())
    deps = sweep_mod.default_deps(queue, index, PubSub())
    assert deps.main_ci(support.ENGINE) == support.main_ci()


def test_default_read_readiness_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pr_ready_mod,
        "readiness_or_unavailable",
        lambda pr, task_id, *, forge_target: support.readiness(head_sha=f"{pr}-{task_id}"),
    )
    got = sweep_mod._default_read_readiness(5, "t", forge_target=_WIDGET_FORGE)  # noqa: SLF001
    assert isinstance(got, pr_ready_mod.PrReadiness)
    assert got.head_sha == "5-t"


def test_resolve_helpers_fall_back_to_defaults(env: tuple[Queue, WorkspaceIndex, Path]) -> None:
    queue, index, _root = env
    assert sweep_mod._resolve_reader(None) is sweep_mod._default_read_readiness  # noqa: SLF001
    custom = support.deps()
    assert sweep_mod._resolve_deps(custom, queue, index, PubSub()) is custom  # noqa: SLF001
    assert sweep_mod._resolve_deps(None, queue, index, PubSub()).main_ci is not custom.main_ci  # noqa: SLF001


def _intent(queue: Queue, *, engine_key: str | None = None) -> int:
    ledger.ensure_schema(queue)
    return ledger.record(
        queue,
        ledger.Entry(
            workspace_key="ws-1", scope_ref="pr:1", shape="stale_review", fingerprint="fp",
            kind=ledger.KIND_ISSUE_INTENT, engine_key=engine_key,
            detail={"slug": "o/r", "title": "T", "issue_body": "B", "comment": "C"},
        ),
    )


def _rows(queue: Queue) -> list[tuple[str, str | None, int | None, dict[str, object]]]:
    return [
        (r.kind, r.outcome, r.ref_id, r.detail)
        for r in (
            ledger._row_from_sql(row)
            for row in queue.run(  # noqa: SLF001
                "SELECT * FROM unblock_ledger ORDER BY id"
            )
        )
    ]


def test_drain_delivers_an_intent_and_records_the_url(queue: Queue) -> None:
    intent = _intent(queue)
    client = support.FakeIssueClient(number=7)
    assert sweep_mod.drain_issue_intents(queue, client) == 1
    assert client.ensured[0][:2] == ("T", "B")
    assert client.comments[0][:2] == (7, "C")
    assert _rows(queue)[-1] == (
        ledger.KIND_ISSUE_DELIVERED,
        "delivered",
        intent,
        {"issue_url": "https://github.com/o/r/issues/7"},
    )
    assert ledger.pending_issue_intents(queue, 5) == []


def test_drain_records_an_attempt_when_no_issue_could_be_ensured(queue: Queue) -> None:
    intent = _intent(queue)
    sweep_mod.drain_issue_intents(queue, support.FakeIssueClient(number=None))
    assert _rows(queue)[-1] == (ledger.KIND_ISSUE_ATTEMPT, "failed", intent, {"issue_url": None})
    assert len(ledger.pending_issue_intents(queue, 5)) == 1


def test_drain_records_an_attempt_when_the_comment_fails(queue: Queue) -> None:
    intent = _intent(queue)
    sweep_mod.drain_issue_intents(queue, support.FakeIssueClient(number=9, comment_ok=False))
    assert _rows(queue)[-1] == (
        ledger.KIND_ISSUE_ATTEMPT,
        "failed",
        intent,
        {"issue_url": "https://github.com/o/r/issues/9"},
    )


def test_drain_survives_a_raising_client(queue: Queue) -> None:
    _intent(queue)

    class _Boom:
        def ensure_shape_issue(self, *_a: object, **_k: object) -> int | None:
            raise RuntimeError("boom")

        def comment(self, *_a: object, **_k: object) -> bool:
            return True

    assert sweep_mod.drain_issue_intents(queue, _Boom()) == 0
    assert len(ledger.pending_issue_intents(queue, 5)) == 1


def test_drain_honours_the_limit(queue: Queue) -> None:
    for _ in range(3):
        _intent(queue)
    assert sweep_mod.drain_issue_intents(queue, support.FakeIssueClient(), limit=2) == 2


def test_drain_resolves_the_forge_target_from_the_engine_key(queue: Queue) -> None:
    _intent(queue, engine_key=None)
    client = support.FakeIssueClient()
    sweep_mod.drain_issue_intents(queue, client)
    assert client.ensured[0][2] == DEFAULT_FORGE_TARGET.repo.slug


def _sweep_context(queue: Queue, tmp_path: Path) -> sweep_mod.SweepContext:
    return sweep_mod.SweepContext(
        queue=queue, pubsub=PubSub(), workspaces_root=tmp_path, now=0.0,
        lock_for=support.lock_for, enqueue_workflow=support.never_enqueue,
        read_readiness=lambda *_a, **_k: support.readiness(), deps=support.deps(),
    )


def test_wait_logs_once_on_first_sight_but_not_on_a_repeated_tick(
    tmp_path: Path, queue: Queue, caplog: pytest.LogCaptureFixture
) -> None:
    ledger.ensure_schema(queue)
    sc = _sweep_context(queue, tmp_path)
    cand = _candidate(tmp_path, with_question=True, with_blocker=True)
    watch: dict[str, sweep_mod.WatchEntry] = {}
    with caplog.at_level("INFO", logger="alan.unblock"):
        sweep_mod._wait(  # noqa: SLF001
            sc, cand, watch, None, shape="stale_review", reasons=["checks pending"]
        )
        entry = watch[cand.ws.task_id]
        sweep_mod._wait(  # noqa: SLF001
            sc, cand, watch, entry, shape="stale_review", reasons=["checks pending"]
        )
    waits = [m for m in caplog.messages if "waiting" in m]
    assert len(waits) == 1
    assert cand.ws.task_id in waits[0]


def test_report_logs_once_but_not_on_a_terminal_reprobe_extension(
    tmp_path: Path, queue: Queue, caplog: pytest.LogCaptureFixture
) -> None:
    ledger.ensure_schema(queue)
    sc = _sweep_context(queue, tmp_path)
    cand = _candidate(tmp_path, with_question=False, with_blocker=False)
    watch: dict[str, sweep_mod.WatchEntry] = {}
    with caplog.at_level("INFO", logger="alan.unblock"):
        sweep_mod._report(  # noqa: SLF001
            sc, cand, watch, None, reason="no recipe recognises this park",
            authority_holder=sweep_mod.HOLDER_HUMAN,
        )
        entry = watch[cand.ws.task_id]
        sweep_mod._report(  # noqa: SLF001
            sc, cand, watch, entry, reason="no recipe recognises this park",
            authority_holder=sweep_mod.HOLDER_HUMAN,
        )
    reports = [m for m in caplog.messages if "reported needs_human" in m]
    assert len(reports) == 1
    assert cand.ws.task_id in reports[0]


def test_ledger_attempt_logs_the_action_and_outcome(
    tmp_path: Path, queue: Queue, caplog: pytest.LogCaptureFixture
) -> None:
    ledger.ensure_schema(queue)
    sc = _sweep_context(queue, tmp_path)
    cand = _candidate(tmp_path, with_question=True, with_blocker=True)
    recipe = recipes_mod.RECIPES[0]
    plan = recipes_mod.Plan(kind="dispatch", team="pr_merge", instructions="do it")
    with caplog.at_level("INFO", logger="alan.unblock"):
        sweep_mod._ledger_attempt(  # noqa: SLF001
            sc, cand, recipe, plan, "fp-1", "probe reason", outcome=ledger.OUTCOME_ACTED
        )
    acted = [m for m in caplog.messages if "acted on" in m]
    assert len(acted) == 1
    assert cand.ws.task_id in acted[0]
    assert ledger.OUTCOME_ACTED in acted[0]


def test_watch_round_trips_the_park_head(queue: Queue) -> None:
    entry = sweep_mod.WatchEntry(next_at=1.0, attempts=1, identity="i", park_head="abc")
    sweep_mod._save_watch(queue, {"a": entry})  # noqa: SLF001
    assert sweep_mod.load_watch(queue) == {"a": entry}


def test_candidate_for_derives_the_park_class_from_the_question_and_its_invocation(
    tmp_path: Path,
) -> None:
    ws = support.parked_ws(tmp_path)
    question = support.question(question_id="non_actionable_feedback_inv-seed")
    support.save(ws, support.handover(ws, questions=[question]))
    cand = sweep_mod.candidate_for(_row(ws.task_id), workspaces_root=tmp_path)
    assert cand is not None
    assert cand.park_class == "non_actionable_feedback"
    assert cand.head is None


def test_candidate_for_names_a_blocker_only_park_after_the_blocker(tmp_path: Path) -> None:
    blocker = Blocker(kind="reason", name="restart", detail="d")
    ws = support.parked_ws(tmp_path, park_reason=ParkReason.CRASH, blockers=[blocker])
    cand = sweep_mod.candidate_for(_row(ws.task_id), workspaces_root=tmp_path)
    assert cand is not None and cand.park_class == "restart"


def test_candidate_for_gives_a_park_with_neither_question_nor_blocker_an_empty_class(
    tmp_path: Path,
) -> None:
    ws = support.parked_ws(tmp_path, park_reason=ParkReason.CRASH, blockers=[])
    cand = sweep_mod.candidate_for(_row(ws.task_id), workspaces_root=tmp_path)
    assert cand is not None and cand.park_class == ""


def _prior(park_head: str | None) -> sweep_mod.WatchEntry:
    return sweep_mod.WatchEntry(next_at=1.0, attempts=3, identity="old", park_head=park_head)


@pytest.mark.parametrize(
    ("prior", "live", "expected"),
    [
        (None, "live", "live"),
        (None, None, None),
        (_prior("old"), "live", "old"),
        (_prior(None), "live", "live"),
        (_prior("old"), None, "old"),
        (_prior(None), None, None),
    ],
)
def test_next_entry_records_the_head_at_first_sight_and_keeps_it_after(
    tmp_path: Path, prior: sweep_mod.WatchEntry | None, live: str | None, expected: str | None
) -> None:
    cand = replace(_candidate(tmp_path, with_question=True, with_blocker=True), head=live)
    entry = sweep_mod._next_entry(cand, prior, next_at=9.0, attempts=2)  # noqa: SLF001
    assert entry == sweep_mod.WatchEntry(
        next_at=9.0, attempts=2, identity="id", park_head=expected
    )


def test_next_entry_carries_the_flags_it_is_given(tmp_path: Path) -> None:
    cand = _candidate(tmp_path, with_question=True, with_blocker=True)
    entry = sweep_mod._next_entry(  # noqa: SLF001
        cand, None, next_at=1.0, attempts=0, acted=True, retried=True, terminal=True
    )
    assert (entry.acted, entry.retried, entry.terminal) == (True, True, True)


def test_retry_after_observe_keeps_the_park_head_and_the_rest_of_the_entry() -> None:
    acted = sweep_mod.WatchEntry(
        next_at=4.0, attempts=2, identity="i", acted=True, terminal=True, park_head="abc"
    )
    retried = sweep_mod._retry_after_observe(acted)  # noqa: SLF001
    assert retried == replace(acted, acted=False, retried=True)


def test_retry_after_observe_passes_through_an_entry_that_did_not_act() -> None:
    idle = sweep_mod.WatchEntry(next_at=4.0, attempts=2, identity="i", park_head="abc")
    assert sweep_mod._retry_after_observe(idle) is idle  # noqa: SLF001
    assert sweep_mod._retry_after_observe(None) is None  # noqa: SLF001


def _seeded_row(
    tmp_path: Path, queue: Queue, *, with_pr: bool, entry: sweep_mod.WatchEntry | None
) -> tuple[sweep_mod.SweepContext, WorkspaceRow, dict[str, sweep_mod.WatchEntry]]:
    ledger.ensure_schema(queue)
    ws = support.parked_ws(tmp_path)
    pr_number = 123 if with_pr else None
    latest = support.handover(ws, questions=[support.question()], pr_number=pr_number)
    support.save(ws, latest)
    row = _row(ws.task_id)
    cand = sweep_mod.candidate_for(row, workspaces_root=tmp_path)
    assert cand is not None
    seeded = replace(entry, identity=cand.identity) if entry is not None else None
    watch = {ws.task_id: seeded} if seeded is not None else {}
    return _sweep_context(queue, tmp_path), row, watch


_NOOP_TWICE = sweep_mod.WatchEntry(next_at=0.0, attempts=1, identity="", acted=True, retried=True)


@pytest.mark.parametrize(("with_pr", "expected"), [(True, False), (False, None)])
def test_a_noop_twice_report_returns_false_only_when_a_head_was_read(
    tmp_path: Path, queue: Queue, with_pr: bool, expected: bool | None
) -> None:
    sc, row, watch = _seeded_row(tmp_path, queue, with_pr=with_pr, entry=_NOOP_TWICE)
    assert sweep_mod._process_row(sc, row, watch) is expected  # noqa: SLF001
    assert [r.kind for r in _report_kinds(queue)] == [ledger.KIND_REPORT, ledger.KIND_ISSUE_INTENT]


@pytest.mark.parametrize(("with_pr", "expected"), [(True, False), (False, None)])
def test_a_ceiling_report_returns_false_only_when_a_head_was_read(
    tmp_path: Path, queue: Queue, with_pr: bool, expected: bool | None
) -> None:
    sc, row, watch = _seeded_row(tmp_path, queue, with_pr=with_pr, entry=None)
    for _ in range(ledger.ATTEMPT_CEILING_PER_WORKSPACE):
        ledger.record(
            queue,
            ledger.Entry(
                workspace_key=row.task_id, scope_ref="ws:x", shape="s", fingerprint="f",
                kind=ledger.KIND_ATTEMPT,
            ),
        )
    assert sweep_mod._process_row(sc, row, watch) is expected  # noqa: SLF001
    reported = [r for r in _report_kinds(queue) if r.kind == ledger.KIND_REPORT]
    assert [r.detail["reason"] for r in reported] == ["per-workspace attempt ceiling reached"]


@pytest.mark.parametrize(("with_pr", "expected"), [(True, False), (False, None)])
def test_an_exhausted_park_that_does_not_rearm_spends_budget_only_when_a_head_was_read(
    tmp_path: Path, queue: Queue, with_pr: bool, expected: bool | None
) -> None:
    exhausted = sweep_mod.WatchEntry(
        next_at=0.0, attempts=sweep_mod.REPROBE_CAP, identity="", terminal=True, park_head="abc"
    )
    sc, row, watch = _seeded_row(tmp_path, queue, with_pr=with_pr, entry=exhausted)
    assert sweep_mod._process_row(sc, row, watch) is expected  # noqa: SLF001
    assert watch[row.task_id].next_at == sc.now + sweep_mod.REPROBE_INTERVAL_S
    assert _report_kinds(queue) == []


def _report_kinds(queue: Queue) -> list[ledger.LedgerRow]:
    return [
        ledger._row_from_sql(row)  # noqa: SLF001
        for row in queue.run(
            "SELECT * FROM unblock_ledger WHERE kind != ? ORDER BY id", (ledger.KIND_ATTEMPT,)
        )
    ]
