from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from alan import unblock_ledger as ledger
from alan.engine_config import EngineConfig, RepoIdentity, register
from alan.finisher_rate import FINISHER_EPOCH, FunnelCounts
from alan.health_windows import Verdict, compute_health_windows, read_window_sources
from alan.queue import Queue
from alan.trajectory import TrajectoryRecord
from alan.workspace import (
    Intervention,
    InterventionAction,
    Requirement,
    Status,
    Task,
    TaskDoDClause,
    TaskStatus,
    Workspace,
)
from tests.test_health_windows import (
    NOW,
    Row,
    ago,
    build_task,
    compute,
    first_attempt_rows,
    make_ws,
)

DEFAULT_SLUG = "DejvMatikar/alan"
SPROCKET_SLUG = "sprocket-co/sprocket"


def register_sprocket_engine() -> None:
    register(EngineConfig(repo=RepoIdentity(owner="sprocket-co", repo="sprocket")))


def failed_ws(
    task_id: str,
    *,
    status: Status = Status.FAILED,
    terminal_days_ago: float | None = 1.0,
    created_days_ago: float = 40.0,
    engine_key: str | None = None,
) -> Workspace:
    return make_ws(
        task_id,
        status=status,
        terminal_days_ago=terminal_days_ago,
        created_days_ago=created_days_ago,
        engine_key=engine_key,
        tasks=[build_task(TaskStatus.PENDING)],
    )


def merge_rows(*task_ids: str) -> str:
    return "\n".join(json.dumps({"action": "merge_ok", "task_id": t}) for t in task_ids)


def record(
    task_id: str,
    verdict: str | None,
    verified_at: str | None,
    *,
    team: str = "implementation",
) -> TrajectoryRecord:
    return TrajectoryRecord(
        task_id=task_id,
        inv_id=None,
        pr_number=1,
        team=team,
        verdict=verdict,
        reward=None,
        fix_cycles=0,
        revisits=0,
        turns=None,
        verified_at=verified_at,
    )


def engage(queue: Queue, key: str, days_ago: float, kind: str = ledger.KIND_ATTEMPT) -> None:
    ledger.ensure_schema(queue)
    ledger.record(
        queue,
        ledger.Entry(
            workspace_key=key,
            scope_ref="pr:1",
            shape="stale_review",
            fingerprint="fp",
            kind=kind,
            outcome=ledger.OUTCOME_ACTED,
            ts=ago(days_ago).timestamp(),
        ),
    )


def test_build_success_is_counted_by_when_each_workspace_finished(queue: Queue) -> None:
    workspaces = [
        make_ws("ok-recent", terminal_days_ago=2),
        failed_ws("bad-recent", terminal_days_ago=3),
        make_ws("ok-older", terminal_days_ago=20),
        make_ws("ok-ancient", terminal_days_ago=45),
    ]
    metric = compute(queue, workspaces=workspaces).fleet.build_success
    assert metric.d7.value == pytest.approx(0.5)
    assert metric.d7.sample == 2
    assert metric.d30.value == pytest.approx(2 / 3)
    assert metric.d30.sample == 3
    assert metric.verdict.higher_is_better is True


def test_a_workspace_finished_exactly_at_the_window_edge_is_inside_it(queue: Queue) -> None:
    metric = compute(
        queue, workspaces=[make_ws("edge", terminal_days_ago=7.0)]
    ).fleet.build_success
    assert (metric.d7.sample, metric.d30.sample) == (1, 1)


def test_workspaces_without_a_terminal_stamp_are_left_out_of_build_success(
    queue: Queue,
) -> None:
    workspaces = [make_ws("dated", terminal_days_ago=2), make_ws("undated", terminal_days_ago=None)]
    metric = compute(queue, workspaces=workspaces).fleet.build_success
    assert (metric.d7.sample, metric.d30.sample) == (1, 1)


