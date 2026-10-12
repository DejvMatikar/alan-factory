from __future__ import annotations

import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import investigator_report as report  # type: ignore[import-not-found]  # noqa: E402

import alan._dispatcher_base as dispatcher_mod
from alan import investigator
from alan.claude_cli import AgentStreamingResult
from alan.incident import Disposition, Incident, IncidentClass, derive_incidents
from alan.incident_store import reconcile_reopen, set_disposition
from alan.queue import QUEUE_DB, Queue
from alan.rca_store import Citation, RcaDraft, TimelineEntry, get_rca_draft, save_rca_draft
from alan.workspace import Meta, Status, Workspace

_REAL_TRIGGER_INVESTIGATOR = investigator.trigger_investigator


@pytest.fixture(autouse=True)
def _restore_real_trigger_investigator(
    _no_real_investigator_spawn: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(investigator, "trigger_investigator", _REAL_TRIGGER_INVESTIGATOR)


def _make_ws(task_id: str, path: Path, *, status: Status = Status.ESCALATED) -> Workspace:
    ts = datetime(2026, 1, 1, tzinfo=UTC)
    meta = Meta(
        task_id=task_id, title="t", description="d", status=status,
        created_at=ts, updated_at=ts,
    )
    return Workspace(task_id=task_id, path=path, meta=meta)


def _make_draft(**overrides: Any) -> RcaDraft:
    defaults: dict[str, Any] = {
        "source": "investigator",
        "proposed_disposition": None,
        "timeline": (
            TimelineEntry(
                description="evidence",
                citation=Citation(kind="audit_row", file="a.jsonl", json_path="$.x"),
            ),
        ),
        "falsification_test": "rerun and confirm",
        "recurrence_signature": "sig",
        "recipe": "",
        "recurrence_signal": "signal",
        "verified": False,
    }
    defaults.update(overrides)
    return RcaDraft(**defaults)


def test_resolve_queue_db_prefers_cli_value(tmp_path: Path) -> None:
    cli_path = tmp_path / "cli-queue.db"
    assert report._resolve_queue_db(str(cli_path), str(tmp_path / "logs")) == cli_path  # noqa: SLF001


def test_resolve_queue_db_falls_back_to_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_path = tmp_path / "env-queue.db"
    monkeypatch.setenv("ALAN_QUEUE_DB_PATH", str(env_path))
    assert report._resolve_queue_db(None, str(tmp_path / "logs")) == env_path  # noqa: SLF001


def test_resolve_queue_db_falls_back_to_logs_root_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ALAN_QUEUE_DB_PATH", raising=False)
    logs_root = tmp_path / "logs"
    assert report._resolve_queue_db(None, str(logs_root)) == tmp_path / "queue.db"  # noqa: SLF001


def test_resolve_queue_db_default_matches_constant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALAN_QUEUE_DB_PATH", raising=False)
    assert report._resolve_queue_db(None, str(report._LOGS_ROOT_DEFAULT)) == QUEUE_DB  # noqa: SLF001


def test_metrics_agreement_and_recurrence_plus_na_empty(
    db_path: Path, queue: Queue, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = report.main(["--queue-db", str(db_path)])
    assert exit_code == 0
    empty_out = capsys.readouterr().out
    assert "METRIC disposition_agreement_rate n/a" in empty_out
    assert "METRIC recurrence_after_disposition n/a" in empty_out

    save_rca_draft(queue, "fp1", _make_draft(proposed_disposition="root_caused"))
    set_disposition(queue, "fp1", Disposition.ROOT_CAUSED, now=datetime(2026, 1, 1, tzinfo=UTC))

    save_rca_draft(queue, "fp2", _make_draft(proposed_disposition="known_flake"))
    set_disposition(queue, "fp2", Disposition.ROOT_CAUSED, now=datetime(2026, 1, 1, tzinfo=UTC))

    save_rca_draft(queue, "fp3", _make_draft(proposed_disposition=None))
    set_disposition(queue, "fp3", Disposition.ROOT_CAUSED, now=datetime(2026, 1, 1, tzinfo=UTC))

    save_rca_draft(
        queue, "fp4", _make_draft(source="human", proposed_disposition="by_design")
    )
    set_disposition(queue, "fp4", Disposition.BY_DESIGN, now=datetime(2026, 1, 1, tzinfo=UTC))

    t1 = datetime(2026, 1, 1, tzinfo=UTC)
    t2 = datetime(2026, 1, 2, tzinfo=UTC)
    set_disposition(queue, "fp5", Disposition.ROOT_CAUSED, now=t1)
    fresh_incident = Incident(
        fingerprint="fp5", incident_class=IncidentClass.TERMINAL_FAILED, scope_ref="ws:x",
        workspace_id="x", pr_number=None, title="t", evidence={}, last_seen=t2.isoformat(),
    )
    reconcile_reopen(queue, [fresh_incident], now=t2)

    exit_code = report.main(["--queue-db", str(db_path)])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "METRIC disposition_agreement_rate 0.5000 (1/2)" in out
    assert "METRIC recurrence_after_disposition 0.2000 (1/5)" in out


def test_endtoend_synthetic_workspace_incident(
    tmp_path: Path, db_path: Path, queue: Queue, capsys: pytest.CaptureFixture[str]
) -> None:
    ws_path = tmp_path / "ws-escalated"
    ws_path.mkdir()
    ws = _make_ws("ws-escalated", ws_path)

    incidents = derive_incidents(
        workspaces=[ws], handovers_by_workspace={ws.task_id: []}
    )
    assert len(incidents) == 1
    fingerprint = incidents[0].fingerprint

    yaml_text = (
        "Investigation complete.\n\n"
        "## RCA_DRAFT_YAML\n"
        "```yaml\n"
        "proposed_disposition: root_caused\n"
        "falsification_test: rerun the workspace and confirm it no longer escalates\n"
        "recurrence_signature: synthetic-escalation\n"
        "recipe: fix the underlying bug\n"
        "timeline: []\n"
        "```\n"
    )
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
        dispatcher_mod._maybe_trigger_investigator(ws, queue)  # noqa: SLF001
        assert done.wait(timeout=5.0)

    draft = get_rca_draft(queue, fingerprint)
    assert draft is not None
    assert draft.proposed_disposition == "root_caused"

    set_disposition(
        queue, fingerprint, Disposition.ROOT_CAUSED, now=datetime(2026, 1, 3, tzinfo=UTC)
    )

    exit_code = report.main(["--queue-db", str(db_path)])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "METRIC disposition_agreement_rate 1.0000 (1/1)" in out
    assert "METRIC recurrence_after_disposition 0.0000 (0/1)" in out
