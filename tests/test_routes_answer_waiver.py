from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import httpx
import pytest
from fastapi.testclient import TestClient

from alan import control_plane, server
from alan.events import PubSub
from alan.handover import ClauseOutcome, Handover, OpenQuestion, save_handover
from alan.merge_recovery import MergeBlockClass
from alan.park import ParkReason
from alan.queue import Queue
from alan.teams import _pr_ready
from alan.teams._verdict import CriticalCountReport
from alan.teams._verification_artifacts import (
    _write_review_artifact,  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
)
from alan.teams._verification_review_post import (
    _CAPABILITY_BOUNDARY_OPTION_VERIFICATION,  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    _CAPABILITY_BOUNDARY_OPTION_WAIVE,  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
)
from alan.workspace import Requirement, Status, Workspace, create, read
from tests import review_forge_fake as forge_fake

_REWRITE_OPTION = "Rewrite or drop this clause, then re-dispatch verification."
_WAIVE_OPTION = "Waive this clause and proceed to merge."


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


def _control_headers() -> dict[str, str]:
    return {control_plane.CONTROL_TOKEN_HEADER: os.environ[control_plane.CONTROL_TOKEN_ENV]}


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


def _capability_boundary_question() -> OpenQuestion:
    return OpenQuestion(
        id="capability-boundary-abc",
        question="waive?",
        proposed_answer_space=[
            _CAPABILITY_BOUNDARY_OPTION_VERIFICATION, _CAPABILITY_BOUNDARY_OPTION_WAIVE,
        ],
        question_class="verification_escalation",
        resolver_role="human",
        blocking=True,
        recommended=_CAPABILITY_BOUNDARY_OPTION_VERIFICATION,
        option_resolvers={
            _CAPABILITY_BOUNDARY_OPTION_VERIFICATION: "verification",
            _CAPABILITY_BOUNDARY_OPTION_WAIVE: "pr_merge",
        },
    )


def _escalation(
    ws: Workspace, *, pr_number: int, from_invocation_id: str = "inv_verify",
    question: OpenQuestion | None = None,
    clause_outcome: ClauseOutcome | None = None,
) -> Handover:
    return Handover(
        handover_id=f"handover_{ws.task_id}_esc",
        handover_type="escalation",
        task_id=ws.task_id,
        from_team="verification",
        from_invocation_id=from_invocation_id,
        iteration=1,
        timestamp_utc=datetime.now(UTC),
        full_synthesis="escalated",
        open_questions=[question or _clause_stall_question()],
        pr_number=pr_number,
        clause_outcome=clause_outcome,
    )


def _clause_outcome_for(ws: Workspace, *, req_id: str = "req-abc") -> ClauseOutcome:
    return ClauseOutcome(
        task_id=ws.task_id,
        actionable_ids=(req_id,),
        clause_texts={req_id: "clause text"},
        evidence={req_id: "evidence"},
        judge_rationale="no fleet role can fix this",
    )


def _answer_url(ws: Workspace) -> str:
    return f"/api/workspaces/{ws.task_id}/tasks/{ws.task_id}/answer"


def _post_answer(client: TestClient, ws: Workspace, body: dict[str, object]) -> httpx.Response:
    return cast(
        httpx.Response,
        client.post(_answer_url(ws), json=body, headers=_control_headers()),
    )


def _stale_review_readiness() -> _pr_ready.PrReadiness:
    return _pr_ready.PrReadiness(
        ok=False, merge_state_status="CLEAN", review_decision="CHANGES_REQUESTED",
        failed_reasons=("review decision CHANGES_REQUESTED",), check_count=1,
    )


def _genuine_red_check_readiness() -> _pr_ready.PrReadiness:
    return _pr_ready.PrReadiness(
        ok=False, merge_state_status="CLEAN", review_decision="CHANGES_REQUESTED",
        failing_checks=("ci/test",), failed_reasons=("failing checks: ci/test",),
    )