def test_only_finished_build_pipeline_workspaces_count_toward_build_success(
    queue: Queue,
) -> None:
    workspaces = [
        make_ws("finished", terminal_days_ago=2),
        make_ws("running", status=Status.ACTIVE, terminal_days_ago=2),
        make_ws("research", terminal_days_ago=2, tasks=[build_task(team="research")]),
    ]
    metric = compute(queue, workspaces=workspaces).fleet.build_success
    assert metric.d30.sample == 1


def test_archiving_a_finished_workspace_does_not_change_build_success(queue: Queue) -> None:
    def build_success(status: Status) -> tuple[float | None, int]:
        workspaces = [
            make_ws("moved", status=status, terminal_days_ago=2),
            failed_ws("bad", terminal_days_ago=3),
        ]
        metric = compute(queue, workspaces=workspaces).fleet.build_success
        return metric.d7.value, metric.d7.sample

    assert build_success(Status.ARCHIVED) == build_success(Status.COMPLETED) == (0.5, 2)


def test_a_partial_workspace_is_a_finished_build_workspace(queue: Queue) -> None:
    metric = compute(
        queue, workspaces=[failed_ws("half", status=Status.PARTIAL, terminal_days_ago=1)]
    ).fleet.build_success
    assert (metric.d7.value, metric.d7.sample) == (0.0, 1)


def live_and_unproven_requirements() -> list[Requirement]:
    return [
        Requirement(id="r1", text="unproven"),
        Requirement(id="r2", text="proven", test_ref="tests/t.py::test_it"),
        Requirement(id="r3", text="withdrawn", withdrawn_by_question_id="q1"),
    ]


def covered_task() -> Task:
    clause = TaskDoDClause(text="covers r4", covers=["r4"], evidence="PR #1")
    return Task(id="t1", team="implementation", status=TaskStatus.COMPLETED, dod_clauses=[clause])


def test_unproven_clauses_are_a_share_of_live_clauses_in_workspaces_finished_in_the_window(
    queue: Queue,
) -> None:
    older_requirements = [
        Requirement(id="r1", text="unproven"),
        Requirement(id="r4", text="covered by a task"),
        Requirement(id="r5", text="also unproven"),
    ]
    workspaces = [
        make_ws("recent", terminal_days_ago=2, requirements=live_and_unproven_requirements()),
        make_ws(
            "older",
            terminal_days_ago=20,
            requirements=older_requirements,
            tasks=[covered_task()],
        ),
        make_ws("no-requirements", terminal_days_ago=1),
        make_ws("ancient", terminal_days_ago=45, requirements=older_requirements),
    ]
    metric = compute(queue, workspaces=workspaces).fleet.unproven_clauses
    assert metric.d7.value == pytest.approx(1 / 2)
    assert metric.d7.sample == 2
    assert metric.d30.value == pytest.approx(3 / 5)
    assert metric.d30.sample == 5
    assert metric.verdict.higher_is_better is False


def test_the_unproven_share_has_no_value_when_no_live_clause_finished_in_the_window(
    queue: Queue,
) -> None:
    metric = compute(
        queue, workspaces=[make_ws("bare", terminal_days_ago=2)]
    ).fleet.unproven_clauses
    assert (metric.d7.value, metric.d7.sample) == (None, 0)


def test_first_pass_approval_counts_only_verifications_dated_inside_each_window(
    queue: Queue,
) -> None:
    records = [
        record("a", "APPROVE", ago(1).isoformat()),
        record("a", "REQUEST_CHANGES", ago(2).isoformat()),
        record("a", "APPROVE", ago(1).replace(tzinfo=None).isoformat()),
        record("a", "APPROVE", ago(20).isoformat()),
        record("a", "APPROVE", ago(45).isoformat()),
        record("a", "APPROVE", None),
        record("a", "APPROVE", "not-a-date"),
        record("a", "APPROVE", ago(1).isoformat(), team="research"),
        record("a", None, ago(1).isoformat()),
    ]
    metric = compute(queue, trajectories=records).fleet.first_pass_approval
    assert metric.d7.value == pytest.approx(2 / 3)
    assert metric.d7.sample == 3
    assert metric.d30.value == pytest.approx(3 / 4)
    assert metric.d30.sample == 4
    assert metric.verdict.higher_is_better is True


