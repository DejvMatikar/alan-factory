from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import httpx
import pytest
from fastapi.testclient import TestClient

from alan import control_plane, server, unblock_recipes
from alan import merge_gate as merge_gate_mod
from alan import park as park_mod
from alan.engine_config import DEFAULT_FORGE_TARGET
from alan.events import PubSub
from alan.handover import Handover, OpenQuestion, save_handover
from alan.merge_recovery import MergeBlockClass
from alan.park import ParkReason
from alan.queue import Queue
from alan.routes import _answer_waiver
from alan.teams import _pr_ready
from alan.teams import _verification_review_post as review_post_mod
from alan.teams._clause_stall import ClauseStall, StalledClause
from alan.teams._pr_waiver_dismissal import WaiverDismissal, WaiverOutcome
from alan.teams._verdict import CriticalCountReport
from alan.teams._verification_artifacts import (
    _write_review_artifact,  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    reviewer_count_var,
)
from alan.workspace import Status, Workspace, create, read
from tests import _unblock_support as support
from tests import review_forge_fake as forge_fake

_REWRITE_OPTION = "Rewrite or drop this clause, then re-dispatch verification."
_WAIVE_OPTION = "Waive this clause and proceed to merge."
_ZERO_CRITICAL_ARTIFACT = "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 0\n"
_NONZERO_CRITICAL_ARTIFACT = "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 2\n"
_PR_NUMBER = 701

_WITHHOLDING_CLASSES = [
    MergeBlockClass.STALE_REVIEW,
    MergeBlockClass.STILL_COMPUTING,
    MergeBlockClass.BLOCKED_PENDING,
    MergeBlockClass.GENUINE,
    MergeBlockClass.DIRTY,
    MergeBlockClass.SONAR_HOTSPOT,
]


@pytest.fixture
def client(ws_root: Path, queue: Queue, pubsub: PubSub) -> Iterator[TestClient]:
    server.app.dependency_overrides[server._queue_dep] = lambda: queue
    server.app.dependency_overrides[server._pubsub_dep] = lambda: pubsub
    server.app.dependency_overrides[server._workspaces_root_dep] = lambda: ws_root
    try:
        with TestClient(server.app, follow_redirects=True) as c:
            yield c
    finally:
        server.app.dependency_overrides.clear()


@pytest.fixture
def logs_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "logs"
    monkeypatch.setenv("ALAN_LOGS_ROOT", str(root))
    return root


def _clause_stall_question() -> OpenQuestion:
    return OpenQuestion(
        id="clause-stall-abc",
        question="waive?",
        proposed_answer_space=[_REWRITE_OPTION, _WAIVE_OPTION],
        question_class="verification_escalation",
        resolver_role="human",
        blocking=True,
        recommended=_REWRITE_OPTION,
        option_resolvers={_REWRITE_OPTION: "verification", _WAIVE_OPTION: "pr_merge"},
    )


def _merge_block_question() -> OpenQuestion:
    return OpenQuestion(
        id="mb-1",
        question="unblock?",
        proposed_answer_space=["proceed", "wait"],
        question_class="merge_block_escalation",
        resolver_role="human",
        blocking=True,
        option_resolvers={"proceed": "pr_merge"},
    )


def _escalation(ws: Workspace, question: OpenQuestion) -> Handover:
    return Handover(
        handover_id=f"handover_{ws.task_id}_esc",
        handover_type="escalation",
        task_id=ws.task_id,
        from_team="verification",
        from_invocation_id="inv_verify",
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="escalated",
        open_questions=[question],
        pr_number=_PR_NUMBER,
    )


def _post_answer(
    client: TestClient, ws: Workspace, question_id: str, chosen_option: str,
) -> httpx.Response:
    return cast(
        httpx.Response,
        client.post(
            f"/api/workspaces/{ws.task_id}/tasks/{ws.task_id}/answer",
            json={"question_id": question_id, "chosen_option": chosen_option},
            headers={
                control_plane.CONTROL_TOKEN_HEADER: os.environ[control_plane.CONTROL_TOKEN_ENV],
            },
        ),
    )