def test_waiver_dismisses_current_head_review_and_dispatches(
    client: TestClient, ws_root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = create("Waiver Answer Route", "d", root=ws_root)
    _write_review_artifact(
        ws, "inv_verify", "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 0\n",
        counts=CriticalCountReport(reviewer=0, judge_reported=True),
    )
    save_handover(ws, _escalation(ws, pr_number=555))
    fake = forge_fake.ReviewForgeFake(
        reviews=[
            forge_fake.FakeReview(
                id=9001, login="alan-verifier", state="CHANGES_REQUESTED", commit_id="head-sha",
            ),
        ],
        readiness=_stale_review_readiness(),
    )
    forge_fake.install(fake, monkeypatch)

    res = _post_answer(
        client, ws, {"question_id": "clause-stall-abc", "chosen_option": _WAIVE_OPTION},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["dispatch"] is not None
    assert body["dispatch"]["team"] == "pr_merge"
    assert queue.list_jobs()
    assert fake.attempted_dismiss_ids == [9001]
    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.status is not Status.AWAITING_INPUT


def test_waiver_withheld_by_floor_still_reparks(
    client: TestClient, ws_root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = create("Waiver Answer Route Floor", "d", root=ws_root)
    _write_review_artifact(
        ws, "inv_verify", "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 3\n",
        counts=CriticalCountReport(reviewer=3, judge_reported=True),
    )
    save_handover(ws, _escalation(ws, pr_number=556))
    fake = forge_fake.ReviewForgeFake(
        reviews=[
            forge_fake.FakeReview(
                id=9002, login="alan-verifier", state="CHANGES_REQUESTED", commit_id="head-sha",
            ),
        ],
        readiness=_stale_review_readiness(),
    )
    forge_fake.install(fake, monkeypatch)

    res = _post_answer(
        client, ws, {"question_id": "clause-stall-abc", "chosen_option": _WAIVE_OPTION},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["dispatch"] is None
    assert queue.list_jobs() == []
    assert fake.attempted_dismiss_ids == []
    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.status is Status.AWAITING_INPUT
    assert reloaded.meta.merge_block_class == MergeBlockClass.STALE_REVIEW.value
    assert reloaded.meta.park_reason is ParkReason.MERGE_BLOCK


def test_waiver_missing_artifact_still_reparks(
    client: TestClient, ws_root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = create("Waiver Answer Route No Artifact", "d", root=ws_root)
    save_handover(ws, _escalation(ws, pr_number=557))
    fake = forge_fake.ReviewForgeFake(
        reviews=[
            forge_fake.FakeReview(
                id=9003, login="alan-verifier", state="CHANGES_REQUESTED", commit_id="head-sha",
            ),
        ],
        readiness=_stale_review_readiness(),
    )
    forge_fake.install(fake, monkeypatch)

    res = _post_answer(
        client, ws, {"question_id": "clause-stall-abc", "chosen_option": _WAIVE_OPTION},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["dispatch"] is None
    assert fake.attempted_dismiss_ids == []
    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.status is Status.AWAITING_INPUT


def test_waiver_dismissal_failure_still_reparks(
    client: TestClient, ws_root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = create("Waiver Answer Route Fail", "d", root=ws_root)
    _write_review_artifact(
        ws, "inv_verify", "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 0\n",
        counts=CriticalCountReport(reviewer=0, judge_reported=True),
    )
    save_handover(ws, _escalation(ws, pr_number=558))
    fake = forge_fake.ReviewForgeFake(
        reviews=[
            forge_fake.FakeReview(
                id=9004, login="alan-verifier", state="CHANGES_REQUESTED", commit_id="head-sha",
            ),
        ],
        readiness=_stale_review_readiness(),
        dismiss_rc=1,
    )
    forge_fake.install(fake, monkeypatch)

    res = _post_answer(
        client, ws, {"question_id": "clause-stall-abc", "chosen_option": _WAIVE_OPTION},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["dispatch"] is None
    assert fake.attempted_dismiss_ids == [9004]
    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.status is Status.AWAITING_INPUT
    assert reloaded.meta.merge_block_class == MergeBlockClass.STALE_REVIEW.value


def test_waiver_other_author_blocking_still_reparks(
    client: TestClient, ws_root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = create("Waiver Answer Route Other Author", "d", root=ws_root)
    _write_review_artifact(
        ws, "inv_verify", "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 0\n",
        counts=CriticalCountReport(reviewer=0, judge_reported=True),
    )
    save_handover(ws, _escalation(ws, pr_number=559))
    fake = forge_fake.ReviewForgeFake(
        reviews=[
            forge_fake.FakeReview(
                id=9005, login="alan-verifier", state="CHANGES_REQUESTED", commit_id="head-sha",
            ),
            forge_fake.FakeReview(id=9006, login="a-human", state="CHANGES_REQUESTED"),
        ],
        readiness=_stale_review_readiness(),
    )
    forge_fake.install(fake, monkeypatch)

    res = _post_answer(
        client, ws, {"question_id": "clause-stall-abc", "chosen_option": _WAIVE_OPTION},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["dispatch"] is None
    assert fake.attempted_dismiss_ids == [9005]
    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.status is Status.AWAITING_INPUT


def test_waiver_no_count_record_still_reparks(
    client: TestClient, ws_root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = create("Waiver Answer Route No Record", "d", root=ws_root)
    _write_review_artifact(
        ws, "inv_verify", "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 0\n"
    )
    save_handover(ws, _escalation(ws, pr_number=563))
    fake = forge_fake.ReviewForgeFake(
        reviews=[
            forge_fake.FakeReview(
                id=9010, login="alan-verifier", state="CHANGES_REQUESTED", commit_id="head-sha",
            ),
        ],
        readiness=_stale_review_readiness(),
    )
    forge_fake.install(fake, monkeypatch)

    res = _post_answer(
        client, ws, {"question_id": "clause-stall-abc", "chosen_option": _WAIVE_OPTION},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["dispatch"] is None
    assert fake.attempted_dismiss_ids == []
    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.status is Status.AWAITING_INPUT


def test_waiver_reviewer_zero_judge_silent_clears(
    client: TestClient, ws_root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = create("Waiver Answer Route Judge Silent", "d", root=ws_root)
    _write_review_artifact(
        ws, "inv_verify", "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 0\n",
        counts=CriticalCountReport(reviewer=0, judge_reported=False),
    )
    save_handover(ws, _escalation(ws, pr_number=564))
    fake = forge_fake.ReviewForgeFake(
        reviews=[
            forge_fake.FakeReview(
                id=9011, login="alan-verifier", state="CHANGES_REQUESTED", commit_id="head-sha",
            ),
        ],
        readiness=_stale_review_readiness(),
    )
    forge_fake.install(fake, monkeypatch)

    res = _post_answer(
        client, ws, {"question_id": "clause-stall-abc", "chosen_option": _WAIVE_OPTION},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["dispatch"] is not None
    assert body["dispatch"]["team"] == "pr_merge"
    assert fake.attempted_dismiss_ids == [9011]


def test_waiver_neither_role_reported_still_reparks(
    client: TestClient, ws_root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = create("Waiver Answer Route Neither Reported", "d", root=ws_root)
    _write_review_artifact(
        ws, "inv_verify", "VERDICT: REQUEST_CHANGES\n",
        counts=CriticalCountReport(reviewer=None, judge_reported=False),
    )
    save_handover(ws, _escalation(ws, pr_number=565))
    fake = forge_fake.ReviewForgeFake(
        reviews=[
            forge_fake.FakeReview(
                id=9012, login="alan-verifier", state="CHANGES_REQUESTED", commit_id="head-sha",
            ),
        ],
        readiness=_stale_review_readiness(),
    )
    forge_fake.install(fake, monkeypatch)

    res = _post_answer(
        client, ws, {"question_id": "clause-stall-abc", "chosen_option": _WAIVE_OPTION},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["dispatch"] is None
    assert fake.attempted_dismiss_ids == []
    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.status is Status.AWAITING_INPUT


def test_waiver_apply_forwards_same_floor_reason_to_dismiss_and_record(
    client: TestClient, ws_root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = create("Waiver Answer Route Forwarding", "d", root=ws_root)
    _write_review_artifact(
        ws, "inv_verify", "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 2\n",
        counts=CriticalCountReport(reviewer=0, judge_reported=True),
    )
    save_handover(ws, _escalation(ws, pr_number=566))
    fake = forge_fake.ReviewForgeFake(
        reviews=[
            forge_fake.FakeReview(
                id=9013, login="alan-verifier", state="CHANGES_REQUESTED", commit_id="head-sha",
            ),
        ],
        readiness=_stale_review_readiness(),
    )
    forge_fake.install(fake, monkeypatch)
    from alan.routes import _answer_waiver  # noqa: PLC0415
    from alan.teams._pr_waiver_dismissal import (  # noqa: PLC0415
        dismiss_for_waiver as original_dismiss,
    )
    from alan.teams._pr_waiver_dismissal import (  # noqa: PLC0415
        record_waiver_dismissal as original_record,
    )

    dismiss_calls: list[object] = []
    record_calls: list[object] = []

    def spy_dismiss(*args: object, **kwargs: object) -> object:
        dismiss_calls.append(kwargs.get("floor_reason"))
        return original_dismiss(*args, **kwargs)  # type: ignore[arg-type]

    def spy_record(*args: object, **kwargs: object) -> None:
        record_calls.append(kwargs.get("floor_reason"))
        original_record(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(_answer_waiver, "dismiss_for_waiver", spy_dismiss)
    monkeypatch.setattr(_answer_waiver, "record_waiver_dismissal", spy_record)

    res = _post_answer(
        client, ws, {"question_id": "clause-stall-abc", "chosen_option": _WAIVE_OPTION},
    )

    assert res.status_code == 200
    assert dismiss_calls == ["posted_count_nonzero"]
    assert record_calls == ["posted_count_nonzero"]


def test_non_waiver_answer_on_verification_escalation_never_dismisses(
    client: TestClient, ws_root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch,
) -> None:
    req = Requirement(text="clause text")
    ws = create("Non Waiver Answer Route", "d", root=ws_root, requirements=[req])
    save_handover(
        ws,
        _escalation(ws, pr_number=560, clause_outcome=_clause_outcome_for(ws, req_id=req.id)),
    )
    fake = forge_fake.ReviewForgeFake(
        reviews=[
            forge_fake.FakeReview(
                id=9007, login="alan-verifier", state="CHANGES_REQUESTED", commit_id="head-sha",
            ),
        ],
        readiness=_stale_review_readiness(),
    )
    forge_fake.install(fake, monkeypatch)

    res = _post_answer(
        client, ws, {"question_id": "clause-stall-abc", "chosen_option": _REWRITE_OPTION},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["dispatch"]["team"] == "verification"
    assert fake.attempted_dismiss_ids == []
    assert fake.reviews[0].state == "CHANGES_REQUESTED"


def test_capability_boundary_waiver_at_zero_criticals_dispatches_pr_merge(
    client: TestClient, ws_root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = create("Waiver Answer Route Capability Boundary", "d", root=ws_root)
    _write_review_artifact(
        ws, "inv_verify", "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 0\n",
        counts=CriticalCountReport(reviewer=0, judge_reported=True),
    )
    save_handover(
        ws, _escalation(ws, pr_number=561, question=_capability_boundary_question()),
    )
    fake = forge_fake.ReviewForgeFake(
        reviews=[
            forge_fake.FakeReview(
                id=9008, login="alan-verifier", state="CHANGES_REQUESTED", commit_id="head-sha",
            ),
        ],
        readiness=_stale_review_readiness(),
    )
    forge_fake.install(fake, monkeypatch)

    res = _post_answer(
        client, ws,
        {
            "question_id": "capability-boundary-abc",
            "chosen_option": _CAPABILITY_BOUNDARY_OPTION_WAIVE,
        },
    )

    assert res.status_code == 200
    body = res.json()
    assert body["dispatch"] is not None
    assert body["dispatch"]["team"] == "pr_merge"
    assert queue.list_jobs()
    assert fake.attempted_dismiss_ids == [9008]
    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.status is not Status.AWAITING_INPUT


def test_red_check_with_stale_review_keeps_genuine_routing(
    client: TestClient, ws_root: Path, queue: Queue, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = create("Waiver Answer Route Red Check", "d", root=ws_root)
    _write_review_artifact(
        ws, "inv_verify", "VERDICT: REQUEST_CHANGES\nDIFF_FINDINGS_CRITICAL: 0\n",
        counts=CriticalCountReport(reviewer=0, judge_reported=True),
    )
    save_handover(ws, _escalation(ws, pr_number=562))
    fake = forge_fake.ReviewForgeFake(
        reviews=[
            forge_fake.FakeReview(
                id=9009, login="alan-verifier", state="CHANGES_REQUESTED", commit_id="stale-sha",
            ),
        ],
        readiness=_genuine_red_check_readiness(),
    )
    forge_fake.install(fake, monkeypatch)

    res = _post_answer(
        client, ws, {"question_id": "clause-stall-abc", "chosen_option": _WAIVE_OPTION},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["dispatch"] is not None
    assert body["dispatch"]["team"] == "pr_merge"
    assert queue.list_jobs()
    reloaded = read(ws.task_id, root=ws_root)
    assert reloaded.meta.status is not Status.AWAITING_INPUT