def test_first_pass_approval_has_no_value_without_a_dated_verification(queue: Queue) -> None:
    metric = compute(
        queue, trajectories=[record("a", "APPROVE", None)]
    ).fleet.first_pass_approval
    assert (metric.d7.value, metric.d7.sample) == (None, 0)


def test_outcome_and_autonomous_rates_count_workspaces_created_in_each_window(
    queue: Queue,
) -> None:
    workspaces = [
        make_ws("merged-recent", created_days_ago=2),
        make_ws("unmerged-recent", created_days_ago=3),
        make_ws("merged-older", created_days_ago=20),
        make_ws("merged-ancient", created_days_ago=45),
    ]
    fleet = compute(
        queue,
        workspaces=workspaces,
        merge_audit_text=merge_rows("merged-recent", "merged-older", "merged-ancient"),
    ).fleet
    assert fleet.outcome.d7.value == pytest.approx(0.5)
    assert fleet.outcome.d7.sample == 2
    assert fleet.outcome.d30.value == pytest.approx(2 / 3)
    assert fleet.outcome.d30.sample == 3
    assert fleet.autonomous.d7.value == pytest.approx(0.5)
    assert fleet.autonomous.d30.value == pytest.approx(2 / 3)


def test_the_funnel_stage_counts_carry_x_of_n_for_each_window(queue: Queue) -> None:
    workspaces = [
        make_ws("merged-recent", created_days_ago=2),
        make_ws("unmerged-recent", created_days_ago=3),
        make_ws("merged-older", created_days_ago=20),
    ]
    fleet = compute(
        queue, workspaces=workspaces, merge_audit_text=merge_rows("merged-recent", "merged-older")
    ).fleet
    assert fleet.funnel.d7 == FunnelCounts(
        dispatched=2,
        not_crashed=2,
        not_needed_human=2,
        converged=2,
        merged_pr=1,
        dod_evidenced=1,
        autonomous=1,
    )
    assert fleet.funnel.d30 == FunnelCounts(
        dispatched=3,
        not_crashed=3,
        not_needed_human=3,
        converged=3,
        merged_pr=2,
        dod_evidenced=2,
        autonomous=2,
    )


def test_an_intervened_success_is_an_outcome_but_not_autonomous(queue: Queue) -> None:
    touched = [Intervention(action=InterventionAction.ANSWER, timestamp_utc=NOW.isoformat())]
    workspaces = [
        make_ws("hands-off", created_days_ago=2),
        make_ws("touched", created_days_ago=2, interventions=touched),
    ]
    fleet = compute(
        queue, workspaces=workspaces, merge_audit_text=merge_rows("hands-off", "touched")
    ).fleet
    assert fleet.outcome.d7.value == pytest.approx(1.0)
    assert fleet.autonomous.d7.value == pytest.approx(0.5)


def test_the_funnel_never_reaches_back_past_its_epoch(queue: Queue) -> None:
    now = FINISHER_EPOCH + timedelta(days=10)
    pre_epoch = make_ws("pre-epoch")
    post_epoch = make_ws("post-epoch")
    workspaces = [
        pre_epoch.model_copy(
            update={
                "meta": pre_epoch.meta.model_copy(
                    update={"created_at": FINISHER_EPOCH - timedelta(days=1)}
                )
            }
        ),
        post_epoch.model_copy(
            update={
                "meta": post_epoch.meta.model_copy(
                    update={"created_at": FINISHER_EPOCH + timedelta(days=5)}
                )
            }
        ),
    ]
    fleet = compute(
        queue,
        now=now,
        workspaces=workspaces,
        merge_audit_text=merge_rows("pre-epoch", "post-epoch"),
    ).fleet
    assert (fleet.outcome.d7.sample, fleet.outcome.d30.sample) == (1, 1)