def _attempt(
    block_class: MergeBlockClass | None,
    *,
    review_decision: str = "CHANGES_REQUESTED",
    recovered: bool = False,
) -> merge_gate_mod.MergeGateAttempt:
    readiness = _pr_ready.PrReadiness(
        ok=recovered,
        merge_state_status="CLEAN",
        review_decision=review_decision,
        failed_reasons=("review decision CHANGES_REQUESTED",),
    )
    if block_class is None:
        return merge_gate_mod.MergeGateAttemptFailed(readiness)
    if recovered:
        return merge_gate_mod.MergeGateRecovered(readiness, block_class, None)
    return merge_gate_mod.MergeGateBlocked(
        readiness, block_class, None, DEFAULT_FORGE_TARGET,
    )


def _stub_attempt(
    monkeypatch: pytest.MonkeyPatch, attempt: merge_gate_mod.MergeGateAttempt,
) -> None:
    monkeypatch.setattr(
        merge_gate_mod, "attempt_recovery",
        lambda _ws, _pr, *, recovered_outcome: attempt,  # noqa: ARG005
    )


def _stub_dismissal(
    monkeypatch: pytest.MonkeyPatch,
    outcome: WaiverOutcome,
    *,
    other_author_blocking: bool = False,
) -> None:
    monkeypatch.setattr(
        _answer_waiver, "dismiss_for_waiver",
        lambda *_a, **_kw: WaiverDismissal(
            outcome, (7,) if outcome == "dismissed" else (), other_author_blocking,
        ),
    )


def _waiver_workspace(ws_root: Path, *, count_record: bool) -> Workspace:
    ws = create("Waiver Repark", "d", root=ws_root)
    if count_record:
        _write_review_artifact(
            ws, "inv_verify", _ZERO_CRITICAL_ARTIFACT,
            counts=CriticalCountReport(reviewer=0, judge_reported=True),
        )
    save_handover(ws, _escalation(ws, _clause_stall_question()))
    return ws


def _withheld_blockers(ws_root: Path, ws: Workspace) -> list[park_mod.Blocker]:
    reloaded = read(ws.task_id, root=ws_root)
    return [b for b in reloaded.meta.blockers if b.name == park_mod.WAIVER_WITHHELD_BLOCKER]


def _merge_gate_outcomes(logs_root: Path) -> list[str]:
    log = logs_root / "merge_gate_audit.jsonl"
    return [json.loads(line)["outcome"] for line in log.read_text().splitlines() if line.strip()]


def _assert_dispatched_to_pr_merge(res: httpx.Response, queue: Queue) -> None:
    assert res.status_code == 200
    body = res.json()
    assert body["dispatch"] is not None
    assert body["dispatch"]["team"] == "pr_merge"
    assert queue.list_jobs()


@pytest.mark.parametrize("block_class", _WITHHOLDING_CLASSES)
def test_withheld_floor_with_standing_review_parks_under_merge_block(
    client: TestClient, ws_root: Path, queue: Queue, logs_root: Path,
    monkeypatch: pytest.MonkeyPatch, block_class: MergeBlockClass,
) -> None:
    ws = _waiver_workspace(ws_root, count_record=False)
    _stub_attempt(monkeypatch, _attempt(block_class))

    res = _post_answer(client, ws, "clause-stall-abc", _WAIVE_OPTION)

    assert res.status_code == 200
    assert res.json()["dispatch"] is None
    assert queue.list_jobs() == []
    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.status is Status.AWAITING_INPUT
    assert reloaded.meta.park_reason is ParkReason.MERGE_BLOCK
    assert reloaded.meta.merge_block_class == block_class.value
    (blocker,) = _withheld_blockers(ws_root, ws)
    assert blocker.kind == "reason"
    assert f"PR #{_PR_NUMBER}" in blocker.detail
    assert "withheld_floor" in blocker.detail
    assert "count_not_reported" in blocker.detail
    assert blocker.instruction is not None
    assert "pr_merge" in blocker.instruction
    assert _merge_gate_outcomes(logs_root) == ["re_park"]


