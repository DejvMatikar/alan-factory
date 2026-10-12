from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import ingest_rca as report  # type: ignore[import-not-found]  # noqa: E402

from alan.queue import QUEUE_DB, Queue
from alan.rca_store import load_rca_drafts

_REAL_SEED = Path(__file__).parent.parent / "docs" / "audits" / (
    "incident-2026-07-03-triage-slot-exhaustion.md"
)

_FD_EXHAUSTION_WEDGE_SEED = """\
# Incident: roadmap-driver wedged by fd exhaustion

## Record

| Field | Value |
|---|---|
| fingerprint | `2026-07-04-roadmap-driver-fd-exhaustion` |
| disposition | `mechanically_recovered` — proxy process restarted |
| recurrence_signature | `fd-exhaustion` |
| recipe | restart the roadmap-driver process and lower its open-file ceiling |

## Timeline (evidence-linked)

- proxy stopped polling mid-session {cite kind=log_line file=alan.log grep_pattern=EMFILE}
- no further journal entries until manual restart
"""

_MISSING_RECORD_SECTION_SEED = """\
# Incident: no record section

## Symptom

Something happened, but there is no Record table to parse.
"""

_MISSING_FINGERPRINT_SEED = """\
## Record

| Field | Value |
|---|---|
| class | `watcher_liveness` |
| falsification_test | rerun and confirm it stays fixed |
"""

_MISSING_FALSIFICATION_AND_RECURRENCE_SEED = """\
## Record

| Field | Value |
|---|---|
| fingerprint | `fp-no-falsification` |
"""

_EXPLICIT_FALSIFICATION_SEED = """\
## Record

| Field | Value |
|---|---|
| fingerprint | `fp-explicit-falsification` |
| falsification_test | rerun the sweep twice and confirm no error is logged |
| recurrence_signature | `some-signature` |
"""

_NO_DISPOSITION_NO_RECIPE_SEED = """\
## Record

| Field | Value |
|---|---|
| fingerprint | `fp-bare-minimum` |
| recurrence_signature | `bare-signature` |
"""

_RECIPE_FROM_FIX_SECTION_SEED = """\
## Record

| Field | Value |
|---|---|
| fingerprint | `fp-recipe-from-fix` |
| recurrence_signature | `fix-section-signature` |

## Fix (this PR)

- prune the stale slots before reseeding
- add a loud failure audit row
"""

_WRAPPED_BULLET_SEED = """\
## Record

| Field | Value |
|---|---|
| fingerprint | `fp-wrapped-bullets` |
| recurrence_signature | `wrapped-signature` |

## Fix (this PR)

- first fix bullet that wraps onto a second physical
  line before the next bullet starts
- second fix bullet, single line

## Timeline (evidence-linked)

- something happened first, and the citation tag wraps onto
  a continuation line {cite kind=log_line file=alan.log grep_pattern=ERROR}
- a single-line timeline bullet with no citation
"""

_TIMELINE_CITATION_VARIETY_SEED = """\
## Record

| Field | Value |
|---|---|
| fingerprint | `fp-timeline-variety` |
| recurrence_signature | `variety-signature` |

## Timeline (evidence-linked)

- audit row evidence {cite kind=audit_row file=job_failure_audit.jsonl json_path=$.error}
- replay command evidence {cite kind=replay_command argv="git log --oneline"}
- bullet with no citation tag at all contributes nothing
- bullet with a tag missing the kind attribute {cite file=alan.log}
- bullet with an unrecognized citation kind {cite kind=mystery file=alan.log}
- bullet whose citation is missing its locator {cite kind=audit_row}
- bullet whose replay_command has no argv {cite kind=replay_command}
"""


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_two_seeds_ingest_as_human_goldlabels(tmp_path: Path, db_path: Path) -> None:
    wedge_path = _write(tmp_path, "fd_exhaustion_wedge.md", _FD_EXHAUSTION_WEDGE_SEED)

    exit_code = report.main(
        ["--queue-db", str(db_path), str(_REAL_SEED), str(wedge_path)]
    )
    assert exit_code == 0

    queue = Queue(db_path=db_path)
    try:
        drafts = load_rca_drafts(queue)
    finally:
        queue.close()

    assert set(drafts) == {
        "2026-07-03-triage-slot-exhaustion",
        "2026-07-04-roadmap-driver-fd-exhaustion",
    }
    triage_draft = drafts["2026-07-03-triage-slot-exhaustion"]
    assert triage_draft.source == "human"
    assert triage_draft.proposed_disposition == "root_caused"
    assert triage_draft.verified is True
    assert triage_draft.falsification_test

    wedge_draft = drafts["2026-07-04-roadmap-driver-fd-exhaustion"]
    assert wedge_draft.source == "human"
    assert wedge_draft.proposed_disposition == "mechanically_recovered"
    assert wedge_draft.recurrence_signature == "fd-exhaustion"
    assert wedge_draft.recipe == (
        "restart the roadmap-driver process and lower its open-file ceiling"
    )
    assert len(wedge_draft.timeline) == 1
    assert wedge_draft.timeline[0].citation.kind == "log_line"
    assert wedge_draft.timeline[0].citation.file == "alan.log"
    assert wedge_draft.timeline[0].citation.grep_pattern == "EMFILE"