def test_a_crashed_workspace_counts_against_the_outcome_rate(queue: Queue) -> None:
    workspaces = [make_ws("crashed", created_days_ago=2), make_ws("fine", created_days_ago=2)]
    fleet = compute(
        queue,
        workspaces=workspaces,
        merge_audit_text=merge_rows("fine"),
        job_failure_text=json.dumps({"workspace_id": "crashed", "error": "boom"}),
    ).fleet
    assert fleet.outcome.d7.value == pytest.approx(0.5)
    assert fleet.funnel.d7.not_crashed == 1


def test_format_error_and_recovery_rates_are_windowed_by_row_timestamp(queue: Queue) -> None:
    rows = (
        first_attempt_rows(when=ago(1), errors=1, total=4)
        + first_attempt_rows(when=ago(20), errors=3, total=4)
        + [
            Row(ago(1), None, 2, "corrected"),
            Row(ago(1), None, 2, "exhausted"),
            Row(ago(20), None, 2, "corrected"),
            Row(ago(20), None, 2, "corrected"),
        ]
    )
    fleet = compute(queue, corrections=rows).fleet
    assert fleet.format_error.d7.value == pytest.approx(0.25)
    assert fleet.format_error.d7.sample == 4
    assert fleet.format_error.d30.value == pytest.approx(0.5)
    assert fleet.format_error.d30.sample == 8
    assert fleet.correction_recovery.d7.value == pytest.approx(0.5)
    assert fleet.correction_recovery.d7.sample == 2
    assert fleet.correction_recovery.d30.value == pytest.approx(0.75)
    assert fleet.correction_recovery.d30.sample == 4
    assert fleet.format_error.verdict.higher_is_better is False
    assert fleet.correction_recovery.verdict.higher_is_better is True


def test_correction_rows_without_a_timestamp_or_past_thirty_days_count_nowhere(
    queue: Queue,
) -> None:
    rows = [
        Row(None, None, 1, "exhausted"),
        Row(ago(40), None, 1, "exhausted"),
        Row(ago(1), None, 3, "exhausted"),
    ]
    result = compute(queue, corrections=rows)
    assert result.fleet.format_error.d30.sample == 0
    assert result.fleet.correction_recovery.d30.sample == 0
    assert result.unattributed == 0


def attribution_rows() -> list[Row]:
    return [
        Row(ago(1), "t-ok", 1, "clean"),
        Row(ago(1), "t-ok", 1, "exhausted"),
        Row(ago(1), "d-ok", 1, "clean"),
        Row(ago(10), "d-ok", 1, "exhausted"),
        Row(ago(1), "ghost", 1, "exhausted"),
        Row(ago(1), None, 1, "clean"),
        Row(ago(1), "t-ok", 2, "corrected"),
        Row(ago(1), "ghost", 2, "exhausted"),
        Row(ago(1), "t-ok", 3, "exhausted"),
        Row(None, "t-ok", 1, "exhausted"),
        Row(ago(40), "ghost", 1, "exhausted"),
    ]


def test_per_repository_format_and_recovery_rates_use_only_attributable_rows(
    queue: Queue,
) -> None:
    register_sprocket_engine()
    workspaces = [make_ws("d-ok"), make_ws("t-ok", engine_key=SPROCKET_SLUG)]
    result = compute(queue, workspaces=workspaces, corrections=attribution_rows())
    fleet, default, sprocket = (
        result.fleet,
        result.repos[DEFAULT_SLUG],
        result.repos[SPROCKET_SLUG],
    )
    assert (fleet.format_error.d7.value, fleet.format_error.d7.sample) == (0.4, 5)
    assert (fleet.format_error.d30.value, fleet.format_error.d30.sample) == (0.5, 6)
    assert (fleet.correction_recovery.d30.value, fleet.correction_recovery.d30.sample) == (0.5, 2)
    assert (sprocket.format_error.d30.value, sprocket.format_error.d30.sample) == (0.5, 2)
    assert (sprocket.correction_recovery.d30.value, sprocket.correction_recovery.d30.sample) == (
        1.0,
        1,
    )
    assert (default.format_error.d7.value, default.format_error.d7.sample) == (0.0, 1)
    assert (default.format_error.d30.value, default.format_error.d30.sample) == (0.5, 2)
    assert (default.correction_recovery.d30.value, default.correction_recovery.d30.sample) == (
        None,
        0,
    )