def test_failed_dismissal_with_standing_review_parks_naming_the_failure(
    client: TestClient, ws_root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = _waiver_workspace(ws_root, count_record=True)
    _stub_attempt(monkeypatch, _attempt(MergeBlockClass.STILL_COMPUTING))
    _stub_dismissal(monkeypatch, "failed")

    res = _post_answer(client, ws, "clause-stall-abc", _WAIVE_OPTION)

    assert res.status_code == 200
    assert res.json()["dispatch"] is None
    assert queue.list_jobs() == []
    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.status is Status.AWAITING_INPUT
    assert reloaded.meta.park_reason is ParkReason.MERGE_BLOCK
    (blocker,) = _withheld_blockers(ws_root, ws)
    assert "failed" in blocker.detail
    assert "dismissal call failed" in blocker.detail


@pytest.mark.parametrize("review_decision", ["APPROVED", ""])
def test_withheld_floor_with_no_standing_review_routes_as_before(
    client: TestClient, ws_root: Path, queue: Queue,
    monkeypatch: pytest.MonkeyPatch, review_decision: str,
) -> None:
    ws = _waiver_workspace(ws_root, count_record=False)
    _stub_attempt(
        monkeypatch, _attempt(MergeBlockClass.STILL_COMPUTING, review_decision=review_decision),
    )

    res = _post_answer(client, ws, "clause-stall-abc", _WAIVE_OPTION)

    _assert_dispatched_to_pr_merge(res, queue)
    assert _withheld_blockers(ws_root, ws) == []
    assert read(ws.task_id, root=ws_root).meta.status is not Status.AWAITING_INPUT


def test_dismissed_waiver_dispatches_after_stale_review_block(
    client: TestClient, ws_root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = _waiver_workspace(ws_root, count_record=True)
    _stub_attempt(monkeypatch, _attempt(MergeBlockClass.STALE_REVIEW))
    _stub_dismissal(monkeypatch, "dismissed")

    res = _post_answer(client, ws, "clause-stall-abc", _WAIVE_OPTION)

    _assert_dispatched_to_pr_merge(res, queue)
    assert _withheld_blockers(ws_root, ws) == []


def test_dismissed_waiver_with_other_author_blocking_reparks_without_waiver_blocker(
    client: TestClient, ws_root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = _waiver_workspace(ws_root, count_record=True)
    _stub_attempt(monkeypatch, _attempt(MergeBlockClass.STALE_REVIEW))
    _stub_dismissal(monkeypatch, "dismissed", other_author_blocking=True)

    res = _post_answer(client, ws, "clause-stall-abc", _WAIVE_OPTION)

    assert res.status_code == 200
    assert res.json()["dispatch"] is None
    assert queue.list_jobs() == []
    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.status is Status.AWAITING_INPUT
    assert reloaded.meta.merge_block_class == MergeBlockClass.STALE_REVIEW.value
    assert _withheld_blockers(ws_root, ws) == []


def test_dismissed_waiver_does_not_override_a_block_it_did_not_clear(
    client: TestClient, ws_root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = _waiver_workspace(ws_root, count_record=True)
    _stub_attempt(monkeypatch, _attempt(MergeBlockClass.SONAR_HOTSPOT))
    _stub_dismissal(monkeypatch, "dismissed")

    res = _post_answer(client, ws, "clause-stall-abc", _WAIVE_OPTION)

    assert res.status_code == 200
    assert res.json()["dispatch"] is None
    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.status is Status.AWAITING_INPUT
    assert reloaded.meta.merge_block_class == MergeBlockClass.SONAR_HOTSPOT.value
    assert _withheld_blockers(ws_root, ws) == []


def test_none_found_waiver_reparks_stale_review_without_waiver_blocker(
    client: TestClient, ws_root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = _waiver_workspace(ws_root, count_record=True)
    _stub_attempt(monkeypatch, _attempt(MergeBlockClass.STALE_REVIEW))
    _stub_dismissal(monkeypatch, "none_found")

    res = _post_answer(client, ws, "clause-stall-abc", _WAIVE_OPTION)

    assert res.status_code == 200
    assert res.json()["dispatch"] is None
    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.status is Status.AWAITING_INPUT
    assert _withheld_blockers(ws_root, ws) == []


def test_none_found_waiver_dispatches_a_poll_lane_class(
    client: TestClient, ws_root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = _waiver_workspace(ws_root, count_record=True)
    _stub_attempt(monkeypatch, _attempt(MergeBlockClass.STILL_COMPUTING))
    _stub_dismissal(monkeypatch, "none_found")

    res = _post_answer(client, ws, "clause-stall-abc", _WAIVE_OPTION)

    _assert_dispatched_to_pr_merge(res, queue)


def test_non_waiver_pr_merge_answer_dispatches_a_poll_lane_class(
    client: TestClient, ws_root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = create("Waiver Repark Non Waiver", "d", root=ws_root)
    save_handover(ws, _escalation(ws, _merge_block_question()))
    _stub_attempt(monkeypatch, _attempt(MergeBlockClass.STILL_COMPUTING))

    res = _post_answer(client, ws, "mb-1", "proceed")

    _assert_dispatched_to_pr_merge(res, queue)
    assert _withheld_blockers(ws_root, ws) == []


def test_recovered_block_dispatches_despite_withheld_waiver(
    client: TestClient, ws_root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = _waiver_workspace(ws_root, count_record=False)
    _stub_attempt(
        monkeypatch, _attempt(MergeBlockClass.STALE_REVIEW, recovered=True),
    )

    res = _post_answer(client, ws, "clause-stall-abc", _WAIVE_OPTION)

    _assert_dispatched_to_pr_merge(res, queue)
    assert _withheld_blockers(ws_root, ws) == []


def test_withheld_waiver_parks_when_the_recovery_attempt_failed_without_a_block_class(
    client: TestClient, ws_root: Path, queue: Queue, logs_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = _waiver_workspace(ws_root, count_record=False)
    _stub_attempt(monkeypatch, _attempt(None))

    res = _post_answer(client, ws, "clause-stall-abc", _WAIVE_OPTION)

    assert res.status_code == 200
    assert res.json()["dispatch"] is None
    assert queue.list_jobs() == []
    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.status is Status.AWAITING_INPUT
    assert reloaded.meta.park_reason is ParkReason.MERGE_BLOCK
    assert reloaded.meta.merge_block_class is None
    (blocker,) = _withheld_blockers(ws_root, ws)
    assert f"PR #{_PR_NUMBER}" in blocker.detail
    assert "count_not_reported" in blocker.detail
    assert _merge_gate_outcomes(logs_root) == ["re_park"]


def test_failed_dismissal_parks_when_the_recovery_attempt_failed_without_a_block_class(
    client: TestClient, ws_root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = _waiver_workspace(ws_root, count_record=True)
    _stub_attempt(monkeypatch, _attempt(None))
    _stub_dismissal(monkeypatch, "failed")

    res = _post_answer(client, ws, "clause-stall-abc", _WAIVE_OPTION)

    assert res.json()["dispatch"] is None
    assert queue.list_jobs() == []
    (blocker,) = _withheld_blockers(ws_root, ws)
    assert "dismissal call failed" in blocker.detail


@pytest.mark.parametrize("review_decision", ["APPROVED", ""])
def test_withheld_waiver_with_no_standing_review_dispatches_after_a_classless_attempt_failure(
    client: TestClient, ws_root: Path, queue: Queue,
    monkeypatch: pytest.MonkeyPatch, review_decision: str,
) -> None:
    ws = _waiver_workspace(ws_root, count_record=False)
    _stub_attempt(monkeypatch, _attempt(None, review_decision=review_decision))

    res = _post_answer(client, ws, "clause-stall-abc", _WAIVE_OPTION)

    _assert_dispatched_to_pr_merge(res, queue)
    assert _withheld_blockers(ws_root, ws) == []


@pytest.mark.parametrize("outcome", ["dismissed", "none_found"])
def test_cleared_or_empty_waiver_dispatches_after_a_classless_attempt_failure(
    client: TestClient, ws_root: Path, queue: Queue,
    monkeypatch: pytest.MonkeyPatch, outcome: WaiverOutcome,
) -> None:
    ws = _waiver_workspace(ws_root, count_record=True)
    _stub_attempt(monkeypatch, _attempt(None))
    _stub_dismissal(monkeypatch, outcome)

    res = _post_answer(client, ws, "clause-stall-abc", _WAIVE_OPTION)

    _assert_dispatched_to_pr_merge(res, queue)
    assert _withheld_blockers(ws_root, ws) == []


def test_withheld_waiver_reparks_without_a_readiness_read_beyond_the_merge_gate_attempt(
    client: TestClient, ws_root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = _waiver_workspace(ws_root, count_record=False)
    readiness_reads: list[int] = []

    class _CountingFake(forge_fake.ReviewForgeFake):
        def check_pr_merge_readiness(
            self, *args: object, **kwargs: object,
        ) -> _pr_ready.PrReadiness:
            readiness_reads.append(1)
            return super().check_pr_merge_readiness(*args, **kwargs)

    fake = _CountingFake(
        reviews=[
            forge_fake.FakeReview(
                id=9101, login="alan-verifier", state="CHANGES_REQUESTED", commit_id="head-sha",
            ),
        ],
        readiness=_pr_ready.PrReadiness(
            ok=False, merge_state_status="CLEAN", review_decision="CHANGES_REQUESTED",
            failed_reasons=("review decision CHANGES_REQUESTED",), check_count=1,
        ),
    )
    forge_fake.install(fake, monkeypatch)
    reads_after_attempt: list[int] = []
    original_attempt = merge_gate_mod.attempt_recovery

    def spy_attempt(
        ws_arg: Workspace, pr_number: int, *, recovered_outcome: str,
    ) -> merge_gate_mod.MergeGateAttempt:
        result = original_attempt(ws_arg, pr_number, recovered_outcome=recovered_outcome)
        reads_after_attempt.append(len(readiness_reads))
        return result

    monkeypatch.setattr(merge_gate_mod, "attempt_recovery", spy_attempt)

    res = _post_answer(client, ws, "clause-stall-abc", _WAIVE_OPTION)

    assert res.status_code == 200
    assert res.json()["dispatch"] is None
    assert fake.attempted_dismiss_ids == []
    assert reads_after_attempt == [len(readiness_reads)]
    assert len(_withheld_blockers(ws_root, ws)) == 1


def test_withheld_blocker_name_is_absent_from_the_stale_review_recipe_set() -> None:
    assert (
        park_mod.WAIVER_WITHHELD_BLOCKER not in unblock_recipes._STALE_REVIEW_BLOCKERS  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    )


def _matching_recipes(tmp_path: Path, *, blockers: list[park_mod.Blocker]) -> set[str]:
    ws = support.parked_ws(
        tmp_path, park_reason=ParkReason.MERGE_BLOCK, blockers=blockers,
        merge_block_class=MergeBlockClass.STALE_REVIEW.value,
    )
    return {r.name for r in unblock_recipes.match_recipes(support.context(ws))}


def _review_blocker() -> park_mod.Blocker:
    return park_mod.Blocker(kind="review_decision", name="review", detail="CHANGES_REQUESTED")


def _withheld_blocker_row() -> park_mod.Blocker:
    return park_mod.Blocker(
        kind="reason", name=park_mod.WAIVER_WITHHELD_BLOCKER, detail="withheld",
    )


def test_stale_review_recipe_ignores_a_withheld_waiver_park_carrying_readiness_blockers(
    tmp_path: Path,
) -> None:
    matching = _matching_recipes(tmp_path, blockers=[_review_blocker(), _withheld_blocker_row()])

    assert "stale_review" not in matching


def test_stale_review_recipe_ignores_a_withheld_waiver_park_carrying_only_the_waiver_blocker(
    tmp_path: Path,
) -> None:
    matching = _matching_recipes(tmp_path, blockers=[_withheld_blocker_row()])

    assert "stale_review" not in matching


def test_stale_review_recipe_still_matches_the_same_park_without_the_waiver_blocker(
    tmp_path: Path,
) -> None:
    matching = _matching_recipes(tmp_path, blockers=[_review_blocker()])

    assert "stale_review" in matching


def test_withheld_blocker_name_never_collides_with_a_readiness_blocker_name() -> None:
    readiness_names = {
        b.name
        for b in park_mod.blockers_from_readiness(
            failing_checks=("ci",), pending_checks=("ci2",), failed_reasons=("r",),
            review_decision="CHANGES_REQUESTED", merge_state_status="DIRTY",
            quarantined_checks=("q",),
        )
    }
    assert park_mod.WAIVER_WITHHELD_BLOCKER not in readiness_names


def _seed_clause_stall_park(
    ws_task: Workspace, counts: CriticalCountReport, artifact: str,
) -> tuple[str, str]:
    stall = ClauseStall(
        clause_ids=("req-a",), repeated_ids=("req-a",),
        clauses=(StalledClause(req_id="req-a", text="clause", evidence="evidence"),),
        judge_rationale="no fleet role can fix this", prior_handover_id="handover_prior",
    )
    token = reviewer_count_var.set(counts)
    try:
        handover = review_post_mod._emit_clause_stall_escalation(  # noqa: SLF001
            ws_task, None, lambda _event: None, "inv_verify", artifact, stall=stall,
        )
    finally:
        reviewer_count_var.reset(token)
    save_handover(ws_task, handover.model_copy(update={"pr_number": _PR_NUMBER}))
    question = handover.open_questions[0]
    waive = next(o for o, r in question.option_resolvers.items() if r == "pr_merge")
    return question.id, waive


def _install_standing_review_forge(
    monkeypatch: pytest.MonkeyPatch,
) -> forge_fake.ReviewForgeFake:
    fake = forge_fake.ReviewForgeFake(
        reviews=[
            forge_fake.FakeReview(
                id=9201, login="alan-verifier", state="CHANGES_REQUESTED", commit_id="head-sha",
            ),
        ],
        readiness=_pr_ready.PrReadiness(
            ok=False, merge_state_status="CLEAN", review_decision="CHANGES_REQUESTED",
            failed_reasons=("review decision CHANGES_REQUESTED",), check_count=1,
        ),
    )
    forge_fake.install(fake, monkeypatch)
    return fake


def test_clause_stall_park_with_clearing_count_dismisses_and_dispatches_on_waive(
    client: TestClient, ws_root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = create("Waiver Repark Stall Clears", "d", root=ws_root)
    question_id, waive = _seed_clause_stall_park(
        ws, CriticalCountReport(reviewer=0, judge_reported=True),
        _ZERO_CRITICAL_ARTIFACT,
    )
    fake = _install_standing_review_forge(monkeypatch)

    res = _post_answer(client, ws, question_id, waive)

    _assert_dispatched_to_pr_merge(res, queue)
    assert fake.attempted_dismiss_ids == [9201]
    assert _withheld_blockers(ws_root, ws) == []


def test_clause_stall_park_with_non_clearing_count_reparks_with_the_nonzero_reason(
    client: TestClient, ws_root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = create("Waiver Repark Stall Withheld", "d", root=ws_root)
    question_id, waive = _seed_clause_stall_park(
        ws, CriticalCountReport(reviewer=2, judge_reported=True),
        _NONZERO_CRITICAL_ARTIFACT,
    )
    fake = _install_standing_review_forge(monkeypatch)

    res = _post_answer(client, ws, question_id, waive)

    assert res.status_code == 200
    assert res.json()["dispatch"] is None
    assert queue.list_jobs() == []
    assert fake.attempted_dismiss_ids == []
    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.status is Status.AWAITING_INPUT
    assert reloaded.meta.park_reason is ParkReason.MERGE_BLOCK
    (blocker,) = _withheld_blockers(ws_root, ws)
    assert "reviewer_nonzero" in blocker.detail
    assert "count_not_reported" not in blocker.detail

