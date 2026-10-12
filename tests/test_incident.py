from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from alan.handover import Handover, OpenQuestion
from alan.health import HealthFinding, HealthState
from alan.incident import (
    _AUDIT_INCIDENT_MAX_AGE_DAYS,  # pyright: ignore[reportPrivateUsage]
    _AUDIT_SOURCE_FILES,  # pyright: ignore[reportPrivateUsage]
    _EXEMPT_PARK_REASONS,  # pyright: ignore[reportPrivateUsage]
    AuditRow,
    Disposition,
    Incident,
    IncidentClass,
    Suppression,
    _audit_rows_in_window,  # pyright: ignore[reportPrivateUsage]
    _health_scope_ref,  # pyright: ignore[reportPrivateUsage]
    _opt_int,  # pyright: ignore[reportPrivateUsage]
    _pr_number_for,  # pyright: ignore[reportPrivateUsage]
    _terminal_evidence,  # pyright: ignore[reportPrivateUsage]
    _within_audit_window,  # pyright: ignore[reportPrivateUsage]
    _WsContext,  # pyright: ignore[reportPrivateUsage]
    dedupe_by_fingerprint,
    derive_incidents,
    digest_overdue,
    gather_audit_rows,
    incident_fingerprint,
)
from alan.incident_store import (
    ack,
    is_closed,
    is_suppressed,
    load_dispositions,
    reconcile_reopen,
    set_disposition,
    set_suppression,
)
from alan.park import Blocker, ParkReason
from alan.queue import Queue
from alan.workspace import Meta, Status, Workspace


def _make_ws(
    task_id: str = "ws1",
    *,
    status: Status = Status.ACTIVE,
    park_reason: ParkReason | None = None,
    merge_block_class: str | None = None,
    blockers: list[Blocker] | None = None,
    team: str | None = None,
    updated_at: datetime | None = None,
    engine_key: str | None = None,
) -> Workspace:
    ts = updated_at or datetime(2026, 1, 1, tzinfo=UTC)
    meta = Meta(
        task_id=task_id,
        title="t",
        description="d",
        status=status,
        park_reason=park_reason,
        merge_block_class=merge_block_class,
        blockers=blockers or [],
        team=team,
        engine_key=engine_key,
        created_at=ts,
        updated_at=ts,
    )
    return Workspace(task_id=task_id, path=Path(task_id), meta=meta)


def _make_handover(
    task_id: str,
    *,
    pr_number: int | None = None,
    to_team_suggestions: list[str] | None = None,
    handover_id: str = "h1",
) -> Handover:
    return Handover(
        handover_id=handover_id,
        handover_type="forward",
        task_id=task_id,
        from_team="implementation",
        from_invocation_id="inv1",
        to_team_suggestions=to_team_suggestions or [],
        iteration=1,
        timestamp_utc=datetime(2026, 1, 1, tzinfo=UTC),
        full_synthesis="s",
        pr_number=pr_number,
    )


def test_incident_fingerprint_stable_across_volatile_pr_state() -> None:
    handovers = {"ws1": [_make_handover("ws1", pr_number=77)]}
    ws_a = _make_ws(
        status=Status.AWAITING_INPUT,
        park_reason=ParkReason.MERGE_BLOCK,
        merge_block_class="stale_review",
        blockers=[Blocker(kind="review_decision", name="CHANGES_REQUESTED", detail="d1")],
    )
    ws_b = _make_ws(
        status=Status.AWAITING_INPUT,
        park_reason=ParkReason.MERGE_BLOCK,
        merge_block_class="stale_review",
        blockers=[
            Blocker(kind="failing_check", name="tests", detail="d2"),
            Blocker(kind="merge_state_status", name="DIRTY", detail="d3"),
        ],
        updated_at=datetime(2026, 2, 1, tzinfo=UTC),
    )
    fp_a = derive_incidents(workspaces=[ws_a], handovers_by_workspace=handovers)[0].fingerprint
    fp_b = derive_incidents(workspaces=[ws_b], handovers_by_workspace=handovers)[0].fingerprint
    assert fp_a == fp_b