def test_missing_record_section_rejected() -> None:
    with pytest.raises(report.IngestError, match="Record"):
        report.parse_seed(_MISSING_RECORD_SECTION_SEED)


def test_missing_fingerprint_and_incident_id_rejected() -> None:
    with pytest.raises(report.IngestError, match="fingerprint"):
        report.parse_seed(_MISSING_FINGERPRINT_SEED)


def test_missing_falsification_test_and_recurrence_signature_rejected() -> None:
    with pytest.raises(report.IngestError, match="falsification_test"):
        report.parse_seed(_MISSING_FALSIFICATION_AND_RECURRENCE_SEED)


def test_explicit_falsification_test_used_verbatim() -> None:
    _, draft = report.parse_seed(_EXPLICIT_FALSIFICATION_SEED)
    assert draft.falsification_test == "rerun the sweep twice and confirm no error is logged"


def test_falsification_test_derived_from_recurrence_signature() -> None:
    _, draft = report.parse_seed(_NO_DISPOSITION_NO_RECIPE_SEED)
    assert "bare-signature" in draft.falsification_test
    assert "`" not in draft.falsification_test
    assert draft.proposed_disposition is None
    assert draft.recipe == ""


def test_recipe_derived_from_fix_section_when_no_explicit_field() -> None:
    _, draft = report.parse_seed(_RECIPE_FROM_FIX_SECTION_SEED)
    assert draft.recipe == (
        "prune the stale slots before reseeding add a loud failure audit row"
    )


def test_timeline_citation_variety_only_valid_tags_survive() -> None:
    _, draft = report.parse_seed(_TIMELINE_CITATION_VARIETY_SEED)
    assert len(draft.timeline) == 2
    kinds = {entry.citation.kind for entry in draft.timeline}
    assert kinds == {"audit_row", "replay_command"}


def test_timeline_section_absent_yields_empty_timeline() -> None:
    _, draft = report.parse_seed(_NO_DISPOSITION_NO_RECIPE_SEED)
    assert draft.timeline == ()


def test_wrapped_bullets_join_continuation_lines_for_recipe_and_timeline() -> None:
    _, draft = report.parse_seed(_WRAPPED_BULLET_SEED)

    assert draft.recipe == (
        "first fix bullet that wraps onto a second physical line before the next bullet "
        "starts second fix bullet, single line"
    )

    assert len(draft.timeline) == 1
    entry = draft.timeline[0]
    assert entry.description == (
        "something happened first, and the citation tag wraps onto a continuation line"
    )
    assert entry.citation.kind == "log_line"
    assert entry.citation.file == "alan.log"
    assert entry.citation.grep_pattern == "ERROR"


def test_ingest_seed_file_missing_file_raises_ingest_error(db_path: Path) -> None:
    queue = Queue(db_path=db_path)
    try:
        with pytest.raises(report.IngestError, match="not found"):
            report.ingest_seed_file(Path("/nonexistent/seed.md"), queue)
    finally:
        queue.close()


def test_ingest_seed_file_unreadable_raises_ingest_error(
    tmp_path: Path, db_path: Path
) -> None:
    directory_as_seed = tmp_path / "not_a_file"
    directory_as_seed.mkdir()
    queue = Queue(db_path=db_path)
    try:
        with pytest.raises(report.IngestError, match="could not be read"):
            report.ingest_seed_file(directory_as_seed, queue)
    finally:
        queue.close()


def test_main_reports_failure_and_continues_past_bad_seed(
    tmp_path: Path, db_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    good = _write(tmp_path, "good.md", _EXPLICIT_FALSIFICATION_SEED)
    bad = _write(tmp_path, "bad.md", _MISSING_RECORD_SECTION_SEED)

    exit_code = report.main(["--queue-db", str(db_path), str(good), str(bad)])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "INGESTED fp-explicit-falsification" in captured.out
    assert "SKIP" in captured.err

    queue = Queue(db_path=db_path)
    try:
        drafts = load_rca_drafts(queue)
    finally:
        queue.close()
    assert set(drafts) == {"fp-explicit-falsification"}


def test_resolve_queue_db_prefers_cli_value(tmp_path: Path) -> None:
    cli_path = tmp_path / "cli-queue.db"
    assert report._resolve_queue_db(str(cli_path)) == cli_path  # noqa: SLF001


def test_resolve_queue_db_falls_back_to_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_path = tmp_path / "env-queue.db"
    monkeypatch.setenv("ALAN_QUEUE_DB_PATH", str(env_path))
    assert report._resolve_queue_db(None) == env_path  # noqa: SLF001


def test_resolve_queue_db_falls_back_to_constant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALAN_QUEUE_DB_PATH", raising=False)
    assert report._resolve_queue_db(None) == QUEUE_DB  # noqa: SLF001