def test_rows_that_match_no_known_workspace_are_counted_as_unattributed(queue: Queue) -> None:
    register_sprocket_engine()
    workspaces = [make_ws("d-ok"), make_ws("t-ok", engine_key=SPROCKET_SLUG)]
    result = compute(queue, workspaces=workspaces, corrections=attribution_rows())
    assert result.unattributed == 3


def test_first_pass_approval_is_split_per_repository_from_each_tasks_engine(
    queue: Queue,
) -> None:
    register_sprocket_engine()
    workspaces = [make_ws("d-ok"), make_ws("t-ok", engine_key=SPROCKET_SLUG)]
    records = [
        record("t-ok", "APPROVE", ago(1).isoformat()),
        record("t-ok", "REQUEST_CHANGES", ago(1).isoformat()),
        record("d-ok", "APPROVE", ago(1).isoformat()),
    ]
    result = compute(queue, workspaces=workspaces, trajectories=records)
    assert result.repos[SPROCKET_SLUG].first_pass_approval.d7.value == pytest.approx(0.5)
    assert result.repos[SPROCKET_SLUG].first_pass_approval.d7.sample == 2
    assert result.repos[DEFAULT_SLUG].first_pass_approval.d7.value == pytest.approx(1.0)
    assert result.fleet.first_pass_approval.d7.sample == 3


def test_trajectories_of_unknown_tasks_fold_into_the_default_repository(queue: Queue) -> None:
    result = compute(queue, trajectories=[record("ghost", "APPROVE", ago(1).isoformat())])
    repo = result.repos[DEFAULT_SLUG]
    assert (repo.first_pass_approval.d7.value, repo.first_pass_approval.d7.sample) == (1.0, 1)
    assert (repo.rescue_load.d7.value, repo.rescue_load.d7.sample) == (None, 0)
    assert repo.build_success.d7.sample == 0


def test_build_success_unproven_and_the_funnel_are_computed_per_repository(
    queue: Queue,
) -> None:
    register_sprocket_engine()
    workspaces = [
        make_ws(
            "d-ok",
            terminal_days_ago=2,
            created_days_ago=3,
            requirements=live_and_unproven_requirements(),
        ),
        make_ws("t-ok", terminal_days_ago=2, created_days_ago=3, engine_key=SPROCKET_SLUG),
        failed_ws("t-bad", terminal_days_ago=2, engine_key=SPROCKET_SLUG, created_days_ago=3),
    ]
    result = compute(
        queue,
        workspaces=workspaces,
        merge_audit_text=merge_rows("d-ok", "t-ok"),
    )
    default, sprocket = result.repos[DEFAULT_SLUG], result.repos[SPROCKET_SLUG]
    assert set(result.repos) == {DEFAULT_SLUG, SPROCKET_SLUG}
    assert (default.build_success.d7.value, default.build_success.d7.sample) == (0.0, 1)
    assert (sprocket.build_success.d7.value, sprocket.build_success.d7.sample) == (0.5, 2)
    assert result.fleet.build_success.d7.value == pytest.approx(1 / 3)
    assert (default.unproven_clauses.d7.value, default.unproven_clauses.d7.sample) == (0.5, 2)
    assert (sprocket.unproven_clauses.d7.value, sprocket.unproven_clauses.d7.sample) == (None, 0)
    assert default.funnel.d30.dispatched == 1
    assert sprocket.funnel.d30.dispatched == 2
    assert sprocket.funnel.d30.merged_pr == 1
    assert result.fleet.funnel.d30.dispatched == 3
    assert sprocket.outcome.d30.value == pytest.approx(0.5)