def test_incident_joins_recurrence_by_fingerprint(queue: Queue) -> None:
    handovers = {"ws1": [_make_handover("ws1", pr_number=77)]}
    ws_first = _make_ws(
        status=Status.AWAITING_INPUT,
        park_reason=ParkReason.MERGE_BLOCK,
        merge_block_class="stale_review",
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    first_pass = derive_incidents(workspaces=[ws_first], handovers_by_workspace=handovers)
    reconcile_reopen(queue, first_pass, now=datetime(2026, 1, 1, tzinfo=UTC))

    ws_second = _make_ws(
        status=Status.AWAITING_INPUT,
        park_reason=ParkReason.MERGE_BLOCK,
        merge_block_class="stale_review",
        blockers=[Blocker(kind="failing_check", name="lint", detail="new volatile detail")],
        updated_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    second_pass = derive_incidents(workspaces=[ws_second], handovers_by_workspace=handovers)
    reconcile_reopen(queue, second_pass, now=datetime(2026, 1, 2, tzinfo=UTC))

    records = load_dispositions(queue)
    assert len(records) == 1
    record = next(iter(records.values()))
    assert record.occurrence_count == 2


def test_health_surface_and_incident_share_fingerprint() -> None:
    finding_a = HealthFinding(
        finding_id="s1",
        kind="build_task",
        severity="high",
        description="d1",
        workspace_id="ws9",
        pr_number=None,
        blocker_flags=["job_failed"],
        action="dispatch_remediation",
    )
    finding_b = HealthFinding(
        finding_id="s1",
        kind="build_task",
        severity="critical",
        description="d2 changed",
        workspace_id="ws9",
        pr_number=None,
        blocker_flags=["pr_checks_failing"],
        action="dispatch_remediation",
    )
    state_a = HealthState(
        verdict="degraded",
        blocked_count=1,
        in_flight_count=0,
        findings=[finding_a],
        computed_at="2026-01-01T00:00:00+00:00",
    )
    state_b = HealthState(
        verdict="degraded",
        blocked_count=1,
        in_flight_count=0,
        findings=[finding_b],
        computed_at="2026-01-02T00:00:00+00:00",
    )
    fp_a = derive_incidents(workspaces=[], health_state=state_a)[0].fingerprint
    fp_b = derive_incidents(workspaces=[], health_state=state_b)[0].fingerprint
    assert fp_a == fp_b


@pytest.mark.parametrize(
    ("park_reason", "status", "expected_class"),
    [
        (ParkReason.CRASH, Status.AWAITING_INPUT, IncidentClass.PARK_CRASH),
        (ParkReason.ROUTING_ERROR, Status.AWAITING_INPUT, IncidentClass.PARK_ROUTING_ERROR),
        (ParkReason.STALL, Status.AWAITING_INPUT, IncidentClass.PARK_STALL),
        (ParkReason.MERGE_BLOCK, Status.AWAITING_INPUT, IncidentClass.MERGE_BLOCK),
        (ParkReason.ESCALATED, Status.ESCALATED, IncidentClass.ESCALATION),
        (
            ParkReason.FROZEN_CHAIN,
            Status.AWAITING_INPUT,
            IncidentClass.PARK_FROZEN_CHAIN,
        ),
        (
            ParkReason.CREDENTIAL,
            Status.AWAITING_INPUT,
            IncidentClass.PARK_CREDENTIAL,
        ),
    ],
)
def test_incident_derive_maps_every_anomalous_park_reason(
    park_reason: ParkReason, status: Status, expected_class: IncidentClass
) -> None:
    ws = _make_ws(status=status, park_reason=park_reason, team="implementation")
    incidents = derive_incidents(workspaces=[ws])
    assert len(incidents) == 1
    assert incidents[0].incident_class == expected_class


def test_incident_derive_skips_by_design_park_reasons() -> None:
    question_ws = _make_ws(
        status=Status.AWAITING_INPUT, park_reason=ParkReason.QUESTION, team="implementation"
    )
    assert derive_incidents(workspaces=[question_ws]) == []

    harvest_ws = _make_ws(task_id="ws2", status=Status.AWAITING_INPUT, team="architecture")
    handovers = {"ws2": [_make_handover("ws2", to_team_suggestions=[])]}
    assert derive_incidents(workspaces=[harvest_ws], handovers_by_workspace=handovers) == []


def _question_handover(
    task_id: str, *, blocking: bool = True, answer: str | None = None
) -> Handover:
    return _make_handover(
        task_id,
        handover_id="hq1",
    ).model_copy(
        update={
            "open_questions": [
                OpenQuestion(
                    id="q1",
                    question="which?",
                    resolver_role="human",
                    blocking=blocking,
                    answer=answer,
                )
            ]
        }
    )


@pytest.mark.parametrize("answer", [None, "resolved"])
def test_question_park_exempt_regardless_of_answer_state(answer: str | None) -> None:
    ws = _make_ws(
        status=Status.AWAITING_INPUT, park_reason=ParkReason.QUESTION, team="implementation"
    )
    handovers = {"ws1": [_question_handover("ws1", answer=answer)]}
    assert derive_incidents(workspaces=[ws], handovers_by_workspace=handovers) == []


def test_non_blocking_question_park_is_exempt() -> None:
    ws = _make_ws(
        status=Status.AWAITING_INPUT, park_reason=ParkReason.QUESTION, team="implementation"
    )
    handovers = {"ws1": [_question_handover("ws1", blocking=False, answer="resolved")]}
    assert derive_incidents(workspaces=[ws], handovers_by_workspace=handovers) == []


def test_awaiting_harvest_exempt_with_no_handovers_at_all() -> None:
    ws = _make_ws(status=Status.AWAITING_INPUT, team="research")
    assert derive_incidents(workspaces=[ws]) == []


def test_awaiting_harvest_not_exempt_when_suggestions_present() -> None:
    ws = _make_ws(status=Status.AWAITING_INPUT, team="architecture")
    handovers = {"ws1": [_make_handover("ws1", to_team_suggestions=["verification"])]}
    incidents = derive_incidents(workspaces=[ws], handovers_by_workspace=handovers)
    assert len(incidents) == 1
    assert incidents[0].incident_class == IncidentClass.RESIDUE


def test_awaiting_harvest_not_exempt_when_team_not_design_family() -> None:
    ws = _make_ws(status=Status.AWAITING_INPUT, team="implementation")
    incidents = derive_incidents(workspaces=[ws])
    assert incidents[0].incident_class == IncidentClass.RESIDUE


def test_awaiting_harvest_not_exempt_when_status_not_awaiting_input() -> None:
    ws = _make_ws(status=Status.ESCALATED, team="architecture")
    incidents = derive_incidents(workspaces=[ws])
    assert incidents[0].incident_class == IncidentClass.ESCALATION


def test_stale_question_park_reason_does_not_suppress_later_failure() -> None:
    ws = _make_ws(status=Status.FAILED, park_reason=ParkReason.QUESTION, team="implementation")
    incidents = derive_incidents(workspaces=[ws])
    assert len(incidents) == 1
    assert incidents[0].incident_class == IncidentClass.TERMINAL_FAILED


def test_stale_question_park_reason_does_not_suppress_escalation() -> None:
    ws = _make_ws(status=Status.ESCALATED, park_reason=ParkReason.QUESTION)
    incidents = derive_incidents(workspaces=[ws])
    assert len(incidents) == 1
    assert incidents[0].incident_class == IncidentClass.ESCALATION


@pytest.mark.parametrize(
    "stale_park_reason",
    [
        ParkReason.CRASH,
        ParkReason.ROUTING_ERROR,
        ParkReason.STALL,
        ParkReason.MERGE_BLOCK,
        ParkReason.FROZEN_CHAIN,
        ParkReason.CREDENTIAL,
    ],
)
def test_stale_park_reason_does_not_suppress_later_failure(
    stale_park_reason: ParkReason,
) -> None:
    ws = _make_ws(status=Status.FAILED, park_reason=stale_park_reason, team="implementation")
    incidents = derive_incidents(workspaces=[ws])
    assert len(incidents) == 1
    assert incidents[0].incident_class == IncidentClass.TERMINAL_FAILED


@pytest.mark.parametrize(
    "stale_park_reason",
    [
        ParkReason.CRASH,
        ParkReason.ROUTING_ERROR,
        ParkReason.STALL,
        ParkReason.MERGE_BLOCK,
        ParkReason.FROZEN_CHAIN,
        ParkReason.CREDENTIAL,
    ],
)
def test_stale_park_reason_does_not_suppress_escalation(stale_park_reason: ParkReason) -> None:
    ws = _make_ws(status=Status.ESCALATED, park_reason=stale_park_reason)
    incidents = derive_incidents(workspaces=[ws])
    assert len(incidents) == 1
    assert incidents[0].incident_class == IncidentClass.ESCALATION


def test_unmapped_anomaly_lands_in_residue_bucket() -> None:
    ws = _make_ws(status=Status.AWAITING_INPUT, team="implementation")
    incidents = derive_incidents(workspaces=[ws])
    assert len(incidents) == 1
    assert incidents[0].incident_class == IncidentClass.RESIDUE


def test_derive_incidents_healthy_workspace_produces_nothing() -> None:
    ws = _make_ws(status=Status.ACTIVE)
    assert derive_incidents(workspaces=[ws]) == []


def test_derive_incidents_terminal_failed() -> None:
    ws = _make_ws(status=Status.FAILED, team="implementation")
    incidents = derive_incidents(workspaces=[ws])
    assert incidents[0].incident_class == IncidentClass.TERMINAL_FAILED
    assert "outcome_reason" in incidents[0].evidence


def test_derive_incidents_terminal_partial() -> None:
    ws = _make_ws(status=Status.PARTIAL, team="implementation")
    incidents = derive_incidents(workspaces=[ws])
    assert incidents[0].incident_class == IncidentClass.TERMINAL_PARTIAL


def test_every_park_reason_is_exempt_or_has_a_typed_source() -> None:
    for reason in ParkReason:
        if reason in _EXEMPT_PARK_REASONS:
            continue
        status = Status.ESCALATED if reason == ParkReason.ESCALATED else Status.AWAITING_INPUT
        ws = _make_ws(status=status, park_reason=reason, team="implementation")
        incidents = derive_incidents(workspaces=[ws])
        assert len(incidents) == 1
        assert incidents[0].incident_class != IncidentClass.RESIDUE


def test_audit_source_files_cover_job_failure_self_heal_merge_gate() -> None:
    assert set(_AUDIT_SOURCE_FILES) == {
        IncidentClass.JOB_FAILURE,
        IncidentClass.SELF_HEAL,
        IncidentClass.MERGE_GATE,
    }


def test_merge_block_evidence_carries_instruction_and_action_url() -> None:
    blockers = [
        Blocker(kind="failed_reason", name="r", detail="d"),
        Blocker(
            kind="review_decision",
            name="CHANGES_REQUESTED",
            detail="d2",
            instruction="do X",
            action_url="https://example.com",
        ),
    ]
    ws = _make_ws(
        status=Status.AWAITING_INPUT,
        park_reason=ParkReason.MERGE_BLOCK,
        merge_block_class="pre_existing_red",
        blockers=blockers,
        team="implementation",
    )
    evidence = derive_incidents(workspaces=[ws])[0].evidence
    assert evidence["instruction"] == "do X"
    assert evidence["action_url"] == "https://example.com"


def test_merge_block_evidence_defaults_when_no_instruction() -> None:
    ws = _make_ws(
        status=Status.AWAITING_INPUT,
        park_reason=ParkReason.MERGE_BLOCK,
        merge_block_class="transient_flake",
        team="implementation",
    )
    evidence = derive_incidents(workspaces=[ws])[0].evidence
    assert evidence["instruction"] is None
    assert evidence["action_url"] is None


def test_frozen_chain_evidence_carries_ancestor_instruction() -> None:
    blockers = [
        Blocker(
            kind="reason",
            name="ancestor-task",
            detail="frozen chain: ancestor task 'ancestor-task' blocks 1 descendant(s): child",
            instruction="Re-dispatch or manually resolve task 'ancestor-task'.",
        ),
    ]
    ws = _make_ws(
        status=Status.AWAITING_INPUT,
        park_reason=ParkReason.FROZEN_CHAIN,
        blockers=blockers,
        team="implementation",
    )
    evidence = derive_incidents(workspaces=[ws])[0].evidence
    assert evidence["instruction"] == "Re-dispatch or manually resolve task 'ancestor-task'."
    assert evidence["action_url"] is None


def test_escalation_evidence_carries_merge_block_class() -> None:
    ws = _make_ws(status=Status.ESCALATED, merge_block_class="pre_existing_red")
    evidence = derive_incidents(workspaces=[ws])[0].evidence
    assert evidence["merge_block_class"] == "pre_existing_red"


def test_terminal_evidence_carries_merge_block_class() -> None:
    ws = _make_ws(status=Status.FAILED, merge_block_class="stale_review", team="implementation")
    evidence = derive_incidents(workspaces=[ws])[0].evidence
    assert evidence["merge_block_class"] == "stale_review"


def test_terminal_evidence_outcome_none_when_status_not_terminal() -> None:
    ws = _make_ws(status=Status.ACTIVE)
    ctx = _WsContext(ws=ws, handovers=[])
    evidence = _terminal_evidence(ctx)
    assert evidence["outcome_reason"] is None


def test_pr_number_for_returns_none_without_handovers() -> None:
    ws = _make_ws(status=Status.ESCALATED)
    ctx = _WsContext(ws=ws, handovers=[])
    assert _pr_number_for(ctx) is None


def test_pr_number_for_returns_latest_pr() -> None:
    ws = _make_ws(status=Status.ESCALATED)
    handovers = [
        _make_handover("ws1", pr_number=None, handover_id="h1"),
        _make_handover("ws1", pr_number=42, handover_id="h2"),
    ]
    ctx = _WsContext(ws=ws, handovers=handovers)
    assert _pr_number_for(ctx) == 42


def test_incident_fingerprint_accepts_class_as_string() -> None:
    fp_enum = incident_fingerprint(IncidentClass.RESIDUE, "ws:x", None)
    fp_str = incident_fingerprint("residue", "ws:x", None)
    assert fp_enum == fp_str


def test_incident_fingerprint_block_class_changes_identity() -> None:
    fp1 = incident_fingerprint(IncidentClass.MERGE_BLOCK, "pr:1", "stale_review")
    fp2 = incident_fingerprint(IncidentClass.MERGE_BLOCK, "pr:1", None)
    assert fp1 != fp2


def test_health_scope_ref_prefers_pr() -> None:
    f = HealthFinding(
        finding_id="s1",
        kind="build_task",
        severity="high",
        description="d",
        workspace_id="ws9",
        pr_number=5,
        blocker_flags=[],
        action="deep_link",
    )
    assert _health_scope_ref(f) == "pr:5"


def test_health_scope_ref_falls_back_to_ws() -> None:
    f = HealthFinding(
        finding_id="s1",
        kind="stale_job",
        severity="high",
        description="d",
        workspace_id="ws9",
        pr_number=None,
        blocker_flags=[],
        action="deep_link",
    )
    assert _health_scope_ref(f) == "ws:ws9"


def test_health_scope_ref_falls_back_to_finding_id() -> None:
    f = HealthFinding(
        finding_id="main_ci",
        kind="main_ci",
        severity="critical",
        description="d",
        workspace_id=None,
        pr_number=None,
        blocker_flags=[],
        action="dispatch_remediation",
    )
    assert _health_scope_ref(f) == "health:main_ci"


def test_opt_int_ignores_non_int_values() -> None:
    assert _opt_int({"pr_number": "not-an-int"}, "pr_number") is None
    assert _opt_int({"pr_number": 5}, "pr_number") == 5


def test_derive_incidents_from_audit_rows_maps_each_class() -> None:
    rows = [
        AuditRow(
            incident_class=IncidentClass.JOB_FAILURE,
            raw={"task_id": "t1", "error": "boom", "timestamp_utc": "2026-01-01T00:00:00+00:00"},
        ),
        AuditRow(
            incident_class=IncidentClass.SELF_HEAL,
            raw={"workspace_id": "t2", "pr_number": 9},
        ),
        AuditRow(
            incident_class=IncidentClass.MERGE_GATE,
            raw={"pr_number": 10, "block_class": "stale_review"},
        ),
    ]
    incidents = derive_incidents(workspaces=[], audit_rows=rows)
    classes = {i.incident_class for i in incidents}
    assert classes == {IncidentClass.JOB_FAILURE, IncidentClass.SELF_HEAL, IncidentClass.MERGE_GATE}
    job_failure = next(i for i in incidents if i.incident_class == IncidentClass.JOB_FAILURE)
    assert job_failure.workspace_id == "t1"
    assert job_failure.scope_ref == "ws:t1"
    merge_gate = next(i for i in incidents if i.incident_class == IncidentClass.MERGE_GATE)
    assert merge_gate.pr_number == 10
    assert merge_gate.scope_ref == "pr:10"


def test_merge_gate_incident_ignores_additive_reconcile_outcome_field() -> None:
    with_field = AuditRow(
        incident_class=IncidentClass.MERGE_GATE,
        raw={"pr_number": 10, "block_class": "dirty", "reconcile_outcome": "conflict"},
    )
    without_field = AuditRow(
        incident_class=IncidentClass.MERGE_GATE,
        raw={"pr_number": 10, "block_class": "dirty"},
    )
    incident_with = derive_incidents(workspaces=[], audit_rows=[with_field])[0]
    incident_without = derive_incidents(workspaces=[], audit_rows=[without_field])[0]
    assert incident_with.fingerprint == incident_without.fingerprint
    assert incident_with.scope_ref == incident_without.scope_ref == "pr:10"
    assert incident_with.pr_number == 10


def test_incident_from_audit_row_falls_back_to_audit_scope_without_ws_or_pr() -> None:
    rows = [AuditRow(incident_class=IncidentClass.JOB_FAILURE, raw={"error": "boom"})]
    incidents = derive_incidents(workspaces=[], audit_rows=rows)
    assert incidents[0].scope_ref == "audit:job_failure"
    assert incidents[0].workspace_id is None
    assert incidents[0].last_seen == ""


def test_gather_audit_rows_missing_files_returns_empty(tmp_path: Path) -> None:
    assert gather_audit_rows(tmp_path) == []


def test_gather_audit_rows_reads_and_parses(tmp_path: Path) -> None:
    (tmp_path / "job_failure_audit.jsonl").write_text(
        json.dumps({"task_id": "t1", "error": "boom"}) + "\nnot json\n\n",
        encoding="utf-8",
    )
    rows = gather_audit_rows(tmp_path)
    assert len(rows) == 1
    assert rows[0].incident_class == IncidentClass.JOB_FAILURE
    assert rows[0].raw["task_id"] == "t1"


def test_gather_audit_rows_skips_non_dict_json_line(tmp_path: Path) -> None:
    (tmp_path / "self_heal_audit.jsonl").write_text(json.dumps([1, 2, 3]) + "\n", encoding="utf-8")
    assert gather_audit_rows(tmp_path) == []


def test_gather_audit_rows_tolerates_oserror(tmp_path: Path) -> None:
    bogus = tmp_path / "merge_gate_audit.jsonl"
    bogus.mkdir()
    assert gather_audit_rows(tmp_path) == []


def test_gather_audit_rows_reuses_cache_when_file_unchanged(tmp_path: Path) -> None:
    (tmp_path / "job_failure_audit.jsonl").write_text(
        json.dumps({"task_id": "t1", "error": "boom"}) + "\n", encoding="utf-8"
    )
    first = gather_audit_rows(tmp_path)
    second = gather_audit_rows(tmp_path)
    assert first == second
    assert len(second) == 1


def test_audit_row_inside_window_produces_incident() -> None:
    now = datetime(2026, 7, 25, tzinfo=UTC)
    row = AuditRow(
        incident_class=IncidentClass.JOB_FAILURE,
        raw={"task_id": "t1", "timestamp_utc": (now - timedelta(days=1)).isoformat()},
    )
    incidents = derive_incidents(workspaces=[], audit_rows=[row], now=now)
    assert len(incidents) == 1


def test_audit_row_outside_window_produces_no_incident() -> None:
    now = datetime(2026, 7, 25, tzinfo=UTC)
    row = AuditRow(
        incident_class=IncidentClass.JOB_FAILURE,
        raw={
            "task_id": "t1",
            "timestamp_utc": (now - timedelta(days=_AUDIT_INCIDENT_MAX_AGE_DAYS + 1)).isoformat(),
        },
    )
    assert derive_incidents(workspaces=[], audit_rows=[row], now=now) == []


def test_audit_row_empty_timestamp_fails_open() -> None:
    now = datetime(2026, 7, 25, tzinfo=UTC)
    row = AuditRow(incident_class=IncidentClass.JOB_FAILURE, raw={"task_id": "t1"})
    incidents = derive_incidents(workspaces=[], audit_rows=[row], now=now)
    assert len(incidents) == 1


def test_derive_incidents_now_none_disables_window() -> None:
    row = AuditRow(
        incident_class=IncidentClass.JOB_FAILURE,
        raw={"task_id": "t1", "timestamp_utc": "2020-01-01T00:00:00+00:00"},
    )
    incidents = derive_incidents(workspaces=[], audit_rows=[row])
    assert len(incidents) == 1


def test_within_audit_window_non_string_timestamp_fails_open() -> None:
    row = AuditRow(incident_class=IncidentClass.JOB_FAILURE, raw={"timestamp_utc": 12345})
    assert _within_audit_window(row, "2026-01-01T00:00:00+00:00") is True


def test_audit_rows_in_window_now_none_returns_rows_unchanged() -> None:
    rows = [AuditRow(incident_class=IncidentClass.JOB_FAILURE, raw={})]
    assert _audit_rows_in_window(rows, None) == rows


def test_workspace_incident_thirty_days_old_survives_window() -> None:
    now = datetime(2026, 7, 25, tzinfo=UTC)
    old_ts = now - timedelta(days=30)
    ws = _make_ws(status=Status.FAILED, team="implementation", updated_at=old_ts)
    incidents = derive_incidents(workspaces=[ws], now=now)
    assert len(incidents) == 1
    assert incidents[0].incident_class == IncidentClass.TERMINAL_FAILED


def _fp_incident(
    fingerprint: str, last_seen: str, *, workspace_id: str | None = None
) -> Incident:
    return Incident(
        fingerprint=fingerprint,
        incident_class=IncidentClass.JOB_FAILURE,
        scope_ref="audit:job_failure",
        workspace_id=workspace_id,
        pr_number=None,
        title="t",
        evidence={},
        last_seen=last_seen,
    )


def test_dedupe_by_fingerprint_survivor_has_later_last_seen() -> None:
    older = _fp_incident("fp1", "2026-01-01T00:00:00+00:00", workspace_id="a")
    newer = _fp_incident("fp1", "2026-01-02T00:00:00+00:00", workspace_id="b")
    result = dedupe_by_fingerprint([older, newer])
    assert len(result) == 1
    assert result[0].workspace_id == "b"


def test_dedupe_by_fingerprint_survivor_keeps_first_occurrence_position() -> None:
    fp1_first = _fp_incident("fp1", "2026-01-01T00:00:00+00:00", workspace_id="a")
    fp2 = _fp_incident("fp2", "2026-01-01T00:00:00+00:00", workspace_id="c")
    fp1_later = _fp_incident("fp1", "2026-01-03T00:00:00+00:00", workspace_id="b")
    result = dedupe_by_fingerprint([fp1_first, fp2, fp1_later])
    assert [i.fingerprint for i in result] == ["fp1", "fp2"]
    assert result[0].workspace_id == "b"


def test_dedupe_by_fingerprint_tie_keeps_first() -> None:
    first = _fp_incident("fp1", "2026-01-01T00:00:00+00:00", workspace_id="a")
    second = _fp_incident("fp1", "2026-01-01T00:00:00+00:00", workspace_id="b")
    result = dedupe_by_fingerprint([first, second])
    assert len(result) == 1
    assert result[0].workspace_id == "a"


def test_dedupe_by_fingerprint_empty_returns_empty() -> None:
    assert dedupe_by_fingerprint([]) == []


def test_workspace_incidents_colliding_on_pr_scope_collapse() -> None:
    handovers = {
        "ws1": [_make_handover("ws1", pr_number=77)],
        "ws2": [_make_handover("ws2", pr_number=77, handover_id="h2")],
    }
    ws1 = _make_ws(
        "ws1",
        status=Status.FAILED,
        team="implementation",
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    ws2 = _make_ws(
        "ws2",
        status=Status.FAILED,
        team="implementation",
        updated_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    incidents = derive_incidents(workspaces=[ws1, ws2], handovers_by_workspace=handovers)
    assert len(incidents) == 1


def test_derive_incidents_windows_audit_only_and_dedupes_across_both_sources() -> None:
    now = datetime(2026, 7, 25, tzinfo=UTC)
    old_audit_row = AuditRow(
        incident_class=IncidentClass.JOB_FAILURE,
        raw={"task_id": "old-audit", "timestamp_utc": (now - timedelta(days=20)).isoformat()},
    )
    dup_audit_row_a = AuditRow(
        incident_class=IncidentClass.MERGE_GATE,
        raw={
            "pr_number": 99,
            "block_class": "stale_review",
            "timestamp_utc": (now - timedelta(days=1)).isoformat(),
        },
    )
    dup_audit_row_b = AuditRow(
        incident_class=IncidentClass.MERGE_GATE,
        raw={
            "pr_number": 99,
            "block_class": "stale_review",
            "timestamp_utc": now.isoformat(),
        },
    )
    ws = _make_ws(status=Status.FAILED, team="implementation", updated_at=now)

    incidents = derive_incidents(
        workspaces=[ws],
        audit_rows=[old_audit_row, dup_audit_row_a, dup_audit_row_b],
        now=now,
    )

    assert len(incidents) == 2
    assert incidents[0].incident_class == IncidentClass.TERMINAL_FAILED
    assert incidents[1].incident_class == IncidentClass.MERGE_GATE
    assert incidents[1].last_seen == now.isoformat()


def test_close_requires_typed_disposition(queue: Queue) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    ack(queue, "fp1", incident_class="merge_block", now=now)
    set_suppression(
        queue,
        "fp1",
        Suppression(reason="flaky", ttl_epoch=now.timestamp() + 3600, recurrence_threshold=5),
        now=now,
    )
    records = load_dispositions(queue)
    assert is_closed(records["fp1"]) is False

    set_disposition(queue, "fp1", Disposition.ROOT_CAUSED, now=now)
    records = load_dispositions(queue)
    assert is_closed(records["fp1"]) is True


def test_suppression_ttl_expiry_resurfaces(queue: Queue) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    suppression = Suppression(
        reason="flaky", ttl_epoch=now.timestamp() + 10, recurrence_threshold=100
    )
    record = set_suppression(queue, "fp1", suppression, incident_class="job_failure", now=now)
    assert is_suppressed(record, now=now) is True
    later = datetime(2026, 1, 1, 0, 0, 20, tzinfo=UTC)
    assert is_suppressed(record, now=later) is False


def test_suppression_auto_unsuppresses_past_recurrence_threshold(queue: Queue) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    suppression = Suppression(
        reason="flaky", ttl_epoch=now.timestamp() + 10_000, recurrence_threshold=2
    )
    set_suppression(queue, "fp1", suppression, incident_class="job_failure", now=now)
    record = load_dispositions(queue)["fp1"]
    assert is_suppressed(record, now=now) is True

    fresh = Incident(
        fingerprint="fp1",
        incident_class=IncidentClass.JOB_FAILURE,
        scope_ref="ws:x",
        workspace_id="x",
        pr_number=None,
        title="t",
        evidence={},
        last_seen=now.isoformat(),
    )
    reconcile_reopen(queue, [fresh], now=now)
    record = load_dispositions(queue)["fp1"]
    assert record.occurrence_count == 2
    assert record.suppression is None
    assert is_suppressed(record, now=now) is False


def test_root_caused_reopens_and_falsifies_on_recurrence(queue: Queue) -> None:
    t1 = datetime(2026, 1, 1, tzinfo=UTC)
    t2 = datetime(2026, 1, 2, tzinfo=UTC)
    t3 = datetime(2026, 1, 3, tzinfo=UTC)
    first = Incident(
        fingerprint="fp1",
        incident_class=IncidentClass.MERGE_BLOCK,
        scope_ref="pr:77",
        workspace_id=None,
        pr_number=77,
        title="t",
        evidence={},
        last_seen=t1.isoformat(),
    )
    reconcile_reopen(queue, [first], now=t1)
    set_disposition(queue, "fp1", Disposition.ROOT_CAUSED, now=t2)
    record = load_dispositions(queue)["fp1"]
    assert is_closed(record) is True

    recurred = Incident(
        fingerprint="fp1",
        incident_class=IncidentClass.MERGE_BLOCK,
        scope_ref="pr:77",
        workspace_id=None,
        pr_number=77,
        title="t",
        evidence={},
        last_seen=t3.isoformat(),
    )
    reconcile_reopen(queue, [recurred], now=t3)
    record = load_dispositions(queue)["fp1"]
    assert is_closed(record) is False
    assert record.diagnosis_falsified is True
    assert record.recurrence_counter == 1


def test_root_caused_does_not_reopen_without_fresh_recurrence(queue: Queue) -> None:
    t1 = datetime(2026, 1, 1, tzinfo=UTC)
    t2 = datetime(2026, 1, 2, tzinfo=UTC)
    first = Incident(
        fingerprint="fp1",
        incident_class=IncidentClass.MERGE_BLOCK,
        scope_ref="pr:77",
        workspace_id=None,
        pr_number=77,
        title="t",
        evidence={},
        last_seen=t1.isoformat(),
    )
    reconcile_reopen(queue, [first], now=t1)
    set_disposition(queue, "fp1", Disposition.ROOT_CAUSED, now=t2)

    reconcile_reopen(queue, [], now=t2)
    record = load_dispositions(queue)["fp1"]
    assert is_closed(record) is True
    assert record.diagnosis_falsified is False


def test_digest_overdue_false_before_deadline() -> None:
    deadline = datetime(2026, 7, 5, 9, 0, tzinfo=UTC)
    now = datetime(2026, 7, 5, 8, 59, tzinfo=UTC)
    assert digest_overdue(None, now, deadline) is False
    assert digest_overdue("2026-07-04", now, deadline) is False


def test_digest_overdue_true_when_past_deadline_and_no_prior_digest() -> None:
    deadline = datetime(2026, 7, 5, 9, 0, tzinfo=UTC)
    now = datetime(2026, 7, 5, 9, 30, tzinfo=UTC)
    assert digest_overdue(None, now, deadline) is True


def test_digest_overdue_true_when_last_date_stale() -> None:
    deadline = datetime(2026, 7, 5, 9, 0, tzinfo=UTC)
    now = datetime(2026, 7, 5, 9, 30, tzinfo=UTC)
    assert digest_overdue("2026-07-04", now, deadline) is True


def test_digest_overdue_false_when_last_date_matches_deadline_date() -> None:
    deadline = datetime(2026, 7, 5, 9, 0, tzinfo=UTC)
    now = datetime(2026, 7, 5, 20, 0, tzinfo=UTC)
    assert digest_overdue("2026-07-05", now, deadline) is False


def test_digest_overdue_true_exactly_at_deadline() -> None:
    deadline = datetime(2026, 7, 5, 9, 0, tzinfo=UTC)
    assert digest_overdue(None, deadline, deadline) is True


def test_digest_overdue_is_pure_given_same_inputs() -> None:
    deadline = datetime(2026, 7, 5, 9, 0, tzinfo=UTC)
    now = datetime(2026, 7, 5, 9, 30, tzinfo=UTC)
    first = digest_overdue("2026-07-04", now, deadline)
    second = digest_overdue("2026-07-04", now, deadline)
    assert first == second is True


def test_derive_incidents_omits_digest_overdue_when_now_or_deadline_missing() -> None:
    incidents = derive_incidents(workspaces=[])
    assert not any(i.incident_class == IncidentClass.DIGEST_OVERDUE for i in incidents)

    incidents = derive_incidents(
        workspaces=[], digest_last_date=None, now=datetime(2026, 7, 5, 9, 30, tzinfo=UTC)
    )
    assert not any(i.incident_class == IncidentClass.DIGEST_OVERDUE for i in incidents)


def test_derive_incidents_appends_digest_overdue_incident_when_overdue() -> None:
    deadline = datetime(2026, 7, 5, 9, 0, tzinfo=UTC)
    now = datetime(2026, 7, 5, 9, 30, tzinfo=UTC)
    incidents = derive_incidents(
        workspaces=[], digest_last_date="2026-07-04", now=now, digest_deadline=deadline
    )
    assert len(incidents) == 1
    incident = incidents[0]
    assert incident.incident_class == IncidentClass.DIGEST_OVERDUE
    assert incident.scope_ref == "system:digest"
    assert incident.workspace_id is None
    assert incident.pr_number is None
    assert incident.evidence == {
        "digest_last_date": "2026-07-04",
        "deadline": deadline.isoformat(),
    }


def test_derive_incidents_digest_overdue_fingerprint_is_stable() -> None:
    deadline = datetime(2026, 7, 5, 9, 0, tzinfo=UTC)
    now_a = datetime(2026, 7, 5, 9, 30, tzinfo=UTC)
    now_b = datetime(2026, 7, 6, 9, 30, tzinfo=UTC)
    fp_a = derive_incidents(
        workspaces=[], digest_last_date="2026-07-04", now=now_a, digest_deadline=deadline
    )[0].fingerprint
    fp_b = derive_incidents(
        workspaces=[],
        digest_last_date="2026-07-05",
        now=now_b,
        digest_deadline=datetime(2026, 7, 6, 9, 0, tzinfo=UTC),
    )[0].fingerprint
    assert fp_a == fp_b


def test_incident_engine_key_defaults_to_none() -> None:
    ws = _make_ws(status=Status.ESCALATED)
    incidents = derive_incidents(workspaces=[ws])
    assert incidents[0].engine_key is None


def test_incident_engine_key_derived_from_workspace_first() -> None:
    ws = _make_ws(status=Status.ESCALATED, engine_key="sprocket-co/sprocket")
    incidents = derive_incidents(workspaces=[ws])
    assert incidents[0].engine_key == "sprocket-co/sprocket"


def test_incident_engine_key_derived_from_residue_workspace() -> None:
    ws = _make_ws(
        status=Status.AWAITING_INPUT,
        park_reason=None,
        team="implementation",
        engine_key="sprocket-co/sprocket",
    )
    incidents = derive_incidents(workspaces=[ws])
    assert incidents[0].incident_class == IncidentClass.RESIDUE
    assert incidents[0].engine_key == "sprocket-co/sprocket"


def test_incident_engine_key_derived_from_health_finding() -> None:
    finding = HealthFinding(
        finding_id="s1",
        kind="build_task",
        severity="high",
        description="d1",
        workspace_id="ws9",
        pr_number=None,
        blocker_flags=["job_failed"],
        action="dispatch_remediation",
        engine_key="sprocket-co/sprocket",
    )
    state = HealthState(
        verdict="degraded",
        blocked_count=1,
        in_flight_count=0,
        findings=[finding],
        computed_at="2026-01-01T00:00:00+00:00",
    )
    incidents = derive_incidents(workspaces=[], health_state=state)
    assert incidents[0].engine_key == "sprocket-co/sprocket"


def test_incident_from_audit_row_engine_key_from_map() -> None:
    rows = [AuditRow(incident_class=IncidentClass.JOB_FAILURE, raw={"task_id": "t1"})]
    incidents = derive_incidents(
        workspaces=[], audit_rows=rows, engine_key_by_workspace={"t1": "sprocket-co/sprocket"}
    )
    assert incidents[0].engine_key == "sprocket-co/sprocket"


def test_incident_from_audit_row_engine_key_none_without_map() -> None:
    rows = [AuditRow(incident_class=IncidentClass.JOB_FAILURE, raw={"task_id": "t1"})]
    incidents = derive_incidents(workspaces=[], audit_rows=rows)
    assert incidents[0].engine_key is None


def test_incident_from_audit_row_engine_key_none_when_workspace_missing_from_map() -> None:
    rows = [AuditRow(incident_class=IncidentClass.JOB_FAILURE, raw={"task_id": "t1"})]
    incidents = derive_incidents(
        workspaces=[], audit_rows=rows, engine_key_by_workspace={"other-ws": "sprocket-co/sprocket"}
    )
    assert incidents[0].engine_key is None


def test_incident_from_audit_row_engine_key_none_without_workspace_id() -> None:
    rows = [AuditRow(incident_class=IncidentClass.JOB_FAILURE, raw={"error": "boom"})]
    incidents = derive_incidents(
        workspaces=[], audit_rows=rows, engine_key_by_workspace={"t1": "sprocket-co/sprocket"}
    )
    assert incidents[0].engine_key is None


def test_escalation_fingerprint_differs_before_and_after_clear() -> None:
    with_label = _make_ws(status=Status.ESCALATED, merge_block_class="stale_review")
    cleared = _make_ws(status=Status.ESCALATED, merge_block_class=None)
    fp_with = derive_incidents(workspaces=[with_label])[0].fingerprint
    fp_cleared = derive_incidents(workspaces=[cleared])[0].fingerprint
    assert fp_with != fp_cleared


def test_cleared_label_escalation_fingerprints_identically_to_never_labeled() -> None:
    cleared = _make_ws(
        status=Status.ESCALATED,
        merge_block_class=None,
        blockers=[Blocker(kind="review_decision", name="CHANGES_REQUESTED", detail="stale")],
        team="team-a",
        updated_at=datetime(2026, 3, 1, tzinfo=UTC),
    )
    never_labeled = _make_ws(
        status=Status.ESCALATED,
        merge_block_class=None,
        blockers=[],
        team="team-b",
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    fp_cleared = derive_incidents(workspaces=[cleared])[0].fingerprint
    fp_never = derive_incidents(workspaces=[never_labeled])[0].fingerprint
    assert fp_cleared == fp_never


def test_merge_block_fingerprint_still_varies_with_block_class() -> None:
    stale = _make_ws(
        status=Status.AWAITING_INPUT,
        park_reason=ParkReason.MERGE_BLOCK,
        merge_block_class="stale_review",
    )
    genuine = _make_ws(
        status=Status.AWAITING_INPUT,
        park_reason=ParkReason.MERGE_BLOCK,
        merge_block_class="genuine",
    )
    fp_stale = derive_incidents(workspaces=[stale])[0].fingerprint
    fp_genuine = derive_incidents(workspaces=[genuine])[0].fingerprint
    assert fp_stale != fp_genuine


def test_credential_park_fingerprint_is_unaffected_by_merge_block_class() -> None:
    ws = _make_ws(
        status=Status.AWAITING_INPUT,
        park_reason=ParkReason.CREDENTIAL,
        merge_block_class="credential",
    )
    ws_cleared = _make_ws(
        status=Status.AWAITING_INPUT,
        park_reason=ParkReason.CREDENTIAL,
        merge_block_class=None,
    )
    fp = derive_incidents(workspaces=[ws])[0].fingerprint
    fp_cleared = derive_incidents(workspaces=[ws_cleared])[0].fingerprint
    assert fp == fp_cleared
