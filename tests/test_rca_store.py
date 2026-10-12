from __future__ import annotations

import json
import threading
from datetime import UTC, datetime

import pytest

from alan.incident import Disposition, Incident, IncidentClass
from alan.incident_store import reconcile_reopen, set_disposition
from alan.queue import Queue
from alan.rca_store import (
    RCA_DRAFTS_META_KEY,
    Citation,
    RcaDraft,
    RcaSchemaError,
    TimelineEntry,
    get_rca_draft,
    load_rca_drafts,
    save_rca_draft,
)


def _make_draft(**overrides: object) -> RcaDraft:
    defaults: dict[str, object] = {
        "source": "investigator",
        "proposed_disposition": "root_caused",
        "timeline": (
            TimelineEntry(
                description="fd exhaustion observed",
                citation=Citation(
                    kind="audit_row", file="job_failure_audit.jsonl", json_path="$.error"
                ),
            ),
            TimelineEntry(
                description="replay confirms the sweep",
                citation=Citation(kind="replay_command", argv=("git", "log", "--oneline")),
            ),
        ),
        "falsification_test": "rerun sweep and confirm fd count stays flat",
        "recurrence_signature": "fd-exhaustion",
        "recipe": "prune invocation slots before seeding",
        "recurrence_signal": "occurrence_count=1",
        "verified": True,
    }
    defaults.update(overrides)
    return RcaDraft(**defaults)  # type: ignore[arg-type]


def test_rca_store_roundtrip_join_survives_disposition_reopen(queue: Queue) -> None:
    fingerprint = "fp-shared"
    draft = _make_draft()
    save_rca_draft(queue, fingerprint, draft)

    t1 = datetime(2026, 1, 1, tzinfo=UTC)
    set_disposition(queue, fingerprint, Disposition.ROOT_CAUSED, now=t1)

    incident = Incident(
        fingerprint=fingerprint,
        incident_class=IncidentClass.TERMINAL_FAILED,
        scope_ref="ws:x",
        workspace_id="x",
        pr_number=None,
        title="t",
        evidence={},
        last_seen=datetime(2026, 1, 2, tzinfo=UTC).isoformat(),
    )
    reconcile_reopen(queue, [incident], now=datetime(2026, 1, 2, tzinfo=UTC))

    joined = get_rca_draft(queue, fingerprint)
    assert joined == draft
    assert load_rca_drafts(queue)[fingerprint] == draft


def test_rca_store_roundtrips_workspace_key_and_created_at(queue: Queue) -> None:
    draft = _make_draft(workspace_key="ws-abc", created_at=1_700_000_000.5)
    save_rca_draft(queue, "fp-keyed", draft)

    loaded = get_rca_draft(queue, "fp-keyed")

    assert loaded == draft
    assert loaded is not None
    assert loaded.workspace_key == "ws-abc"
    assert loaded.created_at == 1_700_000_000.5


def test_rca_store_legacy_dict_without_join_fields_decodes_to_none(queue: Queue) -> None:
    queue.set_meta(
        RCA_DRAFTS_META_KEY,
        json.dumps({"fp-legacy": {"falsification_test": "check it", "timeline": []}}),
    )

    loaded = get_rca_draft(queue, "fp-legacy")

    assert loaded is not None
    assert loaded.workspace_key is None
    assert loaded.created_at is None


@pytest.mark.parametrize("raw", [None, 7, ["ws"], {"k": "v"}])
def test_rca_store_non_string_workspace_key_decodes_to_none(queue: Queue, raw: object) -> None:
    queue.set_meta(
        RCA_DRAFTS_META_KEY,
        json.dumps({"fp-odd": {"falsification_test": "check it", "workspace_key": raw}}),
    )

    loaded = get_rca_draft(queue, "fp-odd")

    assert loaded is not None
    assert loaded.workspace_key is None


@pytest.mark.parametrize("raw", ["yesterday", True, None, [1.0]])
def test_rca_store_non_numeric_created_at_decodes_to_none(queue: Queue, raw: object) -> None:
    queue.set_meta(
        RCA_DRAFTS_META_KEY,
        json.dumps({"fp-odd": {"falsification_test": "check it", "created_at": raw}}),
    )

    loaded = get_rca_draft(queue, "fp-odd")

    assert loaded is not None
    assert loaded.created_at is None


def test_rca_store_integer_created_at_decodes_to_float(queue: Queue) -> None:
    queue.set_meta(
        RCA_DRAFTS_META_KEY,
        json.dumps({"fp-int": {"falsification_test": "check it", "created_at": 1700000000}}),
    )

    loaded = get_rca_draft(queue, "fp-int")

    assert loaded is not None
    assert loaded.created_at == 1_700_000_000.0


def test_rca_store_malformed_load_drops_no_crash(queue: Queue) -> None:
    queue.set_meta(RCA_DRAFTS_META_KEY, "{not json")
    assert load_rca_drafts(queue) == {}

    queue.set_meta(RCA_DRAFTS_META_KEY, json.dumps([1, 2, 3]))
    assert load_rca_drafts(queue) == {}

    queue.set_meta(
        RCA_DRAFTS_META_KEY,
        json.dumps(
            {
                "fp0": {},
                "fp1": "not-a-dict",
                "fp2": {"falsification_test": "", "timeline": []},
                "fp3": {
                    "falsification_test": "check it",
                    "source": 123,
                    "proposed_disposition": 456,
                    "verified": "yes",
                    "unknown_field": "ignored",
                    "timeline": [
                        "not-a-dict",
                        {"description": "no citation dict", "citation": "oops"},
                        {"description": "bad citation", "citation": {"kind": "audit_row"}},
                        {"description": "bad kind", "citation": {"kind": "mystery", "file": "x"}},
                        {
                            "description": "good",
                            "citation": {
                                "kind": "log_line",
                                "file": "alan.log",
                                "grep_pattern": "ERROR",
                            },
                        },
                    ],
                },
                "fp4": {
                    "falsification_test": "still valid",
                    "timeline": "not-a-list",
                },
            }
        ),
    )
    records = load_rca_drafts(queue)
    assert set(records) == {"fp3", "fp4"}
    record = records["fp3"]
    assert record.source == "investigator"
    assert record.proposed_disposition is None
    assert record.verified is False
    assert len(record.timeline) == 1
    assert record.timeline[0].citation.kind == "log_line"
    assert records["fp4"].timeline == ()


def test_rca_store_concurrent_two_fingerprints_no_clobber(queue: Queue) -> None:
    draft_a = _make_draft(recurrence_signal="a")
    draft_b = _make_draft(recurrence_signal="b")
    barrier = threading.Barrier(2)

    def _write(fp: str, draft: RcaDraft) -> None:
        barrier.wait()
        save_rca_draft(queue, fp, draft)

    t1 = threading.Thread(target=_write, args=("fp-a", draft_a))
    t2 = threading.Thread(target=_write, args=("fp-b", draft_b))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    records = load_rca_drafts(queue)
    assert records["fp-a"] == draft_a
    assert records["fp-b"] == draft_b


def test_locatorless_citation_schema_reject() -> None:
    with pytest.raises(RcaSchemaError):
        Citation(kind="audit_row", file="job_failure_audit.jsonl")
    with pytest.raises(RcaSchemaError):
        Citation(kind="log_line", grep_pattern="ERROR")
    with pytest.raises(RcaSchemaError):
        Citation(kind="replay_command")
    with pytest.raises(RcaSchemaError):
        Citation(kind="unknown")


def test_missing_falsification_test_reject() -> None:
    with pytest.raises(RcaSchemaError):
        _make_draft(falsification_test="")