def test_rescue_rates_are_windowed_by_engagement_and_scoped_to_each_repository(
    queue: Queue,
) -> None:
    register_sprocket_engine()
    workspaces = [
        make_ws("ws-a", status=Status.ACTIVE, terminal_days_ago=None),
        make_ws("ws-b", status=Status.ACTIVE, terminal_days_ago=None),
        make_ws("ws-c", status=Status.ACTIVE, terminal_days_ago=None, engine_key=SPROCKET_SLUG),
        make_ws("ws-d", status=Status.ESCALATED, terminal_days_ago=None, engine_key=SPROCKET_SLUG),
    ]
    engage(queue, "ws-a", 3)
    engage(queue, "ws-b", 20)
    engage(queue, "ws-d", 3, kind=ledger.KIND_REPORT)
    result = compute(queue, workspaces=workspaces)
    fleet, default, sprocket = (
        result.fleet,
        result.repos[DEFAULT_SLUG],
        result.repos[SPROCKET_SLUG],
    )
    assert (fleet.rescue_success.d7.value, fleet.rescue_success.d7.sample) == (0.5, 2)
    assert (fleet.rescue_success.d30.value, fleet.rescue_success.d30.sample) == (2 / 3, 3)
    assert (fleet.rescue_load.d7.value, fleet.rescue_load.d7.sample) == (0.5, 4)
    assert (fleet.rescue_load.d30.value, fleet.rescue_load.d30.sample) == (0.75, 4)
    assert (default.rescue_success.d7.value, default.rescue_success.d7.sample) == (1.0, 1)
    assert (default.rescue_success.d30.value, default.rescue_success.d30.sample) == (1.0, 2)
    assert (default.rescue_load.d7.value, default.rescue_load.d7.sample) == (0.5, 2)
    assert (default.rescue_load.d30.value, default.rescue_load.d30.sample) == (1.0, 2)
    assert (sprocket.rescue_success.d30.value, sprocket.rescue_success.d30.sample) == (0.0, 1)
    assert (sprocket.rescue_load.d30.value, sprocket.rescue_load.d30.sample) == (0.5, 2)
    assert fleet.rescue_success.verdict.higher_is_better is True
    assert fleet.rescue_load.verdict.higher_is_better is True


def test_rescue_comparison_is_insufficient_below_ten_samples(queue: Queue) -> None:
    workspaces = [make_ws("ws-a", status=Status.ACTIVE, terminal_days_ago=None)]
    engage(queue, "ws-a", 3)
    metric = compute(queue, workspaces=workspaces).fleet.rescue_success
    assert metric.verdict.verdict is Verdict.INSUFFICIENT


def write_lines(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def test_first_pass_approval_flows_from_the_audit_files_through_to_the_windows(
    queue: Queue, tmp_path: Path
) -> None:
    write_lines(
        tmp_path / "node_turns_audit.jsonl",
        [
            {"task_id": "t1", "inv_id": "inv_1", "team": "implementation", "turns": 3},
            {"task_id": "t2", "inv_id": "inv_1", "team": "implementation", "turns": 4},
        ],
    )
    write_lines(
        tmp_path / "verification_audit.jsonl",
        [
            {
                "action": "review_ok",
                "task_id": "t1",
                "inv_id": "inv_1",
                "verdict": "APPROVE",
                "pr_number": 5,
                "timestamp_utc": ago(2).isoformat(),
            },
            {
                "action": "review_ok",
                "task_id": "t2",
                "inv_id": "inv_1",
                "verdict": "REQUEST_CHANGES",
                "pr_number": 6,
                "timestamp_utc": ago(20).isoformat(),
            },
        ],
    )
    sources = read_window_sources(queue, tmp_path, [], [])
    metric = compute_health_windows(sources, now=NOW).fleet.first_pass_approval
    assert (metric.d7.value, metric.d7.sample) == (1.0, 1)
    assert (metric.d30.value, metric.d30.sample) == (0.5, 2)
    assert metric.d30.covered_days == 30
